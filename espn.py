"""READ-ONLY live ESPN league data. GET requests only; never POSTs to ESPN.
Credentials come only from env ESPN_S2 / ESPN_SWID and are never logged or returned."""
import os, time, copy, requests

SEASON = 2026
LEAGUES = {"WAN": (1852378966, 6), "SLOPE": (1994000491, 2)}
URL = ("https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/%d/segments/0/leagues/%d"
       "?view=mTeam&view=mRoster&view=mMatchupScore&view=mStatus&view=mSettings&view=mPendingTransactions")
POS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DST"}
SLOT = {0: "QB", 2: "RB", 4: "WR", 6: "TE", 23: "FLEX", 16: "DST", 17: "K", 20: "BE", 21: "IR"}
PRO = {0: "FA", 1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL", 7: "DEN", 8: "DET", 9: "GB", 10: "TEN",
       11: "IND", 12: "KC", 13: "LV", 14: "LAR", 15: "MIA", 16: "MIN", 17: "NE", 18: "NO", 19: "NYG", 20: "NYJ",
       21: "PHI", 22: "ARI", 23: "PIT", 24: "LAC", 25: "SF", 26: "SEA", 27: "TB", 28: "WSH", 29: "CAR", 30: "JAX",
       33: "BAL", 34: "HOU"}
INJ = {"QUESTIONABLE": "Q", "DOUBTFUL": "D", "OUT": "O", "INJURY_RESERVE": "IR", "SUSPENSION": "SSPD"}

def creds():
    s2, swid = os.environ.get("ESPN_S2", "").strip(), os.environ.get("ESPN_SWID", "").strip()
    return (s2, swid) if s2 and swid else None

def fetch(lid):
    c = creds()
    if not c: raise RuntimeError("ESPN credentials not set")
    r = requests.get(URL % (SEASON, lid), cookies={"espn_s2": c[0], "SWID": c[1]},
                     headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}, timeout=15, allow_redirects=False)
    if r.status_code != 200: raise RuntimeError("ESPN HTTP %d" % r.status_code)  # never include cookies/body
    try: return r.json()
    except Exception: raise RuntimeError("ESPN returned non-JSON (cookies expired?)")

def tname(t):
    return (t.get("name") or ("%s %s" % (t.get("location", ""), t.get("nickname", ""))).strip() or "Team %s" % t["id"]).strip()

def pname(e):
    p = e.get("playerPoolEntry", {}).get("player", {}) or e.get("player", {})
    pos = POS.get(p.get("defaultPositionId"), "?")
    if pos == "DST": return "DST:" + PRO.get(p.get("proTeamId"), "?"), p, pos
    return p.get("fullName") or str(p.get("id")), p, pos

def _wk_pts(p, period):
    """(ESPN projection, actual) for this scoring period, league-scored appliedTotal. None when absent."""
    pr = ac = None
    for s in p.get("stats") or []:
        if s.get("scoringPeriodId") != period or s.get("statSplitTypeId") != 1: continue
        v = s.get("appliedTotal")
        if v is None: continue
        if s.get("statSourceId") == 1: pr = round(v, 2)
        elif s.get("statSourceId") == 0 and (s.get("stats") or v): ac = round(v, 2)
    return pr, ac

def convert(lkey, d, base, extra=False):
    """extra=True (live app only) adds per-player ESPN week projections/actuals and every team's starters.
    The sync routine calls convert() without it, so data/rosters.json keeps its exact shape."""
    lid, myid = LEAGUES[lkey]
    L = copy.deepcopy(base or {})
    members = {m["id"]: ("%s %s" % (m.get("firstName", ""), m.get("lastName", ""))).strip() or m.get("displayName", "") for m in d.get("members", [])}
    teams, byid, mgrs, players, pids = {}, {}, {}, {}, {}
    period = d.get("scoringPeriodId") or d.get("status", {}).get("currentMatchupPeriod")
    for t in d.get("teams", []):
        n = tname(t); byid[t["id"]] = n
        mgrs[n] = "Logan" if t["id"] == myid else ", ".join(members.get(o, "") for o in t.get("owners", []) if members.get(o)) 
        ents = t.get("roster", {}).get("entries", [])
        lst = []
        for e in ents:
            nm, p, pos = pname(e); pid = p.get("id")
            players[pid] = {"name": nm, "pos": pos, "team": PRO.get(p.get("proTeamId"), "FA")}; pids[nm] = pid
            lst.append((e.get("lineupSlotId"), nm, p, pos))
        teams[t["id"]] = lst
    me = teams.get(myid, [])
    sset = d.get("settings", {}).get("rosterSettings", {}).get("lineupSlotCounts", {})
    order = [0, 2, 4, 6, 23, 16, 17]
    slots = []
    for s in order:
        slots += [SLOT[s]] * int(sset.get(str(s), 0) or 0)
    if not slots: slots = L.get("slots", [])
    # Logan's list: starters in slot order, then bench, then IR (frontend reads index order)
    pool = list(me); mine = []; lineup = []
    for s in slots:
        sid = next(k for k, v in SLOT.items() if v == s)
        hit = next((x for x in pool if x[0] == sid), None)
        if hit: pool.remove(hit)
        mine.append(hit[1] if hit else "(empty)"); lineup.append({"slot": s, "player": hit[1] if hit else None})
    bench = [x for x in pool if x[0] != 21]; ir = [x for x in pool if x[0] == 21]
    mine += [x[1] for x in bench] + [x[1] for x in ir]
    L["slots"] = slots
    L["teams"] = {byid[tid]: (mine if tid == myid else [x[1] for x in lst]) for tid, lst in teams.items()}
    L["my_team"] = byid.get(myid, L.get("my_team"))
    L["managers"] = mgrs; L["teams_count"] = len(teams); L.pop("partial", None)
    L["ir"] = [x[1] for x in ir]; L["lineup"] = lineup
    st = {}
    for lst in teams.values():
        for _, nm, p, _ in lst:
            s = INJ.get(p.get("injuryStatus") or "")
            if s: st[nm] = s
    L["espn_status"] = st
    # records / standings
    stand = []
    for t in d.get("teams", []):
        o = t.get("record", {}).get("overall", {})
        stand.append({"team": byid[t["id"]], "w": o.get("wins", 0), "l": o.get("losses", 0), "t": o.get("ties", 0),
                      "pf": round(o.get("pointsFor", 0), 1), "seed": t.get("playoffSeed") or t.get("rankCalculatedFinal")})
    stand.sort(key=lambda x: (x["seed"] or 99, -x["w"], -x["pf"]))
    for i, s in enumerate(stand): s["rank"] = i + 1
    L["standings"] = stand
    mine_s = next((s for s in stand if s["team"] == L["my_team"]), None)
    if mine_s:
        wl = "%d-%d" % (mine_s["w"], mine_s["l"]) + ("-%d" % mine_s["t"] if mine_s["t"] else "")
        sfx = lambda n: "%d%s" % (n, "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th"))
        L["wl"] = wl; L["record"] = "%s (%s of %d)" % (wl, sfx(mine_s["rank"]), len(stand)); L["place"] = sfx(mine_s["rank"]) + " Place"
    # live matchup
    mp = d.get("status", {}).get("currentMatchupPeriod")
    for m in d.get("schedule", []):
        if m.get("matchupPeriodId") != mp: continue
        h, a = m.get("home", {}), m.get("away", {})
        if myid not in (h.get("teamId"), a.get("teamId")): continue
        us, them = (h, a) if h.get("teamId") == myid else (a, h)
        sc = lambda x: round(x.get("totalPointsLive", x.get("totalPoints", 0)) or 0, 2)
        pj = lambda x: round(x["totalProjectedPointsLive"], 1) if x.get("totalProjectedPointsLive") is not None else None
        L["matchup"] = {"me": sc(us), "opp": byid.get(them.get("teamId"), ""), "opp_score": sc(them),
                        "me_proj": pj(us), "opp_proj": pj(them), "week": mp, "as_of": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())}
    # pending transactions
    pend = []
    seen = set()
    for tx in (d.get("transactions") or []) + (d.get("pendingTransactions") or []):
        if tx.get("id") in seen: continue
        seen.add(tx.get("id"))
        if tx.get("status") not in ("PENDING", "PROPOSED"): continue
        items = tx.get("items", [])
        nm = lambda pid: players.get(pid, {}).get("name", "Player %s" % pid)
        if tx.get("type") in ("TRADE_PROPOSAL", "TRADE_ACCEPT", "TRADE_UPHOLD") or any(i.get("type") == "TRADE" for i in items):
            pend.append({"type": "trade", "status": tx.get("status", "").lower(),
                         "from": byid.get(tx.get("teamId"), ""),
                         "items": [{"player": nm(i.get("playerId")), "from": byid.get(i.get("fromTeamId"), ""), "to": byid.get(i.get("toTeamId"), "")} for i in items],
                         "expires": tx.get("expirationDate")})
        elif tx.get("teamId") == myid:
            add = next((nm(i["playerId"]) for i in items if i.get("type") == "ADD"), None)
            drop = next((nm(i["playerId"]) for i in items if i.get("type") == "DROP"), None)
            pend.append({"type": "waiver", "add": add, "drop": drop, "status": "pending", "bid": tx.get("bidAmount")})
    L["pending"] = pend
    L["pids"] = pids
    # waiver settings (read-only): order, FAAB, processing schedule
    acq = d.get("settings", {}).get("acquisitionSettings", {}) or {}
    me_t = next((t for t in d.get("teams", []) if t["id"] == myid), {}) or {}
    tc = me_t.get("transactionCounter") or {}
    budget = acq.get("acquisitionBudget")
    faab = bool(acq.get("isUsingAcquisitionBudget"))
    L["waiver"] = {"rank": me_t.get("waiverRank"), "teams": len(d.get("teams", [])), "faab": faab,
                   "budget": budget if faab else None,
                   "spent": tc.get("acquisitionBudgetSpent") if faab else None,
                   "remaining": (budget - (tc.get("acquisitionBudgetSpent") or 0)) if faab and budget is not None else None,
                   "process_days": acq.get("waiverProcessDays") or [], "process_hour": acq.get("waiverProcessHour"),
                   "waiver_hours": acq.get("waiverHours"), "order_type": acq.get("waiverOrderReset"),
                   "last_run": d.get("status", {}).get("waiverLastExecutionDate"),
                   "order": [{"team": byid.get(t["id"]), "rank": t.get("waiverRank")} for t in sorted(d.get("teams", []), key=lambda t: t.get("waiverRank") or 99)]}
    if extra:
        wkp, starters = {}, {}
        for tid, lst in teams.items():
            starters[byid[tid]] = [nm for slot, nm, _, _ in lst if slot not in (20, 21)]
            for slot, nm, p, _ in lst:
                pr, ac = _wk_pts(p, period)
                wkp[nm] = {"e": pr, "a": ac, "t": PRO.get(p.get("proTeamId"), "FA")}
        L["wkp"] = wkp; L["starters"] = starters; L["wkp_period"] = period
    L["source"] = "espn"
    return L

def live_rosters(base, cache_fn, ttl):
    """Returns rosters dict with each league replaced by live ESPN data when possible; per-league fallback."""
    R = copy.deepcopy(base); src = {}
    for k in LEAGUES:
        if not creds():
            src[k] = {"live": False, "reason": "no credentials"}; R["leagues"][k]["source"] = "cached"; continue
        def f(k=k):
            return convert(k, fetch(LEAGUES[k][0]), base["leagues"].get(k), extra=True)
        v = cache_fn("espn:" + k, ttl, f)
        if v: R["leagues"][k] = v; src[k] = {"live": True}
        else: R["leagues"][k]["source"] = "cached"; src[k] = {"live": False, "reason": "ESPN error"}
    R["espn"] = src
    return R
