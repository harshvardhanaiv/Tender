"use strict";
/* Run: node tests/answer_bank_assist_test.js
   The Answer Bank assistant: category suggestions, attention flags and the document checklist (all deterministic). */
const assert = require("node:assert/strict");
const A = require("../web/answer-bank-assist.js");

const NOW = new Date("2026-10-21T12:00:00Z");
const tests = [];
const test = (n, f) => tests.push([n, f]);

test("a document is sorted by what its name and text say, the file name counting most", () => {
  assert.equal(A.suggestCategory("Public_Liability_Insurance_2026.pdf", "Certificate of cover").category, "Insurance & Certificates");
  assert.equal(A.suggestCategory("policy.docx", "Our GDPR and data protection arrangements").category, "Cyber & Data Security");
  assert.equal(A.suggestCategory("H&S policy.pdf", "Health and safety at work, risk assessment, RIDDOR").category, "Health & Safety");
  assert.equal(A.suggestCategory("Modern Slavery Statement.pdf", "").category, "Policies & Compliance");
  assert.equal(A.suggestCategory("Case study - Leeds.docx", "").category, "Experience");
  const none = A.suggestCategory("scan0001.pdf", "lorem");
  assert.deepEqual([none.category, none.confidence], ["General", "none"]);
  assert.equal(A.suggestCategory("x.pdf", "we hold public liability cover").confidence, "low", "a hit only in the body is a weak suggestion");
  assert.equal(A.suggestCategory("insurance certificate.pdf", "").confidence, "high");
});

test("expiry dates are read in the common written forms and compared with today", () => {
  const d = (s) => A.findExpiry(s).toISOString().slice(0, 10);
  assert.equal(d("Policy expires 31/12/2025."), "2025-12-31");
  assert.equal(d("Valid until 2027-03-01"), "2027-03-01");
  assert.equal(d("Cover renews on 5 June 2026"), "2026-06-05");
  assert.equal(d("expiry: March 2026"), "2026-03-31", "month and year mean the end of that month");
  assert.equal(d("Issued 01/01/2020. Expires 01/01/2021 and 01/01/2023"), "2021-01-01", "the earliest stated expiry");
  assert.equal(A.findExpiry("No dates here"), null);
  assert.equal(A.findExpiry("expires 45/45/2025"), null, "an impossible date is ignored");
});

test("answers that need attention are flagged with the reason", () => {
  const codes = (item) => A.attentionFlags(item, NOW).map((f) => f.code);
  assert.deepEqual(codes({ answer: "Public liability £10m. Expires 31/12/2025. " + "detail ".repeat(100), updated_at: "2026-09-01 10:00:00", category: "Insurance" }), ["expired"]);
  assert.deepEqual(codes({ answer: "Cover valid until 2026-11-30 " + "detail ".repeat(100), updated_at: "2026-09-01 10:00:00" }), ["expiring"]);
  assert.deepEqual(codes({ answer: "Our policy is [insert policy name here] " + "detail ".repeat(100), updated_at: "2026-09-01" }), ["placeholder"]);
  assert.deepEqual(codes({ answer: "We are good.", updated_at: "2026-09-01" }), ["thin"]);
  assert.deepEqual(codes({ answer: "", updated_at: "2026-09-01" }), ["empty"]);
  assert.deepEqual(codes({ answer: "Short but backed by a file.", file_name: "policy.pdf", updated_at: "2026-09-01" }), [], "a document carries the answer");
  assert.deepEqual(codes({ answer: "detail ".repeat(40), category: "Insurance & Certificates", updated_at: "2025-06-01 00:00:00" }), ["stale"], "insurance goes stale after 12 months");
  assert.deepEqual(codes({ answer: "detail ".repeat(40), category: "Experience", updated_at: "2025-06-01 00:00:00" }), [], "a case study is fine at 16 months");
  assert.deepEqual(codes({ answer: "detail ".repeat(40), category: "Experience", updated_at: "2024-06-01 00:00:00" }), ["stale"]);
});

test("the checklist marks what the bank covers, what needs attention and what is missing", () => {
  const items = [
    { id: 1, category: "Insurance & Certificates", question: "Public liability insurance", answer: "£10m cover, expires 31/12/2025. " + "detail ".repeat(80), updated_at: "2026-09-01" },
    { id: 2, category: "Health & Safety", question: "Health and safety policy", answer: "detail ".repeat(150), updated_at: "2026-09-01" },
    { id: 3, category: "Policies & Compliance", question: "Equality policy", answer: "detail ".repeat(150), file_name: "EDI.pdf", updated_at: "2026-09-01" },
  ];
  const st = A.checklistStatus(items, { now: NOW });
  const by = Object.fromEntries(st.map((s) => [s.id, s]));
  assert.equal(by.pli.status, "attention");
  assert.deepEqual(by.pli.matches, [1]);
  assert.equal(by.pli.flags[0].code, "expired");
  assert.equal(by.hs.status, "covered");
  assert.equal(by.edi.status, "covered");
  assert.equal(by.eli.status, "missing");
  assert.equal(by.ms.status, "missing");
  const sum = A.summary(st);
  assert.deepEqual([sum.total, sum.covered, sum.attention], [A.CHECKLIST.length, 3, 1]);
  assert.ok(sum.essentialMissing >= 3);
  assert.ok(!st.some((s) => s.sector), "no sector extras without sector words");
});

test("documents typical of the user's sector are added to the checklist", () => {
  const ctx = "We are a construction and refurbishment contractor tendering for roofing and groundworks";
  const ids = A.checklistStatus([], { context: ctx, now: NOW }).map((s) => s.id);
  assert.ok(ids.includes("chas") && ids.includes("rams"));
  assert.ok(!ids.includes("iso27"));
  const both = A.checklistStatus([], { context: ctx + " plus cloud software and cyber services", now: NOW }).map((s) => s.id);
  assert.ok(both.includes("chas") && both.includes("iso27"));
  const done = A.checklistStatus([{ id: 9, category: "Insurance & Certificates", question: "CHAS", answer: "CHAS accredited " + "detail ".repeat(80), updated_at: "2026-09-01" }], { context: ctx, now: NOW });
  assert.equal(done.find((s) => s.id === "chas").status, "covered");
});

test("every checklist entry is well formed", () => {
  const all = A.CHECKLIST.concat(...A.SECTOR_ADDONS.map((s) => s.items));
  const seen = new Set();
  for (const e of all) {
    assert.ok(e.id && e.label && e.category && e.why && e.keywords.length, e.id);
    assert.ok(["essential", "common"].includes(e.level), e.id);
    assert.ok(!seen.has(e.id), "duplicate id " + e.id);
    seen.add(e.id);
    assert.ok(e.keywords.every((k) => k === k.toLowerCase()), "keywords are lower case: " + e.id);
  }
});

(async () => {
  let failed = 0;
  for (const [n, f] of tests) {
    try { await f(); console.log(`ok    ${n}`); } catch (e) { failed += 1; console.log(`FAIL  ${n}`); console.log(e.stack || e); }
  }
  console.log(`\n${tests.length - failed} of ${tests.length} passed`);
  process.exitCode = failed ? 1 : 0;
})();
