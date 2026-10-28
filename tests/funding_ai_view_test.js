// Run: node tests/funding_ai_view_test.js
const assert = require("assert");
const f = require("../web/funding-ai.js");

const elig = f.resultHtml("eligibility", { verdict: "probably", headline: "<b>x</b>", criteria: [{ criterion: "c", status: "not_met", evidence: "e" }], missing: ["m"], next_steps: [] });
assert(!elig.includes("<b>x"), "the AI text is escaped");
assert(elig.includes("Probably eligible") && elig.includes("Not met") && elig.includes("Still needed"));
assert(!elig.includes("Next steps"), "an empty list shows no heading");

const idle = f.paneHtml("draft", { status: "idle" }, { draft: 6 });
assert(idle.includes("Run: 6 credits") && idle.includes('data-fai-run="draft"'));
assert(f.paneHtml("summary", { status: "idle" }, { summary: 1 }).includes("Run: 1 credit</button>"));
assert(f.paneHtml("summary", { status: "idle" }, { summary: 0 }).includes("Run: free"));
assert(f.paneHtml("summary", { status: "loading" }, {}).includes("Checking for a saved result"));
assert(f.paneHtml("summary", { status: "loading", running: true }, {}).includes("Working on it"));

const done = f.paneHtml("draft", { status: "done", cached: true, savedAt: "2026-10-28T10:00", result: { sections: [{ heading: "Need", text: "a\nb", gaps: ["staff"] }], gaps: [] } }, { draft: 6 });
assert(done.includes("Saved result from 2026-10-28") && done.includes("Run again (6 credits)") && done.includes("You need to add") && done.includes("a<br>b"));
assert(done.includes("data-fai-refresh"));

const err = f.paneHtml("summary", { status: "error", error: "<i>no</i>" }, { summary: 2 });
assert(!err.includes("<i>") && err.includes("Try again (2 credits)"));

const analysis = f.resultHtml("analysis", { recommendation: "no_go", reason: "Too small.", risks: ["r"] });
assert(analysis.includes("No-go") && analysis.includes("fai-pill--bad") && analysis.includes("Risks"));

const modal = f.modalHtml({ name: 'Grant "x" <b>', funder: "Defra" }, "summary", { summary: { status: "idle" } }, {});
assert(!modal.includes("<b>") && modal.includes('aria-selected="true"') && modal.split("data-fai-tab=").length === 8);

// questions: status and the funder's answer are shown from the kept state, and escaped
const qs = f.resultHtml("questions", { questions: [{ question: "Is VAT eligible? <b>", topic: "eligible costs", why: "Not stated" }, { question: "Second?", topic: "other", why: "" }] },
  { q: { "Is VAT eligible? <b>": { status: "answered", note: "Yes, it is" } } });
assert(!qs.includes("<b>") && qs.includes("1 of 2 answered") && qs.includes('<option value="answered" selected>') && qs.includes('value="Yes, it is"'));
assert(qs.includes("data-fai-copy") && f.resultHtml("questions", { questions: [] }, {}).includes("Nothing to ask"));

// budget: the grant share, the cap, the minimum and a missing rate
const grant = { min_award: 5000, max_award: 50000 };
let c = f.budgetCalc(grant, { funding_rate_percent: 50 }, 80000);
assert.deepStrictEqual([c.grant, c.match, c.share], [40000, 40000, 50]);
c = f.budgetCalc(grant, { funding_rate_percent: 50 }, 200000);
assert.deepStrictEqual([c.grant, c.match], [50000, 150000]); assert(c.notes[0].includes("capped at £50,000"));
c = f.budgetCalc(grant, { funding_rate_percent: 50 }, 6000);
assert.strictEqual(c.grant, 3000); assert(c.notes.some((n) => n.includes("smallest award is £5,000")));
c = f.budgetCalc(grant, { funding_rate_percent: null }, 20000);
assert.strictEqual(c.grant, 20000); assert(c.notes.some((n) => n.includes("no funding rate")));
assert.strictEqual(f.budgetCalc(grant, {}, ""), null); assert.strictEqual(f.budgetCalc(grant, {}, "abc"), null);
const bud = f.resultHtml("budget", { funding_rate_percent: 50, rate_note: "Up to 50%.", eligible_costs: ["Staff"], state_aid: "" }, { cost: "80000" }, grant);
assert(bud.includes("The grant pays 50%") && bud.includes("£40,000") && bud.includes('value="80000"') && bud.includes("Staff"));

// evidence: ticks, documents found in the user's files, expired ones flagged, progress counted
const ev = f.resultHtml("evidence", { items: [
  { item: "Public liability insurance", why: "Required", stage: "award", required: true, documents: [{ filename: "pli.pdf", expiry_date: "2025-01-01", expired: true }] },
  { item: "Annual accounts <i>", why: "", stage: "application", required: false, documents: [{ filename: "accounts.pdf", expiry_date: null, expired: false }] },
  { item: "Bank details", why: "", stage: "claim", required: true, documents: [] }] }, { have: { "Bank details": true } });
assert(!ev.includes("<i>") && ev.includes("2 of 3 ready") && ev.includes("is-expired") && ev.includes("(expired 2025-01-01)") && ev.includes("If awarded") && ev.includes("likely"));
assert(ev.includes('data-fai-have="Bank details" checked') || ev.includes('data-fai-have="Bank details"') && ev.includes("checked"));

// best matches strip
const fund = require("../web/funding.js");
const bh = fund.bestHtml([{ label: "a", name: "A", closing_soon: true, matches: ["x", "y"], band: "mid", applicants: [], locations: [] }, { label: "b", name: "B", closing_soon: false, matches: ["x", "y"], band: "mid", applicants: [], locations: [] }]);
assert(bh.includes("Best matches for your profile") && bh.split("Closing soon").length === 2 && bh.includes('data-ai-label="a"') && bh.includes("All open grants"));
assert.strictEqual(fund.bestHtml([]), "");
console.log("funding ai view ok");
