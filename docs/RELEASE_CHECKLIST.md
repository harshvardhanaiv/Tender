# Release checklist

Run before every deploy. Each line is something that has regressed before.

## Speed (Re-test 4 B7)

- `python tests/test_buyer_feed_speed.py`: the buyer feed, a repeated feed request and a name search stay inside their limits.
- After the deploy, time these on the live site (signed in, the same profile as the testers). Each must answer in under 1 second at p95:
  - Buyer intelligence default feed
  - searches for `leeds`, `Manchester`, `MOD`, `kent` and `software`
  - the Frameworks only filter
  - the radar and the radius filter
- The first request after a data rebuild may take a few seconds while the buyer tables warm; the second must be fast.

## Tests

- `python scratch/run_all_py.sh` (inside the app container) and every `node tests/*.js`.
- Known failures to ignore until fixed: `app_state_drift_test.js`, `supplier_contacts_view_test.js`, `test_growth_inbox_api`, `test_charities`.
