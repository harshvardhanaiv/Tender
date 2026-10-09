"""Centralised environment configuration."""
from __future__ import annotations

import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent

# Credit costs (tunable via env)
CREDIT_COST_SEARCH = int(os.environ.get("CREDIT_COST_SEARCH", "1"))
CREDIT_COST_FIT_SCORE = int(os.environ.get("CREDIT_COST_FIT_SCORE", "1"))
CREDIT_COST_SUMMARY = int(os.environ.get("CREDIT_COST_SUMMARY", "3"))
CREDIT_COST_ANALYSE = int(os.environ.get("CREDIT_COST_ANALYSE", "6"))
CREDIT_COST_PROPOSAL = int(os.environ.get("CREDIT_COST_PROPOSAL", "10"))
CREDIT_COST_AI_DOWNLOAD = int(os.environ.get("CREDIT_COST_AI_DOWNLOAD", "3"))
CREDIT_COST_ENRICH = int(os.environ.get("CREDIT_COST_ENRICH", "3"))
CREDIT_COST_GANTT  = int(os.environ.get("CREDIT_COST_GANTT",  "2"))
CREDIT_COST_METHODOLOGY = int(os.environ.get("CREDIT_COST_METHODOLOGY", "2"))
# Growth Studio: an AI-written first draft of an outreach message. Signals, template drafts, exports
# and campaign tracking are free. 0 makes the AI draft free too (require_credits skips a cost <= 0).
CREDIT_COST_GROWTH_DRAFT = int(os.environ.get("CREDIT_COST_GROWTH_DRAFT", "2"))
# Planning Leads is free -- no credit charge for keyword/filter searches, paging, or the
# unfiltered default list. cost <= 0 makes require_credits() skip charging entirely (see
# metering.py) and also zeroes the "credit_cost_per_search" the frontend uses to decide
# whether to show its pre-search "this will use credits" confirmation, so both sides of
# that behaviour stay in sync from this one setting. Still overridable via env var if
# planning search is ever priced again.
CREDIT_COST_PLANNING_SEARCH = int(os.environ.get("CREDIT_COST_PLANNING_SEARCH", "0"))

# Planning Leads (etenders_scraper/planning). PLANNING_SOURCE selects the data adapter.
# PLANNING_MIN_PLAN restricts the module to a plan tier and above (starter < standard <
# advance; professional/business are aliases). Empty = every signed-in user with credits.
PLANNING_SOURCE = os.environ.get("PLANNING_SOURCE", "planit")
PLANNING_MIN_PLAN = os.environ.get("PLANNING_MIN_PLAN", "").strip().lower()


FREE_TRIAL_CREDITS = int(os.environ.get("FREE_TRIAL_CREDITS", "25"))
LOW_CREDIT_THRESHOLD = int(os.environ.get("LOW_CREDIT_THRESHOLD", "10"))

# Firebase
FIREBASE_PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID", "")
FIREBASE_CREDENTIALS_JSON = os.environ.get("FIREBASE_CREDENTIALS_JSON", "")
FIREBASE_CREDENTIALS_PATH = os.environ.get("FIREBASE_CREDENTIALS_PATH", "")
FIREBASE_WEB_API_KEY = os.environ.get("FIREBASE_WEB_API_KEY", "")
FIREBASE_AUTH_DOMAIN = os.environ.get("FIREBASE_AUTH_DOMAIN", "")
FIREBASE_APP_ID = os.environ.get("FIREBASE_APP_ID", "")

# Stripe
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
def get_app_base_url() -> str:
    try:
        from flask import request
        if request and getattr(request, "host_url", None):
            return request.host_url.rstrip("/")
    except Exception:
        pass

    raw = (os.environ.get("APP_BASE_URL") or os.environ.get("APP_HOST") or "https://tender.civenta.co.uk").strip().rstrip("/")
    if not raw.startswith("http://") and not raw.startswith("https://"):
        if "localhost" in raw or "127.0.0.1" in raw:
            raw = f"http://{raw}"
        else:
            raw = f"https://{raw}"
    return raw

APP_BASE_URL = get_app_base_url()


# Stripe Product & Price IDs
STRIPE_PRODUCT_STANDARD = os.environ.get("STRIPE_PRODUCT_STANDARD", "prod_V7PjTprComHpxP")
STRIPE_PRICE_STANDARD_MONTHLY = os.environ.get("STRIPE_PRICE_STANDARD_MONTHLY", "price_1TifAq10dFB3DskbrWiRhvHG")
STRIPE_PRODUCT_ADVANCE = os.environ.get("STRIPE_PRODUCT_ADVANCE", "prod_V7PTL1oS7NpxdD")
STRIPE_PRICE_ADVANCE_MONTHLY = os.environ.get("STRIPE_PRICE_ADVANCE_MONTHLY", "price_1TifAs10dFB3DskbwgpsqEOr")

PLAN_CREDITS = {
    "standard": 500,
    "advance": 1500,
    "starter": 150,
    "professional": 500,
    "business": 1500,
}
PACK_CREDITS = {
    "pack_100": 100,
    "pack_300": 300,
    "pack_1000": 1000,
}

# Security
ADMIN_EMAILS = {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}
SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "0") == "1"
# Server-enforced idle timeout: the session cookie itself lasts 7 days (PERMANENT_SESSION_LIFETIME
# in server.py) so a browser that never fully quits stays "logged in" indefinitely — this is what
# actually signs an inactive user out, independent of how long the cookie itself is valid for.
SESSION_IDLE_TIMEOUT_SECONDS = int(os.environ.get("SESSION_IDLE_TIMEOUT_MINUTES", "30")) * 60
RATE_LIMIT_LOGIN = int(os.environ.get("RATE_LIMIT_LOGIN", "10"))
RATE_LIMIT_SEARCH = int(os.environ.get("RATE_LIMIT_SEARCH", "30"))
RATE_LIMIT_AI = int(os.environ.get("RATE_LIMIT_AI", "20"))

# Email (optional — Postmark/SES/SMTP)
EMAIL_FROM = os.environ.get("EMAIL_FROM") or os.environ.get("SMTP_USER", "")
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD") or os.environ.get("SMTP_PASS", "")
SMTP_FROM_NAME = os.environ.get("SMTP_FROM_NAME", "TenderFlow")

# Sentry
SENTRY_DSN = os.environ.get("SENTRY_DSN", "")

# Buyer workspace: the Market Radar and Market Engagement pages for public sector buyers
# (web/buyer-workspace.html and the /api/market-radar, /api/buyer-workspace, /api/market-engagement
# routes). Set to 0 to hide it entirely: the routes answer 404 and the menu link is not shown.
ENABLE_BUYER_WORKSPACE = os.environ.get("ENABLE_BUYER_WORKSPACE", "1") == "1"

# Market Radar "Insights" drawer: a per-buyer profile plus a short plain-language DeepSeek summary of the
# buyer's procurement pattern (tender_app/buyer_insights.py). Set to 0 to hide it entirely: the Insights
# button is not shown and its routes answer 404. The factual profile never needs the AI; the narrative
# needs DEEPSEEK_API_KEY and is simply left out ("insight unavailable") when the key is missing or the
# call fails. A narrative is only attempted for a buyer with at least BUYER_INSIGHT_MIN_AWARDS awards.
ENABLE_BUYER_INSIGHTS = os.environ.get("ENABLE_BUYER_INSIGHTS", "1") == "1"
BUYER_INSIGHT_TIMEOUT_SECONDS = float(os.environ.get("BUYER_INSIGHT_TIMEOUT_SECONDS", "25"))
BUYER_INSIGHT_MIN_AWARDS = int(os.environ.get("BUYER_INSIGHT_MIN_AWARDS", "5"))

# Company profile drawer (Market Radar): Companies House filings and owners, plus the Google rating.
# Each source needs its own key and is reported as "not connected" without one; none is guessed.
COMPANIES_HOUSE_API_KEY = os.environ.get("COMPANIES_HOUSE_API_KEY", "")
GOOGLE_PLACES_API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY", "")
COMPANY_PROFILE_TIMEOUT_SECONDS = float(os.environ.get("COMPANY_PROFILE_TIMEOUT_SECONDS", "6"))

# Growth Studio: outreach signals and campaigns for suppliers (web/growth-studio.js and the
# /api/growth routes). Set to 0 to hide it entirely: the routes and its files answer 404 and the
# sidebar item is not shown.
ENABLE_GROWTH_STUDIO = os.environ.get("ENABLE_GROWTH_STUDIO", "1") == "1"

# Background schedulers (deadline emails, award scraping); docker-compose.yml turns them off for local dev
ENABLE_SCHEDULERS = os.environ.get("ENABLE_SCHEDULERS", "1") == "1"
# The email digest scheduler alone, independent of ENABLE_SCHEDULERS -- lets local dev run
# automated email notifications without also starting the award/planning scrapers, which hit
# live external portals on every container start. Defaults to ENABLE_SCHEDULERS when unset, so
# production (which never sets this) is unaffected.
ENABLE_EMAIL_SCHEDULER = os.environ.get("ENABLE_EMAIL_SCHEDULER", "1" if ENABLE_SCHEDULERS else "0") == "1"
