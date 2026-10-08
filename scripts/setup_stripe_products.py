"""Create TenderFlow products/prices in Stripe and print .env lines.

Each subscription tier is ONE Stripe product with monthly + annual prices
(required for correct proration when switching billing interval in the portal).

Usage:
  python scripts/setup_stripe_products.py
"""
from __future__ import annotations

import os
import sys

try:
    import stripe
except ImportError:
    print("Install stripe: pip install stripe", file=sys.stderr)
    raise SystemExit(1)

# (product name, plan metadata key, [(env var, pence, interval), ...])
SUBSCRIPTIONS = [
    ("TenderFlow Starter", "starter", [
        ("STRIPE_PRICE_STARTER_MONTHLY", 2900, "month"),
        ("STRIPE_PRICE_STARTER_ANNUAL", 29000, "year"),
    ]),
    ("TenderFlow Professional", "professional", [
        ("STRIPE_PRICE_PRO_MONTHLY", 7900, "month"),
        ("STRIPE_PRICE_PRO_ANNUAL", 79000, "year"),
    ]),
    ("TenderFlow Business", "business", [
        ("STRIPE_PRICE_BUSINESS_MONTHLY", 19900, "month"),
        ("STRIPE_PRICE_BUSINESS_ANNUAL", 199000, "year"),
    ]),
]

PACKS = [
    ("TenderFlow 100 Credits", "STRIPE_PRICE_PACK_100", 2500),
    ("TenderFlow 300 Credits", "STRIPE_PRICE_PACK_300", 6500),
    ("TenderFlow 1,000 Credits", "STRIPE_PRICE_PACK_1000", 18000),
]


def _load_key() -> str:
    key = (os.environ.get("STRIPE_SECRET_KEY") or "").strip()
    if key:
        return key
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.isfile(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("STRIPE_SECRET_KEY="):
                    return line.split("=", 1)[1].strip()
    print("STRIPE_SECRET_KEY not set", file=sys.stderr)
    raise SystemExit(1)


def _stripe_get(obj, key: str):
    if not obj:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    try:
        return obj[key]
    except (KeyError, TypeError):
        return getattr(obj, key, None)


def _find_product_by_plan(plan_key: str):
    products = stripe.Product.list(limit=100, active=True)
    for prod in products.auto_paging_iter():
        if _stripe_get(prod.metadata, "tenderflow_plan") == plan_key:
            return prod
    return None


def _find_pack_product(env_key: str):
    products = stripe.Product.list(limit=100, active=True)
    for prod in products.auto_paging_iter():
        if _stripe_get(prod.metadata, "tenderflow_pack") == env_key:
            return prod
    return None


def _find_price_for_product(product_id: str, interval: str | None, amount: int):
    prices = stripe.Price.list(product=product_id, active=True, limit=100)
    for price in prices.auto_paging_iter():
        if price.unit_amount != amount or price.currency != "gbp":
            continue
        if interval is None:
            if price.type == "one_time":
                return price
        elif _stripe_get(price.recurring, "interval") == interval:
            return price
    return None


def main() -> None:
    stripe.api_key = _load_key()
    mode = "test" if stripe.api_key.startswith("sk_test_") else "live"
    print(f"# Stripe mode: {mode}\n")

    lines: list[str] = []

    for name, plan_key, price_specs in SUBSCRIPTIONS:
        prod = _find_product_by_plan(plan_key)
        if not prod:
            prod = stripe.Product.create(
                name=name,
                metadata={"tenderflow_plan": plan_key},
            )
            print(f"Created product: {name} ({prod.id})")
        else:
            print(f"Found product: {name} ({prod.id})")

        for env_key, amount, interval in price_specs:
            price = _find_price_for_product(prod.id, interval, amount)
            if not price:
                price = stripe.Price.create(
                    product=prod.id,
                    unit_amount=amount,
                    currency="gbp",
                    recurring={"interval": interval},
                )
                print(f"  Created price: {price.id} (£{amount / 100:.2f}/{interval})")
            else:
                print(f"  Using price: {price.id}")
            lines.append(f"{env_key}={price.id}")

    for name, env_key, amount in PACKS:
        prod = _find_pack_product(env_key)
        if not prod:
            prod = stripe.Product.create(
                name=name,
                metadata={"tenderflow_pack": env_key},
            )
            print(f"Created product: {name} ({prod.id})")
        price = _find_price_for_product(prod.id, None, amount)
        if not price:
            price = stripe.Price.create(
                product=prod.id,
                unit_amount=amount,
                currency="gbp",
            )
            print(f"  Created price: {price.id} (£{amount / 100:.2f})")
        else:
            print(f"  Using price: {price.id}")
        lines.append(f"{env_key}={price.id}")

    print("\n# Paste into .env, then run: python scripts/setup_stripe_portal.py\n")
    for line in lines:
        print(line)


if __name__ == "__main__":
    main()
