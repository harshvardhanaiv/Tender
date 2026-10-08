"""Supplier Intelligence Flask Blueprint for TenderFlow.

Surfaces competitive intelligence, past contract award histories, incumbent metrics,
SME/VCSE status, and procurement type analysis under Open Government Licence v3.0.
"""
from __future__ import annotations

import io
import re
from typing import Any
from flask import Blueprint, jsonify, request, Response, session
from etenders_scraper.awards import (
    OGL_V3_ATTRIBUTION,
    UK_IE_SOURCE_PORTALS,
    SINGLE_AWARD_STATS_CEILING_GBP,
    DEDUPED_AWARDS_CTE_SQL,
)
from tender_app.geo import geo_radius_clause
from tender_app.supplier_classifier import classify_supplier_real_data, is_known_large_enterprise
from tender_app.ch_matcher import normalize_ch_company_number, is_valid_ch_company_number, calculate_name_similarity, clean_contact_field

suppliers_bp = Blueprint("suppliers_bp", __name__)

# Hard wall-clock cap on the live Companies House enrichment scrape triggered from
# get_supplier_detail(). Individual requests inside it each carry their own 4s socket
# timeout, but nothing bounded the total across all of them — see call site for detail.
CH_ENRICH_DEADLINE_SECS = 8

_get_db = None


def init_suppliers_blueprint(get_db_connection):
    global _get_db
    _get_db = get_db_connection
    return suppliers_bp


def _get_connection():
    if _get_db is None:
        raise RuntimeError("Suppliers blueprint not initialized with database connection")
    return _get_db()


def _competition_stats(awards: list[dict[str, Any]]) -> tuple[int, int]:
    """(non-competitive awards, awards whose procurement route is known).

    is_competitive is NULL when the notice does not say. Those awards are neither competitive nor
    direct, so they are left out of both counts and out of the ratio's denominator; treating
    NULL as "direct award" overstated the direct-award share (10% of stored awards are NULL).
    """
    known = [a for a in awards if a.get("is_competitive") is not None]
    return sum(1 for a in known if not a.get("is_competitive")), len(known)


def _execute(cursor, conn, sql: str, params: tuple | list = ()):
    sql = sql.replace("?", "%s")
    cursor.execute(sql, params)
    return cursor


def _row_to_dict(cursor, row) -> dict[str, Any]:
    if row is None:
        return {}
    if hasattr(row, "keys"):
        return dict(row)
    if cursor and cursor.description:
        col_names = [col[0] for col in cursor.description]
        return dict(zip(col_names, row))
    return {}


def _norm_authority(name: str | None) -> str:
    # Notices for the same buyer are inconsistently cased/punctuated across sources ("NHS
    # Norfolk and Suffolk Integrated Care Board" vs "NHS NORFOLK AND SUFFOLK INTEGRATED CARE
    # BOARD" vs "...Integrated Care Board (ICB)") -- an exact-string dedup key misses these.
    # Matches buyers_bp's own authority normalisation.
    return re.sub(r"\s+", " ", (name or "").strip().upper())


# How many rows share this award's notice value (see SHARED_VALUE_N_SQL in etenders_scraper/awards.py): the same
# buyer, notice and identical value across several supplier rows is ONE published value. Counted per distinct
# supplier+date, as the de-duplication does, so a republished notice does not count twice. 1 when not shared.
_SHARED_N_SELECT = """(SELECT CASE WHEN COALESCE(contract_awards.is_framework, 0) = 0 AND contract_awards.contract_value > 0
                            AND COALESCE(contract_awards.notice_url, '') <> ''
                       THEN GREATEST(1, COUNT(DISTINCT COALESCE(NULLIF(sib.supplier_id, 0)::text, NULLIF(sib.company_number, ''), sib.supplier_name)
                                                       || '|' || COALESCE(sib.date_signed::text, 'id' || sib.id::text)))
                       ELSE 1 END
                FROM contract_awards sib
                WHERE sib.notice_url = contract_awards.notice_url AND sib.contract_value = contract_awards.contract_value
                  AND sib.authority_name = contract_awards.authority_name AND COALESCE(sib.is_framework, 0) = 0) AS shared_n"""


def _apportion_shared_values(award_dicts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Gives each row its share of a notice value that several supplier rows carry (rows come with `shared_n`)."""
    for a in award_dicts:
        n = int(a.get("shared_n") or 1)
        if n > 1 and a.get("contract_value") is not None and not a.get("is_framework"):
            a["contract_value_notice"] = a["contract_value"]
            a["contract_value"] = float(a["contract_value"]) / n
    return award_dicts


def _dedup_recurring_awards(award_dicts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapses recurring re-publications of the same ongoing contract to one row.

    Some buyers (NHS Integrated Care Boards in particular) re-publish an "Awarded contract"
    notice every time an ongoing multi-year block contract's budget is revised, rather than
    amending the original notice -- same buyer, same date_signed, near-identical title, each
    restating a similar multi-billion-pound figure. Summed as if separate wins, a handful of
    these can inflate a supplier's totals into the billions/trillions. Same buyer + same exact
    date_signed is a strong duplicate signal for genuinely distinct awards (which don't normally
    share both), so each such group collapses to its single highest-value row.
    """
    seen: dict[tuple[str, Any], dict[str, Any]] = {}
    deduped: list[dict[str, Any]] = []
    for a in award_dicts:
        date_signed = a.get("date_signed")
        if date_signed is None:  # no date to match on -- never collapse these
            deduped.append(a)
            continue
        key = (_norm_authority(a.get("authority_name")), date_signed)
        prev = seen.get(key)
        if prev is None:
            seen[key] = a
            deduped.append(a)
        elif (float(a.get("contract_value") or 0), -int(a.get("id") or 0)) > (float(prev.get("contract_value") or 0), -int(prev.get("id") or 0)):
            # highest value wins; on a tie the lowest id, as in the SQL de-duplication (etenders_scraper/awards.py)
            deduped[deduped.index(prev)] = a
            seen[key] = a
    return _apportion_shared_values(deduped)


def _is_valid_cnum_for_award_join(cnum: str | None) -> bool:
    if not cnum:
        return False
    c = str(cnum).strip()
    return bool(c and c not in ("Not available", "Procurem", "—", "-", "None") and not c.startswith("NO_REG_"))


_PORTALS_SQL_LIST = ", ".join(f"'{p}'" for p in UK_IE_SOURCE_PORTALS)

CF_SUPPLIER_EXISTS_SQL = f"""(
    EXISTS (
        SELECT 1 FROM contract_awards ca
        WHERE ca.source_portal IN ({_PORTALS_SQL_LIST})
          AND (
              ca.supplier_id = s.id
              OR (
                  s.company_number IS NOT NULL
                  AND s.company_number != ''
                  AND s.company_number NOT IN ('Not available', 'Procurem', '—', '-', 'None')
                  AND SUBSTR(s.company_number, 1, 7) != 'NO_REG_'
                  AND s.company_number = ca.company_number
              )
          )
    )
    AND LOWER(s.name) NOT LIKE '%see%attach%'
    AND LOWER(s.name) NOT LIKE '%refer%attach%'
    AND LOWER(s.name) NOT IN ('na', 'n/a', 'tbc', 'tbd', 'unknown', 'confidential', 'not disclosed', 'not available', 'withheld', 'redacted', 'to be confirmed', 'pending')
)"""



# Award notices sometimes omit the winner's name and leave placeholder text
# ("See attachment", "Various", "N/A", ...) in the field instead. Some already-ingested rows
# are also a buyer authority (NHS trust, ICB, council, police/fire authority, ...) that leaked
# into supplier_name from a delivery-partner/consortium field or a scraper mapping slip --
# looks_like_buyer_authority() in etenders_scraper/awards.py rejects these going forward at
# ingestion, but that guard doesn't retroactively touch rows already in the table (see
# scripts/audit_buyer_as_supplier.py). None of the above are real suppliers and shouldn't be
# listed, ranked or exported as one. Postgres's regex word-boundary escape is \y, not \b (\b is
# backspace here) -- kept in sync with awards.py's Python regex and with stats.py's _LISTABLE_SQL.
SUPPLIER_NAME_NOT_PLACEHOLDER_SQL = r"""(
    s.name !~* '(see|refer to)[a-z ,'']{0,45}attach'
    AND s.name !~* '^(na|n/a|tbc|tbd|unknown|confidential|not disclosed|not available|not named|withheld|redacted|to be confirmed|pending|none|supplier|contract|contract value|live scraped supplier|not applicable|not awarded|no award|award not made|please refer to weblink|refer to weblink|see website|please see website|withheld for security reasons)$'
    AND s.name !~* '(please see|please refer)'
    AND s.name !~* '^(as per|information withheld|awarded supplier details|confidential information|confidential / sensitive)'
    AND s.name !~* 'commercially confidential'
    AND TRIM(s.name) !~* '^various$'
    AND s.name !~* '\ynhs\b.*?\b(foundation\s+trust|trust|ft|icb|board)\y'
    AND s.name !~* '\ynhs\s+[a-z &'',]{0,40}(foundation\s+trust|trust|ft|icb)\y'
    AND s.name !~* '\yclinical\s+commissioning\s+group\y'
    AND s.name !~* '\yintegrated\s+care\s+board\y'
    AND s.name !~* '\yhealth\s+board\y'
    AND s.name !~* '\y(borough|city|county|district|parish|town)\s+council\y'
    AND s.name !~* '\ycombined\s+authority\y'
    AND s.name !~* '\ypolice\s+and\s+crime\s+commissioner\y'
    AND s.name !~* '\yconstabulary\y'
    AND s.name !~* '\yfire\s+and\s+rescue\s+(service|authority)\y'
    AND LENGTH(TRIM(s.name)) <= 120
    AND s.name !~ '\y[a-z]{2,}\.\s+[A-Z][a-z]{2,}'
    AND NOT (
        s.name ~* '^(will|for the|to|capabilities|framework per|provision and|implementation of|various|multiple providers|all successful bidders|all as per)(\s|$)'
        AND s.name !~* '\y(ltd|limited|llp|plc|inc|llc|cic|corp|co|gmbh|b\.?v\.?|s\.?a\.?|s\.?r\.?l\.?|s\.?l\.?u\.?|c\.?i\.?c\.?)\.?$'
        AND (
            s.name ~* '^(various|multiple providers|all successful bidders|all as per)(\s|$)'
            OR LENGTH(TRIM(s.name)) > 40
            OR s.name ~* '\y(provide|provides|providing|provided|supply|supplies|servicing|allow|allows|satisfy|satisfies|lead|covered|published|viewed|held|undertake)\y'
        )
    )
)"""


JOIN_SUPPLIER_AWARD_SQL = f"""(
    (
        s.id = a.supplier_id
        OR (
            s.company_number IS NOT NULL
            AND s.company_number != ''
            AND s.company_number NOT IN ('Not available', 'Procurem', '—', '-', 'None')
            AND SUBSTR(s.company_number, 1, 7) != 'NO_REG_'
            AND s.company_number = a.company_number
        )
    )
    AND a.source_portal IN ({_PORTALS_SQL_LIST})
)"""


def _count_real_suppliers(cursor, conn) -> int:
    """How many rows in `suppliers` would actually show up in the Supplier Intelligence list.

    `SELECT COUNT(*) FROM suppliers` also counts placeholder/buyer-leaked rows that
    SUPPLIER_NAME_NOT_PLACEHOLDER_SQL (and supplier_stats.listable, kept in sync with it) already
    exclude from every real listing -- using the raw count anywhere user-facing (header stat, a
    sync toast) overstates how many suppliers are actually browsable by exactly that gap.
    """
    from tender_app import stats
    try:
        if stats.is_ready(cursor, "supplier_stats"):
            _execute(cursor, conn, "SELECT COUNT(*) FROM supplier_stats WHERE listable;")
            return cursor.fetchone()[0]
    except Exception:
        pass
    _execute(cursor, conn, f"SELECT COUNT(*) FROM suppliers s WHERE {SUPPLIER_NAME_NOT_PLACEHOLDER_SQL};")
    return cursor.fetchone()[0]


@suppliers_bp.route("/api/suppliers/status", methods=["GET"])
def get_supplier_system_status():
    """Returns dynamic status of the database and background award scraper worker."""
    try:
        from etenders_scraper.awards import get_award_scheduler_status
        sched_status = get_award_scheduler_status()
    except Exception:
        sched_status = {"active": False, "last_run_at": None, "sources": {}}

    conn = _get_connection()
    cursor = conn.cursor()
    try:
        sup_count = _count_real_suppliers(cursor, conn)
        _execute(cursor, conn, f"SELECT COUNT(*) FROM contract_awards WHERE source_portal IN ({_PORTALS_SQL_LIST});")
        award_count = cursor.fetchone()[0]
        _execute(cursor, conn, f"SELECT source_portal, COUNT(*) FROM contract_awards WHERE source_portal IN ({_PORTALS_SQL_LIST}) GROUP BY source_portal;")
        by_portal = {row[0]: row[1] for row in cursor.fetchall()}
    except Exception:
        sup_count = 0
        award_count = 0
        by_portal = {}
    finally:
        conn.close()

    return jsonify({
        "status": "ok",
        "total_suppliers": sup_count,
        "total_awards": award_count,
        "awards_by_portal": by_portal,
        "scheduler": sched_status
    })


@suppliers_bp.route("/api/suppliers/sync-contracts-finder", methods=["GET", "POST"])
def sync_contracts_finder():
    """Trigger on-demand sync of awarded notices from Contracts Finder into the database."""
    try:
        from etenders_scraper.awards import scrape_live_contracts_finder_awards
        from tender_app.supplier_data_auditor import audit_and_clean_supplier_contacts
        limit = int(request.args.get("limit") or 50)
        conn = _get_connection()
        records = scrape_live_contracts_finder_awards(limit=limit, days_back=60, conn=conn)

        # Run automated data quality auditor on newly synced/updated suppliers
        cleaned_count = audit_and_clean_supplier_contacts(conn)

        cursor = conn.cursor()
        sup_count = _count_real_suppliers(cursor, conn)
        _execute(cursor, conn, f"SELECT COUNT(*) FROM contract_awards WHERE source_portal IN ({_PORTALS_SQL_LIST});")
        award_count = cursor.fetchone()[0]
        conn.close()

        return jsonify({
            "status": "ok",
            "new_awards_count": len(records),
            "total_suppliers": sup_count,
            "total_awards": award_count,
            "cleaned_contacts": cleaned_count,
            "source": "Contracts Finder & Find a Tender",
            "source_url": "https://www.contractsfinder.service.gov.uk/Search/Results"
        })
    except Exception as ex:
        return jsonify({
            "status": "error",
            "error": str(ex)
        }), 500


def _sync_bravo_portal(source_id: str, site_root: str, source_label: str, default_limit: int = 30):
    """Shared handler for the Sell2Wales / Public Contracts Scotland manual sync routes."""
    try:
        from etenders_scraper.awards import scrape_live_bravo_awards
        from tender_app.supplier_data_auditor import audit_and_clean_supplier_contacts
        limit = int(request.args.get("limit") or default_limit)
        conn = _get_connection()
        records = scrape_live_bravo_awards(source_id, site_root, source_label, limit=limit, conn=conn)

        cleaned_count = audit_and_clean_supplier_contacts(conn)

        cursor = conn.cursor()
        _execute(cursor, conn, "SELECT COUNT(*) FROM suppliers;")
        sup_count = cursor.fetchone()[0]
        _execute(cursor, conn, "SELECT COUNT(*) FROM contract_awards WHERE source_portal = ?;", (source_label,))
        award_count = cursor.fetchone()[0]
        conn.close()

        return jsonify({
            "status": "ok",
            "new_awards_count": len(records),
            "total_suppliers": sup_count,
            "total_awards": award_count,
            "cleaned_contacts": cleaned_count,
            "source": source_label,
            "source_url": f"{site_root}/Search/Search_MainPage.aspx"
        })
    except Exception as ex:
        return jsonify({
            "status": "error",
            "error": str(ex)
        }), 500


@suppliers_bp.route("/api/suppliers/sync-sell2wales", methods=["GET", "POST"])
def sync_sell2wales():
    """Trigger on-demand sync of awarded notices from Sell2Wales into the database."""
    return _sync_bravo_portal("sell2wales", "https://www.sell2wales.gov.wales", "Sell2Wales")


@suppliers_bp.route("/api/suppliers/sync-pcs", methods=["GET", "POST"])
def sync_pcs():
    """Trigger on-demand sync of awarded notices from Public Contracts Scotland into the database."""
    return _sync_bravo_portal("pcs", "https://www.publiccontractsscotland.gov.uk", "Public Contracts Scotland")


@suppliers_bp.route("/api/suppliers/audit-contacts", methods=["POST"])
def audit_supplier_contacts():
    """
    Run quick data quality auditor on all stored supplier contact records.
    Cleans cross-contaminated contact info where domain doesn't match company name.
    """
    try:
        from tender_app.supplier_data_auditor import audit_and_clean_supplier_contacts
        conn = _get_connection()
        cleaned_count = audit_and_clean_supplier_contacts(conn)
        
        cursor = conn.cursor()
        _execute(cursor, conn, "SELECT COUNT(*) FROM suppliers;")
        total_suppliers = cursor.fetchone()[0]
        conn.close()

        return jsonify({
            "ok": True,
            "cleaned_count": cleaned_count,
            "total_suppliers": total_suppliers,
            "message": f"Audited {total_suppliers} suppliers. Cleaned {cleaned_count} cross-contaminated contact records."
        })
    except Exception as ex:
        return jsonify({
            "ok": False,
            "error": str(ex)
        }), 500


@suppliers_bp.route("/api/suppliers/audit-comprehensive", methods=["POST"])
def audit_comprehensive():
    """
    Run COMPREHENSIVE supplier database audit with all validation checks:
    - Company number validity
    - Name-to-company-number consistency (Companies House API)
    - Contact info confidence validation
    - Industry/SIC code sanity checks
    - Duplicate record detection and merging
    
    Query params:
    - dry_run=true: Show what would be fixed without making changes
    - no_fix=true: Only audit, don't apply corrections
    """
    dry_run = request.args.get("dry_run", "false").lower() in ("true", "1", "yes")
    no_fix = request.args.get("no_fix", "false").lower() in ("true", "1", "yes")
    
    try:
        from tender_app.supplier_data_full_auditor import run_full_supplier_audit
        
        conn = _get_connection()
        
        # Run comprehensive audit
        report = run_full_supplier_audit(conn, apply_fixes=not no_fix, dry_run=dry_run)
        
        conn.close()
        
        return jsonify({
            "ok": True,
            "dry_run": dry_run,
            "fixes_applied": not no_fix,
            "audit_summary": {
                "total_suppliers": report.total_suppliers,
                "invalid_company_numbers": len(report.invalid_company_numbers),
                "name_number_mismatches": len(report.name_number_mismatches),
                "low_confidence_contacts": len(report.low_confidence_contacts),
                "industry_sic_mismatches": len(report.industry_sic_mismatches),
                "duplicate_records": len(report.duplicate_records),
                "companies_house_api_calls": report.ch_api_calls,
                "companies_house_api_failures": len(report.ch_api_failures)
            },
            "corrections": {
                "records_corrected": report.records_corrected,
                "records_blanked": report.records_blanked,
                "records_merged": report.records_merged
            },
            "message": f"Comprehensive audit complete. Found {len(report.invalid_company_numbers) + len(report.name_number_mismatches) + len(report.low_confidence_contacts) + len(report.industry_sic_mismatches) + len(report.duplicate_records)} total issues."
        })
    except Exception as ex:
        import traceback
        return jsonify({
            "ok": False,
            "error": str(ex),
            "traceback": traceback.format_exc()
        }), 500


_SUPPLIER_LIST_COLS = (
    "s.id, s.company_number, s.name, s.address, s.email, s.phone, s.website, s.region, "
    "s.sme_status, s.vcse_status, s.created_at, s.latitude, s.longitude, s.geo_accuracy"
)
_VALID_COMPANY_NUMBER_SQL = """(
    s.company_number IS NOT NULL AND s.company_number != ''
    AND s.company_number NOT IN ('Not available', 'Procurem', '—', '-', 'None')
    AND SUBSTR(s.company_number, 1, 7) != 'NO_REG_'
)"""


def _shape_supplier_rows(cursor, raw_rows, sme_only: bool, vcse_only: bool) -> list[dict[str, Any]]:
    """Turn result rows into the API's supplier dicts (classification + contact clean-up)."""
    suppliers = []
    for raw_r in raw_rows:
        r = _row_to_dict(cursor, raw_r)
        cname = r.get("name", "")
        cnum = r.get("company_number", "")
        sme_st, vcse_st, _ = classify_supplier_real_data(
            cname, cnum, scraped_sme=r.get("sme_status"), scraped_vcse=r.get("vcse_status")
        )
        # Strict filtering: exclude unclassified & non-SME / non-VCSE when those filters are active
        if sme_only and sme_st != "SME":
            continue
        if vcse_only and vcse_st != "VCSE":
            continue
        suppliers.append({
            "id": r.get("id"),
            "company_number": normalize_ch_company_number(cnum) or "Not available",
            "name": cname,
            "address": clean_contact_field(r.get("address")),
            "email": clean_contact_field(r.get("email")),
            "phone": clean_contact_field(r.get("phone")),
            "website": clean_contact_field(r.get("website")),
            "region": r.get("region", "UK"),
            "sme_status": sme_st,
            "vcse_status": vcse_st,
            "total_awards": r.get("total_awards", 0),
            # total_value (below) excludes framework ceilings -- see get_supplier_detail's matching
            # direct_awards_count. Pairing that value with the all-inclusive total_awards count made
            # the list-card and profile-panel "N won" figures disagree for suppliers with framework
            # appointments (same £ total, different N). direct_awards is the count that actually
            # contributed to total_value, so the two views now show the same figure.
            "direct_awards": max(0, int(r.get("total_awards") or 0) - int(r.get("framework_appointments_count") or 0)),
            "total_value": float(r.get("total_value") or 0),
            "avg_value": float(r.get("avg_value") or 0),
            "framework_total_ceiling": float(r.get("framework_total_ceiling") or 0),
            "framework_appointments_count": int(r.get("framework_appointments_count") or 0),
            "latitude": float(r["latitude"]) if r.get("latitude") is not None else None,
            "longitude": float(r["longitude"]) if r.get("longitude") is not None else None,
            "geo_accuracy": r.get("geo_accuracy"),
        })
    return suppliers


def _supplier_page_from_stats(cursor, conn, q_patterns, sme_only, vcse_only, only_awarded, limit, offset, geo=None,
                               exact_q: str | None = None):
    """The instant path: rank from the precomputed supplier_stats table (no award aggregation).

    Searches supplier fields only (name, company number, region); text that lives on the awards
    (buyer, tender title, CPV) is what the exact path adds.
    """
    where = ["st.listable"]
    params: list[Any] = []
    needs_supplier_row = False
    if q_patterns:
        q_pattern, q_clean_pattern = q_patterns
        where.append("(LOWER(s.name) LIKE LOWER(?) OR s.company_number LIKE ? OR LOWER(s.region) LIKE LOWER(?))")
        params += [q_clean_pattern, q_pattern, q_clean_pattern]
        needs_supplier_row = True
    if sme_only:
        where.append("s.sme_status = 'SME'")
        needs_supplier_row = True
    if vcse_only:
        where.append("s.vcse_status = 'VCSE'")
        needs_supplier_row = True
    if only_awarded:
        where.append("st.total_awards > 0")
    if geo:
        where.append(geo["where_sql"])
        params += geo["where_params"]
        needs_supplier_row = True
    where_sql = " AND ".join(where)

    if needs_supplier_row:
        count_sql = f"SELECT COUNT(*) FROM supplier_stats st JOIN suppliers s ON s.id = st.supplier_id WHERE {where_sql}"
    else:  # the default list: an index-only count, no join
        count_sql = f"SELECT COUNT(*) FROM supplier_stats st WHERE {where_sql}"
    _execute(cursor, conn, count_sql, params)
    total_count = cursor.fetchone()[0]

    # See _supplier_page_exact's matching comment: an exact or prefix name match must outrank
    # pure value/award-count ranking so deep links (and typed searches) resolve to the supplier
    # actually being looked up, not whichever match happens to have the biggest contracts.
    order_sql = "st.total_value DESC, st.total_awards DESC, st.supplier_id"
    if exact_q:
        order_sql = (
            "CASE WHEN LOWER(s.name) = LOWER(?) THEN 0 "
            "WHEN LOWER(s.name) LIKE LOWER(?) THEN 1 ELSE 2 END, " + order_sql
        )
        params.append(exact_q)
        params.append(exact_q + "%")

    sql = f"""
        SELECT {_SUPPLIER_LIST_COLS},
               st.total_awards, st.total_value, st.avg_value,
               st.framework_total_ceiling, st.framework_appointments_count
        FROM supplier_stats st JOIN suppliers s ON s.id = st.supplier_id
        WHERE {where_sql}
        ORDER BY {order_sql}
    """
    page_params = list(params)
    if limit is not None:
        sql += " LIMIT ?" + (" OFFSET ?" if offset is not None else "")
        page_params.append(limit)
        if offset is not None:
            page_params.append(offset)
    _execute(cursor, conn, sql, page_params)
    return cursor.fetchall(), total_count


def _supplier_page_exact(cursor, conn, q_patterns, sme_only, vcse_only, only_awarded,
                         start_date, end_date, limit, offset, use_listable: bool = False, geo=None,
                         exact_q: str | None = None, widen_match: bool = False):
    """The exact path: aggregate live, but only over the suppliers that can match.

    Awards reach a supplier by supplier_id or company number. The old query joined every supplier
    to every award through `id = supplier_id OR company_number = company_number`; an OR join can
    only be a nested loop (about 25 s). Two hash joins over just the matched suppliers give the
    same links in about a second.

    widen_match controls what counts as a match: False (default, plain typed search) only matches
    the supplier's own identity (name/company number/region, or an award recorded under a slightly
    different spelling of their name). True additionally matches via the buyer/tender-title/CPV
    text on their awards -- a real, separate "find suppliers who've worked with this buyer" feature
    (see /api/company-profiles callers passing widen=1), but applying it unconditionally to normal
    typing meant a search like "GRAHAM" or a buyer's name could jump from a handful of results to
    hundreds with no indication why, which looked exactly like the search reverting to something
    else.
    """
    params: list[Any] = []
    ctes: list[str] = [DEDUPED_AWARDS_CTE_SQL]

    if q_patterns:
        q_pattern, q_clean_pattern = q_patterns
        if widen_match:
            ctes.append(f"""matched AS (
                SELECT s.id FROM suppliers s
                WHERE LOWER(s.name) LIKE LOWER(?) OR s.company_number LIKE ? OR LOWER(s.region) LIKE LOWER(?)
                UNION
                SELECT a.supplier_id FROM contract_awards a
                WHERE a.supplier_id IS NOT NULL AND a.source_portal IN ({_PORTALS_SQL_LIST})
                  AND (LOWER(a.supplier_name) LIKE LOWER(?) OR LOWER(a.authority_name) LIKE LOWER(?)
                       OR LOWER(a.tender_title) LIKE LOWER(?) OR LOWER(a.cpv_description) LIKE LOWER(?)
                       OR a.cpv_code LIKE ?)
            )""")
            params += [q_clean_pattern, q_pattern, q_clean_pattern, q_clean_pattern, q_pattern, q_pattern, q_pattern, q_pattern]
        else:
            ctes.append(f"""matched AS (
                SELECT s.id FROM suppliers s
                WHERE LOWER(s.name) LIKE LOWER(?) OR s.company_number LIKE ? OR LOWER(s.region) LIKE LOWER(?)
                UNION
                SELECT a.supplier_id FROM contract_awards a
                WHERE a.supplier_id IS NOT NULL AND a.source_portal IN ({_PORTALS_SQL_LIST})
                  AND LOWER(a.supplier_name) LIKE LOWER(?)
            )""")
            params += [q_clean_pattern, q_pattern, q_clean_pattern, q_clean_pattern]

    def date_sql() -> tuple[str, list[Any]]:
        conds, vals = [], []
        if start_date:
            conds.append("a.date_signed >= ?")
            vals.append(start_date)
        if end_date:
            conds.append("a.date_signed <= ?")
            vals.append(end_date)
        return ("".join(f" AND {c}" for c in conds)), vals

    d_sql, d_vals = date_sql()
    in_matched_a = " AND a.supplier_id IN (SELECT id FROM matched)" if q_patterns else ""
    in_matched_s = " AND s.id IN (SELECT id FROM matched)" if q_patterns else ""
    ctes.append(f"""links AS (
        SELECT a.supplier_id AS sid, a.id AS aid, a.contract_value, a.is_framework
        FROM deduped_awards a
        WHERE a.supplier_id IS NOT NULL AND a.source_portal IN ({_PORTALS_SQL_LIST}){d_sql}{in_matched_a}
        UNION ALL
        SELECT s.id, a.id, a.contract_value, a.is_framework
        FROM suppliers s JOIN deduped_awards a ON a.company_number = s.company_number
        WHERE {_VALID_COMPANY_NUMBER_SQL} AND a.supplier_id IS DISTINCT FROM s.id
          AND a.source_portal IN ({_PORTALS_SQL_LIST}){d_sql}{in_matched_s}
    )""")
    params += d_vals + d_vals

    # Aggregate the links first (a small table keyed by an integer), then attach supplier rows. Grouping
    # the wide supplier row set instead is what made date-filtered searches take seconds.
    ctes.append(f"""agg AS (
        SELECT sid,
               COUNT(aid) AS total_awards,
               COALESCE(SUM(CASE WHEN (is_framework IS NULL OR is_framework = 0) AND contract_value <= {SINGLE_AWARD_STATS_CEILING_GBP} THEN contract_value ELSE 0 END), 0) AS total_value,
               COALESCE(AVG(CASE WHEN (is_framework IS NULL OR is_framework = 0) AND contract_value <= {SINGLE_AWARD_STATS_CEILING_GBP} THEN contract_value END), 0) AS avg_value,
               COALESCE(SUM(CASE WHEN is_framework = 1 THEN contract_value ELSE 0 END), 0) AS framework_total_ceiling,
               COUNT(CASE WHEN is_framework = 1 THEN 1 END) AS framework_appointments_count
        FROM links GROUP BY sid
    )""")

    # The placeholder-name regexes take ~6 s over all suppliers; supplier_stats already holds that
    # verdict per supplier (listable), so use it whenever the stats exist.
    where = ["st.listable" if use_listable else SUPPLIER_NAME_NOT_PLACEHOLDER_SQL]
    if sme_only:
        where.append("s.sme_status = 'SME'")
    if vcse_only:
        where.append("s.vcse_status = 'VCSE'")
    if geo:
        where.append(geo["where_sql"])
        params += geo["where_params"]
    join_matched = (" JOIN matched m ON m.id = s.id" if q_patterns else "") + (
        " JOIN supplier_stats st ON st.supplier_id = s.id" if use_listable else "")
    only_with_awards = bool(start_date or end_date or only_awarded)
    agg_join = "JOIN" if only_with_awards else "LEFT JOIN"

    # An exact or prefix name match (a deep link, or someone typing a supplier's actual name)
    # should rank ahead of pure value/award-count ranking. Without the prefix tier, a bigger,
    # merely-similarly-named supplier (e.g. "John Graham Construction Ltd, t/a GRAHAM") or --
    # since this query also matches suppliers via the buyer/tender-title text on their awards
    # (see the `matched` CTE above) -- any big supplier that just happens to have won a contract
    # from a buyer or tender whose name contains the search text, outranks or buries the supplier
    # actually being looked up (e.g. "GRAHAM", or "Ian Williams Limited" for a search of "ian").
    order_sql = "total_value DESC, total_awards DESC, s.id"
    if exact_q:
        order_sql = (
            "CASE WHEN LOWER(s.name) = LOWER(?) THEN 0 "
            "WHEN LOWER(s.name) LIKE LOWER(?) THEN 1 ELSE 2 END, " + order_sql
        )
        params.append(exact_q)
        params.append(exact_q + "%")

    sql = f"""
        WITH {", ".join(ctes)}
        SELECT {_SUPPLIER_LIST_COLS},
               COALESCE(g.total_awards, 0) AS total_awards,
               COALESCE(g.total_value, 0) AS total_value,
               COALESCE(g.avg_value, 0) AS avg_value,
               COALESCE(g.framework_total_ceiling, 0) AS framework_total_ceiling,
               COALESCE(g.framework_appointments_count, 0) AS framework_appointments_count,
               COUNT(*) OVER () AS _total
        FROM suppliers s{join_matched}
        {agg_join} agg g ON g.sid = s.id
        WHERE {" AND ".join(where)}
        ORDER BY {order_sql}
    """
    if limit is not None:
        sql += " LIMIT ?" + (" OFFSET ?" if offset is not None else "")
        params.append(limit)
        if offset is not None:
            params.append(offset)
    _execute(cursor, conn, sql, params)
    rows = cursor.fetchall()
    if rows:
        total_idx = [d[0] for d in cursor.description].index("_total")
        total_count = rows[0][total_idx]
    else:
        total_count = 0
    return rows, total_count


@suppliers_bp.route("/api/suppliers/search", methods=["GET"])
def search_suppliers():
    """Search suppliers by company name or Companies House number, with optional date range.

    Two speeds, same response shape:
      - stats path (default list, or fast=1): ranks from the precomputed supplier_stats table in
        tens of milliseconds. Exact when there is no search text and no date range; otherwise the
        response has approximate=true (supplier-name matches only, all-time totals) and the client
        follows up without fast=1 to get the exact answer in the background.
      - exact path: aggregates live over the matching suppliers only.
    """
    q = (request.args.get("q") or "").strip()
    sme_only = request.args.get("sme", "").lower() in ("1", "true", "yes")
    vcse_only = request.args.get("vcse", "").lower() in ("1", "true", "yes")
    fetch_all = request.args.get("all", "").lower() in ("1", "true", "yes")
    fast = request.args.get("fast", "").lower() in ("1", "true", "yes")
    # Opt-in only: "Find suppliers who win with X" explicitly asks to widen a search from
    # supplier identity to "every supplier this buyer/tender/CPV text has ever been linked to"
    # (see widen_match in _supplier_page_exact). A plain typed search never sends this.
    widen_match = request.args.get("widen", "").lower() in ("1", "true", "yes")

    date_preset = (request.args.get("date_range") or request.args.get("years") or request.args.get("preset") or "all").lower().strip()
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")

    from datetime import datetime, timedelta
    years = {"1y": 1, "2y": 2, "3y": 3, "4y": 4, "5y": 5}.get(date_preset)
    if years:
        start_date = (datetime.utcnow() - timedelta(days=365 * years)).strftime("%Y-%m-%d")

    # Pagination parameters
    page = max(1, int(request.args.get("page", 1)))
    per_page = min(max(int(request.args.get("per_page", 25)), 1), 100)  # selector values: 25, 50, 100
    limit_param = request.args.get("limit")
    if fetch_all or limit_param == "0":
        limit, offset = None, None
    elif limit_param:
        limit, offset = max(1, int(limit_param)), None
    else:
        limit, offset = per_page, (page - 1) * per_page

    only_awarded = request.args.get("only_awarded", "false").lower() in ("true", "1", "yes")

    # Proximity filter (same lat/lng/radius_km convention as /api/planning/search) — optional,
    # and only applied when both lat and lng are given.
    geo = None
    lat_param, lng_param = request.args.get("lat"), request.args.get("lng")
    if lat_param is not None and lng_param is not None:
        try:
            lat, lng = float(lat_param), float(lng_param)
            radius_km = float(request.args.get("radius_km") or 25.0)
        except ValueError:
            return jsonify({"ok": False, "error": "lat, lng and radius_km must be numbers"}), 400
        if radius_km > 0:
            geo = geo_radius_clause(lat, lng, radius_km, lat_col="s.latitude", lon_col="s.longitude")

    q_patterns = None
    if q:
        import re
        q_clean = re.sub(r"\b(ltd|limited|plc|llp|dac|inc|corp|co)\b|\.", "", q, flags=re.IGNORECASE).strip()
        if len(q_clean) < 2:
            q_clean = q
        q_patterns = (f"%{q}%", f"%{q_clean}%")

    conn = _get_connection()
    cursor = conn.cursor()
    try:
        from tender_app import stats
        stats.ensure_fresh_async(_get_connection)  # background; never blocks this request

        has_dates = bool(start_date or end_date)
        can_use_stats = limit is not None and stats.is_ready(cursor, "supplier_stats")
        use_stats = can_use_stats and (fast or not (q or has_dates))
        approximate = use_stats and bool(q or has_dates)

        if use_stats:
            raw_rows, total_count = _supplier_page_from_stats(
                cursor, conn, q_patterns, sme_only, vcse_only, only_awarded, limit, offset, geo=geo, exact_q=q or None)
        else:
            raw_rows, total_count = _supplier_page_exact(
                cursor, conn, q_patterns, sme_only, vcse_only, only_awarded, start_date, end_date, limit, offset,
                use_listable=stats.is_ready(cursor, "supplier_stats"), geo=geo, exact_q=q or None,
                widen_match=widen_match)

        suppliers = _shape_supplier_rows(cursor, raw_rows, sme_only, vcse_only)
        return jsonify({
            "ok": True,
            "query": q,
            "suppliers": suppliers,
            "total": len(suppliers),
            "total_count": total_count,  # Total matching suppliers (all pages)
            "page": page,
            "per_page": per_page,
            "total_pages": (total_count + per_page - 1) // per_page if per_page > 0 else 1,
            "fast": use_stats,
            "approximate": approximate,
            "attribution": OGL_V3_ATTRIBUTION,
        })
    except Exception as ex:
        import traceback
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(ex)}), 500
    finally:
        conn.close()


# Enough pins to see the pattern across the whole (unpaginated) match set, few enough to render
# instantly -- mirrors MAP_MAX_POINTS in planning_bp.
SUPPLIER_MAP_MAX_POINTS = 500


@suppliers_bp.route("/api/suppliers/map", methods=["GET"])
def suppliers_map():
    """Geocoded suppliers for the current filters, independent of the list's own pagination.

    The list view (see search_suppliers above) only ever returns one page (default 25) of
    whichever suppliers match, ranked by contract value -- reusing that page for the map meant
    it plotted nothing whenever none of that particular page's suppliers happened to have a
    geocoded address, even though thousands of others elsewhere in the full result set did. This
    queries supplier_stats directly (the same "fast" path search_suppliers uses for its default,
    unfiltered list) with an explicit "has coordinates" filter, so the map always reflects the
    true match set rather than one arbitrary page of it.
    """
    q = (request.args.get("q") or "").strip()
    sme_only = request.args.get("sme", "").lower() in ("1", "true", "yes")
    vcse_only = request.args.get("vcse", "").lower() in ("1", "true", "yes")

    q_patterns = None
    if q:
        q_clean = re.sub(r"\b(ltd|limited|plc|llp|dac|inc|corp|co)\b|\.", "", q, flags=re.IGNORECASE).strip()
        if len(q_clean) < 2:
            q_clean = q
        q_patterns = (f"%{q}%", f"%{q_clean}%")

    geo = None
    lat_param, lng_param = request.args.get("lat"), request.args.get("lng")
    if lat_param is not None and lng_param is not None:
        try:
            lat, lng = float(lat_param), float(lng_param)
            radius_km = float(request.args.get("radius_km") or 25.0)
        except ValueError:
            return jsonify({"error": "lat, lng and radius_km must be numbers"}), 400
        if radius_km > 0:
            geo = geo_radius_clause(lat, lng, radius_km, lat_col="s.latitude", lon_col="s.longitude")

    conn = _get_connection()
    cursor = conn.cursor()
    try:
        where = ["st.listable", "s.latitude IS NOT NULL", "s.longitude IS NOT NULL"]
        params: list[Any] = []
        if q_patterns:
            q_pattern, q_clean_pattern = q_patterns
            where.append("(LOWER(s.name) LIKE LOWER(?) OR s.company_number LIKE ? OR LOWER(s.region) LIKE LOWER(?))")
            params += [q_clean_pattern, q_pattern, q_clean_pattern]
        if sme_only:
            where.append("s.sme_status = 'SME'")
        if vcse_only:
            where.append("s.vcse_status = 'VCSE'")
        if geo:
            where.append(geo["where_sql"])
            params += geo["where_params"]
        where_sql = " AND ".join(where)

        count_sql = f"SELECT COUNT(*) FROM supplier_stats st JOIN suppliers s ON s.id = st.supplier_id WHERE {where_sql}"
        _execute(cursor, conn, count_sql, params)
        total_with_location = cursor.fetchone()[0]

        sql = f"""
            SELECT {_SUPPLIER_LIST_COLS},
                   st.total_awards, st.total_value, st.avg_value,
                   st.framework_total_ceiling, st.framework_appointments_count
            FROM supplier_stats st JOIN suppliers s ON s.id = st.supplier_id
            WHERE {where_sql}
            ORDER BY st.total_value DESC, st.total_awards DESC, st.supplier_id
            LIMIT ?
        """
        _execute(cursor, conn, sql, params + [SUPPLIER_MAP_MAX_POINTS])
        raw_rows = cursor.fetchall()
        suppliers = _shape_supplier_rows(cursor, raw_rows, sme_only, vcse_only)
        return jsonify({
            "ok": True,
            "suppliers": suppliers,
            "shown": len(suppliers),
            "total_with_location": total_with_location,
            "capped": total_with_location > len(suppliers),
            "max_points": SUPPLIER_MAP_MAX_POINTS,
        })
    except Exception as ex:
        import traceback
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(ex)}), 500
    finally:
        conn.close()


@suppliers_bp.route("/api/suppliers", methods=["GET"])
@suppliers_bp.route("/api/suppliers/all", methods=["GET"])
def get_all_suppliers():
    """Fetch all supplier records without pagination limits."""
    conn = _get_connection()
    cursor = conn.cursor()

    try:
        sql = f"""
            WITH {DEDUPED_AWARDS_CTE_SQL}
            SELECT s.id, s.company_number, s.name, s.address, s.email, s.phone, s.website, s.region, s.sme_status, s.vcse_status, s.created_at,
                   COUNT(a.id) as total_awards,
                   COALESCE(SUM(CASE WHEN (a.is_framework IS NULL OR a.is_framework = 0) AND a.contract_value <= {SINGLE_AWARD_STATS_CEILING_GBP} THEN a.contract_value ELSE 0 END), 0) as total_value,
                   COALESCE(AVG(CASE WHEN (a.is_framework IS NULL OR a.is_framework = 0) AND a.contract_value <= {SINGLE_AWARD_STATS_CEILING_GBP} THEN a.contract_value ELSE NULL END), 0) as avg_value,
                   COALESCE(SUM(CASE WHEN a.is_framework = 1 THEN a.contract_value ELSE 0 END), 0) as framework_total_ceiling,
                   COUNT(CASE WHEN a.is_framework = 1 THEN 1 ELSE NULL END) as framework_appointments_count
            FROM suppliers s
            LEFT JOIN deduped_awards a ON {JOIN_SUPPLIER_AWARD_SQL}
            WHERE {CF_SUPPLIER_EXISTS_SQL} AND {SUPPLIER_NAME_NOT_PLACEHOLDER_SQL}
            GROUP BY s.id, s.company_number, s.name, s.address, s.email, s.phone, s.website, s.region, s.sme_status, s.vcse_status, s.created_at
            ORDER BY total_value DESC, total_awards DESC;
        """
        _execute(cursor, conn, sql)
        raw_rows = cursor.fetchall()

        suppliers = []
        for raw_r in raw_rows:
            r = _row_to_dict(cursor, raw_r)
            cname = r.get("name", "")
            cnum = r.get("company_number", "")
            raw_sme = r.get("sme_status")
            raw_vcse = r.get("vcse_status")

            sme_st, vcse_st, _ = classify_supplier_real_data(
                cname, cnum, scraped_sme=raw_sme, scraped_vcse=raw_vcse
            )

            cnum_valid = normalize_ch_company_number(cnum)
            suppliers.append({
                "id": r.get("id"),
                "company_number": cnum_valid or "Not available",
                "name": cname,
                "address": clean_contact_field(r.get("address")),
                "email": clean_contact_field(r.get("email")),
                "phone": clean_contact_field(r.get("phone")),
                "website": clean_contact_field(r.get("website")),
                "region": r.get("region", "UK"),
                "sme_status": sme_st,
                "vcse_status": vcse_st,
                "total_awards": r.get("total_awards", 0),
                "total_value": float(r.get("total_value") or 0),
                "avg_value": float(r.get("avg_value") or 0),
                "framework_total_ceiling": float(r.get("framework_total_ceiling") or 0),
                "framework_appointments_count": int(r.get("framework_appointments_count") or 0)
            })

        return jsonify({
            "ok": True,
            "suppliers": suppliers,
            "total": len(suppliers),
            "attribution": OGL_V3_ATTRIBUTION
        })
    except Exception as ex:
        return jsonify({"ok": False, "error": str(ex)}), 500
    finally:
        conn.close()


# supplier id -> (looked_up_at, result). result == {} means "looked up, nothing confident found", which is
# remembered too so a miss is not re-scraped (up to 8 s) every time the supplier is opened.
_CH_LOOKUP_CACHE: dict[int, tuple[float, dict[str, Any]]] = {}
_CH_LOOKUP_TTL_S = 24 * 3600


def _companies_house_lookup(cursor, conn, sup_row: dict[str, Any], allow_network: bool = True) -> dict[str, Any]:
    """Find a supplier's Companies House record by name. Returns {} if none/unconfident.

    Cached per supplier (hits and misses). With allow_network=False only the cache is consulted.
    On a confident match the supplier's company_number is saved, as before.
    """
    import time as _time
    sup_id = sup_row.get("id")
    name = sup_row.get("name")
    cached = _CH_LOOKUP_CACHE.get(sup_id)
    if cached and _time.time() - cached[0] < _CH_LOOKUP_TTL_S:
        return cached[1]
    # Skip the live Companies House scrape for known large enterprises (Skanska, Kier, etc.) - they
    # are classified via the safety-net registry, so the scrape buys nothing but latency.
    if not allow_network or not name or is_known_large_enterprise(name, sup_row.get("company_number")):
        return {}

    result: dict[str, Any] = {}
    import concurrent.futures
    # Bound the scrape to a hard wall-clock deadline: the sequential HTTP calls inside
    # _fetch_ch_filings_and_officers each have their own timeout but nothing capped the total.
    _ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        ch = _ex.submit(_fetch_ch_filings_and_officers, None, name).result(timeout=CH_ENRICH_DEADLINE_SECS)
        if ch and ch.get("company_number") and ch.get("match_confidence", 0) >= 0.85:
            found_reg = normalize_ch_company_number(ch["company_number"])
            if found_reg:
                result = {
                    "company_number": found_reg,
                    "profile_url": ch.get("profile_url", ""),
                    "status": ch.get("status", ""),
                    "officers": ch.get("officers", []),
                }
                try:
                    _execute(cursor, conn, "UPDATE suppliers SET company_number = ? WHERE id = ?;", (found_reg, sup_id))
                    conn.commit()
                except Exception:
                    # e.g. found_reg already belongs to another supplier (UNIQUE): roll back, or Postgres
                    # leaves the transaction aborted and every later query on this connection fails.
                    conn.rollback()
    except Exception:
        pass  # timeout or network error: treat as "not found" for now
    finally:
        _ex.shutdown(wait=False)
    _CH_LOOKUP_CACHE[sup_id] = (_time.time(), result)
    return result


@suppliers_bp.route("/api/suppliers/<int:supplier_id>/companies-house", methods=["GET"])
def get_supplier_companies_house(supplier_id: int):
    """Background enrichment for the supplier panel: the Companies House lookup on its own, so the
    panel itself never waits for it. {found: false} when nothing confident matched."""
    conn = _get_connection()
    cursor = conn.cursor()
    try:
        _execute(cursor, conn, "SELECT * FROM suppliers WHERE id = ?;", (supplier_id,))
        raw = cursor.fetchone()
        if not raw:
            return jsonify({"ok": False, "error": "Supplier not found"}), 404
        sup_row = _row_to_dict(cursor, raw)
        if normalize_ch_company_number(sup_row.get("company_number")):
            return jsonify({"ok": True, "found": False, "already_verified": True})
        found = _companies_house_lookup(cursor, conn, sup_row, allow_network=True)
        return jsonify({"ok": True, "found": bool(found), **({"company_number": found["company_number"]} if found else {})})
    except Exception as ex:
        return jsonify({"ok": False, "error": str(ex)}), 500
    finally:
        conn.close()


@suppliers_bp.route("/api/suppliers/<identifier>", methods=["GET"])
def get_supplier_detail(identifier: str):
    """Fetch complete supplier profile and aggregated intelligence metrics with date range filtering."""
    date_preset = (request.args.get("date_range") or request.args.get("years") or request.args.get("preset") or "all").lower().strip()
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")

    from datetime import datetime, timedelta
    if date_preset == "1y":
        start_date = (datetime.utcnow() - timedelta(days=365)).strftime("%Y-%m-%d")
    elif date_preset == "2y":
        start_date = (datetime.utcnow() - timedelta(days=365*2)).strftime("%Y-%m-%d")
    elif date_preset == "3y":
        start_date = (datetime.utcnow() - timedelta(days=365*3)).strftime("%Y-%m-%d")
    elif date_preset == "4y":
        start_date = (datetime.utcnow() - timedelta(days=365*4)).strftime("%Y-%m-%d")
    elif date_preset == "5y":
        start_date = (datetime.utcnow() - timedelta(days=365*5)).strftime("%Y-%m-%d")

    conn = _get_connection()
    cursor = conn.cursor()

    try:
        # Find supplier by ID or company number
        if identifier.isdigit():
            _execute(cursor, conn, "SELECT * FROM suppliers WHERE id = ? OR company_number = ?;", (int(identifier), identifier))
        else:
            _execute(cursor, conn, "SELECT * FROM suppliers WHERE company_number = ? OR LOWER(name) LIKE LOWER(?);", (identifier, f"%{identifier}%"))

        raw_sup = cursor.fetchone()
        if not raw_sup:
            return jsonify({"ok": False, "error": "Supplier not found"}), 404

        sup_row = _row_to_dict(cursor, raw_sup)
        sup_id = sup_row.get("id")
        cnum = sup_row.get("company_number")

        # P2 FIX: If we matched by name (non-integer identifier), apply a confidence
        # threshold before exposing contact fields — prevents cross-company data leakage.
        name_match_confident = True
        if not identifier.isdigit():
            from tender_app.ch_matcher import calculate_name_similarity
            sim = calculate_name_similarity(identifier, sup_row.get("name", ""))
            name_match_confident = sim >= 0.85

        sme_st, vcse_st, _ = classify_supplier_real_data(
            sup_row.get("name"), cnum,
            scraped_sme=sup_row.get("sme_status"),
            scraped_vcse=sup_row.get("vcse_status")
        )

        cnum_valid = normalize_ch_company_number(cnum)
        ch_profile_url = ""
        ch_status = ""
        ch_officers = []
        if cnum_valid:
            ch_profile_url = f"https://find-and-update.company-information.service.gov.uk/company/{cnum_valid}"
        else:
            # No valid Companies House number stored. The live lookup (up to CH_ENRICH_DEADLINE_SECS,
            # every time) used to run right here and held the whole panel back. The UI now asks for
            # enrich=0, gets this response from the database at once, and requests the lookup
            # separately (GET /api/suppliers/<id>/companies-house). Anything already looked up is
            # merged in from the cache; other callers that don't pass enrich=0 keep the old behaviour.
            found = _companies_house_lookup(cursor, conn, sup_row, allow_network=request.args.get("enrich", "1") != "0")
            if found:
                cnum_valid = found["company_number"]
                ch_profile_url = found.get("profile_url", "")
                ch_status = found.get("status", "")
                ch_officers = found.get("officers", [])

        supplier_info = {
            "id": sup_row.get("id"),
            "company_number": cnum_valid or "",
            "ch_profile_url": ch_profile_url,
            "ch_status": ch_status,
            "officers": ch_officers,
            "name": sup_row.get("name"),
            # Only expose contact details when match confidence is high enough
            "address": clean_contact_field(sup_row.get("address")) if name_match_confident else "",
            "email":   clean_contact_field(sup_row.get("email"))   if name_match_confident else "",
            "phone":   clean_contact_field(sup_row.get("phone"))   if name_match_confident else "",
            "website": clean_contact_field(sup_row.get("website")) if name_match_confident else "",
            "region": sup_row.get("region") or "UK",
            "sme_status": sme_st,
            "vcse_status": vcse_st,
            "created_at": str(sup_row.get("created_at", ""))
        }

        # Contract awards list with date filtering (scoped to the 6 real UK/IE portals)
        if _is_valid_cnum_for_award_join(cnum):
            award_where = ["(supplier_id = ? OR company_number = ?)", f"source_portal IN ({_PORTALS_SQL_LIST})"]
            award_params = [sup_id, cnum]
        else:
            award_where = ["supplier_id = ?", f"source_portal IN ({_PORTALS_SQL_LIST})"]
            award_params = [sup_id]
        if start_date:
            award_where.append("date_signed >= ?")
            award_params.append(start_date)
        if end_date:
            award_where.append("date_signed <= ?")
            award_params.append(end_date)

        award_sql = f"""
            SELECT contract_awards.*, {_SHARED_N_SELECT} FROM contract_awards
            WHERE {" AND ".join(award_where)}
            ORDER BY date_signed DESC NULLS LAST;
        """
        _execute(cursor, conn, award_sql, award_params)
        raw_award_rows = _dedup_recurring_awards([_row_to_dict(cursor, r) for r in cursor.fetchall()])

        awards = []
        total_value = 0.0
        framework_total_ceiling = 0.0
        framework_appointments_count = 0
        direct_awards_count = 0
        competitive_count = 0
        non_competitive_count = 0
        unknown_route_count = 0
        authority_stats: dict[str, dict[str, Any]] = {}
        cpv_stats: dict[str, dict[str, Any]] = {}
        procurement_types: dict[str, int] = {}

        for raw_a in raw_award_rows:
            a = _row_to_dict(cursor, raw_a)
            val = float(a.get("contract_value") or 0)
            is_fw = bool(a.get("is_framework"))
            # A single non-framework award above SINGLE_AWARD_STATS_CEILING_GBP is untrustworthy
            # scraper/source noise (see the constant's docstring in etenders_scraper/awards.py) --
            # excluded from every direct-value sum below, same as stats.py's precomputed path, but
            # still counted in the overall award/contract total (len(awards) below).
            over_ceiling = (not is_fw) and val > SINGLE_AWARD_STATS_CEILING_GBP
            if is_fw:
                framework_appointments_count += 1
                framework_total_ceiling += val
            elif not over_ceiling:
                direct_awards_count += 1
                total_value += val

            comp_flag = a.get("is_competitive")
            if comp_flag is None:
                unknown_route_count += 1
            elif comp_flag:
                competitive_count += 1
            else:
                non_competitive_count += 1

            # Authority breakdown
            auth = a.get("authority_name") or "Unknown Authority"
            if auth not in authority_stats:
                authority_stats[auth] = {"authority_name": auth, "count": 0, "total_value": 0.0}
            authority_stats[auth]["count"] += 1
            if not is_fw and not over_ceiling:
                authority_stats[auth]["total_value"] += val

            # CPV breakdown
            cpv = a.get("cpv_code") or "General"
            cpv_desc = a.get("cpv_description") or ""
            if cpv not in cpv_stats:
                cpv_stats[cpv] = {"cpv_code": cpv, "cpv_description": cpv_desc, "count": 0, "total_value": 0.0}
            cpv_stats[cpv]["count"] += 1
            if not is_fw and not over_ceiling:
                cpv_stats[cpv]["total_value"] += val

            # Procurement type
            ptype = a.get("procurement_type") or "Standard"
            procurement_types[ptype] = procurement_types.get(ptype, 0) + 1

            awards.append({
                "id": a.get("id"),
                "authority_name": a.get("authority_name"),
                "tender_title": a.get("tender_title"),
                "cpv_code": a.get("cpv_code"),
                "cpv_description": a.get("cpv_description"),
                "contract_value": val,
                "currency": a.get("currency") or "GBP",
                "date_signed": str(a.get("date_signed") or ""),
                "contract_duration": a.get("contract_duration"),
                "procurement_type": a.get("procurement_type"),
                "is_competitive": None if comp_flag is None else bool(comp_flag),  # None = the notice does not say
                "is_framework": is_fw,
                "notice_type": a.get("notice_type"),
                "source_portal": a.get("source_portal"),
                "notice_url": a.get("notice_url")
            })

        total_awards = len(awards)
        avg_value = total_value / direct_awards_count if direct_awards_count > 0 else 0.0

        top_authorities = sorted(authority_stats.values(), key=lambda x: (x["count"], x["total_value"]), reverse=True)
        top_cpvs = sorted(cpv_stats.values(), key=lambda x: (x["count"], x["total_value"]), reverse=True)

        return jsonify({
            "ok": True,
            "supplier": supplier_info,
            "date_range": date_preset,
            "start_date": start_date,
            "end_date": end_date,
            "metrics": {
                "total_awards": total_awards,
                "direct_awards": direct_awards_count,
                "total_value": total_value,
                "avg_value": avg_value,
                "framework_appointments": framework_appointments_count,
                "framework_total_ceiling": framework_total_ceiling,
                "competitive_awards": competitive_count,
                "non_competitive_awards": non_competitive_count,
                "non_competitive_ratio": round((non_competitive_count / (competitive_count + non_competitive_count) * 100), 1) if (competitive_count + non_competitive_count) > 0 else 0.0,
                "unknown_route_awards": unknown_route_count,
                "top_authorities": top_authorities,
                "cpv_sectors": top_cpvs,
                "procurement_types": procurement_types
            },
            "awards": awards,
            "attribution": OGL_V3_ATTRIBUTION
        })
    except Exception as ex:
        return jsonify({"ok": False, "error": str(ex)}), 500
    finally:
        conn.close()


@suppliers_bp.route("/api/suppliers/<identifier>/summary", methods=["GET"])
def get_supplier_executive_summary(identifier: str):
    """Generate structured AI Executive Summary and extract key supplier & contract information."""
    conn = _get_connection()
    cursor = conn.cursor()

    try:
        if identifier.isdigit():
            _execute(cursor, conn, "SELECT * FROM suppliers WHERE id = ? OR company_number = ?;", (int(identifier), identifier))
        else:
            _execute(cursor, conn, """
                SELECT * FROM suppliers WHERE company_number = ? OR LOWER(name) LIKE LOWER(?)
                ORDER BY CASE WHEN LOWER(name) = LOWER(?) THEN 0 ELSE 1 END;
                """, (identifier, f"%{identifier}%", identifier))

        raw_sup = cursor.fetchone()
        if not raw_sup:
            return jsonify({"ok": False, "error": "Supplier not found"}), 404

        sup = _row_to_dict(cursor, raw_sup)
        sup_id = sup.get("id")
        cnum = sup.get("company_number")

        # P2 FIX: confidence gate on name-based LIKE matches
        name_match_confident = True
        if not identifier.isdigit():
            from tender_app.ch_matcher import calculate_name_similarity
            _sim = calculate_name_similarity(identifier, sup.get("name", ""))
            name_match_confident = _sim >= 0.85
        if not name_match_confident:
            for _cf in ("email", "phone", "website", "address"):
                sup[_cf] = "Not available"

        sme_st, vcse_st, _ = classify_supplier_real_data(
            sup.get("name"), cnum,
            scraped_sme=sup.get("sme_status"),
            scraped_vcse=sup.get("vcse_status")
        )
        sup["sme_status"] = sme_st
        sup["vcse_status"] = vcse_st

        if _is_valid_cnum_for_award_join(cnum):
            _execute(cursor, conn, f"""
                SELECT contract_awards.*, {_SHARED_N_SELECT} FROM contract_awards
                WHERE (supplier_id = ? OR company_number = ?)
                  AND source_portal IN ({_PORTALS_SQL_LIST})
                ORDER BY date_signed DESC NULLS LAST;
            """, (sup_id, cnum))
        else:
            _execute(cursor, conn, f"""
                SELECT contract_awards.*, {_SHARED_N_SELECT} FROM contract_awards
                WHERE supplier_id = ?
                  AND source_portal IN ({_PORTALS_SQL_LIST})
                ORDER BY date_signed DESC NULLS LAST;
            """, (sup_id,))
        raw_awards = cursor.fetchall()
        awards = _dedup_recurring_awards([_row_to_dict(cursor, r) for r in raw_awards])

        direct_awards = [a for a in awards if not a.get("is_framework")]
        framework_awards = [a for a in awards if a.get("is_framework")]
        # See SINGLE_AWARD_STATS_CEILING_GBP's docstring: a single non-framework award above this
        # is untrustworthy scraper/source noise, excluded from direct value/count (still counted
        # in total_awards above) to match every other aggregation path.
        direct_awards_in_ceiling = [a for a in direct_awards if float(a.get("contract_value") or 0) <= SINGLE_AWARD_STATS_CEILING_GBP]

        total_value = sum(float(a.get("contract_value") or 0) for a in direct_awards_in_ceiling)
        framework_total_ceiling = sum(float(a.get("contract_value") or 0) for a in framework_awards)

        total_awards = len(awards)
        direct_count = len(direct_awards_in_ceiling)
        fw_count = len(framework_awards)
        avg_value = total_value / direct_count if direct_count > 0 else 0.0
        non_comp_awards, known_route_awards = _competition_stats(awards)
        non_comp_pct = round((non_comp_awards / known_route_awards * 100), 1) if known_route_awards > 0 else 0.0

        auth_map = {}
        for a in awards:
            auth = a.get("authority_name") or "Unknown Authority"
            val = float(a.get("contract_value") or 0)
            is_fw = bool(a.get("is_framework"))
            if auth not in auth_map:
                auth_map[auth] = {"authority_name": auth, "count": 0, "total_value": 0.0}
            auth_map[auth]["count"] += 1
            if not is_fw and val <= SINGLE_AWARD_STATS_CEILING_GBP:
                auth_map[auth]["total_value"] += val

        top_authorities = sorted(auth_map.values(), key=lambda x: (x["count"], x["total_value"]), reverse=True)

        win_bullet = f"🏆 **Win Track Record**: Secured {direct_count} direct contract award(s) totaling £{total_value:,.2f} (Avg contract size: £{avg_value:,.2f})."
        if fw_count > 0:
            win_bullet += f" Also appointed to {fw_count} framework agreement(s) (Shared Framework Ceiling: £{framework_total_ceiling:,.2f})."

        summary_bullets = [
            f"🏢 **Supplier Profile**: {sup.get('name')} (Reg #{cnum}) is registered in {sup.get('region', 'UK')}.",
            f"🌱 **Entity Classification**: {sup.get('sme_status', 'Non-SME')} · VCSE Status: {sup.get('vcse_status', 'Non-VCSE')}.",
            f"📞 **Direct Contacts**: Email: {sup.get('email') or 'N/A'} | Phone: {sup.get('phone') or 'N/A'} | Web: {sup.get('website') or 'N/A'}.",
            f"🏠 **Registered Address**: {sup.get('address') or 'N/A'}.",
            win_bullet,
            f"🏛️ **Primary Public Sector Clients**: Top contracting bodies include {', '.join([a['authority_name'] for a in top_authorities[:3]]) or 'Public Sector Bodies'}.",
            f"⚡ **Competitive Stance**: {non_comp_pct}% non-competitive / direct ({non_comp_awards} of {known_route_awards} awards with a stated procurement route)."
        ]

        # Structured 1-paragraph Company Summary
        top_auth_names = ", ".join([a['authority_name'] for a in top_authorities[:2]]) or "UK public sector bodies"
        fw_clause = f" Additionally, they hold {fw_count} framework appointment(s) with a shared framework ceiling of £{framework_total_ceiling:,.2f}." if fw_count > 0 else ""
        company_summary = (
            f"{sup.get('name')} (Reg #{cnum}) is a UK public sector supplier registered in {sup.get('region', 'UK')}, "
            f"classified as a {sup.get('sme_status', 'SME')} / {sup.get('vcse_status', 'Non-VCSE')}. "
            f"The company has secured {direct_count} direct contract award(s) with a cumulative contract value of £{total_value:,.2f} "
            f"(average contract size £{avg_value:,.2f}), primarily delivering services to public sector contracting clients such as {top_auth_names}.{fw_clause}"
        )

        # Structured 2-3 lines Contract Win Analysis
        if non_comp_awards > 0:
            contract_win_analysis = (
                f"{non_comp_awards} of {known_route_awards} awards with a stated procurement route ({non_comp_pct}%) were made "
                f"without open competition, with buying bodies including {top_auth_names}. "
                f"Award notices do not always give the reason for a direct route, so no motive is assumed here."
            )
        else:
            contract_win_analysis = (
                (f"All {known_route_awards} awards with a stated procurement route were made through competitive procedures, "
                 f"with buying bodies including {top_auth_names}, totalling £{total_value:,.2f}."
                 if known_route_awards else
                 "The procurement route (competitive or direct) is not stated in this supplier's award notices.")
            )

        # Call AI for enhanced 1-paragraph summary & 2-3 lines contract win narrative if available
        try:
            from server import call_chat_json
            top_auth_str = ", ".join([f"{a['authority_name']} ({a['count']} won, £{a['total_value']:,.2f})" for a in top_authorities[:5]])
            awards_str = "\n".join([f"- {a.get('tender_title')} | Authority: {a.get('authority_name')} | Value: £{float(a.get('contract_value') or 0):,.2f} | Signed: {a.get('date_signed')} | Framework: {'YES' if a.get('is_framework') else 'NO'}" for a in awards[:10]])

            system_prompt = (
                "You are an expert Public Sector Procurement & Intelligence Analyst. "
                "Analyze the supplier details and contract history provided. Return JSON format with two keys:\n"
                "1. \"company_summary\": EXACTLY ONE concise paragraph summarizing the company background, region, classification, direct contract win value, and framework appointments (noting framework ceilings are shared maximums across multiple suppliers).\n"
                "2. \"contract_win_analysis\": EXACTLY 2 TO 3 SENTENCES explaining how they won their contract(s), specifying the procurement route (e.g. open competition vs direct award), contracting authority, award value, and bidding strategy factors.\n"
                "CRITICAL RULE FOR FRAMEWORKS: If an award is a framework agreement (is_framework=1/YES), its contract_value is a shared maximum framework ceiling across multiple appointed suppliers, NOT direct contract revenue for this supplier alone. NEVER state framework ceilings as total revenue or direct contract wins earned by this supplier alone."
            )
            user_prompt = f"""
            SUPPLIER INFORMATION:
            Name: {sup.get('name')} (Reg #{cnum})
            Region: {sup.get('region', 'UK')}
            Classification: {sup.get('sme_status')}, {sup.get('vcse_status')}

            CONTRACT METRICS:
            Total Awards: {total_awards}
            Total Value: £{total_value:,.2f}
            Avg Value: £{avg_value:,.2f}
            Direct Award Ratio: {non_comp_pct}% ({non_comp_awards} of {known_route_awards} awards with a stated route; the rest are not stated)

            PRIMARY CLIENTS:
            {top_auth_str or 'None'}

            AWARD NOTICES:
            {awards_str or 'None'}
            """

            parsed_ai = call_chat_json(system=system_prompt, user=user_prompt, max_tokens=600, temperature=0.2)
            if parsed_ai.get("company_summary"):
                company_summary = str(parsed_ai["company_summary"]).strip()
            if parsed_ai.get("contract_win_analysis"):
                contract_win_analysis = str(parsed_ai["contract_win_analysis"]).strip()
        except Exception as ai_err:
            print(f"[Supplier Intelligence] Executive summary notice: {ai_err}")

        return jsonify({
            "ok": True,
            "supplier": {
                "id": sup_id,
                "name": sup.get("name"),
                "company_number": cnum,
                "address": sup.get("address"),
                "email": sup.get("email"),
                "phone": sup.get("phone"),
                "website": sup.get("website"),
                "region": sup.get("region"),
                "sme_status": sup.get("sme_status"),
                "vcse_status": sup.get("vcse_status")
            },
            "metrics": {
                "total_awards": total_awards,
                "total_value": total_value,
                "avg_value": avg_value,
                "non_competitive_awards": non_comp_awards,
                "non_competitive_ratio": non_comp_pct,
                "top_authorities": top_authorities
            },
            "summary_bullets": summary_bullets,
            "company_summary": company_summary,
            "contract_win_analysis": contract_win_analysis,
            "awards": awards
        })
    except Exception as ex:
        return jsonify({"ok": False, "error": str(ex)}), 500
    finally:
        conn.close()


def _fetch_ch_filings_and_officers(company_number: str | None, company_name: str | None) -> dict[str, Any]:
    """Look up official filings and active officers from UK Companies House, requiring >=85% name match confidence."""
    import re
    import urllib.request
    import urllib.parse
    from bs4 import BeautifulSoup

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    clean_reg = normalize_ch_company_number(company_number)

    result: dict[str, Any] = {
        "status": "Not available",
        "incorporation_date": "Not available",
        "company_type": "Not available",
        "sic_codes": [],
        "officers": [],
        "company_number": clean_reg or "Not available",
        "profile_url": "",
        "officers_url": "",
        "data_discrepancy": None,
        "match_confidence": 0.0
    }

    target_reg = clean_reg
    direct_success = False

    # 1. Try direct registry lookup if we have an 8-character registration number
    if target_reg:
        base_url = f"https://find-and-update.company-information.service.gov.uk/company/{target_reg}"
        try:
            req = urllib.request.Request(base_url, headers=headers)
            with urllib.request.urlopen(req, timeout=4) as resp:
                if resp.status == 200:
                    soup = BeautifulSoup(resp.read(), "html.parser")
                    status_el = soup.select_one("#company-status")
                    if status_el and "dissolved" not in status_el.text.lower():
                        result["status"] = status_el.get_text(" ", strip=True)
                        inc_el = soup.select_one("#company-creation-date")
                        if inc_el:
                            result["incorporation_date"] = inc_el.get_text(" ", strip=True)
                        type_el = soup.select_one("#company-type")
                        if type_el:
                            result["company_type"] = type_el.get_text(" ", strip=True)
                        result["sic_codes"] = [el.get_text(" ", strip=True) for el in soup.select("#sic-container ul li, #sic0, #sic1, #sic2")]
                        result["profile_url"] = base_url
                        result["match_confidence"] = 1.0
                        direct_success = True
        except Exception as e:
            err_msg = str(e)
            if "404" in err_msg:
                result["data_discrepancy"] = f"Original registration number '{target_reg}' was not found directly on Companies House for '{company_name}'. Searching by legal company name..."
            direct_success = False

    # 2. If direct lookup failed or no valid registration number was provided, search Companies House requiring >=85% name match
    if not direct_success and company_name:
        try:
            # Build search queries: clean search name without noise words/conjunctions
            queries_to_try = []
            cq = re.sub(r"\b(ltd|limited|llp|plc|group|and co|co|corp|inc|the|services)\b", "", company_name, flags=re.I)
            cq = re.sub(r"[^\w\s]", " ", cq)
            cq = re.sub(r"\s+", " ", cq).strip()
            if cq:
                queries_to_try.append(cq)
            clean_raw = re.sub(r"[^\w\s]", " ", company_name).strip()
            if clean_raw and clean_raw not in queries_to_try:
                queries_to_try.append(clean_raw)

            for sq in queries_to_try:
                if direct_success:
                    break
                search_url = f"https://find-and-update.company-information.service.gov.uk/search/companies?q={urllib.parse.quote(sq)}"
                req_s = urllib.request.Request(search_url, headers=headers)
                with urllib.request.urlopen(req_s, timeout=4) as resp_s:
                    soup_s = BeautifulSoup(resp_s.read(), "html.parser")
                    items = soup_s.select("li.type-company")
                    if items:
                        for item in items[:8]:
                            title_a = item.select_one("a")
                            if title_a and title_a.get("href"):
                                cand_name = title_a.get_text(" ", strip=True)
                                href = title_a.get("href")
                                m = re.search(r"/company/([a-zA-Z0-9]{8})", href)
                                if m:
                                    found_reg = m.group(1)
                                    sim_score = calculate_name_similarity(company_name, cand_name)
                                    # Enforce >= 85% high-confidence similarity threshold
                                    if sim_score >= 0.85:
                                        target_reg = found_reg
                                        result["company_number"] = found_reg
                                        result["match_confidence"] = sim_score
                                        result["profile_url"] = f"https://find-and-update.company-information.service.gov.uk/company/{found_reg}"
                                        direct_success = True
                                        try:
                                            req_f = urllib.request.Request(result["profile_url"], headers=headers)
                                            with urllib.request.urlopen(req_f, timeout=4) as resp_f:
                                                soup_f = BeautifulSoup(resp_f.read(), "html.parser")
                                                status_el = soup_f.select_one("#company-status")
                                                inc_el = soup_f.select_one("#company-creation-date")
                                                type_el = soup_f.select_one("#company-type")
                                                if status_el:
                                                    result["status"] = status_el.get_text(" ", strip=True)
                                                if inc_el:
                                                    result["incorporation_date"] = inc_el.get_text(" ", strip=True)
                                                if type_el:
                                                    result["company_type"] = type_el.get_text(" ", strip=True)
                                                result["sic_codes"] = [el.get_text(" ", strip=True) for el in soup_f.select("#sic-container ul li, #sic0, #sic1, #sic2")]
                                        except Exception:
                                            pass
                                        break
        except Exception:
            pass

    # 3. Fetch active officers / directors ONLY if we have a confirmed registration match
    if target_reg:
        off_url = f"https://find-and-update.company-information.service.gov.uk/company/{target_reg}/officers"
        result["officers_url"] = off_url
        try:
            req_off = urllib.request.Request(off_url, headers=headers)
            with urllib.request.urlopen(req_off, timeout=4) as resp_off:
                soup_off = BeautifulSoup(resp_off.read(), "html.parser")
                appointments = soup_off.select(".appointments-list > div, .appointments-list .appointment-1, .appointment-2, .appointment-3")
                officers_list = []
                for appt in appointments[:8]:
                    name_el = appt.select_one("h2.heading-medium, h2 a, h2")
                    role_el = appt.select_one("[id^=officer-role], .officer-role, dd[id^=officer-role]")
                    app_date_el = appt.select_one("[id^=officer-appointed-on], .officer-appointed-on, dd[id^=officer-appointed-on]")
                    if name_el:
                        name_clean = name_el.get_text(" ", strip=True)
                        role_clean = role_el.get_text(" ", strip=True) if role_el else "Director"
                        date_clean = app_date_el.get_text(" ", strip=True) if app_date_el else "Active Officer"
                        officers_list.append({
                            "name": name_clean,
                            "role": role_clean,
                            "appointed": date_clean
                        })
                result["officers"] = officers_list
        except Exception:
            pass

    sme_st, vcse_st, reason = classify_supplier_real_data(
        company_name, normalize_ch_company_number(result.get("company_number")), ch_data=result
    )
    result["sme_status"] = sme_st
    result["vcse_status"] = vcse_st
    result["classification_reason"] = reason

    return result


def _resolve_seller_services(cursor, conn, req_body: dict | None = None, req_args: dict | None = None) -> tuple[str, list[str]]:
    """Determine genuine services sold by the user/seller company to avoid assumptions."""
    req_body = req_body or {}
    req_args = req_args or {}

    # Check request body/args override
    custom_services = req_body.get("services") or req_args.get("services")
    if custom_services:
        if isinstance(custom_services, str):
            services_list = [s.strip() for s in custom_services.split(",") if s.strip()]
        elif isinstance(custom_services, list):
            services_list = [str(s).strip() for s in custom_services if str(s).strip()]
        else:
            services_list = []
        if services_list:
            context = f"Services specified by sales rep: {', '.join(services_list)}"
            return context, services_list

    # Check saved user profile in database
    seller_name = "Civenta Tender & Services Group"
    services_list = [
        "Public Sector Subcontracting & Delivery Surge Support",
        "Cloud Engineering, IT Operations & Legacy Modernization",
        "Programme Management, PMO Assurance & Contract Compliance",
        "Specialist Technical Staffing & Subcontractor Resourcing",
        "Information Security, Cyber Compliance & ISO/Cyber Essentials Advisory"
    ]
    seller_desc = "UK B2B Services Provider specializing in public sector IT delivery, project management, and subcontractor staffing."

    try:
        # Only ever the signed-in user's own profile: with no username there is no row, so the generic
        # defaults above stay (an unscoped read returned whichever user's default profile had the highest id).
        username = session.get("username")
        row = None
        if username:
            wanted = req_args.get("profile_id") or req_body.get("profile_id")
            if wanted is not None and str(wanted).strip().isdigit():
                _execute(cursor, conn, "SELECT name, profile_text, meta_json FROM company_profiles WHERE id = ? AND username = ?;", (int(str(wanted).strip()), username))
                row = cursor.fetchone()
            if not row:  # no id, or an id that is not this user's: their default, then their first profile
                _execute(cursor, conn, "SELECT name, profile_text, meta_json FROM company_profiles WHERE username = ? ORDER BY is_default DESC, id ASC LIMIT 1;", (username,))
                row = cursor.fetchone()
        if row:
            p_dict = _row_to_dict(cursor, row)
            if p_dict.get("name"):
                seller_name = p_dict.get("name")
            if p_dict.get("profile_text"):
                seller_desc = p_dict.get("profile_text")
            meta_str = p_dict.get("meta_json")
            if meta_str:
                import json as _json
                try:
                    meta = _json.loads(meta_str)
                    if isinstance(meta, dict):
                        found_services = meta.get("services") or meta.get("capabilities") or meta.get("core_services")
                        if isinstance(found_services, list) and found_services:
                            services_list = [str(s).strip() for s in found_services if str(s).strip()]
                        elif isinstance(found_services, str) and found_services.strip():
                            services_list = [s.strip() for s in found_services.split("\n") if s.strip()]
                except Exception:
                    pass
    except Exception:
        pass

    seller_context = f"{seller_name}: {seller_desc}"
    return seller_context, services_list


def _sanitize_playbook_output(pb: dict[str, Any], sup: dict[str, Any], target_award: dict[str, Any], ch_data: dict[str, Any], genuine_services: list[str]) -> dict[str, Any]:
    """Fill any missing/placeholder playbook field so the UI never shows a blank cell. Fillers state only what the
    data supports ("Not available", labelled hypotheses); they never invent registry facts."""
    if not isinstance(pb, dict):
        return pb

    def is_invalid(val: Any) -> bool:
        if val is None:
            return True
        s = str(val).strip().lower()
        return s in ("", "n/a", "unknown", "none", "null", "undefined", "not available", "not found in registry", "role-based: n/a", "role-based") or s.startswith("not found")

    # 1. executive_summary
    if is_invalid(pb.get("executive_summary")):
        t_val = float(target_award.get("contract_value") or 0)
        pb["executive_summary"] = (
            f"{sup.get('name', 'The target supplier')} recently secured the '{target_award.get('tender_title', 'public sector contract')}' "
            f"valued at £{t_val:,.2f} with {target_award.get('authority_name', 'a major public authority')}. "
            f"Hypothesis (unconfirmed): an award of this kind may create demand for subcontracting or delivery capacity during mobilisation."
        )

    # 2. who_is_company
    who = pb.setdefault("who_is_company", {})
    if isinstance(who, dict):
        if is_invalid(who.get("narrative")):
            who["narrative"] = (
                f"{sup.get('name')} is registered in {sup.get('region') or 'the UK'}"
                + (f" and classified as {sup.get('sme_status')}" if sup.get('sme_status') else "")
                + ". No further company background could be generated from the available data; "
                "see the award history and registry links for verified detail."
            )
        if not who.get("signals") or not isinstance(who.get("signals"), list):
            who["signals"] = [
                f"Hypothesis: delivery mobilisation pressure under the £{float(target_award.get('contract_value') or 0):,.2f} award timeline.",
                "Hypothesis: possible subcontracting or capacity requirement to protect project milestones."
            ]
        fs = who.setdefault("filings_summary", {})
        if isinstance(fs, dict):
            if is_invalid(fs.get("status")):
                fs["status"] = ch_data.get("status") or "Not confirmed"
            if is_invalid(fs.get("incorporation_date")):
                fs["incorporation_date"] = ch_data.get("incorporation_date") or "Not available"
            if is_invalid(fs.get("sic_code")):
                fs["sic_code"] = (ch_data.get("sic_codes") and ch_data["sic_codes"][0]) or "Not available"
            if is_invalid(fs.get("entity_size")):
                fs["entity_size"] = sup.get("sme_status") or "Not classified"
            if is_invalid(fs.get("parent_group")):
                fs["parent_group"] = "Not available"

    # 3. contract_battlecard
    bc = pb.setdefault("contract_battlecard", [])
    if isinstance(bc, list):
        if not bc:
            bc.append({})
        for card in bc:
            if isinstance(card, dict):
                if is_invalid(card.get("contract_title")):
                    card["contract_title"] = target_award.get("tender_title") or "Public Sector Contract Award"
                if is_invalid(card.get("contracting_authority")):
                    card["contracting_authority"] = target_award.get("authority_name") or "Not stated"
                if is_invalid(card.get("contract_value")):
                    val = target_award.get("contract_value")
                    card["contract_value"] = f"£{float(val):,.2f}" if val else "Not disclosed"
                if is_invalid(card.get("dates")):
                    ds = target_award.get("date_signed") or "Date not stated"
                    dur = target_award.get("contract_duration")
                    card["dates"] = f"{ds} ({dur})" if dur else ds
                if is_invalid(card.get("procurement_route")):
                    card["procurement_route"] = target_award.get("procurement_type") or "Not stated"
                if is_invalid(card.get("competition_level")):
                    card["competition_level"] = "Not stated in the award notice"
                if is_invalid(card.get("scope")):
                    card["scope"] = f"Execution and managed delivery of {target_award.get('tender_title') or 'contract deliverables'}."

    # 4. why_now
    why = pb.setdefault("why_now", {})
    if isinstance(why, dict):
        if is_invalid(why.get("narrative")):
            why["narrative"] = (
                f"Hypothesis (unconfirmed): mobilising a newly won public-sector contract can strain delivery capacity. "
                f"Validate against the buyer's timeline before outreach."
            )
        if not why.get("delivery_risks") or not isinstance(why.get("delivery_risks"), list):
            why["delivery_risks"] = [
                "Possible risk (generic, not verified for this contract): early milestone delivery bottleneck during ramp-up.",
                "Possible risk (generic, not verified for this contract): specialist subcontractor resourcing pinch points.",
                "Possible risk (generic, not verified for this contract): compliance and governance oversight requirements."
            ]

    # 5. target_contacts
    tc = pb.setdefault("target_contacts", {})
    if isinstance(tc, dict):
        contacts = tc.setdefault("contacts", [])
        if not isinstance(contacts, list) or not contacts:
            contacts = [
                {"role": "Commercial Director", "name": "Commercial Director (Bid Owner)", "confidence": "Role-Based Inference — Confirm on LinkedIn before outreach", "responsibility": "Commercial oversight, budget accountability, and supply chain contract negotiations."},
                {"role": "Head of Programme Delivery", "name": "Head of Operations & Delivery", "confidence": "Role-Based Inference — Confirm on LinkedIn before outreach", "responsibility": "Accountable for project governance, resource allocation, and meeting client milestone dates."},
                {"role": "Procurement & Subcontracting Lead", "name": "Framework Delivery Lead", "confidence": "Role-Based Inference — Confirm on LinkedIn before outreach", "responsibility": "Onboards specialist subcontracting partners to fulfill surge capacity requirements."}
            ]
            tc["contacts"] = contacts
        else:
            default_roles = ["Commercial Director", "Head of Programme Delivery", "Procurement & Subcontracting Lead"]
            for idx, c in enumerate(contacts):
                if isinstance(c, dict):
                    if is_invalid(c.get("role")):
                        c["role"] = default_roles[idx % len(default_roles)]
                    if is_invalid(c.get("name")):
                        c["name"] = f"{c['role']} (Bid Owner)"
                    if is_invalid(c.get("confidence")):
                        c["confidence"] = "Role-Based Inference — Confirm on LinkedIn before outreach"
                    if is_invalid(c.get("responsibility")):
                        c["responsibility"] = f"Directly accountable for mobilization milestones and vendor margin on the {target_award.get('tender_title', 'contract')}."
        if is_invalid(tc.get("finding_tip")):
            tc["finding_tip"] = f"Search LinkedIn for '{sup.get('name')}' with keywords 'Commercial Director', 'Delivery Director', or 'Framework Lead'. Cross-reference with the tender notice signatory."

    # 6. ways_in
    ways = pb.setdefault("ways_in", [])
    if not isinstance(ways, list) or not ways:
        pb["ways_in"] = [
            {
                "angle_number": 1,
                "angle_title": "Surge Delivery & Subcontracting Acceleration",
                "matched_service": genuine_services[0] if genuine_services else "Public Sector Subcontracting & Delivery Surge Support",
                "pitch_rationale": "Offer immediate, vetted capacity to guarantee zero penalty slippage on early contract mobilization milestones.",
                "recommendation_badge": "Recommended Primary Angle"
            },
            {
                "angle_number": 2,
                "angle_title": "Specialist Technical & Operational Resourcing",
                "matched_service": genuine_services[1] if len(genuine_services) > 1 else "Specialist Technical Staffing & Subcontractor Resourcing",
                "pitch_rationale": "Position pre-cleared subject matter experts to fulfill specialized requirements without permanent payroll expansion.",
                "recommendation_badge": "Alternative Angle"
            }
        ]

    tender_name = target_award.get("tender_title") or "recent public sector contract award"
    auth_name = target_award.get("authority_name") or "the public authority"
    val_float = float(target_award.get("contract_value") or 0)
    val_str = f"£{val_float:,.2f}" if val_float else "undisclosed value"
    comp_sname = sup.get("name") or "your team"
    primary_svc = genuine_services[0] if genuine_services else "Public Sector Subcontracting & Delivery Surge Support"
    alt_svc = genuine_services[1] if len(genuine_services) > 1 else "Specialist Subcontractor Resourcing & Project Mobilization"

    # 7. sales_scripts (Cold Emails & LinkedIn Suite)
    scripts = pb.setdefault("sales_scripts", {})
    if not isinstance(scripts, dict):
        scripts = {}
        pb["sales_scripts"] = scripts

    # 7a. cold_email_v1 (Delivery Risk Focus)
    ce1 = scripts.setdefault("cold_email_v1", {})
    if not isinstance(ce1, dict) or is_invalid(ce1.get("body")):
        scripts["cold_email_v1"] = {
            "subject": f"Delivery Assurance & Surge Support: {tender_name}",
            "body": (
                f"Hi [Name],\n\n"
                f"Congratulations on securing the {tender_name} with {auth_name} ({val_str}).\n\n"
                f"Large public framework awards often bring rapid delivery ramp-up and initial resourcing crunches. "
                f"We specialize in {primary_svc}, deploying certified, pre-vetted specialists to absorb surge requirements "
                f"and guarantee zero milestone slippage under client SLAs.\n\n"
                f"Would you be open to a brief 15-minute conversation next Tuesday to explore how we can support your delivery team?\n\n"
                f"Best regards,\n[Your Name]\n[Your Title]"
            ),
            "angle_focus": "Delivery Risk Mitigation"
        }

    # 7b. cold_email_v2 (Capacity & Surge Focus)
    ce2 = scripts.setdefault("cold_email_v2", {})
    if not isinstance(ce2, dict) or is_invalid(ce2.get("body")):
        scripts["cold_email_v2"] = {
            "subject": f"Rapid Mobilization & Surge Resourcing: {tender_name}",
            "body": (
                f"Hi [Name],\n\n"
                f"Noticed {comp_sname}'s recent award for {tender_name}. "
                f"Given the timeline commitments under {auth_name}, I wanted to introduce our specialist surge capacity services.\n\n"
                f"We provide {alt_svc}, enabling prime contractors to scale technical and operational bandwidth on demand "
                f"without the overhead of permanent headcount.\n\n"
                f"Could we connect for 10 minutes this week to share our subcontractor rate cards and vetted capability overview?\n\n"
                f"Best regards,\n[Your Name]\n[Your Title]"
            ),
            "angle_focus": "Capacity & Surge Support"
        }

    # 7c. linkedin_note (<300 chars)
    if is_invalid(scripts.get("linkedin_note")):
        note = f"Hi [Name], congratulations on {comp_sname}'s contract win for {tender_name[:40]} with {auth_name}. Would be great to connect on delivery surge capacity."
        scripts["linkedin_note"] = note[:295]

    # 7d. linkedin_followup
    if is_invalid(scripts.get("linkedin_followup")):
        scripts["linkedin_followup"] = (
            f"Thanks for connecting, [Name]. Wanted to briefly share that we partner with prime contractors on {auth_name} programmes "
            f"to provide pre-cleared surge subcontracting ({primary_svc}). Happy to send over a 1-page capability summary if helpful for your mobilization."
        )

    # 7e. cold_call
    cc = scripts.setdefault("cold_call", {})
    if not isinstance(cc, dict) or is_invalid(cc.get("opening")):
        scripts["cold_call"] = {
            "opening": (
                f"Hello [Name], this is [Your Name] from Civenta. I'm reaching out specifically regarding your recent "
                f"award for {tender_name} with {auth_name}. The reason for my call is to understand how your delivery team is "
                f"structuring subcontractor surge capacity for the initial ramp-up phase."
            ),
            "discovery_questions": [
                f"How are you currently resourcing the early mobilization milestones for {tender_name}?",
                "Are you anticipating any internal pinch points between this new contract and existing framework commitments?",
                "What criteria do you use when evaluating specialist subcontracting partners for public sector frameworks?",
                "Would on-demand, vetted delivery capacity help safeguard your margin against penalty clauses?"
            ],
            "voicemail": (
                f"Hi [Name], [Your Name] calling regarding your contract award with {auth_name}. "
                f"We provide specialist surge resourcing ({primary_svc}) for UK public sector delivery. "
                f"I'll follow up with a brief email, or reach me on [Your Phone]. Thank you."
            )
        }

    # 7f. meeting_agenda
    if not scripts.get("meeting_agenda") or not isinstance(scripts.get("meeting_agenda"), list):
        scripts["meeting_agenda"] = [
            f"Review of {tender_name} mobilization timeline & key milestones (5 mins)",
            "Exploration of potential resource pinch points & surge capacity requirements (7 mins)",
            f"Introduction to our {primary_svc} models and pre-cleared team capability (5 mins)",
            "Agreement on next steps, rate card sharing, and NDA alignment (3 mins)"
        ]

    # 8. objection_handling
    objs = pb.setdefault("objection_handling", [])
    if not isinstance(objs, list) or not objs:
        pb["objection_handling"] = [
            {
                "objection": "We handled the tender ourselves and have full internal delivery capacity.",
                "likely_context": "Internal teams are protective of margin, but mobilization often exposes unexpected resource contention.",
                "winning_response": (
                    f"Completely respect that—winning {auth_name} proves your internal capability. We don't replace your team; "
                    f"we act strictly as an on-demand surge safety net for peak milestone crunches so you never incur delay penalties."
                )
            },
            {
                "objection": "Our supply chain was already locked down during the bid submission.",
                "likely_context": "Supply chain was named for compliance, but named subcontractors often face availability constraints once work starts.",
                "winning_response": (
                    "Understood. If any named partner encounters scheduling bottlenecks or capacity caps during rollout, "
                    "we can step in as pre-vetted reserve support on 48 hours' notice without disrupting your existing chain."
                )
            },
            {
                "objection": "We don't need any recruitment agencies.",
                "likely_context": "Prospect is tired of transactional recruiters spamming CVs.",
                "winning_response": (
                    f"We're not a recruitment agency. We are a specialized public sector delivery partner providing managed, "
                    f"outcome-based surge capacity ({primary_svc}) aligned directly to framework SLA outcomes."
                )
            }
        ]

    # 9. outreach_cadence
    cadence = pb.setdefault("outreach_cadence", [])
    if not isinstance(cadence, list) or not cadence:
        pb["outreach_cadence"] = [
            {"day": "Day 1", "channel": "Email", "action": f"Send Cold Email Option 1 (Delivery Risk Focus) referencing {auth_name} award."},
            {"day": "Day 2", "channel": "LinkedIn", "action": "Send LinkedIn connection note (<300 chars) to Commercial Director / Bid Lead."},
            {"day": "Day 5", "channel": "Phone", "action": "First cold call attempt using Phone Opener; leave 25s crisp voicemail if unanswered."},
            {"day": "Day 8", "channel": "Email", "action": "Follow-up email forwarding original message with short case study or capability one-pager."},
            {"day": "Day 12", "channel": "LinkedIn", "action": "Send LinkedIn message following up on connection acceptance with genuine service match."},
            {"day": "Day 16", "channel": "Phone", "action": "Second call attempt; pivot to discovery questions on milestone timeline."},
            {"day": "Day 21", "channel": "Email", "action": "Breakup email offering on-demand surge rate card for future framework spikes."}
        ]

    # 10. sources
    src = pb.setdefault("sources", {})
    if not isinstance(src, dict):
        src = {}
        pb["sources"] = src
    if not src.get("verified_facts") or not isinstance(src.get("verified_facts"), list):
        target_portal = target_award.get("source_portal") or "Contracts Finder"
        src["verified_facts"] = [
            f"Contract award '{tender_name}' officially confirmed on {target_portal} register ({val_str}).",
            f"Awarding contracting body verified as {auth_name}.",
        ]
        from tender_app.ch_matcher import normalize_ch_company_number as _norm_ch
        _reg = _norm_ch(sup.get('company_number') or ch_data.get('company_number'))
        if _reg:  # never claim a registry check for a synthetic / unknown identifier
            src["verified_facts"].append(f"Company registration number on file: #{_reg}.")
    if not src.get("inferences") or not isinstance(src.get("inferences"), list):
        src["inferences"] = [
            f"Contract scale ({val_str}) implies significant mobilization and delivery overhead for {comp_sname}.",
            "Initial milestone deadlines create high operational sensitivity to delivery slippage and subcontractor availability."
        ]
    if not src.get("links") or not isinstance(src.get("links"), list):
        links_list = []
        if target_award.get("notice_url"):
            target_portal = target_award.get("source_portal") or "Contracts Finder"
            links_list.append({"title": f"Official {target_portal} Award Notice", "url": target_award.get("notice_url")})
        if ch_data.get("profile_url"):
            links_list.append({"title": "Official Companies House Registry Record", "url": ch_data.get("profile_url")})
        src["links"] = links_list

    # 11 & 12. sales_priority_score
    if not pb.get("sales_priority_score"):
        score = 85 if val_float > 500000 else 75
        pb["sales_priority_score"] = score
    if not pb.get("sales_priority_score_rationale"):
        pb["sales_priority_score_rationale"] = (
            f"Rule-based score from the award value ({val_str}, {auth_name}); not an AI assessment of fit. "
            f"Review the contract and buyer before prioritising."
        )

    # Recursive pass to eliminate any lingering 'N/A' or 'Unknown' strings
    def _deep_clean(val: Any) -> Any:
        if isinstance(val, str):
            clean_s = val.strip()
            if clean_s.lower() in ("n/a", "unknown", "none", "null", "undefined", "not available", "not found in registry"):
                return "Not available"
            return val.replace("Role-Based: N/A", "Role-Based Commercial Lead")
        elif isinstance(val, dict):
            return {k: _deep_clean(v) for k, v in val.items()}
        elif isinstance(val, list):
            return [_deep_clean(item) for item in val]
        return val

    return _deep_clean(pb)


@suppliers_bp.route("/api/suppliers/<identifier>/bd-playbook", methods=["GET", "POST"])

def generate_bd_playbook(identifier: str):
    """Generate research-backed 10-section Business Development Playbook for targeting a public sector contract winner."""
    req_body = request.get_json(silent=True) if (request.is_json and request.data) else {}
    req_body = req_body or {}
    award_id = request.args.get("award_id") or req_body.get("award_id")

    conn = _get_connection()
    cursor = conn.cursor()

    try:
        # 1. Fetch Supplier record
        if identifier.isdigit():
            _execute(cursor, conn, "SELECT * FROM suppliers WHERE id = ? OR company_number = ?;", (int(identifier), identifier))
        else:
            _execute(cursor, conn, """
                SELECT * FROM suppliers WHERE company_number = ? OR LOWER(name) LIKE LOWER(?)
                ORDER BY CASE WHEN LOWER(name) = LOWER(?) THEN 0 ELSE 1 END;
                """, (identifier, f"%{identifier}%", identifier))

        raw_sup = cursor.fetchone()
        if not raw_sup:
            return jsonify({"ok": False, "error": "Supplier not found"}), 404

        sup = _row_to_dict(cursor, raw_sup)
        sup_id = sup.get("id")
        cnum = sup.get("company_number")
        comp_name = sup.get("name") or "Target Company"

        # 2. Fetch won contracts (scoped to Contracts Finder & Find a Tender)
        has_valid_cnum = _is_valid_cnum_for_award_join(cnum)
        if award_id and str(award_id).isdigit():
            if has_valid_cnum:
                _execute(cursor, conn, "SELECT * FROM contract_awards WHERE id = ? AND (supplier_id = ? OR company_number = ?) AND source_portal IN ('Contracts Finder', 'Find a Tender');", (int(award_id), sup_id, cnum))
            else:
                _execute(cursor, conn, "SELECT * FROM contract_awards WHERE id = ? AND supplier_id = ? AND source_portal IN ('Contracts Finder', 'Find a Tender');", (int(award_id), sup_id))
            raw_awards = cursor.fetchall()
            if not raw_awards:
                if has_valid_cnum:
                    _execute(cursor, conn, "SELECT * FROM contract_awards WHERE (supplier_id = ? OR company_number = ?) AND source_portal IN ('Contracts Finder', 'Find a Tender') ORDER BY date_signed DESC NULLS LAST;", (sup_id, cnum))
                else:
                    _execute(cursor, conn, "SELECT * FROM contract_awards WHERE supplier_id = ? AND source_portal IN ('Contracts Finder', 'Find a Tender') ORDER BY date_signed DESC NULLS LAST;", (sup_id,))
                raw_awards = cursor.fetchall()
        else:
            if has_valid_cnum:
                _execute(cursor, conn, "SELECT * FROM contract_awards WHERE (supplier_id = ? OR company_number = ?) AND source_portal IN ('Contracts Finder', 'Find a Tender') ORDER BY date_signed DESC NULLS LAST;", (sup_id, cnum))
            else:
                _execute(cursor, conn, "SELECT * FROM contract_awards WHERE supplier_id = ? AND source_portal IN ('Contracts Finder', 'Find a Tender') ORDER BY date_signed DESC NULLS LAST;", (sup_id,))
            raw_awards = cursor.fetchall()

        awards = [_row_to_dict(cursor, r) for r in raw_awards]
        target_award = awards[0] if awards else {}

        def _safe_float(val: Any) -> float:
            if val is None or val == "":
                return 0.0
            if isinstance(val, (int, float)):
                return float(val)
            try:
                import re
                cleaned = re.sub(r"[^\d.]", "", str(val))
                return float(cleaned) if cleaned else 0.0
            except Exception:
                return 0.0

        # Split direct wins from framework/call-off appointments before summing — a
        # framework's contract_value is a shared ceiling across every appointed supplier,
        # not this supplier's own revenue, so it must never be folded into "Cumulative
        # Value" below. Mirrors the same split already applied in get_supplier_detail()
        # and get_supplier_executive_summary().
        direct_awards_bd = [a for a in awards if not a.get("is_framework")]
        framework_awards_bd = [a for a in awards if a.get("is_framework")]
        total_val = sum(_safe_float(a.get("contract_value")) for a in direct_awards_bd)
        framework_ceiling_val = sum(_safe_float(a.get("contract_value")) for a in framework_awards_bd)
        target_val = _safe_float(target_award.get("contract_value"))

        # STEP 1 — RESEARCH: Companies House Registry & Officers
        ch_data = _fetch_ch_filings_and_officers(cnum, comp_name)
        seller_context, genuine_services = _resolve_seller_services(cursor, conn, req_body, request.args)

        officers_formatted = "\n".join([
            f"  - {o['name']} ({o['role']}, Appointed: {o['appointed']})"
            for o in ch_data.get("officers", [])
        ]) or "  - No active officers indexed in live registry extract"

        sic_formatted = ", ".join(ch_data.get("sic_codes", [])) or "SIC unlisted"
        services_formatted = "\n".join([f"  - {svc}" for svc in genuine_services])

        awards_history_formatted = "\n".join([
            f"  • Title: {a.get('tender_title')} | Client: {a.get('authority_name')} | Value: £{_safe_float(a.get('contract_value')):,.2f} | Signed: {a.get('date_signed')} | Route: {a.get('procurement_type', 'Open competition')}"
            for a in awards[:6]
        ]) or "  • Single public contract award on record"

        # Construct Research-Backed System & User Prompts
        system_prompt = (
            "You are an elite B2B sales researcher producing an authoritative, actionable business-development playbook "
            "for our sales team, targeting a company that just won a public sector contract.\n\n"
            "STRICT RULES YOU MUST FOLLOW:\n"
            "1. GROUND EVERYTHING IN THE INPUTS: state only facts present in the INPUTS below. If a fact is not provided (registry details, dates, names, figures, websites, emails), write 'Not available' - never invent or guess it. Labelled hypotheses are allowed and must be marked as INFERENCES.\n"
            "2. For Target Contacts: If named officers are not in the registry extract, provide exact high-priority executive roles (e.g. 'Commercial Director', 'Head of Programme Delivery', 'Public Sector Framework Manager') and set confidence to 'Role-Based Inference — Confirm on LinkedIn before outreach', specifying their exact commercial mandate for this contract.\n"
            "3. For Contract Battlecard: Use the provided contract details (contract title, contracting authority, contract value, dates signed/duration, procurement route). If competition level or specific sub-contract scope is not in the tender notice summary, provide a reasoned procurement analysis (e.g., 'Open competition under Crown Commercial Service framework thresholds; Inferred', 'Full lifecycle implementation and managed service delivery').\n"
            "4. Never state an inference as if it were a verified fact. Clearly label facts as VERIFIED and hypotheses as INFERENCES.\n"
            "5. Do NOT recommend services we do not offer. Match all positioning ONLY to the seller's actual listed services.\n"
            "6. Flag any inconsistency in source data (e.g. registry discrepancies), but explicitly instruct the rep NOT to raise it with the prospect.\n"
            "7. CRITICAL RULE FOR FRAMEWORKS: If an award's Route says 'Framework' or 'Call-off', its Value is a shared maximum ceiling split across every supplier appointed to that framework, NOT revenue this company earned alone. NEVER state a framework ceiling as this company's own contract value, win, or revenue.\n"
            "8. Return clean, valid JSON matching the exact required keys."
        )

        user_prompt = f"""
        === INPUTS ===
        TARGET PROSPECT (Won Contract Recipient):
        - Company Name: {comp_name}
        - Company Registration Number: {cnum or ch_data.get('company_number') or 'Not available'}
        - Region: {sup.get('region') or 'United Kingdom'}
        - Entity Size / Classification: {sup.get('sme_status') or 'Not classified'}, {sup.get('vcse_status') or 'Not classified'}
        - Registered Address: {sup.get('address') or 'Not available'}
        - Website: {sup.get('website') or 'Not available'}
        - Contact Email: {sup.get('email') or 'Not available'}
        - Contact Phone: {sup.get('phone') or 'Not available'}

        COMPANIES HOUSE OFFICIAL REGISTRY DATA:
        - Entity Status: {ch_data.get('status') or 'Not available'}
        - Incorporation Date: {ch_data.get('incorporation_date') or 'Not available'}
        - Entity Type: {ch_data.get('company_type') or 'Not available'}
        - SIC Codes / Nature of Business: {sic_formatted}
        - Official Registry Link: {ch_data.get('profile_url') or 'Not available'}
        - Verified Registry Officers / Directors:
        {officers_formatted}
        - Data Inconsistency Flag: {ch_data.get('data_discrepancy') or 'None detected'}

        TARGET CONTRACT AWARD WON (Tender / Notice Data):
        - Contract Title: {target_award.get('tender_title', 'Public Sector Contract Award')}
        - Contracting Authority: {target_award.get('authority_name') or 'Not stated'}
        - Contract Value: £{target_val:,.2f}
        - Award / Signed Date: {target_award.get('date_signed') or 'Not stated'}
        - Contract Duration / Timeline: {target_award.get('contract_duration') or 'Not stated'}
        - Procurement Route: {target_award.get('procurement_type') or 'Not stated'}
        - Total Direct Public Sector Wins on Record: {len(direct_awards_bd)} (Cumulative Value: £{total_val:,.2f})
        {f"- Also Appointed To: {len(framework_awards_bd)} framework/call-off agreement(s) with a combined shared ceiling of £{framework_ceiling_val:,.2f} — this ceiling is split across every supplier appointed to the framework, NOT this company's own revenue. Never state it as their direct win value." if framework_awards_bd else ""}
        - Public Sector Award History Pattern:
        {awards_history_formatted}

        OUR ACTUAL SERVICES (What we genuinely sell — DO NOT assume or invent others):
        Seller Organization Context: {seller_context}
        Strict Catalog of Services We Offer:
        {services_formatted}

        === REQUIRED PLAYBOOK OUTPUT STRUCTURE ===
        Produce a JSON object containing the following EXACT keys (REMINDER: use 'Not available' for anything the INPUTS do not give; never invent it):

        1. "executive_summary": (string, full prose) The single most compelling, specific reason this is a live opportunity right now (not generic praise or congratulations).
        2. "who_is_company": (object)
           - "narrative": (string, full prose) Business overview, heritage, structure, entity size classification, and SIC nature of business.
           - "signals": (list of strings) 1 or 2 recent events / operational realities that indicate a real, unforced need for external help (e.g. rapid contract scale vs headcount, delivery ramp-up, compliance surge).
           - "filings_summary": (object) {{ "incorporation_date": str, "sic_code": str, "entity_size": str, "status": str, "parent_group": str }}
        3. "contract_battlecard": (list of objects) Table of contract(s) with keys:
           - "contract_title": str
           - "contracting_authority": str
           - "contract_value": str (e.g. "£4,500,000.00")
           - "dates": str (Award date & delivery duration)
           - "procurement_route": str
           - "competition_level": str (Competition route / estimated bidder intensity; note if inferred)
           - "scope": str (Specific scope of deliverables required under the contract)
        4. "why_now": (object)
           - "narrative": (string, full prose) The bigger context: programme/framework pipeline this contract sits inside, competitive dynamics, and delivery risk.
           - "programme_pipeline_context": str
           - "competitive_dynamics": str
           - "delivery_risks": (list of strings) 2-3 specific operational, capacity, or technical delivery risks.
        5. "target_contacts": (object)
           - "contacts": (list of objects) 3 target personas/contacts with keys:
             - "name": str (Real name if verified from officers/filings, or role title with commercial lead designation)
             - "role": str (e.g. Chief Operating Officer, Commercial Director, Programme Delivery Director)
             - "confidence": str ("Verified Officer", "High Confidence", or "Role-Based Inference — Confirm on LinkedIn before outreach")
             - "responsibility": str (Why this role owns the budget/delivery for this specific contract)
           - "finding_tip": (string) Practical, specific tip for the sales rep on how to identify the exact bid owner / contract delivery lead on LinkedIn or tender portal notices.
        6. "ways_in": (list of 3 to 5 objects) Honestly-differentiated angles matched ONLY to our actual services listed above. Must include a clear instruction to the reader to pick ONE or TWO angles. Each object has:
           - "angle_number": int
           - "angle_title": str
           - "matched_service": str (The exact genuine service from our catalog)
           - "pitch_rationale": str (Specific value proposition and how to introduce it without sounding presumptuous)
           - "recommendation_badge": str ("Recommended Primary Angle" or "Alternative Angle")
        7. "sales_scripts": (object)
           - "cold_email_v1": (object) {{ "subject": str, "body": str, "angle_focus": "Delivery Risk Mitigation" }}
           - "cold_email_v2": (object) {{ "subject": str, "body": str, "angle_focus": "Capacity & Surge Support" }}
           - "linkedin_note": (string, <300 characters connection invitation referencing contract win)
           - "linkedin_followup": (string, follow-up message after connection is accepted)
           - "cold_call": (object) {{ "opening": str, "discovery_questions": (list of 4-5 open-ended questions tailored to contract execution), "voicemail": str }}
           - "meeting_agenda": (list of 4 strings for a 20-minute introductory discovery session)
        8. "objection_handling": (list of objects) Specific to what THIS company would plausibly say given their size and contract win (not generic sales objections). Keys:
           - "objection": str (e.g. "We handled the bid ourselves and have internal capacity", "We already locked in our supply chain during the tender submission")
           - "likely_context": str (What they actually mean/face internally)
           - "winning_response": str (Conversational, non-combative pivot)
        9. "outreach_cadence": (list of objects) Day-by-day plan across multi-channel touchpoints. Keys:
           - "day": str (e.g. "Day 1", "Day 3", "Day 5", "Day 8", "Day 12", "Day 15", "Day 21")
           - "channel": str (e.g. "Email", "LinkedIn Connection", "Phone / Voicemail", "Email Follow-up", "Executive Touchpoint")
           - "action": str (Specific step to execute, referencing angle selection)
        10. "sources": (object)
           - "verified_facts": (list of strings) Claims traceable to official named sources (Companies House, Contracts Finder, Tender Notice).
           - "inferences": (list of strings) Logical deductions drawn regarding operational need, capacity, or competition.
           - "data_inconsistencies": (list of strings) Any data discrepancy detected, each ending with "Internal note only: Do NOT raise this inconsistency with the prospect."
           - "links": (list of objects) [{{ "title": str, "url": str }}]
        11. "sales_priority_score": (int between 0 and 100)
        12. "sales_priority_score_rationale": (string, 1 concise sentence explaining the score)

        Populate every key. Where the INPUTS do not contain the information, write 'Not available' rather than guessing, and put any deduction under "inferences", not "verified_facts".
        """

        from server import call_chat_api
        raw_playbook = None
        try:
            raw_playbook = call_chat_api(
                system=system_prompt,
                user=user_prompt,
                max_tokens=4000,
                temperature=0.25,
                response_format_json=True
            )
        except Exception as api_err:
            print(f"[BD Playbook AI warning] {api_err} - falling back to research-backed sanitizer")

        playbook_data = {}
        if raw_playbook:
            from tender_app.llm_json import parse_llm_json
            try:
                parsed_pb = parse_llm_json(raw_playbook)
                playbook_data = parsed_pb if isinstance(parsed_pb, dict) else {}
            except ValueError as parse_err:
                print(f"[BD Playbook AI warning] unusable JSON ({parse_err}) - falling back to sanitizer")

        # Sanitize and enrich playbook data to guarantee zero 'N/A' or 'Unknown' placeholders
        playbook_data = _sanitize_playbook_output(playbook_data, sup, target_award, ch_data, genuine_services)

        # Maintain backwards-compatibility aliases
        if playbook_data:
            if "sales_scripts" in playbook_data and isinstance(playbook_data["sales_scripts"], dict):
                scripts = playbook_data["sales_scripts"]
                if "cold_email_v1" in scripts and isinstance(scripts["cold_email_v1"], dict):
                    playbook_data.setdefault("personalized_cold_email", f"Subject: {scripts['cold_email_v1'].get('subject', '')}\n\n{scripts['cold_email_v1'].get('body', '')}")
                if "linkedin_note" in scripts:
                    playbook_data.setdefault("linkedin_message", scripts["linkedin_note"])
                if "cold_call" in scripts and isinstance(scripts["cold_call"], dict):
                    playbook_data.setdefault("cold_call_script", scripts["cold_call"].get("opening", ""))
                    playbook_data.setdefault("discovery_questions", scripts["cold_call"].get("discovery_questions", []))

        return jsonify({
            "ok": True,
            "supplier": sup,
            "target_award": target_award,
            "playbook": playbook_data,
            "registry_data": ch_data,
            "seller_services": genuine_services,
            "ai_provider": "DeepSeek AI (deepseek-chat)"
        })
    except Exception as ex:
        import traceback
        error_trace = traceback.format_exc()
        print(f"[BD Playbook Error] {error_trace}")  # Log full traceback to console
        return jsonify({"ok": False, "error": f"Failed to generate Business Development Playbook: {str(ex)}", "traceback": error_trace if request.args.get('debug') else None}), 500
    finally:
        conn.close()


@suppliers_bp.route("/api/suppliers/similar-awards", methods=["GET"])
def get_similar_awards():
    """Surface past contract award winners matching CPV code or Contracting Authority."""
    cpv = (request.args.get("cpv") or "").strip()
    authority = (request.args.get("authority") or "").strip()
    limit = min(int(request.args.get("limit", 10)), 50)

    conn = _get_connection()
    cursor = conn.cursor()

    try:
        where_clauses = ["a.source_portal IN ('Contracts Finder', 'Find a Tender')"]
        params: list[Any] = []

        filter_clauses = []
        if cpv:
            # Match exact CPV or 4-digit CPV division prefix
            cpv_prefix = cpv[:4] if len(cpv) >= 4 else cpv
            filter_clauses.append("(a.cpv_code LIKE ? OR a.cpv_code = ?)")
            params.extend([f"{cpv_prefix}%", cpv])

        if authority:
            filter_clauses.append("LOWER(a.authority_name) LIKE LOWER(?)")
            params.append(f"%{authority}%")

        if filter_clauses:
            where_clauses.append("(" + " OR ".join(filter_clauses) + ")")

        where_sql = " WHERE " + " AND ".join(where_clauses)

        sql = f"""
            SELECT a.*, s.name as supplier_name, s.sme_status, s.vcse_status, s.region as supplier_region
            FROM contract_awards a
            LEFT JOIN suppliers s ON a.supplier_id = s.id
            {where_sql}
            ORDER BY a.date_signed DESC NULLS LAST, a.contract_value DESC
            LIMIT ?;
        """
        params.append(limit)

        _execute(cursor, conn, sql, params)
        raw_rows = cursor.fetchall()

        similar_awards = []
        incumbent_map: dict[str, dict[str, Any]] = {}

        for raw_r in raw_rows:
            r = _row_to_dict(cursor, raw_r)
            val = float(r.get("contract_value") or 0)
            sname = r.get("supplier_name") or r.get("company_number") or "Unknown Supplier"
            similar_awards.append({
                "id": r.get("id"),
                "supplier_name": sname,
                "company_number": r.get("company_number"),
                "sme_status": r.get("sme_status"),
                "vcse_status": r.get("vcse_status"),
                "authority_name": r.get("authority_name"),
                "tender_title": r.get("tender_title"),
                "cpv_code": r.get("cpv_code"),
                "cpv_description": r.get("cpv_description"),
                "contract_value": val,
                "currency": r.get("currency") or "GBP",
                "date_signed": str(r.get("date_signed") or ""),
                "contract_duration": r.get("contract_duration"),
                "procurement_type": r.get("procurement_type"),
                "is_competitive": bool(r.get("is_competitive")),
                "source_portal": r.get("source_portal"),
                "notice_url": r.get("notice_url")
            })

            if sname not in incumbent_map:
                incumbent_map[sname] = {
                    "supplier_name": sname,
                    "company_number": r.get("company_number"),
                    "sme_status": r.get("sme_status"),
                    "vcse_status": r.get("vcse_status"),
                    "win_count": 0,
                    "total_value": 0.0
                }
            incumbent_map[sname]["win_count"] += 1
            incumbent_map[sname]["total_value"] += val

        incumbents = sorted(incumbent_map.values(), key=lambda x: (x["win_count"], x["total_value"]), reverse=True)

        return jsonify({
            "ok": True,
            "cpv": cpv,
            "authority": authority,
            "similar_awards": similar_awards,
            "incumbents": incumbents,
            "attribution": OGL_V3_ATTRIBUTION
        })
    except Exception as ex:
        return jsonify({"ok": False, "error": str(ex)}), 500
    finally:
        conn.close()


@suppliers_bp.route("/api/suppliers/authority-suppliers", methods=["GET"])
def get_authority_suppliers():
    """Surface incumbent suppliers used by a given contracting authority."""
    authority = (request.args.get("authority") or "").strip()
    if not authority:
        return jsonify({"ok": False, "error": "Missing authority parameter"}), 400

    conn = _get_connection()
    cursor = conn.cursor()

    try:
        # Same de-duplicated source as every other total, with framework ceilings and untrustworthy single values
        # left out of the sum (this one used to add up raw rows, so a framework counted once per appointed supplier).
        _execute(cursor, conn, f"""
            WITH {DEDUPED_AWARDS_CTE_SQL}
            SELECT s.id, s.company_number, s.name, s.sme_status, s.vcse_status, s.region,
                   COUNT(a.id) as contracts_won,
                   COALESCE(SUM(CASE WHEN a.is_framework = 1 OR a.contract_value > {SINGLE_AWARD_STATS_CEILING_GBP} THEN 0 ELSE a.contract_value END), 0) as total_awarded_value,
                   MAX(a.date_signed) as latest_award_date
            FROM deduped_awards a
            JOIN suppliers s ON a.supplier_id = s.id
            WHERE a.source_portal IN ('Contracts Finder', 'Find a Tender') AND LOWER(a.authority_name) LIKE LOWER(?)
            GROUP BY s.id, s.company_number, s.name, s.sme_status, s.vcse_status, s.region
            ORDER BY contracts_won DESC, total_awarded_value DESC;
        """, (f"%{authority}%",))

        raw_rows = cursor.fetchall()
        suppliers = []
        for raw_r in raw_rows:
            r = _row_to_dict(cursor, raw_r)
            suppliers.append({
                "supplier_id": r.get("id"),
                "company_number": r.get("company_number"),
                "name": r.get("name"),
                "sme_status": r.get("sme_status"),
                "vcse_status": r.get("vcse_status"),
                "region": r.get("region"),
                "contracts_won": r.get("contracts_won"),
                "total_awarded_value": float(r.get("total_awarded_value") or 0),
                "latest_award_date": str(r.get("latest_award_date") or "")
            })

        return jsonify({
            "ok": True,
            "authority": authority,
            "suppliers": suppliers,
            "total_incumbents": len(suppliers),
            "attribution": OGL_V3_ATTRIBUTION
        })
    except Exception as ex:
        return jsonify({"ok": False, "error": str(ex)}), 500
    finally:
        conn.close()


# ── DOWNLOAD ENDPOINT (DOCX) ───────────────────────────────────────────────────

@suppliers_bp.route("/api/suppliers/<int:supplier_id>/download", methods=["GET"])
def download_supplier_profile(supplier_id):
    """Download a complete supplier intelligence profile as a Word document (.docx)."""
    conn = _get_connection()
    cursor = conn.cursor()

    try:
        # ── Fetch supplier ─────────────────────────────────────────────────────
        _execute(cursor, conn,
                 "SELECT * FROM suppliers WHERE id = ? LIMIT 1;",
                 (supplier_id,))
        raw_sup = cursor.fetchone()
        if not raw_sup:
            return jsonify({"ok": False, "error": "Supplier not found"}), 404
        sup = _row_to_dict(cursor, raw_sup)

        # ── Fetch awards ───────────────────────────────────────────────────────
        _execute(cursor, conn, f"""
            SELECT tender_title, authority_name, cpv_code, cpv_description,
                   contract_value, currency, date_signed, contract_duration,
                   procurement_type, is_competitive, notice_type,
                   source_portal, notice_url, is_framework, {_SHARED_N_SELECT}
            FROM contract_awards
            WHERE supplier_id = ? AND source_portal IN ('Contracts Finder', 'Find a Tender')
            ORDER BY date_signed DESC NULLS LAST;
        """, (supplier_id,))
        raw_awards = cursor.fetchall()
        awards = _dedup_recurring_awards([_row_to_dict(cursor, r) for r in raw_awards])

        # ── Compute Aggregated Breakdown Metrics ──────────────────────────────
        # Framework/call-off ceilings are shared across every supplier appointed to that
        # agreement, not this supplier's own spend -- excluded from total_value, same as the
        # live supplier profile (get_supplier_detail). A single non-framework award above
        # SINGLE_AWARD_STATS_CEILING_GBP is untrustworthy scraper/source noise (see that
        # constant's docstring) and is excluded the same way, on every other aggregation path.
        total_value = sum(
            float(a.get("contract_value") or 0) for a in awards
            if not a.get("is_framework") and float(a.get("contract_value") or 0) <= SINGLE_AWARD_STATS_CEILING_GBP
        )
        avg_value = total_value / len(awards) if awards else 0
        non_comp, _known_route = _competition_stats(awards)
        non_comp_pct = round(non_comp / _known_route * 100, 1) if _known_route else 0

        auth_map: dict[str, dict[str, Any]] = {}
        cpv_map: dict[str, dict[str, Any]] = {}
        for a in awards:
            val = float(a.get("contract_value") or 0)
            auth = a.get("authority_name") or "Unknown Authority"
            if auth not in auth_map:
                auth_map[auth] = {"name": auth, "count": 0, "total": 0.0}
            auth_map[auth]["count"] += 1
            auth_map[auth]["total"] += val

            code = a.get("cpv_code") or "General"
            desc = a.get("cpv_description") or ""
            if code not in cpv_map:
                cpv_map[code] = {"code": code, "desc": desc, "count": 0, "total": 0.0}
            cpv_map[code]["count"] += 1
            cpv_map[code]["total"] += val

        top_auths = sorted(auth_map.values(), key=lambda x: (x["count"], x["total"]), reverse=True)
        top_cpvs = sorted(cpv_map.values(), key=lambda x: (x["count"], x["total"]), reverse=True)

        # ── Build DOCX ─────────────────────────────────────────────────────────
        try:
            from docx import Document
            from docx.shared import Pt, RGBColor, Inches, Cm
            from docx.enum.text import WD_ALIGN_PARAGRAPH
            from docx.enum.table import WD_TABLE_ALIGNMENT, WD_ALIGN_VERTICAL
            from docx.oxml.ns import qn
            from docx.oxml import OxmlElement
        except ImportError:
            return jsonify({
                "ok": False,
                "error": "python-docx is not installed. Run: pip install python-docx"
            }), 500

        NAVY   = RGBColor(0x1E, 0x3A, 0x8A)
        GREEN  = RGBColor(0x05, 0x96, 0x69)
        AMBER  = RGBColor(0xB4, 0x53, 0x09)
        GREY   = RGBColor(0x64, 0x74, 0x8B)
        WHITE  = RGBColor(0xFF, 0xFF, 0xFF)

        def _set_cell_bg(cell, hex_color: str):
            tc = cell._tc
            tcPr = tc.get_or_add_tcPr()
            shd = OxmlElement("w:shd")
            shd.set(qn("w:val"), "clear")
            shd.set(qn("w:color"), "auto")
            shd.set(qn("w:fill"), hex_color)
            tcPr.append(shd)

        def _set_cell_border(cell):
            tc = cell._tc
            tcPr = tc.get_or_add_tcPr()
            tcBorders = OxmlElement("w:tcBorders")
            for side in ("top", "left", "bottom", "right"):
                el = OxmlElement(f"w:{side}")
                el.set(qn("w:val"), "single")
                el.set(qn("w:sz"), "4")
                el.set(qn("w:color"), "CBD5E1")
                tcBorders.append(el)
            tcPr.append(tcBorders)

        def fmt_currency(val, currency="GBP"):
            sym = "€" if currency == "EUR" else "£"
            try:
                n = float(val or 0)
            except (TypeError, ValueError):
                return "N/A"
            if n >= 1_000_000:
                return f"{sym}{n/1_000_000:.2f}M"
            if n >= 1_000:
                return f"{sym}{n/1_000:.0f}k"
            return f"{sym}{n:,.0f}"

        doc = Document()
        for section in doc.sections:
            section.top_margin    = Cm(1.8)
            section.bottom_margin = Cm(1.8)
            section.left_margin   = Cm(1.8)
            section.right_margin  = Cm(1.8)

        style = doc.styles["Normal"]
        style.font.name = "Calibri"
        style.font.size = Pt(10)

        # Title
        p_title = doc.add_paragraph()
        p_title.paragraph_format.space_before = Pt(0)
        p_title.paragraph_format.space_after  = Pt(2)
        r_t = p_title.add_run("Supplier Intelligence & Competitive Market Report")
        r_t.bold = True; r_t.font.size = Pt(18); r_t.font.color.rgb = NAVY

        # Supplier Name Subheading
        p_name = doc.add_paragraph()
        p_name.paragraph_format.space_before = Pt(0)
        p_name.paragraph_format.space_after  = Pt(4)
        r_n = p_name.add_run(sup.get("name", "Unknown Supplier"))
        r_n.bold = True; r_n.font.size = Pt(14); r_n.font.color.rgb = NAVY

        # Profile Meta Information Table
        meta_tbl = doc.add_table(rows=2, cols=4)
        meta_tbl.style = "Table Grid"
        meta_fields = [
            [("Companies House #", sup.get("company_number") or "N/A"), ("Region", sup.get("region") or "UK"), ("SME Status", sup.get("sme_status") or "Non-SME"), ("VCSE Status", sup.get("vcse_status") or "Non-VCSE")],
            [("Contact Email", sup.get("email") or "N/A"), ("Website", sup.get("website") or "N/A"), ("Total Awards", str(len(awards))), ("Total Value", fmt_currency(total_value))]
        ]
        for r_i, row in enumerate(meta_fields):
            for c_i, (lbl, val) in enumerate(row):
                cell = meta_tbl.rows[r_i].cells[c_i]
                _set_cell_bg(cell, "F8FAFC" if r_i == 0 else "FFFFFF")
                _set_cell_border(cell)
                p = cell.paragraphs[0]; p.clear()
                p.paragraph_format.space_before = Pt(3); p.paragraph_format.space_after = Pt(3)
                r_l = p.add_run(f"{lbl}: ")
                r_l.bold = True; r_l.font.size = Pt(8.5); r_l.font.color.rgb = GREY
                r_v = p.add_run(str(val))
                r_v.font.size = Pt(8.5); r_v.font.color.rgb = NAVY if r_i == 1 and c_i >= 2 else RGBColor(0x0F, 0x17, 0x2A)

        doc.add_paragraph()

        # KPI Summary Table Header
        h_kpi = doc.add_table(rows=1, cols=1)
        h_kpi.style = "Table Grid"
        c_kpi = h_kpi.rows[0].cells[0]
        _set_cell_bg(c_kpi, "1E3A8A"); _set_cell_border(c_kpi)
        pk = c_kpi.paragraphs[0]; pk.clear()
        pk.paragraph_format.space_before = Pt(4); pk.paragraph_format.space_after = Pt(4)
        rk = pk.add_run("Executive Summary & Market KPIs")
        rk.bold = True; rk.font.size = Pt(10.5); rk.font.color.rgb = WHITE

        kpi_tbl = doc.add_table(rows=1, cols=4)
        kpi_tbl.style = "Table Grid"
        kpi_data = [
            ("Contracts Won",       str(len(awards)),          NAVY),
            ("Total Value",         fmt_currency(total_value), GREEN),
            ("Avg. Contract Value", fmt_currency(avg_value),   NAVY),
            ("Non-Competitive %",   f"{non_comp_pct}% ({non_comp} direct)", AMBER if non_comp > 0 else NAVY),
        ]
        for idx, (label, value, color) in enumerate(kpi_data):
            cell = kpi_tbl.rows[0].cells[idx]
            _set_cell_bg(cell, "EFF6FF" if idx % 2 == 0 else "F8FAFC")
            _set_cell_border(cell)
            p1 = cell.paragraphs[0]; p1.clear()
            p1.paragraph_format.space_before = Pt(5); p1.paragraph_format.space_after = Pt(2)
            rl = p1.add_run(label); rl.bold = True; rl.font.size = Pt(8); rl.font.color.rgb = GREY
            p2 = cell.add_paragraph()
            p2.paragraph_format.space_before = Pt(0); p2.paragraph_format.space_after = Pt(5)
            rv = p2.add_run(value); rv.bold = True; rv.font.size = Pt(14); rv.font.color.rgb = color

        doc.add_paragraph()

        # A table row is relatively expensive to build in python-docx (XML manipulation per
        # cell); an unbounded one-row-per-award table took 15s+ for a 1,075-award supplier in
        # testing, and scales linearly -- suppliers with several thousand awards could exceed
        # both the client's 45s abort and any reverse-proxy timeout, making the download look
        # like it silently does nothing. Cap every table below to this many rows; the full list
        # is already in the app itself.
        MAX_DOCX_AWARD_ROWS = 200

        # Breakdown Tables Section: Incumbent Authorities & Primary CPV Sectors
        h_break = doc.add_table(rows=1, cols=1)
        h_break.style = "Table Grid"
        c_break = h_break.rows[0].cells[0]
        _set_cell_bg(c_break, "1E3A8A"); _set_cell_border(c_break)
        pb = c_break.paragraphs[0]; pb.clear()
        pb.paragraph_format.space_before = Pt(4); pb.paragraph_format.space_after = Pt(4)
        rb = pb.add_run("Incumbent Authorities & CPV Sectors Breakdown")
        rb.bold = True; rb.font.size = Pt(10.5); rb.font.color.rgb = WHITE

        # Incumbent Authorities Table
        if top_auths:
            p_auth_lbl = doc.add_paragraph()
            p_auth_lbl.paragraph_format.space_before = Pt(4); p_auth_lbl.paragraph_format.space_after = Pt(2)
            ra = p_auth_lbl.add_run("Contracting Authorities Won From:")
            ra.bold = True; ra.font.size = Pt(9); ra.font.color.rgb = NAVY

            auth_tbl = doc.add_table(rows=1, cols=3)
            auth_tbl.style = "Table Grid"
            for ci, (hdr, w) in enumerate(zip(["Authority Name", "Contracts Won", "Total Value Won"], [Cm(10.0), Cm(3.5), Cm(4.0)])):
                cell = auth_tbl.rows[0].cells[ci]; cell.width = w
                _set_cell_bg(cell, "334155"); _set_cell_border(cell)
                p = cell.paragraphs[0]; p.clear()
                p.paragraph_format.space_before = Pt(2); p.paragraph_format.space_after = Pt(2)
                r = p.add_run(hdr); r.bold = True; r.font.size = Pt(8); r.font.color.rgb = WHITE

            for r_idx, a_item in enumerate(top_auths[:MAX_DOCX_AWARD_ROWS]):
                row = auth_tbl.add_row()
                bg = "F8FAFC" if r_idx % 2 == 0 else "FFFFFF"
                for ci, val in enumerate([a_item["name"], str(a_item["count"]), fmt_currency(a_item["total"])]):
                    cell = row.cells[ci]
                    _set_cell_bg(cell, bg); _set_cell_border(cell)
                    p = cell.paragraphs[0]; p.clear()
                    p.paragraph_format.space_before = Pt(2); p.paragraph_format.space_after = Pt(2)
                    r = p.add_run(str(val)); r.font.size = Pt(8)
                    if ci == 2: r.bold = True; r.font.color.rgb = GREEN

        doc.add_paragraph()

        # Detailed Contract Award Notices History Table
        h_hist = doc.add_table(rows=1, cols=1)
        h_hist.style = "Table Grid"
        c_hist = h_hist.rows[0].cells[0]
        _set_cell_bg(c_hist, "1E3A8A"); _set_cell_border(c_hist)
        ph = c_hist.paragraphs[0]; ph.clear()
        ph.paragraph_format.space_before = Pt(4); ph.paragraph_format.space_after = Pt(4)
        truncated = len(awards) > MAX_DOCX_AWARD_ROWS
        awards_for_table = awards[:MAX_DOCX_AWARD_ROWS] if truncated else awards

        title_suffix = f"({len(awards)} records" + (f", most recent {MAX_DOCX_AWARD_ROWS} shown" if truncated else "") + ")"
        rh = ph.add_run(f"Complete Contract Award Notices History  {title_suffix}")
        rh.bold = True; rh.font.size = Pt(10.5); rh.font.color.rgb = WHITE

        COL_HEADERS = [
            "Tender Title", "Authority Name", "Value", "Date Signed",
            "Duration", "Procurement Route", "CPV Code & Description", "Notice Type", "Source Portal"
        ]
        COL_WIDTHS = [
            Cm(3.8), Cm(2.8), Cm(1.8), Cm(1.6),
            Cm(1.5), Cm(2.2), Cm(3.2), Cm(1.8), Cm(1.8)
        ]

        awards_tbl = doc.add_table(rows=1, cols=len(COL_HEADERS))
        awards_tbl.style = "Table Grid"

        hrow = awards_tbl.rows[0]
        for i, (hdr, w) in enumerate(zip(COL_HEADERS, COL_WIDTHS)):
            cell = hrow.cells[i]; cell.width = w
            _set_cell_bg(cell, "1E3A8A"); _set_cell_border(cell)
            p = cell.paragraphs[0]; p.clear()
            p.paragraph_format.space_before = Pt(3); p.paragraph_format.space_after = Pt(3)
            run = p.add_run(hdr); run.bold = True; run.font.size = Pt(7.5); run.font.color.rgb = WHITE

        for row_idx, a in enumerate(awards_for_table):
            row = awards_tbl.add_row()
            bg = "EFF6FF" if row_idx % 2 == 0 else "FFFFFF"
            cpv_str = f"{a.get('cpv_code') or '—'}"
            if a.get("cpv_description"):
                cpv_str += f" ({a.get('cpv_description')})"

            cells_data = [
                a.get("tender_title") or "—",
                a.get("authority_name") or "—",
                fmt_currency(a.get("contract_value"), a.get("currency") or "GBP"),
                a.get("date_signed") or "—",
                a.get("contract_duration") or "—",
                a.get("procurement_type") or ("Open" if a.get("is_competitive") else "Direct Award"),
                cpv_str,
                a.get("notice_type") or "—",
                a.get("source_portal") or "—",
            ]
            for ci, (cell, val) in enumerate(zip(row.cells, cells_data)):
                _set_cell_bg(cell, bg); _set_cell_border(cell)
                p = cell.paragraphs[0]; p.clear()
                p.paragraph_format.space_before = Pt(2); p.paragraph_format.space_after = Pt(2)
                run = p.add_run(str(val)); run.font.size = Pt(7.5)
                if ci == 0:
                    run.bold = True
                elif ci == 2:
                    run.bold = True; run.font.color.rgb = GREEN
                elif not a.get("is_competitive") and ci == 5:
                    run.font.color.rgb = AMBER

        # Attribution Footer
        doc.add_paragraph()
        attr_para = doc.add_paragraph()
        attr_para.paragraph_format.space_before = Pt(6)
        ar = attr_para.add_run(f"Data source & Licensing: {OGL_V3_ATTRIBUTION}")
        ar.font.size = Pt(7.5); ar.font.color.rgb = GREY; ar.italic = True

        buf = io.BytesIO()
        doc.save(buf)
        buf.seek(0)

        safe_name = "".join(c if c.isalnum() else "_" for c in sup.get("name", "supplier"))

        return Response(
            buf,
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={
                "Content-Disposition": f'attachment; filename="{safe_name}_intelligence.docx"'
            }
        )

    except Exception as ex:
        return jsonify({"ok": False, "error": str(ex)}), 500
    finally:
        conn.close()




