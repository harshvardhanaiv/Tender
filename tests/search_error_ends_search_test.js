"use strict";
/* Run: node tests/search_error_ends_search_test.js
   A search that fails must not leave the "Searching: 'a'" badge (and its spinner / Stop button) on screen for ever. loadPage() sets the phase to
   "loading" before it asks the server; an error status, a lost connection or an unreadable reply used to return with the phase still "loading".
   Cuts the real loadPage / _endSearchWithError / updateActiveSearchKeywordBadge out of web/app.js and runs them against stubs. */
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

function makeEnv(fetchImpl) {
  const classes = new Set(["hidden"]);
  const badge = {
    classList: { add: (c) => classes.add(c), remove: (c) => classes.delete(c), contains: (c) => classes.has(c) },
    style: {}, setAttribute() {}, querySelector: () => null,
  };
  const status = [];
  const ctx = vm.createContext({
    console, setTimeout, clearTimeout, URLSearchParams, encodeURIComponent,
    state: { q: "a", rows: [], scope: "find_tender", searchPhase: "idle", pollTimer: null, jobId: null, lang: "en", keywordScope: "all", meta: null },
    document: { body: { classList: { add() {}, remove() {} } } },
    $: (id) => (id === "activeSearchKeywordBadge" ? badge : null),
    t: (k) => (k === "searching_keyword" ? "Searching:" : "Keyword:"),
    setStatus: (m) => status.push(m),
    renderSkeletonRows() {}, renderPortalChips() {}, renderPortalsInCountries() {}, renderPortalSummary() {},
    updateStopButton() {}, updateSearchProgressUI() {}, getScopeValue: () => "find_tender",
    parseKeywordQuery: (q) => ({ query: q, scope: "all" }), translateKeyword: (q) => q,
    isUkCountryActive: () => true, activeCountySelection: () => [], fullySelectedNations: () => [],
    refreshCredits() {}, _resetCollapsedMatchGroups() {}, normalizeRow: (r) => r,
    fetch: fetchImpl,
  });
  vm.runInContext([
    cut("function stopSearchPoll(", /^}/),
    cut("function updateActiveSearchKeywordBadge(", /^}/),
    cut("function _endSearchWithError(", /^}/),
    "let _searchGeneration = 0; let _lastSettledRowCount = 0; let _searchStartedAt = 0; const MAJORITY_GUARD_TIMEOUT_MS = 5000;",
    cut("async function loadPage(", /^}/),
    "this.loadPage = loadPage; this.updateActiveSearchKeywordBadge = updateActiveSearchKeywordBadge;",
  ].join("\n"), ctx);
  return { ctx, classes, status };
}

const tick = () => new Promise((r) => setImmediate(r));
const tests = [];
const test = (n, f) => tests.push([n, f]);

test("before the answer arrives the badge says Searching", async () => {
  const pending = [];
  const env = makeEnv(() => new Promise((r) => { pending.push(r); }));
  const p = env.ctx.loadPage(true);
  await tick();
  assert.equal(env.ctx.state.searchPhase, "loading");
  assert.ok(env.classes.has("is-searching"), "the badge is not showing Searching while the request is out");
  pending[0]({ ok: false, status: 500, text: async () => "boom" });
  await p;
});

test("an error status ends the search: phase stopped, badge not searching, the error is shown", async () => {
  const env = makeEnv(async () => ({ ok: false, status: 500, text: async () => "Internal Server Error" }));
  await env.ctx.loadPage(true);
  assert.equal(env.ctx.state.searchPhase, "stopped");
  assert.ok(!env.classes.has("is-searching"), "the badge still says Searching after a failed search");
  assert.ok(env.status.some((m) => /Internal Server Error/.test(m)), env.status.join(" | "));
});

test("a lost connection on a new search ends it the same way, and does not throw", async () => {
  const env = makeEnv(async () => { throw new TypeError("Failed to fetch"); });
  await env.ctx.loadPage(true);
  assert.equal(env.ctx.state.searchPhase, "stopped");
  assert.ok(!env.classes.has("is-searching"));
  assert.ok(env.status.some((m) => /could not reach the server/i.test(m)), env.status.join(" | "));
});

test("an unreadable reply ends the search", async () => {
  const env = makeEnv(async () => ({ ok: true, status: 200, json: async () => { throw new SyntaxError("Unexpected token <"); }, text: async () => "<html>" }));
  await env.ctx.loadPage(true);
  assert.equal(env.ctx.state.searchPhase, "stopped");
  assert.ok(!env.classes.has("is-searching"));
});

test("a poll that fails throws, so pollSearchProgress can retry and then stop", async () => {
  const bad = makeEnv(async () => ({ ok: false, status: 502, text: async () => "Bad Gateway" }));
  bad.ctx.state.jobId = "job-1";
  await assert.rejects(() => bad.ctx.loadPage(false, true), /HTTP 502/);
  const gone = makeEnv(async () => { throw new TypeError("Failed to fetch"); });
  gone.ctx.state.jobId = "job-1";
  await assert.rejects(() => gone.ctx.loadPage(false, true), /Failed to fetch/);
});

test("a search that is superseded while in flight is left to the newer one", async () => {
  const pending = [];
  const env = makeEnv(() => new Promise((r) => { pending.push(r); }));
  const first = env.ctx.loadPage(true);
  await tick();
  const second = env.ctx.loadPage(true); // a newer search starts while the first request is out
  await tick();
  assert.equal(pending.length, 2);
  pending[0]({ ok: false, status: 500, text: async () => "stale" });
  await first;
  assert.equal(env.ctx.state.searchPhase, "loading", "the old search's failure must not end the newer one");
  assert.ok(!env.status.some((m) => /stale/.test(m)));
  pending[1]({ ok: false, status: 500, text: async () => "real failure" });
  await second;
  assert.equal(env.ctx.state.searchPhase, "stopped");
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { await fn(); console.log(`ok    ${name}`); } catch (err) { failed += 1; console.log(`FAIL  ${name}`); console.log(err && err.stack ? err.stack : err); }
  }
  console.log(`\n${tests.length - failed} of ${tests.length} passed`);
  process.exitCode = failed ? 1 : 0;
})();
