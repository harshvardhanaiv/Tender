"""Extended schema migrations for SaaS tables (Postgres)."""
from __future__ import annotations

from typing import Any


def _pg_column_exists(cursor, table: str, column: str) -> bool:
    cursor.execute(
        """
        SELECT EXISTS (
            SELECT FROM information_schema.columns
            WHERE table_name = %s AND column_name = %s
        )
        """,
        (table, column),
    )
    return bool(cursor.fetchone()[0])


def migrate_users_table(cursor) -> None:
    """Add Firebase columns and role to users table."""
    cols = [
        ("firebase_uid", "VARCHAR(128) UNIQUE"),
        ("email", "VARCHAR(255)"),
        ("display_name", "VARCHAR(255)"),
        ("legacy_username", "VARCHAR(100)"),
        ("role", "VARCHAR(50) DEFAULT 'member'"),
    ]
    for name, typedef in cols:
        if not _pg_column_exists(cursor, "users", name):
            cursor.execute(f"ALTER TABLE users ADD COLUMN {name} {typedef}")
    # password_hash nullable for Firebase-only users
    cursor.execute(
        "ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL"
    )


def create_saas_tables(cursor) -> None:
    """Create credit, subscription, and webhook tables."""
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS credit_wallet (
            username VARCHAR(100) PRIMARY KEY,
            balance INTEGER NOT NULL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS credit_ledger (
            id SERIAL PRIMARY KEY,
            username VARCHAR(100) NOT NULL,
            delta INTEGER NOT NULL,
            balance_after INTEGER NOT NULL,
            reason VARCHAR(100) NOT NULL,
            ref_id VARCHAR(255),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_credit_ledger_username
        ON credit_ledger(username);
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id SERIAL PRIMARY KEY,
            username VARCHAR(100) NOT NULL UNIQUE,
            stripe_customer_id VARCHAR(100),
            stripe_subscription_id VARCHAR(100),
            plan VARCHAR(50) NOT NULL DEFAULT 'free',
            status VARCHAR(50) NOT NULL DEFAULT 'trialing',
            current_period_end TIMESTAMP,
            monthly_credits INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stripe_events (
            event_id VARCHAR(100) PRIMARY KEY,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS admin_audit_log (
            id SERIAL PRIMARY KEY,
            admin_email VARCHAR(255) NOT NULL,
            action VARCHAR(100) NOT NULL,
            target_username VARCHAR(100),
            details_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS generated_proposals (
            id SERIAL PRIMARY KEY,
            username VARCHAR(100) NOT NULL,
            tender_key VARCHAR(255) NOT NULL,
            title TEXT,
            company_name TEXT,
            proposal_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_gen_prop_user_key
        ON generated_proposals(username, tender_key);
    """)


def migrate_suppliers_table(cursor) -> None:
    """Add the phone column that award ingestion and the suppliers API read and write, plus the
    coordinates used by the Supplier Intelligence map/proximity filter (see
    scripts/geocode_suppliers.py, which fills them from the supplier's address)."""
    if not _pg_column_exists(cursor, "suppliers", "phone"):
        cursor.execute("ALTER TABLE suppliers ADD COLUMN phone VARCHAR(255)")
    for name, typedef in (("latitude", "NUMERIC"), ("longitude", "NUMERIC"), ("geo_accuracy", "VARCHAR(20)")):
        if not _pg_column_exists(cursor, "suppliers", name):
            cursor.execute(f"ALTER TABLE suppliers ADD COLUMN {name} {typedef}")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_suppliers_geo ON suppliers (latitude, longitude) "
        "WHERE latitude IS NOT NULL"
    )
    # Name-identity lookup used by etenders_scraper.awards.resolve_supplier_company_number; the
    # expression must match awards.SUPPLIER_NAME_KEY_SQL exactly for Postgres to use the index.
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_suppliers_name_key "
        "ON suppliers (UPPER(REGEXP_REPLACE(name, '[^A-Za-z0-9]', '', 'g')))"
    )


def migrate_contract_awards_table(cursor) -> None:
    """Add contract end date, framework flag, buyer type, and lat/long to contract_awards table."""
    cols_def = [
        ("contract_start_date", "VARCHAR(50)"),
        ("contract_end_date", "VARCHAR(50)"),
        ("is_framework", "INT DEFAULT 0"),
        ("buyer_type", "VARCHAR(100)"),
        ("latitude", "NUMERIC"),
        ("longitude", "NUMERIC"),
    ]
    for name, typedef in cols_def:
        if not _pg_column_exists(cursor, "contract_awards", name):
            cursor.execute(f"ALTER TABLE contract_awards ADD COLUMN {name} {typedef}")
    # The buyer pages match awards by normalised buyer name (buyers_bp._norm_auth_sql). Without an
    # index on that expression every lookup normalises all ~380k rows with a regex.
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_awards_authority_norm "
        "ON contract_awards (UPPER(TRIM(REGEXP_REPLACE(authority_name, '\\s+', ' ', 'g'))))"
    )
    # That index no longer matches: the buyer key was widened ("&" -> AND, leading "THE", trailing "COUNCIL",
    # punctuation), so a buyer lookup normalised all ~500k rows (a profile took ~30 s). Index the current key.
    # The index name carries a hash of the expression: "CREATE INDEX IF NOT EXISTS" keeps an existing index of the same
    # name even when the expression it was built from has changed, which silently stops it matching the queries.
    import hashlib
    from tender_app.blueprints.buyers_bp import _norm_auth_sql
    key_sql = _norm_auth_sql("authority_name")
    index_name = "idx_awards_buyer_key_" + hashlib.md5(key_sql.encode("utf-8")).hexdigest()[:8]
    cursor.execute(f"CREATE INDEX IF NOT EXISTS {index_name} ON contract_awards (({key_sql}))")
    cursor.execute(
        "SELECT indexname FROM pg_indexes WHERE tablename = 'contract_awards' "
        "AND (indexname LIKE 'idx_awards_buyer_key%%' OR indexname = 'idx_awards_authority_norm_key') AND indexname <> %s",
        (index_name,),
    )
    for (old_name,) in cursor.fetchall():   # a key from an earlier version of the expression is just dead weight
        cursor.execute(f"DROP INDEX IF EXISTS {old_name}")


def migrate_saved_searches_table(cursor) -> None:
    """Add the freshness timestamp saved-search cache refreshes update."""
    if not _pg_column_exists(cursor, "saved_searches", "last_refreshed_at"):
        cursor.execute("ALTER TABLE saved_searches ADD COLUMN last_refreshed_at TIMESTAMP")


def migrate_recent_searches_table(cursor) -> None:
    """Add per-search email alert opt-in and the freshness timestamp its cache refreshes update."""
    if not _pg_column_exists(cursor, "recent_searches", "last_refreshed_at"):
        cursor.execute("ALTER TABLE recent_searches ADD COLUMN last_refreshed_at TIMESTAMP")
    if not _pg_column_exists(cursor, "recent_searches", "email_alerts_enabled"):
        cursor.execute("ALTER TABLE recent_searches ADD COLUMN email_alerts_enabled BOOLEAN NOT NULL DEFAULT FALSE")


def create_search_cache_tables(cursor) -> None:
    """Fast-path result cache for saved/recent searches: powers instant re-open and
    the email digest's last-4-recent + saved search sync, without re-scraping live."""
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS saved_search_cache (
            id SERIAL PRIMARY KEY,
            saved_search_id INTEGER NOT NULL,
            tender_id VARCHAR(255) NOT NULL,
            first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_confirmed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status VARCHAR(20) NOT NULL DEFAULT 'active',
            tender_data TEXT NOT NULL,
            UNIQUE(saved_search_id, tender_id)
        );
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS recent_search_cache (
            id SERIAL PRIMARY KEY,
            recent_search_id INTEGER NOT NULL,
            tender_id VARCHAR(255) NOT NULL,
            first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_confirmed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status VARCHAR(20) NOT NULL DEFAULT 'active',
            tender_data TEXT NOT NULL,
            UNIQUE(recent_search_id, tender_id)
        );
    """)


def create_planning_tables(cursor) -> None:
    """Planning Leads: UK planning applications harvested nightly from PlanIt.

    Unlike tenders_master (all TEXT, live-search side-effect cache) this table is the
    primary store and is properly typed, because the Planning Leads API filters and
    sorts in SQL rather than shipping every row to the browser.

    Deliberately absent: any monetary value column. Planning registers carry no
    contract value and one must never be synthesised — same standard applied in
    awards.py, where eTenders IE/NI are excluded from supplier intelligence rather
    than invent a winner. Also absent: agent_tel and applicant_address, which are
    personal data of private individuals under UK GDPR and are not needed here.
    """
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS planning_applications (
            id                    VARCHAR(255) PRIMARY KEY,
            uid                   VARCHAR(255),
            planning_portal_id    VARCHAR(64),
            authority             VARCHAR(255),
            authority_id          INTEGER,
            country               VARCHAR(32),
            region                VARCHAR(128),
            description           TEXT,
            address               TEXT,
            postcode              VARCHAR(16),
            latitude              DOUBLE PRECISION,
            longitude             DOUBLE PRECISION,
            app_size              VARCHAR(16),
            app_state             VARCHAR(32),
            app_type              VARCHAR(32),
            n_dwellings           INTEGER,
            low_value_reason      VARCHAR(20),
            applicant_name        TEXT,
            applicant_company     TEXT,
            agent_name            TEXT,
            agent_company         TEXT,
            agent_address         TEXT,
            case_officer          TEXT,
            ward_name             VARCHAR(255),
            start_date            DATE,
            decided_date          DATE,
            consultation_end_date DATE,
            target_decision_date  DATE,
            decision              TEXT,
            docs_url              TEXT,
            detail_url            TEXT,
            planit_url            TEXT,
            lead_score            SMALLINT,
            raw_json              TEXT,
            last_changed          TIMESTAMP,
            created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    # Added after the table first shipped: paperwork and householder-scale works PlanIt
    # mistypes or oversizes as a genuine opportunity (see etenders_scraper/planning/
    # scoring.py:low_value_reason). Existing rows are classified by
    # `scripts/planning_backfill.py --rescore-all`.
    if not _pg_column_exists(cursor, "planning_applications", "low_value_reason"):
        cursor.execute(
            "ALTER TABLE planning_applications ADD COLUMN low_value_reason VARCHAR(20)"
        )
    for name, cols in [
        ("idx_planning_authority", "authority"),
        ("idx_planning_country", "country"),
        ("idx_planning_size", "app_size"),
        ("idx_planning_state", "app_state"),
        ("idx_planning_type", "app_type"),
        ("idx_planning_start", "start_date DESC"),
        ("idx_planning_decided", "decided_date DESC"),
        ("idx_planning_score", "lead_score DESC"),
        ("idx_planning_low_value", "low_value_reason"),
    ]:
        cursor.execute(
            f"CREATE INDEX IF NOT EXISTS {name} ON planning_applications({cols});"
        )
    # Full-text index backing the ?q= keyword filter. Expression must match the
    # to_tsvector(...) built in planning_bp._search_sql exactly or Postgres will
    # fall back to a sequential scan.
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_planning_fts ON planning_applications
        USING GIN (to_tsvector('english',
            coalesce(description, '') || ' ' || coalesce(address, '')));
    """)

    # PlanIt authority lookup. country is derived from the ONS GSS code prefix
    # (E/W/S/N), which is authoritative, so it never has to be guessed from a name.
    # Refreshed weekly by the harvester — it costs two requests of the daily budget.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS planning_areas (
            area_id     INTEGER PRIMARY KEY,
            area_name   VARCHAR(255),
            long_name   VARCHAR(255),
            area_type   VARCHAR(128),
            gss_code    VARCHAR(16),
            country     VARCHAR(32),
            region      VARCHAR(128),
            is_planning BOOLEAN,
            updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # Removal requests (e.g. UK GDPR erasure). Deleting the row alone is not enough: the
    # next nightly harvest would re-insert it, so the upsert skips any id listed here.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS planning_suppressions (
            id          VARCHAR(255) PRIMARY KEY,
            reason      TEXT,
            created_by  VARCHAR(255),
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # Harvest bookkeeping. requests_today/requests_date enforce PlanIt's requested
    # 300-requests-per-day ceiling across process restarts, not just within one run.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS planning_harvest_state (
            source         VARCHAR(50) PRIMARY KEY,
            last_success_at TIMESTAMP,
            last_run_at    TIMESTAMP,
            last_status    VARCHAR(50),
            last_error     TEXT,
            requests_today INTEGER DEFAULT 0,
            requests_date  DATE,
            cursor_state   TEXT
        );
    """)


def create_buyer_locations_table(cursor) -> None:
    """Where each buyer (contracting authority) is, for the buyer map and distance filter.

    Award rows only carry a coordinate when the source gave one, and the stored ones turned out
    to be a single England-wide placeholder, so buyer positions are kept here instead.
    authority_key is the buyer name upper-cased with whitespace collapsed (the same
    normalisation buyers_bp groups by). accuracy: 'council_centroid' (middle of the council's
    planning applications), 'geocoded' (a name-matched point in OpenStreetMap Nominatim), 'area_centroid'
    (a Nominatim match that is an administrative area, so approximate), or 'miss'
    (looked up, nothing trustworthy found; kept so it is not retried, coordinates NULL).
    """
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS buyer_locations (
            authority_key   TEXT PRIMARY KEY,
            authority_name  TEXT NOT NULL,
            latitude        DOUBLE PRECISION,
            longitude       DOUBLE PRECISION,
            accuracy        TEXT NOT NULL,
            matched_name    TEXT,
            source          TEXT,
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def create_market_engagement_tables(cursor) -> None:
    """Preliminary market engagement (PME) workspace for public sector buyers (see
    tender_app/blueprints/market_engagement_bp.py). Rows belong to the user who created them."""
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS market_engagements (
            id                  SERIAL PRIMARY KEY,
            username            VARCHAR(255) NOT NULL,
            organisation        TEXT,
            title               TEXT NOT NULL,
            category_json       TEXT,
            category_label      TEXT,
            est_value           NUMERIC,
            term_years          NUMERIC,
            engagement_type     VARCHAR(24) NOT NULL DEFAULT 'questionnaire',
            objectives          TEXT,
            supplier_day_at     TEXT,
            supplier_day_place  TEXT,
            response_deadline   DATE,
            contact_name        TEXT,
            contact_email       TEXT,
            status              VARCHAR(16) NOT NULL DEFAULT 'draft',
            notice_text         TEXT,
            published_url       TEXT,
            published_at        TIMESTAMP,
            closed_at           TIMESTAMP,
            converted_at        TIMESTAMP,
            created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_market_engagements_user ON market_engagements (username, updated_at DESC)"
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS market_engagement_suppliers (
            id              SERIAL PRIMARY KEY,
            engagement_id   INTEGER NOT NULL REFERENCES market_engagements(id) ON DELETE CASCADE,
            supplier_key    TEXT NOT NULL,
            supplier_name   TEXT NOT NULL,
            supplier_id     INTEGER,
            source          VARCHAR(12) NOT NULL DEFAULT 'matched',
            included        BOOLEAN NOT NULL DEFAULT TRUE,
            status          VARCHAR(16) NOT NULL DEFAULT 'not_contacted',
            note            TEXT,
            stats_json      TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (engagement_id, supplier_key)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS market_engagement_log (
            id              SERIAL PRIMARY KEY,
            engagement_id   INTEGER NOT NULL REFERENCES market_engagements(id) ON DELETE CASCADE,
            at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            kind            VARCHAR(24) NOT NULL,
            message         TEXT NOT NULL
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_market_engagement_log_eng ON market_engagement_log (engagement_id, at)"
    )


def create_growth_tables(cursor) -> None:
    """Growth Studio (tender_app/blueprints/growth_bp.py): a supplier's outreach campaigns and their
    targets, which signals they dismissed, and the buyers who asked not to be contacted. Every row
    belongs to the user who made it. profile_id has no foreign key on purpose: deleting a company
    profile must not delete the campaigns run under it (profile_name keeps what it was called)."""
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS growth_campaigns (
            id                SERIAL PRIMARY KEY,
            username          VARCHAR(255) NOT NULL,
            profile_id        INTEGER,
            profile_name      TEXT,
            name              TEXT NOT NULL,
            filters_json      TEXT,
            category_label    TEXT,
            channel           VARCHAR(12) NOT NULL DEFAULT 'email',
            status            VARCHAR(12) NOT NULL DEFAULT 'draft',
            subject           TEXT NOT NULL DEFAULT '',
            body              TEXT NOT NULL DEFAULT '',
            created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_activity_at  TIMESTAMP
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_growth_campaigns_user ON growth_campaigns (username, updated_at DESC)"
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS growth_campaign_targets (
            id             SERIAL PRIMARY KEY,
            campaign_id    INTEGER NOT NULL REFERENCES growth_campaigns(id) ON DELETE CASCADE,
            buyer_key      TEXT NOT NULL,
            buyer_name     TEXT NOT NULL,
            signal_key     TEXT NOT NULL,
            signal_type    VARCHAR(12) NOT NULL,
            snapshot_json  TEXT,
            included       BOOLEAN NOT NULL DEFAULT TRUE,
            contact_email  TEXT,
            status         VARCHAR(12) NOT NULL DEFAULT 'not_sent',
            sent_at        TIMESTAMP,
            replied_at     TIMESTAMP,
            meeting_at     TIMESTAMP,
            note           TEXT,
            created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (campaign_id, buyer_key)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS growth_signal_state (
            username    VARCHAR(255) NOT NULL,
            profile_id  INTEGER NOT NULL DEFAULT 0,
            signal_key  TEXT NOT NULL,
            state       VARCHAR(12) NOT NULL DEFAULT 'dismissed',
            updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (username, profile_id, signal_key)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS growth_suppressions (
            username    VARCHAR(255) NOT NULL,
            buyer_key   TEXT NOT NULL,
            buyer_name  TEXT,
            reason      VARCHAR(24),
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (username, buyer_key)
        )
        """
    )


def run_saas_migrations(conn: Any) -> None:
    cursor = conn.cursor()
    try:
        create_buyer_locations_table(cursor)
        from tender_app.stats import ensure_stats_tables
        ensure_stats_tables(cursor)
        migrate_users_table(cursor)
        create_saas_tables(cursor)
        migrate_suppliers_table(cursor)
        migrate_contract_awards_table(cursor)
        migrate_saved_searches_table(cursor)
        migrate_recent_searches_table(cursor)
        create_search_cache_tables(cursor)
        create_planning_tables(cursor)
        create_market_engagement_tables(cursor)
        create_growth_tables(cursor)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
