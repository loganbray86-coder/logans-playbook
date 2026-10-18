"""FantasyPros weekly PPR projections (real FP consensus projections, never derived).

FantasyPros' position projection tables only show the top 10 to logged-out visitors, so we
take those 6 tables plus each wanted player's public projection page
(/nfl/projections/<slug>.php?scoring=PPR, "Projections (Week N)" table, Points column).
Slugs come from FantasyPros' own ranking data (player_filename). A guessed slug is accepted
only when the page's player name AND team match. Values are cached in memory + cache/fpproj.json
and refreshed in a background thread about every 45-60 minutes. Read-only GETs to FantasyPros."""
import json, os, re, time, threading, random, requests
from concurrent.futures import ThreadPoolExecutor

BASE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(BASE, "cache", "fpproj.json")
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
URL = "https://www.fantasypros.com/nfl/projections/%s.php?scoring=PPR"
TTL = 45 * 60            # refresh a player's value after ~45 min (cycle finishes well inside 60)
MISS_TTL = 6 * 3600      # retry a slug that had no page/table after 6 h
WORKERS = 3
_lock = threading.Lock()
_db = {"week": None, "players": {}, "tables": {}}   # players: slug -> {pts, week, at, name, team, ok}
_want = {}               # slug -> {"name","team","pos","prio","guess","locked"}
_thread = None
STATE = {"fetched": 0, "errors": 0, "last_cycle": None, "cycle_s": None, "running": False}

def _load():
    global _db
    try:
        d = json.load(open(PATH))
        if isinstance(d, dict) and "players" in d: _db = d
    except Exception: pass
_load()

def _save():
    try:
        with _lock: s = json.dumps(_db)
        tmp = PATH + ".tmp"; open(tmp, "w").write(s); os.replace(tmp, PATH)
    except Exception: pass

def _txt(h): return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h)).strip()

def parse_page(t):
    """Returns (week, points, name, title_team) from a FantasyPros player projection page."""
    m = re.search(r"Projections \(Week (\d+)\)(.*?)</table>", t, re.S)
    nm = re.search(r'player-bio-header_bio-name-trigger"[^>]*fp-player-name="([^"]+)"', t)
    ti = re.search(r"<title>(.*?)</title>", t, re.S)
    team = None
    if ti:
        parts = [x.strip() for x in ti.group(1).split("|")]
        team = parts[1] if len(parts) > 2 and parts[1] else None
    if not m: return None, None, nm.group(1) if nm else None, team
    body = m.group(2)
    ths = [_txt(x) for x in re.findall(r"<th[^>]*>(.*?)</th>", body, re.S)]
    tds = [_txt(x) for x in re.findall(r"<td[^>]*>(.*?)</td>", body, re.S)]
    pts = None
    if ths and tds and ths[-1].lower().startswith("points") and len(tds) >= len(ths):
        try: pts = float(tds[len(ths) - 1].replace(",", ""))
        except ValueError: pts = None
    elif tds:
        try: pts = float(tds[-1].replace(",", ""))
        except ValueError: pts = None
    return int(m.group(1)), pts, nm.group(1) if nm else None, team

def parse_table(t):
    """Top rows of /nfl/projections/<pos>.php: [(fp_id, name, slug, pts)]."""
    out = []
    for fid, row in re.findall(r'<tr class="mpb-player-(\d+)[^"]*">(.*?)</tr>', t, re.S):
        a = re.search(r'href="/nfl/projections/([^"]+)\.php"[^>]*fp-player-name="([^"]+)"', row)
        v = re.findall(r'data-sort-value="([^"]+)"', row)
        if a and v:
            try: out.append((fid, a.group(2), a.group(1), float(v[-1])))
            except ValueError: pass
    wk = re.search(r"Week (\d+) PPR", t) or re.search(r"Week (\d+) [A-Z/]+ Projections", t)
    return (int(wk.group(1)) if wk else None), out

def _get(url):
    r = requests.get(url, headers=UA, timeout=20)
    if r.status_code != 200: raise RuntimeError("FP HTTP %d" % r.status_code)
    return r.text

def _norm(n):
    import unicodedata
    n = unicodedata.normalize("NFKD", n or "").encode("ascii", "ignore").decode().lower()
    n = re.sub(r"[.'`]", "", n); n = re.sub(r"[^a-z0-9 ]", " ", n); n = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", n)
    return " ".join(n.split())

def _fetch_one(slug, meta, week):
    try:
        t = _get(URL % slug)
    except Exception as e:
        STATE["errors"] += 1
        return slug, {"ok": False, "err": str(e)[:60], "at": time.time()}
    STATE["fetched"] += 1
    wk, pts, nm, team = parse_page(t)
    rec = {"at": time.time(), "name": nm, "team": team, "week": wk, "pts": pts, "ok": wk is not None}
    if meta.get("guess"):   # guessed slug: must be the same person on the same team
        if not nm or _norm(nm) != _norm(meta.get("name")) or (meta.get("team_full") and team and team != meta["team_full"]) or (meta.get("team_full") and not team):
            rec = {"ok": False, "err": "guess mismatch", "at": time.time()}
    return slug, rec

def _tables(week):
    for pos in ("qb", "rb", "wr", "te", "k", "dst"):
        try:
            wk, rows = parse_table(_get(URL % pos))
        except Exception:
            STATE["errors"] += 1; continue
        if wk and week and wk != week: continue
        with _lock:
            for fid, nm, slug, pts in rows:
                old = _db["players"].get(slug) or {}
                _db["players"][slug] = {**old, "at": time.time(), "name": nm, "week": wk or week, "pts": round(pts, 2), "ok": True, "src": "table"}
            _db["tables"][pos] = time.time()

def _due(slug, meta, week):
    r = _db["players"].get(slug)
    if not r: return True
    age = time.time() - r.get("at", 0)
    if not r.get("ok"): return age > MISS_TTL
    if r.get("week") != week: return age > 15 * 60      # page not on this week yet / rolled over
    if meta.get("locked"): return False                  # game started: pregame projection is final
    return age > TTL

def _cycle(week):
    t0 = time.time()
    if week and _db.get("week") != week:
        with _lock: _db["week"] = week
    _tables(week)
    with _lock: items = sorted(_want.items(), key=lambda kv: kv[1].get("prio", 9))
    todo = [(s, m) for s, m in items if _due(s, m, week)]
    if todo:
        with ThreadPoolExecutor(WORKERS) as ex:
            for slug, rec in ex.map(lambda sm: (time.sleep(random.uniform(.2, .6)), _fetch_one(sm[0], sm[1], week))[1], todo):
                with _lock:
                    old = _db["players"].get(slug) or {}
                    # keep a good value for this week if a refetch fails or the page already rolled to next week
                    if old.get("ok") and old.get("week") == week and (not rec.get("ok") or rec.get("week") != week):
                        old["at"] = time.time(); _db["players"][slug] = old
                    else:
                        _db["players"][slug] = rec
    STATE["last_cycle"] = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()); STATE["cycle_s"] = round(time.time() - t0, 1)
    _save()

_week_fn = None
def _loop():
    STATE["running"] = True
    while True:
        try:
            wk = _week_fn() if _week_fn else _db.get("week")
            _cycle(wk)
        except Exception:
            STATE["errors"] += 1
        time.sleep(60)   # each pass only refetches entries older than TTL

def want(entries, week_fn):
    """entries: {slug: meta}. Starts the background refresher once."""
    global _thread, _week_fn
    _week_fn = week_fn
    with _lock:
        for s, m in entries.items():
            cur = _want.get(s)
            if not cur or m.get("prio", 9) < cur.get("prio", 9) or m.get("locked") != cur.get("locked"): _want[s] = m
    if _thread is None or not _thread.is_alive():
        _thread = threading.Thread(target=_loop, daemon=True); _thread.start()

def value(slug, week):
    """FP PPR projection for this week, or None. Never estimated."""
    with _lock: r = _db["players"].get(slug)
    if not r or not r.get("ok") or r.get("week") != week or r.get("pts") is None: return None
    return r["pts"]

def status(week):
    with _lock:
        ok = sum(1 for s in _want if (_db["players"].get(s) or {}).get("week") == week and (_db["players"].get(s) or {}).get("ok"))
        return {**STATE, "wanted": len(_want), "have": ok, "week": week}

def guess_slug(name):
    s = _norm(name).replace(" ", "-")
    return s or None
