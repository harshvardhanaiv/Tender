# Company Profile vs Answer Bank

When to use each, how they differ, and what the codebase actually does today.

---

## One-line rule

| Feature | Use it when you need to… |
|---|---|
| **Company Profile** | Describe **who the company is** so TenderFlow can score fit and give the bid writer company context. |
| **Answer Bank** | Store **pre-approved answers** to recurring tender questions so the AI reuses them **verbatim** instead of inventing facts. |

They are complementary, not alternatives. Most serious users should maintain **both**.

---

## Decision guide: when to use which

### Use **Company Profile** when…

1. **Deciding whether to bid** — Fit Score (quick + deep) needs a profile. Without one, scoring cannot run against your capabilities.
2. **Capturing structured company facts** — NACE/sector, turnover, geography, size, contacts, DUNS, website, LinkedIn, etc. (`meta_json`).
3. **Capturing a narrative capability summary** — history, services, differentiators, certifications (`profile_text`). This is keyword-rich context for matching tenders.
4. **Attaching supporting documents** — CVs, certificates, brochures, past-performance packs linked to that profile.
5. **Bidding as a specific entity** — You can keep multiple profiles (e.g. parent company vs trading division) and select one per tender.

**Put in Profile:** “We are a mid-sized facilities management company, NACE N, ~€12m turnover, based in Dublin, ISO 45001, typically deliver soft FM for local authorities…”

### Use **Answer Bank** when…

1. **You already have approved wording** for questions that appear on many ITTs (quality, H&S, social value, methodology, team, pricing approach).
2. **You must not invent facts** — case studies, client names, statistics, policy statements, accreditation claims.
3. **Compliance/legal has signed off** a paragraph that bid writers must reuse.
4. **You want Stage 3 (bid drafting) to auto-fill** from a reusable Q&A library instead of starting from scratch every time.

**Put in Answer Bank:**  
Category: `Health & Safety`  
Q: *Describe your approach to health and safety management*  
A: *(exact approved paragraph, word-count aware)*

### Do **not** confuse them

| Content type | Prefer |
|---|---|
| Annual turnover, sector codes, office locations | **Profile** (`meta_json`) |
| Free-text “who we are / what we do” for matching | **Profile** (`profile_text`) |
| Uploaded PDFs/Word evidence packs | **Profile documents** |
| Reusable scored Q&A paragraphs | **Answer Bank** |
| Named case studies with metrics (approved text) | **Answer Bank** |
| “Should we bid on this?” | **Profile** → Fit Score |
| “Write the H&S method statement for this ITT” | **Answer Bank** → Stage 3 |

**Rule of thumb:** Profile = identity & eligibility. Answer Bank = reusable bid prose.

---

## Side-by-side comparison

| | Company Profile | Answer Bank |
|---|---|---|
| **Purpose** | Identity + capability for fit & context | Pre-approved reusable bid answers |
| **Granularity** | One (or few) company records | Many Q&A items |
| **Shape** | `name`, `profile_text`, `meta_json` + documents | `category`, `question`, `answer` |
| **Selection** | User picks a profile per score / bid | Auto-loaded for the logged-in user (all items) |
| **Used in Fit Score?** | **Yes** | **No** |
| **Used in bid Stage 3?** | **Yes** (as company context / docs) | **Yes** (verbatim injection) |
| **AI instruction** | Treat as background context | Prefer / use **verbatim**; do not paraphrase |
| **UI entry** | Company Profiles modal (`#cpModal`) | Answer Bank modal (`#abModal`) |
| **API** | `/api/company-profiles` (+ documents) | `/api/answer-bank` |
| **Scoped by** | `username` + profile `id` | `username` only (not per-profile) |

---

## Intended product workflow

```
1. Create / maintain Company Profile(s)
        │
        ▼
2. Search tenders → Fit Score uses Profile
        │  (decide pursue / pass)
        ▼
3. Maintain Answer Bank (common approved answers)
        │
        ▼
4. Run Bid Assistant on a chosen tender
        │
        ├─ Stage 1: dissect tender docs
        ├─ Stage 2: compliance checklist
        ├─ Stage 3: draft response
        │     • company name from selected Profile
        │     • company context = profile_text + selected/uploaded docs
        │     • Answer Bank injected as pre-approved Q&A
        └─ Stage 4+: clarifications / timeline
```

---

## Implementation status in this codebase

**Short answer: yes — the intended split is largely implemented.** Profile drives fit + company context; Answer Bank drives verbatim Stage 3 injection. A few design nuances / gaps remain (see below).

### What is implemented

#### Company Profile — implemented

| Capability | Status | Where |
|---|---|---|
| CRUD profiles (`name`, `profile_text`, `meta_json`) | Done | `tender_app/blueprints/profiles_bp.py` → `/api/company-profiles` |
| Profile documents upload/list/download/preview | Done | Same blueprint → `/api/company-profiles/<id>/documents` |
| AI generate / enrich profile | Done | `/api/company-profiles/generate-info`, enrich endpoints |
| Fit Score requires & uses profile | Done | `server.py` → `POST /api/fit-score` loads `meta_json` + `profile_text` |
| Bid setup: select profile | Done | `web/index.html` `#bidCompanyProfileSelect`, `web/app.js` |
| Bid pipeline gets company name from profile | Done | `web/app.js` derives `company` from selected profile name |
| Bid pipeline gets `profile_text` as context | Done* | Frontend encodes `profile_text` into a synthetic company file before `/api/proposal` |
| Bid pipeline can include profile documents | Done | Checked docs → `user_document_ids` loaded server-side |

\*Not as a separate prompt field named “Bidder Profile”; it is merged into **ADDITIONAL COMPANY CONTEXT** via uploaded/synthetic company files.

#### Answer Bank — implemented

| Capability | Status | Where |
|---|---|---|
| Table `answer_bank` (category, question, answer) | Done | `server.py` schema init (Postgres + SQLite) |
| CRUD API | Done | `profiles_bp.py` → `GET/POST /api/answer-bank`, `PUT/DELETE /api/answer-bank/<id>` |
| Dedicated manage UI + categories | Done | `web/index.html` Answer Bank modals; `web/app.js` `initAnswerBank()` |
| Default categories (H&S, Social Value, etc.) | Done | `AB_DEFAULT_CATS` in `web/app.js` |
| Injected into Stage 3 bid drafting | Done | `server.py` `run_bid_pipeline` / proposal stream fetches all user answers |
| Verbatim instruction to the model | Done | Stage 3 user prompt: *“use these verbatim where relevant — do NOT paraphrase”* |
| Char budget for injection | Done | `smart_chunk(..., max_chars=8000)` on answer bank context |

### Design vs code: intentional differences

| Original plan / methodology | Actual implementation |
|---|---|
| `PLAN.md` Phase 4: field `question_key` | Uses free-text `category` + `question` (no `question_key`) |
| `PLAN.md`: manage UI as tab inside `#cpModal` | Separate Answer Bank modal (`#abModal`) — clearer UX |
| `METHODOLOGY.md`: Stage 3 inputs “Bidder Profile + Answer Bank” as peer channels | Answer Bank is a dedicated prompt block; Profile narrative arrives via **company context documents** (including auto-injected `profile_text` file) |
| Implied per-question smart matching | **All** Answer Bank rows for the user are injected (then truncated to ~8k chars) — no category↔ITT matching filter yet |
| Implied per-profile answer sets | Answer Bank is **per username**, shared across all of that user’s profiles |

### Gaps / not implemented yet

1. **No Answer Bank in Fit Score** — by design today; scoring uses Profile only.
2. **No automatic “promote bid answer → Answer Bank”** — users add answers manually.
3. **No per-profile Answer Bank** — if two legal entities need different approved wording, they currently share one bank (or need separate user accounts).
4. **No semantic retrieval** — large banks may truncate; relevance is left to the LLM over the dumped list.
5. **Profile `meta_json` is not separately injected into Stage 3** — structured fields power Fit Score; Stage 3 mainly sees `profile_text` + documents + Answer Bank. Structured meta only helps bidding indirectly (name selection, docs, scoring upstream).

---

## Practical examples

### Example A — New user, first week

1. Create a **Company Profile** (AI generate or manual): sector, turnover, locations, rich `profile_text`.
2. Run searches and use **Fit Score** to shortlist.
3. After first bid review, copy approved H&S / Quality / Social Value paragraphs into **Answer Bank**.
4. Next bid: select the same profile, run Bid Assistant — Stage 3 should lean on the bank for those themes.

### Example B — What goes where for “ISO 9001”

| Statement | Store as |
|---|---|
| “We hold ISO 9001:2015” as a capability fact for matching | Profile (`profile_text` / meta / certificate PDF) |
| Full scored answer: “Our quality management system is certified to ISO 9001… [200 words]” | Answer Bank (category `Quality`) |

### Example C — Wrong placement

| Mistake | Why it hurts |
|---|---|
| Putting only Answer Bank entries, no Profile | Fit Score cannot run; weak company identity for bidding |
| Dumping long approved Q&A only into `profile_text` | Fit Score context gets noisy; Stage 3 may paraphrase instead of treating text as locked answers |
| Putting turnover / NACE only in Answer Bank | Fit Score never sees it |

---

## Code reference map

| Concern | Primary files |
|---|---|
| Product methodology (Stage 3 + Answer Bank injection) | `METHODOLOGY.md` §3.3 |
| Original MVP plan (Phase 4) | `PLAN.md` §Phase 4 |
| Profile + Answer Bank APIs | `tender_app/blueprints/profiles_bp.py` |
| Fit Score (Profile only) | `server.py` → `/api/fit-score` |
| Bid pipeline Stage 3 injection | `server.py` → proposal / `run_bid_pipeline` (Answer Bank fetch + Stage 3 prompt) |
| Profile → company_files for bid | `web/app.js` (encode `profile_text` before `/api/proposal`) |
| Answer Bank UI | `web/index.html`, `web/app.js` (`openAnswerBank`, `AB_DEFAULT_CATS`) |
| Profile UI | `web/index.html` `#cpModal`, `web/app.js` company profile helpers |

---

## Summary checklist for users

**Before searching / scoring**

- [ ] At least one Company Profile with `meta_json` + solid `profile_text`
- [ ] Profile selected in the Fit Score / filter bar

**Before generating a strong bid**

- [ ] Correct Company Profile selected in Bid Setup
- [ ] Relevant profile documents checked (if any)
- [ ] Answer Bank filled for your recurring themes (Quality, H&S, Social Value, Experience, Methodology, Team, …)

**Ongoing**

- [ ] After a winning or approved response, promote reusable paragraphs into Answer Bank
- [ ] Keep Profile facts current (turnover, locations, certifications) so Fit Score stays trustworthy

---

*Last verified against the Tender codebase implementation of company profiles, answer bank CRUD, Fit Score, and Stage 3 bid drafting.*
