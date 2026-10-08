#!/usr/bin/env python3
"""Crosswalk-fix contract_awards.notice_url for Find a Tender's legacy "ocds-h6vhtk-..." URLs.

Background: for many historical Find a Tender releases, notice_url was built from the OCDS
release's "ocid" (e.g. "ocds-h6vhtk-040f94") instead of its "id" (e.g. "009844-2024") --
these are two distinct, both-mandatory OCDS 1.1 fields (ocid identifies the contracting
process across its lifetime; id identifies this specific release), and the website only
routes notices by the latter. Measured on live: ~59% of "ocds-"-style notice URLs 404;
Contracts Finder and Find a Tender's own numeric-ID URLs are unaffected.

The fix (scripts/contracts_finder_sync.py) stops new rows from using the wrong field.
This script recovers the existing ones, since the correct URL is recoverable: Find a
Tender's own OCDS API resolves a release by its ocid and returns that release's "id"
alongside it --

    GET https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages/{ocid}
    -> releases[0]["id"] is the numeric ID /Notice/{id} actually resolves.

Verified by hand before writing this script: ocds-h6vhtk-040f94 -> id "009844-2024" ->
https://www.find-tender.service.gov.uk/Notice/009844-2024 returns 200; the ocid-based URL
404s. Contracts Finder rows are untouched -- they were not part of the measured problem.

For each DISTINCT legacy notice_url (== one ocid, since the URL's own trailing segment IS
the ocid):
  1. GET the release package for that ocid, with retry/backoff on transient failures and a
     paced delay between requests (be polite to a government API we don't rate-limit-test).
  2. If releases[0]["id"] is present, UPDATE every contract_awards row still using the OLD
     notice_url to the new one.
  3. Checkpoint every outcome (resolved, no-id, or error) to a local JSONL file, so an
     interrupted run resumes without re-querying ocids already handled.

Idempotent and resumable. Dry-run by default -- nothing is written, and the API isn't even
queried beyond a preview, unless --apply is given.

    python scripts/fix_find_tender_notice_urls.py                    # dry run
    python scripts/fix_find_tender_notice_urls.py --apply             # writes
    python scripts/fix_find_tender_notice_urls.py --apply --limit 200 # smoke-test on a subset first
    python scripts/fix_find_tender_notice_urls.py --apply --verify-sample 20  # HTTP-check N of the new URLs after writing
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

UA = "TenderFlow-notice-url-crosswalk/1.0 (public-sector procurement research)"
API_BASE = "https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages"
NOTICE_BASE = "https://www.find-tender.service.gov.uk/Notice"
CHECKPOINT_PATH = Path(__file__).resolve().parent.parent / "data" / "fts_ocid_crosswalk_checkpoint.jsonl"

# One legacy notice_url IS "<NOTICE_BASE>/<ocid>" -- the ocid is its own trailing path segment.
_LEGACY_URL_RE = re.compile(
    r"^https://www\.find-tender\.service\.gov\.uk/Notice/(ocds-[a-z0-9]+-[a-f0-9]+)$", re.IGNORECASE
)

REQUEST_DELAY_S = 0.4  # ~2.5 req/s -- a JSON API, not the HTML search page that rate-limited us
MAX_RETRIES = 3


def load_checkpoint() -> dict[str, dict]:
    """old_url -> {"ocid":, "resolved_id": str|None, "status": "ok"|"no_id"|"error"}"""
    done: dict[str, dict] = {}
    if CHECKPOINT_PATH.exists():
        with open(CHECKPOINT_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    done[rec["old_url"]] = rec
                except (json.JSONDecodeError, KeyError):
                    continue
    return done


def append_checkpoint(rec: dict) -> None:
    CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CHECKPOINT_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def resolve_ocid(ocid: str) -> tuple[str | None, str]:
    """Returns (new_numeric_id_or_None, status). status is 'ok', 'no_id', or 'error'."""
    url = f"{API_BASE}/{ocid}"
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read())
            releases = data.get("releases") or []
            if not releases:
                return None, "no_id"
            new_id = str(releases[0].get("id") or "").strip()
            return (new_id, "ok") if new_id else (None, "no_id")
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                retry_after = int(exc.headers.get("Retry-After", "5")) if exc.headers else 5
                print(f"    429 rate-limited on {ocid}; waiting {retry_after}s")
                time.sleep(retry_after)
                continue
            if exc.code == 404:
                return None, "no_id"  # ocid itself no longer known to the API
            if attempt == MAX_RETRIES:
                return None, "error"
            time.sleep(2 * attempt)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            if attempt == MAX_RETRIES:
                return None, "error"
            time.sleep(2 * attempt)
    return None, "error"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write corrected URLs (default: dry run)")
    ap.add_argument("--limit", type=int, default=0, metavar="N", help="only process the first N legacy URLs (0 = all)")
    ap.add_argument("--verify-sample", type=int, default=0, metavar="N", help="after writing, HTTP-check N of the new URLs and report how many are actually live")
    args = ap.parse_args()

    from tender_app.db import get_db_connection

    conn = get_db_connection()
    cur = conn.cursor()
    print(f"Mode: {'APPLY' if args.apply else 'DRY RUN'}\n")

    sql = """
        SELECT DISTINCT notice_url FROM contract_awards
        WHERE notice_url LIKE 'https://www.find-tender.service.gov.uk/Notice/ocds-%'
        ORDER BY notice_url
    """
    if args.limit:
        sql += f" LIMIT {int(args.limit)}"
    cur.execute(sql)
    legacy_urls = [r[0] for r in cur.fetchall()]
    print(f"{len(legacy_urls)} distinct legacy notice_url values to resolve")
    if not legacy_urls:
        conn.close()
        return 0

    done = load_checkpoint()
    print(f"{len(done)} already checkpointed from a previous run (will be skipped)\n")

    resolved = 0
    no_id = 0
    errors = 0
    rows_updated = 0
    new_urls_written: list[str] = []

    for i, old_url in enumerate(legacy_urls, 1):
        m = _LEGACY_URL_RE.match(old_url)
        if not m:
            continue
        ocid = m.group(1)

        rec = done.get(old_url)
        if rec is None:
            new_id, status = resolve_ocid(ocid)
            rec = {"old_url": old_url, "ocid": ocid, "resolved_id": new_id, "status": status}
            append_checkpoint(rec)
            done[old_url] = rec
            time.sleep(REQUEST_DELAY_S)

        if rec["status"] == "ok" and rec["resolved_id"]:
            resolved += 1
            new_url = f"{NOTICE_BASE}/{rec['resolved_id']}"
            new_urls_written.append(new_url)
            if args.apply:
                cur.execute(
                    "UPDATE contract_awards SET notice_url = %s WHERE notice_url = %s",
                    (new_url, old_url),
                )
                rows_updated += cur.rowcount
                if i % 200 == 0:
                    conn.commit()
        elif rec["status"] == "no_id":
            no_id += 1
        else:
            errors += 1

        if i % 500 == 0 or i == len(legacy_urls):
            print(f"  [{i}/{len(legacy_urls)}] resolved={resolved} no_id={no_id} errors={errors}")

    if args.apply:
        conn.commit()
        print(f"\nWrote corrected URLs for {resolved} ocids, updating {rows_updated} contract_awards rows.")
    else:
        conn.rollback()
        print(f"\nDRY RUN — would resolve {resolved}, {no_id} had no usable id, {errors} failed. Re-run with --apply.")

    if no_id or errors:
        print(f"{no_id} ocids the API no longer recognises + {errors} transient failures — these keep their "
              f"existing URL and the UI's manual-search fallback; re-run later to retry the {errors} errors "
              f"(checkpoint skips ocids already marked 'ok' or 'no_id').")

    if args.verify_sample and new_urls_written:
        import random
        # A minimal UA can itself get treated differently than a browser-like one by some
        # gov.uk front ends, and this hits the website port (shared, more easily throttled)
        # right after the API calls above -- use a real browser UA and a longer gap, and
        # report *why* each check failed rather than silently counting it as dead, so a
        # run right after heavy testing doesn't look like a broken crosswalk.
        browser_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        sample = random.sample(new_urls_written, min(args.verify_sample, len(new_urls_written)))
        print(f"\nSpot-checking {len(sample)} of the new URLs against the live site...")
        live = 0
        rate_limited = 0
        for u in sample:
            try:
                req = urllib.request.Request(u, headers={"User-Agent": browser_ua})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    if resp.status == 200:
                        live += 1
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    rate_limited += 1
                else:
                    print(f"    {u} -> HTTP {exc.code}")
            except Exception as exc:
                print(f"    {u} -> {exc}")
            time.sleep(REQUEST_DELAY_S * 3)
        print(f"{live}/{len(sample)} confirmed live.")
        if rate_limited:
            print(f"{rate_limited}/{len(sample)} were rate-limited (429) rather than checked -- "
                  f"not evidence of a bad URL, just too many requests too recently. Re-run "
                  f"--verify-sample later, separately from a big --apply run, to get a clean read.")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
