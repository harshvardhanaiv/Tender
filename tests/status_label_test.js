"use strict";
/* Run: node tests/status_label_test.js
   Live check after Round 28: a Contracts Finder notice with a closing date of 16/04/2026 showed Status "Active" beside a "Closed" badge, because the
   Status column printed the portal's own label. It must read "Closed" once the deadline has passed. Cuts the real functions out of web/app.js. */
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
const CODE = [
  cut("function _calendarDate(", /^}/), cut("function parseDate(", /^}/), cut("function isDeadlinePassed(", /^}/),
  cut("function tableDate(", /^}/), cut("function isAwardNotice(", /^}/), cut("function isClosedTender(", /^}/), cut("function statusLabel(", /^}/), cut("function dashDaysLeft(", /^}/), cut("const EXPIRING_DOC_RE", /;$/), cut("function docNeedsExpiryDate(", /^}/), cut("function docNameKey(", /^}/),
  'this.statusLabel = statusLabel; this.tableDate = tableDate; this.dashDaysLeft = dashDaysLeft; this.docNeedsExpiryDate = docNeedsExpiryDate; this.docNameKey = docNameKey;',
].join("\n");
const ctx = vm.createContext({});
vm.runInContext(CODE, ctx);
const label = (row) => ctx.statusLabel(row);

const tests = [];
const test = (n, f) => tests.push([n, f]);

test("an Active notice whose deadline has passed reads Closed", () => {
  assert.equal(label({ status: "Active", submission_deadline: "16/04/2026" }), "Closed");
  assert.equal(label({ status: "active", submission_deadline: "2026-04-16" }), "Closed");
  assert.equal(label({ status: "Open", days_until_deadline: -3 }), "Closed");
});

test("an Active notice with a future deadline, or none, stays Active", () => {
  assert.equal(label({ status: "Active", submission_deadline: "31/12/2099" }), "Active");
  assert.equal(label({ status: "Active" }), "Active");
  assert.equal(label({ status: "Active", submission_deadline: "TBC" }), "Active");
});

test("other statuses are shown exactly as published", () => {
  assert.equal(label({ status: "Awarded", submission_deadline: "16/04/2026" }), "Awarded");
  assert.equal(label({ status: "Cancelled", submission_deadline: "16/04/2026" }), "Cancelled");
  assert.equal(label({ status: "" }), "");
  assert.equal(label({}), "");
});

test("both renderers use it", () => {
  const src = lines.join("\n");
  assert.ok(src.includes('tdText(statusLabel(row), "col-status td--status")'), "table row prints the raw status again");
  assert.ok(src.includes("${esc(statusLabel(row))}"), "card prints the raw status again");
});

test("supplier and buyer labels on both renderers go through the name formatter", () => {
  const src = lines.join("\n");
  assert.equal((src.match(/Awarded to \$\{esc\(displayName\((sup|cardSup)\)\)\}/g) || []).length, 3, "an 'Awarded to' label shows the raw capitals again");
  assert.ok(src.includes("${esc(displayName(authority))}</button>"), "the card's buyer button shows the raw name again");
});

test("report 6 T1: award notices and no-deadline notices are never open; a future deadline is open", () => {
  const closed = (row) => ctx.isClosedTender(row);
  const pcsAward = { source: "pcs", status: null, notice_type: "03 Contract Award Notice - Successful Supplier(s)", date_published: "29/10/2026" };
  const quickQuote = { source: "pcs", status: null, notice_type: "Quick Quote Award", title: "Award of Intelligent Email Security" };
  assert.equal(closed(pcsAward), true);
  assert.equal(closed(quickQuote), true);
  assert.equal(closed({ source: "pcs", status: null, title: "Award of Solar PV and Battery", date_published: "29/10/2026" }), true);
  assert.equal(closed({ source: "pcs", status: null, date_published: "29/10/2026" }), true, "no status and no deadline is nothing to bid on");
  assert.equal(closed({ source: "find_tender", status: "Active", submission_deadline: "31/12/2099", notice_type: "UK4: Tender Notice" }), false);
  assert.equal(closed({ source: "gca_agreements", status: "Live", deadline_date: "2099-03-06" }), false);
  assert.equal(closed({ source: "x", title: "Awards management software", status: "Active", submission_deadline: "31/12/2099" }), false, "'Awards' in a title is not an award notice");
});

test("report 6 T2: opening a tender does not run the paid deep score", () => {
  const src = fs.readFileSync(path.join(__dirname, "..", "web", "app.js"), "utf8");
  assert.ok(!/renderRows\(\);\s*loadDeepFit\(/.test(src), "the detail view starts the paid score on open again");
  assert.ok(!/if \(fitActive\(\)\) loadDeepFit\(window\._detailRow, null\)/.test(src), "a profile switch starts the paid score again");
  assert.ok(src.includes("data-run-deep-fit"), "the 'Run AI deep score · 1 credit' button is gone");
});

test("report 6 T7: the results table shows one date format", () => {
  const tableDate = ctx.tableDate;
  assert.equal(tableDate("2026-10-29"), "29 Oct 2026");
  assert.equal(tableDate("29/10/2026"), "29 Oct 2026");
  assert.equal(tableDate("25/03/2025 14:30:00".split(" ")[0]), "25 Mar 2025");
  assert.equal(tableDate(""), "");
  assert.equal(tableDate("TBC"), "TBC");
});

test("report 6 T7/T8: duplicate supplier lines, a buyer's area and profile suggestions are wired in", () => {
  const src = fs.readFileSync(path.join(__dirname, "..", "web", "app.js"), "utf8");
  assert.ok(src.includes("shownLabelValues"), "the details list the awarded supplier twice again");
  assert.ok(src.includes("(buyer's area)"), "a council's own name no longer gives the card a place");
  assert.ok(src.includes("data-suggested-search") && src.includes("function suggestedSearchTerms"), "the empty start page lost its profile suggestions");
});

test("re-test 3 T1: a scraped field label is not a deadline, but real and portal-published deadlines stay open", () => {
  const closed = ctx.isClosedTender;
  const iso = (d) => new Date(Date.now() + d * 86400000).toISOString().slice(0, 10);
  // PCS: "Notice Type:" was scraped into the deadline, so the award notice looked open
  assert.equal(closed({ source: "pcs", title: "Oracle Business Intelligence (BI) Software Licencing & Support", submission_deadline: "Notice Type:" }), true);
  // rows seen live for "business intelligence": Find a Tender tender, GCA framework, Sell2Wales engagement, BOAMP
  assert.equal(closed({ source: "find_tender", status: "Active", notice_type: "UK4: Tender notice", submission_deadline: "13/11/2099 17:00:00", days_until_deadline: 14 }), false);
  assert.equal(closed({ source: "gca_agreements", status: "Live", submission_deadline: "15/11/2099 00:00:00", days_until_deadline: 16 }), false);
  assert.equal(closed({ source: "sell2wales", notice_type: "UK2", submission_deadline: "26/11/2099", deadline_date: iso(27) }), false);
  assert.equal(closed({ source: "boamp", submission_deadline: "Voir avis officiel" }), false);
  // awards and no-deadline notices are never open
  assert.equal(closed({ source: "find_tender", status: "Awarded", notice_type: "F03: Contract award notice" }), true);
  assert.equal(closed({ source: "pcs", title: "Award of a contract without prior publication" }), true);
});

test("re-test 3 T1/T5: weaker portal matches are counted and can be shown, after the real matches", () => {
  const src = fs.readFileSync(path.join(__dirname, "..", "web", "app.js"), "utf8");
  assert.ok(src.includes("data-toggle-loose") && src.includes("state.showLooseMatches"), "the hidden weaker matches are not announced");
  assert.ok(src.includes("if (!!a._loose !== !!b._loose) return a._loose ? 1 : -1;"), "loose matches must sort after real ones");
});

test("re-test 3 HS1: opening a saved proposal makes no AI call by itself", () => {
  const src = fs.readFileSync(path.join(__dirname, "..", "web", "app.js"), "utf8");
  assert.ok(!src.includes("// Auto-generate the AI methodology diagram"), "the methodology diagram is generated on open again");
  assert.ok(src.includes('addEventListener("click", () => openMethodologyStudio(true))'), "the methodology button must still generate");
  assert.ok(!/else if \(_lastProposal\?\.bid_response\?\.technical_methodology\) \{\s*\/\/ Missing phases[\s\S]{0,120}openGanttPanel\(\)/.test(src), "the timeline is built on open again");
});

test("re-test 3 PL1: Pipeline reads portal dates day first", () => {
  const now = new Date(2026, 9, 30, 12, 0).getTime();   // 30 Oct 2026, noon
  assert.equal(ctx.dashDaysLeft("01/11/2026 12:00", now), 2, "1 November, not 11 January");
  assert.equal(ctx.dashDaysLeft("01/11/2026 12:00:00", now), 2);
  assert.equal(ctx.dashDaysLeft("12/01/2027", now) > 60, true, "12 January 2027 is months away");
  assert.equal(ctx.dashDaysLeft("2026-11-05T12:00:00", now), 6);
  assert.equal(ctx.dashDaysLeft("30/10/2026 17:00", now), 1, "a deadline later today is not closed");
  assert.equal(ctx.dashDaysLeft("", now), null);
  assert.equal(ctx.dashDaysLeft("not a date", now), null);
});

test("re-test 3 SN8/T8/G0: the selected supplier is in the address, the company's own name is not suggested, sections have addresses", () => {
  const src = fs.readFileSync(path.join(__dirname, "..", "web", "app.js"), "utf8");
  assert.ok(src.includes('parts.push("id=" + encodeURIComponent(supplierId))'), "the selected supplier is not written to the address");
  assert.ok(src.includes("ownName.has(w)"), "the company's own name can be suggested as a keyword again");
  for (const route of ["/growth", "/answers", "/documents", "/history", "/pipeline"]) {
    assert.ok(src.includes(`path: "${route}"`), `no address for ${route}`);
  }
  assert.ok(src.includes("Page not found"), "an unknown address does not say so inside the app");
});

test("re-test 3 PN1/PN2: 'developer' alone is not a building word, and a new search clears a lead that is not in it", () => {
  const src = fs.readFileSync(path.join(__dirname, "..", "web", "planning.js"), "utf8");
  const words = /const BUILD_WORDS = (\/.*\/gi);/.exec(src);
  assert.ok(words, "BUILD_WORDS not found");
  const re = new RegExp(words[1].slice(1, -3), "gi");
  assert.equal("software developer and engineer".match(re), null, "a software developer is not a property developer");
  assert.ok("groundworks and plumbing".match(re), "building words still match");
  assert.ok("property developer".match(re) && "civil engineering".match(re));
  assert.ok(src.includes("clearDetail()") && src.includes("view.emptyDetailHtml"));
});

test("re-test 3 DC1/DC2: a certificate with no expiry date is flagged, and the same file is recognised", () => {
  assert.equal(ctx.docNeedsExpiryDate({ filename: "AIVHUB_LTD_QMS_ISO9001.pdf" }), true);
  assert.equal(ctx.docNeedsExpiryDate({ filename: "Public Liability Insurance 2026.pdf" }), true);
  assert.equal(ctx.docNeedsExpiryDate({ filename: "Public Liability Insurance 2026.pdf", expiry_date: "2027-01-01" }), false);
  assert.equal(ctx.docNeedsExpiryDate({ filename: "Company brochure.pdf" }), false, "an ordinary file needs no date");
  assert.equal(ctx.docNameKey("AIVHUB LTD - ISMS ISO27001.pdf"), ctx.docNameKey("AIVHUB_LTD_-_ISMS_ISO27001.pdf"), "the server stores the name with underscores");
  const src = fs.readFileSync(path.join(__dirname, "..", "web", "app.js"), "utf8");
  assert.ok(src.includes("diffDays <= 60"), "the expiry warning starts 60 days before");
  assert.ok(src.includes("Already uploaded"), "a second upload of the same file is not questioned");
});

(() => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { fn(); console.log(`ok    ${name}`); } catch (err) { failed += 1; console.log(`FAIL  ${name}`); console.log(err && err.stack ? err.stack : err); }
  }
  console.log(`\n${tests.length - failed} of ${tests.length} passed`);
  process.exitCode = failed ? 1 : 0;
})();
