"""List active Stripe prices for the configured account."""
import os
import stripe

stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
prices = stripe.Price.list(limit=50, active=True)
if not prices.data:
    print("NO_PRICES_FOUND — create products in https://dashboard.stripe.com/test/products")
for p in prices.data:
    prod = stripe.Product.retrieve(p.product)
    amt = (p.unit_amount or 0) / 100
    rec = p.recurring["interval"] if p.recurring else "one_time"
    print(f"{p.id}\t{prod.name}\t{amt} {p.currency.upper()}\t{rec}")
