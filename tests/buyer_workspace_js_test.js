"use strict";
/* Run: node tests/buyer_workspace_js_test.js   (no packages needed; Node 18 or later)

   Tests the buyer workspace's front-end logic without a browser: the formatting helpers, hash routing,
   category specs, the API wrapper (CSRF header, error messages), and the pure HTML renderers for Market
   Radar and Market Engagement, including that nothing from the server can inject markup or a
   non-http(s) link. buyer-workspace.js loads cleanly in Node; buyer-engagement.js registers its
   listeners on `document` as it loads, so it gets a stub that records them. */
const assert = require("node:assert/strict");
const path = require("node:path");

const web = (file) => path.join(__dirname, "..", "web", file);

globalThis.location = { href: "", hash: "" };
const BW = require(web("buyer-workspace.js"));
const listeners = {};
globalThis.document = {
  addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
  getElementById: () => null,
  querySelector: () => null,
};
require(web("buyer-engagement.js"));
const ENG = BW.engagement;

const tests = [];
const test = (name, fn) => tests.push([name, fn]);
const has = (html, ...fragments) => fragments.forEach((f) => assert.ok(html.includes(f), `expected ${JSON.stringify(f)} in:\n${html}`));
const lacks = (html, ...fragments) => fragments.forEach((f) => assert.ok(!html.includes(f), `did not expect ${JSON.stringify(f)} in:\n${html}`));

// ── formatting ────────────────────────────────────────────────────────────────────────────────
test("esc escapes every character that can open markup", () => {
  assert.equal(BW.esc(`<a href="x">&'`), "&lt;a href=&quot;x&quot;&gt;&amp;&#39;");
  assert.equal(BW.esc(null), "");
  assert.equal(BW.esc(undefined), "");
  assert.equal(BW.esc(0), "0");
});

test("fmtMoney", () => {
  const cases = [
    [null, "–"], ["abc", "–"], [0, "£0"], [999, "£999"], [9999, "£9,999"], [10000, "£10k"], [250000, "£250k"],
    ["250000", "£250k"], [999499, "£999k"], [999500, "£1m"], [1000000, "£1m"], [1500000, "£1.5m"], [2100000, "£2.1m"],
    [9960000, "£10m"], [12345678, "£12m"], [999499999, "£999m"], [999500000, "£1bn"], [1240000000, "£1.2bn"], [2e9, "£2bn"],
  ];
  cases.forEach(([input, expected]) => assert.equal(BW.fmtMoney(input), expected, `fmtMoney(${input})`));
});

test("fmtInt, fmtPct, plural, trimNum, clamp", () => {
  assert.equal(BW.fmtInt(null), "–");
  assert.equal(BW.fmtInt(1234567), "1,234,567");
  assert.equal(BW.fmtPct(0.333), "33%");
  assert.equal(BW.fmtPct(1), "100%");
  assert.equal(BW.fmtPct(null), "–");
  assert.equal(BW.plural(0, "buyer"), "0 buyers");
  assert.equal(BW.plural(1, "buyer"), "1 buyer");
  assert.equal(BW.plural(2, "category", "categories"), "2 categories");
  assert.equal(BW.plural(1234, "award"), "1,234 awards");
  assert.equal(BW.trimNum(2, 1), "2");
  assert.equal(BW.trimNum(2.5, 1), "2.5");
  assert.equal(BW.trimNum(12, 0), "12");
  assert.equal(BW.clamp(5, 0, 3), 3);
  assert.equal(BW.clamp(-1, 0, 3), 0);
  assert.equal(BW.clamp(2, 0, 3), 2);
});

test("fmtDate reads ISO dates and timestamps, and shows a dash for anything else", () => {
  assert.equal(BW.fmtDate("2025-03-01"), "1 Mar 2025");
  assert.equal(BW.fmtDate("2025-03-01T10:00:00Z"), "1 Mar 2025");
  assert.equal(BW.fmtDate(null), "–");
  assert.equal(BW.fmtDate("garbage"), "–");
});

test("safeUrl only lets http(s) links through", () => {
  ["https://example.org/a", "http://example.org", "HTTPS://EXAMPLE.ORG/x"].forEach((u) => assert.equal(BW.safeUrl(u), u));
  ["javascript:alert(1)", "data:text/html,x", "//evil.example", "ftp://example.org", "https://a b", "", null, undefined, 42, {}]
    .forEach((u) => assert.equal(BW.safeUrl(u), null, String(u)));
});

// ── routing and category specs ────────────────────────────────────────────────────────────────
test("parseHash and buildHash", () => {
  let h = BW.parseHash("#/radar?category=housing&authority=county");
  assert.deepEqual(h.parts, ["radar"]);
  assert.equal(h.params.get("category"), "housing");
  assert.equal(h.params.get("authority"), "county");
  h = BW.parseHash("#/engagements/12?step=3");
  assert.deepEqual(h.parts, ["engagements", "12"]);
  assert.equal(h.params.get("step"), "3");
  assert.deepEqual(BW.parseHash("").parts, []);
  assert.deepEqual(BW.parseHash(undefined).parts, [], "falls back to location.hash");
  assert.deepEqual(BW.parseHash("#/a%20b/c").parts, ["a b", "c"]);
  assert.equal(BW.buildHash(["radar"], new URLSearchParams([["category", "a b"], ["x", ""]])), "#/radar?category=a+b");
  assert.equal(BW.buildHash(["engagements", 12]), "#/engagements/12");
  assert.equal(BW.buildHash([]), "#/");
  const round = BW.parseHash(BW.buildHash(["radar"], new URLSearchParams([["q", "tree surgery & more"]])));
  assert.equal(round.params.get("q"), "tree surgery & more");
});

test("qs drops empty values but keeps zero", () => {
  assert.equal(BW.qs({ a: 1, b: "", c: null, d: undefined, e: "x y" }), "?a=1&e=x+y");
  assert.equal(BW.qs({}), "");
  assert.equal(BW.qs({ page: 0 }), "?page=0");
});

test("category specs convert between objects, URL parameters and stored keys", () => {
  assert.equal(BW.specOf(null), null);
  assert.deepEqual(BW.specOf({ preset: "p", label: "x", key: "preset:p" }), { preset: "p" });
  assert.deepEqual(BW.specOf({ cpv: "5072", q: "boiler", key: "k" }), { cpv: "5072", q: "boiler" });
  assert.deepEqual(BW.specOf({ cpv: "5072" }), { cpv: "5072" });

  assert.deepEqual(BW.specFromParams(new URLSearchParams("category=housing&cpv=1")), { preset: "housing" });
  assert.equal(BW.specFromParams(new URLSearchParams("authority=county")), null);
  const custom = BW.specFromParams(new URLSearchParams("cpv=5072&q=boiler"));
  assert.equal(custom.cpv, "5072");
  assert.equal(custom.q, "boiler");
  assert.equal(BW.specFromParams(new URLSearchParams("cpv=5072")).q, undefined);

  assert.deepEqual(BW.specFromKey("preset:housing-repairs-gas"), { preset: "housing-repairs-gas" });
  assert.deepEqual(BW.specFromKey("cpv:5072"), { cpv: "5072" });
  assert.deepEqual(BW.specFromKey("q:tree surgery"), { q: "tree surgery" });
  assert.deepEqual(BW.specFromKey("cpv:5072|q:boiler"), { cpv: "5072", q: "boiler" });
  assert.equal(BW.specFromKey(""), null);
  assert.equal(BW.specFromKey("nonsense"), null);

  const params = BW.specToParams({ cpv: "5072", q: "boiler" }, new URLSearchParams("category=old&authority=county"));
  assert.equal(params.get("category"), null, "the previous category is replaced");
  assert.equal(params.get("authority"), "county", "other filters are kept");
  assert.equal(params.get("cpv"), "5072");
  assert.equal(params.get("q"), "boiler");
  assert.equal(BW.specToParams(null, new URLSearchParams("category=old&x=1")).toString(), "x=1");
});

test("defaultAuthority follows the user's organisation", () => {
  assert.equal(BW.defaultAuthority(null), "all");
  assert.equal(BW.defaultAuthority({ type: "county" }), "county");
  assert.equal(BW.defaultAuthority({ type: "local-other" }), "local-government");
});

test("niceTicks", () => {
  assert.deepEqual(BW.niceTicks(1000, 100000), [1000, 2000, 5000, 10000, 20000, 50000, 100000]);
  const wide = BW.niceTicks(100, 1e8);
  assert.equal(wide.length, 7, "a wide range keeps only the powers of ten");
  wide.forEach((v) => assert.equal(Math.log10(v) % 1, 0, `${v} is a power of ten`));
  assert.deepEqual(BW.niceTicks(3000, 4000), []);
});

// ── the API wrapper ───────────────────────────────────────────────────────────────────────────
const reply = (status, body, isJson = true) => ({
  status, ok: status >= 200 && status < 300,
  json: async () => { if (!isJson) throw new SyntaxError("not json"); return body; },
});
function stubFetch(handler) {
  const calls = [];
  globalThis.fetch = async (url, init) => { calls.push({ url, init }); return handler(url, init); };
  return calls;
}

test("api sends the CSRF token and JSON only on writes", async () => {
  BW.state.csrf = "tok-123";
  const calls = stubFetch(() => reply(200, { ok: true }));
  assert.deepEqual(await BW.api("/api/x"), { ok: true });
  assert.equal(calls[0].init.method, "GET");
  assert.deepEqual(calls[0].init.headers, {});
  assert.equal(calls[0].init.body, undefined);
  assert.equal(calls[0].init.credentials, "same-origin");
  await BW.api("/api/x", { method: "PUT", body: { n: 1 } });
  assert.equal(calls[1].init.method, "PUT");
  assert.equal(calls[1].init.headers["X-CSRF-Token"], "tok-123");
  assert.equal(calls[1].init.headers["Content-Type"], "application/json");
  assert.equal(calls[1].init.body, '{"n":1}');
});

test("api turns failures into messages a person can act on", async () => {
  stubFetch(() => reply(400, { error: "cpv must be 2 to 8 digits" }));
  await assert.rejects(BW.api("/api/x"), (e) => e instanceof BW.ApiError && e.status === 400 && e.message === "cpv must be 2 to 8 digits");
  stubFetch(() => reply(429, { error: "Rate limit exceeded. Try again later." }));
  await assert.rejects(BW.api("/api/x"), (e) => e.status === 429 && /searching very quickly/.test(e.message));
  stubFetch(() => reply(500, null, false));
  await assert.rejects(BW.api("/api/x"), (e) => e.status === 500 && e.message === "Something went wrong (500).");
  stubFetch(() => { throw new TypeError("Failed to fetch"); });
  await assert.rejects(BW.api("/api/x"), (e) => e instanceof BW.ApiError && e.status === 0 && /Could not reach the server/.test(e.message));
  stubFetch(() => { throw Object.assign(new Error("aborted"), { name: "AbortError" }); });
  await assert.rejects(BW.api("/api/x"), (e) => e.name === "AbortError" && !(e instanceof BW.ApiError), "aborts are not errors to show");
  globalThis.location.href = "";
  stubFetch(() => reply(401, { error: "Unauthorized" }));
  await assert.rejects(BW.api("/api/x"), (e) => e.status === 401);
  assert.equal(globalThis.location.href, "/login.html", "an expired session goes back to sign-in");
});

// ── Market Radar renderers ────────────────────────────────────────────────────────────────────
test("valueCell separates published values, ceilings and missing values", () => {
  has(BW.valueCell(null, false), "Value not published");
  assert.equal(BW.valueCell(250000, false), "£250k");
  has(BW.valueCell(5e6, true), "£5m", "Framework ceiling", "shared by every supplier");
});

test("awardLine escapes text and links only http(s) notices", () => {
  const award = { supplier: "<b>Acme</b>", value: 250000, value_is_ceiling: false, signed: "2025-03-01", started: "2025-03-01",
    ends: "2027-03-01", route: "Open procedure", url: "https://example.org/n/1", title: 'A "quoted" <title>' };
  const html = BW.awardLine(award);
  has(html, 'href="https://example.org/n/1"', 'target="_blank"', 'rel="noopener noreferrer"', "A &quot;quoted&quot; &lt;title&gt;",
    "&lt;b&gt;Acme&lt;/b&gt;", "£250k", "Open procedure", "started 1 Mar 2025", "ends 1 Mar 2027");
  lacks(html, "<b>Acme</b>", "<title>");
  lacks(BW.awardLine({ ...award, url: "javascript:alert(1)" }), "<a ", "javascript:");
  const bare = BW.awardLine({ value: null, route: "Not stated" });
  has(bare, "Supplier not stated", "Value not published", "Notice");
  lacks(bare, "Not stated ·");
});

test("the dashboard states both windows: six months ahead and six months back", () => {
  const src = require("node:fs").readFileSync(path.join(__dirname, "..", "web", "buyer-workspace.js"), "utf8");
  assert.ok(src.includes("Contracts ending in the next 6 months") && src.includes("Peer awards in the last 6 months"));
  assert.ok(!/last 5 months|last five months/.test(src), "the odd five-month window is gone");
});

test("the workspace shows names through the shared formatter (capitals, 'Of', stray colon)", () => {
  require(path.join(__dirname, "..", "web", "name-format.js"));   // the page loads it before buyer-workspace.js
  has(BW.awardLine({ supplier: "LEAP LEGAL SOFTWARE LTD", title: "x" }), "Leap Legal Software Ltd");
  const html = BW.peersTableHtml([peer({ buyer: "London Borough Of Camden", main_supplier: "Gristwood and Toms:" })]);
  has(html, "London Borough of Camden", "Gristwood and Toms</td>");
  lacks(html, "Borough Of", "Toms:");
  delete globalThis.NameFormat;   // the other tests expect names exactly as given
});

test("awardLine says why an award is in the category and when its value is only a share of a notice", () => {
  const html = BW.awardLine({ supplier: "Acme", value: 3000000, value_is_ceiling: false, started: "2025-03-01", route: "Open procedure", title: "Retrofit",
    matched_by: "CPV 48445000", shared_with: 3, notice_value: 9000000 });
  has(html, "in this category by CPV 48445000", "share of a £9m notice with 3 suppliers");
  const plain = BW.awardLine({ supplier: "Acme", value: 3000000, shared_with: 1, notice_value: null, matched_by: null, title: "x" });
  lacks(plain, "in this category by", "share of a");
  lacks(BW.awardLine({ title: "x", matched_by: "title word <b>crm</b>" }), "<b>crm</b>");
});

const peer = (over = {}) => ({
  key: "CAMDEN", buyer: "London Borough of <Camden>", type_label: "London boroughs", awards: 3, frameworks: 1, main_supplier: "Acme & Co",
  is_me: false, latest: { supplier: "Acme & Co", value: 100000, value_is_ceiling: false, started: "2025-03-01", route: "Open procedure", title: "x" },
  ...over,
});

test("peersTableHtml", () => {
  has(BW.peersTableHtml([]), "No buyers match");
  const html = BW.peersTableHtml([peer({ is_me: true }), peer({ key: "ISLINGTON", buyer: "Islington", frameworks: 2, main_supplier: null,
    latest: { supplier: null, value: 5e6, value_is_ceiling: true, started: null, route: null } }), peer({ key: "HACKNEY", buyer: "Hackney", frameworks: 0 })]);
  has(html, 'data-key="CAMDEN"', "London Borough of &lt;Camden&gt;", "Acme &amp; Co", "Your organisation", "1 framework<", "2 frameworks<",
    "£100k", "started 1 Mar 2025", "Open procedure", "Framework ceiling", "Title not published", "Not stated", '<span class="bw-faint">–</span>');
  lacks(html, "<Camden>", "1 frameworks", "<script");
  assert.equal((html.match(/Your organisation/g) || []).length, 1, "only the user's own row is marked");
  assert.equal((html.match(/data-action="peer-toggle"/g) || []).length, 3);
});

test("row actions: similar suppliers always, Insights only when the feature is on", () => {
  const rows = [peer({ main_supplier_key: "ACME", main_supplier: "Acme & Co" }), peer({ key: "ISLINGTON", buyer: "Islington", main_supplier: null })];
  BW.state.insights = false;
  let html = BW.peersTableHtml(rows);
  has(html, 'data-action="similar-suppliers" data-supplier="ACME"', "View similar suppliers");
  lacks(html, 'data-action="peer-insights"');
  assert.equal((html.match(/data-action="similar-suppliers"/g) || []).length, 1, "no button for a row without a main supplier");
  BW.state.insights = true;
  html = BW.peersTableHtml(rows);
  assert.equal((html.match(/data-action="peer-insights"/g) || []).length, 2);
  assert.ok(html.indexOf('data-action="peer-toggle"') < html.indexOf('data-action="peer-insights"'), "Insights sits below Contracts");
  BW.state.insights = false;
});

const insightData = (over = {}) => ({
  buyer: "Wigan <Council>", type_label: "Other local government", category: { label: "Housing repairs: gas" },
  in_category: { awards: 3, frameworks: 1, suppliers: 2, total_value: 250000, shown: 1, recent_awards: [{ title: "Boiler <b>plant</b>", supplier: "Hayman", value: 132000, signed: "2026-08-18", route: "Open competition", url: "javascript:alert(1)" }] },
  profile: { buyer_type: "x", stats: { total_contracts: 6, total_spend: 480000, earliest_award: "2022-01-10", latest_award: "2025-01-10", unique_suppliers: 3, direct_awards: 1, competitive_awards: 5 },
    top_suppliers: [{ supplier: "Acme <Ltd>", contracts: 3, value: 300000, framework_appointments: 0 }], sectors: [{ label: "Boiler maintenance", awards: 4 }],
    recent_awards: [{ title: "Old job", supplier: "Acme", value: 5e6, value_is_ceiling: true, signed: "2025-01-10", competitive: 1, url: null }] },
  ...over,
});

test("insightFactsHtml", () => {
  const html = BW.insightFactsHtml(insightData());
  has(html, "Contracts on record", "£480k", "5 competitive · 1 direct", "In Housing repairs: gas", "3 awards", "1 framework appointment", "Main suppliers", "Acme &lt;Ltd&gt;",
    "Frequent sectors", "Recent award history", "Framework ceiling", "Competitive", "Boiler &lt;b&gt;plant&lt;/b&gt;");
  lacks(html, "<b>plant", "javascript:", "<Ltd>");
  const bare = BW.insightFactsHtml(insightData({ profile: null }));
  has(bare, "full buyer profile could not be loaded", "In Housing repairs: gas");
  lacks(bare, "Main suppliers", "Contracts on record");
});

test("insightNarrativeHtml: waiting, written, too thin, unavailable", () => {
  has(BW.insightNarrativeHtml(null), "Writing a summary");
  const ok = BW.insightNarrativeHtml({ available: true, narrative: "It <awards> 6 contracts.", provider: "DeepSeek", based_on: ["6 contract awards <x>."] });
  has(ok, "AI insight", "It &lt;awards&gt; 6 contracts.", "Written by DeepSeek", "Facts it was given", "6 contract awards &lt;x&gt;.");
  lacks(ok, "<awards>", "<x>");
  has(BW.insightNarrativeHtml({ available: false, reason: "thin", message: "Only 1 award on record." }), "Only 1 award on record.", "bw-note--info");
  const down = BW.insightNarrativeHtml({ available: false, reason: "unavailable", message: "Insight unavailable right now." });
  has(down, "Insight unavailable right now.", "figures above are unaffected");
  lacks(down, "Writing a summary", "Facts it was given");
});

test("similarSuppliersHtml", () => {
  const base = { supplier: "Acme <Ltd>", basis: "cpv", cpv: ["50721"], cpv_labels: { 50721: "Boiler maintenance" }, category: { label: "Housing repairs: gas" }, total: 30,
    rows: [{ supplier: "Beta Heating", shared_awards: 2, awards: 3, shared_value: 200000, buyers: 2, shared_cpv: ["50721"] }, { supplier: "Gamma", shared_awards: 1, awards: 1, shared_value: null, buyers: 1, shared_cpv: [] }] };
  const html = BW.similarSuppliersHtml(base);
  has(html, "same CPV classes as Acme &lt;Ltd&gt;", "50721 Boiler maintenance", "Beta Heating", "£200k", "of 3", "CPV 50721", "Showing 2 of 30", '<span class="bw-faint">–</span>');
  lacks(html, "<Ltd>");
  has(BW.similarSuppliersHtml({ ...base, basis: "category", cpv: [] }), "carry no CPV code", "every other supplier");
  has(BW.similarSuppliersHtml({ ...base, rows: [], total: 0 }), "No similar suppliers found");
});

test("companyProfileHtml: sources fail soft, text is escaped, owners and filings render", () => {
  const html = BW.companyProfileHtml({ supplier: { name: "Acme" }, google: { status: "ok", rating: 4.25, count: 120, url: "https://maps.example/x" },
    companies_house: { status: "ok", matched_by: "name", company_number: "01234567", name: "ACME <LTD>", company_status: "active", incorporated: "2010-03-01", last_accounts: "2025-03-31", accounts_next_due: "2026-12-31",
      last_confirmation_statement: "2025-06-01", url: "https://find.example/c/01234567", filings_error: false, owners_error: false,
      filings: [{ date: "2025-09-01", description: "accounts-with-accounts-type-full", category: "accounts" }],
      owners: [{ name: "Jane <Doe>", control: ["ownership of shares 25 to 50 percent"], since: "2020-01-01", ceased: null }, { name: "Old Owner", control: [], ceased: "2021-01-01" }] } });
  has(html, "4.3 <small>/ 5</small>", "120 reviews", "ACME &lt;LTD&gt;", "Last accounts made up to", "31 Mar 2025", "accounts with accounts type full",
    "Jane &lt;Doe&gt;", "ownership of shares 25 to 50 percent", "1 former owner not shown", "Matched to Companies House by name");
  lacks(html, "<LTD>", "<Doe>", "Old Owner");
  has(BW.companyProfileHtml({ supplier: {}, google: { status: "not_connected" }, companies_house: { status: "error" } }), "Not connected: no API key", "Unavailable right now");
});

test("not awarded tab: two groups, escaped, show-more only when there is more", () => {
  const d = { category: { label: "Housing <repairs>" }, limit: 25,
    elsewhere: { total: 40, groups: [{ cpv: "507", label: "Repair services" }], rows: [{ supplier_id: 7, supplier: "Nearby <Co>", region: "North West", sme: true, notices: 3, latest: "2025-06-01" }] },
    registered: { total: 1, terms: ["gas", "boiler"], rows: [{ supplier_id: 9, supplier: "Gas Heat Ltd", region: null, sme: false }] } };
  const html = BW.notAwardedHtml(d);
  has(html, "Awarded elsewhere, none in this category", "Registered, no awards anywhere", "Housing &lt;repairs&gt;", "Nearby &lt;Co&gt;", "North West · SME · 3 notices in related codes", "CPV 507 Repair services",
    "gas, boiler", 'data-action="company-profile" data-supplier-id="7"', "Showing 1 of 40", 'data-action="not-awarded-more"', "Gas Heat Ltd");
  lacks(html, "<Co>", "<repairs>");
  assert.equal((html.match(/not-awarded-more/g) || []).length, 1, "registered list is complete, so no second button");
  has(BW.notAwardedHtml({ ...d, elsewhere: { total: 0, rows: [], groups: [] }, registered: { total: 0, rows: [], terms: [] } }), "defined by search words only", "No words in this category");
  const tabs = BW.similarTabsHtml("notawarded", "x");
  has(tabs, 'data-tab="awarded" aria-selected="false"', 'data-tab="notawarded" aria-selected="true"');
});

const supplierRow =  (over = {}) => ({
  supplier: "Acme <Ltd>", buyers: 4, contracts: 5, framework_appointments: 1, total_value: 750000, valued_contracts: 4, avg_contract: 187500,
  avg_term_months: 24, repeat_rate: 0.33, price_band: "£ Lower", latest_signed: "2025-03-01", ...over,
});

test("suppliersPanelHtml", () => {
  has(BW.suppliersPanelHtml({ suppliers: { total: 0, rows: [] } }), "No suppliers found");
  const html = BW.suppliersPanelHtml({ suppliers: { total: 30, rows: [supplierRow(), supplierRow({
    supplier: "Beta", buyers: 2, contracts: 2, framework_appointments: 0, total_value: null, valued_contracts: 0, avg_contract: null,
    avg_term_months: null, repeat_rate: null, price_band: null, latest_signed: null }), supplierRow({ supplier: "Gamma", avg_term_months: 18 })] } });
  has(html, "Acme &lt;Ltd&gt;", 'title="Acme &lt;Ltd&gt;"', "30 suppliers in this view; the 3 used by the most buyers.", "width:100.0%", "width:50.0%",
    "2 yrs", "1.5 yrs", "33%", "£ Lower", "£188k", "4 with a value", "latest award 1 Mar 2025", "There are no ratings");
  lacks(html, "<Ltd>");
  const beta = html.slice(html.lastIndexOf(">Beta<"));  // its table row, not its bar
  has(beta.slice(0, beta.indexOf("</tr>")), "–");
});

const cost = (over = {}) => ({
  overall: { type: null, label: "All buyers in this view", contracts: 8, annualised: 6,
    annual: { p10: 150000, median: 225000, p90: 300000, min: 100000, max: 300000 },
    total: { p10: 150000, median: 350000, p90: 550000, min: 100000, max: 600000 } },
  by_type: [
    { type: "london-borough", label: "London boroughs", contracts: 5, annualised: 3, annual: null, total: { p10: 120000, median: 200000, p90: 360000, min: 100000, max: 400000 } },
    { type: "county", label: "County councils", contracts: 3, annualised: 3, annual: null, total: null },
  ],
  excluded_frameworks: 3, excluded_no_value: 2, ...over,
});

test("costPanelHtml shows annual or total ranges and says what is left out", () => {
  const annual = BW.costPanelHtml(cost(), "annual");
  has(annual, "Typical annual value", "£150k to £300k a year · typical £225k", "6 contracts with a stated term (of 8 with a value)",
    "smallest £100k, largest £300k", 'data-basis="annual" aria-pressed="true"', 'data-basis="total" aria-pressed="false"',
    "3 framework or call-off awards (shared ceilings) and 2 awards without a value are left out");
  assert.equal((annual.match(/class="bw-range"/g) || []).length, 1, "only blocks that have an annual range");

  lacks(annual, "very large award");
  has(BW.costPanelHtml(cost({ excluded_outliers: 1 }), "annual"), "The typical range also excludes 1 very large award of £250m or more");
  has(BW.costPanelHtml(cost({ excluded_outliers: 3 }), "annual"), "excludes 3 very large awards");

  const total = BW.costPanelHtml(cost(), "total");
  has(total, "Typical total contract value", "All buyers in this view", "London boroughs");
  lacks(total, "County councils");
  assert.equal((total.match(/class="bw-range"/g) || []).length, 2);

  const fallback = BW.costPanelHtml(cost({ overall: { ...cost().overall, annual: null } }), "annual");
  has(fallback, "Typical total contract value", 'data-basis="annual" aria-pressed="false" disabled');

  const single = BW.costPanelHtml(cost({ by_type: [cost().by_type[0]] }), "total");
  has(single, "London boroughs");
  lacks(single, "All buyers in this view");
  assert.equal((single.match(/class="bw-range"/g) || []).length, 1, "one group is not repeated under an 'all buyers' row");

  const none = BW.costPanelHtml(cost({ overall: { ...cost().overall, annual: null, total: null }, by_type: [] }), "total");
  has(none, "Not enough published values", "Fewer than 5 direct contracts");
  lacks(none, "bw-range__track");
});

test("costPanelHtml keeps every bar inside its track", () => {
  const html = BW.costPanelHtml(cost(), "total");
  const bars = [...html.matchAll(/class="bw-range__fill" style="left:([\d.]+)%;width:([\d.]+)%"/g)].map((m) => [Number(m[1]), Number(m[2])]);
  assert.equal(bars.length, 2);
  bars.forEach(([left, width]) => {
    assert.ok(left >= 0 && width >= 0.8 && left + width <= 100.01, `bar ${left}+${width} escapes the track`);
  });
  const mids = [...html.matchAll(/class="bw-range__mid" style="left:([\d.]+)%"/g)].map((m) => Number(m[1]));
  mids.forEach((m, i) => assert.ok(m >= bars[i][0] && m <= bars[i][0] + bars[i][1] + 0.01, "the median sits inside its own bar"));
});

test("kpiHtml", () => {
  const html = BW.kpiHtml({ awards: 73, frameworks: 23, buyers: 13, suppliers: 40, total_value: 12400000, coverage: { with_value: 0.727 } });
  has(html, ">73<", "23 frameworks or call-offs", ">13<", ">40<", "£12m", "73% of awards state a direct value");
  const empty = BW.kpiHtml({ awards: 0, frameworks: 0, buyers: 0, suppliers: 0, total_value: null });
  has(empty, ">0<", ">–<");
});

test("renewalsTableHtml", () => {
  const html = BW.renewalsTableHtml([
    { title: "Heat <meters>", supplier: "Acme", value: 250000, value_is_ceiling: false, ends: "2026-11-01", days_left: 27, route: "Open procedure", cpv: "50721000", url: "https://example.org/n" },
    { title: null, supplier: null, value: null, ends: "2026-12-01", days_left: 1, route: null, cpv: "", url: "javascript:alert(1)" },
  ]);
  has(html, 'href="https://example.org/n"', "Heat &lt;meters&gt;", "in 27 days", "in 1 day<", 'data-title="Re-procurement: Heat &lt;meters&gt;"',
    'data-cpv="50721000"', "Supplier not stated", "Value not published", "Not stated", "1 Nov 2026");
  lacks(html, "javascript:", "<meters>");
  assert.equal((html.match(/data-action="plan-from"/g) || []).length, 2);
});

test("activityTableHtml links each award back to its category", () => {
  const html = BW.activityTableHtml([
    { buyer: "London Borough of Islington", title: "Gas servicing", category: "Housing repairs & gas servicing", category_key: "preset:housing-repairs-gas",
      supplier: "Acme", value: 100000, value_is_ceiling: false, signed: "2026-08-01" },
    { buyer: "Hackney", title: "", category: "Boilers", category_key: "cpv:5072|q:boiler", supplier: null, value: null, signed: null },
    { buyer: "Kent", title: "", category: null, category_key: null, supplier: "X", value: null, signed: null },
  ]);
  has(html, 'href="#/radar?category=housing-repairs-gas"', "Housing repairs &amp; gas servicing", 'href="#/radar?cpv=5072&amp;q=boiler"', "1 Aug 2026", "£100k");
  assert.equal((html.match(/class="bw-link"/g) || []).length, 2, "no link when there is no category");
});

// ── Market Engagement renderers ───────────────────────────────────────────────────────────────
BW.state.options = { presets: [
  { preset: "housing-repairs-gas", label: "Housing repairs & gas servicing", hint: "" },
  { preset: "crm-case-management", label: "CRM & case management software", hint: "" },
] };

const STEP_TITLES = ["Define scope & category", "Identify suppliers to engage", "Publish the engagement notice", "Collect responses & keep it fair", "Hand over to the tender"];
const steps = (...states) => STEP_TITLES.map((title, i) => ({ n: i + 1, title, state: states[i] }));
const plan = (over = {}) => ({
  id: 7, title: 'Heat "meters" <PME>', organisation: "Camden", category: { key: "preset:housing-repairs-gas", label: "Housing repairs & gas servicing", preset: "housing-repairs-gas" },
  category_label: "Housing repairs & gas servicing", est_value: 2100000, term_years: 4, engagement_type: "both", objectives: "", supplier_day_at: "20 Nov",
  supplier_day_place: "Hall", response_deadline: "2026-11-14", contact_name: "Jane", contact_email: "jane@example.org", status: "draft", notice_text: null,
  published_url: null, published_at: null, closed_at: null, converted_at: null, created_at: "2026-10-05T10:00:00", updated_at: "2026-10-05T10:00:00",
  steps: steps("done", "active", "upcoming", "upcoming", "upcoming"), supplier_counts: { included: 2, responded: 1, contacted: 1 },
  suppliers: [
    { id: 11, supplier_name: "Acme <Ltd>", included: true, status: "invited", note: "Phoned", source: "matched",
      stats: { buyers: 4, awards: 5, avg_contract: 187500, size_hint: "smaller contracts", latest_signed: "2025-03-01" } },
    { id: 12, supplier_name: "Local Plumbing", included: true, status: "responded", note: null, source: "manual", stats: null },
    { id: 13, supplier_name: "Left Out Ltd", included: false, status: "not_contacted", note: null, source: "matched", stats: null },
  ],
  log: [{ id: 1, at: "2026-10-05T10:00:00", kind: "created", message: "Plan created" }, { id: 2, at: "2026-10-06T09:00:00", kind: "note", message: "Second <entry>" }],
  allowed_transitions: ["published"], ...over,
});
const use = (p) => { ENG.detail.plan = p; ENG.detail.scopeDraft = null; ENG.detail.noticeDraft = null; ENG.detail.missing = []; return p; };

test("the engagement script registers itself on the shared BW object", () => {
  assert.equal(typeof BW.openNewEngagement, "function");
  assert.ok(ENG && typeof ENG.detailHtml === "function");
  ["click", "change", "input"].forEach((type) => assert.equal((listeners[type] || []).length, 1, `one ${type} listener`));
});

test("statusBadge and planCard", () => {
  has(ENG.statusBadge("published"), "Published", "bw-badge--good");
  has(ENG.statusBadge("converted"), "Handed over");
  has(ENG.statusBadge("nonsense"), "Draft");
  const card = ENG.planCard(plan());
  has(card, 'href="#/engagements/7"', "Heat &quot;meters&quot; &lt;PME&gt;", "£2.1m over 4 yrs", "2 suppliers, 1 responded", "Next: Identify suppliers to engage");
  lacks(card, '"meters"', "<PME>");
  has(ENG.planCard(plan({ term_years: 1 })), "over 1 yr<");
  has(ENG.planCard(plan({ est_value: null })), "2 suppliers");
  lacks(ENG.planCard(plan({ est_value: null })), "£");
  has(ENG.planCard(plan({ steps: steps("done", "done", "done", "done", "done"), status: "converted" })), "All steps complete");
  const mini = ENG.miniSteps(steps("done", "active", "upcoming", "upcoming", "upcoming"));
  assert.equal((mini.match(/<i /g) || []).length, 5);
  has(mini, 'class="is-done"', 'class="is-active"');
});

test("a new engagement starts on the category being researched, not always the first preset", () => {
  assert.deepEqual(ENG.prefillSpec({ category: { preset: "crm-case-management" } }), { preset: "crm-case-management" });
  assert.deepEqual(ENG.prefillSpec({ category: { cpv: "5072" } }), { cpv: "5072", q: undefined });
  assert.deepEqual(ENG.prefillSpec({ category: { q: "tree surgery" } }), { cpv: undefined, q: "tree surgery" });
  assert.deepEqual(ENG.prefillSpec({ cpv: 72212 }), { cpv: "72212" });
  assert.equal(ENG.prefillSpec({}), null);
  assert.equal(ENG.prefillSpec({ category: null }), null);
  has(ENG.categoryFields(ENG.prefillSpec({ category: { preset: "crm-case-management" } })), '<option value="crm-case-management" selected>');
  has(ENG.categoryFields(null), "such as 50720000");   // a real full CPV code; a 4-digit one worked but the full one is what people copy
});

test("stepSummary says where each step stands", () => {
  const p = plan();
  assert.equal(ENG.stepSummary(p, 1), "Housing repairs &amp; gas servicing · £2.1m");
  assert.equal(ENG.stepSummary(plan({ est_value: null }), 1), "Housing repairs &amp; gas servicing");
  assert.equal(ENG.stepSummary(p, 2), "2 suppliers chosen");
  assert.equal(ENG.stepSummary(p, 3), "Not drafted yet");
  assert.equal(ENG.stepSummary(plan({ notice_text: "x" }), 3), "Draft saved");
  // generated or typed but not saved: neither "Not drafted yet" nor "Draft saved"
  ENG.detail.noticeDraft = "A generated notice";
  assert.equal(ENG.stepSummary(p, 3), "Unsaved draft");
  assert.equal(ENG.stepSummary(plan({ notice_text: "x" }), 3), "Unsaved draft");
  assert.equal(ENG.stepSummary(plan({ notice_text: "A generated notice" }), 3), "Draft saved", "once saved it is just saved");
  ENG.detail.noticeDraft = "   ";
  assert.equal(ENG.stepSummary(p, 3), "Not drafted yet", "a blank box is not a draft");
  ENG.detail.noticeDraft = null;
  assert.equal(ENG.stepSummary(plan({ published_at: "2026-11-02T09:00:00" }), 3), "Published 2 Nov 2026");
  assert.equal(ENG.stepSummary(p, 4), "Opens when you publish");
  assert.equal(ENG.stepSummary(plan({ status: "published" }), 4), "1 of 2 responded");
  assert.equal(ENG.stepSummary(p, 5), "Not yet");
  assert.equal(ENG.stepSummary(plan({ converted_at: "2026-11-20T09:00:00" }), 5), "Handed over 20 Nov 2026");
});

test("stepSuppliers lists everyone, ticks only those included, and stays read-only once handed over", () => {
  const html = ENG.stepSuppliers(use(plan()));
  has(html, "Acme &lt;Ltd&gt;", 'aria-label="Include Acme &lt;Ltd&gt;"', "Added by you", "smaller contracts", "4 buyers · 5 awards · typical contract £188k",
    "latest award 1 Mar 2025", "2 of 3 included.", "Left Out Ltd", "proxy: notices do not say whether a supplier is an SME");
  lacks(html, "<Ltd>");
  assert.equal((html.match(/data-field="included" checked/g) || []).length, 2, "the supplier that was taken off the list is not ticked");
  lacks(html, "disabled");
  has(ENG.stepSuppliers(use(plan({ suppliers: [], supplier_counts: { included: 0, responded: 0, contacted: 0 } }))), "No suppliers yet.");
  const locked = ENG.stepSuppliers(use(plan({ status: "converted" })));
  assert.ok((locked.match(/disabled/g) || []).length >= 6, "every control is disabled after hand-over");
  use(plan());
});

test("stepNotice never lets a stored notice or link break out", () => {
  const draft = ENG.stepNotice(use(plan()));
  has(draft, "Generate draft", "TenderFlow does not publish notices", 'id="publishUrl"', "Mark as published", "Procurement Act 2023");
  lacks(draft, "Still to fill in");
  ENG.detail.missing = ["response deadline", "contact name and email"];
  has(ENG.stepNotice(plan()), "Still to fill in:", "response deadline, contact name and email");
  ENG.detail.missing = [];
  const saved = ENG.stepNotice(use(plan({ notice_text: "</textarea><script>alert(1)</script>" })));
  has(saved, "&lt;/textarea&gt;&lt;script&gt;alert(1)&lt;/script&gt;", "Regenerate from scope");
  lacks(saved, "</textarea><script>");
  const published = ENG.stepNotice(use(plan({ status: "published", published_at: "2026-11-02T09:00:00", published_url: "https://www.find-tender.service.gov.uk/Notice/1" })));
  has(published, "Marked as published 2 Nov 2026", 'href="https://www.find-tender.service.gov.uk/Notice/1"');
  lacks(published, 'id="publishUrl"');
  const unsafe = ENG.stepNotice(use(plan({ status: "published", published_url: "javascript:alert(1)" })));
  has(unsafe, 'href="#"');
  lacks(unsafe, 'href="javascript:');
  use(plan());
});

test("stepResponses", () => {
  const html = ENG.stepResponses(use(plan({ status: "published" })));
  has(html, "Phoned", "Local Plumbing", "Close the engagement", 'id="logNote"');
  lacks(html, "Left Out Ltd");
  assert.ok(html.indexOf("Second &lt;entry&gt;") !== -1 && html.indexOf("Second &lt;entry&gt;") < html.indexOf("Plan created"), "newest entry first");
  assert.equal((html.match(/<option value="invited" selected>/g) || []).length, 1);
  assert.equal((html.match(/<option value="responded" selected>/g) || []).length, 1);
  lacks(ENG.stepResponses(use(plan())), "Close the engagement");
  has(ENG.stepResponses(use(plan({ suppliers: [] }))), "Choose suppliers in step 2 first.");
  const locked = ENG.stepResponses(use(plan({ status: "converted" })));
  lacks(locked, 'id="logNote"');
  use(plan());
});

test("stepHandover", () => {
  has(ENG.stepHandover(use(plan({ status: "closed", allowed_transitions: ["converted", "published"] }))), "Download hand-over pack", "Mark as handed over to the tender");
  const early = ENG.stepHandover(use(plan()));
  has(early, "You can hand over once the notice has been published.");
  lacks(early, "Mark as handed over");
  const done = ENG.stepHandover(use(plan({ status: "converted", converted_at: "2026-11-20T09:00:00", allowed_transitions: [] })));
  has(done, "Handed over 20 Nov 2026", "now read-only");
  lacks(done, "Mark as handed over to the tender</button>");
  has(ENG.stepHandover(use(plan())), ">2<", ">1<");
  use(plan());
});

test("stepScope", () => {
  const html = ENG.stepScope(use(plan()));
  has(html, 'value="Heat &quot;meters&quot; &lt;PME&gt;"', "Save scope", 'href="#/radar?category=housing-repairs-gas"', "Research it in Market Radar",
    '<option value="housing-repairs-gas" selected>', '<option value="crm-case-management" >', "Something else…");
  lacks(html, 'value="Heat "meters"');
  has(html, 'class="bw-field-row " id="dayRow"');
  has(ENG.stepScope(use(plan({ engagement_type: "questionnaire" }))), 'class="bw-field-row hidden" id="dayRow"');
  const custom = ENG.stepScope(use(plan({ category: { key: "cpv:5072", label: "Repair", cpv: "5072" } })));
  has(custom, '<option value="__custom" selected>', 'value="50720000"');
  const locked = ENG.stepScope(use(plan({ status: "converted" })));
  lacks(locked, "Save scope");
  assert.ok((locked.match(/disabled/g) || []).length >= 10);
  use(plan());
});

test("detailHtml", () => {
  ENG.detail.open = new Set([2]);
  const html = ENG.detailHtml(use(plan()));
  has(html, "Heat &quot;meters&quot; &lt;PME&gt;", "Draft", "← All engagements", "Delete this engagement", "Housing repairs &amp; gas servicing · Camden",
    'id="step-2"', 'aria-expanded="true" aria-controls="step-2"', 'aria-expanded="false" aria-controls="step-1"');
  lacks(html, 'id="step-1"', 'id="step-3"');
  assert.equal((html.match(/<li class="bw-step bw-step--/g) || []).length, 5);
  has(html, "bw-step--done", "bw-step--active", "bw-step--upcoming");
  lacks(ENG.detailHtml(use(plan({ status: "converted" }))), "Delete this engagement");
  use(plan());
});

test("category fields: reading, comparing and drawing", () => {
  const fakeRoot = (choice, custom) => ({ querySelector: (sel) => ({ "#engCategory": { value: choice }, "#engCategoryCustom": { value: custom } })[sel] });
  assert.deepEqual(ENG.readCategory(fakeRoot("housing-repairs-gas", "")), { preset: "housing-repairs-gas" });
  assert.deepEqual(ENG.readCategory(fakeRoot("__custom", " 5072 ")), { cpv: "5072" });
  assert.deepEqual(ENG.readCategory(fakeRoot("__custom", "50-72")), { cpv: "5072" });
  assert.deepEqual(ENG.readCategory(fakeRoot("__custom", "tree surgery")), { q: "tree surgery" });
  assert.deepEqual(ENG.readCategory(fakeRoot("__custom", "   ")), { q: "" }, "empty text is caught by the caller");

  assert.equal(ENG.sameCategory({ preset: "a" }, { preset: "a", key: "x", label: "y" }), true);
  assert.equal(ENG.sameCategory({ cpv: "5072" }, { cpv: "5072", q: undefined }), true);
  assert.equal(ENG.sameCategory({}, {}), true);
  // The box now shows the 8-digit code while the plan holds the prefix: saving an untouched scope is not a category change.
  assert.equal(ENG.sameCategory({ cpv: "50720000" }, { cpv: "5072" }), true);
  assert.equal(ENG.sameCategory({ cpv: "48445000", q: "crm" }, { cpv: "48445", q: "crm" }), true);
  assert.equal(ENG.sameCategory({ cpv: "50720000" }, { cpv: "50721000" }), false);
  assert.equal(ENG.sameCategory({ cpv: "50720000" }, { q: "boilers" }), false);
  assert.equal(ENG.sameCategory({ preset: "a" }, { preset: "b" }), false);
  assert.equal(ENG.sameCategory({ preset: "a" }, { cpv: "a" }), false);

  const preset = ENG.categoryFields({ preset: "crm-case-management" });
  has(preset, "data-category-select", '<option value="crm-case-management" selected>', 'class="hidden"');
  const custom = ENG.categoryFields({ cpv: "5072", q: "boiler" }, "sCategory", "disabled");
  // The API keeps a CPV as its prefix (48445000 -> 48445); the box shows the 8-digit code people copy from a notice.
  has(custom, '<option value="__custom" selected>', 'value="50720000 boiler"', 'id="sCategoryWrap" class=""', "disabled");
  has(ENG.categoryFields({ cpv: "48445" }), 'value="48445000"');
  has(ENG.categoryFields({ cpv: "48445000" }), 'value="48445000"');
  has(ENG.categoryFields({ q: "tree surgery" }), 'value="tree surgery"');
  has(ENG.categoryFields(null), 'class="hidden"');
});

// ── runner ────────────────────────────────────────────────────────────────────────────────────
(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try {
      await fn();
      console.log(`ok    ${name}`);
    } catch (err) {
      failed += 1;
      console.log(`FAIL  ${name}`);
      console.log(err && err.stack ? err.stack : err);
    }
  }
  console.log(`\n${tests.length - failed} of ${tests.length} passed`);
  process.exitCode = failed ? 1 : 0;
})();
