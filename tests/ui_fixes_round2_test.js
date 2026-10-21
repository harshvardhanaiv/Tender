"use strict";
/* Run: node tests/ui_fixes_round2_test.js
   Small fixes from the second QA round, pinned so they do not come back:
     - "tenders found so far" never goes down while a search runs, and a search left in a hidden tab asks for its answer when the
       tab is shown again (hidden tabs run timers about once a minute, so it looked stuck on its last portal),
     - changing the buyer feed's filters keeps the cards on screen instead of swapping in blank skeletons,
     - French and Dutch "Keyword Score" headers wrap instead of running into the next header (styles.css has
       "thead th { white-space: nowrap !important }"),
     - an Answer Bank upload does not claim it sorted a document into a category the user had chosen themselves,
     - Answer Bank tabs and cards spell a category the way it is stored,
     - removing a custom service uses the app's dialog, and the Pipeline board's columns are a full 60% of the window. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const read = (f) => fs.readFileSync(path.join(__dirname, "..", "web", f), "utf8").replace(/\r\n/g, "\n");
const app = read("app.js");
const lines = app.split("\n");
function cut(first) {
  const start = lines.findIndex((l) => l.startsWith(first));
  assert.ok(start >= 0, `app.js has no line starting ${first}`);
  let end = start;
  while (!/^}/.test(lines[end])) end += 1;
  return lines.slice(start, end + 1).join("\n");
}

const tests = [];
const test = (n, f) => tests.push([n, f]);

test("the found count is the highest seen for this search and starts again for the next one", () => {
  const src = cut("function updateSearchProgressUI(");
  assert.match(src, /if \(!state\._foundPeak \|\| state\._foundPeak\.job !== state\.jobId\) state\._foundPeak = \{ job: state\.jobId, n: 0 \};/);
  assert.match(src, /state\._foundPeak\.n = Math\.max\(state\._foundPeak\.n, liveCount\);\s*const tenderCount = state\._foundPeak\.n;/);
});

test("showing the tab again polls the running search at once", () => {
  assert.match(app, /document\.addEventListener\("visibilitychange", \(\) => \{\s*if \(document\.hidden \|\| !state\.jobId \|\| state\.searchPhase !== "loading" \|\| state\.searchPaused\) return;\s*stopSearchPoll\(\);\s*pollSearchProgress\(\);/);
});

test("the buyer feed keeps its cards while a new filter loads", () => {
  assert.match(app, /else if \(!feedEl\?\.querySelector\("\.feed-card"\)\) renderBuyerFeedSkeleton\(\);/);
  assert.match(app, /else if \(!radarTable\?\.querySelector\("tbody tr td:nth-child\(2\)"\)\) renderBuyerRadarSkeleton\(\);/);
});

test("the score headers may wrap and the column is wide enough for the longest label", () => {
  const css = read("responsive-fixes.css");
  assert.match(css, /\.table thead th\.col-fit, \.table thead th\.col-ai-score \{ white-space: normal !important;[^}]*overflow-wrap: normal;/);
  assert.ok(!/overflow-wrap: anywhere/.test(css.split("\n").filter((l) => /col-fit/.test(l)).join("\n")), "never mid-word");
  assert.match(css, /\.table col\.col-fit, \.table th\.col-fit \{ width: 112px !important;/);
  assert.match(app, /id: "fit", label: "Keyword Score", labelKey: "th_ai_fit", width: "112px"/);
  const tr = read("translations.js");
  assert.match(tr, /"th_ai_fit": "Score mots-clés"/);
  assert.match(tr, /"th_ai_fit": "Trefwoordscore"/);
});

test("an upload says the user's own category was kept, and only says 'sorted into' when it did sort", () => {
  const el = { dataset: {} };
  const ctx = vm.createContext({ $: () => el });
  vm.runInContext(cut("function abNoteSuggestedCategory(") + "\nthis.note = abNoteSuggestedCategory;", ctx);
  const sugg = { category: "Insurance & Certificates", reason: "Mentions “public liability”", confidence: "high" };
  ctx.note(sugg, null);
  assert.equal(el.dataset.suggestion, 'Sorted into "Insurance & Certificates" (Mentions “public liability”). Change it above if that is wrong.');
  ctx.note(sugg, "Pricing");
  assert.equal(el.dataset.suggestion, 'This looks like "Insurance & Certificates" (Mentions “public liability”), but your choice of "Pricing" was kept.');
  el.dataset.suggestion = "x";
  ctx.note({ confidence: "none" }, null);
  assert.equal(el.dataset.suggestion, "x", "no suggestion, no note");
  assert.match(app, /\} else if \(sugg && sugg\.confidence !== "none" && catSel && catSel\.value && catSel\.value !== sugg\.category\) \{\s*kept = catSel\.value;/);
});

test("categories keep their stored spelling in tabs and cards", () => {
  const ctx = vm.createContext({});
  vm.runInContext(cut("function formatCatLabel(") + "\nthis.f = formatCatLabel;", ctx);
  assert.equal(ctx.f("Insurance & Certificates"), "Insurance & Certificates");
  assert.equal(ctx.f("  Cyber & Data Security "), "Cyber & Data Security");
  assert.equal(ctx.f(""), "");
  assert.equal(ctx.f(null), "");
});

test("removing a custom service uses the app's dialog, with the native box only as a fallback", () => {
  const svc = read("growth-services.js");
  assert.match(svc, /const ask = ctx\.confirm \|\| \(\(m\) => root\.confirm\(m\)\);\s*if \(!\(await ask\("Remove this service\?/);
  assert.match(read("growth-studio.js"), /api, toast, confirm: confirmDialog, options:/);
});

test("the Pipeline board is a full 60% of the window tall (its border is allowed for)", () => {
  assert.match(read("theme.css"), /min-height: max\(322px, calc\(60vh \+ 2px\)\); height: max\(322px, calc\(60vh \+ 2px\)\)/);
});

let failed = 0;
(async () => {
  for (const [name, fn] of tests) {
    try { await fn(); console.log(`ok    ${name}`); } catch (err) { failed += 1; console.log(`FAIL  ${name}`); console.log(err && err.stack ? err.stack : err); }
  }
  console.log(`\n${tests.length - failed} of ${tests.length} passed`);
  process.exitCode = failed ? 1 : 0;
})();
