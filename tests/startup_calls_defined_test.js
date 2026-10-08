"use strict";
/* Run: node tests/startup_calls_defined_test.js
   loadCurrentUser() runs on every page load and wraps everything in an empty catch block, so a call to a
   function that does not exist throws a ReferenceError that nothing shows. Round 28 regression: a commit deleted
   rememberCountyRegionBaseline() but left its call, so on live the profile menu stayed on "Loading...", the
   avatar had no initial and saved/recent searches, theme and language were never applied.
   This reads the real web/*.js and checks that every function the start-up code calls is defined somewhere. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const WEB = path.join(__dirname, "..", "web");
const read = (n) => fs.readFileSync(path.join(WEB, n), "utf8").replace(/\r\n/g, "\n");
const app = read("app.js");
const appLines = app.split("\n");

function cutFunction(name) {
  const start = appLines.findIndex((l) => new RegExp(`^(async )?function ${name}\\(`).test(l));
  assert.ok(start >= 0, `app.js has no top-level function ${name}()`);
  let end = start;
  while (end < appLines.length && !/^\}\s*;?\s*$/.test(appLines[end])) end += 1;
  return appLines.slice(start, end + 1).join("\n");
}

// everything the page can define: functions and variables in every script it loads, plus browser built-ins
const ALL_SOURCE = fs.readdirSync(WEB).filter((f) => f.endsWith(".js")).map(read).join("\n");
const defined = new Set();
for (const m of ALL_SOURCE.matchAll(/(?:^|[^.\w$])(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)/g)) defined.add(m[1]);
for (const m of ALL_SOURCE.matchAll(/(?:^|[^.\w$])(?:const|let|var)\s+([A-Za-z_$][\w$]*)/g)) defined.add(m[1]);
for (const m of ALL_SOURCE.matchAll(/window\.([A-Za-z_$][\w$]*)\s*=/g)) defined.add(m[1]);
const BUILTINS = new Set(("if for while switch catch function return typeof await async new fetch parseInt parseFloat setTimeout clearTimeout " +
  "setInterval clearInterval requestAnimationFrame alert confirm Array Object String Number Boolean Promise Math JSON Date Set Map Error " +
  "Symbol RegExp encodeURIComponent decodeURIComponent isNaN Intl URL URLSearchParams structuredClone queueMicrotask").split(/\s+/));

function undefinedCallsIn(fnName) {
  const body = cutFunction(fnName).replace(/\/\/.*$/gm, "").replace(/\/\*[\s\S]*?\*\//g, "");
  const called = new Set();
  for (const m of body.matchAll(/(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(/g)) called.add(m[1]);
  return [...called].filter((n) => !BUILTINS.has(n) && !defined.has(n)).sort();
}

const tests = [];
const test = (n, f) => tests.push([n, f]);

// Only calls made unconditionally can crash start-up; `typeof x === "function" && x()` guarded ones are checked too, which is stricter but harmless
test("loadCurrentUser() calls only functions that exist", () => {
  const missing = undefinedCallsIn("loadCurrentUser");
  assert.deepEqual(missing, [], `loadCurrentUser() calls functions that are not defined anywhere: ${missing.join(", ")}`);
});

test("the checker really catches a missing function", () => {
  const saved = defined.has("zzNotAFunction");
  assert.equal(saved, false);
  const body = "async function demo() { zzNotAFunction(); loadPortalPrefs(); }";
  const called = [...body.matchAll(/(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(/g)].map((m) => m[1]).filter((n) => n !== "demo" && !BUILTINS.has(n) && !defined.has(n));
  assert.deepEqual(called, ["zzNotAFunction"]);
});

test("the removed function is not called anywhere", () => {
  assert.equal(/rememberCountyRegionBaseline\s*\(/.test(ALL_SOURCE) && !defined.has("rememberCountyRegionBaseline"), false,
    "rememberCountyRegionBaseline() is called but no longer defined");
});

(async () => {
  let failed = 0;
  for (const [n, f] of tests) {
    try { await f(); console.log(`ok    ${n}`); } catch (e) { failed += 1; console.log(`FAIL  ${n}`); console.log(e.stack || e); }
  }
  console.log(`\n${tests.length - failed} of ${tests.length} passed`);
  process.exitCode = failed ? 1 : 0;
})();
