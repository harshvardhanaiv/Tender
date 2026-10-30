"use strict";
/* Run: node tests/search_url_test.js
   Re-test 3 T4 + T6: Tender Search opens on Opportunities, and the query, mode, portals and keyword scope live in the address. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const SearchUrl = require(path.join(__dirname, "..", "web", "search-url.js"));
const app = fs.readFileSync(path.join(__dirname, "..", "web", "app.js"), "utf8");

let passed = 0, failed = 0;
function test(name, fn) { try { fn(); passed++; console.log("ok   " + name); } catch (e) { failed++; console.log("FAIL " + name + "\n  " + e.message); } }
const PORTALS = ["find_tender", "contracts_finder", "pcs"];

test("the default search writes only the query", () => {
  assert.equal(SearchUrl.toParams({ q: "roofing", statusFilter: "opportunities", keywordScope: "all" }, null).toString(), "q=roofing");
});
test("mode, portals and scope are written when they are not the default", () => {
  const s = SearchUrl.toParams({ q: "roofing", statusFilter: "awards", keywordScope: "title" }, ["pcs", "find_tender"]).toString();
  assert.equal(s, "q=roofing&mode=awards&portals=pcs%2Cfind_tender&scope=title");
});
test("they are read back, and unknown values fall back to the defaults", () => {
  assert.deepEqual(SearchUrl.fromSearch("?q=x&mode=awards&portals=pcs,nope&scope=buyer", PORTALS), { mode: "awards", portals: ["pcs"], scope: "buyer" });
  assert.deepEqual(SearchUrl.fromSearch("?q=x&mode=bogus&portals=nope&scope=zzz", PORTALS), { mode: "opportunities", portals: null, scope: null });
  assert.equal(SearchUrl.fromSearch("?mode=awarded", PORTALS).mode, "awards");
  assert.equal(SearchUrl.fromSearch("", PORTALS).mode, "opportunities");
});
test("a saved 'All' no longer overrides the default: the page opens on Opportunities", () => {
  assert.ok(!/state\.statusFilter\s*=\s*lsGet\(LS_STATUS_FILTER/.test(app), "the saved notice type must not be restored on load");
  assert.ok(!/state\.statusFilter = p\.status_filter/.test(app), "the account's saved notice type must not be applied on load");
});
test("search() and initAll use SearchUrl and the page loads it before app.js", () => {
  assert.ok(app.includes("function syncSearchUrl()") && app.includes("SearchUrl.fromSearch("));
  const html = fs.readFileSync(path.join(__dirname, "..", "web", "index.html"), "utf8");
  assert.ok(html.indexOf("/search-url.js") > 0 && html.indexOf("/search-url.js") < html.indexOf("/app.js"));
});
console.log(`\n${passed} of ${passed + failed} passed`);
process.exit(failed ? 1 : 0);
