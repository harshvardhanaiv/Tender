"use strict";
/* Run: node tests/retest3_batch_a_test.js
   Re-test 3, batch A: front-end rules. Each test names the QA item it covers. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const web = (f) => fs.readFileSync(path.join(__dirname, "..", "web", f), "utf8");
let passed = 0, failed = 0;
function test(name, fn) { try { fn(); passed++; console.log("ok   " + name); } catch (e) { failed++; console.log("FAIL " + name + "\n  " + e.message); } }

test("SN5: the supplier header shows no count until the number arrives", () => {
  const html = web("index.html");
  assert.ok(/id="lblSupplierCountWrap" hidden>/.test(html), "the count sentence starts hidden");
  assert.ok(!/id="lblSupplierCount">—</.test(html), "no dash placeholder");
  const app = web("app.js");
  assert.ok(app.includes('$("lblSupplierCountWrap").hidden = !known'));
});

test("T7: one date format in cards and the detail panel, and a time of day is kept", () => {
  const app = web("app.js");
  const m = app.match(/function noticeDate\(value\) \{[\s\S]*?\n\}\n/);
  assert.ok(m, "noticeDate exists");
  const ctx = {};
  new Function("ctx", `function parseDate(s){const d=new Date(s.length===10&&s[4]==="-"?s+"T00:00:00":s);return isNaN(d)?null:d;}
    function tableDate(v){const d=parseDate(String(v).trim());return d?d.toLocaleDateString("en-GB",{day:"numeric",month:"short",year:"numeric"}):String(v);}
    ${m[0]}; ctx.f = noticeDate;`)(ctx);
  assert.equal(ctx.f("2026-10-30"), "30 Oct 2026");
  assert.equal(ctx.f("2026-10-30 12:00"), "30 Oct 2026 12:00");
  assert.equal(ctx.f("Not stated"), "Not stated");
  assert.equal(ctx.f(""), "");
  assert.ok(app.includes("esc(noticeDate(published))") && app.includes("esc(noticeDate(deadline))") && app.includes("esc(noticeDate(rawDeadline))"));
});

test("GS7: a Growth Studio tab never shrinks below its label", () => {
  const css = web("growth-studio.css");
  const rule = css.slice(css.indexOf("\n.gs-tab {"), css.indexOf("\n.gs-tab:hover"));
  assert.ok(/flex:\s*0 0 auto/.test(rule) && /min-width:\s*max-content/.test(rule), rule);
});

test("AB3: the Answer Bank category chips have a line of their own, wrapping, instead of running under the buttons", () => {
  const css = web("theme.css");
  const rule = css.split("\n").find((l) => l.startsWith("body.tf-shell #abModal .ab-tabs {"));
  assert.ok(rule && /flex:\s*1 1 100%/.test(rule) && /flex-wrap:\s*wrap/.test(rule) && !/nowrap/.test(rule), rule);
  assert.ok(/#abModal \.ab-controls \{[^}]*flex-wrap: wrap/.test(css));
});

test("DC3: the Bid Response Assistant ticks the profile's current documents when nothing valid is remembered", () => {
  const app = web("app.js");
  const m = app.match(/function bidDocsToTick\(docs, rememberedIds, today\) \{[\s\S]*?\n\}\n/);
  assert.ok(m);
  const ctx = {};
  new Function("ctx", m[0] + "; ctx.f = bidDocsToTick;")(ctx);
  const today = new Date(2026, 9, 30);
  const docs = [{ id: 1 }, { id: 2, expiry_date: "2027-03-01" }, { id: 3, expiry_date: "2026-01-01" }, { id: 4, expiry_date: "2026-10-30" }];
  assert.deepEqual([...ctx.f(docs, new Set(), today)], ["1", "2", "4"], "nothing remembered: every current document, not the expired one");
  assert.deepEqual([...ctx.f(docs, new Set(["99", "98"]), today)], ["1", "2", "4"], "only deleted documents remembered: same");
  assert.deepEqual([...ctx.f(docs, new Set(["3", "99"]), today)], ["3"], "a remembered document that exists is kept, even if expired");
  assert.deepEqual([...ctx.f([], new Set(), today)], []);
  assert.ok(app.includes("bidDocsToTick(uniqueDocs, checkedIds, new Date())"));
});

function cut(name) {
  const app = web("app.js");
  const m = app.match(new RegExp("function " + name + "\\([^)]*\\) \\{[\\s\\S]*?\\n\\}\\n"));
  assert.ok(m, name + " exists");
  const ctx = {};
  new Function("ctx", m[0] + "; ctx.f = " + name + ";")(ctx);
  return ctx.f;
}

test("HS4: an unstated submission deadline reads 'Not stated', not three times over", () => {
  const f = cut("submissionDeadlineText");
  assert.equal(f({ date: "NOT STATED", time: "NOT STATED", timezone: "NOT STATED" }), "Not stated");
  assert.equal(f(undefined), "Not stated");
  assert.equal(f({ date: "30 Oct 2026", time: "12:00", timezone: "GMT" }), "30 Oct 2026 12:00 (GMT)");
  assert.equal(f({ date: "30 Oct 2026", time: "Not stated", timezone: "not stated" }), "30 Oct 2026");
});

test("HS3: runs of the same tender are one History entry, newest first", () => {
  const f = cut("groupHistoryRuns");
  const items = [
    { id: 9, source: "find_tender", resource_id: "A", type: "analysis", company: "Acme" },
    { id: 8, source: "find_tender", resource_id: "B", type: "analysis", company: "Acme" },
    { id: 7, source: "find_tender", resource_id: "A", type: "analysis", company: "acme " },
    { id: 6, source: "find_tender", resource_id: "A", type: "proposal", company: "Acme" },
    { id: 5, source: "", resource_id: "", type: "summary" },
    { id: 4, source: "", resource_id: "", type: "summary" },
  ];
  const g = f(items);
  assert.deepEqual(g.map((x) => [x.latest.id, x.runs.map((r) => r.id)]), [[9, [9, 7]], [8, [8]], [6, [6]], [5, [5]], [4, [4]]]);
});

test("B9: the radar sends its two widening filters and says what it shows", () => {
  const app = web("app.js"), html = web("index.html");
  assert.ok(app.includes('queryParams.set("all_categories", "1")') && app.includes('queryParams.set("ending_soon", "1")'));
  assert.ok(html.includes('id="chkRadarAllCategories"') && html.includes('id="chkRadarEndingSoon"') && html.includes('id="radarScopeText"'));
  const f = cut("radarScopeText");
  assert.equal(f({ all_categories: false, service_labels: ["software packages", "IT services"], min_days: 60 }), "Renewals in software packages, IT services, 2 to 12 months out");
  assert.equal(f({ all_categories: true, service_labels: [], min_days: 0 }), "Renewals in all categories, up to 12 months out, including those ending soon");
});

test("T5: the weaker-matches link says whether they are in the count and how many", () => {
  const f = cut("looseHintHtml");
  assert.equal(f(0, false), "");
  assert.match(f(12, false), /12 weaker matches not counted — show them/);
  assert.match(f(12, true), /12 weaker matches included in this count — hide them/);
  assert.match(f(1, false), /1 weaker match not counted — show it/);
  assert.match(f(1500, true), /1,500 weaker matches/);
  assert.ok(f(3, false).includes('data-toggle-loose="1"'), "the click handler still finds it");
  assert.ok(web("app.js").includes("looseHint = looseHintHtml(loose, wasShowing)"));
});

//__MORE__
console.log(`\n${passed} of ${passed + failed} passed`);
process.exit(failed ? 1 : 0);
