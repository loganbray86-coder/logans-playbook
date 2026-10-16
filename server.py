import json, re, time, os, threading, unicodedata
import requests
import adv as ADV
from flask import Flask, jsonify, send_from_directory, request
BASE = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
app = Flask(__name__, static_folder=os.path.join(BASE, "static"))
os.makedirs(os.path.join(BASE, "cache"), exist_ok=True)
_cache, _lock = {}, threading.Lock()
STATUS = {}

def cached(key, ttl, fn):
    now = time.time()
    with _lock:
        c = _cache.get(key)
        if c and now - c[0] < ttl: return c[1]
    try:
        v = fn(); STATUS[key] = {"ok": True, "fetched": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())}
    except Exception as e:
        STATUS[key] = {"ok": False, "error": str(e)[:200], "fetched": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())}
        if c: return c[1]
        return None
    with _lock: _cache[key] = (now, v)
    return v

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
    return cached("fp:" + page, 3600, f)

def fc(teams):
    return cached(f"fc{teams}", 3600, lambda: requests.get(
        f"https://api.fantasycalc.com/values/current?isDynasty=false&numQbs=1&numTeams={teams}&ppr=1", headers=UA, timeout=20).json())

def sleeper():
    path = os.path.join(BASE, "cache", "sleeper_slim.json")
    def f():
        if os.path.exists(path) and time.time() - os.path.getmtime(path) < 86400:
            return json.load(open(path))
        raw = requests.get("https://api.sleeper.app/v1/players/nfl", timeout=60).json()
        slim = {}
        for p in raw.values():
            if not p.get("full_name") or p.get("position") not in ("QB", "RB", "WR", "TE", "K"): continue
            if not p.get("team"): continue
            slim[norm(p["full_name"]) + "|" + p["position"]] = {k: p.get(k) for k in ("injury_status", "injury_body_part", "injury_notes", "status", "team", "news_updated", "depth_chart_order")}
        json.dump(slim, open(path, "w")); return slim
    return cached("sleeper", 86400, f)

def rototrade():
    d = json.load(open(os.path.join(BASE, "data", "rototrade.json")))
    STATUS["rototrade"] = {"ok": True, "fetched": "seed file, as of " + d["as_of"], "note": d.get("source")}
    return d

def rosters(): return json.load(open(os.path.join(BASE, "data", "rosters.json")))

def build():
    players = {}
    def get(p, pos):
        key = norm(p["player_name"]) + "|" + pos
        if pos == "DST": key = "dst:" + p["player_team_id"]
        e = players.setdefault(key, {"key": key, "name": p["player_name"], "pos": pos, "team": p.get("player_team_id"), "bye": p.get("player_bye_week")})
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
            e["inj"] = s.get("injury_status"); e["inj_part"] = s.get("injury_body_part"); e["inj_note"] = s.get("injury_notes")
    nmap = {norm(e["name"]): e["name"] for e in players.values()}
    for e in players.values():
        if e["pos"] == "RB" and e.get("team"):
            mates = sorted(x for x in depth.get(ADV.canon(e["team"]), []) if x[1] != norm(e["name"]))
            if mates: e["cuff"] = nmap.get(mates[0][1], mates[0][1].title())
    # rosters
    R = rosters(); unmatched = {}
    lastk = {}
    for e in players.values():
        if e["pos"] == "K": lastk.setdefault(norm(e["name"]).split()[-1], []).append(e)
    for lid, L in R["leagues"].items():
        for tname, lst in L["teams"].items():
            for n in lst:
                if n.startswith("DST:"): e = players.get("dst:" + n[4:])
                elif n.startswith("K:"):
                    c = lastk.get(norm(n[2:]), []); e = c[0] if len(c) == 1 else None
                else:
                    c = byname.get(norm(n), []); e = c[0] if c else None
                if not e:
                    k = "unranked:" + norm(n); e = players.setdefault(k, {"key": k, "name": n, "pos": "?", "unranked": True})
                    unmatched.setdefault(lid, []).append(n)
                e.setdefault("owners", {})[lid] = tname
                R.setdefault("resolved", {}).setdefault(lid, {}).setdefault(tname, []).append(e["key"])
    advd = cached("adv", 21600, lambda: ADV.build_adv(norm)); extra = {}
    cur = int(meta.get("weekly", {}).get("week") or 1)
    if advd:
        for e in players.values():
            a = advd["players"].get(norm(e["name"]) + "|" + str(e["pos"]))
            if a: e["adv"] = a
        sched = cached("sched", 21600, lambda: ADV.schedule(cur))
        extra = {"team_stats": advd["teams"], "adv_season": advd["season"], "adv_thru": advd["thru_week"], "fpa": advd["fpa"]}
        if sched:
            extra["sched"] = sched; extra["sos"] = ADV.sos(sched, advd["fpa"], cur)
    return {**extra, "cur_week": cur, "players": list(players.values()), "meta": meta, "rosters": R, "unmatched": unmatched,
            "rt_as_of": rt["as_of"], "status": STATUS, "built": time.time()}

@app.route("/api/data")
def api_data():
    return jsonify(cached("build", 600, build) if not request.args.get("refresh") else build())

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
    return jsonify(cached("espn", 60, f) or {"games": [], "error": STATUS.get("espn")})

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
    return cached("news", 300, f)

@app.route("/api/news")
def api_news(): return jsonify({"items": list(reversed(news() or [])), "status": STATUS.get("news")})

@app.route("/api/refresh", methods=["POST", "GET"])
def api_refresh():
    with _lock: _cache.clear()
    for f in ("sleeper_slim.json", "sched.json"):
        fp = os.path.join(BASE, "cache", f)
        if os.path.exists(fp): os.utime(fp, (0, 0))
    d = build()
    with _lock: _cache["build"] = (time.time(), d)
    news()
    return jsonify({"ok": True, "updated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "status": STATUS})

@app.route("/api/status")
def api_status(): return jsonify(STATUS)

@app.route("/api")
def api_docs():
    return """<html><body style="background:#0b0f17;color:#e8edf5;font-family:sans-serif;padding:20px;max-width:800px">
<h1>Logan's Playbook JSON API</h1><p>All read-only GET endpoints. Nothing here touches ESPN.</p>
<h3>GET /api/data</h3><p>Everything the app uses. Add <code>?refresh=1</code> to rebuild now (otherwise cached 10 min; sources cached 1 h, Sleeper and nflverse daily/6 h).</p>
<ul><li><b>players[]</b>: key, name, pos, team, bye, opp, own (FP avg roster %), wk {rank,pos_rank,pts (FP PPR projection),best,worst,avg,std,grade,delta}, ros {overall,pos_rank,pos_num,...}, rt (RotoTrade seed), fc12/fc16 (FantasyCalc), inj/inj_part/inj_note (Sleeper), adv {g,ppg,tgt_g,ts,ay_g,touch_g,snap,last3[],trend,trend_val,tot{}} (nflverse), cuff (Sleeper depth chart RB2), owners {WAN|SLOPE: team}</li>
<li><b>fpa</b>: {pos:{team:{fpa,rank}}} PPR points allowed per game (rank 32 = most allowed)</li><li><b>sched</b>: {team:{week:"vs OPP"|"@OPP"}} weeks cur..17 from ESPN</li>
<li><b>sos</b>: {team:{pos:{ros|wan_po|slope_po:{avg_rank,grade}}}}</li><li><b>team_stats</b>: {team:{off_ppg,give_g}}</li>
<li><b>rosters</b>, <b>meta</b>, <b>status</b> (per-source ok/fetched), cur_week, adv_season, adv_thru, rt_as_of, unmatched</li></ul>
<h3>GET /api/scoreboard</h3><p>Live ESPN NFL scoreboard (cached 60 s): week, games[] {status,state,teams[{abbr,score,record,logo,home}],broadcast}.</p>
<h3>GET /api/status</h3><p>Source health only.</p></body></html>"""

@app.route("/manifest.json")
def manifest(): return send_from_directory(app.static_folder,"manifest.json",mimetype="application/manifest+json")
@app.route("/sw.js")
def sw(): return send_from_directory(app.static_folder,"sw.js",mimetype="application/javascript")
@app.route("/")
def index(): return send_from_directory(app.static_folder, "index.html")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8765)), threaded=True)
