"use strict";
/* Run: node tests/bid_todo_test.js
   The bid page's "Things to confirm" list: every bracketed gap in the draft listed once, and resolving one puts the value
   in every place it appears. Cuts the real functions out of web/app.js. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const app = fs.readFileSync(path.join(__dirname, "..", "web", "app.js"), "utf8").replace(/\r\n/g, "\n");
function cut(name) {
  const at = app.indexOf("function " + name + "(");
  assert.ok(at >= 0, name + " is not in app.js");
  const i = app.indexOf("{", app.indexOf(")", at));
  let depth = 0;
  for (let j = i; j < app.length; j++) {
    if (app[j] === "{") depth++;
    else if (app[j] === "}" && --depth === 0) return app.slice(at, j + 1);
  }
  throw new Error(name);
}
const re = /^const _TODO_RE = .*$/m.exec(app);
assert.ok(re, "_TODO_RE is not in app.js");
const ctx = vm.createContext({});
vm.runInContext([re[0], cut("_todoSections"), cut("collectBidTodos"), cut("resolveBidTodoText"), "this.c = collectBidTodos; this.r = resolveBidTodoText;"].join("\n"), ctx);

const tests = [];
const test = (n, f) => tests.push([n, f]);
const draft = () => ({ bid_response: {
  cover_letter: { content: "Dear [NEEDS INPUT: buyer contact name], we hold [CONFIRM: ISO 9001 - from a past bid]. See [1] and [x] and [a link](http://x)." },
  methodology: { content: "Turnover is [CONFIRM: GBP 2.1m] and we hold [CONFIRM: ISO 9001 - from a past bid]." },
  itt_question_responses: [{ question_number: "5", response: "Lead: [NEEDS INPUT: account manager]. [Add 1-2 case studies]" }] } });

test("each gap is listed once, with every section it appears in, and inputs come first", () => {
  const items = ctx.c(draft());
  assert.equal(items.length, 5);
  assert.deepEqual(Array.from(items.map(i => i.kind)), ["input", "input", "confirm", "past", "other"]);
  const iso = items.find(i => i.kind === "past");
  assert.equal(iso.count, 2);
  assert.deepEqual(Array.from(iso.sections), ["Cover letter", "Methodology"]);
  assert.equal(iso.value, "ISO 9001");
});

test("links, checkboxes and footnote numbers are not gaps", () => {
  const tags = ctx.c(draft()).map(i => i.tag);
  assert.ok(!tags.some(t => t === "[1]" || t === "[x]" || t.includes("a link")));
});

test("resolving a gap puts the value in every place and the gap leaves the list", () => {
  const p = draft();
  assert.equal(ctx.r(p, "[CONFIRM: ISO 9001 - from a past bid]", "ISO 9001:2015"), 2);
  assert.ok(p.bid_response.cover_letter.content.includes("ISO 9001:2015") && p.bid_response.methodology.content.includes("ISO 9001:2015"));
  assert.equal(ctx.c(p).length, 4);
});

test("a typed value cannot break a table and an empty value changes nothing", () => {
  const p = draft();
  assert.equal(ctx.r(p, "[NEEDS INPUT: account manager]", "  "), 0);
  ctx.r(p, "[NEEDS INPUT: account manager]", "Sam | Lee\nSmith");
  assert.ok(p.bid_response.itt_question_responses[0].response.includes("Sam / Lee Smith"));
});

test("the card is built after the prices card and its buttons are wired", () => {
  const a = app.indexOf("try { renderBidPricesCard(); }"), b = app.indexOf("try { renderBidTodosCard(); }");
  assert.ok(a > 0 && b > a && b - a < 400, "the to-do card is drawn right after the prices card");
  assert.ok(app.includes("#bidTodoCard .bid-todo__put, #bidTodoCard .bid-todo__go"));
});

test("the sticky bar has a count pill that jumps to the card, and the readiness link goes there too", () => {
  assert.ok(app.includes('id="bidTodoPill"') && app.includes('e.target.closest("#bidTodoPill")) _scrollToTodoCard()'));
  assert.ok(app.includes("pill.textContent = _todoPillText(items.length)"), "the pill follows the list");
  assert.ok(/things to confirm\$\/i\.test\(String\(label\)\.trim\(\)\)\) \{ _scrollToTodoCard\(\)/.test(app), "gotoBidSection knows the list");
});

test("typed values and prices are saved on the server and read back once per bid", () => {
  assert.ok(app.includes('"/api/bid-ui-state"') && app.includes('"/api/bid-ui-state?bid_key="'));
  assert.ok(/function saveBidPrices\(v, fromServer\)[\s\S]{0,260}pushBidUiState\(\{ prices: v \}\)/.test(app), "prices go to the server");
  assert.ok(app.includes("pushBidUiState({ todo_drafts: _todoDrafts })"), "typed values go to the server");
  assert.ok(app.indexOf("ensureBidUiState();   //") < app.indexOf("renderBidPricesCard(); } catch"), "state is reset before the cards are drawn");
});

let failed = 0;
for (const [n, f] of tests) { try { f(); console.log("ok   " + n); } catch (e) { failed++; console.log("FAIL " + n + "\n" + (e.stack || e)); } }
console.log(`\n${tests.length - failed} of ${tests.length} passed`);
process.exit(failed ? 1 : 0);
