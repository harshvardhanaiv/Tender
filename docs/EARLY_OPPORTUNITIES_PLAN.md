# TenderFlow — Early Signals Plan (Planning Leads as pre-tender intelligence)

> Status: living document. Companion to the Product Strategy Note (benchmarked against
> Tussell & Stotles, 21 Sep 2026) and the Round 10 QA note. Triggered by a read of
> Tussell's "Early Opportunities" product page.

## 1. What Tussell Early Opportunities actually is

Tussell launched **Early Opportunities** as a separate, AI-powered module sitting above
its core spend-data product. It is **not** spend/invoice data — it's a signal-detection
product, which means Tussell now directly overlaps with what Stotles' "Signal Score" does.
Concretely, per Tussell's own product page:

- **Data harvesting** — thousands of government documents scanned monthly across three
  verticals: Central Government, Local Government, NHS. Sources include meeting minutes,
  strategy papers, procurement pipelines/forward plans, and budget documents.
- **AI + human verification** — an AI engine flags candidate buying signals in that
  unstructured text; Tussell's own "public sector data experts" manually review and
  categorize each one before it's published. This is explicitly a hybrid pipeline, not
  pure AI — accuracy is the selling point, which lines up with Tussell's broader
  "trusted data" brand position (see Section 3 of the Product Strategy Note).
- **Structured output** — each verified signal becomes a searchable record: buyer,
  category, and a link back to the source document.
- **Positioning** — marketed as "a step further up the funnel" than tender alerts:
  buyers are engaged "months before rivals," before a spec is even fixed, letting a
  supplier help shape the eventual tender rather than just respond to it.
- **Workflow fit** — sold into capture management / BD teams for account
  prioritization, not procurement teams.
- **Named users** — enterprise accounts (Microsoft, AWS, Google, Capita, Serco, BT, EY,
  Jacobs, Oracle, Virgin Media O2, Reed), i.e. large bid/capture teams with dedicated BD
  headcount to act on the leads.

## 2. What this changes about TenderFlow's competitive position

The original Product Strategy Note (Section 01) framed "pre-tender buying-signal
detection" as Stotles' differentiator and TenderFlow's Planning Leads as the answer to
it. Early Opportunities means **Tussell has now shipped the same category of product**,
so the comparison is no longer "Stotles does document-mining, Tussell doesn't." Two
things still hold, and one thing needs sharpening:

- **Still true — Planning Leads is structurally different, not just a smaller version
  of the same idea.** Tussell/Stotles infer a signal from unstructured prose (a line in
  a committee minute, a mention in a forward plan) via AI + manual review — slow to
  produce, and the underlying fact is soft (a stated intention, not a formal record).
  TenderFlow's `planning_applications` table (`etenders_scraper/planning/`) is built on
  **structured statutory register data from PlanIt** — a formally submitted planning
  application with a real address, lat/long, dwelling count, applicant/agent company
  name, and a legal decision status. For construction, fit-out, M&E, security and FM
  categories, this is a harder, earlier, and cheaper-to-produce signal than anything
  document-mining can extract — it requires no AI inference step to exist at all, only
  scoring (`etenders_scraper/planning/scoring.py::explain_lead_score`) and PII/GDPR
  suppression (`/api/admin/planning/suppress` in `planning_bp.py`) on top.
- **Still true — TenderFlow's product surrounds a signal with search, buyer/supplier
  intel and bid drafting in one login; Tussell's doesn't natively include tender search
  or a bid wizard.**
- **Needs sharpening — "we have a forward-looking data source and they don't" is no
  longer the pitch.** The pitch has to become **"our forward-looking data is a
  structured government record, not an AI guess at a sentence in a PDF"** — precision
  and provenance as the wedge, not novelty. That also means the data-trust work in
  Section 3 of the Strategy Note (label what every number *is* — direct record vs.
  inferred signal vs. estimate) applies to Planning Leads too, and should be shipped
  alongside anything below, not after it.
- **Genuine remaining gap — non-construction categories.** Planning applications only
  signal construction-adjacent demand. A council's forward plan announcing an upcoming
  IT re-procurement, or a budget paper flagging new adult-social-care spend, has no
  planning-register equivalent. That's the one thing Early Opportunities covers that
  Planning Leads structurally cannot — scoped as Phase D below, deliberately last.

## 3. The build, grounded in what already exists

### Phase A (Now) — Make Planning Leads behave like a feed, not a lookup module
Directly extends Planning Leads: this is what Section 5 of the Strategy Note calls the
single highest-leverage change, and it's also the fastest way to make TenderFlow's
version of "Early Opportunities" real without new data acquisition.

- **A.1** New `GET /api/planning/feed` route in `tender_app/blueprints/planning_bp.py`.
  Joins the user's `company_profiles.meta_json` (`business_type` — the NACE sector
  letter already captured per `profiles_bp.py:2450`) and their existing
  `alert_keywords` preference (`profiles_bp.py:954/990`, already used for tender email
  alerts) against `planning_applications` filtered to `OPPORTUNITIES_SQL` (excludes
  paperwork/minor-works/duplicates, per `scoring.py`), `lead_score >= <threshold>`, and
  `start_date`/`decided_date` in the last N days. No new schema needed for a first cut —
  this reuses fields that already exist.
- **A.2** Surface it as a first-class card on the dashboard/landing view (the same
  landing surface Phase 3.4 of `docs/PLAN.md` already earmarks for "new high-fit
  matches"), e.g. *"3 new major schemes in your sector, decided this week"* — not a
  module the user has to remember to open.
  - **Acceptance:** a user with `business_type = F` (Construction) and no manual
    filters sees relevant new/decided Large or Medium schemes on login without
    visiting `/planning` first.
- **A.3** Planning-specific saved-search alerting, mirroring the pattern already proven
  for tender search (`saved_search_cache` table in `tender_app/db_ext.py`, sent via
  `tender_app/email_notifier.py`). Add a `planning_search_cache` table
  (`saved_search_id, application_id, first_seen_at, status`) with the same
  first-seen/still-active diffing logic, so a saved planning query emails only genuinely
  new matches instead of the whole result set each run.
  - **Acceptance:** saving a planning search with a filter sends an email only when a
    *new* application matching it appears, using the existing nightly digest
    infrastructure — no new alert-sending code path.

### Phase B (Now) — Let a user track a scheme the way they track a tender
Directly extends Section 9 (Pipeline → CRM) using the same table, not a parallel one.

- **B.1** Add `source_type VARCHAR(20) NOT NULL DEFAULT 'tender'` and
  `expected_tender_date TEXT` columns to the existing `pipeline` table (`server.py:298`).
  A planning scheme becomes a pipeline row with `source_type='planning'`,
  `tender_key` = the planning application `uid`, and a note like *"expect an FM/security
  tender ~9 months after decision"* stored in the existing `notes` field.
- **B.2** Add a "Track" action on each planning application card/detail view
  (`web/planning.js`), calling the same pipeline-insert endpoint tender cards already
  use, just with `source_type='planning'` and the planning record's fields mapped onto
  `title`/`contracting_authority`/`estimated_value` (left null — Planning Leads
  deliberately carries no value field, per the docstring in `planning_bp.py:8-10`, and
  that should stay true here rather than inventing an estimate).
  - **Acceptance:** a tracked planning scheme shows up on the existing Pipeline kanban
    board next to tracked tenders, distinguished by a badge, not a separate board.

### Phase C (Next) — Cross-link a scheme to its authority's live tenders and awards
Directly extends Section 12 (one buyer page, not three modules) and costs no new data —
`planning_applications.authority` and the buyer name used by Buyer Intelligence/tender
search should already refer to the same councils.

- **C.1** On a planning application's detail page, add a panel pulling that authority's
  live tenders (existing `/api/search` scoped by buyer) and past supplier awards
  (existing Buyer Intelligence data) — read-only cross-links, no schema change.
- **C.2** Normalize `planning_applications.authority` strings against whatever
  canonical buyer-name table Buyer Intelligence already uses, so the join is exact-match
  rather than fuzzy (check for existing mismatches first — e.g. "Kent County Council" vs
  "Kent CC" — before shipping the join).
  - **Acceptance:** opening a Large/Permitted scheme under a given council shows that
    council's currently-live tenders and top incumbent suppliers on the same screen.

### Phase D (Later, and scope carefully) — Document-mined signals for non-planning categories
This is the part that is actually equivalent to what Tussell built — mining budgets,
forward plans and committee minutes for buying intent in categories planning registers
don't cover (IT, professional services, social care, etc.). Flagging it explicitly as
**last** and **scoped**, for the same reason Section 8 (decision-maker contacts) in the
Strategy Note is scoped as a later, build-vs-partner decision rather than an immediate
build:

- It requires a genuinely new harvester (analogous to
  `etenders_scraper/planning/harvester.py`, but for unstructured PDFs/HTML across
  thousands of council/NHS/central-gov publication pages) plus an AI-classification step
  *and* the human-verification layer Tussell explicitly keeps in its own pipeline —
  without the human check, this is exactly the kind of AI-fabricated-confidence problem
  the Round 10 QA note already flagged in AI Summary, applied to a new surface.
- Recommend treating this as **research-then-decide**, not committed scope: spike
  a narrow pilot (e.g. one sector, one category of source document — local authority
  forward plans, which are usually a single structured PDF/table per council, unlike
  meeting minutes) before deciding whether to build the general case or license/partner
  for it.

## 4. Phasing

| Phase | Focus | Depends on | Why this order |
|---|---|---|---|
| Now | A — Planning Leads as a personalised feed + saved-search alerts | existing `planning_bp.py`, `company_profiles.meta_json`, `saved_search_cache` pattern | Highest leverage-to-effort ratio; no new data acquisition |
| Now | B — Track a scheme in Pipeline | existing `pipeline` table | Small schema change, immediately useful, consistent with Strategy Note Section 9 |
| Next | C — Unified authority page (planning ⨯ tenders ⨯ suppliers) | A, existing Buyer Intelligence + tender search | Needs 2+ modules worth cross-linking to justify the page |
| Later | D — Document-mined signals for non-construction categories | none of the above strictly, but should follow data-trust work (Strategy Note Section 3) | Large new build; needs a scoped pilot and a build-vs-partner call before committing |

## 6. Addendum (2026-09-22) — decision-maker contact data was reclassified, and shipped

The Product Strategy Note this document is a companion to placed "decision-maker contact
data" (its Section 08) in the **Later — biggest lift** bucket, reasoning by analogy with
Tussell's 80,000+ contact database: a big, expensive, GDPR-sensitive build. A code read
found that reasoning didn't hold for TenderFlow specifically — most of the pipeline
already existed:

- The canonical scrape schema (`etenders_scraper/fields.py`) already captures
  `contact_name` / `contact_email` / `contact_phone` per notice, populated by
  `contracts_finder.py`, `find_tender.py`, `bravo_search.py`.
- Every searched notice, contact fields included, is already cached as raw JSON in the
  `tenders_master` table (the same table `buyers_bp.py::_fetch_open_opportunities`
  already reads for "open opportunities").
- The Buyer Intelligence API already returned a `contact_info` object, and the frontend
  drawer (`web/app.js`) was already fully wired to render it — `email`, `phone`,
  `website`, `address` fields all present in the DOM.
- The only actual gap: `buyers_bp.py::get_buyer_detail` hardcoded `contact_info` to
  `"Not available"` for every field, with a comment ("Honest contact details — no
  cross-contamination or fabrication") indicating this was a deliberate stub, not an
  oversight — likely added for the same reason the Round 10 QA note flags AI-fabricated
  buyer data elsewhere in the app: better to show nothing than something wrong.

**Shipped:** `buyers_bp.py` now has `_fetch_buyer_contact_info()`, which reads the most
recent `tenders_master` row for that authority with a real `contact_email`/`contact_phone`
and returns it — never inferred, never AI-generated, same honesty guarantee the stub's
comment already promised, just backed by real data instead of a static string. Wired into
both `get_buyer_detail` code paths (the zero-award-history branch and the normal branch).
No frontend change was needed.

**Still open, genuinely later work:**
- No `website`/`address` field exists anywhere in the canonical scrape schema, so those
  two fields will keep showing "Not available" until/unless that's added as a new field
  to the scrapers — not attempted here, to avoid guessing at a source that doesn't exist.
- Contact info is only surfaced on the Buyer Intelligence drawer today; showing it on
  individual tender search-result cards would be a `web/app.js` render-path addition, not
  a backend change (the data already flows through the same tender record).
- This is a *derived-from-notices* contact, not Tussell's licensed org-chart database —
  it'll be sparse for buyers with few recently-scraped notices, and it returns whatever
  named contact a portal put on the notice (often a "Procurement Team" mailbox, not a
  named decision-maker). Building or licensing an actual decision-maker directory is
  still the "Later, biggest lift" item — this addendum only fixes the free, already-
  collected slice of that gap.

## 7. One-sentence version

Tussell's Early Opportunities proves the "signal before the tender exists" category is
now contested by both competitors — TenderFlow's answer isn't to copy their
document-mining pipeline, it's to make Planning Leads (a harder, structured, already-live
signal) impossible to ignore by pushing it into the feed, the pipeline, and the buyer
page instead of leaving it a fourth module a user has to remember to check.
