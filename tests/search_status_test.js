"use strict";
/* Run: node tests/search_status_test.js   (no packages needed; Node 18 or later)

   web/search-status.js turns the server's per-portal results into everything the Tender Search page says
   about a search: the "waiting on ..." line, the per-portal summary, the results header and the empty
   state. These tests pin the Round 27 behaviour: the progress line names exactly the portals that have
   not settled, a portal that returned nothing is never confused with one that failed, the header counts
   portals that answered, and nothing the server sends can inject markup. */
const assert = require("node:assert/strict");
const path = require("node:path");

const S = require(path.join(__dirname, "..", "web", "search-status.js"));

const tests = [];
const test = (name, fn) => tests.push([name, fn]);
const has = (html, ...fragments) => fragments.forEach((f) => assert.ok(html.includes(f), `expected ${JSON.stringify(f)} in:\n${html}`));
const lacks = (html, ...fragments) => fragments.forEach((f) => assert.ok(!html.includes(f), `did not expect ${JSON.stringify(f)} in:\n${html}`));

const labels = { find_tender: "Find a Tender (UK)", contracts_finder: "Contracts Finder (UK)", procontract: "ProContract (UK Councils)" };
const labelOf = (id) => labels[id] || id;
const IDS = ["find_tender", "contracts_finder", "procontract"];

const result = (id, status, over = {}) => ({ label: labelOf(id), status, count: 0, durationMs: 1200, message: null, partial: false, available: null, late: false, ...over });
const metaOf = (statuses, over = {}) => ({ portal_results: Object.fromEntries(IDS.map((id) => [id, result(id, statuses[id] || "ok", (over[id] || {}))])) });

test("portalList reads the server's typed results in the order it sent them", () => {
  const list = S.portalList({ portal_results: {
    procontract: { label: "ProContract (UK Councils)", status: "timeout", count: 2, shown: 4, stored: 2, hidden_by_county: 1, durationMs: 60000, message: "slow", partial: true, available: 90, late: false },
    find_tender: { status: "empty" },
  } }, labelOf);
  assert.deepEqual(list.map((p) => p.id), ["procontract", "find_tender"]);
  assert.deepEqual(list[0], { id: "procontract", label: "ProContract (UK Councils)", status: "timeout", count: 2, shown: 4, stored: 2, hiddenByCounty: 1, droppedInvalid: 0, droppedDuplicate: 0,
    durationMs: 60000, message: "slow", partial: true, available: 90, late: false });
  assert.equal(list[1].label, "Find a Tender (UK)", "a portal without a label is named from the page's own list");
  assert.equal(list[1].count, 0);
  assert.deepEqual(S.portalList({}, labelOf), []);
  assert.deepEqual(S.portalList(null, labelOf), []);
});

test("portalList works the same statuses out from older responses", () => {
  const list = S.portalList({
    source_progress: { a: "pending", b: "loading", c: "first_page", d: "fetching_all", e: "complete", f: "complete", g: "complete", h: "error", i: "error", j: "timeout", k: "stopped" },
    errors: { g: "only part", h: "boom", i: "boom too" },
    source_counts: { e: 3, f: 0, g: 0, h: 0, i: 2, j: 1 },
  }, labelOf);
  const by = Object.fromEntries(list.map((p) => [p.id, p]));
  assert.equal(by.a.status, "pending");
  ["b", "c", "d"].forEach((id) => assert.equal(by[id].status, "running", id));
  assert.equal(by.e.status, "ok");
  assert.equal(by.f.status, "empty", "complete, nothing, no complaint: a genuine zero");
  assert.equal(by.g.status, "error", "complete with a complaint and nothing to show is not 'empty'");
  assert.equal(by.h.status, "error");
  assert.deepEqual([by.i.status, by.i.partial, by.i.message], ["ok", true, "boom too"], "an error after some rows is partial");
  assert.equal(by.j.status, "timeout");
  assert.equal(by.k.status, "stopped");
});

test("waitingNote names exactly the portals not yet settled, whatever order they settle in", () => {
  const permutations = (items) => (items.length <= 1 ? [items] : items.flatMap((x, i) => permutations([...items.slice(0, i), ...items.slice(i + 1)]).map((rest) => [x, ...rest])));
  assert.equal(permutations(IDS).length, 6);
  for (const order of permutations(IDS)) {
    const statuses = Object.fromEntries(IDS.map((id) => [id, "running"]));
    order.forEach((settling, step) => {
      statuses[settling] = "ok";
      const list = S.portalList(metaOf(statuses), labelOf);
      const waiting = IDS.filter((id) => statuses[id] === "running");
      const p = S.progress(list);
      assert.equal(p.total, 3);
      assert.equal(p.settled, step + 1, `${order.join(">")} after ${settling}`);
      assert.deepEqual(p.waiting.map((w) => w.id), waiting);
      const note = S.waitingNote(list);
      if (waiting.length === 0) assert.equal(note, "");
      else if (waiting.length === 1) assert.equal(note, ` — waiting on ${labelOf(waiting[0])}`);
      else assert.equal(note, ` — waiting on ${labelOf(waiting[0])} and ${labelOf(waiting[1])}`);
    });
  }
  const none = S.portalList(metaOf({ find_tender: "pending", contracts_finder: "pending", procontract: "running" }), labelOf);
  assert.equal(S.waitingNote(none), " — waiting on 3 portals", "more than two: a count, not a list");
  const odd = S.portalList({ portal_results: { x: { label: "<b>Evil</b>", status: "running" } } }, labelOf);
  assert.equal(S.waitingNote(odd), " — waiting on &lt;b&gt;Evil&lt;/b&gt;");
});

test("a timed-out, failed or stopped portal is settled, so the search is not 'waiting' on it", () => {
  const list = S.portalList(metaOf({ find_tender: "timeout", contracts_finder: "error", procontract: "stopped" }), labelOf);
  assert.equal(S.waitingNote(list), "");
  assert.equal(S.progress(list).settled, 3);
});

test("headerSub counts the portals that answered, not the portals that were selected", () => {
  const all = S.portalList(metaOf({}), labelOf);
  assert.equal(S.headerSub(all, 3, 1, false), "Across 3 portals in 1 country");
  const one = S.portalList(metaOf({ procontract: "timeout" }), labelOf);
  assert.equal(S.headerSub(one, 3, 1, false), "Across 2 of 3 portals in 1 country");
  const zeroIsAnAnswer = S.portalList(metaOf({ find_tender: "empty" }), labelOf);
  assert.equal(S.headerSub(zeroIsAnAnswer, 3, 2, false), "Across 3 portals in 2 countries", "'0 results' is an answer");
  const stopped = S.portalList(metaOf({ find_tender: "stopped", procontract: "error" }), labelOf);
  assert.equal(S.headerSub(stopped, 3, 1, false), "Across 1 of 3 portals in 1 country");
  assert.equal(S.headerSub(one, 3, 1, true), "Across 3 portals in 1 country", "while searching nothing has failed yet");
  assert.equal(S.headerSub([], 4, 2, false), "Across 4 portals in 2 countries", "no portal information: the selected portals");
  assert.equal(S.headerSub(S.portalList({ portal_results: { a: result("a", "ok") } }, labelOf), 1, 1, false), "Across 1 portal in 1 country");
});

test("chipText says what happened in a few words", () => {
  const chip = (status, over) => S.chipText({ ...result("find_tender", status), ...over });
  assert.equal(chip("ok", { count: 6 }), "6");
  assert.equal(chip("ok", { count: 120, available: 15419 }), "120 of 15,419");
  assert.equal(chip("ok", { count: 6, available: 6 }), "6");
  assert.equal(chip("ok", { count: 4, partial: true }), "4 · incomplete");
  assert.equal(chip("ok", { count: 6, stored: 4 }), "6 · +4 saved earlier");
  assert.equal(chip("empty"), "0 · no results");
  assert.equal(chip("timeout"), "timed out");
  assert.equal(chip("timeout", { count: 5 }), "5 shown · timed out");
  assert.equal(chip("error", { message: "Find a Tender is rate-limiting requests (HTTP 429)." }), "rate-limited");
  assert.equal(chip("error", { message: "Find a Tender answered with its “Something went wrong” page" }), "error page");
  assert.equal(chip("error", { message: "ProContract did not respond in time" }), "no answer");
  assert.equal(chip("error", { message: "something odd" }), "failed");
  assert.equal(chip("stopped"), "stopped");
  assert.equal(chip("running"), "searching…");
  assert.equal(chip("pending"), "waiting");
});

test("summaryHtml: a chip per portal that needs attention, Retry only where a retry makes sense, the reason in plain sight", () => {
  const list = S.portalList(metaOf(
    { find_tender: "error", contracts_finder: "ok", procontract: "timeout" },
    { find_tender: { message: "Find a Tender is rate-limiting requests (HTTP 429). Try again in a minute." },
      contracts_finder: { count: 6 }, procontract: { count: 4, message: "ProContract did not respond in time (waited 75 s)", partial: true, durationMs: 75000 } }), labelOf);
  const html = S.summaryHtml(list);
  has(html, "portal-summary__chip--error", "portal-summary__chip--timeout",
    "Find a Tender (UK)", "ProContract (UK Councils)", "rate-limited", "4 shown · timed out",
    '<div class="portal-summary__reason"><b>Find a Tender</b> is rate-limiting requests (HTTP 429). Try again in a minute.</div>',
    '<div class="portal-summary__reason"><b>ProContract</b> did not respond in time (waited 75 s)</div>');
  assert.equal((html.match(/data-portal-action="retry"/g) || []).length, 2);
  has(html, 'data-portal-action="retry" data-portal="find_tender"', 'data-portal-action="retry" data-portal="procontract"');
  lacks(html, "portal-summary__chip--ok", "Contracts Finder (UK)");   // answered normally: no chip
  const withZero = S.summaryHtml(S.portalList(metaOf({ find_tender: "empty", contracts_finder: "stopped", procontract: "running" }), labelOf));
  has(withZero, 'data-portal-action="resume" data-portal="contracts_finder"', "Contracts Finder (UK)");
  lacks(withZero, 'data-portal-action="retry"', "portal-summary__reason", "no results", "searching…");   // empty and running portals get no chip
  assert.equal(S.summaryHtml([]), "");
});

test("summaryHtml: when every portal answered normally there is no chip row at all (it read as applied filters)", () => {
  const list = S.portalList(metaOf({ find_tender: "ok", contracts_finder: "ok", procontract: "empty", pcs: "running", sell2wales: "pending" },
    { find_tender: { count: 119, available: 15456, stored: 19 }, contracts_finder: { count: 179, stored: 101 } }), labelOf);
  assert.equal(S.summaryHtml(list), "");
  assert.equal(S.summaryHtml(S.portalList(metaOf({ find_tender: "ok" }, { find_tender: { count: 5 } }), labelOf)), "");
});

test("the reason line names the portal once, however the server worded the message", () => {
  const line = (label, message) => S.summaryHtml([{ id: "x", label, status: "error", message, count: 0 }]).match(/<div class="portal-summary__reason">.*?<\/div>/)[0];
  assert.equal(line("Contracts Finder (UK)", "Contracts Finder did not respond in time (waited 60 s)"),
    '<div class="portal-summary__reason"><b>Contracts Finder</b> did not respond in time (waited 60 s)</div>');
  assert.equal(line("Contracts Finder", "contracts finder did not respond"), '<div class="portal-summary__reason"><b>contracts finder</b> did not respond</div>', "the message's own spelling is kept");
  assert.equal(line("GCA Frameworks", "GCA said no"), '<div class="portal-summary__reason"><b>GCA Frameworks</b>: GCA said no</div>', "a message that does not start with the name gets it in front");
  assert.equal(line("eTenders (Ireland)", "Request failed"), '<div class="portal-summary__reason"><b>eTenders (Ireland)</b>: Request failed</div>');
  assert.equal(line("(odd)", "anything"), '<div class="portal-summary__reason"><b>(odd)</b>: anything</div>', "a label that is only a bracket is not shortened to nothing");
});

test("summaryHtml escapes everything the server sends", () => {
  const list = S.portalList({ portal_results: { "x\"><img>": { label: "<img src=x onerror=alert(1)>", status: "error", message: "<script>alert(2)</script>" } } }, labelOf);
  const html = S.summaryHtml(list);
  lacks(html, "<img", "<script", 'x"><img>');
  has(html, "&lt;img src=x onerror=alert(1)&gt;", "&lt;script&gt;alert(2)&lt;/script&gt;");
  // a message that starts with the portal's name is split after the name: the rest must be escaped too
  const named = S.summaryHtml([{ id: "x", label: "Find a Tender (UK)", status: "error", count: 0, message: 'Find a Tender <img src=x onerror=alert(3)> "quoted" & more' }]);
  lacks(named, "<img");
  has(named, "<b>Find a Tender</b> &lt;img src=x onerror=alert(3)&gt; &quot;quoted&quot; &amp; more");
});

test("the chip tooltip carries the detail", () => {
  const html = S.summaryHtml(S.portalList({ portal_results: { find_tender: { label: "Find a Tender (UK)", status: "timeout", count: 20, available: 15419, durationMs: 6100, stored: 3, hidden_by_county: 7, dropped_duplicate: 3, dropped_invalid: 1, message: "note", late: true } } }, labelOf));
  has(html, "6.1 s", "15,419 notices match on the portal", "3 of the rows shown were saved by earlier searches", "7 hidden by the county filter", "3 duplicates removed", "1 unreadable row dropped", "answered after the time limit");
});

test("countyNoteHtml says how many rows the county filter removed", () => {
  const filter = { counties: 48, hidden_total: 25, hidden_by_source: { find_tender: 20, procontract: 5 } };
  const html = S.countyNoteHtml(filter, 48, labelOf);
  has(html, "<strong>25 results hidden by your county filter</strong>", "Find a Tender (UK) 20 · ProContract (UK Councils) 5", "your 48 selected counties");
  has(S.countyNoteHtml({ counties: 1, hidden_total: 1, hidden_by_source: { find_tender: 1 } }, 1, labelOf), "1 result hidden", "your 1 selected county;");
  assert.equal(S.countyNoteHtml(null, 48, labelOf), "");
  assert.equal(S.countyNoteHtml({ counties: 48, hidden_total: 0, hidden_by_source: {} }, 48, labelOf), "");
  assert.equal(S.countyNoteHtml(filter, 0, labelOf), "", "no county selected: nothing was filtered");
});

test("emptyState tells a failed portal from a genuine zero", () => {
  const ctx = (over) => ({ searching: false, hasQuery: true, query: "Construction", rowsFromServer: 0, hiddenByCounty: 0, ...over });
  const single = (status, over) => S.portalList({ portal_results: { find_tender: result("find_tender", status, over) } }, labelOf);

  const failed = S.emptyState(single("error", { message: "boom" }), ctx());
  assert.equal(failed.title, "Find a Tender (UK) did not answer");
  has(failed.msg, "Find a Tender (UK) did not answer for “Construction”", "Use Retry above");
  lacks(failed.msg, "widen", "broader");

  const zero = S.emptyState(single("empty"), ctx());
  assert.equal(zero.title, "No tenders found");
  has(zero.msg, "Find a Tender (UK) returned 0 results for “Construction”", "broader keyword");

  const allFailed = S.emptyState(S.portalList(metaOf({ find_tender: "error", contracts_finder: "timeout", procontract: "error" }), labelOf), ctx());
  assert.equal(allFailed.title, "No portal answered");
  const someFailed = S.emptyState(S.portalList(metaOf({ find_tender: "timeout", contracts_finder: "empty", procontract: "empty" }), labelOf), ctx());
  assert.equal(someFailed.title, "1 portal did not answer");
  has(someFailed.msg, "Find a Tender (UK) did not answer");
  const manyZero = S.emptyState(S.portalList(metaOf({ find_tender: "empty", contracts_finder: "empty", procontract: "empty" }), labelOf), ctx());
  has(manyZero.msg, "Find a Tender (UK), Contracts Finder (UK), ProContract (UK Councils) returned 0 results");
  has(S.emptyState(single("empty"), ctx({ hiddenByCounty: 12 })).msg, "12 results were hidden by your county filter");
  has(S.emptyState(single("empty"), ctx({ hiddenByCounty: 1 })).msg, "1 result was hidden by your county filter");
  has(S.emptyState(single("empty"), ctx({ query: "<b>x</b>" })).msg, "“&lt;b&gt;x&lt;/b&gt;”");

  assert.equal(S.emptyState(single("empty"), ctx({ searching: true })), null, "still searching: the page's own wording");
  assert.equal(S.emptyState(single("empty"), ctx({ hasQuery: false })), null);
  assert.equal(S.emptyState(single("error"), ctx({ rowsFromServer: 5 })), null, "rows exist but are filtered out: the page's own wording");
  assert.equal(S.emptyState([], ctx()), null);
  assert.equal(S.emptyState(single("stopped"), ctx()), null, "a stopped search proves nothing either way");
});

test("failedPortals", () => {
  const list = S.portalList(metaOf({ find_tender: "error", contracts_finder: "timeout", procontract: "empty" }), labelOf);
  assert.deepEqual(S.failedPortals(list).map((p) => p.id), ["find_tender", "contracts_finder"]);
});

test("resultsHeadline: rows hidden by the page's own filters are not a sample", () => {
  // Round 28 follow-up: 8,765 rows loaded, 6,177 left after the filters, and no portal reporting a larger total.
  // The header used to say "Showing sample of 6,177 of 8,765 tenders".
  const list = S.portalList(metaOf({}, { find_tender: { count: 5000 }, contracts_finder: { count: 3000 }, procontract: { count: 765 } }), labelOf);
  const h = S.resultsHeadline(6177, 8765, list);
  assert.equal(h.main, "6,177 tenders");
  assert.ok(!/sample/i.test(h.main), "no sample wording when nothing was left unfetched");
});

test("resultsHeadline: no 'Sample only' line, even when a portal holds more notices than were fetched", () => {
  // asked for removal after the live check: "Sample only: Find a Tender (UK) 119 of 15,456 fetched" sat under every search
  const list = S.portalList(metaOf({}, { find_tender: { count: 119, available: 15422 }, contracts_finder: { count: 173, available: 173 } }), labelOf);
  const h = S.resultsHeadline(13, 304, list);
  assert.equal(h.main, "13 tenders");
  assert.equal(h.note, undefined);
  assert.ok(!/sample/i.test(JSON.stringify(h)));
});

test("resultsHeadline: nothing hidden, singular, and no search information", () => {
  assert.equal(S.resultsHeadline(1, 1, []).main, "1 tender");
  assert.equal(S.resultsHeadline(0, 0, null).main, "0 tenders");
  assert.equal(S.resultsHeadline(25, 25, []).main, "25 tenders");
});

test("reconciliationText makes fetched, saved, hidden and shown add up in words", () => {
  // the Round 28 "Construction" search: 304 fetched, but 13 shown, 212 "before filters" and 158 "hidden" read as contradictory
  const rec = { total: { fetched: 304, dropped_invalid: 10, dropped_duplicate: 20, saved_added: 5, hidden_by_county: 158, shown: 121, balanced: true } };
  assert.equal(S.reconciliationText(rec, 13),
    "304 fetched − 10 unreadable − 20 duplicates + 5 saved earlier − 158 hidden by the county filter = 121 sent to the page; your other filters leave 13");
  assert.equal(304 - 10 - 20 + 5 - 158, 121, "the example itself balances");
  assert.equal(S.reconciliationText({ total: { fetched: 12, dropped_invalid: 0, dropped_duplicate: 1, saved_added: 0, hidden_by_county: 0, shown: 11, balanced: true } }, 11),
    "12 fetched − 1 duplicate = 11 sent to the page");
  assert.ok(S.reconciliationText({ total: { fetched: 5, shown: 4, balanced: false } }, 4).includes("do not add up"));
  assert.equal(S.reconciliationText(null, 5), "");
  assert.equal(S.reconciliationText({}, 5), "");
});

test("supplyChainLabel marks a contractor advertising a sub-contract, and never a public buyer or utility", () => {
  const LABEL = "Private / supply-chain opportunity";
  for (const name of ["Balfour Beatty Civil Engineering Limited", "Kier Construction Ltd", "Morgan Sindall Construction & Infrastructure Limited", "Crest Nicholson Homes Ltd"]) {
    assert.equal(S.supplyChainLabel(name), LABEL, name);
  }
  for (const name of ["Gateshead Council", "National Grid Electricity Transmission plc", "Network Rail Infrastructure Limited", "Thames Water Utilities Limited",
    "NHS Supply Chain", "University of Bath", "Cuan Mhuire", "Ardent Plumbing Ltd", "Balfour Beatty", "", null, undefined]) {
    assert.equal(S.supplyChainLabel(name), "", String(name));
  }
});

test("both result renderers use the label instead of a Buyer intel link", () => {
  const fs = require("node:fs");
  const app = fs.readFileSync(path.join(__dirname, "..", "web", "app.js"), "utf8");
  assert.ok((app.match(/SearchStatus\.supplyChainLabel/g) || []).length >= 2, "the table row and the card must both check the label");
  assert.ok(app.includes("btn-intel-badge--private"), "the private-opportunity badge is rendered");
});

test("the results header shows the count only: no 'Sample only' or 'How this adds up' line, the chip box hides when empty", () => {
  const fs = require("node:fs");
  const app = fs.readFileSync(path.join(__dirname, "..", "web", "app.js"), "utf8");
  assert.ok(!app.includes("How this adds up"), "the reconciliation line is back on the page");
  assert.ok(!app.includes("headline.note"), "the sample note is back on the page");
  assert.ok(app.includes('wrap.classList.toggle("hidden", !chips)'), "the portal summary box must hide when it has no chips");
  assert.ok(app.includes("el.title = recon"), "the counts stay available as the hover text of the total");
});

test("the page header no longer builds its own 'sample' text from the row counts", () => {
  const fs = require("node:fs");
  const app = fs.readFileSync(path.join(__dirname, "..", "web", "app.js"), "utf8");
  assert.ok(!app.includes("Showing sample of"), "app.js must not decide 'sample' from filtered vs loaded rows");
  assert.ok(app.includes("SearchStatus.resultsHeadline"), "the header must use SearchStatus.resultsHeadline");
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try {
      await fn();
      console.log(`ok    ${name}`);
    } catch (err) {
      failed += 1;
      console.log(`FAIL  ${name}`);
      console.log(err && err.stack ? err.stack : err);
    }
  }
  console.log(`\n${tests.length - failed} of ${tests.length} passed`);
  process.exitCode = failed ? 1 : 0;
})();
