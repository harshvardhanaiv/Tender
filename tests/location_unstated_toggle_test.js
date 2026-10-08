"use strict";
/* Run: node tests/location_unstated_toggle_test.js
   The "Hide notices with location not stated" checkbox must really hide those rows and remember its state.
   Before the fix nothing ever set state.hideLocationNotStated, so ticking the box did nothing, and
   isLocationUnstated was private to uk_counties.js so rows were never recognised when every county was selected.
   Real code is cut out of web/app.js and web/uk_counties.js; nothing is copied. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const WEB = path.join(__dirname, "..", "web");
const read = (n) => fs.readFileSync(path.join(WEB, n), "utf8").replace(/\r\n/g, "\n");
const lines = read("app.js").split("\n");
function cutFunction(name) {
  const start = lines.findIndex((l) => new RegExp(`^function ${name}\\(`).test(l));
  assert.ok(start >= 0, `app.js has no function ${name}()`);
  let end = start;
  while (!/^\}\s*;?\s*$/.test(lines[end])) end += 1;
  return lines.slice(start, end + 1).join("\n");
}
const tests = [];
const test = (n, f) => tests.push([n, f]);

const ctx = { window: {}, console };
vm.createContext(ctx);
vm.runInContext(read("uk_counties.js"), ctx);
const rowsOf = () => [
  { source: "find_tender", title: "A", location: "Kent" },
  { source: "find_tender", title: "B", location: "" },
  { source: "find_tender", title: "C", location: "UK" },
];

function page() {
  const handlers = {};
  const box = { checked: false, addEventListener: (ev, fn) => { handlers[ev] = fn; } };
  const saved = [];
  const calls = { render: 0 };
  const sandbox = {
    state: { hideLocationNotStated: false },
    $: (id) => (id === "chkHideUnstatedLocation" ? box : null),
    lsSet: (k, v) => saved.push([k, v]),
    LS_HIDE_UNSTATED_LOCATION: "tf_hide_unstated_location",
    renderRows: () => { calls.render += 1; },
    isLocationUnstated: ctx.window.isLocationUnstated,
    typeof: undefined,
  };
  return { handlers, box, saved, calls, sandbox };
}

test("uk_counties.js exposes isLocationUnstated", () => {
  assert.equal(typeof ctx.window.isLocationUnstated, "function");
  assert.equal(ctx.window.isLocationUnstated({ location: "UK" }), true);
  assert.equal(ctx.window.isLocationUnstated({ location: "Kent" }), false);
});

test("ticking the box sets the state, saves it and re-renders; unticking restores", () => {
  const p = page();
  const code = cutFunction("setupCountyFilterUI").replace(/\n  if \(searchInput\)[\s\S]*$/, "\n}");
  vm.createContext(p.sandbox);
  vm.runInContext(code + "\nsetupCountyFilterUI();", p.sandbox);
  p.box.checked = true; p.handlers.change();
  assert.equal(p.sandbox.state.hideLocationNotStated, true);
  assert.deepEqual(p.saved.at(-1), ["tf_hide_unstated_location", true]);
  assert.equal(p.calls.render, 1);
  p.box.checked = false; p.handlers.change();
  assert.equal(p.sandbox.state.hideLocationNotStated, false);
});

test("filteredAndSortedRows drops unstated rows only while the toggle is on, even with every county selected", () => {
  const src = cutFunction("filteredAndSortedRows");
  const m = src.match(/  \/\/ Filter out location not stated[\s\S]*?\n  \}\n/);
  assert.ok(m, "the hide-unstated block is missing from filteredAndSortedRows");
  const run = (on) => {
    const sb = { state: { hideLocationNotStated: on }, isLocationUnstated: ctx.window.isLocationUnstated, rows: rowsOf() };
    vm.createContext(sb);
    vm.runInContext("let rows = rows0;".replace("rows0", "this.rows") + m[0] + "; this.out = rows.map(r => r.title);", sb);
    return sb.out;
  };
  assert.deepEqual(run(true), ["A"]);
  assert.deepEqual(run(false), ["A", "B", "C"]);
});

test("the saved value is read on load", () => {
  assert.match(read("app.js"), /state\.hideLocationNotStated = lsGet\(LS_HIDE_UNSTATED_LOCATION, false\) === true;/);
});

(async () => {
  let failed = 0;
  for (const [n, f] of tests) {
    try { await f(); console.log(`ok    ${n}`); } catch (e) { failed += 1; console.log(`FAIL  ${n}`); console.log(e.stack || e); }
  }
  console.log(`\n${tests.length - failed} of ${tests.length} passed`);
  process.exitCode = failed ? 1 : 0;
})();
