"""Stripe billing — checkout, portal, webhooks."""
from __future__ import annotations

import logging
import os
from typing import Any, Callable

from tender_app.config import get_app_base_url, PACK_CREDITS, PLAN_CREDITS
from tender_app.credits import grant_credits, get_balance

logger = logging.getLogger(__name__)

_stripe = None


def _env(key: str) -> str:
    return (os.environ.get(key) or "").strip()


def _plan_catalog() -> dict:
    """Build catalog from current env with only active Standard and Advance plans.

    amount_gbp here is the net, ex-VAT price the page quotes as "+ taxes", and is only the
    last-resort fallback used when Stripe can't be reached (see _live_amount_gbp below,
    which is what actually renders on the pricing page) — keep it in sync with whatever's
    live in Stripe, since a stale value here silently wins if the API call ever fails."""
    return {
        "standard_monthly": {
            "price_id": _env("STRIPE_PRICE_STANDARD_MONTHLY") or _env("STRIPE_PRICE_STARTER_MONTHLY") or "price_1TifAq10dFB3DskbrWiRhvHG",
            "product_id": _env("STRIPE_PRODUCT_STANDARD") or _env("STRIPE_PRODUCT_STARTER") or "prod_V7PjTprComHpxP",
            "plan": "standard",
            "credits": PLAN_CREDITS.get("standard", 500),
            "mode": "subscription",
            "label": "Solo",
            "amount_gbp": 49.99,
        },
        "advance_monthly": {
            "price_id": _env("STRIPE_PRICE_ADVANCE_MONTHLY") or _env("STRIPE_PRICE_PRO_MONTHLY") or "price_1TifAs10dFB3DskbwgpsqEOr",
            "product_id": _env("STRIPE_PRODUCT_ADVANCE") or _env("STRIPE_PRODUCT_PRO") or "prod_V7PTL1oS7NpxdD",
            "plan": "advance",
            "credits": PLAN_CREDITS.get("advance", 1500),
            "mode": "subscription",
            "label": "Teams",
            "amount_gbp": 99.99,
        },
        "pack_100": {
            "price_id": _env("STRIPE_PRICE_PACK_100"),
            "product_id": _env("STRIPE_PRODUCT_PACK_100"),
            "plan": "pack",
            "credits": PACK_CREDITS.get("pack_100", 100),
            "mode": "payment",
            "label": "100 Credits",
            "amount_gbp": 25,
        },
        "pack_300": {
            "price_id": _env("STRIPE_PRICE_PACK_300"),
            "product_id": _env("STRIPE_PRODUCT_PACK_300"),
            "plan": "pack",
            "credits": PACK_CREDITS.get("pack_300", 300),
            "mode": "payment",
            "label": "300 Credits",
            "amount_gbp": 65,
        },
        "pack_1000": {
            "price_id": _env("STRIPE_PRICE_PACK_1000"),
            "product_id": _env("STRIPE_PRODUCT_PACK_1000"),
            "plan": "pack",
            "credits": PACK_CREDITS.get("pack_1000", 1000),
            "mode": "payment",
            "label": "1,000 Credits",
            "amount_gbp": 180,
        },
    }


def _stripe_client():
    global _stripe
    if _stripe is not None:
        return _stripe
    secret = _env("STRIPE_SECRET_KEY")
    if not secret:
        return None
    import stripe

    stripe.api_key = secret
    _stripe = stripe
    return stripe


def _stripe_get(obj, key: str):
    """Read a field from a StripeObject or plain dict."""
    if not obj:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    try:
        return obj[key]
    except (KeyError, TypeError):
        return getattr(obj, key, None)


def _stripe_dict(obj) -> dict:
    if not obj:
        return {}
    if isinstance(obj, dict):
        return obj
    to_dict = getattr(obj, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    return dict(obj)


_PRICE_CACHE: dict[str, tuple[float, float]] = {}  # price_id -> (amount_gbp, fetched_at)
_PRICE_CACHE_TTL_SECS = 600  # 10 min — long enough to spare Stripe a call per page view,
                             # short enough that a price change in Stripe shows up same-session
_VAT_RATE = 0.20  # UK VAT, used to derive the net price from a tax-inclusive Stripe price


def _live_amount_gbp(price_id: str | None, fallback: float) -> float:
    """Net (ex-VAT) amount to display, read live from Stripe by price_id rather than from
    a hand-typed number in _plan_catalog(). The pricing page quotes "+ taxes", so a price
    Stripe marks tax_behavior="inclusive" holds the gross total and has to have VAT divided
    back out before display; an "exclusive" price is already net. Falls back to the
    hardcoded default — not an error — if Stripe isn't configured, the price_id is missing,
    or the call fails."""
    if not price_id:
        return fallback
    import time
    cached = _PRICE_CACHE.get(price_id)
    now = time.time()
    if cached and (now - cached[1]) < _PRICE_CACHE_TTL_SECS:
        return cached[0]
    stripe = _stripe_client()
    if not stripe:
        return fallback
    try:
        price = stripe.Price.retrieve(price_id)
        unit_amount = _stripe_get(price, "unit_amount")
        if unit_amount is None:
            return fallback
        amount = unit_amount / 100
        if _stripe_get(price, "tax_behavior") == "inclusive":
            amount = amount / (1 + _VAT_RATE)
        amount = round(amount, 2)
        _PRICE_CACHE[price_id] = (amount, now)
        return amount
    except Exception:
        logger.exception("Could not fetch live Stripe price for %s; using fallback", price_id)
        return fallback


def get_public_catalog() -> list[dict]:
    """Catalog for pricing page (no secret price IDs required for display)."""
    subs = []
    packs = []
    for key, item in _plan_catalog().items():
        amount_gbp = item["amount_gbp"]
        if item["mode"] == "subscription":
            amount_gbp = _live_amount_gbp(item.get("price_id"), item["amount_gbp"])
        entry = {
            "id": key,
            "label": item["label"],
            "credits": item["credits"],
            "amount_gbp": amount_gbp,
            "mode": item["mode"],
            "plan": item["plan"],
            "available": bool(item.get("price_id") or item.get("product_id")),
        }
        if item["mode"] == "subscription":
            subs.append(entry)
        else:
            packs.append(entry)
    return {"subscriptions": subs, "packs": packs}


def _catalog_price_ids() -> set[str]:
    return {item["price_id"] for item in _plan_catalog().values() if item.get("price_id")}


def _active_subscription(stripe, customer_id: str) -> dict[str, Any]:
    """Return active Stripe subscription details for a customer."""
    empty: dict[str, Any] = {
        "active": False,
        "legacy": False,
        "interval": None,
        "price_id": None,
        "sub_id": None,
    }
    if not customer_id:
        return empty
    try:
        subs = stripe.Subscription.list(customer=customer_id, status="active", limit=5)
        if not subs.data:
            return empty
        sub = subs.data[0]
        item = sub["items"]["data"][0]
        price = item["price"]
        price_id = price["id"] if isinstance(price["id"], str) else price.id
        rec = _stripe_get(price, "recurring")
        interval = _stripe_get(rec, "interval")
        catalog = _catalog_price_ids()
        return {
            "active": True,
            "legacy": price_id not in catalog,
            "interval": interval,
            "price_id": price_id,
            "sub_id": sub.id,
        }
    except Exception:
        return empty


def cancel_active_subscription(conn, username: str) -> dict[str, Any]:
    """Cancel the customer's active Stripe subscription immediately."""
    stripe = _stripe_client()
    if not stripe:
        raise RuntimeError("Stripe is not configured")
    ph = "%s"
    cursor = conn.cursor()
    cursor.execute(
        f"SELECT stripe_customer_id FROM subscriptions WHERE username = {ph}",
        (username,),
    )
    row = cursor.fetchone()
    if not row or not row[0]:
        cursor.close()
        return {"ok": False, "error": "No billing account found"}
    customer_id = row[0]
    sub_info = _active_subscription(stripe, customer_id)
    if not sub_info["active"] or not sub_info["sub_id"]:
        cursor.close()
        return {"ok": False, "error": "No active subscription to cancel"}
    stripe.Subscription.cancel(sub_info["sub_id"])
    cursor.execute(
        f"""UPDATE subscriptions SET
            stripe_subscription_id = NULL,
            plan = 'free',
            status = 'canceled',
            monthly_credits = 0,
            updated_at = CURRENT_TIMESTAMP
            WHERE username = {ph}""",
        (username,),
    )
    conn.commit()
    cursor.close()
    return {"ok": True, "canceled": sub_info["sub_id"]}


def _get_or_create_customer(conn, username: str, email: str) -> str:
    stripe = _stripe_client()
    if not stripe:
        raise RuntimeError("Stripe is not configured")
    ph = "%s"
    cursor = conn.cursor()
    cursor.execute(
        f"SELECT stripe_customer_id FROM subscriptions WHERE username = {ph}",
        (username,),
    )
    row = cursor.fetchone()
    if row and row[0]:
        try:
            cust = stripe.Customer.retrieve(row[0])
            addr = getattr(cust, "address", None) or {}
            if not isinstance(addr, dict):
                addr = getattr(addr, "to_dict", lambda: {})() if hasattr(addr, "to_dict") else {}
            if addr.get("country") != "GB":
                stripe.Customer.modify(
                    row[0],
                    address={"country": "GB"},
                    shipping={"name": username or "Valued Customer", "address": {"country": "GB"}},
                    preferred_locales=["en-GB"],
                )
            cursor.close()
            return row[0]
        except Exception:
            pass

    customer = stripe.Customer.create(
        email=email,
        address={"country": "GB"},
        shipping={"name": username or "Valued Customer", "address": {"country": "GB"}},
        preferred_locales=["en-GB"],
        metadata={"username": username},
    )
    cursor.execute(
        f"""INSERT INTO subscriptions (username, stripe_customer_id, plan, status)
            VALUES ({ph}, {ph}, 'free', 'trialing')
            ON CONFLICT (username) DO UPDATE SET stripe_customer_id = EXCLUDED.stripe_customer_id""",
        (username, customer.id),
    )
    conn.commit()
    cursor.close()
    return customer.id


def _customer_has_details(stripe, customer_id: str) -> bool:
    if not customer_id:
        return False
    try:
        cust = stripe.Customer.retrieve(customer_id)
        if cust and (cust.get("address") or cust.get("default_source") or (cust.get("invoice_settings") or {}).get("default_payment_method")):
            return True
        subs = stripe.Subscription.list(customer=customer_id, status="active", limit=1)
        if subs and subs.data:
            return True
        invoices = stripe.Invoice.list(customer=customer_id, status="paid", limit=1)
        if invoices and invoices.data:
            return True
    except Exception:
        pass
    return False


PLAN_WEIGHTS = {
    "free": 0,
    "starter": 0,
    "standard": 1,
    "solo": 1,
    "advance": 2,
    "teams": 2,
}


def create_checkout_session(
    conn,
    username: str,
    email: str,
    catalog_key: str,
) -> str:
    stripe = _stripe_client()
    if not stripe:
        raise RuntimeError("Stripe is not configured")
    item = _plan_catalog().get(catalog_key)
    if not item:
        raise ValueError(f"Unknown or unconfigured plan: {catalog_key}")
    
    price_id = item.get("price_id")
    if not price_id and item.get("product_id"):
        prices = stripe.Price.list(product=item["product_id"], active=True, limit=1)
        if prices.data:
            price_id = prices.data[0].id

    if not price_id:
        raise ValueError(f"Unknown or unconfigured plan price: {catalog_key}")

    customer_id = _get_or_create_customer(conn, username, email)
    mode = item["mode"]
    base_url = get_app_base_url()

    if mode == "subscription":
        sub_info = _active_subscription(stripe, customer_id)
        if sub_info["active"]:
            if sub_info.get("price_id") == price_id:
                raise ValueError("You are already subscribed to this plan.")

            ph = "%s"
            cursor = conn.cursor()
            cursor.execute(f"SELECT plan FROM subscriptions WHERE username = {ph}", (username,))
            row = cursor.fetchone()
            cursor.close()
            current_plan = (row[0] if row else "free").lower()

            current_weight = PLAN_WEIGHTS.get(current_plan, PLAN_WEIGHTS.get((sub_info.get("plan") or "").lower(), 0))
            new_weight = PLAN_WEIGHTS.get((item["plan"] or "").lower(), 0)

            # DOWNGRADE LOGIC: Take effect at period end without immediate charge or credit loss
            if new_weight < current_weight and sub_info.get("sub_id"):
                sub = stripe.Subscription.retrieve(sub_info["sub_id"])
                sub_item_id = sub["items"]["data"][0]["id"]
                
                # Modify subscription items on Stripe with proration_behavior="none"
                stripe.Subscription.modify(
                    sub_info["sub_id"],
                    items=[{"id": sub_item_id, "price": price_id}],
                    proration_behavior="none",
                    metadata={"username": username, "plan": item["plan"], "pending_downgrade": "1"},
                )
                
                # Send the user back into the app they were actually using, not the
                # standalone pricing page — app.js picks this param up on load, confirms
                # it, shows a toast, and strips it from the URL.
                return f"{base_url}/?billing_downgraded=1&plan={item['plan']}"

    # UPGRADE or NEW SUBSCRIPTION LOGIC: Immediate redirect to Stripe Checkout
    has_details = _customer_has_details(stripe, customer_id)

    session_params: dict[str, Any] = {
        "customer": customer_id,
        "mode": mode,
        "line_items": [{"price": price_id, "quantity": 1}],
        "currency": "gbp",
        "locale": "en-GB",
        "payment_method_types": ["card", "paypal"],
        "tax_id_collection": {"enabled": True},
        "customer_update": {
            "address": "auto",
            "name": "auto",
            "shipping": "auto",
        },
        # Both land back in the main app (not the standalone pricing.html page the user
        # left to get here) — app.js reads these params on load, confirms/acknowledges,
        # shows a toast, and strips them from the URL.
        "success_url": f"{base_url}/?billing_success=1&session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{base_url}/?billing_cancelled=1",
        "metadata": {
            "username": username,
            "catalog_key": catalog_key,
            "credits": str(item["credits"]),
            "plan": item["plan"],
        },
    }

    # Ask for full details on first checkout only. Save to Stripe Customer so future checkouts auto pre-fill.
    if not has_details:
        session_params["billing_address_collection"] = "required"
        session_params["phone_number_collection"] = {"enabled": True}
        session_params["custom_fields"] = [
            {
                "key": "company_name",
                "label": {"type": "custom", "custom": "Company / Business Name"},
                "type": "text",
                "optional": True,
            }
        ]
    if _env("STRIPE_AUTOMATIC_TAX") == "1":
        session_params["automatic_tax"] = {"enabled": True}
    if mode == "subscription":
        session_params["subscription_data"] = {
            "metadata": {"username": username, "plan": item["plan"]},
        }
    
    try:
        session = stripe.checkout.Session.create(**session_params)
    except Exception as ex:
        if "paypal" in str(ex).lower():
            session_params["payment_method_types"] = ["card"]
            session = stripe.checkout.Session.create(**session_params)
        else:
            raise ex

    return session.url


def confirm_checkout_session(
    conn,
    username: str,
    session_id: str,
) -> dict[str, Any]:
    """Fulfill a paid Checkout session (fallback when webhooks cannot reach localhost)."""
    stripe = _stripe_client()
    if not stripe:
        raise RuntimeError("Stripe is not configured")
    session_id = (session_id or "").strip()
    if not session_id:
        raise ValueError("Missing session_id")

    checkout = stripe.checkout.Session.retrieve(session_id)
    if checkout.payment_status != "paid":
        return {
            "ok": False,
            "error": "Checkout session was not completed or was cancelled. No charges were made to your account.",
        }

    meta = _stripe_dict(checkout.metadata)
    if _username_from_metadata(meta) != username.strip().lower():
        return {"ok": False, "error": "This payment belongs to another account"}

    _handle_checkout_completed(conn, _stripe_dict(checkout))
    invoice_pdf = None
    invoice_id = _stripe_get(checkout, "invoice")
    if invoice_id:
        try:
            inv = stripe.Invoice.retrieve(invoice_id)
            invoice_pdf = inv.invoice_pdf or inv.hosted_invoice_url
        except Exception:
            pass
    return {
        "ok": True,
        "balance": get_balance(conn, username),
        "plan": meta.get("plan", "free"),
        "invoice_pdf": invoice_pdf,
    }


def create_portal_session(conn, username: str, email: str) -> str:
    stripe = _stripe_client()
    if not stripe:
        raise RuntimeError("Stripe is not configured")
    customer_id = _get_or_create_customer(conn, username, email)
    base_url = get_app_base_url()
    params: dict[str, Any] = {
        "customer": customer_id,
        # Land back in the app itself (with the pricing modal reopened) rather
        # than the standalone pricing.html page, which lacks the app shell.
        "return_url": f"{base_url}/?openBilling=1",
    }
    portal_config = _env("STRIPE_PORTAL_CONFIGURATION_ID")
    if portal_config:
        params["configuration"] = portal_config
    try:
        session = stripe.billing_portal.Session.create(**params)
        return session.url
    except Exception:
        params.pop("configuration", None)
        session = stripe.billing_portal.Session.create(**params)
        return session.url


def get_billing_status(conn, username: str) -> dict:
    ph = "%s"
    cursor = conn.cursor()
    cursor.execute(
        f"""SELECT plan, status, monthly_credits, current_period_end, stripe_customer_id,
                   stripe_subscription_id
            FROM subscriptions WHERE username = {ph}""",
        (username,),
    )
    sub = cursor.fetchone()
    cursor.execute(f"SELECT balance FROM credit_wallet WHERE username = {ph}", (username,))
    bal = cursor.fetchone()
    cursor.close()
    customer_id = sub[4] if sub else None
    sub_info: dict[str, Any] = {"legacy": False, "interval": None, "price_id": None}
    invoice_pdf = None
    stripe = _stripe_client()
    if stripe and customer_id:
        sub_info = _active_subscription(stripe, customer_id)
        try:
            invoices = stripe.Invoice.list(customer=customer_id, limit=1)
            if invoices and invoices.data:
                inv = invoices.data[0]
                invoice_pdf = inv.invoice_pdf or inv.hosted_invoice_url
        except Exception:
            pass
    return {
        "plan": sub[0] if sub else "free",
        "status": sub[1] if sub else "trialing",
        "monthly_credits": sub[2] if sub else 0,
        "current_period_end": str(sub[3]) if sub and sub[3] else None,
        "balance": int(bal[0]) if bal else 0,
        "has_stripe_customer": bool(sub and sub[4]),
        "subscription_legacy": sub_info.get("legacy", False),
        "billing_interval": sub_info.get("interval"),
        "stripe_subscription_id": sub[5] if sub else None,
        "invoice_pdf": invoice_pdf,
    }


def _event_processed(conn, event_id: str) -> bool:
    ph = "%s"
    cursor = conn.cursor()
    cursor.execute(f"SELECT 1 FROM stripe_events WHERE event_id = {ph}", (event_id,))
    found = cursor.fetchone() is not None
    cursor.close()
    return found


def _mark_event(conn, event_id: str) -> None:
    ph = "%s"
    cursor = conn.cursor()
    cursor.execute(
        f"INSERT INTO stripe_events (event_id) VALUES ({ph}) ON CONFLICT DO NOTHING",
        (event_id,),
    )
    conn.commit()
    cursor.close()


def handle_webhook(
    payload: bytes,
    sig_header: str,
    get_db_connection: Callable,
) -> dict:
    stripe = _stripe_client()
    if not stripe:
        return {"ok": False, "error": "Stripe not configured"}
    webhook_secret = _env("STRIPE_WEBHOOK_SECRET")
    if not webhook_secret:
        return {"ok": False, "error": "Webhook secret not configured"}

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except Exception as ex:
        return {"ok": False, "error": str(ex)}

    event_id = event["id"]
    conn = get_db_connection()
    try:
        if _event_processed(conn, event_id):
            return {"ok": True, "duplicate": True}

        etype = event["type"]
        data = event["data"]["object"]

        if etype == "checkout.session.completed":
            _handle_checkout_completed(conn, data)
        elif etype == "checkout.session.expired":
            logger.info(f"[Stripe Webhook] Checkout session expired/cancelled: {data.get('id')}")
        elif etype == "invoice.paid":
            _handle_invoice_paid(conn, data)
        elif etype in ("customer.subscription.updated", "customer.subscription.deleted"):
            _handle_subscription_change(conn, data, etype)

        _mark_event(conn, event_id)
        return {"ok": True, "type": etype}
    finally:
        conn.close()


def _username_from_metadata(meta: dict) -> str:
    return (_stripe_get(meta, "username") or "").strip().lower()


def _handle_checkout_completed(conn, session: dict) -> None:
    session_id = session.get("id", "")
    if session_id and _event_processed(conn, f"checkout:{session_id}"):
        return

    meta = _stripe_dict(session.get("metadata"))
    username = _username_from_metadata(meta)
    if not username:
        return
    mode = session.get("mode")
    catalog_key = meta.get("catalog_key", "")
    item = _plan_catalog().get(catalog_key, {})
    credits = int(meta.get("credits") or item.get("credits") or 0)
    plan = meta.get("plan") or item.get("plan") or "free"

    ph = "%s"
    cursor = conn.cursor()

    if mode == "payment" and credits > 0:
        grant_credits(conn, username, credits, "pack_purchase", session.get("id", ""))

    if mode == "subscription":
        sub_id = session.get("subscription")
        monthly = int(item.get("credits") or PLAN_CREDITS.get(plan, 0))

        cursor.execute(f"SELECT stripe_subscription_id, plan, monthly_credits FROM subscriptions WHERE username = {ph}", (username,))
        row = cursor.fetchone()
        old_sub_id = row[0] if row else None
        old_monthly_credits = int(row[2]) if row and row[2] is not None else 0

        if old_sub_id and sub_id and old_sub_id != sub_id:
            try:
                stripe = _stripe_client()
                if stripe:
                    stripe.Subscription.cancel(old_sub_id)
            except Exception as ex:
                logger.warning(f"Failed to cancel previous subscription {old_sub_id}: {ex}")

        cursor.execute(
            f"""UPDATE subscriptions SET
                stripe_subscription_id = {ph},
                plan = {ph},
                status = 'active',
                monthly_credits = {ph},
                updated_at = CURRENT_TIMESTAMP
                WHERE username = {ph}""",
            (sub_id, plan, monthly, username),
        )

        if old_monthly_credits > 0:
            credits_to_grant = max(0, monthly - old_monthly_credits)
            reason = "subscription_upgrade"
        else:
            credits_to_grant = credits if credits > 0 else monthly
            reason = "subscription_start"

        if credits_to_grant > 0:
            grant_credits(conn, username, credits_to_grant, reason, sub_id or "")
    conn.commit()
    cursor.close()
    if session_id:
        _mark_event(conn, f"checkout:{session_id}")


def _handle_invoice_paid(conn, invoice: dict) -> None:
    sub_id = invoice.get("subscription")
    if not sub_id:
        return
    stripe = _stripe_client()
    sub = stripe.Subscription.retrieve(sub_id)
    meta = sub.get("metadata") or {}
    username = _username_from_metadata(meta)
    if not username:
        return
    plan = meta.get("plan", "starter")
    credits = PLAN_CREDITS.get(plan, 0)
    if credits > 0:
        grant_credits(conn, username, credits, "subscription_renewal", invoice.get("id", ""))


def _handle_subscription_change(conn, sub: dict, etype: str) -> None:
    meta = sub.get("metadata") or {}
    username = _username_from_metadata(meta)
    if not username:
        return
    status = sub.get("status", "canceled")
    plan = meta.get("plan", "free")
    if etype == "customer.subscription.deleted":
        status = "canceled"
        plan = "free"
    ph = "%s"
    cursor = conn.cursor()
    period_end = sub.get("current_period_end")
    cursor.execute(
        f"""UPDATE subscriptions SET status = {ph}, plan = {ph},
            current_period_end = to_timestamp({ph}),
            updated_at = CURRENT_TIMESTAMP
            WHERE username = {ph}""",
        (status, plan, period_end, username),
    )
    conn.commit()
    cursor.close()
