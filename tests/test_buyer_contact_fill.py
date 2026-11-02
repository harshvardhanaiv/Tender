"""Re-test 4 B8: a buyer's contact block comes from the notices we hold, not only from searched ones.
Run: python tests/test_buyer_contact_fill.py (dev database; own rows, cleaned up)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ENABLE_SCHEDULERS", "0")
os.environ.setdefault("ENABLE_EMAIL_SCHEDULER", "0")
from server import create_app  # noqa: E402

create_app()   # wires the buyers blueprint to the database
from tender_app.blueprints import buyers_bp as b  # noqa: E402
from tender_app import tender_index as ti  # noqa: E402

NAME = "ZZ Contact Test City Council"


def _notice(rid, **party):
    p = {"id": "x", "name": NAME, "roles": ["buyer"], **party}
    return {"source": "find_tender", "resource_id": rid, "title": "A notice", "description": "", "contracting_authority": NAME, "notice_type": "tender",
            "status": "open", "published_at": None, "deadline_at": None, "value": None, "currency": "GBP", "cpv_codes": "", "location_text": "",
            "region": "", "postcode": "", "detail_url": "", "raw": {"ocid": rid, "parties": [p]}, "source_label": "Find a Tender"}


def test_the_contact_block_of_indexed_notices_fills_the_four_fields():
    conn = b._get_connection()
    cur = conn.cursor()
    try:
        ti.ensure_tables(cur)
        ti.upsert_notices(cur, [
            _notice("ZZ-CONTACT-1", contactPoint={"email": "procurement@zz-city.example", "telephone": "0113 000 0000"},
                    address={"streetAddress": "1 Civic Hall", "locality": "Leeds", "postalCode": "LS1 1UR"}),
            _notice("ZZ-CONTACT-2", contactPoint={"url": "https://www.zz-city.example"}),
        ])
        conn.commit()
        info = b._fetch_buyer_contact_info(cur, conn, NAME)
        assert info == {"email": "procurement@zz-city.example", "phone": "0113 000 0000", "website": "https://www.zz-city.example",
                        "address": "1 Civic Hall, Leeds, LS1 1UR"}, info
        # another buyer's party in the same notice is never used
        cur.execute("UPDATE tender_notices SET raw = %s WHERE resource_id = 'ZZ-CONTACT-1'",
                    (json.dumps({"parties": [{"name": "Somebody Else", "roles": ["buyer"], "contactPoint": {"email": "x@else.example"}}]}),))
        cur.execute("DELETE FROM tender_notices WHERE resource_id = 'ZZ-CONTACT-2'")
        conn.commit()
        info = b._fetch_buyer_contact_info(cur, conn, NAME)
        assert info["email"] == "Not available", info
    finally:
        conn.rollback()
        cur.execute("DELETE FROM tender_notices WHERE resource_id LIKE 'ZZ-CONTACT-%'")
        conn.commit()
        conn.close()


def test_an_inbox_found_by_the_contact_lookup_is_used_when_notices_have_none():
    from tender_app import market_radar as mr
    conn = b._get_connection()
    cur = conn.cursor()
    key = mr.canonical_buyer_key(NAME)
    try:
        cur.execute("INSERT INTO buyer_contact_cache (buyer_key, status, email, candidates, website, checked_at, lookup_version) "
                    "VALUES (%s, 'found', 'contracts@zz-city.example', '[]', 'https://zz-city.example', now(), 3) "
                    "ON CONFLICT (buyer_key) DO UPDATE SET status = 'found', email = EXCLUDED.email, website = EXCLUDED.website", (key,))
        conn.commit()
        info = b._fetch_buyer_contact_info(cur, conn, NAME)
        assert info["email"] == "contracts@zz-city.example" and info["website"] == "https://zz-city.example", info
    finally:
        conn.rollback()
        cur.execute("DELETE FROM buyer_contact_cache WHERE buyer_key = %s", (key,))
        conn.commit()
        conn.close()


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
            print("ok   ", name)
    print(f"{n} passed")
