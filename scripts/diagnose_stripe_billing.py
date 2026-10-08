"""Print Stripe subscription + portal config diagnostics."""
import os
import stripe

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
email = os.environ.get("DIAG_EMAIL", "jits023@gmail.com")
portal_id = os.environ.get("STRIPE_PORTAL_CONFIGURATION_ID", "")

customers = stripe.Customer.list(email=email, limit=5)
print(f"=== Customer {email} ===")
for c in customers.data:
    print(f"Customer {c.id}")
    subs = stripe.Subscription.list(customer=c.id, status="all", limit=20)
    for s in subs.data:
        item = s["items"]["data"][0]
        price = item["price"]
        prod_id = price["product"] if isinstance(price["product"], str) else price["product"]["id"]
        prod = stripe.Product.retrieve(prod_id)
        rec = price["recurring"] if "recurring" in price and price["recurring"] else None
        interval = rec["interval"] if rec else "once"
        print(
            f"  sub {s.id} status={s.status} "
            f"product={prod.name} ({prod.id}) "
            f"price={price.id} {price['unit_amount']/100}/{interval}"
        )

if portal_id:
    cfg = stripe.billing_portal.Configuration.retrieve(portal_id)
    prods = cfg["features"]["subscription_update"]["products"]
    print(f"\n=== Portal config {portal_id} ===")
    print(f"subscription_update enabled: {cfg['features']['subscription_update']['enabled']}")
    for p in prods:
        prod = stripe.Product.retrieve(p["product"])
        print(f"  product: {prod.name} ({p['product']})")
        for pid in p["prices"]:
            pr = stripe.Price.retrieve(pid)
            iv = pr["recurring"]["interval"] if pr.get("recurring") else "once"
            print(f"    price {pid} £{pr['unit_amount']/100}/{iv}")
