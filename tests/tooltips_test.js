"use strict";
/* Run: node tests/tooltips_test.js
   The hover help for buttons (web/tooltips.js): where each tip comes from and in what order, that every button written in
   index.html has one, that the tips never contain markup, and that the Growth Studio / Outreach buttons the other scripts
   draw are covered too. No browser: elements are small stand-ins. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const T = require(path.join(__dirname, "..", "web", "tooltips.js"));

// a stand-in for a DOM element with the few things resolve() and apply() read
function el(attrs = {}, text = "", id = "") {
  const a = { ...attrs };
  return {
    id,
    dataset: Object.fromEntries(Object.entries(a).filter(([k]) => k.startsWith("data-")).map(([k, v]) => [k.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase()), v])),
    textContent: text,
    getAttribute: (n) => (n in a ? a[n] : null),
    setAttribute: (n, v) => { a[n] = String(v); if (n.startsWith("data-")) this_ds(a, n, v); },
    hasAttribute: (n) => n in a,
    removeAttribute: (n) => { delete a[n]; },
    _a: a,
  };
}
function this_ds() { /* resolve() reads dataset only for the grouping values, which are set up front */ }

const tests = [];
const test = (n, f) => tests.push([n, f]);

test("a button's own title is its tip, then written data-tip, then the id, the action, the label and last the aria-label", () => {
  assert.equal(T.resolve(el({ title: "Mine", "data-action": "in-delete" }, "Delete", "btnLogout")), "Mine");
  assert.equal(T.resolve(el({ "data-tip": "Written by hand", "data-action": "in-delete" }, "Delete", "btnLogout")), "Written by hand");
  assert.equal(T.resolve(el({}, "Logout", "btnLogout")), T.BY_ID.btnLogout);
  assert.equal(T.resolve(el({ "data-action": "in-undo" }, "Undo")), T.BY_ACTION["in-undo"]);
  assert.equal(T.resolve(el({}, "🗂 Save to Answer Bank")), T.BY_LABEL["save to answer bank"]);
  assert.equal(T.resolve(el({ "aria-label": "Open the thing" }, "")), "Open the thing");
  assert.equal(T.resolve(el({}, "Some button nobody wrote help for")), "");
  assert.equal(T.resolve(null), "");
  assert.equal(T.resolve({}), "");
});

test("buttons that share an action are told apart by decision, panel and filter", () => {
  const d = (decision) => T.resolve(el({ "data-action": "ch-post-decide", "data-decision": decision }, "x"));
  assert.match(d("approve"), /Publish this post/);
  assert.match(d("reject"), /Reject this post/);
  assert.match(d("cancel"), /Cancel this post/);
  assert.notEqual(d("approve"), d("reject"));
  const p = (panel) => T.resolve(el({ "data-action": "ch-panel", "data-panel": panel }, "x"));
  assert.deepEqual(["approve", "edit", "ai", "time"].map(p).filter(Boolean).length, 4);
  assert.equal(new Set(["approve", "edit", "ai", "time"].map(p)).size, 4);
  const f = (filter) => T.resolve(el({ "data-action": "in-filter", "data-filter": filter }, "x"));
  assert.equal(new Set(["replies", "sent", "bounced", "all"].map(f)).size, 4);
});

test("a visually hidden button (the form's invisible submit) is left alone", () => {
  const hidden = el({ class: "visually-hidden" }, "Apply");
  hidden.classList = { contains: (c) => c === "visually-hidden" };
  assert.equal(T.apply(hidden), false);
  assert.ok(!("data-tip" in hidden._a));
});

test("close buttons of any look say Close, whatever they are called", () => {
  for (const t of ["×", "✕", "&times;", "x"]) assert.equal(T.resolve(el({}, t)), "Close this.", t);
  assert.equal(T.resolve(el({ "aria-label": "Close history" }, "×")), "Close this.");
});

test("a table lookup is by the label's words only: emoji, symbols, capitals and spacing do not matter", () => {
  assert.equal(T.norm("🗂 Save to Answer Bank"), "save to answer bank");
  assert.equal(T.norm("＋ Add Document"), "add document");
  assert.equal(T.norm("  CLEAR  ALL "), "clear all");
  assert.equal(T.resolve(el({}, "  ✨ Enrich ")), T.BY_LABEL.enrich);
  assert.equal(T.resolve(el({}, "constructor")), "", "an object's own methods are not tips");
  assert.equal(T.resolve(el({ "data-action": "__proto__" }, "")), "");
  assert.equal(T.resolve(el({ "data-action": "toString" }, "")), "");
});

test("apply() stores the tip in data-tip, drops the slow native title and names an icon-only button", () => {
  const withTitle = el({ title: "Collapse sidebar" }, "");
  assert.equal(T.apply(withTitle), true);
  assert.equal(withTitle._a["data-tip"], "Collapse sidebar");
  assert.ok(!("title" in withTitle._a), "the native tooltip would show twice");
  assert.equal(withTitle._a["aria-label"], "Collapse sidebar", "an icon-only button keeps an accessible name");
  assert.ok(!("data-tip-auto" in withTitle._a), "a tip the page wrote is never replaced");
  const labelled = el({ "aria-label": "Account menu" }, "");
  const table = el({}, "Logout", "btnLogout");
  T.apply(table);
  assert.equal(table._a["data-tip"], T.BY_ID.btnLogout);
  assert.equal(table._a["data-tip-auto"], "1", "a tip from a table is marked so it can be looked up again");
  assert.ok(!("aria-label" in table._a), "a button with text needs no aria-label");
  T.apply(labelled);
  assert.equal(labelled._a["aria-label"], "Account menu", "an existing aria-label is kept");
  assert.equal(T.apply(el({}, "No help written")), false);
  const sym = el({}, "×", "buyerDrawerClose");
  T.apply(sym);
  assert.equal(sym._a["aria-label"], "Close the buyer's details", "a symbol-only button gets a name a screen reader can say");
});

test("a table tip is looked up again when the button changes, a hand-written one is not", () => {
  const b = el({ "data-action": "in-undo" }, "Undo");
  T.apply(b);
  assert.equal(b._a["data-tip"], T.BY_ACTION["in-undo"]);
  b._a["data-action"] = "in-more";
  b.dataset.action = "in-more";
  T.apply(b);
  assert.equal(b._a["data-tip"], T.BY_ACTION["in-more"]);
  const hand = el({ "data-tip": "Hand written" }, "Undo");
  hand._a["data-action"] = "in-undo";
  T.apply(hand);
  assert.equal(hand._a["data-tip"], "Hand written");
});

test("every tip is one short plain sentence: no markup, no stray whitespace, nothing too long", () => {
  const all = [...Object.values(T.BY_ID), ...Object.values(T.BY_ACTION), ...Object.values(T.BY_LABEL)];
  assert.ok(all.length > 300, "the tables are not empty: " + all.length);
  for (const tip of all) {
    assert.ok(!/[<>]/.test(tip), "markup in: " + tip);
    assert.equal(tip, tip.trim(), "whitespace in: " + tip);
    assert.ok(tip.length >= 6 && tip.length <= 140, `length ${tip.length}: ${tip}`);
    assert.ok(/[.!?]$/.test(tip), "no full stop: " + tip);
  }
});

// ── the buttons that are written in index.html ──────────────────────────────────────────────────
const html = fs.readFileSync(path.join(__dirname, "..", "web", "index.html"), "utf8");
const staticButtons = [...html.matchAll(/<button\b([^>]*)>([\s\S]*?)<\/button>/g)].map((m) => {
  const attrs = {};
  for (const a of m[1].matchAll(/([a-zA-Z-]+)="([^"]*)"/g)) attrs[a[1]] = a[2].replace(/&amp;/g, "&");
  return { attrs, id: attrs.id || "", text: m[2].replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim() };
});

test("every button written in index.html gets a tip", () => {
  assert.ok(staticButtons.length > 150, "found the buttons: " + staticButtons.length);
  const missing = staticButtons.filter((b) => !/visually-hidden/.test(b.attrs.class || "") && !T.resolve(el(b.attrs, b.text, b.id))).map((b) => `${b.id || "(no id)"} "${b.text}"`);
  assert.deepEqual(missing, [], "no tip for:\n  " + missing.join("\n  "));
});

test("every id in the table is a real button in index.html (a typo would silently give nothing)", () => {
  const ids = new Set(staticButtons.map((b) => b.id));
  const unknown = Object.keys(T.BY_ID).filter((id) => !ids.has(id));
  assert.deepEqual(unknown, [], "ids that are not buttons: " + unknown.join(", "));
});

// ── the buttons the scripts draw ────────────────────────────────────────────────────────────────
const webDir = path.join(__dirname, "..", "web");
const drawn = new Set();
for (const f of ["growth-view.js", "growth-studio.js", "growth-channels.js", "growth-inbox.js", "email-outreach.js"]) {
  const src = fs.readFileSync(path.join(webDir, f), "utf8");
  for (const m of src.matchAll(/data-action="([a-z][a-z0-9-]*)"/g)) drawn.add(m[1]);
}
test("every Growth Studio, Social, Calls and Inbox action has a tip (or is a tab, which the id covers)", () => {
  const SKIP = new Set(["tab", "target-include", "toggle-select", "ch-post-decide", "ch-panel", "in-filter"]); // tabs by id; checkboxes are not buttons; // told apart by another attribute, tested above
  const missing = [...drawn].filter((a) => !SKIP.has(a) && !T.BY_ACTION[a]);
  assert.deepEqual(missing, [], "actions without a tip: " + missing.join(", "));
});

test("the Growth Studio buttons that matter most say what they will do", () => {
  assert.match(T.BY_ACTION["in-delete"], /Outreach keeps its own record/);
  assert.match(T.BY_ACTION["ch-post-create"], /Nothing is published until you approve/);
  assert.match(T.BY_ACTION["ch-call-make"], /rings this number straight away/);
  assert.match(T.BY_ACTION["mark-sent"], /sends nothing/i);
  assert.match(T.BY_ACTION["outreach-send"], /ticked buyer/);
  assert.match(T.BY_ID.btnTestEmailAlert, /really sends an email/);
});

let failed = 0;
for (const [name, fn] of tests) {
  try {
    fn();
    console.log("ok    " + name);
  } catch (err) {
    failed++;
    console.log("FAIL  " + name + "\n" + err.message);
  }
}
console.log(`\n${tests.length - failed} of ${tests.length} passed`);
process.exit(failed ? 1 : 0);
