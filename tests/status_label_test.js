"use strict";
/* Run: node tests/status_label_test.js
   Live check after Round 28: a Contracts Finder notice with a closing date of 16/04/2026 showed Status "Active" beside a "Closed" badge, because the
   Status column printed the portal's own label. It must read "Closed" once the deadline has passed. Cuts the real functions out of web/app.js. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const lines = fs.readFileSync(path.join(__dirname, "..", "web", "app.js"), "utf8").replace(/\r\n/g, "\n").split("\n");
function cut(first, lastPattern) {
  const start = lines.findIndex((l) => l.startsWith(first));
  assert.ok(start >= 0, `app.js has no line starting ${first}`);
  let end = start;
  while (!lastPattern.test(lines[end])) end += 1;
  return lines.slice(start, end + 1).join("\n");
}
const CODE = [
  cut("function _calendarDate(", /^}/), cut("function parseDate(", /^}/), cut("function isDeadlinePassed(", /^}/),
  cut("function isAwardNotice(", /^}/), cut("function isClosedTender(", /^}/), cut("function statusLabel(", /^}/),
  'this.statusLabel = statusLabel;',
].join("\n");
const ctx = vm.createContext({});
vm.runInContext(CODE, ctx);
const label = (row) => ctx.statusLabel(row);

const tests = [];
const test = (n, f) => tests.push([n, f]);

test("an Active notice whose deadline has passed reads Closed", () => {
  assert.equal(label({ status: "Active", submission_deadline: "16/04/2026" }), "Closed");
  assert.equal(label({ status: "active", submission_deadline: "2026-04-16" }), "Closed");
  assert.equal(label({ status: "Open", days_until_deadline: -3 }), "Closed");
});

test("an Active notice with a future deadline, or none, stays Active", () => {
  assert.equal(label({ status: "Active", submission_deadline: "31/12/2099" }), "Active");
  assert.equal(label({ status: "Active" }), "Active");
  assert.equal(label({ status: "Active", submission_deadline: "TBC" }), "Active");
});

test("other statuses are shown exactly as published", () => {
  assert.equal(label({ status: "Awarded", submission_deadline: "16/04/2026" }), "Awarded");
  assert.equal(label({ status: "Cancelled", submission_deadline: "16/04/2026" }), "Cancelled");
  assert.equal(label({ status: "" }), "");
  assert.equal(label({}), "");
});

test("both renderers use it", () => {
  const src = lines.join("\n");
  assert.ok(src.includes('tdText(statusLabel(row), "col-status td--status")'), "table row prints the raw status again");
  assert.ok(src.includes("${esc(statusLabel(row))}"), "card prints the raw status again");
});

test("supplier and buyer labels on both renderers go through the name formatter", () => {
  const src = lines.join("\n");
  assert.equal((src.match(/Awarded to \$\{esc\(displayName\((sup|cardSup)\)\)\}/g) || []).length, 3, "an 'Awarded to' label shows the raw capitals again");
  assert.ok(src.includes("${esc(displayName(authority))}</button>"), "the card's buyer button shows the raw name again");
});

test("report 6 T1: award notices and no-deadline notices are never open; a future deadline is open", () => {
  const closed = (row) => ctx.isClosedTender(row);
  const pcsAward = { source: "pcs", status: null, notice_type: "03 Contract Award Notice - Successful Supplier(s)", date_published: "29/10/2026" };
  const quickQuote = { source: "pcs", status: null, notice_type: "Quick Quote Award", title: "Award of Intelligent Email Security" };
  assert.equal(closed(pcsAward), true);
  assert.equal(closed(quickQuote), true);
  assert.equal(closed({ source: "pcs", status: null, title: "Award of Solar PV and Battery", date_published: "29/10/2026" }), true);
  assert.equal(closed({ source: "pcs", status: null, date_published: "29/10/2026" }), true, "no status and no deadline is nothing to bid on");
  assert.equal(closed({ source: "find_tender", status: "Active", submission_deadline: "31/12/2099", notice_type: "UK4: Tender Notice" }), false);
  assert.equal(closed({ source: "gca_agreements", status: "Live", deadline_date: "2099-03-06" }), false);
  assert.equal(closed({ source: "x", title: "Awards management software", status: "Active", submission_deadline: "31/12/2099" }), false, "'Awards' in a title is not an award notice");
});

test("report 6 T2: opening a tender does not run the paid deep score", () => {
  const src = fs.readFileSync(path.join(__dirname, "..", "web", "app.js"), "utf8");
  assert.ok(!/renderRows\(\);\s*loadDeepFit\(/.test(src), "the detail view starts the paid score on open again");
  assert.ok(!/if \(fitActive\(\)\) loadDeepFit\(window\._detailRow, null\)/.test(src), "a profile switch starts the paid score again");
  assert.ok(src.includes("data-run-deep-fit"), "the 'Run AI deep score · 1 credit' button is gone");
});

(() => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { fn(); console.log(`ok    ${name}`); } catch (err) { failed += 1; console.log(`FAIL  ${name}`); console.log(err && err.stack ? err.stack : err); }
  }
  console.log(`\n${tests.length - failed} of ${tests.length} passed`);
  process.exitCode = failed ? 1 : 0;
})();
