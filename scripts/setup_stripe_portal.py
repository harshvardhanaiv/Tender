"""Enable plan switching in Stripe Customer Portal.

Usage:
  python scripts/setup_stripe_portal.py

Prints STRIPE_PORTAL_CONFIGURATION_ID for .env
"""
from __future__ import annotations

import os
import sys

try:
    import stripe
except ImportError:
    print("pip install stripe", file=sys.stderr)
    raise SystemExit(1)

SUBSCRIPTION_ENV_KEYS = [
    "STRIPE_PRICE_STARTER_MONTHLY",
    "STRIPE_PRICE_STARTER_ANNUAL",
    "STRIPE_PRICE_PRO_MONTHLY",
    "STRIPE_PRICE_PRO_ANNUAL",
    "STRIPE_PRICE_BUSINESS_MONTHLY",
    "STRIPE_PRICE_BUSINESS_ANNUAL",
]


def _load_key() -> str:
    key = (os.environ.get("STRIPE_SECRET_KEY") or "").strip()
    if key:
        return key
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("STRIPE_SECRET_KEY="):
                return line.split("=", 1)[1].strip()
    raise SystemExit("STRIPE_SECRET_KEY not found")


def _load_env(key: str) -> str:
    val = (os.environ.get(key) or "").strip()
    if val:
        return val
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip()
    return ""


def _portal_products() -> list[dict]:
    by_product: dict[str, list[str]] = {}
    for env_key in SUBSCRIPTION_ENV_KEYS:
        price_id = _load_env(env_key)
        if not price_id:
            continue
        price = stripe.Price.retrieve(price_id)
        prod_id = price.product if isinstance(price.product, str) else price.product.id
        by_product.setdefault(prod_id, []).append(price_id)
    return [{"product": pid, "prices": prices} for pid, prices in by_product.items()]


def _stripe_get(obj, key: str):
    if not obj:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    try:
        return obj[key]
    except (KeyError, TypeError):
        return getattr(obj, key, None)


def _find_existing_config():
    configs = stripe.billing_portal.Configuration.list(limit=20)
    for cfg in configs.auto_paging_iter():
        if _stripe_get(cfg.metadata, "tenderflow") == "1":
            return cfg
    return None


def main() -> None:
    stripe.api_key = _load_key()
    products = _portal_products()
    if not products:
        raise SystemExit("No subscription price IDs found in .env")

    features = {
        "subscription_update": {
            "enabled": True,
            "default_allowed_updates": ["price"],
            "proration_behavior": "create_prorations",
            "products": products,
        },
        "subscription_cancel": {
            "enabled": True,
            "mode": "at_period_end",
        },
        "payment_method_update": {"enabled": True},
        "invoice_history": {"enabled": True},
        "customer_update": {
            "enabled": True,
            "allowed_updates": ["email", "address"],
        },
    }

    existing = _find_existing_config()
    if existing:
        cfg = stripe.billing_portal.Configuration.modify(existing.id, features=features)
        print(f"Updated portal configuration {cfg.id}")
    else:
        cfg = stripe.billing_portal.Configuration.create(
            features=features,
            metadata={"tenderflow": "1"},
            business_profile={
                "privacy_policy_url": f"{_load_env('APP_BASE_URL') or 'http://localhost:8092'}/legal/privacy.html",
                "terms_of_service_url": f"{_load_env('APP_BASE_URL') or 'http://localhost:8092'}/legal/terms.html",
            },
        )
        print(f"Created portal configuration {cfg.id}")

    print(f"\n# Add to .env:\nSTRIPE_PORTAL_CONFIGURATION_ID={cfg.id}")
    print("\nRestart Docker, then open Manage subscription again — you should see Update plan.")


if __name__ == "__main__":
    main()
