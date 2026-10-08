"""Run: python tests/test_market_radar_api.py   (plain asserts; needs the dev database, skips itself without one).

Drives the real Flask app through its test client as throwaway users (zz_buyer_test_*) and removes
everything they created, so it is safe to run against the local dev database:

  * login and CSRF are enforced, the page and its files are served, the feature flag hides all of it,
  * every Market Radar preset runs against the real awards table and the SQL agrees with the Python mirror,
  * the analysis payload is internally consistent (every award belongs to exactly one peer ...),
  * request validation, peer paging / sorting / search, the organisation, watchlist and dashboard,
  * the whole market engagement lifecycle, including that one user can never see another's plan.

It reads real award data, so it asserts shapes and invariants rather than exact counts, and skips the
checks that need data the database does not have.
"""
import json
import os
import re
import subprocess
import sys
import time
import traceback
import urllib.parse
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# create_app() starts the email scheduler, which really sends mail to the addresses in the dev
# database, unless it is switched off. These must be set before server / tender_app.config import.
os.environ["ENABLE_EMAIL_SCHEDULER"] = "0"
os.environ["ENABLE_SCHEDULERS"] = "0"
os.environ["ENABLE_BUYER_WORKSPACE"] = "1"

RUN = uuid.uuid4().hex[:8]
USER_A = f"zz_buyer_test_{RUN}_a"
USER_B = f"zz_buyer_test_{RUN}_b"
USER_C = f"zz_buyer_test_{RUN}_c"  # request-validation checks: the radar endpoints are rate limited per user
USERS = [USER_A, USER_B, USER_C]
CSRF = "test-csrf-token"
H = {"X-CSRF-Token": CSRF}
HOUSING = "category=housing-repairs-gas&authority=london-borough&window=3y"


class Skip(Exception):
    pass


def skip(why):
    raise Skip(why)


_app = None


def app():
    global _app
    if _app is None:
        from server import create_app
        _app = create_app()
    return _app


def db():
    from tender_app.db import get_db_connection
    return get_db_connection()


def client(user=None):
    c = app().test_client()
    if user:
        with c.session_transaction() as s:
            s.update(logged_in=True, username=user, email=f"{user}@test.invalid", last_activity=time.time(), csrf_token=CSRF)
    return c


def call(c, method, url, body=None, headers=H):
    kwargs = {"headers": headers}
    if body is not None:
        kwargs["json"] = body
    r = getattr(c, method)(url, **kwargs)
    return r.status_code, (r.get_json(silent=True) or {})


_award_rows = []


def award_rows():
    if not _award_rows:
        conn = db()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM contract_awards")
                _award_rows.append(cur.fetchone()[0])
        finally:
            conn.close()
    return _award_rows[0]


def need_awards():
    if award_rows() < 20_000:
        skip("the awards table is too small to check against")


# ───────────────────────────────────────── access ────────────────────────────────────────────────

def test_login_and_csrf_are_required():
    anon = client()
    for url in ("/api/market-radar/options", "/api/market-engagement", "/api/buyer-workspace/me"):
        assert anon.get(url).status_code == 401, url
    r = anon.get("/buyer-workspace")
    assert r.status_code == 302 and "login" in r.headers["Location"]
    c = client(USER_A)
    writes = (
        ("put", "/api/buyer-workspace/organisation", {"name": None}),
        ("put", "/api/buyer-workspace/watchlist", {"categories": []}),
        ("post", "/api/market-engagement", {"title": "Valid title", "category": {"preset": "housing-repairs-gas"}}),
    )
    for method, url, body in writes:
        assert call(c, method, url, body, headers={})[0] == 403, f"{url} accepted a request without a CSRF token"
        assert call(c, method, url, body, headers={"X-CSRF-Token": "wrong"})[0] == 403, url


def test_page_and_files_are_served():
    c = client(USER_A)
    page = c.get("/buyer-workspace")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert 'id="bwMain"' in html
    assets = set(re.findall(r'(?:href|src)="(/buyer-[\w.-]+\.(?:js|css))(?:\?[^"]*)?"', html))
    assert assets == {"/buyer-workspace.css", "/buyer-workspace.js", "/buyer-engagement.js"}, assets
    for path in sorted(assets):
        r = c.get(path)
        assert r.status_code == 200 and len(r.data) > 1000, path
    assert call(c, "get", "/api/config/public")[1]["features"] == {"buyerWorkspace": True, "growthStudio": True}


def test_feature_flag_off_hides_the_workspace():
    probe = (
        "import json, time\n"
        "from server import create_app\n"
        "c = create_app().test_client()\n"
        "with c.session_transaction() as s:\n"
        "    s.update(logged_in=True, username='zz_buyer_test_flag', last_activity=time.time(), csrf_token='t')\n"
        "print('PROBE' + json.dumps({'page': c.get('/buyer-workspace').status_code,\n"
        "    'script': c.get('/buyer-workspace.js').status_code,\n"
        "    'radar': c.get('/api/market-radar/options').status_code,\n"
        "    'engagement': c.get('/api/market-engagement').status_code,\n"
        "    'features': c.get('/api/config/public').get_json()['features']}))\n"
    )
    env = dict(os.environ, ENABLE_BUYER_WORKSPACE="0", ENABLE_EMAIL_SCHEDULER="0", ENABLE_SCHEDULERS="0", PYTHONPATH=str(ROOT))
    run = subprocess.run([sys.executable, "-c", probe], cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=240)
    line = next((ln for ln in run.stdout.splitlines() if ln.startswith("PROBE")), None)
    assert line, f"the probe printed no result:\n{run.stdout[-800:]}\n{run.stderr[-800:]}"
    assert json.loads(line[len("PROBE"):]) == {
        "page": 404, "script": 404, "radar": 404, "engagement": 404, "features": {"buyerWorkspace": False, "growthStudio": True},
    }


# ─────────────────────────────────────── Market Radar ────────────────────────────────────────────

def test_options_and_category_suggestions():
    from tender_app import market_radar as mr
    c = client(USER_A)
    st, o = call(c, "get", "/api/market-radar/options")
    assert st == 200 and o["default_window"] == "3y"
    assert [p["preset"] for p in o["presets"]] == [p["id"] for p in mr.CATEGORY_PRESETS]
    assert [a["id"] for a in o["authority_types"]] == [t for t, _ in mr.AUTHORITY_TYPES]
    assert [w["id"] for w in o["windows"]] == ["1y", "3y", "5y", "all"] and [s["id"] for s in o["scopes"]] == ["all", "direct"]
    st, s = call(c, "get", "/api/market-radar/categories?q=boiler")
    assert st == 200 and "housing-repairs-gas" in [p["preset"] for p in s["presets"]]
    assert all(set(x) == {"cpv", "label", "awards"} for x in s["cpv"])
    labels = [x["label"].lower() for x in s["cpv"]]
    assert len(labels) == len(set(labels)), "one suggestion per label"
    st, s = call(c, "get", "/api/market-radar/categories?q=")
    assert len(s["presets"]) == len(mr.CATEGORY_PRESETS) and s["cpv"] == []
    st, s = call(c, "get", "/api/market-radar/categories?q=zzqxv")
    assert st == 200 and s["presets"] == [] and s["cpv"] == []


def test_every_preset_runs_against_the_real_table():
    need_awards()
    from tender_app import market_radar as mr
    conn = db()
    try:
        with conn.cursor() as cur:
            for spec in mr.CATEGORY_PRESETS:
                cat = mr.resolve_category(preset=spec["id"])
                rows, _ = mr.fetch_award_rows(cur, cat, "all", "3y")
                assert rows, f"preset {spec['id']} matched no awards in the last three years: re-check its CPV codes and keywords"
                stray = [r["id"] for r in rows if not mr.category_matches(cat, r["tender_title"], r["cpv_code"])]
                assert not stray, f"{spec['id']}: SQL returned awards the Python mirror rejects: {stray[:5]}"
                assert all(r["authority_name"] for r in rows)
    finally:
        conn.close()


def test_analysis_payload_is_consistent():
    need_awards()
    c = client(USER_A)
    t0 = time.time()
    st, a = call(c, "get", f"/api/market-radar/analysis?{HOUSING}")
    first = time.time() - t0
    assert st == 200, a
    s = a["summary"]
    if s["awards"] == 0:
        skip("no housing repair awards for London boroughs in this database")
    assert a["category"] == {"key": "preset:housing-repairs-gas", "label": "Housing repairs & gas servicing",
                             "preset": "housing-repairs-gas"}
    assert a["filters"] == {"authority": "london-borough", "authority_label": "London boroughs", "window": "3y",
                            "window_label": "Last 3 years", "scope": "all"}
    assert a["computed_at"].endswith("Z") and a["truncated"] is False and {"values", "size", "cost"} <= set(a["notes"])
    assert a["suppliers"]["total"] >= len(a["suppliers"]["rows"]) and len(a["suppliers"]["rows"]) <= 25
    buyer_counts = [r["buyers"] for r in a["suppliers"]["rows"]]
    assert buyer_counts == sorted(buyer_counts, reverse=True), "widest adoption first"

    # every award belongs to exactly one peer, and a peer is a distinct buyer
    awards, keys, page = 0, [], 1
    while True:
        st, p = call(c, "get", f"/api/market-radar/peers?{HOUSING}&per_page=100&page={page}")
        assert st == 200
        for row in p["rows"]:
            assert not any(k.startswith("_") for k in row), "private fields must not leave the server"
            assert row["is_me"] is False and row["type"] == "london-borough"
        awards += sum(r["awards"] for r in p["rows"])
        keys += [r["key"] for r in p["rows"]]
        if page >= p["pages"]:
            break
        page += 1
    assert awards == s["awards"], "peer awards add up to the summary"
    assert len(keys) == len(set(keys)) == s["buyers"] == a["peers"]["total"]

    cost = a["cost"]
    assert cost["excluded_frameworks"] == s["frameworks"]
    assert cost["overall"]["contracts"] <= s["awards"] - s["frameworks"]
    assert sum(b["contracts"] for b in cost["by_type"]) == cost["overall"]["contracts"]

    t0 = time.time()
    again = call(c, "get", f"/api/market-radar/analysis?{HOUSING}")[1]
    assert again["summary"] == s and again["computed_at"] == a["computed_at"], "the second request is served from the cache"
    assert time.time() - t0 < max(2.0, first), "cached"

    st, direct = call(c, "get", f"/api/market-radar/analysis?{HOUSING}&scope=direct")
    assert st == 200 and direct["summary"]["frameworks"] == 0
    # a call-off signed the same day as its framework appointment is one row under "all" (the dearer wins),
    # so leaving frameworks out can only bring rows back, never lose any
    assert direct["summary"]["awards"] >= s["awards"] - s["frameworks"]


def test_custom_categories():
    need_awards()
    c = client(USER_A)
    st, a = call(c, "get", "/api/market-radar/analysis?cpv=5072&q=boiler&window=5y")
    assert st == 200 and a["category"]["key"] == "cpv:5072|q:boiler" and a["category"]["cpv"] == "5072"
    assert a["category"]["label"].startswith(("Repair", "CPV 5072")) or "boiler" in a["category"]["label"]
    st, b = call(c, "get", "/api/market-radar/analysis?q=boiler&window=5y")
    assert st == 200 and b["summary"]["awards"] >= a["summary"]["awards"], "adding a CPV code can only narrow the search"


def test_request_validation():
    c = client(USER_C)
    base = "category=housing-repairs-gas"
    for qs in ("", "category=nope", "cpv=5", "cpv=abc", "q=!!", f"{base}&authority=wrong", f"{base}&authority=local-other",
               f"{base}&window=10y", f"{base}&scope=sideways", f"{base}&per_page=0", f"{base}&per_page=101",
               f"{base}&per_page=x", f"{base}&page=0", f"{base}&page=99999", f"{base}&sort=sideways"):
        st, j = call(c, "get", "/api/market-radar/analysis?" + qs)
        assert st == 400 and j.get("error"), (qs, st, j)
    assert call(c, "get", f"/api/market-radar/peers/awards?{base}")[0] == 400, "buyer is required"
    assert call(c, "get", f"/api/market-radar/peers/awards?{base}&buyer=NO%20SUCH%20BUYER")[0] == 404
    assert call(c, "get", "/api/market-radar/peers?category=nope")[0] == 400


def test_peer_paging_search_sort_and_detail():
    need_awards()
    c = client(USER_A)
    q = "category=housing-repairs-gas&authority=local-government&window=5y"
    st, p1 = call(c, "get", f"/api/market-radar/peers?{q}&per_page=5&page=1&sort=value")
    assert st == 200
    if p1["total"] < 7:
        skip("fewer than seven peers in this view")
    assert p1["per_page"] == 5 and p1["sort"] == "value" and len(p1["rows"]) == 5 and p1["pages"] == -(-p1["total"] // 5)
    values = [r["total_value"] or 0 for r in p1["rows"]]
    assert values == sorted(values, reverse=True)
    p2 = call(c, "get", f"/api/market-radar/peers?{q}&per_page=5&page=2&sort=value")[1]
    assert not {r["key"] for r in p1["rows"]} & {r["key"] for r in p2["rows"]}, "pages do not overlap"
    assert max(r["total_value"] or 0 for r in p2["rows"]) <= values[-1]

    names = [r["buyer"].lower() for r in call(c, "get", f"/api/market-radar/peers?{q}&per_page=100&sort=name")[1]["rows"]]
    assert names == sorted(names), "A to Z"
    counts = [r["awards"] for r in call(c, "get", f"/api/market-radar/peers?{q}&per_page=100&sort=awards")[1]["rows"]]
    assert counts == sorted(counts, reverse=True)

    word = re.sub(r"[^a-z]", "", p1["rows"][0]["buyer"].lower().split()[-1]) or "council"
    found = call(c, "get", f"/api/market-radar/peers?{q}&per_page=100&search={urllib.parse.quote(word)}")[1]
    assert found["rows"] and all(word in r["buyer"].lower() for r in found["rows"])
    assert call(c, "get", f"/api/market-radar/peers?{q}&search=zzqxvzzqxv")[1]["rows"] == []

    peer = p1["rows"][0]
    st, d = call(c, "get", f"/api/market-radar/peers/awards?{q}&buyer={urllib.parse.quote(peer['key'])}")
    assert st == 200 and d["total_awards"] == peer["awards"] and d["shown"] == len(d["awards"]) <= 40
    for award in d["awards"]:
        assert {"title", "supplier", "value", "value_is_ceiling", "signed", "started", "ends", "route", "url"} <= set(award)
    signed = [x["signed"] or "" for x in d["awards"]]
    assert signed == sorted(signed, reverse=True), "newest first"


# ─────────────────────────────────── organisation, watchlist, dashboard ──────────────────────────

def test_organisation_watchlist_and_dashboard():
    c = client(USER_A)
    st, me_ = call(c, "get", "/api/buyer-workspace/me")
    assert st == 200 and me_["username"] == USER_A and me_["organisation"] is None and me_["watchlist"] == []
    assert call(c, "get", "/api/buyer-workspace/organisations?q=a")[1]["results"] == [], "one letter is not a search"
    st, found = call(c, "get", "/api/buyer-workspace/organisations?q=camden")
    if not found["results"]:
        skip("no organisation matching 'camden' in this database")
    org = found["results"][0]
    assert {"name", "key", "type", "type_label", "buyer_type", "contracts"} <= set(org)
    keys = [o["key"] for o in found["results"]]
    assert len(keys) == len(set(keys)), "spelling variants of one council are merged"

    st, j = call(c, "put", "/api/buyer-workspace/organisation", {"name": org["name"]})
    assert st == 200 and j["organisation"]["name"] == org["name"]
    assert call(c, "put", "/api/buyer-workspace/organisation", {"name": "Nonexistent Council XYZ"})[0] == 404
    assert call(c, "put", "/api/buyer-workspace/organisation", {"name": 12345})[0] == 400
    assert call(c, "put", "/api/buyer-workspace/organisation", {"name": "x" * 301})[0] == 400
    assert call(c, "get", "/api/buyer-workspace/me")[1]["organisation"]["name"] == org["name"], "bad requests change nothing"

    wanted = [{"preset": "housing-repairs-gas"}, {"cpv": "5072"}, {"q": "tree surgery"}, {"preset": "housing-repairs-gas"}]
    st, w = call(c, "put", "/api/buyer-workspace/watchlist", {"categories": wanted})
    assert st == 200
    expected_keys = ["preset:housing-repairs-gas", "cpv:5072", "q:tree surgery"]
    assert [x["key"] for x in w["watchlist"]] == expected_keys, "duplicates collapse"
    assert [x["key"] for x in call(c, "get", "/api/buyer-workspace/me")[1]["watchlist"]] == expected_keys
    for bad in ({}, {"categories": "x"}, {"categories": ["x"]}, {"categories": [{"preset": "nope"}]},
                {"categories": [{"cpv": str(50 + i)} for i in range(13)]}):
        st, j = call(c, "put", "/api/buyer-workspace/watchlist", bad)
        assert st == 400 and j.get("error"), bad
    assert [x["key"] for x in call(c, "get", "/api/buyer-workspace/me")[1]["watchlist"]] == expected_keys

    st, d = call(c, "get", "/api/buyer-workspace/dashboard")
    assert st == 200 and d["unavailable"] == [], d["unavailable"]
    assert d["organisation"]["name"] == org["name"] and d["watchlist"]["count"] == 3
    assert d["engagements"] == {"active": 0, "total": 0, "by_status": {}, "recent": []}
    assert d["renewals"]["days"] == 183 and d["renewals"]["count"] >= len(d["renewals"]["items"])
    for item in d["renewals"]["items"]:
        assert 0 <= item["days_left"] <= 183 and item["ends"]
    for item in d["peer_activity"]:
        assert item["buyer"] != org["name"] and item["category_key"] in expected_keys

    st, j = call(c, "put", "/api/buyer-workspace/organisation", {"name": ""})
    assert st == 200 and j["organisation"] is None
    d = call(c, "get", "/api/buyer-workspace/dashboard")[1]
    assert d["organisation"] is None and d["renewals"] is None and d["peer_activity"] == []
    assert call(c, "put", "/api/buyer-workspace/watchlist", {"categories": []})[1]["watchlist"] == []


def test_my_organisation_is_marked_among_the_peers():
    need_awards()
    c = client(USER_A)
    rows = call(c, "get", f"/api/market-radar/peers?{HOUSING}&per_page=100")[1].get("rows", [])
    if not rows:
        skip("no London borough housing repair awards in this database")
    # choose, as "my organisation", a buyer that really appears among the peers (found the way a user would)
    mine = None
    for peer in rows[:5]:
        word = max(peer["key"].split(), key=len).lower()
        found = call(c, "get", "/api/buyer-workspace/organisations?q=" + urllib.parse.quote(word))[1].get("results", [])
        mine = next((o for o in found if o["key"] == peer["key"]), None)
        if mine:
            break
    if not mine:
        skip("none of the first peers can be found in the organisation search")
    assert call(c, "put", "/api/buyer-workspace/organisation", {"name": mine["name"]})[0] == 200
    try:
        marked = [r for r in call(c, "get", f"/api/market-radar/peers?{HOUSING}&per_page=100")[1]["rows"] if r["is_me"]]
        assert [r["key"] for r in marked] == [mine["key"]], "exactly one peer is marked as the user's own organisation"
        analysis = call(c, "get", f"/api/market-radar/analysis?{HOUSING}")[1]
        assert [r["key"] for r in analysis["peers"]["rows"] if r["is_me"]] in ([], [mine["key"]])
    finally:
        call(c, "put", "/api/buyer-workspace/organisation", {"name": ""})
    assert not [r for r in call(c, "get", f"/api/market-radar/peers?{HOUSING}&per_page=100")[1]["rows"] if r["is_me"]]


# ───────────────────────────────────── market engagement ─────────────────────────────────────────

def _plan_body(**extra):
    body = {
        "title": "Responsive repairs and gas servicing: 2027 refresh", "category": {"preset": "housing-repairs-gas"},
        "organisation": "London Borough of Probe", "est_value": 2_100_000, "term_years": 4, "engagement_type": "both",
        "supplier_day_at": "20 Nov 2026, 10:00", "supplier_day_place": "Town Hall, Room 2",
        "response_deadline": "2026-11-14", "contact_name": "Sarah Jones", "contact_email": "sarah@test.invalid",
    }
    body.update(extra)
    return body


def test_engagement_validation_and_isolation():
    a, b = client(USER_A), client(USER_B)
    st, plan = call(a, "post", "/api/market-engagement", _plan_body())
    assert st == 201 and plan["status"] == "draft" and "username" not in plan
    assert plan["category"] == {"key": "preset:housing-repairs-gas", "label": "Housing repairs & gas servicing",
                                "preset": "housing-repairs-gas"}
    assert [s["state"] for s in plan["steps"]] == ["done", "active", "upcoming", "upcoming", "upcoming"]
    assert plan["est_value"] == 2_100_000 and plan["engagement_type_label"] == "Questionnaire and supplier day"
    pid = plan["id"]
    try:
        cat = {"preset": "housing-repairs-gas"}
        for body in ({"title": "ab", "category": cat}, {"title": "Valid title"}, {"title": "Valid title", "category": {"preset": "nope"}},
                     {"title": "Valid title", "category": {}}, {"title": "Valid title", "category": cat, "est_value": -5},
                     {"title": "Valid title", "category": cat, "contact_email": "nope"}, {"category": cat}):
            st, j = call(a, "post", "/api/market-engagement", body)
            assert st == 400 and j.get("error"), body
        for patch in ({"contact_email": "nope"}, {"term_years": 99}, {"category": {"preset": "nope"}},
                      {"status": "converted"}, {"username": USER_B}, {}):
            st, j = call(a, "patch", f"/api/market-engagement/{pid}", patch)
            assert st == 400 and j.get("error"), patch
        assert call(a, "get", f"/api/market-engagement/{pid}")[1]["status"] == "draft", "workflow fields cannot be edited"

        url = f"/api/market-engagement/{pid}"
        for method, path, body in (
            ("get", url, None), ("patch", url, {"title": "Hijacked title"}), ("delete", url, None),
            ("get", url + "/notice", None), ("get", url + "/handoff", None), ("post", url + "/publish", {}),
            ("post", url + "/suppliers", {"name": "Intruder Ltd"}), ("post", url + "/suppliers/match", {}),
            ("post", url + "/log", {"message": "hello"}), ("patch", url + "/suppliers/1", {"note": "x"}),
        ):
            assert call(b, method, path, body)[0] == 404, f"another user could reach {method.upper()} {path}"
        assert call(b, "get", "/api/market-engagement")[1]["plans"] == []
        assert [p["id"] for p in call(a, "get", "/api/market-engagement")[1]["plans"]] == [pid]
        assert call(a, "get", "/api/market-engagement/abc")[0] == 404 and call(a, "get", "/api/market-engagement/99999999")[0] == 404
    finally:
        call(a, "delete", f"/api/market-engagement/{pid}")


def test_engagement_picks_up_the_saved_organisation():
    c = client(USER_C)
    plan = call(c, "post", "/api/market-engagement", _plan_body(organisation=None))[1]
    try:
        assert plan["organisation"] is None
        conn = db()
        try:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO user_prefs (username, pref_key, pref_value, updated_at) VALUES (%s, 'buyer_org_name', %s, NOW())",
                            (USER_C, "London Borough Of Test"))
            conn.commit()
        finally:
            conn.close()
        other = call(c, "post", "/api/market-engagement", _plan_body(organisation=None))[1]
        assert other["organisation"] == "London Borough Of Test"
        call(c, "delete", f"/api/market-engagement/{other['id']}")
    finally:
        call(c, "delete", f"/api/market-engagement/{plan['id']}")


def test_engagement_lifecycle():
    c = client(USER_A)
    pid = call(c, "post", "/api/market-engagement", _plan_body())[1]["id"]
    url = f"/api/market-engagement/{pid}"
    try:
        # suppliers: shortlist from the award history, then by hand
        st, m = call(c, "post", url + "/suppliers/match", {"window": "5y"})
        assert st == 200 and m["basis"] == "Last 5 years" and m["smaller_first_is_a_proxy"] is False
        if m["candidates"]:
            assert 0 < m["added"] <= m["candidates"] <= 40 and all(s["source"] == "matched" for s in m["suppliers"])
            assert call(c, "post", url + "/suppliers/match", {"window": "5y"})[1]["added"] == 0, "matching twice adds nothing new"
        assert call(c, "post", url + "/suppliers/match", {"window": "10y"})[0] == 400
        st, j = call(c, "post", url + "/suppliers", {"name": "Local Plumbing Co Ltd"})
        assert st == 201 and j["added"] is True
        st, j = call(c, "post", url + "/suppliers", {"name": "LOCAL PLUMBING CO LIMITED"})
        assert st == 200 and j["added"] is False, "the same supplier under another spelling is not added twice"
        assert call(c, "post", url + "/suppliers", {"name": "x"})[0] == 400
        mine = next(s for s in j["suppliers"] if s["supplier_name"] == "Local Plumbing Co Ltd")
        assert mine["source"] == "manual" and mine["status"] == "not_contacted" and mine["included"] is True
        sup = f"{url}/suppliers/{mine['id']}"
        st, j = call(c, "patch", sup, {"status": "invited", "note": "Phoned 2 Nov"})
        assert st == 200
        updated = next(s for s in j["suppliers"] if s["id"] == mine["id"])
        assert updated["status"] == "invited" and updated["note"] == "Phoned 2 Nov"
        for bad in ({"status": "ghosted"}, {"included": "yes"}, {}, {"note": "x" * 1001}):
            assert call(c, "patch", sup, bad)[0] == 400, bad
        assert call(c, "patch", f"{url}/suppliers/99999999", {"note": "x"})[0] == 404

        # notice: drafted, saved, then published
        st, n = call(c, "get", url + "/notice")
        assert st == 200 and n["saved"] is False and n["text"].startswith("PRELIMINARY MARKET ENGAGEMENT NOTICE (DRAFT)")
        assert "Procurement Act 2023" in n["text"] and n["missing"] == []
        st, j = call(c, "post", url + "/publish", {})
        assert st == 409 and "notice" in j["error"].lower()
        assert call(c, "patch", url, {"notice_text": n["text"]})[0] == 200
        assert call(c, "get", url + "/notice")[1]["saved"] is True
        st, j = call(c, "post", url + "/publish", {"url": "javascript:alert(1)"})
        assert st == 400 and "http" in j["error"]
        assert call(c, "get", url)[1]["status"] == "draft", "a refused request changes nothing"
        st, j = call(c, "post", url + "/publish", {"url": "https://www.find-tender.service.gov.uk/Notice/000000-2026"})
        assert st == 200 and j["status"] == "published" and j["published_at"] and j["allowed_transitions"] == ["closed", "converted"]
        assert call(c, "post", url + "/publish", {})[0] == 409, "already published"

        d = call(c, "get", "/api/buyer-workspace/dashboard")[1]
        assert d["engagements"]["active"] == 1 and d["engagements"]["by_status"] == {"published": 1}
        assert d["engagements"]["recent"][0]["id"] == pid

        # record, close, hand over
        st, j = call(c, "post", url + "/log", {"message": "Three questionnaires back"})
        assert st == 201 and j["log"][-1]["message"] == "Three questionnaires back" and j["log"][-1]["kind"] == "note"
        assert call(c, "post", url + "/log", {"message": "   "})[0] == 400
        st, j = call(c, "post", url + "/close")
        assert st == 200 and j["status"] == "closed" and j["closed_at"] and j["allowed_transitions"] == ["converted", "published"]
        st, j = call(c, "post", url + "/convert")
        assert st == 200 and j["status"] == "converted" and j["converted_at"] and j["allowed_transitions"] == []
        assert [s["state"] for s in j["steps"]] == ["done"] * 5
        assert call(c, "post", url + "/close")[0] == 409 and call(c, "post", url + "/publish", {})[0] == 409

        for method, path, body in (("patch", url, {"title": "Changed title"}), ("post", url + "/suppliers", {"name": "Late Addition Ltd"}),
                                   ("patch", sup, {"note": "late"}), ("delete", sup, None), ("post", url + "/suppliers/match", {})):
            assert call(c, method, path, body)[0] == 409, f"{method.upper()} {path} must be refused once handed over"
        d = call(c, "get", "/api/buyer-workspace/dashboard")[1]
        assert d["engagements"]["active"] == 0 and d["engagements"]["by_status"] == {"converted": 1}

        st, h = call(c, "get", url + "/handoff")
        assert st == 200 and re.fullmatch(r"market-engagement-[a-z0-9-]+\.md", h["filename"])
        md = h["markdown"]
        assert md.startswith("# Market engagement hand-over: Responsive repairs and gas servicing")
        assert "| Local Plumbing Co Ltd | Invited | Phoned 2 Nov |" in md and "Three questionnaires back" in md
        assert "find-tender.service.gov.uk/Notice/000000-2026" in md and "Not legal advice" in md

        log = call(c, "get", url)[1]["log"]
        assert [e["kind"] for e in log][0] == "created" and {"suppliers", "supplier", "notice", "published", "closed", "converted", "note"} <= {e["kind"] for e in log}
        assert [e["at"] for e in log] == sorted(e["at"] for e in log)

        conn = db()
        try:
            with conn.cursor() as cur:
                assert call(c, "delete", url)[0] == 200 and call(c, "get", url)[0] == 404
                cur.execute("SELECT (SELECT COUNT(*) FROM market_engagement_suppliers WHERE engagement_id = %s), "
                            "(SELECT COUNT(*) FROM market_engagement_log WHERE engagement_id = %s)", (pid, pid))
                assert cur.fetchone() == (0, 0), "deleting a plan deletes its suppliers and record"
        finally:
            conn.close()
        assert call(c, "delete", url)[0] == 404
    finally:
        call(c, "delete", url)


# ───────────────────────────────────────────── runner ────────────────────────────────────────────

def cleanup():
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM market_engagements WHERE username = ANY(%s)", (USERS,))
            cur.execute("DELETE FROM user_prefs WHERE username = ANY(%s)", (USERS,))
        conn.commit()
    finally:
        conn.close()


def main() -> None:
    try:
        db().close()
    except Exception as exc:  # noqa: BLE001
        print(f"SKIP: the database is not reachable from here ({type(exc).__name__}: {exc})")
        return
    app()  # a failure to build the app is a real failure, not a reason to skip
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    tests.sort(key=lambda item: item[1].__code__.co_firstlineno)
    failed, skipped = [], []
    try:
        for name, fn in tests:
            started = time.time()
            try:
                fn()
                print(f"ok    {name} ({time.time() - started:.1f}s)")
            except Skip as why:
                skipped.append(name)
                print(f"skip  {name}: {why}")
            except Exception:  # noqa: BLE001 - report every failure, not just the first
                failed.append(name)
                print(f"FAIL  {name}")
                traceback.print_exc()
    finally:
        cleanup()
    print(f"\n{len(tests) - len(failed) - len(skipped)} passed, {len(skipped)} skipped, {len(failed)} failed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
