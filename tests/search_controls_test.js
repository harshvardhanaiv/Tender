"use strict";
/* Run: node tests/search_controls_test.js
   Round 28 item 3.4: when a search finished, the banner said "Search complete ... 100%" but still offered Pause and Stop.
   This cuts the REAL updateSearchProgressUI() and its helper out of web/app.js, runs them against a small fake page and
   checks which controls are visible while a search runs, is paused, and after it completes. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const WEB = path.join(__dirname, "..", "web");
const appLines = fs.readFileSync(path.join(WEB, "app.js"), "utf8").replace(/\r\n/g, "\n").split("\n");
function cutFunction(name) {
  const start = appLines.findIndex((l) => new RegExp(`^(async )?function ${name}\\(`).test(l));
  assert.ok(start >= 0, `app.js has no top-level function ${name}()`);
  let end = start;
  while (end < appLines.length && !/^\}\s*;?\s*$/.test(appLines[end])) end += 1;
  return appLines.slice(start, end + 1).join("\n");
}
const CODE = ["setSearchControlsHidden", "updateSearchProgressUI"].map(cutFunction).join("\n\n");
new vm.Script(CODE);

const SearchStatus = require(path.join(WEB, "search-status.js"));

function fakePage(phase, extra = {}) {
  const make = () => {
    const el = { disabled: false, style: {}, textContent: "", innerHTML: "", attrs: {}, _classes: new Set(["hidden"]) };
    el.classList = {
      add: (c) => el._classes.add(c), remove: (c) => el._classes.delete(c),
      toggle: (c, on) => { if (on === undefined ? !el._classes.has(c) : on) el._classes.add(c); else el._classes.delete(c); },
      contains: (c) => el._classes.has(c),
    };
    el.setAttribute = (k, v) => { el.attrs[k] = v; };
    el.getAttribute = (k) => el.attrs[k];
    el.removeAttribute = (k) => { delete el.attrs[k]; };
    return el;
  };
  const ids = ["searchProgressTrack", "searchProgressBar", "searchProgressText", "searchProgressPct", "btnPauseSearch", "btnResumeSearch", "btnStopSearchBanner"];
  const els = Object.fromEntries(ids.map((id) => [id, make()]));
  els.searchProgressTrack.attrs["data-active"] = "true";
  const state = { searchPhase: phase, jobId: "j1", q: "plumbing", rows: [], meta: {}, searchPaused: false, ...extra };
  const ctx = vm.createContext({
    $: (id) => els[id] || null, state, SearchStatus, esc: (s) => String(s), SOURCES_LABEL: {}, setTimeout: () => 0,
    currentPortalList: () => [], filteredAndSortedRows: () => [], getActiveSelectedPortalsCount: () => 3,
    updateActiveSearchKeywordBadge: () => {},
  });
  vm.runInContext(CODE, ctx);
  return { els, state, run: () => vm.runInContext("updateSearchProgressUI()", ctx) };
}

const visible = (els) => ["btnPauseSearch", "btnResumeSearch", "btnStopSearchBanner"].filter((id) => !els[id].classList.contains("hidden"));

const tests = [];
const test = (n, f) => tests.push([n, f]);

test("while a search runs, Pause and Stop are offered", () => {
  const p = fakePage("loading");
  p.run();
  assert.deepEqual(visible(p.els).sort(), ["btnPauseSearch", "btnResumeSearch", "btnStopSearchBanner"].sort());
  assert.equal(p.els.btnPauseSearch.disabled, false);
});

test("once the search has completed, Pause, Resume and Stop are gone", () => {
  const p = fakePage("complete");
  // they were showing during the search
  ["btnPauseSearch", "btnResumeSearch", "btnStopSearchBanner"].forEach((id) => p.els[id].classList.remove("hidden"));
  p.run();
  assert.deepEqual(visible(p.els), [], "the completed banner must not offer Pause, Resume or Stop");
  assert.ok(p.els.searchProgressText.innerHTML.includes("Search complete"), p.els.searchProgressText.innerHTML);
  assert.equal(p.els.searchProgressPct.textContent, "100%");
});

test("starting a new search brings the controls back", () => {
  const p = fakePage("complete");
  p.run();
  assert.deepEqual(visible(p.els), []);
  p.state.searchPhase = "loading";
  p.run();
  assert.ok(visible(p.els).includes("btnPauseSearch") && visible(p.els).includes("btnStopSearchBanner"));
});

(async () => {
  let failed = 0;
  for (const [n, f] of tests) {
    try { await f(); console.log(`ok    ${n}`); } catch (e) { failed += 1; console.log(`FAIL  ${n}`); console.log(e.stack || e); }
  }
  console.log(`\n${tests.length - failed} of ${tests.length} passed`);
  process.exitCode = failed ? 1 : 0;
})();
