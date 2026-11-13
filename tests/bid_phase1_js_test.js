// Phase 1 front-end helpers: alert titles (fix plan E2) and the placeholder count (G9).
// Run: node tests/bid_phase1_js_test.js
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

const src = fs.readFileSync(path.join(__dirname, "..", "web", "app.js"), "utf8");
function grab(name) {
  const start = src.indexOf(`function ${name}(`);
  assert(start >= 0, `${name} not found`);
  let depth = 0, i = src.indexOf("{", start);
  for (; i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}" && --depth === 0) break;
  }
  return src.slice(start, i + 1);
}

const ctx = { SOURCES_LABEL: { pcs: "Public Contracts Scotland", sam_gov: "SAM.gov" } };
vm.createContext(ctx);
vm.runInContext(grab("cleanAlertTitle") + "\n" + grab("countUnresolvedPlaceholdersInText"), ctx);

let n = 0;
const eq = (a, b, m) => { assert.strictEqual(a, b, m); n++; };

eq(ctx.cleanAlertTitle("Matthewsgreen Community Centre", "procontract:DN1"), "Matthewsgreen Community Centre", "real title kept");
eq(ctx.cleanAlertTitle("SAM.gov https://alpha.sam.gov/opp/123", "sam_gov:123"), "SAM.gov", "URL stripped");
eq(ctx.cleanAlertTitle("pcs:SEP564280", "pcs:SEP564280"), "A tracked tender on Public Contracts Scotland", "raw key replaced");
eq(ctx.cleanAlertTitle("View Notice", "pcs:SEP1"), "A tracked tender on Public Contracts Scotland", "link text replaced");
eq(ctx.cleanAlertTitle("", "find_tender:1"), "A tracked tender on find tender", "unknown portal label falls back to its id");

eq(ctx.countUnresolvedPlaceholdersInText("A [NEEDS INPUT: x] b [Add 1-2 case studies] c [to be inserted] [link](http://a) [1] [ ]"), 3, "same placeholders as the server");
eq(ctx.countUnresolvedPlaceholdersInText(""), 0, "empty");

// M15 / R1 / R5 / R6: the "Before we write your bid" box.
const _consts = src.slice(src.indexOf("const _PORTAL_STEPS = ["), src.indexOf("function bidPortalSteps("))
  + src.slice(src.indexOf("const _EXPECTED_DOCS = ["), src.indexOf("// The \"Before we write your bid\" box"));
vm.runInContext(_consts.replace(/^const /gm, "var ") + "\n" + grab("bidPortalSteps") + "\n" + grab("bidPreflight")
  + "\nfunction noticeDate(v) { return String(v).slice(0, 10); }", ctx);
const pcRow = { source: "procontract", resource_id: "DN831703", submission_deadline: "", title: "Matthewsgreen" };
const costs = { proposal: "10 credits", download: "3 credits" };
let pre = ctx.bidPreflight(pcRow, [], "", [], false, costs, true);
ok(pre.show && !/\(unknown\)/.test(pre.message) && /notice gives no deadline/.test(pre.message), "R1: no '(unknown) has passed'");
ok(/procontract\.due-north\.com/.test(pre.message) && /reference DN831703/.test(pre.message), "ProContract steps with the reference");
ok(/☐ Questionnaire or response template/.test(pre.message) && /uses 10 credits; each download afterwards uses 3 credits/.test(pre.message), "expected documents + cost");
pre = ctx.bidPreflight(pcRow, [{ filename: "Doc4_Questionnaire.pdf" }, { filename: "Clarification_log_6_Nov.pdf" }], "", ["Hall bid 2025"], true, costs, false);
ok(pre.show && /✓ Questionnaire/.test(pre.message) && /✓ Updates, addenda/.test(pre.message) && /Hall bid 2025/.test(pre.message), "R5: ticked winning bid listed");
pre = ctx.bidPreflight({ source: "custom" }, [{ filename: "a.pdf" }], "", [], true, costs, false);
ok(!pre.show, "custom upload confirmed recently: no box");
ok(ctx.bidPortalSteps({ source: "find_tender", description: "Documents on In-Tend" }).portal === "In-Tend", "portal from the description");
pre = ctx.bidPreflight({ source: "contracts_finder", submission_deadline: "30/11/2099" }, [], "", [], false, costs, false, [{ title: "EEF RFP brief.pdf" }]);
ok(!/No buyer documents are attached/.test(pre.message) && /1 public document on the notice/.test(pre.message) && /EEF RFP brief/.test(pre.message), "A2: public PDF counted");
const pre2 = ctx.bidPreflight({ source: "contracts_finder" }, [], "", [], false, costs, false, [{ title: "The EEF is seeking proposals from a team.", filename: "EEF_product_team_rfp_-_oct_2026_1.0.pdf" }]);
ok(/✓ Invitation \/ instructions/.test(pre2.message) && /EEF_product_team_rfp/.test(pre2.message), "public RFP ticked by its file name and shown by it");
ok(/Deadline we will use: 30\/11\/2099/.test(pre.message), "deadline line");
pre = ctx.bidPreflight({ source: "in_tend", submission_deadline: "20/11/2099" }, [{ filename: "ITT.pdf" }, { filename: "Clarifications.pdf" }], "", [], true, costs, false, [],
                       { verdict: "No-bid", fit_score: 10, summary: "A BI firm.", capability_gaps: ["owns no print presses"] });
ok(pre.show && pre.warnings[0].startsWith("Fit check from the notice, before any credits: No-bid (fit 10/100)") && /owns no print presses/.test(pre.message),
   "M16: verdict before credits, as a warning when it is not Bid");
ok(ctx.bidPortalSteps({ source: "find_tender" }) === null, "no portal named: no steps");
ok(/reference DN831703/.test(ctx.bidPortalSteps({ source: "find_tender", resource_id: "093543-2026", description: "Documents on ProContract (DN831703)." }).steps)
   && !/093543/.test(ctx.bidPortalSteps({ source: "find_tender", resource_id: "093543-2026", description: "Documents on ProContract." }).steps),
   "the portal reference, never another portal's id");

// M16 card
vm.runInContext("function esc(s){return String(s).replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));}\n" + grab("bidDecisionCardHtml"), ctx);
const card = ctx.bidDecisionCardHtml({ verdict: "No-bid", fit_score: 0, summary: "No <venue> record.", capability_gaps: ["no venue"], partner_for: [], mandatory: [], criteria: [] }, []);
ok(/Advice: No-bid · fit 0\/100/.test(card) && /&lt;venue&gt;/.test(card), "decision card escapes and shows the verdict");
ok(ctx.bidDecisionCardHtml(null, []) === "", "no decision, no stages: nothing");

vm.runInContext(grab("mergeFreshTenderDetails"), ctx);
const realRow = { title: "Embedded product team ... EEF's Digital Public Service", description: "Our ambition is to build...", submission_deadline: "30/11/2026" };
let merged = ctx.mergeFreshTenderDetails(realRow, { _stub: true, title: "Contracts Finder Notice #ocds-x", description: "UK Contracts Finder public notice #ocds-x." });
ok(merged.title === realRow.title && merged.description === realRow.description, "a stand-in never replaces the real title or description");
merged = ctx.mergeFreshTenderDetails(realRow, { description: "", submission_deadline: "08/12/2026", cpv_codes: [] , title: "Not available"});
ok(merged.description === realRow.description && merged.submission_deadline === "08/12/2026" && merged.title === realRow.title, "empty values are skipped, real updates kept");
function ok(c, m) { assert(c, m); n++; }

console.log(`bid_phase1_js_test: ${n} checks passed`);
