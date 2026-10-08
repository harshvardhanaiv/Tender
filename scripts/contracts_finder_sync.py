#!/usr/bin/env python3
"""Contracts Finder & Find a Tender OCDS Backfill and Incremental Sync Script.

Ingests awarded-contract notices for Supplier Intelligence from official public OCDS data sources:
1. Contracts Finder (UK below & above threshold notices, 2016-present):
   - Bulk downloadable archives from Open Contracting Partnership Data Registry (Publication 128)
   - Live Search API for incremental updates
2. Find a Tender (UK above-threshold notices, 1 Jan 2021-present):
   - Bulk downloadable archives from Open Contracting Partnership Data Registry (Publication 41)
   - Official Live OCDS API (https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages)

Features:
- Multi-lot normalization at source: every supplier on every award becomes its own record.
- Strict no-fabrication rule for CPV codes and contract values.
- Authentic Companies House validation & PPON preservation.
- Value sanity guardrails: flags implausible values (>£10B) and suspicious repetitive placeholder values.
- Cross-source deduplication across Contracts Finder and Find a Tender.
- Ingestion into the app's PostgreSQL database (DB_* environment variables).
- Polite rate limiting, exponential backoff, and resumable checkpoints.

QA & Verification Reference (DO NOT SCRAPE):
- Find a Tender Human Search UI: https://www.find-tender.service.gov.uk/Search/Results
- Note: This URL is for human confirmation and spot-checking only. Do NOT scrape this page.
- Filter check: With "Award", "Termination", and "Contract" stages selected, benchmark total
  notices target is ~177,695 (with minor drift as new notices publish).
- Spot-checks: Sample 5-10 ingested supplier/authority/value records and cross-verify against
  the portal UI or direct /Notice/{id} URL to confirm published facts match ingested data.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Generator

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Add repository root to python path for imports
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from etenders_scraper.awards import (
    ingest_award_record,
    ingest_award_records_from_jsonl,
    check_value_sanity,
    MAX_PLAUSIBLE_AWARD_VALUE_GBP,
)

# Constants - Contracts Finder
CF_BULK_URL_TEMPLATE = "https://data.open-contracting.org/en/publication/128/download?name={year}.jsonl.gz"
CF_BULK_FULL_URL = "https://data.open-contracting.org/en/publication/128/download?name=full.jsonl.gz"
CF_LIVE_SEARCH_API_URL = "https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search"

# Constants - Find a Tender (Service launched 1 Jan 2021 post-Brexit)
FTS_BULK_URL_TEMPLATE = "https://data.open-contracting.org/en/publication/41/download?name={year}.jsonl.gz"
FTS_BULK_FULL_URL = "https://data.open-contracting.org/en/publication/41/download?name=full.jsonl.gz"
FTS_LIVE_API_URL = "https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages"
FTS_FIRST_YEAR = 2021

# OCDS tender.procurementMethod -> label shown in the app. Unknown methods stay None.
PROCUREMENT_METHOD_LABELS = {
    "open": "Open competition",
    "selective": "Restricted procedure",
    "limited": "Limited competition / negotiated",
    "direct": "Direct award",
}

DEFAULT_USER_AGENT = "SupplierIntelligenceSync/1.0 (Government Procurement Intelligence; contact: data@tenderflow.co.uk)"
STATE_FILE_PATH = REPO_ROOT / "data" / ".cf_sync_state.json"


def load_sync_state(path: Path = STATE_FILE_PATH, source: str = "contracts_finder") -> dict[str, Any]:
    """Loads checkpoint and last sync state for the specified source."""
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if source in data and isinstance(data[source], dict):
                    return data[source]
                # Backward compatibility: flat state file from earlier version
                if source == "contracts_finder" and "completed_years" in data:
                    return data
        except Exception as ex:
            print(f"[Warning] Failed to read sync state ({ex}), initializing fresh state.")
    return {
        "last_sync_timestamp": None,
        "completed_years": [],
        "in_progress": {},
        "last_cursor": None
    }


def save_sync_state(state: dict[str, Any], path: Path = STATE_FILE_PATH, source: str = "contracts_finder") -> None:
    """Saves checkpoint and last sync state atomically under the source namespace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    full_data = {}
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                full_data = json.load(f)
                # If old flat format, preserve it under contracts_finder
                if "completed_years" in full_data and "contracts_finder" not in full_data:
                    full_data = {"contracts_finder": full_data}
        except Exception:
            full_data = {}

    full_data[source] = state
    temp_path = path.with_suffix(".tmp")
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(full_data, f, indent=2)
    temp_path.replace(path)


def normalize_supplier_identifier(raw_id: str, party: dict[str, Any]) -> tuple[str, str, str]:
    """Extracts (company_number, ppon, identifier_scheme) from raw ID and party details."""
    cnum = ""
    ppon = ""
    scheme = ""

    identifier = party.get("identifier", {}) if isinstance(party, dict) else {}
    party_id = str(identifier.get("id") or "").strip()
    scheme = str(identifier.get("scheme") or "").strip()

    raw_id_str = str(raw_id or "").strip()

    # Check for Companies House scheme or prefix
    if scheme == "GB-COH" and party_id:
        cnum = party_id
    elif "GB-COH-" in raw_id_str:
        cnum = raw_id_str.replace("GB-COH-", "").strip()

    # Check for PPON (Public Procurement Organisation Number)
    if "PPON" in scheme or "GB-PPON" in scheme or "GB-PPON" in raw_id_str:
        ppon = party_id or raw_id_str.replace("GB-PPON-", "").strip()

    # Also check additionalIdentifiers
    if not ppon and isinstance(party, dict):
        for aid in party.get("additionalIdentifiers", []):
            if isinstance(aid, dict):
                a_scheme = str(aid.get("scheme") or "")
                if "PPON" in a_scheme:
                    ppon = str(aid.get("id") or "").strip()
                    break
                elif a_scheme == "GB-COH" and not cnum:
                    cnum = str(aid.get("id") or "").strip()

    # Fallback to CFS identifier if present
    if not cnum and "GB-CFS-" in raw_id_str:
        cnum = raw_id_str.replace("GB-CFS-", "CF").strip()

    return cnum, ppon, scheme or "UNKNOWN"


def extract_cpv_classifications(tender: dict[str, Any]) -> tuple[str, str, list[dict[str, str]]]:
    """Extracts all CPV classification codes and descriptions from tender items and tender classification.
    Adheres strictly to the NO-FABRICATION rule: leaves empty / 'Not available' if none present.
    """
    cpv_list: list[dict[str, str]] = []
    seen_codes: set[str] = set()

    # 1. Check top-level tender.classification
    main_c = tender.get("classification")
    if isinstance(main_c, dict):
        cid = str(main_c.get("id") or "").strip()
        cdesc = str(main_c.get("description") or "").strip()
        scheme = str(main_c.get("scheme") or "").upper()
        if cid and cid not in seen_codes and (scheme == "CPV" or cid.isdigit()):
            seen_codes.add(cid)
            cpv_list.append({"code": cid, "description": cdesc or "Not available", "scheme": scheme or "CPV"})

    # 2. Check all tender.items
    for item in tender.get("items", []):
        if not isinstance(item, dict):
            continue
        ic = item.get("classification")
        if isinstance(ic, dict):
            cid = str(ic.get("id") or "").strip()
            cdesc = str(ic.get("description") or "").strip()
            scheme = str(ic.get("scheme") or "").upper()
            if cid and cid not in seen_codes and (scheme == "CPV" or cid.isdigit()):
                seen_codes.add(cid)
                cpv_list.append({"code": cid, "description": cdesc or "Not available", "scheme": scheme or "CPV"})

        for ac in item.get("additionalClassifications", []):
            if isinstance(ac, dict):
                acid = str(ac.get("id") or "").strip()
                acdesc = str(ac.get("description") or "").strip()
                acscheme = str(ac.get("scheme") or "").upper()
                if acid and acid not in seen_codes and (acscheme == "CPV" or acid.isdigit()):
                    seen_codes.add(acid)
                    cpv_list.append({"code": acid, "description": acdesc or "Not available", "scheme": acscheme or "CPV"})

    if cpv_list:
        primary_code = cpv_list[0]["code"]
        primary_desc = cpv_list[0]["description"]
    else:
        primary_code = ""
        primary_desc = "Not available"

    return primary_code, primary_desc, cpv_list


def normalize_ocds_release(release: dict[str, Any], source_portal: str = "Contracts Finder") -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Normalizes an OCDS release applying the multi-lot fix at the source.

    Loops through EVERY award in release["awards"], and for EACH award, loops
    through EVERY supplier in award["suppliers"] — producing ONE normalized record
    per (award, supplier) pair.

    If an award has NO suppliers, creates a flagged record with flagged_no_supplier: True.

    Returns:
        tuple: (normalized_records, flagged_records)
    """
    normalized_records: list[dict[str, Any]] = []
    flagged_records: list[dict[str, Any]] = []

    ocid = release.get("ocid") or ""
    # OCDS 1.1 requires every release to carry BOTH "ocid" (stable across the contracting
    # process's lifetime) and "id" (this specific release) — Find a Tender's own website
    # routes notices by the latter (e.g. "009844-2024"), never by ocid. Confirmed directly:
    # querying find-tender.service.gov.uk's live OCDS API by ocid returns a release whose
    # "id" field is exactly the numeric ID that /Notice/{id} resolves; /Notice/{ocid} 404s
    # for the majority of historical releases (measured ~59% dead on a live sample).
    release_id = str(release.get("id") or "").strip()
    tender = release.get("tender", {}) if isinstance(release.get("tender"), dict) else {}
    tender_title = tender.get("title") or "Public Sector Contract Award"
    buyer = release.get("buyer", {}) if isinstance(release.get("buyer"), dict) else {}
    buyer_name = buyer.get("name") or "UK Public Sector Authority"

    # Index parties by id
    parties_by_id: dict[str, dict[str, Any]] = {}
    for p in release.get("parties", []):
        if isinstance(p, dict) and p.get("id"):
            parties_by_id[p["id"]] = p

    # Find buyer name from parties if missing
    if not buyer_name or buyer_name == "UK Public Sector Authority":
        for p in parties_by_id.values():
            if "buyer" in p.get("roles", []):
                buyer_name = p.get("name") or buyer_name
                break

    # Notice URL extraction from documents
    notice_url = ""
    for doc in tender.get("documents", []):
        if isinstance(doc, dict):
            u = doc.get("url", "")
            if "/Notice/" in u:
                notice_url = u
                break

    # CPV Classifications
    primary_cpv, primary_cpv_desc, all_cpvs = extract_cpv_classifications(tender)

    procurement_method = tender.get("procurementMethod") or ""
    procurement_method_details = tender.get("procurementMethodDetails") or ""
    procurement_type = PROCUREMENT_METHOD_LABELS.get(procurement_method)
    is_competitive = {"open": 1, "selective": 1, "limited": 0, "direct": 0}.get(procurement_method)

    contracts_by_award = {c.get("awardID"): c for c in release.get("contracts", [])
                          if isinstance(c, dict) and c.get("awardID")}

    awards = release.get("awards", [])
    if not isinstance(awards, list) or len(awards) == 0:
        return normalized_records, flagged_records

    for award_idx, award in enumerate(awards, 1):
        if not isinstance(award, dict):
            continue

        award_id = award.get("id") or f"{ocid}-award-{award_idx}"
        award_title = award.get("title") or tender_title

        # Find a Tender publishes the signed date, value and period on the contract
        # linked to the award (contracts[].awardID), not on the award itself.
        contract = contracts_by_award.get(award.get("id")) or {}

        # Contract value: the award's own value, else its contract's. Never the tender
        # estimate — that is the buyer's budget, not what this supplier was awarded.
        val_obj = award.get("value") if isinstance(award.get("value"), dict) else {}
        if val_obj.get("amount") is None and isinstance(contract.get("value"), dict):
            val_obj = contract["value"]
        contract_value = float(val_obj["amount"]) if val_obj.get("amount") is not None else None
        currency = val_obj.get("currency") or "GBP"

        # Date signed: left empty when the source doesn't state it
        date_raw = contract.get("dateSigned") or award.get("date") or ""
        date_signed = date_raw[:10] or None

        # Contract period / duration
        contract_period = award.get("contractPeriod") if isinstance(award.get("contractPeriod"), dict) else {}
        if not contract_period and isinstance(contract.get("period"), dict):
            contract_period = contract["period"]
        start_p = (contract_period.get("startDate") or "")[:10]
        end_p = (contract_period.get("endDate") or "")[:10]
        if start_p and end_p:
            contract_duration = f"{start_p} to {end_p}"
        elif contract_period.get("durationInDays"):
            contract_duration = f"{contract_period['durationInDays']} days"
        else:
            contract_duration = None

        # Award specific document notice URL
        award_notice_url = notice_url
        if not award_notice_url:
            for doc in award.get("documents", []):
                if isinstance(doc, dict):
                    u = doc.get("url", "")
                    if "/Notice/" in u:
                        award_notice_url = u
                        break

        # Generate portal-specific notice URL
        if source_portal == "Find a Tender":
            fallback_base = "https://www.find-tender.service.gov.uk"
            # Prefer the release's own "id" (the numeric ID the website actually routes
            # on) over "ocid" — see the note where release_id is extracted above.
            full_notice_url = award_notice_url or (
                f"{fallback_base}/Notice/{release_id}" if release_id
                else (f"{fallback_base}/Notice/{ocid}" if ocid else f"{fallback_base}/Search")
            )
        else:
            fallback_base = "https://www.contractsfinder.service.gov.uk"
            full_notice_url = award_notice_url or (f"{fallback_base}/Notice/{ocid}" if ocid else f"{fallback_base}/Search/Results")

        suppliers = award.get("suppliers", [])
        if not isinstance(suppliers, list) or len(suppliers) == 0:
            # FLAG explicitly: award with NO suppliers
            flagged_rec = {
                "ocid": ocid,
                "award_id": award_id,
                "tender_title": tender_title,
                "authority_name": buyer_name,
                "contract_value": contract_value,
                "currency": currency,
                "date_signed": date_signed,
                "cpv_code": primary_cpv,
                "cpv_description": primary_cpv_desc,
                "procurement_method": procurement_method,
                "procurement_method_details": procurement_method_details,
                "notice_url": full_notice_url,
                "source_portal": source_portal,
                "flagged_no_supplier": True,
                "flag_reason": "Award has no suppliers listed in release"
            }
            flagged_records.append(flagged_rec)
            continue

        # CRITICAL MULTI-LOT FIX: Process EVERY supplier on this award
        for sup in suppliers:
            if not isinstance(sup, dict):
                continue
            sup_name = (sup.get("name") or "").strip()
            if not sup_name or len(sup_name) < 2:
                continue

            # Skip known junk placeholder values
            sup_name_lower = sup_name.lower()
            if any(junk in sup_name_lower for junk in [
                "maximum number", "associated tender", "live scraped", "see attached",
                "see tender", "suppliers", "unknown", "n/a", "none", "tbc", "not applicable",
                "contract value", "contracting authorities", "not awarded", "no award", "award not made",
                "refer to weblink", "see website", "redacted", "withheld for security reasons"
            ]):
                continue

            sup_id = str(sup.get("id") or "").strip()
            party = parties_by_id.get(sup_id, {})

            cnum, ppon, scheme = normalize_supplier_identifier(sup_id, party)

            # Address
            addr_obj = party.get("address", {}) if isinstance(party.get("address"), dict) else {}
            address_parts = []
            for k in ["streetAddress", "locality", "postalCode", "countryName"]:
                v = addr_obj.get(k)
                if v:
                    address_parts.append(str(v).replace("\r", " ").replace("\n", " ").strip())
            address = ", ".join(address_parts)

            # Contact point
            contact_obj = party.get("contactPoint", {}) if isinstance(party.get("contactPoint"), dict) else {}
            email = contact_obj.get("email") or ""
            phone = contact_obj.get("telephone") or ""
            website = contact_obj.get("url") or ""

            # Region inference
            region = "UK"
            addr_lower = address.lower()
            if "london" in addr_lower:
                region = "UKI - London"
            elif any(s in addr_lower for s in ["scotland", "edinburgh", "glasgow", "aberdeen"]):
                region = "UKM - Scotland"
            elif any(w in addr_lower for w in ["wales", "cardiff", "swansea", "newport"]):
                region = "UKL - Wales"
            elif "belfast" in addr_lower or "northern ireland" in addr_lower:
                region = "UKN - Northern Ireland"

            # Supplier size/VCSE as declared in the notice; unknown stays None
            details = party.get("details") if isinstance(party.get("details"), dict) else {}
            sme_status = {"sme": "SME", "large": "Non-SME"}.get(str(details.get("scale") or "").lower())
            vcse_status = {True: "VCSE", False: "Non-VCSE"}.get(details.get("vcse"))

            techniques = tender.get("techniques", {}) if isinstance(tender.get("techniques"), dict) else {}
            has_fw = techniques.get("hasFrameworkAgreement")
            is_fw_flag = 1 if (has_fw is True or "framework" in procurement_method_details.lower() or "call-off" in procurement_method_details.lower() or "framework" in procurement_method.lower()) else 0

            from etenders_scraper.awards import classify_buyer_type
            b_type = classify_buyer_type(buyer_name)

            record = {
                "ocid": ocid,
                "award_id": award_id,
                "tender_title": tender_title,
                "award_title": award_title,
                "supplier_name": sup_name,
                "supplier_identifier": sup_id,
                "company_number": cnum,
                "ppon": ppon or None,
                "authority_name": buyer_name,
                "address": address,
                "email": email,
                "phone": phone,
                "website": website,
                "cpv_code": primary_cpv,
                "cpv_description": primary_cpv_desc,
                "all_cpv_codes": all_cpvs,
                "contract_value": contract_value,
                "currency": currency,
                "date_signed": date_signed,
                "contract_start_date": start_p,
                "contract_end_date": end_p,
                "contract_duration": contract_duration,
                "procurement_type": procurement_type,
                "procurement_method": procurement_method,
                "procurement_method_details": procurement_method_details,
                "is_competitive": is_competitive,
                "is_framework": is_fw_flag,
                "buyer_type": b_type,
                "notice_type": "Awarded contract",
                "source_portal": source_portal,
                "notice_url": full_notice_url,
                "region": region,
                "sme_status": sme_status,
                "vcse_status": vcse_status,
                "flagged_no_supplier": False
            }
            normalized_records.append(record)

    return normalized_records, flagged_records


class ContractsFinderSync:
    """Manages backfill and sync execution for Contracts Finder and Find a Tender OCDS data."""

    def __init__(self,
                 source: str = "contracts_finder",
                 mode: str = "sync",
                 year: int | None = None,
                 all_years: bool = False,
                 since_date: str | None = None,
                 until_date: str | None = None,
                 limit_releases: int | None = None,
                 rate_limit: float = 1.0,
                 output_file: Path | None = None,
                 flagged_file: Path | None = None,
                 value_anomaly_file: Path | None = None,
                 max_value_threshold: float = MAX_PLAUSIBLE_AWARD_VALUE_GBP,
                 auto_ingest: bool = False,
                 db_conn: Any = None,
                 db_conns: list[Any] | None = None,
                 resume: bool = True,
                 overwrite: bool = False):
        self.source = source.lower()  # "contracts_finder", "find_a_tender", or "both"
        self.mode = mode
        self.year = year or (2026 if self.source != "find_a_tender" else 2026)
        self.all_years = all_years
        self.since_date = since_date
        self.until_date = until_date
        self.limit_releases = limit_releases
        self.rate_limit = rate_limit
        self.auto_ingest = auto_ingest
        self.db_conns = db_conns if db_conns is not None else ([db_conn] if db_conn else [])
        self.db_conn = self.db_conns[0] if self.db_conns else None
        self.resume = resume
        self.overwrite = overwrite
        self.max_value_threshold = max_value_threshold

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        data_dir = REPO_ROOT / "data"
        data_dir.mkdir(parents=True, exist_ok=True)

        prefix = "cf_fts" if self.source == "both" else ("fts" if self.source == "find_a_tender" else "contracts_finder")
        self.output_file = output_file or (data_dir / f"{prefix}_{mode}_{timestamp}.jsonl")
        self.flagged_file = flagged_file or (data_dir / f"{prefix}_flagged_no_supplier_{mode}_{timestamp}.jsonl")
        self.value_anomaly_file = value_anomaly_file or (data_dir / f"{prefix}_flagged_value_anomaly_{mode}_{timestamp}.jsonl")

        # Runtime metrics
        self.releases_processed = 0
        self.records_normalized = 0
        self.records_flagged_no_supplier = 0
        self.records_flagged_value_anomaly = 0
        self.records_ingested_db = 0
        self.records_duplicate_db = 0
        self.ingest_errors = 0
        self.seen_value_counts: dict[float, int] = {}
        self.errors_encountered: list[dict[str, Any]] = []

    def _open_stream_with_retry(self, url: str, max_retries: int = 5) -> Any:
        """Opens URL stream with polite exponential backoff retry and 403 cooldown."""
        backoff = 2.0
        req = urllib.request.Request(url, headers={
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "application/json, application/gzip, */*"
        })

        for attempt in range(1, max_retries + 1):
            try:
                time.sleep(self.rate_limit)
                return urllib.request.urlopen(req, timeout=60)
            except urllib.error.HTTPError as http_err:
                if http_err.code == 403:
                    print(f"[Rate Limit] ⚠️ HTTP 403 received from upstream API. Rate limit triggered.")
                    print("[Rate Limit] Pausing 5 minutes as required by official documentation before retry...")
                    time.sleep(305)
                    continue
                elif http_err.code in (429, 500, 502, 503, 504):
                    print(f"[Retry {attempt}/{max_retries}] HTTP {http_err.code} on {url}: Retrying in {backoff:.1f}s...")
                    time.sleep(backoff)
                    time.sleep(backoff)
                    backoff *= 2.0
                else:
                    raise http_err
            except (urllib.error.URLError, TimeoutError, ConnectionResetError) as net_err:
                print(f"[Retry {attempt}/{max_retries}] Network error ({net_err}) on {url}: Retrying in {backoff:.1f}s...")
                time.sleep(backoff)
                backoff *= 2.0

        raise RuntimeError(f"Failed to fetch {url} after {max_retries} attempts.")

    def _download_archive_to_disk(self, url: str, cache_name: str, max_retries: int = 5) -> Path:
        """Downloads a bulk archive fully to a local file before processing.

        Streaming decompression directly off the live HTTP response was unreliable for
        these large archives: slow per-record DB ingestion between reads let the
        upstream CDN's idle-connection timeout fire mid-download, truncating the gzip
        stream. Downloading first (a tight read loop with no processing in between)
        avoids that, and lets us verify the byte count before trusting the file.
        """
        cache_dir = REPO_ROOT / "data" / ".backfill_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        dest = cache_dir / cache_name

        for attempt in range(1, max_retries + 1):
            try:
                resp = self._open_stream_with_retry(url, max_retries=max_retries)
                expected_size = resp.headers.get("Content-Length")
                expected_size = int(expected_size) if expected_size else None

                tmp_dest = dest.with_suffix(dest.suffix + ".part")
                total = 0
                with open(tmp_dest, "wb") as f:
                    while True:
                        chunk = resp.read(1024 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                        total += len(chunk)

                if expected_size is not None and total != expected_size:
                    raise IOError(f"Incomplete download: got {total} bytes, expected {expected_size}")

                tmp_dest.replace(dest)
                return dest
            except Exception as ex:
                print(f"[Download {attempt}/{max_retries}] Failed downloading {url}: {ex}")
                if attempt == max_retries:
                    raise
                time.sleep(3 * attempt)

        raise RuntimeError(f"Failed to download {url} after {max_retries} attempts.")

    def _process_release(self, release: dict[str, Any], source_portal: str, out_f: Any, flag_f: Any, anom_f: Any) -> None:
        """Normalizes release, applies value sanity guardrail, and ingests into DB."""
        norm_recs, flag_recs = normalize_ocds_release(release, source_portal=source_portal)

        for rec in norm_recs:
            # Value sanity check
            is_valid, sanity_reason = check_value_sanity(rec, self.max_value_threshold)
            if not is_valid:
                anom_rec = dict(rec)
                anom_rec["flagged_value_anomaly"] = True
                anom_rec["flag_reason"] = sanity_reason
                anom_f.write(json.dumps(anom_rec) + "\n")
                self.records_flagged_value_anomaly += 1
                # The award itself is real; only the value is untrustworthy.
                rec = dict(rec, contract_value=None)

            out_f.write(json.dumps(rec) + "\n")
            self.records_normalized += 1

            if self.auto_ingest and self.db_conns:
                inserted_any = False
                for c in self.db_conns:
                    try:
                        if ingest_award_record(rec, c, overwrite=self.overwrite):
                            inserted_any = True
                    except Exception as conn_ex:
                        if hasattr(c, "rollback"):
                            c.rollback()
                        self.ingest_errors += 1
                        if self.ingest_errors <= 5 or self.ingest_errors % 1000 == 0:
                            print(f"[Ingest Error #{self.ingest_errors}] {type(conn_ex).__name__}: {conn_ex}", flush=True)
                if inserted_any:
                    self.records_ingested_db += 1
                else:
                    self.records_duplicate_db += 1

        for frec in flag_recs:
            flag_f.write(json.dumps(frec) + "\n")
            self.records_flagged_no_supplier += 1

    # ── CONTRACTS FINDER BACKFILL & SYNC ─────────────────────────────────────────

    def run_cf_backfill_year(self, year: int, out_f: Any, flag_f: Any, anom_f: Any, state: dict[str, Any]) -> None:
        """Stream-processes a single year's bulk OCDS archive from Contracts Finder."""
        url = CF_BULK_URL_TEMPLATE.format(year=year)
        print(f"\n[Contracts Finder Backfill] 🚀 Streaming bulk archive for year {year} from:\n          {url}")

        start_line = 0
        if self.resume and str(year) in state.get("in_progress", {}):
            start_line = state["in_progress"][str(year)].get("lines_processed", 0)
            print(f"[Contracts Finder Backfill] Resuming year {year} from line {start_line}...")

        try:
            archive_path = self._download_archive_to_disk(url, f"cf_{year}.jsonl.gz")
            with gzip.open(archive_path, "rb") as gz:
                current_line = 0
                for raw_line in gz:
                    current_line += 1
                    if current_line <= start_line:
                        continue

                    if self.limit_releases and self.releases_processed >= self.limit_releases:
                        print(f"[Backfill] Reached release limit cap ({self.limit_releases}). Stopping.")
                        break

                    line_str = raw_line.decode("utf-8", errors="replace").strip()
                    if not line_str:
                        continue

                    try:
                        release = json.loads(line_str)
                    except json.JSONDecodeError as decode_err:
                        self.errors_encountered.append({"year": year, "line": current_line, "error": f"JSON decode error: {decode_err}"})
                        continue

                    self.releases_processed += 1
                    try:
                        self._process_release(release, "Contracts Finder", out_f, flag_f, anom_f)
                    except Exception as parse_err:
                        self.errors_encountered.append({"year": year, "line": current_line, "ocid": release.get("ocid"), "error": str(parse_err)})

                    if self.releases_processed % 100 == 0:
                        out_f.flush()
                        flag_f.flush()
                        anom_f.flush()
                        if self.auto_ingest and self.db_conns:
                            for c in self.db_conns:
                                if hasattr(c, "commit"):
                                    c.commit()
                        state["in_progress"][str(year)] = {"lines_processed": current_line}
                        save_sync_state(state, source="contracts_finder")
                        print(f"[CF Progress] Processed {self.releases_processed:,} releases, {self.records_normalized:,} normalized ({self.records_flagged_value_anomaly:,} value anomalies quarantined, {self.records_flagged_no_supplier:,} no supplier)...")

            archive_path.unlink(missing_ok=True)

            if not (self.limit_releases and self.releases_processed >= self.limit_releases):
                if year not in state.get("completed_years", []):
                    state["completed_years"].append(year)
                if str(year) in state.get("in_progress", {}):
                    del state["in_progress"][str(year)]
                save_sync_state(state, source="contracts_finder")
                print(f"[Contracts Finder Backfill] ✅ Completed year {year}.")

        except Exception as ex:
            print(f"[Contracts Finder Backfill Error] Failed streaming year {year}: {ex}")
            self.errors_encountered.append({"year": year, "error": str(ex)})

    def run_cf_sync(self, out_f: Any, flag_f: Any, anom_f: Any) -> None:
        """Incremental live sync from Contracts Finder Search API."""
        state = load_sync_state(source="contracts_finder")
        since = self.since_date or state.get("last_sync_timestamp")
        if not since:
            since = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00")
            print(f"[CF Sync] No previous sync timestamp. Defaulting to last 7 days: {since}")
        else:
            print(f"[CF Sync] Resuming from last sync timestamp: {since}")

        params = {"publishedFrom": since, "stages": "award", "limit": 100}
        if self.until_date:
            params["publishedTo"] = self.until_date

        next_url: str | None = f"{CF_LIVE_SEARCH_API_URL}?{urllib.parse.urlencode(params)}"
        print(f"[CF Sync] Connecting to live Contracts Finder Search API...")

        while next_url:
            if self.limit_releases and self.releases_processed >= self.limit_releases:
                print(f"[CF Sync] Reached release limit cap ({self.limit_releases}). Stopping.")
                break

            try:
                resp = self._open_stream_with_retry(next_url)
                data = json.loads(resp.read().decode("utf-8"))
            except Exception as ex:
                print(f"[CF Sync Error] Failed fetching page {next_url}: {ex}")
                self.errors_encountered.append({"url": next_url, "error": str(ex)})
                break

            releases = data.get("releases", [])
            if not releases:
                print("[CF Sync] No more releases returned on this page. Finished.")
                break

            for release in releases:
                if self.limit_releases and self.releases_processed >= self.limit_releases:
                    break
                self.releases_processed += 1
                try:
                    self._process_release(release, "Contracts Finder", out_f, flag_f, anom_f)
                except Exception as parse_err:
                    self.errors_encountered.append({"ocid": release.get("ocid"), "error": str(parse_err)})

            out_f.flush()
            flag_f.flush()
            anom_f.flush()
            if self.auto_ingest and self.db_conns:
                for c in self.db_conns:
                    if hasattr(c, "commit"):
                        c.commit()

            print(f"[CF Progress] Processed {self.releases_processed:,} releases, {self.records_normalized:,} normalized ({self.records_flagged_value_anomaly:,} value anomalies quarantined, {self.records_flagged_no_supplier:,} no supplier)...")

            links = data.get("links", {})
            next_url = links.get("next") if isinstance(links, dict) else None

        state["last_sync_timestamp"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        save_sync_state(state, source="contracts_finder")

    # ── FIND A TENDER BACKFILL & SYNC ────────────────────────────────────────────

    def run_fts_backfill_year(self, year: int, out_f: Any, flag_f: Any, anom_f: Any, state: dict[str, Any]) -> None:
        """Stream-processes a single year's bulk OCDS archive from Find a Tender (2021-2026)."""
        if year < FTS_FIRST_YEAR:
            print(f"[Find a Tender Backfill] ⚠️ Year {year} is prior to launch date (1 Jan 2021). Skipping.")
            return

        url = FTS_BULK_URL_TEMPLATE.format(year=year)
        print(f"\n[Find a Tender Backfill] 🚀 Streaming bulk archive for year {year} from:\n          {url}")

        start_line = 0
        if self.resume and str(year) in state.get("in_progress", {}):
            start_line = state["in_progress"][str(year)].get("lines_processed", 0)
            print(f"[Find a Tender Backfill] Resuming year {year} from line {start_line}...")

        try:
            archive_path = self._download_archive_to_disk(url, f"fts_{year}.jsonl.gz")
            with gzip.open(archive_path, "rb") as gz:
                current_line = 0
                for raw_line in gz:
                    current_line += 1
                    if current_line <= start_line:
                        continue

                    if self.limit_releases and self.releases_processed >= self.limit_releases:
                        print(f"[Backfill] Reached release limit cap ({self.limit_releases}). Stopping.")
                        break

                    line_str = raw_line.decode("utf-8", errors="replace").strip()
                    if not line_str:
                        continue

                    try:
                        release = json.loads(line_str)
                    except json.JSONDecodeError as decode_err:
                        self.errors_encountered.append({"year": year, "line": current_line, "error": f"JSON decode error: {decode_err}"})
                        continue

                    self.releases_processed += 1
                    try:
                        self._process_release(release, "Find a Tender", out_f, flag_f, anom_f)
                    except Exception as parse_err:
                        self.errors_encountered.append({"year": year, "line": current_line, "ocid": release.get("ocid"), "error": str(parse_err)})

                    if self.releases_processed % 100 == 0:
                        out_f.flush()
                        flag_f.flush()
                        anom_f.flush()
                        if self.auto_ingest and self.db_conns:
                            for c in self.db_conns:
                                if hasattr(c, "commit"):
                                    c.commit()
                        state["in_progress"][str(year)] = {"lines_processed": current_line}
                        save_sync_state(state, source="find_a_tender")
                        print(f"[FTS Progress] Processed {self.releases_processed:,} releases, {self.records_normalized:,} normalized ({self.records_flagged_value_anomaly:,} value anomalies quarantined, {self.records_flagged_no_supplier:,} no supplier)...")

            archive_path.unlink(missing_ok=True)

            if not (self.limit_releases and self.releases_processed >= self.limit_releases):
                if year not in state.get("completed_years", []):
                    state["completed_years"].append(year)
                if str(year) in state.get("in_progress", {}):
                    del state["in_progress"][str(year)]
                save_sync_state(state, source="find_a_tender")
                print(f"[Find a Tender Backfill] ✅ Completed year {year}.")

        except Exception as ex:
            print(f"[Find a Tender Backfill Error] Failed streaming year {year}: {ex}")
            self.errors_encountered.append({"year": year, "error": str(ex)})

    def run_fts_sync(self, out_f: Any, flag_f: Any, anom_f: Any) -> None:
        """Incremental live sync from official Find a Tender API via cursor-based pagination."""
        state = load_sync_state(source="find_a_tender")
        since = self.since_date or state.get("last_sync_timestamp")
        if not since:
            since = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00")
            print(f"[FTS Sync] No previous sync timestamp. Defaulting to last 7 days: {since}")
        else:
            print(f"[FTS Sync] Resuming from last sync timestamp: {since}")

        # Ensure ISO datetime format for FTS
        if "T" not in since:
            since = f"{since}T00:00:00"

        params = {"limit": 100, "updatedFrom": since}
        if self.until_date:
            until_val = self.until_date if "T" in self.until_date else f"{self.until_date}T23:59:59"
            params["updatedTo"] = until_val

        next_url: str | None = f"{FTS_LIVE_API_URL}?{urllib.parse.urlencode(params)}"
        print(f"[FTS Sync] Connecting to Find a Tender Live API at:\n          {next_url}")

        page_num = 0
        while next_url:
            page_num += 1
            if self.limit_releases and self.releases_processed >= self.limit_releases:
                print(f"[FTS Sync] Reached release limit cap ({self.limit_releases}). Stopping.")
                break

            try:
                resp = self._open_stream_with_retry(next_url)
                data = json.loads(resp.read().decode("utf-8"))
            except Exception as ex:
                print(f"[FTS Sync Error] Failed fetching page {next_url}: {ex}")
                self.errors_encountered.append({"url": next_url, "error": str(ex)})
                break

            releases = data.get("releases", [])
            if not releases:
                print(f"[FTS Sync] Page {page_num} returned 0 releases. Sync finished.")
                break

            for release in releases:
                if self.limit_releases and self.releases_processed >= self.limit_releases:
                    break
                self.releases_processed += 1
                try:
                    self._process_release(release, "Find a Tender", out_f, flag_f, anom_f)
                except Exception as parse_err:
                    self.errors_encountered.append({"ocid": release.get("ocid"), "error": str(parse_err)})

            out_f.flush()
            flag_f.flush()
            anom_f.flush()
            if self.auto_ingest and self.db_conns:
                for c in self.db_conns:
                    if hasattr(c, "commit"):
                        c.commit()

            print(f"[FTS Progress] Page {page_num}: Processed {self.releases_processed:,} releases, {self.records_normalized:,} normalized ({self.records_flagged_value_anomaly:,} value anomalies quarantined, {self.records_flagged_no_supplier:,} no supplier)...")

            links = data.get("links", {})
            next_url = links.get("next") if isinstance(links, dict) else None

        state["last_sync_timestamp"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        save_sync_state(state, source="find_a_tender")

    # ── ORCHESTRATION ────────────────────────────────────────────────────────────

    def _ensure_db_connections(self, max_wait_s: int = 900) -> None:
        """Replace closed DB connections, waiting for Postgres to come back if it is down.

        A dropped connection (DB restart, host sleep) otherwise fails every year retry instantly.
        """
        if not (self.auto_ingest and self.db_conns):
            return
        from tender_app.db import get_db_connection
        for i, c in enumerate(self.db_conns):
            if not getattr(c, "closed", 0):
                continue
            waited = 0
            while True:
                try:
                    self.db_conns[i] = get_db_connection()
                    print(f"[Database] Reconnected to PostgreSQL after connection loss (waited {waited}s).", flush=True)
                    break
                except Exception as ex:
                    if waited >= max_wait_s:
                        raise
                    print(f"[Database] Reconnect failed ({ex}); retrying in 30s...", flush=True)
                    time.sleep(30)
                    waited += 30
        self.db_conn = self.db_conns[0]

    def run_backfill(self) -> None:
        """Executes historical bulk backfill for the configured source(s)."""
        print(f"=" * 75)
        print(f"OCDS BULK BACKFILL MODE - SOURCE: {self.source.upper()}")
        print(f"Output File:        {self.output_file}")
        print(f"Flagged (No Sup):   {self.flagged_file}")
        print(f"Value Anomalies:    {self.value_anomaly_file}")
        print(f"Max Value Ceiling:  £{self.max_value_threshold:,.0f}")
        print(f"Ingest into DB:     {self.auto_ingest}")
        print(f"=" * 75)

        with open(self.output_file, "a", encoding="utf-8") as out_f, \
             open(self.flagged_file, "a", encoding="utf-8") as flag_f, \
             open(self.value_anomaly_file, "a", encoding="utf-8") as anom_f:

            # 1. Contracts Finder Backfill (2016-2026)
            if self.source in ("contracts_finder", "both"):
                cf_state = load_sync_state(source="contracts_finder") if self.resume else {"completed_years": [], "in_progress": {}}
                cf_years = list(range(2016, 2027)) if self.all_years else [self.year]
                for y in cf_years:
                    if self.resume and y in cf_state.get("completed_years", []) and not self.limit_releases:
                        print(f"[Contracts Finder Backfill] Skipping year {y} (already completed).")
                        continue
                    for attempt in range(3):
                        self._ensure_db_connections()
                        self.run_cf_backfill_year(y, out_f, flag_f, anom_f, cf_state)
                        if y in cf_state.get("completed_years", []) or (self.limit_releases and self.releases_processed >= self.limit_releases):
                            break
                        print(f"[Contracts Finder Backfill] Year {y} did not complete cleanly (attempt {attempt + 1}/3), retrying after backoff...")
                        time.sleep(5 * (attempt + 1))
                    if self.limit_releases and self.releases_processed >= self.limit_releases:
                        break

            # 2. Find a Tender Backfill (2021-2026 only!)
            if self.source in ("find_a_tender", "both") and not (self.limit_releases and self.releases_processed >= self.limit_releases):
                fts_state = load_sync_state(source="find_a_tender") if self.resume else {"completed_years": [], "in_progress": {}}
                fts_years = list(range(FTS_FIRST_YEAR, 2027)) if self.all_years else [max(self.year, FTS_FIRST_YEAR)]
                for y in fts_years:
                    if self.resume and y in fts_state.get("completed_years", []) and not self.limit_releases:
                        print(f"[Find a Tender Backfill] Skipping year {y} (already completed).")
                        continue
                    for attempt in range(3):
                        self._ensure_db_connections()
                        self.run_fts_backfill_year(y, out_f, flag_f, anom_f, fts_state)
                        if y in fts_state.get("completed_years", []) or (self.limit_releases and self.releases_processed >= self.limit_releases):
                            break
                        print(f"[Find a Tender Backfill] Year {y} did not complete cleanly (attempt {attempt + 1}/3), retrying after backoff...")
                        time.sleep(5 * (attempt + 1))
                    if self.limit_releases and self.releases_processed >= self.limit_releases:
                        break

        if self.auto_ingest and self.db_conns:
            self._ensure_db_connections()
            for c in self.db_conns:
                if hasattr(c, "commit"):
                    c.commit()

    def run_sync(self) -> None:
        """Executes incremental live API sync for the configured source(s)."""
        print(f"=" * 75)
        print(f"OCDS INCREMENTAL SYNC MODE - SOURCE: {self.source.upper()}")
        print(f"Output File:        {self.output_file}")
        print(f"Flagged (No Sup):   {self.flagged_file}")
        print(f"Value Anomalies:    {self.value_anomaly_file}")
        print(f"Max Value Ceiling:  £{self.max_value_threshold:,.0f}")
        print(f"Ingest into DB:     {self.auto_ingest}")
        print(f"=" * 75)

        with open(self.output_file, "a", encoding="utf-8") as out_f, \
             open(self.flagged_file, "a", encoding="utf-8") as flag_f, \
             open(self.value_anomaly_file, "a", encoding="utf-8") as anom_f:

            # 1. Contracts Finder Sync
            if self.source in ("contracts_finder", "both"):
                self.run_cf_sync(out_f, flag_f, anom_f)

            # 2. Find a Tender Sync
            if self.source in ("find_a_tender", "both") and not (self.limit_releases and self.releases_processed >= self.limit_releases):
                self.run_fts_sync(out_f, flag_f, anom_f)

        if self.auto_ingest and self.db_conns:
            for c in self.db_conns:
                if hasattr(c, "commit"):
                    c.commit()

    def print_summary(self) -> None:
        """Prints a comprehensive end-of-run summary."""
        print("\n" + "=" * 75)
        print("RUN SUMMARY & METRICS")
        print("=" * 75)
        print(f"Source:                                 {self.source.upper()}")
        print(f"Mode:                                   {self.mode.upper()}")
        print(f"Total OCDS Releases Processed:          {self.releases_processed:,}")
        print(f"Total Normalized Award Records:         {self.records_normalized:,}")
        print(f"Awards Flagged (No Supplier):           {self.records_flagged_no_supplier:,}")
        print(f"Awards Flagged (Value Anomaly Quarantined): {self.records_flagged_value_anomaly:,}")
        if self.auto_ingest:
            print(f"Database Records Ingested (New):        {self.records_ingested_db:,}")
            print(f"Database Duplicate Records Skipped:     {self.records_duplicate_db:,}")
        print(f"Parse / Network Errors Encountered:     {len(self.errors_encountered)}")
        print(f"Normalized Output File:                 {self.output_file}")
        print(f"Flagged (No Supplier) File:             {self.flagged_file}")
        print(f"Flagged (Value Anomaly) File:           {self.value_anomaly_file}")

        if self.errors_encountered:
            print("\n⚠️ ERROR DETAILS (Sample up to 10):")
            for err in self.errors_encountered[:10]:
                print(f" - {err}")
            if len(self.errors_encountered) > 10:
                print(f" ... and {len(self.errors_encountered) - 10} more errors.")
        print("=" * 75 + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Contracts Finder & Find a Tender OCDS Ingestion Script")
    parser.add_argument("--source", choices=["contracts_finder", "find_a_tender", "both"], default="contracts_finder",
                        help="Data source: 'contracts_finder', 'find_a_tender', or 'both' (default: contracts_finder)")
    parser.add_argument("--mode", choices=["backfill", "sync"], default="sync",
                        help="Operation mode: 'backfill' for bulk download or 'sync' for live API (default: sync)")
    parser.add_argument("--year", type=int, default=2026,
                        help="Single year in backfill mode (e.g. 2026). Default: 2026 (FTS only supports >= 2021)")
    parser.add_argument("--all-years", action="store_true",
                        help="Download all years (2016-2026 for CF, 2021-2026 for FTS)")
    parser.add_argument("--since", type=str, default=None,
                        help="Date filter for sync mode (ISO format YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS)")
    parser.add_argument("--until", type=str, default=None,
                        help="Optional upper date filter for sync mode")
    parser.add_argument("--limit-releases", type=int, default=None,
                        help="Cap on maximum number of releases to process (useful for test runs)")
    parser.add_argument("--rate-limit", type=float, default=1.0,
                        help="Seconds delay between live API / stream requests (default: 1.0)")
    parser.add_argument("--max-value-threshold", type=float, default=MAX_PLAUSIBLE_AWARD_VALUE_GBP,
                        help="Maximum plausible contract award value ceiling in GBP (default: 10,000,000,000)")
    parser.add_argument("--output", type=str, default=None,
                        help="Custom output file path for normalized JSONL")
    parser.add_argument("--flagged-output", type=str, default=None,
                        help="Custom output file path for awards flagged with no supplier")
    parser.add_argument("--value-anomaly-output", type=str, default=None,
                        help="Custom output file path for awards flagged with value anomalies")
    parser.add_argument("--ingest", action="store_true",
                        help="Directly ingest normalized records into database via awards.py pipeline")
    parser.add_argument("--no-resume", action="store_true",
                        help="Disable resumption from checkpoint state")
    parser.add_argument("--overwrite", action="store_true",
                        help="Correct awards already in the DB with the source's values instead of skipping them")

    args = parser.parse_args()

    conns = []
    if args.ingest:
        from tender_app.db import get_db_connection
        conns.append(get_db_connection())
        print(f"[Database] Connected to PostgreSQL at {os.environ.get('DB_HOST', 'localhost')}:{os.environ.get('DB_PORT', '5432')} for ingestion.")

    sync_tool = ContractsFinderSync(
        source=args.source,
        mode=args.mode,
        year=args.year,
        all_years=args.all_years,
        since_date=args.since,
        until_date=args.until,
        limit_releases=args.limit_releases,
        rate_limit=args.rate_limit,
        output_file=Path(args.output) if args.output else None,
        flagged_file=Path(args.flagged_output) if args.flagged_output else None,
        value_anomaly_file=Path(args.value_anomaly_output) if args.value_anomaly_output else None,
        max_value_threshold=args.max_value_threshold,
        auto_ingest=args.ingest,
        db_conns=conns,
        resume=not args.no_resume,
        overwrite=args.overwrite
    )

    try:
        if args.mode == "backfill":
            sync_tool.run_backfill()
        else:
            sync_tool.run_sync()
    finally:
        for c in conns:
            if hasattr(c, "close"):
                c.close()

    sync_tool.print_summary()
    return 0


if __name__ == "__main__":
    sys.exit(main())
