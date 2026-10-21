"use strict";
/* Run: node tests/search_date_filter_test.js
   The date filter lives in one place: the "Any date" chip above the results (its popover has Anytime, 7d, 14d, 30d, 60d and a
   custom range). The duplicate "Published" box under Tender search in the left rail is gone, and nothing still points at it. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const read = (f) => fs.readFileSync(path.join(__dirname, "..", "web", f), "utf8").replace(/\r\n/g, "\n");
const html = read("index.html");
const app = read("app.js");
const theme = read("theme.css");

const tests = [];
const test = (n, f) => tests.push([n, f]);

test("the left rail has no Published box any more, in the page, the script or the styles", () => {
  for (const [name, src] of [["index.html", html], ["app.js", app], ["theme.css", theme]]) {
    assert.ok(!/railDatePills|railDateFilter|tf-rail__filter|tf-rail__pills/.test(src), name + " still mentions the rail date box");
  }
  const rail = html.slice(html.indexOf('id="navSearch"'), html.indexOf('id="btnDashboard"'));
  assert.ok(!/Published|rm-pill/.test(rail), "nothing between Tender search and Pipeline");
});

test("the Any date chip and its popover still offer every choice", () => {
  assert.match(html, /id="chipPublished"/);
  const pills = html.slice(html.indexOf('id="rmDatePills"'), html.indexOf('id="rmCustomSliderContainer"'));
  for (const d of ["all", "7", "14", "30", "60", "custom"]) assert.ok(pills.includes(`data-days="${d}"`), d);
});

test("the date pills are wired to the popover (and the older filter bar) only", () => {
  assert.match(app, /const containers = \[\$\("datePillsContainer"\), \$\("rmDatePills"\)\]\.filter\(Boolean\);/);
  assert.equal((app.match(/\$\("datePillsContainer"\), \$\("rmDatePills"\)\]/g) || []).length, 2);
});

test("window.showToast exists, so the watchlist, location and GPS messages are shown", () => {
  assert.match(app, /window\.showToast = _showToast;/);
});

let failed = 0;
for (const [name, fn] of tests) {
  try { fn(); console.log(`ok    ${name}`); } catch (err) { failed += 1; console.log(`FAIL  ${name}`); console.log(err && err.stack ? err.stack : err); }
}
console.log(`\n${tests.length - failed} of ${tests.length} passed`);
process.exitCode = failed ? 1 : 0;
