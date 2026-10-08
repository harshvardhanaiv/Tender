"use strict";
/* Run: node tests/growth_view_test.js   (no packages needed; Node 18 or later)

   Tests Growth Studio's front-end logic without a browser (web/growth-view.js: pure functions only):
   the formatting helpers, the request arguments the page sends, and the HTML of every pane, including
   that nothing that came from a public notice or from the user can open markup, break out of an
   attribute or add a non-http(s) link. The controller (growth-studio.js) holds no logic of its own
   that is not covered here or by tests/test_growth_studio_api.py. */
const assert = require("node:assert/strict");
const path = require("node:path");

const V = require(path.join(__dirname, "..", "web", "growth-view.js"));

const tests = [];
const test = (name, fn) => tests.push([name, fn]);
const has = (html, ...fragments) => fragments.forEach((f) => assert.ok(html.includes(f), `expected ${JSON.stringify(f)} in:\n${html}`));
const lacks = (html, ...fragments) => fragments.forEach((f) => assert.ok(!html.includes(f), `did not expect ${JSON.stringify(f)} in:\n${html}`));

const EVIL = `<img src=x onerror=alert(1)>"'><script>alert(2)</script>`;
// What a hostile notice could carry: nothing in the output may contain these unescaped.
const noMarkup = (html) => lacks(html, "<img src=x", "<script>alert", 'onerror=alert(1)>"', '"><script', "javascript:");

// ── formatting ────────────────────────────────────────────────────────────────────────────────
test("esc escapes every character that can open markup", () => {
  assert.equal(V.esc(`<a href="x">&'`), "&lt;a href=&quot;x&quot;&gt;&amp;&#39;");
  assert.equal(V.esc(null), "");
  assert.equal(V.esc(undefined), "");
  assert.equal(V.esc(0), "0");
});

test("safeUrl only lets http(s) links through", () => {
  assert.equal(V.safeUrl("https://example.org/a b?x=1"), "https://example.org/a%20b?x=1");
  assert.equal(V.safeUrl("http://example.org"), "http://example.org/");
  for (const bad of ["javascript:alert(1)", "data:text/html,x", "ftp://example.org", "//example.org", "example.org", "", null, undefined, "vbscript:x"]) {
    assert.equal(V.safeUrl(bad), null, String(bad));
  }
});

test("fmtDate, fmtMoney, fmtMoneyShort", () => {
  assert.equal(V.fmtDate("2026-10-05"), "5 Oct 2026");
  assert.equal(V.fmtDate("2027-01-31T10:00:00"), "31 Jan 2027");
  assert.equal(V.fmtDate("2026-13-05"), null);
  assert.equal(V.fmtDate("soon"), null);
  assert.equal(V.fmtDate(null), null);
  assert.equal(V.fmtMoney(64000), "£64,000");
  assert.equal(V.fmtMoney(0), "£0");
  assert.equal(V.fmtMoney(null), null);
  assert.equal(V.fmtMoney(NaN), null);
  const short = [[950, "£950"], [9999, "£9,999"], [10000, "£10k"], [64000, "£64k"], [999999, "£1000k"], [1000000, "£1m"], [1250000, "£1.25m"],
    [1500000, "£1.5m"], [2000000, "£2m"], [10000000, "£10m"], [12000000, "£12m"], [100000000, "£100m"], [1750000, "£1.75m"]];
  for (const [n, text] of short) assert.equal(V.fmtMoneyShort(n), text, String(n));
  assert.equal(V.fmtMoneyShort(null), null);
});

test("plural, inDays", () => {
  assert.equal(V.plural(1, "signal"), "1 signal");
  assert.equal(V.plural(2, "signal"), "2 signals");
  assert.equal(V.plural(0, "buyer"), "0 buyers");
  assert.equal(V.plural(3, "new development", "new developments"), "3 new developments");
  assert.equal(V.plural(2, "person", "people"), "2 people", "an irregular plural is used as given");
  assert.equal(V.plural(1, "person", "people"), "1 person");
  assert.equal(V.plural(1, "more contract"), "1 more contract");
  assert.equal(V.inDays(91), "in 91 days");
  assert.equal(V.inDays(1), "tomorrow");
  assert.equal(V.inDays(0), "today");
  assert.equal(V.inDays(-3), "today");
  assert.equal(V.inDays(null), "");
});

test("server times without a zone are read as UTC", () => {
  const t = Date.UTC(2026, 9, 5, 10, 0, 0);
  assert.equal(V.parseServerTime("2026-10-05T10:00:00"), t);
  assert.equal(V.parseServerTime("2026-10-05T10:00:00Z"), t);
  assert.equal(V.parseServerTime("2026-10-05T11:00:00+01:00"), t);
  assert.equal(V.parseServerTime(null), null);
  assert.equal(V.parseServerTime("garbage"), null);
  const at = (minutes) => V.timeAgo("2026-10-05T10:00:00", t + minutes * 60000);
  assert.equal(at(1), "Just now");
  assert.equal(at(5), "5 minutes ago");
  assert.equal(at(60), "1 hour ago");
  assert.equal(at(5 * 60), "5 hours ago");
  assert.equal(at(24 * 60), "1 day ago");
  assert.equal(at(3 * 24 * 60), "3 days ago");
  assert.equal(at(90 * 24 * 60), "3 months ago");
  assert.equal(V.timeAgo(null), "Never");
  assert.equal(V.timeAgo("nonsense"), "Never");
  assert.equal(V.timeAgo("2026-10-05T10:00:00", t - 5 * 60000), "Just now", "a clock that is a little behind never shows a negative time");
});

test("fitBand, filenameFromDisposition, insertAt", () => {
  assert.deepEqual([100, 75, 74, 50, 49, 0].map(V.fitBand), ["high", "high", "mid", "mid", "low", "low"]);
  assert.equal(V.filenameFromDisposition('attachment; filename="plan-csv-2026-10-05.csv"', "x.csv"), "plan-csv-2026-10-05.csv");
  assert.equal(V.filenameFromDisposition("attachment; filename=plain.txt", "x"), "plain.txt");
  assert.equal(V.filenameFromDisposition("", "fallback.csv"), "fallback.csv");
  assert.equal(V.filenameFromDisposition(null, "fallback.csv"), "fallback.csv");
  assert.deepEqual(V.insertAt("Hello team", 6, 6, "{{x}}"), { value: "Hello {{x}}team", caret: 11 });
  assert.deepEqual(V.insertAt("Hello team", 0, 5, "Hi"), { value: "Hi team", caret: 2 }, "a selection is replaced");
  assert.deepEqual(V.insertAt("abc", null, null, "!"), { value: "abc!", caret: 4 }, "no caret means the end");
  assert.deepEqual(V.insertAt("abc", 99, 120, "!"), { value: "abc!", caret: 4 }, "a caret past the end is clamped");
  assert.deepEqual(V.insertAt("abc", 2, 1, "!"), { value: "ab!c", caret: 3 }, "an end before the start is clamped");
  assert.deepEqual(V.insertAt("", 0, 0, "x"), { value: "x", caret: 1 });
});

// ── request arguments ─────────────────────────────────────────────────────────────────────────
test("filters become the JSON and the query string the API takes", () => {
  const f = { preset: "housing-repairs-gas", types: ["renewal", "development"], days: 90, authority: "nhs", where: "Leicestershire", frameworks: false };
  assert.deepEqual(V.filtersBody(f), { preset: "housing-repairs-gas", types: ["renewal", "development"], days: 90, authority: "nhs", where: "Leicestershire", frameworks: false });
  const q = new URLSearchParams(V.signalsQuery(f, 7, true));
  assert.equal(q.get("preset"), "housing-repairs-gas");
  assert.equal(q.get("types"), "renewal,development");
  assert.equal(q.get("days"), "90");
  assert.equal(q.get("authority"), "nhs");
  assert.equal(q.get("where"), "Leicestershire");
  assert.equal(q.get("frameworks"), "0");
  assert.equal(q.get("profile_id"), "7");
  assert.equal(q.get("show_dismissed"), "1");
  const plain = new URLSearchParams(V.signalsQuery({ cpv: "5072", q: "boiler" }, null, false));
  assert.equal(plain.get("cpv"), "5072");
  assert.equal(plain.get("q"), "boiler");
  assert.equal(plain.get("preset"), null);
  assert.equal(plain.get("authority"), null, "the default buyer type is not sent");
  assert.equal(plain.get("where"), null);
  assert.equal(plain.get("profile_id"), null);
  assert.equal(plain.get("show_dismissed"), null);
  assert.equal(plain.get("days"), "180");
  assert.equal(plain.get("frameworks"), "1");
  assert.equal(plain.get("types"), "renewal,development,engagement", "no types means all three");
  const both = V.filtersBody({ preset: "a", cpv: "45", q: "x" });
  assert.deepEqual([both.preset, both.cpv, both.q], ["a", undefined, undefined], "a preset wins over custom words");
  assert.equal(V.filtersBody({ preset: "a", days: "bad" }).days, 180);
  assert.deepEqual(V.filtersBody({ preset: "a", types: [] }).types, ["renewal", "development", "engagement"]);
});

test("hasCategory, defaultFilters, campaignKeys", () => {
  assert.equal(V.hasCategory({ preset: "x" }), true);
  assert.equal(V.hasCategory({ cpv: "45" }), true);
  assert.equal(V.hasCategory({ q: "boiler" }), true);
  assert.equal(V.hasCategory({}), false);
  assert.equal(V.hasCategory(null), false);
  assert.equal(V.hasCategory({ preset: "" }), false);
  assert.deepEqual(V.defaultFilters({ default_days: 120 }), { types: ["renewal", "development", "engagement"], days: 120, authority: "all", where: "", frameworks: true });
  assert.equal(V.defaultFilters().days, 180);
  assert.deepEqual(V.campaignKeys("a", ["a", "b"]), ["a", "b"], "a ticked card means the whole selection");
  assert.deepEqual(V.campaignKeys("c", ["a", "b"]), ["c"], "an unticked card means just that signal");
  assert.deepEqual(V.campaignKeys("a", []), ["a"]);
  const selected = ["a", "b"];
  V.campaignKeys("a", selected).push("z");
  assert.deepEqual(selected, ["a", "b"], "the selection is copied, never handed out");
});

// ── signals ───────────────────────────────────────────────────────────────────────────────────
const renewal = (extra) => ({
  key: "renewal:HINCKLEY", type: "renewal", badge: "Contract renewal", buyer: "Hinckley & Bosworth Borough Council", authority: "District & borough councils",
  headline: "“Gas servicing” contract ends in 91 days (31 Jan 2027)", detail: "Incumbent: ABC Heating Ltd. Last award on record: £64,000.", date: "2027-01-31", days: 91,
  fit: 88, band: "high", fit_parts: [
    { key: "timing", label: "Timing", points: 35, max: 35, note: "Ends in 91 days" }, { key: "category", label: "Category", points: 30, max: 30, note: "In the category" },
    { key: "profile", label: "Your profile", points: 8, max: 20, note: "Shares 1 word" }, { key: "evidence", label: "Evidence", points: 15, max: 15, note: "The notice gives a value" }],
  contracts: [{ title: "Gas servicing", ends: "2027-01-31", days_left: 91, framework: false, suppliers: ["ABC Heating Ltd"], supplier_count: 1, value: 64000, annual_value: 21300 },
    { title: "Heating framework", ends: "2027-02-28", days_left: 119, framework: true, suppliers: ["A Ltd", "B Ltd", "C Ltd"], supplier_count: 5, value: null, annual_value: null }],
  more_contracts: 2, target: { name: "Hinckley", key: "HINCKLEY", targetable: true, reason: null }, links: [{ label: "Award notice", url: "https://example.org/n/1" }],
  state: { dismissed: false, opted_out: false, campaigns: [] }, ...extra,
});
const ctx = (extra) => ({ selected: [], expanded: [], ...extra });

test("a renewal card shows the buyer, the headline, the fit and what you can do", () => {
  const html = V.signalCard(renewal(), ctx());
  has(html, 'data-key="renewal:HINCKLEY"', "Contract renewal", "Hinckley &amp; Bosworth Borough Council", "District &amp; borough councils", "“Gas servicing” contract ends in 91 days",
    "Incumbent: ABC Heating Ltd.", 'class="gs-fit gs-fit--high"', "Fit <b>88%</b>", 'aria-expanded="false"', 'data-action="toggle-fit"', 'data-action="start-campaign"', "Start campaign →",
    'data-action="dismiss"', 'type="checkbox" data-action="toggle-select"', "Select Hinckley &amp; Bosworth Borough Council", 'href="https://example.org/n/1"', 'rel="noopener noreferrer"', "Award notice ↗");
  lacks(html, "is-selected", "Restore", "gs-signal__why", "gs-parts", " checked");
});

test("opening the score shows where every point came from", () => {
  const html = V.signalCard(renewal(), ctx({ expanded: ["fit:renewal:HINCKLEY"] }));
  has(html, 'aria-expanded="true"', 'class="gs-parts"', "Timing", "35/35", "Your profile", "8/20", "Shares 1 word", 'style="width:40%"', 'style="width:100%"');
});

test("several contracts fold away until asked for", () => {
  const closed = V.signalCard(renewal(), ctx());
  has(closed, "Show all 4 contracts", 'data-action="toggle-more"', 'aria-expanded="false"');
  lacks(closed, "Heating framework");
  const open = V.signalCard(renewal(), ctx({ expanded: ["more:renewal:HINCKLEY"] }));
  has(open, "Hide all 4 contracts", "Gas servicing", "Heating framework", "Framework · ends 28 Feb 2027 (in 119 days)", "Appointed: A Ltd, B Ltd, C Ltd +2",
    "Incumbent: ABC Heating Ltd", "£64,000 (about £21,300 a year)", "2 more contracts not shown");
  lacks(open, "£null", "undefined");
  lacks(V.signalCard(renewal({ contracts: renewal().contracts.slice(0, 1), more_contracts: 0 }), ctx()), "gs-contracts");
});

test("selected, dismissed, opted-out and already-in-a-campaign signals say so", () => {
  has(V.signalCard(renewal(), ctx({ selected: ["renewal:HINCKLEY"] })), "is-selected", " checked");
  const dismissed = V.signalCard(renewal({ state: { dismissed: true, opted_out: false, campaigns: [] } }), ctx());
  has(dismissed, "is-dismissed", "Dismissed", 'data-action="restore"', "Restore");
  lacks(dismissed, 'data-action="dismiss"');
  const campaign = V.signalCard(renewal({ state: { dismissed: false, opted_out: false, campaigns: [{ id: 12, name: "Q1 renewals" }] } }), ctx());
  has(campaign, 'data-action="open-campaign"', 'data-id="12"', "In: Q1 renewals");
  has(V.signalCard(renewal({ state: { dismissed: false, opted_out: true, campaigns: [] }, target: { targetable: false, reason: "They asked not to be contacted." } }), ctx()),
    "Do not contact", "gs-signal__why", "They asked not to be contacted.");
});

test("a signal that cannot become a target has no checkbox and a disabled button that says why", () => {
  const reason = "No developer company is named on this application, so there is nobody to add to a campaign.";
  const html = V.signalCard({ ...renewal(), key: "development:p1", type: "development", buyer: "Developer not named", contracts: undefined, more_contracts: 0,
    target: { name: null, key: null, targetable: false, reason } }, ctx());
  has(html, "gs-signal--development", "New development", "gs-signal__select--off", `disabled title="${reason}"`, reason, "gs-signal__why");
  lacks(html, 'type="checkbox"', 'data-action="start-campaign"');
  has(V.signalCard({ ...renewal(), type: "engagement", key: "engagement:3" }, ctx()), "Market engagement open", "Respond to engagement →");
});

test("nothing from a notice can inject markup or a link", () => {
  const html = V.signalCard(renewal({
    key: `renewal:${EVIL}`, buyer: EVIL, authority: EVIL, headline: EVIL, detail: EVIL,
    contracts: [{ title: EVIL, ends: "2027-01-31", days_left: 5, suppliers: [EVIL], supplier_count: 1, value: 1, framework: false }], more_contracts: 0,
    fit_parts: [{ label: EVIL, points: 1, max: 2, note: EVIL }],
    links: [{ label: EVIL, url: "javascript:alert(1)" }, { label: EVIL, url: "https://example.org/ok?a=1&b=<2>" }],
    state: { dismissed: false, opted_out: false, campaigns: [{ id: EVIL, name: EVIL }] },
  }), ctx({ expanded: [`fit:renewal:${EVIL}`, `more:renewal:${EVIL}`], selected: [] }));
  noMarkup(html);
  has(html, "&lt;img src=x onerror=alert(1)&gt;", "https://example.org/ok?a=1&amp;b=%3C2%3E");
  assert.equal((html.match(/<a /g) || []).length, 1, "the javascript: link was dropped, the https one kept");
});

const suggestions = [{ preset: "housing-repairs-gas", label: "Housing repairs & gas servicing", matches: ["gas", "boil"] }];
const data = (extra) => ({ total: 3, shown: 3, counts: { renewal: 2, development: 1, engagement: 0, total: 3, dismissed: 0 }, signals: [renewal(), renewal({ key: "renewal:B", buyer: "Blaby" }),
  { ...renewal(), key: "development:1", type: "development" }], notes: { fit: "Fit note.", renewal: "Renewal note.", values: "Values note.", where: "Where note.", development: null, truncated: null }, ...extra });
const pane = (extra) => V.signalsPane({ filters: { preset: "housing-repairs-gas", types: ["renewal", "development", "engagement"] }, status: "ok", data: data(), selected: [], expanded: [],
  showDismissed: false, suggestions, campaigns: null, ...extra });

test("without a category the page asks for one and offers suggestions", () => {
  const html = V.signalsPane({ filters: {}, status: "idle", data: null, selected: [], expanded: [], suggestions });
  has(html, "Choose a category to see signals", "or start from a suggestion", 'data-action="pick-category"', 'data-preset="housing-repairs-gas"', "Housing repairs &amp; gas servicing", "Matches: gas, boil");
  const bare = V.signalsPane({ filters: {}, status: "idle", data: null, selected: [], expanded: [], suggestions: [] });
  has(bare, "Pick one above.");
  lacks(bare, "suggestion", "gs-chips");
});

test("loading, refreshing and failing", () => {
  has(V.signalsPane({ filters: { preset: "x" }, status: "loading", data: null, selected: [], expanded: [] }), "Finding signals…");
  const refreshing = pane({ status: "loading" });
  has(refreshing, "Updating…", "gs-list");
  const failed = V.signalsPane({ filters: { preset: "x" }, status: "error", error: `Nope ${EVIL}`, data: null, selected: [], expanded: [] });
  has(failed, "gs-note--bad", 'data-action="reload-signals"', "Try again");
  noMarkup(failed);
});

test("the signal list, its counts and its notes", () => {
  const html = pane();
  has(html, "3 signals: 2 renewals · 1 new development", 'class="gs-list"', 'id="gsSelBar"', "How to read these signals", "Fit note.", "Renewal note.", "Values note.", "Where note.");
  assert.equal((html.match(/<article /g) || []).length, 3);
  lacks(html, "dismissed</button>", "gs-selbar\"");
  const cut = pane({ data: data({ total: 426, shown: 50, counts: { renewal: 26, development: 400, engagement: 0, total: 426, dismissed: 0 }, shown_by_type: { renewal: 25, development: 25, engagement: 0 } }) });
  has(cut, "426 signals: 25 of 26 renewals · 25 of 400 new developments", "The best 25 of each type are shown. Narrow the filters to see others.");
  has(pane({ maxPerType: 10, data: data({ total: 12, shown: 3 }) }), "The best 10 of each type are shown");
  lacks(html, "of each type are shown");
  const one = pane({ data: data({ total: 41, shown: 26, counts: { renewal: 1, development: 40, engagement: 0, total: 41, dismissed: 0 }, shown_by_type: { renewal: 1, development: 25, engagement: 0 } }) });
  has(one, "41 signals: 1 renewal · 25 of 40 new developments");
  has(pane({ data: data({ counts: { renewal: 3, development: 0, engagement: 0, total: 3, dismissed: 2 } }) }), 'data-action="toggle-dismissed"', "Show 2 dismissed", 'aria-pressed="false"');
  has(pane({ showDismissed: true, data: data({ counts: { renewal: 3, development: 0, engagement: 0, total: 3, dismissed: 2 } }) }), "Hide 2 dismissed", 'aria-pressed="true"');
  has(pane({ data: data({ notes: { development: "Not for this category.", truncated: "Only the soonest are shown." } }) }), "Not for this category.", "Only the soonest are shown.");
  lacks(pane({ filters: { preset: "x", types: ["renewal"] }, data: data({ notes: { development: "Not for this category." } }) }), "Not for this category.");
});

test("an empty result explains itself", () => {
  const html = pane({ data: data({ total: 0, shown: 0, signals: [], counts: { renewal: 0, development: 0, engagement: 0, total: 0, dismissed: 0 } }) });
  has(html, "No signals match these filters", "longer time window", "No signals");
  lacks(html, "gs-list");
});

test("the selection bar only appears with a selection, and offers existing campaigns", () => {
  assert.equal(V.selectionBar({ selected: [], campaigns: [] }), "");
  const one = V.selectionBar({ selected: ["a"], campaigns: null });
  has(one, "<b>1</b> selected", 'data-action="create-from-selection"', 'data-action="clear-selection"');
  lacks(one, "gsAddTo");
  const withCampaigns = V.selectionBar({ selected: ["a", "b"], campaigns: [{ id: 1, name: "Open one", status: "active" }, { id: 2, name: "Done", status: "completed" }, { id: 3, name: `Evil ${EVIL}`, status: "draft" }] });
  has(withCampaigns, "<b>2</b> selected", 'id="gsAddTo"', '<option value="1">Open one</option>', '<option value="3">');
  lacks(withCampaigns, "Done");
  noMarkup(withCampaigns);
  has(pane({ selected: ["renewal:HINCKLEY"] }), "gs-selbar");
});

// ── campaigns ─────────────────────────────────────────────────────────────────────────────────
const campaign = (extra) => ({ id: 4, name: "Housing renewals", status: "active", channel: "email", channel_label: "Email", category_label: "Housing repairs & gas servicing", profile_name: "Priority Plumbing",
  counts: { targets: 6, sent: 4, replied: 2, meetings: 1 }, last_activity_at: "2026-10-05T10:00:00", created_at: "2026-10-01T10:00:00", ...extra });

test("the campaigns table", () => {
  const html = V.campaignsPane({ status: "ok", list: [campaign(), campaign({ id: 5, name: EVIL, status: "draft", counts: { targets: 1, sent: 0, replied: 0, meetings: 0 }, last_activity_at: null })], suppressions: [] });
  has(html, "Housing renewals", "Active", "Draft", "Email", "4 sent · 2 replied · 1 meeting", "0 sent · 0 replied · 0 meetings", "Housing repairs &amp; gas servicing · Priority Plumbing", 'data-action="open-campaign"', 'data-id="4"', "Open →");
  noMarkup(html);
  lacks(html, "Do-not-contact");
});

test("no campaigns yet, loading, failing and the do-not-contact list", () => {
  const none = V.campaignsPane({ status: "ok", list: [], suppressions: [{ key: "BLABY", name: "Blaby District Council", created_at: "2026-10-01T09:00:00" }], suppressionsOpen: true });
  has(none, "No campaigns yet", "Nothing is sent from TenderFlow", "Do-not-contact list (1)", " open", "Blaby District Council", "1 Oct 2026", 'data-action="remove-suppression"', 'data-key="BLABY"');
  has(V.campaignsPane({ status: "loading", list: null }), "Loading campaigns…");
  has(V.campaignsPane({ status: "error", error: "Down" }), "Down", 'data-action="reload-campaigns"');
  lacks(V.campaignsPane({ status: "ok", list: [], suppressions: [] }), "Do-not-contact");
  noMarkup(V.suppressionsBlock([{ key: EVIL, name: EVIL, created_at: null }], false));
});

// ── builder ───────────────────────────────────────────────────────────────────────────────────
const target = (extra) => ({ id: 1, buyer_key: "HINCKLEY", buyer_name: "Hinckley & Bosworth Borough Council", signal_key: "renewal:HINCKLEY", signal_type: "renewal", signal_label: "Contract renewal",
  headline: "Gas servicing ends in 91 days", date: "2027-01-31", fit: 88, links: [], included: true, contact_email: "procurement@hinckley.example", personal_email: false, status: "not_sent", opted_out: false, ...extra });
const detail = (extra) => ({ campaign: { id: 4, name: "Housing renewals", profile_name: "Priority Plumbing", category_label: "Housing repairs & gas servicing", channel: "email", channel_label: "Email", status: "draft",
  subject: "Subject {{buyer_name}}", body: "Hello {{buyer_contact}}\n\nBody." }, targets: [target(), target({ id: 2, buyer_name: "Blaby", buyer_key: "BLABY", contact_email: null, status: "sent" })],
  sender: { company_name: "Priority" }, warnings: [], eligible: 2, ...extra });
const options = { channels: [{ id: "email", label: "Email" }, { id: "letter", label: "Letter" }], credit_cost_draft: 2,
  merge_fields: [{ name: "buyer_name", label: "Buyer name", types: ["renewal", "development", "engagement"] }, { name: "renewal_date", label: "End date", types: ["renewal"] }, { name: "scheme_summary", label: "Scheme", types: ["development"] }] };
const builder = (extra) => V.builderPane({ status: "ok", detail: detail(), draft: null, dirty: false, preview: null, previewTarget: 1, warnings: null, drafting: false, options, aiNote: "", ...extra });

test("the builder without a campaign, loading, failing", () => {
  has(V.builderPane({ status: "idle", detail: null }), "No campaign open");
  has(V.builderPane({ status: "loading", detail: null }), "Opening the campaign…");
  has(V.builderPane({ status: "error", error: "Gone", detail: null }), "Gone", 'data-action="back-to-campaigns"');
});

test("the builder shows the buyers, the message and what you can do with them", () => {
  const html = builder();
  has(html, 'value="Housing renewals"', 'data-field="campaign-name"', 'data-field="campaign-channel"', '<option value="email" selected>Email</option>', 'data-field="campaign-status"', '<option value="draft" selected>Draft</option>',
    'data-action="delete-campaign"', "2 of 2 will be exported", "Hinckley &amp; Bosworth Borough Council", 'data-action="target-include" data-id="1" checked', 'value="procurement@hinckley.example"',
    '<option value="not_sent" selected>', '<option value="sent" selected>', 'data-action="remove-target"', 'id="gsSubject"', 'value="Subject {{buyer_name}}"', 'id="gsBody"',
    "Hello {{buyer_contact}}\n\nBody.", 'data-action="save-message" disabled', 'data-action="draft-template"', 'data-action="draft-ai"', "Write with AI", "2 credits",
    'data-action="export" data-format="csv"', 'data-format="mailchimp"', 'data-action="copy-mailchimp"', 'data-action="mark-sent"', 'id="gsPreview"', 'id="gsAiNote"', "role-based public sector inboxes");
  lacks(html, "Unsaved changes", "Looks like a named person", "is-off");
});

test("unsaved changes, drafting and nothing to export are all visible", () => {
  has(builder({ dirty: true }), "Unsaved changes", 'data-action="save-message">');
  lacks(builder({ dirty: true }), 'data-action="save-message" disabled');
  has(builder({ drafting: true }), "Writing…");
  has(builder({ drafting: true }), 'data-action="draft-ai" disabled');
  has(builder({ options: { ...options, credit_cost_draft: 1 } }), "1 credit<");
  lacks(builder({ options: { ...options, credit_cost_draft: 0 } }), "credit");
  const none = builder({ detail: detail({ eligible: 0 }) });
  has(none, 'data-action="mark-sent" disabled', 'data-action="export" data-format="csv" disabled', 'data-format="mailchimp" disabled', "0 of 2 will be exported");
  has(builder({ detail: detail({ targets: [], eligible: 0 }) }), "No buyers left", "Add a buyer to see the message");
});

test("a message with no sender to sign it says so, and the Mailchimp fallback is plain text to select", () => {
  const unsigned = builder({ detail: detail({ sender: {} }) });
  has(unsigned, "this message is not signed", "Company profiles");
  lacks(builder(), "this message is not signed");
  lacks(builder({ detail: detail({ sender: { sender_name: "Sam Ray" } }) }), "this message is not signed");
  has(builder(), 'id="gsMcBox"');
  const box = V.mailchimpBox(`Hello *|GREETING|*, ${EVIL}`);
  has(box, 'id="gsMcText"', "readonly", "*|GREETING|*", "Import the audience CSV first");
  noMarkup(box);
});

test("buyers who are off, opted out or have a named person's email are marked", () => {
  const html = builder({ detail: detail({ targets: [target({ included: false }), target({ id: 2, opted_out: true, status: "opted_out", personal_email: true, contact_email: "john.smith@x.example" })], eligible: 0 }) });
  has(html, "is-off", 'data-action="target-include" data-id="2" checked disabled', "Do not contact", "Looks like a named person", "procurement@ where you can", '<option value="opted_out" selected>Opted out: never contact</option>');
  lacks(html, 'data-id="1" checked');
});

test("the editor shows the saved message, or the unsaved draft, and never lets either break out", () => {
  has(builder({ draft: { subject: "My own", body: "Draft body" } }), 'value="My own"', ">Draft body</textarea>");
  const html = builder({ draft: { subject: EVIL, body: `</textarea>${EVIL}` }, aiNote: EVIL, detail: detail({ targets: [target({ buyer_name: EVIL, headline: EVIL, contact_email: EVIL })] }) });
  noMarkup(html);
  lacks(html, "</textarea><img", "</textarea><script");
  assert.equal((html.match(/<\/textarea>/g) || []).length, 1, "exactly one textarea is closed");
});

test("merge field buttons are only the ones this campaign can fill", () => {
  const renewalOnly = V.mergeChips(options.merge_fields, ["renewal"]);
  has(renewalOnly, 'data-name="buyer_name"', 'data-name="renewal_date"', "{{renewal_date}}");
  lacks(renewalOnly, "scheme_summary");
  const mixed = V.mergeChips(options.merge_fields, ["renewal", "development"]);
  has(mixed, "renewal_date", "scheme_summary");
  has(V.mergeChips(options.merge_fields, []), "renewal_date", "scheme_summary");
  assert.equal(V.mergeChips(undefined, ["renewal"]), "");
  has(builder({ warnings: [{ code: "no_opt_out", text: "The message has no way to opt out." }] }), "gs-warnings", "The message has no way to opt out.");
  assert.equal(V.warningsHtml([]), "");
  noMarkup(V.warningsHtml([{ text: EVIL }]));
});

test("the preview", () => {
  const targets = [target(), target({ id: 2, buyer_name: "Blaby" }), target({ id: 3, buyer_name: "Gone", opted_out: true })];
  const html = V.previewHtml({ to: "procurement@x.example", subject: "Hi Hinckley", body: "Line one\nLine two", missing: ["incumbent"], unknown: ["typo"] }, targets, 2);
  has(html, '<option value="2" selected>Blaby</option>', '<option value="1">', "To: procurement@x.example", "<b>Subject:</b> Hi Hinckley", "Line one\nLine two", "{{incumbent}}", "{{typo}}", "Not a merge field");
  lacks(html, '<option value="3"');
  has(V.previewHtml({ to: null, subject: "s", body: "b", missing: [], unknown: [] }, targets, 1), "no email address yet");
  has(V.previewHtml(null, targets, 1), "Preparing the preview…");
  has(V.previewHtml(null, [], null), "Add a buyer to see the message");
  has(V.previewHtml(null, [target({ opted_out: true })], null), "Add a buyer to see the message");
  noMarkup(V.previewHtml({ to: EVIL, subject: EVIL, body: EVIL, missing: [EVIL], unknown: [EVIL] }, [target({ buyer_name: EVIL })], 1));
});

// ── performance ───────────────────────────────────────────────────────────────────────────────
const perf = (extra) => ({ window: "365", days: 365, tiles: { campaigns_run: 2, buyers_reached: 9, sent: 10, replied: 3, meetings: 1, reply_rate: 0.3, tenders_tracked: 4, pipeline_value: 1750000, tenders_with_value: 3 },
  funnel: [{ label: "Sent", value: 10 }, { label: "Replied", value: 3 }, { label: "Meeting", value: 1 }],
  campaigns: [{ id: 4, name: "Housing renewals", status: "active", targets: 6, sent: 6, replied: 2, meetings: 1, tenders: 2, last_activity: null },
    { id: 5, name: "Drafted", status: "draft", targets: 3, sent: 0, replied: 0, meetings: 0, tenders: 0, last_activity: null },
    { id: 6, name: "No reply yet", status: "active", targets: 3, sent: 3, replied: 0, meetings: 0, tenders: 0, last_activity: null }],
  notes: { opened: "Opens are not tracked.", pipeline: "A tender counts once." }, ...extra });

test("the performance tiles, funnel and outcomes table", () => {
  const html = V.performancePane({ status: "ok", data: perf() });
  has(html, "Campaigns run", ">2<", "Buyers reached", ">9<", "Reply rate", "30%", "3 of 10 replied", "Pipeline value influenced", "£1.75m", "4 tenders tracked, 3 with a value", "10 messages recorded as sent",
    'data-field="performance-days"', '<option value="365" selected>Last 12 months</option>', "Response funnel", 'style="width:100%"', 'style="width:30%"', 'style="width:10%"',
    "Campaign → Pipeline outcomes", "Housing renewals", "2 tenders", "Not sent", "None yet", "How these figures are worked out", "Opens are not tracked.", "A tender counts once.");
  const none = V.performancePane({ status: "ok", data: perf({ tiles: { ...perf().tiles, reply_rate: null, pipeline_value: null, sent: 0, tenders_tracked: 0 } }) });
  has(none, "No messages sent yet", "0 tenders tracked");
  assert.equal((none.match(/gs-stat__value">–</g) || []).length, 2, "a rate and a value that do not exist are dashes, never 0%");
  lacks(none, "with a value");
  has(V.performancePane({ status: "ok", data: perf({ campaigns: [] }) }), "Nothing to report yet");
  has(V.performancePane({ status: "loading", data: null }), "Loading results…");
  has(V.performancePane({ status: "error", error: "Down", data: null }), "Down", 'data-action="reload-performance"');
  has(V.performancePane({ status: "ok", data: perf({ window: "all" }) }), '<option value="all" selected>All time</option>');
  const evil = V.performancePane({ status: "ok", data: perf({ campaigns: [{ id: EVIL, name: EVIL, status: "active", targets: 1, sent: 1, replied: 0, meetings: 0, tenders: 0 }], notes: { x: EVIL } }) });
  noMarkup(evil);
});

test("funnel bars: a small count is still visible and an empty step is empty", () => {
  const html = V.funnelBars([{ label: "Sent", value: 200 }, { label: "Replied", value: 1 }, { label: "Meeting", value: 0 }]);
  has(html, 'style="width:100%"', 'style="width:3%"', 'style="width:0%"');
  has(V.funnelBars([{ label: "Sent", value: 0 }]), 'style="width:0%"');
  assert.equal(V.funnelBars([]).includes("gs-funnel__row"), false);
});

// ── runner ────────────────────────────────────────────────────────────────────────────────────
let failed = 0;
for (const [name, fn] of tests) {
  try {
    fn();
    console.log(`ok    ${name}`);
  } catch (err) {
    failed += 1;
    console.log(`FAIL  ${name}`);
    console.log(err && err.stack ? err.stack : err);
  }
}
console.log(`\n${tests.length - failed} of ${tests.length} passed`);
process.exitCode = failed ? 1 : 0;
