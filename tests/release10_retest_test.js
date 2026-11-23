"use strict";
/* Run: node tests/release10_retest_test.js
   Fixes for the 23 Nov re-test that live in web/: one pipeline row for repeated deadline changes (item 13), the AIV dashboard
   menu entry only for an admin (B2), the buyer's limit in the answer plan (item 6), the past-bid advice (item 4). */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const app = fs.readFileSync(path.join(__dirname, "..", "web", "app.js"), "utf8").replace(/\r\n/g, "\n");
const index = fs.readFileSync(path.join(__dirname, "..", "web", "index.html"), "utf8").replace(/\r\n/g, "\n");
function cut(name) {
  const at = app.indexOf("function " + name + "(");
  assert.ok(at >= 0, name + " is not in app.js");
  const i = app.indexOf("{", app.indexOf(")", at));
  let depth = 0;
  for (let j = i; j < app.length; j++) {
    if (app[j] === "{") depth++;
    else if (app[j] === "}" && --depth === 0) return app.slice(at, j + 1);
  }
  throw new Error(name);
}
const ctx = vm.createContext({});
vm.runInContext(["function deepAlertKey(key) { const k = String(key || \"\"); return k.includes(\":\") ? k : `etenders_ie:${k}`; }",
  cut("groupDeepAlerts"), "this.g = groupDeepAlerts;"].join("\n"), ctx);

const tests = [];
const test = (n, f) => tests.push([n, f]);

test("repeated deadline changes on one tender are one row with the latest message and a count", () => {
  const list = [
    { id: 1, tender_key: "pcs:A1", kind: "deadline_changed", message: "was 16/11, now 18/11." },
    { id: 2, tender_key: "pcs:A1", kind: "deadline_changed", message: "was 18/11, now 20/11." },
    { id: 3, tender_key: "pcs:A1", kind: "deadline_changed", message: "was 20/11, now 25/11." },
    { id: 4, tender_key: "pcs:B2", kind: "new_form", message: "A new response form was added: x" },
    { id: 5, tender_key: "pcs:A1", kind: "new_form", message: "A new response form was added: y" },
  ];
  const g = ctx.g(list);
  assert.equal(g.length, 3, "A1 deadline, B2 form, A1 form");
  const a1 = g.find(x => x.latest.kind === "deadline_changed");
  assert.equal(a1.all.length, 3);
  assert.equal(a1.latest.message, "was 20/11, now 25/11.", "the latest change is the one shown");
  assert.deepEqual(Array.from(a1.ids), [1, 2, 3], "dismissing the row dismisses every change");
  assert.equal(g[0].latest.id, 5, "newest group first");
});

test("the banner counts rows, shows 'Changed N times' and dismisses a whole group", () => {
  assert.ok(app.includes("Must not miss: ${groups.length} alert"), "the header counts groups, not every change");
  assert.ok(app.includes("Changed ${g.all.length} times: see every change"));
  assert.ok(app.includes('data-deep-alert-dismiss="${g.ids.join(",")}"') && app.includes("for (const id of ids) await fetch(\"/api/deep-alerts/dismiss\""));
});

test("the AIV dashboard menu entry starts hidden and only an admin gets it back", () => {
  assert.ok(/<button id="btnAivDashboard" class="tf-nav hidden"/.test(index));
  assert.ok(app.includes('document.getElementById("btnAivDashboard")?.classList.toggle("hidden", !state.isAdmin)'));
});

test("the answer plan shows the buyer's limit beside the target, and the learned panel shows the past-bid advice", () => {
  assert.ok(app.includes("limit ${esc(String(r.limit))}"));
  assert.ok(app.includes("${d.advice ?") && app.includes('class="bid-pv-warn"'));
});

let failed = 0;
for (const [n, f] of tests) { try { f(); console.log("ok   " + n); } catch (e) { failed++; console.log("FAIL " + n + "\n" + (e.stack || e)); } }
console.log(`\n${tests.length - failed} of ${tests.length} passed`);
process.exit(failed ? 1 : 0);
