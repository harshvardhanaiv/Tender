"use strict";
/* Run: node tests/parse_date_test.js
   D05: parseDate() used to build `new Date(y, m - 1, d)` straight from the digits, and JavaScript rolls impossible
   dates over instead of rejecting them: "03/25/2026" became January 2028, "31/04/2026" became 1 May, "05.03.2026" and
   "05/03/26" fell through to the US-order parser (3 May), and "5th March 2026" was not a date at all. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const lines = fs.readFileSync(path.join(__dirname, "..", "web", "app.js"), "utf8").replace(/\r\n/g, "\n").split("\n");
function cut(first) {
  const start = lines.findIndex((l) => l.startsWith(first));
  assert.ok(start >= 0, `app.js has no line starting ${first}`);
  let end = start;
  while (!/^}/.test(lines[end])) end += 1;
  return lines.slice(start, end + 1).join("\n");
}
const ctx = vm.createContext({});
vm.runInContext([cut("function _calendarDate("), cut("function parseDate("), "this.parseDate = parseDate;"].join("\n"), ctx);
const ymd = (s) => {
  const d = ctx.parseDate(s);
  return d ? `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}` : null;
};

const tests = [];
const test = (n, f) => tests.push([n, f]);

test("ISO dates, with or without a time part", () => {
  assert.equal(ymd("2026-03-05"), "2026-03-05");
  assert.equal(ymd("2026-03-05T23:30:00Z"), "2026-03-05");
  assert.equal(ymd("2026-03-05 23:30:00"), "2026-03-05");
});

test("DD/MM/YYYY is read day first, as the portals publish it", () => {
  assert.equal(ymd("05/03/2026"), "2026-03-05");
  assert.equal(ymd("5/3/2026"), "2026-03-05");
  assert.equal(ymd("25/03/2026"), "2026-03-25");
  assert.equal(ymd("05-03-2026"), "2026-03-05");
  assert.equal(ymd("05.03.2026"), "2026-03-05");
});

test("MM/DD/YYYY is accepted only when it cannot be day first (the month would be above 12)", () => {
  assert.equal(ymd("03/25/2026"), "2026-03-25");
  assert.equal(ymd("12/25/2026"), "2026-12-25");
});

test("two-digit years are 20YY", () => {
  assert.equal(ymd("05/03/26"), "2026-03-05");
  assert.equal(ymd("5.3.26"), "2026-03-05");
});

test("worded dates", () => {
  assert.equal(ymd("5 Mar 2026"), "2026-03-05");
  assert.equal(ymd("5 March 2026"), "2026-03-05");
  assert.equal(ymd("5th March 2026"), "2026-03-05");
  assert.equal(ymd("March 5, 2026"), "2026-03-05");
});

test("other numeric layouts", () => {
  assert.equal(ymd("2026/03/05"), "2026-03-05");
  assert.equal(ymd("20260305"), "2026-03-05");
});

test("a day that does not exist is not a date (no roll-over into the next month)", () => {
  assert.equal(ymd("31/04/2026"), null);
  assert.equal(ymd("29/02/2026"), null);
  assert.equal(ymd("29/02/2028"), "2028-02-29");
  assert.equal(ymd("2026-02-30"), null);
  assert.equal(ymd("13/13/2026"), null);
});

test("empty and placeholder values stay null", () => {
  for (const v of [null, undefined, "", "  ", "—", "TBC", "n/a", "today"]) assert.equal(ymd(v), null, String(v));
});

(() => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { fn(); console.log(`ok    ${name}`); } catch (err) { failed += 1; console.log(`FAIL  ${name}`); console.log(err && err.stack ? err.stack : err); }
  }
  console.log(`\n${tests.length - failed} of ${tests.length} passed`);
  process.exitCode = failed ? 1 : 0;
})();
