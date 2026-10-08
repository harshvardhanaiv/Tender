"""Automated Email Notification Worker & Profile-based Best-Fit Tender Engine."""
from __future__ import annotations

import json
import re
import threading
import time
from typing import Any, Dict, List, Optional

from tender_app.email_svc import send_best_fit_digest
from etenders_scraper.sources import SOURCES, search_single_source
from etenders_scraper.filters import SearchFilters


# Standard English, business boilerplate, and address stopwords to prevent spurious keyword matches
STOPWORDS = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and", "any", "are", "aren't",
    "as", "at", "be", "because", "been", "before", "being", "below", "between", "both", "but", "by",
    "can", "cannot", "could", "did", "do", "does", "doing", "down", "during", "each", "few", "for",
    "from", "further", "had", "has", "have", "having", "he", "her", "here", "hers", "herself", "him",
    "himself", "his", "how", "if", "in", "into", "is", "it", "its", "itself", "just", "me", "more",
    "most", "my", "myself", "no", "nor", "not", "now", "of", "off", "on", "once", "only", "or",
    "other", "our", "ours", "ourselves", "out", "over", "own", "same", "she", "should", "so", "some",
    "such", "than", "that", "the", "their", "theirs", "them", "themselves", "then", "there", "these",
    "they", "this", "those", "through", "to", "too", "under", "until", "up", "very", "was", "we",
    "were", "what", "when", "where", "which", "while", "who", "whom", "why", "with", "would", "you",
    "your", "yours", "yourself", "yourselves",
    # Business boilerplate stopwords:
    "company", "limited", "ltd", "llc", "plc", "inc", "corp", "profile", "context", "services", "service",
    "solutions", "solution", "system", "systems", "provide", "provides", "providing", "provider",
    "based", "work", "works", "working", "business", "businesses", "client", "clients", "experience",
    "capabilities", "performance", "customize", "here", "overview", "established", "founded",
    "specializing", "specializes", "operates", "operating", "operation",
    # Address and registration noise:
    "road", "street", "avenue", "lane", "drive", "way", "close", "court", "house", "building", "suite", "floor",
    "postcode", "city", "county", "country", "registered", "registration", "number", "reg", "incorporation",
    "incorporated", "england", "wales", "scotland", "ireland", "london", "reading", "berkshire", "uk",
    "january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december",
    "private", "public", "general", "various", "tender", "tenders"
}

GENERIC_SEARCH_WORDS = {"services", "service", "tender", "tenders", "contract", "contracts", "support", "supply"}


def extract_user_search_and_profile_context(
    cursor, username: str, target_profile_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Extract comprehensive user search history (recent & saved searches, alert keywords)
    and active company profile (name, capabilities, sector keywords, meta).
    """
    ph = "%s"

    # 1. Fetch search history from recent_searches (only last 4 most recent searches for email alerts)
    recent_queries = []
    preferred_portals = set()
    try:
        cursor.execute(
            f"SELECT query, scope FROM recent_searches WHERE username = {ph} ORDER BY searched_at DESC, id DESC LIMIT 4",
            (username,)
        )
        for row in cursor.fetchall():
            q = (row[0] or "").strip()
            sc = (row[1] or "").strip()
            if q and len(q) >= 2 and q.lower() not in [rq.lower() for rq in recent_queries]:
                recent_queries.append(q)
            if sc and sc != "all":
                for p in sc.split(","):
                    p_clean = p.strip().lower()
                    if p_clean:
                        preferred_portals.add(p_clean)
    except Exception as e:
        print(f"[EmailNotifier] Error fetching recent_searches for {username}: {e}")

    # 2. Fetch saved searches
    saved_queries = []
    try:
        cursor.execute(
            f"SELECT query, scope FROM saved_searches WHERE username = {ph} ORDER BY id DESC LIMIT 15",
            (username,)
        )
        for row in cursor.fetchall():
            q = (row[0] or "").strip()
            sc = (row[1] or "").strip()
            if q and len(q) >= 2 and q.lower() not in [sq.lower() for sq in saved_queries]:
                saved_queries.append(q)
            if sc and sc != "all":
                for p in sc.split(","):
                    p_clean = p.strip().lower()
                    if p_clean:
                        preferred_portals.add(p_clean)
    except Exception as e:
        print(f"[EmailNotifier] Error fetching saved_searches for {username}: {e}")

    # 3. Fetch explicit alert keywords from user_prefs
    alert_keywords = []
    try:
        cursor.execute(
            f"SELECT pref_value FROM user_prefs WHERE username = {ph} AND pref_key IN ('alert_keywords', 'search_keywords')",
            (username,)
        )
        for row in cursor.fetchall():
            val = (row[0] or "").strip()
            if val:
                for kw in re.split(r"[,;\n]+", val):
                    kw_clean = kw.strip()
                    if kw_clean and len(kw_clean) >= 2 and kw_clean.lower() not in [ak.lower() for ak in alert_keywords]:
                        alert_keywords.append(kw_clean)
    except Exception as e:
        print(f"[EmailNotifier] Error fetching alert_keywords for {username}: {e}")

    # 4. Fetch company profile
    company_name = "Your Company"
    profile_text = ""
    meta_json = "{}"
    try:
        if target_profile_id:
            cursor.execute(
                f"SELECT id, name, profile_text, meta_json FROM company_profiles WHERE username = {ph} AND id = {ph}",
                (username, target_profile_id)
            )
        else:
            # Check user_prefs for selected_profile_id first
            cursor.execute(
                f"SELECT pref_value FROM user_prefs WHERE username = {ph} AND pref_key = 'selected_profile_id'",
                (username,)
            )
            pref_p = cursor.fetchone()
            sel_pid = (pref_p[0] or "").strip() if pref_p else ""
            if sel_pid and sel_pid.isdigit():
                cursor.execute(
                    f"SELECT id, name, profile_text, meta_json FROM company_profiles WHERE username = {ph} AND id = {ph}",
                    (username, int(sel_pid))
                )
            else:
                cursor.execute(
                    f"SELECT id, name, profile_text, meta_json FROM company_profiles WHERE username = {ph} ORDER BY is_default DESC, id DESC LIMIT 1",
                    (username,)
                )
        p_row = cursor.fetchone()
        if p_row:
            company_name = (p_row[1] or "Your Company").strip()
            profile_text = (p_row[2] or "").strip()
            meta_json = p_row[3] or "{}"
    except Exception as e:
        print(f"[EmailNotifier] Error fetching company profile for {username}: {e}")

    # 5. Extract core domain keywords from company profile and meta_json
    profile_keywords = []
    meta = {}
    try:
        meta = json.loads(meta_json or "{}") if isinstance(meta_json, str) else (meta_json or {})
        if isinstance(meta, dict):
            for k in ["keywords", "tags", "services", "capabilities", "sectors", "industry"]:
                val = meta.get(k)
                if isinstance(val, list):
                    profile_keywords.extend([str(item).strip().lower() for item in val if str(item).strip()])
                elif isinstance(val, str) and val.strip():
                    for token in re.split(r"[,;\n]+", val):
                        if token.strip():
                            profile_keywords.append(token.strip().lower())
    except Exception:
        pass

    # Extract meaningful domain tokens from profile_text
    if profile_text:
        clean_text = re.sub(r"(?i)company profile context for [^.]+\.?", "", profile_text)
        clean_text = re.sub(r"(?i)customize your core capabilities[^.]+\.?", "", clean_text)
        words = re.findall(r"\b[a-zA-Z]{3,}\b", clean_text)
        for w in words:
            w_lower = w.lower()
            if w_lower not in STOPWORDS and len(w_lower) >= 4 and w_lower not in profile_keywords:
                profile_keywords.append(w_lower)

    # 6. Build combined prioritized query terms for live searching
    search_queries = []
    for q in (recent_queries + saved_queries + alert_keywords):
        if q and q.lower() not in [sq.lower() for sq in search_queries]:
            search_queries.append(q)

    # Add top 2-3 specific profile domain terms if search queries are few
    for pk in profile_keywords[:5]:
        if pk.lower() not in [sq.lower() for sq in search_queries] and len(search_queries) < 6:
            search_queries.append(pk)

    return {
        "company_name": company_name,
        "profile_text": profile_text,
        "meta": meta,
        "recent_queries": recent_queries,
        "saved_queries": saved_queries,
        "alert_keywords": alert_keywords,
        "profile_keywords": profile_keywords,
        "search_queries": search_queries,
        "preferred_portals": list(preferred_portals),
    }


def compute_fit_score(
    tender: Dict[str, Any],
    profile_text: str = "",
    keywords: Optional[List[str]] = None,
    company_name: str = "Your Company",
    search_keywords: Optional[List[str]] = None,
    profile_keywords: Optional[List[str]] = None,
) -> tuple[int, str]:
    """
    Calculate fit score percentage (0-100) and human-readable reason.
    STRICT REQUIREMENT: If the tender matches NEITHER search keywords NOR company profile,
    score is 0 (strict rejection).
    """
    title = (tender.get("title") or "").strip()
    desc = (tender.get("description") or "").strip()
    buyer = (tender.get("contracting_authority") or tender.get("authority") or tender.get("buyer") or "").strip()
    cpv = (tender.get("cpv_description") or tender.get("cpv_code") or "").strip()

    title_lower = title.lower()
    full_text_lower = f"{title} {desc} {buyer} {cpv}".lower()

    if not full_text_lower.strip():
        return 0, "No tender text to evaluate."

    s_keywords = search_keywords or []
    p_keywords = profile_keywords or keywords or []

    if not s_keywords and not p_keywords:
        # Fallback profile extraction if keywords were omitted
        words = re.findall(r"\b[a-zA-Z]{4,}\b", profile_text.lower())
        p_keywords = [w for w in words if w not in STOPWORDS][:15]

    # 1. Match against Search History & Saved Keywords (active intent)
    matched_searches_title = []
    matched_searches_body = []
    for term in s_keywords:
        term_clean = term.strip().lower()
        if not term_clean or len(term_clean) < 2 or term_clean in GENERIC_SEARCH_WORDS:
            continue
        pattern = r"\b" + re.escape(term_clean) + r"\b"
        if re.search(pattern, title_lower):
            matched_searches_title.append(term)
        elif re.search(pattern, full_text_lower):
            matched_searches_body.append(term)

    # 2. Match against Company Profile Capabilities & Core Terms
    matched_profile_title = []
    matched_profile_body = []
    for term in p_keywords:
        term_clean = term.strip().lower()
        if not term_clean or len(term_clean) < 3 or term_clean in STOPWORDS or term_clean in GENERIC_SEARCH_WORDS:
            continue
        pattern = r"\b" + re.escape(term_clean) + r"\b"
        if re.search(pattern, title_lower):
            matched_profile_title.append(term)
        elif re.search(pattern, full_text_lower):
            matched_profile_body.append(term)

    total_search_matches = list(dict.fromkeys(matched_searches_title + matched_searches_body))
    total_profile_matches = list(dict.fromkeys(matched_profile_title + matched_profile_body))

    # STRICT REJECTION: If no keywords from search history and no profile terms match:
    if not total_search_matches and not total_profile_matches:
        return 0, "No match to company profile or search history keywords."

    # 3. Calculate weighted score (starting from 55 base for verified keyword alignment)
    score = 55

    # Reward Search History (strongest active user intent signal)
    if matched_searches_title:
        score += min(25, len(matched_searches_title) * 15)  # Title matches are prime
    if matched_searches_body:
        score += min(15, len(matched_searches_body) * 8)

    # Reward Company Profile capabilities
    if matched_profile_title:
        score += min(15, len(matched_profile_title) * 10)
    if matched_profile_body:
        score += min(10, len(matched_profile_body) * 5)

    # Cap between 60% and 98%
    score = max(60, min(98, score))

    # 4. Formulate crisp, personalized rationale for why this fits
    reasons = []
    if total_search_matches:
        search_terms_display = ", ".join(f"'{m}'" for m in total_search_matches[:3])
        reasons.append(f"Matches your searched keyword(s) {search_terms_display}")

    if total_profile_matches:
        profile_terms_display = ", ".join(total_profile_matches[:3])
        reasons.append(f"aligns with {company_name}'s capabilities in {profile_terms_display}")

    reason_str = " • ".join(reasons) + "."
    return score, reason_str


# Minimum gap between best-fit digests per frequency setting. The scheduler polls hourly, so
# "immediately" means "on the next poll after a new match", not a push. The weekly/daily gaps
# are trimmed by _DIGEST_DUE_GRACE_SECS so poll-time drift can't push a digest a whole extra cycle.
_FREQUENCY_MIN_GAP_SECS = {"immediately": 0, "daily": 24 * 3600, "weekly": 7 * 24 * 3600}
_DIGEST_DUE_GRACE_SECS = 5 * 60
# A weekly digest has a week of matches to cover, so it can't share the hourly cap of 8.
_FREQUENCY_MAX_TENDERS = {"immediately": 8, "daily": 15, "weekly": 25}
_LAST_DIGEST_PREF_KEY = "last_digest_sent_epoch"


def normalize_frequency(value: Any) -> str:
    """Map the stored frequency to 'immediately' | 'daily' | 'weekly'.

    The settings form saves 'Immediately'/'Daily'/'Weekly' while the email footer links save
    'instant'/'daily'/'weekly'; unrecognised values fall back to the seeded default, 'daily'."""
    v = str(value or "").strip().lower()
    if v in ("immediately", "immediate", "instant"):
        return "immediately"
    if v == "weekly":
        return "weekly"
    return "daily"


def seconds_since_last_digest(cursor, username: str) -> Optional[float]:
    """Age of this user's last real digest in seconds, or None if they've never had one.

    Test sends don't count (they never write the pref). Users emailed before this pref
    existed are bootstrapped from alerted_tenders so the switch doesn't fire one extra email."""
    cursor.execute(
        "SELECT pref_value FROM user_prefs WHERE username = %s AND pref_key = %s",
        (username, _LAST_DIGEST_PREF_KEY),
    )
    row = cursor.fetchone()
    if row and row[0]:
        try:
            return max(0.0, time.time() - float(row[0]))
        except ValueError:
            pass
    cursor.execute(
        "SELECT EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - MAX(alerted_at))) FROM alerted_tenders WHERE username = %s",
        (username,),
    )
    row = cursor.fetchone()
    return float(row[0]) if row and row[0] is not None else None


def mark_digest_sent(cursor, username: str) -> None:
    cursor.execute(
        """INSERT INTO user_prefs (username, pref_key, pref_value, updated_at)
           VALUES (%s, %s, %s, NOW())
           ON CONFLICT (username, pref_key)
           DO UPDATE SET pref_value = EXCLUDED.pref_value, updated_at = NOW()""",
        (username, _LAST_DIGEST_PREF_KEY, str(time.time())),
    )


def get_user_email_settings(cursor, username: str) -> Dict[str, Any]:
    """Fetch email preferences for given user from user_prefs table."""
    query = "SELECT pref_key, pref_value FROM user_prefs WHERE username = %s"
    cursor.execute(query, (username,))
    rows = cursor.fetchall()
    prefs = {r[0]: r[1] for r in rows}

    auto_enabled = (
        prefs.get("automated_emails_enabled", "true") != "false" and
        prefs.get("best_fit_emails_enabled", "true") != "false"
    )

    return {
        "automated_emails_enabled": auto_enabled,
        "best_fit_emails_enabled": prefs.get("best_fit_emails_enabled", "true") != "false",
        "notification_email": prefs.get("notification_email", ""),
        "min_fit_score": int(prefs.get("min_fit_score", "70")),
        # new_match_frequency wins, matching what the settings screen shows (see
        # profiles_bp.py's GET /api/email-settings); email_frequency is the older key.
        "frequency": normalize_frequency(prefs.get("new_match_frequency") or prefs.get("email_frequency")),
        "profile_id": prefs.get("selected_profile_id", ""),
        "search_keywords": prefs.get("alert_keywords", ""),
    }


def find_best_fit_tenders_for_user(
    cursor, username: str, min_score: int = 65, target_profile_id: Optional[str] = None, max_results: int = 8
) -> tuple[List[Dict[str, Any]], str, str]:
    """
    Search and rank tenders strictly matching user's search history keywords and company profile.
    Tenders with 0 relevance to the user's keywords or company are strictly excluded.
    """
    # 1. Extract unified search and profile context
    ctx = extract_user_search_and_profile_context(cursor, username, target_profile_id=target_profile_id)
    company_name = ctx["company_name"]
    profile_text = ctx["profile_text"]
    search_queries = ctx["search_queries"]
    profile_keywords = ctx["profile_keywords"]
    preferred_portals = ctx["preferred_portals"]

    # If no search history and no profile keywords exist, return empty (never send random junk)
    if not search_queries and not profile_keywords:
        print(f"[EmailNotifier] No search history or profile keywords found for user '{username}'. Skipping.")
        return [], company_name, profile_text

    candidate_tenders = []
    seen_keys = set()

    # 2. Local Query: Check cached tenders in tenders_master matching searched keywords
    try:
        active_terms = [q for q in search_queries if q.lower() not in GENERIC_SEARCH_WORDS][:4]
        if not active_terms and profile_keywords:
            active_terms = profile_keywords[:3]

        for q in active_terms:
            q_term = f"%{q.strip().lower()}%"
            cursor.execute("""
                SELECT id, portal_id, title, contracting_authority, description, url, value, closing_date, published_date, raw_json
                FROM tenders_master
                WHERE LOWER(title) LIKE %s OR LOWER(description) LIKE %s
                ORDER BY id DESC LIMIT 15
            """, (q_term, q_term))

            rows = cursor.fetchall()
            for r in rows:
                tid, portal, title, auth, desc, url, val, closing, pub, raw_j = r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9]
                unique_key = f"{portal}:{tid}"
                if unique_key not in seen_keys:
                    seen_keys.add(unique_key)
                    t_dict = {}
                    if raw_j:
                        try:
                            t_dict = json.loads(raw_j)
                        except Exception:
                            pass
                    t_dict["resource_id"] = tid
                    t_dict["source"] = portal
                    t_dict["title"] = title
                    t_dict["contracting_authority"] = auth
                    t_dict["description"] = desc
                    # The typed columns can be blank (older cache rows); a blank must never
                    # overwrite the value already present in raw_json.
                    t_dict["detail_url"] = url or t_dict.get("detail_url") or ""
                    t_dict["estimated_value"] = val or t_dict.get("estimated_value") or t_dict.get("estimated_value_eur") or ""
                    t_dict["submission_deadline"] = closing or t_dict.get("submission_deadline") or ""
                    candidate_tenders.append(t_dict)
    except Exception as local_err:
        print(f"[EmailNotifier] Local tenders_master search error for {username}: {local_err}")

    # 2.5 Refresh and sync saved search caches for this user
    try:
        from tender_app.saved_search_cache import refresh_saved_search_cache, get_cached_saved_search_results
        from tender_app.db import get_db_connection
        cursor.execute("SELECT id FROM saved_searches WHERE username = %s ORDER BY id DESC LIMIT 10", (username,))
        ss_ids = [r[0] for r in cursor.fetchall()]
        for ssid in ss_ids:
            try:
                # Hand the helpers the connection FACTORY, never this function's own
                # cursor.connection: they open and close the connection they are given (see the
                # recent-searches block below), so the shared one was closed under every later
                # query in this run -- the alerted-tender lookup, record_sent_alerts and
                # mark_digest_sent all failed, and the same tenders were re-emailed every run.
                refresh_saved_search_cache(get_db_connection, ssid, username=username)
                cached = get_cached_saved_search_results(get_db_connection, ssid, username=username, window_hours=48)
                for cr in (cached.get("rows") or []):
                    src = cr.get("source") or "etenders_ie"
                    rid = cr.get("resource_id") or cr.get("id") or cr.get("title")
                    ukey = f"{src}:{rid}"
                    if ukey not in seen_keys:
                        seen_keys.add(ukey)
                        candidate_tenders.append(cr)
            except Exception as _sse:
                print(f"[EmailNotifier] Error syncing saved search cache {ssid}: {_sse}")
    except Exception as ssc_err:
        print(f"[EmailNotifier] Saved searches cache sync error for {username}: {ssc_err}")

    # 2.6 Refresh and sync recent search caches for this user (only last 4 recent searches active for email alerts)
    try:
        from tender_app.saved_search_cache import refresh_recent_search_cache, get_cached_recent_search_results
        from tender_app.db import get_db_connection
        cursor.execute(
            "SELECT id FROM recent_searches WHERE username = %s ORDER BY searched_at DESC, id DESC LIMIT 4",
            (username,)
        )
        recent_rows = cursor.fetchall()
        for (rsid,) in recent_rows:
            try:
                # refresh_recent_search_cache/get_cached_recent_search_results each open and
                # close their own connection (the standard get_db_conn_fn contract) -- they must
                # NOT be handed this function's own shared cursor's connection (get_db_connection,
                # not `lambda: cursor.connection`), or they close it out from under every
                # subsequent query in this request (record_sent_alerts, mark_digest_sent, the
                # caller's conn.commit()), silently failing them every time. See caller for how
                # that used to surface: "Sent transactional email" logged as sent, immediately
                # followed by "cursor already closed" on the recording step right after --
                # meaning the alert was never actually recorded, so the same tenders kept
                # re-triggering a duplicate email on every subsequent scheduler run.
                refresh_recent_search_cache(get_db_connection, rsid, username=username)
                # Automatically include results from only the last 4 recent searches in email alerts
                cached = get_cached_recent_search_results(get_db_connection, rsid, username=username, window_hours=48)
                for cr in (cached.get("rows") or []):
                    src = cr.get("source") or "etenders_ie"
                    rid = cr.get("resource_id") or cr.get("id") or cr.get("title")
                    ukey = f"{src}:{rid}"
                    if ukey not in seen_keys:
                        seen_keys.add(ukey)
                        candidate_tenders.append(cr)
            except Exception as _rse:
                print(f"[EmailNotifier] Error syncing recent search cache {rsid}: {_rse}")
    except Exception as rsc_err:
        print(f"[EmailNotifier] Recent searches cache sync error for {username}: {rsc_err}")

    # 3. Live Search: Search preferred portals concurrently using user's actual keywords
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from etenders_scraper.sources import SOURCES, search_single_source

    all_source_ids = [s for s in SOURCES.keys() if s != "etenders_ni"]
    if preferred_portals:
        target_portals = [p for p in preferred_portals if p in SOURCES]
        if not target_portals:
            target_portals = all_source_ids
    else:
        target_portals = all_source_ids

    active_live_terms = [q for q in search_queries if q.lower() not in GENERIC_SEARCH_WORDS][:3]
    if not active_live_terms and profile_keywords:
        active_live_terms = profile_keywords[:3]

    def _fetch_portal(src: str, kw: str) -> List[Dict[str, Any]]:
        try:
            results, _ = search_single_source(keyword=kw, source_id=src)
            for r in results:
                if "portal" not in r:
                    r["portal"] = SOURCES.get(src, {}).get("label", src)
            return results
        except Exception:
            return []

    executor = ThreadPoolExecutor(max_workers=12)
    try:
        futures = [executor.submit(_fetch_portal, src, kw) for kw in active_live_terms for src in target_portals]
        for fut in as_completed(futures, timeout=12):
            try:
                rows = fut.result()
                for r in rows:
                    src = r.get("source") or "etenders_ie"
                    rid = r.get("resource_id") or r.get("id") or r.get("title")
                    unique_key = f"{src}:{rid}"
                    if unique_key not in seen_keys:
                        seen_keys.add(unique_key)
                        candidate_tenders.append(r)
            except Exception:
                pass
    except Exception:
        pass
    finally:
        try:
            executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass

    # 4. Strict Scoring & Filtering: Only keep tenders that truly match search keywords or company capabilities
    scored_tenders = []
    for t in candidate_tenders:
        score, reason = compute_fit_score(
            t,
            profile_text=profile_text,
            company_name=company_name,
            search_keywords=search_queries,
            profile_keywords=profile_keywords,
        )
        if score >= min_score:
            t["fit_score"] = score
            t["fit_reason"] = reason
            t["fit_band"] = "High Match" if score >= 80 else "Good Match"
            scored_tenders.append(t)

    # Sort descending by fit score
    scored_tenders.sort(key=lambda x: x.get("fit_score", 0), reverse=True)

    # Cache top scored tenders to database
    try:
        for t in scored_tenders[:25]:
            t_id = str(t.get("resource_id") or t.get("id") or "").strip()
            if not t_id:
                continue
            portal_id = str(t.get("source") or t.get("portal") or "").strip()
            title = str(t.get("title") or "")
            authority = str(t.get("contracting_authority") or t.get("authority") or "")
            description = str(t.get("description") or "")
            url = str(t.get("detail_url") or t.get("url") or t.get("link") or "")
            val = str(t.get("estimated_value_eur") or t.get("value") or "")
            closing = str(t.get("submission_deadline") or t.get("closing_date") or "")
            published = str(t.get("date_of_publication_invitation") or t.get("date_published") or "")
            raw_json = json.dumps(t, default=str)
            cursor.execute("""
                INSERT INTO tenders_master (id, portal_id, title, contracting_authority, description, url, value, closing_date, published_date, raw_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(id) DO UPDATE SET raw_json=EXCLUDED.raw_json, updated_at=CURRENT_TIMESTAMP
            """, (t_id, portal_id, title, authority, description, url, val, closing, published, raw_json))
    except Exception as _save_err:
        print("[EmailNotifier] Error caching candidate tenders:", _save_err)

    return scored_tenders[:max_results], company_name, profile_text


def get_verified_recipient_emails(cur, username: str) -> List[str]:
    """Return list of verified recipient email addresses for user. Unverified, bounced, or test domains are strictly excluded."""
    from tender_app.blueprints.profiles_bp import _get_user_recipients
    recipients = _get_user_recipients(cur, username)
    valid = []
    for r in recipients:
        em = str(r.get("email") or "").strip().lower()
        if not em or "@" not in em or "." not in em:
            continue
        if em.endswith("@example.com") or em.endswith("@test.com"):
            continue
        if r.get("verified") is True and not r.get("hard_bounced"):
            valid.append(em)
    return valid


def run_automated_email_job(get_db_conn_fn, username: str, is_test: bool = False) -> Dict[str, Any]:
    """Execute best-fit tender notification job for a user."""
    return process_automated_email_notifications(get_db_conn_fn, username, is_test=is_test)


def check_alerted_tenders_and_filter(
    cur, username: str, candidate_tenders: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Filter candidate tenders to include ONLY:
    1. Genuinely NEW tenders not previously alerted to this user.
    2. Materially CHANGED tenders (fit score ±10pts, deadline change, value change, scope update).
    Suppresses tenders marked 'not_relevant' by user feedback.
    """
    ph = "%s"
    
    # 1. Fetch user's negative feedback to suppress marked tenders
    try:
        cur.execute(f"SELECT tender_key FROM alert_feedback WHERE username = {ph} AND feedback_type = 'not_relevant'", (username,))
        negative_keys = {str(r[0]) for r in cur.fetchall()}
    except Exception:
        negative_keys = set()

    # 2. Fetch previously alerted tenders for this user
    try:
        cur.execute(f"SELECT tender_key, fit_score, submission_deadline, estimated_value, title FROM alerted_tenders WHERE username = {ph}", (username,))
        alerted_map = {str(r[0]): {"fit_score": r[1], "deadline": r[2], "value": r[3], "title": r[4]} for r in cur.fetchall()}
    except Exception:
        alerted_map = {}

    eligible = []
    for t in candidate_tenders:
        src = str(t.get("source") or "etenders_ie")
        rid = str(t.get("resource_id") or t.get("id") or "")
        key = t.get("tender_key") or f"{src}:{rid}"
        t["tender_key"] = key

        if key in negative_keys:
            continue  # Exclude user-dismissed tender

        if key not in alerted_map:
            # Genuinely NEW tender!
            t["is_new_alert"] = True
            eligible.append(t)
        else:
            # Check for MATERIAL CHANGE
            old = alerted_map[key]
            old_score = old.get("fit_score") or 0
            new_score = t.get("fit_score") or 0
            old_dl = str(old.get("deadline") or "").strip()
            new_dl = str(t.get("submission_deadline") or t.get("deadline") or "").strip()
            old_val = str(old.get("value") or "").strip()
            new_val = str(t.get("estimated_value_eur") or t.get("estimated_value") or t.get("value") or "").strip()
            old_title = str(old.get("title") or "").strip()
            new_title = str(t.get("title") or "").strip()

            change_reasons = []

            # Fit Score Change (±10 points)
            if abs(new_score - old_score) >= 10:
                change_reasons.append(f"Fit score updated ({old_score}% → {new_score}%)")

            # Deadline Change
            if new_dl and old_dl and new_dl != old_dl:
                change_reasons.append(f"Deadline moved (was {old_dl}, now {new_dl})")

            # Value Change
            if new_val and old_val and new_val != old_val:
                change_reasons.append(f"Contract value updated (now {new_val})")

            # Scope / Title Change
            if new_title and old_title and new_title != old_title:
                change_reasons.append("Tender title & scope updated")

            if change_reasons:
                t["is_new_alert"] = False
                t["material_change_reason"] = "; ".join(change_reasons)
                eligible.append(t)

    return eligible


def record_sent_alerts(cur, username: str, sent_tenders: List[Dict[str, Any]]) -> None:
    """Record alerted tenders into alerted_tenders table to prevent duplicate emails."""
    ph = "%s"
    for t in sent_tenders:
        src = str(t.get("source") or "etenders_ie")
        rid = str(t.get("resource_id") or t.get("id") or "")
        key = t.get("tender_key") or f"{src}:{rid}"
        title = str(t.get("title") or "Tender Opportunity")
        fit_score = int(t.get("fit_score", 0))
        dl = str(t.get("submission_deadline") or t.get("deadline") or "")
        val = str(t.get("estimated_value_eur") or t.get("estimated_value") or t.get("value") or "")

        try:
            cur.execute("""
                INSERT INTO alerted_tenders (username, tender_key, source, resource_id, title, fit_score, submission_deadline, estimated_value, alerted_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (username, tender_key) DO UPDATE SET
                    fit_score = EXCLUDED.fit_score,
                    submission_deadline = EXCLUDED.submission_deadline,
                    estimated_value = EXCLUDED.estimated_value,
                    title = EXCLUDED.title,
                    alerted_at = CURRENT_TIMESTAMP
            """, (username, key, src, rid, title, fit_score, dl, val))
        except Exception as _rec_err:
            print(f"[EmailNotifier] Error recording alerted tender {key}: {_rec_err}")


def process_automated_email_notifications(
    get_db_conn_fn, username: str, is_test: bool = False
) -> Dict[str, Any]:
    """Check user's saved searches, find best-fit tenders, and send email notification."""
    conn = get_db_conn_fn()
    cur = conn.cursor()

    try:
        settings = get_user_email_settings(cur, username)

        if not is_test and not settings["automated_emails_enabled"]:
            cur.close()
            conn.close()
            return {"ok": False, "message": "Automated emails are disabled for user"}

        recipient_emails = get_verified_recipient_emails(cur, username)
        if not recipient_emails:
            cur.close()
            conn.close()
            return {"ok": False, "message": "No verified notification email addresses found for user"}

        # Honour the user's chosen cadence. Checked before the search below, which hits every
        # portal live, so a user who isn't due yet costs nothing. Test sends always go through.
        frequency = settings["frequency"]
        min_gap = _FREQUENCY_MIN_GAP_SECS[frequency]
        if not is_test and min_gap:
            age = seconds_since_last_digest(cur, username)
            if age is not None and age < min_gap - _DIGEST_DUE_GRACE_SECS:
                cur.close()
                conn.close()
                return {"ok": True, "message": f"Next {frequency} digest not due yet", "count": 0}

        # Fetch best fit tenders
        tenders, company_name, profile_text = find_best_fit_tenders_for_user(
            cur, username, min_score=settings["min_fit_score"], target_profile_id=settings["profile_id"],
            max_results=_FREQUENCY_MAX_TENDERS[frequency],
        )

        if not tenders:
            cur.close()
            conn.close()
            return {"ok": True, "message": "No matching tenders found above min fit score", "count": 0}

        # PART 1: Filter out duplicate alerted tenders unless genuinely NEW or MATERIALLY CHANGED!
        eligible_tenders = check_alerted_tenders_and_filter(cur, username, tenders)

        if not eligible_tenders and not is_test:
            cur.close()
            conn.close()
            print(f"[EmailNotifier] 0 new or materially updated tenders for user '{username}'. Skipping email notification.")
            return {"ok": True, "message": "No new or materially updated tenders to notify", "count": 0}

        tenders_to_send = eligible_tenders if eligible_tenders else tenders[:3]

        display_name = username.split("@")[0].capitalize() if "@" in username else username.capitalize()
        sent_count = 0
        for recipient_email in recipient_emails:
            if send_best_fit_digest(recipient_email, display_name, tenders_to_send, company_name=company_name, profile_text=profile_text):
                sent_count += 1

        if sent_count > 0:
            record_sent_alerts(cur, username, tenders_to_send)
            if not is_test:
                mark_digest_sent(cur, username)
            conn.commit()

        cur.close()
        conn.close()

        return {
            "ok": sent_count > 0,
            "recipient": ", ".join(recipient_emails),
            "matches_count": len(tenders_to_send),
            "company_name": company_name,
            "message": f"Sent best-fit email with {len(tenders_to_send)} tender(s) to {sent_count} verified recipient(s)" if sent_count > 0 else "Failed to send email"
        }

    except Exception as e:
        if cur:
            cur.close()
        if conn:
            conn.close()
        print(f"[Email Notifier Error] Failed to run job for {username}: {e}")
        return {"ok": False, "error": str(e)}


def cleanup_invalid_and_unverified_addresses(get_db_conn_fn) -> None:
    """Clean up existing invalid/bounced addresses (such as @example.com or @test.com) and ensure strict OTP verification."""
    try:
        conn = get_db_conn_fn()
        cur = conn.cursor()
        ph = "%s"

        cur.execute("SELECT username, pref_key, pref_value FROM user_prefs WHERE pref_key = 'notification_recipients'")
        rows = cur.fetchall()
        updated_count = 0

        for uname, pkey, pval in rows:
            if not pval:
                continue
            try:
                recipients = json.loads(pval)
                if not isinstance(recipients, list):
                    continue
                changed = False
                for r in recipients:
                    em = str(r.get("email") or "").strip().lower()
                    if em.endswith("@example.com") or em.endswith("@test.com") or em in ("admin@example.com", "test233password123@example.com"):
                        r["verified"] = False
                        r["hard_bounced"] = True
                        r["bounce_reason"] = "550 5.1.1 Example/test domain"
                        changed = True
                    elif r.get("verified") and not r.get("verified_at"):
                        # Previously auto-verified without OTP flow: mark unverified
                        r["verified"] = False
                        changed = True

                if changed:
                    new_val = json.dumps(recipients)
                    cur.execute(
                        f"UPDATE user_prefs SET pref_value = %s, updated_at = NOW() WHERE username = %s AND pref_key = 'notification_recipients'",
                        (new_val, uname),
                    )
                    updated_count += 1
            except Exception:
                continue

        # Also check notification_email prefs that point to @example.com or @test.com
        cur.execute("SELECT username, pref_value FROM user_prefs WHERE pref_key = 'notification_email'")
        email_rows = cur.fetchall()
        for uname, em_val in email_rows:
            em = str(em_val or "").strip().lower()
            if em.endswith("@example.com") or em.endswith("@test.com"):
                cur.execute("UPDATE user_prefs SET pref_value = 'false', updated_at = NOW() WHERE username = %s AND pref_key = 'automated_emails_enabled'", (uname,))

        conn.commit()
        cur.close()
        conn.close()
        print(f"[Email Notifier] Cleaned up {updated_count} recipient records with strict OTP/bounce rules.", flush=True)
    except Exception as ex:
        print(f"[Email Notifier Cleanup Error] {ex}", flush=True)


def backfill_email_prefs_for_existing_users(get_db_conn_fn) -> None:
    """Ensure all existing/old users in the database have default email preferences in user_prefs."""
    try:
        conn = get_db_conn_fn()
        cur = conn.cursor()
        ph = "%s"

        cur.execute("SELECT username, email FROM users")
        users = cur.fetchall()

        count = 0
        for u in users:
            uname = u[0]
            uemail = u[1] or (uname if "@" in str(uname) else "")
            if not uname:
                continue

            defaults = [
                ("automated_emails_enabled", "true"),
                ("best_fit_emails_enabled", "true"),
                ("email_frequency", "daily"),
                ("min_fit_score", "70"),
            ]

            for pk, pv in defaults:
                cur.execute(
                    f"""INSERT INTO user_prefs (username, pref_key, pref_value)
                        VALUES ({ph}, {ph}, {ph})
                        ON CONFLICT (username, pref_key) DO NOTHING""",
                    (uname, pk, pv),
                )
            count += 1

        conn.commit()
        cur.close()
        conn.close()
        print(f"[Email Notifier] Verified/Backfilled default email settings for {count} existing user(s).")
    except Exception as ex:
        print(f"[Email Notifier Error] Failed to backfill email preferences for existing users: {ex}")


_scheduler_started = False
_scheduler_lock = threading.Lock()


def init_email_scheduler(get_db_conn_fn, interval_minutes: int = 60) -> None:
    """Launch background daemon thread to periodically process automated email digests for all registered accounts."""
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            print("[Email Scheduler] Background worker thread is already running.")
            return
        _scheduler_started = True

    def worker():
        print(f"[Email Scheduler] Initialized background worker. First check runs in 120s, repeating every {interval_minutes}m.")
        # Staggered initial delay on startup so Flask server and live searches don't collide
        time.sleep(120)
        
        # 1. Clean up invalid/bounced addresses
        cleanup_invalid_and_unverified_addresses(get_db_conn_fn)

        # 2. Backfill default email settings for old users who registered before this feature
        backfill_email_prefs_for_existing_users(get_db_conn_fn)

        while True:
            try:
                conn = get_db_conn_fn()
                cur = conn.cursor()

                # Find all registered users except those who explicitly disabled automated emails
                cur.execute("""
                    SELECT DISTINCT u.username
                    FROM users u
                    WHERE LOWER(u.username) NOT IN (
                        SELECT LOWER(username) FROM user_prefs
                        WHERE pref_key IN ('automated_emails_enabled', 'best_fit_emails_enabled')
                          AND pref_value = 'false'
                    )
                """)
                users = [row[0] for row in cur.fetchall()]
                cur.close()
                conn.close()

                print(f"[Email Scheduler] Found {len(users)} registered account(s) active for automated email notifications.")
                for user in users:
                    print(f"[Email Scheduler] Processing automated notifications for user: {user}")
                    res = run_automated_email_job(get_db_conn_fn, user, is_test=False)
                    print(f"[Email Scheduler] Automation result for {user}: {res}")

            except Exception as ex:
                print(f"[Email Scheduler Error] Worker iteration failed: {ex}")

            time.sleep(interval_minutes * 60)

    t = threading.Thread(target=worker, daemon=True, name="TenderFlow-EmailScheduler")
    t.start()
