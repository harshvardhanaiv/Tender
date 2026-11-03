"use strict";
/* Run: node tests/retest5_small_test.js
   Re-test 5: the Planning page no longer promises an applicant for every lead; /api/health is a liveness check, not a list of portals. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const read = (...p) => fs.readFileSync(path.join(__dirname, "..", ...p), "utf8");
let passed = 0, failed = 0;
function test(name, fn) { try { fn(); passed++; console.log("ok   " + name); } catch (e) { failed++; console.log("FAIL " + name + "\n  " + e.message); } }

test("P2: the Planning subtitle says the applicant is named only where the council publishes one", () => {
  const html = read("web", "index.html");
  assert.ok(!html.includes("who is behind the scheme"));
  assert.ok(html.includes("names the applicant or agent where the council publishes one"));
});
test("/api/health no longer lists portal ids and the page does not read them", () => {
  const server = read("server.py");
  const i = server.indexOf("def api_health()");
  assert.ok(i > 0 && !server.slice(i, i + 700).includes("list(SOURCES.keys())"));
  assert.ok(server.slice(i, i + 700).includes('"sourceCount"'));
  assert.ok(!/data\.sources/.test(read("web", "app.js").slice(read("web", "app.js").indexOf("async function checkServerHealth()"), read("web", "app.js").indexOf("checkServerHealth();"))));
});
test("the Pipeline list panel and the alerts banner start collapsed on every visit", () => {
  const app = read("web", "app.js");
  assert.ok(app.includes("const _pipelineAccOpen = { all: false };"));
  assert.ok(app.includes('<details class="deep-alert-banner" id="deepAlertBanner"${_deepAlertsOpen ? " open" : ""}>'));
  assert.ok(app.includes("let _deepAlertsOpen = false;"));
});
console.log(`\n${passed} of ${passed + failed} passed`);
process.exit(failed ? 1 : 0);
