"""Import the Charity Commission register of charities (England and Wales) and link it to suppliers.

The Commission publishes a daily extract of the public register under the Open Government Licence, no key
needed. We read the tab-delimited charity file (about 44 MB zipped, 400k rows including removed charities)
in a stream, keep the registered main charities, and:

  * store them in charity_register;
  * link a supplier to its charity in supplier_charity: by company number when the charity is a company
    (the strong match), else by an exact name match when that name belongs to exactly one registered
    charity and the supplier has no Companies House number of its own (a limited company that merely shares
    a charity's name is a different organisation);
  * mark the linked suppliers VCSE, which is what a registered charity is.

    python -m tender_app.charity_import                  # download today's extract and import it
    python -m tender_app.charity_import --file x.zip     # import an extract already on disk
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import tempfile
import zipfile
from datetime import datetime
from typing import Any, Iterator

import requests

from etenders_scraper.awards import supplier_name_key

EXTRACT_URL = "https://ccewuksprdoneregsadata1.blob.core.windows.net/data/txt/publicextract.charity.zip"
MEMBER = "publicextract.charity.txt"
_BATCH = 5000
_ACTIVITIES_MAX = 1000

# What a Companies House number looks like (England and Wales, Scotland, NI); the fallback ids the supplier
# loader invents ("GB-FTS-...", "CF...") and "Not available" do not.
CH_NUMBER_SQL = "^([0-9]{8}|[A-Z]{2}[0-9]{6})$"

COLUMNS = (
    "charity_number", "name", "name_key", "company_number", "registered_on", "income", "expenditure", "year_end",
    "postcode", "phone", "email", "website", "is_cio", "activities",
)


def normalise_company_number(raw: str | None) -> str | None:
    n = (raw or "").strip().upper().replace(" ", "")
    if not n:
        return None
    if n.isdigit():
        return n.zfill(8) if len(n) <= 8 else None
    return n if len(n) == 8 else None


def _day(raw: str | None) -> str | None:
    text = (raw or "").strip()[:10]
    return text if len(text) == 10 and text[4] == "-" else None


def _money(raw: str | None) -> float | None:
    try:
        return float(raw) if raw not in (None, "") else None
    except ValueError:
        return None


def parse_row(row: dict[str, str]) -> dict[str, Any] | None:
    """One register row, or None when it is not a registered main charity we can name."""
    if row.get("charity_registration_status") != "Registered" or (row.get("linked_charity_number") or "0") != "0":
        return None
    name = (row.get("charity_name") or "").strip()
    try:
        number = int(row.get("registered_charity_number") or "")
    except ValueError:
        return None
    if not name:
        return None
    website = (row.get("charity_contact_web") or "").strip()
    return {
        "charity_number": number,
        "name": name,
        "name_key": supplier_name_key(name),
        "company_number": normalise_company_number(row.get("charity_company_registration_number")),
        "registered_on": _day(row.get("date_of_registration")),
        "income": _money(row.get("latest_income")),
        "expenditure": _money(row.get("latest_expenditure")),
        "year_end": _day(row.get("latest_acc_fin_period_end_date")),
        "postcode": (row.get("charity_contact_postcode") or "").strip() or None,
        "phone": (row.get("charity_contact_phone") or "").strip() or None,
        "email": (row.get("charity_contact_email") or "").strip().lower() or None,
        "website": website or None,
        "is_cio": (row.get("charity_is_cio") or "").strip().lower() == "true",
        "activities": ((row.get("charity_activities") or "").strip()[:_ACTIVITIES_MAX]) or None,
    }


def iter_charities(zip_path: str) -> Iterator[dict[str, Any]]:
    # The file is tab-delimited with no quoting at all: treating quotes as quoting loses rows.
    csv.field_size_limit(10_000_000)
    with zipfile.ZipFile(zip_path) as z:
        member = MEMBER if MEMBER in z.namelist() else z.namelist()[0]
        with z.open(member) as raw:
            reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""), delimiter="\t", quoting=csv.QUOTE_NONE)
            for row in reader:
                rec = parse_row(row)
                if rec:
                    yield rec


def download(dest_dir: str, log=print) -> str:
    path = os.path.join(dest_dir, "publicextract.charity.zip")
    with requests.get(EXTRACT_URL, stream=True, timeout=120, headers={"User-Agent": "TenderFlow charity import"}) as resp:
        resp.raise_for_status()
        with open(path, "wb") as out:
            for chunk in resp.iter_content(1 << 20):
                out.write(chunk)
    log(f"[charity] downloaded {os.path.getsize(path) / 1e6:.0f} MB")
    return path


def load_register(conn: Any, records: Iterator[dict[str, Any]], log=print, prune: bool = True) -> int:
    """Upsert the registered charities; with prune, charities missing from this extract (removed since) are deleted."""
    from psycopg2.extras import execute_values

    cur = conn.cursor()
    cur.execute("SELECT NOW()")
    started = cur.fetchone()[0]
    cols = ", ".join(COLUMNS)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in COLUMNS if c != "charity_number")
    sql = (f"INSERT INTO charity_register ({cols}, updated_at) VALUES %s "
           f"ON CONFLICT (charity_number) DO UPDATE SET {updates}, updated_at = EXCLUDED.updated_at")
    total = 0
    batch: list[tuple] = []

    def flush() -> None:
        nonlocal total
        if batch:
            execute_values(cur, sql, batch, template="(" + ", ".join(["%s"] * len(COLUMNS)) + ", NOW())")
            total += len(batch)
            batch.clear()
            log(f"[charity] loaded {total}")

    for rec in records:
        batch.append(tuple(rec[c] for c in COLUMNS))
        if len(batch) >= _BATCH:
            flush()
    flush()
    if prune:
        cur.execute("DELETE FROM charity_register WHERE updated_at < %s", (started,))
    conn.commit()
    cur.close()
    return total


def link_suppliers(conn: Any, supplier_ids: list[int] | None = None) -> dict[str, int]:
    """Rebuild supplier_charity and mark the linked suppliers VCSE (all suppliers, or just `supplier_ids`)."""
    cur = conn.cursor()
    scope, scope_params = ("AND s.id = ANY(%s)", (supplier_ids,)) if supplier_ids is not None else ("", ())
    if supplier_ids is None:
        cur.execute("DELETE FROM supplier_charity")
    else:
        cur.execute("DELETE FROM supplier_charity WHERE supplier_id = ANY(%s)", (supplier_ids,))
    cur.execute(
        """
        INSERT INTO supplier_charity (supplier_id, charity_number, match_kind)
        SELECT DISTINCT ON (s.id) s.id, c.charity_number, 'company_number'
        FROM suppliers s
        JOIN charity_register c ON c.company_number = UPPER(TRIM(s.company_number))
        WHERE UPPER(TRIM(s.company_number)) ~ %s """ + scope + """
        ORDER BY s.id, c.charity_number
        """,
        (CH_NUMBER_SQL,) + scope_params,
    )
    by_company = cur.rowcount
    cur.execute(
        """
        INSERT INTO supplier_charity (supplier_id, charity_number, match_kind)
        SELECT s.id, c.charity_number, 'name'
        FROM suppliers s
        JOIN (
            SELECT MIN(charity_number) AS charity_number, name_key
            FROM charity_register GROUP BY name_key HAVING COUNT(*) = 1
        ) c ON c.name_key = UPPER(REGEXP_REPLACE(s.name, '[^A-Za-z0-9]', '', 'g'))
        WHERE COALESCE(UPPER(TRIM(s.company_number)), '') !~ %s """ + scope + """
          AND NOT EXISTS (SELECT 1 FROM supplier_charity x WHERE x.supplier_id = s.id)
        ON CONFLICT (supplier_id) DO NOTHING
        """,
        (CH_NUMBER_SQL,) + scope_params,
    )
    by_name = cur.rowcount
    cur.execute(
        """
        UPDATE suppliers SET vcse_status = 'VCSE'
        WHERE id IN (SELECT supplier_id FROM supplier_charity) AND COALESCE(vcse_status, '') <> 'VCSE'
          AND (%s IS NULL OR id = ANY(%s))
        """,
        (supplier_ids, supplier_ids),
    )
    marked = cur.rowcount
    conn.commit()
    cur.close()
    return {"by_company_number": by_company, "by_name": by_name, "newly_marked_vcse": marked}


def import_register(conn: Any, zip_path: str, log=print) -> dict[str, Any]:
    from tender_app.db_ext import create_charity_tables

    cur = conn.cursor()
    create_charity_tables(cur)
    conn.commit()
    cur.close()
    stats: dict[str, Any] = {"charities": load_register(conn, iter_charities(zip_path), log)}
    stats.update(link_suppliers(conn))
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", help="a publicextract.charity.zip already downloaded (the tab-delimited one)")
    args = parser.parse_args()
    from tender_app.db import get_db_connection

    conn = get_db_connection()
    try:
        if args.file:
            print(import_register(conn, args.file), "at", datetime.now().isoformat(timespec="seconds"))
        else:
            with tempfile.TemporaryDirectory() as tmp:
                print(import_register(conn, download(tmp)), "at", datetime.now().isoformat(timespec="seconds"))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
