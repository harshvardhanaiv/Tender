# Scheduled portal index for Tender Search (re-test T3)

Status: design only, nothing built. Written 2026-10-30.

## The problem

A "business intelligence" search across the 10 portals takes about 2.5 minutes. First rows arrive after about 9 seconds, Find a Tender,
Public Contracts Scotland (PCS) and Sell2Wales are still "waiting" after 80 seconds, and the list is re-polled every 5 to 11 seconds.
The cause is that every search is a live fan-out to each portal (`etenders_scraper/sources/progressive_search.py`): PCS alone gets a
3 minute first-page budget and 10 minutes to finish, because it answers slowly (about 2 MB of page state per result page).

Contracts Finder already avoids this: `contracts_finder.start_feed_prewarm()` scans its feed on a schedule and keeps the last scan in
`cf_feed_snapshot` (`cf_feed_store.py`), so a search filters memory instead of calling the portal. T3 is that idea for every portal.

## What the index holds

Open and recently closed notices only (tenders, contract notices, DPS and framework listings, and awards from the last 90 days).
Awards are already held in `contract_awards`; they stay there and are not copied.

One table, `tender_notices`:

| Column | Notes |
|---|---|
| `source`, `resource_id` | primary key; the same pair the app already uses as `tender_key` |
| `title`, `description`, `contracting_authority` | text, plus a `tsvector` column for search (GIN index) |
| `notice_type`, `status` | normalised once at ingestion, so "open" no longer needs the client's guesswork (T1) |
| `published_at`, `deadline_at` | real timestamps, not portal strings (fixes the four date formats, T7) |
| `value`, `currency` | numeric plus currency code per portal |
| `cpv_codes`, `location_text`, `region`, `postcode` | region from NUTS or postcode at ingestion (T7 location gap) |
| `detail_url`, `raw` (JSONB) | link back to the portal, and the row exactly as the portal gave it |
| `first_seen_at`, `last_seen_at`, `closed_at` | a notice not seen for two runs is marked closed, never deleted |

## Refresh schedule

One job per portal, run by the existing scheduler thread (the one that runs the email jobs). Different portals need different cadence:

| Portal | Method | Cadence |
|---|---|---|
| Contracts Finder | existing feed scan, written to the table too | every 30 minutes |
| Find a Tender | OCDS release package API (`/api/1.0/ocdsReleasePackages`), by `updatedFrom` | every 30 minutes |
| Sell2Wales, eTenders NI, ProContract, YORtender, NEPO, GCA | each portal's list page or feed, newest first, stop at the first notice already stored | every 60 minutes |
| Public Contracts Scotland | the slow one: fetch newest notices only, with the scrape cursor `ef291d1` already added | every 2 hours, overnight full sweep |
| eTenders Ireland | its batch fetch (`_fetch_ie_batch`) | every 60 minutes |

Each run records `source`, start, finish, rows seen, rows new and any error in an `index_runs` table, so a portal that stopped updating is
visible. Runs are one at a time per portal and never overlap a search's live call.

## How search reads it

1. `/api/search` queries `tender_notices` first, with the user's words, counties, dates and status. The result is the first page in
   well under a second, with a stable order (published date, then score), so nothing reshuffles (T3 order).
2. The existing progressive job still runs, but only to top up: for a portal whose last successful run is older than its cadence, or when
   the user presses "Search live portals". New rows are appended, with a "12 new results: show" bar, as the report asked.
3. Each result carries `indexed_at` and the response says per portal how fresh it is ("Find a Tender: 12 minutes ago, PCS: 1 hour ago"),
   so an old index is never presented as live.
4. If the table is empty or the portal's job has failed for over 6 hours, search falls back to today's live behaviour for that portal.

## What it would take

Build order, each step shippable on its own:

1. `tender_notices`, `index_runs`, the normaliser (notice type, status, dates, region) and tests with real notices from each portal.
   Roughly 2 to 3 days.
2. Contracts Finder and Find a Tender ingestion (two APIs, no scraping). About 2 days. This alone covers most UK results and fixes T1
   at the source.
3. Search reads the index first; live job becomes the top-up; stable order and the "new results" bar. About 2 days.
4. The scraped portals one at a time, PCS last. Each is 1 to 2 days and each can break when the portal changes its page.
5. Freshness line, admin view of `index_runs`, alerts when a portal is stale. About 1 day.

## Risks and decisions needed

- **Portal terms and load.** Scheduled scraping of PCS, Sell2Wales and the others is more regular traffic than user searches. Confirm
  this is acceptable, and keep the cadence modest.
- **Storage.** Roughly 60,000 open or recent notices at a few KB each, plus the full text index: a few hundred MB. The production
  container has no persistent volume for files, so everything lives in Postgres, as `cf_feed_snapshot` does.
- **Credits.** Today a search may cost a credit. Searching the index is cheap: decide whether it stays charged.
- **Coverage gap.** A notice published and withdrawn between two runs is missed. The live top-up covers the user's own query.
- **Deploy and migration.** Needs a one-off backfill on production (a production write, so approval first) and a rollback switch
  (`SEARCH_USE_INDEX=0`) to return to live-only search.
- **Not in scope:** international portals (TED, SAM.gov, BOAMP) stay live-only.

## How we would know it worked

- "business intelligence" across the UK portals returns its first page in under 1 second and all rows within 5 seconds.
- Row order does not change while the list is on screen.
- Opportunities shows exactly the notices with a deadline today or later (T1 by construction).
- Every result shows its portal's freshness.
