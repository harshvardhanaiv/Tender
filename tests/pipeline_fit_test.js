"use strict";
/* Run: node tests/pipeline_fit_test.js
   Pipeline vs search: a tracked tender showed the score frozen when it was tracked (87) while the search list showed
   the cached deep / quick score (15), and the "High fit" tile counted 12 while the list under it showed 5.
   pipelineItemFit() reads the same cache the search list reads, the stored value is only the fallback, and the
   high-fit section says how many of the counted tenders it shows (with a way to show them all). */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const appSrc = fs.readFileSync(path.join(__dirname, "..", "web", "app.js"), "utf8").replace(/\r\n/g, "\n");
const lines = appSrc.split("\n");
function cut(first) {
  const start = lines.findIndex((l) => l.startsWith(first));
  assert.ok(start >= 0, `app.js has no line starting ${first}`);
  let end = start;
  while (!/^}/.test(lines[end])) end += 1;
  return lines.slice(start, end + 1).join("\n");
}
function page(fitCache) {
  const ctx = vm.createContext({ state: { fitCache } });
  vm.runInContext(cut("function fitBandFor(") + "\n" + cut("function pipelineItemFit(") + "\nthis.pipelineItemFit = pipelineItemFit;", ctx);
  return ctx;
}
const tests = [];
const test = (n, f) => tests.push([n, f]);

test("the cached deep score wins over the figure stored when the tender was tracked", () => {
  const p = page({ "k1": { deep: { score: 15, band: "weak" }, quick: { score: 40 } } });
  assert.deepEqual({ ...p.pipelineItemFit({ tender_key: "k1", fit_score: 87, fit_band: "strong" }) }, { score: 15, band: "weak" });
});

test("without a deep score the quick score the search list shows is used", () => {
  const p = page({ "k1": { quick: { score: 55, band: "possible" } } });
  assert.equal(p.pipelineItemFit({ tender_key: "k1", fit_score: 87 }).score, 55);
});

test("a tender that was never scored in this browser falls back to the stored score, with a band", () => {
  const p = page({});
  // one set of bands app-wide (80+ strong, 50-79 possible): 72 used to read "strong" here and "possible" in the list
  assert.deepEqual({ ...p.pipelineItemFit({ tender_key: "k2", fit_score: 85 }) }, { score: 85, band: "strong" });
  assert.deepEqual({ ...p.pipelineItemFit({ tender_key: "k2", fit_score: 72 }) }, { score: 72, band: "possible" });
  assert.deepEqual({ ...p.pipelineItemFit({ tender_key: "k2", fit_score: 45, fit_band: "possible" }) }, { score: 45, band: "possible" });
  assert.equal(p.pipelineItemFit({ tender_key: "k3" }), null);
});

test("the board counts High fit from the live scores and the section states how many it shows", () => {
  const board = cut("function renderPipelineBoard(");
  assert.ok(board.includes("pipelineItemFit(item)"), "items must be rescored from the cache");
  assert.ok(/of \$\{highFit\.length\}/.test(board), "the list header must say N of the counted total");
  assert.ok(board.includes("dashToggleHighFit"), "a way to show every high-fit tender");
  assert.ok(cut("function wireDashboardActions(").includes("dashToggleHighFit"));
});

test("tracking stores the score the list shows for the row", () => {
  assert.ok(cut("async function trackTender(").includes("getRowFit(row)"));
});

// Moving a card on the board (stage dropdown, drag) used to save it under a second, title-based key: a pipeline item has no
// resource id, so rowKey(item) differed from its real key. The original stayed put, a copy appeared in the new stage and the
// copy lost the original's "+ Open opportunity" tag.
function boardPage(pipeline) {
  const posts = [];
  const ctx = vm.createContext({
    state: { pipeline, fitCache: {}, activeKey: null }, posts,
    fetch: async (url, init) => { posts.push(JSON.parse(init.body)); return { ok: true }; },
    renderRows() {}, renderPipelineBoard() {}, refreshTrackPanel() {}, window: {},
    getRowFit: () => ({ score: 5, band: "weak" }),
  });
  vm.runInContext([cut("function rowKey("), cut("async function trackTender("), "this.trackTender = trackTender;"].join("\n"), ctx);
  return ctx;
}
const item = { tender_key: "procontract:DN827866", stage: "bidding", title: "PR-2026-00150 Cleaning Services", source: "procontract",
  contracting_authority: "High Peak BC", submission_deadline: "", estimated_value: "", fit_score: 77, fit_band: "possible", added_from: "open_opportunity", notes: "n" };

test("moving a card keeps its own key, its tag and its score, and makes no second card", async () => {
  const p = boardPage({ [item.tender_key]: { ...item } });
  await p.trackTender(p.state.pipeline[item.tender_key], "submitted", item.tender_key);
  assert.equal(p.posts.length, 1);
  assert.equal(p.posts[0].tender_key, "procontract:DN827866");
  assert.equal(p.posts[0].added_from, "open_opportunity");
  assert.equal(p.posts[0].fit_score, 77);
  assert.equal(p.posts[0].fit_band, "possible");
  assert.deepEqual(Object.keys(p.state.pipeline), ["procontract:DN827866"]);
  assert.equal(p.state.pipeline["procontract:DN827866"].stage, "submitted");
});

test("a pipeline item opened in the details panel is also saved under its own key", async () => {
  const p = boardPage({ [item.tender_key]: { ...item } });
  await p.trackTender(p.state.pipeline[item.tender_key], "won");
  assert.equal(p.posts[0].tender_key, "procontract:DN827866");
  assert.equal(p.posts[0].added_from, "open_opportunity");
  assert.deepEqual(Object.keys(p.state.pipeline), ["procontract:DN827866"]);
});

test("a search result is still tracked under its resource id and takes the score the list shows", async () => {
  const p = boardPage({});
  await p.trackTender({ source: "procontract", resource_id: "DN1", title: "T" }, "watching");
  assert.equal(p.posts[0].tender_key, "procontract:DN1");
  assert.equal(p.posts[0].added_from, "tender_search");
  assert.equal(p.posts[0].fit_score, 5);
});

test("the board passes the card's key when a stage is chosen or a card is dropped", () => {
  const wire = cut("function wireDashboardActions(");
  assert.equal((wire.match(/trackTender\(item, (newStage|targetStage), key\)/g) || []).length, 2);
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { await fn(); console.log(`ok    ${name}`); } catch (err) { failed += 1; console.log(`FAIL  ${name}`); console.log(err && err.stack ? err.stack : err); }
  }
  console.log(`\n${tests.length - failed} of ${tests.length} passed`);
  process.exitCode = failed ? 1 : 0;
})();
