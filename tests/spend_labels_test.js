"use strict";
/* Run: node tests/spend_labels_test.js
   Round 28 item 2.4: one figure was called "Total Spend" in Buyer Intelligence while Market Radar called the same kind of number a
   published value, and a QA reader took GBP 1bn for one borough to include framework ceilings. The label now names its basis
   (direct contracts, a shared notice counted once, ceilings excluded) wherever the figure is shown. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const read = (n) => fs.readFileSync(path.join(__dirname, "..", "web", n), "utf8");
const app = read("app.js");
const index = read("index.html");

const tests = [];
const test = (n, f) => tests.push([n, f]);

test("the buyer card and the buyer drawer say 'Direct contract value', with the basis in a tooltip", () => {
  assert.ok(/Direct contract value<\/span>/.test(app), "buyer card label");
  assert.ok(/>Direct contract value<\/span>/.test(index), "buyer drawer label");
  for (const src of [app, index]) {
    assert.ok(/direct \(non-framework\) contracts/.test(src), "the tooltip names what is counted");
    assert.ok(/counted once/.test(src), "the tooltip says a shared notice counts once");
    assert.ok(/Framework and call-off ceilings are not included/.test(src), "the tooltip says ceilings are excluded");
  }
});

test("the old unlabelled 'Total Spend' wording is gone from the buyer views", () => {
  assert.ok(!/>Total Spend<\/span>/.test(app), "buyer card still says Total Spend");
  assert.ok(!/uppercase;">Total Spend<\/span>/.test(index), "buyer drawer still says Total Spend");
  assert.ok(!/Total Spend \(£\)/.test(index), "supplier table header still says Total Spend");
});

test("the supplier list says what its 'won (value)' figure is", () => {
  assert.ok(/Wins and published value of direct contracts/.test(app));
});

(async () => {
  let failed = 0;
  for (const [n, f] of tests) {
    try { await f(); console.log(`ok    ${n}`); } catch (e) { failed += 1; console.log(`FAIL  ${n}`); console.log(e.stack || e); }
  }
  console.log(`\n${tests.length - failed} of ${tests.length} passed`);
  process.exitCode = failed ? 1 : 0;
})();
