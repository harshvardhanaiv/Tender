# Feedback round: what is built, what needs a decision

Status of the numbered feedback items. "Built" means it is on the branch with tests. "Needs input" means the work depends on an answer or a list only the product owner can give.

## 1. Map and location: built
- 📌 Pin button beside GPS in the Supplier, Buyer and Planning distance panels (`web/location-pin.js`). Drop a pin, drag it, search a postcode, and **pick the distance on the same map** (a slider draws the circle; "Any distance" is a tick box).
- The last place is remembered per user in the browser and restored until they change it or press ✕. It wins over the company-profile postcode.
- Growth Studio has no distance filter: award notices carry no reliable buyer address (its place box matches buyer names). A map filter there would need buyer coordinates, which the Buyer feed estimates from the authority name. Not done; say if you want it.

## 2. Global watchlist and pipeline: built
- Buyers and suppliers can be starred in Supplier Intelligence, Buyer Intelligence (profile and feed cards) and Growth Studio. The Pipeline page lists them with the section they came from; tracked tenders carry "+ Tender search" or "+ Open opportunity".
- The Pipeline page now scrolls.

## 3. Supplier Intelligence fit score: built (on the Buyer Intelligence feed)
Hover or focus the match badge: four weighted factors with a sentence each, plus an *estimated* engagement likelihood. "Why it fits" is always visible. Min fit selector filters the feed. The likelihood is a heuristic ranking aid from public award data, not a prediction of winning, and says so.

## Growth Studio: built
- Multi-select services (up to 12 at once); signals match **any** of them and each signal lists which of your services its contracts fall under. Equal-fit signals that touch more of your services rank first.
- 51 built-in services in 7 groups (was 10). Every CPV branch in the awards data is searchable and can be added as a service, so the list is not fixed.
- Users can add their own services (name, title words, CPV codes). They are **private**. "Offer to all users" only queues them: an administrator approves with `POST /api/admin/growth-services/<id>/visibility {"visibility":"shared"}` (list: `GET /api/admin/growth-services`). This stops one person's typo or spam appearing in everyone's list.
- Min fit selector (Any, 50, 60, 75, 90). "N hidden below X% fit" is shown.
- Fit calculation reviewed: the category part (30 of 100 for a CPV match, 20 for a title word) now names the *matching* service, not the whole list. The weights were left alone because changing them re-ranks every user's saved signals; say if you want them retuned.

## 5. Scoring and AI
**Built**
- "AI Score" renamed **Keyword Score** (English, French, Dutch). The AI deep score keeps its name.
- One set of fit bands everywhere in the browser: 80+ strong, 50 to 79 possible, below 50 weak. The pipeline and some badges used 70/40 while the list and the AI prompt used 80/50, so one score could show two colours. The recommendation emails use the same bands.

**What exists today (so decisions are about facts)**
| Score | Where | Cost | Inputs |
|---|---|---|---|
| Keyword Score | browser, instant | free | profile keywords in the list summary, region, value vs turnover, deadline |
| AI deep score | `POST /api/fit-score`, cached per tender + profile in `fit_scores` | credits + AI tokens | full description |
| Growth fit | server, deterministic | free | timing, category, profile words, evidence |
| Buyer match | server, deterministic | free | sector, distance, renewal, openness |

**Recommendation (not built)**
1. Keep tier 0 (Keyword Score) free and instant. Its known weakness is that it reads only the list summary, so it moves when the full text arrives. Cheapest fix: compute it again server-side from the stored full text when a notice is ingested and cache it per (tender, profile version).
2. Run the AI score **only on a shortlist**: tenders with a Keyword Score above a threshold, or that the user tracked. Send a truncated description (about 1,500 tokens) and ask for a fixed small JSON answer. This is where most token cost is saved.
3. Background scoring: a `score_jobs` table plus one worker thread (the app already runs scheduler threads) that scores the shortlist after a search and on a nightly pass over the Pipeline. The page shows "scoring…" and refreshes the badge when a job finishes; the user is not blocked.
4. Cache key = (tender id, profile id, hash of profile text). Edit the profile, only then re-score.
5. Measure before tuning: log tokens per fit call and the share of calls that were cache hits. With those two numbers the accuracy/time/cost trade-off becomes arithmetic. No token prices are assumed here.

## 6. Additional buyer platforms and housing associations: needs input
Waiting on the two lists. Once received: each platform is an `etenders_scraper` source with its own portal id (Growth/Buyer/Supplier pages need no change); housing associations become a buyer category next to councils (`classify_buyer_type`, the Growth "buyer type" filter and the Buyer filter), matched by registered-provider name from your list rather than guessed from words like "homes".

## 7. Answer Bank: built (rules, no AI tokens)
- Uploads are sorted into a category from the file name and text; the form says which category was chosen and why, and it can be changed.
- Answers are flagged: expired or expiring (dates read from the text), unfinished (TBC, [insert …]), very short, empty, out of date (12 months for insurance and policies, 24 otherwise).
- **Document checklist**: 16 usual documents (insurance, H&S, EDI, modern slavery, GDPR, ISO, accounts, case studies, …) marked In your bank / Needs attention / Missing, plus extras for the user's sector (construction, IT, care, cleaning, transport) read from their profile and tracked tenders.
- Not built: "documents kept by similar companies". It needs data pooled across users. If wanted: count, per sector, the share of companies holding each checklist document, show it only when at least 10 companies back the figure, and never expose any file or text.

## 8. History: built
History has a **Recently viewed** tab: tenders, buyers and suppliers opened, newest first, with a count, kept in the browser only (cleared on logout or with Clear). Searches already have their own recent-searches list.

## 9. Progress bar and background processing: built
- The bar has a visible track and a stronger colour.
- While searching it lists which portals answered (with counts), which are still working and which failed.
- After 20 seconds it says which portal is taking longer than usual and that the faster results are already in the list.
- Results from faster portals already appear as they arrive (the page polls and merges rows while portals finish); this was not changed.

## 10. Date filter: built
"Published" pills (Anytime, 7d, 14d, 30d, 60d, Custom) sit in the left panel under Tender search and stay in step with the Any date chip. Custom opens the chip's date sliders.

## 11. Token rollover: needs a decision (facts first)
**Today credits never expire.** There is one balance per user (`credit_wallet`); a subscription renewal adds its credits on top (`PLAN_CREDITS`: 150/500/1,500 a month) and nothing is removed. So unused tokens already carry forward without limit. A three-month limit would *take away* something users have now.

Decisions needed:
1. Is the intent to **keep** unlimited carry-over, or to **limit** subscription credits to 3 months?
2. Do bought packs (100/300/1,000) expire? Suggested: no.
3. Spend order if there is an expiry: oldest first (suggested), packs last.
4. Existing balances: leave untouched (suggested) or put under the new rule from a date?

If a limit is chosen: replace the single number with `credit_lots (username, amount_left, source, granted_at, expires_at)`, spend oldest first, keep `credit_wallet.balance` as the sum, show "N credits expire on <date>", email a reminder 14 days before, and migrate current balances into one lot with no expiry. About two days of work with tests.
