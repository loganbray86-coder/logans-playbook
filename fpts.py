"""ESPN default PPR scoring computed from ESPN stat ids. Used only when ESPN did not return appliedTotal
(public endpoints). League endpoints (with cookies) return league-scored appliedTotal, which wins."""
OFF = {"3": .04, "4": 4, "20": -2, "24": .1, "25": 6, "53": 1, "42": .1, "43": 6, "19": 2, "26": 2, "44": 2, "72": -2,
       "101": 6, "102": 6}
K = {"74": 5, "77": 4, "80": 3, "85": -1, "86": 1}
DST = {"99": 1, "95": 2, "96": 2, "98": 2, "97": 2, "94": 6, "101": 6, "102": 6,
       "89": 5, "90": 4, "91": 3, "92": 1, "123": -1, "124": -3, "125": -5,
       "128": 5, "129": 3, "130": 2, "132": -1, "133": -3, "134": -5, "135": -6, "136": -7}

def pts(stats, pos):
    if not stats: return None
    tbl = K if pos == "K" else DST if pos == "DST" else OFF
    return round(sum(float(stats.get(k, 0) or 0) * v for k, v in tbl.items()), 2)

def best(entry, pos):
    a = entry.get("appliedTotal")
    if a not in (None, 0, 0.0): return round(a, 2)
    return pts(entry.get("stats") or {}, pos)
