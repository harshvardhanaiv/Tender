# TenderFlow MVP Development Roadmap & Gantt Chart

This document outlines the detailed timeline, phase dependencies, milestones, and resource allocation for the development and launch of the **TenderFlow** MVP.

---

## 1. Project Timeline & Gantt Chart

The MVP development timeline is structured over a **4-week** execution phase (Phases 0, 1, 2, 3, and 6), followed by a **1-week** maintenance & optimization phase (Phases 4 and 5).

```mermaid
gantt
    title TenderFlow MVP Implementation Timeline (June - July 2026)
    dateFormat  YYYY-MM-DD
    axisFormat  %b %d

    section Phase 0: Design Foundation
    Define Design Tokens (styles.css)    :active, p0_1, 2026-06-19, 3d
    Dark Theme & Font Styling (Outfit/Inter) :p0_2, after p0_1, 3d
    Remove Inline CSS & Class Standardisation :p0_3, after p0_2, 3d
    Standardize Buttons, Badges, Modals  :p0_4, after p0_3, 2d
    Milestone: Design System Frozen       :milestone, m0, after p0_4, 0d

    section Phase 1: UX Redesign
    Results View Toggle (Table / Cards)    :p1_1, after p0_4, 3d
    Skeleton Loaders & Portal Status Chips :p1_2, after p1_1, 3d
    Rich Search Filters & Saved Search Bar :p1_3, after p1_2, 4d
    Responsive Mobile UI Adjustments       :p1_4, after p1_3, 2d

    section Phase 2: Fit Scoring (AI)
    Tier 1 Rules-Based Score (Financial/Geo) :p2_1, after p1_2, 4d
    Tier 2 AI Deep Score (DeepSeek Setup)     :p2_2, after p2_1, 5d
    Fit Score Caching & UI Badges         :p2_3, after p2_2, 3d
    Milestone: AI Discovery Suite Live   :milestone, m2, after p2_3, 0d

    section Phase 3: Pipeline & Tracking
    Saved Searches (Server-Side SQLite/PG) :p3_1, after p1_3, 4d
    Tender Tracking & Pipeline Board (Kanban):p3_2, after p3_1, 5d
    Dashboard Analytics Landing Page       :p3_3, after p3_2, 4d

    section Phase 5: Maintainability
    Blueprints & JS Modularization        :p5_1, after p2_3, 5d

    section Phase 6: Security Hardening
    Secret Rotation & Env Configuration   :p6_1, after p3_3, 2d
    Login Rate-Limiting & Password Policy  :p6_2, after p6_1, 3d
    Data Isolation Audit & HttpOnly Cookies:p6_3, after p6_2, 3d
    Milestone: Production Ready Release    :milestone, m4, after p6_3, 0d
```

---

## 2. Milestone Deliverables

| Milestone ID | Title | Date | Targets & Deliverables |
|:---|:---|:---|:---|
| **M0** | Design System Frozen | `2026-06-30` | Core tokens established in `web/styles.css`, light/dark themes working, consistent button/input styling across the entire app. |
| **M1** | Discovery UX Redesigned | `2026-07-06` | Unified search, list-card toggle, skeleton loaders showing progress across 4 government portals, filters functioning. |
| **M2** | AI Discovery Suite Live | `2026-07-15` | Multi-portal tender search with Tier 1 and Tier 2 Fit Scoring. DeepSeek integration fetching full documents and caching scores in the SQLite/Postgres DB. |
| **M3** | CRM Board & Saved Search | `2026-07-20` | User dashboard showing saved searches and Kanban board representing tender phases (Watching → Bidding → Submitted → Won/Lost). |
| **M4** | Production Ready Release | `2026-07-28` | DeepSeek secrets rotated out of codebase, password/cookie security implemented, data isolated by username context, final end-to-end testing complete. |

---

## 3. Detailed Phase Breakdown & Tasks

### Phase 0: Design Foundation (Est. Duration: 11 Days)
Focuses on establishing a unified aesthetic (premium glassmorphism, Outfit/Inter typography, clean color variables) to ensure later stages adapt immediately to changes.
- **Task 0.1**: Build CSS Custom Properties system under `:root` for standard sizing, colors, font-sizes, shadows, borders.
- **Task 0.2**: Implement system-wide dark mode triggered via HTML attribute `[data-theme="dark"]` and toggle button.
- **Task 0.3**: Refactor `web/index.html` to eliminate all utility inline styles. Move styles into standard components.
- **Task 0.4**: Implement standard design blocks for buttons, status badges, forms, and modal chrome.

### Phase 1: Results & Discovery UX (Est. Duration: 12 Days)
Transforms list view from a basic table into a modern portal search client.
- **Task 1.1**: Build list card component view option for search results.
- **Task 1.2**: Implement async loading chips showing search status, response timing, and captcha notices.
- **Task 1.3**: Add price threshold range inputs and buyer location/authority selectors.
- **Task 1.4**: Configure local saved-search list using LocalStorage.

### Phase 2: Fit Scoring Integration (Est. Duration: 12 Days)
Incorporates DeepSeek AI and business logic to score tender suitability.
- **Task 2.1**: Implement backend helper mapping CPV codes, location NUTS codes, and bidder turnover checks.
- **Task 2.2**: Integrate `deepseek-chat` for deep capability analysis.
- **Task 2.3**: Establish `fit_scores` caching database table to store previous computations and prevent redundant API queries.
- **Task 2.4**: Create visual indicators (Strong, Possible, Weak) and details tooltips on search results card components.

### Phase 3: Pipeline & Tracking CRM (Est. Duration: 13 Days)
Implements user tracking mechanisms for active bids.
- **Task 3.1**: Model SQLite/Postgres tables for saved search configuration and persistent queries.
- **Task 3.2**: Build Kanban column component board showing statuses (`Watching`, `Bidding`, `Submitted`, `Won/Lost`).
- **Task 3.3**: Create simple overview stats showing upcoming deadlines, win/loss ratio, and tracked tenders.

### Phase 5 & 6: Clean-up, Security & Hardening (Est. Duration: 13 Days)
Critical final steps to separate developer settings from production keys, secure database reads, and prepare for hosting.
- **Task 5.1**: Split the monolithic `server.py` into Flask Blueprints (`auth`, `search`, `ai`, `pipeline`, `db`) and separate Javascript into import modules.
- **Task 6.1**: Extract all keys (DeepSeek API, Firebase credentials) to `.env` variables and verify `.gitignore` lists them.
- **Task 6.2**: Establish rate limiters on public-facing routes and enforce password constraints.
- **Task 6.3**: Implement strict user verification checks and isolated query boundaries on SQLite/Postgres reads.
