// Run: node tests/funding_view_test.js
const assert = require("assert");
const f = require("../web/funding.js");

assert.strictEqual(f.money(30500), "£30.5k");
assert.strictEqual(f.money(2000000), "£2m");
assert.strictEqual(f.money(2500), "£2,500");
assert.strictEqual(f.awardText({ min_award: 1500, max_award: 30500 }), "£1,500 to £30.5k");
assert.strictEqual(f.awardText({ min_award: null, max_award: 50000 }), "Up to £50k");
assert.strictEqual(f.awardText({ min_award: null, max_award: null, total_award: 3800000 }), "Pot £3.8m");
assert.strictEqual(f.awardText({ min_award: null, max_award: null }), "Award size not stated");

const grant = {
  label: "x", name: 'Grant <b>"one"</b>', funder: "Defra", summary: "First line", min_award: 1000, max_award: 50000,
  locations: ["National"], applicants: ["Private Sector"], close_date: "2026-11-05", days_left: 6, ongoing: false,
  fit: { verdict: "unlikely", blocker: "Only open in <Scotland>" }, saved: false,
};
const html = f.cardHtml(grant);
assert(!html.includes("<b>") && !html.includes("<Scotland>"), "grant text is escaped");
assert(html.includes("Closes 5 Nov 2026 · 6 days left") && html.includes("funding-tag--soon"));
assert(html.includes("Unlikely to qualify") && html.includes("funding-fit--unlikely") && html.includes("<strong>Blocker:</strong> Only open in &lt;Scotland&gt;"));
assert(html.includes("UK-wide") && html.includes('data-details="x"') && html.includes('data-save="x"') && html.includes(">Save<"));

const timed = f.cardHtml({ ...grant, close_at: "2028-03-31T23:59", close_date: "2028-03-31", days_left: 521 });
assert(timed.includes("Closes 31 Mar 2028, 11:59pm · 521 days left"));
assert(f.cardHtml({ ...grant, close_at: "2026-11-04T12:00" }).includes(", 12:00pm") && f.cardHtml({ ...grant, close_at: "2026-11-04T00:30" }).includes(", 12:30am"));
assert(f.metaText({ imported: true, total: 9, open_total: 10, likely: 1, has_profile: true, closed_today: 1 }).endsWith("1 grant closed today and is hidden"));
const likely = f.cardHtml({ ...grant, fit: { verdict: "likely", why: "UK wide" }, saved: true });
assert(likely.includes("Likely eligible") && likely.includes("<strong>Why:</strong> UK wide") && likely.includes("is-saved") && likely.includes(">Saved<"));
const check = f.cardHtml({ ...grant, fit: { verdict: "check", check: "Read the criteria" }, locations: ["England", "Wales"] });
assert(check.includes("Check eligibility") && check.includes("<strong>Check:</strong> Read the criteria") && check.includes("England, Wales"));

const allYear = f.cardHtml({ ...grant, ongoing: true, close_date: "2030-04-01", days_left: null, fit: undefined });
assert(allYear.includes("Rolling, no fixed deadline") && !allYear.includes("2030") && !allYear.includes("funding-fit"));

assert.strictEqual(f.pagerHtml({ page: 1, pages: 1 }), "");
assert(f.pagerHtml({ page: 2, pages: 3 }).includes('data-page="1"') && f.pagerHtml({ page: 3, pages: 3 }).includes("disabled"));
assert(f.metaText({ imported: false }).includes("fag_import"));
assert.strictEqual(f.metaText({ imported: true, total: 0 }), "No open grants match these filters.");
assert.strictEqual(f.metaText({ imported: true, total: 9, open_total: 10, likely: 1, has_profile: true }), "9 of 10 open grants · 1 likely eligible");
assert.strictEqual(f.metaText({ imported: true, total: 1, open_total: 1, has_profile: true }), "1 open grant");
assert(f.metaText({ imported: true, total: 2, open_total: 2, has_profile: false }).includes("Add a company profile"));
assert.strictEqual(f.awardText({ min_award: 1000000, max_award: null }), "From £1m");
assert.strictEqual(f.awardText({ min_award: 2500, max_award: 2500 }), "£2,500");

const d = f.detailHtml({ ...grant, summary: "Para one\nPara <two>", eligibility: "Must be a UK business\nMust employ staff", apply_url: "https://apply.example/x", url: "https://gov.example/x", open_date: "2026-01-02", total_award: 2000000, applicants: ["Private Sector", "Non-profit"], saved: true });
assert(d.includes("About this grant") && d.includes("<p>Para one</p>") && d.includes("<p>Para &lt;two&gt;</p>"), "summary shows in full, escaped");
assert(d.includes("Who can apply") && d.includes("<p>Must employ staff</p>") && d.includes("Private Sector, Non-profit"));
assert(d.includes('href="https://apply.example/x"') && d.includes('href="https://gov.example/x"') && d.includes("data-drawer-ai") && d.includes(">Saved<"));
assert(d.includes("Closes") && d.includes("6 days left") && d.includes("Total pot") && d.includes("2 Jan 2026"));
assert(f.detailHtml({ ...grant, eligibility: null }).includes("does not list eligibility details"));

global.location = { pathname: "/", search: "", hash: "#funding/some-grant?where=Scotland&q=net%20zero" };
const h = f.parseHash();
assert.strictEqual(h.label, "some-grant");
assert.strictEqual(h.params.get("where"), "Scotland");
assert.strictEqual(h.params.get("q"), "net zero");
global.location = { pathname: "/", search: "", hash: "#funding" };
assert.strictEqual(f.parseHash().label, "");
global.location = { pathname: "/", search: "", hash: "#other" };
assert.strictEqual(f.parseHash(), null);
global.location = { pathname: "/funding/cladding-safety-scheme-1", search: "?where=Scotland&size=10000-100000", hash: "" };
const pth = f.parseHash();
assert.strictEqual(pth.label, "cladding-safety-scheme-1");
assert.strictEqual(pth.params.get("where"), "Scotland");
assert.strictEqual(pth.params.get("size"), "10000-100000");
global.location = { pathname: "/funding", search: "?q=ai", hash: "" };
assert.strictEqual(f.parseHash().label, "");
assert.strictEqual(f.parseHash().params.get("q"), "ai");
global.location = { pathname: "/funding/", search: "", hash: "" };
assert.strictEqual(f.parseHash().label, "", "a trailing slash is fine");
global.location = { pathname: "/fundingx", search: "", hash: "" };
assert.strictEqual(f.parseHash(), null, "another page is not Funding");
console.log("funding view ok");
