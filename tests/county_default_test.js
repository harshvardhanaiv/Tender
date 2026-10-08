"use strict";
/* Run: node tests/county_default_test.js
   Live and local checks after Round 28: every account had all 140 counties ticked (the saved default), and the county filter still hid notices
   ("11 results hidden by your county filter") although no filter was visible anywhere. The county filter now applies only when the reader has
   narrowed the selection; all counties ticked means no county filter, on the page and in the request sent to the server. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const appSrc = fs.readFileSync(path.join(__dirname, "..", "web", "app.js"), "utf8").replace(/\r\n/g, "\n");
const lines = appSrc.split("\n");
function cut(first, lastPattern) {
  const start = lines.findIndex((l) => l.startsWith(first));
  assert.ok(start >= 0, `app.js has no line starting ${first}`);
  let end = start;
  while (!lastPattern.test(lines[end])) end += 1;
  return lines.slice(start, end + 1).join("\n");
}

const countyCtx = { window: {}, console };
vm.createContext(countyCtx);
vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "web", "uk_counties.js"), "utf8"), countyCtx);
const { UK_COUNTIES } = countyCtx.window;

function pageWith(selected) {
  const ctx = vm.createContext({ state: { selectedUkCounties: selected }, UK_COUNTIES });
  vm.runInContext(cut("function activeCountySelection(", /^}/) + "\nthis.activeCountySelection = activeCountySelection;", ctx);
  return ctx;
}
const names = UK_COUNTIES.map((c) => c.name);

const tests = [];
const test = (n, f) => tests.push([n, f]);

test("every county ticked (the default) is not a county filter", () => {
  assert.equal(names.length, 140);
  assert.equal(pageWith(names).activeCountySelection().length, 0);
  assert.equal(pageWith([...names].reverse()).activeCountySelection().length, 0, "order does not matter");
});

test("one county short, or a handful, still filters, with exactly the chosen counties", () => {
  assert.equal(pageWith(names.slice(1)).activeCountySelection().length, 139);
  assert.deepEqual(Array.from(pageWith(["Kent", "Essex"]).activeCountySelection()), ["Kent", "Essex"]);
});

test("nothing selected, or no selection at all, is no filter", () => {
  assert.equal(pageWith([]).activeCountySelection().length, 0);
  assert.equal(pageWith(undefined).activeCountySelection().length, 0);
  assert.equal(pageWith(null).activeCountySelection().length, 0);
});

test("the request, the page filter, the filter badge and both notices all use it", () => {
  assert.ok(appSrc.includes("const searchCounties = activeCountySelection();"), "the search request must send only an active county selection");
  assert.ok(appSrc.includes("fullySelectedNations(searchCounties)"));
  assert.ok(appSrc.includes("rows.filter((row) => matchRowToCounties(row, activeCountySelection()))"), "the page filter must use it");
  assert.ok(appSrc.includes("activeCountySelection().length > 0\n  );"), "hasActiveFilters must use it");
  assert.ok(appSrc.includes("!(activeCountySelection().length > 0)"), "the county note must only show for an active county filter");
  assert.ok(appSrc.includes("state.meta?.county_filter, activeCountySelection().length,"), "the portal summary must pass the active count");
  assert.ok(!/state\.selectedUkCounties\.join\(','\)/.test(appSrc), "the request must not send the raw saved selection");
});

(() => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { fn(); console.log(`ok    ${name}`); } catch (err) { failed += 1; console.log(`FAIL  ${name}`); console.log(err && err.stack ? err.stack : err); }
  }
  console.log(`\n${tests.length - failed} of ${tests.length} passed`);
  process.exitCode = failed ? 1 : 0;
})();
