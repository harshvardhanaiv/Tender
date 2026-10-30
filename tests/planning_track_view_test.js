"use strict";
/* Run: node tests/planning_track_view_test.js
   Re-test 3 PN3: Planning lead detail has "Track lead", and the search (?q=) and the open lead (&lead=) are in the address.
   planning.js keeps everything inside one closure, so this reads the source and checks the pieces are wired together. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const src = fs.readFileSync(path.join(__dirname, "..", "web", "planning.js"), "utf8");
let passed = 0, failed = 0;
function test(name, fn) { try { fn(); passed++; console.log("ok   " + name); } catch (e) { failed++; console.log("FAIL " + name + "\n  " + e.message); } }

test("planning.js is valid JavaScript", () => { new vm.Script(src); });
test("the lead detail has a Track lead button that posts to, and deletes from, the track route", () => {
  assert.ok(src.includes('data-planning-action="track"') && src.includes(">Track lead</button>"));
  assert.ok(src.includes('method: on ? "POST" : "DELETE"') && src.includes("/api/planning/track/"));
  assert.ok(src.includes('if (action === "track") toggleTrack('));
  assert.ok(/Added to your Pipeline/.test(src) && /conditions are discharged/.test(src) && /start on site|work starts on site/i.test(src));
});
test("the track route path keeps the slashes of a PlanIt id and encodes each part", () => {
  const m = src.match(/const trackPath = (\(id\) => `[^`]+`);/);
  assert.ok(m);
  const trackPath = eval(m[1]);
  assert.equal(trackPath("Leeds/26/01234/FU"), "/api/planning/track/Leeds/26/01234/FU");
  assert.equal(trackPath("A B/26 1"), "/api/planning/track/A%20B/26%201");
});
test("the search and the open lead are written to the address and read back when the section opens", () => {
  assert.ok(src.includes('u.set("q", q)') && src.includes('u.set("lead", view.selectedId)'));
  assert.ok(/function syncUrl\(\)/.test(src));
  const calls = (src.match(/\bsyncUrl\(\);/g) || []).length;
  assert.ok(calls >= 3, "search, showDetail and clearDetail all update the address");
  assert.ok(src.includes('fromUrl.get("q")') && src.includes('showDetail(fromUrl.get("lead"))'));
  assert.ok(src.includes('"/planning" + location.search'), "opening the section keeps ?q= and &lead= instead of dropping them");
});
console.log(`\n${passed} of ${passed + failed} passed`);
process.exit(failed ? 1 : 0);
