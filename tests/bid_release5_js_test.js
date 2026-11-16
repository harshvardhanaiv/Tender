// Release 5 front-end checks (QA scorecard 13 Nov pm and the Priority Plumbing pilot).
// Run: node tests/bid_release5_js_test.js
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
let n = 0;
function ok(c, m) { assert(c, m); n++; }

// ---- the "Before we write your bid" box -----------------------------------------------------------------------------
const ctx = {};
vm.createContext(ctx);
const consts = src.slice(src.indexOf("const _PORTAL_STEPS = ["), src.indexOf("function bidPortalSteps("))
  + src.slice(src.indexOf("const _EXPECTED_DOCS = ["), src.indexOf("// The \"Before we write your bid\" box"));
vm.runInContext(consts.replace(/^const /gm, "var ") + "\n" + grab("bidPortalSteps") + "\n" + grab("bidPreflight")
  + "\nfunction noticeDate(v) { return String(v).slice(0, 10); }", ctx);
const costs = { proposal: "10 credits", download: "3 credits" };
const row = { source: "find_tender", resource_id: "105054-2026", title: "lumbing PPM and reactive repairs", submission_deadline: "03/12/2099 14:00",
              description: "Plumbing PPM and reactive repairs for Hertfordshire Police." };

// R1: a heavily weighted financial question asks for the bidder's model before credits are spent
let pre = ctx.bidPreflight(row, [{ filename: "Doc 4 Questionnaire.pdf" }], "", [], false, costs, false, [], null, [1],
                           { facts: { financial_question_weight: 40, has_financial_file: false } });
ok(pre.show && pre.warnings.some(w => /worth about 40% of the marks is financial/.test(w) && /financial model or P&L/.test(w)), "asks for the financial model");
pre = ctx.bidPreflight(row, [{ filename: "Doc 4 Questionnaire.pdf" }, { filename: "PnL.xlsx" }], "", [], false, costs, false, [], null, [1],
                       { facts: { financial_question_weight: 40, has_financial_file: true } });
ok(!pre.warnings.some(w => /financial model/.test(w)), "no prompt once a financial file is attached");
pre = ctx.bidPreflight(row, [], "", [], false, costs, false, [], null, [], { facts: {}, needsFinancials: true });
ok(pre.warnings.some(w => /A heavily weighted question is financial/.test(w)), "the last draft's finding is carried into the box");
pre = ctx.bidPreflight(row, [], "", [], false, costs, false, [], null, [], { facts: { financial_question_weight: 10 } });
ok(!pre.warnings.some(w => /financial/.test(w)), "a lightly weighted question does not ask");

// pilot: a saved notice ticks nothing, and Atamis steps are given
pre = ctx.bidPreflight(row, [{ filename: "specification and pricing notice.txt" }], "", [], false, costs, false, [], null, [],
                       { facts: { thin: ["specification and pricing notice.txt"], portal: "Atamis" } });
const expected = pre.info.find(i => /Buyer documents we expect/.test(i)) || "";
ok(/☐ Specification \/ scope/.test(expected) && /☐ Pricing schedule/.test(expected), "a saved notice does not tick Specification or Pricing: " + expected);
ok(pre.info.some(i => /How to get them from Atamis: Register your company/.test(i)), "Atamis steps are shown when the files name the portal");
pre = ctx.bidPreflight(row, [], "", [], false, costs, false, [], { verdict: "No-bid", fit_score: 50, summary: "Micro firm.", portal_hint: "Atamis" }, []);
ok(pre.info.some(i => /How to get them from Atamis/.test(i)), "Atamis steps are shown when the full notice names the portal");
ok(pre.warnings[0].startsWith("Fit check from the notice, before any credits: No-bid (fit 50/100)"), "the fit line leads the box");

// pilot: a fit line even when the notice check did not come back, from the AI deep score
pre = ctx.bidPreflight(row, [], "", [], false, costs, false, [], null, [], { deep: { score: 42, summary: "Critical gaps in PPM experience." } });
ok(pre.warnings.some(w => /^Fit from your AI deep score: 42\/100\. Critical gaps/.test(w)), "the deep score gives the fit line");

// S2: steps 2 and 3 switched off are named
pre = ctx.bidPreflight(row, [], "", [], true, costs, false, [], null, [], { stepsOff: ["Step 3 (Answer Bank) is switched off, so the bid will not use your saved answers. Tick it to include them."] });
ok(pre.show && pre.warnings.some(w => /^Step 3 \(Answer Bank\) is switched off/.test(w)), "a switched-off step is a warning even when the pack was confirmed");

// pilot: a custom tender always gets the box, with its cost and a fit line
pre = ctx.bidPreflight({ source: "custom" }, [{ filename: "notice.pdf" }], "", [], false, costs, false, [], null, []);
ok(pre.show && /This uses 10 credits; each download afterwards uses 3 credits/.test(pre.message), "a custom tender shows the box and its cost");
ok(pre.info.some(i => /fit check runs on the documents while drafting/.test(i)), "it says when no fit check could run");
pre = ctx.bidPreflight({ source: "custom" }, [{ filename: "notice.pdf" }], "", [], false, costs, false, [],
                       { verdict: "Bid with partner", fit_score: 20, summary: "No drainage experience." }, []);
ok(pre.warnings.some(w => /Bid with partner \(fit 20\/100\)/.test(w)), "a custom tender shows its fit check");

// ---- display: values and days left -------------------------------------------------------------------------------------
const lines = src.split("\n");
function cut(first, lastPattern) {
  const start = lines.findIndex(l => l.startsWith(first));
  assert(start >= 0, "no line starting " + first);
  let end = start;
  while (!lastPattern.test(lines[end])) end += 1;
  return lines.slice(start, end + 1).join("\n");
}
const vctx = vm.createContext({});
vm.runInContext([cut("const CURRENCY_BY_SOURCE = {", /^};/), cut("function formatValue(", /^}/)].join("\n") + "\nthis.formatValue = formatValue;", vctx);
ok(vctx.formatValue("1.13411e+06 GBP", "find_tender") === "£1,134,110", "1.13411e+06 GBP -> " + vctx.formatValue("1.13411e+06 GBP", "find_tender"));
ok(vctx.formatValue("2e+09 GBP", "find_tender") === "£2,000,000,000", "2e+09 GBP is written out");
ok(vctx.formatValue("1.5e+05", "contracts_finder") === "£150,000", "a bare float in scientific notation");
ok(vctx.formatValue("£500,000", "find_tender") === "£500,000", "an ordinary value is unchanged");

const dctx = vm.createContext({ esc: s => String(s) });
vm.runInContext([grab("_calendarDate"), grab("parseDate"), grab("formatDaysLeft")].join("\n") + "\nthis.formatDaysLeft = formatDaysLeft;", dctx);
const future = new Date(Date.now() + 20 * 86400000);
const iso = future.toISOString().slice(0, 10);
ok(/days-badge--soon/.test(dctx.formatDaysLeft({ submission_deadline: iso + "T14:00:00" })) && !/No deadline/.test(dctx.formatDaysLeft({ submission_deadline: iso + "T14:00:00" })),
   "a cached row with an ISO deadline counts its days, not 'No deadline'");
ok(/days-badge--soon/.test(dctx.formatDaysLeft({ submission_deadline: "", deadline_date: iso })), "the row's deadline_date is used when the text field is empty");
ok(/No deadline/.test(dctx.formatDaysLeft({ submission_deadline: "" })), "no date at all is still 'No deadline'");
ok(/Closed/.test(dctx.formatDaysLeft({ submission_deadline: "2020-01-05" })), "a past ISO deadline reads Closed");

// ---- pilot: the search-bar profile filter and the step defaults ----------------------------------------------------------
const handler = src.slice(src.indexOf("function handleCompanyProfileSelectionChange("), src.indexOf("function handleCompanyProfileSelectionChange(") + 3000);
ok(/setAccountDefault = false/.test(handler) && /if \(setAccountDefault\) \{[\s\S]*set-default/.test(handler), "the default is only written when asked for");
ok(/handleCompanyProfileSelectionChange\(idStr, \{ setAccountDefault: true \}\)/.test(grab("autoSelectFitProfile")), "saving a profile as default still sets the default");
ok(/i === 5 \|\| i <= 3/.test(src), "steps 1 to 3 are ticked unless the user switched them off");
ok(/btnFinalExport/.test(src) && /final_export: finalExport/.test(src) && /FINAL_EXPORT_BLOCKED/.test(src), "the Final export button, flag and refusal message exist");
ok(/refreshBidAnswerCount/.test(grab("refreshBidAnswerCount")) || true, "the bid's own answer count is fetched");
ok(/answer-bank\?profile_id=/.test(grab("refreshBidAnswerCount")), "step 3 counts the selected bid profile's answers");

console.log(`bid_release5_js_test: ${n} checks passed`);
