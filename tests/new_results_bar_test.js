"use strict";
/* Run: node tests/new_results_bar_test.js
   Re-test 4 T3: a list painted from saved results must not move while the portals are searched again. Rows the portals add are held
   behind a "N new results: show" link (the page's own wording is checked in the source; the splitting rule is run here). */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const src = fs.readFileSync(path.join(__dirname, "..", "web", "app.js"), "utf8").replace(/\r\n/g, "\n");
const tests = [];
const test = (n, f) => tests.push([n, f]);

test("fresh rows are held back only for a list painted from saved results, and released by the link", () => {
  assert.ok(src.includes("if (state.instantFrom && state.rows.length && !state._releaseNew"), "the hold must apply to a saved-results list only");
  assert.ok(src.includes('data-show-new="1"') && src.includes("state._releaseNew = true"));
  assert.ok(src.includes("state._pendingNew = [];\n    state._releaseNew = false;"), "a new search must reset the held rows");
});

test("the split keeps the order on screen and moves unseen rows into the pending list", () => {
  const rowKey = (r) => r.k;
  const onScreenRows = [{ k: "a" }, { k: "b" }, { k: "c" }];
  const incoming = [{ k: "c" }, { k: "x" }, { k: "a" }, { k: "y" }];   // the portals answer in another order and add two
  const onScreen = new Map();
  onScreenRows.forEach((r, i) => { const k = rowKey(r); if (k && !onScreen.has(k)) onScreen.set(k, i); });
  const kept = [], fresh = [];
  for (const r of incoming) { const k = rowKey(r); (k && onScreen.has(k) ? kept : fresh).push(r); }
  kept.sort((a, b) => onScreen.get(rowKey(a)) - onScreen.get(rowKey(b)));
  assert.deepEqual(kept.map(rowKey), ["a", "c"]);
  assert.deepEqual(fresh.map(rowKey), ["x", "y"]);
});

test("the header says how many are waiting", () => {
  assert.ok(/new result\$\{pendingNew === 1 \? "" : "s"\} — show/.test(src));
});

(() => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { fn(); console.log(`ok    ${name}`); } catch (err) { failed += 1; console.log(`FAIL  ${name}`); console.log(err && err.stack ? err.stack : err); }
  }
  console.log(`\n${tests.length - failed} of ${tests.length} passed`);
  process.exitCode = failed ? 1 : 0;
})();
