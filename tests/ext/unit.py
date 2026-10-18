import sys, json, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mock_server as M
c = M.server.app.test_client(); ext = "chrome-extension://aniopchmkabeloipmicldolcmpjmmggj"
def post(body, origin=ext): return c.post("/api/ext/connect", json=body, headers={"Origin": origin} if origin else {})
ok = lambda name, cond: print(("PASS " if cond else "FAIL ") + name)
r = c.open("/api/ext/connect", method="OPTIONS", headers={"Origin": ext, "Access-Control-Request-Method": "POST"})
ok("preflight from add-on allowed (204 + CORS)", r.status_code == 204 and r.headers.get("Access-Control-Allow-Origin") == ext)
r = c.open("/api/ext/connect", method="OPTIONS", headers={"Origin": "https://evil.example"}); ok("preflight from other site refused", r.status_code == 403 and "Access-Control-Allow-Origin" not in r.headers)
r = post({"espn_s2": M.S2, "swid": M.SW}, origin="https://evil.example"); ok("POST from other website refused", r.status_code == 403)
r = post({}); ok("no cookies -> friendly 400", r.status_code == 400 and r.get_json()["code"] == "no_cookies")
r = post({"espn_s2": M.S2, "swid": M.SW, "discover": True}); j = r.get_json(); body = r.get_data(as_text=True)
L = {x["id"]: x for x in j["leagues"]}
ok("discovery finds the football league only (not basketball)", list(L) == [1852378966] and j["found_on_espn"] == 1)
x = L.get(1852378966, {})
ok("league validated + my team detected from SWID", x.get("ok") and x.get("tid") == 3 and x.get("team"))
ok("token returned and opens only for that league", bool(M.server.VAULT.open_token(x["tok"], 1852378966)) and not M.server.VAULT.open_token(x["tok"], 1946))
ok("cookies never echoed back", M.S2 not in body and M.SW not in body)
ok("CORS header on response + no-store", r.headers.get("Access-Control-Allow-Origin") == ext and r.headers.get("Cache-Control") == "no-store")
r = post({"espn_s2": M.S2, "swid": M.SW, "discover": False, "leagues": [{"id": 1946}, {"id": 1234}, "1852378966"]}); j = r.get_json(); L = {x["id"]: x for x in j["leagues"]}
ok("multiple leagues in one go (public 1946 ok, mock private ok)", L[1946]["ok"] and L[1852378966]["ok"] and L[1852378966]["tid"] == 3)
ok("public league where account isn't a member -> no team, list to pick from", L[1946]["tid"] is None and len(L[1946]["teams"]) == 12)
ok("bad league id -> clear message", not L[1234]["ok"] and L[1234]["code"] == "not_found")
r = post({"espn_s2": "MOCKS2" + "y" * 150, "swid": M.SW, "leagues": [1852378966]}); j = r.get_json()
ok("wrong cookies -> 'not in that league'", j["leagues"][0]["code"] == "bad_creds")
tok = L[1852378966]["tok"]
r = c.get("/api/data", headers={"X-Leagues": json.dumps([{"id": 1852378966, "tid": 3, "season": 2026, "tok": tok}])}); d = r.get_json()
ok("token from add-on loads the team in the app", "L1852378966_3" in d["rosters"]["leagues"] and not d["rosters"]["errors"])
r = c.get("/static/playbook-connect.zip"); ok("zip served from the app", r.status_code == 200 and r.data[:2] == b"PK")
