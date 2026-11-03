"use strict";
/* Run: node tests/retest4_small_test.js   Re-test 4, small front-end items. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const app = fs.readFileSync(path.join(__dirname, "..", "web", "app.js"), "utf8").replace(/\r\n/g, "\n");
let passed = 0, failed = 0;
function test(name, fn) { try { fn(); passed++; console.log("ok   " + name); } catch (e) { failed++; console.log("FAIL " + name + "\n  " + e.message); } }

test("T7: PCS values quoted with a currency code read as money", () => {
  const m = app.match(/const CURRENCY_BY_SOURCE = \{[\s\S]*?\n\};\n[\s\S]*?\nfunction formatValue\(v, source\) \{[\s\S]*?\n\}\n/);
  assert.ok(m, "formatValue exists");
  const ctx = {};
  new Function("ctx", `${m[0]}; ctx.f = formatValue;`)(ctx);
  assert.equal(ctx.f("200 000.00 GBP", "pcs"), "£200,000");
  assert.equal(ctx.f("1,250,000.40 EUR", "etenders_ie"), "€1,250,000");
  assert.equal(ctx.f("100,000", "contracts_finder"), "£100,000");
  assert.equal(ctx.f("TBC", "pcs"), "TBC");
});

test("T8: words from the company's legal details are not suggested as searches", () => {
  const m = app.match(/const NOT_SOLD_WORDS = new Set\(\[(.*?)\]\);/);
  assert.ok(m && m[1].includes('"registered"'));
});

test("An active framework with no deadline says so", () => {
  assert.ok(app.includes("Open framework, no deadline published"));
});

test("TN3: the closed-hidden count does not depend on the weaker-matches toggle", () => {
  const i = app.indexOf("Counted over the same set whether or not the weaker matches are shown");
  assert.ok(i > 0 && app.slice(i, i + 700).includes("state.showLooseMatches = true;"));
  assert.ok(app.includes("state.showLooseMatches = looseWas;"));
});

test("Re-test 5 TN2: a title-only portal hit with none of the words stays in the list, ranked last", () => {
  const i = app.indexOf('else if (level === "none" && !hasDesc)');
  assert.ok(i > 0, "the title-only branch exists");
  assert.ok(app.slice(i, i + 500).includes("row._partial = true"), "it becomes a partial match, not a hidden loose one");
  assert.ok(/\} else if \(level === "none"\) \{\s+if \(!state\.showLooseMatches\) return false;/.test(app), "a row whose own description lacks the words is still collapsed");
});

console.log(`\n${passed} of ${passed + failed} passed`);
process.exitCode = failed ? 1 : 0;
