"use strict";
/* Run: node tests/results_count_matches_rows_test.js
   D11: the "N tenders" total, the pagination ("of N") and the visible rows must all come from one list, the list
   filteredAndSortedRows() returns after dedupe, scope, county and client post-filters. renderRows() used to draw the
   rows while the header was only refreshed by some of its 60-odd callers, so a tab, term or filter change could leave
   "N tenders" describing an older list. renderRows() now hands its list to updateResultsTotal(). */
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

/* A page with just enough of the real functions: the real renderRows() and updateResultsTotal(), a stubbed filter
   (so the test chooses what "the filtered list" is) and stubbed DOM. */
function makePage(listFn) {
  const out = { header: null, paginationTotal: null, drawn: 0, cards: 0 };
  const fakeEl = () => ({ innerHTML: "", title: "", classList: { toggle() {}, add() {}, remove() {} }, appendChild() {}, scrollTop: 0, setAttribute() {}, style: {} });
  const resultsTotal = fakeEl();
  const sandbox = {
    console,
    state: { view: "table", q: "", rows: [{}], statusFilter: "all", currentPage: 1, pageSize: 25, managedCountries: ["United Kingdom"], searchPhase: "complete", keywordScope: "all", jobId: null },
    PORTALS: [{ id: "find_tender", country: "United Kingdom" }],
    _selectedPortals: null,
    TABLE_COLUMNS: { all: [1, 2, 3] },
    MATCH_ROLES: {},
    _collapsedMatchGroups: new Set(),
    document: {
      querySelector: () => fakeEl(),
      createDocumentFragment: () => ({ appendChild() { out.cards += 1; } }),
    },
    $: (id) => (id === "resultsTotal" ? resultsTotal : fakeEl()),
    filteredAndSortedRows: () => listFn(),
    SearchStatus: { resultsHeadline: (n) => ({ main: `${n} tenders` }), headerSub: () => "Across 1 portal", reconciliationText: () => "" },
    esc: (s) => String(s),
    updateFitColumnVisibility() {}, renderSkeletonRows() {}, updateViewToggle() {}, renderTableHeader() {}, updateSortHeader() {},
    updateDetailsWatchlistButton() {}, initTableColumnResizing() {}, renderEmptyState() {}, renderPortalsInCountries() {}, renderPortalSummary() {},
    updateActiveSearchKeywordBadge() {}, currentPortalList: () => [],
    parseKeywordQuery: () => ({ query: "", scope: "all" }), shouldGroupMatchRoles: () => false, getActiveTableScope: () => "all",
    getRowMatchRole: () => null,
    buildResultRow: () => { out.drawn += 1; return fakeEl(); },
    buildResultCard: () => { out.drawn += 1; return fakeEl(); },
    renderPagination: (total) => { out.paginationTotal = total; },
  };
  vm.createContext(sandbox);
  vm.runInContext([cut("function updateResultsTotal("), cut("function renderRows("),
    "this.updateResultsTotal = updateResultsTotal; this.renderRows = renderRows;"].join("\n"), sandbox);
  return { sandbox, out, resultsTotal };
}

const tests = [];
const test = (n, f) => tests.push([n, f]);
const rowsOf = (n) => Array.from({ length: n }, (_, i) => ({ id: i }));

test("the header count is the number of rows renderRows() draws (one page of a longer list)", () => {
  const { sandbox, out, resultsTotal } = makePage(() => rowsOf(60));
  sandbox.renderRows();
  assert.equal(out.paginationTotal, 60, "pagination total");
  assert.equal(out.drawn, 25, "one page of 25 is drawn");
  assert.match(resultsTotal.innerHTML, />60 tenders</, "header total must be the full filtered list");
});

test("a list that changes between renders is followed by the header without any extra header call", () => {
  let n = 40;
  const { sandbox, out, resultsTotal } = makePage(() => rowsOf(n));
  sandbox.renderRows();
  assert.match(resultsTotal.innerHTML, />40 tenders</);
  n = 7; // a tab, term or county filter now keeps only 7 rows; only renderRows() runs
  sandbox.renderRows();
  assert.equal(out.paginationTotal, 7);
  assert.match(resultsTotal.innerHTML, />7 tenders</, "the header must not keep describing the previous list");
});

test("updateResultsTotal(list) counts the list it is given, not a second filter pass", () => {
  const { sandbox, resultsTotal } = makePage(() => rowsOf(99));
  sandbox.updateResultsTotal(rowsOf(3));
  assert.match(resultsTotal.innerHTML, />3 tenders</);
  sandbox.updateResultsTotal(); // older callers: falls back to the filtered list
  assert.match(resultsTotal.innerHTML, />99 tenders</);
});

test("renderRows() refreshes the header from the list it just drew", () => {
  const body = cut("function renderRows(");
  assert.ok(body.includes("updateResultsTotal(allRows)"), "renderRows must pass its own list to the header");
  assert.ok(body.indexOf("renderPagination(totalCount") < body.indexOf("updateResultsTotal(allRows)"));
});

(() => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { fn(); console.log(`ok    ${name}`); } catch (err) { failed += 1; console.log(`FAIL  ${name}`); console.log(err && err.stack ? err.stack : err); }
  }
  console.log(`\n${tests.length - failed} of ${tests.length} passed`);
  process.exitCode = failed ? 1 : 0;
})();
