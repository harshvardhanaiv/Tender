"use strict";
/* Run: node tests/name_format_test.js
   Round 28 item 2.6: names showed as LEAP LEGAL SOFTWARE LTD next to title-case ones, "London Borough Of Camden" ("Of" capitalised) and
   "Gristwood and Toms:" with a stray colon. web/name-format.js is the one place that decides how a name is shown. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const N = require(path.join(__dirname, "..", "web", "name-format.js"));

const tests = [];
const test = (n, f) => tests.push([n, f]);

test("capitals become title case, keeping acronyms and small words", () => {
  const cases = {
    "LEAP LEGAL SOFTWARE LTD": "Leap Legal Software Ltd",
    "INTEC FOR BUSINESS LIMITED": "Intec for Business Limited",
    "BALFOUR BEATTY CIVIL ENGINEERING LIMITED": "Balfour Beatty Civil Engineering Limited",
    "THE BOROUGH COUNCIL OF GATESHEAD": "The Borough Council of Gateshead",
    "NHS SUPPLY CHAIN": "NHS Supply Chain",
    "UK RESEARCH & INNOVATION": "UK Research & Innovation",
    "SALESFORCE UK LIMITED": "Salesforce UK Limited",
    "NEWCASTLE-UPON-TYNE HOSPITALS NHS FOUNDATION TRUST": "Newcastle-upon-Tyne Hospitals NHS Foundation Trust",
    "ST. MARY'S COLLEGE": "St. Mary's College",
    "MARKS AND SPENCER PLC": "Marks and Spencer PLC",
    "A1M ROADS 2B LTD": "A1M Roads 2B Ltd",
  };
  for (const [from, to] of Object.entries(cases)) assert.equal(N.display(from), to, from);
});

test("a small word in the middle of a mixed-case name goes to lower case", () => {
  assert.equal(N.display("London Borough Of Camden"), "London Borough of Camden");
  assert.equal(N.display("Stoke On Trent City Council"), "Stoke on Trent City Council");
  assert.equal(N.display("Bank Of England"), "Bank of England");
  assert.equal(N.display("The Royal Borough Of Greenwich"), "The Royal Borough of Greenwich", "a leading 'The' stays");
});

test("trailing punctuation from a bad scrape is dropped and spaces collapse", () => {
  assert.equal(N.display("Gristwood and Toms:"), "Gristwood and Toms");
  assert.equal(N.display("  Acme   Ltd , "), "Acme Ltd");
  assert.equal(N.display("Altered Images Ltd."), "Altered Images Ltd.", "a real full stop is kept");
});

test("a name that is already well cased is left exactly as published", () => {
  for (const name of ["McDonald & Sons Ltd", "iSOFT Group Limited", "Camden Highline", "Places for People Group Limited", "H. Malone & Sons Ltd", "A.T. SERVICES LIMITED".replace("A.T. SERVICES LIMITED", "A.T. Services Limited")]) {
    assert.equal(N.display(name), name, name);
  }
});

test("empty and odd input is safe", () => {
  assert.equal(N.display(null), "");
  assert.equal(N.display(undefined), "");
  assert.equal(N.display("   "), "");
  assert.equal(N.display(":"), "");
  assert.equal(N.display("ABC"), "ABC", "too short to be sure it is shouting");
  assert.equal(N.display(12345), "12345");
});

test("the pages load the formatter and the workspace and buyer cards use it", () => {
  const web = (n) => fs.readFileSync(path.join(__dirname, "..", "web", n), "utf8");
  assert.ok(web("buyer-workspace.html").includes("/name-format.js"), "buyer-workspace.html must load name-format.js before buyer-workspace.js");
  assert.ok(web("buyer-workspace.html").indexOf("/name-format.js") < web("buyer-workspace.html").indexOf("/buyer-workspace.js"));
  assert.ok(web("index.html").includes("/name-format.js"));
  assert.ok(/NameFormat\.display/.test(web("buyer-workspace.js")), "the workspace formats names");
  assert.ok(/NameFormat\.display/.test(web("app.js")), "Buyer Intelligence formats names");
});

(async () => {
  let failed = 0;
  for (const [n, f] of tests) {
    try { await f(); console.log(`ok    ${n}`); } catch (e) { failed += 1; console.log(`FAIL  ${n}`); console.log(e.stack || e); }
  }
  console.log(`\n${tests.length - failed} of ${tests.length} passed`);
  process.exitCode = failed ? 1 : 0;
})();
