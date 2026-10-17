"""READ-ONLY ESPN player cards + waiver pool. GET only; never POSTs to ESPN.
League endpoints use ESPN_S2/ESPN_SWID from env (never logged/returned); public endpoints need no auth."""
import json, re, time, datetime, requests
import espn as ESPN
import fpts as F

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36", "Accept": "application/json"}
G = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/%d" % ESPN.SEASON
LG = G + "/segments/0/leagues/%d"
POS = ESPN.POS
PRO = ESPN.PRO
INJ = {"QUESTIONABLE": "Q", "DOUBTFUL": "D", "OUT": "O", "INJURY_RESERVE": "IR", "SUSPENSION": "SSPD", "DAY_TO_DAY": "DTD"}
INJL = {"Q": "Questionable", "D": "Doubtful", "O": "Out", "IR": "Injured Reserve", "SSPD": "Suspended", "DTD": "Day-to-Day"}
ELIG = {0: "QB", 2: "RB", 4: "WR", 6: "TE", 16: "D/ST", 17: "K", 23: "FLEX"}
DAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]

def _get(url, flt=None, cookies=None, timeout=25):
    h = dict(UA)
    if flt is not None: h["X-Fantasy-Filter"] = json.dumps(flt)
    r = requests.get(url, headers=h, cookies=cookies, timeout=timeout, allow_redirects=False)
    if r.status_code != 200: raise RuntimeError("ESPN HTTP %d" % r.status_code)
    return r.json()

def _cookies():
    c = ESPN.creds()
    return {"espn_s2": c[0], "SWID": c[1]} if c else None

def week_now(cached):
    def f():
        d = _get(G)
        return int((d.get("currentScoringPeriod") or {}).get("id") or 1)
    return cached("espn_week", 1800, f) or 1

def pro_sched(cached):
    """{proTeamId: {abbrev, bye, games: {period: {opp, home, date, game_id}}}} from public proTeamSchedules."""
    def f():
        d = _get(G + "?view=proTeamSchedules_wl")
        out = {}
        for t in d["settings"]["proTeams"]:
            if not t.get("id"): continue
            gm = {}
            for per, gl in (t.get("proGamesByScoringPeriod") or {}).items():
                for g in gl:
                    home = g["homeProTeamId"] == t["id"]
                    gm[per] = {"opp": PRO.get(g["awayProTeamId"] if home else g["homeProTeamId"], "?"), "home": home,
                               "date": g.get("date"), "game_id": g.get("id")}
            out[str(t["id"])] = {"abbrev": PRO.get(t["id"], t.get("abbrev")), "bye": t.get("byeWeek"), "games": gm}
        return out
    return cached("pro_sched", 21600, f) or {}

def _stat_ids(wk):
    return ["002026", "102026", "002025", "11%d%d" % (ESPN.SEASON, wk), "10%d%d" % (ESPN.SEASON, wk)]

def slim(p, wk, extra=None):
    """Normalize an ESPN player object (global or league) into what the app needs."""
    pos = POS.get(p.get("defaultPositionId"), "?")
    own = p.get("ownership") or {}
    games, season, proj, sproj, last = {}, None, None, None, None
    for s in p.get("stats") or []:
        sid, per, src, split = s.get("id", ""), s.get("scoringPeriodId"), s.get("statSourceId"), s.get("statSplitTypeId")
        if src == 0 and split == 1 and per and s.get("seasonId", ESPN.SEASON) == ESPN.SEASON:
            games[per] = {"pts": F.best(s, pos), "stats": s.get("stats") or {}, "game_id": s.get("externalId")}
        elif src == 0 and split == 0 and s.get("seasonId") == ESPN.SEASON:
            season = s
        elif src == 1 and split == 1 and per == wk and s.get("seasonId", ESPN.SEASON) == ESPN.SEASON:
            proj = s
        elif src == 1 and split == 0 and s.get("seasonId") == ESPN.SEASON:
            sproj = s
    tot = round(sum(g["pts"] for g in games.values() if g["pts"] is not None), 2) if games else None
    gp = len([g for g in games.values() if g["stats"]])
    lw = games.get(wk - 1)
    out = {"id": p.get("id"), "name": p.get("fullName"), "first": p.get("firstName"), "last": p.get("lastName"), "pos": pos,
           "team": PRO.get(p.get("proTeamId"), "FA"), "pro_id": p.get("proTeamId"), "jersey": p.get("jersey"),
           "inj": INJ.get(p.get("injuryStatus") or ""), "injured": p.get("injured"),
           "elig": [ELIG[e] for e in (p.get("eligibleSlots") or []) if e in ELIG],
           "own": round(own["percentOwned"], 1) if own.get("percentOwned") is not None else None,
           "own_chg": round(own["percentChange"], 2) if own.get("percentChange") is not None else None,
           "start": round(own["percentStarted"], 1) if own.get("percentStarted") is not None else None,
           "proj": F.best(proj, pos) if proj and (proj.get("stats") or proj.get("appliedTotal")) else None,
           "season_pts": tot, "gp": gp, "avg": round(tot / gp, 2) if tot is not None and gp else None,
           "last_pts": lw["pts"] if lw else None, "last_wk": wk - 1 if lw else None,
           "news_ts": p.get("lastNewsDate")}
    if extra is not None:
        extra.update({"games": games, "season": season, "proj_raw": proj, "sproj": sproj})
    return out

def global_pool(cached, wk):
    """Top ~750 active players by % rostered, public endpoint (no cookies)."""
    def f():
        flt = {"filterActive": {"value": True}, "filterSlotIds": {"value": [0, 2, 4, 6, 16, 17]},
               "sortPercOwned": {"sortPriority": 1, "sortAsc": False}, "limit": 750,
               "filterStatsForTopScoringPeriodIds": {"value": 17, "additionalValue": _stat_ids(wk)}}
        d = _get(G + "/players?scoringPeriodId=%d&view=kona_player_info" % wk, flt, timeout=40)
        return [slim(p, wk) for p in d]
    return cached("gpool:%d" % wk, 900, f) or []

def league_pool(cached, lkey, wk):
    """Available players (FA + waivers) in one league with league scoring + waiver dates. Needs cookies."""
    ck = _cookies()
    if not ck: return None
    lid = ESPN.LEAGUES[lkey][0]
    def f():
        flt = {"players": {"filterStatus": {"value": ["FREEAGENT", "WAIVERS"]}, "filterSlotIds": {"value": [0, 2, 4, 6, 16, 17, 23]},
                           "sortPercOwned": {"sortPriority": 1, "sortAsc": False}, "limit": 400,
                           "filterStatsForTopScoringPeriodIds": {"value": 17, "additionalValue": _stat_ids(wk)}}}
        d = _get(LG % lid + "?scoringPeriodId=%d&view=kona_player_info" % wk, flt, ck, timeout=40)
        out = []
        for e in d.get("players") or []:
            p = e.get("player") or {}
            s = slim(p, wk)
            st = (e.get("status") or "").upper()
            s["type"] = "WA" if st == "WAIVERS" else "FA"
            wd = e.get("waiverProcessDate")
            s["waiver_until"] = wd
            out.append(s)
        return out
    return cached("lpool:%s:%d" % (lkey, wk), 180, f)

def rostered_ids(L):
    return {int(v) for v in (L.get("pids") or {}).values() if v is not None}

def available(cached, lkey, R, wk):
    """Returns (players, source). Prefer the league call (exact FA/WA status); fall back to global minus rosters."""
    L = R["leagues"][lkey]
    lp = None
    try: lp = league_pool(cached, lkey, wk)
    except Exception: lp = None
    if lp:
        return lp, "espn-league"
    ids = rostered_ids(L)
    names = {n for lst in L.get("teams", {}).values() for n in lst}
    out = []
    for p in global_pool(cached, wk):
        nm = ("DST:" + p["team"]) if p["pos"] == "DST" else p["name"]
        if p["id"] in ids or nm in names or p["team"] == "FA": continue
        q = dict(p); q["type"] = "FA"; q["waiver_until"] = None; q["type_unverified"] = True
        out.append(q)
    return out, "espn-global"

def next_waiver(W):
    """Next processing time from league settings (days + hour, US/Eastern)."""
    days, hr = W.get("process_days") or [], W.get("process_hour")
    if not days: return None
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("America/New_York")
    except Exception:
        return None
    now = datetime.datetime.now(tz)
    for i in range(0, 8):
        d = now + datetime.timedelta(days=i)
        if DAYS[d.weekday()] in [x.upper() for x in days]:
            t = d.replace(hour=int(hr or 0), minute=0, second=0, microsecond=0)
            if t > now: return int(t.timestamp() * 1000)
    return None

def waivers(cached, lkey, R, wk, sched):
    L = R["leagues"][lkey]
    pl, src = available(cached, lkey, R, wk)
    pl = [p for p in pl if p["pos"] in ("QB", "RB", "WR", "TE", "K", "DST")]
    for p in pl:
        g = (sched.get(str(p["pro_id"])) or {}).get("games", {}).get(str(wk))
        p["bye"] = (sched.get(str(p["pro_id"])) or {}).get("bye")
        p["opp"] = (("vs " if g["home"] else "@") + g["opp"]) if g else ("BYE" if p.get("pro_id") else None)
        p["kick"] = g.get("date") if g else None
    # position rank by season fantasy points (among the public top-750 pool + these)
    W = dict(L.get("waiver") or {})
    W["next_ms"] = next_waiver(W)
    return {"league": lkey, "week": wk, "source": src, "players": pl, "waiver": W, "pos_rank": pos_ranks(cached, wk),
            "fetched": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())}

def pos_ranks(cached, wk):
    def f():
        gp = global_pool(cached, wk); out = {}
        for pos in ("QB", "RB", "WR", "TE", "K", "DST"):
            ps = sorted([p for p in gp if p["pos"] == pos and p["season_pts"] is not None], key=lambda p: -p["season_pts"])
            for i, p in enumerate(ps): out[str(p["id"])] = i + 1
        return out
    return cached("posrank:%d" % wk, 900, f) or {}

# ---------------- player card ----------------
COLS = {
    "QB": [("C/A", None), ("YDS", "3"), ("TD", "4"), ("INT", "20"), ("CAR", "23"), ("YDS", "24"), ("TD", "25")],
    "RB": [("CAR", "23"), ("YDS", "24"), ("TD", "25"), ("REC", "53"), ("YDS", "42"), ("TD", "43"), ("TAR", "58")],
    "WR": [("REC", "53"), ("YDS", "42"), ("AVG", None), ("TD", "43"), ("TAR", "58"), ("CAR", "23"), ("YDS", "24"), ("TD", "25")],
    "TE": [("REC", "53"), ("YDS", "42"), ("AVG", None), ("TD", "43"), ("TAR", "58"), ("CAR", "23"), ("YDS", "24"), ("TD", "25")],
    "K": [("FG", None), ("FG 50+", "74"), ("FG 40-49", "77"), ("XP", None), ("MISS", "85")],
    "DST": [("SACK", "99"), ("INT", "95"), ("FR", "96"), ("TD", "94"), ("SAF", "98"), ("PA", "120"), ("YA", "127")],
}

def statrow(stats, pos, proj=False):
    if not stats: return None
    v = lambda k: float(stats.get(k, 0) or 0)
    out = []
    for lab, k in COLS.get(pos, []):
        if lab == "C/A": x = "%d/%d" % (round(v("1")), round(v("0")))
        elif lab == "AVG": x = round(v("42") / v("53"), 1) if v("53") else 0.0
        elif lab == "FG": x = "%d/%d" % (round(v("83")), round(v("84")))
        elif lab == "XP": x = "%d/%d" % (round(v("86")), round(v("87")))
        else:
            x = v(k); x = round(x, 1) if proj and abs(x - round(x)) > .05 and x < 50 else int(round(x))
        out.append(x)
    return out

def fantasy_news(cached, pid, n=12):
    def f():
        d = _get("https://site.api.espn.com/apis/fantasy/v2/games/ffl/news/players?playerId=%s&limit=%d" % (pid, n))
        out = []
        for x in d.get("feed") or []:
            st = re.sub(r"<[^>]+>", " ", x.get("story") or ""); st = re.sub(r"\s+", " ", st).strip()
            if len(st) > 420: st = st[:417].rsplit(" ", 1)[0] + "…"
            out.append({"headline": x.get("headline"), "story": st or None, "published": x.get("published"),
                        "type": x.get("type"), "premium": str(x.get("premium")).lower() == "true",
                        "video": bool((x.get("video") or [])) or x.get("type") == "Media",
                        "image": ((x.get("images") or [{}])[0] or {}).get("url"),
                        "link": (((x.get("links") or {}).get("web") or {}).get("href"))})
        return out
    return cached("fnews:%s" % pid, 600, f) or []

def athlete(cached, pid):
    def f():
        a = _get("https://site.web.api.espn.com/apis/common/v3/sports/football/nfl/athletes/%s" % pid).get("athlete", {})
        inj = (a.get("injuries") or [{}])
        inj = inj[0] if inj else {}
        return {"age": a.get("age"), "ht": a.get("displayHeight"), "wt": a.get("displayWeight"), "college": (a.get("college") or {}).get("name"),
                "exp": a.get("displayExperience"), "draft": a.get("displayDraft"), "dob": a.get("displayDOB"),
                "team_name": (a.get("team") or {}).get("displayName"), "status": (a.get("status") or {}).get("name"),
                "inj_type": (inj.get("type") or {}).get("description") if isinstance(inj.get("type"), dict) else inj.get("type"),
                "inj_detail": " ".join(str(x) for x in [((inj.get("details") or {}).get("type")), ((inj.get("details") or {}).get("returnDate"))] if x) or None,
                "inj_status": inj.get("status")}
    if int(pid) < 0: return {}
    try: return cached("ath:%s" % pid, 3600, f) or {}
    except Exception: return {}

def gamelog_events(cached, pid):
    """Real-NFL results per game (score, W/L) from the public site API."""
    def f():
        d = _get("https://site.web.api.espn.com/apis/common/v3/sports/football/nfl/athletes/%s/gamelog" % pid)
        return {k: {"result": v.get("gameResult"), "score": v.get("score"), "week": v.get("week")} for k, v in (d.get("events") or {}).items()}
    if int(pid) < 0: return {}
    try: return cached("glog:%s" % pid, 900, f) or {}
    except Exception: return {}

def card(cached, pid, lkey, R, wk, sched):
    pid = int(pid)
    def f():
        flt = {"filterIds": {"value": [pid]}, "filterStatsForTopScoringPeriodIds": {"value": 18, "additionalValue": _stat_ids(wk)}}
        d = _get(G + "/players?scoringPeriodId=%d&view=kona_playercard" % wk, flt)
        if not d: raise RuntimeError("player not found")
        return d[0]
    from concurrent.futures import ThreadPoolExecutor
    ex_ = ThreadPoolExecutor(3)
    fut = {"ath": ex_.submit(athlete, cached, pid), "ev": ex_.submit(gamelog_events, cached, pid), "news": ex_.submit(fantasy_news, cached, pid)}
    ex_.shutdown(wait=False)
    def res(k, d):
        try: return fut[k].result(timeout=25) or d
        except Exception: return d
    raw = cached("gcard:%s:%d" % (pid, wk), 600, f)
    if not raw: return None
    # league-scored version when cookies exist (applied totals use THIS league's scoring)
    lraw, lstatus = None, None
    ck = _cookies()
    if ck and lkey in ESPN.LEAGUES:
        def lf():
            flt = {"players": {"filterIds": {"value": [pid]}, "filterStatsForTopScoringPeriodIds": {"value": 18, "additionalValue": _stat_ids(wk)}}}
            d = _get(LG % ESPN.LEAGUES[lkey][0] + "?scoringPeriodId=%d&view=kona_playercard" % wk, flt, ck)
            return (d.get("players") or [None])[0]
        try: lraw = cached("lcard:%s:%s:%d" % (lkey, pid, wk), 300, lf)
        except Exception: lraw = None
    src = (lraw or {}).get("player") or raw
    ex = {}
    s = slim(src, wk, ex)
    pos = s["pos"]
    if lraw:
        lstatus = {"status": lraw.get("status"), "on_team_id": lraw.get("onTeamId"), "waiver_until": lraw.get("waiverProcessDate")}
    # ownership in this league
    L = R["leagues"].get(lkey) or {}
    nm = ("DST:" + s["team"]) if pos == "DST" else s["name"]
    owner = None
    for tname, lst in (L.get("teams") or {}).items():
        if nm in lst or pid in [int(x) for x in ((L.get("pids") or {}).get(n) for n in lst) if x is not None]:
            owner = tname; break
    lg = {"league": lkey, "team": owner, "manager": (L.get("managers") or {}).get(owner) if owner else None,
          "mine": bool(owner and owner == L.get("my_team")), "status": "ONTEAM" if owner else None, "waiver_until": None,
          "verified": bool(lraw)}
    if not owner:
        if lstatus and lstatus["status"] in ("WAIVERS", "FREEAGENT"):
            lg["status"] = lstatus["status"]; lg["waiver_until"] = lstatus.get("waiver_until")
        else:
            lg["status"] = "FREEAGENT"; lg["unverified"] = True
    # slot in Logan's lineup
    if lg["mine"]:
        lg["slot"] = next((x["slot"] for x in (L.get("lineup") or []) if x.get("player") == nm), None) or ("IR" if nm in (L.get("ir") or []) else "BE")
    # game log (1..18), schedule
    ts = sched.get(str(s["pro_id"])) or {}
    ev = res("ev", {})
    log = []
    for w in range(1, 19):
        g = ts.get("games", {}).get(str(w))
        e = ex["games"].get(w)
        row = {"wk": w, "opp": (("" if g["home"] else "@") + g["opp"]) if g else ("BYE" if ts else None), "date": g.get("date") if g else None}
        if e and e["stats"]:
            row["pts"] = e["pts"]; row["stats"] = statrow(e["stats"], pos)
            r = ev.get(str(e.get("game_id") or (g or {}).get("game_id")))
            if r: row["result"] = r.get("result"); row["score"] = r.get("score")
        log.append(row)
    sched_rows = []
    for w in range(wk, 19):
        g = ts.get("games", {}).get(str(w))
        sched_rows.append({"wk": w, "opp": g["opp"] if g else None, "home": g["home"] if g else None, "date": g.get("date") if g else None, "bye": not g})
    o = (src.get("outlooks") or {}).get("outlooksByWeek") or {}
    a = res("ath", {})
    news = res("news", [])
    try: pr = pos_ranks(cached, wk).get(str(pid))
    except Exception: pr = None
    g_now = ts.get("games", {}).get(str(wk))
    return {**s, "headshot": None if pid < 0 else "https://a.espncdn.com/i/headshots/nfl/players/full/%d.png" % pid,
            "team_name": a.get("team_name"), "bye": ts.get("bye"), "league": lg, "pos_rank": pr, "bio": a,
            "inj_label": INJL.get(s["inj"]) if s["inj"] else ("Healthy" if src.get("injuryStatus") in ("ACTIVE", None) else src.get("injuryStatus")),
            "inj_note": " · ".join(x for x in [a.get("inj_type"), a.get("inj_detail")] if x) or None,
            "cols": [c[0] for c in COLS.get(pos, [])],
            "proj_row": statrow((ex.get("proj_raw") or {}).get("stats"), pos, True),
            "season_row": statrow((ex.get("season") or {}).get("stats"), pos),
            "week": {"wk": wk, "opp": (("vs " if g_now["home"] else "@") + g_now["opp"]) if g_now else "BYE", "date": g_now.get("date") if g_now else None},
            "outlook": o.get(str(wk)), "outlook_wk": wk if o.get(str(wk)) else None, "season_outlook": src.get("seasonOutlook"),
            "log": log, "schedule": sched_rows, "news": news, "scored_by": "league" if lraw else "espn-default-ppr",
            "fetched": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())}
