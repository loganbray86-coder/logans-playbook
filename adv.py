import csv, io, os, time, json, requests
from collections import defaultdict
BASE = os.path.dirname(os.path.abspath(__file__)); C = os.path.join(BASE, "cache"); os.makedirs(C, exist_ok=True)
NV = "https://github.com/nflverse/nflverse-data/releases/download/"
CANON = {"LA": "LAR", "JAC": "JAX", "WSH": "WAS", "LV": "LV"}
def canon(t): return CANON.get(t, t) if t else t

def dl(name, url, ttl=21600):
    p = os.path.join(C, name)
    if not (os.path.exists(p) and time.time() - os.path.getmtime(p) < ttl):
        r = requests.get(url, timeout=60, allow_redirects=True)
        if r.status_code != 200: raise Exception(f"{url} -> {r.status_code}")
        open(p, "wb").write(r.content)
    return list(csv.DictReader(open(p, encoding="utf-8")))

def nflverse():
    for season in (2026, 2025):
        try:
            sp = dl(f"sp{season}.csv", f"{NV}stats_player/stats_player_week_{season}.csv")
            try: sn = dl(f"sn{season}.csv", f"{NV}snap_counts/snap_counts_{season}.csv")
            except Exception: sn = []
            return season, [r for r in sp if r["season_type"] == "REG"], sn
        except Exception: continue
    raise Exception("nflverse unavailable")

f = lambda x: float(x) if x not in ("", None, "NA") else 0.0

def build_adv(norm):
    season, sp, sn = nflverse()
    snaps = defaultdict(dict)
    for r in sn:
        if r.get("game_type", "REG") != "REG": continue
        snaps[norm(r["player"]) + "|" + {"HB":"RB","FB":"RB"}.get(r["position"], r["position"])][int(r["week"])] = round(f(r["offense_pct"]) * 100)
    off = defaultdict(dict); give = defaultdict(dict); per = defaultdict(dict); fpa = defaultdict(lambda: defaultdict(float)); dgames = defaultdict(set)
    for r in sp:
        pos = r["position"]
        if pos not in ("QB", "RB", "WR", "TE"): continue
        wk = int(r["week"]); k = norm(r["player_display_name"]) + "|" + pos
        comp = {c: f(r[c]) for c in ("passing_yards","passing_tds","passing_interceptions","rushing_yards","rushing_tds","receptions","receiving_yards","receiving_tds","fumbles_lost_total","passing_2pt_conversions","rushing_2pt_conversions","receiving_2pt_conversions")}
        tm = canon(r["team"]); off[tm][wk] = off[tm].get(wk, 0) + f(r["fantasy_points_ppr"])
        give[tm][wk] = give[tm].get(wk, 0) + f(r["passing_interceptions"]) + f(r["sack_fumbles_lost"]) + f(r["rushing_fumbles_lost"]) + f(r["receiving_fumbles_lost"]) + f(r["sacks_suffered"]) * 0.5
        per[k][wk] = {"c": comp, "ppr": f(r["fantasy_points_ppr"]), "tgt": int(f(r["targets"])), "ts": f(r["target_share"]), "ay": f(r["receiving_air_yards"]),
                      "touch": int(f(r["carries"]) + f(r["receptions"])), "team": canon(r["team"]), "opp": canon(r["opponent_team"])}
        o = canon(r["opponent_team"]); fpa[o][pos] += f(r["fantasy_points_ppr"]); dgames[o].add(wk)
    maxwk = max(int(r["week"]) for r in sp)
    adv = {}
    for k, wks in per.items():
        ws = sorted(wks); n = len(ws); avg = lambda key, l: round(sum(wks[w][key] for w in l) / len(l), 2) if l else None
        last3 = ws[-3:]; prior = ws[:-3] or ws
        sv = snaps.get(k, {})
        usage = lambda w: wks[w]["tgt"] + wks[w]["touch"] * (0 if k.endswith("|QB") else 1) if not k.endswith("|QB") else wks[w]["ppr"]
        trend_val = (sum(wks[w]["touch"] + wks[w]["tgt"] for w in last3) / len(last3)) - (sum(wks[w]["touch"] + wks[w]["tgt"] for w in prior) / len(prior))
        snap_l = [sv[w] for w in sorted(sv)]
        adv[k] = {"g": n, "ppg": avg("ppr", ws), "tgt_g": avg("tgt", ws), "ts": round(avg("ts", ws) * 100, 1), "ay_g": avg("ay", ws), "touch_g": avg("touch", ws),
                  "snap": round(sum(snap_l) / len(snap_l)) if snap_l else None,
                  "last3": [{"wk": w, "ppr": wks[w]["ppr"], "tgt": wks[w]["tgt"], "touch": wks[w]["touch"], "snap": sv.get(w), "opp": wks[w]["opp"]} for w in last3],
                  "tot": {c: round(sum(wks[w]["c"][c] for w in ws), 1) for c in wks[ws[0]]["c"]},
                  "trend": "up" if trend_val >= 1.5 else "down" if trend_val <= -1.5 else "flat", "trend_val": round(trend_val, 1), "team": wks[ws[-1]]["team"]}
    table = {}
    for pos in ("QB", "RB", "WR", "TE"):
        vals = {t: fpa[t][pos] / max(1, len(dgames[t])) for t in dgames}
        order = sorted(vals, key=lambda t: vals[t])  # least allowed first
        table[pos] = {t: {"fpa": round(vals[t], 1), "rank": i + 1} for i, t in enumerate(order)}  # rank 32 = most allowed = easiest
    teams = {t: {"off_ppg": round(sum(off[t].values()) / len(off[t]), 1), "give_g": round(sum(give[t].values()) / len(give[t]), 2)} for t in off}
    return {"season": season, "thru_week": maxwk, "players": adv, "fpa": table, "teams": teams}

def grade(rank, n=32):
    if rank is None: return None
    p = rank / n
    return "A" if p > 0.8 else "B" if p > 0.6 else "C" if p > 0.4 else "D" if p > 0.2 else "F"

def schedule(cur, last=17):
    p = os.path.join(C, "sched.json")
    if os.path.exists(p) and time.time() - os.path.getmtime(p) < 21600:
        return json.load(open(p))
    out = defaultdict(dict)
    for w in range(cur, last + 1):
        d = requests.get(f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?week={w}&seasontype=2", timeout=20).json()
        for e in d.get("events", []):
            cs = e["competitions"][0]["competitors"]
            for c in cs:
                o = [x for x in cs if x is not c][0]
                out[canon(c["team"]["abbreviation"])][str(w)] = ("vs " if c["homeAway"] == "home" else "@") + canon(o["team"]["abbreviation"])
    out = dict(out); json.dump(out, open(p, "w")); return out

def sos(sched, fpa, cur):
    res = {}
    rng = {"ros": range(cur, 18), "wan_po": range(13, 18), "slope_po": range(12, 18)}
    for team, wk in sched.items():
        res[team] = {}
        for pos in ("QB", "RB", "WR", "TE"):
            for name, r in rng.items():
                ranks = [fpa[pos].get(wk[str(w)].split()[-1].lstrip("@"), {}).get("rank") for w in r if str(w) in wk]
                ranks = [x for x in ranks if x]
                res[team].setdefault(pos, {})[name] = round(sum(ranks) / len(ranks), 1) if ranks else None
    # convert avg opp-rank to grade by ranking teams
    for pos in ("QB", "RB", "WR", "TE"):
        for name in rng:
            teams = sorted([t for t in res if res[t][pos][name] is not None], key=lambda t: res[t][pos][name])
            for i, t in enumerate(teams):
                res[t][pos][name] = {"avg_rank": res[t][pos][name], "grade": grade(i + 1, len(teams))}
    return res
