# Round 28 fix plan (for Antigravity)

Source: "TenderFlow - Round 28 Full Test.html" (QA on tender.civenta.co.uk, 5 Oct 2026).
Branch: `V2`. App: Flask + vanilla JS + PostgreSQL. This plan was written by reading the code and running
**read-only** queries against the local dev database. **No code was changed.**

Round 27 items (Find a Tender results, per-portal status chips, profile/portal persistence) are confirmed fixed. Do not touch them
except where item 3.1 says so. Round 28 is mostly **data quality** plus a few Tender Search and Buyer workspace behaviours.

## How to work (project rules, mandatory)

1. Every fix ships with a test that **fails on the old behaviour**. Keep the test permanently in `tests/`.
2. Run the whole suite before calling anything done: `bash tests/run_all.sh` (17 files at the time of writing, all passing).
3. Trace shared logic to **every** use before changing it (several items below touch constants and SQL shared by 3 modules).
4. Re-confirm each root cause on real data before fixing. Do not pattern-match to a past bug. Section "Verified" says what was already confirmed.
5. Tests are plain-assert scripts (no pytest), run as one-off containers:
   `docker compose run --rm --no-deps -T -e ENABLE_EMAIL_SCHEDULER=0 -e ENABLE_SCHEDULERS=0 app python tests/<file>.py`.
   Style reference: `tests/test_growth_studio_api.py` (throwaway `zz_*` users and fixture rows, removed in `finally`).
6. Editing any `.py` restarts the dev worker. `docker-compose.yml` now sets `ENABLE_EMAIL_SCHEDULER: "0"`, so this no longer sends real mail once the container is recreated (`docker compose up -d app`); until then, batch your edits.
7. Do not commit or deploy. The owner does that. `tests/` is untracked: `git add tests/`.
8. Dev DB figures differ from live (for example the buyer in 2.1 has 55 raw / 53 de-duplicated awards locally, 41 / 39 on live). The mechanism is the same.

## Order of work

| # | Workstream | Priority | Report items |
|---|---|---|---|
| A | One buyer aggregate, one definition of spend | P1 | 2.1, 2.4 |
| B | Supplier-name validation and cleanup | P1 | 2.2 |
| C | Market Radar category accuracy and outliers | P1 | 2.3 |
| D | Tender Search filter behaviour and counts | P2 | 3.1, 3.2, 3.3 |
| E | Market engagement defaults and step status; "Latest contract" column | P2 | 4.1, 2.5 |
| F | Formatting, empty columns, CPV search, wording, dashboard windows | P3 | 2.6, 3.4, 4.2 |

---

## A. One buyer aggregate and one definition of spend (2.1, 2.4)

### A1. Same buyer, two sets of figures (2.1) — VERIFIED
**Cause.** There are two aggregation paths for Buyer Intelligence and only one de-duplicates awards.
- `GET /api/buyers/opportunity-feed` with no search text reads the precomputed `buyer_stats` table (`_buyer_items_from_stats`, `tender_app/blueprints/buyers_bp.py:998`; table built by `refresh_buyer_stats`, `tender_app/stats.py:156`). That refresh runs on `DEDUPED_AWARDS_CTE_SQL`.
- `GET /api/buyers/search` (`buyers_bp.py:369`), `GET /api/buyers/<name>` (`:535`) and the feed's own live path (when text is searched, `:1137` onward) query raw `contract_awards` with no de-duplication.
- Confirmed on dev data for Central London Community Healthcare NHS Trust: raw rows 55, de-duplicated 53 (`buyer_stats.total_contracts` = 53). The repeat-supplier rate differs for the same reason (it is derived from contracts vs distinct suppliers).
- Secondary: `buyer_stats` is refreshed lazily every 6 h (`STATS_TTL_S`), so it can also lag behind.

**Fix.**
1. Make all three paths use the same de-duplicated source. Preferred: read `buyer_stats` everywhere a buyer total is shown, and have the text-search path only choose *which* buyers match, then read their figures from `buyer_stats`. Fallback: put `DEDUPED_AWARDS_CTE_SQL` in front of the live queries.
2. Do the same for the per-buyer detail blocks in `get_buyer_detail` (total spend, awards, repeat rate, supplier list, CPV breakdown, `:553`–`:790`).
3. Add a consistency check as a test (not a nightly job): for a sample of buyers, feed value == search value == detail value for total spend, contracts, unique suppliers and repeat rate.

**Tests.** Insert a buyer with deliberate duplicate award rows (the duplicate shape that `DEDUPED_AWARDS_CTE_SQL` collapses). Assert the three endpoints return identical `total_contracts`, `total_spend`, `unique_suppliers` and repeat rate. It must fail before the fix.

### A2. One definition of "spend" (2.4) — PARTLY VERIFIED
**Findings.**
- Camden is split in raw data into four spellings (`LONDON BOROUGH OF CAMDEN` 17, `London Borough Of Camden` 2, `London Borough of Camden` 247, `London Borough of Camden Council` 44). The SQL group key `_norm_auth_sql` (`buyers_bp.py:55`) folds case and whitespace but **not** the trailing "Council"/"The" noise, so "…Camden" and "…Camden Council" stay separate. Market Radar's `canonical_buyer_key` / `normalise_name` (`tender_app/market_radar.py:360`–`:383`) already strips that noise.
- The £1,023.9M for Camden is not framework ceilings (those are excluded). It comes from awards flagged non-framework with enormous values. In the dev DB there are 1,028 non-framework awards at or above £2bn and 2,738 above £500m. See C2: this is the same defect as the £2bn Salesforce row.
- The shared cap `SINGLE_AWARD_STATS_CEILING_GBP = 2_000_000_000` (`etenders_scraper/awards.py:47`) is applied with `<=` in Market Radar (`prepare_row`) and `>` in Buyer Intelligence, so an award of exactly £2bn is included in Radar totals and the cap is also far too high to catch call-off ceilings.
- The Supplier Intelligence list "won (£5.84B)" for Fujifilm / "£5.48B" for Medtronic: `supplier_stats.total_value` already excludes frameworks and values over the cap (`stats.py:113`–`:150`), so the list is probably showing a different field or the same oversized non-framework values. **Locate the exact field the list renders before fixing.** Do not assume.
- "Places for People" typed "Other public bodies": `classify_buyer_type` (`buyers_bp.py:170`) and `market_radar.classify_authority` (`:320`) are two different classifiers.

**Fix.**
1. Write the definition once, in one module (suggest `etenders_scraper/awards.py` next to the cap): *spend = direct (non-framework) contract value that passes the outlier rule in C2; framework/call-off ceilings are reported separately and labelled.* Use it from `stats.py`, `buyers_bp.py`, `suppliers_bp.py` and `market_radar.py`. Grep every use of `SINGLE_AWARD_STATS_CEILING_GBP` and `is_framework` (about 20 sites) and convert each.
2. Label each figure in the UI with its basis ("Direct contract value" vs "Framework ceilings").
3. Reuse `canonical_buyer_key` for buyer grouping in `buyer_stats` / Buyer Intelligence so Camden appears once. Keep the display name rule (prefer proper case, longest). Check `refresh_buyer_stats`'s DELETE clause uses the same key.
4. Use one authority-type classifier. Make `classify_buyer_type` call `market_radar.classify_authority` (or the reverse), add "Housing associations" detection for names such as Places for People, and re-run `buyer_type` on existing rows (a one-off script, dry-run first).

**Tests.** Camden fixtures in four spellings collapse to one buyer; a £2bn non-framework award is excluded from spend in all four modules; Places for People classifies as housing association in both classifiers.

**Product decision needed (ask before implementing):** the exact cut-off for "untrustworthy single award" (see C2).

---

## B. Supplier names holding description text (2.2) — VERIFIED

**Cause.** Awards are stored with whatever the parser took as the supplier. In the dev DB, 100 of 501,882 awards have `supplier_name` over 120 characters, 18 over 300, all from **Find a Tender** (examples start "capabilities and the very latest technologies…", "for the Provision and Implementation of an Integrated Business Solution…", "framework per product line to a maximum of 3 Framework Participants…"). 79 rows in `suppliers` also have names over 120 characters. The ingest guard at `etenders_scraper/awards.py:262`–`:275` rejects a fixed list of placeholder strings and buyer-looking names but has no length or sentence check. Market Radar's display guard `_PLACEHOLDER_SUPPLIER` (`market_radar.py:408`) is also a fixed list.

**Fix.**
1. **Ingest:** add a `looks_like_description(name)` rule in `awards.py` used by the ingest guard: reject (or store as "not named") when length > 120, or it contains a sentence (". " followed by a word, or ends with a full stop), or begins with a lower-case word such as "will", "for the", "to ", "capabilities". Choose the thresholds from the real data in step 3, not from the report's suggestion alone.
2. **Parser:** find where the Find a Tender source picks `supplier_name` (`etenders_scraper/sources/find_tender.py`, and the multi-lot parser around `awards.py:540`–`:680`) and fix the case where a free-text paragraph is taken as a name. Reproduce with one of the raw notices from the 18 examples.
3. **Data audit then cleanup:** a script (dry-run by default, `--apply` flag) that lists every supplier/award name over 100 characters and every name matching the rule, grouped by source, so thresholds can be tuned. On `--apply`, null the award's supplier link and remove orphaned `suppliers` rows. Existing data scripts to follow: `scripts/data_quality_cleanup.py`, `tender_app/supplier_data_auditor.py`.
4. **Display:** make `prepare_row` use the same rule, so Radar never shows such a name even before cleanup; show "Supplier not named".
5. `_LISTABLE_SQL` in `stats.py` and `SUPPLIER_NAME_NOT_PLACEHOLDER_SQL` in `suppliers_bp.py` must agree with the new rule (their comment says they are kept identical).

**Tests.** The three real example strings are rejected at ingest and hidden by `prepare_row`; normal long company names (for example "The Borough Council Of Gateshead", 60-ish characters, and a legitimate 100-character consortium name) are kept.

---

## C. Market Radar category accuracy and outliers (2.3)

### C1. Awards that do not belong in a category — VERIFIED
**Cause.** `CATEGORY_PRESETS` (`market_radar.py:67`) matches an award when its CPV starts with a prefix **or** its title contains a keyword as a plain substring. For CRM the keywords `crm` and `case management` over-match. Dev data shows hits outside software CPVs: "CRMCAA - Ruskin Mill College to Newent" (CPV 60140000), "Tablets for Waste In Cab CRM" (30213000), "Case Management Equipment" (75211000), "Complex Care Case Management Pilot Service" (85000000). The report's Medtronic call-off, Thornacre Engineering and Hackney "customer care services" rows come from the same mechanism; confirm each by reading its title and CPV before changing rules.

**Fix.**
1. Match keywords on **word boundaries** (`\mcrm\M` in Postgres; keep `category_matches` the Python mirror in step).
2. Require the CPV division to be plausible when a keyword matched: for IT presets, keyword matches count only if the CPV is missing or begins with `48`, `72`, `30` (hardware) or `79` as appropriate. Decide the allowed prefixes by listing the keyword-only matches per category from the data (query as above) and reviewing them. Put the allowed divisions in the preset (`keyword_cpv`).
3. Apply the same review to the other nine presets (the report says they were not checked). Produce a table per preset: CPV-matched count, keyword-only count, and a sample of 20 keyword-only titles. Fix the ones that are plainly wrong.
4. Show a per-award "why it matched" marker in the drill-down (CPV prefix vs title keyword).

**Tests.** `tests/test_market_radar_core.py` and `tests/test_market_radar_api.py` already hold preset coverage; add negative cases for each wrong title above, and positive cases for real CRM titles ("Dynamics CRM Solution Specialist" at CPV 79620000 needs a decision: keep or drop) so the boundary is explicit.

### C2. £2bn outlier dominates totals — VERIFIED
**Cause.** `prepare_row` keeps a non-framework value only if `value <= SINGLE_AWARD_STATS_CEILING_GBP` (£2bn), so exactly £2bn passes. The Salesforce row sits exactly on the cap. The dev DB holds 1,028 non-framework awards at or above £2bn, and many are rounded numbers (£2bn, £6bn, £8bn) that look like framework or DPS ceilings that were not flagged `is_framework`.

**Fix.**
1. Investigate why those rows are not flagged: is `is_framework` missing for call-offs, DPS, "agreement" or "lot" notices? Fix the flag at ingest (`awards.py` / sources) and back-fill.
2. Add an outlier rule that is relative as well as absolute: a single value above a lower ceiling (propose £250m, **confirm with product**) or above N times the category median is excluded from totals and medians and listed in a separate "Very large awards" line with a count and a link.
3. Make the cap comparison strict and shared (see A2). Cost benchmark (`cost_table`, `market_radar.py:680`): apply the same exclusion and return `excluded_outliers`; the UI shows "typical range excludes N outliers" (`web/buyer-workspace.js:~473`).
4. For the 17x range, show median and inter-quartile range as the headline and min/max as secondary.

**Tests.** A fixture category with 12 normal awards and one £2bn award: totals, medians and the benchmark exclude it, and `excluded_outliers == 1`.

---

## D. Tender Search (3.1 to 3.4)

Files: `web/app.js`, `web/search-status.js`, `web/portal-prefs.js`, `tender_app/blueprints/search_bp.py`, `etenders_scraper/progressive_search.py`. Existing tests to extend: `tests/app_state_drift_test.js`, `tests/portal_prefs_test.js`, `tests/search_status_test.js`, `tests/test_county_filter_portals.py`, `tests/test_progressive_search_status.py`, `tests/test_search_api_portal_status.py`.

### D1. Clearing the county filter changes portals and leaves stale text (3.1)
- **Behaviour (by design in code):** `applyCountyRegionPortalScoping` (`app.js:2931`) re-derives `_selectedPortals` from the counties every time the *county set changes*, and saves it. Clearing 48 counties therefore moves "3 of 7" to "6 of 7". Round 27 only stopped this running on page load.
- **Product decision needed:** the report wants portal selection independent of counties (counties may *suggest* portals). Recommended: stop auto-writing `_selectedPortals`; show a dismissible suggestion ("Your counties are served by 3 portals. Use them?") that the user accepts. Keep the existing scope hint text.
- **Stale banner:** the "N results hidden by your county filter" line (`search-status.js:185`–`:212`, `app.js:3397`) and the "Across 2 of 3 portals" header must be recomputed on every filter change, including clearing the county filter, and a result from a portal that is not in the selected set (Sell2Wales) must not be shown. Find which render path leaves them behind.
- **Tests:** extend `app_state_drift_test.js` / `portal_prefs_test.js`: clearing counties leaves `_selectedPortals` unchanged; banner text is empty when no county filter is active.

### D2. County filter hides everything for the pilot's keyword (3.2)
Notices with no stated place are dropped by the county filter (see `project_county_filter_drops_portals` notes and `tests/test_county_filter_portals.py`). Change the default: keep them, shown in a "Location not stated" group with a toggle to hide, in `search_bp.py` (filter) and the results renderer. Optionally infer location from the buyer's registered address (`tender_app/geo.py`, `buyer_locations`). Test: a notice with no place and a county filter is returned in the "not stated" group, not removed, and counted separately.

### D3. Counts do not reconcile (3.3)
Define each number once (fetched, matched keyword, after filters, saved earlier) and return all of them from the API; the UI shows them in a tooltip and they must satisfy `fetched - dropped_by_each_filter = shown`. Do not change "+N saved earlier" during a retry unless the saved set changed. Reproduce first with the "Construction" search (304 chips total vs 212 "before filters" vs 158 hidden vs 13 shown) and the repeated "plumbing" retries; find where `212` and `158` are computed. Test: a job result where the identity above holds exactly, and stable saved-earlier counts across two retries.

### D4. Smaller items (3.4, all P3)
- "119 of 15,422": say it is a sample and offer "Load more".
- Label old or deadline-less "Active" notices ("No deadline stated", "Published 2021").
- "Balfour Beatty Civil Engineering Limited" as authority: label supply-chain opportunities and do not link them to Buyer intel.
- After completion hide or disable **Pause/Stop** (`app.js:7909`–`:8016`, `btnPauseSearch`).
- Find a Tender HTTP 429: automatic back-off with retry, a short cache for repeated queries, and request spacing (`etenders_scraper/sources/find_tender.py`). Keep the typed "rate-limited" status and per-portal Retry from Round 27.

---

## E. Market engagement and the "Latest contract" column

### E1. Market engagement (4.1)
Files: `web/buyer-engagement.js`, `tender_app/market_engagement.py`, `tender_app/blueprints/market_engagement_bp.py`; tests `tests/test_market_radar_*.py`, `tests/buyer_workspace_js_test.js`.
- **40 suppliers pre-ticked:** `shortlist_suppliers` rows are inserted as included. Insert them as not included. Counts and the "N of M included" hint already exist.
- **"Typical contract £45m":** `shortlist_suppliers` takes `avg_contract` from `suppliers_table`, which uses `value_ok`; huge values mean the C2 outlier problem, not a UI bug. Re-check after C2 and show the median rather than the mean.
- **Step 1 ticked while empty:** `step_states` (`market_engagement.py:257`) marks step 1 done when title and category exist. Require the fields the notice needs (estimated value, term, deadline, contact) as well; drive from the same `missing` list that `build_notice_text` returns.
- **Default category:** new plans default to housing; pass the Radar category through when starting from Radar.
- **Draft status:** show "Unsaved draft" between Generate and Save (`stepSummary`, `buyer-engagement.js:120`).
- **Placeholder:** show `£2,100,000`-style hints, not `2100000` (the number input stays numeric; add a visible formatted hint).

### E2. "Latest contract" column (2.5) — VERIFIED
`peers_table` (`market_radar.py:563`) already returns `latest.title`, but the template prints the supplier (`web/buyer-workspace.js:368`). Show the title, with the supplier in "Main supplier"; fall back to "Title not published". Also `latest` is chosen by `signed` date (`_newest_first`) while `started` is displayed: show the same date that was used for ordering and label it ("awarded" or "started"). Test in `tests/buyer_workspace_js_test.js`.

---

## F. P3 polish (2.6, 4.2, 3.4)

- **Empty columns:** "Repeat business" needs at least 3 buyers per supplier and "Price band" needs at least 6 suppliers with 2+ valued contracts, so they are blank in small categories (`suppliers_table`, `market_radar.py:620`). Hide the two columns when no row has a value; keep the explanation in the hint.
- **Name display:** one formatter (title-case, exceptions for PLC, UK, LLP, NHS, LTD; "Of"→"of"; trim trailing punctuation such as "Gristwood and Toms:"), used by the Radar tables, Buyer Intelligence cards and the dashboard. Prefer the formatter in the display layer over rewriting stored names.
- **Spacing** in the organisation search result: "London Borough Of CamdenLondon boroughs" (missing separator in `web/buyer-workspace.js:~732`).
- **Pluralisation:** `plural()` at `buyer-workspace.js:38` gets `1 published contracts` and `1 awards without a value`; fix the call sites at `:714` and `:732` and the "without a value" string.
- **Currency formatting** in Tender Search: one formatter (£ prefix, no pence, compact units) across ProContract, Contracts Finder and Find a Tender rows.
- **CPV search:** accept a full eight-digit CPV (`resolve_category` / `suggest_categories`, `market_radar.py:189`, `:938`): match on leading digits, and change the hint ("such as 5072") to a real example.
- **Dashboard windows (4.2):** tiles say "6 months" and "last 5 months" while Radar defaults to 3 years (`buyer-workspace.js:663`–`:675`). Use one stated window, or label each clearly.

---

## Cross-cutting checks before finishing
- After A and C, re-run the report's numbers on dev data: the same buyer must show identical totals in feed, search and profile; the CRM category must contain only CRM and case-management software awards; headline totals must change when the outlier is excluded.
- Re-run the mutation approach used for Growth Studio on any new rule (supplier-name guard, category matcher, outlier rule) to be sure the tests actually bite.
- Check production-affecting migrations: the supplier-name cleanup and buyer-type re-classification must be dry-run first, reversible or backed up, and run by the owner.

## Out of scope / still untested by QA
Mobile and tablet widths of the Buyer workspace, dark mode, the 1290px filter bar, Follow category saving, "Direct contracts only" and the 12-month/all-time filters, the Total contract value toggle, engagement steps 4 and 5, and classification review of the other eight categories (covered in C1 step 3). Test data left on the QA account (organisation = Camden, a draft "QA test CRM replacement (delete me)") is the owner's to delete.
