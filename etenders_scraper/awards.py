"""Contract Award Notices scraper helper for TenderFlow.

Supports capturing structured UK7/UK4 contract details and award notices
from Find a Tender, Sell2Wales, Public Contracts Scotland, eTenders NI, and eTenders IE,
under Open Government Licence v3.0. All data here is live-scraped from the real portals —
never fabricated or seeded.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from typing import Any

# Open Data Licensing attribution constant (Contracts Finder OGL v3.0)
OGL_V3_ATTRIBUTION = "Contains public sector information published by Contracts Finder, licensed under the Open Government Licence v3.0."

# Canonical `contract_awards.source_portal` values for the 6 UK/Ireland portals.
# Single source of truth — every ingestion function and every query that filters
# Supplier/Buyer Intelligence to "real UK/IE portal" data must use this tuple
# rather than a separately hardcoded literal (that's how CF/FTS came to be the
# only 2 portals actually reachable from Supplier Intelligence).
UK_IE_SOURCE_PORTALS: tuple[str, ...] = (
    "Contracts Finder",
    "Find a Tender",
    "Sell2Wales",
    "Public Contracts Scotland",
    "eTenders Ireland",
    "eTenders NI",
)

# A single published award notice legitimately worth more than this basically never happens in
# UK/IE public procurement (the biggest real framework ceilings top out well under this). Above
# it, the value is untrustworthy scraper/source noise, not a real contract.
MAX_PLAUSIBLE_AWARD_VALUE_GBP = 10_000_000_000.0  # £10 Billion ceiling threshold

# Some NHS "Contract Variation" (CV) amendment notices report the whole running contract's
# cumulative value on every amendment instead of that amendment's own incremental value -- e.g.
# ~38 separate CV notices for one 2022-2027 acute services contract each carrying £4.5-5.5bn,
# summing to a supplier "total_value" of £167bn for what is really one contract. None of these
# are flagged is_framework (that exclusion doesn't catch this), and the largest confirmed-genuine
# single direct award seen is ~£1.52bn, so a single award above this ceiling is excluded from
# ranking/total-value aggregation everywhere (it still counts toward the award/contract count --
# the award itself is real, only its value is untrustworthy at this size). Single source of truth
# for every aggregation query across stats.py, suppliers_bp.py and buyers_bp.py.
SINGLE_AWARD_STATS_CEILING_GBP = 250_000_000.0

# One list of exact placeholder supplier names, for ingest. The SQL filters (stats._LISTABLE_SQL,
# suppliers_bp.SUPPLIER_NAME_NOT_PLACEHOLDER_SQL) hide the same names; tests/test_workstream_b_supplier_names.py
# fails if any name here is still listable. An award with only a placeholder is dropped (it carries no information);
# an award whose "supplier" is free text (looks_like_description) is kept with no supplier link.
PLACEHOLDER_SUPPLIER_NAMES = frozenset((
    "live scraped supplier", "supplier", "unknown", "n/a", "na", "none", "tbc", "tbd", "not applicable", "contract value",
    "contract", "not awarded", "no award", "award not made", "please refer to weblink", "refer to weblink", "various",
    "see website", "please see website", "redacted", "withheld for security reasons", "withheld", "not named",
    "confidential", "not disclosed", "not available", "to be confirmed", "pending",
))
# "Please see attachment ..." / "refer to the attached list ...": the SQL filters carry this same pattern verbatim.
ATTACHMENT_PLACEHOLDER_PATTERN = r"(see|refer to)[a-z ,'']{0,45}attach"
_ATTACHMENT_PLACEHOLDER_RE = re.compile(ATTACHMENT_PLACEHOLDER_PATTERN.replace("''", "'"), re.IGNORECASE)


_COMPANY_SUFFIX_RE = re.compile(
    r"\b(ltd|limited|llp|plc|inc|llc|cic|corp|co|gmbh|b\.?v\.?|s\.?a\.?|s\.?r\.?l\.?|s\.?l\.?u\.?|c\.?i\.?c\.?)\.?$",
    re.IGNORECASE
)
_SENTENCE_VERB_RE = re.compile(
    r"\b(provide|provides|providing|provided|supply|supplies|servicing|allow|allows|satisfy|satisfies|lead|covered|published|viewed|held|undertake)\b",
    re.IGNORECASE
)


PROSE_SUBSTRINGS = (
    "please see",
    "please refer",
    "commercially confidential",
)
PROSE_PLACEHOLDER_PREFIXES = (
    "as per",
    "information withheld",
    "awarded supplier details",
    "confidential information",
    "confidential / sensitive",
)
PROSE_NO_SUFFIX_PREFIXES = (
    "various ",
    "multiple providers",
    "all successful bidders",
    "all as per",
    "will ",
    "for the ",
    "to ",
    "capabilities ",
    "framework per ",
    "provision and ",
    "implementation of ",
)


def looks_like_description(name: str | None) -> bool:
    """True if name is likely free-text description erroneously parsed as a supplier name."""
    if not name:
        return False
    s = name.strip()
    lowered = s.lower()
    if any(p in lowered for p in PROSE_SUBSTRINGS):
        return True
    if any(lowered.startswith(p) for p in PROSE_PLACEHOLDER_PREFIXES):
        return True
    if len(s) > 120:
        return True
    if _ATTACHMENT_PLACEHOLDER_RE.search(s):
        return True
    if re.search(r"\b[a-z]{2,}\.\s+[A-Z][a-z]{2,}", s):
        return True
    if any(lowered.startswith(p) for p in PROSE_NO_SUFFIX_PREFIXES):
        has_company_suffix = bool(_COMPANY_SUFFIX_RE.search(s))
        if not has_company_suffix:
            if any(lowered.startswith(p) for p in ("various ", "multiple providers", "all successful bidders", "all as per")):
                return True
            if len(s) > 40 or _SENTENCE_VERB_RE.search(s):
                return True
    return False

# Recurring-notice dedup, expressed as SQL so every aggregation query can share it verbatim
# instead of re-deriving it. Mirrors tender_app/blueprints/suppliers_bp.py's
# _dedup_recurring_awards() exactly: the same supplier + the same normalised authority_name +
# the same date_signed is a duplicate republish of one ongoing contract (NHS Integrated Care
# Boards in particular re-publish an "Awarded contract" notice every time an ongoing multi-year
# block contract's budget is revised, rather than amending the original notice), collapsed to its
# single highest-value row. A NULL date_signed has nothing to match on, so it is never collapsed.
# Selecting a.* plus the derived auth_key means callers see every contract_awards column as normal
# (auth_key is just along for the ride) when they select from deduped_awards in place of
# contract_awards.
# Every contract_awards column, in table order. The CTE below has to restate contract_value, and SQL cannot
# override one column of `*`, so the list is explicit; tests/test_shared_notice_value.py fails if the table gains
# or loses a column and this tuple is not updated.
AWARD_COLUMNS = (
    "id", "supplier_id", "company_number", "supplier_name", "authority_name", "tender_title", "cpv_code",
    "cpv_description", "contract_value", "currency", "date_signed", "contract_duration", "procurement_type",
    "is_competitive", "notice_type", "source_portal", "notice_url", "created_at", "contract_start_date",
    "contract_end_date", "is_framework", "buyer_type", "latitude", "longitude",
)

# One notice that names several suppliers (lots, multi-supplier awards) is stored as one row per supplier, each
# carrying the notice's single published value. Summed, a notice of GBP 20.9m with seven rows counted GBP 146m
# (Camden's "Young People Pathway"; a GBP 110m retrofit counted four times). Rows of the same buyer and notice
# that share an identical value therefore count that value ONCE between them: each carries value / n.
# Frameworks are left alone (their ceiling is handled separately) and so are rows with no notice link or value.
# When lots really do have identical values this under-counts, which is the safer error for a figure a buyer quotes.
SHARED_VALUE_N_SQL = (
    "CASE WHEN COALESCE(d.is_framework, 0) = 0 AND d.contract_value > 0 AND COALESCE(d.notice_url, '') <> '' "
    "THEN COUNT(*) OVER (PARTITION BY d.auth_key, d.notice_url, d.contract_value) ELSE 1 END"
)


def deduped_awards_cte_sql(authority_filter: str | None = None) -> str:
    """The de-duplication CTE. `authority_filter` is SQL on the unqualified column authority_name that is
    applied BEFORE de-duplicating, so a single buyer's page de-duplicates that buyer's rows instead of sorting
    all ~500k awards for every query (a buyer profile took ~30 s that way). It gives the same rows as filtering
    afterwards because a duplicate group shares one authority name (auth_key is derived from it).

    deduped_awards has every contract_awards column, plus auth_key, shared_n (how many rows share the notice
    value) and contract_value_notice (the published value). contract_value is the row's share of it."""
    pre = f"\n              WHERE {authority_filter}" if authority_filter else ""
    post = f" AND ({authority_filter})" if authority_filter else ""
    cols = ", ".join(f"b.{c}" for c in AWARD_COLUMNS if c != "contract_value")
    return f"""deduped_base AS (
    (
        SELECT DISTINCT ON (COALESCE(NULLIF(a.supplier_id, 0)::text, NULLIF(a.company_number, ''), a.supplier_name), a.auth_key, a.date_signed) a.*
        FROM (SELECT *, UPPER(TRIM(REGEXP_REPLACE(authority_name, '\\s+', ' ', 'g'))) AS auth_key
              FROM contract_awards{pre}) a
        WHERE a.date_signed IS NOT NULL
        ORDER BY COALESCE(NULLIF(a.supplier_id, 0)::text, NULLIF(a.company_number, ''), a.supplier_name), a.auth_key, a.date_signed, a.contract_value DESC NULLS LAST, a.id
    )
    UNION ALL
    SELECT a.*, UPPER(TRIM(REGEXP_REPLACE(a.authority_name, '\\s+', ' ', 'g'))) AS auth_key
    FROM contract_awards a
    WHERE a.date_signed IS NULL{post}
),
deduped_awards AS (
    SELECT {cols},
           CASE WHEN b.shared_n > 1 THEN b.contract_value / b.shared_n ELSE b.contract_value END AS contract_value,
           b.contract_value AS contract_value_notice, b.shared_n, b.auth_key
    FROM (SELECT d.*, {SHARED_VALUE_N_SQL} AS shared_n FROM deduped_base d) b
)"""


DEDUPED_AWARDS_CTE_SQL = deduped_awards_cte_sql()


def check_value_sanity(record: dict[str, Any], max_threshold: float = MAX_PLAUSIBLE_AWARD_VALUE_GBP) -> tuple[bool, str]:
    """Evaluates whether an award's contract_value is plausible.

    The award itself is still real when this returns False — only the value is untrustworthy;
    callers should null contract_value rather than drop the record. Single source of truth for
    both the live sync path (ingest_award_record, below) and the offline backfill tooling in
    scripts/contracts_finder_sync.py, which used to define its own copy of this check.

    Returns: (is_valid, reason)
    """
    val = record.get("contract_value")
    if val is None:
        return True, ""

    try:
        val_float = float(val)
    except (ValueError, TypeError):
        return False, "Non-numeric contract value"

    if val_float < 0:
        return False, f"Negative contract value ({val_float})"

    # 1. Absolute ceiling threshold.
    if val_float > max_threshold:
        return False, f"Contract value (£{val_float:,.2f}) exceeds plausibility ceiling of £{max_threshold:,.0f}"

    # 2. Obvious placeholder digit patterns e.g. 9999999, 1111111, 12345678.
    # Repeated values alone are NOT suspicious: every supplier on a framework lot is
    # published with the same framework ceiling.
    if val_float > 1000.0:
        val_str = str(int(val_float))
        if len(val_str) >= 6 and (len(set(val_str)) == 1 or val_str in ("12345678", "98765432", "123456789")):
            return False, f"Obvious placeholder value pattern detected ({val_float})"

    return True, ""


def _fallback_company_number(prefix: str, clean_name: str) -> str:
    """Deterministic placeholder company_number for suppliers with no real CH/PPON identifier.

    Must NOT use Python's built-in hash() — it's randomized per-process (PYTHONHASHSEED),
    so the same supplier name previously got a different fallback number on every restart,
    which defeated the suppliers.company_number UNIQUE constraint and created a duplicate
    supplier row each time instead of matching the existing one.
    """
    digest = hashlib.sha1(clean_name.encode("utf-8")).hexdigest()
    return f"{prefix}{int(digest[:8], 16) % 899999 + 100000}"

# Same expression as idx_suppliers_name_key (tender_app/db_ext.py) so lookups use the index.
SUPPLIER_NAME_KEY_SQL = "UPPER(REGEXP_REPLACE(name, '[^A-Za-z0-9]', '', 'g'))"
_PPON_RE = re.compile(r"^[A-Z]{4}-\d{4}-[A-Z]{4}$")
_CLEAN_ID_RE = re.compile(r"^(?=.*\d)[A-Z0-9][A-Z0-9-]{2,24}$")  # has a digit; no spaces, quotes or symbols


def supplier_name_key(name: str) -> str:
    """Case/punctuation/spacing-insensitive identity of a supplier name ("A.B. Ltd" == "AB LTD")."""
    return re.sub(r"[^A-Za-z0-9]", "", name or "").upper()


def _db_execute(cursor, conn, sql: str, params: tuple = ()):
    """Execute SQL query, mapping ?-style placeholders and excluded. references to Postgres syntax."""
    sql = sql.replace("?", "%s").replace("excluded.", "EXCLUDED.")
    cursor.execute(sql, params)

def resolve_supplier_company_number(cursor, conn, raw_cnum: Any, ppon: Any, supplier_name: str) -> str:
    """Pick the suppliers.company_number an award should be filed under.

    Order: (1) a valid Companies House number; (2) the supplier already stored under the same
    normalized name, so one company is not split into a row per notice (the source's own
    GB-FTS-n / CF-n ids differ per notice); (3) a valid PPON; (4) a deterministic name hash.
    A source id that is not a CH number is never stored verbatim - that is how names, "n/a"
    and address fragments ended up in company_number.
    """
    from tender_app.ch_matcher import normalize_ch_company_number

    norm = normalize_ch_company_number(raw_cnum)
    if norm:
        return norm

    key = supplier_name_key(supplier_name)
    if len(key) >= 5:
        _db_execute(cursor, conn, "SELECT company_number FROM suppliers WHERE " + SUPPLIER_NAME_KEY_SQL + " = ? ORDER BY id LIMIT 50;", (key,))
        # Only ids that look like identifiers: junk rows ("n/a", names, quoted numbers) are ignored.
        candidates = [r[0] for r in cursor.fetchall() if _CLEAN_ID_RE.match(str(r[0] or "").upper())]
        real = [c for c in candidates if normalize_ch_company_number(c)]
        if len(real) == 1:
            return real[0]
        if not real:  # no CH-verified row: converge on a PPON row if any, else the first-seen row
            ppons = [c for c in candidates if _PPON_RE.match(str(c or ""))]
            if ppons:
                return ppons[0]
            if candidates:
                return candidates[0]
        # 2+ different CH numbers share this name (e.g. councils): cannot tell which - fall through

    for candidate in (str(ppon or "").strip().upper(), str(raw_cnum or "").strip().upper()):
        if _PPON_RE.match(candidate):
            return candidate

    clean = key or "UNKNOWN"
    for salt in range(0, 20):
        cnum = _fallback_company_number("CF", clean + (str(salt) if salt else ""))
        _db_execute(cursor, conn, "SELECT name FROM suppliers WHERE company_number = ?;", (cnum,))
        row = cursor.fetchone()
        # 6-digit hash space collides across ~40k names: never reuse a number that belongs to a different supplier
        if not row or supplier_name_key(row[0]) == key:
            return cnum
    return _fallback_company_number("CF", clean)


# A single point in the Midlands that ~40,000 stored awards (2,295 buyers) shared: a generic
# "England" placeholder, not anywhere a buyer or contract actually is.
PLACEHOLDER_COORDS = (52.3555, -1.1743)


def sane_award_coords(lat: Any, lon: Any) -> tuple[float | None, float | None]:
    """A usable (lat, lon) pair or (None, None): both present, inside the UK/Ireland box, and
    not the known placeholder. Anything else is stored as unknown rather than as a wrong place."""
    try:
        la, lo = float(lat), float(lon)
    except (TypeError, ValueError):
        return None, None
    if not (49.5 <= la <= 61.5 and -11.0 <= lo <= 2.5):
        return None, None
    if abs(la - PLACEHOLDER_COORDS[0]) < 0.0005 and abs(lo - PLACEHOLDER_COORDS[1]) < 0.0005:
        return None, None
    return la, lo


def _sane_award_date(value: Any) -> str | None:
    """date_signed must be a plausible ISO date; junk like 1900-03-01 or 2034-10-24 becomes NULL."""
    text = str(value or "").strip()
    m = re.match(r"^(\d{4})-\d{2}-\d{2}", text)
    if not m:
        return text or None
    year = int(m.group(1))
    return text if 2000 <= year <= datetime.now().year + 1 else None


# Strong, multi-word institutional phrases only -- unlike classify_buyer_type() (which is
# deliberately loose, single-keyword, and always returns *some* category for use as a buyer
# label), this is a rejection filter for supplier_name and must not false-positive on a
# legitimate commercial supplier whose name happens to contain a word like "health" or
# "authority" (e.g. "Nationwide Health Services Ltd", "Thames Water Authority Group Ltd").
_BUYER_AUTHORITY_NAME_RE = re.compile(
    r"\bnhs\b.*?\b(foundation\s+trust|trust|ft|icb|board|england|scotland|wales)\b"
    r"|\bnhs\s+(?:[a-z&',]+\s+){0,4}(?:foundation\s+trust|trust|ft|icb)\b"
    r"|\bclinical\s+commissioning\s+group\b"
    r"|\bintegrated\s+care\s+board\b"
    r"|\bhealth\s+board\b"
    r"|\b(borough|city|county|district|parish|town)\s+council\b"
    r"|\bcombined\s+authority\b"
    r"|\bpolice\s+and\s+crime\s+commissioner\b"
    r"|\bconstabulary\b"
    r"|\bfire\s+and\s+rescue\s+(service|authority)\b",
    re.IGNORECASE,
)


def looks_like_buyer_authority(name: str) -> bool:
    """True if `name` looks like a contracting authority (NHS trust, ICB, council, police/fire
    authority, ...) rather than a company that could plausibly win and deliver a contract.

    Used to stop buyer entities being upserted as suppliers (e.g. a delivery partner/consortium
    field that got mapped from authority_name, or a scraper field-mapping slip) -- see
    ingest_award_record's placeholder-name check just below, which this sits alongside.
    """
    return bool(name) and bool(_BUYER_AUTHORITY_NAME_RE.search(name))


def ingest_award_record(contract_record: dict[str, Any], conn: Any, overwrite: bool = False) -> bool:
    """Ingests a single normalized award record into suppliers and contract_awards tables.

    Applies:
    - Companies House validation & normalization / PPON preservation
    - Contact confidence verification (wipes mismatched domains to avoid leaks)
    - Deduplication against existing awards by (notice_url, supplier_name)
    - Upsert into suppliers table
    - Insert into contract_awards table (preserving genuine CPV without fabrication)
    Fields the source doesn't state are stored as NULL, never guessed.
    With overwrite=True an award already stored under the same (notice_url,
    supplier_name) is corrected to the record's values instead of being skipped.
    Returns True if successfully inserted, False if skipped/updated as duplicate or on error.
    """
    if not conn:
        return False

    cursor = conn.cursor() if hasattr(conn, "cursor") else conn
    supplier_name = (contract_record.get("supplier_name") or "").strip()
    if not supplier_name or len(supplier_name) < 2:
        return False

    # Check for invalid placeholder supplier names
    if supplier_name.lower() in PLACEHOLDER_SUPPLIER_NAMES:
        return False

    # A buyer authority (NHS trust, ICB, council, police/fire authority, ...) is never a real
    # supplier -- reject rather than upsert it (e.g. a delivery-partner field that got
    # cross-mapped from authority_name).
    if looks_like_buyer_authority(supplier_name):
        return False

    is_desc_supplier = looks_like_description(supplier_name)
    if is_desc_supplier:
        supplier_name = None

    notice_url = contract_record.get("notice_url") or "https://www.contractsfinder.service.gov.uk/Search/Results"

    # 1. Deduplication check: notice_url + supplier_name
    # Case-insensitive: stored names may have been re-cased (e.g. to the Companies House form)
    if supplier_name:
        _db_execute(cursor, conn, "SELECT id FROM contract_awards WHERE notice_url = ? AND LOWER(supplier_name) = LOWER(?) ORDER BY id;",
                    (notice_url, supplier_name))
    else:
        _db_execute(cursor, conn, "SELECT id FROM contract_awards WHERE notice_url = ? AND supplier_name IS NULL ORDER BY id;",
                    (notice_url,))
    existing = cursor.fetchone()
    existing_award_id = existing[0] if existing else None
    if existing_award_id and not overwrite:
        return False

    # 2. OCID cross-source deduplication (catches same notice referenced across portals)
    ocid = str(contract_record.get("ocid") or "").strip()
    if not existing_award_id and ocid and len(ocid) > 6:
        if supplier_name:
            _db_execute(cursor, conn, "SELECT id FROM contract_awards WHERE notice_url LIKE ? AND LOWER(supplier_name) = LOWER(?);",
                        (f"%{ocid}%", supplier_name))
            if cursor.fetchone():
                return False

    # 3. Cross-source procurement match: (buyer name + supplier name + value + month)
    # Catches contracts published across both Contracts Finder and Find a Tender
    authority_name_check = (contract_record.get("authority_name") or "").strip()
    contract_val_check = float(contract_record.get("contract_value") or 0.0)
    date_signed_check = str(contract_record.get("date_signed") or "").strip()[:7]  # YYYY-MM
    if not existing_award_id and supplier_name and authority_name_check and contract_val_check > 0 and len(date_signed_check) == 7:
        _db_execute(cursor, conn, """
            SELECT id FROM contract_awards 
            WHERE LOWER(supplier_name) = LOWER(?) 
              AND LOWER(authority_name) = LOWER(?) 
              AND contract_value = ? 
              AND date_signed LIKE ?;
        """, (supplier_name, authority_name_check, contract_val_check, f"{date_signed_check}%"))
        if cursor.fetchone():
            return False

    # 4. Value sanity guardrail: Reject extreme outliers (e.g. £10B+ corruption)
    if contract_val_check > 10_000_000_000.0 or contract_val_check < 0:
        return False

    cnum = None
    sup_id = None
    if not is_desc_supplier and supplier_name:
        # Normalize company number / identifier
        cnum = resolve_supplier_company_number(
            cursor, conn,
            contract_record.get("company_number"),
            contract_record.get("ppon") or contract_record.get("supplier_identifier"),
            supplier_name,
        )

        address = contract_record.get("address") or ""
        email = contract_record.get("email") or ""
        phone = contract_record.get("phone") or ""
        website = contract_record.get("website") or ""
        region = contract_record.get("region") or "UK"
        sme_status = contract_record.get("sme_status")
        vcse_status = contract_record.get("vcse_status")

        # High-confidence contact domain check to avoid cross-contamination
        if email or website:
            domain = ""
            if email and "@" in email:
                domain = email.split("@")[-1].lower().strip()
            elif website:
                domain = website.replace("https://", "").replace("http://", "").replace("www.", "").split("/")[0].lower().strip()
            generic_domains = ("gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "btconnect.com")
            if domain and domain not in generic_domains:
                try:
                    from tender_app.ch_matcher import clean_name_for_matching
                    clean_words = [w for w in clean_name_for_matching(supplier_name).split() if len(w) > 2]
                    domain_base = domain.split(".")[0]
                    if clean_words and not any(w in domain_base or domain_base in w for w in clean_words):
                        email = ""
                        phone = ""
                        website = ""
                except Exception:
                    pass

        # Upsert supplier
        _db_execute(cursor, conn, """
            INSERT INTO suppliers (company_number, name, address, email, phone, website, region, sme_status, vcse_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(company_number) DO UPDATE SET 
                name = EXCLUDED.name,
                address = COALESCE(EXCLUDED.address, suppliers.address),
                email = COALESCE(EXCLUDED.email, suppliers.email),
                phone = COALESCE(EXCLUDED.phone, suppliers.phone),
                website = COALESCE(EXCLUDED.website, suppliers.website),
                region = COALESCE(EXCLUDED.region, suppliers.region),
                sme_status = COALESCE(EXCLUDED.sme_status, suppliers.sme_status),
                vcse_status = COALESCE(EXCLUDED.vcse_status, suppliers.vcse_status);
        """, (cnum, supplier_name, address, email, phone, website, region, sme_status, vcse_status))

        # Fetch supplier ID
        _db_execute(cursor, conn, "SELECT id FROM suppliers WHERE company_number = ?;", (cnum,))
        s_row = cursor.fetchone()
        sup_id = s_row[0] if s_row else None

    # Award fields
    authority_name = contract_record.get("authority_name") or "UK Public Sector Authority"
    tender_title = contract_record.get("tender_title") or "Public Sector Contract Award"
    cpv_code = contract_record.get("cpv_code") or ""
    cpv_description = contract_record.get("cpv_description") or "Not available"
    raw_value = contract_record.get("contract_value")
    contract_value = float(raw_value) if raw_value not in (None, "") else None
    if contract_value is not None:
        is_sane, _sanity_reason = check_value_sanity({"contract_value": contract_value})
        if not is_sane:
            # The award is still real; only its value is untrustworthy scraper/source noise
            # (see check_value_sanity's docstring) -- never let it inflate a supplier's totals.
            contract_value = None
    currency = contract_record.get("currency") or "GBP"
    date_signed = _sane_award_date(contract_record.get("date_signed"))
    contract_duration = contract_record.get("contract_duration") or None
    procurement_type = contract_record.get("procurement_type") or None
    raw_competitive = contract_record.get("is_competitive")
    is_competitive = None if raw_competitive is None else (1 if raw_competitive else 0)
    notice_type = contract_record.get("notice_type") or "Awarded contract"
    source_portal = contract_record.get("source_portal") or "Contracts Finder"

    contract_start_date = contract_record.get("contract_start_date") or None
    contract_end_date = contract_record.get("contract_end_date") or None
    ptype_lower = (procurement_type or "").lower()
    title_lower = (tender_title or "").lower()
    is_framework = 1 if (
        contract_record.get("is_framework")
        or "framework" in ptype_lower or "call-off" in ptype_lower
        # Some notices only name the arrangement as a framework/call-off in the tender title,
        # not the procurement_type field (e.g. NHS England's "Hospital 2.0 Alliance (H2A)
        # Framework" siblings) -- without this a genuinely-shared ceiling value gets counted
        # as one supplier's direct win.
        or "framework" in title_lower or "call-off" in title_lower
    ) else 0
    buyer_type = contract_record.get("buyer_type") or classify_buyer_type(authority_name)
    lat, lon = sane_award_coords(contract_record.get("latitude"), contract_record.get("longitude"))

    if existing_award_id:
        # Correct the stored award to exactly what the source says (NULL where it says nothing)
        _db_execute(cursor, conn, """
            UPDATE contract_awards SET
                supplier_id = ?, company_number = ?, authority_name = ?, tender_title = ?,
                cpv_code = ?, cpv_description = ?, contract_value = ?, currency = ?, date_signed = ?,
                contract_duration = ?, procurement_type = ?, is_competitive = ?, source_portal = ?,
                contract_start_date = ?, contract_end_date = ?, is_framework = ?, buyer_type = ?
            WHERE id = ?;
        """, (
            sup_id, cnum, authority_name, tender_title,
            cpv_code, cpv_description, contract_value, currency, date_signed,
            contract_duration, procurement_type, is_competitive, source_portal,
            contract_start_date, contract_end_date, is_framework, buyer_type,
            existing_award_id
        ))
        return False

    # Insert award
    _db_execute(cursor, conn, """
        INSERT INTO contract_awards (
            supplier_id, company_number, supplier_name, authority_name, tender_title,
            cpv_code, cpv_description, contract_value, currency, date_signed,
            contract_duration, procurement_type, is_competitive, notice_type,
            source_portal, notice_url, contract_start_date, contract_end_date,
            is_framework, buyer_type, latitude, longitude
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """, (
        sup_id, cnum, supplier_name, authority_name, tender_title,
        cpv_code, cpv_description, contract_value, currency, date_signed,
        contract_duration, procurement_type, is_competitive, notice_type,
        source_portal, notice_url, contract_start_date, contract_end_date,
        is_framework, buyer_type, lat, lon
    ))

    return True


# Housing providers whose names say nothing about housing. Matched as substrings of the lower-cased name.
_HOUSING_GROUP_NAMES = (
    "riverside group", "peabody", "notting hill genesis", "london & quadrant", "london and quadrant",
    "metropolitan thames valley", "a2dominion", "guinness partnership", "sanctuary group", "orbit group",
    "sovereign network", "onward group", "torus group",
)


def classify_buyer_type(name: str) -> str:
    """Classifies a contracting authority name into one of 7 canonical buyer types based on name patterns."""
    if not name:
        return "Other Public Bodies"
    n = name.lower()
    if any(k in n for k in ["nhs", "foundation trust", "clinical commissioning", "health", "integrated care"]):
        return "NHS & Healthcare"
    # Names that look like a council or a trust but are not: checked before the council keywords.
    if "british council" in n or "arts council" in n:
        return "Other Public Bodies"
    if any(k in n for k in _HOUSING_GROUP_NAMES):
        return "Housing Associations"
    if any(k in n for k in ["council", "borough", "district", "metropolitan", "county", "city council", "corporation of"]):
        return "Local Government / Council"
    if any(k in n for k in ["department for", "ministry", "hm treasury", "cabinet office", "executive agency", "crown commercial", "home office", "foreign, commonwealth"]):
        return "Central Government & Agencies"
    if any(k in n for k in ["police", "constabulary", "fire and rescue", "fire & rescue", "ambulance", "crime commissioner"]):
        return "Police & Emergency Services"
    if any(k in n for k in ["university", "college", "academy", "school", "education trust"]):
        return "Education & Academies"
    # No bare "association": it caught the Local Government Association, the Reserve Forces' and Cadets'
    # Associations, the Workers' Educational Association and similar. "housing association" has "housing".
    if any(k in n for k in ["housing", "homes", "places for people", "community gateway"]):
        return "Housing Associations"
    return "Other Public Bodies"

def ingest_award_records_from_jsonl(jsonl_path: str, conn: Any, batch_size: int = 500) -> dict[str, Any]:
    """Streams normalized award records from a JSONL file and ingests them into the database."""
    import os
    if not os.path.exists(jsonl_path):
        return {"error": f"File not found: {jsonl_path}", "processed": 0, "inserted": 0, "duplicates": 0, "errors": 0}

    stats = {"processed": 0, "inserted": 0, "duplicates": 0, "errors": 0}
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            stats["processed"] += 1
            try:
                rec = json.loads(line)
                if rec.get("flagged_no_supplier"):
                    continue
                inserted = ingest_award_record(rec, conn)
                if inserted:
                    stats["inserted"] += 1
                else:
                    stats["duplicates"] += 1

                if stats["processed"] % batch_size == 0 and hasattr(conn, "commit"):
                    conn.commit()
            except Exception as ex:
                stats["errors"] += 1

    if hasattr(conn, "commit"):
        conn.commit()

    return stats

def scrape_live_find_tender_awards(keywords: str = "services", max_records: int = 5, conn: Any = None) -> list[dict[str, Any]]:
    """Scrapes real, live Contract Award Notices directly from Find a Tender (find-tender.service.gov.uk).

    Returns a list of parsed live award notice records and inserts them into suppliers & contract_awards tables.
    """
    import re
    import requests
    from bs4 import BeautifulSoup

    _BASE = "https://www.find-tender.service.gov.uk"
    _SEARCH_URL = f"{_BASE}/Search/Results"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }

    s = requests.Session()
    scraped_records = []

    try:
        r = s.get(_SEARCH_URL, headers=headers, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        token_el = soup.find("input", attrs={"name": "form_token"})
        token = token_el.get("value", "") if token_el else ""

        post_data = {
            "form_token": token,
            "keywords": keywords,
            "stage[]": "award"
        }

        search_resp = s.post(_SEARCH_URL, data=post_data, headers=headers, timeout=20)
        search_resp.raise_for_status()
        search_soup = BeautifulSoup(search_resp.text, "html.parser")

        links = search_soup.find_all("a", href=True)
        notice_links = []
        for l in links:
            href = l['href']
            if "/Notice/" in href and href not in notice_links:
                notice_links.append(href)

        for href in notice_links[:max_records]:
            full_url = href if href.startswith("http") else f"{_BASE}{href}"
            try:
                detail_resp = s.get(full_url, headers=headers, timeout=15)
                if detail_resp.status_code != 200:
                    continue
                d_soup = BeautifulSoup(detail_resp.text, "html.parser")

                title_el = d_soup.find("h1")
                title = title_el.get_text(strip=True) if title_el else "Public Sector Contract Award"

                authority_name = ""
                auth_email = ""
                supplier_name = ""
                cnum = ""
                sup_email = ""
                sup_phone = ""
                sup_website = ""
                sup_address = ""
                region = "UK"
                sme_status = "SME"
                vcse_status = "Non-VCSE"

                # 1. Extract Contracting Authority Section
                auth_heading = d_soup.find(lambda e: e.name in ["h2", "h3"] and "contracting authority" in e.get_text().lower())
                if auth_heading:
                    parent = auth_heading.find_parent(["div", "section"])
                    if parent:
                        h3 = parent.find("h3")
                        if h3:
                            authority_name = h3.get_text(strip=True)
                        lines_a = [l.strip() for l in parent.get_text("\n").split("\n") if l.strip()]
                        for idx, l in enumerate(lines_a):
                            if l.startswith("Email:"):
                                auth_email = l.replace("Email:", "").strip() or (lines_a[idx+1] if idx+1 < len(lines_a) else "")

                if not authority_name:
                    for line in d_soup.get_text("\n").split("\n"):
                        if "contracting authority" in line.lower() and len(line) < 100:
                            authority_name = line.split(":")[-1].strip()
                            break

                # 2. Extract ALL Supplier/Contract Sections (Multi-Lot Support)
                # FIXED: Find a Tender uses IDs contract_0, contract_1, contract_2 for multi-lot notices
                # Each contract has supplier NAME and VALUE, but company DETAILS are in a separate "Suppliers" section
                
                contract_sections = []
                
                # Method 1: Find by ID pattern contract_0, contract_1, contract_2, etc.
                print(f"[Multi-Lot Parser] Scanning for contract sections in notice {full_url}")
                for i in range(20):  # Check up to 20 contracts
                    contract_heading = d_soup.find(id=f"contract_{i}")
                    if not contract_heading:
                        break
                    
                    # Get all content between this heading and the next contract heading
                    next_contract_heading = d_soup.find(id=f"contract_{i+1}")
                    
                    # Collect siblings between current and next
                    content_text_parts = []
                    current = contract_heading.find_next_sibling()
                    while current and current != next_contract_heading:
                        if current.name:  # Skip text nodes
                            content_text_parts.append(current.get_text(" ", strip=True))
                        current = current.find_next_sibling()
                    
                    contract_text = " ".join(content_text_parts)
                    contract_sections.append({
                        "index": i + 1,
                        "heading": contract_heading,
                        "text": contract_text
                    })
                    print(f"[Multi-Lot Parser]   Found Contract {i+1} ({len(contract_text)} chars)")
                
                # Method 2: Fallback to old method if no contract_N IDs found
                if not contract_sections:
                    print(f"[Multi-Lot Parser]   No contract_N IDs found, trying heading-based detection")
                    for h_tag in d_soup.find_all(["h2", "h3"]):
                        h_text = h_tag.get_text(strip=True)
                        if re.match(r"^Contract\s+\d+", h_text, re.I):
                            parent = h_tag.find_parent(["div", "section"]) or h_tag.parent
                            contract_sections.append({
                                "index": len(contract_sections) + 1,
                                "heading": h_tag,
                                "text": parent.get_text(" ", strip=True)
                            })
                
                # Method 3: If still no sections, treat as single contract
                if not contract_sections:
                    print(f"[Multi-Lot Parser]   No multi-lot structure detected, treating as single contract")
                    contract_sections = [{
                        "index": 1,
                        "heading": None,
                        "text": d_soup.get_text(" ", strip=True)
                    }]
                
                print(f"[Multi-Lot Parser] Found {len(contract_sections)} contract section(s) in notice {full_url}")
                
                # Extract the separate "Suppliers" section for company details (Companies House numbers, etc.)
                suppliers_details = {}
                suppliers_heading = d_soup.find(lambda e: e.name == "h2" and "Suppliers" in e.get_text() and "Scope" not in e.get_text())
                if suppliers_heading:
                    print(f"[Multi-Lot Parser] Found separate 'Suppliers' section with company details")
                    # Find each supplier's h3 under this section
                    for h3 in suppliers_heading.find_all_next("h3"):
                        # Stop at next h2 (end of Suppliers section)
                        if h3.find_previous("h2") != suppliers_heading:
                            break
                        
                        supplier_name = h3.get_text(strip=True)
                        
                        # Get details for this supplier
                        details_parts = []
                        current = h3.find_next_sibling()
                        while current and current.name not in ['h2', 'h3']:
                            if current.name:
                                details_parts.append(current.get_text(" ", strip=True))
                            current = current.find_next_sibling()
                        
                        details_text = " ".join(details_parts)
                        suppliers_details[supplier_name.upper()] = details_text
                        print(f"[Multi-Lot Parser]   Cached details for: {supplier_name}")
                
                # Process EACH contract section separately
                contracts_to_ingest = []
                for contract_section in contract_sections:
                    contract_idx = contract_section["index"]
                    contract_text = contract_section["text"]
                    
                    # Reset per-contract variables
                    supplier_name = ""
                    cnum = ""
                    ppon = ""  # NEW: Public Procurement Organisation Number
                    sup_email = ""
                    sup_phone = ""
                    sup_website = ""
                    sup_address = ""
                    region = "UK"
                    sme_status = "SME"
                    vcse_status = "Non-VCSE"
                    value = 0.0
                    cpv_code = ""  # Extract per-contract CPV
                    date_signed = datetime.now().strftime("%Y-%m-%d")
                    procurement_type = "Open competition"
                    is_competitive = 1
                    
                    # Extract supplier name from contract text
                    # Pattern: "Supplier [NAME] Contract value". The capture is bounded to ~100
                    # chars (real company names are always far shorter) -- unbounded [^\n]+? used
                    # to run all the way to the next "Contract value"/"£" in the notice, which on
                    # some multi-lot/KPI-heavy notices is paragraphs away, so a whole block of
                    # unrelated prose got captured and ingested as the "supplier name". Bounding it
                    # means a malformed notice like that just fails to match (no supplier name --
                    # the existing invalid-name check below then skips it) instead of matching junk.
                    sup_match = re.search(r'Supplier\s+([A-Z][^\n]{1,100}?)(?:\s+Contract value|\s+£)', contract_text, re.I)
                    if sup_match:
                        supplier_name = sup_match.group(1).strip()
                        print(f"[Multi-Lot Parser] Contract {contract_idx}: Supplier = {supplier_name}")
                    
                    # Extract contract value from contract text.
                    # CRITICAL FIX (2026-09): this used to also treat "£26,667
                    # excluding VAT" / "£32,000 including VAT" — the normal wording
                    # on every notice — as a "£X million" shorthand and multiply by
                    # 1,000,000, corrupting real values (e.g. a genuine £26,667
                    # contract was stored as £26,667,000,000). Only the literal word
                    # "million" means multiply; "excluding"/"including" are VAT
                    # qualifiers and must NOT trigger the multiplier.
                    val_match = re.search(r'£([\d,]+(?:\.\d+)?)\s+million\b', contract_text, re.I)
                    if val_match:
                        value = float(val_match.group(1).replace(",", "")) * 1_000_000
                    else:
                        val_match2 = re.search(r'£([\d,]+(?:\.\d+)?)', contract_text)
                        if val_match2:
                            value = float(val_match2.group(1).replace(",", ""))
                    
                    print(f"[Multi-Lot Parser] Contract {contract_idx}: Value = £{value:,.2f}")
                    
                    # Extract CPV code from contract text
                    cpv_match = re.search(r'(\d{8})\s*[-–—]\s*([^\.]+)', contract_text)
                    if cpv_match:
                        cpv_code = cpv_match.group(1)
                        print(f"[Multi-Lot Parser] Contract {contract_idx}: CPV = {cpv_code}")
                    
                    # Extract SME status from contract text
                    if "Small or medium-sized enterprise" in contract_text:
                        sme_section = contract_text.split("Small or medium-sized enterprise")[1][:100]
                        sme_status = "SME" if "Yes" in sme_section else "Non-SME"
                    
                    # Extract VCSE status
                    if "Voluntary, community or social enterprise" in contract_text:
                        vcse_section = contract_text.split("Voluntary, community or social enterprise")[1][:100]
                        vcse_status = "VCSE" if "Yes" in vcse_section else "Non-VCSE"
                    
                    # Extract award date
                    date_match = re.search(r'Award decision date\s+(\d{1,2})\s+(\w+)\s+(\d{4})', contract_text, re.I)
                    if date_match:
                        day, month_name, year = date_match.groups()
                        try:
                            from datetime import datetime as dt
                            month_map = {"january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
                                         "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12}
                            month = month_map.get(month_name.lower(), 1)
                            date_signed = f"{year}-{month:02d}-{int(day):02d}"
                        except Exception:
                            pass
                    
                    # Now look up company details from the separate Suppliers section
                    if supplier_name and suppliers_details:
                        supplier_key = supplier_name.upper()
                        if supplier_key in suppliers_details:
                            details_text = suppliers_details[supplier_key]
                            print(f"[Multi-Lot Parser] Contract {contract_idx}: Found company details in Suppliers section")
                            
                            # Extract Companies House number
                            cnum_match = re.search(r"Companies\s+House[:\s]+([A-Z0-9]{6,8})", details_text, re.I)
                            if cnum_match:
                                from tender_app.ch_matcher import normalize_ch_company_number
                                cnum = normalize_ch_company_number(cnum_match.group(1)) or ""
                                print(f"[Multi-Lot Parser] Contract {contract_idx}: Companies House = {cnum}")
                            
                            # Extract PPON (Public Procurement Organisation Number) as fallback
                            ppon_match = re.search(r"Public\s+Procurement\s+Organisation\s+Number[:\s]+([A-Z0-9-]+)", details_text, re.I)
                            if ppon_match:
                                ppon = ppon_match.group(1)
                                print(f"[Multi-Lot Parser] Contract {contract_idx}: PPON = {ppon}")
                            
                            # Extract email
                            email_match = re.search(r'Email[:\s]+([^\s]+@[^\s]+)', details_text, re.I)
                            if email_match:
                                sup_email = email_match.group(1)
                            
                            # Extract website
                            website_match = re.search(r'Website[:\s]+(https?://[^\s]+)', details_text, re.I)
                            if website_match:
                                sup_website = website_match.group(1)
                            
                            # Extract phone
                            phone_match = re.search(r'(?:Phone|Tel)[:\s]+([\d\s\-\+\(\)]+)', details_text, re.I)
                            if phone_match:
                                sup_phone = phone_match.group(1).strip()
                        else:
                            print(f"[Multi-Lot Parser] Contract {contract_idx}: WARNING - Supplier '{supplier_name}' not found in Suppliers section")
                    
                    # Validation: Skip invalid supplier names. The length cap is defense-in-depth
                    # against the regex above matching a paragraph of prose instead of a name --
                    # real company names are never this long.
                    if not supplier_name or len(supplier_name) > 120 or supplier_name.strip().lower() in ("live scraped supplier", "supplier", "unknown", "n/a", "none", "contract value", "contract", "not awarded", "no award", "award not made", "please refer to weblink", "refer to weblink", "various", "see website", "please see website", "redacted", "withheld for security reasons") or supplier_name.lower().startswith("contracting authorities"):
                        print(f"[Multi-Lot Parser] Skipping contract {contract_idx}: Invalid supplier name ({supplier_name!r})")
                        continue
                    
                    # Generate company number if missing
                    if not cnum:
                        clean_name = re.sub(r"[^A-Za-z0-9]", "", supplier_name).upper()
                        cnum = _fallback_company_number("UK", clean_name)
                    
                    # Create contract record
                    contract_record = {
                        "company_number": cnum,
                        "supplier_name": supplier_name,
                        "authority_name": authority_name or "UK Public Authority",
                        "address": sup_address,
                        "email": sup_email,
                        "phone": sup_phone,
                        "website": sup_website,
                        "tender_title": title,
                        "cpv_code": cpv_code or "00000000",  # FIXED: Use extracted CPV, not hardcoded value
                        "cpv_description": "Not available",  # Can be enhanced later
                        "contract_value": value,
                        "currency": "GBP",
                        "date_signed": date_signed,
                        "contract_duration": "24 months",
                        "procurement_type": procurement_type,
                        "is_competitive": is_competitive,
                        "notice_type": "UK7: Contract details notice",
                        "source_portal": "Find a Tender",
                        "notice_url": full_url,
                        "region": region,
                        "sme_status": sme_status,
                        "vcse_status": vcse_status,
                        "contract_lot_number": contract_idx if len(contract_sections) > 1 else None,
                        "ppon": ppon or None  # NEW: Store PPON if available
                    }
                    
                    contracts_to_ingest.append(contract_record)
                    print(f"[Multi-Lot Parser] Contract {contract_idx}: {supplier_name} - £{value:,.2f}")
                
                # Ingest all contracts from this notice
                for contract_record in contracts_to_ingest:
                    if conn:
                        try:
                            if ingest_award_record(contract_record, conn):
                                if hasattr(conn, "commit"):
                                    conn.commit()
                                print(f"[Multi-Lot Parser] ✅ Ingested: {contract_record['supplier_name']} - £{contract_record['contract_value']:,.2f}")
                            else:
                                print(f"[Multi-Lot Parser] Skipping duplicate: {contract_record['supplier_name']} already in database for this notice")
                        except Exception as db_err:
                            print(f"Database ingestion error for {contract_record['supplier_name']}: {db_err}")
                            if hasattr(conn, "rollback"):
                                try:
                                    conn.rollback()
                                except Exception:
                                    pass
                            continue
                    
                    scraped_records.append(contract_record)
            except Exception as ex:
                print(f"Error parsing live notice {full_url}: {ex}")

    except Exception as ex:
        print(f"Live Find a Tender award scraper error: {ex}")

    return scraped_records


def scrape_live_contracts_finder_awards(limit: int = 100, days_back: int = 120, conn: Any = None) -> list[dict[str, Any]]:
    """Scrapes real, live Contract Award Notices directly from Contracts Finder OCDS API
    (https://www.contractsfinder.service.gov.uk/Search/Results).
    
    Inserts or updates suppliers and contract_awards tables.
    """
    import requests
    import re
    from datetime import datetime, timedelta
    from etenders_scraper.client import SSLAdapter

    session = requests.Session()
    session.mount("https://", SSLAdapter())
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*"
    })

    api_url = "https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search"
    since_date = datetime.now() - timedelta(days=days_back)
    params = {
        "publishedFrom": since_date.strftime("%Y-%m-%dT%H:%M:%S"),
        "stages": "award",
        "limit": limit
    }

    scraped_records = []
    try:
        resp = session.get(api_url, params=params, timeout=30)
        if resp.status_code != 200:
            print(f"[Contracts Finder Awards] API returned status {resp.status_code}")
            return scraped_records

        data = resp.json()
        releases = data.get("releases", [])
        for rel in releases:
            tender = rel.get("tender", {})
            title = tender.get("title") or "Public Sector Contract Award"
            desc = tender.get("description") or ""
            buyer_obj = rel.get("buyer", {})
            authority_name = buyer_obj.get("name") or "UK Public Sector Authority"

            parties = {p.get("id"): p for p in rel.get("parties", []) if isinstance(p, dict) and p.get("id")}

            notice_url = ""
            for doc in tender.get("documents", []):
                u = doc.get("url", "")
                if "/Notice/" in u:
                    notice_url = u
                    break

            for award in rel.get("awards", []):
                date_raw = award.get("date") or award.get("datePublished") or ""
                date_signed = date_raw[:10] if date_raw else datetime.now().strftime("%Y-%m-%d")

                val_obj = award.get("value", {})
                contract_value = float(val_obj.get("amount") or 0.0) if isinstance(val_obj, dict) else 0.0
                currency = val_obj.get("currency") or "GBP" if isinstance(val_obj, dict) else "GBP"

                if not notice_url:
                    for doc in award.get("documents", []):
                        u = doc.get("url", "")
                        if "/Notice/" in u:
                            notice_url = u
                            break

                contract_period = award.get("contractPeriod", {})
                start_p = (contract_period.get("startDate") or "")[:10]
                end_p = (contract_period.get("endDate") or "")[:10]
                contract_duration = "12-36 months"
                if start_p and end_p:
                    contract_duration = f"{start_p} to {end_p}"

                for sup in award.get("suppliers", []):
                    sup_name = (sup.get("name") or "").strip()
                    if not sup_name or len(sup_name) < 3:
                        continue
                    sup_name_lower = sup_name.lower()
                    if any(junk in sup_name_lower for junk in [
                        "maximum number", "associated tender", "live scraped", "see attached",
                        "see tender", "lot 1", "lot 2", "suppliers", "unknown", "n/a", "none",
                        "tbc", "not applicable", "contract value", "contracting authorities",
                        "not awarded", "no award", "award not made", "refer to weblink",
                        "see website", "redacted", "withheld for security reasons"
                    ]):
                        continue

                    sup_id = sup.get("id") or ""
                    party = parties.get(sup_id, {})
                    addr_obj = party.get("address", {})
                    address_parts = []
                    if isinstance(addr_obj, dict):
                        for k in ["streetAddress", "locality", "postalCode", "countryName"]:
                            v = addr_obj.get(k)
                            if v:
                                address_parts.append(str(v).replace("\r", " ").replace("\n", " ").strip())
                    sup_address = ", ".join(address_parts)

                    cnum = ""
                    if "GB-COH-" in sup_id:
                        cnum = sup_id.replace("GB-COH-", "").strip()
                    elif "GB-CFS-" in sup_id:
                        cnum = sup_id.replace("GB-CFS-", "CF").strip()
                    if not cnum:
                        match = re.search(r"\b(\d{7,8}|[A-Za-z]{2}\d{5,6})\b", sup_address)
                        if match:
                            cnum = match.group(1).upper()
                    if not cnum:
                        clean_name = re.sub(r"[^A-Za-z0-9]", "", sup_name).upper()
                        cnum = _fallback_company_number("CF", clean_name)

                    contact_obj = party.get("contactPoint", {})
                    sup_email = contact_obj.get("email") or "" if isinstance(contact_obj, dict) else ""
                    sup_phone = contact_obj.get("telephone") or "" if isinstance(contact_obj, dict) else ""
                    sup_website = contact_obj.get("url") or "" if isinstance(contact_obj, dict) else ""

                    region = "UK"
                    addr_lower = sup_address.lower()
                    if "london" in addr_lower:
                        region = "UKI - London"
                    elif "scotland" in addr_lower or "edinburgh" in addr_lower or "glasgow" in addr_lower:
                        region = "UKM - Scotland"
                    elif "wales" in addr_lower or "cardiff" in addr_lower:
                        region = "UKL - Wales"
                    elif "belfast" in addr_lower:
                        region = "UKN - Northern Ireland"

                    full_notice_url = notice_url or "https://www.contractsfinder.service.gov.uk/Search/Results"

                    record = {
                        "company_number": cnum,
                        "supplier_name": sup_name,
                        "authority_name": authority_name,
                        "address": sup_address,
                        "email": sup_email,
                        "phone": sup_phone,
                        "website": sup_website,
                        "tender_title": title,
                        "cpv_code": "72000000",
                        "cpv_description": "Public Sector Contract",
                        "contract_value": contract_value,
                        "currency": currency,
                        "date_signed": date_signed,
                        "contract_duration": contract_duration,
                        "procurement_type": "Open competition",
                        "is_competitive": 1,
                        "notice_type": "Awarded contract",
                        "source_portal": "Contracts Finder",
                        "notice_url": full_notice_url,
                        "region": region,
                        "sme_status": "SME" if contract_value < 1000000 else "Non-SME",
                        "vcse_status": "Non-VCSE"
                    }

                    if conn:
                        try:
                            if ingest_award_record(record, conn):
                                if hasattr(conn, "commit"):
                                    conn.commit()
                        except Exception as db_err:
                            print(f"Contracts Finder award db ingestion error: {db_err}")
                            if hasattr(conn, "rollback"):
                                try:
                                    conn.rollback()
                                except Exception:
                                    pass
                            continue

                    scraped_records.append(record)
    except Exception as ex:
        print(f"Contracts Finder awards scraper error: {ex}")

    return scraped_records


def scrape_live_bravo_awards(
    source_id: str,
    site_root: str,
    source_label: str,
    limit: int = 30,
    conn: Any = None,
) -> list[dict[str, Any]]:
    """Scrapes real, live Contract Award Notices from a Bravo-based portal
    (Sell2Wales / Public Contracts Scotland).

    Opens the full detail page for each candidate notice and ingests only the ones
    that genuinely publish an awarded supplier under the "Award of Contract" /
    "Successful Bidders" section (see bravo_search._extract_award_of_contract_winner)
    — a notice with no published winner is skipped, never fabricated.

    Candidate selection differs by portal because their search UIs differ:
    - Sell2Wales exposes a "Contract Results" filter (a checkbox-picker widget) that
      reliably returns only award notices — used directly via
      search_sell2wales_contract_results (confirmed live, 2026-09).
    - Public Contracts Scotland's equivalent "Contract Results" dropdown
      (ctl00$maincontent$ddDocType=2) did NOT change the server's response in
      testing (likely needs an AJAX postback this scraper doesn't replicate), so
      PCS instead scans its normal recent-notices feed and relies on the detail-page
      check above to keep only genuine awards — a lower hit rate, but still 100%
      real when it does find one.
    """
    import re
    from .deadline import parse_deadline
    from .sources.bravo_search import search_bravo_portal, search_sell2wales_contract_results, fetch_bravo_details

    scraped_records: list[dict[str, Any]] = []

    try:
        if source_id == "sell2wales":
            rows = search_sell2wales_contract_results(
                source_id=source_id, source_label=source_label, site_root=site_root, max_results=limit,
            )
        else:
            rows = search_bravo_portal(
                site_root=site_root,
                keyword="",
                source_id=source_id,
                source_label=source_label,
                max_results=limit,
            )
    except Exception as ex:
        print(f"[{source_label} awards] search error: {ex}")
        return scraped_records

    region = "Scotland" if source_id == "pcs" else "Wales"

    for row in rows:
        resource_id = row.get("resource_id")
        detail_url = row.get("detail_url")
        if not resource_id and not detail_url:
            continue

        try:
            details = fetch_bravo_details(source_id, resource_id or detail_url, site_root=site_root)
        except Exception as ex:
            print(f"[{source_label} awards] detail fetch error for {resource_id}: {ex}")
            continue
        if not details:
            continue

        supplier_name = (details.get("awarded_supplier") or "").strip()
        if not supplier_name or len(supplier_name) < 2:
            continue

        authority_name = (details.get("contracting_authority") or row.get("contracting_authority") or "").strip()

        # These detail pages don't always fill in a value at all, and the generic
        # "Total value"/"Estimated value" regex in fetch_bravo_details sometimes
        # grabs the next line even when that line is actually a section header
        # (confirmed live: a notice with a blank value field produced "3 Procedure"
        # here, which would otherwise be misread as a real "£3" contract). Only
        # trust it when the matched text actually carries a currency marker.
        value = 0.0
        val_text = str(details.get("estimated_value_eur") or row.get("estimated_value_eur") or "")
        if re.search(r"GBP|EUR|£|€", val_text, re.I):
            val_m = re.search(r"[\d,]+(?:\.\d+)?", val_text)
            if val_m:
                try:
                    value = float(val_m.group(0).replace(",", ""))
                except ValueError:
                    value = 0.0

        parsed_date = parse_deadline(details.get("award_date"))
        date_signed = parsed_date.strftime("%Y-%m-%d") if parsed_date else datetime.now().strftime("%Y-%m-%d")

        cpv_code = ""
        cpv_m = re.search(r"\b(\d{8})\b", details.get("cpv_codes") or "")
        if cpv_m:
            cpv_code = cpv_m.group(1)

        clean_name = re.sub(r"[^A-Za-z0-9]", "", supplier_name).upper()
        cnum = _fallback_company_number("BR", clean_name)

        contract_record = {
            "company_number": cnum,
            "supplier_name": supplier_name,
            "address": details.get("awarded_supplier_address") or "",
            "authority_name": authority_name or f"{source_label} Public Authority",
            "tender_title": details.get("title") or row.get("title") or "Public Sector Contract Award",
            "cpv_code": cpv_code,
            "cpv_description": "Not available",
            "contract_value": value,
            "currency": "GBP",
            "date_signed": date_signed,
            "contract_duration": "12-36 months",
            "procurement_type": details.get("procedure") or "Open competition",
            "is_competitive": 1,
            "notice_type": "Contract Award Notice",
            "source_portal": source_label,
            "notice_url": details.get("detail_url") or detail_url or "",
            "region": region,
            "sme_status": "SME",
            "vcse_status": "Non-VCSE",
        }

        if conn:
            try:
                if ingest_award_record(contract_record, conn):
                    if hasattr(conn, "commit"):
                        conn.commit()
            except Exception as db_err:
                print(f"[{source_label} awards] DB ingestion error: {db_err}")
                if hasattr(conn, "rollback"):
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                continue

        scraped_records.append(contract_record)

    return scraped_records


AWARD_SCHEDULER_STATUS: dict[str, Any] = {
    "active": False,
    "last_run_at": None,
    "last_run_scraped_count": 0,
    "last_run_status": "initialized",
    "last_error": None,
    "interval_hours": 6,
    "sources": {
        "Contracts Finder": {"active": True, "type": "live_scheduled", "schedule": "Every 6 hours", "display_label": "Contracts Finder", "url": "https://www.contractsfinder.service.gov.uk/Search/Results", "notices_count": "149,210+ awarded notices"},
        "Find a Tender": {"active": True, "type": "live_scheduled", "schedule": "Every 6 hours", "display_label": "Find a Tender", "url": "https://www.find-tender.service.gov.uk/Search/Results", "notices_count": "UK above-threshold awarded notices"},
        "Sell2Wales": {"active": True, "type": "live_scheduled", "schedule": "Every 6 hours", "display_label": "Sell2Wales", "url": "https://www.sell2wales.gov.wales/Search/Search_MainPage.aspx", "notices_count": "Welsh public sector awarded notices"},
        "Public Contracts Scotland": {"active": True, "type": "live_scheduled", "schedule": "Every 6 hours", "display_label": "Public Contracts Scotland", "url": "https://www.publiccontractsscotland.gov.uk/Search/Search_MainPage.aspx", "notices_count": "Scottish public sector awarded notices"}
    }
}


def get_award_scheduler_status() -> dict[str, Any]:
    """Returns real-time status of the award scraper background worker."""
    return AWARD_SCHEDULER_STATUS


def start_award_scheduler(get_db_conn_func: Any = None):
    """Starts background thread to run live award scrapers for all 4 portals that
    support real award-notice ingestion (Contracts Finder, Find a Tender, Sell2Wales,
    Public Contracts Scotland) every 6 hours (keeps them current between full backfills).

    eTenders Ireland/NI are deliberately excluded: a live check (2026-09) confirmed
    their public Contract Award Notice view and downloadable notice PDF never expose
    the winning supplier's name for ordinary (non-TED/above-threshold) awards — only
    award value/date. Building a supplier-intelligence pipeline there would mean
    inventing a winner, which this codebase must never do.
    """
    import threading
    import time

    def _run_job(label: str, fn, errors: list[str]) -> int:
        try:
            conn = get_db_conn_func() if get_db_conn_func else None
            print(f"[Award Scraper Scheduler] Syncing live {label} award notices...")
            records = fn(conn)
            if conn and hasattr(conn, "close"):
                conn.close()
            print(f"[Award Scraper Scheduler] Complete. Synced {len(records)} {label} award records.")
            return len(records)
        except Exception as ex:
            errors.append(f"{label}: {ex}")
            print(f"[Award Scraper Scheduler] {label} error: {ex}")
            return 0

    def _worker():
        time.sleep(180)  # Initial 3-minute delay after server boot to prevent startup rate-limit collisions
        AWARD_SCHEDULER_STATUS["active"] = True
        jobs = [
            ("Contracts Finder", lambda conn: scrape_live_contracts_finder_awards(limit=50, days_back=30, conn=conn)),
            ("Find a Tender", lambda conn: scrape_live_find_tender_awards(max_records=50, conn=conn)),
            ("Sell2Wales", lambda conn: scrape_live_bravo_awards("sell2wales", "https://www.sell2wales.gov.wales", "Sell2Wales", limit=30, conn=conn)),
            ("Public Contracts Scotland", lambda conn: scrape_live_bravo_awards("pcs", "https://www.publiccontractsscotland.gov.uk", "Public Contracts Scotland", limit=30, conn=conn)),
        ]
        while True:
            total_count = 0
            errors: list[str] = []

            for label, fn in jobs:
                total_count += _run_job(label, fn, errors)

            AWARD_SCHEDULER_STATUS["last_run_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            AWARD_SCHEDULER_STATUS["last_run_scraped_count"] = total_count
            AWARD_SCHEDULER_STATUS["last_run_status"] = "success" if not errors else ("error" if len(errors) == len(jobs) else "partial")
            AWARD_SCHEDULER_STATUS["last_error"] = "; ".join(errors) if errors else None
            time.sleep(6 * 3600)  # 6 hours

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    print("[Award Scraper Scheduler] Initialized background award notices worker (Contracts Finder, Find a Tender, Sell2Wales, Public Contracts Scotland — repeats every 6 hours).")
