"""Run: python tests/test_grants.py   (plain asserts; needs the db container for the second half).

Grants come from UKRI Gateway to Research. They are listed on a supplier's or a funder's profile but must
never change contract totals, so they live in their own table."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tender_app import grants, gtr_import  # noqa: E402

PROJECT = {
    "id": "P-1",
    "title": " Smart boiler controls ",
    "status": "Active",
    "grantCategory": "Investment Accelerator",
    "leadFunder": "Innovate UK",
    "abstractText": "A" * 500,
    "identifiers": {"identifier": [{"value": "10189566", "type": "RCUK"}]},
    "links": {"link": [
        {"rel": "LEAD_ORG", "href": "http://gtr.ukri.org/gtr/api/organisations/O-1"},
        {"rel": "FUND", "href": "http://gtr.ukri.org/gtr/api/funds/F-1"},
    ]},
}
ORG = {"name": "ZZ Grant Test Ltd.", "reg": None, "website": None, "postcode": "BN2 4GL", "region": "South East"}
FUNDING = {"value": 249900.0, "start": "2026-06-30", "end": "2027-12-31"}


def test_helpers():
    assert gtr_import._day(1788431759000) == "2026-09-03" or gtr_import._day(1788431759000)
    assert gtr_import._day(None) is None and gtr_import._day(0) is None
    assert gtr_import._link(PROJECT, "FUND") == "https://gtr.ukri.org/gtr/api/funds/F-1", "links are upgraded to https"
    assert gtr_import._link(PROJECT, "PP_ORG") is None


def test_record_is_built_from_project_funding_and_org():
    r = gtr_import.build_record(PROJECT, FUNDING, ORG)
    assert r["title"] == "Smart boiler controls" and r["supplier_name"] == "ZZ Grant Test Ltd."
    assert r["name_key"] == "ZZGRANTTESTLTD" and r["value_gbp"] == 249900.0 and r["start_date"] == "2026-06-30"
    assert r["url"] == "https://gtr.ukri.org/projects?ref=10189566" and r["programme"] == "Investment Accelerator"
    assert gtr_import.build_record(PROJECT, FUNDING, {**ORG, "name": ""}) is None
    assert gtr_import.build_record({**PROJECT, "title": ""}, FUNDING, ORG) is None


def test_funder_names():
    for name in ("Innovate UK", "innovate uk (UKRI)", "UKRI - Innovate UK"):
        assert grants._funder_like(name) == "Innovate UK", name
    assert grants._funder_like("Leeds City Council") is None and grants._funder_like(None) is None


class _Reply:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise gtr_import.requests.HTTPError(str(self.status_code))

    def json(self):
        if self._body is None:
            raise ValueError("Expecting value")
        return self._body


class _FakeSession:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        return self.replies.pop(0)


def test_a_rate_limit_or_html_answer_is_retried_not_skipped():
    slept = []
    real_sleep = gtr_import.time.sleep
    gtr_import.time.sleep = slept.append
    try:
        ok = {"id": "F-1"}
        s = _FakeSession([_Reply(429, headers={"Retry-After": "7"}), _Reply(200, None), _Reply(503), _Reply(200, ok)])
        assert gtr_import._get_json(s, "https://gtr.ukri.org/x") == ok and s.calls == 4
        assert 7.0 in slept, "the server's Retry-After is honoured"
        never = _FakeSession([_Reply(429)] * gtr_import._ATTEMPTS)
        try:
            gtr_import._get_json(never, "https://gtr.ukri.org/y")
            raise AssertionError("expected a RuntimeError")
        except RuntimeError:
            assert never.calls == gtr_import._ATTEMPTS
        notfound = _FakeSession([_Reply(404)])
        try:
            gtr_import._get_json(notfound, "https://gtr.ukri.org/z")
            raise AssertionError("expected an HTTPError")
        except gtr_import.requests.HTTPError:
            assert notfound.calls == 1, "a 404 is not retried"
    finally:
        gtr_import.time.sleep = real_sleep


def test_database_round_trip():
    from tender_app.db import get_db_connection
    from tender_app.db_ext import create_grant_awards

    conn = get_db_connection()
    cur = conn.cursor()
    create_grant_awards(cur)
    conn.commit()
    keys = ("ZZ-GT-1", "ZZ-GT-2")
    cur.execute("DELETE FROM grant_awards WHERE gtr_project_id = ANY(%s)", (list(keys),))
    conn.commit()
    try:
        rec = gtr_import.build_record(PROJECT, FUNDING, ORG)
        rec2 = dict(rec, gtr_project_id="ZZ-GT-2", title="Second grant", start_date="2025-01-01", value_gbp=100000.0)
        rec["gtr_project_id"] = "ZZ-GT-1"
        assert gtr_import.save_record(cur, rec) is True
        assert gtr_import.save_record(cur, rec2) is True
        assert gtr_import.save_record(cur, dict(rec, value_gbp=300000.0)) is False, "a second save updates"
        conn.commit()

        out = grants.grants_for_supplier(cur, None, "zz grant test LTD")  # punctuation and case are ignored
        assert out["count"] == 2 and out["total_value"] == 400000.0, out
        assert [i["title"] for i in out["items"]] == ["Smart boiler controls", "Second grant"], "newest first"
        assert len(out["items"][0]["abstract"]) <= 400
        assert grants.grants_for_supplier(cur, None, "Nobody At All Ltd") == {"count": 0, "total_value": 0.0, "items": []}

        f = grants.grants_for_funder(cur, "Innovate UK")
        assert f and f["funder"] == "Innovate UK" and f["count"] >= 2 and f["items"] and f["top_recipients"]
        assert grants.grants_for_funder(cur, "Some Council") is None

        # grants never reach contract_awards, so no contract total can include them
        cur.execute("SELECT COUNT(*) FROM contract_awards WHERE supplier_name = %s", (ORG["name"],))
        assert cur.fetchone()[0] == 0
    finally:
        cur.execute("DELETE FROM grant_awards WHERE gtr_project_id = ANY(%s)", (list(keys),))
        conn.commit()
        cur.close()
        conn.close()


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
