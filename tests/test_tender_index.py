"""The tender notice index (docs/PORTAL_INDEX_DESIGN.md, steps 1 and 2): normalising OCDS releases and storing them.

Run: python tests/test_tender_index.py   (the database half needs the dev database; it uses its own source name and cleans up)
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tender_app import tender_index as ti  # noqa: E402

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)


def release(**over):
    base = {
        "ocid": "ocds-h6vhtk-zz0001", "id": "abc-123-1", "date": "2026-10-29T09:00:00Z", "tag": ["tender"],
        "buyer": {"name": "Leeds City Council"},
        "tender": {"title": "Business intelligence platform", "description": "A BI and analytics platform.", "status": "active",
                   "tenderPeriod": {"endDate": "2026-11-13T12:00:00Z"}, "value": {"amount": 250000, "currency": "GBP"},
                   "items": [{"classification": {"id": "72316000", "description": "Data analysis services"},
                              "deliveryAddresses": [{"region": "UKE4", "locality": "Leeds", "postalCode": "LS1 1UR"}]}]},
    }
    base.update(over)
    return base


def test_an_invitation_with_a_future_deadline_is_open():
    n = ti.normalise_release(release(), "zz_test", "Test", now=NOW)
    assert n["status"] == "open" and n["notice_type"] == "tender"
    assert n["deadline_at"].date().isoformat() == "2026-11-13" and n["published_at"].date().isoformat() == "2026-10-29"
    assert n["value"] == 250000 and n["currency"] == "GBP"
    assert n["postcode"] == "LS1 1UR" and "Leeds" in n["location_text"] and n["cpv_codes"].startswith("72316000")
    assert n["contracting_authority"] == "Leeds City Council"


def test_award_notices_and_notices_without_a_deadline_are_never_open():
    """Re-test T1: PCS 'Award of ...' notices with no deadline filled Opportunities."""
    award = ti.normalise_release(release(tag=["award"]), "zz_test", "Test", now=NOW)
    assert award["status"] == "awarded" and award["notice_type"] == "award"
    titled = release()
    titled["tender"] = {**titled["tender"], "title": "Award of Intelligent Email Security"}
    assert ti.normalise_release(titled, "zz_test", "Test", now=NOW)["status"] == "awarded"
    nodl = release()
    nodl["tender"] = {k: v for k, v in nodl["tender"].items() if k != "tenderPeriod"}
    assert ti.normalise_release(nodl, "zz_test", "Test", now=NOW)["status"] == "closed"
    past = release()
    past["tender"] = {**past["tender"], "tenderPeriod": {"endDate": "2026-10-01T12:00:00Z"}}
    assert ti.normalise_release(past, "zz_test", "Test", now=NOW)["status"] == "closed"
    today = release()
    today["tender"] = {**today["tender"], "tenderPeriod": {"endDate": "2026-10-30T09:00:00Z"}}
    assert ti.normalise_release(today, "zz_test", "Test", now=NOW)["status"] == "open", "a deadline today is still open"


def test_cancelled_and_complete_tenders_are_not_open_and_a_release_without_an_id_is_skipped():
    for status, expected in (("cancelled", "cancelled"), ("withdrawn", "cancelled"), ("complete", "closed")):
        r = release()
        r["tender"] = {**r["tender"], "status": status}
        assert ti.normalise_release(r, "zz_test", "Test", now=NOW)["status"] == expected, status
    assert ti.normalise_release({"tender": {"title": "x"}}, "zz_test", "Test") is None


def test_a_missing_address_gives_empty_location_not_a_made_up_one():
    r = release()
    r["tender"] = {**r["tender"], "items": []}
    n = ti.normalise_release(r, "zz_test", "Test", now=NOW)
    assert n["location_text"] == "" and n["region"] == "" and n["postcode"] == ""


def _conn():
    try:
        from tender_app.db import get_db_connection
        return get_db_connection()
    except (Exception, SystemExit) as exc:
        raise unittest.SkipTest(f"no database: {exc}")


def test_storing_a_notice_twice_updates_it_and_expiry_closes_it():
    conn = _conn()
    cur = conn.cursor()
    try:
        ti.ensure_tables(cur)
        cur.execute("DELETE FROM tender_notices WHERE source = 'zz_test'")
        soon = datetime.now(timezone.utc) + timedelta(days=5)
        r = release()
        r["tender"] = {**r["tender"], "tenderPeriod": {"endDate": soon.strftime("%Y-%m-%dT%H:%M:%SZ")}}
        n = ti.normalise_release(r, "zz_test", "Test")
        assert ti.upsert_notices(cur, [n]) == (1, 1)
        n["title"] = "Business intelligence platform (amended)"
        assert ti.upsert_notices(cur, [n]) == (1, 0), "the second write is an update, not a new row"
        cur.execute("SELECT count(*), max(title), max(status) FROM tender_notices WHERE source = 'zz_test'")
        assert cur.fetchone() == (1, "Business intelligence platform (amended)", "open")
        cur.execute("SELECT count(*) FROM tender_notices WHERE source = 'zz_test' AND search_tsv @@ websearch_to_tsquery('english', 'analytics')")
        assert cur.fetchone()[0] == 1, "the text index finds the notice by a word in its description"
        assert ti.close_expired(cur, now=soon + timedelta(days=1)) >= 1
        cur.execute("SELECT status, closed_at IS NOT NULL FROM tender_notices WHERE source = 'zz_test'")
        assert cur.fetchone() == ("closed", True)
        assert ti.close_unseen(cur, "zz_test", datetime.now(timezone.utc) + timedelta(days=1)) == 0, "only open notices are closed as unseen"
    finally:
        conn.rollback()
        cur.execute("DELETE FROM tender_notices WHERE source = 'zz_test'")
        conn.commit()
        conn.close()


def test_a_failing_feed_is_recorded_not_raised_and_keeps_what_it_wrote():
    conn = _conn()
    conn.close()

    def batches(state):
        yield [release(ocid="ocds-zz-1"), release(ocid="ocds-zz-2")]
        raise RuntimeError("the feed stopped answering")

    out = ti._ingest(_conn, "zz_test", batches, lambda r: ti.normalise_release(r, "zz_test", "Test"))
    assert out["ok"] is False and out["complete"] is False and out["seen"] == 2 and "stopped answering" in out["error"], out
    c = _conn()
    cur = c.cursor()
    cur.execute("SELECT count(*) FROM tender_notices WHERE source = 'zz_test'")
    assert cur.fetchone()[0] == 2, "the two notices written before the failure are kept"
    cur.execute("SELECT ok, complete, rows_seen FROM index_runs WHERE source = 'zz_test' ORDER BY id DESC LIMIT 1")
    assert cur.fetchone() == (False, False, 2)
    cur.execute("DELETE FROM tender_notices WHERE source = 'zz_test'")
    cur.execute("DELETE FROM index_runs WHERE source = 'zz_test'")
    c.commit()
    c.close()


def test_infinity_in_a_feed_is_stored_as_null():
    """Find a Tender sends an unbounded maximumParticipants as Infinity; Postgres rejects it in JSON."""
    r = release()
    r["tender"] = {**r["tender"], "techniques": {"frameworkAgreement": {"maximumParticipants": float("inf")}}}
    conn = _conn()
    cur = conn.cursor()
    try:
        ti.ensure_tables(cur)
        cur.execute("DELETE FROM tender_notices WHERE source = 'zz_test'")
        assert ti.upsert_notices(cur, [ti.normalise_release(r, "zz_test", "Test", now=NOW)]) == (1, 1)
    finally:
        conn.rollback()
        conn.close()


def test_planning_notices_are_early_signals_and_an_older_release_never_overwrites_a_newer_one():
    plan = ti.normalise_release(release(tag=["planning"]), "zz_test", "Test", now=NOW)
    assert plan["notice_type"] == "planning" and plan["status"] == "planned"
    upd = ti.normalise_release(release(tag=["tenderUpdate"]), "zz_test", "Test", now=NOW)
    assert upd["notice_type"] == "tender" and upd["status"] == "open"
    conn = _conn()
    cur = conn.cursor()
    try:
        ti.ensure_tables(cur)
        cur.execute("DELETE FROM tender_notices WHERE source = 'zz_test'")
        newer = ti.normalise_release(release(date="2026-10-29T12:00:00Z", tag=["award"]), "zz_test", "Test", now=NOW)
        older = ti.normalise_release(release(date="2026-10-20T12:00:00Z"), "zz_test", "Test", now=NOW)
        assert ti.upsert_notices(cur, [newer]) == (1, 1)
        assert ti.upsert_notices(cur, [older]) == (1, 0)
        cur.execute("SELECT status FROM tender_notices WHERE source = 'zz_test'")
        assert cur.fetchone()[0] == "awarded", "the award release is newer, so the earlier tender release must not reopen it"
    finally:
        conn.rollback()
        conn.close()


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok   ", name)
            except unittest.SkipTest as exc:
                print("skip ", name, exc)
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print("FAIL ", name, repr(exc)[:300])
    sys.exit(1 if failures else 0)
