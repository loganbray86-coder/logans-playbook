import urllib.parse
import json, re, time, os, threading, unicodedata, hashlib
import requests
import vault as VAULT
import coach as COACH
APP_VERSION = "2.0.0-friends"
import adv as ADV
import espn as ESPN
import cards as CARDS
import fpproj as FPP
from flask import Flask, jsonify, send_from_directory, request
BASE = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
app = Flask(__name__, static_folder=os.path.join(BASE, "static"))
os.makedirs(os.path.join(BASE, "cache"), exist_ok=True)
_cache, _lock = {}, threading.Lock()
_refreshing = set()
_refresh_depth = threading.local()
STATUS = {}

def in_nfl_window():
    """Thu/Sun/Mon NFL game windows in US/Eastern."""
    try:
        from zoneinfo import ZoneInfo
        now = __import__("datetime").datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc) - timedelta(hours=4)
    wd, h = now.weekday(), now.hour
    if wd == 6:  # Sunday ~12:00–23:59 ET
        return 12 <= h <= 23
    if wd in (0, 3):  # Mon / Thu night games ~19:00–23:59 ET
        return 19 <= h <= 23
    return False

def public_ttl(kind="default"):
    """15 min normally; 5 min for scores/news during NFL game windows."""
    if kind in ("scores", "news") and in_nfl_window():
        return 300
    return 900

def _stamp():
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())

def _store(key, v, ok=True, error=None):
    now = time.time()
    with _lock:
        _cache[key] = (now, v)
        _refreshing.discard(key)
    if key.startswith("u:"): return v      # per-user/per-league entries stay out of the public /api/status
    st = {"ok": ok, "fetched": _stamp()}
    if error: st["error"] = error
    STATUS[key] = st
    return v

def _run_refresh(key, fn):
    """Blocking refresh. Nested cached() calls also block so a bg rebuild gets fresh sources."""
    prev = getattr(_refresh_depth, "on", False)
    _refresh_depth.on = True
    try:
        try:
            v = fn()
            return _store(key, v, True)
        except Exception as e:
            with _lock:
                stale = _cache.get(key)
                _refreshing.discard(key)
            if not key.startswith("u:"): STATUS[key] = {"ok": False, "error": str(e)[:200], "fetched": _stamp()}
            return stale[1] if stale else None
    finally:
        _refresh_depth.on = prev

def cached(key, ttl, fn):
    """Return cached value. If expired, serve stale and refresh in a background thread.
    Inside a refresh (thread-local), always fetch fresh so nested sources are not stale."""
    now = time.time()
    blocking = getattr(_refresh_depth, "on", False)
    with _lock:
        c = _cache.get(key)
        if c and now - c[0] < ttl and not blocking:
            return c[1]
        if c and now - c[0] < ttl and blocking:
            return c[1]  # still fresh — no need to refetch
        stale = c[1] if c else None
        already = key in _refreshing
        if stale is not None and not already and not blocking:
            _refreshing.add(key)
            kick = True
        elif not already:
            _refreshing.add(key)
            kick = False
        else:
            kick = False
    if stale is not None and not blocking:
        if kick:
            threading.Thread(target=_run_refresh, args=(key, fn), daemon=True).start()
        return stale
    # No stale, or we are inside a refresh — block for fresh data
    return _run_refresh(key, fn)

def norm(n):
    n = unicodedata.normalize("NFKD", n).encode("ascii", "ignore").decode().lower()
    n = re.sub(r"[.'`]", "", n); n = re.sub(r"[^a-z0-9 ]", " ", n)
    n = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", n)
    return " ".join(n.split())

FP = "https://www.fantasypros.com/nfl/rankings/"
WEEKLY = {"QB": "qb.php", "RB": "ppr-rb.php", "WR": "ppr-wr.php", "TE": "ppr-te.php", "DST": "dst.php", "K": "k.php"}
ROS = {"QB": "ros-qb.php", "RB": "ros-ppr-rb.php", "WR": "ros-ppr-wr.php", "TE": "ros-ppr-te.php", "DST": "ros-dst.php", "K": "ros-k.php", "OVR": "ros-ppr-overall.php"}

def fp_page(page):
    def f():
        t = requests.get(FP + page, headers=UA, timeout=20).text
        i = t.index("var ecrData = ") + 14
        d, _ = json.JSONDecoder().raw_decode(t[i:])
        return d
    return cached("fp:" + page, public_ttl(), f)

def fc(teams):
    # FantasyCalc API terms: cache and refresh each query no more than once every 3 hours, with attribution.
    return cached(f"fc{teams}", 3 * 3600, lambda: requests.get(
        f"https://api.fantasycalc.com/values/current?isDynasty=false&numQbs=1&numTeams={teams}&ppr=1", headers=UA, timeout=20).json())

def sleeper():
    path = os.path.join(BASE, "cache", "sleeper_slim.json")
    def f():
        if os.path.exists(path) and time.time() - os.path.getmtime(path) < 86400:   # Sleeper docs: full player list at most once a day
            return json.load(open(path))
        raw = requests.get("https://api.sleeper.app/v1/players/nfl", timeout=60).json()
        slim = {}
        for p in raw.values():
            if not p.get("full_name") or p.get("position") not in ("QB", "RB", "WR", "TE", "K"): continue
            if not p.get("team"): continue
            slim[norm(p["full_name"]) + "|" + p["position"]] = {k: p.get(k) for k in ("injury_status", "injury_body_part", "injury_notes", "status", "team", "news_updated", "depth_chart_order", "espn_id")}
        json.dump(slim, open(path, "w")); return slim
    return cached("sleeper", 86400, f)

def espn_ids():
    def f():
        r = requests.get("https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/%d/players?view=players_wl" % time.gmtime().tm_year,
                         headers={**UA, "X-Fantasy-Filter": '{"filterActive":{"value":true}}'}, timeout=30).json()
        pm = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K"}
        out = {}
        for p in r:
            pos = pm.get(p.get("defaultPositionId"))
            if pos and p.get("fullName"):
                out[norm(p["fullName"]) + "|" + pos] = {"id": p["id"], "own": (p.get("ownership") or {}).get("percentOwned")}
        return out
    return cached("espn_ids", 21600, f)

def rototrade():
    d = json.load(open(os.path.join(BASE, "data", "rototrade.json")))
    STATUS["rototrade"] = {"ok": True, "fetched": "seed file, as of " + d["as_of"], "note": d.get("source")}
    return d

def rosters_file():
    mock = os.environ.get("LP_MOCK_ROSTERS")  # local testing only: a saved copy of live rosters
    return json.load(open(mock if mock and os.path.exists(mock) else os.path.join(BASE, "data", "rosters.json")))
def espn_ttl(): return 60 if in_nfl_window() else 120
def rosters(): return ESPN.live_rosters(rosters_file(), cached, espn_ttl())   # legacy (sync tools)

TEAM_FULL = {}            # FP team code -> full name (from FP D/ST names)
ESPN2FP = {"JAX": "JAC", "WSH": "WAS", "LA": "LAR"}
FP_WEEK = {"wk": None}
def fp_team(t): return ESPN2FP.get(t, t) if t else t

def espn_defaults(wk):
    """ESPN default-PPR week projection for any player (public pool), keyed by ESPN id and D/ST team."""
    out = {}
    try:
        for p in CARDS.global_pool(cached, wk) or []:
            if p.get("proj") is None: continue
            out[("dst:" + fp_team(p["team"])) if p["pos"] == "DST" else p["id"]] = p["proj"]
    except Exception: pass
    return out

def attach_projections(players, R, cur, lname):
    """ESPN (league-scored when live) + FantasyPros PPR projections on every player, and both
    matchup projections. FP values come only from FantasyPros pages; unmatched -> None."""
    now = time.time()
    try: ewk = CARDS.week_now(cached)
    except Exception: ewk = cur
    ed = espn_defaults(ewk) if ewk == cur else {}
    for e in players.values():
        v = ed.get("dst:" + str(e.get("team"))) if e.get("pos") == "DST" else ed.get(e.get("espn_id"))
        if v is not None: e["espn_proj"] = v
    # ESPN league projections/actuals for rostered players
    mine_opp = set()
    for lid, L in R["leagues"].items():
        wkp = L.get("wkp") or {}
        if L.get("wkp_period") not in (None, cur): wkp = {}
        for tname, nmap in lname.get(lid, {}).items():
            for n, e in nmap.items():
                x = wkp.get(n)
                if x:
                    e.setdefault("ep", {})[lid] = x.get("e"); e.setdefault("ea", {})[lid] = x.get("a")
                    if x.get("t") and not e.get("team_espn"): e["team_espn"] = x["t"]
        opp = (L.get("matchup") or {}).get("opp")
        for t in (L.get("my_team"), opp):
            for e in lname.get(lid, {}).get(t, {}).values(): mine_opp.add(e["key"])
    # FantasyPros: which players to keep fresh
    want = {}
    def locked(e):
        if any(v is not None for v in (e.get("ea") or {}).values()): return True
        if e.get("game_status") not in (None, "", "scheduled", "created"): return True
        return bool(e.get("kickoff") and float(e["kickoff"]) < now)
    for e in players.values():
        rostered = bool(e.get("owners"))
        wkr = (e.get("wk") or {}).get("rank")
        lim = {"QB": 40, "RB": 70, "WR": 90, "TE": 40, "K": 40, "DST": 40}.get(e.get("pos"))
        if not rostered and not (wkr is not None and lim and int(wkr) <= lim): continue
        if str(e.get("bye")) == str(cur): continue        # bye: no game, nothing to project
        slug, guess = e.get("fp_slug"), False
        if not slug and rostered and e.get("pos") != "DST":
            slug, guess = FPP.guess_slug(e["name"]), True
        if not slug: continue
        e["fp_slug"] = slug
        prio = 0 if e["key"] in mine_opp else 1 if rostered else 2
        tf = TEAM_FULL.get(fp_team(e.get("team_espn") or e.get("team"))) if guess else None
        want[slug] = {"name": e["name"], "pos": e.get("pos"), "prio": prio, "guess": guess, "team_full": tf, "locked": locked(e)}
    FP_WEEK["wk"] = cur
    FPP.want(want, lambda: FP_WEEK["wk"])
    for e in players.values():
        if e.get("fp_slug"):
            v = FPP.value(e["fp_slug"], cur)
            if v is not None: e["fpp"] = v
    # matchup projections: starters only; played/in-progress players count their actual points (as ESPN does)
    for lid, L in R["leagues"].items():
        m = L.get("matchup")
        if not m: continue
        st = L.get("starters") or {}
        def tot(team, names):
            nmap = lname.get(lid, {}).get(team, {})
            if names is None: return None
            r = {"espn": 0.0, "fp": 0.0, "fp_pre": 0.0, "fp_missing": [], "espn_missing": [], "n": 0}
            for n in names:
                if not n or n == "(empty)": continue
                e = nmap.get(n); r["n"] += 1
                if not e: r["fp_missing"].append(n); r["espn_missing"].append(n); continue
                act = (e.get("ea") or {}).get(lid); ep = (e.get("ep") or {}).get(lid)
                bye = str(e.get("bye")) == str(cur)
                fpv = 0.0 if bye else e.get("fpp")
                r["espn"] += act if act is not None else (ep or 0.0)
                if ep is None and act is None and not bye: r["espn_missing"].append(n)
                if fpv is None: r["fp_missing"].append(n)
                else: r["fp_pre"] += fpv
                if act is not None: r["fp"] += act
                elif fpv is not None: r["fp"] += fpv
            for k in ("espn", "fp", "fp_pre"): r[k] = round(r[k], 1)
            return r
        me_names = [x.get("player") for x in (L.get("lineup") or [])] or st.get(L.get("my_team"))
        m["proj2"] = {"me": tot(L.get("my_team"), me_names), "opp": tot(m.get("opp"), st.get(m.get("opp"))),
                      "fp_week": cur, "fp_status": FPP.status(cur)}

def build_base():
    """Everything that is the same for every user: FantasyPros, FantasyCalc, RotoTrade, Sleeper,
    ESPN ids, nflverse, schedule. Built once and shared; no rosters in here."""
    players = {}
    def get(p, pos):
        key = norm(p["player_name"]) + "|" + pos
        if pos == "DST": key = "dst:" + p["player_team_id"]
        e = players.setdefault(key, {"key": key, "name": p["player_name"], "pos": pos, "team": p.get("player_team_id"), "bye": p.get("player_bye_week")})
        fn = p.get("player_filename") or ""
        if fn.endswith(".php") and not e.get("fp_slug"): e["fp_slug"] = fn[:-4]
        if pos == "DST" and p.get("player_team_id"): TEAM_FULL[p["player_team_id"]] = p["player_name"]
        return e
    meta = {}
    for pos, page in WEEKLY.items():
        d = fp_page(page)
        if not d: continue
        meta["weekly"] = {"week": d.get("week"), "updated": d.get("last_updated"), "experts": d.get("total_experts")}
        for p in d["players"]:
            e = get(p, pos)
            e["opp"] = p.get("player_opponent"); e["own"] = p.get("player_owned_avg"); e["kickoff"] = p.get("player_game_kickoff_ts"); e["game_status"] = p.get("player_game_status")
            e["wk"] = {"rank": p.get("rank_ecr"), "pos_rank": p.get("pos_rank"), "pts": float(p["r2p_pts"]) if p.get("r2p_pts") not in (None, "") else None,
                       "best": p.get("rank_min"), "worst": p.get("rank_max"), "avg": p.get("rank_ave"), "std": p.get("rank_std"), "grade": p.get("start_sit_grade"), "delta": p.get("player_ecr_delta")}
    for pos, page in ROS.items():
        if pos == "OVR": continue
        d = fp_page(page)
        if not d: continue
        meta["ros"] = {"updated": d.get("last_updated")}
        for p in d["players"]:
            e = get(p, pos)
            e["ros"] = {"pos_rank": p.get("pos_rank") or f"{pos}{p.get('rank_ecr')}", "pos_num": p.get("rank_ecr"), "best": p.get("rank_min"), "worst": p.get("rank_max"), "avg": p.get("rank_ave"), "std": p.get("rank_std")}
    d = fp_page(ROS["OVR"])
    if d:
        for p in d["players"]:
            pos = p.get("player_position_id")
            k = ("dst:" + p["player_team_id"]) if pos == "DST" else norm(p["player_name"]) + "|" + pos
            if k in players: players[k].setdefault("ros", {})["overall"] = p.get("rank_ecr")
    # values
    byname = {}
    for e in players.values(): byname.setdefault(norm(e["name"]), []).append(e)
    for teams in (12, 16):
        for v in fc(teams) or []:
            pl = v["player"]; c = byname.get(norm(pl["name"]), [])
            c = [x for x in c if x["pos"] == pl.get("position")] or c[:1]
            if c: c[0][f"fc{teams}"] = v["value"]
            else:
                k = norm(pl["name"]) + "|" + pl.get("position", "")
                e = players.setdefault(k, {"key": k, "name": pl["name"], "pos": pl.get("position"), "team": pl.get("maybeTeam")})
                e[f"fc{teams}"] = v["value"]; byname.setdefault(norm(pl["name"]), []).append(e)
    rt = rototrade()
    for n, val in rt["values"].items():
        c = byname.get(norm(n), [])
        if c: c[0]["rt"] = val
    sl = sleeper() or {}
    depth = {}
    for k2, v in sl.items():
        if k2.endswith("|RB") and v.get("team") and v.get("depth_chart_order"): depth.setdefault(ADV.canon(v["team"]), []).append((v["depth_chart_order"], k2.split("|")[0]))
    for e in players.values():
        s = sl.get(norm(e["name"]) + "|" + str(e["pos"]))
        if s:
            if s.get("espn_id"): e["espn_id"] = s.get("espn_id")
            e["inj"] = s.get("injury_status"); e["inj_part"] = s.get("injury_body_part"); e["inj_note"] = s.get("injury_notes")
    try: eids = espn_ids() or {}
    except Exception: eids = {}
    for e in players.values():
        x = eids.get(norm(e["name"]) + "|" + str(e["pos"]))
        if x:
            e["espn_id"] = x["id"]
            if x.get("own") is not None: e["espn_own"] = round(x["own"], 1)
    nmap = {norm(e["name"]): e["name"] for e in players.values()}
    for e in players.values():
        if e["pos"] == "RB" and e.get("team"):
            mates = sorted(x for x in depth.get(ADV.canon(e["team"]), []) if x[1] != norm(e["name"]))
            if mates: e["cuff"] = nmap.get(mates[0][1], mates[0][1].title())
    advd = cached("adv", 21600, lambda: ADV.build_adv(norm)); extra = {}
    cur = int(meta.get("weekly", {}).get("week") or 1)
    if advd:
        for e in players.values():
            a = advd["players"].get(norm(e["name"]) + "|" + str(e["pos"]))
            if a: e["adv"] = a
        sched = cached("sched", public_ttl(), lambda: ADV.schedule(cur))
        extra = {"team_stats": advd["teams"], "adv_season": advd["season"], "adv_thru": advd["thru_week"], "fpa": advd["fpa"]}
        if sched:
            extra["sched"] = sched; extra["sos"] = ADV.sos(sched, advd["fpa"], cur)
    return {"players": players, "meta": meta, "extra": extra, "cur": cur, "rt_as_of": rt["as_of"], "built": time.time()}

def base():
    return cached("base", public_ttl(), build_base)

def build_for(ctxs):
    """One user's view: shared base + their leagues' live rosters, projections and matchups."""
    B = base()
    if not B: raise RuntimeError("player data still loading")
    players = {k: dict(e) for k, e in B["players"].items()}   # base entries never hold per-league fields
    meta, cur = B["meta"], B["cur"]
    byname = {}
    for e in players.values(): byname.setdefault(norm(e["name"]), []).append(e)
    R = assemble_R(ctxs); unmatched = {}; lname = {}
    lastk = {}
    for e in players.values():
        if e["pos"] == "K": lastk.setdefault(norm(e["name"]).split()[-1], []).append(e)
    for lid, L in R["leagues"].items():
        for tname, lst in L["teams"].items():
            for n in lst:
                if n.startswith("DST:"): e = players.get("dst:" + n[4:]) or players.get("dst:" + fp_team(n[4:]))
                elif n.startswith("K:"):
                    c = lastk.get(norm(n[2:]), []); e = c[0] if len(c) == 1 else None
                else:
                    c = byname.get(norm(n), []); e = c[0] if c else None
                if not e:
                    k = "unranked:" + norm(n); e = players.setdefault(k, {"key": k, "name": n, "pos": "?", "unranked": True})
                    unmatched.setdefault(lid, []).append(n)
                e.setdefault("owners", {})[lid] = tname
                lname.setdefault(lid, {}).setdefault(tname, {})[n] = e
                R.setdefault("resolved", {}).setdefault(lid, {}).setdefault(tname, []).append(e["key"])
    try: attach_projections(players, R, cur, lname)
    except Exception as ex: STATUS["projections"] = {"ok": False, "error": str(ex)[:200], "fetched": _stamp()}
    else: STATUS["fp_proj"] = {"ok": True, "fetched": _stamp(), **FPP.status(cur)}
    return {**B["extra"], "cur_week": cur, "players": list(players.values()), "meta": meta, "rosters": R, "unmatched": unmatched,
            "rt_as_of": B["rt_as_of"], "status": {k: v for k, v in STATUS.items()}, "built": time.time()}

# ---------------------------------------------------------------------------
# Leagues per user. The browser keeps its own list (localStorage) and sends it on every
# request in the X-Leagues header: [{"id": leagueId, "tid": teamId, "season": 2026, "tok": "..."}].
#   - public league: read from ESPN with NO cookies
#   - private league: "tok" is the sealed cookie token from /api/connect (vault.py), only for that league
#   - Logan's own WAN/SLOPE: server env ESPN_S2/ESPN_SWID, only when the request carries his GM PIN
# Nothing here writes to ESPN.
# ---------------------------------------------------------------------------
MAX_LEAGUES = 8
FRIEND_TTL = 180          # seconds a friend's league data is shared before ESPN is asked again
ERR_TTL = 60              # remember "private"/"not found" briefly so bad IDs don't hammer ESPN
_lg_cache, _lg_locks = {}, {}

def _owner():
    """True only when the request carries Logan's valid GM PIN. No header = friend (no failure counted)."""
    if not (request.headers.get("X-GM-Pin") or "").strip(): return False
    return bool(_pin_ok())

def _int(v, lo=1, hi=10**12):
    try:
        v = int(str(v).strip())
        return v if lo <= v <= hi else None
    except Exception: return None

def _specs():
    raw = request.headers.get("X-Leagues")
    if raw is None: return None
    try: arr = json.loads(raw)
    except Exception: return []
    out = []
    for x in arr if isinstance(arr, list) else []:
        if not isinstance(x, dict): continue
        lid, tid, season = _int(x.get("id")), _int(x.get("tid"), 0, 10**6), _int(x.get("season") or ESPN.SEASON, 2018, 2100)
        if lid and tid is not None and season: out.append({"id": lid, "tid": tid, "season": season, "tok": str(x.get("tok") or "")[:4000] or None})
    return out[:MAX_LEAGUES]

def make_ctxs(owner=None):
    """-> list of league contexts {key, lid, tid, season, cookies, ctag, owner_league}."""
    owner = _owner() if owner is None else owner
    specs = _specs()
    if specs is None: specs = [{"id": v[0], "tid": v[1], "season": ESPN.SEASON, "tok": None} for v in ESPN.LEAGUES.values()] if owner else []
    out, seen = [], set()
    for sp in specs:
        mine = next((k for k, v in ESPN.LEAGUES.items() if v == (sp["id"], sp["tid"]) and sp["season"] == ESPN.SEASON), None)
        if owner and mine and not sp.get("tok"):
            ck = CARDS._cookies()
            c = {"key": mine, "lid": sp["id"], "tid": sp["tid"], "season": sp["season"], "cookies": ck, "ctag": "env" if ck else None, "owner_league": True}
        else:
            ck, bad = None, False
            if sp.get("tok"):
                ck = VAULT.open_token(sp["tok"], sp["id"]); bad = ck is None
            c = {"key": "L%d_%d" % (sp["id"], sp["tid"]) + ("" if sp["season"] == ESPN.SEASON else "_%d" % sp["season"]),
                 "lid": sp["id"], "tid": sp["tid"], "season": sp["season"], "cookies": ck,
                 "ctag": None if bad else (VAULT.tag(sp["tok"]) if ck else "pub"), "owner_league": False, "bad_token": bad}
        if c["key"] in seen: continue
        seen.add(c["key"]); out.append(c)
    return out

def league_raw(lid, season, cookies, ctag, ttl):
    """Cached raw ESPN league JSON, shared by everyone reading the same league the same way
    (public, one sealed token, or Logan's env cookies). Errors cached briefly."""
    key = "%s:%s:%s" % (lid, season, ctag)
    now = time.time()
    c = _lg_cache.get(key)
    if c and now - c[0] < (ERR_TTL if isinstance(c[1], ESPN.LeagueError) else ttl): pass
    else:
        lk = _lg_locks.setdefault(key, threading.Lock())
        with lk:
            c = _lg_cache.get(key)
            if not (c and time.time() - c[0] < (ERR_TTL if isinstance(c[1], ESPN.LeagueError) else ttl)):
                try: v = ESPN.fetch_league(lid, season, cookies)
                except ESPN.LeagueError as e: v = e
                except Exception: v = ESPN.LeagueError("espn", "Couldn't reach ESPN")
                if isinstance(v, ESPN.LeagueError) and c and not isinstance(c[1], ESPN.LeagueError) and v.code == "espn":
                    v = c[1]          # ESPN hiccup: keep showing the last good copy
                c = (time.time(), v); _lg_cache[key] = c
    if isinstance(c[1], ESPN.LeagueError): raise c[1]
    return c[1]

def _prune():
    now = time.time()
    for k, (t, _) in list(_lg_cache.items()):
        if now - t > 1800: _lg_cache.pop(k, None); _lg_locks.pop(k, None)
    with _lock:
        for k in [k for k, (t, _) in _cache.items() if k.startswith("u:") and now - t > 1800]: _cache.pop(k, None)

def team_list(d):
    members = {m["id"]: ("%s %s" % (m.get("firstName", ""), m.get("lastName", ""))).strip() or m.get("displayName", "") for m in d.get("members", [])}
    out = []
    for t in d.get("teams", []):
        o = (t.get("record") or {}).get("overall") or {}
        out.append({"id": t["id"], "name": ESPN.tname(t), "abbrev": t.get("abbrev"), "logo": t.get("logo"),
                    "managers": [members[x] for x in t.get("owners", []) if members.get(x)],
                    "record": "%d-%d" % (o.get("wins", 0), o.get("losses", 0)) + ("-%d" % o["ties"] if o.get("ties") else "")})
    st = d.get("settings") or {}
    return {"id": d.get("id"), "season": d.get("seasonId"), "name": st.get("name") or "ESPN League %s" % d.get("id"),
            "size": st.get("size") or len(out)}, out

ERR_TEXT = {"private": "This league is private. Connect your ESPN account to load it.",
            "bad_creds": "Your ESPN connection didn't work for this league (it may have expired). Reconnect ESPN.",
            "not_found": "We couldn't find that league for this season. Check the league ID or link.",
            "bad_team": "That team isn't in this league anymore. Pick your team again.",
            "espn": "ESPN isn't answering right now. Try again in a minute."}

def assemble_R(ctxs):
    """Rosters dict for these leagues (same shape the app always used) + per-league errors."""
    rf = None
    R = {"leagues": {}, "espn": {}, "errors": {}, "resolved": {}, "order": [c["key"] for c in ctxs], "as_of": _stamp()}
    for c in ctxs:
        k = c["key"]
        if c.get("owner_league"):
            rf = rf or rosters_file()
            b = rf["leagues"].get(k)
            L = None
            if c["ctag"]:
                try:
                    d = league_raw(c["lid"], c["season"], c["cookies"], "env", espn_ttl())
                    L = ESPN.convert(k, d, b, extra=True); L["team_ids"] = {ESPN.tname(t): t["id"] for t in d.get("teams", [])}
                except Exception: L = None
            if L: R["espn"][k] = {"live": True}
            else:
                L = copy.deepcopy(b); L["source"] = "cached"
                R["espn"][k] = {"live": False, "reason": "no credentials" if not c["ctag"] else "ESPN error"}
            L["league_id"], L["my_team_id"], L["short"] = c["lid"], c["tid"], {"WAN": "WAN", "SLOPE": "SLOPE"}.get(k, k)
            L["display"] = {"WAN": "WAN 2027", "SLOPE": "Slopeside"}.get(k, L.get("name") or k)
            R["leagues"][k] = L; R["as_of"] = rf.get("last_synced") or R["as_of"]
            continue
        if c.get("bad_token"):
            R["errors"][k] = {"code": "bad_creds", "msg": ERR_TEXT["bad_creds"], "id": c["lid"], "tid": c["tid"]}; continue
        try:
            d = league_raw(c["lid"], c["season"], c["cookies"], c["ctag"], FRIEND_TTL)
            if not any(t.get("id") == c["tid"] for t in d.get("teams", [])): raise ESPN.LeagueError("bad_team")
            info, _ = team_list(d)
            L = ESPN.convert(k, d, {"name": info["name"]}, extra=True, myid=c["tid"], me_label=None)
            L["name"] = info["name"]; L["display"] = info["name"]
            L["short"] = re.sub(r"[^A-Za-z0-9]", "", "".join(w[0] for w in info["name"].split() if w))[:5].upper() or "LG"
            L["private"] = bool(c["cookies"]); L["team_ids"] = {ESPN.tname(t): t["id"] for t in d.get("teams", [])}
            R["leagues"][k] = L; R["espn"][k] = {"live": True}
        except ESPN.LeagueError as e:
            R["errors"][k] = {"code": e.code, "msg": ERR_TEXT.get(e.code, ERR_TEXT["espn"]), "id": c["lid"], "tid": c["tid"]}
    return R

def _ukey(ctxs):
    return "u:build:" + hashlib.sha256(json.dumps([[c["key"], c["ctag"]] for c in ctxs]).encode()).hexdigest()[:20]

@app.route("/api/data")
def api_data():
    _prune()
    owner = _owner()
    ctxs = make_ctxs(owner)
    if not ctxs:
        return jsonify({"owner": owner, "need_setup": True, "rosters": {"leagues": {}, "errors": {}, "order": []}, "players": [], "cur_week": (base() or {}).get("cur")})
    k = _ukey(ctxs)
    ttl = espn_ttl() if owner else FRIEND_TTL
    if request.args.get("refresh"):
        with _lock: _cache.pop(k, None)
    d = cached(k, ttl, lambda: build_for(ctxs))
    if d is None: return jsonify({"ok": False, "error": "Still loading player data, try again in a moment."}), 503
    return jsonify({**d, "owner": owner, "brand": "Logan's Playbook" if owner else "Playbook"})

def _ctx():
    """League context for ?lg=KEY, only among the leagues this request is allowed to read."""
    ctxs = make_ctxs()
    lg = str(request.args.get("lg", "")).strip()
    c = next((x for x in ctxs if x["key"] == lg), None) or next((x for x in ctxs if x["key"].upper() == lg.upper()), None)
    return c, ctxs

@app.route("/api/league-teams")
def api_league_teams():
    """Team picker. ?id=<leagueId or ESPN league link>&season=2026 ; header X-League-Token for private."""
    raw = str(request.args.get("id", ""))
    m = re.search(r"leagueId=(\d+)", raw) or re.fullmatch(r"\s*(\d{1,12})\s*", raw)
    if not m: return jsonify({"ok": False, "code": "bad_id", "msg": "That doesn't look like an ESPN league link or ID. It's the number after leagueId= in your league's web address."}), 400
    lid = int(m.group(1))
    sm = re.search(r"seasonId=(\d{4})", raw)
    season = _int(request.args.get("season") or (sm.group(1) if sm else ESPN.SEASON), 2018, 2100) or ESPN.SEASON
    tm = re.search(r"teamId=(\d+)", raw)
    tok = (request.headers.get("X-League-Token") or "").strip() or None
    ck = VAULT.open_token(tok, lid) if tok else None
    if tok and not ck: return jsonify({"ok": False, "code": "bad_creds", "msg": ERR_TEXT["bad_creds"], "id": lid}), 401
    try: d = league_raw(lid, season, ck, VAULT.tag(tok) if ck else "pub", FRIEND_TTL)
    except ESPN.LeagueError as e:
        return jsonify({"ok": False, "code": e.code, "msg": ERR_TEXT.get(e.code, ERR_TEXT["espn"]), "id": lid, "season": season}), 401 if e.code in ("private", "bad_creds") else 404 if e.code == "not_found" else 502
    info, teams = team_list(d)
    return jsonify({"ok": True, "league": {**info, "season": season, "private": bool(ck)}, "teams": teams, "team_hint": int(tm.group(1)) if tm else None})


@app.route("/api/connect", methods=["POST"])
def api_connect():
    """Private league: check the user's espn_s2 + SWID against that one league (a read), then hand back a
    sealed token. The cookies are never logged, never stored in plain text, never sent back."""
    now = time.time(); ip = _ip()
    _cf[ip] = [t for t in _cf[ip] if now - t < 3600]
    if len(_cf[ip]) >= 15: return jsonify({"ok": False, "code": "slow_down", "msg": "Too many tries. Wait a bit and try again."}), 429
    b = request.get_json(silent=True) or {}
    raw = str(b.get("id", ""))
    m = re.search(r"leagueId=(\d+)", raw) or re.fullmatch(r"\s*(\d{1,12})\s*", raw)
    s2, swid = VAULT.clean_s2(b.get("espn_s2")), VAULT.clean_swid(b.get("swid"))
    if not m: return jsonify({"ok": False, "code": "bad_id", "msg": "Add the league link or ID first."}), 400
    if not s2: return jsonify({"ok": False, "code": "bad_s2", "msg": "That espn_s2 value doesn't look right. It's a long code (over 100 characters). Copy the whole Value."}), 400
    if not swid: return jsonify({"ok": False, "code": "bad_swid", "msg": "That SWID doesn't look right. It looks like {1A2B3C4D-....} with curly brackets."}), 400
    lid = int(m.group(1)); season = _int(b.get("season") or ESPN.SEASON, 2018, 2100) or ESPN.SEASON
    _cf[ip].append(now)
    tok = VAULT.seal(lid, s2, swid)
    try: d = league_raw(lid, season, {"espn_s2": s2, "SWID": swid}, VAULT.tag(tok), FRIEND_TTL)
    except ESPN.LeagueError as e:
        code = "bad_creds" if e.code in ("private", "bad_creds") else e.code
        msg = "ESPN didn't accept those codes for this league. Make sure you're signed in to ESPN as a member of this league, then copy both values again." if code == "bad_creds" else ERR_TEXT.get(code, ERR_TEXT["espn"])
        return jsonify({"ok": False, "code": code, "msg": msg}), 401 if code == "bad_creds" else 404 if code == "not_found" else 502
    info, teams = team_list(d)
    return jsonify({"ok": True, "token": tok, "league": {**info, "season": season, "private": True}, "teams": teams})

# ---------------------------------------------------------------------------
# "Playbook Connect" Chrome add-on (extension/playbook-connect). The add-on reads the friend's own espn_s2 + SWID
# cookies once, posts them here, and gets back one sealed token per league. Same rules as /api/connect: the codes
# are only used for reads, never logged, never stored, never sent back.
EXT_IDS = [x.strip() for x in os.environ.get("EXT_IDS", "aniopchmkabeloipmicldolcmpjmmggj").split(",") if x.strip()]
EXT_ORIGINS = {"chrome-extension://" + x for x in EXT_IDS}
FAN_URL = "https://fan.api.espn.com/apis/v2/fans/%s?displayHiddenPrefs=true&context=fantasy&useCookieAuth=true&source=fantasyapp-ios&featureFlags=challengeEntries"

def _ext_cors(resp):
    o = request.headers.get("Origin", "")
    if o in EXT_ORIGINS:
        resp.headers["Access-Control-Allow-Origin"] = o
        resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Access-Control-Max-Age"] = "600"
        resp.headers["Vary"] = "Origin"
    resp.headers["Cache-Control"] = "no-store"
    return resp

def espn_leagues(cookies, swid, season):
    """Best effort: ask ESPN's fan profile which fantasy football leagues this account is in (read only).
    ESPN doesn't document this, so any failure just means 'none found' and the friend picks/pastes a league."""
    try:
        r = ESPN.http_get(FAN_URL % urllib.parse.quote(swid), cookies=cookies, timeout=10)
        if r.status_code != 200: return []
        j = r.json()
    except Exception:
        return []
    out, seen = [], set()
    for pref in (j.get("preferences") or []):
        e = ((pref.get("metaData") or {}).get("entry") or {})
        if not e or str(e.get("abbrev") or "").upper() not in ("FFL", "") and e.get("gameId") not in (1, "1"): continue
        if e.get("seasonId") and int(e.get("seasonId")) != int(season): continue
        for g in (e.get("groups") or []):
            try: lid = int(g.get("groupId"))
            except (TypeError, ValueError): continue
            if lid in seen: continue
            seen.add(lid); out.append({"id": lid, "tid": e.get("entryId")})
    return out[:10]

@app.route("/api/ext/connect", methods=["POST", "OPTIONS"])
def api_ext_connect():
    o = request.headers.get("Origin")
    if request.method == "OPTIONS":
        return _ext_cors(app.response_class(status=204 if o in EXT_ORIGINS else 403))
    # Only the Playbook Connect add-on (or a same-site page) may call this.
    if o and o not in EXT_ORIGINS and o != request.host_url.rstrip("/"):
        return _ext_cors(jsonify({"ok": False, "code": "bad_origin", "msg": "This only works from the Playbook Connect add-on."})), 403
    now = time.time(); ip = _ip()
    _cf[ip] = [t for t in _cf[ip] if now - t < 3600]
    if len(_cf[ip]) >= 15: return _ext_cors(jsonify({"ok": False, "code": "slow_down", "msg": "Too many tries. Wait a bit and try again."})), 429
    b = request.get_json(silent=True) or {}
    s2, swid = VAULT.clean_s2(b.get("espn_s2")), VAULT.clean_swid(b.get("swid"))
    if not s2 or not swid:
        return _ext_cors(jsonify({"ok": False, "code": "no_cookies", "msg": "Couldn't find your ESPN sign-in. Sign in at fantasy.espn.com in this Chrome, then try again."})), 400
    _cf[ip].append(now)
    season = _int(b.get("season") or ESPN.SEASON, 2018, 2100) or ESPN.SEASON
    cookies = {"espn_s2": s2, "SWID": swid}
    want = []
    for x in (b.get("leagues") or [])[:10]:
        lid = _int((x or {}).get("id") if isinstance(x, dict) else x, 1, 10**12)
        if lid and lid not in [w["id"] for w in want]:
            want.append({"id": lid, "tid": _int(x.get("tid"), 0, 1000) if isinstance(x, dict) else None, "from": "you"})
    found = espn_leagues(cookies, swid, season) if b.get("discover", True) else []
    for f in found:
        if f["id"] not in [w["id"] for w in want]: want.append({**f, "from": "espn"})
    res = []
    for w in want[:10]:
        tok = VAULT.seal(w["id"], s2, swid)
        try: d = league_raw(w["id"], season, cookies, VAULT.tag(tok), FRIEND_TTL)
        except ESPN.LeagueError as e:
            code = "bad_creds" if e.code in ("private", "bad_creds") else e.code
            res.append({"id": w["id"], "ok": False, "code": code, "from": w["from"],
                        "msg": "ESPN says this account isn't in that league." if code == "bad_creds" else ERR_TEXT.get(code, ERR_TEXT["espn"])})
            continue
        info, teams = team_list(d)
        sw = swid.upper()
        mine = next((t["id"] for t in d.get("teams", []) if sw in [str(x).upper() for x in (t.get("owners") or [])]), None)
        if mine is None and w.get("tid") in [t["id"] for t in teams]: mine = w["tid"]
        me = next((t for t in teams if t["id"] == mine), None)
        res.append({"id": w["id"], "ok": True, "season": season, "name": info["name"], "size": info["size"], "from": w["from"],
                    "tid": mine, "team": me["name"] if me else None, "teams": [{"id": t["id"], "name": t["name"]} for t in teams], "tok": tok})
    s2 = swid = cookies = None     # drop the codes now; only sealed tokens leave this function
    return _ext_cors(jsonify({"ok": True, "season": season, "searched_espn": bool(b.get("discover", True)), "found_on_espn": len(found), "leagues": res}))

@app.route("/api/player/<pid>")
def api_player(pid):
    """ESPN-style player card. Read-only ESPN GETs, cached."""
    try: pid = int(pid)
    except ValueError: return jsonify({"ok": False, "error": "bad id"}), 400
    ctx, ctxs = _ctx()
    try:
        wk = CARDS.week_now(cached)
        R = assemble_R([ctx]) if ctx else {"leagues": {}}
        if ctx and ctx["key"] not in R["leagues"]: ctx = None
        c = CARDS.card(cached, pid, ctx, R, wk, CARDS.pro_sched(cached))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:160]}), 502
    if not c: return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify({"ok": True, "card": c})

@app.route("/api/waivers")
def api_waivers():
    """Live waiver wire for one league: every available player (FA + waivers) with ESPN projections/ownership."""
    ctx, _ = _ctx()
    if not ctx: return jsonify({"ok": False, "error": "league not loaded"}), 404
    try:
        wk = CARDS.week_now(cached)
        R = assemble_R([ctx])
        if ctx["key"] not in R["leagues"]:
            e = R["errors"].get(ctx["key"]) or {}
            return jsonify({"ok": False, "error": e.get("msg") or "league not available", "code": e.get("code")}), 401
        return jsonify({"ok": True, **CARDS.waivers(cached, ctx, R, wk, CARDS.pro_sched(cached))})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:160]}), 502

@app.route("/api/scoreboard")
def api_scoreboard():
    def f():
        d = requests.get("https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard", timeout=15).json()
        out = []
        for ev in d.get("events", []):
            comp = ev["competitions"][0]; teams = []
            for c in comp["competitors"]:
                teams.append({"abbr": c["team"]["abbreviation"], "name": c["team"].get("shortDisplayName"), "score": c.get("score"), "home": c["homeAway"] == "home",
                              "record": (c.get("records") or [{}])[0].get("summary"), "logo": c["team"].get("logo")})
            out.append({"id": ev["id"], "date": ev["date"], "status": ev["status"]["type"]["shortDetail"], "state": ev["status"]["type"]["state"],
                        "teams": teams, "broadcast": ",".join(b for x in comp.get("broadcasts", []) for b in x.get("names", []))})
        return {"week": d.get("week", {}).get("number"), "games": out}
    return jsonify(cached("espn", public_ttl("scores"), f) or {"games": [], "error": STATUS.get("espn")})

NEWS_PATH = os.path.join(BASE, "cache", "news.json")
def news():
    def f():
        import xml.etree.ElementTree as ET
        old = json.load(open(NEWS_PATH)) if os.path.exists(NEWS_PATH) else []
        seen = {x["id"] for x in old}
        r = ET.fromstring(requests.get("https://www.rotowire.com/rss/news.php?sport=NFL", headers=UA, timeout=15).content)
        for it in r.iter("item"):
            g = it.findtext("guid")
            if g in seen: continue
            t = it.findtext("title") or ""
            old.append({"id": g, "player": t.split(":")[0].strip(), "title": t, "desc": (it.findtext("description") or "").split("Visit RotoWire")[0].strip(),
                        "link": it.findtext("link"), "date": it.findtext("pubDate"), "src": "RotoWire"})
        old = old[-400:]; json.dump(old, open(NEWS_PATH, "w")); return old
    return cached("news", public_ttl("news"), f)

@app.route("/api/news")
def api_news(): return jsonify({"items": list(reversed(news() or [])), "status": STATUS.get("news")})

_last_global = {"t": 0}
@app.route("/api/refresh", methods=["POST", "GET"])
def api_refresh():
    owner = _owner(); ctxs = make_ctxs(owner)
    # Everyone: drop this user's league copies so the next load re-reads ESPN.
    for c in ctxs:
        for k in [k for k in _lg_cache if k.startswith("%s:%s:" % (c["lid"], c["season"]))]:
            if time.time() - _lg_cache[k][0] > 20: _lg_cache.pop(k, None)
    with _lock:
        _cache.pop(_ukey(ctxs), None)
        for k in [k for k in _cache if k.startswith("u:lpool:") and any(k.startswith("u:lpool:%s:" % c["lid"]) for c in ctxs)]: _cache.pop(k, None)
    # Shared sources (FantasyPros etc.) are global: only the owner can force them, at most every 5 minutes.
    if not owner or time.time() - _last_global["t"] < 300:
        return jsonify({"ok": True, "scope": "leagues", "updated": _stamp()})
    _last_global["t"] = time.time()
    with _lock:
        for k in [k for k in _cache if not k.startswith("u:")]: _cache.pop(k, None)
    for f in ("sleeper_slim.json", "sched.json"):
        fp = os.path.join(BASE, "cache", f)
        if os.path.exists(fp): os.utime(fp, (0, 0))
    d = build_base()
    with _lock: _cache["base"] = (time.time(), d)
    news()
    return jsonify({"ok": True, "updated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "status": STATUS})

@app.route("/api/status")
def api_status(): return jsonify(STATUS)

@app.route("/api")
def api_docs():
    return """<html><body style="background:#0b0f17;color:#e8edf5;font-family:sans-serif;padding:20px;max-width:800px">
<h1>Playbook JSON API</h1><p>Nothing here writes to ESPN. ESPN is only ever read.</p>
<h3>GET /api/data</h3><p>Everything the app uses. Add <code>?refresh=1</code> to force a blocking rebuild. Otherwise public sources (FantasyPros, FantasyCalc, Sleeper, schedule, news, build) use a 15 min TTL (5 min for scores/news during Thu/Sun/Mon NFL windows). Expired cache is served immediately and refreshed in a background thread. nflverse adv stays ~6 h.</p>
<ul><li><b>players[]</b>: key, name, pos, team, bye, opp, own (FP avg roster %), wk {rank,pos_rank,pts (FP PPR projection),best,worst,avg,std,grade,delta}, ros {overall,pos_rank,pos_num,...}, rt (RotoTrade seed), fc12/fc16 (FantasyCalc), inj/inj_part/inj_note (Sleeper), adv {g,ppg,tgt_g,ts,ay_g,touch_g,snap,last3[],trend,trend_val,tot{}} (nflverse), cuff (Sleeper depth chart RB2), owners {WAN|SLOPE: team}, <b>fpp</b> (FantasyPros Week PPR projection from FP projection pages; absent = no match), <b>ep</b>/<b>ea</b> {league: ESPN league-scored week projection / actual}, espn_proj (ESPN default PPR, public pool)</li><li><b>rosters.leagues[lg].matchup.proj2</b>: {me,opp: {espn, fp, fp_pre, fp_missing[], n}} starters only; played players count actual points</li>
<li><b>fpa</b>: {pos:{team:{fpa,rank}}} PPR points allowed per game (rank 32 = most allowed)</li><li><b>sched</b>: {team:{week:"vs OPP"|"@OPP"}} weeks cur..17 from ESPN</li>
<li><b>sos</b>: {team:{pos:{ros|wan_po|slope_po:{avg_rank,grade}}}}</li><li><b>team_stats</b>: {team:{off_ppg,give_g}}</li>
<li><b>rosters</b>, <b>meta</b>, <b>status</b> (per-source ok/fetched), cur_week, adv_season, adv_thru, rt_as_of, unmatched</li></ul>
<h3>GET /api/player/&lt;espnId&gt;?lg=WAN|SLOPE</h3><p>ESPN player card: header, injury, % rostered/started (+/-), ownership in that league, week projection, season/proj stat rows, 18-week game log, remaining schedule, outlook, fantasy news. League-scored when ESPN cookies are set on the server.</p>
<h3>GET /api/waivers?lg=WAN|SLOPE</h3><p>Live waiver wire: all available players (FA + WA with clear date) with ESPN projection, % rostered and change, % started, season pts/avg, last week, opponent; plus waiver order, FAAB and next processing time.</p>
<h3>GET /api/scoreboard</h3><p>Live ESPN NFL scoreboard (cached 60 s): week, games[] {status,state,teams[{abbr,score,record,logo,home}],broadcast}.</p>
<h3>GET /api/status</h3><p>Source health only.</p>
<h3>POST /api/request</h3><p>One-tap GM request (header X-GM-Pin). Body {league: WAN|SLOPE, type: lineup|waiver|trade|chat, text, details}. Delivered to the GM bot (routine webhook if configured, always queued). Never touches ESPN.</p>
<h3>GET /api/requests?token=GM_TOKEN&amp;status=pending|all&amp;since=ts</h3><p>Bot queue. POST /api/requests/&lt;id&gt;/ack?token=… {status: in_progress|done|failed|declined|needs_ok, note}.</p></body></html>"""

# ---------------------------------------------------------------------------
# One-tap GM requests. The app NEVER writes to ESPN. A tap only delivers a
# request to the Fantasy Football GM bot, which verifies and makes the move.
#   GM_PIN          passcode a device must send (X-GM-Pin) to submit requests
#   GM_TOKEN        shared secret the bot uses to poll/ack the queue
#   GM_WEBHOOK_URL  optional: Grok Bot routine webhook URL (instant push)
#   GM_WEBHOOK_KEY  optional: that routine's key, sent as Authorization: Bearer
# ---------------------------------------------------------------------------
import hmac, uuid, collections, copy
_cf = collections.defaultdict(list)       # ip -> [ts] of /api/connect tries
REQ_PATH = os.path.join(BASE, "cache", "requests.jsonl")
_req_lock = threading.Lock()
_fails = collections.defaultdict(list)   # ip -> [ts]
_fails_all = []
REQ_TYPES = ("lineup", "waiver", "trade", "chat")
REQ_LEAGUES = ("WAN", "SLOPE", "")

def _load_reqs():
    out = collections.OrderedDict()
    if os.path.exists(REQ_PATH):
        for line in open(REQ_PATH):
            try:
                r = json.loads(line)
                if r.get("_upd"): out.get(r["id"], {}).update({k: v for k, v in r.items() if k not in ("_upd", "id")})
                else: out[r["id"]] = r
            except Exception: pass
    return out
REQS = _load_reqs()

def _append(rec):
    with open(REQ_PATH, "a") as f: f.write(json.dumps(rec) + "\n")

def _ip():
    return (request.headers.get("X-Forwarded-For") or request.remote_addr or "?").split(",")[0].strip()

def _locked_out():
    now = time.time(); ip = _ip()
    _fails[ip] = [t for t in _fails[ip] if now - t < 3600]
    _fails_all[:] = [t for t in _fails_all if now - t < 3600]
    return len(_fails[ip]) >= 8 or len(_fails_all) >= 40

def _pin_ok():
    pin = os.environ.get("GM_PIN", "")
    got = (request.headers.get("X-GM-Pin") or "").strip()
    if not pin: return None  # not configured: fail closed
    if _locked_out(): return False
    if got and hmac.compare_digest(got.encode(), pin.encode()): return True
    now = time.time(); _fails[_ip()].append(now); _fails_all.append(now)
    return False

def _token_ok():
    tok = os.environ.get("GM_TOKEN", "")
    got = request.args.get("token") or (request.headers.get("Authorization") or "").replace("Bearer ", "", 1).strip()
    return bool(tok) and bool(got) and hmac.compare_digest(got.encode(), tok.encode())

def _public(r):
    return {k: r.get(k) for k in ("id", "at", "league", "type", "text", "status", "note", "delivered", "updated")}

def _push_webhook(r):
    url, key = os.environ.get("GM_WEBHOOK_URL", ""), os.environ.get("GM_WEBHOOK_KEY", "")
    if not (url.startswith("https://") and key): return "queued"
    body = {"source": "logans-playbook", "kind": "gm_request", "request": _public(r), "details": r.get("details"),
            "ack": "POST /api/requests/%s/ack with GM_TOKEN when done" % r["id"]}
    try:
        k = key if key.lower().startswith("bearer ") else "Bearer " + key
        resp = requests.post(url, json=body, headers={"Authorization": k, "Content-Type": "application/json"}, timeout=8)
        return "webhook" if resp.status_code < 300 else "queued (webhook %d)" % resp.status_code
    except Exception as e:
        return "queued (webhook error)"

@app.route("/api/pin-check", methods=["POST"])
def api_pin_check():
    ok = _pin_ok()
    if ok is None: return jsonify({"ok": False, "error": "not_configured"}), 503
    return (jsonify({"ok": True}), 200) if ok else (jsonify({"ok": False, "error": "bad_pin"}), 401)

@app.route("/api/request", methods=["POST"])
def api_request():
    ok = _pin_ok()
    if ok is None: return jsonify({"ok": False, "error": "not_configured"}), 503
    if not ok: return jsonify({"ok": False, "error": "bad_pin"}), 401
    b = request.get_json(silent=True) or {}
    typ = str(b.get("type", "chat")).lower(); lg = str(b.get("league", "")).upper()
    text = str(b.get("text", "")).strip()[:2000]
    if typ not in REQ_TYPES or lg not in REQ_LEAGUES or not text:
        return jsonify({"ok": False, "error": "bad_request"}), 400
    det = b.get("details") if isinstance(b.get("details"), dict) else {}
    det = det if len(json.dumps(det)) <= 4000 else {"truncated": True}
    now = time.time()
    with _req_lock:
        for r in reversed(list(REQS.values())[-20:]):  # double-tap protection
            if r["text"] == text and now - r["ts"] < 90 and r.get("status") == "pending":
                return jsonify({"ok": True, "id": r["id"], "delivered": r.get("delivered"), "dup": True})
        r = {"id": uuid.uuid4().hex[:10], "ts": now, "at": _stamp(), "league": lg, "type": typ, "text": text,
             "details": det, "status": "pending", "note": None, "ip": _ip()}
        REQS[r["id"]] = r
        while len(REQS) > 500: REQS.popitem(last=False)
        _append(r)
    r["delivered"] = _push_webhook(r)
    with _req_lock: _append({"_upd": 1, "id": r["id"], "delivered": r["delivered"]})
    return jsonify({"ok": True, "id": r["id"], "delivered": r["delivered"]})

@app.route("/api/my-requests", methods=["POST"])
def api_my_requests():
    ok = _pin_ok()
    if not ok: return jsonify({"ok": False}), 401 if ok is False else 503
    ids = (request.get_json(silent=True) or {}).get("ids") or []
    with _req_lock: out = [_public(REQS[i]) for i in ids[:50] if i in REQS]
    return jsonify({"ok": True, "requests": out})

@app.route("/api/requests")
def api_requests():
    if not _token_ok(): return jsonify({"ok": False}), 401
    st = request.args.get("status", "pending"); since = float(request.args.get("since", 0) or 0)
    with _req_lock:
        out = [{**_public(r), "ts": r["ts"], "details": r.get("details")} for r in REQS.values()
               if (st == "all" or r.get("status") == st) and r["ts"] > since]
    return jsonify({"ok": True, "now": time.time(), "requests": out})

@app.route("/api/requests/<rid>/ack", methods=["POST"])
def api_ack(rid):
    if not _token_ok(): return jsonify({"ok": False}), 401
    b = request.get_json(silent=True) or {}
    st = str(b.get("status", "done"))
    if st not in ("in_progress", "done", "failed", "declined", "needs_ok"): return jsonify({"ok": False, "error": "bad_status"}), 400
    with _req_lock:
        r = REQS.get(rid)
        if not r: return jsonify({"ok": False, "error": "not_found"}), 404
        r.update({"status": st, "note": str(b.get("note") or "")[:500] or None, "updated": _stamp()})
        _append({"_upd": 1, "id": rid, "status": r["status"], "note": r["note"], "updated": r["updated"]})
    return jsonify({"ok": True, "request": _public(r)})

@app.route("/api/config")
def api_config():
    p = COACH.provider()
    return jsonify({"version": APP_VERSION, "help_email": os.environ.get("HELP_EMAIL", "").strip() or None,
                    "coach_ai": p["name"] if p else None, "season": ESPN.SEASON})

@app.route("/api/coach", methods=["POST"])
def api_coach():
    """Read-only Coach. Body {question, lg, history}. Uses this request's own leagues only."""
    b = request.get_json(silent=True) or {}
    q = str(b.get("question") or "").strip()[:500]
    if not q: return jsonify({"ok": False, "error": "Ask a question first."}), 400
    if not COACH.user_ok(_ip()): return jsonify({"ok": False, "error": "You've asked a lot this hour. Take a breather and try again soon."}), 429
    owner = _owner(); ctxs = make_ctxs(owner)
    lg = str(b.get("lg") or "")
    if b.get("leagueId") is not None and b.get("teamId") is not None:
        lg = next((c["key"] for c in ctxs if c["lid"] == _int(b.get("leagueId")) and c["tid"] == _int(b.get("teamId"), 0)), lg)
    if not ctxs: return jsonify({"ok": False, "error": "Add your ESPN league first."}), 400
    d = cached(_ukey(ctxs), espn_ttl() if owner else FRIEND_TTL, lambda: build_for(ctxs))
    if not d: return jsonify({"ok": False, "error": "Still loading data, try again in a moment."}), 503
    if lg not in d["rosters"]["leagues"]: lg = next(iter(d["rosters"].get("order") or []), None)
    if lg not in d["rosters"]["leagues"]: return jsonify({"ok": False, "error": "That league isn't loaded."}), 404
    hist = b.get("history") if isinstance(b.get("history"), list) else []
    gm = b.get("mode") == "gm"     # Ask GM depth for anyone, always scoped to this request's own league; never the owner webhook
    try: out = COACH.answer(q, d, lg, hist[-6:], _ukey(ctxs), gm, b.get("brief") if gm else None)
    except Exception: return jsonify({"ok": False, "error": "Coach hit a snag. Try asking another way."}), 500
    return jsonify({"ok": True, "league": lg, **out})

FEED_PATH = os.path.join(BASE, "cache", "feedback.jsonl")
_fb = collections.defaultdict(list)
@app.route("/api/feedback", methods=["POST"])
def api_feedback():
    now = time.time(); ip = _ip()
    _fb[ip] = [t for t in _fb[ip] if now - t < 3600]
    if len(_fb[ip]) >= 5: return jsonify({"ok": False, "error": "Thanks! You've sent a few already, try again later."}), 429
    b = request.get_json(silent=True) or {}
    msg = str(b.get("message") or "").strip()[:2000]
    if len(msg) < 3: return jsonify({"ok": False, "error": "Type a message first."}), 400
    if os.path.exists(FEED_PATH) and os.path.getsize(FEED_PATH) > 2_000_000: return jsonify({"ok": False, "error": "Feedback box is full right now."}), 507
    _fb[ip].append(now)
    rec = {"at": _stamp(), "topic": str(b.get("topic") or "")[:40], "message": msg, "contact": str(b.get("contact") or "")[:120],
           "app": str(b.get("app") or "")[:60]}
    with open(FEED_PATH, "a") as f: f.write(json.dumps(rec) + "\n")
    return jsonify({"ok": True})

@app.route("/api/feedback/list")
def api_feedback_list():
    if not _token_ok(): return jsonify({"ok": False}), 401
    out = [json.loads(l) for l in open(FEED_PATH)] if os.path.exists(FEED_PATH) else []
    return jsonify({"ok": True, "items": out[-200:]})

@app.route("/manifest.json")
def manifest(): return send_from_directory(app.static_folder,"manifest.json",mimetype="application/manifest+json")
@app.route("/sw.js")
def sw(): return send_from_directory(app.static_folder,"sw.js",mimetype="application/javascript")
@app.route("/")
def index(): return send_from_directory(app.static_folder, "index.html")

def _warm():
    try:
        wk = CARDS.week_now(cached); CARDS.pro_sched(cached); CARDS.pos_ranks(cached, wk)
    except Exception: pass
threading.Thread(target=_warm, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8765)), threaded=True)
