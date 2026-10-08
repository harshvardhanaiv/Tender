"use strict";
/* Run: node tests/format_value_test.js
   Round 28 item 2.6: Tender Search showed a contract value three different ways: ProContract "£6,699,997.00" (pence), Contracts Finder "100,000"
   (no pound sign) and Find a Tender "£500,000". This cuts the real formatValue() out of web/app.js and checks one format for every portal. */
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
const CODE = [cut("const CURRENCY_BY_SOURCE = {", /^};/), cut("function formatValue(", /^}/)].join("\n");
const ctx = vm.createContext({});
vm.runInContext(CODE + "\nthis.formatValue = formatValue;", ctx);
const fmt = (v, src) => ctx.formatValue(v, src);

const tests = [];
const test = (n, f) => tests.push([n, f]);

test("every portal shows the same format: symbol, separators, no pence", () => {
  assert.equal(fmt("£6,699,997.00", "procontract"), "£6,699,997");
  assert.equal(fmt("100,000", "contracts_finder"), "£100,000");
  assert.equal(fmt("£500,000", "find_tender"), "£500,000");
  assert.equal(fmt("500000", "pcs"), "£500,000");
  assert.equal(fmt(1234567.89, "sell2wales"), "£1,234,568");
});

test("a euro portal keeps the euro sign, and a value already in euros stays euros", () => {
  assert.equal(fmt("171200", "etenders_ie"), "€171,200");
  assert.equal(fmt("€500,000", "etenders_ie"), "€500,000");
  assert.equal(fmt("€0", "etenders_ie"), "€0");
});

test("text with words in it is left exactly as published", () => {
  assert.equal(fmt("TBC", "contracts_finder"), "TBC");
  assert.equal(fmt("£1m - £2m", "find_tender"), "£1m - £2m");
  assert.equal(fmt("Not available", "find_tender"), "Not available");
});

test("an unknown portal gets separators but no invented currency", () => {
  assert.equal(fmt("100000", "some_new_portal"), "100,000");
});

(async () => {
  let failed = 0;
  for (const [n, f] of tests) {
    try { await f(); console.log(`ok    ${n}`); } catch (e) { failed += 1; console.log(`FAIL  ${n}`); console.log(e.stack || e); }
  }
  console.log(`\n${tests.length - failed} of ${tests.length} passed`);
  process.exitCode = failed ? 1 : 0;
})();
