// Run: node tests/funding_view_test.js
const assert = require("assert");
const f = require("../web/funding.js");

assert.strictEqual(f.money(30500), "£30.5k");
assert.strictEqual(f.money(2000000), "£2m");
assert.strictEqual(f.money(1500), "£1.5k");
assert.strictEqual(f.awardText({ min_award: 1500, max_award: 30500 }), "£1.5k to £30.5k");
assert.strictEqual(f.awardText({ min_award: null, max_award: 50000 }), "Up to £50k");
assert.strictEqual(f.awardText({ min_award: null, max_award: null }), "Award size not stated");

const grant = {
  name: 'Grant <b>"one"</b>', funder: "Defra", summary: "First line\nSecond line", min_award: 1000, max_award: 50000, total_award: 2000000,
  locations: ["England"], applicants: ["Private Sector"], close_date: "2026-11-05", days_left: 14, ongoing: false,
  apply_url: "https://example.gov.uk/apply", url: "https://find-government-grants.service.gov.uk/grants/x", matches: ["boiler", "pump"], band: "mid",
};
const html = f.cardHtml(grant);
assert(!html.includes("<b>"), "grant text is escaped");
assert(html.includes("Closes 5 Nov 2026 · 14 days left") && html.includes("funding-tag--soon"));
assert(html.includes("Matches your profile: boiler, pump") && html.includes("funding-fit--mid"));
assert(html.includes("First line") && !html.includes("Second line"), "only the first paragraph of the summary shows");
assert(html.includes('href="https://example.gov.uk/apply"') && html.includes("Full details"));

const allYear = f.cardHtml({ ...grant, ongoing: true, close_date: "2030-04-01", days_left: null, matches: [], apply_url: null });
assert(allYear.includes("Open all year") && !allYear.includes("2030") && !allYear.includes("Matches your profile"));
assert(allYear.includes('href="https://find-government-grants.service.gov.uk/grants/x"') && !allYear.includes("Full details"));

assert.strictEqual(f.pagerHtml({ page: 1, pages: 1 }), "");
assert(f.pagerHtml({ page: 2, pages: 3 }).includes('data-page="1"') && f.pagerHtml({ page: 3, pages: 3 }).includes("disabled"));
assert(f.metaText({ imported: false }).includes("fag_import"));
assert.strictEqual(f.metaText({ imported: true, total: 0 }), "No open grants match these filters.");
assert(f.metaText({ imported: true, total: 1, has_profile: true }).startsWith("1 open grant."));
console.log("funding view ok");
