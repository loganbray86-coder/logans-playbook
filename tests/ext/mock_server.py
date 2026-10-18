# Local test server: real Playbook app, with ESPN reads for the private test league + ESPN fan profile mocked.
import os, sys, json
sys.path.insert(0, "/workspace/playbook-friends"); os.chdir("/workspace/playbook-friends")
os.environ.pop("ESPN_S2", None); os.environ.pop("ESPN_SWID", None)
import espn as ESPN
RAW = json.load(open("/workspace/espn_sync/wan_new.json"))
S2 = "MOCKS2" + "x" * 150; SW = "{11111111-2222-3333-4444-555555555555}"
RAW["teams"][2]["owners"] = [SW]     # mock account owns team id 3 in the mock private league
real = ESPN.http_get
class R:
    def __init__(s, c, j=None): s.status_code = c; s._j = j
    def json(s): return s._j
FAN = {"preferences": [{"metaData": {"entry": {"abbrev": "FFL", "gameId": 1, "seasonId": 2026, "entryId": 3, "groups": [{"groupId": 1852378966, "groupName": "mock"}]}}},
                       {"metaData": {"entry": {"abbrev": "FBA", "gameId": 3, "seasonId": 2026, "entryId": 1, "groups": [{"groupId": 999}]}}}]}
def fake(url, cookies=None, timeout=15):
    good = bool(cookies) and cookies.get("espn_s2") == S2 and cookies.get("SWID") == SW
    if "fan.api.espn.com" in url: return R(200, FAN) if good else R(401)
    if "/leagues/1852378966" in url: return R(200, RAW) if good else R(401)
    return real(url, cookies=cookies, timeout=timeout)
ESPN.http_get = fake
import server
if __name__ == "__main__":
    server.app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "8812")), threaded=True)
