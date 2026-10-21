// Run: node tests/growth_services_view_test.js   (the picker's pure helpers; the widget itself needs a page)
const assert = require("assert");
const S = require("../web/growth-services.js");

const presets = [
  { preset: "cyber-security", label: "Cyber security", hint: "Security testing", group: "Technology" },
  { preset: "housing-repairs-gas", label: "Housing repairs & gas servicing", hint: "Boilers", group: "Housing & property" },
  { preset: "legal-services", label: "Legal services", hint: "Legal advice", group: "Professional services" },
];
const services = [
  { id: "service:1", label: "Pool maintenance", keywords: ["swimming pool"], cpv: ["5041"], mine: true, visibility: "private", group: "Your services" },
  { id: "service:2", label: "Lido upkeep", keywords: ["lido"], cpv: [], mine: false, visibility: "shared", group: "Shared by other users" },
];
const items = S.catalogue(presets, services);

assert.deepStrictEqual(S.idsOf("a, b ,,service:3"), ["a", "b", "service:3"]);
assert.strictEqual(S.presetOf(["a", "b"]), "a,b");
assert.strictEqual(items.length, 5);
assert.strictEqual(items.find((i) => i.id === "service:1").hint, "swimming pool, CPV 5041");

const all = S.grouped(items, "");
assert.deepStrictEqual(all.map((g) => g.group), ["Your services", "Technology", "Housing & property", "Professional services", "Shared by other users"], "your own first, shared last");
const f = S.grouped(items, "pool");
assert.deepStrictEqual(f.map((g) => g.items.map((i) => i.id)), [["service:1"]], "matches the keyword text; empty groups drop out");
assert.deepStrictEqual(S.grouped(items, "gas boiler").flatMap((g) => g.items.map((i) => i.id)), ["housing-repairs-gas"], "every word must match");
assert.deepStrictEqual(S.grouped(items, "zzz"), []);

assert.strictEqual(S.summary([], items), "Choose services…");
assert.strictEqual(S.summary(["cyber-security"], items), "Cyber security");
assert.strictEqual(S.summary(["cyber-security", "service:1"], items), "2 services");
assert.strictEqual(S.summary(["service:99"], items), "service:99", "an unknown id is shown rather than hidden");

assert.deepStrictEqual(S.toggle(["a"], "b", 3), ["a", "b"]);
assert.deepStrictEqual(S.toggle(["a", "b"], "a", 3), ["b"]);
assert.deepStrictEqual(S.toggle(["a", "b", "c"], "d", 3), ["a", "b", "c"], "the cap holds");
console.log("growth_services_view_test: ok");
