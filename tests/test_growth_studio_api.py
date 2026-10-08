"""Run: python tests/test_growth_studio_api.py   (plain asserts; needs the dev database, skips itself without one)

Drives the real Flask app through its test client as throwaway users (zz_growth_test_*) against
fixture rows it inserts and removes again (awards for "ZZ Growth Test ..." buyers, planning rows
"zz_growth_test_*", published engagements, Pipeline entries, company profiles, Answer Bank entries),
so the assertions are exact and it is safe to run against the local dev database:

  * login and CSRF are enforced, the feature flag hides everything, the options and context are right,
  * renewal / development / engagement signals follow the fixtures: windows, frameworks, categories,
    exclusions, ranking, who is targetable, and what other users can never see,
  * dismissing, opting out, campaigns from signals, targets, status history, the do-not-contact list,
  * the message: preview, template and AI drafts (credits charged once, refunded on any failure), CSV and
    Mailchimp exports, and Pipeline results,
  * every campaign belongs to its owner: another user gets a 404 on all of it.

It never calls a real AI provider and never sends mail.
"""
import csv
import io
import json
import os
import re
import subprocess
import sys
import time
import traceback
import urllib.parse
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# create_app() starts the email scheduler, which really sends mail to the addresses in the dev
# database, unless it is switched off. These must be set before server / tender_app.config import.
os.environ["ENABLE_EMAIL_SCHEDULER"] = "0"
os.environ["ENABLE_SCHEDULERS"] = "0"
os.environ["ENABLE_GROWTH_STUDIO"] = "1"

RUN = uuid.uuid4().hex[:8]
USER_A = f"zz_growth_test_{RUN}_a"   # the main flow: profiles, signals, campaigns
USER_B = f"zz_growth_test_{RUN}_b"   # another tenant: publishes engagements, must never see A's data
USER_C = f"zz_growth_test_{RUN}_c"   # request validation
USER_D = f"zz_growth_test_{RUN}_d"   # AI drafts and credits (no company profile at all)
USER_E = f"zz_growth_test_{RUN}_e"   # categories and authority types against the real awards
USER_F = f"zz_growth_test_{RUN}_f"   # performance and Pipeline
USERS = [USER_A, USER_B, USER_C, USER_D, USER_E, USER_F]
CSRF = "test-csrf-token"
H = {"X-CSRF-Token": CSRF}

TODAY = date.today()
WHERE = "ZZ Growth Test"
ALPHA = "ZZ Growth Test Alpha Borough Council"
BETA = "ZZ Growth Test Beta District Council"
GAMMA = "ZZ Growth Test Gamma NHS Trust"
ALPHA_KEY = "ZZ GROWTH TEST ALPHA"
BETA_KEY = "ZZ GROWTH TEST BETA"
PROFILE_TEXT = "We service gas boilers and heating systems for housing providers."
FILTERS = {"preset": "housing-repairs-gas", "types": ["renewal", "development"], "days": 180, "where": WHERE}
RENEWALS = {"preset": "housing-repairs-gas", "types": ["renewal"], "days": 180, "where": WHERE}


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


def reset_limits():
    """The rate limiter is per user and per route and lives in memory; each test starts with a clean slate."""
    from tender_app import security
    security._buckets.clear()


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


def qs(**params):
    flat = {k: (",".join(v) if isinstance(v, (list, tuple)) else v) for k, v in params.items() if v is not None}
    return urllib.parse.urlencode(flat)


def get_signals(c, **params):
    return call(c, "get", "/api/growth/signals?" + qs(**{**RENEWALS, **params}))


def by_buyer(signals, name):
    return next((s for s in signals if s["buyer"] == name), None)


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ─────────────────────────────────────────── fixtures ─────────────────────────────────────────────

def iso(days):
    return (TODAY + timedelta(days=days)).isoformat()


def cleanup():
    conn = db()
    try:
        with conn.cursor() as cur:
            for table in ("growth_campaigns", "growth_signal_state", "growth_suppressions", "user_prefs", "answer_bank", "pipeline",
                          "market_engagements", "credit_ledger", "credit_wallet", "company_profiles"):
                cur.execute(f"DELETE FROM {table} WHERE username = ANY(%s)", (USERS,))
            cur.execute("DELETE FROM contract_awards WHERE authority_name LIKE 'ZZ Growth Test%'")
            cur.execute("DELETE FROM planning_applications WHERE id LIKE 'zz_growth_test_%'")
        conn.commit()
    finally:
        conn.close()


FIXTURE_IDS = {}


def make_fixtures():
    conn = db()
    try:
        with conn.cursor() as cur:
            def award(authority, title, value, end_days, supplier="ZZ Growth Test Heating Ltd", cpv="50721000", framework=0,
                      buyer_type="Local Government / Council", currency="GBP", portal="Contracts Finder"):
                start = TODAY + timedelta(days=end_days - 1096)
                cur.execute(
                    """INSERT INTO contract_awards (supplier_name, authority_name, tender_title, cpv_code, contract_value, currency,
                           date_signed, contract_start_date, contract_end_date, procurement_type, is_framework, buyer_type,
                           source_portal, notice_url)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'Open', %s, %s, %s, %s)""",
                    (supplier, authority, title, cpv, value, currency, start.isoformat(), start.isoformat(), iso(end_days),
                     framework, buyer_type, portal, f"https://example.org/zz-growth/{abs(hash((authority, title, supplier))) % 10**8}"),
                )

            award(ALPHA, "ZZ gas servicing and boiler maintenance", 90000, 91)
            award(ALPHA, "ZZ heating maintenance framework", 5_000_000, 120, supplier="ZZ Growth Alpha Ltd", framework=1)
            award(ALPHA, "ZZ heating maintenance framework", 5_000_000, 120, supplier="ZZ Growth Beta Ltd", framework=1)
            award(ALPHA, "ZZ boiler replacement works", 20000, -1)                       # already ended
            award(ALPHA, "ZZ gas safety programme", 40000, 400)                          # beyond a 180 day window
            award(ALPHA, "ZZ boiler EUR contract", 10000, 50, currency="EUR")            # not GBP
            award(ALPHA, "ZZ boiler elsewhere", 10000, 50, portal="Some Other Portal")   # not a UK / Ireland portal
            award(ALPHA, "ZZ website redesign", 10000, 30, cpv="72000000")               # another category
            award(BETA, "ZZ gas safety checks", 30000, 45)
            award(GAMMA, "ZZ boiler servicing", 60000, 60, buyer_type="NHS & Healthcare")

            def plan(n, authority="ZZ Growth Test Authority", **kw):
                row = dict(app_size="Large", app_state="Permitted", app_type="Outline", n_dwellings=150, low_value_reason=None,
                           applicant_company=None, agent_company="ZZ Growth Agent LLP", decided=-30, start=-300, lead_score=80,
                           description="Outline planning application for up to 150 dwellings", address=f"Land at Test Lane {n}")
                row.update(kw)
                cur.execute(
                    """INSERT INTO planning_applications (id, uid, authority, country, region, description, address, app_size, app_state,
                           app_type, n_dwellings, low_value_reason, applicant_company, agent_company, decided_date, start_date,
                           lead_score, detail_url)
                       VALUES (%s, %s, %s, 'England', 'East Midlands', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (f"zz_growth_test_{n}", f"ZZ/{n}", authority, row["description"], row["address"], row["app_size"], row["app_state"],
                     row["app_type"], row["n_dwellings"], row["low_value_reason"], row["applicant_company"], row["agent_company"],
                     None if row["decided"] is None else TODAY + timedelta(days=row["decided"]), TODAY + timedelta(days=row["start"]),
                     row["lead_score"], f"https://example.org/zz-plan/{n}"),
                )

            plan(1, applicant_company="ZZ Growth Test Homes Ltd")
            plan(2, n_dwellings=60, decided=-60)
            plan(3, description="EIA Screening Opinion for 300 dwellings", decided=-10, applicant_company="ZZ Screening Ltd")
            plan(4, app_state="Undecided", decided=None, applicant_company="ZZ Undecided Ltd")
            plan(5, low_value_reason="minor_works", applicant_company="ZZ Minor Ltd")
            plan(6, app_size="Medium", n_dwellings=5, applicant_company="ZZ Small Ltd")
            plan(7, decided=-400, applicant_company="ZZ Growth Test Old Homes Ltd")
            plan(8, app_state="Conditions", n_dwellings=40, decided=-20, applicant_company="Mr J Smith", address="Plot 1 Test Road")

            def profile(user, name, text, default, meta=None):
                cur.execute(
                    """INSERT INTO company_profiles (username, name, profile_text, meta_json, is_default)
                       VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                    (user, name, text, json.dumps(meta or {}), default),
                )
                return cur.fetchone()[0]

            FIXTURE_IDS["A1"] = profile(USER_A, "ZZ Growth Profile One", PROFILE_TEXT, True, {
                "org_name": "ZZ Growth Heating Ltd", "contact_name": "Sam Ray", "phone": "01632 960001", "email": "sam@zz-growth.invalid"})
            FIXTURE_IDS["A2"] = profile(USER_A, "ZZ Growth Profile Two", "We build bespoke software and websites.", False)
            FIXTURE_IDS["B1"] = profile(USER_B, "ZZ Growth Profile Of B", "Plumbing contractor", True)

            def answer(user, category, question, text):
                cur.execute("INSERT INTO answer_bank (username, category, question, answer) VALUES (%s, %s, %s, %s)", (user, category, question, text))

            answer(USER_A, "Accreditations", "Which accreditations do you hold?", "ZZ Gas Safe registered and NICEIC approved")
            answer(USER_A, "Pricing", "What are your day rates?", "ZZ secret day rates")
            answer(USER_B, "Accreditations", "Which accreditations do you hold?", "ZZ B-only accreditation")

            def engagement(status="published", url="https://example.org/zz-notice/1", org=ALPHA, deadline=20, preset="housing-repairs-gas", title="ZZ responsive repairs"):
                cur.execute(
                    """INSERT INTO market_engagements (username, organisation, title, category_json, category_label, engagement_type,
                           supplier_day_at, supplier_day_place, response_deadline, contact_name, contact_email, status, published_url, published_at)
                       VALUES (%s, %s, %s, %s, 'label', 'supplier_day', '2026-11-10 10:00', 'Town Hall', %s, 'Pat Buyer',
                               'secret@zz.invalid', %s, %s, NOW()) RETURNING id""",
                    (USER_B, org, title, json.dumps({"preset": preset}), None if deadline is None else TODAY + timedelta(days=deadline), status, url),
                )
                return cur.fetchone()[0]

            FIXTURE_IDS["E1"] = engagement()
            engagement(status="draft", title="ZZ draft")
            engagement(url="", title="ZZ no link")
            engagement(status="closed", title="ZZ closed")
            engagement(deadline=-1, title="ZZ past deadline")
            engagement(org=None, title="ZZ no organisation")
            FIXTURE_IDS["E6"] = engagement(org=GAMMA, preset="crm-case-management", title="ZZ crm engagement", url="https://example.org/zz-notice/6")
        conn.commit()
    finally:
        conn.close()


def make_campaign(c, keys, filters=None, **extra):
    st, body = call(c, "post", "/api/growth/campaigns", {"keys": keys, "filters": filters or FILTERS, **extra})
    assert st == 201, (st, body)
    return body


def signal_keys(c, *names, **params):
    st, body = get_signals(c, **params)
    assert st == 200, body
    return [by_buyer(body["signals"], n)["key"] for n in names]


# ─────────────────────────────────────────────── access ───────────────────────────────────────────

def test_login_and_csrf_are_required():
    reset_limits()
    anon = client()
    for url in ("/api/growth/options", "/api/growth/context", "/api/growth/signals?preset=housing-repairs-gas", "/api/growth/suppressions",
                "/api/growth/campaigns", "/api/growth/campaigns/1", "/api/growth/campaigns/1/export", "/api/growth/performance"):
        assert anon.get(url).status_code == 401, url
    c = client(USER_C)
    writes = (
        ("put", "/api/growth/targeting", {"filters": RENEWALS}),
        ("put", "/api/growth/signals/state", {"key": "renewal:X", "dismissed": True}),
        ("post", "/api/growth/campaigns", {"keys": ["renewal:X"], "filters": RENEWALS}),
        ("patch", "/api/growth/campaigns/1", {"name": "x"}),
        ("delete", "/api/growth/campaigns/1", None),
        ("post", "/api/growth/campaigns/1/targets", {"keys": ["renewal:X"], "filters": RENEWALS}),
        ("patch", "/api/growth/campaigns/1/targets/1", {"included": False}),
        ("delete", "/api/growth/campaigns/1/targets/1", None),
        ("post", "/api/growth/campaigns/1/mark-sent", {}),
        ("post", "/api/growth/campaigns/1/preview", {}),
        ("post", "/api/growth/campaigns/1/draft", {"mode": "template"}),
        ("delete", "/api/growth/suppressions?key=x", None),
    )
    for method, url, body in writes:
        assert call(c, method, url, body, headers={})[0] == 403, f"{url} accepted a request without a CSRF token"
        assert call(c, method, url, body, headers={"X-CSRF-Token": "wrong"})[0] == 403, url


def test_feature_flag_off_hides_growth_studio():
    probe = (
        "import json, time\n"
        "from server import create_app\n"
        "c = create_app().test_client()\n"
        "with c.session_transaction() as s:\n"
        "    s.update(logged_in=True, username='zz_growth_test_flag', last_activity=time.time(), csrf_token='t')\n"
        "print('PROBE' + json.dumps({'options': c.get('/api/growth/options').status_code,\n"
        "    'campaigns': c.get('/api/growth/campaigns').status_code,\n"
        "    'files': [c.get(p).status_code for p in ('/growth-studio.js', '/growth-view.js', '/growth-studio.css')],\n"
        "    'features': c.get('/api/config/public').get_json()['features']}))\n"
    )
    env = dict(os.environ, ENABLE_GROWTH_STUDIO="0", ENABLE_EMAIL_SCHEDULER="0", ENABLE_SCHEDULERS="0", PYTHONPATH=str(ROOT))
    run = subprocess.run([sys.executable, "-c", probe], cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=240)
    line = next((ln for ln in run.stdout.splitlines() if ln.startswith("PROBE")), None)
    assert line, f"the probe printed no result:\n{run.stdout[-800:]}\n{run.stderr[-800:]}"
    assert json.loads(line[len("PROBE"):]) == {
        "options": 404, "campaigns": 404, "files": [404, 404, 404], "features": {"buyerWorkspace": True, "growthStudio": False},
    }


def test_page_files_are_served_and_the_flag_is_public():
    c = client(USER_C)
    for path in ("/growth-studio.js", "/growth-view.js", "/growth-studio.css"):
        r = c.get(path)
        assert r.status_code == 200 and len(r.data) > 1000, path
    assert call(c, "get", "/api/config/public")[1]["features"] == {"buyerWorkspace": True, "growthStudio": True}
    html = c.get("/").get_data(as_text=True)
    for fragment in ('id="growthStudioModal"', 'id="btnGrowthStudio"', "/growth-view.js", "/growth-studio.js", "/growth-studio.css"):
        assert fragment in html, fragment


# ───────────────────────────────────── options, context, targeting ────────────────────────────────

def test_options():
    from tender_app import growth_studio as gs, market_radar as mr
    from tender_app.config import CREDIT_COST_GROWTH_DRAFT
    st, o = call(client(USER_C), "get", "/api/growth/options")
    assert st == 200
    assert [p["preset"] for p in o["presets"]] == [p["id"] for p in mr.CATEGORY_PRESETS]
    assert [a["id"] for a in o["authority_types"]] == [t for t, _ in mr.AUTHORITY_TYPES]
    assert [t["id"] for t in o["signal_types"]] == list(gs.SIGNAL_TYPES) and o["day_choices"] == [90, 120, 180, 270, 365]
    assert [f["name"] for f in o["merge_fields"]] == [m["name"] for m in gs.MERGE_FIELDS]
    assert o["credit_cost_draft"] == CREDIT_COST_GROWTH_DRAFT and o["opt_out_line"] == gs.OPT_OUT_LINE and o["max_per_type"] == gs.MAX_PER_TYPE
    assert [c["id"] for c in o["channels"]] == list(gs.CHANNELS) and o["target_statuses"] == list(gs.TARGET_STATUSES)


def test_context_profiles_and_saved_targeting():
    c = client(USER_A)
    st, ctx = call(c, "get", "/api/growth/context")
    assert st == 200
    assert [p["name"] for p in ctx["profiles"]] == ["ZZ Growth Profile One", "ZZ Growth Profile Two"], "the default profile comes first"
    assert ctx["profiles"][0]["is_default"] and ctx["profile_id"] == FIXTURE_IDS["A1"] and all(p["has_description"] for p in ctx["profiles"])
    assert ctx["filters"] is None and ctx["category"] is None
    assert ctx["suggestions"][0]["preset"] == "housing-repairs-gas" and {"gas", "boil", "heat"} <= set(ctx["suggestions"][0]["matches"])

    saved = {**RENEWALS, "authority": "nhs", "frameworks": False}
    st, body = call(c, "put", "/api/growth/targeting", {"profile_id": FIXTURE_IDS["A2"], "filters": saved})
    assert st == 200 and body["category"]["preset"] == "housing-repairs-gas" and body["filters"]["authority"] == "nhs"
    ctx = call(c, "get", "/api/growth/context")[1]
    assert ctx["profile_id"] == FIXTURE_IDS["A2"], "the last profile used is remembered"
    assert ctx["filters"] == body["filters"] == {**saved, "types": ["renewal"]} and ctx["category"]["label"] == "Housing repairs & gas servicing"
    suggested = [s["preset"] for s in ctx["suggestions"]]
    assert "housing-repairs-gas" not in suggested and "crm-case-management" in suggested, "suggestions follow the chosen profile's own words"

    other = {**RENEWALS, "days": 90}
    call(c, "put", "/api/growth/targeting", {"profile_id": FIXTURE_IDS["A1"], "filters": other})
    assert call(c, "get", "/api/growth/context")[1]["filters"]["days"] == 90
    call(c, "put", "/api/growth/targeting", {"profile_id": FIXTURE_IDS["A2"], "filters": saved})
    assert call(c, "get", "/api/growth/context")[1]["filters"]["authority"] == "nhs", "each profile keeps its own filters"
    st, asked = call(c, "get", f"/api/growth/context?profile_id={FIXTURE_IDS['A1']}")
    assert st == 200 and asked["profile_id"] == FIXTURE_IDS["A1"] and asked["filters"]["days"] == 90, "a profile's own saved filters, on request"
    assert call(c, "get", "/api/growth/context")[1]["profile_id"] == FIXTURE_IDS["A2"], "asking for a profile does not change the remembered one"
    for bad, status in (("99999999", 404), (str(FIXTURE_IDS["B1"]), 404), ("abc", 400)):
        assert call(c, "get", f"/api/growth/context?profile_id={bad}")[0] == status, bad
    call(c, "put", "/api/growth/targeting", {"profile_id": FIXTURE_IDS["A1"], "filters": RENEWALS})

    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE user_prefs SET pref_value = 'not json' WHERE username = %s AND pref_key = %s", (USER_A, f"growth_targeting_{FIXTURE_IDS['A1']}"))
        conn.commit()
    finally:
        conn.close()
    ctx = call(c, "get", "/api/growth/context")[1]
    assert ctx["filters"] is None and ctx["profile_id"] == FIXTURE_IDS["A1"], "unreadable saved filters are ignored, not an error"
    call(c, "put", "/api/growth/targeting", {"profile_id": FIXTURE_IDS["A1"], "filters": RENEWALS})

    none = call(client(USER_D), "get", "/api/growth/context")[1]
    assert none["profiles"] == [] and none["profile_id"] is None and none["suggestions"] == [], "a user with no profile still gets a page"


def test_targeting_validation():
    c = client(USER_C)
    for body, status in (({}, 400), ({"filters": "x"}, 400), ({"filters": {}}, 400), ({"filters": {"preset": "nope"}}, 400),
                         ({"filters": {**RENEWALS, "days": 5}}, 400), ({"profile_id": 99999999, "filters": RENEWALS}, 404),
                         ({"profile_id": FIXTURE_IDS["A1"], "filters": RENEWALS}, 404), ({"profile_id": "abc", "filters": RENEWALS}, 400),
                         ({"profile_id": True, "filters": RENEWALS}, 400)):
        st, resp = call(c, "put", "/api/growth/targeting", body)
        assert st == status and resp.get("error"), (body, st, resp)
    assert call(c, "put", "/api/growth/targeting", {"filters": RENEWALS})[0] == 200, "no profile is allowed"
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pref_key FROM user_prefs WHERE username = %s AND pref_key LIKE 'growth_%%'", (USER_C,))
            assert [r[0] for r in cur.fetchall()] == ["growth_targeting_0"], "nothing stored for the refused requests"
    finally:
        conn.close()


# ─────────────────────────────────────────── renewal signals ──────────────────────────────────────

def test_renewal_signals_follow_the_fixtures():
    reset_limits()
    c = client(USER_A)
    st, r = get_signals(c, profile_id=FIXTURE_IDS["A1"])
    assert st == 200
    assert [s["buyer"] for s in r["signals"]] == [ALPHA, GAMMA, BETA], "best fit first"
    assert r["counts"] == {"renewal": 3, "development": 0, "engagement": 0, "total": 3, "dismissed": 0}
    assert r["total"] == r["shown"] == 3 and r["profile"] == {"id": FIXTURE_IDS["A1"], "name": "ZZ Growth Profile One", "has_description": True}
    assert r["filters"]["category"]["preset"] == "housing-repairs-gas" and r["filters"]["authority_label"] == "All public bodies"
    alpha, gamma, beta = r["signals"]
    assert (alpha["fit"], gamma["fit"], beta["fit"]) == (94, 88, 75)
    assert alpha["fit"] == sum(p["points"] for p in alpha["fit_parts"]) and [p["key"] for p in alpha["fit_parts"]] == ["timing", "category", "profile", "evidence"]
    assert alpha["key"] == f"renewal:{ALPHA_KEY}" and alpha["type"] == "renewal" and alpha["band"] == "high" and gamma["band"] == "high" and beta["band"] == "high"
    assert alpha["days"] == 91 and alpha["date"] == iso(91) and alpha["authority"] == "District & borough councils" and gamma["authority"] == "NHS & healthcare"
    assert alpha["headline"].startswith("“ZZ gas servicing and boiler maintenance” contract ends in 91 days")
    assert "Incumbent: ZZ Growth Test Heating Ltd." in alpha["detail"] and "Last award on record: £90,000 (about £30,000 a year over 3 years)." in alpha["detail"]
    assert alpha["target"] == {"name": ALPHA, "key": ALPHA_KEY, "targetable": True, "reason": None}
    assert alpha["state"] == {"dismissed": False, "opted_out": False, "campaigns": []} and alpha["more_contracts"] == 0
    assert alpha["links"] and alpha["links"][0]["label"] == "Award notice" and alpha["links"][0]["url"].startswith("https://example.org/zz-growth/")

    contracts = {x["title"]: x for x in alpha["contracts"]}
    assert set(contracts) == {"ZZ gas servicing and boiler maintenance", "ZZ heating maintenance framework"}, "ended, far-off, EUR, other-portal and other-category awards are all left out"
    framework = contracts["ZZ heating maintenance framework"]
    assert framework["framework"] and framework["value"] is None and framework["annual_value"] is None and framework["supplier_count"] == 2
    assert sorted(framework["suppliers"]) == ["ZZ Growth Alpha Ltd", "ZZ Growth Beta Ltd"]
    assert "5,000,000" not in json.dumps(r), "a framework's shared value never reaches the browser"
    assert all(0 <= x["days_left"] <= 180 for s in r["signals"] for x in s["contracts"])
    assert r["notes"]["fit"] and r["notes"]["renewal"] and r["notes"]["values"] and r["notes"]["where"] and r["notes"]["development"] is None and r["notes"]["truncated"] is None


def test_renewal_filters():
    reset_limits()
    c = client(USER_A)
    far = get_signals(c, days=730)[1]
    assert {x["title"] for x in by_buyer(far["signals"], ALPHA)["contracts"]} == {
        "ZZ gas servicing and boiler maintenance", "ZZ heating maintenance framework", "ZZ gas safety programme"}
    assert by_buyer(far["signals"], ALPHA)["fit"] == 94, "the best-fitting contract still leads"
    near = get_signals(c, days=60)[1]
    assert [s["buyer"] for s in near["signals"]] == [GAMMA, BETA], "only contracts ending within 60 days"
    assert [s["buyer"] for s in get_signals(c, authority="nhs")[1]["signals"]] == [GAMMA]
    assert get_signals(c, authority="london-borough")[1]["signals"] == []
    assert {s["buyer"] for s in get_signals(c, authority="local-government")[1]["signals"]} == {ALPHA, BETA}
    assert [s["buyer"] for s in get_signals(c, authority="district")[1]["signals"]] == [ALPHA, BETA]
    no_frameworks = get_signals(c, frameworks="0")[1]
    assert [x["title"] for x in by_buyer(no_frameworks["signals"], ALPHA)["contracts"]] == ["ZZ gas servicing and boiler maintenance"]
    assert [s["buyer"] for s in get_signals(c, where="beta district")[1]["signals"]] == [BETA], "a place filter matches the buyer's name, any case"
    assert get_signals(c, where="no such buyer")[1]["signals"] == []
    assert [s["buyer"] for s in get_signals(c, where="ZZ Growth Test Gamma")[1]["signals"]] == [GAMMA]
    wild = get_signals(c, where="100% _")
    assert wild[0] == 200 and wild[1]["signals"] == [], "LIKE characters in the filter are matched literally"
    it = get_signals(c, preset="it-services-software")[1]
    assert [s["buyer"] for s in it["signals"]] == [ALPHA] and it["signals"][0]["contracts"][0]["title"] == "ZZ website redesign"
    custom = get_signals(c, preset=None, q="boiler")[1]
    assert {s["buyer"] for s in custom["signals"]} == {ALPHA, GAMMA} and custom["filters"]["category"]["q"] == "boiler"
    cpv = get_signals(c, preset=None, cpv="5072")[1]
    assert {s["buyer"] for s in cpv["signals"]} == {ALPHA, BETA, GAMMA} and "5072" in json.dumps(cpv["filters"]["category"])


def test_a_profile_changes_the_fit_but_not_the_signals():
    reset_limits()
    c = client(USER_A)
    one = get_signals(c, profile_id=FIXTURE_IDS["A1"])[1]
    two = get_signals(c, profile_id=FIXTURE_IDS["A2"])[1]
    assert {s["buyer"] for s in one["signals"]} == {s["buyer"] for s in two["signals"]}
    assert by_buyer(one["signals"], ALPHA)["fit"] == 94 and by_buyer(two["signals"], ALPHA)["fit"] == 80, "a profile about software shares no words with the contracts"
    default = get_signals(c)[1]
    assert default["profile"]["id"] == FIXTURE_IDS["A1"] and by_buyer(default["signals"], ALPHA)["fit"] == 94, "no profile_id means the default profile"
    nobody = get_signals(client(USER_D))[1]
    assert nobody["profile"] is None and by_buyer(nobody["signals"], ALPHA)["fit"] == 80 and \
        "no description" in next(p for p in by_buyer(nobody["signals"], ALPHA)["fit_parts"] if p["key"] == "profile")["note"]


# ───────────────────────────────────────── development signals ────────────────────────────────────

def test_development_signals_follow_the_fixtures():
    reset_limits()
    c = client(USER_A)
    st, r = get_signals(c, types="development")
    assert st == 200 and r["counts"] == {"renewal": 0, "development": 3, "engagement": 0, "total": 3, "dismissed": 0}
    keys = [s["key"] for s in r["signals"]]
    assert keys == ["development:zz_growth_test_1", "development:zz_growth_test_8", "development:zz_growth_test_2"], \
        "screening opinion, undecided, minor works, small and stale schemes are all left out"
    one, personal, unnamed = r["signals"]
    assert (one["fit"], personal["fit"], unnamed["fit"]) == (93, 63, 63)
    assert one["buyer"] == "ZZ Growth Test Homes Ltd" and one["target"] == {"name": "ZZ Growth Test Homes Ltd", "key": "dev:ZZ GROWTH TEST HOMES", "targetable": True, "reason": None}
    assert one["headline"] == "150 dwellings at Land at Test Lane 1 approved 4 weeks ago" and one["authority"] == "Planning authority: ZZ Growth Test Authority"
    assert "Planning agent: ZZ Growth Agent LLP." in one["detail"] and one["links"] == [{"label": "Planning record", "url": "https://example.org/zz-plan/1"}]
    assert one["scheme"]["dwellings"] == 150 and "value" not in one
    for s in (personal, unnamed):
        assert s["buyer"] == "Developer not named" and s["target"]["targetable"] is False and s["target"]["key"] is None and s["target"]["reason"]
    assert "Mr J Smith" not in json.dumps(r), "an individual's name is never passed on"
    assert one["fit"] == sum(p["points"] for p in one["fit_parts"])

    stale = get_signals(c, types="development", days=730)[1]
    assert stale["counts"]["development"] == 4
    assert [s["key"] for s in stale["signals"]] == [f"development:zz_growth_test_{n}" for n in (1, 7, 8, 2)],         "a stale scheme with a developer still ranks above fresh ones with nobody to approach"
    narrow = get_signals(c, types="development", days=40)[1]
    assert [s["key"] for s in narrow["signals"]] == ["development:zz_growth_test_1", "development:zz_growth_test_8"], "approved in the last 40 days"
    assert get_signals(c, types="development", where="no such place")[1]["signals"] == []
    assert get_signals(c, types="development", where="zz growth test authority")[1]["counts"]["development"] == 3
    assert get_signals(c, types="development", where="Test Lane 2")[1]["counts"]["development"] == 1, "the place filter also matches the site address"


def test_development_signals_only_for_building_categories():
    reset_limits()
    c = client(USER_A)
    crm = get_signals(c, preset="crm-case-management", types="development,renewal")[1]
    assert crm["counts"]["development"] == 0 and "construction" in crm["notes"]["development"]
    assert not [s for s in crm["signals"] if s["type"] == "development"]
    for category in ({"preset": "construction-works"}, {"preset": "grounds-tree-works"}, {"preset": None, "cpv": "45"}, {"preset": None, "q": "plumbing"}):
        got = get_signals(c, types="development", **category)[1]
        assert got["counts"]["development"] == 3 and got["notes"]["development"] is None, category
    assert get_signals(c, preset=None, cpv="72", types="development")[1]["counts"]["development"] == 0


# ───────────────────────────────────────── engagement signals ─────────────────────────────────────

def test_engagement_signals_show_only_public_engagements_of_other_users():
    reset_limits()
    a, b = client(USER_A), client(USER_B)
    st, r = get_signals(a, types="engagement")
    assert st == 200 and [s["key"] for s in r["signals"]] == [f"engagement:{FIXTURE_IDS['E1']}"], "draft, unlinked, closed, expired and nameless ones are hidden"
    s = r["signals"][0]
    assert s["buyer"] == ALPHA and s["fit"] == 100 and s["date"] == iso(20) and s["days"] == 20
    assert s["target"] == {"name": ALPHA, "key": ALPHA_KEY, "targetable": True, "reason": None}
    assert s["links"] == [{"label": "Published notice", "url": "https://example.org/zz-notice/1"}]
    assert "Responses by" in s["detail"] and "Supplier day: 2026-11-10 10:00 (Town Hall)." in s["detail"]
    assert "zz.invalid" not in json.dumps(r) and "Pat Buyer" not in json.dumps(r), "the buyer's contact details stay private"
    assert get_signals(b, types="engagement")[1]["signals"] == [], "the publisher does not see their own engagement as a signal"
    crm = get_signals(a, types="engagement", preset="crm-case-management")[1]
    assert [x["key"] for x in crm["signals"]] == [f"engagement:{FIXTURE_IDS['E6']}"] and crm["signals"][0]["fit"] == 100
    related = get_signals(a, types="engagement", preset="construction-works")[1]
    assert [x["key"] for x in related["signals"]] == [f"engagement:{FIXTURE_IDS['E1']}"], "a related category still shows it"
    assert next(p for p in related["signals"][0]["fit_parts"] if p["key"] == "category")["points"] == 25
    assert get_signals(a, types="engagement", authority="nhs")[1]["signals"] == []

    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE market_engagements SET status = 'closed' WHERE id = %s", (FIXTURE_IDS["E1"],))
        conn.commit()
        assert get_signals(a, types="engagement")[1]["signals"] == [], "closing an engagement takes it off the list at once"
        with conn.cursor() as cur:
            cur.execute("UPDATE market_engagements SET status = 'published' WHERE id = %s", (FIXTURE_IDS["E1"],))
        conn.commit()
    finally:
        conn.close()
    assert len(get_signals(a, types="engagement")[1]["signals"]) == 1


def test_each_signal_type_is_capped_on_its_own():
    reset_limits()
    from tender_app import growth_studio as gs
    a = client(USER_A)
    st, r = get_signals(a, types="renewal,development,engagement")
    assert st == 200 and r["counts"]["renewal"] == 3 and r["counts"]["development"] == 3 and r["counts"]["engagement"] == 1
    assert r["shown_by_type"] == {"renewal": 3, "development": 3, "engagement": 1} and r["shown"] == r["total"] == 7
    original = gs.MAX_PER_TYPE
    try:
        gs.MAX_PER_TYPE = 2
        st, r = get_signals(a, types="renewal,development,engagement")
        assert r["shown_by_type"] == {"renewal": 2, "development": 2, "engagement": 1}
        assert r["shown"] == 5 == len(r["signals"]) and r["total"] == 7
        assert r["counts"]["renewal"] == 3, "counts are of everything, not of what is shown"
        assert [s["fit"] for s in r["signals"]] == sorted((s["fit"] for s in r["signals"]), reverse=True)
        assert {s["key"] for s in r["signals"] if s["type"] == "renewal"} == {f"renewal:{ALPHA_KEY}", "renewal:ZZ GROWTH TEST GAMMA NHS TRUST"},             "the best of each type is kept"
        assert {s["type"] for s in r["signals"]} == {"renewal", "development", "engagement"}, "one big type cannot push the others off the page"
    finally:
        gs.MAX_PER_TYPE = original


# ────────────────────────────────────────────── validation ────────────────────────────────────────

def test_signal_request_validation():
    reset_limits()
    c = client(USER_C)
    for params in ({}, {"preset": "nope"}, {"preset": "housing-repairs-gas", "types": "bogus"}, {"preset": "housing-repairs-gas", "days": "x"},
                   {"preset": "housing-repairs-gas", "days": "10"}, {"preset": "housing-repairs-gas", "authority": "x"},
                   {"preset": "housing-repairs-gas", "authority": "local-other"}, {"preset": "housing-repairs-gas", "where": "x"},
                   {"preset": "housing-repairs-gas", "where": "x" * 80}, {"cpv": "1"}, {"cpv": "12345678901"}):
        st, body = call(c, "get", "/api/growth/signals?" + qs(**params))
        assert st == 400 and body.get("error"), (params, st, body)
    for pid, status in (("99999999", 404), (str(FIXTURE_IDS["A1"]), 404), ("abc", 400), ("-3", 400)):
        st, body = get_signals(c, profile_id=pid)
        assert st == status and body.get("error"), (pid, st)
    assert get_signals(c, profile_id="0")[0] == 200 and get_signals(c, profile_id="")[0] == 200, "0 or blank mean no profile"


# ──────────────────────────────────────── signal state ────────────────────────────────────────────

def test_dismissing_a_signal_is_per_user_and_per_profile():
    reset_limits()
    a, b = client(USER_A), client(USER_B)
    key = signal_keys(a, ALPHA)[0]
    p1, p2 = FIXTURE_IDS["A1"], FIXTURE_IDS["A2"]
    assert call(a, "put", "/api/growth/signals/state", {"profile_id": p1, "key": key, "dismissed": True}) == (200, {"key": key, "dismissed": True})
    assert call(a, "put", "/api/growth/signals/state", {"profile_id": p1, "key": key, "dismissed": True})[0] == 200, "dismissing twice is harmless"
    st, r = get_signals(a, profile_id=p1)
    assert [s["buyer"] for s in r["signals"]] == [GAMMA, BETA] and r["counts"]["dismissed"] == 1 and r["counts"]["renewal"] == 2 and r["counts"]["total"] == 2
    shown = get_signals(a, profile_id=p1, show_dismissed="1")[1]
    assert [s["buyer"] for s in shown["signals"]] == [ALPHA, GAMMA, BETA] and by_buyer(shown["signals"], ALPHA)["state"]["dismissed"] is True
    assert by_buyer(get_signals(a, profile_id=p2)[1]["signals"], ALPHA), "another profile still sees it"
    assert by_buyer(get_signals(b)[1]["signals"], ALPHA), "another user still sees it"
    assert call(a, "put", "/api/growth/signals/state", {"profile_id": p1, "key": key, "dismissed": False})[0] == 200
    assert by_buyer(get_signals(a, profile_id=p1)[1]["signals"], ALPHA)["state"]["dismissed"] is False
    for body in ({"key": "nonsense", "dismissed": True}, {"key": key, "dismissed": "yes"}, {"key": key}, {"dismissed": True},
                 {"key": "renewal:" + "x" * 400, "dismissed": True}, {"key": key, "dismissed": True, "profile_id": 99999999},
                 {"key": key, "dismissed": True, "profile_id": FIXTURE_IDS["B1"]}):
        st, resp = call(a, "put", "/api/growth/signals/state", body)
        assert st in (400, 404) and resp.get("error"), body
    assert call(a, "put", "/api/growth/signals/state", {"key": key, "dismissed": True})[0] == 200
    assert by_buyer(get_signals(a, profile_id=p1)[1]["signals"], ALPHA) is None, "no profile_id means the default profile, as it does when listing"
    assert by_buyer(get_signals(a, profile_id=p2)[1]["signals"], ALPHA), "and the other profile still shows it"
    call(a, "put", "/api/growth/signals/state", {"key": key, "dismissed": False})
    assert by_buyer(get_signals(a, profile_id=p1)[1]["signals"], ALPHA)
    d = client(USER_D)
    assert call(d, "put", "/api/growth/signals/state", {"key": key, "dismissed": True})[0] == 200
    assert by_buyer(get_signals(d)[1]["signals"], ALPHA) is None, "a user with no profile can dismiss too"
    assert by_buyer(get_signals(client(USER_C))[1]["signals"], ALPHA), "another user with no profile, so the very same state key, is not affected"
    assert by_buyer(get_signals(a, profile_id=p1)[1]["signals"], ALPHA), "and it does not touch anyone else's list"
    call(d, "put", "/api/growth/signals/state", {"key": key, "dismissed": False})


# ───────────────────────────────────────── campaign lifecycle ─────────────────────────────────────

def test_campaign_from_signals_and_its_targets():
    reset_limits()
    from tender_app import growth_studio as gs
    a, b = client(USER_A), client(USER_B)
    _, r = get_signals(a, **{"types": "renewal,development"})
    alpha = by_buyer(r["signals"], ALPHA)["key"]
    beta = by_buyer(r["signals"], BETA)["key"]
    dev_ok, dev_none = "development:zz_growth_test_1", "development:zz_growth_test_2"
    body = make_campaign(a, [beta, dev_none, alpha, dev_ok, "renewal:NO SUCH BUYER", alpha])
    camp, targets = body["campaign"], body["targets"]
    assert camp["status"] == "draft" and camp["channel"] == "email" and camp["profile_id"] == FIXTURE_IDS["A1"] and camp["profile_name"] == "ZZ Growth Profile One"
    assert camp["name"].startswith("Housing repairs & gas servicing: renewals, ") and camp["category_label"] == "Housing repairs & gas servicing"
    assert camp["filters"]["preset"] == "housing-repairs-gas" and camp["filters"]["where"] == WHERE
    assert [t["buyer_name"] for t in targets] == [ALPHA, "ZZ Growth Test Homes Ltd", BETA], "best fit first; duplicates and unusable signals dropped"
    assert [t["signal_type"] for t in targets] == ["renewal", "development", "renewal"] and [t["fit"] for t in targets] == [94, 93, 75]
    assert all(t["status"] == "not_sent" and t["included"] and t["contact_email"] is None and not t["opted_out"] for t in targets)
    reasons = {s["key"]: s["reason"] for s in body["skipped"]}
    assert set(reasons) == {dev_none, "renewal:NO SUCH BUYER"} and "No developer" in reasons[dev_none] and "no longer in the list" in reasons["renewal:NO SUCH BUYER"]
    assert body["eligible"] == 3 and body["sender"]["company_name"] == "ZZ Growth Heating Ltd" and body["sender"]["sender_name"] == "Sam Ray"
    assert "Hello {{buyer_contact}}" in camp["body"] and "Sam Ray" in camp["body"] and gs.OPT_OUT_LINE in camp["body"] and body["warnings"] == []
    assert camp["subject"] == "{{category}} support ahead of your contract renewal"
    cid = camp["id"]

    st, again = call(a, "get", f"/api/growth/campaigns/{cid}")
    assert st == 200 and again["campaign"] == camp and again["targets"] == targets
    st, listing = call(a, "get", "/api/growth/campaigns")
    mine = next(x for x in listing["campaigns"] if x["id"] == cid)
    assert mine["counts"] == {"targets": 3, "sent": 0, "replied": 0, "meetings": 0} and "body" not in mine and "subject" not in mine
    assert call(a, "get", f"/api/growth/campaigns?profile_id={FIXTURE_IDS['A2']}")[1]["campaigns"] == []
    assert cid in [x["id"] for x in call(a, "get", f"/api/growth/campaigns?profile_id={FIXTURE_IDS['A1']}")[1]["campaigns"]]

    inside = by_buyer(get_signals(a, **{"types": "renewal,development"})[1]["signals"], ALPHA)
    assert inside["state"]["campaigns"] == [{"id": cid, "name": camp["name"]}], "a signal says which campaign already has the buyer"
    assert by_buyer(get_signals(a, profile_id=FIXTURE_IDS["A2"])[1]["signals"], ALPHA)["state"]["campaigns"] == [], "campaigns are per profile"
    assert by_buyer(get_signals(b)[1]["signals"], ALPHA)["state"]["campaigns"] == [], "and never shown to another user"

    for method, url, payload in (("get", f"/api/growth/campaigns/{cid}", None), ("patch", f"/api/growth/campaigns/{cid}", {"name": "stolen"}),
                                 ("delete", f"/api/growth/campaigns/{cid}", None), ("get", f"/api/growth/campaigns/{cid}/export", None),
                                 ("post", f"/api/growth/campaigns/{cid}/draft", {"mode": "template"}), ("post", f"/api/growth/campaigns/{cid}/preview", {}),
                                 ("post", f"/api/growth/campaigns/{cid}/mark-sent", {}),
                                 ("patch", f"/api/growth/campaigns/{cid}/targets/{targets[0]['id']}", {"included": False}),
                                 ("delete", f"/api/growth/campaigns/{cid}/targets/{targets[0]['id']}", None),
                                 ("post", f"/api/growth/campaigns/{cid}/targets", {"keys": [alpha], "filters": FILTERS})):
        st, resp = call(b, method, url, payload)
        assert st == 404 and resp.get("error") == "Campaign not found", (method, url, st, resp)
    assert call(b, "get", "/api/growth/campaigns")[1]["campaigns"] == [], "another user's list is empty"
    assert call(a, "get", f"/api/growth/campaigns/{cid}")[1]["campaign"]["name"] == camp["name"], "and nothing of A's was touched"


def test_campaign_creation_validation():
    reset_limits()
    from tender_app.blueprints import growth_bp
    a = client(USER_A)
    key = signal_keys(a, ALPHA)[0]
    bad = ({}, {"keys": []}, {"keys": "x", "filters": FILTERS}, {"keys": [key], "filters": "x"}, {"keys": [key]}, {"keys": [1], "filters": FILTERS},
           {"keys": [""], "filters": FILTERS}, {"keys": [key] * 3 + [f"renewal:{i}" for i in range(250)], "filters": FILTERS},
           {"keys": [key], "filters": {**FILTERS, "days": 1}}, {"keys": [key], "filters": FILTERS, "name": "x" * 121},
           {"keys": [key], "filters": FILTERS, "name": 5}, {"keys": [key], "filters": FILTERS, "channel": "fax"},
           {"keys": [key], "filters": FILTERS, "profile_id": "abc"})
    for body in bad:
        st, resp = call(a, "post", "/api/growth/campaigns", body)
        assert st == 400 and resp.get("error"), (body if len(str(body)) < 200 else "…", st, resp)
    assert call(a, "post", "/api/growth/campaigns", {"keys": [key], "filters": FILTERS, "profile_id": 99999999})[0] == 404
    assert call(a, "post", "/api/growth/campaigns", {"keys": [key], "filters": FILTERS, "profile_id": FIXTURE_IDS["B1"]})[0] == 404, "another user's profile"
    st, resp = call(a, "post", "/api/growth/campaigns", {"keys": ["development:zz_growth_test_2"], "filters": FILTERS})
    assert st == 400 and "No developer" in resp["error"], "nothing usable is a clear error, not an empty campaign"
    named = make_campaign(a, [key], name="  My   own  name ", channel="phone")
    assert named["campaign"]["name"] == "My own name" and named["campaign"]["channel"] == "phone" and named["campaign"]["channel_label"] == "Phone"
    only_engagement = signal_keys(a, ALPHA, types="engagement")
    made = make_campaign(a, only_engagement, filters={**FILTERS, "types": ["engagement"]})
    assert made["campaign"]["channel"] == "portal" and made["campaign"]["subject"].startswith("Our response to your market engagement")

    original = growth_bp.MAX_CAMPAIGNS
    try:
        growth_bp.MAX_CAMPAIGNS = 1
        st, resp = call(a, "post", "/api/growth/campaigns", {"keys": [key], "filters": FILTERS})
        assert st == 409 and "limit" in resp["error"]
    finally:
        growth_bp.MAX_CAMPAIGNS = original


def test_changing_a_campaign():
    reset_limits()
    a = client(USER_A)
    cid = make_campaign(a, signal_keys(a, ALPHA))["campaign"]["id"]
    url = f"/api/growth/campaigns/{cid}"
    st, body = call(a, "patch", url, {"name": " Renamed ", "channel": "letter", "status": "paused", "subject": "S {{buyer_name}}", "body": "B with nothing at the end"})
    assert st == 200 and body["campaign"]["name"] == "Renamed" and body["campaign"]["channel"] == "letter" and body["campaign"]["status"] == "paused"
    assert body["campaign"]["subject"] == "S {{buyer_name}}" and body["campaign"]["body"] == "B with nothing at the end"
    assert [w["code"] for w in body["warnings"]] == ["no_opt_out"] and body["campaign"]["last_activity_at"]
    for bad in ({}, {"name": ""}, {"name": "x" * 121}, {"channel": "fax"}, {"status": "done"}, {"subject": "s" * 201}, {"body": "b" * 6001}, {"body": 5}, {"colour": "red"}):
        st, resp = call(a, "patch", url, bad)
        assert st == 400 and resp.get("error"), bad
    assert call(a, "get", url)[1]["campaign"]["name"] == "Renamed", "refused changes change nothing"
    assert call(a, "patch", "/api/growth/campaigns/99999999", {"name": "x"})[0] == 404 and call(a, "get", "/api/growth/campaigns/99999999")[0] == 404
    assert call(a, "delete", url)[0] == 200 and call(a, "get", url)[0] == 404 and call(a, "delete", url)[0] == 404
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM growth_campaign_targets WHERE campaign_id = %s", (cid,))
            assert cur.fetchone()[0] == 0, "deleting a campaign deletes its targets"
    finally:
        conn.close()


def test_targets_status_history_and_opting_out():
    reset_limits()
    a = client(USER_A)
    alpha, beta = signal_keys(a, ALPHA, BETA)
    body = make_campaign(a, [alpha, beta])
    cid = body["campaign"]["id"]
    t_alpha, t_beta = body["targets"]
    base = f"/api/growth/campaigns/{cid}/targets"

    st, r = call(a, "patch", f"{base}/{t_alpha['id']}", {"contact_email": " Procurement@ZZ-Alpha.INVALID ", "note": " rang them "})
    row = next(t for t in r["targets"] if t["id"] == t_alpha["id"])
    assert st == 200 and row["contact_email"] == "procurement@zz-alpha.invalid" and row["note"] == "rang them" and row["personal_email"] is False
    row = next(t for t in call(a, "patch", f"{base}/{t_alpha['id']}", {"contact_email": "john.smith@zz-alpha.invalid"})[1]["targets"] if t["id"] == t_alpha["id"])
    assert row["personal_email"] is True, "a firstname.lastname address is flagged (advice only, never refused)"
    cleared = next(t for t in call(a, "patch", f"{base}/{t_alpha['id']}", {"contact_email": ""})[1]["targets"] if t["id"] == t_alpha["id"])
    assert cleared["contact_email"] is None
    for bad in ({}, {"contact_email": "nope"}, {"included": "yes"}, {"status": "won"}, {"note": "n" * 501}, {"unknown": 1}):
        assert call(a, "patch", f"{base}/{t_alpha['id']}", bad)[0] == 400, bad
    assert call(a, "patch", f"{base}/99999999", {"included": False})[0] == 404

    assert call(a, "get", f"/api/growth/campaigns/{cid}")[1]["campaign"]["status"] == "draft"
    sent = next(t for t in call(a, "patch", f"{base}/{t_alpha['id']}", {"status": "sent"})[1]["targets"] if t["id"] == t_alpha["id"])
    assert sent["status"] == "sent" and sent["sent_at"] and sent["replied_at"] is None and sent["meeting_at"] is None
    assert call(a, "get", f"/api/growth/campaigns/{cid}")[1]["campaign"]["status"] == "active", "the first message sent makes a draft campaign active"
    meeting = next(t for t in call(a, "patch", f"{base}/{t_alpha['id']}", {"status": "meeting"})[1]["targets"] if t["id"] == t_alpha["id"])
    assert meeting["sent_at"] == sent["sent_at"] and meeting["replied_at"] and meeting["meeting_at"], "a meeting implies a reply, a reply implies it was sent"
    back = next(t for t in call(a, "patch", f"{base}/{t_alpha['id']}", {"status": "replied"})[1]["targets"] if t["id"] == t_alpha["id"])
    assert back["replied_at"] == meeting["replied_at"] and back["meeting_at"] is None
    fresh = next(t for t in call(a, "patch", f"{base}/{t_alpha['id']}", {"status": "not_sent"})[1]["targets"] if t["id"] == t_alpha["id"])
    assert fresh["sent_at"] is None and fresh["replied_at"] is None and fresh["meeting_at"] is None
    listing = next(x for x in call(a, "get", "/api/growth/campaigns")[1]["campaigns"] if x["id"] == cid)
    assert listing["counts"] == {"targets": 2, "sent": 0, "replied": 0, "meetings": 0}

    off = call(a, "patch", f"{base}/{t_beta['id']}", {"included": False})[1]
    assert off["eligible"] == 1 and next(t for t in off["targets"] if t["id"] == t_beta["id"])["included"] is False
    call(a, "patch", f"{base}/{t_beta['id']}", {"included": True})

    st, r = call(a, "patch", f"{base}/{t_beta['id']}", {"status": "opted_out"})
    row = next(t for t in r["targets"] if t["id"] == t_beta["id"])
    assert row["status"] == "opted_out" and row["opted_out"] is True and r["eligible"] == 1
    sup = call(a, "get", "/api/growth/suppressions")[1]["suppressions"]
    assert [(s["key"], s["name"], s["reason"]) for s in sup] == [(BETA_KEY, BETA, "opted_out")]
    assert call(client(USER_B), "get", "/api/growth/suppressions")[1]["suppressions"] == [], "the do-not-contact list is per user"
    assert call(client(USER_B), "delete", f"/api/growth/suppressions?key={urllib.parse.quote(BETA_KEY)}")[0] == 404, "another user cannot take a buyer off your list"
    assert len(call(a, "get", "/api/growth/suppressions")[1]["suppressions"]) == 1, "and the buyer is still on it"
    beta_signal = by_buyer(get_signals(a)[1]["signals"], BETA)
    assert beta_signal["state"]["opted_out"] is True and beta_signal["target"]["targetable"] is False and "do-not-contact" in beta_signal["target"]["reason"]
    st, resp = call(a, "post", "/api/growth/campaigns", {"keys": [beta], "filters": RENEWALS})
    assert st == 400 and "do-not-contact" in resp["error"], "an opted-out buyer cannot be put into a new campaign"
    st, added = call(a, "post", f"/api/growth/campaigns/{cid}/targets", {"keys": [beta], "filters": RENEWALS})
    assert st == 200 and added["added"] == 0 and "do-not-contact" in added["skipped"][0]["reason"]
    assert by_buyer(get_signals(client(USER_B))[1]["signals"], BETA)["target"]["targetable"], "another user is not affected"

    assert call(a, "delete", f"/api/growth/suppressions?key={urllib.parse.quote(BETA_KEY)}")[0] == 200
    assert call(a, "delete", f"/api/growth/suppressions?key={urllib.parse.quote(BETA_KEY)}")[0] == 404 and call(a, "delete", "/api/growth/suppressions")[0] == 400
    assert by_buyer(get_signals(a)[1]["signals"], BETA)["target"]["targetable"] is True
    assert call(a, "get", f"/api/growth/campaigns/{cid}")[1]["targets"][1]["status"] == "opted_out", "history is kept when the buyer is removed from the list"

    st, r = call(a, "delete", f"{base}/{t_beta['id']}")
    assert st == 200 and [t["buyer_name"] for t in r["targets"]] == [ALPHA] and call(a, "delete", f"{base}/{t_beta['id']}")[0] == 404


def test_adding_targets_to_an_existing_campaign():
    reset_limits()
    from tender_app.blueprints import growth_bp
    a = client(USER_A)
    alpha, beta, gamma = signal_keys(a, ALPHA, BETA, GAMMA)
    cid = make_campaign(a, [alpha])["campaign"]["id"]
    url = f"/api/growth/campaigns/{cid}/targets"
    st, r = call(a, "post", url, {"keys": [alpha, beta, gamma, "development:zz_growth_test_2"], "filters": FILTERS})
    assert st == 200 and r["added"] == 2 and [t["buyer_name"] for t in r["targets"]] == [ALPHA, GAMMA, BETA]
    reasons = {s["key"]: s["reason"] for s in r["skipped"]}
    assert set(reasons) == {alpha, "development:zz_growth_test_2"}
    assert reasons[alpha] == "This buyer is already in the campaign." and reasons["development:zz_growth_test_2"].startswith("No developer company")
    assert call(a, "post", url, {"keys": [beta], "filters": FILTERS})[1]["added"] == 0
    for bad in ({}, {"keys": []}, {"keys": [beta]}, {"keys": [beta], "filters": 3}):
        assert call(a, "post", url, bad)[0] == 400, bad
    original = growth_bp.MAX_TARGETS
    try:
        growth_bp.MAX_TARGETS = 3
        st, resp = call(a, "post", url, {"keys": ["development:zz_growth_test_1"], "filters": FILTERS})
        assert st == 409 and "at most 3" in resp["error"]
    finally:
        growth_bp.MAX_TARGETS = original


def test_mark_sent_records_the_campaign_going_out():
    reset_limits()
    a = client(USER_A)
    alpha, beta, gamma = signal_keys(a, ALPHA, BETA, GAMMA)
    body = make_campaign(a, [alpha, beta, gamma])
    cid = body["campaign"]["id"]
    t_alpha, t_beta, t_gamma = (next(t for t in body["targets"] if t["buyer_name"] == n) for n in (ALPHA, BETA, GAMMA))
    base = f"/api/growth/campaigns/{cid}/targets"
    call(a, "patch", f"{base}/{t_beta['id']}", {"included": False})
    call(a, "patch", f"{base}/{t_gamma['id']}", {"status": "opted_out"})
    st, r = call(a, "post", f"/api/growth/campaigns/{cid}/mark-sent", {})
    assert st == 200 and r["marked"] == 1 and r["campaign"]["status"] == "active"
    status = {t["buyer_name"]: t["status"] for t in r["targets"]}
    assert status == {ALPHA: "sent", BETA: "not_sent", GAMMA: "opted_out"}, "switched-off and opted-out buyers are never marked as sent"
    assert call(a, "post", f"/api/growth/campaigns/{cid}/mark-sent", {})[1]["marked"] == 0, "marking twice changes nothing"
    call(a, "patch", f"{base}/{t_beta['id']}", {"included": True})
    assert call(a, "post", f"/api/growth/campaigns/{cid}/mark-sent", {"target_ids": [t_beta["id"], t_gamma["id"], 99999999]})[1]["marked"] == 1
    for bad in ({"target_ids": "1"}, {"target_ids": ["1"]}, {"target_ids": [True]}):
        assert call(a, "post", f"/api/growth/campaigns/{cid}/mark-sent", bad)[0] == 400, bad
    call(a, "delete", f"/api/growth/suppressions?key={urllib.parse.quote('ZZ GROWTH TEST GAMMA NHS TRUST')}")


# ──────────────────────────────────────────── the message ─────────────────────────────────────────

def test_campaign_chips_are_not_shared_between_users_without_a_profile():
    reset_limits()
    d, c = client(USER_D), client(USER_C)  # neither has a company profile, so both read the same "no profile" state
    camp = make_campaign(d, signal_keys(d, ALPHA))["campaign"]
    mine = by_buyer(get_signals(d)[1]["signals"], ALPHA)
    assert camp["id"] in [x["id"] for x in mine["state"]["campaigns"]]
    assert by_buyer(get_signals(c)[1]["signals"], ALPHA)["state"]["campaigns"] == [], "another user's campaign is never shown on your signal"


def read_csv(text):
    assert text.startswith(chr(0xFEFF)), "a byte order mark so Excel reads £ correctly"
    return list(csv.reader(io.StringIO(text[1:])))


def test_preview_and_exports():
    reset_limits()
    from tender_app import growth_studio as gs
    a = client(USER_A)
    alpha, beta, gamma = signal_keys(a, ALPHA, BETA, GAMMA)
    body = make_campaign(a, [alpha, beta, gamma])
    cid = body["campaign"]["id"]
    t = {x["buyer_name"]: x for x in body["targets"]}
    base = f"/api/growth/campaigns/{cid}"
    st, r = call(a, "patch", base, {"subject": "{{category}} for {{buyer_name}}", "body": "Hi {{buyer_contact}}, {{buyer_name}}'s “{{contract_title}}” ends {{renewal_date}}. {{typo}} {{incumbent}} {{scheme_summary}}\n\n" + gs.OPT_OUT_LINE})
    assert st == 200 and [w["code"] for w in r["warnings"]] == ["unknown_fields"]
    call(a, "patch", f"{base}/targets/{t[ALPHA]['id']}", {"contact_email": "procurement@zz-alpha.invalid"})
    call(a, "patch", f"{base}/targets/{t[BETA]['id']}", {"included": False})
    call(a, "patch", f"{base}/targets/{t[GAMMA]['id']}", {"status": "opted_out"})

    st, p = call(a, "post", f"{base}/preview", {"target_id": t[ALPHA]["id"]})
    assert st == 200 and p["buyer"] == ALPHA and p["to"] == "procurement@zz-alpha.invalid"
    assert p["subject"] == f"Housing repairs & gas servicing for {ALPHA}"
    assert p["body"].startswith(f"Hi procurement team, {ALPHA}'s “ZZ gas servicing and boiler maintenance” ends {gs.long_date(TODAY + timedelta(days=91))}. {{{{typo}}}} ZZ Growth Test Heating Ltd ")
    assert p["unknown"] == ["typo"] and p["missing"] == ["scheme_summary"]
    assert [w["code"] for w in p["warnings"]] == ["unknown_fields"], "the saved text is checked the same way as an export would be"
    unsaved = call(a, "post", f"{base}/preview", {"target_id": t[ALPHA]["id"], "subject": "New {{buyer_name}}", "body": "Only {{renewal_date}}"})[1]
    assert unsaved["subject"] == f"New {ALPHA}" and unsaved["body"] == f"Only {gs.long_date(TODAY + timedelta(days=91))}" and unsaved["unknown"] == [] and unsaved["missing"] == []
    assert [w["code"] for w in unsaved["warnings"]] == ["no_opt_out"], "unsaved text is checked too, so the page can warn while you type"
    assert call(a, "get", base)[1]["campaign"]["subject"] == "{{category}} for {{buyer_name}}", "a preview never saves"
    assert call(a, "post", f"{base}/preview", {})[1]["buyer"] == ALPHA, "no target means the first included one"
    assert call(a, "post", f"{base}/preview", {"target_id": 99999999})[0] == 404
    assert call(a, "post", f"{base}/preview", {"subject": "s" * 300})[0] == 400

    r = a.get(f"{base}/export?format=csv")
    assert r.status_code == 200 and r.mimetype == "text/csv" and "utf-8" in r.headers["Content-Type"].lower()
    assert "no-store" in r.headers["Cache-Control"], "an export of buyer data must never be cached"
    assert re.fullmatch(r'attachment; filename="[a-z0-9-]+-csv-\d{4}-\d{2}-\d{2}\.csv"', r.headers["Content-Disposition"]), r.headers["Content-Disposition"]
    rows = read_csv(r.get_data(as_text=True))
    assert rows[0][:2] == ["Buyer", "Signal"] and [x[0] for x in rows[1:]] == [ALPHA], "switched-off and opted-out buyers are not exported"
    assert rows[1][4] == "procurement@zz-alpha.invalid" and rows[1][5] == f"Housing repairs & gas servicing for {ALPHA}" and "{{typo}}" in rows[1][6]
    mc = a.get(f"{base}/export?format=mailchimp")
    assert mc.status_code == 200 and mc.mimetype == "text/csv" and "-mailchimp-" in mc.headers["Content-Disposition"]
    mrows = read_csv(mc.get_data(as_text=True))
    assert mrows[0][0] == "Email Address" and mrows[1][0] == "procurement@zz-alpha.invalid" and mrows[1][1] == ALPHA and mrows[1][3] == "ZZ gas servicing and boiler maintenance"
    msg = a.get(f"{base}/export?format=mailchimp-message")
    assert msg.status_code == 200 and msg.mimetype == "text/plain" and msg.headers["Content-Disposition"].endswith('.txt"')
    assert "*|COMPANY|*" in msg.get_data(as_text=True) and "{{buyer_name}}" not in msg.get_data(as_text=True)
    assert a.get(f"{base}/export?format=xlsx").status_code == 400 and a.get(f"{base}/export?format=").status_code == 200, "a blank format is the CSV"

    call(a, "patch", f"{base}/targets/{t[ALPHA]['id']}", {"included": False})
    assert a.get(f"{base}/export?format=csv").status_code == 400, "nobody left to export is an error, not an empty file"
    assert a.get(f"{base}/export?format=mailchimp-message").status_code == 200, "the message does not depend on who is included"
    call(a, "patch", f"{base}/targets/{t[ALPHA]['id']}", {"included": True})
    call(a, "patch", base, {"body": ""})
    st, resp = call(a, "get", f"{base}/export?format=csv")
    assert st == 400 and "message" in resp["error"], "no export before the message is written"
    call(a, "delete", f"/api/growth/suppressions?key={urllib.parse.quote('ZZ GROWTH TEST GAMMA NHS TRUST')}")


def test_template_draft_is_returned_not_saved_and_costs_nothing():
    reset_limits()
    from tender_app import growth_studio as gs
    from tender_app.credits import get_balance
    a = client(USER_A)
    body = make_campaign(a, signal_keys(a, ALPHA))
    cid = body["campaign"]["id"]
    call(a, "patch", f"/api/growth/campaigns/{cid}", {"subject": "mine", "body": "mine"})
    st, d = call(a, "post", f"/api/growth/campaigns/{cid}/draft", {})
    assert st == 200 and d["mode"] == "template" and d["warnings"] == [] and d["subject"] == "{{category}} support ahead of your contract renewal"
    assert "Hello {{buyer_contact}}" in d["body"] and gs.OPT_OUT_LINE in d["body"] and "Sam Ray" in d["body"]
    assert call(a, "get", f"/api/growth/campaigns/{cid}")[1]["campaign"]["body"] == "mine", "a draft is returned, never saved over the user's text"
    assert call(a, "post", f"/api/growth/campaigns/{cid}/draft", {"mode": "template"})[1]["body"] == d["body"]
    for bad in ({"mode": "robot"}, {"mode": "ai", "instructions": "x" * 501}, {"mode": "ai", "instructions": 5}):
        assert call(a, "post", f"/api/growth/campaigns/{cid}/draft", bad)[0] == 400, bad
    conn = db()
    try:
        assert get_balance(conn, USER_A) == 0, "template drafts never touch the wallet"
    finally:
        conn.close()


def test_ai_draft_charges_once_and_refunds_every_failure():
    reset_limits()
    import server
    from tender_app import growth_studio as gs, metering
    from tender_app.config import CREDIT_COST_GROWTH_DRAFT as COST
    from tender_app.credits import get_balance, grant_credits
    assert COST > 0, "the credit tests need a price on AI drafts (CREDIT_COST_GROWTH_DRAFT)"
    metering.send_low_credit = lambda *args, **kwargs: None  # never email anyone from a test

    d = client(USER_D)
    cid = make_campaign(d, signal_keys(d, ALPHA))["campaign"]["id"]
    url = f"/api/growth/campaigns/{cid}/draft"
    seen = []

    def good(system, user, **kwargs):
        seen.append((system, user))
        return {"subject": "Boiler cover for {{buyer_name}}", "body": "Hello {{buyer_contact}}, we can help with {{category}}."}

    def wallet():
        conn = db()
        try:
            return get_balance(conn, USER_D)
        finally:
            conn.close()

    original = server.call_chat_json
    try:
        server.call_chat_json = good
        st, r = call(d, "post", url, {"mode": "ai"})
        assert st == 402 and r["code"] == "INSUFFICIENT_CREDITS" and r["required"] == COST and r["balance"] == 0 and seen == [], "no credits: refused before the AI is called"

        conn = db()
        try:
            grant_credits(conn, USER_D, COST * 3, "test")
        finally:
            conn.close()
        st, r = call(d, "post", url, {"mode": "ai", "instructions": "Mention our own vans"})
        assert st == 200 and r["mode"] == "ai" and r["cost"] == COST and r["subject"] == "Boiler cover for {{buyer_name}}"
        assert r["body"].endswith(gs.OPT_OUT_LINE) and r["warnings"] == [], "the opt-out line is added when the model leaves it out"
        assert wallet() == COST * 2 and len(seen) == 1
        system, user = seen[0]
        assert gs.OPT_OUT_LINE in system and "{{contract_title}}" in system and "Mention our own vans" in user and "Housing repairs & gas servicing" in user

        def down(system, user, **kwargs):
            raise server.AIServiceError("The AI provider is unavailable", status_code=503)

        def junk(system, user, **kwargs):
            return ["not", "a", "message"]

        def no_key(system, user, **kwargs):
            raise ValueError("API key is not configured for provider: DEEPSEEK")

        for fake, status in ((down, 503), (junk, 502), (no_key, 502)):
            server.call_chat_json = fake
            before = wallet()
            st, r = call(d, "post", url, {"mode": "ai"})
            assert st == status and "not been charged" in r["error"], (fake.__name__, st, r)
            assert wallet() == before, f"{fake.__name__}: the credits are refunded"
        conn = db()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT delta, reason FROM credit_ledger WHERE username = %s ORDER BY id", (USER_D,))
                ledger = cur.fetchall()
        finally:
            conn.close()
        assert ledger[0] == (COST * 3, "test") and ledger[1] == (-COST, "growth_draft")
        assert [x for x in ledger[2:] if x[0] < 0] == [(-COST, "growth_draft")] * 3 and [x for x in ledger[2:] if x[0] > 0] == [(COST, "refund_growth_draft")] * 3
    finally:
        server.call_chat_json = original


def test_ai_draft_is_grounded_in_the_users_own_profile_and_answers():
    reset_limits()
    import server
    from tender_app import metering
    from tender_app.credits import grant_credits
    metering.send_low_credit = lambda *args, **kwargs: None
    a = client(USER_A)
    cid = make_campaign(a, signal_keys(a, ALPHA))["campaign"]["id"]
    conn = db()
    try:
        grant_credits(conn, USER_A, 10, "test")
    finally:
        conn.close()
    seen = []
    original = server.call_chat_json

    def spy(system, user, **kwargs):
        seen.append(user)
        return {"subject": "S", "body": "B"}

    try:
        server.call_chat_json = spy
        assert call(a, "post", f"/api/growth/campaigns/{cid}/draft", {"mode": "ai"})[0] == 200
    finally:
        server.call_chat_json = original
    user = seen[0]
    assert PROFILE_TEXT in user and "Company: ZZ Growth Heating Ltd" in user and "Signed by: Sam Ray" in user
    assert "ZZ Gas Safe registered and NICEIC approved" in user, "accreditations from the user's Answer Bank are used"
    assert "ZZ secret day rates" not in user, "only answers about who the company is, not pricing"
    assert "ZZ B-only accreditation" not in user, "another user's Answer Bank is never read"


# ───────────────────────────────────────────── performance ────────────────────────────────────────

def test_performance_joins_campaigns_to_pipeline():
    reset_limits()
    f, b = client(USER_F), client(USER_B)
    assert call(f, "get", "/api/growth/performance")[1]["tiles"]["sent"] == 0
    alpha, beta = signal_keys(f, ALPHA, BETA)
    body = make_campaign(f, [alpha, beta])
    cid = body["campaign"]["id"]
    t = {x["buyer_name"]: x for x in body["targets"]}
    base = f"/api/growth/campaigns/{cid}"
    call(f, "post", f"{base}/mark-sent", {})
    call(f, "patch", f"{base}/targets/{t[ALPHA]['id']}", {"status": "meeting"})
    now = utc_now()
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE growth_campaign_targets SET sent_at = %s, replied_at = %s, meeting_at = %s WHERE id = %s",
                        (now - timedelta(days=10), now - timedelta(days=8), now - timedelta(days=5), t[ALPHA]["id"]))
            cur.execute("UPDATE growth_campaign_targets SET sent_at = %s WHERE id = %s", (now - timedelta(days=10), t[BETA]["id"]))

            def tender(user, key, authority, value, days_ago, stage="watching"):
                cur.execute(
                    """INSERT INTO pipeline (username, tender_key, title, source, contracting_authority, estimated_value, stage, created_at)
                       VALUES (%s, %s, %s, 'Contracts Finder', %s, %s, %s, %s)""",
                    (user, f"zz-growth-{RUN}-{key}", f"ZZ tender {key}", authority, value, stage, now - timedelta(days=days_ago)),
                )

            tender(USER_F, "1", "ZZ Growth Test Alpha Borough Council", "£250,000", 5, "bidding")
            tender(USER_F, "2", "ZZ Growth Test Alpha Council", "1.5m", 4)                       # another spelling of the same buyer
            tender(USER_F, "3", "ZZ Growth Test Alpha Borough Council", "£100,000", 20)          # tracked before the message was sent
            tender(USER_F, "4", "ZZ Growth Test Beta District Council", "TBC", 2)                # credited, but no value
            tender(USER_F, "5", "Somebody Else Council", "£900,000", 2)
            tender(USER_B, "6", "ZZ Growth Test Alpha Borough Council", "£700,000", 2)          # another user's Pipeline
        conn.commit()
    finally:
        conn.close()

    st, p = call(f, "get", "/api/growth/performance")
    assert st == 200 and p["window"] == "365" and p["days"] == 365
    assert p["tiles"] == {"campaigns_run": 1, "buyers_reached": 2, "sent": 2, "replied": 1, "meetings": 1, "reply_rate": 0.5,
                          "tenders_tracked": 3, "pipeline_value": 1750000, "tenders_with_value": 2}
    assert p["funnel"] == [{"label": "Sent", "value": 2}, {"label": "Replied", "value": 1}, {"label": "Meeting", "value": 1}]
    [row] = p["campaigns"]
    assert (row["id"], row["targets"], row["sent"], row["replied"], row["meetings"], row["tenders"]) == (cid, 2, 2, 1, 1, 3)
    assert "Opens are not tracked" in p["notes"]["opened"] and "once" in p["notes"]["pipeline"]
    assert call(f, "get", "/api/growth/performance?days=30")[1]["tiles"]["sent"] == 2
    assert call(f, "get", "/api/growth/performance?days=all")[1]["tiles"]["tenders_tracked"] == 3
    assert call(f, "get", f"/api/growth/performance?profile_id={FIXTURE_IDS['A1']}")[1]["tiles"]["sent"] == 0, "filtered to a profile the campaign was not run under"
    for bad in ("days=7", "days=abc", "profile_id=abc"):
        assert call(f, "get", "/api/growth/performance?" + bad)[0] == 400, bad

    mine = call(b, "get", "/api/growth/performance")[1]
    assert mine["tiles"]["sent"] == 0 and mine["tiles"]["tenders_tracked"] == 0 and mine["campaigns"] == [], "B's Pipeline credits nobody and A's campaign is invisible to B"
    call(f, "patch", f"{base}/targets/{t[ALPHA]['id']}", {"status": "not_sent"})
    after = call(f, "get", "/api/growth/performance")[1]["tiles"]
    assert after["sent"] == 1 and after["tenders_tracked"] == 1, "taking back a sent message takes its tenders back out"


# ─────────────────────────────────── real data: SQL agrees with Python ────────────────────────────

def test_every_preset_and_authority_type_runs_against_the_real_awards():
    reset_limits()
    from tender_app import market_radar as mr
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM contract_awards")
            if cur.fetchone()[0] < 20_000:
                skip("the awards table is too small to check against")
    finally:
        conn.close()
    c = client(USER_E)
    seen_any = False
    for spec in mr.CATEGORY_PRESETS:
        cat = mr.resolve_category(preset=spec["id"])
        st, r = call(c, "get", "/api/growth/signals?" + qs(preset=spec["id"], types="renewal", days=365))
        assert st == 200, (spec["id"], r)
        for s in r["signals"]:
            seen_any = True
            for x in s["contracts"]:
                assert mr.category_matches(cat, x["title"], x["cpv"], x["cpv_description"]), f"{spec['id']}: SQL returned a contract the Python mirror rejects: {x['title']}"
                assert 0 <= x["days_left"] <= 365 and iso(x["days_left"]) == x["ends"]
                assert not (x["framework"] and x["value"] is not None), "a framework value reached the page"
            assert s["fit"] == sum(p["points"] for p in s["fit_parts"]) <= 100 and s["target"]["targetable"]
        assert [s["fit"] for s in r["signals"]] == sorted((s["fit"] for s in r["signals"]), reverse=True)
        assert len({s["key"] for s in r["signals"]}) == len(r["signals"]), "one signal per buyer"
    assert seen_any, "no preset produced a renewal signal at all: is the awards data current?"
    from tender_app import growth_studio as gs
    original = gs.MAX_PER_TYPE
    try:
        # With the per-type cap a narrower filter can surface buyers that ranked below the cap before, so lift
        # it to compare whole sets.
        gs.MAX_PER_TYPE = 100_000
        everyone = {s["key"] for s in call(c, "get", "/api/growth/signals?" + qs(preset="housing-repairs-gas", types="renewal", days=365))[1]["signals"]}
        assert everyone, "the housing preset should have renewals in the next year"
        for type_id, _ in mr.AUTHORITY_TYPES:
            if type_id == "local-other":
                continue
            st, r = call(c, "get", "/api/growth/signals?" + qs(preset="housing-repairs-gas", types="renewal", days=365, authority=type_id))
            assert st == 200, (type_id, r)
            assert {s["key"] for s in r["signals"]} <= everyone, f"{type_id}: an authority filter can only remove buyers, never add them"
            if type_id == "all":
                assert {s["key"] for s in r["signals"]} == everyone
    finally:
        gs.MAX_PER_TYPE = original


# ───────────────────────────────────────────── runner ─────────────────────────────────────────────

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
    cleanup()  # whatever a crashed earlier run left behind
    try:
        make_fixtures()
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
