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
  cut("function parseDate(", /^}/), cut("function isDeadlinePassed(", /^}/),
  cut("function isClosedTender(", /^}/), cut("function statusLabel(", /^}/),
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

(() => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { fn(); console.log(`ok    ${name}`); } catch (err) { failed += 1; console.log(`FAIL  ${name}`); console.log(err && err.stack ? err.stack : err); }
  }
  console.log(`\n${tests.length - failed} of ${tests.length} passed`);
  process.exitCode = failed ? 1 : 0;
})();
