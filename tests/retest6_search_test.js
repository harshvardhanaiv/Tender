"use strict";
/* Run: node tests/retest6_search_test.js
   Re-test 6: a short search word matches whole words only ("cars" no longer finds "cardio"), a title-only row with no sign of a one-word
   search is listed last as unconfirmed, and a rate-limited portal that has saved rows says what the list holds. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const S = require(path.join(__dirname, "..", "web", "search-status.js"));
const app = fs.readFileSync(path.join(__dirname, "..", "web", "app.js"), "utf8");
let passed = 0, failed = 0;
function test(name, fn) { try { fn(); passed++; console.log("ok   " + name); } catch (e) { failed++; console.log("FAIL " + name + "\n  " + e.message); } }

const start = app.indexOf("function stemInText(");
const stemInText = new Function(app.slice(start, app.indexOf("function rowSearchText(")) + "; return stemInText;")();

test("a short stem is a whole word with an ordinary ending", () => {
  for (const t of ["supply of 26 new hybrid cars", "ev car salary sacrifice", "cars, vans and buses", "car parks"]) assert.ok(stemInText(t, "car"), t);
  for (const t of ["task force cardio", "care home services", "scarborough borough council", "carpet fitting", "cardiac monitors"]) assert.ok(!stemInText(t, "car"), t);
});
test("a longer stem still matches inside a word", () => {
  assert.ok(stemInText("software development", "develop"));
  assert.ok(stemInText("refurbishment works", "refurbish"));
});
test("the matcher uses it, and a one-word title-only row with no sign of the word is unconfirmed", () => {
  assert.ok(app.includes("const hits = words.filter((w) => stemInText(hay, w));"));
  assert.ok(app.includes("return words.every((w) => stemInText(hay, w));"));
  assert.ok(app.includes("a one-word search: a title-only row that shows no sign of the word"));
});
test("a rate-limited portal with saved rows says what the list holds", () => {
  const list = S.portalList({ portal_results: { find_tender: { label: "Find a Tender (UK)", status: "ok", count: 20, stored: 77, partial: true,
    message: "Find a Tender is rate-limiting requests (HTTP 429). Try again in a minute." } } }, (id) => id);
  const html = S.summaryHtml(list);
  assert.ok(html.includes("20 live results and 77 saved from earlier searches are shown; press Retry for the full live list."), html);
  assert.ok(!html.slice(html.indexOf("portal-summary__reason")).includes("Try again in a minute"), "the reason line no longer says to try again");
});
test("with no saved rows the portal's own message is unchanged", () => {
  const list = S.portalList({ portal_results: { find_tender: { label: "Find a Tender (UK)", status: "error", count: 0, stored: 0,
    message: "Find a Tender is rate-limiting requests (HTTP 429). Try again in a minute." } } }, (id) => id);
  assert.ok(S.summaryHtml(list).includes("Try again in a minute."));
});
console.log(`\n${passed} of ${passed + failed} passed`);
process.exit(failed ? 1 : 0);
