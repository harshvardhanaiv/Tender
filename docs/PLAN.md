# TenderFlow — MVP Implementation Plan

> Status: living document. Order is deliberate — **UI/UX and features first, security & login last** (per product decision).

## Guiding principle
Turn "a tool that lists tenders" into **"a product that tells me which tenders to bid on and helps me win them."** The differentiator is the **Fit Score** plus the existing AI bid workflow. Every MVP item serves that story.

## Tech reality (no rewrite — ship fast on the current stack)
- **Backend:** single Flask file `server.py` (~4,000 lines), SQLite/Postgres, DeepSeek API.
- **Frontend:** vanilla JS `web/app.js` (~3,600 lines) with a central `state` object, `renderRows()`, modal-driven AI flows. No build step.

## Key decisions
- **Fit Score = Approach B:** rules-based ("quick fit") across the whole list (instant, free) + AI ("deep fit" with reasons) on tender open. UI must clearly distinguish quick vs deep fit.
- **MVP cut-line:** Phases 0 + 1 + 2 + 3 + 6. Phases 4, 5 follow as v1.1.

---

## Phase 0 — Design foundation  *(START HERE)*
One consistent, themeable look so every later phase inherits it.
- **0.1** Define design token system in `web/styles.css` `:root` (colors, spacing, radius, shadow, font scale).
- **0.2** Add dark theme via `[data-theme="dark"]`; adopt login's Outfit/Inter fonts + gradient-accent language in the app shell.
- **0.3** Extract inline `style="..."` from `web/index.html` (filter pills, sliders, modal rows) into utility/component classes.
- **0.4** Component pass: standardize button variants, badges, inputs, modal chrome.
- **Acceptance:** app + login share one visual language; `data-theme` toggles light/dark; no inline styles in results/filter area.

## Phase 1 — Results & discovery UX redesign
- **1.1** Results view toggle: Table ↔ Card list (`renderRows()`); reuse existing `formatDaysLeft` badge.
- **1.2** Skeleton loaders + per-portal progress chips using `meta.source_counts` / `errors` / `source_timings_sec`.
- **1.3** Richer filters: value range + buyer/source (extend `#dateFilterRow` and `state`).
- **1.4** Saved-search bar + recent searches (localStorage for MVP; server-side in Phase 3).
- **1.5** Friendly empty/error states; inline NI captcha explanation instead of red banner.
- **1.6** Responsive/mobile pass (`.card--main` height, 1380px container; card view default on narrow screens).

## Phase 2 — Fit Score (flagship)
- **2.1** `POST /api/fit-score` — input: tender fields + selected profile (`meta_json` + `profile_text`); output `{score, band, reasons[]}`; reuse `call_deepseek_stage`.
- **2.2** Two tiers: **Tier 1** rules-based (NACE sector, enterprise size vs turnover, geography/NUTS, deadline feasibility); **Tier 2** AI deep score on tender open.
- **2.3** Cache: table `fit_scores(username, tender_key, score, reasons_json, created_at)`.
- **2.4** Fit badge in results (🟢 Strong / 🟡 Possible / 🔴 Weak) with reason tooltip.
- **2.5** Sort "Best fit" + filter "Fit ≥ 60".
- **2.6** "Why this score" rationale in the details panel; upgrade quick→deep fit on open.

## Phase 3 — Pipeline & saved searches (stickiness)
- **3.1** Saved searches server-side: `saved_searches(username, name, query, scope, filters_json, created_at)`.
- **3.2** Pipeline board: `pipeline(username, tender_key, stage, notes, owner, updated_at)`; stages Watching → Bidding → Submitted → Won/Lost.
- **3.3** "Save / Track" action on each tender.
- **3.4** Dashboard landing on login (tracked by stage, upcoming deadlines, new high-fit matches).

## Phase 4 — Reusable answer bank (v1.1)
- **4.1** `answer_bank(username, question_key, question, answer, updated_at)`.
- **4.2** Auto-fill questionnaire answers in `run_bid_pipeline` / `/api/proposal`.
- **4.3** Manage answers UI (extra step/tab in `#cpModal`).

## Phase 5 — Backend maintainability (parallel enabler, v1.1)
- **5.1** Split `server.py` into Flask blueprints (`auth`, `search`, `ai`, `profiles`, `pipeline`, `export`, `db`).
- **5.2** Modularize `app.js` into `web/js/*.js` via `<script type="module">`.
- **5.3** Centralize DeepSeek client + config in env.

## Phase 6 — Security & login hardening  *(LAST)*
- **6.1** *(Critical)* Move secrets to env + **rotate** committed DeepSeek key (`server.py:340`) and Flask secret (`server.py:583`); verify `.gitignore`.
- **6.2** Strengthen auth: stronger password rules, login rate limiting, optional email verification.
- **6.3** Password reset flow (email token).
- **6.4** Session hardening: Secure/HttpOnly/SameSite cookies, expiry, CSRF on POST.
- **6.5** Remove hardcoded `LOGIN_USERNAME/PASSWORD` fallback (`server.py:678`).
- **6.6** Per-user data isolation audit (every query scoped by `username`).

---

## Rough timeline
MVP cut-line (Phases 0,1,2,3,6) ≈ 3–4 weeks focused work.
