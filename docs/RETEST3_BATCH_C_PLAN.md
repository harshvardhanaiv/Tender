# Re-test 3, batch C: plan (nothing built yet)

Written 2026-10-30. Each item says what it would change, what it would take, and what needs a production write. Order is by value for effort.

## 1. T3: Tender Search in under 15 seconds (steps 3 to 5 of `docs/PORTAL_INDEX_DESIGN.md`)

Built already: `tender_notices`, `index_runs`, Contracts Finder and Find a Tender ingestion (`tender_app/tender_index.py`, `scripts/index_portals.py`).

- **Step 3a, a schedule per portal (about 1 day).** Run `ingest_contracts_finder` every 30 minutes and `ingest_find_tender` every 30 minutes from the
  existing scheduler thread, each writing `index_runs`. `SEARCH_USE_INDEX=0` switches everything off.
- **Step 3b, search reads the index first (about 2 days).** `/api/search` answers page 1 from `tender_notices` (Postgres full text on a new
  `tsvector` column, GIN index), with a stable order (published date, then rank). The live job becomes a top-up for a portal whose last run is
  older than its cadence, or when the user presses "Search live portals". Rows are marked with `indexed_at`.
- **Step 4, the scraped portals (1 to 2 days each, PCS last).** Sell2Wales, eTenders NI, ProContract, YORtender, NEPO, GCA, then PCS (newest
  only, overnight full sweep). Find a Tender must never time out: it comes from the OCDS API, not from a live search.
- **Step 5, freshness line and an admin view of `index_runs` (1 day).**
- Needs from you: approval for scheduled traffic to the scraped portals; a one-off production backfill (a production write); whether a search
  that only reads the index still costs a credit.
- Done when: "business intelligence" returns page 1 in under 1 s and everything in under 15 s on production, Find a Tender included.

## 2. B7 / R8: buyer and supplier searches under 1 s p95 (about 3 days)

- Buyer search: today it aggregates per request. Add `buyer_cpv_spend` (buyer, CPV division, spend, awards) beside `buyer_stats`, rebuilt by
  `stats.refresh_all`, and serve search and the feed from those two tables. (Re-test 3 B1 already adds `buyer_stats.cpv_spend` as JSON; this
  would normalise it into a table so it can be filtered and sorted in SQL.)
- Supplier search: a `pg_trgm` GIN index on the canonical supplier name key, and a prefix/word index for the common-word case ("software",
  "Capita plc"). Cache the first page of the 200 most common words for 10 minutes.
- Needs: `CREATE EXTENSION pg_trgm` on production (a production write), then one stats rebuild (about 5.5 minutes).
- Done when: 20 sample buyer and supplier searches have p95 under 1 s measured against production, read-only.

## 3. B5 / B6 / B8: merge buyer variants, locate buyers, fill contacts (about 4 days)

- Merge: extend the buyer entity table with Kent "t/a" records, MoD units and the duplicate NHS trust, as alias rules plus a reviewed list; a dry
  run prints every group it would merge and a revert list (the same pattern as `scripts/merge_buyer_aliases.py`).
- Locate: geocode the 8,035 buyers with no location (`scripts/geocode_buyers.py`) in batches from the buyer's postcode where a notice has one,
  else by name against postcodes.io / Nominatim, recording `accuracy` as today. Rate-limited, run overnight.
- Contacts: read the contact block of Find a Tender and Contracts Finder notices (OCDS `buyer.contactPoint`) into `buyer_contact_cache`.
- Needs: production writes (merge, geocode, contacts), each after its dry run is read.

## 4. P2: planning applicant company (about 2 days)

PlanIt `other_fields` and the council portal name the applicant for some councils. Read it at harvest into `applicant_company`, then match to
Companies House with the existing `ch_matcher.py`. Needs a harvest re-run for history (PlanIt allows about 300 requests a day), so history fills
over several nights.

## 5. R9 / R12: Wales coverage (about 1 day, after item 3)

Sell2Wales has 84 awards on file. Check whether the portal lists awards in a feed or only in notices; if a feed, add it to the nightly award
scrape. Buyer variants are shared with item 3.

## What I would build first

Items 1 (steps 3a and 3b) and 2: they are the two reported targets (15 s and 1 s). Say which of these you want, or in what order.
