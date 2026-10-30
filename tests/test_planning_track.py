"""Re-test 3 PN3: tracking a planning lead puts it in Pipeline and raises stage alerts (decided, conditions discharged, start on site).

Run: python tests/test_planning_track.py   (the DB part needs the dev database; zz- fixtures are removed in `finally`)"""
import os
import sys
import time
import unittest

os.environ.setdefault("ENABLE_SCHEDULERS", "0")
os.environ.setdefault("ENABLE_EMAIL_SCHEDULER", "0")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tender_app import planning_track as pt  # noqa: E402

SITE = {"id": "Zz/26/001", "authority": "Zz", "address": "1 Test Road, Zzton", "description": "Erection of 120 dwellings", "app_state": "Undecided"}


def related(desc, state="Permitted", app_type="Conditions", rid="Zz/26/002"):
    return {"id": rid, "authority": "Zz", "address": "1 test road, zzton ", "description": desc, "app_state": state, "app_type": app_type}


def test_stage_follows_the_application_and_the_applications_at_the_same_address():
    assert pt.stage_of(SITE) == "submitted"
    assert pt.stage_of({**SITE, "app_state": "Permitted"}) == "decided"
    assert pt.stage_of({**SITE, "app_state": "Undecided", "decided_date": "2026-10-01"}) == "decided"
    permitted = {**SITE, "app_state": "Conditions"}
    assert pt.stage_of(permitted, [related("Approval of details pursuant to condition 4")]) == "conditions_discharged"
    assert pt.stage_of(permitted, [related("Discharge of conditions 3 and 5", state="Undecided")]) == "decided", "an undecided discharge is not discharged"
    assert pt.stage_of(permitted, [related("Notice of commencement of development", state="Other", app_type="Other")]) == "started"
    assert pt.stage_of(permitted, [related("Discharge of condition 2"), related("Start of works notice", app_type="Other", rid="Zz/26/003")]) == "started"
    other_site = {**related("Notice of commencement"), "address": "9 Elsewhere Lane"}
    assert pt.stage_of(permitted, [other_site]) == "decided", "another address is not this scheme"
    assert pt.stage_of({**SITE, "app_state": "Rejected"}, [related("Notice of commencement")]) == "decided", "a refused scheme does not start"


def test_alerts_say_each_stage_passed_once_and_nothing_when_it_has_not_moved():
    assert pt.alerts_for_change("submitted", "submitted", "Scheme") == []
    assert pt.alerts_for_change("decided", "submitted", "Scheme") == [], "never backwards"
    assert pt.alerts_for_change("submitted", "decided", "Scheme") == ["Planning lead “Scheme”: decided."]
    two = pt.alerts_for_change("decided", "started", "Scheme")
    assert two == ["Planning lead “Scheme”: conditions discharged.", "Planning lead “Scheme”: start on site."]
    assert len(pt.alerts_for_change("submitted", "started", "S")) == 3


def test_tracking_adds_a_pipeline_entry_and_raises_an_alert_when_the_lead_is_decided():
    from server import create_app
    from tender_app.db import get_db_connection
    try:
        conn = get_db_connection()
    except Exception as exc:  # noqa: BLE001
        raise unittest.SkipTest(f"no database: {exc}")
    user = "zz_track@test.invalid"
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM planning_applications WHERE id LIKE 'Zz/26/%'")
        cur.execute("INSERT INTO planning_applications (id, uid, authority, address, description, app_state, app_type, app_size) "
                    "VALUES ('Zz/26/001','26/001','Zz','1 Test Road, Zzton','Erection of 120 dwellings','Undecided','Full','Large')")
        conn.commit()
        c = create_app().test_client()
        with c.session_transaction() as s:
            s.update(logged_in=True, username=user, email=user, last_activity=time.time(), csrf_token="x")
        H = {"X-CSRF-Token": "x"}
        r = c.get("/api/planning/track/Zz/26/001")
        if r.status_code in (402, 403):
            return
        assert r.get_json()["tracked"] is False
        assert c.post("/api/planning/track/Zz/26/001").status_code == 403, "CSRF is required"
        r = c.post("/api/planning/track/Zz/26/001", headers=H)
        assert r.status_code == 200 and r.get_json()["stage"] == "submitted", r.get_json()
        assert c.post("/api/planning/track/Zz/26/nope", headers=H).status_code == 404
        assert c.get("/api/planning/track/Zz/26/001").get_json()["tracked"] is True
        items = c.get("/api/pipeline").get_json()["items"]
        mine = [i for i in items if i["tender_key"] == "planning:Zz/26/001"]
        assert len(mine) == 1 and mine[0]["stage"] == "watching" and mine[0]["added_from"] == "planning" and "120 dwellings" in mine[0]["title"], mine
        assert [a for a in c.get("/api/deep-alerts").get_json()["alerts"] if a["kind"] == "planning_stage"] == [], "nothing has changed yet"
        cur.execute("UPDATE planning_applications SET app_state = 'Conditions', decided_date = CURRENT_DATE WHERE id = 'Zz/26/001'")
        conn.commit()
        alerts = [a for a in c.get("/api/deep-alerts").get_json()["alerts"] if a["kind"] == "planning_stage"]
        assert len(alerts) == 1 and alerts[0]["tender_key"] == "planning:Zz/26/001" and "decided" in alerts[0]["message"], alerts
        again = [a for a in c.get("/api/deep-alerts").get_json()["alerts"] if a["kind"] == "planning_stage"]
        assert len(again) == 1, "the same stage is not alerted twice"
        assert c.delete("/api/planning/track/Zz/26/001", headers=H).status_code == 200
        assert c.get("/api/planning/track/Zz/26/001").get_json()["tracked"] is False
        assert not [i for i in c.get("/api/pipeline").get_json()["items"] if i["tender_key"] == "planning:Zz/26/001"]
    finally:
        conn.rollback()
        cur.execute("DELETE FROM planning_applications WHERE id LIKE 'Zz/26/%'")
        cur.execute("DELETE FROM pipeline WHERE username = %s", (user,))
        cur.execute("DELETE FROM deep_alerts WHERE username = %s", (user,))
        cur.execute("DELETE FROM planning_tracks WHERE username = %s", (user,))
        conn.commit()
        conn.close()


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print("PASS", name)
            except unittest.SkipTest as exc:
                print("SKIP", name, exc)
            except Exception as exc:  # noqa: BLE001
                import traceback; traceback.print_exc()
                failures += 1; print("FAIL", name, "->", repr(exc))
    sys.exit(1 if failures else 0)
