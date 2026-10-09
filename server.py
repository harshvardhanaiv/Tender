#!/usr/bin/env python3

from __future__ import annotations



try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import io
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import smtplib
import hashlib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone

from pathlib import Path

from typing import Any, Iterable



from flask import Flask, jsonify, request, send_file, redirect, session, Response
import os
import time

APP_DIR = Path(__file__).parent

def load_env():
    env_path = APP_DIR / ".env"
    if env_path.exists():
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        key, val = line.split("=", 1)
                        os.environ.setdefault(key.strip(), val.strip())
        except Exception as e:
            print("Warning: Failed to load .env file:", e)

load_env()

from tender_app.db import get_db_connection
from tender_app.db_ext import run_saas_migrations
from tender_app.firebase_auth import init_firebase
from tender_app.config import (
    CREDIT_COST_AI_DOWNLOAD,
    CREDIT_COST_ANALYSE,
    CREDIT_COST_FIT_SCORE,
    CREDIT_COST_PROPOSAL,
    CREDIT_COST_SUMMARY,
    CREDIT_COST_ENRICH,
    CREDIT_COST_GANTT,
    CREDIT_COST_METHODOLOGY,
    ENABLE_BUYER_WORKSPACE,
    ENABLE_GROWTH_STUDIO,
    ENABLE_SCHEDULERS,
    ENABLE_EMAIL_SCHEDULER,
    SESSION_COOKIE_SECURE,
    SESSION_IDLE_TIMEOUT_SECONDS,
    SENTRY_DSN,
)
from tender_app.metering import require_credits
from tender_app.blueprints.auth_bp import init_auth_blueprint
from tender_app.blueprints.billing_bp import init_billing_blueprint
from tender_app.blueprints.admin_bp import init_admin_blueprint
from tender_app.llm_json import parse_llm_json
from tender_app.blueprints.profiles_bp import init_profiles_blueprint
from tender_app.blueprints.search_bp import search_bp
from tender_app.blueprints.ai_bp import ai_bp
from tender_app.blueprints.suppliers_bp import suppliers_bp, init_suppliers_blueprint
from tender_app.blueprints.buyers_bp import buyers_bp, init_buyers_blueprint
from tender_app.blueprints.planning_bp import init_planning_blueprint
from tender_app.blueprints.market_radar_bp import init_market_radar_blueprint
from tender_app.blueprints.market_engagement_bp import init_market_engagement_blueprint
from tender_app.blueprints.growth_bp import init_growth_blueprint
from tender_app.email_notifier import init_email_scheduler
from tender_app.security import ensure_csrf_token, validate_csrf



from etenders_scraper.fields import label_to_key

from etenders_scraper.parser import DETAIL_URL_TEMPLATE, parse_detail_fields

from etenders_scraper.scraper import EtendersScraper

from etenders_scraper.cft_documents import list_cft_documents, fetch_and_extract_text, _extract_bytes

from etenders_scraper.sources import (
    SOURCES,
    parse_tender_key,
    search_ie_only_page,
    search_single_source,
    source_public_url,
    tender_key,
)
from etenders_scraper.sources.progressive_search import (
    get_job,
    job_snapshot,
    start_progressive_search,
)
from etenders_scraper.sources.registry import UK_IE_SOURCE_IDS



STATIC_DIR = APP_DIR / "web"

def init_db():
    os.makedirs("uploads/profile_docs", exist_ok=True)
    os.makedirs("uploads/user_docs", exist_ok=True)

    db_host = os.environ.get("DB_HOST", "localhost")
    db_port = os.environ.get("DB_PORT", "5432")
    db_name = os.environ.get("DB_NAME", "postgres")

    try:
        conn = get_db_connection(connect_timeout=3)
    except Exception as e:
        raise SystemExit(
            f"Cannot connect to PostgreSQL at {db_host}:{db_port}/{db_name}: {e}. Check the DB_* settings in .env and that the database is running."
        )

    try:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username VARCHAR(100) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS analyses (
                id SERIAL PRIMARY KEY,
                username VARCHAR(100) NOT NULL,
                source VARCHAR(100) NOT NULL,
                resource_id VARCHAR(255) NOT NULL,
                tender_title TEXT NOT NULL,
                company TEXT,
                files_json TEXT,
                analysis_json TEXT NOT NULL,
                type VARCHAR(50) NOT NULL DEFAULT 'analysis',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        # Check if table company_profiles exists
        cursor.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'public'
                AND table_name = 'company_profiles'
            );
        """)
        table_exists = cursor.fetchone()[0]
        
        if not table_exists:
            cursor.execute("""
                CREATE TABLE company_profiles (
                    id SERIAL PRIMARY KEY,
                    username VARCHAR(100) NOT NULL DEFAULT 'admin',
                    name VARCHAR(100) NOT NULL,
                    profile_text TEXT NOT NULL,
                    meta_json TEXT,
                    is_default BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(username, name)
                );
            """)
        else:
            # Check if username column exists
            cursor.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.columns 
                    WHERE table_name='company_profiles' AND column_name='username'
                );
            """)
            col_exists = cursor.fetchone()[0]
            if not col_exists:
                # Add column username
                cursor.execute("ALTER TABLE company_profiles ADD COLUMN username VARCHAR(100) NOT NULL DEFAULT 'admin';")
                # Drop existing unique constraints on name
                cursor.execute("""
                    SELECT conname 
                    FROM pg_constraint 
                    WHERE conrelid = 'company_profiles'::regclass 
                    AND contype = 'u';
                """)
                constraints = cursor.fetchall()
                for con in constraints:
                    conname = con[0]
                    cursor.execute(f"ALTER TABLE company_profiles DROP CONSTRAINT IF EXISTS {conname};")
                # Add compound unique constraint
                cursor.execute("ALTER TABLE company_profiles ADD CONSTRAINT company_profiles_username_name_key UNIQUE (username, name);")
            # Add meta_json column if missing (PostgreSQL)
            cursor.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.columns 
                    WHERE table_name='company_profiles' AND column_name='meta_json'
                );
            """)
            if not cursor.fetchone()[0]:
                cursor.execute("ALTER TABLE company_profiles ADD COLUMN meta_json TEXT;")

            # Add is_default column if missing
            cursor.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.columns 
                    WHERE table_name='company_profiles' AND column_name='is_default'
                );
            """)
            if not cursor.fetchone()[0]:
                cursor.execute("ALTER TABLE company_profiles ADD COLUMN is_default BOOLEAN NOT NULL DEFAULT FALSE;")
                cursor.execute("""
                    UPDATE company_profiles cp 
                    SET is_default = TRUE 
                    WHERE cp.id = (
                        SELECT cp2.id FROM company_profiles cp2 
                        WHERE cp2.username = cp.username 
                        ORDER BY cp2.id ASC LIMIT 1
                    ) 
                    AND NOT EXISTS (
                        SELECT 1 FROM company_profiles cp3 
                        WHERE cp3.username = cp.username AND cp3.is_default = TRUE
                    );
                """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS company_profile_documents (
                id SERIAL PRIMARY KEY,
                profile_id INT NOT NULL,
                username VARCHAR(100) NOT NULL,
                filename VARCHAR(255) NOT NULL,
                file_path TEXT NOT NULL,
                file_size BIGINT NOT NULL,
                issue_date VARCHAR(50),
                expiry_date VARCHAR(50),
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_profile FOREIGN KEY (profile_id) REFERENCES company_profiles(id) ON DELETE CASCADE
            );
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_documents (
                id SERIAL PRIMARY KEY,
                username VARCHAR(100) NOT NULL,
                filename VARCHAR(255) NOT NULL,
                file_path TEXT NOT NULL,
                file_size BIGINT NOT NULL,
                issue_date VARCHAR(50),
                expiry_date VARCHAR(50),
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        for tbl in ["company_profile_documents", "user_documents"]:
            for col in ["issue_date", "expiry_date"]:
                cursor.execute(f"""
                    DO $$ 
                    BEGIN 
                        BEGIN
                            ALTER TABLE {tbl} ADD COLUMN {col} VARCHAR(50);
                        EXCEPTION
                            WHEN duplicate_column THEN NULL;
                        END;
                    END $$;
                """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS fit_scores (
                id SERIAL PRIMARY KEY,
                username VARCHAR(100) NOT NULL,
                tender_key VARCHAR(255) NOT NULL,
                profile_id VARCHAR(50) NOT NULL DEFAULT '',
                score INTEGER NOT NULL,
                band VARCHAR(20) NOT NULL,
                reasons_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(username, tender_key, profile_id)
            );
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS saved_searches (
                id SERIAL PRIMARY KEY,
                username VARCHAR(100) NOT NULL,
                name VARCHAR(200) NOT NULL,
                query VARCHAR(500) NOT NULL,
                scope VARCHAR(500) NOT NULL DEFAULT 'all',
                filters_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(username, name)
            );
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS recent_searches (
                id SERIAL PRIMARY KEY,
                username VARCHAR(100) NOT NULL,
                query VARCHAR(500) NOT NULL,
                scope VARCHAR(500) NOT NULL DEFAULT 'all',
                searched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(username, query, scope)
            );
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_prefs (
                username VARCHAR(100) NOT NULL,
                pref_key VARCHAR(100) NOT NULL,
                pref_value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (username, pref_key)
            );
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pipeline (
                id SERIAL PRIMARY KEY,
                username VARCHAR(100) NOT NULL,
                tender_key VARCHAR(255) NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                source VARCHAR(100) NOT NULL DEFAULT '',
                contracting_authority TEXT,
                submission_deadline TEXT,
                estimated_value TEXT,
                stage VARCHAR(50) NOT NULL DEFAULT 'watching',
                notes TEXT,
                fit_score INTEGER,
                fit_band VARCHAR(20),
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(username, tender_key)
            );
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS answer_bank (
                id SERIAL PRIMARY KEY,
                username VARCHAR(100) NOT NULL,
                category VARCHAR(100) NOT NULL DEFAULT 'General',
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                profile_id INTEGER REFERENCES company_profiles(id) ON DELETE SET NULL,
                file_name VARCHAR(255),
                file_content TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Add profile_id, file_name, file_content to answer_bank if missing
        cursor.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.columns
                WHERE table_name='answer_bank' AND column_name='profile_id'
            );
        """)
        if not cursor.fetchone()[0]:
            cursor.execute("ALTER TABLE answer_bank ADD COLUMN profile_id INTEGER REFERENCES company_profiles(id) ON DELETE SET NULL;")
        cursor.execute("SELECT EXISTS (SELECT FROM information_schema.columns WHERE table_name='answer_bank' AND column_name='file_name');")
        if not cursor.fetchone()[0]:
            cursor.execute("ALTER TABLE answer_bank ADD COLUMN file_name VARCHAR(255);")
        cursor.execute("SELECT EXISTS (SELECT FROM information_schema.columns WHERE table_name='answer_bank' AND column_name='file_content');")
        if not cursor.fetchone()[0]:
            cursor.execute("ALTER TABLE answer_bank ADD COLUMN file_content TEXT;")

        # Previous winning bids uploaded by the user; used as style/structure reference in proposals.
        # profile_id NULL = available to every company profile of that user.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS winning_bids (
                id SERIAL PRIMARY KEY,
                username VARCHAR(100) NOT NULL,
                profile_id INTEGER REFERENCES company_profiles(id) ON DELETE CASCADE,
                title VARCHAR(255) NOT NULL,
                buyer VARCHAR(255),
                contract_year VARCHAR(10),
                file_name VARCHAR(255),
                extracted_text TEXT NOT NULL,
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_winning_bids_user_profile ON winning_bids (username, profile_id);")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS translation_cache (
                text_hash VARCHAR(64) NOT NULL,
                target_lang VARCHAR(10) NOT NULL,
                original_text TEXT NOT NULL,
                translated_text TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (text_hash, target_lang)
            );
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS email_notifications (
                id SERIAL PRIMARY KEY,
                username VARCHAR(100) NOT NULL,
                tender_key VARCHAR(255) NOT NULL,
                days_threshold INTEGER NOT NULL,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(username, tender_key, days_threshold)
            );
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS alerted_tenders (
                id SERIAL PRIMARY KEY,
                username VARCHAR(255) NOT NULL,
                tender_key VARCHAR(255) NOT NULL,
                source VARCHAR(100),
                resource_id VARCHAR(255),
                title TEXT,
                fit_score INTEGER DEFAULT 0,
                submission_deadline VARCHAR(100),
                estimated_value VARCHAR(100),
                scope_hash VARCHAR(100),
                alerted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(username, tender_key)
            );
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS alert_feedback (
                id SERIAL PRIMARY KEY,
                username VARCHAR(255) NOT NULL,
                tender_key VARCHAR(255) NOT NULL,
                feedback_type VARCHAR(50) NOT NULL,
                reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tenders_master (
                id VARCHAR(255) PRIMARY KEY,
                portal_id VARCHAR(100),
                title TEXT,
                contracting_authority TEXT,
                location TEXT,
                description TEXT,
                region TEXT,
                country TEXT,
                url TEXT,
                value TEXT,
                closing_date TEXT,
                published_date TEXT,
                notice_type TEXT,
                cpv_codes TEXT,
                raw_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tenders_portal ON tenders_master(portal_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tenders_country ON tenders_master(country);")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS suppliers (
                id SERIAL PRIMARY KEY,
                company_number VARCHAR(100) UNIQUE NOT NULL,
                name TEXT NOT NULL,
                address TEXT,
                email VARCHAR(255),
                website VARCHAR(255),
                region VARCHAR(100),
                sme_status VARCHAR(50) DEFAULT 'Non-SME',
                vcse_status VARCHAR(50) DEFAULT 'Non-VCSE',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_suppliers_cnum ON suppliers(company_number);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_suppliers_name ON suppliers(name);")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS contract_awards (
                id SERIAL PRIMARY KEY,
                supplier_id INT REFERENCES suppliers(id) ON DELETE SET NULL,
                company_number VARCHAR(100),
                supplier_name TEXT,
                authority_name TEXT,
                tender_title TEXT,
                cpv_code VARCHAR(100),
                cpv_description TEXT,
                contract_value NUMERIC,
                currency VARCHAR(20) DEFAULT 'GBP',
                date_signed VARCHAR(50),
                contract_duration VARCHAR(100),
                procurement_type VARCHAR(100),
                is_competitive INT DEFAULT 1,
                notice_type VARCHAR(100),
                source_portal VARCHAR(100),
                notice_url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_awards_supplier ON contract_awards(supplier_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_awards_cnum ON contract_awards(company_number);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_awards_authority ON contract_awards(authority_name);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_awards_cpv ON contract_awards(cpv_code);")
        # Ingest dedups every award on notice_url; without this each check scans the table
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_awards_notice_url ON contract_awards(notice_url);")
        # Widen columns that were originally VARCHAR(255): real buyer/supplier names (e.g.
        # multi-authority consortia) can run well past that and were being rejected outright.
        cursor.execute("ALTER TABLE suppliers ALTER COLUMN name TYPE TEXT;")
        cursor.execute("ALTER TABLE contract_awards ALTER COLUMN supplier_name TYPE TEXT;")
        cursor.execute("ALTER TABLE contract_awards ALTER COLUMN authority_name TYPE TEXT;")

        conn.commit()
        cursor.close()
        conn.close()
        print(f"[DATABASE TARGET] Active DB Engine: PostgreSQL (Host: '{db_host}', Port: '{db_port}', Database: '{db_name}')")
        _run_saas_migrations_pg()
    except Exception as e:
        raise SystemExit(f"Failed database initialization setup: {e}")

def _run_saas_migrations_pg():
    try:
        conn = get_db_connection()
        run_saas_migrations(conn)
        conn.close()
    except Exception as e:
        print(f"SaaS schema migration warning: {e}")

def generate_tender_id(row):
    if not isinstance(row, dict):
        return ""
    rid = str(row.get("resource_id") or row.get("id") or "").strip()
    if rid:
        return rid
    url = str(row.get("detail_url") or row.get("url") or row.get("link") or "").strip()
    title = str(row.get("title") or "").strip()
    portal = str(row.get("source") or row.get("portal") or "").strip()
    if url:
        return hashlib.md5(url.encode('utf-8')).hexdigest()
    raw = f"{portal}:{title}".lower()
    return hashlib.md5(raw.encode('utf-8')).hexdigest()

# Portal source key -> country, for tenders_master.country (scrapers don't emit one).
_SOURCE_COUNTRY = {
    "etenders_ie": "Ireland", "etenders_ni": "Northern Ireland", "sell2wales": "Wales",
    "pcs": "Scotland", "find_tender": "United Kingdom", "contracts_finder": "United Kingdom",
    "sam_gov": "United States", "canadabuys": "Canada", "eu_ted": "European Union",
    "austender": "Australia", "gebiz": "Singapore", "gets_nz": "New Zealand",
    "boamp": "France", "bund": "Germany",
}


def upsert_tenders(rows):
    if not rows or not isinstance(rows, list):
        return
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        for r in rows:
            if not isinstance(r, dict):
                continue
            t_id = generate_tender_id(r)
            if not t_id:
                continue
            portal_id = str(r.get("source") or r.get("portal") or "").strip()
            title = str(r.get("title") or "")
            authority = str(r.get("contracting_authority") or r.get("authority") or "")
            location = str(r.get("location") or "")
            description = str(r.get("description") or "")
            region = str(r.get("region") or "")
            country = str(r.get("country") or _SOURCE_COUNTRY.get(portal_id, ""))
            url = str(r.get("url") or r.get("link") or r.get("detail_url") or "")
            val = str(r.get("value") or r.get("estimated_value") or r.get("estimated_value_eur") or "")
            closing = str(r.get("closing_date") or r.get("deadline") or r.get("submission_deadline") or r.get("deadline_date") or "")
            published = str(r.get("published_date") or r.get("date_published") or r.get("created_at") or "")
            notice_type = str(r.get("notice_type") or "")
            cpv = str(r.get("cpv_codes") or "")
            raw_json = json.dumps(r, default=str)

            cursor.execute("""
                INSERT INTO tenders_master (
                    id, portal_id, title, contracting_authority, location, description,
                    region, country, url, value, closing_date, published_date, notice_type, cpv_codes, raw_json
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(id) DO UPDATE SET
                    title = EXCLUDED.title,
                    contracting_authority = EXCLUDED.contracting_authority,
                    location = EXCLUDED.location,
                    description = EXCLUDED.description,
                    region = EXCLUDED.region,
                    country = EXCLUDED.country,
                    url = EXCLUDED.url,
                    value = EXCLUDED.value,
                    closing_date = EXCLUDED.closing_date,
                    published_date = EXCLUDED.published_date,
                    notice_type = EXCLUDED.notice_type,
                    cpv_codes = EXCLUDED.cpv_codes,
                    raw_json = EXCLUDED.raw_json,
                    updated_at = CURRENT_TIMESTAMP;
            """, (t_id, portal_id, title, authority, location, description, region, country, url, val, closing, published, notice_type, cpv, raw_json))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error upserting tenders: {e}")

def search_tenders_db(q="", limit=500):
    rows = []
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        query_sql = "SELECT raw_json FROM tenders_master"
        params = []
        where_clauses = []

        q_clean = q.strip().lower() if q and q.strip() and q.strip().lower() != "all" else ""
        if q_clean:
            pattern = f"%{q_clean}%"
            where_clauses.append("(LOWER(title) LIKE %s OR LOWER(contracting_authority) LIKE %s OR LOWER(description) LIKE %s OR LOWER(location) LIKE %s OR LOWER(raw_json) LIKE %s)")
            params.extend([pattern, pattern, pattern, pattern, pattern])

        if where_clauses:
            query_sql += " WHERE " + " AND ".join(where_clauses)

        query_sql += " ORDER BY created_at DESC LIMIT " + str(limit)

        cursor.execute(query_sql, params)
        fetched = cursor.fetchall()

        for item in fetched:
            raw_str = item[0]
            if raw_str:
                try:
                    r_obj = json.loads(raw_str)
                    if q_clean:
                        sup = (r_obj.get("supplier_name") or r_obj.get("awarded_supplier") or r_obj.get("supplier") or "").lower()
                        auth = (r_obj.get("contracting_authority") or r_obj.get("authority_name") or "").lower()
                        desc = (r_obj.get("description") or r_obj.get("scope") or "").lower()
                        title = (r_obj.get("title") or "").lower()
                        if q_clean in title:
                            r_obj["_match_reason"] = "title"
                        elif q_clean in sup:
                            r_obj["_match_reason"] = "supplier name"
                        elif q_clean in auth:
                            r_obj["_match_reason"] = "authority"
                        elif q_clean in desc:
                            r_obj["_match_reason"] = "scope text"
                    rows.append(r_obj)
                except Exception:
                    pass

        # Also search contract_awards for awarded contracts matching supplier, title, authority, cpv description
        if q_clean:
            try:
                ph = "%s"
                award_sql = f"""
                    SELECT id, supplier_name, authority_name, tender_title, cpv_code, cpv_description,
                           contract_value, currency, date_signed, contract_duration, procurement_type,
                           notice_type, source_portal, notice_url
                    FROM contract_awards
                    WHERE LOWER(supplier_name) LIKE {ph}
                       OR LOWER(tender_title) LIKE {ph}
                       OR LOWER(authority_name) LIKE {ph}
                       OR LOWER(cpv_description) LIKE {ph}
                    LIMIT 100
                """
                cursor.execute(award_sql, [pattern, pattern, pattern, pattern])
                award_rows = cursor.fetchall()
                for ar in award_rows:
                    aid, sname, aname, ttitle, ccode, cdesc, cval, curr, dsigned, cdur, ptype, ntype, sportal, nurl = ar
                    src_id = "find_tender"
                    if sportal:
                        s_lower = sportal.lower()
                        if "scotland" in s_lower or "pcs" in s_lower:
                            src_id = "pcs"
                        elif "wales" in s_lower:
                            src_id = "sell2wales"
                        elif "ni" in s_lower:
                            src_id = "etenders_ni"
                        elif "ireland" in s_lower or "ie" in s_lower:
                            src_id = "etenders_ie"
                        elif "contract" in s_lower and "finder" in s_lower:
                            src_id = "contracts_finder"

                    desc = cdesc or ""
                    if sname:
                        desc = f"Awarded supplier: {sname}. {desc}".strip()
                    val_str = f"£{float(cval):,.0f}" if cval else ""
                    match_reason = "supplier name" if q_clean in (sname or "").lower() else "scope text" if q_clean in (cdesc or "").lower() else "authority" if q_clean in (aname or "").lower() else "title"

                    rows.append({
                        "id": f"award_{aid}",
                        "resource_id": f"award_{aid}",
                        "title": ttitle or f"Contract Award to {sname}",
                        "contracting_authority": aname or "Public Authority",
                        "supplier_name": sname or "",
                        "awarded_supplier": sname or "",
                        "awarded_to": sname or "",
                        "winner": sname or "",
                        "description": desc,
                        "status": "Awarded",
                        "notice_type": ntype or "Contract Award",
                        "source": src_id,
                        "source_label": sportal or "Find a Tender",
                        "detail_url": nurl or "",
                        "estimated_value_eur": val_str,
                        "date_published": dsigned or "",
                        "cpv_codes": ccode or "",
                        "contract_duration_in_months_or_years_including_any_options_and_renewals": cdur or "",
                        "_match_reason": match_reason,
                    })
            except Exception as e_award:
                print(f"Error querying contract_awards: {e_award}")

        conn.close()
    except Exception as e:
        print(f"Error searching tenders in DB: {e}")
    return rows

def translate_texts(texts: list[str], target_lang: str) -> list[str]:
    if not texts or target_lang.lower() not in ("fr", "nl"):
        return texts

    import hashlib
    
    def chunk_text(text: str, max_chars: int = 4000) -> list[str]:
        if len(text) <= max_chars:
            return [text]
        paragraphs = text.split("\n")
        chunks = []
        current_chunk = []
        current_len = 0
        for p in paragraphs:
            if current_len + len(p) + 1 > max_chars:
                if current_chunk:
                    chunks.append("\n".join(current_chunk))
                    current_chunk = []
                    current_len = 0
                if len(p) > max_chars:
                    sentences = p.split(". ")
                    for s in sentences:
                        if current_len + len(s) + 2 > max_chars:
                            if current_chunk:
                                chunks.append(". ".join(current_chunk) + ".")
                                current_chunk = []
                                current_len = 0
                            if len(s) > max_chars:
                                for i in range(0, len(s), max_chars):
                                    chunks.append(s[i:i+max_chars])
                            else:
                                current_chunk.append(s)
                                current_len += len(s) + 2
                        else:
                            current_chunk.append(s)
                            current_len += len(s) + 2
                else:
                    current_chunk.append(p)
                    current_len += len(p) + 1
            else:
                current_chunk.append(p)
                current_len += len(p) + 1
        if current_chunk:
            chunks.append("\n".join(current_chunk))
        return chunks

    def translate_single_text_chunked(text: str, lang: str) -> str:
        if not text or not text.strip():
            return text
        from deep_translator import GoogleTranslator
        import time
        import random
        
        chunks = chunk_text(text)
        translated_chunks = []
        translator = GoogleTranslator(source='auto', target=lang)
        for chunk in chunks:
            if not chunk.strip():
                translated_chunks.append(chunk)
                continue
            
            translated_ok = False
            for attempt in range(3):
                try:
                    translated_chunks.append(translator.translate(chunk))
                    translated_ok = True
                    break
                except Exception as e:
                    if attempt < 2:
                        time.sleep(1 + random.random() * 2)
                    else:
                        try:
                            print("Translation error on text chunk after 3 attempts:", str(e).encode('ascii', errors='replace').decode('ascii'))
                        except Exception:
                            pass
                        translated_chunks.append(chunk)
        return "\n".join(translated_chunks)

    # Prepare list for results
    results = [None] * len(texts)
    missing_indices = []
    missing_texts = []
    
    conn = get_db_connection()
    cursor = conn.conn.cursor() if hasattr(conn, "conn") else conn.cursor()
    
    hashes = []
    for i, text in enumerate(texts):
        if not text or not text.strip():
            results[i] = text
            continue
        text_hash = hashlib.sha256(text.encode('utf-8')).hexdigest()
        hashes.append((i, text, text_hash))
        
    if hashes:
        for idx, orig_text, h in hashes:
            try:
                cursor.execute(
                    "SELECT translated_text FROM translation_cache WHERE text_hash = %s AND target_lang = %s", 
                    (h, target_lang)
                )
                row = cursor.fetchone()
                if row:
                    results[idx] = row[0]
                else:
                    missing_indices.append(idx)
                    missing_texts.append(orig_text)
            except Exception as e:
                missing_indices.append(idx)
                missing_texts.append(orig_text)

    # Translate missing texts using deep-translator batch API with individual fallback
    if missing_texts:
        try:
            from deep_translator import GoogleTranslator
            import time
            
            translator = GoogleTranslator(source='auto', target=target_lang)
            batch_size = 20
            translations = []
            
            for i in range(0, len(missing_texts), batch_size):
                chunk = missing_texts[i:i+batch_size]
                chunk_translations = None
                for attempt in range(3):
                    try:
                        chunk_translations = translator.translate_batch(chunk)
                        break
                    except Exception as e:
                        if attempt < 2:
                            time.sleep(2)
                        else:
                            try:
                                print("Translation batch error, falling back to individual: " + str(e).encode('ascii', errors='replace').decode('ascii'))
                            except Exception:
                                pass
                            chunk_translations = [translate_single_text_chunked(item, target_lang) for item in chunk]
                
                if chunk_translations:
                    translations.extend(chunk_translations)
                else:
                    translations.extend(chunk)
                time.sleep(0.5)
            
            if len(translations) == len(missing_texts):
                for m_idx, orig_text, trans_text in zip(missing_indices, missing_texts, translations):
                    results[m_idx] = trans_text
                    h = hashlib.sha256(orig_text.encode('utf-8')).hexdigest()
                    try:
                        cursor.execute(
                            """INSERT INTO translation_cache (text_hash, target_lang, original_text, translated_text) 
                               VALUES (%s, %s, %s, %s)
                               ON CONFLICT (text_hash, target_lang) DO UPDATE SET translated_text = EXCLUDED.translated_text""",
                            (h, target_lang, orig_text, trans_text)
                        )
                    except Exception as db_err:
                        try:
                            print("Error caching translation:", str(db_err).encode('ascii', errors='replace').decode('ascii'))
                        except Exception:
                            pass
                conn.commit()
            else:
                for m_idx, orig_text in zip(missing_indices, missing_texts):
                    results[m_idx] = orig_text
        except Exception as api_err:
            try:
                print("Error during deep-translator translation:", str(api_err).encode('ascii', errors='replace').decode('ascii'))
            except Exception:
                pass
            for m_idx, orig_text in zip(missing_indices, missing_texts):
                results[m_idx] = orig_text
            
    cursor.close()
    conn.close()
    
    for i, res in enumerate(results):
        if res is None:
            results[i] = texts[i]
            
    return results

# Legacy env login removed — Firebase Auth only

# ── Secrets — loaded from .env (never hardcoded) ─────────────────────────────
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
if not DEEPSEEK_API_KEY:
    print("WARNING: DEEPSEEK_API_KEY is not set. AI features (Fit Score, Analysis, Bid Response) will fail.")

DEEPSEEK_MODEL   = "deepseek-chat"
DEEPSEEK_URL     = "https://api.deepseek.com/chat/completions"


def _deepseek_friendly_error(http_err) -> str:
    """Convert a urllib HTTPError from DeepSeek into a user-friendly message."""
    import json as _json
    try:
        body = http_err.read().decode("utf-8", errors="replace")
        msg = _json.loads(body).get("error", {})
        if isinstance(msg, dict):
            msg = msg.get("message", body)
    except Exception:
        msg = str(http_err)
    if http_err.code == 429:
        return "DeepSeek is rate-limited right now — too many requests. Please wait 30–60 seconds and try again."
    if http_err.code == 402:
        return "DeepSeek account has insufficient balance. Top up at https://platform.deepseek.com/top-up"
    if http_err.code == 401:
        return "Invalid DeepSeek API key — check DEEPSEEK_API_KEY in your .env file."
    return f"DeepSeek API error {http_err.code}: {msg}"


class AIServiceError(Exception):
    def __init__(self, message, status_code=500):
        super().__init__(message)
        self.status_code = status_code


def get_ai_credentials():
    """Retrieve AI credentials from request headers or fall back to system env variables."""
    try:
        from flask import request
        provider = request.headers.get("X-AI-Provider")
        api_key = request.headers.get("X-AI-Key")
        model = request.headers.get("X-AI-Model")
    except RuntimeError:
        # Called outside of a Flask request context (e.g. streaming generator)
        provider = api_key = model = None
    
    if not provider or provider == "system":
        return "deepseek", os.environ.get("DEEPSEEK_API_KEY", ""), "deepseek-chat"
        
    if provider in ("claude", "anthropic") and not api_key:
        api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY", "")

    return provider, api_key, model


def call_chat_api(system: str, user: str, max_tokens: int = 4000, temperature: float = 0.3, response_format_json: bool = False, provider: str = None, api_key: str = None, model: str = None) -> str:
    """Unified function to call DeepSeek, OpenAI, Gemini, or Claude based on headers/env settings."""
    h_provider, h_api_key, h_model = get_ai_credentials()
    
    p = provider or h_provider
    k = api_key or h_api_key
    m = model or h_model
    
    if not k:
        raise ValueError(f"API key is not configured for provider: {p.upper()}")
        
    if p == "deepseek":
        return _call_deepseek(system, user, k, m, max_tokens, temperature, response_format_json)
    elif p == "openai":
        return _call_openai(system, user, k, m, max_tokens, temperature, response_format_json)
    elif p == "gemini":
        return _call_gemini(system, user, k, m, max_tokens, temperature, response_format_json)
    elif p in ("claude", "anthropic"):
        return _call_claude(system, user, k, m, max_tokens, temperature, response_format_json)
    else:
        raise ValueError(f"Unsupported AI provider: {p}")


def call_deepseek_insight(system: str, user: str, max_tokens: int = 500, temperature: float = 0.2, timeout: float = 25) -> str:
    """DeepSeek only, with the server's own DEEPSEEK_API_KEY and a short timeout, for Market Radar's buyer insight.

    Unlike call_chat_api this ignores the caller's X-AI-Provider / X-AI-Key headers: the buyer insight is a
    system feature on a fixed provider, never the user's chosen model or BYO key. Raises ValueError when the
    key is not configured and AIServiceError (or a urllib/socket error) when DeepSeek fails or times out.
    """
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        raise ValueError("API key is not configured for provider: DEEPSEEK")
    return _call_deepseek(system, user, key, DEEPSEEK_MODEL, max_tokens, temperature, False, timeout=timeout)


def call_chat_json(system: str, user: str, max_tokens: int = 4000, temperature: float = 0.3,
                   provider: str = None, api_key: str = None, model: str = None,
                   expect: type = dict, retries: int = 1):
    """call_chat_api + tolerant JSON parsing, with one stricter retry if the reply is unusable.

    Honours the caller's chosen provider / BYO key exactly like call_chat_api. Every JSON-returning
    AI feature should use this rather than calling a provider directly and json.loads-ing the text.
    """
    last_err = None
    for attempt in range(retries + 1):
        prompt = user if attempt == 0 else (
            user + "\n\nYour previous reply was not valid JSON. Reply again with ONLY the JSON value: "
                   "no markdown fences, no commentary, complete and properly closed."
        )
        raw = call_chat_api(system=system, user=prompt, max_tokens=max_tokens,
                            temperature=temperature if attempt == 0 else 0.0,
                            response_format_json=True, provider=provider, api_key=api_key, model=model)
        try:
            parsed = parse_llm_json(raw)
            if isinstance(parsed, expect):
                return parsed
            last_err = ValueError(f"expected JSON {expect.__name__}, got {type(parsed).__name__}")
        except ValueError as exc:
            last_err = exc
        print(f"[AI] unusable JSON from model (attempt {attempt + 1}/{retries + 1}): {last_err}")
    raise AIServiceError(f"The AI returned an unusable response ({last_err}). Please try again.", status_code=502)


def _call_deepseek(system: str, user: str, api_key: str, model: str, max_tokens: int, temperature: float, response_format_json: bool, timeout: float = 120) -> str:
    import urllib.request
    import json
    
    url = "https://api.deepseek.com/chat/completions"
    payload_dict = {
        "model": model or "deepseek-chat",
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": []
    }
    if system:
        payload_dict["messages"].append({"role": "system", "content": system})
    payload_dict["messages"].append({"role": "user", "content": user})
    
    if response_format_json:
        payload_dict["response_format"] = {"type": "json_object"}
        
    payload = json.dumps(payload_dict).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read())
            return result["choices"][0]["message"]["content"].strip()
    except urllib.error.HTTPError as http_err:
        raise AIServiceError(_deepseek_friendly_error(http_err), status_code=http_err.code) from None


def _call_openai(system: str, user: str, api_key: str, model: str, max_tokens: int, temperature: float, response_format_json: bool) -> str:
    import urllib.request
    import json
    
    url = "https://api.openai.com/v1/chat/completions"
    payload_dict = {
        "model": model or "gpt-4o",
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": []
    }
    if system:
        payload_dict["messages"].append({"role": "system", "content": system})
    payload_dict["messages"].append({"role": "user", "content": user})
    
    if response_format_json:
        payload_dict["response_format"] = {"type": "json_object"}
        
    payload = json.dumps(payload_dict).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read())
            return result["choices"][0]["message"]["content"].strip()
    except urllib.error.HTTPError as http_err:
        body = ""
        try:
            body = http_err.read().decode("utf-8", errors="replace")
            err_data = json.loads(body)
            msg = err_data.get("error", {}).get("message", body)
        except Exception:
            msg = body or str(http_err)
        raise AIServiceError(f"OpenAI API error {http_err.code}: {msg}", status_code=http_err.code) from None


def _call_gemini(system: str, user: str, api_key: str, model: str, max_tokens: int, temperature: float, response_format_json: bool) -> str:
    import urllib.request
    import json
    
    model_name = model or os.environ.get("GEMINI_DEFAULT_MODEL", "gemini-1.5-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
    
    payload_dict = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": user}]
            }
        ]
    }
    
    if system:
        payload_dict["systemInstruction"] = {
            "parts": [{"text": system}]
        }
        
    generation_config = {
        "temperature": temperature,
        "maxOutputTokens": max_tokens
    }
    if response_format_json:
        generation_config["responseMimeType"] = "application/json"
        
    payload_dict["generationConfig"] = generation_config
    
    payload = json.dumps(payload_dict).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
        method="POST",
    )
    
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read())
            try:
                candidate = result["candidates"][0]
                text = candidate["content"]["parts"][0]["text"].strip()
                return text
            except (KeyError, IndexError):
                raise ValueError(f"Gemini returned an unexpected response structure: {json.dumps(result)}")
    except urllib.error.HTTPError as http_err:
        body = ""
        try:
            body = http_err.read().decode("utf-8", errors="replace")
            err_data = json.loads(body)
            msg = err_data.get("error", {}).get("message", body)
        except Exception:
            msg = body or str(http_err)
        raise AIServiceError(f"Gemini API error {http_err.code}: {msg}", status_code=http_err.code) from None


def _call_claude(system: str, user: str, api_key: str, model: str, max_tokens: int, temperature: float, response_format_json: bool) -> str:
    import urllib.request
    import json

    url = "https://api.anthropic.com/v1/messages"
    prompt_user = user
    if response_format_json and "json" not in prompt_user.lower():
        prompt_user += "\n\nCRITICAL: Return ONLY valid JSON format with no markdown blocks or extra text."

    payload_dict = {
        "model": model or os.environ.get("ANTHROPIC_DEFAULT_MODEL", "claude-sonnet-5"),
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [
            {"role": "user", "content": prompt_user}
        ]
    }
    if system:
        payload_dict["system"] = system

    payload = json.dumps(payload_dict).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read())
            content_blocks = result.get("content", [])
            for block in content_blocks:
                if block.get("type") == "text":
                    return block.get("text", "").strip()
            return ""
    except urllib.error.HTTPError as http_err:
        body = ""
        try:
            body = http_err.read().decode("utf-8", errors="replace")
            err_data = json.loads(body)
            msg = err_data.get("error", {}).get("message", body)
        except Exception:
            msg = body or str(http_err)
        raise AIServiceError(f"Claude API error {http_err.code}: {msg}", status_code=http_err.code) from None

def _filter_answer_bank_by_stage1(ab_rows, stage1_result):
    """Return answer bank rows relevant to the ITT categories found in Stage 1 dissection."""
    if not stage1_result:
        return ab_rows

    # Collect category signals from Stage 1
    signals = set()
    for q in (stage1_result.get("itt_question_responses") or stage1_result.get("itt_questions") or []):
        cat = str(q.get("category") or q.get("section") or "").lower()
        if cat:
            signals.add(cat)
        q_txt = str(q.get("question_text") or "").lower()
        if q_txt:
            signals.add(q_txt)
    for k in ("evaluation_criteria", "evaluation_factors", "criteria"):
        for ec in (stage1_result.get(k) or []):
            if isinstance(ec, dict):
                signals.add(str(ec.get("criterion") or ec.get("name") or "").lower())
            else:
                signals.add(str(ec).lower())

    if not signals:
        return ab_rows  # No filter possible — return all

    # Map well-known Answer Bank categories to ITT signal keywords
    CAT_MAP = {
        "health & safety": {"health", "safety", "h&s", "hse"},
        "social value": {"social", "value", "community", "equality"},
        "quality": {"quality", "iso", "assurance", "management"},
        "methodology": {"methodology", "approach", "technical", "delivery"},
        "experience": {"experience", "case study", "reference", "track record"},
        "team": {"team", "personnel", "staff", "cv", "resource"},
        "pricing": {"price", "cost", "commercial", "fee"},
        "company overview": {"company", "organisation", "background"},
    }

    matched = []
    unmatched = []
    for row in ab_rows:
        cat_key = str(row[0]).lower()
        keywords = CAT_MAP.get(cat_key, {cat_key})
        # Check if any keyword matches a signal or category itself matches
        if any(kw in sig for kw in keywords for sig in signals) or any(sig in cat_key for sig in signals):
            matched.append(row)
        else:
            unmatched.append(row)

    # Always include matched first; pad with unmatched if budget allows
    return matched + unmatched

PAGE_SIZE = 100
API_VERSION = 6  # non-blocking per-portal parallel search with live updates

SEARCH_CACHE: dict[str, tuple[list[dict[str, Any]], dict[str, Any]]] = {}
_JOB_BY_CACHE_KEY: dict[str, str] = {}



DETAIL_FIELDS: tuple[str, ...] = (

    "Description",

    "Procurement Type",

    "Estimated value (EUR)",

    "CPV Codes",

    "Procedure",

    "Contract duration in months or years, including any options and renewals",

    "End of clarification period",

    "Allow suppliers to make an online Expression Of Interest",

    "Contract awarded in Lots",

    "EU funding",

    "Date of Publication/Invitation",

    "TED links for published notices",

)

DETAIL_KEYS = tuple(label_to_key(x) for x in DETAIL_FIELDS)

EXPORT_COLUMNS = [

    "source_label",

    "resource_id",

    "detail_url",

    "title",

    "description",

    "contracting_authority",

    "submission_deadline",

    "estimated_value_eur",

    "cpv_codes",

    "procedure",

    "procurement_type",

    "contract_duration_in_months_or_years_including_any_options_and_renewals",

    "end_of_clarification_period",

    "allow_suppliers_to_make_an_online_expression_of_interest",

    "contract_awarded_in_lots",

    "eu_funding",

    "date_of_publication_invitation",

    "ted_links_for_published_notices",

]

HEADER_LABELS = {

    "source_label": "Source",

    "resource_id": "Reference / Resource ID",

    "detail_url": "Tender URL",

    "title": "Title",

    "description": "Description",

    "contracting_authority": "Contracting authority",

    "submission_deadline": "Submission deadline",

    "estimated_value_eur": "Estimated Value",

    "cpv_codes": "CPV Codes",

    "procedure": "Procedure Type",

    "procurement_type": "Procurement Type",

    "contract_duration_in_months_or_years_including_any_options_and_renewals": "Contract Duration",

    label_to_key("Description"): "Description",

    label_to_key("Procurement Type"): "Procurement Type",

    label_to_key("Estimated value (EUR)"): "Estimated Value",

    label_to_key("CPV Codes"): "CPV Codes",

    label_to_key("Procedure"): "Procedure Type",

    label_to_key("Time-limit for receipt of tenders or requests to participate"): "Tender Receipt Deadline",

    label_to_key("Deadline for dispatching invitations"): "Invitation Dispatch Deadline",

    label_to_key("End of clarification period"): "Clarification Period End",

    label_to_key("Tenders Opening Date"): "Tenders Opening Date",

    label_to_key("Allow suppliers to make an online Expression Of Interest"): "Online Expression Of Interest Allowed",

    label_to_key("Contract awarded in Lots"): "Contract Awarded in Lots",

    label_to_key("Contract duration in months or years, including any options and renewals"): "Contract Duration",

    label_to_key("Validity of Tender in days or months"): "Tender Validity",

    label_to_key("Multiple tenders will be accepted"): "Multiple Tenders Accepted",

    label_to_key("EU funding"): "EU Funding",

    label_to_key("Date of Publication/Invitation"): "Publication/Invitation Date",

    label_to_key("TED links for published notices"): "TED Notice Links",
}


# ── Dynamic non-eTenders document crawler ───────────────────────────────────
def list_non_etenders_documents(source: str, resource_id: str, detail_url: str = "") -> list[dict[str, str]]:
    if not detail_url:
        try:
            details = _get_tender_details_dict(source, resource_id)
            detail_url = details.get("detail_url") or ""
        except Exception:
            pass
    
    if not detail_url:
        return []

    import urllib.parse
    import requests
    from bs4 import BeautifulSoup

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    docs = []
    seen_urls = {detail_url.split("#")[0]}

    try:
        resp = requests.get(detail_url, headers=headers, timeout=12)
        if resp.status_code != 200:
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue

            resolved_url = urllib.parse.urljoin(detail_url, href)
            clean_url = resolved_url.split("#")[0]
            if clean_url in seen_urls:
                continue

            parsed_sub = urllib.parse.urlparse(clean_url)
            if parsed_sub.scheme not in ("http", "https"):
                continue

            sub_path = parsed_sub.path.lower()
            if any(sub_path.endswith(ext) for ext in (".pdf", ".docx", ".doc", ".txt", ".xlsx", ".zip")):
                seen_urls.add(clean_url)
                filename = clean_url.split("/")[-1] or a.get_text(" ", strip=True) or "document"
                filename = urllib.parse.unquote(filename)
                title = a.get_text(" ", strip=True) or filename
                docs.append({
                    "doc_id": clean_url,
                    "title": title,
                    "filename": filename,
                    "is_direct_url": "1"
                })
                if len(docs) >= 8:
                    break
    except Exception as e:
        print(f"[list_non_etenders_documents] Error: {e}")

    return docs


def fetch_tender_documents_and_extract(source: str, resource_id: str, detail_url: str, max_chars: int = 120000) -> tuple[str, list[str], list[dict[str, str]]]:
    """Retrieve all available documents from the portal and extract text content.
    Returns (combined_text, list_of_filenames, list_of_docs_meta).
    """
    cfg = SOURCES.get(source) if source else None
    if not cfg or not resource_id:
        return "", [], []

    doc_text = ""
    doc_names = []
    docs_meta = []

    if cfg.get("type") == "etenders":
        docs = list_cft_documents(cfg["base_url"], resource_id)
        docs_meta = docs
        for doc in docs:
            text = fetch_and_extract_text(
                cfg["base_url"], resource_id, doc["doc_id"], doc["filename"],
                max_chars=max_chars,
            )
            if text.strip():
                doc_names.append(doc["title"] or doc["filename"])
                doc_text += f"\n\n=== Document: {doc['title']} ===\n{text}"
    elif cfg.get("type") == "bravo":
        from etenders_scraper.sources.bravo_search import fetch_bravo_details
        site_root = cfg.get("site_root", "https://www.sell2wales.gov.wales")
        bravo_detail = fetch_bravo_details(source, resource_id, site_root=site_root)
        if bravo_detail and bravo_detail.get("full_text"):
            doc_names.append(f"Notice Details ({cfg.get('label', 'Sell2Wales')})")
            formatted = f"=== Notice Metadata & Details ===\n"
            formatted += f"Title: {bravo_detail.get('title')}\n"
            formatted += f"Authority: {bravo_detail.get('contracting_authority')} ({bravo_detail.get('contact_email')})\n"
            formatted += f"Deadlines: Submission={bravo_detail.get('submission_deadline')} | Clarification={bravo_detail.get('clarification_deadline')} | Award={bravo_detail.get('award_date')}\n"
            formatted += f"Value: {bravo_detail.get('estimated_value_eur')}\n"
            formatted += f"Procedure: {bravo_detail.get('procedure')} (Framework: {bravo_detail.get('is_framework')})\n\n"
            if bravo_detail.get("procurement_description"):
                formatted += f"--- Procurement Scope & Description ---\n{bravo_detail['procurement_description']}\n\n"
            if bravo_detail.get("lots_breakdown"):
                formatted += f"--- Lots Breakdown & Award Criteria ---\n{bravo_detail['lots_breakdown']}\n\n"
            if bravo_detail.get("submission_instructions"):
                formatted += f"--- Submission Instructions & Portal Details ---\n{bravo_detail['submission_instructions']}\n\n"
            formatted += f"--- Complete Notice Text ---\n{bravo_detail['full_text']}"
            doc_text += f"\n\n{formatted}"

        # Also list and crawl any linked PDFs or attachments
        docs = list_non_etenders_documents(source, resource_id, detail_url)
        docs_meta = docs
        for doc in docs:
            doc_url = doc["doc_id"]
            import requests
            try:
                headers = {
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                }
                sub_resp = requests.get(doc_url, headers=headers, timeout=15)
                if sub_resp.status_code == 200:
                    text = _extract_bytes(sub_resp.content, doc["filename"], max_chars=max_chars)
                    if text.strip():
                        doc_names.append(doc["title"] or doc["filename"])
                        doc_text += f"\n\n=== Document: {doc['title']} ===\n{text}"
            except Exception as e:
                print(f"Failed to auto-download document {doc_url}: {e}")
    else:
        # Non-etenders portals: dynamically crawl linked PDFs and docx
        docs = list_non_etenders_documents(source, resource_id, detail_url)
        docs_meta = docs
        for doc in docs:
            doc_url = doc["doc_id"]
            import requests
            try:
                headers = {
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                }
                sub_resp = requests.get(doc_url, headers=headers, timeout=15)
                if sub_resp.status_code == 200:
                    text = _extract_bytes(sub_resp.content, doc["filename"], max_chars=max_chars)
                    if text.strip():
                        doc_names.append(doc["title"] or doc["filename"])
                        doc_text += f"\n\n=== Document: {doc['title']} ===\n{text}"
            except Exception as e:
                print(f"Failed to auto-download document {doc_url}: {e}")

    # Fallback to notice page scraping if no documents were extracted
    if not doc_text and detail_url:
        url_text = scrape_tender_url_text(detail_url, max_chars=max_chars)
        if url_text.strip():
            doc_names.append(f"Tender Notice Page ({detail_url})")
            doc_text += f"\n\n=== Tender Notice Page ===\n{url_text}"

    return doc_text, doc_names, docs_meta


# ── URL scraping helper ─────────────────────────────────────────────────────
def scrape_tender_url_text(url: str, max_chars: int = 8000) -> str:
    """Fetch a tender notice URL, crawl up to 3 relevant sub-links or documents,
    and return the combined plain-text content.
    """
    if not url:
        return ""
    
    import urllib.parse
    import re
    import requests
    from bs4 import BeautifulSoup
    from etenders_scraper.cft_documents import _extract_bytes

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
    }
    
    try:
        # Fetch primary page
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        
        # Check if primary page is binary (like directly linking to a PDF)
        content_type = resp.headers.get("Content-Type", "").lower()
        if "html" not in content_type:
            # It's a direct PDF or doc
            filename = url.split("/")[-1] or "document"
            text = _extract_bytes(resp.content, filename, max_chars=max_chars)
            return text
            
        soup = BeautifulSoup(resp.text, "html.parser")
        
        # Remove scripts, styles, metadata
        for tag in soup(["script", "style", "noscript", "head", "iframe"]):
            tag.decompose()
            
        primary_text = soup.get_text(separator="\n", strip=True)
        primary_text = re.sub(r"\n{3,}", "\n\n", primary_text)
        
        results = [f"=== Primary Notice Page: {url} ===\n{primary_text}"]
        
        # Find all links to crawl
        links_to_crawl = []
        seen_urls = {url.split("#")[0]}
        
        # Parse host of primary URL to prioritize same-domain links
        parsed_primary = urllib.parse.urlparse(url)
        primary_host = parsed_primary.netloc.lower()
        
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
                
            resolved_url = urllib.parse.urljoin(url, href)
            clean_url = resolved_url.split("#")[0]
            if clean_url in seen_urls:
                continue
            
            parsed_sub = urllib.parse.urlparse(clean_url)
            if parsed_sub.scheme not in ("http", "https"):
                continue
                
            sub_host = parsed_sub.netloc.lower()
            sub_path = parsed_sub.path.lower()
            sub_query = parsed_sub.query.lower()
            
            # Exclude known external non-procurement domains
            exclude_domains = ("twitter.com", "facebook.com", "linkedin.com", "youtube.com", 
                               "google.com", "instagram.com", "t.co", "youtu.be", "pinterest.com")
            if any(domain in sub_host for domain in exclude_domains):
                continue
                
            # Filter for relevance
            is_relevant = False
            
            # 1. Same host is highly relevant (likely sub-pages or direct document downloads)
            if sub_host == primary_host:
                # Still check if it looks like a junk link (login, cookie policies, help)
                junk_keywords = ("/login", "/register", "/signin", "/help", "/cookie", "/privacy", "/terms")
                if not any(kw in sub_path for kw in junk_keywords):
                    is_relevant = True
                    
            # 2. Known procurement portals are highly relevant
            procurement_domains = ("find-tender.service.gov.uk", "contractsfinder.service.gov.uk", 
                                   "sell2wales.gov.wales", "publiccontractsscotland.gov.uk", 
                                   "etenders.gov.ie", "etendersni.gov.uk", "eu-supply.com", "proactis")
            if any(domain in sub_host for domain in procurement_domains):
                is_relevant = True
                
            # 3. Path keywords indicating specs or docs
            spec_keywords = ("tender", "opportunity", "notice", "contract", "document", 
                             "attachment", "specification", "spec", "guidance", "itt", "rfp", "download", "cft")
            if any(kw in sub_path or kw in sub_query for kw in spec_keywords):
                is_relevant = True
                
            # 4. Extension checks
            if any(sub_path.endswith(ext) for ext in (".pdf", ".docx", ".doc", ".txt", ".xlsx", ".zip")):
                is_relevant = True
                
            if is_relevant:
                seen_urls.add(clean_url)
                link_text = a.get_text(" ", strip=True) or "Linked Document"
                links_to_crawl.append((clean_url, link_text))
                if len(links_to_crawl) >= 3: # Limit to 3 relevant links to keep it fast
                    break
                    
        # Crawl the sub-links
        for sub_url, link_label in links_to_crawl:
            try:
                sub_resp = requests.get(sub_url, headers=headers, timeout=8)
                sub_resp.raise_for_status()
                
                sub_ct = sub_resp.headers.get("Content-Type", "").lower()
                if "html" not in sub_ct:
                    # Parse binary file
                    filename = sub_url.split("/")[-1] or link_label or "document"
                    # Add extensions if missing based on content-type
                    if "." not in filename:
                        if "pdf" in sub_ct:
                            filename += ".pdf"
                        elif "word" in sub_ct or "docx" in sub_ct:
                            filename += ".docx"
                    sub_text = _extract_bytes(sub_resp.content, filename, max_chars=40000)
                    if sub_text.strip():
                        results.append(f"\n=== Linked Document: {link_label} ({sub_url}) ===\n{sub_text}")
                else:
                    # HTML sub-page
                    sub_soup = BeautifulSoup(sub_resp.text, "html.parser")
                    for tag in sub_soup(["script", "style", "noscript", "head", "iframe"]):
                        tag.decompose()
                    sub_text = sub_soup.get_text(separator="\n", strip=True)
                    sub_text = re.sub(r"\n{3,}", "\n\n", sub_text)
                    if sub_text.strip():
                        results.append(f"\n=== Linked Page: {link_label} ({sub_url}) ===\n{sub_text}")
            except Exception as e:
                print(f"[scrape_tender_url_text] Failed to crawl sub-link {sub_url}: {e}")
                
        combined_text = "\n\n".join(results)
        return combined_text[:max_chars]
        
    except Exception as exc:
        print(f"[scrape_tender_url_text] Failed to scrape {url}: {exc}")
        return ""


def _find_tender_in_cache(source: str, resource_id: str) -> dict[str, Any] | None:
    # 1. Search in SEARCH_CACHE
    for cache_key, (rows, meta) in SEARCH_CACHE.items():
        for row in rows:
            if (row.get("resource_id") == resource_id or row.get("ocid") == resource_id or row.get("id") == resource_id) and (not source or row.get("source") == source):
                return row
                
    # 2. Search in progressive search jobs
    from etenders_scraper.sources.progressive_search import _JOBS, _JOBS_LOCK
    with _JOBS_LOCK:
        for job in list(_JOBS.values()):
            with job.lock:
                for row in job.rows:
                    if (row.get("resource_id") == resource_id or row.get("ocid") == resource_id or row.get("id") == resource_id) and (not source or row.get("source") == source):
                        return row

    # 3. Search database tenders_master
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        ph = "%s"
        cur.execute(
            f"SELECT raw_json FROM tenders_master WHERE id={ph} OR id={ph} OR id LIKE {ph} LIMIT 1",
            (resource_id, f"{source}:{resource_id}", f"%{resource_id}%")
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row and row[0]:
            try:
                parsed = json.loads(row[0])
                if parsed and isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
    except Exception as ex:
        pass

    # 4. Search database contract_awards if it's an award notice
    raw_str = str(resource_id or "")
    if "award_" in raw_str.lower() or source in {"award", "awards"}:
        try:
            aid_part = raw_str.lower().split("award_")[-1].split(":")[0]
            if aid_part.isdigit():
                aid = int(aid_part)
                conn = get_db_connection()
                cur = conn.cursor()
                ph = "%s"
                cur.execute(
                    f"""
                    SELECT id, supplier_name, authority_name, tender_title, cpv_code, cpv_description,
                           contract_value, currency, date_signed, contract_duration, procurement_type,
                           notice_type, source_portal, notice_url
                    FROM contract_awards
                    WHERE id = {ph}
                    LIMIT 1
                    """,
                    (aid,)
                )
                ar = cur.fetchone()
                cur.close()
                conn.close()
                if ar:
                    aid, sname, aname, ttitle, ccode, cdesc, cval, curr, dsigned, cdur, ptype, ntype, sportal, nurl = ar
                    src_id = "find_tender"
                    if sportal:
                        s_lower = sportal.lower()
                        if "scotland" in s_lower or "pcs" in s_lower:
                            src_id = "pcs"
                        elif "wales" in s_lower:
                            src_id = "sell2wales"
                        elif "ni" in s_lower:
                            src_id = "etenders_ni"
                        elif "ireland" in s_lower or "ie" in s_lower:
                            src_id = "etenders_ie"
                        elif "contract" in s_lower and "finder" in s_lower:
                            src_id = "contracts_finder"

                    desc = cdesc or ""
                    if sname:
                        desc = f"Awarded supplier: {sname}. {desc}".strip()
                    val_str = f"£{float(cval):,.0f}" if cval else ""
                    return {
                        "id": f"award_{aid}",
                        "resource_id": f"award_{aid}",
                        "title": ttitle or f"Contract Award to {sname or 'Supplier'}",
                        "contracting_authority": aname or "Public Authority",
                        "authority_name": aname or "Public Authority",
                        "supplier_name": sname or "",
                        "awarded_supplier": sname or "",
                        "awarded_to": sname or "",
                        "winner": sname or "",
                        "description": desc,
                        "status": "Awarded",
                        "notice_type": ntype or "Contract Award",
                        "source": src_id,
                        "source_label": sportal or "Find a Tender",
                        "source_portal": sportal or "Find a Tender",
                        "detail_url": nurl or "",
                        "contract_value": val_str,
                        "estimated_value_eur": val_str,
                        "date_signed": dsigned or "",
                        "award_date": dsigned or "",
                        "date_published": dsigned or "",
                        "cpv_codes": ccode or "",
                        "contract_duration": cdur or "",
                        "contract_duration_in_months_or_years_including_any_options_and_renewals": cdur or "",
                        "procurement_route": ptype or "Open competition",
                        "procurement_type": ptype or "Open competition",
                    }
        except Exception:
            pass

    return None


def _detail_from_cached_row(row: dict[str, Any], resource_id: str) -> dict[str, Any]:
    res = dict(row)
    res.update({
        "resource_id": resource_id,
        "title": row.get("title", ""),
        "description": row.get("description", ""),
        "estimated_value_eur": row.get("estimated_value_eur", row.get("value", "")),
        "cpv_codes": row.get("cpv_codes", ""),
        "procedure": row.get("procedure", "Open Procedure"),
        "procurement_type": row.get("procurement_type", "Services"),
        "submission_deadline": row.get("submission_deadline", row.get("closing_date", "")),
        "contract_duration_in_months_or_years_including_any_options_and_renewals": row.get(
            "contract_duration_in_months_or_years_including_any_options_and_renewals", ""
        ),
        "end_of_clarification_period": row.get("end_of_clarification_period", ""),
        "allow_suppliers_to_make_an_online_expression_of_interest": row.get(
            "allow_suppliers_to_make_an_online_expression_of_interest", "Yes"
        ),
        "contract_awarded_in_lots": row.get("contract_awarded_in_lots", "No"),
        "eu_funding": row.get("eu_funding", "No"),
        "date_of_publication_invitation": row.get("date_published", row.get("date_of_publication_invitation", row.get("published_date", ""))),
        "ted_links_for_published_notices": row.get("ted_links_for_published_notices", ""),
        "detail_url": row.get("detail_url", row.get("url", row.get("link", ""))),
        "contracting_authority": row.get("contracting_authority", row.get("authority", "")),
    })
    return res


def _get_tender_details_dict(source: str, resource_id: str, bypass_cache: bool = False) -> dict:
    cfg = SOURCES.get(source)
    if not cfg:
      return {"error": "unknown source"}

    # 1. Check cache first for all source types
    cached_row = None if bypass_cache else _find_tender_in_cache(source, resource_id)
    cached_detail = None
    if cached_row:
      cached_detail = _detail_from_cached_row(cached_row, resource_id)
      cached_detail["source"] = source
      cached_detail["source_label"] = cfg["label"]

    if cfg["type"] == "etenders":
        detail_url = (
            f"{cfg['base_url'].rstrip('/')}/"
            f"{DETAIL_URL_TEMPLATE.format(resource_id=resource_id)}"
        )
        try:
            scraper = EtendersScraper(delay_seconds=0.5, base_url=cfg["base_url"])
            html = scraper.client.get(detail_url)
            filtered = _extract_required_fields(html)
            filtered["resource_id"] = resource_id
            filtered["detail_url"] = detail_url
            filtered["source"] = source
            filtered["source_label"] = cfg["label"]
            return filtered
        except Exception:
            if cached_detail:
                return cached_detail
            return {
                "source": source,
                "source_label": cfg["label"],
                "resource_id": resource_id,
                "title": f"Tender #{resource_id}",
                "description": f"Ireland eTenders public procurement notice #{resource_id}.",
                "detail_url": detail_url,
                "procedure": "Open Procedure",
                "procurement_type": "Services",
            }

    elif cfg["type"] == "find_tender":
        from etenders_scraper.sources.find_tender import fetch_find_tender_details
        detail = fetch_find_tender_details(resource_id)
        if not detail:
            if cached_detail:
                return cached_detail
            return {
                "source": source,
                "source_label": cfg["label"],
                "resource_id": resource_id,
                "title": f"Find a Tender Notice #{resource_id}",
                "description": f"UK public procurement notice #{resource_id}.",
                "detail_url": f"https://www.find-tender.service.gov.uk/Notice/{resource_id}",
                "procedure": "Open Procedure",
            }
        detail["source"] = source
        detail["source_label"] = cfg["label"]
        return detail

    elif cfg["type"] == "contracts_finder":
        from etenders_scraper.sources.contracts_finder import fetch_contracts_finder_details
        detail = fetch_contracts_finder_details(resource_id)
        if not detail:
            if cached_detail:
                return cached_detail
            return {
                "source": source,
                "source_label": cfg["label"],
                "resource_id": resource_id,
                "title": f"Contracts Finder Notice #{resource_id}",
                "description": f"UK Contracts Finder public notice #{resource_id}.",
                "detail_url": f"https://www.contractsfinder.service.gov.uk/notice/{resource_id}",
                "procedure": "Open Opportunity",
            }
        detail["source"] = source
        detail["source_label"] = cfg["label"]
        return detail

    elif cfg["type"] == "procontract":
        from etenders_scraper.sources.procontract import fetch_procontract_details
        detail_url = (cached_detail or {}).get("detail_url", "")
        detail = fetch_procontract_details(resource_id, detail_url=detail_url)
        if not detail:
            if cached_detail:
                return cached_detail
            return {
                "source": source,
                "source_label": cfg["label"],
                "resource_id": resource_id,
                "title": f"ProContract Opportunity #{resource_id}",
                "description": f"UK council procurement notice #{resource_id} via ProContract.",
                "detail_url": detail_url,
                "procedure": "Open Opportunity",
            }
        detail["source"] = source
        detail["source_label"] = cfg["label"]
        if cached_detail:
            for k, v in cached_detail.items():
                if v and not detail.get(k):
                    detail[k] = v
        return detail

    elif cfg["type"] == "gca_agreements" or source == "gca_agreements":
        from etenders_scraper.sources.gca_agreements import fetch_gca_agreement_details
        detail = fetch_gca_agreement_details(resource_id)
        if not detail:
            if cached_detail:
                return cached_detail
            return {
                "source": source,
                "source_label": cfg["label"],
                "resource_id": resource_id,
                "title": f"GCA Agreement #{resource_id}",
                "description": f"UK Government Commercial Agency framework agreement #{resource_id}.",
                "detail_url": f"https://www.gca.gov.uk/agreements/{resource_id}",
                "procedure": "Framework Agreement",
            }
        detail["source"] = source
        detail["source_label"] = cfg["label"]
        if cached_detail:
            for k, v in cached_detail.items():
                if v and not detail.get(k):
                    detail[k] = v
        return detail

    elif cfg.get("type") == "bravo" and cfg.get("site_root"):
        from etenders_scraper.sources.bravo_search import fetch_bravo_details
        detail = fetch_bravo_details(source, resource_id, site_root=cfg["site_root"])
        if detail:
            detail["source"] = source
            detail["source_label"] = cfg["label"]
            if cached_detail:
                for k, v in cached_detail.items():
                    if v and not detail.get(k):
                        detail[k] = v
            return detail
        if cached_detail:
            return cached_detail

    # Construct detail URL for bravo / meta portals
    detail_url = ""
    if cfg.get("site_root"):
        detail_url = f"{cfg['site_root'].rstrip('/')}/Search/show/search_view.aspx?ID={resource_id}"
    elif cfg.get("domain"):
        detail_url = f"https://{cfg.get('domain')}"

    if cached_detail:
        if not cached_detail.get("detail_url") and detail_url:
            cached_detail["detail_url"] = detail_url
        return cached_detail

    domain = cfg.get("domain", "")
    return {
        "source": source,
        "source_label": cfg["label"],
        "resource_id": resource_id,
        "title": f"{cfg['label']} Opportunity",
        "description": f"Procurement notice published on {cfg['label']}. Please open the official notice link for complete documentation.",
        "detail_url": detail_url,
        "procedure": "Open Opportunity",
        "procurement_type": "Services",
        "estimated_value_eur": "N/A",
        "submission_deadline": "See official notice",
        "date_of_publication_invitation": datetime.now().strftime("%d/%m/%Y"),
    }


def _check_and_mark_portal_error_page(res: dict) -> dict:
    if not isinstance(res, dict):
        return res
    title = str(res.get("title") or "").lower().strip()
    desc = str(res.get("description") or "").lower().strip()
    error_patterns = [
        "bad page parameters",
        "bad page",
        "log-in bad page",
        "page you requested has not been supplied",
        "page not found",
        "404 not found",
        "500 internal server error",
        "service unavailable",
        "access denied",
        "the page you requested has not been supplied with the correct parameters"
    ]
    if any(p in title or p in desc for p in error_patterns):
        res["is_error_page"] = True
    return res


# ── Fake / invalid tender row detection ──────────────────────────────────────
_INVALID_ROW_PATTERNS = [
    "bad page parameters",
    "bad page",
    "log-in bad page",
    "page you requested has not been supplied",
    "page not found",
    "404 not found",
    "500 internal server error",
    "service unavailable",
    "access denied",
    "the page you requested has not been supplied with the correct parameters",
    "no actual tender",
    "no actual requirements",
    "please log in",
    "login required",
    "session expired",
    "you must be logged in",
    "javascript is required",
    "enable cookies",
    "website error",
    "this page is not available",
    "403 forbidden",
    "gateway timeout",
    "passwort vergessen",
    "benutzername vergessen",
    "renewsession",
    "cookiecheck",
    "anmelden mit",
    "bezeichnung",
]

_SYNTHETIC_ROW_PATTERNS = [
    "technical solutions contract",
    "contract #0",
    "supply, delivery and implementation of",
    "provision of digital infrastructure, cloud &",
    "indexed portal notice",
]

def _is_invalid_tender_row(row: dict, query: str = "") -> bool:
    """Return True if this row is an error/login/bad-page artefact or synthetic fake tender that must be hidden."""
    if not isinstance(row, dict):
        return True
    title = str(row.get("title") or "").strip()
    desc  = str(row.get("description") or "").strip()
    auth  = str(row.get("contracting_authority") or "").strip()
    title_lower = title.lower()
    desc_lower  = desc.lower()
    auth_lower  = auth.lower()
    # Must have a real title of at least 8 chars
    if not title_lower or len(title_lower) < 8:
        return True
    # Error-page content in title or description
    if any(p in title_lower or p in desc_lower for p in _INVALID_ROW_PATTERNS):
        return True
    # Safeguard against synthetic fake tenders / templates
    if any(p in title_lower for p in _SYNTHETIC_ROW_PATTERNS):
        return True
    if re.search(r"contract\s*#\s*0?\d+", title_lower):
        return True
    if re.search(r"&\s*technical\s+solutions", title_lower):
        return True
    if re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", title_lower):
        return True

    if query and query.strip() and query.strip().lower() != "all":
        kw = query.strip().lower()
        prefix_match = re.match(r"^(?:supplier|winner|contractor|buyer|authority|title):\s*(.*)$", kw, re.I)
        if prefix_match:
            kw = prefix_match.group(1).strip()
        if kw and len(kw) >= 3:
            # Check if keyword is spliced into known template authority
            if kw in auth_lower and any(pat in auth_lower for pat in ["department of", "ministry of", "authority", "directorate", "agency"]):
                if any(phrase in auth_lower for phrase in ["housing, local government &", "& technology", "executive agency for", "government digital &", "infrastructure &"]):
                    return True
            # Verbatim keyword spliced with fixed template phrase in title
            if kw in title_lower and any(p in title_lower for p in ["technical solutions", "contract #", "implementation of", "provision of digital infrastructure"]):
                return True
    return False


def _tender_detail_json(source: str, resource_id: str, bypass_cache: bool = False):
    res = _get_tender_details_dict(source, resource_id, bypass_cache=bypass_cache)
    if "error" in res:
        return jsonify(res), 404
    _check_and_mark_portal_error_page(res)
    return jsonify(res)

# ── Email Deadline Alerts ──────────────────────────────────────────────────────

def _parse_deadline_date(deadline_str: str):
    """Parse a submission deadline string to a date object. Returns None if unparseable."""
    if not deadline_str:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d %b %Y", "%B %d, %Y", "%d %B %Y"):
        try:
            return datetime.strptime(deadline_str.strip()[:10], fmt).date()
        except ValueError:
            pass
    try:
        from dateutil import parser as du_parser
        return du_parser.parse(deadline_str, dayfirst=True).date()
    except Exception:
        pass
    return None


def _send_smtp_email(to_addr: str, subject: str, html_body: str, error_container: list = None, list_unsubscribe_url: str = None):
    """Send an HTML email via SMTP. Reads credentials from environment."""
    smtp_host = os.environ.get("SMTP_HOST", "")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASS") or os.environ.get("SMTP_PASSWORD") or ""
    from_name = os.environ.get("SMTP_FROM_NAME", "TenderFlow")

    if not smtp_host or not smtp_user or not smtp_pass:
        err_msg = f"SMTP not configured (host={smtp_host}, user={smtp_user}, has_pass={bool(smtp_pass)})"
        print(f"[Email] {err_msg} — skipping email to {to_addr}")
        if error_container is not None:
            error_container.append(err_msg)
        return False

    from email.header import Header
    from tender_app.email_svc import _html_to_plain_fallback
    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = f"{from_name} <{smtp_user}>"
    msg["To"] = to_addr
    if list_unsubscribe_url:
        # Required by Gmail/Yahoo's bulk-sender rules for recurring automated mail like
        # deadline/recommendation alerts — its absence alone can tip borderline mail into spam.
        msg["List-Unsubscribe"] = f"<{list_unsubscribe_url}>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    # An HTML-only message (no text/plain alternative) is itself a spam-filter signal.
    msg.attach(MIMEText(_html_to_plain_fallback(html_body), "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        if smtp_port == 465:
            # SMTP over SSL
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=15) as server:
                server.login(smtp_user, smtp_pass)
                server.sendmail(smtp_user, [to_addr], msg.as_bytes())
        else:
            # SMTP over STARTTLS (typically 587) — this branch used to open the connection,
            # do the TLS handshake, then fall straight through without ever calling login()
            # or sendmail(), yet still returned True: on the exact port this app is configured
            # for, deadline and recommendation alerts were silently never sent at all.
            with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
                server.ehlo()
                server.starttls()
                server.login(smtp_user, smtp_pass)
                server.sendmail(smtp_user, [to_addr], msg.as_bytes())
        try:
            clean_subj = subject.encode('ascii', 'replace').decode('ascii')
            print(f"[Email] Sent deadline alert to {to_addr}: {clean_subj}")
        except Exception:
            pass
        return True
    except Exception as e:
        err_msg = str(e)
        try:
            print(f"[Email] Failed to send to {to_addr}: {err_msg.encode('ascii', 'replace').decode('ascii')}")
        except Exception:
            pass
        if error_container is not None:
            error_container.append(err_msg)
        return False


def _build_email_html(username: str, tender: dict, days_remaining: int) -> tuple[str, str]:
    """Build an HTML email body (and its pause/unsubscribe URL) for a deadline alert using
    the TenderFlow High-Fit Alerts template (Image 1 design)."""
    try:
        from tender_app.email_svc import render_best_fit_digest_html
        t_copy = dict(tender)
        t_copy["material_change_reason"] = f"Deadline approaching (closing in {days_remaining} day{'s' if days_remaining != 1 else ''})"
        _, html, _, pause_url = render_best_fit_digest_html(username, [t_copy], company_name="Your Profile", email=username)
        if html:
            return html, pause_url
    except Exception as ex:
        print(f"[Email] Fallback in _build_email_html: {ex}")

    title = tender.get("title", "Untitled Tender")
    buyer = tender.get("contracting_authority") or "N/A"
    deadline = tender.get("submission_deadline", "")
    procedure = tender.get("procedure") or "N/A"
    description = tender.get("description") or "N/A"
    source_label = tender.get("source_label") or tender.get("source") or "N/A"
    cpv_codes = tender.get("cpv_codes") or "N/A"
    
    contact_phone = tender.get("contact_phone") or tender.get("telephone") or tender.get("phone") or "N/A"
    contact_email = tender.get("contact_email") or tender.get("email") or "N/A"
    
    pub_date_raw = tender.get("date_of_publication_invitation") or tender.get("date_published") or ""
    estimation = tender.get("estimated_value_eur") or tender.get("estimated_value") or "N/A"
    
    fit_score = tender.get("fit_score") or 0
    fit_band = tender.get("fit_band") or ""
    fit_band_cap = str(fit_band).capitalize() or "N/A"
    
    days_remaining_label = f"closing in {days_remaining} day{'s' if days_remaining != 1 else ''}"

    def format_to_dd_mm_yyyy(d_str: str) -> str:
        if not d_str:
            return "N/A"
        d_str = d_str.strip()
        for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d %b %Y", "%B %d, %Y", "%d %B %Y"):
            try:
                dt = datetime.strptime(d_str[:10], fmt)
                return dt.strftime("%d-%m-%Y")
            except Exception:
                pass
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"):
            try:
                dt = datetime.strptime(d_str.split(".")[0].split("Z")[0].split("+")[0], fmt)
                return dt.strftime("%d-%m-%Y")
            except Exception:
                pass
        return d_str

    pub_date = format_to_dd_mm_yyyy(pub_date_raw)
    if pub_date == "N/A":
        pub_date = datetime.now().strftime("%d-%m-%Y")

    try:
        from tender_app.config import get_app_base_url
        base_url = get_app_base_url()
    except Exception:
        base_url = "http://localhost:8092"

    import urllib.parse
    encoded_title = urllib.parse.quote(title)
    source_id = tender.get("source") or ""
    rid = tender.get("resource_id") or tender.get("id") or ""

    if source_id and rid:
        encoded_src = urllib.parse.quote(str(source_id))
        encoded_rid = urllib.parse.quote(str(rid))
        app_url = f"{base_url}/?source={encoded_src}&id={encoded_rid}&tender_title={encoded_title}"
    else:
        app_url = f"{base_url}/?q={encoded_title}"

    from tender_app.email_svc import alert_pref_token
    pause_url = f"{base_url}/api/alerts/preference?cadence=off&user={urllib.parse.quote(username)}&token={alert_pref_token(username)}"

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Tender Alert</title>
</head>
<body style="font-family: Arial, sans-serif; font-size: 14px; line-height: 1.5; color: #333; background: #fff; margin: 0; padding: 20px;">
  
  <p style="font-size: 16px; font-weight: bold; color: #1e3a8a; margin: 0 0 10px 0;">Tender Alert Notification</p>
  
  <p style="font-size: 16px; font-weight: bold; margin: 0 0 15px 0;"><a href="{app_url}" target="_blank" style="color: #1e3a8a; text-decoration: none;">{title}</a></p>
  
  <p style="margin: 0 0 8px 0;"><strong>Publication date:</strong> {pub_date}</p>
  <p style="margin: 0 0 8px 0;"><strong>Response deadline:</strong> {deadline} ({days_remaining_label})</p>
  <p style="margin: 0 0 8px 0;"><strong>Estimation:</strong> {estimation}</p>
  <p style="margin: 0 0 8px 0;"><strong>Quick Fit:</strong> {fit_score}% ({fit_band_cap})</p>
  <p style="margin: 0 0 8px 0;"><strong>Source:</strong> {source_label}</p>
  <p style="margin: 0 0 8px 0;"><strong>Procedure:</strong> {procedure}</p>
  <p style="margin: 0 0 8px 0;"><strong>Buyer:</strong> {buyer}</p>
  <p style="margin: 0 0 8px 0;"><strong>Contact Email:</strong> {contact_email}</p>
  <p style="margin: 0 0 8px 0;"><strong>Contact Number:</strong> {contact_phone}</p>
  <p style="margin: 0 0 8px 0;"><strong>CPINs / CPV Codes:</strong> {cpv_codes}</p>
  
  <div style="margin-top: 15px; margin-bottom: 20px;">
    <p style="margin: 0 0 5px 0;"><strong>Tender Description:</strong></p>
    <p style="margin: 0; color: #555; white-space: pre-wrap; font-size: 13.5px; line-height: 1.6;">{description}</p>
  </div>

  <div style="margin: 20px 0;">
    <a href="{app_url}" target="_blank" style="display: inline-block; background-color: #4f46e5; color: #ffffff; text-decoration: none; font-size: 13px; font-weight: 600; padding: 10px 18px; border-radius: 6px;">View Tender in TenderFlow &rarr;</a>
  </div>

  <hr style="border: 0; border-top: 1px solid #ddd; margin: 25px 0 15px 0;">

  <p style="font-size: 11px; color: #888; margin: 0;">
    You received this because this matches your tracked tenders &middot; Manage alerts &middot; <a href="{pause_url}" style="color: #888;">Unsubscribe</a>
  </p>

</body>
</html>""", pause_url


def _run_email_settings_migration(cur):
    cols = [
        ("email_alerts_enabled", "BOOLEAN DEFAULT TRUE"),
        ("new_match_alerts_enabled", "BOOLEAN DEFAULT TRUE"),
        ("new_match_frequency", "VARCHAR(50) DEFAULT 'Immediately'"),
        ("deadline_reminders_enabled", "BOOLEAN DEFAULT TRUE"),
        ("deadline_reminders_threshold", "VARCHAR(50) DEFAULT '7 days before'"),
        ("low_credit_warning_enabled", "BOOLEAN DEFAULT FALSE"),
        ("low_credit_threshold", "VARCHAR(50) DEFAULT 'Below 50 credits'"),
        ("fit_score_threshold", "VARCHAR(50) DEFAULT '40%'")
    ]
    for col_name, col_def in cols:
        try:
            cur.execute(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col_name} {col_def}")
        except Exception:
            pass


def _default_email_settings():
    return {
        "email_alerts_enabled": True,
        "new_match_alerts_enabled": True,
        "new_match_frequency": "Immediately",
        "deadline_reminders_enabled": True,
        "deadline_reminders_threshold": "7 days before",
        "low_credit_warning_enabled": False,
        "low_credit_threshold": "Below 50 credits",
        "fit_score_threshold": "40%",
    }


def send_deadline_emails():
    """Check all pipeline items and send deadline reminder emails based on user settings."""
    print("[Email] Running deadline email check...")
    today = datetime.now(timezone.utc).date()

    try:
        conn = get_db_connection()
        ph = "%s"
        cur = conn.cursor()

        # Run migration on users table to ensure columns exist
        _run_email_settings_migration(cur)
        conn.commit()

        # Fetch pipeline items joined with user settings
        cur.execute(
            """SELECT p.username, p.tender_key, p.title, p.source, p.contracting_authority,
                      p.submission_deadline, p.fit_score, p.fit_band,
                      u.email_alerts_enabled, u.deadline_reminders_enabled,
                      u.deadline_reminders_threshold, u.fit_score_threshold,
                      u.email
               FROM pipeline p
               JOIN users u ON p.username = u.username"""
        )
        rows = cur.fetchall()

        sent_count = 0
        for row in rows:
            (username, tender_key, title, source, authority, deadline_str, fit_score, fit_band,
             u_alerts_enabled, u_reminders_enabled, u_threshold, u_fit_threshold, u_email) = row

            target_email = (u_email or username or "").strip()
            if not target_email or "@" not in target_email:
                print(f"[Email] Skipping user '{username}' — no valid email address: '{target_email}'")
                continue

            if not deadline_str:
                continue

            # 1. Check if user has alerts turned on
            alerts_on = bool(u_alerts_enabled) if u_alerts_enabled is not None else True
            reminders_on = bool(u_reminders_enabled) if u_reminders_enabled is not None else True
            if not alerts_on or not reminders_on:
                continue

            # 2. Check Fit Score Threshold
            fit_score_val = fit_score or 0
            try:
                fit_thresh_str = str(u_fit_threshold or "40%").replace("%", "").replace("+", "").strip()
                fit_thresh = int(fit_thresh_str)
            except Exception:
                fit_thresh = 40

            if fit_score_val < fit_thresh:
                continue

            # 3. Check Deadline Day Threshold
            deadline_date = _parse_deadline_date(deadline_str)
            if not deadline_date:
                continue

            days_remaining = (deadline_date - today).days
            
            # Map user selection to allowed days
            u_threshold_str = str(u_threshold or "7 days before").lower()
            allowed_days = []
            if "7" in u_threshold_str:
                allowed_days = [7]
            elif "3" in u_threshold_str:
                allowed_days = [3]
            elif "1" in u_threshold_str:
                allowed_days = [1]
            else:
                # Fallback / 'All'
                allowed_days = [1, 3, 7]

            if days_remaining not in allowed_days:
                continue

            # Check if we already sent for this threshold
            cur.execute(
                f"SELECT 1 FROM email_notifications WHERE username={ph} AND tender_key={ph} AND days_threshold={ph}",
                (username, tender_key, days_remaining)
            )
            if cur.fetchone():
                continue  # Already sent

            # Fetch detailed tender info
            source_id, rid = parse_tender_key(tender_key)
            full_details = {}
            try:
                full_details = _get_tender_details_dict(source_id, rid)
            except Exception as e:
                print(f"[Email] Failed to fetch full details for {tender_key}: {e}")

            tender = {
                "title": title, "source": source, "contracting_authority": authority,
                "submission_deadline": deadline_str, "fit_score": fit_score, "fit_band": fit_band or ""
            }
            if isinstance(full_details, dict):
                for k, v in full_details.items():
                    if v and (not tender.get(k) or k == "description" or k not in tender):
                        tender[k] = v

            subject = f"⚠️ Tender closing in {days_remaining} day{'s' if days_remaining > 1 else ''} — {title[:60]}"
            html, pause_url = _build_email_html(username, tender, days_remaining)

            if _send_smtp_email(target_email, subject, html, list_unsubscribe_url=pause_url):
                try:
                    cur.execute(
                        f"INSERT INTO email_notifications (username, tender_key, days_threshold) VALUES ({ph},{ph},{ph})",
                        (username, tender_key, days_remaining)
                    )
                    conn.commit()
                    sent_count += 1
                except Exception:
                    conn.rollback()

        cur.close()
        conn.close()
        print(f"[Email] Deadline check complete. Sent {sent_count} email(s).")
    except Exception as e:
        print(f"[Email] Error in deadline check: {e}")


def _build_recommendation_email_html(username, company_name, tenders) -> tuple[str, str]:
    """Build an HTML recommendation email body (and its pause/unsubscribe URL) using the
    TenderFlow High-Fit Alerts template (Image 1 design)."""
    try:
        from tender_app.email_svc import render_best_fit_digest_html
        _, html, _, pause_url = render_best_fit_digest_html(username, tenders, company_name=company_name, email=username)
        if html:
            return html, pause_url
    except Exception as ex:
        print(f"[Email] Fallback in _build_recommendation_email_html: {ex}")

    try:
        from tender_app.config import get_app_base_url
        base_url = get_app_base_url()
    except Exception:
        base_url = "http://localhost:8092"

    import urllib.parse as _urllib_parse
    from tender_app.email_svc import alert_pref_token
    pause_url = f"{base_url}/api/alerts/preference?cadence=off&user={_urllib_parse.quote(username)}&token={alert_pref_token(username)}"

    tenders_html = ""
    for t in tenders:
        title = t.get("title", "Tender Opportunity")
        authority = t.get("authority", "Public Authority")
        deadline = t.get("deadline", "N/A")
        score = t.get("fit_score", 70)
        source = str(t.get("source", "")).upper()
        source_id = t.get("source") or ""
        rid = t.get("resource_id") or t.get("id") or ""
        import urllib.parse
        encoded_title = urllib.parse.quote(title)

        if source_id and rid:
            encoded_src = urllib.parse.quote(str(source_id))
            encoded_rid = urllib.parse.quote(str(rid))
            app_url = f"{base_url}/?source={encoded_src}&id={encoded_rid}&tender_title={encoded_title}"
        else:
            app_url = f"{base_url}/?q={encoded_title}"

        reason = t.get("fit_reason") or "Matched to your search keywords and company capabilities."

        tenders_html += f"""
        <div style="background-color: #f9fbfd; border: 1px solid #e2e8f0; border-left: 4px solid #4f46e5; border-radius: 8px; padding: 16px; margin-bottom: 16px;">
            <div style="display: flex; justify-content: space-between; align-items: flex-start;">
                <h3 style="margin: 0 0 8px 0; font-size: 15px; color: #1e293b;"><a href="{app_url}" target="_blank" style="color:#1e293b; text-decoration:none;">{title}</a></h3>
                <span style="background-color: #dcfce7; color: #15803d; font-size: 12px; font-weight: 700; padding: 3px 8px; border-radius: 12px; white-space: nowrap;">{score}% Fit</span>
            </div>
            <p style="margin: 4px 0; font-size: 13px; color: #64748b;"><strong>Buyer:</strong> {authority} &middot; <strong>Portal:</strong> {source}</p>
            <p style="margin: 4px 0 6px 0; font-size: 13px; color: #64748b;"><strong>Deadline:</strong> {deadline}</p>
            <div style="background-color: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 6px; padding: 7px 10px; margin: 8px 0 12px 0; font-size: 12px; color: #166534; line-height: 1.4;">
                <strong>🎯 Why this fits:</strong> {reason}
            </div>
            <a href="{app_url}" target="_blank" style="display: inline-block; background-color: #4f46e5; color: #ffffff; text-decoration: none; font-size: 12px; font-weight: 600; padding: 6px 12px; border-radius: 6px;">View Tender & Add to Pipeline &rarr;</a>
        </div>
        """

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: Arial, sans-serif; background-color: #f8fafc; color: #334155; margin: 0; padding: 20px;">
    <div style="max-width: 600px; margin: 0 auto; background: #ffffff; border-radius: 12px; padding: 24px; border: 1px solid #e2e8f0;">
        <h2 style="margin-top: 0; color: #0f172a; font-size: 20px;">🎯 Handpicked Tenders for {company_name}</h2>
        <p style="font-size: 14px; color: #475569; line-height: 1.5;">
            We noticed you don't currently have any active tenders in your <strong>TenderFlow Pipeline</strong>. 
            Based on your company profile, our AI engine identified these high-fit contract opportunities for you:
        </p>
        <div style="margin-top: 20px;">
            {tenders_html}
        </div>
        <div style="margin-top: 24px; padding-top: 16px; border-top: 1px solid #e2e8f0; text-align: center;">
            <a href="{base_url}/" style="display: inline-block; background-color: #0f172a; color: #ffffff; text-decoration: none; font-size: 14px; font-weight: 600; padding: 10px 20px; border-radius: 8px;">Explore All Active Tenders &rarr;</a>
        </div>
        <p style="font-size: 11px; color: #94a3b8; margin-top: 24px; text-align: center;">
            You received this email because you are registered on TenderFlow &middot; <a href="{base_url}/#settings" style="color: #64748b;">Manage Notification Settings</a> &middot; <a href="{pause_url}" style="color: #64748b;">Unsubscribe</a>
        </p>
    </div>
</body>
</html>""", pause_url


def send_empty_pipeline_recommendation_emails():
    """Find users who have 0 tenders in their pipeline and send them profile-matched tender recommendations."""
    print("[Email] Running empty-pipeline recommendation check...")
    try:
        conn = get_db_connection()
        ph = "%s"
        cur = conn.cursor()

        # Find users with 0 pipeline items
        cur.execute(
            """SELECT u.username, u.email, u.email_alerts_enabled
               FROM users u
               WHERE u.username NOT IN (SELECT DISTINCT username FROM pipeline WHERE username IS NOT NULL)"""
        )
        empty_users = cur.fetchall()

        sent_count = 0
        for u_row in empty_users:
            username, u_email, u_alerts_enabled = u_row[0], u_row[1], u_row[2]
            target_email = (u_email or username or "").strip()
            if not target_email or "@" not in target_email:
                continue

            # Skip dummy/test email addresses
            domain = target_email.split("@")[-1].lower()
            if domain in ["example.com", "test.com", "invalid", "localhost", "sample.com"]:
                continue

            alerts_on = bool(u_alerts_enabled) if u_alerts_enabled is not None else True
            if not alerts_on:
                continue

            # Anti-spam check: check if recommendation sent within last 7 days (days_threshold = -999)
            cur.execute(
                f"SELECT sent_at FROM email_notifications WHERE username={ph} AND days_threshold={ph} ORDER BY sent_at DESC LIMIT 1",
                (username, -999)
            )
            last_sent = cur.fetchone()
            if last_sent:
                try:
                    sent_date = _parse_deadline_date(str(last_sent[0]))
                    if sent_date and (datetime.now(timezone.utc).date() - sent_date).days < 7:
                        continue
                except Exception:
                    pass

            # Fetch user's company profile (prefer default profile)
            cur.execute(
                f"SELECT id, name, profile_text FROM company_profiles WHERE username={ph} ORDER BY is_default DESC, id ASC LIMIT 1",
                (username,)
            )
            prof = cur.fetchone()
            prof_id = prof[0] if prof else None
            comp_name = prof[1] if prof else "Your Company"

            # Fetch high-fit tenders strictly matching user's search history and company profile
            recommended_tenders = []
            try:
                from tender_app.email_notifier import find_best_fit_tenders_for_user
                raw_tenders, comp_name_found, _ = find_best_fit_tenders_for_user(
                    cur, username, min_score=65, target_profile_id=str(prof_id) if prof_id else None, max_results=5
                )
                if comp_name_found and comp_name_found != "Your Company":
                    comp_name = comp_name_found

                for t in raw_tenders:
                    recommended_tenders.append({
                        "title": t.get("title") or "Tender Opportunity",
                        "source": t.get("source") or t.get("portal") or "UK",
                        "authority": t.get("contracting_authority") or t.get("authority") or "Public Authority",
                        "deadline": t.get("submission_deadline") or t.get("closing_date") or "Open",
                        "fit_score": t.get("fit_score", 75),
                        "fit_band": t.get("fit_band", "High Match"),
                        "fit_reason": t.get("fit_reason", "Matched to your search keywords and company capabilities."),
                        "resource_id": t.get("resource_id") or t.get("id") or "",
                        "id": t.get("id") or t.get("resource_id") or "",
                    })
            except Exception as _match_err:
                print(f"[Email] Error running find_best_fit_tenders_for_user for {username}: {_match_err}")

            # STRICT CHECK: If no tenders matched the user's search keywords or company profile, skip!
            if not recommended_tenders:
                continue

            subject = f"🎯 Recommended Tenders for {comp_name} — High-Fit Opportunities"
            html, pause_url = _build_recommendation_email_html(username, comp_name, recommended_tenders)

            if _send_smtp_email(target_email, subject, html, list_unsubscribe_url=pause_url):
                try:
                    # (username, tender_key, days_threshold) is UNIQUE, so a plain INSERT only worked
                    # for a user's first recommendation. Every later one raised, the row the 7-day
                    # check above reads never moved, and the same email went out again on every run
                    # (each worker start, then daily) while the log said "Sent 0 email(s)".
                    cur.execute(
                        f"""INSERT INTO email_notifications (username, tender_key, days_threshold) VALUES ({ph},{ph},{ph})
                            ON CONFLICT (username, tender_key, days_threshold) DO UPDATE SET sent_at = CURRENT_TIMESTAMP""",
                        (username, "EMPTY_PIPELINE_RECOMMENDATION", -999)
                    )
                    conn.commit()
                    sent_count += 1
                except Exception as _rec_err:
                    print(f"[Email] Could not record the recommendation email sent to {username}: {_rec_err}")
                    conn.rollback()

        cur.close()
        conn.close()
        print(f"[Email] Empty-pipeline recommendation check complete. Sent {sent_count} email(s).")
    except Exception as e:
        print(f"[Email] Error in recommendation email check: {e}")


def start_email_scheduler():
    """Start a background thread that runs send_deadline_emails() and send_empty_pipeline_recommendation_emails() once per day."""
    def _loop():
        while True:
            try:
                send_deadline_emails()
                send_empty_pipeline_recommendation_emails()
            except Exception as e:
                print(f"[Email Scheduler] Unhandled error: {e}")
            # Sleep 24 hours
            threading.Event().wait(86400)

    t = threading.Thread(target=_loop, daemon=True, name="EmailDeadlineScheduler")
    t.start()
    print("[Email Scheduler] Started — will check deadlines & empty-pipeline recommendations every 24 hours.")


def _parse_json_robust(text):
    import re
    if not text:
        return {}
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
        cleaned = cleaned.rsplit("```", 1)[0].strip()
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].strip()

    try:
        return json.loads(cleaned, strict=False)
    except Exception:
        pass

    match = re.search(r'\{.*\}', cleaned, re.DOTALL)
    if match:
        cleaned = match.group(0)

    try:
        return json.loads(cleaned, strict=False)
    except Exception:
        pass

    try:
        in_string = False
        chars = []
        escaped = False
        i = 0
        while i < len(cleaned):
            c = cleaned[i]
            if escaped:
                chars.append(c)
                escaped = False
                i += 1
                continue
            if c == '\\':
                chars.append(c)
                escaped = True
                i += 1
                continue
            if c == '"':
                in_string = not in_string
                chars.append(c)
            elif c == '\n' and in_string:
                chars.append('\\n')
            elif c == '\r' and in_string:
                chars.append('\\r')
            elif c == '\t' and in_string:
                chars.append('\\t')
            else:
                chars.append(c)
            i += 1
        cleaned = "".join(chars)
        return json.loads(cleaned, strict=False)
    except Exception:
        pass

    # Truncated JSON auto-repair attempt
    try:
        repaired = cleaned
        quote_count = len(re.findall(r'(?<!\\)"', repaired))
        if quote_count % 2 != 0:
            repaired += '"'
        last_brace = repaired.rfind('}')
        if last_brace != -1:
            repaired_sub = repaired[:last_brace+1]
            open_brackets = repaired_sub.count('[') - repaired_sub.count(']')
            open_braces = repaired_sub.count('{') - repaired_sub.count('}')
            repaired_sub += ']' * max(0, open_brackets) + '}' * max(0, open_braces)
            return json.loads(repaired_sub, strict=False)
    except Exception:
        pass

    return {}

def verify_and_clean_bid_claims(bid_response: dict, ground_truth_str: str, checklist_items: list = None) -> dict:
    """
    Post-generation hallucination guard (structural enforcement, not just a prompt).

    Scans all generated bid proposal text fields for:
    1. Specific percentages (e.g. "40%") not grounded in source data
    2. Named monetary figures (e.g. "£2M", "€500k") not grounded in source data
    3. Generic named-org phrases suggesting invented clients not in source data
    4. Existing suspicious claim patterns (proven track record without evidence, etc.)

    In thin-profile mode (< 3 answer bank entries or < 200 chars) all four checks
    are applied with a lower tolerance — this is when fabrication risk is highest.

    Verified claims are left unchanged. Unverified specifics are replaced with
    clearly labelled [PLACEHOLDER: ...] tags the user sees and must fill in.
    """
    if not isinstance(bid_response, dict):
        return bid_response

    gt_lower = (ground_truth_str or "").lower()

    # Thin profile detection: answer bank is sparse → higher fabrication risk
    is_thin_profile = len(gt_lower.strip()) < 200

    # Checklist completed names
    completed_checklist_names: set[str] = set()
    if checklist_items:
        for item in checklist_items:
            status = str(item.get("status") or "").lower()
            if status in ("completed", "signed", "done", "true", "1"):
                completed_checklist_names.add(
                    str(item.get("item_name") or item.get("document_name") or "").lower()
                )

    # ── Pattern 1: Specific percentage claims ─────────────────────────────────
    _pct_re = re.compile(r'\b(\d{1,3})\s*%', re.IGNORECASE)

    # ── Pattern 2: Monetary figures (£/€/$) with unit ─────────────────────────
    _money_re = re.compile(
        r'[£€\$]\s*\d[\d,\.]*\s*(?:m(?:illion)?|k|bn|billion|thousand)?'
        r'|\b\d[\d,\.]*\s*(?:million|billion|thousand)\s*(?:pound|euro|dollar)s?\b',
        re.IGNORECASE
    )

    # ── Pattern 3: Generic named-org client phrases not in source data ─────────
    _named_org_re = re.compile(
        r'\ba\s+(?:leading|major|global|top|large|prominent|national|well-known|tier-1|tier-\d)\s+'
        r'(?:\w+\s+){0,3}(?:bank|insurer|insurance\s+group|retailer|manufacturer|authority|'
        r'council|trust|nhs\s+trust|university|airline|telco|telecoms\s+provider|energy\s+company|'
        r'utility|developer|contractor|consultancy|law\s+firm|firm|organisation|organization|company|'
        r'client|customer|employer|government\s+department)\b',
        re.IGNORECASE
    )

    # ── Pattern 4: Pre-existing suspicious claim phrases ──────────────────────
    suspicious_claims = [
        (
            r'\b(proven track record|established track record|proven history|established history)\b',
            ["case study", "past project", "track record", "experience", "client", "awarded"],
            "[Add 1–2 case studies here — none found in Answer Bank]"
        ),
        (
            r'\b(signed and executed|already signed|fully signed|signed and submitted)\b',
            ["signed", "completed"],
            "[Status: in progress / being finalised — not marked as signed in Checklist]"
        ),
    ]

    def _pct_in_source(pct_str: str) -> bool:
        return pct_str in gt_lower or f"{pct_str}%" in gt_lower

    def _money_in_source(match_str: str) -> bool:
        # Strip symbols and normalise for lookup
        digits = re.sub(r'[£€\$,\s]', '', match_str).lower()
        return digits in gt_lower or match_str.lower().replace(',', '') in gt_lower

    def clean_text_field(text: str) -> str:
        if not text or not isinstance(text, str):
            return text
        res = text

        # P3a-1: Percentage claims
        def _replace_pct(m):
            pct_val = m.group(1)
            if not _pct_in_source(pct_val):
                return f"[PLACEHOLDER: insert verified percentage — '{pct_val}%' not found in company data]"
            return m.group(0)
        if is_thin_profile or not any(c.isdigit() for c in gt_lower):
            res = _pct_re.sub(_replace_pct, res)

        # P3a-2: Monetary figures
        def _replace_money(m):
            raw = m.group(0)
            if not _money_in_source(raw):
                return f"[PLACEHOLDER: insert verified figure — '{raw}' not found in company data]"
            return raw
        if is_thin_profile:
            res = _money_re.sub(_replace_money, res)

        # P3a-3: Named generic org phrases suggesting invented clients
        def _replace_named_org(m):
            raw = m.group(0)
            # Only replace if the phrasing doesn't appear in the source data
            if raw.lower().strip() not in gt_lower:
                return "[PLACEHOLDER: insert a real client example from Answer Bank or remove]"
            return raw
        res = _named_org_re.sub(_replace_named_org, res)

        # P3a-4: Pre-existing suspicious claim patterns
        for pattern, req_keywords, placeholder in suspicious_claims:
            if re.search(pattern, res, re.IGNORECASE):
                has_ev = any(kw in gt_lower for kw in req_keywords) or \
                         any(kw in gt_lower for kw in ["certif", "iso", "accred", "experience", "project", "award"])
                if not has_ev:
                    res = re.sub(pattern, placeholder, res, flags=re.IGNORECASE)

        return res

    sections = [
        "cover_letter", "executive_summary", "understanding_of_requirements",
        "technical_methodology", "quality_and_compliance", "social_value",
        "relevant_experience", "team_and_personnel", "pricing_notes", "declarations"
    ]
    for key in sections:
        obj = bid_response.get(key)
        if isinstance(obj, dict) and "content" in obj and isinstance(obj["content"], str):
            obj["content"] = clean_text_field(obj["content"])

    if "itt_question_responses" in bid_response and isinstance(bid_response["itt_question_responses"], list):
        for q in bid_response["itt_question_responses"]:
            if isinstance(q, dict) and "response" in q and isinstance(q["response"], str):
                q["response"] = clean_text_field(q["response"])

    return bid_response



def create_app() -> Flask:

    app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")
    _flask_secret = os.environ.get("FLASK_SECRET_KEY", "")
    if not _flask_secret:
        import secrets as _secrets
        _flask_secret = _secrets.token_hex(32)
        print("WARNING: FLASK_SECRET_KEY not set — using a random key. Sessions will not survive restarts. "
              "Set FLASK_SECRET_KEY in .env to fix this.")
    app.secret_key = _flask_secret
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=SESSION_COOKIE_SECURE,
        PERMANENT_SESSION_LIFETIME=86400 * 7,
        # Cache static assets for 1 hour in browsers (normal refresh uses cached files)
        SEND_FILE_MAX_AGE_DEFAULT=3600,
    )

    # Add cache headers for static files so normal refreshes are instant.
    # Hard refresh (Ctrl+F5) intentionally bypasses this — that's expected browser behaviour.
    @app.after_request
    def add_security_headers(response):
        # Conservative set that cannot break the inline-script/CDN frontend (a CSP would).
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        # geolocation=(self), not (): the distance filters' "Use my location" button needs it on
        # our own origin — () disabled it page-wide, so the browser refused without ever prompting.
        response.headers.setdefault("Permissions-Policy", "geolocation=(self), camera=(), microphone=()")
        if request.headers.get("X-Forwarded-Proto", request.scheme) == "https":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    @app.after_request
    def add_cache_headers(response):
        path = request.path
        # Long-lived cache for versioned/static assets
        if path.startswith("/flags/") or path.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".woff", ".woff2", ".ttf")):
            response.cache_control.max_age = 86400  # 1 day
            response.cache_control.public = True
        # Short cache for JS/CSS - allows normal refresh to be instant
        elif path.endswith((".js", ".css")):
            response.cache_control.max_age = 3600   # 1 hour
            response.cache_control.public = True
        # HTML (including the app shell served at "/") and API - always revalidate
        elif path == "/" or path.endswith(".html") or path.startswith("/api/"):
            response.cache_control.no_cache = True
            response.cache_control.must_revalidate = True
        return response

    if SENTRY_DSN:
        try:
            import sentry_sdk
            from sentry_sdk.integrations.flask import FlaskIntegration
            sentry_sdk.init(dsn=SENTRY_DSN, integrations=[FlaskIntegration()], traces_sample_rate=0.1)
        except ImportError:
            pass

    # Initialize Database (PostgreSQL)
    init_db()
    init_firebase()

    # Start background email deadline scheduler and award notice scraper scheduler (off in local dev).
    # The email scheduler has its own flag so local dev can get automated notifications without
    # also starting the scrapers below, which hit live external portals on every container start.
    if ENABLE_EMAIL_SCHEDULER:
        start_email_scheduler()
    else:
        print("[Email Scheduler] Disabled (ENABLE_EMAIL_SCHEDULER=0)")

    if ENABLE_SCHEDULERS:
        try:
            from etenders_scraper.awards import start_award_scheduler
            start_award_scheduler(get_db_connection)
        except Exception as _sc_err:
            print("Warning: Could not start award scraper scheduler:", _sc_err)
        try:
            from etenders_scraper.planning.harvester import start_planning_scheduler
            start_planning_scheduler(get_db_connection)
        except Exception as _pl_err:
            print("Warning: Could not start planning leads harvester:", _pl_err)
        try:
            # Contracts Finder has no keyword filter, so every search scans its whole feed
            # (minutes). Warm that cache at startup and keep it warm, otherwise the first search
            # after a deploy or a quiet spell sits at "1 of 3 portals finished" while it scans.
            from etenders_scraper.sources.cf_feed_store import DbFeedStore
            from etenders_scraper.sources.contracts_finder import start_feed_prewarm
            from etenders_scraper.sources.registry import SOURCES as _CF_SOURCES
            start_feed_prewarm(
                "contracts_finder",
                _CF_SOURCES["contracts_finder"]["label"],
                store=DbFeedStore(get_db_connection),
            )
        except Exception as _cf_err:
            print("Warning: Could not start Contracts Finder feed warm-up:", _cf_err)
    else:
        print("[Schedulers] Disabled (ENABLE_SCHEDULERS=0)")

    app.register_blueprint(init_auth_blueprint(get_db_connection))
    app.register_blueprint(init_billing_blueprint(get_db_connection))
    app.register_blueprint(init_admin_blueprint(get_db_connection))
    app.register_blueprint(init_profiles_blueprint(get_db_connection))
    app.register_blueprint(search_bp)
    app.register_blueprint(ai_bp)
    app.register_blueprint(init_suppliers_blueprint(get_db_connection))
    app.register_blueprint(init_buyers_blueprint(get_db_connection))
    app.register_blueprint(init_planning_blueprint(get_db_connection))
    if ENABLE_BUYER_WORKSPACE:
        app.register_blueprint(init_market_radar_blueprint(get_db_connection))
        app.register_blueprint(init_market_engagement_blueprint(get_db_connection))
    if ENABLE_GROWTH_STUDIO:
        app.register_blueprint(init_growth_blueprint(get_db_connection))

    # Note: /api/email-settings GET/POST is handled by init_profiles_blueprint (profiles_bp.py)


    @app.route("/api/alerts/feedback", methods=["GET", "POST"])
    def api_alerts_feedback():
        """Handle user feedback on an alerted tender (e.g. 'not_relevant' or 'tell_us_why')."""
        action = request.args.get("action") or request.form.get("action") or "not_relevant"
        tender_key = request.args.get("tender_key") or request.form.get("tender_key") or ""
        username = request.args.get("username") or session.get("username") or ""
        reason = request.args.get("reason") or request.form.get("reason") or ""

        if username and tender_key:
            try:
                conn = get_db_connection()
                ph = "%s"
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO alert_feedback (username, tender_key, feedback_type, reason, created_at)
                    VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
                """, (username, tender_key, action, reason))
                conn.commit()
                cur.close()
                conn.close()
            except Exception as e:
                print(f"[AlertFeedback Error] {e}")

        title_msg = "Feedback Received — TenderFlow"
        if action == "not_relevant":
            body_msg = "Thank you! We've marked this tender as not relevant. TenderFlow's AI matching engine will adjust future recommendations for your account."
        else:
            body_msg = "Thank you for your feedback! Your insights help us continuously refine and improve your tender recommendations."

        return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{title_msg}</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {{ font-family: Inter, -apple-system, sans-serif; background: #f8fafc; color: #0f172a; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; padding: 20px; }}
        .card {{ background: #ffffff; border: 1px solid #e2e8f0; border-radius: 16px; padding: 32px; max-width: 480px; width: 100%; text-align: center; box-shadow: 0 10px 25px rgba(0,0,0,0.05); }}
        .icon {{ font-size: 40px; margin-bottom: 12px; }}
        h2 {{ margin: 0 0 12px 0; font-size: 20px; font-weight: 700; color: #0f172a; }}
        p {{ font-size: 14px; color: #475569; line-height: 1.6; margin-bottom: 24px; }}
        .btn {{ display: inline-block; background: #4f46e5; color: #ffffff; text-decoration: none; font-weight: 600; padding: 10px 20px; border-radius: 8px; font-size: 14px; }}
    </style>
</head>
<body>
    <div class="card">
        <div class="icon">🎯</div>
        <h2>Feedback Recorded</h2>
        <p>{body_msg}</p>
        <a href="/" class="btn">Return to TenderFlow Dashboard &rarr;</a>
    </div>
</body>
</html>"""

    @app.route("/api/alerts/preference", methods=["GET", "POST"])
    def api_alerts_preference():
        """Handle deep-linked alert preference changes (e.g. cadence=daily, cadence=weekly, cadence=off)."""
        cadence = (request.args.get("cadence") or request.form.get("cadence") or "daily").lower()
        link_user = (request.args.get("user") or request.args.get("username") or "").strip()
        username = link_user or session.get("username") or ""

        # This route is reachable without a session (email links). A logged-in user may change
        # their own settings; otherwise the link must carry a valid signature, except pausing,
        # which stays open so unsubscribing works from emails sent before links were signed.
        if username and cadence != "off" and (session.get("username") or "").lower() != username.lower():
            import hmac as _hmac
            from tender_app.email_svc import alert_pref_token
            expected = alert_pref_token(username)
            if not expected or not _hmac.compare_digest(expected, request.args.get("token") or ""):
                username = ""

        if username:
            try:
                conn = get_db_connection()
                ph = "%s"
                cur = conn.cursor()
                # Links carry the address as written in the email; stored usernames are
                # lowercase, so resolve to the stored spelling before touching either table.
                cur.execute("SELECT username FROM users WHERE LOWER(username) = LOWER(%s) OR LOWER(email) = LOWER(%s) LIMIT 1", (username, username))
                found = cur.fetchone()
                if found:
                    username = found[0]

                enabled_val = "false" if cadence == "off" else "true"
                prefs_to_save = [("automated_emails_enabled", enabled_val)]
                if cadence != "off":
                    # Both keys: the settings screen reads new_match_frequency first and the
                    # notifier reads it the same way, so writing only email_frequency here
                    # would leave the screen and the schedule showing the old cadence. Pausing
                    # leaves the cadence alone so resuming picks up where the user left off.
                    freq_val = "weekly" if cadence == "weekly" else ("immediately" if cadence in ("instant", "immediate", "immediately") else "daily")
                    prefs_to_save += [("email_frequency", freq_val), ("new_match_frequency", freq_val)]

                for pkey, pval in prefs_to_save:
                    cur.execute("""
                        INSERT INTO user_prefs (username, pref_key, pref_value)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (username, pref_key) DO UPDATE SET pref_value = EXCLUDED.pref_value
                    """, (username, pkey, pval))

                if cadence == "off":
                    cur.execute(f"UPDATE users SET email_alerts_enabled = {ph} WHERE username = {ph}", (False, username))
                else:
                    cur.execute(f"UPDATE users SET email_alerts_enabled = {ph} WHERE username = {ph}", (True, username))

                conn.commit()
                cur.close()
                conn.close()
            except Exception as e:
                print(f"[AlertPreference Error] {e}")

        status_msg = "paused" if cadence == "off" else f"updated to '{cadence.capitalize()}'"
        return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Preferences Updated — TenderFlow</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {{ font-family: Inter, -apple-system, sans-serif; background: #f8fafc; color: #0f172a; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; padding: 20px; }}
        .card {{ background: #ffffff; border: 1px solid #e2e8f0; border-radius: 16px; padding: 32px; max-width: 480px; width: 100%; text-align: center; box-shadow: 0 10px 25px rgba(0,0,0,0.05); }}
        .icon {{ font-size: 40px; margin-bottom: 12px; }}
        h2 {{ margin: 0 0 12px 0; font-size: 20px; font-weight: 700; color: #0f172a; }}
        p {{ font-size: 14px; color: #475569; line-height: 1.6; margin-bottom: 24px; }}
        .btn {{ display: inline-block; background: #4f46e5; color: #ffffff; text-decoration: none; font-weight: 600; padding: 10px 20px; border-radius: 8px; font-size: 14px; }}
    </style>
</head>
<body>
    <div class="card">
        <div class="icon">🔔</div>
        <h2>Preferences Updated</h2>
        <p>Your TenderFlow email alert notifications have been successfully {status_msg}.</p>
        <a href="/#settings" class="btn">Manage All Settings &rarr;</a>
    </div>
</body>
</html>"""

    @app.route("/api/email-notifications/test", methods=["POST"])
    def api_email_notifications_test():
        if not session.get("logged_in"):
            return jsonify({"error": "Unauthorized"}), 401
        username = session.get("username", "")
        if not username:
            return jsonify({"error": "No user session found"}), 400

        try:
            target_email = session.get("email") or username
            conn = get_db_connection()
            ph = "%s"
            cur = conn.cursor()

            if not target_email or "@" not in target_email:
                try:
                    cur.execute(f"SELECT email FROM users WHERE username={ph}", (username,))
                    row = cur.fetchone()
                    if row and row[0] and "@" in str(row[0]):
                        target_email = str(row[0]).strip()
                except Exception:
                    pass

            if not target_email or "@" not in target_email:
                cur.close()
                conn.close()
                return jsonify({"ok": False, "error": f"No valid recipient email address found for account '{username}'"}), 400

            # Load user's actual email settings from database
            try:
                _run_email_settings_migration(cur)
                cur.execute(f"""
                    SELECT email_alerts_enabled, deadline_reminders_enabled,
                           deadline_reminders_threshold, fit_score_threshold
                    FROM users WHERE username={ph}
                """, (username,))
                user_sett = cur.fetchone()
            except Exception:
                user_sett = None

            alerts_on = True
            reminders_on = True
            u_threshold_str = "7 days before"
            u_fit_str = "40%"

            if user_sett:
                alerts_on = bool(user_sett[0]) if user_sett[0] is not None else True
                reminders_on = bool(user_sett[1]) if user_sett[1] is not None else True
                u_threshold_str = str(user_sett[2] or "7 days before")
                u_fit_str = str(user_sett[3] or "40%")

            # Parse fit score threshold
            try:
                fit_thresh = int(u_fit_str.replace("%", "").replace("+", "").strip())
            except Exception:
                fit_thresh = 40

            # Parse deadline days threshold
            days_thresh = 7
            if "3" in u_threshold_str:
                days_thresh = 3
            elif "1" in u_threshold_str:
                days_thresh = 1

            # Check for real matching tender in user's pipeline meeting fit_thresh
            matching_tenders = []
            try:
                cur.execute(f"""
                    SELECT tender_key, title, source, contracting_authority, submission_deadline, fit_score, fit_band
                    FROM pipeline
                    WHERE username={ph} AND fit_score >= {ph}
                    ORDER BY fit_score DESC
                """, (username, fit_thresh))
                matching_tenders = cur.fetchall()
            except Exception:
                matching_tenders = []

            matched_tender = None
            days_rem = days_thresh

            for mt in matching_tenders:
                t_key, t_title, t_source, t_auth, t_deadline, t_fit, t_band = mt
                d_date = _parse_deadline_date(t_deadline) if t_deadline else None
                d_remaining = (d_date - datetime.now(timezone.utc).date()).days if d_date else None
                
                source_id, rid = parse_tender_key(t_key)
                full_details = {}
                try:
                    full_details = _get_tender_details_dict(source_id, rid)
                except Exception:
                    pass

                tender_obj = {
                    "title": t_title, "source": t_source, "contracting_authority": t_auth,
                    "submission_deadline": t_deadline or "", "fit_score": t_fit or fit_thresh, "fit_band": t_band or "strong"
                }
                if isinstance(full_details, dict):
                    for k, v in full_details.items():
                        if v and (not tender_obj.get(k) or k == "description" or k not in tender_obj):
                            tender_obj[k] = v

                matched_tender = tender_obj
                if d_remaining is not None and d_remaining >= 0:
                    days_rem = d_remaining
                break

            cur.close()
            conn.close()

            # If no real pipeline tender matches, generate customized preview matching user's configured thresholds
            if not matched_tender:
                fit_band_label = "strong" if fit_thresh >= 70 else ("possible" if fit_thresh >= 50 else "weak")
                mock_date = (datetime.now() + timedelta(days=days_thresh)).strftime("%d/%m/%Y")
                matched_tender = {
                    "title": f"Preview Alert: Custom Tender ({fit_thresh}% Fit Score)",
                    "source": "etenders_ie",
                    "contracting_authority": "TenderFlow Automated Alert System",
                    "submission_deadline": mock_date,
                    "fit_score": fit_thresh,
                    "fit_band": fit_band_label,
                    "description": f"This test notification was generated dynamically based on your current email settings:\n\n"
                                   f"• Minimum Fit Score Filter: ≥{fit_thresh}%\n"
                                   f"• Deadline Alert Schedule: {days_thresh} day(s) before closing\n"
                                   f"• Alerts Status: {'ACTIVE (ON)' if (alerts_on and reminders_on) else 'DISABLED (OFF)'}\n\n"
                                   f"When tenders in your pipeline match these criteria, daily automated emails like this will be delivered to {target_email}.",
                    "procurement_type": "Services",
                    "estimated_value_eur": "250,000 EUR",
                    "cpv_codes": "72000000 - IT services & software development",
                    "procedure": "Open Procedure",
                    "contact_name": "Tender Alert Assistant",
                    "contact_email": target_email,
                }
                days_rem = days_thresh

            # Status banner reflecting saved settings
            if alerts_on and reminders_on:
                status_banner = f"""
                <div style="background:#f0fdf4;border:1px solid #bbf7d0;color:#166534;padding:10px 14px;border-radius:8px;margin-bottom:15px;font-size:13px;">
                  <strong>🟢 Test Alert Evaluation:</strong> Saved settings are <strong>ACTIVE</strong><br>
                  • <strong>Fit Score Filter:</strong> ≥{fit_thresh}% &nbsp;|&nbsp; • <strong>Deadline Alert:</strong> {days_thresh} day(s) before closing
                </div>"""
            else:
                status_banner = f"""
                <div style="background:#fffbeb;border:1px solid #fde68a;color:#92400e;padding:10px 14px;border-radius:8px;margin-bottom:15px;font-size:13px;">
                  <strong>⚠️ Note: Email Alerts are currently DISABLED in your settings.</strong><br>
                  Your configured settings: Fit Threshold ≥{fit_thresh}% | Deadline Alert: {days_thresh} day(s) before closing.<br>
                  <em>Turn on "Email Alerts Enabled" in settings to receive live daily notifications.</em>
                </div>"""

            subject = f"🧪 TenderFlow Alert Test: Fit ≥{fit_thresh}%, {days_rem}d Deadline"
            raw_html, pause_url = _build_email_html(username, matched_tender, days_rem)

            target_marker = "<body style=\"font-family: Arial, sans-serif; font-size: 14px; line-height: 1.5; color: #333; background: #fff; margin: 0; padding: 20px;\">"
            if target_marker in raw_html:
                html = raw_html.replace(target_marker, target_marker + status_banner, 1)
            else:
                html = status_banner + raw_html

            errs = []
            sent = _send_smtp_email(target_email, subject, html, error_container=errs, list_unsubscribe_url=pause_url)

            if not sent:
                err_detail = errs[0] if errs else "Unknown error"
                return jsonify({"ok": False, "error": f"SMTP server failed to send test email. Error detail: {err_detail}"}), 500

            return jsonify({
                "ok": True,
                "message": f"Test email sent to {target_email}! (Settings evaluated: Fit Score ≥{fit_thresh}%, Deadline {days_thresh}d before)"
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/auth/bypass")
    def api_auth_bypass():
        # Dev-only shortcut that logs the caller in as a full session with no password —
        # this route was previously unguarded, reachable by anyone who requested this exact
        # URL. Disabled unless ENABLE_AUTH_BYPASS is explicitly set — neither compose file sets
        # it, so it is off by default; opt in per machine via .env for local UI testing only and
        # never in .env.production. 404, not 403, so a scan against production doesn't learn the
        # route exists at all.
        if os.environ.get("ENABLE_AUTH_BYPASS", "").strip().lower() not in ("1", "true", "yes"):
            return ("", 404)
        session.clear()
        session["logged_in"] = True
        session["username"] = "testuser"
        session["email"] = "test@example.com"
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            ph = "%s"
            cursor.execute(f"SELECT username FROM users WHERE username = {ph}", ("testuser",))
            row = cursor.fetchone()
            if not row:
                cursor.execute(
                    f"INSERT INTO users (username, email, role, firebase_uid) VALUES ({ph}, {ph}, {ph}, {ph})",
                    ("testuser", "test@example.com", "member", "test_firebase_uid")
                )
                cursor.execute(
                    f"INSERT INTO credit_wallet (username, balance) VALUES ({ph}, 100)",
                    ("testuser",)
                )
                cursor.execute(
                    f"INSERT INTO company_profiles (username, name, profile_text) VALUES ({ph}, {ph}, {ph})",
                    ("testuser", "Test Company Ltd", "We build AI applications, web scrapers, data analysis tools, and software solutions using Python, Node, React, and databases.")
                )

            conn.commit()
            cursor.close()
        except Exception as ex:
            print("Bypass error:", ex)
        finally:
            conn.close()
        return redirect("/")

    @app.before_request
    def buyer_workspace_gate():
        # web/ is served from the site root, so the page's own files need the switch too (the API
        # routes are simply not registered when it is off).
        if not ENABLE_BUYER_WORKSPACE and request.path.startswith("/buyer-workspace"):
            return ("", 404)
        return None

    @app.before_request
    def growth_studio_gate():
        # Same for Growth Studio's own files (the /api/growth routes are not registered when it is off).
        if not ENABLE_GROWTH_STUDIO and request.path.startswith("/growth-"):
            return ("", 404)
        return None

    @app.before_request
    def check_auth():
        allowed_paths = [
            "/login.html", "/pricing.html", "/api/auth/firebase", "/api/logout",
            "/api/auth/forgot-password",
            # Linked from emails and opened (or POSTed by mail clients' one-click unsubscribe)
            # without a session; the handlers do their own checks.
            "/api/alerts/preference", "/api/alerts/feedback",
            "/api/health", "/api/config/public", "/api/stripe/webhook",
            "/api/billing/catalog", "/translations.js", "/api/auth/bypass",
            "/favicon.ico", "/favicon.svg", "/apple-touch-icon.png",
            # pricing.html is public, so the stylesheet/scripts it loads must be too —
            # otherwise a logged-out visitor gets an unstyled page with its modals showing.
            "/styles.css", "/theme.css", "/shell.js", "/pricing.js",
        ]
        allowed_prefixes = ("/static/", "/legal/")
        if request.path in allowed_paths or request.path.startswith(allowed_prefixes):
            if session.get("logged_in") and request.path == "/login.html":
                return redirect("/")
            return None

        if not session.get("logged_in"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Unauthorized"}), 401
            if request.path in ("/pricing.html",) or request.path.startswith("/legal/"):
                return None
            return redirect("/login.html")

        # Idle timeout: the session cookie itself lasts 7 days (PERMANENT_SESSION_LIFETIME
        # below), so without this a browser that's never fully quit stays signed in
        # indefinitely. last_activity is touched on every authenticated request, so it only
        # advances on genuine traffic — nothing here polls in the background to fake activity.
        now_ts = time.time()
        last_activity = session.get("last_activity")
        if last_activity is not None and (now_ts - last_activity) > SESSION_IDLE_TIMEOUT_SECONDS:
            session.clear()
            if request.path.startswith("/api/"):
                return jsonify({"error": "Unauthorized", "reason": "session_expired"}), 401
            if request.path in ("/pricing.html",) or request.path.startswith("/legal/"):
                return None
            return redirect("/login.html")
        session["last_activity"] = now_ts

        if request.method not in ("GET", "HEAD", "OPTIONS"):
            username = session.get("username")
            if username:
                conn = get_db_connection()
                try:
                    cursor = conn.cursor()
                    ph = "%s"
                    cursor.execute(f"SELECT role FROM users WHERE username = {ph}", (username,))
                    row = cursor.fetchone()
                    role = row[0] if (row and row[0]) else "member"
                    cursor.close()
                    if role == "viewer":
                        conn.close()
                        return jsonify({"error": "Forbidden: viewers cannot modify data"}), 403
                except Exception as ex:
                    print("Error checking viewer role in before_request:", ex)
                finally:
                    conn.close()

            if not validate_csrf():
                if request.path.startswith("/api/"):
                    return jsonify({"error": "Invalid CSRF token"}), 403
        return None



    @app.get("/")

    def index():

        return app.send_static_file("index.html")



    @app.get("/pricing.html")
    def pricing_page():
        return app.send_static_file("pricing.html")

    @app.get("/buyer-workspace")
    def buyer_workspace_page():
        return app.send_static_file("buyer-workspace.html")

    @app.get("/admin.html")
    def admin_page():
        # Login is enforced by check_auth; the page itself is only for admins.
        from tender_app.blueprints.admin_bp import _is_admin
        if not _is_admin():
            return redirect("/")
        return app.send_static_file("admin.html")

    @app.get("/api/health")
    def api_health():
        return jsonify(
            {
                "ok": True,
                "apiVersion": API_VERSION,
                "multiPortal": True,
                "sources": list(SOURCES.keys()),
            }
        )

    @app.get("/api/sources")

    def api_sources():

        return jsonify(

            {

                "sources": [
                    {
                        "id": sid,
                        "label": cfg["label"],
                        "url": source_public_url(cfg),
                    }
                    for sid, cfg in SOURCES.items()
                ]

            }

        )

    # ── AI routes (ai_bp) ─────────────────────────────────────────────────────
    @app.post("/api/fit-score")
    @require_credits(CREDIT_COST_FIT_SCORE, "fit_score", get_db_connection)
    def api_fit_score():
        """Deep AI fit score for one tender against a selected company profile.

        Body: { source, resource_id, profile_id, tender: {title, authority,
                value, description, ...}, refresh: bool }
        Returns: { ok, score, band, reasons[], summary, cached }
        """
        body = request.get_json(silent=True) or {}
        username = session.get("username", "admin")
        source = (body.get("source") or "").strip()
        resource_id = str(body.get("resource_id") or "").strip()
        profile_id = str(body.get("profile_id") or "").strip()
        tender = body.get("tender") or {}
        refresh = bool(body.get("refresh"))

        print(f"[DEBUG /api/fit-score] username={username}, profile_id={profile_id}, source={source}, resource_id={resource_id}")

        profile_text = ""
        meta_json = "{}"
        profile_name = ""

        # 1) Load the company profile (first: the cache check needs to know if it changed)
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            if profile_id:
                cur.execute(
                    "SELECT name, profile_text, meta_json, id FROM company_profiles WHERE id=%s AND username=%s",
                    (profile_id, username),
                )
            else:
                cur.execute(
                    "SELECT name, profile_text, meta_json, id FROM company_profiles WHERE username=%s ORDER BY is_default DESC, id DESC LIMIT 1",
                    (username,),
                )
            prow = cur.fetchone()
            cur.close()
            conn.close()
            print(f"[DEBUG /api/fit-score] DB query result row: {prow}")
            if prow:
                profile_name = prow[0] or ""
                profile_text = prow[1] or ""
                meta_json = prow[2] or "{}"
                if not profile_id and prow[3]:
                    profile_id = str(prow[3])
            else:
                print(f"[DEBUG /api/fit-score] No profile row found in DB for profile_id={profile_id}, username={username}")
        except Exception as e:
            print("fit-score profile load failed:", e)

        if not profile_id or (not profile_text and meta_json in ("{}", "", None)):
            return jsonify({"ok": False, "error": "No company profile available"}), 400

        tender_key = f"{source}:{resource_id}" if resource_id else f"{source}:{(tender.get('title') or '')[:120]}"

        if not profile_text and meta_json in ("{}", "", None):
            print(f"[DEBUG /api/fit-score] Profile text is empty and meta_json is empty. Returning 404!")
            return jsonify({"ok": False, "error": "Company profile not found"}), 404

        # A cached score is only valid for the profile text it was computed from.
        profile_hash = hashlib.sha1(f"{profile_text}|{meta_json}".encode("utf-8")).hexdigest()[:16]

        # 2) Cache lookup
        if not refresh:
            try:
                conn = get_db_connection()
                cur = conn.cursor()
                cur.execute(
                    "SELECT score, band, reasons_json FROM fit_scores WHERE username=%s AND tender_key=%s AND profile_id=%s",
                    (username, tender_key, profile_id),
                )
                row = cur.fetchone()
                cur.close()
                conn.close()
                if row:
                    reasons_obj = {}
                    try:
                        reasons_obj = json.loads(row[2] or "{}")
                    except Exception:
                        reasons_obj = {}
                    # Entries saved before profile_hash existed have none: keep serving them.
                    if reasons_obj.get("profile_hash") not in (None, profile_hash):
                        raise LookupError("profile changed since this score was computed")
                    return jsonify({
                        "ok": True,
                        "cached": True,
                        "score": row[0],
                        "band": row[1],
                        "reasons": reasons_obj.get("reasons", []),
                        "gaps": reasons_obj.get("gaps", []),
                        "suggestions": reasons_obj.get("suggestions", []),
                        "summary": reasons_obj.get("summary", ""),
                    })
            except LookupError as e:
                print("fit-score cache skipped:", e)
            except Exception as e:
                print("fit-score cache lookup failed:", e)

        # 2.5) Load answer bank categories as soft signal for scoring (filtered by selected profile)
        ab_categories = []
        try:
            ab_conn = get_db_connection()
            ab_cur = ab_conn.cursor()
            ab_cur.execute(
                "SELECT DISTINCT category FROM answer_bank WHERE username=%s AND (profile_id=%s OR profile_id IS NULL)",
                (username, int(profile_id) if profile_id else None),
            )
            ab_categories = [r[0] for r in ab_cur.fetchall()]
            ab_cur.close(); ab_conn.close()
        except Exception as e:
            print("fit-score answer-bank categories fetch failed:", e)

        # 3) Build prompt and call DeepSeek
        tender_blob = json.dumps({
            "title": tender.get("title", ""),
            "authority": tender.get("authority") or tender.get("contracting_authority", ""),
            "estimated_value": tender.get("value") or tender.get("estimated_value_eur", ""),
            "procedure": tender.get("procedure", ""),
            "status": tender.get("status", ""),
            "deadline": tender.get("deadline") or tender.get("submission_deadline", ""),
            "description": (tender.get("description") or "")[:4000],
            "source": source,
        }, ensure_ascii=False)

        system = (
            "You are a UK/Ireland public-procurement bid strategist. Score how well a tender "
            "fits a supplier's company profile for the purpose of deciding whether to bid. "
            "Respond ONLY with JSON: {\"score\": <0-100 integer>, \"summary\": <one sentence>, "
            "\"reasons\": [<up to 4 short strengths>], \"gaps\": [<up to 4 short risks/gaps>], "
            "\"suggestions\": [<up to 4 short, concrete actions the supplier could take to close the "
            "corresponding gap, same order as gaps — e.g. team up with a subcontractor, get a "
            "specific certification, partner locally>]}. "
            "Score bands: 80-100 strong fit, 50-79 possible fit, 0-49 weak fit. Be realistic and "
            "weigh sector/capability match, company size vs contract value, geography, and feasibility."
        )
        ab_str = f"ESTABLISHED BID ANSWER AREAS (categories where the company has pre-approved responses): {', '.join(ab_categories)}\n\n" if ab_categories else ""
        user = (
            f"COMPANY PROFILE NAME: {profile_name}\n"
            f"COMPANY STRUCTURED DATA: {meta_json}\n"
            f"COMPANY CONTEXT / EXPERIENCE: {profile_text[:4000]}\n\n"
            f"{ab_str}"
            f"TENDER: {tender_blob}\n\n"
            "Return the JSON now."
        )

        try:
            parsed = call_chat_json(system=system, user=user, max_tokens=700, temperature=0.2)
        except Exception as e:
            msg = str(e)
            print("fit-score AI call failed:", msg)
            return jsonify({"ok": False, "error": msg}), 429 if "rate-limited" in msg else 502

        try:
            score = int(round(float(parsed.get("score", 0))))
        except Exception:
            score = 0
        score = max(0, min(100, score))
        band = "strong" if score >= 80 else "possible" if score >= 50 else "weak"
        reasons = [str(r) for r in (parsed.get("reasons") or [])][:4]
        gaps = [str(g) for g in (parsed.get("gaps") or [])][:4]
        suggestions = [str(s) for s in (parsed.get("suggestions") or [])][:4]
        summary = str(parsed.get("summary", ""))

        # 4) Persist cache
        reasons_json = json.dumps({"reasons": reasons, "gaps": gaps, "suggestions": suggestions, "summary": summary, "profile_hash": profile_hash})
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO fit_scores (username, tender_key, profile_id, score, band, reasons_json)
                   VALUES (%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (username, tender_key, profile_id)
                   DO UPDATE SET score=EXCLUDED.score, band=EXCLUDED.band,
                                 reasons_json=EXCLUDED.reasons_json, created_at=CURRENT_TIMESTAMP""",
                (username, tender_key, profile_id, score, band, reasons_json),
            )
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            print("fit-score cache write failed:", e)

        return jsonify({
            "ok": True,
            "cached": False,
            "score": score,
            "band": band,
            "reasons": reasons,
            "gaps": gaps,
            "suggestions": suggestions,
            "summary": summary,
        })

    COUNTY_ALIASES_MAP = {
        "Greater London": ["london", "camden", "greenwich", "hackney", "hammersmith", "fulham", "islington", "kensington", "chelsea", "lambeth", "lewisham", "southwark", "tower hamlets", "wandsworth", "westminster", "barking", "dagenham", "barnet", "bexley", "brent", "bromley", "croydon", "ealing", "enfield", "haringey", "harrow", "havering", "hillingdon", "hounslow", "kingston", "merton", "newham", "redbridge", "richmond", "sutton", "waltham forest", "city of london"],
        "City of London": ["city of london"],
        "Greater Manchester": ["manchester", "salford", "bolton", "bury", "oldham", "rochdale", "stockport", "tameside", "trafford", "wigan"],
        "West Midlands": ["birmingham", "coventry", "wolverhampton", "dudley", "sandwell", "solihull", "walsall"],
        "West Yorkshire": ["leeds", "bradford", "wakefield", "calderdale", "kirklees", "halifax", "huddersfield"],
        "South Yorkshire": ["sheffield", "doncaster", "rotherham", "barnsley"],
        "Merseyside": ["liverpool", "wirral", "sefton", "knowsley", "st helens"],
        "Tyne and Wear": ["newcastle", "sunderland", "gateshead", "south tyneside", "north tyneside"],
        "Bristol": ["bristol", "bath and north east somerset", "north somerset", "south gloucestershire"],
        "East Riding of Yorkshire": ["hull", "kingston upon hull", "east riding"],
        "Berkshire": ["reading", "slough", "bracknell", "windsor", "maidenhead", "wokingham", "west berkshire"],
        "Cambridgeshire": ["cambridge", "peterborough", "fenland", "huntingdonshire"],
        "Cheshire": ["cheshire east", "cheshire west", "chester", "warrington", "halton"],
        "Cornwall": ["cornwall", "isles of scilly", "truro"],
        "Cumbria": ["cumbria", "cumberland", "westmorland", "furness", "carlisle"],
        "Derbyshire": ["derby", "derbyshire"],
        "Devon": ["plymouth", "torbay", "exeter", "devon"],
        "Dorset": ["bournemouth", "poole", "christchurch", "dorset"],
        "Durham": ["county durham", "darlington", "hartlepool", "stockton"],
        "Essex": ["southend", "thurrock", "chelmsford", "colchester", "essex"],
        "Gloucestershire": ["gloucester", "cheltenham", "gloucestershire"],
        "Hampshire": ["southampton", "portsmouth", "winchester", "hampshire"],
        "Lancashire": ["blackpool", "blackburn", "preston", "lancashire", "lancaster"],
        "Leicestershire": ["leicester", "leicestershire"],
        "Lincolnshire": ["lincoln", "north lincolnshire", "north east lincolnshire", "lincolnshire"],
        "Norfolk": ["norwich", "norfolk"],
        "North Yorkshire": ["york", "middlesbrough", "redcar", "north yorkshire", "harrogate"],
        "Northamptonshire": ["northampton", "north northamptonshire", "west northamptonshire"],
        "Nottinghamshire": ["nottingham", "nottinghamshire"],
        "Oxfordshire": ["oxford", "oxfordshire"],
        "Staffordshire": ["stoke", "stoke-on-trent", "staffordshire", "stafford"],
        "Suffolk": ["ipswich", "suffolk"],
        "Surrey": ["surrey", "guildford", "woking", "epsom"],
        "Warwickshire": ["warwick", "warwickshire", "stratford", "nuneaton"],
        "Wiltshire": ["swindon", "salisbury", "wiltshire"],
        "City of Edinburgh": ["edinburgh", "city of edinburgh"],
        "Glasgow City": ["glasgow", "glasgow city"],
        "Aberdeen City": ["aberdeen", "aberdeen city"],
        "Dundee City": ["dundee", "dundee city"],
        "Highland": ["highlands", "highland", "inverness"],
        "Na h-Eileanan Siar (Western Isles)": ["western isles", "eilean siar", "stornoway", "outer hebrides"],
        "Dumfries and Galloway": ["dumfries", "galloway"],
        "Perth and Kinross": ["perth", "kinross"],
        "Argyll and Bute": ["argyll", "bute"],
        "Scottish Borders": ["borders", "scottish borders"],
        "Orkney Islands": ["orkney"],
        "Shetland Islands": ["shetland"],
        "Fife": ["fife", "kirkcaldy", "dunfermline"],
        "Moray": ["moray", "elgin"],
        "Stirling": ["stirling"],
        "Falkirk": ["falkirk"],
        "Cardiff": ["cardiff", "caerdydd"],
        "Swansea": ["swansea", "abertawe"],
        "Newport": ["newport", "casnewydd"],
        "Rhondda Cynon Taf": ["rhondda", "cynon", "taf", "rct"],
        "Carmarthenshire": ["carmarthen", "carmarthenshire", "sir gâr"],
        "Caerphilly": ["caerphilly", "caerffili"],
        "Flintshire": ["flintshire", "sir y fflint"],
        "Bridgend": ["bridgend", "pen-y-bont"],
        "Neath Port Talbot": ["neath", "port talbot", "castell-nedd"],
        "Wrexham": ["wrexham", "wrecsam"],
        "Powys": ["powys"],
        "Vale of Glamorgan": ["vale of glamorgan", "bro morgannwg", "barry"],
        "Pembrokeshire": ["pembrokeshire", "sir benfro"],
        "Gwynedd": ["gwynedd", "bangor"],
        "Conwy": ["conwy"],
        "Denbighshire": ["denbighshire", "sir ddinbych"],
        "Monmouthshire": ["monmouthshire", "sir fynwy"],
        "Torfaen": ["torfaen"],
        "Blaenau Gwent": ["blaenau gwent"],
        "Ceredigion": ["ceredigion", "aberystwyth"],
        "Isle of Anglesey": ["anglesey", "ynys môn"],
        "Merthyr Tydfil": ["merthyr", "merthyr tydfil"],
        "Clwyd": ["clwyd"],
        "Dyfed": ["dyfed"],
        "Gwent": ["gwent"],
        "Mid Glamorgan": ["mid glamorgan"],
        "South Glamorgan": ["south glamorgan"],
        "West Glamorgan": ["west glamorgan"],
        "Antrim": ["antrim", "belfast", "lisburn", "ballymena"],
        "Armagh": ["armagh", "craigavon"],
        "Down": ["down", "newry", "mourne", "ards", "north down"],
        "Fermanagh": ["fermanagh", "omagh"],
        "Londonderry": ["londonderry", "derry", "strabane", "causeway"],
        "Tyrone": ["tyrone", "dungannon"],
        "Dublin": ["dublin", "dlr", "fingal", "south dublin"],
        "Cork": ["cork"],
        "Galway": ["galway"],
        "Limerick": ["limerick"],
        "Waterford": ["waterford"],
        "Tipperary": ["tipperary"],
        "Kildare": ["kildare"],
        "Meath": ["meath"],
        "Wicklow": ["wicklow"],
        "Louth": ["louth", "drogheda", "dundalk"],
        "Donegal": ["donegal"],
        "Kerry": ["kerry", "tralee", "killarney"],
        "Mayo": ["mayo", "castlebar"],
        "Clare": ["clare", "ennis"],
        "Wexford": ["wexford"],
        "Kilkenny": ["kilkenny"],
        "Westmeath": ["westmeath", "athlon", "mullingar"],
        "Laois": ["laois", "portlaoise"],
        "Offaly": ["offaly", "tullamore"],
        "Cavan": ["cavan"],
        "Sligo": ["sligo"],
        "Roscommon": ["roscommon"],
        "Monaghan": ["monaghan"],
        "Carlow": ["carlow"],
        "Longford": ["longford"],
        "Leitrim": ["leitrim", "carrick-on-shannon"]
    }

    SPECIAL_COUNTY_WORDS = {
        "down": r"\bcounty\s+down\b|\bco\.?\s*down\b|\bdown\s+(district|council|area)\b|\bnewry.*down\b",
        "mayo": r"\bcounty\s+mayo\b|\bco\.?\s*mayo\b|\bmayo\b(?!r)",
        "clare": r"\bcounty\s+clare\b|\bco\.?\s*clare\b|\bclare\b(?![\w])",
        "ross": r"\bross\b|\bross-shire\b"
    }

    _SERVER_COUNTY_REGEX_CACHE = {}

    def get_server_county_regex(county_name):
        key = county_name.lower().strip()
        if key in _SERVER_COUNTY_REGEX_CACHE:
            return _SERVER_COUNTY_REGEX_CACHE[key]
        terms = [county_name]
        for k, aliases in COUNTY_ALIASES_MAP.items():
            if k.lower() == key:
                terms.extend(aliases)
                break
        parts = []
        for term in terms:
            t = term.lower().strip()
            if t in SPECIAL_COUNTY_WORDS:
                parts.append(SPECIAL_COUNTY_WORDS[t])
            elif len(t) <= 4:
                parts.append(r"\b" + re.escape(t) + r"\b")
            else:
                parts.append(r"\b" + re.escape(t))
        try:
            compiled = re.compile("|".join(parts), re.IGNORECASE)
            _SERVER_COUNTY_REGEX_CACHE[key] = compiled
            return compiled
        except Exception:
            return None

    # A notice's own location often names only a nation or region, never a county: Find a Tender writes
    # "UKD - North West (England)", "UKI - London", "UKM - Scotland". Matching county names cannot place
    # those, so an England-only search lost every one of them (Round 27). The page says which nations have
    # EVERY one of their counties selected (`nations`); a notice whose location names such a nation is
    # kept. Only the location and region fields are read for this, not the title or description. Keep in
    # step with NATION_HINTS in web/uk_counties.js.
    NATION_HINTS = {
        "England": re.compile(r"\b(england|yorkshire and the humber|east of england|north east|north west|south east|south west|east midlands|west midlands|london)\b", re.IGNORECASE),
        "Scotland": re.compile(r"\bscotland\b", re.IGNORECASE),
        "Wales": re.compile(r"\bwales\b", re.IGNORECASE),
        "Northern Ireland": re.compile(r"\bnorthern ireland\b", re.IGNORECASE),
    }

    def is_location_unstated(r):
        if not r:
            return True
        loc = str(r.get("location") or "").strip().lower()
        reg = str(r.get("region") or "").strip().lower()
        combined = f"{loc} {reg}".strip()
        if not combined:
            return True
        if combined in ("location not stated", "not stated", "unspecified", "location not specified", "not specified", "n/a", "uk", "united kingdom", "unknown"):
            return True
        return False

    def count_by_source(rows):
        counts: dict[str, int] = {}
        for r in rows:
            src = (r.get("source") or "") if isinstance(r, dict) else ""
            counts[src] = counts.get(src, 0) + 1
        return counts

    def build_count_reconciliation(c0, c1, d, c2, c3, c4, c5):
        """Per portal: fetched - dropped_invalid - dropped_duplicate + saved_added - hidden_by_county == shown.

        c0 rows the portals returned to this search, c1 after the invalid-row filter, d saved rows from earlier
        searches (after the same filter), c2 after merging by tender id, c3 after the second invalid pass,
        c4 after same-source same-title dedup, c5 what is sent (after the county filter)."""
        out: dict[str, dict] = {}
        for src in set(c0) | set(d) | set(c5):
            g = lambda c: c.get(src, 0)
            entry = {
                "fetched": g(c0),
                "dropped_invalid": (g(c0) - g(c1)) + (g(c2) - g(c3)),
                "dropped_duplicate": (g(c1) + g(d) - g(c2)) + (g(c3) - g(c4)),
                "saved_added": g(d),
                "hidden_by_county": g(c4) - g(c5),
                "shown": g(c5),
            }
            entry["balanced"] = (entry["fetched"] - entry["dropped_invalid"] - entry["dropped_duplicate"]
                                 + entry["saved_added"] - entry["hidden_by_county"]) == entry["shown"]
            out[src] = entry
        keys = ("fetched", "dropped_invalid", "dropped_duplicate", "saved_added", "hidden_by_county", "shown")
        total = {k: sum(e[k] for e in out.values()) for k in keys}
        total["balanced"] = all(e["balanced"] for e in out.values())
        return {"by_source": out, "total": total}

    def match_tender_counties(r, county_list, nations=()):
        if not county_list:
            return True, False
        source_str = (r.get("source") or "").lower()
        if source_str and source_str not in UK_IE_SOURCE_IDS:
            return False, False
        text_to_search = " ".join([
            r.get("title") or "",
            r.get("contracting_authority") or "",
            r.get("location") or "",
            r.get("description") or "",
            r.get("region") or ""
        ])
        for c in county_list:
            reg = get_server_county_regex(c)
            if reg and reg.search(text_to_search):
                return True, False
        where = f"{r.get('location') or ''} {r.get('region') or ''}"
        if where.strip():
            for nation in nations:
                hint = NATION_HINTS.get(nation)
                if hint and hint.search(where):
                    return True, False

        if is_location_unstated(r):
            return True, True

        return False, False

    # ── Search routes (search_bp) ───────────────────────────────────────────
    # NOTE: Search is FREE — no credit charge. Only AI actions (fit-score, analyse, proposal, etc.) charge credits.
    @app.get("/api/search")
    def api_search():
        q = (request.args.get("q") or "").strip()
        page = max(1, int(request.args.get("page") or "1"))
        refresh = request.args.get("refresh") == "1"
        scope = (request.args.get("scope") or request.args.get("source") or "all").strip()
        if not q:
            return jsonify({"error": "missing q"}), 400

        lang = request.args.get("lang", "en").strip().lower()

        def translate_row_list(rows_list):
            if lang not in ("fr", "de", "nl") or not q or not rows_list:
                return rows_list
            texts_to_translate = []
            for row in rows_list:
                for key in ["title", "contracting_authority", "procedure", "status"]:
                    val = row.get(key)
                    if isinstance(val, str) and val.strip():
                        texts_to_translate.append(val)
            unique_texts = list(set(texts_to_translate))
            if unique_texts:
                translated_list = translate_texts(unique_texts, lang)
                translation_map = dict(zip(unique_texts, translated_list))
                translated_rows = []
                for row in rows_list:
                    new_row = dict(row)
                    for key in ["title", "contracting_authority", "procedure", "status"]:
                        val = new_row.get(key)
                        if isinstance(val, str) and val.strip():
                            new_row[key] = translation_map.get(val, val)
                    translated_rows.append(new_row)
                return translated_rows
            return rows_list

        use_progressive = request.args.get("progressive", "1") != "0"

        # Ireland-only legacy pagination (only when progressive=0).
        if scope == "etenders_ie" and not use_progressive:
            page_rows, total = search_ie_only_page(q, page=page, page_size=PAGE_SIZE)
            page_rows = translate_row_list(page_rows)
            known_min = (page - 1) * PAGE_SIZE + len(page_rows)
            has_more = len(page_rows) >= PAGE_SIZE
            return jsonify(
                {
                    "query": q,
                    "page": page,
                    "pageSize": PAGE_SIZE,
                    "total": total if total is not None else known_min,
                    "totalExact": total is not None,
                    "hasMore": has_more and total is None,
                    "rows": page_rows,
                    "scope": "etenders_ie",
                    "meta": {
                        "source_counts": {"etenders_ie": total if total is not None else known_min},
                        "total": total if total is not None else known_min,
                        "errors": {},
                        "urgent_count": sum(
                            1 for r in page_rows if r.get("deadline_urgency") == "critical"
                        ),
                        "deadline_alert_days": 10,
                    },
                }
            )

        if scope != "all":
            # Support comma-separated list of portal IDs
            requested_ids = [s.strip() for s in scope.split(",") if s.strip()]
            unknown = [s for s in requested_ids if s not in SOURCES]
            if unknown:
                return jsonify({"error": f"unknown scope: {','.join(unknown)}"}), 400
            # Normalise: if all portals selected, treat as "all"
            if set(requested_ids) == set(SOURCES.keys()):
                scope = "all"

        cache_key = f"{q.lower()}:{scope}"
        job_id = (request.args.get("jobId") or "").strip()

        if use_progressive:
            if refresh or cache_key not in _JOB_BY_CACHE_KEY:
                job = start_progressive_search(q, scope)
                _JOB_BY_CACHE_KEY[cache_key] = job.job_id
                job_id = job.job_id
            elif job_id:
                job = get_job(job_id)
            else:
                job = get_job(_JOB_BY_CACHE_KEY.get(cache_key, ""))

            if job is None:
                job = start_progressive_search(q, scope)
                _JOB_BY_CACHE_KEY[cache_key] = job.job_id
                job_id = job.job_id

            rows, meta, phase = job_snapshot(job)
            if phase == "complete":
                SEARCH_CACHE[cache_key] = (rows, meta)
            total = meta.get("total", len(rows))
            total_exact = bool(meta.get("totalExact"))
            # what the portals returned to THIS search, before saved rows from earlier searches are added below
            live_tender_ids = {generate_tender_id(r) for r in rows}
        else:
            if refresh or cache_key not in SEARCH_CACHE:
                rows, meta = search_single_source(q, scope)
                SEARCH_CACHE[cache_key] = (rows, meta)
            else:
                rows, meta = SEARCH_CACHE[cache_key]
            phase = "complete"
            job_id = ""
            total = len(rows)
            total_exact = True
            live_tender_ids = {generate_tender_id(r) for r in rows}

        # Translate search list query results
        rows = translate_row_list(rows)
        _c0 = count_by_source(rows)
        # Filter out fake / error-page / synthetic rows before saving to DB
        rows = [r for r in rows if not _is_invalid_tender_row(r, q)]
        _c1 = count_by_source(rows)

        # Upsert live scraped tenders into DB master store
        if rows:
            if use_progressive and job is not None:
                # A growing result list is re-read on every poll; only store rows not yet stored.
                new_rows = []
                for r in rows:
                    tid = generate_tender_id(r)
                    if tid and tid not in job.upserted_ids:
                        job.upserted_ids.add(tid)
                        new_rows.append(r)
                upsert_tenders(new_rows)
            else:
                upsert_tenders(rows)

        # Query database master store for stored tenders matching query q
        db_rows = search_tenders_db(q, limit=500)
        if db_rows:
            db_rows = translate_row_list(db_rows)
            db_rows = [r for r in db_rows if not _is_invalid_tender_row(r, q)]
        _d = count_by_source(db_rows or [])

        # Combine live results + stored DB results (de-duplicating by tender ID)
        seen_ids = set()
        combined_rows = []
        for r in rows:
            tid = generate_tender_id(r)
            if tid and tid not in seen_ids:
                seen_ids.add(tid)
                combined_rows.append(r)
        for r in db_rows:
            tid = generate_tender_id(r)
            if tid and tid not in seen_ids:
                seen_ids.add(tid)
                combined_rows.append(r)
        rows = combined_rows
        _c2 = count_by_source(rows)

        # P1a: Filter out fake / error-page / login-wall rows before sending to client.
        rows = [r for r in rows if not _is_invalid_tender_row(r, q)]
        _c3 = count_by_source(rows)

        # P1b: Same-source, same-title dedup (catches identical scraped rows from one portal).
        seen_source_titles: set[tuple[str, str]] = set()
        deduped: list[dict] = []
        for r in rows:
            src   = (r.get("source") or "").strip()
            rtitle = (r.get("title") or "").strip().lower()
            key   = (src, rtitle)
            if key in seen_source_titles:
                continue
            seen_source_titles.add(key)
            deduped.append(r)
        rows = deduped
        _c4 = count_by_source(rows)

        total = len(rows)

        if q and q.strip() and q.strip().lower() != "all":
            q_clean = q.strip().lower()
            prefix_match = re.match(r"^(?:supplier|winner|contractor|buyer|authority|title):\s*(.*)$", q_clean, re.I)
            if prefix_match:
                q_clean = prefix_match.group(1).strip()
            if q_clean and q_clean != "all":
                for r in rows:
                    if not r.get("_match_reason"):
                        sup = (r.get("supplier_name") or r.get("awarded_supplier") or r.get("supplier") or r.get("winner") or "").lower()
                        sub = (r.get("subcontractor") or r.get("framework_members") or "").lower()
                        auth = (r.get("contracting_authority") or r.get("authority_name") or "").lower()
                        desc = (r.get("description") or r.get("scope") or r.get("abstract") or "").lower()
                        title = (r.get("title") or "").lower()
                        if q_clean in title:
                            r["_match_reason"] = "title"
                        elif q_clean in sup:
                            r["_match_reason"] = "supplier name"
                        elif q_clean in sub:
                            r["_match_reason"] = "subcontractor"
                        elif q_clean in auth:
                            r["_match_reason"] = "authority"
                        elif q_clean in desc:
                            r["_match_reason"] = "scope text"

        # Mark what the portals themselves returned for this query. A portal matches on text the list row does not carry
        # (an eTenders Ireland / NI row has only a title and a buyer), so the page must not drop these rows just because
        # the word is not in the fields it can see: it files them under "Returned by the portal" instead.
        for r in rows:
            if generate_tender_id(r) in live_tender_ids:
                r["_portal_hit"] = True

        # Filter by Counties if specified
        counties_raw = request.args.get("counties", "").strip()
        whole_nations = [n.strip() for n in request.args.get("nations", "").split(",") if n.strip() in NATION_HINTS]
        county_filter_info = None
        if counties_raw:
            county_list = [c.strip() for c in counties_raw.split(",") if c.strip()]
            if county_list:
                # Count what the filter removes, per portal that was part of this search, so the
                # page can say so -- otherwise a portal that finished with rows shows nothing.
                scope_ids = set(SOURCES) if scope == "all" else {s.strip() for s in scope.split(",") if s.strip()}
                kept_rows = []
                hidden_by_source: dict[str, int] = {}
                unstated_count = 0
                for r in rows:
                    kept, unstated = match_tender_counties(r, county_list, whole_nations)
                    if kept:
                        if unstated:
                            r["location_not_stated"] = True
                            unstated_count += 1
                        kept_rows.append(r)
                    elif r.get("source") in scope_ids:
                        hidden_by_source[r["source"]] = hidden_by_source.get(r["source"], 0) + 1
                rows = kept_rows
                total = len(rows)
                county_filter_info = {
                    "counties": len(county_list),
                    "hidden_total": sum(hidden_by_source.values()),
                    "hidden_by_source": hidden_by_source,
                    "unstated_total": unstated_count,
                }

        # Recalculate source counts and total in meta to reflect all deduplicated combined rows
        source_counts = {}
        for r in rows:
            src = r.get("source")
            if src:
                source_counts[src] = source_counts.get(src, 0) + 1
        if not isinstance(meta, dict):
            meta = {}
        meta["source_counts"] = source_counts
        meta["reconciliation"] = build_count_reconciliation(_c0, _c1, _d, _c2, _c3, _c4, count_by_source(rows))
        if county_filter_info:
            meta["county_filter"] = county_filter_info
        else:
            meta.pop("county_filter", None)

        # Say, per portal, what it returned to this search and how that relates to what is on screen:
        # `count` is what the portal answered with, `shown` the rows in the list now, `stored` those of
        # them that were saved by earlier searches (the list always includes those), and
        # `hidden_by_county` what the county filter took out. Without this a portal that returned
        # nothing looks the same as one whose rows were filtered or came from an earlier search.
        portal_results = meta.get("portal_results")
        if use_progressive and isinstance(portal_results, dict):
            stored_by_source: dict[str, int] = {}
            for r in rows:
                src = r.get("source")
                if src and generate_tender_id(r) not in live_tender_ids:
                    stored_by_source[src] = stored_by_source.get(src, 0) + 1
            hidden_by_county = (county_filter_info or {}).get("hidden_by_source", {})
            for sid, pr in portal_results.items():
                pr["shown"] = source_counts.get(sid, 0)
                pr["stored"] = stored_by_source.get(sid, 0)
                pr["hidden_by_county"] = hidden_by_county.get(sid, 0)
                rec = meta["reconciliation"]["by_source"].get(sid) or {}
                pr["dropped_invalid"] = rec.get("dropped_invalid", 0)
                pr["dropped_duplicate"] = rec.get("dropped_duplicate", 0)
        return jsonify(
            {
                "query": q,
                "page": 1,
                "pageSize": len(rows) or PAGE_SIZE,
                "total": total,
                "totalExact": total_exact,
                "hasMore": False,
                "rows": rows,
                "scope": scope,
                "apiVersion": API_VERSION,
                "searchPhase": phase,
                "jobId": job_id or None,
                "meta": meta,
            }
        )

    @app.post("/api/search/stop")
    def api_search_stop():
        body = request.get_json(silent=True) or {}
        job_id = (body.get("jobId") or request.args.get("jobId") or "").strip()
        source_id = (body.get("sourceId") or request.args.get("sourceId") or "").strip()
        
        if not job_id:
            return jsonify({"ok": False, "error": "missing jobId"}), 400
            
        from etenders_scraper.sources.progressive_search import stop_all_search, stop_source_search
        
        if source_id:
            ok = stop_source_search(job_id, source_id)
        else:
            ok = stop_all_search(job_id)
            
        if not ok:
            return jsonify({"ok": False, "error": "job not found"}), 404
            
        return jsonify({"ok": True})

    @app.post("/api/search/resume")
    def api_search_resume():
        body = request.get_json(silent=True) or {}
        job_id = (body.get("jobId") or request.args.get("jobId") or "").strip()
        source_id = (body.get("sourceId") or request.args.get("sourceId") or "").strip()
        
        if not job_id:
            return jsonify({"ok": False, "error": "missing jobId"}), 400
            
        from etenders_scraper.sources.progressive_search import resume_all_search, resume_source_search
        
        if source_id:
            ok = resume_source_search(job_id, source_id)
        else:
            ok = resume_all_search(job_id)
            
        if not ok:
            return jsonify({"ok": False, "error": "job not found"}), 404

        return jsonify({"ok": True})

    @app.post("/api/search/retry")
    def api_search_retry():
        """Run one portal of a search again (after it failed, timed out or came back empty)."""
        body = request.get_json(silent=True) or {}
        job_id = (body.get("jobId") or "").strip()
        source_id = (body.get("sourceId") or "").strip()
        if not job_id or not source_id:
            return jsonify({"ok": False, "error": "jobId and sourceId are required"}), 400

        from etenders_scraper.sources.progressive_search import retry_source_search

        result = retry_source_search(job_id, source_id)
        if result is None:
            return jsonify({"ok": False, "error": "job not found"}), 404
        if result is False:
            return jsonify({"ok": False, "error": "that portal is not part of this search"}), 400
        return jsonify({"ok": True})

    @app.get("/api/tender/<resource_id>")
    def api_tender_legacy(resource_id: str):
        """Backward-compatible Ireland detail endpoint (original app)."""
        return api_tender("etenders_ie", resource_id)

    @app.get("/api/tender/<source>/<resource_id>")
    def api_tender(source: str, resource_id: str):
        if source not in SOURCES:
            return jsonify({"error": "unknown source"}), 404
        
        refresh = request.args.get("refresh") == "1"
        resp = _tender_detail_json(source, resource_id, bypass_cache=refresh)
        if isinstance(resp, tuple):
            return resp
            
        if hasattr(resp, "get_json"):
            detail = resp.get_json()
        else:
            return resp

        # Translate tender details notice
        lang = request.args.get("lang", "en").strip().lower()
        if lang in ("fr", "de", "nl") and isinstance(detail, dict):
            keys_to_translate = ["title", "description", "contracting_authority", "procedure", "procurement_type", "status"]
            texts_to_translate = []
            keys_found = []
            for key in keys_to_translate:
                val = detail.get(key)
                if isinstance(val, str) and val.strip():
                    texts_to_translate.append(val)
                    keys_found.append(key)
            if texts_to_translate:
                translated_texts = translate_texts(texts_to_translate, lang)
                for key, trans_val in zip(keys_found, translated_texts):
                    detail[key] = trans_val
            return jsonify(detail)

        return resp

    @app.get("/api/tender-docs/<source>/<resource_id>")
    def api_tender_docs(source: str, resource_id: str):
        """List documents available on the CfT documents tab."""
        cfg = SOURCES.get(source)
        if not cfg:
            return jsonify({"docs": [], "supported": False})
        
        if cfg.get("type") == "etenders":
            docs = list_cft_documents(cfg["base_url"], resource_id)
            return jsonify({"docs": docs, "supported": True, "count": len(docs)})
        else:
            detail_url = request.args.get("detail_url") or ""
            docs = list_non_etenders_documents(source, resource_id, detail_url)
            return jsonify({"docs": docs, "supported": True, "count": len(docs)})

    @app.post("/api/download-questionnaire")
    def api_download_questionnaire():
        import zipfile
        from datetime import datetime

        body = request.get_json(silent=True) or {}
        title = body.get("title") or "Unknown Tender"
        authority = body.get("authority") or "Unknown Authority"
        final_draft = body.get("final_draft") or ""

        def xml_escape(text: Any) -> str:
            s = str(text or "")
            return (
                s.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
                .replace("'", "&apos;")
            )

        title_esc = xml_escape(title)
        auth_esc = xml_escape(authority)
        date_str = xml_escape(datetime.now().strftime("%d/%m/%Y"))

        xml_content = []
        xml_content.append('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>')
        xml_content.append('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">')
        xml_content.append('  <w:body>')

        # Document Header/Title
        xml_content.append('    <w:p>')
        xml_content.append('      <w:pPr><w:spacing w:after="240"/></w:pPr>')
        xml_content.append('      <w:r>')
        xml_content.append('        <w:rPr>')
        xml_content.append('          <w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/>')
        xml_content.append('          <w:b/>')
        xml_content.append('          <w:sz w:val="40"/>')
        xml_content.append('          <w:color w:val="1E3A8A"/>')
        xml_content.append('        </w:rPr>')
        xml_content.append('        <w:t>Tender Bid Clarification Document</w:t>')
        xml_content.append('      </w:r>')
        xml_content.append('    </w:p>')

        # Meta Info
        for label, val in [("Tender:", title_esc), ("Authority:", auth_esc), ("Date:", date_str)]:
            xml_content.append('    <w:p>')
            xml_content.append('      <w:pPr><w:spacing w:after="60"/></w:pPr>')
            xml_content.append('      <w:r>')
            xml_content.append('        <w:rPr>')
            xml_content.append('          <w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/>')
            xml_content.append('          <w:b/>')
            xml_content.append('          <w:sz w:val="20"/>')
            xml_content.append('        </w:rPr>')
            xml_content.append(f'        <w:t>{label} </w:t>')
            xml_content.append('      </w:r>')
            xml_content.append('      <w:r>')
            xml_content.append('        <w:rPr>')
            xml_content.append('          <w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/>')
            xml_content.append('          <w:sz w:val="20"/>')
            xml_content.append('        </w:rPr>')
            xml_content.append(f'        <w:t>{val}</w:t>')
            xml_content.append('      </w:r>')
            xml_content.append('    </w:p>')

        # Divider
        xml_content.append('    <w:p>')
        xml_content.append('      <w:pPr>')
        xml_content.append('        <w:spacing w:after="360"/>')
        xml_content.append('        <w:pBdr>')
        xml_content.append('          <w:bottom w:val="single" w:sz="12" w:space="4" w:color="3B82F6"/>')
        xml_content.append('        </w:pBdr>')
        xml_content.append('      </w:pPr>')
        xml_content.append('    </w:p>')

        # Draft Letter Text
        paragraphs = final_draft.split("\n")
        for p_text in paragraphs:
            trimmed = p_text.strip()
            if not trimmed:
                # Add an empty line spacer
                xml_content.append('    <w:p><w:pPr><w:spacing w:after="120"/></w:pPr></w:p>')
                continue

            xml_content.append('    <w:p>')
            xml_content.append('      <w:pPr><w:spacing w:after="120"/></w:pPr>')
            xml_content.append('      <w:r>')
            xml_content.append('        <w:rPr>')
            xml_content.append('          <w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/>')
            xml_content.append('          <w:sz w:val="22"/>') # 11pt
            xml_content.append('        </w:rPr>')
            xml_content.append(f'        <w:t>{xml_escape(trimmed)}</w:t>')
            xml_content.append('      </w:r>')
            xml_content.append('    </w:p>')

        xml_content.append('  </w:body>')
        xml_content.append('</w:document>')

        doc_xml = "\n".join(xml_content)

        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(
                "[Content_Types].xml",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
                '  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
                '  <Default Extension="xml" ContentType="application/xml"/>\n'
                '  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>\n'
                '</Types>',
            )
            z.writestr(
                "_rels/.rels",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
                '  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>\n'
                '</Relationships>',
            )
            z.writestr("word/document.xml", doc_xml)

        out.seek(0)
        return send_file(
            out,
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            as_attachment=True,
            download_name="clarification_questions.docx",
        )

    @app.get("/api/analyses")
    def get_analyses():
        username = session.get("username", "admin")
        company = (request.args.get("company") or "").strip()
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            if company:
                if company == "__none__":
                    cursor.execute(
                        "SELECT id, source, resource_id, tender_title, company, type, created_at FROM analyses WHERE username = %s AND (company IS NULL OR TRIM(company) = '') ORDER BY id DESC",
                        (username,)
                    )
                else:
                    cursor.execute(
                        "SELECT id, source, resource_id, tender_title, company, type, created_at FROM analyses WHERE username = %s AND LOWER(TRIM(company)) = LOWER(%s) ORDER BY id DESC",
                        (username, company)
                    )
            else:
                cursor.execute(
                    "SELECT id, source, resource_id, tender_title, company, type, created_at FROM analyses WHERE username = %s ORDER BY id DESC",
                    (username,)
                )
            rows = cursor.fetchall()
            cursor.close()
            conn.close()
            
            results = []
            for row in rows:
                results.append({
                    "id": row[0],
                    "source": row[1],
                    "resource_id": row[2],
                    "tender_title": row[3],
                    "company": row[4],
                    "type": row[5],
                    "created_at": row[6].isoformat() if hasattr(row[6], "isoformat") else str(row[6])
                })
            return jsonify({"ok": True, "analyses": results})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.get("/api/analyses/history")
    def get_analysis_history():
        """Fetch all historical versions generated for a specific tender resource and type."""
        username = session.get("username", "admin")
        source = request.args.get("source", "")
        resource_id = request.args.get("resource_id", "")
        type_name = request.args.get("type", "")

        if not source or not resource_id:
            return jsonify({"ok": False, "error": "source and resource_id required"}), 400

        try:
            conn = get_db_connection()
            cur = conn.cursor()
            ph = "%s"
            
            if type_name:
                cur.execute(
                    f"SELECT id, tender_title, company, type, created_at FROM analyses WHERE username={ph} AND source={ph} AND resource_id={ph} AND type={ph} ORDER BY id DESC",
                    (username, source, resource_id, type_name)
                )
            else:
                cur.execute(
                    f"SELECT id, tender_title, company, type, created_at FROM analyses WHERE username={ph} AND source={ph} AND resource_id={ph} ORDER BY id DESC",
                    (username, source, resource_id)
                )
            rows = cur.fetchall()
            cur.close()
            conn.close()

            history = []
            for r in rows:
                history.append({
                    "id": r[0],
                    "tender_title": r[1],
                    "company": r[2],
                    "type": r[3],
                    "created_at": r[4].isoformat() if hasattr(r[4], "isoformat") else str(r[4])
                })
            return jsonify({"ok": True, "history": history})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.get("/api/analyses/<int:analysis_id>")
    def get_analysis_detail(analysis_id):
        username = session.get("username", "admin")
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, source, resource_id, tender_title, company, files_json, type, analysis_json, created_at FROM analyses WHERE id = %s AND username = %s",
                (analysis_id, username)
            )
            row = cursor.fetchone()
            cursor.close()
            conn.close()
            
            if not row:
                return jsonify({"ok": False, "error": "Analysis not found"}), 404
                
            return jsonify({
                "ok": True,
                "analysis": {
                    "id": row[0],
                    "source": row[1],
                    "resource_id": row[2],
                    "tender_title": row[3],
                    "company": row[4],
                    "files_json": json.loads(row[5]) if row[5] else [],
                    "type": row[6],
                    "analysis_json": json.loads(row[7]) if row[7] else {},
                    "created_at": row[8].isoformat() if hasattr(row[8], "isoformat") else str(row[8])
                }
            })
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.delete("/api/analyses/<int:analysis_id>")
    def delete_analysis(analysis_id):
        username = session.get("username", "admin")
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM analyses WHERE id = %s AND username = %s",
                (analysis_id, username)
            )
            conn.commit()
            cursor.close()
            conn.close()
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.post("/api/analyse")
    @require_credits(CREDIT_COST_ANALYSE, "analyse", get_db_connection)
    def api_analyse():
        """Analyse a tender with DeepSeek. Auto-fetches CfT documents from eTenders."""
        import urllib.request
        import base64

        body = request.get_json(silent=True) or {}
        tender = body.get("tender") or {}
        company = (body.get("company") or "").strip()
        uploaded_files = body.get("files") or []

        title      = tender.get("title", "Unknown tender")
        description= tender.get("description", "")
        value      = tender.get("estimated_value_eur", "")
        procedure  = tender.get("procedure", "")
        proc_type  = tender.get("procurement_type", "")
        deadline   = tender.get("submission_deadline", "")
        duration   = tender.get("contract_duration_in_months_or_years_including_any_options_and_renewals", "")
        authority  = tender.get("contracting_authority", "")
        cpv        = tender.get("cpv_codes", "")
        clarif_end = tender.get("end_of_clarification_period", "")
        days_left  = tender.get("days_until_deadline", "")
        source     = tender.get("source", "")
        resource_id= tender.get("resource_id", "")
        detail_url = tender.get("detail_url", "")

        username = session.get("username", "admin")
        files_json = json.dumps(uploaded_files, sort_keys=True)
        lang = (body.get("lang") or request.args.get("lang", "en")).strip().lower()
        analysis_type = f"analysis_{lang}" if lang != "en" else "analysis"

        try:
            # Check cache first
            refresh = body.get("refresh", False)
            if refresh:
                try:
                    conn = get_db_connection()
                    cursor = conn.cursor()
                    cursor.execute(
                        "DELETE FROM analyses WHERE username = %s AND source = %s AND resource_id = %s AND company = %s AND files_json = %s AND type = %s",
                        (username, source, resource_id, company, files_json, analysis_type)
                    )
                    conn.commit()
                    cursor.close()
                    conn.close()
                except Exception as e:
                    print("Failed to clear cached analysis:", e)
            else:
                try:
                    conn = get_db_connection()
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT analysis_json FROM analyses WHERE username = %s AND source = %s AND resource_id = %s AND company = %s AND files_json = %s AND type = %s",
                        (username, source, resource_id, company, files_json, analysis_type)
                    )
                    cached_row = cursor.fetchone()
                    cursor.close()
                    conn.close()

                    if cached_row:
                        analysis = json.loads(cached_row[0])
                        return jsonify({
                            "ok": True,
                            "analysis": analysis,
                            "docs_used": [f.get("filename") for f in uploaded_files if f.get("filename")] or [title],
                            "docs_found": len(uploaded_files),
                            "cached": True
                        })
                except Exception as e:
                    print("Failed to query analysis cache:", e)

            # Auto-fetch documents from portal
            doc_text, doc_names, _ = fetch_tender_documents_and_extract(
                source, resource_id, detail_url, max_chars=120000
            )

            # Process uploaded files
            for f in uploaded_files:
                fname = f.get("filename") or "uploaded_file"
                b64_content = f.get("content") or ""
                if b64_content:
                    try:
                        file_data = base64.b64decode(b64_content)
                        text = _extract_bytes(file_data, fname, max_chars=120000)
                        if text.strip():
                            doc_names.append(fname)
                            doc_text += f"\n\n=== Uploaded Document: {fname} ===\n{text}"
                    except Exception as e:
                        doc_text += f"\n\n=== Uploaded Document: {fname} (Failed to parse: {str(e)}) ===\n"

            company_ctx = f"The company considering this tender is: {company}.\n" if company else ""
            doc_ctx = f"\n\nTENDER DOCUMENTS CONTENT:\n{doc_text[:240000]}\n" if doc_text else ""

            lang_names = {
                "fr": "French (Français)",
                "de": "German (Deutsch)",
                "nl": "Dutch (Nederlands)"
            }
            lang_instruction = ""
            if lang in lang_names:
                lang_name = lang_names[lang]
                lang_instruction = f"\n\nCRITICAL LANGUAGE REQUIREMENT: You MUST write all textual content in the JSON response in {lang_name} instead of English. Keep all JSON keys exactly in English."

            tender_context = f"""TENDER DETAILS:
- Title: {title}
- Contracting Authority: {authority}
- Description: {description}
- Estimated Value: {value}
- Procedure Type: {procedure} / {proc_type}
- CPV Codes: {cpv}
- Submission Deadline: {deadline} ({days_left} days left)
- Clarification Deadline: {clarif_end}
- Contract Duration: {duration}
{company_ctx}{doc_ctx}"""

            # ── STAGE 1: Ground-Truth Metadata & Requirement Extraction ───────────────
            stage1_prompt = f"""You are a senior Procurement Analyst. Read the tender documents and extract verified ground-truth metadata and core facts about this tender. Return ONLY valid JSON with no markdown or code fences. Escape all newlines as \\n inside string values.
{tender_context}

Extract and return this JSON:
{{
  "verified_authority": "Exact name of contracting authority/buyer from text (e.g. Social Security Scotland, NHS Wales), or 'Unknown Authority' if not found",
  "notice_status": "Contract Award Notice (Closed) | Live Opportunity (Open for Bids) | Prior Information Notice (PIN)",
  "contractual_structure": "Direct Prime Contractor | Subcontractor under Prime | Framework Call-off | Unknown",
  "prime_contractor_name": "Exact name of prime contractor if this is a subcontract opportunity, or null if direct or unknown",
  "project_objectives": "Clear statement of what the buyer wants to achieve",
  "deliverables": ["list", "of", "deliverables"],
  "budget": "Budget / estimated value details",
  "technology_stack": ["technologies", "platforms", "tools explicitly mentioned in text"],
  "gaps": ["list", "of", "missing", "or", "unclear", "information"]
}}"""
            try:
                stage1_response = call_chat_api(system="", user=stage1_prompt, max_tokens=2000, temperature=0.0)
                stage1_data = _parse_json_robust(stage1_response) if stage1_response else {}
            except Exception as st1_err:
                print(f"[Stage 1 Error]: {st1_err}")
                stage1_data = {}

            verified_auth = stage1_data.get("verified_authority") or authority
            notice_status = stage1_data.get("notice_status") or "Live Opportunity (Open for Bids)"
            contract_struct = stage1_data.get("contractual_structure") or "Direct Prime Contractor"
            prime_name = stage1_data.get("prime_contractor_name")
            tech_stack_str = ", ".join(stage1_data.get("technology_stack", [])) if stage1_data.get("technology_stack") else "None explicitly listed"

            ground_truth_header = f"""VERIFIED GROUND TRUTH (STAGE 1 FACT BASE):
- Contracting Authority: {verified_auth}
- Notice Status: {notice_status}
- Contractual Structure: {contract_struct} (Prime Contractor: {prime_name or 'N/A'})
- Explicit Technology Mentioned: {tech_stack_str}"""

            # ── STAGE 2: 8 Specialist Agents ──────────────────────────────────────────
            specialist_agents = [
                {
                    "name": "Technical Architect",
                    "focus": "infrastructure, cloud platforms (Azure/AWS/GCP), APIs, databases, Power BI, Fabric, networking, integration, hosting model, data pipelines, software architecture",
                    "questions_hint": "Ask about Azure subscriptions/licensing, available APIs, integration patterns, infrastructure ownership, platform versions, network requirements, hosting constraints, technical SLAs, data volumes, performance requirements, disaster recovery"
                },
                {
                    "name": "Data Engineer",
                    "focus": "datasets, data ownership, data quality, data volumes, formats, refresh frequencies, metadata, lineage, retention policies, data pipelines, ETL/ELT requirements",
                    "questions_hint": "Ask about which datasets are in scope, who owns them, data quality standards, update/refresh frequency, historical data availability, data formats, access methods, sensitive data classification, data migration scope"
                },
                {
                    "name": "Security Consultant",
                    "focus": "GDPR compliance, data classification, personal data handling, encryption requirements, audit logging, identity & access management, ISO 27001, Cyber Essentials, penetration testing, information security policies",
                    "questions_hint": "Ask about data classification levels, GDPR data controller/processor roles, security accreditation requirements (ISO 27001, Cyber Essentials Plus), encryption at rest/in transit, audit logging requirements, access control model, penetration testing expectations, security review process"
                },
                {
                    "name": "Commercial Consultant",
                    "focus": "budget, pricing model, payment milestones, licensing costs, VAT treatment, travel & expenses policy, cost ownership, change control pricing, optional extensions",
                    "questions_hint": "Ask about fixed price vs T&M, payment milestone schedule, whether travel/expenses are within budget, software licensing responsibility, VAT treatment, change control mechanism and cost, extension pricing, budget breakdown expectations"
                },
                {
                    "name": "Delivery Manager",
                    "focus": "governance structure, workshops, sprint cadence, stakeholder availability, knowledge transfer requirements, acceptance testing process, success criteria, project reporting, escalation paths",
                    "questions_hint": "Ask about governance model, how many stakeholders are available for workshops, sprint/agile expectations, sign-off process, knowledge transfer requirements, acceptance criteria, project reporting format, escalation path, resource availability from buyer side"
                },
                {
                    "name": "Business Analyst",
                    "focus": "use cases, KPIs, business objectives, reporting requirements, decision-making processes, success metrics, user personas, current state vs future state",
                    "questions_hint": "Ask about specific use cases, how success will be measured (KPIs), who the end users are, current pain points, existing systems to replace/integrate, reporting and dashboard requirements, sign-off authority"
                },
                {
                    "name": "Legal Consultant",
                    "focus": "contract terms, liability limits, insurance requirements, IP ownership, subcontracting rules, data ownership, termination clauses, warranties, indemnities",
                    "questions_hint": "Ask about contract template (standard/bespoke), liability cap, required insurance levels, IP ownership of deliverables, subcontracting permitted, data ownership post-contract, termination for convenience clauses, warranty period"
                },
                {
                    "name": "Procurement Specialist",
                    "focus": "evaluation criteria weightings, mandatory requirements and consequences, submission format (word limits, attachments, templates), scoring methodology, clarification process rules, consortium/subcontracting rules",
                    "questions_hint": "Ask about exact evaluation weightings, scoring methodology for quality questions, whether templates are mandatory, word/page limits per section, consortium rules, how clarification questions should be submitted, whether there are any mandatory pass/fail criteria not listed"
                }
            ]

            all_raw_questions = []

            def _run_specialist(agent):
                agent_prompt = f"""You are an expert {agent["name"]} reviewing a tender on behalf of a bidding company. Your job is to identify every assumption, gap, ambiguity, or missing piece of information that a {agent["name"]} MUST know before they can accurately estimate cost, confirm deliverability, or write a compliant bid response.

Your specialist domain: {agent["focus"]}
Guidance: {agent["questions_hint"]}

{ground_truth_header}

{tender_context}

RULES:
1. Only raise questions about genuinely missing, ambiguous, or unclear information.
2. If information is clearly stated in the tender, do NOT ask about it.
3. SCOPE GROUNDING: Focus on questions relevant to your domain. For generic service/staffing tenders, focus on operational governance, commercial risks, contract terms, and deliverables rather than hallucinating un-mentioned deep data engineering.
4. Be specific — reference the exact document section, clause, or page if available.
5. Think commercially: if you cannot price it, you must ask.
6. Think about risk: if it could cause a major delivery or compliance problem, you must ask.
7. Return ONLY valid JSON. Escape all newlines as \\n inside string values.
{lang_instruction}

Return this JSON:
{{
  "agent": "{agent["name"]}",
  "questions": [
    {{
      "category": "General | Technical | Data | Security | Commercial | Legal | Delivery | Reporting | Future / Support",
      "sub_category": "specific sub-topic e.g. Azure Licensing | GDPR | Payment Terms | KPIs",
      "reference": "Document name + section/clause/page reference, or 'Not mentioned in documents'",
      "issue": "The specific gap, ambiguity, or missing assumption",
      "question": "Professional clarification question suitable for submission to the buyer",
      "priority": "Critical | High | Medium | Low",
      "priority_reason": "Why this priority level — what risk does it represent?",
      "bid_impact": "Business rationale: how this affects pricing, delivery, compliance, or bid strategy"
    }}
  ]
}}"""
                try:
                    agent_response = call_chat_api(system="", user=agent_prompt, max_tokens=3000, temperature=0.1)
                    agent_data = _parse_json_robust(agent_response)
                    questions = agent_data.get("questions", [])
                    for q in questions:
                        q["agent"] = agent["name"]
                    return agent["name"], questions

                except Exception as agent_err:
                    print(f"[Stage 2 Error] Agent {agent['name']} failed: {agent_err}")
                    return agent["name"], []

            agent_map = {}
            with ThreadPoolExecutor(max_workers=len(specialist_agents)) as executor:
                futures = [executor.submit(_run_specialist, agent) for agent in specialist_agents]
                for future in as_completed(futures):
                    try:
                        aname, qs = future.result()
                        agent_map[aname] = qs
                    except Exception as err:
                        print(f"[Stage 2 Error] Specialist thread execution error: {err}")

            # Stage 2 Per-Agent Diagnostics Logging
            diag_summary = ", ".join([f"{name}: {len(qs)} Qs" for name, qs in agent_map.items()])
            print(f"[Stage 2 Diagnostics] Specialist Yields -> {diag_summary} (Total Raw: {sum(len(qs) for qs in agent_map.values())})")

            # Interleave questions round-robin across all 8 specialist agents (max 4 per agent to keep total <= 32)
            agent_map_trimmed = {}
            for aname, q_list in agent_map.items():
                agent_map_trimmed[aname] = q_list[:4]

            all_raw_questions = []
            max_len = max((len(q_list) for q_list in agent_map_trimmed.values()), default=0)
            for i in range(max_len):
                for aname, q_list in agent_map_trimmed.items():
                    if i < len(q_list):
                        all_raw_questions.append(q_list[i])

            # ── STAGE 3: Merge, Deduplicate & Finalise ──────────────────────────────
            questions_for_merge = all_raw_questions[:32]
            all_questions_json_trimmed = json.dumps(questions_for_merge, ensure_ascii=False)

            merge_prompt = f"""You are a senior Bid Manager reviewing clarification questions generated by a team of specialist consultants for this tender:

{ground_truth_header}
Tender: {title}
Authority: {verified_auth}
{company_ctx}
{doc_ctx[:3000]}

Below are {len(all_raw_questions)} raw questions from 8 specialist reviewers (Technical Architect, Data Engineer, Security Consultant, Commercial Consultant, Delivery Manager, Business Analyst, Legal Consultant, Procurement Specialist).

RAW QUESTIONS:
{all_questions_json_trimmed}

YOUR TASKS:
1. STRICT DEDUPLICATION: You MUST aggressively merge exact duplicates and near-duplicates. Never include multiple questions on the same underlying topic (e.g. payment milestones, contract extensions, VAT, pricing models, or security clearances must be asked EXACTLY ONCE).
2. REAL SCOPE VERIFICATION: Verify each question against the actual tender description. If a question makes specialized domain assumptions not grounded in the tender (e.g. asking about ETL pipelines for a generic PM staffing role), DISCARD IT.
3. MANDATORY CATEGORY COVERAGE: You MUST retain at least 1–3 high-value questions for EVERY active category present in raw questions (Technical, Security, Legal, Commercial, Delivery, Reporting, General, Data). NO valid operational category present in the input may be dropped to 0 questions. Limit total output to 25–30 unique, highest-impact questions.
4. GROUP all questions into standardized categories: General, Technical, Data, Security, Commercial, Legal, Delivery, Reporting, Future / Support.
5. PRIORITISE: within each group, Critical first, then High, Medium, Low. Explain why each question is prioritized.
6. RENUMBER sequentially starting from 1.
7. Write an executive summary of the key areas needing clarification.
8. Evaluate the Tender Validation Checklist across all operational areas.
9. Write a brief cover email/letter intro to the buyer.
10. Extract exact submission instructions: where to submit (email/portal) and how to submit (format/deadline).
11. Return ONLY valid JSON. Escape all newlines as \\n inside string values.
{lang_instruction}

Return this JSON structure:
{{
  "executive_summary": "Concise summary of main areas needing clarification and why they are critical to an accurate bid",
  "total_questions": <number>,
  "questions_table": [
    {{
      "no": 1,
      "category": "General | Technical | Data | Security | Commercial | Legal | Delivery | Reporting | Future / Support",
      "sub_category": "specific sub-topic",
      "reference": "document + section reference",
      "issue": "gap or ambiguity",
      "question": "professional question for the buyer",
      "priority": "Critical | High | Medium | Low",
      "priority_reason": "why this priority and what risk it represents",
      "bid_impact": "business rationale: impact on pricing/delivery/compliance/strategy",
      "agent": "which specialist raised this"
    }}
  ],
  "priority_questions": {{
    "critical": ["question text 1", "question text 2"],
    "high": ["question text 1", "question text 2"],
    "medium": ["question text 1"],
    "low": ["question text 1"]
  }},
  "questions_by_category": {{
    "General": [1, 2],
    "Technical": [],
    "Data": [],
    "Security": [],
    "Commercial": [],
    "Legal": [],
    "Delivery": [],
    "Reporting": [],
    "Future / Support": []
  }},
  "checklist": {{
    "Scope": "Fully defined | Partially defined | Not defined",
    "Deliverables": "Measurable | Partially measurable | Not measurable",
    "Data": "Formats/quality/ownership clear | Partially clear | Not clear",
    "Hosting": "Infrastructure ownership clear | Partially clear | Not clear",
    "Security": "GDPR/audit/access defined | Partially defined | Not defined",
    "Commercial": "Budget/pricing/payment clear | Partially clear | Not clear",
    "Legal": "Contract/liability available | Partially available | Not available",
    "Delivery": "Governance/milestones defined | Partially defined | Not defined",
    "Training": "Knowledge transfer specified | Partially specified | Not specified",
    "Support": "Post-project support defined | Partially defined | Not defined",
    "Procurement": "Evaluation/submission rules clear | Partially clear | Not clear"
  }},
  "questions_to_avoid": [],
  "final_draft": "Dear Buyer, Please find enclosed our clarification questions regarding the tender.",
  "submission_instructions": {{
    "where_to_submit": "Specific details from documents on where to submit.",
    "how_to_submit": "Specific format/template/method."
  }}
}}"""

            try:
                merge_response = call_chat_api(system="", user=merge_prompt, max_tokens=4000, temperature=0.0)
                analysis = _parse_json_robust(merge_response)
                if isinstance(analysis, list) and len(analysis) > 0:
                    analysis = {"questions_table": analysis}

                if isinstance(analysis, dict):
                    if not analysis.get("questions_table"):
                        for alt_key in ["questions", "items", "clarifications", "questions_list", "table"]:
                            if analysis.get(alt_key) and isinstance(analysis[alt_key], list):
                                analysis["questions_table"] = analysis[alt_key]
                                break
                    if analysis.get("questions_table"):
                        analysis["total_questions"] = len(analysis["questions_table"])

                if not isinstance(analysis, dict) or not analysis.get("questions_table") or len(analysis["questions_table"]) < 1:
                    raise ValueError(f"Merge returned invalid JSON or empty questions table")
            except Exception as merge_err:
                print(f"[Merge Stage] Note: {merge_err}. Assembling directly from {len(all_raw_questions)} raw agent questions.")
                priority_order = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
                all_raw_questions.sort(key=lambda q: priority_order.get(q.get("priority", "Medium"), 2))
                for i, q in enumerate(all_raw_questions, 1):
                    q["no"] = i
                by_cat = {}
                for q in all_raw_questions:
                    cat = q.get("category", "General")
                    by_cat.setdefault(cat, [])
                    by_cat[cat].append(q["no"])
                pq = {"critical": [], "high": [], "medium": [], "low": []}
                for q in all_raw_questions:
                    p = q.get("priority", "Medium").lower()
                    if p in pq:
                        pq[p].append(q.get("question", ""))
                analysis = {
                    "executive_summary": f"Multi-agent advisory analysis generated {len(all_raw_questions)} clarification questions across {len(by_cat)} categories.",
                    "total_questions": len(all_raw_questions),
                    "questions_table": all_raw_questions,
                    "priority_questions": pq,
                    "questions_by_category": by_cat,
                    "checklist": {
                        "Scope": "Partially defined",
                        "Deliverables": "Partially measurable",
                        "Data": "Partially clear",
                        "Hosting": "Partially clear",
                        "Security": "Partially defined",
                        "Commercial": "Partially clear",
                        "Legal": "Partially available",
                        "Delivery": "Partially defined"
                    },
                    "questions_to_avoid": [],
                    "final_draft": f"Dear {authority},\n\nFollowing our review of the tender documents for '{title}', we wish to submit the following clarification questions:\n\n" + "\n".join([f"{q['no']}. [{q.get('category','General')}] {q.get('question','')}" for q in all_raw_questions]) + "\n\nWe look forward to your response.\n\nYours sincerely,\n" + (company or "The Bidding Company"),
                    "submission_instructions": {
                        "where_to_submit": "Not specified in tender documents",
                        "how_to_submit": "Not specified in tender documents"
                    }
                }

            # Python-level post-processing: Enforce hard deduplication and 30-item hard cap
            if isinstance(analysis, dict) and analysis.get("questions_table"):
                q_table = analysis["questions_table"]
                seen_texts = set()
                deduped_q = []
                for q in q_table:
                    # Multi-key extraction of question text
                    q_raw = ""
                    if isinstance(q, dict):
                        for k in ["question", "question_text", "clarification", "text", "issue"]:
                            if q.get(k) and isinstance(q[k], str):
                                q_raw = q[k]
                                break
                    elif isinstance(q, str):
                        q_raw = q
                    
                    # Normalize string: lowercase, collapse spaces, strip punctuation
                    q_norm = "".join(c.lower() for c in q_raw if c.isalnum() or c.isspace()).strip()
                    q_norm = " ".join(q_norm.split())

                    if q_norm:
                        if q_norm not in seen_texts:
                            seen_texts.add(q_norm)
                            deduped_q.append(q)
                    else:
                        deduped_q.append(q)

                deduped_q = deduped_q[:30]
                for idx, q in enumerate(deduped_q, 1):
                    if isinstance(q, dict):
                        q["no"] = idx
                analysis["questions_table"] = deduped_q
                analysis["total_questions"] = len(deduped_q)

                # Programmatically generate full final_draft clarification document
                auth_name = verified_auth if verified_auth and verified_auth != "Unknown Authority" else (authority or "Contracting Authority")
                comp_name = company or "The Bidding Company"
                letter_lines = [
                    f"Dear {auth_name},",
                    "",
                    f"Following our review of the tender documents for '{title}', we wish to submit the following clarification questions on behalf of {comp_name}:",
                    ""
                ]
                for q in deduped_q:
                    if isinstance(q, dict):
                        q_no = q.get("no", "")
                        q_cat = q.get("category", "General")
                        q_txt = q.get("question", "")
                        letter_lines.append(f"{q_no}. [{q_cat}] {q_txt}")
                        letter_lines.append("")
                letter_lines.append("We look forward to your response.")
                letter_lines.append("")
                letter_lines.append("Yours sincerely,")
                letter_lines.append(comp_name)

                analysis["final_draft"] = "\n".join(letter_lines)

            # Post-process & enrich submission_instructions if missing or generic
            if not isinstance(analysis.get("submission_instructions"), dict):
                analysis["submission_instructions"] = {}

            sub_inst = analysis["submission_instructions"]
            where_val = str(sub_inst.get("where_to_submit") or "").strip()
            how_val = str(sub_inst.get("how_to_submit") or "").strip()

            deep_details = _get_tender_details_dict(source, resource_id) or {}
            c_email = deep_details.get("contact_email") or ""
            c_instructions = deep_details.get("submission_instructions") or ""
            c_authority = deep_details.get("contracting_authority") or authority or "the Contracting Authority"
            c_clarif = deep_details.get("clarification_deadline") or clarif_end or ""

            if not where_val or "not specified" in where_val.lower():
                if c_instructions and ("portal" in c_instructions.lower() or "etender" in c_instructions.lower()):
                    m_url = re.search(r"https?://[^\s\)]+", c_instructions)
                    portal_url = m_url.group(0) if m_url else ""
                    if portal_url:
                        where_val = f"Submit via the portal at {portal_url}"
                        if c_email:
                            where_val += f" or email direct queries to {c_email} ({c_authority})."
                    else:
                        where_val = f"Submit via the procurement portal specified for {c_authority}."
                        if c_email:
                            where_val += f" Direct email: {c_email}."
                elif c_email:
                    where_val = f"Submit via email directly to {c_email} ({c_authority})."
                elif source in ("sell2wales", "pcs"):
                    where_val = f"Submit online via the {SOURCES.get(source, {}).get('label', 'portal')} messaging area (http://etenderwales.bravosolution.co.uk or {SOURCES.get(source, {}).get('site_root', '')})."
                elif source == "etenders_ie":
                    where_val = "Submit online via the Ireland eTenders portal (https://www.etenders.gov.ie)."
                else:
                    where_val = f"Submit online via the official procurement portal for {c_authority}."
                sub_inst["where_to_submit"] = where_val

            if not how_val or "not specified" in how_val.lower():
                how_parts = []
                how_parts.append("Submit clarification questions electronically in written format via the portal messaging area or via email.")
                if c_email:
                    how_parts.append(f"Direct email contact: {c_email}.")
                if c_clarif:
                    how_parts.append(f"Ensure all clarification requests are submitted before the deadline of {c_clarif}.")
                sub_inst["how_to_submit"] = " ".join(how_parts)

            # Save to database cache
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO analyses (username, source, resource_id, tender_title, company, files_json, type, analysis_json) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (username, source, resource_id, title, company, files_json, analysis_type, json.dumps(analysis))
                )
                conn.commit()
                cursor.close()
                conn.close()
            except Exception as db_err:
                print("Failed to save analysis to history:", db_err)

            return jsonify({
                "ok": True,
                "analysis": analysis,
                "docs_used": doc_names,
                "docs_found": len(doc_names),
            })
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, AIServiceError):
                return jsonify({"ok": False, "error": str(exc)}), exc.status_code
            return jsonify({"ok": False, "error": str(exc)}), 500

    @app.post("/api/summary")
    @require_credits(CREDIT_COST_SUMMARY, "summary", get_db_connection)
    def api_summary():
        """Generate a brief overview and requirements summary of a tender using DeepSeek."""
        import urllib.request

        body = request.get_json(silent=True) or {}
        tender = body.get("tender") or {}

        title      = tender.get("title", "Unknown tender")
        description= tender.get("description", "")
        value      = tender.get("estimated_value_eur", "")
        procedure  = tender.get("procedure", "")
        proc_type  = tender.get("procurement_type", "")
        deadline   = tender.get("submission_deadline", "")
        duration   = tender.get("contract_duration_in_months_or_years_including_any_options_and_renewals", "")
        authority  = tender.get("contracting_authority", "")
        cpv        = tender.get("cpv_codes", "")
        clarif_end = tender.get("end_of_clarification_period", "")
        days_left  = tender.get("days_until_deadline", "")
        source     = tender.get("source", "")
        resource_id= tender.get("resource_id", "")
        detail_url = tender.get("detail_url", "")

        username = session.get("username", "admin")
        lang = (body.get("lang") or request.args.get("lang", "en")).strip().lower()
        summary_type = f"summary_{lang}" if lang != "en" else "summary"

        # Check cache first
        refresh = body.get("refresh", False)
        if refresh:
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute(
                    "DELETE FROM analyses WHERE username = %s AND source = %s AND resource_id = %s AND type = %s",
                    (username, source, resource_id, summary_type)
                )
                conn.commit()
                cursor.close()
                conn.close()
            except Exception as e:
                print("Failed to clear cached summary:", e)
        else:
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT analysis_json FROM analyses WHERE username = %s AND source = %s AND resource_id = %s AND type = %s",
                    (username, source, resource_id, summary_type)
                )
                cached_row = cursor.fetchone()
                cursor.close()
                conn.close()

                if cached_row:
                    summary_data = json.loads(cached_row[0])
                    return jsonify({
                        "ok": True,
                        "summary": summary_data,
                        "cached": True
                    })
            except Exception as e:
                print("Failed to query summary cache:", e)

        try:
            # Auto-fetch documents from portal
            doc_text, _, _ = fetch_tender_documents_and_extract(
                source, resource_id, detail_url, max_chars=120000
            )

            doc_ctx = f"\n\nTENDER DOCUMENTS CONTENT:\n{doc_text[:240000]}\n" if doc_text else ""

            lang_names = {
                "fr": "French (Français)",
                "de": "German (Deutsch)",
                "nl": "Dutch (Nederlands)"
            }
            lang_instruction = ""
            if lang in lang_names:
                lang_name = lang_names[lang]
                lang_instruction = f"\n\nCRITICAL LANGUAGE REQUIREMENT: You MUST write all textual explanations, lists, overview statements, requirements, and content values in the JSON response in the {lang_name} language instead of English. However, you MUST keep the JSON keys exactly in English as defined below (e.g. keep keys like 'title', 'authority', 'overview', 'scope_of_work', 'requirements', etc. exactly as they are). Only translate the content values associated with the keys."

            summary_agents = [
                {
                    "key": "executive",
                    "role": "Executive Analyst",
                    "instruction": "Analyze the high-level objective, strategic buyer intent, procedure, and core project goals.",
                    "schema": '"overview": "A clear 3-4 sentence executive overview of what this tender is about, buyer goals, and project scope."'
                },
                {
                    "key": "technical",
                    "role": "Technical Specialist",
                    "instruction": "Extract all technical specifications, scope items, key deliverables, technology stack, hosting model, and data specifications.",
                    "schema": '"scope_of_work": ["scope item 1", "scope item 2", "deliverable 3"], "requirements": ["technical requirement 1", "operational requirement 2"]'
                },
                {
                    "key": "compliance",
                    "role": "Compliance Auditor",
                    "instruction": "Extract mandatory pass/fail suitability requirements, required accreditations (ISO 27001, Cyber Essentials), legal & insurance minimums.",
                    "schema": '"eligibility": ["mandatory criterion 1", "accreditation requirement 2", "eligibility condition 3"]'
                },
                {
                    "key": "commercial",
                    "role": "Commercial & Timeline Lead",
                    "instruction": "Extract estimated contract value, payment structure, contract duration, submission deadline, and clarification deadline.",
                    "schema": '"estimated_value": "estimated contract value", "key_deadlines": {"submission": "submission deadline details", "clarification": "clarification end date", "duration": "contract duration"}'
                }
            ]

            def _run_summary_agent(ag):
                p = f"""You are an expert {ag["role"]} reviewing this tender opportunity.
{doc_ctx}

TENDER DETAILS:
- Title: {title}
- Contracting Authority: {authority}
- Description: {description}
- Estimated Value: {value}
- Procedure Type: {procedure} / {proc_type}
- CPV Codes: {cpv}
- Submission Deadline: {deadline} ({days_left} days left)
- Clarification Deadline: {clarif_end}
- Contract Duration: {duration}

Your task: {ag["instruction"]}
Return ONLY valid JSON with no markdown fences:
{{
  {ag["schema"]}
}}
{lang_instruction}"""
                try:
                    res_str = call_chat_api(system="", user=p, max_tokens=1500, temperature=0.2)
                    res_obj = _parse_json_robust(res_str)
                    return ag["key"], res_obj if isinstance(res_obj, dict) else {}
                except Exception as err:
                    print(f"Summary agent {ag['role']} error: {err}")
                    return ag["key"], {}

            summary_parts = {}
            with ThreadPoolExecutor(max_workers=len(summary_agents)) as executor:
                futures = [executor.submit(_run_summary_agent, ag) for ag in summary_agents]
                for future in as_completed(futures):
                    k, res_obj = future.result()
                    summary_parts[k] = res_obj if isinstance(res_obj, dict) else {}

            exec_part = summary_parts.get("executive") or {}
            tech_part = summary_parts.get("technical") or {}
            comp_part = summary_parts.get("compliance") or {}
            comm_part = summary_parts.get("commercial") or {}

            summary_data = {
                "title": title,
                "authority": authority,
                "overview": exec_part.get("overview") or (description[:350] if description else f"Tender opportunity published by {authority}."),
                "scope_of_work": tech_part.get("scope_of_work") or [description[:250]] if description else ["Refer to tender documentation for full scope"],
                "requirements": tech_part.get("requirements") or [],
                "eligibility": comp_part.get("eligibility") or ["Refer to tender documentation for mandatory eligibility requirements"],
                "key_deadlines": comm_part.get("key_deadlines") or {
                    "submission": deadline or "See tender notice",
                    "clarification": clarif_end or "Not specified",
                    "duration": duration or "Not specified"
                },
                "estimated_value": comm_part.get("estimated_value") or value or "Not specified"
            }

            # Save to database cache
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO analyses (username, source, resource_id, tender_title, company, files_json, type, analysis_json) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (username, source, resource_id, title, "", "[]", summary_type, json.dumps(summary_data))
                )
                conn.commit()
                cursor.close()
                conn.close()
            except Exception as db_err:
                print("Failed to save summary to history:", db_err)

            return jsonify({
                "ok": True,
                "summary": summary_data
            })
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, AIServiceError):
                return jsonify({"ok": False, "error": str(exc)}), exc.status_code
            return jsonify({"ok": False, "error": str(exc)}), 500

# =============================================================================
    # BID RESPONSE ASSISTANT — DEEPSEEK PROMPT TEMPLATES
    # =============================================================================

    STAGE_1_SYSTEM = """You are a senior UK public procurement analyst with 15 years of experience 
reading Invitations to Tender (ITT), Requests for Proposal (RFP), and Prior Information Notices 
for UK and Irish government contracts.

Your job is to dissect tender documents and any crawled notice links/documents with forensic precision. 
You extract only what is explicitly stated in the provided text. You NEVER invent, infer, or assume 
requirements that are not clearly written. If something is ambiguous, you flag it as ambiguous — you do not guess.
Do deep research on all provided documents and sub-link pages. Ensure you target specific clauses, financial values, 
security accreditations (like ISO, Cyber Essentials), exact deadlines, and technical specifications. Avoid generalities.

You must return ONLY valid JSON. No preamble, no explanation, no markdown fences. Raw JSON only."""

    STAGE_1_USER = """Analyse the following tender documents in full and extract every structured 
requirement into the JSON schema below.

TENDER DOCUMENTS:
================
{tender_text}
================

Return ONLY this JSON structure (fill every field; use null if genuinely not stated, 
use "NOT STATED" string where a field was expected but missing):

{{
  "tender_reference": "string — official reference number",
  "tender_title": "string",
  "contracting_authority": "string — full name of buyer organisation",
  "procurement_type": "string — e.g. Services / Supplies / Works",
  "contract_value": {{
    "estimated": "string — e.g. £250,000",
    "currency": "string",
    "vat_inclusive": "boolean or null"
  }},
  "contract_duration": "string — e.g. 2 years with option to extend by 12 months",
  "submission_deadline": {{
    "date": "string — DD/MM/YYYY",
    "time": "string — e.g. 12:00 noon",
    "timezone": "string — e.g. GMT/BST",
    "portal": "string — where to submit e.g. eTenders, Find a Tender, email"
  }},
  "clarification_deadline": {{
    "date": "string or null",
    "method": "string or null — e.g. portal Q&A, email"
  }},
  "evaluation_criteria": [
    {{
      "criterion": "string — exact name as written",
      "weighting_percent": "number or null",
      "type": "string — Quality / Price / Pass-Fail / Social Value",
      "sub_criteria": ["string array — list sub-criteria if stated, else empty array"],
      "scoring_method": "string or null — e.g. 0-10 scale, MEAT, pass/fail"
    }}
  ],
  "scope_of_work": {{
    "summary": "string — 2-3 sentence plain-English summary",
    "full_scope": "string — verbatim or near-verbatim from the ITT scope section",
    "key_deliverables": ["string array — bullet list of deliverables"],
    "locations": ["string array — delivery locations if stated"],
    "volumes": "string or null — e.g. estimated 500 units/year"
  }},
  "itt_questions": [
    {{
      "number": "string — e.g. Q1, 3.2, Section 4a",
      "question_text": "string — verbatim question from ITT",
      "word_limit": "number or null",
      "page_limit": "number or null",
      "weighting": "string or null",
      "mandatory": "boolean"
    }}
  ],
  "mandatory_requirements": [
    {{
      "requirement": "string — exact wording",
      "consequence_of_failure": "string — e.g. disqualification, zero score",
      "evidence_required": "string — what proof is needed"
    }}
  ],
  "forms_to_sign": [
    {{
      "form_name": "string — e.g. Form of Tender, Appendix A",
      "reference": "string or null — e.g. Appendix 3",
      "who_must_sign": "string — e.g. Authorised Signatory / Director",
      "notes": "string or null"
    }}
  ],
  "certificates_required": [
    {{
      "certificate": "string — e.g. ISO 9001:2015, Cyber Essentials Plus",
      "mandatory_or_scored": "string — MANDATORY / SCORED / DESIRABLE",
      "minimum_level": "string or null — e.g. minimum £5m public liability",
      "expiry_required": "boolean — must cert be in date at submission",
      "equivalent_accepted": "boolean — is equivalent evidence accepted"
    }}
  ],
  "insurance_requirements": [
    {{
      "type": "string — e.g. Public Liability, Professional Indemnity",
      "minimum_value": "string — e.g. £5,000,000 per claim",
      "mandatory": "boolean"
    }}
  ],
  "financial_requirements": [
    {{
      "requirement": "string — e.g. minimum annual turnover £500k",
      "evidence": "string — e.g. audited accounts last 2 years",
      "threshold": "string or null"
    }}
  ],
  "references_required": {{
    "number_required": "number or null",
    "type": "string or null — e.g. previous similar contracts",
    "format": "string or null — e.g. referee contact details + description",
    "minimum_contract_value": "string or null",
    "recency_requirement": "string or null — e.g. within last 3 years"
  }},
  "social_value_requirements": {{
    "required": "boolean",
    "weighting": "string or null",
    "themes": ["string array — e.g. local employment, carbon reduction"],
    "reporting_required": "boolean"
  }},
  "tupe_applies": "boolean or null — Transfer of Undertakings",
  "special_conditions": ["string array — any unusual or notable conditions"],
  "ambiguities_detected": [
    {{
      "section": "string — where in the document",
      "issue": "string — what is unclear or contradictory"
    }}
  ]
}}"""

    STAGE_2_SYSTEM = """You are a bid manager preparing a submission checklist for a UK/Ireland 
public sector tender response. You work from a structured tender dissection and produce an 
exhaustive, actionable checklist.

Every item must have a clear action, a status flag, and where relevant a reference to the 
clause or section in the tender that requires it. 

You NEVER add items not supported by the tender dissection. 
You NEVER omit mandatory items.
Return ONLY valid JSON. No preamble, no explanation, no markdown fences."""

    STAGE_2_USER = """Using the tender dissection below, produce a complete submission checklist.

TENDER DISSECTION (Stage 1 output):
================
{stage1_json}
================

COMPANY NAME: {company_name}

Return ONLY this JSON structure:

{{
  "checklist_summary": {{
    "total_items": "number",
    "mandatory_items": "number",
    "items_needing_signature": "number",
    "items_needing_placeholder": "number",
    "estimated_effort": "string — e.g. High / Medium / Low"
  }},
  "checklist_items": [
    {{
      "id": "number — sequential 1,2,3...",
      "category": "string — one of: FORM | CERTIFICATE | INSURANCE | FINANCIAL | REFERENCE | POLICY | TECHNICAL | DECLARATION | OTHER",
      "item_name": "string — clear plain-English name",
      "description": "string — what it is and why it is needed",
      "status_flag": "string — one of: MANDATORY | SCORED | DESIRABLE | SIGN_REQUIRED | PLACEHOLDER_NEEDED",
      "action_required": "string — specific instruction e.g. 'Obtain from certifying body' / 'Sign and date' / 'Insert company-specific content'",
      "tender_reference": "string or null — clause/section/appendix reference",
      "can_ai_draft": "boolean — true if AI can draft a placeholder, false if must come from the bidder",
      "placeholder_label": "string or null — e.g. [INSERT: ISO 9001 Certificate Number] — only populate if can_ai_draft is false",
      "notes": "string or null — e.g. must be signed by a Director; must cover minimum £5m"
    }}
  ],
  "placeholder_summary": [
    {{
      "item_id": "number",
      "item_name": "string",
      "what_is_needed": "string — precise description of what the bidder must supply",
      "urgency": "string — HIGH / MEDIUM / LOW based on whether it blocks submission"
    }}
  ],
  "recommended_submission_order": ["string array — ordered list of item names in recommended submission sequence"]
}}"""


    STAGE_3_SYSTEM = """You are a professional bid writer with deep expertise in UK and Irish public 
sector procurement. You have written hundreds of winning tender responses for contracts ranging 
from £50,000 to £50,000,000 across central government, local authorities, NHS, and education.

Your writing style is:
- Formal, confident, and evidence-focused
- Structured with clear headers and logical flow
- Buyer-centric: mirror the buyer's language and priorities back to them
- Specific: reference exact evaluation criteria, weightings, and ITT question numbers
- Compliant: answer every numbered ITT question directly and completely

CRITICAL GROUND TRUTH & COMPLIANCE RULES YOU MUST FOLLOW STRICTLY:
1. FACTUAL GROUND TRUTH RULE: State a specific factual claim (a case study, a certification, a completed/signed document, a technical capability, or programming language/tool expertise) ONLY IF that exact fact is explicitly present in the provided DATA / ANSWER BANK / DOCUMENTS / CHECKLIST.
2. MISSING FACT PLACEHOLDERS: If a specific fact is NOT present in the data (e.g. no case study found in Answer Bank/Profile for this sector), you MUST write a clearly visible placeholder tag instead: '[Add 1–2 case studies here — none found in Answer Bank]'. NEVER write prose asserting an unverified fact or invented client name, metric, or past project.
3. CHECKLIST COMPLIANCE ALIGNMENT: For compliance items (Appendix forms, signatures, insurance, policies): you may state 'completed' or 'signed' ONLY IF the Checklist state explicitly confirms it is signed/completed. Otherwise you MUST state 'in progress' / 'being finalised'.
4. CLARIFICATION ACKNOWLEDGMENT RULE: Where a Critical or High priority clarification finding affects a claim (budget caps, delivery timeline, or scope boundaries), acknowledge the issue honestly rather than asserting contradictory promises. Follow this pattern: "A point we need clarified before pricing: [issue]. We have raised this in our clarification questions and will price to estimated requirements, subject to your response."
5. GREETING RULE: Address the Cover Letter to 'Dear [Authority/Department Name] Procurement Team,' when the authority or department name is known. Fall back to 'Dear Sir/Madam,' ONLY if no authority or department name is present.
6. Answer EVERY ITT question extracted in the dissection — do not skip any.
7. Pre-approved Answer Bank entries MUST appear verbatim — do not paraphrase or summarise them.

Return ONLY valid JSON. No preamble, no markdown fences."""

    STAGE_3_USER = """Write a complete, professional tender response for the company below, 
responding to this tender opportunity.

COMPANY NAME: {company_name}
COMPANY STRUCTURED DATA (sector, turnover, geography, certifications — use factually, do not invent):
{company_structured_data}

ADDITIONAL COMPANY CONTEXT (from user uploads, if any): 
{company_context}

PRE-APPROVED ANSWER BANK (use these verbatim where relevant — do NOT paraphrase):
{answer_bank_context}

CHECKLIST COMPLIANCE STATUS (Stage 2 output):
{checklist_status_summary}

CRITICAL CLARIFICATION FINDINGS (Stage 4 output):
{critical_clarifications_summary}

TENDER DISSECTION (Stage 1 output):
================
{stage1_json}
================

Write the full bid response. For each ITT question listed in the dissection, write a full answer.
Where the Answer Bank contains a relevant answer, COPY IT VERBATIM into that section.
Respect word limits strictly. Use [Add 1-2 case studies here...] or [PLACEHOLDER: what is needed] for anything requiring 
company-specific information not covered by the Answer Bank.

Return ONLY this JSON structure:

{{
  "document_title": "string — e.g. Tender Response: [Tender Title] | [Company Name]",
  "cover_letter": {{
    "content": "string — full cover letter text, 400-500 words, addressed to the contracting authority",
    "word_count": "number"
  }},
  "executive_summary": {{
    "content": "string — 500-700 words. Why this company, why now, key differentiators referenced to their evaluation criteria",
    "word_count": "number"
  }},
  "understanding_of_requirements": {{
    "content": "string — 600-900 words. Demonstrate deep understanding of their scope, challenges, and objectives. Mirror their language.",
    "word_count": "number"
  }},
  "itt_question_responses": [
    {{
      "question_number": "string — matches number from Stage 1 dissection",
      "question_text": "string — verbatim question",
      "word_limit": "number or null",
      "response": "string — full answer to this specific question",
      "word_count": "number",
      "placeholders_used": ["string array — list any [PLACEHOLDER:...] tags used in this response"]
    }}
  ],
  "technical_methodology": {{
    "content": "string — detailed approach/methodology section, 800-1200 words, structured with sub-headings per deliverable or phase",
    "word_count": "number"
  }},
  "relevant_experience": {{
    "content": "string — experience section with [Add 1–2 case studies here — none found in Answer Bank] markers if no real case studies exist",
    "word_count": "number",
    "case_study_placeholders": [
      {{
        "label": "string — e.g. Case Study 1",
        "what_to_include": "string — precise guidance on what the bidder should insert"
      }}
    ]
  }},
  "team_and_personnel": {{
    "content": "string — team section with [PLACEHOLDER: Insert CV / bio for key roles] markers",
    "word_count": "number",
    "personnel_placeholders": ["string array — list of roles needing CVs/bios"]
  }},
  "quality_and_compliance": {{
    "content": "string — quality management, processes, accreditations. Reference their specific requirements.",
    "word_count": "number"
  }},
  "social_value": {{
    "content": "string — social value commitments aligned to their stated themes and weighting. Be specific with measurable commitments. Use [PLACEHOLDER] for specific numbers.",
    "themes_addressed": ["string array — which social value themes are covered"],
    "word_count": "number"
  }},
  "pricing_notes": {{
    "content": "string — pricing narrative (not the numbers themselves — those are [PLACEHOLDER]). Explain pricing approach, transparency, value for money.",
    "pricing_table_placeholder": "string — instruction e.g. [PLACEHOLDER: Insert completed Pricing Schedule per Appendix B of ITT]"
  }},
  "declarations": {{
    "content": "string — standard declarations paragraph confirming compliance, no conflicts of interest, authority to submit",
    "forms_referenced": ["string array — list any forms from the checklist that must accompany this section"]
  }},
  "all_placeholders": [
    {{
      "location": "string — which section this placeholder appears in",
      "placeholder_text": "string — the exact [PLACEHOLDER: ...] tag",
      "what_bidder_must_provide": "string — clear instruction"
    }}
  ]
}}"""

    STAGE_4_SYSTEM = """You are an experienced bid consultant advising a supplier on what clarification 
questions to submit to the contracting authority before the tender deadline.

Good clarification questions:
- Target genuine ambiguities that could affect the bid strategy or scoring
- Ask about scoring methodology where the ITT is vague
- Confirm scope boundaries to avoid under or over-promising
- Surface contradictions between documents
- Do NOT ask questions whose answers are already clearly stated in the ITT
- Do NOT ask questions that reveal bid strategy or look unprepared

Return ONLY valid JSON. No preamble, no markdown fences."""

    STAGE_4_USER = """Based on the tender dissection below, generate the most strategically valuable 
clarification questions this bidder should submit before the clarification deadline.

TENDER DISSECTION:
================
{stage1_json}
================

Return ONLY this JSON structure:

{{
  "clarification_deadline": "string — from dissection, or 'NOT STATED — confirm urgently'",
  "submission_method": "string — how to submit clarifications per the ITT",
  "total_questions": "number",
  "questions": [
    {{
      "id": "number — sequential",
      "priority": "string — CRITICAL / HIGH / MEDIUM",
      "category": "string — Scoring / Scope / Commercial / Technical / Process / Legal",
      "question": "string — professionally worded, ready to copy-paste to the buyer",
      "reason_for_asking": "string — internal note explaining why this matters strategically",
      "ambiguity_source": "string — which section/clause prompted this question",
      "impact_if_not_clarified": "string — what risk this ambiguity creates for the bid"
    }}
  ],
  "strategic_notes": "string — 2-3 sentences of overall advice on the clarification strategy for this tender"
}}"""

    def call_deepseek_stage(system: str, user: str, api_key: str, max_tokens: int = 4000,
                             _provider: str = None, _key: str = None, _model: str = None) -> dict:
        import json
        import re

        if _provider and _key:
            # Pre-captured credentials (avoids request context issues in streaming generators)
            provider, custom_key, model = _provider, _key, _model
        else:
            provider, custom_key, model = get_ai_credentials()
        final_key = custom_key if (provider != "deepseek" or custom_key) else api_key

        try:
            raw = call_chat_api(system=system, user=user, max_tokens=max_tokens, temperature=0.3, response_format_json=True, provider=provider, api_key=final_key, model=model)
        except Exception as err:
            raise RuntimeError(str(err)) from None

        heal_and_parse_json = parse_llm_json  # shared implementation (tender_app/llm_json.py)

        try:
            return heal_and_parse_json(raw)
        except Exception as parse_err:
            print("ERROR: Failed to parse or heal DeepSeek response JSON.")
            print("RAW RESPONSE SNIPPET:")
            print(raw[:1000] + "\n..." if len(raw) > 1000 else raw)
            raise parse_err

    def summarise_single_doc(name: str, content: str, api_key: str,
                             _provider: str = None, _key: str = None, _model: str = None) -> str:
        system = "You are a professional bid analyst helper. Summarise the provided tender document."
        user = f"""Please read the tender document '{name}' and write a highly detailed summary. 
        Retain all key requirements, evaluation criteria, deadlines, portals, forms to sign, insurances, financial thresholds, certificates required (like ISO, accreditations), and specific questions asked by the buyer.
        Do not lose specific details, names, numbers, clauses, or sections. Do not assume or invent.
        
        DOCUMENT CONTENT:
        {content[:150000]}
        """
        # Same provider / BYO-key handling as call_deepseek_stage (this runs inside a streaming
        # generator, so the route passes in credentials it captured while it still had a request).
        if _provider and _key:
            provider, custom_key, model = _provider, _key, _model
        else:
            provider, custom_key, model = get_ai_credentials()
        final_key = custom_key if (provider != "deepseek" or custom_key) else api_key
        return call_chat_api(system=system, user=user, max_tokens=2000, temperature=0.2,
                             provider=provider, api_key=final_key, model=model).strip()

    def smart_chunk(text: str, max_chars: int = 180000) -> str:
        if len(text) <= max_chars:
            return text
        truncated = text[:max_chars]
        return (
            truncated
            + "\n\n[SYSTEM NOTE: Document text was truncated at the context limit. "
            "The above represents the first portion of the tender documents. "
            "If critical requirements appear near the end of documents, consider "
            "splitting into multiple analysis passes.]"
        )

    def run_bid_pipeline(
        tender_text: str,
        company_name: str,
        company_context: str,
        api_key: str,
        answer_bank_context: str = "",
        _provider: str = None,
        _key: str = None,
        _model: str = None,
    ) -> dict:
        import json
        stage1_result = call_deepseek_stage(
            system=STAGE_1_SYSTEM,
            user=STAGE_1_USER.format(tender_text=smart_chunk(tender_text)),
            api_key=api_key,
            max_tokens=4000,
            _provider=_provider, _key=_key, _model=_model
        )
        stage1_json_str = json.dumps(stage1_result, indent=2)

        stage2_result = call_deepseek_stage(
            system=STAGE_2_SYSTEM,
            user=STAGE_2_USER.format(stage1_json=stage1_json_str, company_name=company_name),
            api_key=api_key,
            max_tokens=3000,
            _provider=_provider, _key=_key, _model=_model
        )

        stage3_result = call_deepseek_stage(
            system=STAGE_3_SYSTEM,
            user=STAGE_3_USER.format(
                company_name=company_name,
                company_structured_data="No structured company data provided.",
                company_context=smart_chunk(company_context, max_chars=20000) if company_context else "No additional company context provided.",
                answer_bank_context=answer_bank_context if answer_bank_context else "No pre-approved answers provided.",
                stage1_json=stage1_json_str
            ),
            api_key=api_key,
            max_tokens=4000,
            _provider=_provider, _key=_key, _model=_model
        )

        stage4_result = call_deepseek_stage(
            system=STAGE_4_SYSTEM,
            user=STAGE_4_USER.format(stage1_json=stage1_json_str),
            api_key=api_key,
            max_tokens=2000,
            _provider=_provider, _key=_key, _model=_model
        )

        return {
            "dissection":       stage1_result,
            "checklist":        stage2_result,
            "bid_response":     stage3_result,
            "clarifications":   stage4_result,
        }

    @app.post("/api/proposal")
    @require_credits(CREDIT_COST_PROPOSAL, "proposal", get_db_connection)
    def api_proposal():
        """Analyze tender details/documents to build a required document checklist and covering letter using DeepSeek."""
        import base64

        body = request.get_json(silent=True) or {}
        tender = body.get("tender") or {}
        company = (body.get("company") or "").strip()
        uploaded_files = body.get("files") or []
        company_files = body.get("company_files") or []

        title      = tender.get("title", "Unknown tender")
        description= tender.get("description", "")
        value      = tender.get("estimated_value_eur", "")
        procedure  = tender.get("procedure", "")
        proc_type  = tender.get("procurement_type", "")
        deadline   = tender.get("submission_deadline", "")
        duration   = tender.get("contract_duration_in_months_or_years_including_any_options_and_renewals", "")
        authority  = tender.get("contracting_authority", "")
        cpv        = tender.get("cpv_codes", "")
        source     = tender.get("source", "")
        resource_id= tender.get("resource_id", "")
        detail_url = tender.get("detail_url", "")

        username = session.get("username", "admin")
        profile_id = body.get("profile_id")

        # Fetch meta_json for the selected profile to inject into Stage 3
        company_structured_data = "No structured company data available."
        if profile_id:
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT meta_json FROM company_profiles WHERE id = %s AND username = %s",
                    (int(profile_id), username)
                )
                row = cursor.fetchone()
                if row and row[0]:
                    meta_json_str = row[0]
                    try:
                        parsed_meta = json.loads(meta_json_str)
                        if parsed_meta:
                            formatted_parts = []
                            for k, v in parsed_meta.items():
                                if v:
                                    formatted_parts.append(f"{k.replace('_', ' ').title()}: {v}")
                            if formatted_parts:
                                company_structured_data = "\n".join(formatted_parts)
                            else:
                                company_structured_data = meta_json_str
                    except Exception:
                        company_structured_data = meta_json_str
                cursor.close()
                conn.close()
            except Exception as e:
                print("Failed to load company profile meta_json for proposal:", e)

        # Load user documents by ID and append to company_files
        user_document_ids = body.get("user_document_ids") or []
        if user_document_ids:
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                for doc_id in user_document_ids:
                    if profile_id:
                        cursor.execute(
                            "SELECT filename, file_path FROM company_profile_documents WHERE id = %s AND profile_id = %s AND username = %s",
                            (doc_id, int(profile_id), username)
                        )
                    else:
                        cursor.execute(
                            "SELECT filename, file_path FROM user_documents WHERE id = %s AND username = %s",
                            (doc_id, username)
                        )
                    row = cursor.fetchone()
                    if row:
                        fname, file_path = row
                        if file_path and os.path.exists(file_path):
                            with open(file_path, "rb") as f_in:
                                file_data = f_in.read()
                                if not any(cf.get("filename") == fname for cf in company_files):
                                    company_files.append({
                                        "filename": fname,
                                        "content": base64.b64encode(file_data).decode("utf-8")
                                    })
                cursor.close()
                conn.close()
            except Exception as e:
                print("Failed to load user documents for proposal:", e)

        # Previous winning bids selected in the Bid Assistant (style/structure reference only)
        winning_bids = []
        raw_wb_ids = body.get("winning_bid_ids") or []
        wb_ids = []
        for _wid in raw_wb_ids[:5]:
            try:
                wb_ids.append(int(_wid))
            except (TypeError, ValueError):
                pass
        if wb_ids:
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id, title, buyer, contract_year, extracted_text FROM winning_bids "
                    "WHERE id = ANY(%s) AND username = %s ORDER BY id",
                    (wb_ids, username)
                )
                for r in cursor.fetchall():
                    winning_bids.append({
                        "id": r[0], "title": r[1], "buyer": r[2] or "",
                        "year": r[3] or "", "text": r[4] or "",
                    })
                cursor.close()
                conn.close()
            except Exception as e:
                print("Failed to load winning bids for proposal:", e)

        def winning_bids_block(max_chars: int) -> str:
            """Reference block for a writer prompt; empty string when no bids were selected."""
            if not winning_bids:
                return ""
            per_bid = max(1500, max_chars // len(winning_bids))
            parts = []
            for i, wb in enumerate(winning_bids, 1):
                meta = " | ".join(x for x in (wb["buyer"], wb["year"]) if x)
                header = f"[Winning Bid {i}: {wb['title']}{' (' + meta + ')' if meta else ''}]"
                text = wb["text"]
                if len(text) > per_bid:
                    text = text[:per_bid] + "\n[...truncated]"
                parts.append(f"{header}\n{text}")
            return (
                "\n\nPAST WINNING BIDS (reference for structure, tone, depth and win themes ONLY):\n"
                + "\n\n".join(parts)
                + "\nWINNING BID RULE: Use the PAST WINNING BIDS only to learn how successful responses are structured, "
                "how evidence and win themes are presented, and the level of detail expected. Do NOT copy passages "
                "verbatim, and do NOT reuse client names, figures, prices, dates or claims from them unless the same fact "
                "also appears in DATA / ANSWER BANK / CHECKLIST."
            )

        lang = (body.get("lang") or request.args.get("lang", "en")).strip().lower()
        prop_type = f"proposal_{lang}" if lang != "en" else "proposal"

        # Capture AI credentials NOW while we are inside the request context.
        # The streaming generator runs outside the request context, so request.headers
        # cannot be accessed inside generate().
        _ai_provider, _ai_key, _ai_model = get_ai_credentials()

        def generate():
            try:
                # Check cache first
                files_payload = {
                    "files": uploaded_files,
                    "company_files": company_files
                }
                if winning_bids:
                    # Only added when used, so cache keys for proposals without winning bids are unchanged
                    files_payload["winning_bids"] = [
                        [wb["id"], hashlib.md5(wb["text"].encode("utf-8", "ignore")).hexdigest()[:8]]
                        for wb in winning_bids
                    ]
                files_json = json.dumps(files_payload, sort_keys=True)

                refresh = body.get("refresh", False)
                cached_proposal = None
                if refresh:
                    try:
                        conn = get_db_connection()
                        cursor = conn.cursor()
                        cursor.execute(
                            "DELETE FROM analyses WHERE username = %s AND source = %s AND resource_id = %s AND company = %s AND files_json = %s AND type = %s",
                            (username, source, resource_id, company, files_json, prop_type)
                        )
                        conn.commit()
                        cursor.close()
                        conn.close()
                    except Exception as e:
                        print("Failed to clear cached proposal:", e)
                else:
                    try:
                        conn = get_db_connection()
                        cursor = conn.cursor()
                        cursor.execute(
                            "SELECT analysis_json FROM analyses WHERE username = %s AND source = %s AND resource_id = %s AND company = %s AND files_json = %s AND type = %s",
                            (username, source, resource_id, company, files_json, prop_type)
                        )
                        cached_row = cursor.fetchone()
                        cursor.close()
                        conn.close()
                        if cached_row:
                            cached_proposal = json.loads(cached_row[0])
                            cached_proposal["company"] = company
                    except Exception as e:
                        print("Failed to query proposal cache:", e)

                if cached_proposal:
                    yield json.dumps({"status": "progress", "stage": 1, "message": "Fetching and extracting tender documents..."}) + "\n"
                    yield json.dumps({"status": "progress", "stage": 1, "message": "Analysing tender documents... (Stage 1/5)"}) + "\n"
                    yield json.dumps({"status": "progress", "stage": 2, "message": "Building submission checklist... (Stage 2/5)"}) + "\n"
                    yield json.dumps({"status": "progress", "stage": 3, "message": "Drafting bid response... (Stage 3/5, this takes ~30-60 seconds)"}) + "\n"
                    yield json.dumps({"status": "progress", "stage": 4, "message": "Generating clarification questions... (Stage 4/5)"}) + "\n"
                    
                    if "gantt" not in cached_proposal:
                        yield json.dumps({"status": "progress", "stage": 5, "message": "Extracting Gantt project timeline... (Stage 5/5)"}) + "\n"
                        try:
                            duration_str = str((cached_proposal.get("dissection") or {}).get("contract_duration", "")).lower()
                            if any(w in duration_str for w in ["year", "month", "12", "24", "36"]):
                                unit = "months"
                            else:
                                unit = "weeks"
                            max_units = 12 if unit == "months" else 16
                            methodology_text = (cached_proposal.get("bid_response") or {}).get("technical_methodology", {}).get("content", "")
                            
                            system_gantt = (
                                "You are a project planning expert. Extract a project delivery timeline from the bid methodology text. "
                                f"Use {unit} as the time unit. Return ONLY valid JSON: "
                                '{"unit":"' + unit + '","phases":[{"id":1,"label":"Phase Name","start":1,"duration":2,'
                                '"assignee":"Team Lead","colour":"#3b82f6","is_milestone":false}]}'
                                " No markdown, no extra keys."
                            )
                            user_gantt = (
                                f"Extract 5–10 project delivery phases from this technical methodology section.\n"
                                f"Tender: {title}\n"
                                f"Contract duration context: {duration_str or 'not specified'}\n"
                                f"Time unit: {unit} (max timeline: {max_units} {unit})\n\n"
                                f"METHODOLOGY TEXT:\n{methodology_text[:8000]}\n\n"
                                f"Assign sensible colours from this palette: #3b82f6 #10b981 #f59e0b #8b5cf6 #ef4444 #06b6d4 #f97316 #84cc16\n"
                                f"Return JSON only."
                            )
                            
                            gantt_result = {}
                            if methodology_text:
                                gantt_result = call_deepseek_stage(
                                    system=system_gantt,
                                    user=user_gantt,
                                    api_key=DEEPSEEK_API_KEY,
                                    max_tokens=1200,
                                    _provider=_ai_provider, _key=_ai_key, _model=_ai_model
                                )
                            cached_proposal["gantt"] = gantt_result
                            
                            # Save back to database
                            conn = get_db_connection()
                            cursor = conn.cursor()
                            ph = "%s"
                            cursor.execute(
                                f"UPDATE analyses SET analysis_json={ph} WHERE username={ph} AND source={ph} AND resource_id={ph} AND company={ph} AND type={ph}",
                                (json.dumps(cached_proposal), username, source, resource_id, company, prop_type)
                            )
                            conn.commit()
                            cursor.close()
                            conn.close()
                        except Exception as gantt_err:
                            print("Failed to auto-extract Gantt for cached proposal:", gantt_err)
                            cached_proposal["gantt"] = {"unit": "weeks", "phases": []}
                    else:
                        yield json.dumps({"status": "progress", "stage": 5, "message": "Loading saved Gantt project timeline... (Stage 5/5)"}) + "\n"
                        
                    yield json.dumps({
                        "status": "complete",
                        "ok": True,
                        "proposal": cached_proposal,
                        "docs_used": [f.get("filename") for f in uploaded_files if f.get("filename")] or [title],
                        "docs_found": len(uploaded_files),
                        "cached": True
                    }) + "\n"
                    return

                # Step 1: Fetch documents and prepare metadata
                yield json.dumps({"status": "progress", "stage": 1, "message": "Fetching and extracting tender documents..."}) + "\n"
                
                doc_text = ""
                doc_names: list[str] = []
                documents = []
                
                # Main tender details context
                documents.append({
                    "name": "Tender Details Overview",
                    "content": f"Tender Title: {title}\nDescription: {description}\nProcuring Authority: {authority}\nEstimated Value: {value}\nDeadline: {deadline}\nDuration: {duration}\nCPV Codes: {cpv}\nProcedure: {procedure} / {proc_type}"
                })

                # Auto-fetch documents from portal
                cfg = SOURCES.get(source) if source else None
                if cfg and resource_id:
                    docs_meta = list_cft_documents(cfg["base_url"], resource_id) if cfg.get("type") == "etenders" else list_non_etenders_documents(source, resource_id, detail_url)
                    for doc in docs_meta:
                        doc_url = doc["doc_id"]
                        if cfg.get("type") == "etenders":
                            text = fetch_and_extract_text(
                                cfg["base_url"], resource_id, doc["doc_id"], doc["filename"],
                                max_chars=120000,
                            )
                        else:
                            import requests
                            text = ""
                            try:
                                headers = {
                                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                                }
                                sub_resp = requests.get(doc_url, headers=headers, timeout=15)
                                if sub_resp.status_code == 200:
                                    text = _extract_bytes(sub_resp.content, doc["filename"], max_chars=120000)
                            except Exception as e:
                                print(f"Failed to auto-download document {doc_url}: {e}")
                        
                        if text.strip():
                            doc_names.append(doc["title"] or doc["filename"])
                            documents.append({
                                "name": doc["title"] or doc["filename"],
                                "content": text
                            })

                # Fallback to notice page scraping if no documents were extracted
                if not doc_names and detail_url:
                    url_text = scrape_tender_url_text(detail_url, max_chars=40000)
                    if url_text.strip():
                        doc_names.append(f"Tender Notice Page ({detail_url})")
                        documents.append({
                            "name": f"Tender Notice Page ({detail_url})",
                            "content": url_text
                        })


                # Process uploaded tender files
                for f in uploaded_files:
                    fname = f.get("filename") or "uploaded_file"
                    b64_content = f.get("content") or ""
                    if b64_content:
                        try:
                            file_data = base64.b64decode(b64_content)
                            text = _extract_bytes(file_data, fname, max_chars=120000)
                            if text.strip():
                                doc_names.append(fname)
                                documents.append({
                                    "name": fname,
                                    "content": text
                                })
                        except Exception as e:
                            documents.append({
                                "name": fname,
                                "content": f"(Failed to parse: {str(e)})"
                            })

                # Process uploaded company profile files
                company_documents = []
                for f in company_files:
                    fname = f.get("filename") or "company_doc"
                    b64_content = f.get("content") or ""
                    if b64_content:
                        try:
                            file_data = base64.b64decode(b64_content)
                            text = _extract_bytes(file_data, fname, max_chars=120000)
                            if text.strip():
                                company_documents.append({
                                    "name": fname,
                                    "content": text
                                })
                        except Exception as e:
                            company_documents.append({
                                "name": fname,
                                "content": f"(Failed to parse: {str(e)})"
                            })

                # Smart chunking - Pre-summarisation pass if combined size > 400,000 chars (~100k tokens)
                total_len = sum(len(doc["content"]) for doc in documents)
                if total_len > 400000:
                    yield json.dumps({"status": "progress", "stage": 1, "message": "Tender documents exceed context limit. Running pre-summarisation pass..."}) + "\n"
                    for idx, doc in enumerate(documents):
                        if len(doc["content"]) > 60000:
                            yield json.dumps({"status": "progress", "stage": 1, "message": f"Pre-summarising: {doc['name']}..."}) + "\n"
                            try:
                                summary = summarise_single_doc(doc["name"], doc["content"], DEEPSEEK_API_KEY, _provider=_ai_provider, _key=_ai_key, _model=_ai_model)
                                documents[idx]["content"] = f"[SUMMARISED VERSION]: {summary}"
                            except Exception as e:
                                documents[idx]["content"] = doc["content"][:60000] + "\n...[TRUNCATED due to error in summarisation]"

                # Compressing company profiles
                total_comp_len = sum(len(doc["content"]) for doc in company_documents)
                if total_comp_len > 120000:
                    yield json.dumps({"status": "progress", "stage": 1, "message": "Pre-summarising company context profiles..."}) + "\n"
                    for idx, doc in enumerate(company_documents):
                        if len(doc["content"]) > 40000:
                            try:
                                summary = summarise_single_doc(doc["name"], doc["content"], DEEPSEEK_API_KEY, _provider=_ai_provider, _key=_ai_key, _model=_ai_model)
                                company_documents[idx]["content"] = f"[SUMMARISED VERSION]: {summary}"
                            except Exception as e:
                                company_documents[idx]["content"] = doc["content"][:40000] + "\n...[TRUNCATED]"

                # Combine doc contents for pipeline
                combined_tender_text = ""
                for doc in documents:
                    combined_tender_text += f"\n\n=== Document: {doc['name']} ===\n{doc['content']}"

                company_ctx_text = ""
                for doc in company_documents:
                    company_ctx_text += f"\n\n=== Company Context Document: {doc['name']} ===\n{doc['content']}"

                lang_names = {
                    "fr": "French (Français)",
                    "de": "German (Deutsch)",
                    "nl": "Dutch (Nederlands)"
                }
                lang_instruction = ""
                if lang in lang_names:
                    lang_instruction = f"\n\nCRITICAL LANGUAGE REQUIREMENT: All content values in the JSON response MUST be written in {lang_names[lang]} instead of English. The JSON keys themselves MUST remain in English as defined in the schema."

                # 4 Chained DeepSeek calls with sequential status streaming updates
                
                # Stage 1: Tender Dissection
                yield json.dumps({"status": "progress", "stage": 1, "message": "Analysing tender documents... (Stage 1/4)"}) + "\n"
                stage1_result = call_deepseek_stage(
                    system=STAGE_1_SYSTEM,
                    user=STAGE_1_USER.format(tender_text=smart_chunk(combined_tender_text)) + lang_instruction,
                    api_key=DEEPSEEK_API_KEY,
                    max_tokens=4000,
                    _provider=_ai_provider, _key=_ai_key, _model=_ai_model
                )
                stage1_json_str = json.dumps(stage1_result, indent=2)

                # Stage 2: Document Checklist
                yield json.dumps({"status": "progress", "stage": 2, "message": "Building submission checklist... (Stage 2/5)"}) + "\n"
                stage2_result = call_deepseek_stage(
                    system=STAGE_2_SYSTEM,
                    user=STAGE_2_USER.format(stage1_json=stage1_json_str, company_name=company or "Bidding Company") + lang_instruction,
                    api_key=DEEPSEEK_API_KEY,
                    max_tokens=3000,
                    _provider=_ai_provider, _key=_ai_key, _model=_ai_model
                )

                # Stage 3: Clarification Questions (Run BEFORE proposal generation to feed findings into Stage 4 proposal writers)
                yield json.dumps({"status": "progress", "stage": 3, "message": "Generating clarification questions & analyzing strategic risks... (Stage 3/5)"}) + "\n"
                stage4_result = call_deepseek_stage(
                    system=STAGE_4_SYSTEM,
                    user=STAGE_4_USER.format(stage1_json=stage1_json_str) + lang_instruction,
                    api_key=DEEPSEEK_API_KEY,
                    max_tokens=2000,
                    _provider=_ai_provider, _key=_ai_key, _model=_ai_model
                )

                # Extract Critical / High Clarification Findings
                critical_clarifications = []
                if isinstance(stage4_result, dict) and "questions" in stage4_result and isinstance(stage4_result["questions"], list):
                    for q in stage4_result["questions"]:
                        prio = str(q.get("priority", "")).upper()
                        if prio in ("CRITICAL", "HIGH"):
                            critical_clarifications.append(f"• [{prio}] {q.get('category', 'Scope')}: {q.get('question', '')} (Impact: {q.get('strategic_reason', '')})")

                critical_clarifications_summary = "\n".join(critical_clarifications) if critical_clarifications else "No Critical or High priority clarification issues identified."

                # Build Checklist Compliance Status Summary
                checklist_items = []
                if isinstance(stage2_result, dict):
                    checklist_items = stage2_result.get("required_documents") or stage2_result.get("checklist") or []
                
                checklist_summary_parts = []
                if checklist_items and isinstance(checklist_items, list):
                    for ci in checklist_items:
                        iname = ci.get("item_name") or ci.get("document_name") or "Required Item"
                        st = str(ci.get("status") or "outstanding").lower()
                        checklist_summary_parts.append(f"• {iname}: Status = {st}")
                checklist_status_summary = "\n".join(checklist_summary_parts) if checklist_summary_parts else "All required compliance items default to 'outstanding / in progress'."

                # Fetch answer bank for this user (filtered by selected profile)
                answer_bank_ctx = ""
                try:
                    ab_conn = get_db_connection()
                    ab_cur = ab_conn.cursor()
                    ab_cur.execute(
                        "SELECT category, question, answer, file_name, file_content FROM answer_bank WHERE username=%s AND (profile_id=%s OR profile_id IS NULL) ORDER BY category, id",
                        (username, int(profile_id) if profile_id else None)
                    )
                    ab_rows = ab_cur.fetchall()
                    ab_cur.close(); ab_conn.close()
                    if ab_rows:
                        ab_rows_sorted = _filter_answer_bank_by_stage1(ab_rows, stage1_result)
                        ab_parts = []
                        for r in ab_rows_sorted:
                            entry_text = f"[{r[0]}]\nQ: {r[1]}\nA: {r[2]}"
                            fname = r[3] if len(r) > 3 and r[3] else None
                            fcontent = r[4] if len(r) > 4 and r[4] else None
                            if fname or fcontent:
                                entry_text += f"\n[Attached Document: {fname or 'Document'}]"
                                if fcontent:
                                    entry_text += f"\nDocument Content: {fcontent[:3000]}"
                            ab_parts.append(entry_text)
                        answer_bank_ctx = "\n\n".join(ab_parts)
                except Exception as e:
                    print("Failed to fetch answer bank for proposal:", e)

                # Stage 4: Multi-Agent Parallel Proposal Generation
                yield json.dumps({"status": "progress", "stage": 4, "message": "Drafting multi-agent bid proposal in parallel... (Stage 4/5)"}) + "\n"

                today_str = datetime.now().strftime("%d %B %Y")
                authority_name = authority or (stage1_result.get("contracting_authority") if isinstance(stage1_result, dict) else None) or "Contracting Authority"
                greeting_target = f"Dear {authority_name} Procurement Team," if authority_name and "unknown" not in authority_name.lower() else "Dear Sir/Madam,"

                proposal_writers = [
                    {
                        "key": "w1",
                        "name": "Executive & Win Theme Specialist",
                        "prompt": f"""You are a senior Bid Executive. Write the Cover Letter, Executive Summary, and Understanding of Requirements for this proposal.

TENDER DISSECTION:
{stage1_json_str}

COMPANY: {company or "Bidding Company"}
DATA: {company_structured_data}
CONTEXT: {smart_chunk(company_ctx_text, max_chars=10000) if company_ctx_text else 'None'}
ANSWER BANK: {smart_chunk(answer_bank_ctx, max_chars=4000) if answer_bank_ctx else 'None'}
CHECKLIST STATUS: {checklist_status_summary}
CRITICAL CLARIFICATIONS: {critical_clarifications_summary}{winning_bids_block(6000)}

CRITICAL RULES:
1. GREETING RULE: Begin the Cover Letter with '{greeting_target}'.
2. TRUTHFULNESS & GROUND TRUTH: State factual claims (case studies, certifications, completed forms) ONLY IF present in DATA/ANSWER BANK/CHECKLIST. If case study data is missing, write '[Add 1–2 case studies here — none found in Answer Bank]'.
3. CLARIFICATION ACKNOWLEDGMENT: If a Critical Clarification affects claims, acknowledge it: 'A point we need clarified before pricing: [issue]. We have raised this in our clarification questions and will price to estimated requirements, subject to your response.'

Return ONLY valid JSON with no markdown fences matching this schema:
{{
  "cover_letter": {{"content": "400-500 words formal cover letter addressed to authority", "word_count": 450}},
  "executive_summary": {{"content": "500-700 words win themes, key differentiators aligned to evaluation criteria", "word_count": 600}},
  "understanding_of_requirements": {{"content": "600-900 words deep understanding of buyer scope, challenges, and goals", "word_count": 750}}
}}
{lang_instruction}"""
                    },
                    {
                        "key": "w2",
                        "name": "Technical Solution Architect & ITT Response Writer",
                        "prompt": f"""You are a Lead Solution Architect. Write the Technical Methodology and ITT Question Responses.

TENDER DISSECTION:
{stage1_json_str}

COMPANY: {company or "Bidding Company"}
DATA: {company_structured_data}
ANSWER BANK: {smart_chunk(answer_bank_ctx, max_chars=6000) if answer_bank_ctx else 'None'}
CRITICAL CLARIFICATIONS: {critical_clarifications_summary}{winning_bids_block(9000)}

Return ONLY valid JSON with no markdown fences matching this schema:
{{
  "technical_methodology": {{"content": "800-1200 words detailed approach and methodology with sub-headings for deliverables", "word_count": 1000}},
  "itt_question_responses": [
    {{
      "question_number": "Q1",
      "question_text": "verbatim ITT question",
      "word_limit": null,
      "response": "full comprehensive answer to question",
      "word_count": 300,
      "placeholders_used": []
    }}
  ]
}}
{lang_instruction}"""
                    },
                    {
                        "key": "w3",
                        "name": "Quality, Security & Social Value Lead",
                        "prompt": f"""You are a Head of Quality, Security & Compliance. Write the Quality Assurance, Social Value, and Declarations sections.

TENDER DISSECTION:
{stage1_json_str}

COMPANY: {company or "Bidding Company"}
DATA: {company_structured_data}
CHECKLIST STATUS: {checklist_status_summary}{winning_bids_block(4000)}

RULE: State compliance forms/declarations as 'completed' or 'signed' ONLY IF confirmed in CHECKLIST STATUS. Otherwise state 'in progress' / 'being finalised'.

Return ONLY valid JSON with no markdown fences matching this schema:
{{
  "quality_and_compliance": {{"content": "quality management, ISO 9001/27001, audit processes, Cyber Essentials", "word_count": 500}},
  "social_value": {{"content": "social value commitments, sustainability, local employment, diversity", "themes_addressed": ["Local Employment", "Environmental Sustainability"], "word_count": 450}},
  "declarations": {{"content": "formal compliance declaration and conflict of interest statement", "forms_referenced": []}}
}}
{lang_instruction}"""
                    },
                    {
                        "key": "w4",
                        "name": "Delivery, Experience & Pricing Consultant",
                        "prompt": f"""You are a Commercial & Delivery Director. Write the Relevant Experience, Team & Personnel, and Pricing Narrative sections.

TENDER DISSECTION:
{stage1_json_str}

COMPANY: {company or "Bidding Company"}
DATA: {company_structured_data}
ANSWER BANK: {smart_chunk(answer_bank_ctx, max_chars=4000) if answer_bank_ctx else 'None'}
CRITICAL CLARIFICATIONS: {critical_clarifications_summary}{winning_bids_block(6000)}

RULE: If no past case studies exist in DATA/ANSWER BANK, output '[Add 1–2 case studies here — none found in Answer Bank]'. Do NOT fabricate false case studies or unverified project metrics.

Return ONLY valid JSON with no markdown fences matching this schema:
{{
  "relevant_experience": {{"content": "experience section narrative with case study placeholders", "word_count": 500, "case_study_placeholders": [{{"label": "Case Study 1", "what_to_include": "Insert past project details"}}]}},
  "team_and_personnel": {{"content": "team governance structure and key personnel narrative", "word_count": 400, "personnel_placeholders": ["Project Manager", "Solution Architect"]}},
  "pricing_notes": {{"content": "pricing narrative, transparency, value for money approach", "pricing_table_placeholder": "[PLACEHOLDER: Insert Pricing Schedule]"}}
}}
{lang_instruction}"""
                    }
                ]

                def _run_writer_agent(w):
                    try:
                        res_str = call_deepseek_stage(
                            system="You are a senior proposal writer. Return ONLY valid JSON.",
                            user=w["prompt"],
                            api_key=DEEPSEEK_API_KEY,
                            max_tokens=3000,
                            _provider=_ai_provider, _key=_ai_key, _model=_ai_model
                        )
                        if isinstance(res_str, dict):
                            return w["key"], res_str
                        return w["key"], _parse_json_robust(str(res_str))
                    except Exception as w_err:
                        print(f"Writer {w['name']} error: {w_err}")
                        return w["key"], {}

                writer_results = {}
                with ThreadPoolExecutor(max_workers=len(proposal_writers)) as executor:
                    futures = [executor.submit(_run_writer_agent, w) for w in proposal_writers]
                    for future in as_completed(futures):
                        k, res_obj = future.result()
                        writer_results[k] = res_obj

                w1 = writer_results.get("w1", {})
                w2 = writer_results.get("w2", {})
                w3 = writer_results.get("w3", {})
                w4 = writer_results.get("w4", {})

                stage3_result = {
                    "document_title": f"Tender Response: {title} | {company or 'Bidding Company'}",
                    "cover_letter": w1.get("cover_letter") or {"content": f"{greeting_target}\n\nWe are pleased to submit our tender response for '{title}'.", "word_count": 150},
                    "executive_summary": w1.get("executive_summary") or {"content": f"Executive summary for {company or 'our company'} responding to {title}.", "word_count": 200},
                    "understanding_of_requirements": w1.get("understanding_of_requirements") or {"content": f"Detailed understanding of requirements for {title}.", "word_count": 250},
                    "technical_methodology": w2.get("technical_methodology") or {"content": f"Technical delivery methodology for {title}.", "word_count": 300},
                    "itt_question_responses": w2.get("itt_question_responses") or [],
                    "quality_and_compliance": w3.get("quality_and_compliance") or {"content": "Quality assurance and compliance processes.", "word_count": 200},
                    "social_value": w3.get("social_value") or {"content": "Social value commitments and environmental policy.", "themes_addressed": ["Sustainability"], "word_count": 200},
                    "declarations": w3.get("declarations") or {"content": "We confirm full compliance with all tender terms.", "forms_referenced": []},
                    "relevant_experience": w4.get("relevant_experience") or {"content": "Relevant case study experience.", "word_count": 200, "case_study_placeholders": []},
                    "team_and_personnel": w4.get("team_and_personnel") or {"content": "Delivery team structure.", "word_count": 200, "personnel_placeholders": []},
                    "pricing_notes": w4.get("pricing_notes") or {"content": "Pricing schedule narrative.", "pricing_table_placeholder": "[PLACEHOLDER: Insert Pricing Schedule]"},
                    "all_placeholders": []
                }
                # Secondary Defense-in-Depth Scan for unverified claims
                ground_truth_combined = f"{company_structured_data}\n{answer_bank_ctx}\n{company_ctx_text}"
                stage3_result = verify_and_clean_bid_claims(stage3_result, ground_truth_combined, checklist_items)

                # Stage 5: Gantt timeline extraction
                yield json.dumps({"status": "progress", "stage": 5, "message": "Extracting Gantt project timeline... (Stage 5/5)"}) + "\n"
                duration_str = str(stage1_result.get("contract_duration", "")).lower()
                if any(w in duration_str for w in ["year", "month", "12", "24", "36"]):
                    unit = "months"
                else:
                    unit = "weeks"
                max_units = 12 if unit == "months" else 16
                methodology_text = (stage3_result.get("technical_methodology") or {}).get("content", "")
                scope_text = (stage1_result.get("scope_of_work") or {}).get("full_scope", "") or \
                             (stage1_result.get("scope_of_work") or {}).get("summary", "")
                deliverables = stage1_result.get("scope_of_work", {}).get("key_deliverables", [])

                # Combine all available context for phase extraction
                gantt_context = methodology_text or scope_text or title
                if deliverables:
                    gantt_context += "\n\nKey deliverables: " + "; ".join(str(d) for d in deliverables[:10])

                system_gantt = (
                    "You are a project planning expert. Extract a project delivery timeline. "
                    f"Use {unit} as the time unit (or Quarters Q1-Q12 for long contracts). Return ONLY valid JSON: "
                    '{"unit":"' + unit + '","phases":[{"id":1,"label":"Phase Name","start":1,"duration":2,'
                    '"assignee":"Team Lead","colour":"#3b82f6","is_milestone":false}]}'
                    " Rules: return 5-8 phases. CRITICAL: Never exceed 12 total time units (e.g. max 12 months/quarters) to keep schedule tables compact and readable. No markdown, no extra keys."
                )
                user_gantt = (
                    f"Extract 5–8 project delivery phases for this tender.\n"
                    f"Tender title: {title}\n"
                    f"Contract duration: {duration_str or 'not specified'}\n"
                    f"Time unit: {unit} (max {max_units} {unit})\n\n"
                    f"CONTEXT:\n{gantt_context[:6000]}\n\n"
                    f"Use this colour palette: #3b82f6 #10b981 #f59e0b #8b5cf6 #ef4444 #06b6d4 #f97316 #84cc16\n"
                    f"Even if context is limited, return a realistic delivery timeline inferred from the tender topic. Return JSON only."
                )

                gantt_result = {}
                try:
                    gantt_result = call_deepseek_stage(
                        system=system_gantt,
                        user=user_gantt,
                        api_key=DEEPSEEK_API_KEY,
                        max_tokens=1200,
                        _provider=_ai_provider, _key=_ai_key, _model=_ai_model
                    )
                    if not gantt_result.get("phases"):
                        gantt_result = {"unit": unit, "phases": []}
                except Exception as gantt_err:
                    print("Stage 5 Gantt extraction failed:", gantt_err)
                    gantt_result = {"unit": unit, "phases": []}

                # Complete payload
                proposal_data = {
                    "company": company,
                    "dissection": stage1_result,
                    "checklist": stage2_result,
                    "bid_response": stage3_result,
                    "clarifications": stage4_result,
                    "gantt": gantt_result
                }

                # Save to database cache
                try:
                    conn = get_db_connection()
                    cursor = conn.cursor()
                    cursor.execute(
                        "INSERT INTO analyses (username, source, resource_id, tender_title, company, files_json, type, analysis_json) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                        (username, source, resource_id, title, company, files_json, prop_type, json.dumps(proposal_data))
                    )
                    conn.commit()
                    cursor.close()
                    conn.close()
                except Exception as db_err:
                    print("Failed to save proposal to history:", db_err)

                yield json.dumps({
                    "status": "complete",
                    "ok": True,
                    "proposal": proposal_data,
                    "docs_used": doc_names,
                    "docs_found": len(doc_names)
                }) + "\n"

            except Exception as e:
                import traceback
                print("ERROR in bid pipeline:")
                traceback.print_exc()
                yield json.dumps({"status": "error", "error": str(e)}) + "\n"

        from flask import stream_with_context
        return Response(stream_with_context(generate()), mimetype="application/x-ndjson")

    @app.post("/api/proposals/save")
    def api_save_proposal():
        username = session.get("username", "admin")
        body = request.get_json(silent=True) or {}
        tender_key = body.get("tender_key") or body.get("tender_id") or ""
        proposal_data = body.get("proposal") or body.get("proposal_data")
        if not tender_key or not proposal_data:
            return jsonify({"ok": False, "error": "Missing tender_key or proposal data"}), 400
        
        proposal_json = json.dumps(proposal_data) if not isinstance(proposal_data, str) else proposal_data
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO generated_proposals (username, tender_key, proposal_json, updated_at)
                VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (username, tender_key) 
                DO UPDATE SET proposal_json = EXCLUDED.proposal_json, updated_at = CURRENT_TIMESTAMP
                """,
                (username, tender_key, proposal_json)
            )
            conn.commit()
            conn.close()
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.get("/api/proposals/latest")
    def api_get_latest_proposal():
        username = session.get("username", "admin")
        tender_key = request.args.get("tender_key", "").strip()
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            if tender_key:
                cursor.execute(
                    "SELECT tender_key, proposal_json, updated_at FROM generated_proposals WHERE username = %s AND tender_key = %s",
                    (username, tender_key)
                )
            else:
                cursor.execute(
                    "SELECT tender_key, proposal_json, updated_at FROM generated_proposals WHERE username = %s ORDER BY updated_at DESC LIMIT 1",
                    (username,)
                )
            row = cursor.fetchone()
            conn.close()
            if row:
                t_key, p_json, u_at = row[0], row[1], row[2]
                try:
                    p_data = json.loads(p_json)
                except Exception:
                    p_data = p_json
                return jsonify({
                    "ok": True,
                    "tender_key": t_key,
                    "proposal": p_data,
                    "updated_at": str(u_at) if u_at else None
                })
            else:
                return jsonify({"ok": True, "proposal": None})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.post("/api/download-covering-letter")
    @require_credits(CREDIT_COST_AI_DOWNLOAD, "download_covering_letter", get_db_connection)
    def api_download_covering_letter():
        import zipfile
        from datetime import datetime

        body = request.get_json(silent=True) or {}
        title = body.get("title") or "Unknown Tender"
        authority = body.get("authority") or "Unknown Authority"
        covering_letter = body.get("covering_letter") or ""
        checklist = body.get("checklist") or []

        def xml_escape(text: Any) -> str:
            s = str(text or "")
            return (
                s.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
                .replace("'", "&apos;")
            )

        title_esc = xml_escape(title)
        auth_esc = xml_escape(authority)
        date_str = xml_escape(datetime.now().strftime("%d/%m/%Y"))

        xml_content = []
        xml_content.append('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>')
        xml_content.append('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">')
        xml_content.append('  <w:body>')

        # Document Header
        xml_content.append('    <w:p>')
        xml_content.append('      <w:pPr><w:spacing w:after="240"/></w:pPr>')
        xml_content.append('      <w:r>')
        xml_content.append('        <w:rPr>')
        xml_content.append('          <w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/>')
        xml_content.append('          <w:b/>')
        xml_content.append('          <w:sz w:val="36"/>')
        xml_content.append('          <w:color w:val="1E3A8A"/>')
        xml_content.append('        </w:rPr>')
        xml_content.append('        <w:t>Tender Bid Submission Covering Letter</w:t>')
        xml_content.append('      </w:r>')
        xml_content.append('    </w:p>')

        # Meta Info
        for label, val in [("Tender:", title_esc), ("Authority:", auth_esc), ("Date:", date_str)]:
            xml_content.append('    <w:p>')
            xml_content.append('      <w:pPr><w:spacing w:after="60"/></w:pPr>')
            xml_content.append('      <w:r>')
            xml_content.append('        <w:rPr>')
            xml_content.append('          <w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/>')
            xml_content.append('          <w:b/>')
            xml_content.append('          <w:sz w:val="20"/>')
            xml_content.append('        </w:rPr>')
            xml_content.append(f'        <w:t>{label} </w:t>')
            xml_content.append('      </w:r>')
            xml_content.append('      <w:r>')
            xml_content.append('        <w:rPr>')
            xml_content.append('          <w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/>')
            xml_content.append('          <w:sz w:val="20"/>')
            xml_content.append('        </w:rPr>')
            xml_content.append(f'        <w:t>{val}</w:t>')
            xml_content.append('      </w:r>')
            xml_content.append('    </w:p>')

        # Divider
        xml_content.append('    <w:p>')
        xml_content.append('      <w:pPr>')
        xml_content.append('        <w:spacing w:after="360"/>')
        xml_content.append('        <w:pBdr>')
        xml_content.append('          <w:bottom w:val="single" w:sz="12" w:space="4" w:color="3B82F6"/>')
        xml_content.append('        </w:pBdr>')
        xml_content.append('      </w:pPr>')
        xml_content.append('    </w:p>')

        # Covering Letter paragraphs
        paragraphs = covering_letter.split("\n")
        for p_text in paragraphs:
            trimmed = p_text.strip()
            if not trimmed:
                xml_content.append('    <w:p><w:pPr><w:spacing w:after="120"/></w:pPr></w:p>')
                continue

            xml_content.append('    <w:p>')
            xml_content.append('      <w:pPr><w:spacing w:after="120"/></w:pPr>')
            xml_content.append('      <w:r>')
            xml_content.append('        <w:rPr>')
            xml_content.append('          <w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/>')
            xml_content.append('          <w:sz w:val="22"/>') # 11pt
            xml_content.append('        </w:rPr>')
            xml_content.append(f'        <w:t>{xml_escape(trimmed)}</w:t>')
            xml_content.append('      </w:r>')
            xml_content.append('    </w:p>')

        # Section: Required Documents Checklist
        if checklist:
            xml_content.append('    <w:p>')
            xml_content.append('      <w:pPr><w:spacing w:before="360" w:after="120"/></w:pPr>')
            xml_content.append('      <w:r>')
            xml_content.append('        <w:rPr>')
            xml_content.append('          <w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/>')
            xml_content.append('          <w:b/>')
            xml_content.append('          <w:sz w:val="26"/>')
            xml_content.append('          <w:color w:val="1E3A8A"/>')
            xml_content.append('        </w:rPr>')
            xml_content.append('        <w:t>Enclosed Documents Checklist</w:t>')
            xml_content.append('      </w:r>')
            xml_content.append('    </w:p>')

            # Table for Checklist
            xml_content.append('    <w:tbl>')
            xml_content.append('      <w:tblPr>')
            xml_content.append('        <w:tblStyle w:val="TableGrid"/>')
            xml_content.append('        <w:tblW w:w="5000" w:type="pct"/>')
            xml_content.append('        <w:tblBorders>')
            xml_content.append('          <w:top w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>')
            xml_content.append('          <w:left w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>')
            xml_content.append('          <w:bottom w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>')
            xml_content.append('          <w:right w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>')
            xml_content.append('          <w:insideH w:val="single" w:sz="4" w:space="0" w:color="E2E8F0"/>')
            xml_content.append('          <w:insideV w:val="single" w:sz="4" w:space="0" w:color="E2E8F0"/>')
            xml_content.append('        </w:tblBorders>')
            xml_content.append('      </w:tblPr>')

            # Header Row
            xml_content.append('      <w:tr>')
            for th_name, w_val in [("No.", "500"), ("Document Name", "2000"), ("Reference", "1000"), ("Status", "1500")]:
                xml_content.append('        <w:tc>')
                xml_content.append(f'          <w:tcPr><w:tcW w:w="{w_val}" w:type="dxa"/><w:shd w:fill="F1F5F9"/></w:tcPr>')
                xml_content.append('          <w:p>')
                xml_content.append('            <w:pPr><w:spacing w:before="60" w:after="60"/></w:pPr>')
                xml_content.append('            <w:r>')
                xml_content.append('              <w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:b/><w:sz w:val="20"/></w:rPr>')
                xml_content.append(f'              <w:t>{xml_escape(th_name)}</w:t>')
                xml_content.append('            </w:r>')
                xml_content.append('          </w:p>')
                xml_content.append('        </w:tc>')
            xml_content.append('      </w:tr>')

            # Data Rows
            for idx, item in enumerate(checklist):
                no = item.get("no") or str(idx + 1)
                name = item.get("name") or "Unnamed Document"
                ref = item.get("reference") or "—"
                mandatory = "Mandatory" if item.get("mandatory") else "Optional"

                xml_content.append('      <w:tr>')
                
                # Cells
                for val, is_bold in [(str(no), False), (name, True), (ref, False), (mandatory, False)]:
                    xml_content.append('        <w:tc>')
                    xml_content.append('          <w:p>')
                    xml_content.append('            <w:pPr><w:spacing w:before="60" w:after="60"/></w:pPr>')
                    xml_content.append('            <w:r>')
                    xml_content.append('              <w:rPr>')
                    xml_content.append('                <w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/>')
                    if is_bold:
                        xml_content.append('                <w:b/>')
                    xml_content.append('                <w:sz w:val="20"/>')
                    if val == "Mandatory":
                        xml_content.append('                <w:color w:val="B91C1C"/>') # Red text for mandatory
                    xml_content.append('              </w:rPr>')
                    xml_content.append(f'              <w:t>{xml_escape(val)}</w:t>')
                    xml_content.append('            </w:r>')
                    xml_content.append('          </w:p>')
                    xml_content.append('        </w:tc>')
                
                xml_content.append('      </w:tr>')

            xml_content.append('    </w:tbl>')

        xml_content.append('  </w:body>')
        xml_content.append('</w:document>')

        doc_xml = "\n".join(xml_content)

        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(
                "[Content_Types].xml",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
                '  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
                '  <Default Extension="xml" ContentType="application/xml"/>\n'
                '  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>\n'
                '</Types>',
            )
            z.writestr(
                "_rels/.rels",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
                '  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>\n'
                '</Relationships>',
            )
            z.writestr("word/document.xml", doc_xml)

        out.seek(0)
        return send_file(
            out,
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            as_attachment=True,
            download_name="bid_covering_letter.docx",
        )

    @app.post("/api/download-proposal")
    @require_credits(CREDIT_COST_AI_DOWNLOAD, "download_proposal", get_db_connection)
    def api_download_proposal():
        import zipfile
        import re
        from datetime import datetime

        body = request.get_json(silent=True) or {}
        title = body.get("title") or "Unknown Tender"
        authority = body.get("authority") or "Unknown Authority"
        company_name = body.get("company_name") or "Bidding Company"
        bid_response = body.get("bid_response") or {}
        checklist = body.get("checklist") or bid_response.get("checklist") or []
        clarifications = body.get("clarifications") or bid_response.get("clarification_questions") or bid_response.get("clarifications") or []
        methodology_diagram = body.get("methodology") or bid_response.get("methodology") or {}
        methodology_image_b64 = body.get("methodology_image_b64") or body.get("methodology_b64") or ""
        gantt_chart = body.get("gantt") or bid_response.get("gantt") or {}
        gantt_image_b64 = body.get("gantt_image_b64") or body.get("gantt_b64") or ""

        def xml_escape(text: Any) -> str:
            s = str(text or "")
            return (
                s.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
                .replace("'", "&apos;")
            )

        xml_content = []
        xml_content.append('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>')
        xml_content.append('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">')
        xml_content.append('  <w:body>')

        def add_heading(text, size=28, color="1E3A8A", space_before=240, space_after=120):
            xml_content.append('    <w:p>')
            xml_content.append(f'      <w:pPr><w:spacing w:before="{space_before}" w:after="{space_after}"/></w:pPr>')
            xml_content.append('      <w:r>')
            xml_content.append('        <w:rPr>')
            xml_content.append('          <w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/>')
            xml_content.append('          <w:b/>')
            xml_content.append(f'          <w:sz w:val="{size}"/>')
            xml_content.append(f'          <w:color w:val="{color}"/>')
            xml_content.append('        </w:rPr>')
            xml_content.append(f'        <w:t>{xml_escape(text)}</w:t>')
            xml_content.append('      </w:r>')
            xml_content.append('    </w:p>')

        def add_paragraphs(text, size=22):
            if not text:
                return
            for line in text.split("\n"):
                trimmed = line.strip()
                if not trimmed:
                    xml_content.append('    <w:p><w:pPr><w:spacing w:after="120"/></w:pPr></w:p>')
                    continue
                
                is_bullet = trimmed.startswith(("* ", "- ", "• "))
                bullet_text = trimmed[2:] if is_bullet else trimmed
                
                xml_content.append('    <w:p>')
                if is_bullet:
                    xml_content.append('      <w:pPr><w:ind w:left="360"/><w:spacing w:after="120"/></w:pPr>')
                    xml_content.append('      <w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:sz w:val="22"/></w:rPr><w:t>• </w:t></w:r>')
                else:
                    xml_content.append('      <w:pPr><w:spacing w:after="120"/></w:pPr>')
                
                parts = bullet_text.split("**")
                for idx, part in enumerate(parts):
                    if not part:
                        continue
                    is_bold = (idx % 2 == 1)
                    
                    # Split sub-part by placeholder brackets e.g. [Add ...] or [PLACEHOLDER: ...]
                    ph_chunks = re.split(r"(\[(?:PLACEHOLDER|Add|Insert|Verify|Status)[^\]]*\])", part)
                    for chunk in ph_chunks:
                        if not chunk:
                            continue
                        is_ph = bool(re.match(r"^\[(?:PLACEHOLDER|Add|Insert|Verify|Status)[^\]]*\]$", chunk, re.IGNORECASE))
                        xml_content.append('      <w:r>')
                        xml_content.append('        <w:rPr>')
                        xml_content.append('          <w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/>')
                        xml_content.append(f'          <w:sz w:val="{size}"/>')
                        if is_bold or is_ph:
                            xml_content.append('          <w:b/>')
                        if is_ph:
                            xml_content.append('          <w:highlight w:val="yellow"/>')
                            xml_content.append('          <w:color w:val="854D0E"/>')
                        xml_content.append('        </w:rPr>')
                        xml_content.append(f'        <w:t>{xml_escape(chunk)}</w:t>')
                        xml_content.append('      </w:r>')
                xml_content.append('    </w:p>')

        def add_page_break():
            xml_content.append('    <w:p>')
            xml_content.append('      <w:r>')
            xml_content.append('        <w:br w:type="page"/>')
            xml_content.append('      </w:r>')
            xml_content.append('    </w:p>')

        # ----------------------------------------------------
        # 1. Cover Page Title Block
        # ----------------------------------------------------
        xml_content.append('    <w:p>')
        xml_content.append('      <w:pPr><w:spacing w:before="1200" w:after="120"/><w:jc w:val="center"/></w:pPr>')
        xml_content.append('      <w:r>')
        xml_content.append('        <w:rPr>')
        xml_content.append('          <w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/>')
        xml_content.append('          <w:b/>')
        xml_content.append('          <w:sz w:val="48"/>')
        xml_content.append('          <w:color w:val="1E3A8A"/>')
        xml_content.append('        </w:rPr>')
        xml_content.append(f'        <w:t>{xml_escape(bid_response.get("document_title") or "Tender Bid Submission Response")}</w:t>')
        xml_content.append('      </w:r>')
        xml_content.append('    </w:p>')

        # Persistent AI Draft Banner
        xml_content.append('    <w:p>')
        xml_content.append('      <w:pPr><w:spacing w:after="180"/><w:jc w:val="center"/></w:pPr>')
        xml_content.append('      <w:r>')
        xml_content.append('        <w:rPr>')
        xml_content.append('          <w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/>')
        xml_content.append('          <w:b/>')
        xml_content.append('          <w:sz w:val="22"/>')
        xml_content.append('          <w:color w:val="B45309"/>')
        xml_content.append('          <w:highlight w:val="yellow"/>')
        xml_content.append('        </w:rPr>')
        xml_content.append('        <w:t>⚠️ AI DRAFT — review before submitting</w:t>')
        xml_content.append('      </w:r>')
        xml_content.append('    </w:p>')

        xml_content.append('    <w:p>')
        xml_content.append('      <w:pPr><w:spacing w:before="120" w:after="480"/><w:jc w:val="center"/></w:pPr>')
        xml_content.append('      <w:r>')
        xml_content.append('        <w:rPr>')
        xml_content.append('          <w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/>')
        xml_content.append('          <w:sz w:val="24"/>')
        xml_content.append('          <w:color w:val="64748B"/>')
        xml_content.append('        </w:rPr>')
        xml_content.append('        <w:t>PROFESSIONAL PROPOSAL &amp; COMPLIANCE RESPONSE</w:t>')
        xml_content.append('      </w:r>')
        xml_content.append('    </w:p>')

        # Authority and Bidding Company Info Block
        xml_content.append('    <w:p>')
        xml_content.append('      <w:pPr><w:spacing w:before="600" w:after="120"/><w:jc w:val="center"/></w:pPr>')
        xml_content.append('      <w:r>')
        xml_content.append('        <w:rPr>')
        xml_content.append('          <w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/>')
        xml_content.append('          <w:sz w:val="22"/>')
        xml_content.append('          <w:color w:val="475569"/>')
        xml_content.append('        </w:rPr>')
        xml_content.append(f'        <w:t>Prepared for: {xml_escape(authority)}</w:t>')
        xml_content.append('      </w:r>')
        xml_content.append('    </w:p>')

        xml_content.append('    <w:p>')
        xml_content.append('      <w:pPr><w:spacing w:after="480"/><w:jc w:val="center"/></w:pPr>')
        xml_content.append('      <w:r>')
        xml_content.append('        <w:rPr>')
        xml_content.append('          <w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/>')
        xml_content.append('          <w:sz w:val="22"/>')
        xml_content.append('          <w:color w:val="475569"/>')
        xml_content.append('        </w:rPr>')
        xml_content.append(f'        <w:t>Prepared by: {xml_escape(company_name)}</w:t>')
        xml_content.append('      </w:r>')
        xml_content.append('    </w:p>')

        # Center Border Line separator
        xml_content.append('    <w:p>')
        xml_content.append('      <w:pPr>')
        xml_content.append('        <w:spacing w:after="720"/>')
        xml_content.append('        <w:pBdr>')
        xml_content.append('          <w:bottom w:val="single" w:sz="18" w:space="8" w:color="3B82F6"/>')
        xml_content.append('        </w:pBdr>')
        xml_content.append('      </w:pPr>')
        xml_content.append('    </w:p>')

        # Date of submission
        date_str = datetime.now().strftime("%d/%m/%Y")
        xml_content.append('    <w:p>')
        xml_content.append('      <w:pPr><w:spacing w:after="240"/><w:jc w:val="center"/></w:pPr>')
        xml_content.append('      <w:r>')
        xml_content.append('        <w:rPr>')
        xml_content.append('          <w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/>')
        xml_content.append('          <w:sz w:val="20"/>')
        xml_content.append('          <w:italic/>')
        xml_content.append('          <w:color w:val="94A3B8"/>')
        xml_content.append('        </w:rPr>')
        xml_content.append(f'        <w:t>Date: {date_str}  |  Ref: {xml_escape(title[:30])}_BID_v1.0</w:t>')
        xml_content.append('      </w:r>')
        xml_content.append('    </w:p>')

        add_page_break()

        # ----------------------------------------------------
        # 2. Table of Contents
        # ----------------------------------------------------
        add_heading("Table of Contents", size=32)

        # Word dynamic field block
        xml_content.append('    <w:p>')
        xml_content.append('      <w:r>')
        xml_content.append('        <w:fldChar w:fldCharType="begin"/>')
        xml_content.append('      </w:r>')
        xml_content.append('      <w:r>')
        xml_content.append('        <w:instrText xml:space="preserve"> TOC \\o "1-3" \\h \\z \\u </w:instrText>')
        xml_content.append('      </w:r>')
        xml_content.append('      <w:r>')
        xml_content.append('        <w:fldChar w:fldCharType="separate"/>')
        xml_content.append('      </w:r>')
        xml_content.append('      <w:r>')
        xml_content.append('        <w:fldChar w:fldCharType="end"/>')
        xml_content.append('      </w:r>')
        xml_content.append('    </w:p>')

        # Visual Table of Contents Dot Leader List
        xml_content.append('    <w:p><w:pPr><w:spacing w:before="120" w:after="120"/></w:pPr><w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:sz w:val="22"/><w:b/><w:color w:val="475569"/></w:rPr><w:t>Proposal Contents Outline:</w:t></w:r></w:p>')

        toc_items = [
            "Section 1: Bid Submission Covering Letter",
            "Section 2: Executive Summary",
            "Section 3: Understanding of Requirements & Scope",
            "Section 4: Technical Methodology & Approach",
            "Section 5: Relevant Experience & Case Studies",
            "Section 6: Team, Resources & Key Personnel",
            "Section 7: Quality Assurance & Standards Compliance",
            "Section 8: Social Value & Sustainability Commitments",
            "Section 9: Pricing Schedule & Commercial Commentary",
            "Section 10: Specific Questionnaire (ITT) Responses",
            "Section 11: Official Declarations & Statement of Intent",
            "Appendix A: Document Submission Checklist",
            "Appendix B: Clarification Questions",
            "Appendix C: Placeholder Summary (Human To-Do List)"
        ]

        for item in toc_items:
            xml_content.append('    <w:p>')
            xml_content.append('      <w:pPr>')
            xml_content.append('        <w:spacing w:after="60"/>')
            xml_content.append('        <w:tabs><w:tab w:val="right" w:leader="dot" w:pos="8500"/></w:tabs>')
            xml_content.append('      </w:pPr>')
            xml_content.append('      <w:r>')
            xml_content.append('        <w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:sz w:val="20"/></w:rPr>')
            xml_content.append(f'        <w:t>{xml_escape(item)}</w:t>')
            xml_content.append('      </w:r>')
            xml_content.append('      <w:r>')
            xml_content.append('        <w:tab/>')
            xml_content.append('      </w:r>')
            xml_content.append('    </w:p>')

        add_page_break()

        # ----------------------------------------------------
        # 3. Bid Response Sections (1 to 11)
        # ----------------------------------------------------
        
        # Section 1: Covering Letter
        cover_letter = bid_response.get("cover_letter") or {}
        if cover_letter.get("content"):
            add_heading("Section 1: Bid Submission Covering Letter", size=32)
            add_paragraphs(cover_letter.get("content"))
            add_page_break()

        # Section 2: Executive Summary
        exec_summary = bid_response.get("executive_summary") or {}
        if exec_summary.get("content"):
            add_heading("Section 2: Executive Summary", size=32)
            add_paragraphs(exec_summary.get("content"))
            add_page_break()

        # Section 3: Understanding of Requirements
        understanding = bid_response.get("understanding_of_requirements") or {}
        if understanding.get("content"):
            add_heading("Section 3: Understanding of Requirements & Scope", size=32)
            add_paragraphs(understanding.get("content"))
            add_page_break()

        # Section 4: Technical Methodology
        methodology = bid_response.get("technical_methodology") or {}
        has_meth_content = bool(methodology.get("content"))
        has_meth_diagram = bool(methodology_diagram and methodology_diagram.get("phases"))
        has_meth_img = bool(methodology_image_b64)
        has_gantt = bool(gantt_chart and gantt_chart.get("phases"))

        if has_meth_content or has_meth_diagram or has_meth_img or has_gantt:
            add_heading("Section 4: Technical Methodology & Approach", size=32)
            if has_meth_content:
                add_paragraphs(methodology.get("content"))

            # --- Embedded High-Res Methodology Diagram Image ---
            if has_meth_img:
                add_heading("Delivery Methodology Visual Framework", size=24, color="0F766E", space_before=180)
                xml_content.append('    <w:p>')
                xml_content.append('      <w:pPr><w:jc w:val="center"/><w:spacing w:before="120" w:after="240"/></w:pPr>')
                xml_content.append('      <w:r>')
                xml_content.append('        <w:drawing>')
                xml_content.append('          <wp:inline distT="0" distB="0" distL="0" distR="0" xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">')
                xml_content.append('            <wp:extent cx="5400000" cy="2700000"/>')
                xml_content.append('            <wp:effectExtent l="0" t="0" r="0" b="0"/>')
                xml_content.append('            <wp:docPr id="100" name="Methodology Diagram"/>')
                xml_content.append('            <wp:cNvGraphicFramePr>')
                xml_content.append('              <a:graphicOptions xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"/>')
                xml_content.append('            </wp:cNvGraphicFramePr>')
                xml_content.append('            <a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">')
                xml_content.append('              <a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">')
                xml_content.append('                <pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">')
                xml_content.append('                  <pic:nvPicPr>')
                xml_content.append('                    <pic:cNvPr id="0" name="methodology_diagram.png"/>')
                xml_content.append('                    <pic:cNvPicPr/>')
                xml_content.append('                  </pic:nvPicPr>')
                xml_content.append('                  <pic:blipFill>')
                xml_content.append('                    <a:blip r:embed="rIdMethImg" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/>')
                xml_content.append('                    <a:stretch><a:fillRect/></a:stretch>')
                xml_content.append('                  </pic:blipFill>')
                xml_content.append('                  <pic:spPr>')
                xml_content.append('                    <a:xfrm><a:off x="0" y="0"/><a:ext cx="5400000" cy="2700000"/></a:xfrm>')
                xml_content.append('                    <a:prstGeom prst="rect"><a:avLst/></a:prstGeom>')
                xml_content.append('                  </pic:spPr>')
                xml_content.append('                </pic:pic>')
                xml_content.append('              </a:graphicData>')
                xml_content.append('            </a:graphic>')
                xml_content.append('          </wp:inline>')
                xml_content.append('        </w:drawing>')
                xml_content.append('      </w:r>')
                xml_content.append('    </w:p>')

            # --- Embedded Methodology Diagram Data ---
            if has_meth_diagram:
                add_heading("AI-Generated Delivery Methodology Overview", size=24, color="0F766E", space_before=180)
                diagram_title = methodology_diagram.get("diagram_title") or "Delivery Framework"
                style_name = str(methodology_diagram.get("style") or "linear").replace("_", " ").title()
                xml_content.append('    <w:p>')
                xml_content.append('      <w:pPr><w:spacing w:after="120"/></w:pPr>')
                xml_content.append('      <w:r>')
                xml_content.append('        <w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:b/><w:sz w:val="20"/></w:rPr>')
                xml_content.append(f'        <w:t>Framework Style: {xml_escape(style_name)} | Diagram Title: {xml_escape(diagram_title)}</w:t>')
                xml_content.append('      </w:r>')
                xml_content.append('    </w:p>')
                
                # Render phases table
                xml_content.append('    <w:tbl>')
                xml_content.append('      <w:tblPr>')
                xml_content.append('        <w:tblStyle w:val="TableGrid"/>')
                xml_content.append('        <w:tblW w:w="5000" w:type="pct"/>')
                xml_content.append('        <w:tblBorders>')
                xml_content.append('          <w:top w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>')
                xml_content.append('          <w:left w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>')
                xml_content.append('          <w:bottom w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>')
                xml_content.append('          <w:right w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>')
                xml_content.append('          <w:insideH w:val="single" w:sz="4" w:space="0" w:color="E2E8F0"/>')
                xml_content.append('          <w:insideV w:val="single" w:sz="4" w:space="0" w:color="E2E8F0"/>')
                xml_content.append('        </w:tblBorders>')
                xml_content.append('      </w:tblPr>')
                
                # Header row
                xml_content.append('      <w:tr>')
                for th_name, w_val in [("Icon", "800"), ("Phase Label", "2200"), ("Description", "5000")]:
                    xml_content.append('        <w:tc>')
                    xml_content.append(f'          <w:tcPr><w:tcW w:w="{w_val}" w:type="dxa"/><w:shd w:fill="F1F5F9"/></w:tcPr>')
                    xml_content.append('          <w:p>')
                    xml_content.append('            <w:pPr><w:spacing w:before="60" w:after="60"/></w:pPr>')
                    xml_content.append('            <w:r>')
                    xml_content.append('              <w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:b/><w:sz w:val="20"/></w:rPr>')
                    xml_content.append(f'              <w:t>{xml_escape(th_name)}</w:t>')
                    xml_content.append('            </w:r>')
                    xml_content.append('          </w:p>')
                    xml_content.append('        </w:tc>')
                xml_content.append('      </w:tr>')
                
                # Data rows
                for phase in methodology_diagram.get("phases", []):
                    xml_content.append('      <w:tr>')
                    for val, w_val in [
                        (phase.get("icon") or "⚙️", "800"),
                        (phase.get("label") or "Phase", "2200"),
                        (phase.get("description") or "", "5000")
                    ]:
                        xml_content.append('        <w:tc>')
                        xml_content.append(f'          <w:tcPr><w:tcW w:w="{w_val}" w:type="dxa"/></w:tcPr>')
                        xml_content.append('          <w:p>')
                        xml_content.append('            <w:pPr><w:spacing w:before="60" w:after="60"/></w:pPr>')
                        xml_content.append('            <w:r>')
                        xml_content.append('              <w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:sz w:val="20"/></w:rPr>')
                        xml_content.append(f'              <w:t>{xml_escape(val)}</w:t>')
                        xml_content.append('            </w:r>')
                        xml_content.append('          </w:p>')
                        xml_content.append('        </w:tc>')
                    xml_content.append('      </w:tr>')
                xml_content.append('    </w:tbl>')

            # --- Embedded Gantt Chart Image & Table ---
            has_gantt_img = bool(gantt_image_b64)
            if has_gantt_img:
                add_heading("Project Delivery Schedule Visual Diagram", size=24, color="0F766E", space_before=180)
                xml_content.append('    <w:p>')
                xml_content.append('      <w:pPr><w:jc w:val="center"/><w:spacing w:before="120" w:after="240"/></w:pPr>')
                xml_content.append('      <w:r>')
                xml_content.append('        <w:drawing>')
                xml_content.append('          <wp:inline distT="0" distB="0" distL="0" distR="0" xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">')
                xml_content.append('            <wp:extent cx="5400000" cy="2700000"/>')
                xml_content.append('            <wp:effectExtent l="0" t="0" r="0" b="0"/>')
                xml_content.append('            <wp:docPr id="101" name="Gantt Chart Diagram"/>')
                xml_content.append('            <wp:cNvGraphicFramePr>')
                xml_content.append('              <a:graphicOptions xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"/>')
                xml_content.append('            </wp:cNvGraphicFramePr>')
                xml_content.append('            <a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">')
                xml_content.append('              <a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">')
                xml_content.append('                <pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">')
                xml_content.append('                  <pic:nvPicPr>')
                xml_content.append('                    <pic:cNvPr id="0" name="gantt_chart.png"/>')
                xml_content.append('                    <pic:cNvPicPr/>')
                xml_content.append('                  </pic:nvPicPr>')
                xml_content.append('                  <pic:blipFill>')
                xml_content.append('                    <a:blip r:embed="rIdGanttImg" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/>')
                xml_content.append('                    <a:stretch><a:fillRect/></a:stretch>')
                xml_content.append('                  </pic:blipFill>')
                xml_content.append('                  <pic:spPr>')
                xml_content.append('                    <a:xfrm><a:off x="0" y="0"/><a:ext cx="5400000" cy="2700000"/></a:xfrm>')
                xml_content.append('                    <a:prstGeom prst="rect"><a:avLst/></a:prstGeom>')
                xml_content.append('                  </pic:spPr>')
                xml_content.append('                </pic:pic>')
                xml_content.append('              </a:graphicData>')
                xml_content.append('            </a:graphic>')
                xml_content.append('          </wp:inline>')
                xml_content.append('        </w:drawing>')
                xml_content.append('      </w:r>')
                xml_content.append('    </w:p>')

            if has_gantt:
                add_heading("Project Delivery Timeline (Gantt Breakdown)", size=24, color="0F766E", space_before=180)
                unit = gantt_chart.get("unit") or "weeks"
                unit_abbr = "W" if "week" in unit.lower() else "M"
                
                phases_list = gantt_chart.get("phases", [])
                total_max_units = 6
                for phase in phases_list:
                    p_start = int(phase.get("start") or 1)
                    p_dur = int(phase.get("duration") or 1)
                    p_end = p_start + p_dur - 1
                    if p_end > total_max_units:
                        total_max_units = p_end

                num_cols = min(total_max_units, 12)
                col_buckets = []
                for c in range(1, num_cols + 1):
                    c_start = int(round((c - 1) * total_max_units / num_cols)) + 1
                    c_end = int(round(c * total_max_units / num_cols))
                    if c_end < c_start:
                        c_end = c_start
                    col_buckets.append((c_start, c_end))

                col_id_w = 400
                col_name_w = 2600
                col_info_w = 1300
                col_owner_w = 1100
                timeline_total_w = 3600
                col_unit_w = max(280, timeline_total_w // num_cols)

                xml_content.append('    <w:tbl>')
                xml_content.append('      <w:tblPr>')
                xml_content.append('        <w:tblStyle w:val="TableGrid"/>')
                xml_content.append('        <w:tblW w:w="9000" w:type="dxa"/>')
                xml_content.append('        <w:tblBorders>')
                xml_content.append('          <w:top w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>')
                xml_content.append('          <w:left w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>')
                xml_content.append('          <w:bottom w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>')
                xml_content.append('          <w:right w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>')
                xml_content.append('          <w:insideH w:val="single" w:sz="4" w:space="0" w:color="E2E8F0"/>')
                xml_content.append('          <w:insideV w:val="single" w:sz="4" w:space="0" w:color="E2E8F0"/>')
                xml_content.append('        </w:tblBorders>')
                xml_content.append('      </w:tblPr>')
                xml_content.append('      <w:tblGrid>')
                xml_content.append(f'        <w:gridCol w:w="{col_id_w}"/>')
                xml_content.append(f'        <w:gridCol w:w="{col_name_w}"/>')
                xml_content.append(f'        <w:gridCol w:w="{col_info_w}"/>')
                xml_content.append(f'        <w:gridCol w:w="{col_owner_w}"/>')
                for _ in range(num_cols):
                    xml_content.append(f'        <w:gridCol w:w="{col_unit_w}"/>')
                xml_content.append('      </w:tblGrid>')

                # Header Row
                xml_content.append('      <w:tr>')
                xml_content.append(f'        <w:tc><w:tcPr><w:tcW w:w="{col_id_w}" w:type="dxa"/><w:shd w:fill="F1F5F9"/></w:tcPr>')
                xml_content.append('          <w:p><w:pPr><w:spacing w:before="60" w:after="60"/></w:pPr>')
                xml_content.append('            <w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:b/><w:sz w:val="20"/></w:rPr><w:t>ID</w:t></w:r>')
                xml_content.append('          </w:p></w:tc>')
                
                xml_content.append(f'        <w:tc><w:tcPr><w:tcW w:w="{col_name_w}" w:type="dxa"/><w:shd w:fill="F1F5F9"/></w:tcPr>')
                xml_content.append('          <w:p><w:pPr><w:spacing w:before="60" w:after="60"/></w:pPr>')
                xml_content.append('            <w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:b/><w:sz w:val="20"/></w:rPr><w:t>Phase / Milestone</w:t></w:r>')
                xml_content.append('          </w:p></w:tc>')
                
                xml_content.append(f'        <w:tc><w:tcPr><w:tcW w:w="{col_info_w}" w:type="dxa"/><w:shd w:fill="F1F5F9"/></w:tcPr>')
                xml_content.append('          <w:p><w:pPr><w:spacing w:before="60" w:after="60"/></w:pPr>')
                xml_content.append('            <w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:b/><w:sz w:val="20"/></w:rPr><w:t>Timeline</w:t></w:r>')
                xml_content.append('          </w:p></w:tc>')
                
                xml_content.append(f'        <w:tc><w:tcPr><w:tcW w:w="{col_owner_w}" w:type="dxa"/><w:shd w:fill="F1F5F9"/></w:tcPr>')
                xml_content.append('          <w:p><w:pPr><w:spacing w:before="60" w:after="60"/></w:pPr>')
                xml_content.append('            <w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:b/><w:sz w:val="20"/></w:rPr><w:t>Owner</w:t></w:r>')
                xml_content.append('          </w:p></w:tc>')
                
                for c_start, c_end in col_buckets:
                    col_hdr = f"{unit_abbr}{c_start}" if c_start == c_end else f"{unit_abbr}{c_start}-{c_end}"
                    xml_content.append(f'        <w:tc><w:tcPr><w:tcW w:w="{col_unit_w}" w:type="dxa"/><w:shd w:fill="E2E8F0"/></w:tcPr>')
                    xml_content.append('          <w:p><w:pPr><w:spacing w:before="60" w:after="60"/><w:jc w:val="center"/></w:pPr>')
                    xml_content.append(f'            <w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:b/><w:sz w:val="16"/></w:rPr><w:t>{col_hdr}</w:t></w:r>')
                    xml_content.append('          </w:p></w:tc>')
                xml_content.append('      </w:tr>')

                # Data rows
                for idx, phase in enumerate(phases_list):
                    p_id = phase.get("id") or str(idx + 1)
                    p_lbl = phase.get("label") or "Task"
                    p_start = int(phase.get("start") or 1)
                    p_dur = int(phase.get("duration") or 1)
                    p_end = p_start + p_dur - 1
                    p_owner = phase.get("assignee") or "—"
                    is_ms = bool(phase.get("is_milestone"))
                    p_color_hex = str(phase.get("colour") or "3B82F6").replace("#", "")

                    if is_ms:
                        p_lbl_display = f"◆ [Milestone] {p_lbl}"
                        time_display = f"{unit_abbr}{p_start}"
                    else:
                        p_lbl_display = p_lbl
                        time_display = f"{unit_abbr}{p_start}-{p_end} ({p_dur} {unit})"

                    xml_content.append('      <w:tr>')
                    
                    xml_content.append(f'        <w:tc><w:tcPr><w:tcW w:w="{col_id_w}" w:type="dxa"/></w:tcPr>')
                    xml_content.append('          <w:p><w:pPr><w:spacing w:before="60" w:after="60"/></w:pPr>')
                    xml_content.append(f'            <w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:sz w:val="20"/></w:rPr><w:t>{xml_escape(p_id)}</w:t></w:r>')
                    xml_content.append('          </w:p></w:tc>')
                    
                    xml_content.append(f'        <w:tc><w:tcPr><w:tcW w:w="{col_name_w}" w:type="dxa"/></w:tcPr>')
                    xml_content.append('          <w:p><w:pPr><w:spacing w:before="60" w:after="60"/></w:pPr>')
                    xml_content.append(f'            <w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:b/><w:sz w:val="20"/></w:rPr><w:t>{xml_escape(p_lbl_display)}</w:t></w:r>')
                    xml_content.append('          </w:p></w:tc>')
                    
                    xml_content.append(f'        <w:tc><w:tcPr><w:tcW w:w="{col_info_w}" w:type="dxa"/></w:tcPr>')
                    xml_content.append('          <w:p><w:pPr><w:spacing w:before="60" w:after="60"/></w:pPr>')
                    xml_content.append(f'            <w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:sz w:val="20"/></w:rPr><w:t>{xml_escape(time_display)}</w:t></w:r>')
                    xml_content.append('          </w:p></w:tc>')
                    
                    xml_content.append(f'        <w:tc><w:tcPr><w:tcW w:w="{col_owner_w}" w:type="dxa"/></w:tcPr>')
                    xml_content.append('          <w:p><w:pPr><w:spacing w:before="60" w:after="60"/></w:pPr>')
                    xml_content.append(f'            <w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:sz w:val="20"/></w:rPr><w:t>{xml_escape(p_owner)}</w:t></w:r>')
                    xml_content.append('          </w:p></w:tc>')

                    for c_start, c_end in col_buckets:
                        is_active = max(p_start, c_start) <= min(p_end, c_end)
                        if is_ms and (c_start <= p_start <= c_end):
                            xml_content.append(f'        <w:tc><w:tcPr><w:tcW w:w="{col_unit_w}" w:type="dxa"/><w:shd w:fill="F59E0B"/></w:tcPr>')
                            xml_content.append('          <w:p><w:pPr><w:spacing w:before="60" w:after="60"/><w:jc w:val="center"/></w:pPr>')
                            xml_content.append('            <w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:b/><w:sz w:val="18"/><w:color w:val="FFFFFF"/></w:rPr><w:t>◆</w:t></w:r>')
                            xml_content.append('          </w:p></w:tc>')
                        elif not is_ms and is_active:
                            xml_content.append(f'        <w:tc><w:tcPr><w:tcW w:w="{col_unit_w}" w:type="dxa"/><w:shd w:fill="{p_color_hex}"/></w:tcPr>')
                            xml_content.append('          <w:p><w:pPr><w:spacing w:before="60" w:after="60"/><w:jc w:val="center"/></w:pPr>')
                            xml_content.append('            <w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:b/><w:sz w:val="14"/><w:color w:val="FFFFFF"/></w:rPr><w:t>■</w:t></w:r>')
                            xml_content.append('          </w:p></w:tc>')
                        else:
                            xml_content.append(f'        <w:tc><w:tcPr><w:tcW w:w="{col_unit_w}" w:type="dxa"/></w:tcPr>')
                            xml_content.append('          <w:p><w:pPr><w:spacing w:before="60" w:after="60"/></w:pPr>')
                            xml_content.append('            <w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:sz w:val="14"/></w:rPr><w:t></w:t></w:r>')
                            xml_content.append('          </w:p></w:tc>')

                    xml_content.append('      </w:tr>')
                xml_content.append('    </w:tbl>')
            
            add_page_break()


        # Section 5: Relevant Experience
        experience = bid_response.get("relevant_experience") or {}
        if experience.get("content"):
            add_heading("Section 5: Relevant Experience & Case Studies", size=32)
            add_paragraphs(experience.get("content"))
            add_page_break()

        # Section 6: Team & Key Personnel
        team = bid_response.get("team_and_personnel") or {}
        if team.get("content"):
            add_heading("Section 6: Team, Resources & Key Personnel", size=32)
            add_paragraphs(team.get("content"))
            add_page_break()

        # Section 7: Quality and Compliance
        quality = bid_response.get("quality_and_compliance") or {}
        if quality.get("content"):
            add_heading("Section 7: Quality Assurance & Standards Compliance", size=32)
            add_paragraphs(quality.get("content"))
            add_page_break()

        # Section 8: Social Value
        social = bid_response.get("social_value") or {}
        if social.get("content"):
            add_heading("Section 8: Social Value & Sustainability Commitments", size=32)
            add_paragraphs(social.get("content"))
            add_page_break()

        # Section 9: Pricing Notes
        pricing = bid_response.get("pricing_notes") or {}
        if pricing.get("content"):
            add_heading("Section 9: Pricing Schedule & Commercial Commentary", size=32)
            add_paragraphs(pricing.get("content"))
            add_page_break()

        # Section 10: Specific Questionnaire Responses
        itt_responses = bid_response.get("itt_question_responses") or []
        if itt_responses:
            add_heading("Section 10: Specific Questionnaire (ITT) Responses", size=32)
            for idx, q_item in enumerate(itt_responses):
                q_num = q_item.get("question_number") or str(idx + 1)
                q_text = q_item.get("question_text") or "Tender Question"
                response_text = q_item.get("response") or ""

                xml_content.append('    <w:p>')
                xml_content.append('      <w:pPr><w:spacing w:before="180" w:after="60"/></w:pPr>')
                xml_content.append('      <w:r>')
                xml_content.append('        <w:rPr>')
                xml_content.append('          <w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/>')
                xml_content.append('          <w:b/>')
                xml_content.append('          <w:sz w:val="24"/>')
                xml_content.append('          <w:color w:val="1E3A8A"/>')
                xml_content.append('        </w:rPr>')
                xml_content.append(f'        <w:t>Question {xml_escape(q_num)}: </w:t>')
                xml_content.append('      </w:r>')
                xml_content.append('      <w:r>')
                xml_content.append('        <w:rPr>')
                xml_content.append('          <w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/>')
                xml_content.append('          <w:italic/>')
                xml_content.append('          <w:sz w:val="22"/>')
                xml_content.append('          <w:color w:val="475569"/>')
                xml_content.append('        </w:rPr>')
                xml_content.append(f'        <w:t>"{xml_escape(q_text)}"</w:t>')
                xml_content.append('      </w:r>')
                xml_content.append('    </w:p>')

                add_paragraphs(response_text)
            add_page_break()

        # Section 11: Declarations
        declarations = bid_response.get("declarations") or {}
        if declarations.get("content"):
            add_heading("Section 11: Official Declarations & Statement of Intent", size=32)
            add_paragraphs(declarations.get("content"))
            add_page_break()

        # ----------------------------------------------------
        # 4. Appendices (A, B, C)
        # ----------------------------------------------------
        
        # Appendix A: Document Checklist
        add_heading("Appendix A: Document Submission Checklist", size=32)
        xml_content.append('    <w:p>')
        xml_content.append('      <w:pPr><w:spacing w:after="180"/></w:pPr>')
        xml_content.append('      <w:r>')
        xml_content.append('        <w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:sz w:val="22"/></w:rPr>')
        xml_content.append('        <w:t>The following checklist details the full set of required certificates, signed declarations, policy forms, and supporting information identified from the tender specifications:</w:t>')
        xml_content.append('      </w:r>')
        xml_content.append('    </w:p>')

        if checklist:
            xml_content.append('    <w:tbl>')
            xml_content.append('      <w:tblPr>')
            xml_content.append('        <w:tblStyle w:val="TableGrid"/>')
            xml_content.append('        <w:tblW w:w="5000" w:type="pct"/>')
            xml_content.append('        <w:tblBorders>')
            xml_content.append('          <w:top w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>')
            xml_content.append('          <w:left w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>')
            xml_content.append('          <w:bottom w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>')
            xml_content.append('          <w:right w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>')
            xml_content.append('          <w:insideH w:val="single" w:sz="4" w:space="0" w:color="E2E8F0"/>')
            xml_content.append('          <w:insideV w:val="single" w:sz="4" w:space="0" w:color="E2E8F0"/>')
            xml_content.append('        </w:tblBorders>')
            xml_content.append('      </w:tblPr>')
            xml_content.append('      <w:tblGrid>')
            xml_content.append('        <w:gridCol w:w="600"/>')
            xml_content.append('        <w:gridCol w:w="2200"/>')
            xml_content.append('        <w:gridCol w:w="1200"/>')
            xml_content.append('        <w:gridCol w:w="1200"/>')
            xml_content.append('        <w:gridCol w:w="2800"/>')
            xml_content.append('      </w:tblGrid>')

            # Header row (5 columns)
            xml_content.append('      <w:tr>')
            for th_name, w_val in [("#", "600"), ("Document Name / Requirement", "2200"), ("Type", "1200"), ("Action / Status", "1200"), ("Clause Notes / Requirements", "2800")]:
                xml_content.append('        <w:tc>')
                xml_content.append(f'          <w:tcPr><w:tcW w:w="{w_val}" w:type="dxa"/><w:shd w:fill="F1F5F9"/></w:tcPr>')
                xml_content.append('          <w:p>')
                xml_content.append('            <w:pPr><w:spacing w:before="60" w:after="60"/></w:pPr>')
                xml_content.append('            <w:r>')
                xml_content.append('              <w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:b/><w:sz w:val="20"/></w:rPr>')
                xml_content.append(f'              <w:t>{xml_escape(th_name)}</w:t>')
                xml_content.append('            </w:r>')
                xml_content.append('          </w:p>')
                xml_content.append('        </w:tc>')
            xml_content.append('      </w:tr>')

            # Data rows
            for idx, item in enumerate(checklist):
                no = item.get("no") or str(idx + 1)
                name = item.get("name") or "Unnamed Document"
                type_flag = item.get("type") or "MANDATORY"
                status = item.get("status") or "To obtain"
                notes = item.get("notes") or "—"

                # Color coding status type
                type_color = "000000"
                if "MANDATORY" in str(type_flag).upper():
                    type_color = "B91C1C"
                elif "SCORED" in str(type_flag).upper():
                    type_color = "4338CA"

                xml_content.append('      <w:tr>')
                
                # Column 1: #
                xml_content.append('        <w:tc>')
                xml_content.append('          <w:p><w:pPr><w:spacing w:before="60" w:after="60"/></w:pPr>')
                xml_content.append('            <w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:sz w:val="20"/></w:rPr>')
                xml_content.append(f'              <w:t>{xml_escape(no)}</w:t>')
                xml_content.append('            </w:r></w:p>')
                xml_content.append('        </w:tc>')

                # Column 2: Document
                xml_content.append('        <w:tc>')
                xml_content.append('          <w:p><w:pPr><w:spacing w:before="60" w:after="60"/></w:pPr>')
                xml_content.append('            <w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:b/><w:sz w:val="20"/></w:rPr>')
                xml_content.append(f'              <w:t>{xml_escape(name)}</w:t>')
                xml_content.append('            </w:r></w:p>')
                xml_content.append('        </w:tc>')

                # Column 3: Type
                xml_content.append('        <w:tc>')
                xml_content.append('          <w:p><w:pPr><w:spacing w:before="60" w:after="60"/></w:pPr>')
                xml_content.append('            <w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:b/><w:sz w:val="20"/><w:color w:val="' + type_color + '"/></w:rPr>')
                xml_content.append(f'              <w:t>{xml_escape(type_flag)}</w:t>')
                xml_content.append('            </w:r></w:p>')
                xml_content.append('        </w:tc>')

                # Column 4: Status
                xml_content.append('        <w:tc>')
                xml_content.append('          <w:p><w:pPr><w:spacing w:before="60" w:after="60"/></w:pPr>')
                xml_content.append('            <w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:sz w:val="20"/></w:rPr>')
                xml_content.append(f'              <w:t>⬜ {xml_escape(status)}</w:t>')
                xml_content.append('            </w:r></w:p>')
                xml_content.append('        </w:tc>')

                # Column 5: Notes
                xml_content.append('        <w:tc>')
                xml_content.append('          <w:p><w:pPr><w:spacing w:before="60" w:after="60"/></w:pPr>')
                xml_content.append('            <w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:sz w:val="20"/></w:rPr>')
                xml_content.append(f'              <w:t>{xml_escape(notes)}</w:t>')
                xml_content.append('            </w:r></w:p>')
                xml_content.append('        </w:tc>')

                xml_content.append('      </w:tr>')
            xml_content.append('    </w:tbl>')
        else:
            xml_content.append('    <w:p><w:r><w:t>No documents extracted for checklist.</w:t></w:r></w:p>')

        add_page_break()

        # Appendix B: Clarification Questions
        add_heading("Appendix B: Clarification Questions", size=32)
        xml_content.append('    <w:p>')
        xml_content.append('      <w:pPr><w:spacing w:after="180"/></w:pPr>')
        xml_content.append('      <w:r>')
        xml_content.append('        <w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:sz w:val="22"/></w:rPr>')
        xml_content.append('        <w:t>The following clarification questions should be submitted via the portal before the clarification deadline to resolve ambiguities or contradictions in the specifications:</w:t>')
        xml_content.append('      </w:r>')
        xml_content.append('    </w:p>')

        if clarifications:
            xml_content.append('    <w:tbl>')
            xml_content.append('      <w:tblPr>')
            xml_content.append('        <w:tblStyle w:val="TableGrid"/>')
            xml_content.append('        <w:tblW w:w="5000" w:type="pct"/>')
            xml_content.append('        <w:tblBorders>')
            xml_content.append('          <w:top w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>')
            xml_content.append('          <w:left w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>')
            xml_content.append('          <w:bottom w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>')
            xml_content.append('          <w:right w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>')
            xml_content.append('          <w:insideH w:val="single" w:sz="4" w:space="0" w:color="E2E8F0"/>')
            xml_content.append('          <w:insideV w:val="single" w:sz="4" w:space="0" w:color="E2E8F0"/>')
            xml_content.append('        </w:tblBorders>')
            xml_content.append('      </w:tblPr>')
            xml_content.append('      <w:tblGrid>')
            xml_content.append('        <w:gridCol w:w="500"/>')
            xml_content.append('        <w:gridCol w:w="900"/>')
            xml_content.append('        <w:gridCol w:w="1100"/>')
            xml_content.append('        <w:gridCol w:w="2800"/>')
            xml_content.append('        <w:gridCol w:w="2500"/>')
            xml_content.append('        <w:gridCol w:w="1200"/>')
            xml_content.append('      </w:tblGrid>')

            # Header row (6 columns)
            xml_content.append('      <w:tr>')
            for th_name, w_val in [("#", "500"), ("Priority", "900"), ("Category", "1100"), ("Question", "2800"), ("Strategic Context / Impact", "2500"), ("Reference", "1200")]:
                xml_content.append('        <w:tc>')
                xml_content.append(f'          <w:tcPr><w:tcW w:w="{w_val}" w:type="dxa"/><w:shd w:fill="F1F5F9"/></w:tcPr>')
                xml_content.append('          <w:p>')
                xml_content.append('            <w:pPr><w:spacing w:before="60" w:after="60"/></w:pPr>')
                xml_content.append('            <w:r>')
                xml_content.append('              <w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:b/><w:sz w:val="20"/></w:rPr>')
                xml_content.append(f'              <w:t>{xml_escape(th_name)}</w:t>')
                xml_content.append('            </w:r>')
                xml_content.append('          </w:p>')
                xml_content.append('        </w:tc>')
            xml_content.append('      </w:tr>')

            # Data rows
            for idx, q in enumerate(clarifications):
                no = q.get("no") or str(idx + 1)
                prio = q.get("priority") or "MEDIUM"
                cat = q.get("category") or "General"
                q_text = q.get("question") or q.get("question_text") or q.get("clarification") or q.get("text") or q.get("issue") or ""
                reason = q.get("reason_for_asking") or q.get("priority_reason") or q.get("reason") or ""
                impact = q.get("impact_if_not_clarified") or q.get("bid_impact") or q.get("impact") or ""
                ref = q.get("ambiguity_source") or q.get("reference") or "—"

                # Priority Colors
                prio_color = "000000"
                prio_upper = str(prio).upper()
                if "CRITICAL" in prio_upper:
                    prio_color = "B91C1C"
                elif "HIGH" in prio_upper:
                    prio_color = "C2410C"
                elif "MEDIUM" in prio_upper:
                    prio_color = "2563EB"
                elif "LOW" in prio_upper:
                    prio_color = "166534"

                reason_part = f"Reason: {reason}" if reason else ""
                impact_part = f"Impact: {impact}" if impact else ""
                strat_val = "\n".join(filter(None, [reason_part, impact_part])) or "—"

                xml_content.append('      <w:tr>')
                
                # Cells with explicit column widths
                cells_data = [
                    (str(no), "000000", False, "500"),
                    (str(prio), prio_color, True, "900"),
                    (str(cat), "000000", False, "1100"),
                    (str(q_text), "1E3A8A", True, "2800"),
                    (strat_val, "000000", False, "2500"),
                    (str(ref), "475569", False, "1200")
                ]
                for val, color, is_bold, col_width in cells_data:
                    xml_content.append('        <w:tc>')
                    xml_content.append(f'          <w:tcPr><w:tcW w:w="{col_width}" w:type="dxa"/></w:tcPr>')
                    xml_content.append('          <w:p>')
                    xml_content.append('            <w:pPr><w:spacing w:before="60" w:after="60"/></w:pPr>')
                    for part in val.split("\n"):
                        xml_content.append('            <w:r>')
                        xml_content.append('              <w:rPr>')
                        xml_content.append('                <w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/>')
                        if is_bold:
                            xml_content.append('                <w:b/>')
                        xml_content.append('                <w:sz w:val="20"/>')
                        if color != "000000":
                            xml_content.append(f'                <w:color w:val="{color}"/>')
                        xml_content.append('              </w:rPr>')
                        xml_content.append(f'              <w:t>{xml_escape(part)}</w:t>')
                        xml_content.append('            </w:r>')
                    xml_content.append('          </w:p>')
                    xml_content.append('        </w:tc>')

                xml_content.append('      </w:tr>')
            xml_content.append('    </w:tbl>')
        else:
            xml_content.append('    <w:p><w:r><w:t>No clarification questions generated.</w:t></w:r></w:p>')

        add_page_break()

        # Appendix C: Placeholder Summary
        add_heading("Appendix C: Placeholder Summary (Human To-Do List)", size=32)
        xml_content.append('    <w:p>')
        xml_content.append('      <w:pPr><w:spacing w:after="180"/></w:pPr>')
        xml_content.append('      <w:r>')
        xml_content.append('        <w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:sz w:val="22"/></w:rPr>')
        xml_content.append('        <w:t>The following list highlights all company-specific details, CVs, case studies, or figures that require human input (demarcated by [PLACEHOLDER: ...]) prior to bid submission:</w:t>')
        xml_content.append('      </w:r>')
        xml_content.append('    </w:p>')

        # Scan generated content dynamically for unresolved placeholders
        placeholders_found = []
        placeholder_regex = re.compile(r"\[(?:PLACEHOLDER|PLACEHOLDER_NEEDED|INSERT|TODO|TO_BE_CONFIRMED|REQUIRED):?[^\]]*\]", re.IGNORECASE)

        sections_to_scan = [
            ("Cover Letter", cover_letter.get("content", "")),
            ("Executive Summary", exec_summary.get("content", "")),
            ("Understanding of Requirements", understanding.get("content", "")),
            ("Technical Methodology", methodology.get("content", "")),
            ("Relevant Experience", experience.get("content", "")),
            ("Team & Personnel", team.get("content", "")),
            ("Quality & Compliance", quality.get("content", "")),
            ("Social Value", social.get("content", "")),
            ("Pricing Notes", pricing.get("content", "")),
            ("Declarations", declarations.get("content", ""))
        ]

        for idx, q_item in enumerate(itt_responses):
            q_num = q_item.get("question_number") or str(idx + 1)
            sections_to_scan.append((f"ITT Q{q_num}", q_item.get("response", "")))

        for sec_name, content in sections_to_scan:
            matches = placeholder_regex.findall(content)
            for m in matches:
                clean_act = m.strip("[]").replace("PLACEHOLDER_NEEDED:", "").replace("PLACEHOLDER:", "").replace("INSERT:", "").strip()
                placeholders_found.append({
                    "section": sec_name,
                    "tag": m,
                    "action": clean_act or "Input required"
                })

        # Also scan Appendix A Checklist items marked as PLACEHOLDER_NEEDED or To obtain
        if checklist:
            for item in checklist:
                c_status = str(item.get("status") or "").upper()
                c_type = str(item.get("type") or "").upper()
                if "PLACEHOLDER" in c_status or "TO OBTAIN" in c_status or "NEEDED" in c_status:
                    doc_name = item.get("name") or "Required Document"
                    placeholders_found.append({
                        "section": "Appendix A: Submission Checklist",
                        "tag": f"[PLACEHOLDER_NEEDED: {doc_name}]",
                        "action": f"Obtain and attach required {c_type} document: '{doc_name}'"
                    })

        if placeholders_found:
            xml_content.append('    <w:tbl>')
            xml_content.append('      <w:tblPr>')
            xml_content.append('        <w:tblStyle w:val="TableGrid"/>')
            xml_content.append('        <w:tblW w:w="5000" w:type="pct"/>')
            xml_content.append('        <w:tblBorders>')
            xml_content.append('          <w:top w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>')
            xml_content.append('          <w:left w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>')
            xml_content.append('          <w:bottom w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>')
            xml_content.append('          <w:right w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>')
            xml_content.append('          <w:insideH w:val="single" w:sz="4" w:space="0" w:color="E2E8F0"/>')
            xml_content.append('          <w:insideV w:val="single" w:sz="4" w:space="0" w:color="E2E8F0"/>')
            xml_content.append('        </w:tblBorders>')
            xml_content.append('      </w:tblPr>')
            xml_content.append('      <w:tblGrid>')
            xml_content.append('        <w:gridCol w:w="500"/>')
            xml_content.append('        <w:gridCol w:w="2000"/>')
            xml_content.append('        <w:gridCol w:w="3000"/>')
            xml_content.append('        <w:gridCol w:w="3500"/>')
            xml_content.append('      </w:tblGrid>')

            # Table Header
            xml_content.append('      <w:tr>')
            for th_name, w_val in [("Section Location", "2000"), ("Placeholder Tag Identifier", "3000"), ("Action Required by Bidding Team", "4000")]:
                xml_content.append('        <w:tc>')
                xml_content.append(f'          <w:tcPr><w:tcW w:w="{w_val}" w:type="dxa"/><w:shd w:fill="F1F5F9"/></w:tcPr>')
                xml_content.append('          <w:p>')
                xml_content.append('            <w:pPr><w:spacing w:before="60" w:after="60"/></w:pPr>')
                xml_content.append('            <w:r>')
                xml_content.append('              <w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:b/><w:sz w:val="20"/></w:rPr>')
                xml_content.append(f'              <w:t>{xml_escape(th_name)}</w:t>')
                xml_content.append('            </w:r>')
                xml_content.append('          </w:p>')
                xml_content.append('        </w:tc>')
            xml_content.append('      </w:tr>')

            # Rows
            for p in placeholders_found:
                xml_content.append('      <w:tr>')
                
                # Location
                xml_content.append('        <w:tc>')
                xml_content.append('          <w:p><w:pPr><w:spacing w:before="60" w:after="60"/></w:pPr>')
                xml_content.append('            <w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:sz w:val="20"/></w:rPr>')
                xml_content.append(f'              <w:t>{xml_escape(p["section"])}</w:t>')
                xml_content.append('            </w:r></w:p>')
                xml_content.append('        </w:tc>')

                # Tag
                xml_content.append('        <w:tc>')
                xml_content.append('          <w:p><w:pPr><w:spacing w:before="60" w:after="60"/></w:pPr>')
                xml_content.append('            <w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:b/><w:color w:val="C2410C"/><w:sz w:val="20"/></w:rPr>')
                xml_content.append(f'              <w:t>{xml_escape(p["tag"])}</w:t>')
                xml_content.append('            </w:r></w:p>')
                xml_content.append('        </w:tc>')

                # Action
                xml_content.append('        <w:tc>')
                xml_content.append('          <w:p><w:pPr><w:spacing w:before="60" w:after="60"/></w:pPr>')
                xml_content.append('            <w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:sz w:val="20"/></w:rPr>')
                xml_content.append(f'              <w:t>To supply: {xml_escape(p["action"])}</w:t>')
                xml_content.append('            </w:r></w:p>')
                xml_content.append('        </w:tc>')

                xml_content.append('      </w:tr>')
            xml_content.append('    </w:tbl>')
        else:
            xml_content.append('    <w:p><w:pPr><w:spacing w:after="120"/></w:pPr>')
            xml_content.append('      <w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:sz w:val="22"/><w:color w:val="166534"/><w:b/></w:rPr>')
            xml_content.append('        <w:t>✓ No unresolved placeholders detected in proposal draft. Ready to submit!</w:t>')
            xml_content.append('      </w:r>')
            xml_content.append('    </w:p>')

        # Close XML
        xml_content.append('  </w:body>')
        xml_content.append('</w:document>')

        doc_xml = "\n".join(xml_content)

        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            content_types_entries = [
                '  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
                '  <Default Extension="xml" ContentType="application/xml"/>',
                '  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            ]
            doc_rels_entries = []

            if methodology_image_b64:
                try:
                    import base64
                    clean_b64 = methodology_image_b64.split(",")[-1]
                    img_bytes = base64.b64decode(clean_b64)
                    z.writestr("word/media/methodology_diagram.png", img_bytes)
                    content_types_entries.append('  <Default Extension="png" ContentType="image/png"/>')
                    doc_rels_entries.append('  <Relationship Id="rIdMethImg" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/methodology_diagram.png"/>')
                except Exception as b64_err:
                    print("Failed to embed methodology diagram image into docx:", b64_err)

            if gantt_image_b64:
                try:
                    import base64
                    clean_g_b64 = gantt_image_b64.split(",")[-1]
                    g_img_bytes = base64.b64decode(clean_g_b64)
                    z.writestr("word/media/gantt_chart.png", g_img_bytes)
                    if 'Extension="png"' not in "".join(content_types_entries):
                        content_types_entries.append('  <Default Extension="png" ContentType="image/png"/>')
                    doc_rels_entries.append('  <Relationship Id="rIdGanttImg" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/gantt_chart.png"/>')
                except Exception as b64_g_err:
                    print("Failed to embed gantt chart image into docx:", b64_g_err)

            z.writestr(
                "[Content_Types].xml",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
                + "\n".join(content_types_entries) +
                '\n</Types>',
            )
            z.writestr(
                "_rels/.rels",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
                '  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>\n'
                '</Relationships>',
            )
            if doc_rels_entries:
                z.writestr(
                    "word/_rels/document.xml.rels",
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
                    + "\n".join(doc_rels_entries) +
                    '\n</Relationships>',
                )
            z.writestr("word/document.xml", doc_xml)

        out.seek(0)
        return send_file(
            out,
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            as_attachment=True,
            download_name="bid_full_proposal.docx",
        )

    @app.post("/api/download-dissection")
    @require_credits(CREDIT_COST_AI_DOWNLOAD, "download_dissection", get_db_connection)
    def api_download_dissection():
        import zipfile
        import io
        from datetime import datetime
        body = request.get_json(silent=True) or {}
        title = body.get("title") or "Unknown Tender"
        authority = body.get("authority") or "Unknown Authority"
        d = body.get("dissection") or {}

        def xml_escape(text: Any) -> str:
            s = str(text or "")
            return (
                s.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
                .replace("'", "&apos;")
            )

        title_esc = xml_escape(title)
        auth_esc = xml_escape(authority)
        date_str = datetime.now().strftime("%d/%m/%Y")

        xml_content = []
        xml_content.append('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>')
        xml_content.append('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">')
        xml_content.append('  <w:body>')

        # Heading
        xml_content.append('    <w:p>')
        xml_content.append('      <w:pPr><w:spacing w:before="240" w:after="120"/></w:pPr>')
        xml_content.append('      <w:r>')
        xml_content.append('        <w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:b/><w:sz w:val="36"/><w:color w:val="1E3A8A"/></w:rPr>')
        xml_content.append(f'        <w:t>Tender Dissection &amp; Requirements Summary</w:t>')
        xml_content.append('      </w:r>')
        xml_content.append('    </w:p>')

        # Meta info
        meta_items = [
            ("Tender Opportunity:", title),
            ("Contracting Authority:", authority),
            ("Date of Dissection:", date_str),
            ("Official Reference:", d.get("tender_reference", "—")),
            ("Procurement Type:", d.get("procurement_type", "—")),
            ("Estimated Value:", f"{d.get('contract_value', {}).get('estimated', '—')} {d.get('contract_value', {}).get('currency', '')}"),
            ("Contract Duration:", d.get("contract_duration", "—")),
            ("Submission Deadline:", f"{d.get('submission_deadline', {}).get('date', '')} {d.get('submission_deadline', {}).get('time', '')} ({d.get('submission_deadline', {}).get('timezone', '')})")
        ]
        for label, val in meta_items:
            xml_content.append('    <w:p>')
            xml_content.append('      <w:pPr><w:spacing w:after="60"/></w:pPr>')
            xml_content.append('      <w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:b/><w:sz w:val="20"/></w:rPr>')
            xml_content.append(f'        <w:t>{xml_escape(label)} </w:t>')
            xml_content.append('      </w:r>')
            xml_content.append('      <w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:sz w:val="20"/></w:rPr>')
            xml_content.append(f'        <w:t>{xml_escape(val)}</w:t>')
            xml_content.append('      </w:r>')
            xml_content.append('    </w:p>')

        # Divider
        xml_content.append('    <w:p><w:pPr><w:spacing w:after="240"/><w:pBdr><w:bottom w:val="single" w:sz="6" w:space="4" w:color="3B82F6"/></w:pBdr></w:pPr></w:p>')

        # Scope
        if d.get("scope_of_work"):
            xml_content.append('    <w:p><w:pPr><w:spacing w:before="180" w:after="60"/></w:pPr><w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:b/><w:sz w:val="24"/><w:color w:val="1E3A8A"/></w:rPr><w:t>Scope of Work Summary</w:t></w:r></w:p>')
            scope = d["scope_of_work"]
            summary = scope.get("summary") or ""
            for p_text in summary.split("\n"):
                if p_text.strip():
                    xml_content.append('    <w:p><w:pPr><w:spacing w:after="120"/></w:pPr><w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:sz w:val="22"/></w:rPr>')
                    xml_content.append(f'      <w:t>{xml_escape(p_text.strip())}</w:t>')
                    xml_content.append('    </w:r></w:p>')
            
            key_delivs = scope.get("key_deliverables") or []
            if key_delivs:
                xml_content.append('    <w:p><w:pPr><w:spacing w:before="120" w:after="60"/></w:pPr><w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:b/><w:sz w:val="20"/><w:color w:val="475569"/></w:rPr><w:t>Key Deliverables:</w:t></w:r></w:p>')
                for kd in key_delivs:
                    xml_content.append('    <w:p><w:pPr><w:ind w:left="360"/><w:spacing w:after="60"/></w:pPr>')
                    xml_content.append('      <w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:sz w:val="22"/></w:rPr><w:t>• </w:t></w:r>')
                    xml_content.append('      <w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:sz w:val="22"/></w:rPr>')
                    xml_content.append(f'        <w:t>{xml_escape(kd)}</w:t>')
                    xml_content.append('      </w:r>')
                    xml_content.append('    </w:p>')

        # Eval Criteria
        eval_crit = d.get("evaluation_criteria") or []
        if eval_crit:
            xml_content.append('    <w:p><w:pPr><w:spacing w:before="180" w:after="60"/></w:pPr><w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:b/><w:sz w:val="24"/><w:color w:val="1E3A8A"/></w:rPr><w:t>Evaluation Criteria &amp; Weightings</w:t></w:r></w:p>')
            for ec in eval_crit:
                wp = ec.get("weighting_percent")
                weight_str = f"{wp}%" if wp else "Unstated weighting"
                xml_content.append('    <w:p><w:pPr><w:ind w:left="360"/><w:spacing w:after="60"/></w:pPr>')
                xml_content.append('      <w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:sz w:val="22"/></w:rPr><w:t>• </w:t></w:r>')
                xml_content.append('      <w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:b/><w:sz w:val="22"/></w:rPr>')
                xml_content.append(f'        <w:t>{xml_escape(ec.get("criterion", ""))} </w:t>')
                xml_content.append('      </w:r>')
                xml_content.append('      <w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:sz w:val="22"/></w:rPr>')
                xml_content.append(f'        <w:t>({weight_str}) - Category: {xml_escape(ec.get("type", "—"))}</w:t>')
                xml_content.append('      </w:r>')
                xml_content.append('    </w:p>')

        # Ambiguities
        ambig = d.get("ambiguities_detected") or []
        if ambig:
            xml_content.append('    <w:p><w:pPr><w:spacing w:before="180" w:after="60"/></w:pPr><w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:b/><w:sz w:val="24"/><w:color w:val="B91C1C"/></w:rPr><w:t>Identified Ambiguities &amp; Risks</w:t></w:r></w:p>')
            for a in ambig:
                xml_content.append('    <w:p><w:pPr><w:spacing w:after="60"/></w:pPr>')
                xml_content.append('      <w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:b/><w:sz w:val="22"/><w:color w:val="B91C1C"/></w:rPr>')
                xml_content.append(f'        <w:t>[{xml_escape(a.get("section", "General"))}] </w:t>')
                xml_content.append('      </w:r>')
                xml_content.append('      <w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:sz w:val="22"/><w:color w:val="991B1B"/></w:rPr>')
                xml_content.append(f'        <w:t>{xml_escape(a.get("issue", ""))}</w:t>')
                xml_content.append('      </w:r>')
                xml_content.append('    </w:p>')

        xml_content.append('  </w:body>')
        xml_content.append('</w:document>')

        doc_xml = "\n".join(xml_content)
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(
                "[Content_Types].xml",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
                '  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
                '  <Default Extension="xml" ContentType="application/xml"/>\n'
                '  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>\n'
                '</Types>',
            )
            z.writestr(
                "_rels/.rels",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
                '  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>\n'
                '</Relationships>',
            )
            z.writestr("word/document.xml", doc_xml)
        out.seek(0)
        return send_file(
            out,
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            as_attachment=True,
            download_name="bid_dissection_summary.docx"
        )

    @app.post("/api/download-checklist")
    @require_credits(CREDIT_COST_AI_DOWNLOAD, "download_checklist", get_db_connection)
    def api_download_checklist():
        import zipfile
        import io
        from datetime import datetime
        body = request.get_json(silent=True) or {}
        title = body.get("title") or "Unknown Tender"
        authority = body.get("authority") or "Unknown Authority"
        checklist = body.get("checklist") or {}
        checklist_items = checklist.get("checklist_items") or []

        def xml_escape(text: Any) -> str:
            s = str(text or "")
            return (
                s.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
                .replace("'", "&apos;")
            )

        xml_content = []
        xml_content.append('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>')
        xml_content.append('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">')
        xml_content.append('  <w:body>')

        # Heading
        xml_content.append('    <w:p>')
        xml_content.append('      <w:pPr><w:spacing w:before="240" w:after="120"/></w:pPr>')
        xml_content.append('      <w:r>')
        xml_content.append('        <w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:b/><w:sz w:val="36"/><w:color w:val="1E3A8A"/></w:rPr>')
        xml_content.append(f'        <w:t>Tender Bid Submission Checklist</w:t>')
        xml_content.append('      </w:r>')
        xml_content.append('    </w:p>')

        # Meta info
        date_str = datetime.now().strftime("%d/%m/%Y")
        for label, val in [
            ("Tender Opportunity:", title),
            ("Contracting Authority:", authority),
            ("Export Date:", date_str)
        ]:
            xml_content.append('    <w:p>')
            xml_content.append('      <w:pPr><w:spacing w:after="60"/></w:pPr>')
            xml_content.append('      <w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:b/><w:sz w:val="20"/></w:rPr>')
            xml_content.append(f'        <w:t>{xml_escape(label)} </w:t>')
            xml_content.append('      </w:r>')
            xml_content.append('      <w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:sz w:val="20"/></w:rPr>')
            xml_content.append(f'        <w:t>{xml_escape(val)}</w:t>')
            xml_content.append('      </w:r>')
            xml_content.append('    </w:p>')

        # Divider
        xml_content.append('    <w:p><w:pPr><w:spacing w:after="240"/><w:pBdr><w:bottom w:val="single" w:sz="6" w:space="4" w:color="3B82F6"/></w:pBdr></w:pPr></w:p>')

        # Table
        if checklist_items:
            xml_content.append('    <w:tbl>')
            xml_content.append('      <w:tblPr>')
            xml_content.append('        <w:tblStyle w:val="TableGrid"/>')
            xml_content.append('        <w:tblW w:w="5000" w:type="pct"/>')
            xml_content.append('        <w:tblBorders>')
            xml_content.append('          <w:top w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>')
            xml_content.append('          <w:left w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>')
            xml_content.append('          <w:bottom w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>')
            xml_content.append('          <w:right w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>')
            xml_content.append('          <w:insideH w:val="single" w:sz="4" w:space="0" w:color="E2E8F0"/>')
            xml_content.append('          <w:insideV w:val="single" w:sz="4" w:space="0" w:color="E2E8F0"/>')
            xml_content.append('        </w:tblBorders>')
            xml_content.append('      </w:tblPr>')

            # Header row
            xml_content.append('      <w:tr>')
            for th_name, w_val in [("ID", "600"), ("Category", "1400"), ("Requirement Item Name", "2200"), ("Action Required", "3000"), ("Type", "1200"), ("Ref", "1600")]:
                xml_content.append('        <w:tc>')
                xml_content.append(f'          <w:tcPr><w:tcW w:w="{w_val}" w:type="dxa"/><w:shd w:fill="F1F5F9"/></w:tcPr>')
                xml_content.append('          <w:p>')
                xml_content.append('            <w:pPr><w:spacing w:before="60" w:after="60"/></w:pPr>')
                xml_content.append('            <w:r>')
                xml_content.append('              <w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:b/><w:sz w:val="20"/></w:rPr>')
                xml_content.append(f'              <w:t>{xml_escape(th_name)}</w:t>')
                xml_content.append('            </w:r>')
                xml_content.append('          </w:p>')
                xml_content.append('        </w:tc>')
            xml_content.append('      </w:tr>')

            # Rows
            for item in checklist_items:
                no = item.get("id") or "—"
                cat = item.get("category") or "—"
                name = item.get("item_name") or "Unnamed Item"
                action = item.get("action_required") or "—"
                type_flag = item.get("status_flag") or "MANDATORY"
                ref = item.get("tender_reference") or "—"
                notes = item.get("notes") or ""

                type_color = "000000"
                if "MANDATORY" in str(type_flag).upper() or "SIGN" in str(type_flag).upper():
                    type_color = "B91C1C"

                xml_content.append('      <w:tr>')
                
                # Cells
                for val, color, is_bold in [
                    (str(no), "000000", True),
                    (str(cat), "4338CA", True),
                    (str(name), "000000", True),
                    (f"{action}\nNote: {notes}" if notes else str(action), "000000", False),
                    (str(type_flag), type_color, True),
                    (str(ref), "475569", False)
                ]:
                    xml_content.append('        <w:tc>')
                    xml_content.append('          <w:p>')
                    xml_content.append('            <w:pPr><w:spacing w:before="60" w:after="60"/></w:pPr>')
                    for part in val.split("\n"):
                        xml_content.append('            <w:r>')
                        xml_content.append('              <w:rPr>')
                        xml_content.append('                <w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/>')
                        if is_bold:
                            xml_content.append('                <w:b/>')
                        xml_content.append('                <w:sz w:val="20"/>')
                        if color != "000000":
                            xml_content.append(f'                <w:color w:val="{color}"/>')
                        xml_content.append('              </w:rPr>')
                        xml_content.append(f'              <w:t>{xml_escape(part)}</w:t>')
                        xml_content.append('            </w:r>')
                    xml_content.append('          </w:p>')
                    xml_content.append('        </w:tc>')

                xml_content.append('      </w:tr>')
            xml_content.append('    </w:tbl>')
        else:
            xml_content.append('    <w:p><w:r><w:t>No checklist items generated.</w:t></w:r></w:p>')

        xml_content.append('  </w:body>')
        xml_content.append('</w:document>')

        doc_xml = "\n".join(xml_content)
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(
                "[Content_Types].xml",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
                '  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
                '  <Default Extension="xml" ContentType="application/xml"/>\n'
                '  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>\n'
                '</Types>',
            )
            z.writestr(
                "_rels/.rels",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
                '  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>\n'
                '</Relationships>',
            )
            z.writestr("word/document.xml", doc_xml)
        out.seek(0)
        return send_file(
            out,
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            as_attachment=True,
            download_name="bid_submission_checklist.docx"
        )

    @app.post("/api/download-clarifications")
    @require_credits(CREDIT_COST_AI_DOWNLOAD, "download_clarifications", get_db_connection)
    def api_download_clarifications():
        import zipfile
        import io
        from datetime import datetime
        body = request.get_json(silent=True) or {}
        title = body.get("title") or "Unknown Tender"
        authority = body.get("authority") or "Unknown Authority"
        c = body.get("clarifications") or {}
        questions = c.get("questions") or []
        notes = c.get("strategic_notes") or ""

        def xml_escape(text: Any) -> str:
            s = str(text or "")
            return (
                s.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
                .replace("'", "&apos;")
            )

        xml_content = []
        xml_content.append('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>')
        xml_content.append('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">')
        xml_content.append('  <w:body>')

        # Heading
        xml_content.append('    <w:p>')
        xml_content.append('      <w:pPr><w:spacing w:before="240" w:after="120"/></w:pPr>')
        xml_content.append('      <w:r>')
        xml_content.append('        <w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:b/><w:sz w:val="36"/><w:color w:val="1E3A8A"/></w:rPr>')
        xml_content.append(f'        <w:t>Tender Clarification Questions</w:t>')
        xml_content.append('      </w:r>')
        xml_content.append('    </w:p>')

        # Meta info
        date_str = datetime.now().strftime("%d/%m/%Y")
        for label, val in [
            ("Tender Opportunity:", title),
            ("Contracting Authority:", authority),
            ("Export Date:", date_str),
            ("Clarification Deadline:", c.get("clarification_deadline", "—")),
            ("Submission Method:", c.get("submission_method", "—"))
        ]:
            xml_content.append('    <w:p>')
            xml_content.append('      <w:pPr><w:spacing w:after="60"/></w:pPr>')
            xml_content.append('      <w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:b/><w:sz w:val="20"/></w:rPr>')
            xml_content.append(f'        <w:t>{xml_escape(label)} </w:t>')
            xml_content.append('      </w:r>')
            xml_content.append('      <w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:sz w:val="20"/></w:rPr>')
            xml_content.append(f'        <w:t>{xml_escape(val)}</w:t>')
            xml_content.append('      </w:r>')
            xml_content.append('    </w:p>')

        # Divider
        xml_content.append('    <w:p><w:pPr><w:spacing w:after="240"/><w:pBdr><w:bottom w:val="single" w:sz="6" w:space="4" w:color="3B82F6"/></w:pBdr></w:pPr></w:p>')

        # Strategic Note
        if notes:
            xml_content.append('    <w:p><w:pPr><w:spacing w:before="120" w:after="120"/></w:pPr>')
            xml_content.append('      <w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:b/><w:sz w:val="20"/><w:color w:val="0F766E"/></w:rPr><w:t>Strategic Note: </w:t></w:r>')
            xml_content.append('      <w:r><w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:italic/><w:sz w:val="20"/><w:color w:val="0D9488"/></w:rPr>')
            xml_content.append(f'        <w:t>{xml_escape(notes)}</w:t>')
            xml_content.append('      </w:r>')
            xml_content.append('    </w:p>')

        # Table
        if questions:
            xml_content.append('    <w:tbl>')
            xml_content.append('      <w:tblPr>')
            xml_content.append('        <w:tblStyle w:val="TableGrid"/>')
            xml_content.append('        <w:tblW w:w="5000" w:type="pct"/>')
            xml_content.append('        <w:tblBorders>')
            xml_content.append('          <w:top w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>')
            xml_content.append('          <w:left w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>')
            xml_content.append('          <w:bottom w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>')
            xml_content.append('          <w:right w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>')
            xml_content.append('          <w:insideH w:val="single" w:sz="4" w:space="0" w:color="E2E8F0"/>')
            xml_content.append('          <w:insideV w:val="single" w:sz="4" w:space="0" w:color="E2E8F0"/>')
            xml_content.append('        </w:tblBorders>')
            xml_content.append('      </w:tblPr>')

            # Header row
            xml_content.append('      <w:tr>')
            for th_name, w_val in [("#", "500"), ("Priority", "900"), ("Category", "1100"), ("Suggested Question", "2800"), ("Reason / Impact", "2500"), ("Reference", "1200")]:
                xml_content.append('        <w:tc>')
                xml_content.append(f'          <w:tcPr><w:tcW w:w="{w_val}" w:type="dxa"/><w:shd w:fill="F1F5F9"/></w:tcPr>')
                xml_content.append('          <w:p>')
                xml_content.append('            <w:pPr><w:spacing w:before="60" w:after="60"/></w:pPr>')
                xml_content.append('            <w:r>')
                xml_content.append('              <w:rPr><w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/><w:b/><w:sz w:val="20"/></w:rPr>')
                xml_content.append(f'              <w:t>{xml_escape(th_name)}</w:t>')
                xml_content.append('            </w:r>')
                xml_content.append('          </w:p>')
                xml_content.append('        </w:tc>')
            xml_content.append('      </w:tr>')

            # Rows
            for idx, q in enumerate(questions):
                no = q.get("id") or str(idx + 1)
                prio = q.get("priority") or "MEDIUM"
                cat = q.get("category") or "General"
                q_text = q.get("question") or ""
                reason = q.get("reason_for_asking") or ""
                impact = q.get("impact_if_not_clarified") or ""
                ref = q.get("ambiguity_source") or "—"

                prio_color = "000000"
                prio_upper = str(prio).upper()
                if "CRITICAL" in prio_upper:
                    prio_color = "B91C1C"
                elif "HIGH" in prio_upper:
                    prio_color = "C2410C"
                elif "MEDIUM" in prio_upper:
                    prio_color = "2563EB"

                xml_content.append('      <w:tr>')
                
                # Cells
                for val, color, is_bold in [
                    (str(no), "000000", False),
                    (str(prio), prio_color, True),
                    (str(cat), "000000", False),
                    (str(q_text), "1E3A8A", True),
                    (f"Reason: {reason}\nImpact: {impact}", "000000", False),
                    (str(ref), "475569", False)
                ]:
                    xml_content.append('        <w:tc>')
                    xml_content.append('          <w:p>')
                    xml_content.append('            <w:pPr><w:spacing w:before="60" w:after="60"/></w:pPr>')
                    for part in val.split("\n"):
                        xml_content.append('            <w:r>')
                        xml_content.append('              <w:rPr>')
                        xml_content.append('                <w:rFonts w:ascii="Segoe UI" w:hAnsi="Segoe UI"/>')
                        if is_bold:
                            xml_content.append('                <w:b/>')
                        xml_content.append('                <w:sz w:val="20"/>')
                        if color != "000000":
                            xml_content.append(f'                <w:color w:val="{color}"/>')
                        xml_content.append('              </w:rPr>')
                        xml_content.append(f'              <w:t>{xml_escape(part)}</w:t>')
                        xml_content.append('            </w:r>')
                    xml_content.append('          </w:p>')
                    xml_content.append('        </w:tc>')

                xml_content.append('      </w:tr>')
            xml_content.append('    </w:tbl>')
        else:
            xml_content.append('    <w:p><w:r><w:t>No clarification questions generated.</w:t></w:r></w:p>')

        xml_content.append('  </w:body>')
        xml_content.append('</w:document>')

        doc_xml = "\n".join(xml_content)
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(
                "[Content_Types].xml",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
                '  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
                '  <Default Extension="xml" ContentType="application/xml"/>\n'
                '  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>\n'
                '</Types>',
            )
            z.writestr(
                "_rels/.rels",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
                '  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>\n'
                '</Relationships>',
            )
            z.writestr("word/document.xml", doc_xml)
        out.seek(0)
        return send_file(
            out,
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            as_attachment=True,
            download_name="bid_clarifications.docx"
        )

    # ── Enrich a section of an existing generated bid response ──────────────
    @app.post("/api/enrich-proposal")
    @require_credits(CREDIT_COST_ENRICH, "enrich_proposal", get_db_connection)
    def api_enrich_proposal():
        """Re-generate a single section of an existing bid response with AI enrichment."""
        import base64

        body = request.get_json(silent=True) or {}
        section      = (body.get("section") or "").strip()       # e.g. "cover_letter"
        instructions = (body.get("instructions") or "").strip()  # user's free-text instructions
        tender       = body.get("tender") or {}
        company      = (body.get("company") or "").strip()
        enrich_files = body.get("enrich_files") or []            # list of {filename, content (b64)}
        uploaded_files  = body.get("files") or []
        company_files   = body.get("company_files") or []

        source       = tender.get("source", "")
        resource_id  = tender.get("resource_id", "")
        title        = tender.get("title", "Unknown tender")
        username     = session.get("username", "admin")

        # Load user documents by ID and append to company_files
        user_document_ids = body.get("user_document_ids") or []
        profile_id = body.get("profile_id")
        if user_document_ids:
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                for doc_id in user_document_ids:
                    if profile_id:
                        cursor.execute(
                            "SELECT filename, file_path FROM company_profile_documents WHERE id = %s AND profile_id = %s AND username = %s",
                            (doc_id, int(profile_id), username)
                        )
                    else:
                        cursor.execute(
                            "SELECT filename, file_path FROM user_documents WHERE id = %s AND username = %s",
                            (doc_id, username)
                        )
                    row = cursor.fetchone()
                    if row:
                        fname, file_path = row
                        if file_path and os.path.exists(file_path):
                            with open(file_path, "rb") as f_in:
                                file_data = f_in.read()
                                if not any(cf.get("filename") == fname for cf in company_files):
                                    company_files.append({
                                        "filename": fname,
                                        "content": base64.b64encode(file_data).decode("utf-8")
                                    })
                cursor.close()
                conn.close()
            except Exception as e:
                print("Failed to load user documents for enrichment:", e)

        lang         = (body.get("lang") or request.args.get("lang", "en")).strip().lower()
        prop_type    = f"proposal_{lang}" if lang != "en" else "proposal"

        # Allowed enrichable sections and their display names
        ENRICHABLE_SECTIONS = {
            "cover_letter":                  "Draft Bid Covering Letter",
            "executive_summary":             "Executive Summary",
            "understanding_of_requirements": "Understanding of Scope & Requirements",
            "technical_methodology":         "Technical Methodology",
            "social_value":                  "Social Value Commitments",
            "relevant_experience":           "Relevant Experience",
            "pricing_notes":                 "Pricing Schedule & Commercial Commentary",
        }
        ITT_SECTION_PREFIX = "itt_question_"  # e.g. "itt_question_0"

        is_itt = section.startswith(ITT_SECTION_PREFIX)
        if section not in ENRICHABLE_SECTIONS and not is_itt:
            return jsonify({"error": f"Unknown section: {section}"}), 400

        # ── 1. Load existing proposal from DB cache ──────────────────────────
        files_payload = {"files": uploaded_files, "company_files": company_files}
        files_json    = json.dumps(files_payload, sort_keys=True)

        existing_proposal = None
        record_id = None
        try:
            conn   = get_db_connection()
            cursor = conn.cursor()
            ph = "%s"
            cursor.execute(
                f"SELECT id, analysis_json FROM analyses WHERE username={ph} AND source={ph} AND resource_id={ph} AND company={ph} AND type={ph} ORDER BY id DESC LIMIT 1",
                (username, source, resource_id, company, prop_type)
            )
            row = cursor.fetchone()
            if not row:
                cursor.execute(
                    f"SELECT id, analysis_json FROM analyses WHERE username={ph} AND source={ph} AND resource_id={ph} AND type={ph} ORDER BY id DESC LIMIT 1",
                    (username, source, resource_id, prop_type)
                )
                row = cursor.fetchone()
            cursor.close()
            conn.close()
            if row:
                record_id = row[0]
                existing_proposal = json.loads(row[1])
        except Exception as e:
            print("Failed to load proposal from cache for enrichment:", e)

        if not existing_proposal:
            return jsonify({"error": "No existing proposal found. Generate a bid response first."}), 404

        # ── 2. Extract current section text ──────────────────────────────────
        if is_itt:
            try:
                q_index = int(section[len(ITT_SECTION_PREFIX):])
            except ValueError:
                return jsonify({"error": "Invalid ITT question index."}), 400
            itt_questions = (existing_proposal.get("bid_response") or {}).get("itt_question_responses") or []
            if q_index < 0 or q_index >= len(itt_questions):
                return jsonify({"error": f"ITT question index {q_index} out of range."}), 400
            current_section_text  = itt_questions[q_index].get("response", "")
            section_display_name  = f"ITT Question {q_index + 1}: {itt_questions[q_index].get('question_text', '')[:100]}"
        else:
            bid_response_block   = existing_proposal.get("bid_response") or {}
            section_obj          = bid_response_block.get(section) or {}
            current_section_text = section_obj.get("content", "")
            section_display_name = ENRICHABLE_SECTIONS[section]

        # ── 3. Parse enrichment documents ────────────────────────────────────
        enrich_doc_text = ""
        for f in enrich_files:
            fname    = f.get("filename") or "enrichment_doc"
            b64_data = f.get("content")  or ""
            if b64_data:
                try:
                    from etenders_scraper.cft_documents import _extract_bytes
                    file_bytes = base64.b64decode(b64_data)
                    text = _extract_bytes(file_bytes, fname, max_chars=60000)
                    if text.strip():
                        enrich_doc_text += f"\n\n=== Enrichment Document: {fname} ===\n{text}"
                except Exception as ex:
                    enrich_doc_text += f"\n\n[Could not parse {fname}: {ex}]"

        # ── 4. Call DeepSeek to enrich the section ───────────────────────────
        system_prompt = (
            "You are a professional bid writer and tender specialist. "
            "Your task is to rewrite and improve a single section of an existing bid response "
            "based on the user's specific instructions. "
            "Return ONLY valid JSON in the exact schema: {\"enriched_content\": \"<full rewritten section text>\"} "
            "with no markdown fences, no extra keys."
        )
        user_prompt = (
            f"You are improving the following section of a bid response document:\n\n"
            f"SECTION: {section_display_name}\n"
            f"TENDER: {title}\n"
            f"COMPANY: {company or 'Bidding Company'}\n\n"
            f"CURRENT SECTION TEXT:\n{current_section_text}\n\n"
            f"USER ENRICHMENT INSTRUCTIONS:\n"
            f"{instructions if instructions else 'Improve the quality, detail and persuasiveness of this section.'}"
        )
        if enrich_doc_text:
            user_prompt += f"\n\nADDITIONAL CONTEXT DOCUMENTS:{enrich_doc_text[:60000]}"
        user_prompt += (
            "\n\nIMPORTANT: Maintain the same approximate length or longer. "
            "Use [PLACEHOLDER: ...] tags for any company-specific details still to be filled in. "
            "Return ONLY valid JSON: {\"enriched_content\": \"<full new text>\"}"
        )

        try:
            # Shared dispatcher: honours the user's chosen provider / BYO key and repairs bad JSON.
            enriched_data = call_chat_json(system=system_prompt, user=user_prompt, max_tokens=4000, temperature=0.35)
            enriched_text = enriched_data.get("enriched_content", "")
            if not enriched_text:
                return jsonify({"error": "AI returned empty enriched content."}), 500
        except Exception as ai_err:
            print("Enrichment AI call failed:", ai_err)
            return jsonify({"error": f"AI enrichment failed: {str(ai_err)}"}), 500

        # ── 5. Patch the proposal with enriched content ───────────────────────
        if is_itt:
            existing_proposal["bid_response"]["itt_question_responses"][q_index]["response"]   = enriched_text
            existing_proposal["bid_response"]["itt_question_responses"][q_index]["word_count"] = len(enriched_text.split())
        else:
            if "bid_response" not in existing_proposal:
                existing_proposal["bid_response"] = {}
            if section not in existing_proposal["bid_response"]:
                existing_proposal["bid_response"][section] = {}
            existing_proposal["bid_response"][section]["content"]    = enriched_text
            existing_proposal["bid_response"][section]["word_count"] = len(enriched_text.split())

        # ── 6. Write updated proposal back to DB ─────────────────────────────
        try:
            conn   = get_db_connection()
            cursor = conn.cursor()
            ph     = "%s"
            if record_id is not None:
                cursor.execute(
                    f"DELETE FROM analyses WHERE id={ph}",
                    (record_id,)
                )
            else:
                cursor.execute(
                    f"DELETE FROM analyses WHERE username={ph} AND source={ph} AND resource_id={ph} AND company={ph} AND type={ph}",
                    (username, source, resource_id, company, prop_type)
                )
            cursor.execute(
                f"INSERT INTO analyses (username, source, resource_id, tender_title, company, files_json, type, analysis_json) "
                f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})",
                (username, source, resource_id, title, company, files_json, prop_type, json.dumps(existing_proposal))
            )
            conn.commit()
            cursor.close()
            conn.close()
        except Exception as db_err:
            print("Failed to save enriched proposal:", db_err)

        return jsonify({
            "ok": True,
            "section": section,
            "enriched_content": enriched_text,
            "proposal": existing_proposal,
        })

    # ── Extract Gantt phases from methodology text ───────────────────────────
    @app.post("/api/extract-gantt")
    @require_credits(CREDIT_COST_GANTT, "extract_gantt", get_db_connection)
    def api_extract_gantt():
        """Use AI to extract project phases from the bid's technical methodology section."""
        body        = request.get_json(silent=True) or {}
        tender      = body.get("tender") or {}
        company     = (body.get("company") or "").strip()
        source      = tender.get("source", "")
        resource_id = tender.get("resource_id", "")
        title       = tender.get("title", "Unknown tender")
        username    = session.get("username", "admin")
        lang        = (body.get("lang") or "en").strip().lower()
        prop_type   = f"proposal_{lang}" if lang != "en" else "proposal"
        unit_hint   = (body.get("unit") or "auto").strip().lower()

        # Load cached proposal
        existing_proposal = None
        try:
            conn   = get_db_connection()
            cursor = conn.cursor()
            ph = "%s"
            cursor.execute(
                f"SELECT analysis_json FROM analyses WHERE username={ph} AND source={ph} AND resource_id={ph} AND company={ph} AND type={ph} ORDER BY id DESC LIMIT 1",
                (username, source, resource_id, company, prop_type)
            )
            row = cursor.fetchone()
            if not row:
                cursor.execute(
                    f"SELECT analysis_json FROM analyses WHERE username={ph} AND source={ph} AND resource_id={ph} AND type={ph} ORDER BY id DESC LIMIT 1",
                    (username, source, resource_id, prop_type)
                )
                row = cursor.fetchone()
            cursor.close()
            conn.close()
            if row:
                existing_proposal = json.loads(row[0])
        except Exception as e:
            print("extract-gantt: DB load failed:", e)

        if not existing_proposal:
            return jsonify({"error": "No existing proposal found."}), 404

        methodology_text = (
            (existing_proposal.get("bid_response") or {})
            .get("technical_methodology", {})
            .get("content", "")
        )
        if not methodology_text:
            return jsonify({"error": "No technical methodology content found in the proposal."}), 400

        dissection = existing_proposal.get("dissection") or {}
        duration_str = str(dissection.get("contract_duration", "")).strip()
        submission_deadline = (dissection.get("submission_deadline") or {}).get("date", "")

        # ── Robustly parse contract duration to a numeric value ──────────────
        import re as _re
        def _parse_duration(s):
            """Return (numeric_value, unit) from a free-text duration string."""
            s = s.lower()
            # Years first (convert to months)
            m = _re.search(r'(\d+(?:\.\d+)?)\s*year', s)
            if m:
                years = float(m.group(1))
                # Check for extension months
                ext = _re.search(r'extend.*?(\d+)\s*month', s)
                ext_m = int(ext.group(1)) if ext else 0
                return int(years * 12) + ext_m, "months"
            # Months
            m = _re.search(r'(\d+)\s*month', s)
            if m:
                return int(m.group(1)), "months"
            # Weeks
            m = _re.search(r'(\d+)\s*week', s)
            if m:
                return int(m.group(1)), "weeks"
            return None, None

        parsed_duration, parsed_unit = _parse_duration(duration_str)

        if unit_hint in ("weeks", "months"):
            unit = unit_hint
        elif parsed_unit:
            unit = parsed_unit
        elif any(w in duration_str.lower() for w in ["year", "month", "12", "24", "36", "48", "60"]):
            unit = "months"
        else:
            unit = "weeks"

        # Set max_units from parsed duration, or sensible defaults
        if parsed_duration and parsed_unit == unit:
            max_units = parsed_duration
        elif parsed_duration and parsed_unit == "months" and unit == "weeks":
            max_units = parsed_duration * 4
        else:
            max_units = 12 if unit == "months" else 16

        # Estimate contract start date (~4-6 weeks after submission deadline = award period)
        contract_start_note = ""
        if submission_deadline:
            contract_start_note = (
                f"Submission deadline: {submission_deadline}. "
                f"Assume contract start is approximately 4-6 weeks after submission deadline (award period). "
            )

        system_prompt = (
            "You are a senior project planning expert with 15 years of UK public sector procurement experience. "
            f"Extract a detailed, accurate project delivery timeline. Use {unit} as the time unit. "
            "CRITICAL RULES:\n"
            "1. Phases must be SEQUENTIAL with no gaps — each phase start = previous phase start + previous phase duration\n"
            "2. Phase 1 always starts at start=1\n"
            f"3. Total timeline must fit within {max_units} {unit}\n"
            "4. Duration must reflect real effort (not all phases equal length)\n"
            "5. Return ONLY valid JSON — no markdown, no extra keys:\n"
            '{"unit":"' + unit + f'","total_{unit}":{max_units},"phases":['
            '{"id":1,"label":"Phase Name","start":1,"duration":2,"assignee":"Role","colour":"#3b82f6","is_milestone":false}]}}'
        )
        user_prompt = (
            f"Tender: {title}\n"
            f"Contract duration: {duration_str or 'not specified'} → use {max_units} {unit} total\n"
            f"{contract_start_note}"
            f"Time unit: {unit}\n\n"
            f"TECHNICAL METHODOLOGY:\n{methodology_text[:8000]}\n\n"
            f"Extract 5-8 realistic delivery phases. Each phase MUST start immediately after the previous ends. "
            f"Use this colour palette (vary colours): #3b82f6 #10b981 #f59e0b #8b5cf6 #ef4444 #06b6d4 #f97316 #84cc16\n"
            f"Return JSON only."
        )

        try:
            gantt_data = call_chat_json(system=system_prompt, user=user_prompt, max_tokens=1500, temperature=0.1)
            phases = gantt_data.get("phases", [])
            gantt_unit = gantt_data.get("unit", unit)
            if not phases:
                return jsonify({"error": "AI returned no phases."}), 500

            # Validate and auto-fix sequential ordering
            cursor_pos = 1
            for p in phases:
                p["start"] = cursor_pos
                p["duration"] = max(1, int(p.get("duration") or 1))
                cursor_pos += p["duration"]

        except Exception as e:
            print("extract-gantt AI error:", e)
            return jsonify({"error": f"AI extraction failed: {e}"}), 500

        # Save gantt to proposal cache
        gantt_payload = {
            "unit": gantt_unit,
            "phases": phases,
            "total_units": max_units,
            "contract_duration_str": duration_str,
            "submission_deadline": submission_deadline,
        }
        existing_proposal["gantt"] = gantt_payload
        try:
            conn   = get_db_connection()
            cursor = conn.cursor()
            ph = "%s"
            cursor.execute(
                f"UPDATE analyses SET analysis_json={ph} WHERE username={ph} AND source={ph} AND resource_id={ph} AND company={ph} AND type={ph}",
                (json.dumps(existing_proposal), username, source, resource_id, company, prop_type)
            )
            conn.commit()
            cursor.close()
            conn.close()
        except Exception as e:
            print("extract-gantt: DB save failed:", e)

        return jsonify({
            "ok": True,
            "unit": gantt_unit,
            "phases": phases,
            "total_units": max_units,
            "contract_duration_str": duration_str,
            "submission_deadline": submission_deadline,
        })

    # ── Save edited Gantt phases (free — no AI call) ─────────────────────────
    @app.post("/api/save-gantt")
    def api_save_gantt():
        """Persist user-edited Gantt JSON back to DB. No credits consumed."""
        if not session.get("username"):
            return jsonify({"error": "Not authenticated"}), 401

        body        = request.get_json(silent=True) or {}
        tender      = body.get("tender") or {}
        company     = (body.get("company") or "").strip()
        source      = tender.get("source", "")
        resource_id = tender.get("resource_id", "")
        username    = session.get("username", "admin")
        lang        = (body.get("lang") or "en").strip().lower()
        prop_type   = f"proposal_{lang}" if lang != "en" else "proposal"
        phases      = body.get("phases") or []
        unit        = (body.get("unit") or "weeks").strip()

        try:
            conn   = get_db_connection()
            cursor = conn.cursor()
            ph = "%s"
            cursor.execute(
                f"SELECT id, analysis_json FROM analyses WHERE username={ph} AND source={ph} AND resource_id={ph} AND company={ph} AND type={ph} ORDER BY id DESC LIMIT 1",
                (username, source, resource_id, company, prop_type)
            )
            row = cursor.fetchone()
            if not row:
                cursor.execute(
                    f"SELECT id, analysis_json FROM analyses WHERE username={ph} AND source={ph} AND resource_id={ph} AND type={ph} ORDER BY id DESC LIMIT 1",
                    (username, source, resource_id, prop_type)
                )
                row = cursor.fetchone()
            if not row:
                cursor.close()
                conn.close()
                return jsonify({"error": "No cached proposal found."}), 404
            record_id = row[0]
            proposal = json.loads(row[1])
            proposal["gantt"] = {"unit": unit, "phases": phases}
            cursor.execute(
                f"UPDATE analyses SET analysis_json={ph} WHERE id={ph}",
                (json.dumps(proposal), record_id)
            )
            conn.commit()
            cursor.close()
            conn.close()
        except Exception as e:
            print("save-gantt DB error:", e)
            return jsonify({"error": str(e)}), 500

        return jsonify({"ok": True})

    # ── AI Methodology Diagram Generator ─────────────────────────────────────
    @app.post("/api/generate-methodology")
    @require_credits(CREDIT_COST_METHODOLOGY, "generate_methodology", get_db_connection)
    def api_generate_methodology():
        """Use DeepSeek to analyse the bid methodology and produce a tailored diagram.

        Returns: { ok, style, title, phases:[{label, icon, colour, description}] }
        """
        body        = request.get_json(silent=True) or {}
        tender      = body.get("tender") or {}
        company     = (body.get("company") or "").strip()
        source      = tender.get("source", "")
        resource_id = tender.get("resource_id", "")
        title       = tender.get("title", "Unknown tender")
        username    = session.get("username", "admin")
        lang        = (body.get("lang") or "en").strip().lower()
        prop_type   = f"proposal_{lang}" if lang != "en" else "proposal"
        forced_style = (body.get("forced_style") or "").strip()

        # Load cached proposal for methodology text
        existing_proposal = None
        try:
            conn   = get_db_connection()
            cursor = conn.cursor()
            ph = "%s"
            cursor.execute(
                f"SELECT analysis_json FROM analyses WHERE username={ph} AND source={ph} AND resource_id={ph} AND company={ph} AND type={ph} ORDER BY id DESC LIMIT 1",
                (username, source, resource_id, company, prop_type)
            )
            row = cursor.fetchone()
            if not row:
                cursor.execute(
                    f"SELECT analysis_json FROM analyses WHERE username={ph} AND source={ph} AND resource_id={ph} AND type={ph} ORDER BY id DESC LIMIT 1",
                    (username, source, resource_id, prop_type)
                )
                row = cursor.fetchone()
            cursor.close()
            conn.close()
            if row:
                existing_proposal = json.loads(row[0])
        except Exception as e:
            print("generate-methodology: DB load failed:", e)

        if not existing_proposal:
            return jsonify({"error": "No existing proposal found."}), 404

        methodology_text = (
            (existing_proposal.get("bid_response") or {})
            .get("technical_methodology", {})
            .get("content", "")
        )
        scope_summary = (
            (existing_proposal.get("dissection") or {})
            .get("scope_of_work", {})
            .get("summary", "")
        )
        if not methodology_text:
            return jsonify({"error": "No technical methodology content found in the proposal."}), 400

        system_prompt = (
            "You are an expert bid writer and project delivery specialist with 15 years experience in UK/Ireland public sector procurement. "
            + (f"The user has SPECIFICALLY REQUESTED the '{forced_style}' delivery framework — you MUST use this framework. " if forced_style else
               "Analyse the provided tender methodology text and determine the SINGLE best delivery framework. ")
            + "Available frameworks: 'agile' (iterative sprints), 'waterfall' (sequential gates), 'linear' (step-by-step flow), "
            "'pdca' (Plan-Do-Check-Act spiral), 'prince2' (structured stage gates), 'hub_spoke' (central hub with branches), "
            "'vmodel' (test-driven V shape), 'mindmap' (radial/mind map structure). "
            "Extract 4–8 key delivery phases/stages that match the actual methodology described. "
            "Return ONLY valid JSON: "
            '{"style":"linear","diagram_title":"string (10 words max)","phases":['
            '{"label":"string (3 words max)","icon":"single emoji","colour":"hex colour","description":"string (8 words max)"}]}'
            " Use a visually distinct colour palette. No markdown, no extra keys."
        )
        user_prompt = (
            f"Tender: {title}\n"
            f"Scope summary: {scope_summary[:500] if scope_summary else 'Not provided'}\n\n"
            f"TECHNICAL METHODOLOGY:\n{methodology_text[:6000]}\n\n"
            "Analyse the above and return the best framework + phases as JSON."
        )

        try:
            meth_data = call_chat_json(system=system_prompt, user=user_prompt, max_tokens=800, temperature=0.2)
            phases = meth_data.get("phases", [])
            if not phases:
                return jsonify({"error": "AI returned no phases."}), 500
            return jsonify({
                "ok": True,
                "style":         meth_data.get("style", "linear"),
                "diagram_title": meth_data.get("diagram_title", "Delivery Methodology"),
                "phases":        phases
            })
        except Exception as e:
            print("generate-methodology AI error:", e)
            return jsonify({"error": f"AI generation failed: {e}"}), 500

    if ENABLE_EMAIL_SCHEDULER:
        try:
            init_email_scheduler(get_db_connection, interval_minutes=60)
        except Exception as _ex_sched:
            print("Failed to start email scheduler:", _ex_sched)

    return app





def _extract_required_fields(html: str) -> dict[str, str]:
    """Parse detail page using the canonical parser, then keep only the fields we expose."""
    all_fields = parse_detail_fields(html)

    # Always include every DETAIL_KEY (blank string if absent) so the
    # frontend/export always gets a consistent set of keys.
    result: dict[str, str] = {}
    for key in DETAIL_KEYS:
        result[key] = str(all_fields.get(key) or "")

    # Also carry through a few extra keys the canonical parser may produce
    # under different names (aliases used in fields.py).
    for src, dst in (
        ("name_of_contracting_authority", "contracting_authority"),
        ("time_limit_for_receipt_of_tenders_or_requests_to_participate", "submission_deadline"),
    ):
        if all_fields.get(src) and not result.get(dst):
            result[dst] = str(all_fields[src])

    return result





if __name__ == "__main__":
    import os
    app = create_app()
    port = int(os.environ.get("PORT", 8092))
    print("Multi-portal tender search API v", API_VERSION)
    print("Sources:", ", ".join(SOURCES.keys()))
    print(f"Serving on http://0.0.0.0:{port}  (run: python server.py)")
    # Flask's own app.run() is single-threaded (its startup banner says as much: "do not use in
    # a production deployment") -- it can only handle one HTTP request at a time, which let a
    # slower poll response for a progressive search arrive and render after a newer one already
    # had, silently leaving a stale result on screen with no error. waitress (already a dependency)
    # serves on one process but multiple threads, so _JOBS/SEARCH_CACHE/_JOB_BY_CACHE_KEY -- all
    # plain in-memory dicts -- keep working exactly as before; a multi-process server instead would
    # scatter that state across processes and break progressive-search job lookups entirely.
    from waitress import serve
    serve(app, host="0.0.0.0", port=port, threads=8)

