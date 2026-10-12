// Comma search terms, the browser results cache, and combining ticked saved/recent chips.
const assert = require("node:assert/strict");
const C = require("../web/search-cache.js");

const tests = [];
const test = (n, f) => tests.push([n, f]);
const row = (id, matched) => ({ resource_id: id, title: "T" + id, matched_terms: matched });

test("splitTerms matches the server rule", () => {
  assert.deepEqual(C.splitTerms("software, dashboard"), ["software", "dashboard"]);
  assert.deepEqual(C.splitTerms("  Software ,, software ; Cyber Security "), ["software", "cyber security"]);
  assert.deepEqual(C.splitTerms("supplier: acme, beta"), ["acme", "beta"]);
  assert.deepEqual(C.splitTerms("software development"), ["software development"]);
  assert.deepEqual(C.splitTerms("all"), []);
  assert.equal(C.splitTerms("a1,a2,a3,a4,a5,a6,a7").length, 5);
});

test("a stored search comes back at once, for the same scope and counties only", () => {
  const m = new Map();
  C._storeIn(m, "software", "all", "Essex/", [row(1), row(2)], 2, 1000);
  assert.equal(C._lookupIn(m, "Software", "all", "Essex/", 2000, 10000).rows.length, 2);
  assert.equal(C._lookupIn(m, "software", "all", "Kent/", 2000, 10000), null, "another county choice is another search");
  assert.equal(C._lookupIn(m, "software", "pcs", "Essex/", 2000, 10000), null);
  assert.equal(C._lookupIn(m, "software", "all", "Essex/", 20000, 10000), null, "too old");
});

test("a comma search is stored per term and a combination is assembled from earlier searches", () => {
  const m = new Map();
  C._storeIn(m, "software, dashboard", "all", "", [row(1, ["software"]), row(2, ["software", "dashboard"]), row(3, ["dashboard"])], 3, 1000);
  assert.equal(C._lookupIn(m, "software", "all", "", 1500, 10000).rows.length, 2);
  assert.equal(C._lookupIn(m, "dashboard", "all", "", 1500, 10000).rows.length, 2);
  // another order of the same terms is the same search
  assert.equal(C._lookupIn(m, "dashboard, software", "all", "", 1500, 10000).rows.length, 3);
  // a new combination built from two single-term entries; rows found by both come first
  const m2 = new Map();
  C._storeIn(m2, "software", "all", "", [row(1), row(2)], 2, 1000);
  C._storeIn(m2, "cyber", "all", "", [row(2), row(3)], 2, 1000);
  const hit = C._lookupIn(m2, "software, cyber", "all", "", 1500, 10000);
  assert.equal(hit.assembled, true);
  assert.deepEqual(hit.rows.map((r) => r.resource_id), [2, 1, 3]);
  assert.equal(C._lookupIn(m2, "software, cyber, cloud", "all", "", 1500, 10000), null, "one term was never searched");
});

test("combineChips joins ticked searches into one comma search", () => {
  const out = C.combineChips([{ q: "software", scope: "all" }, { q: "Dashboard, software", scope: "all" }, { q: "cyber security", scope: "all" }]);
  assert.equal(out.q, "software, dashboard, cyber security");
  assert.equal(out.scope, "all");
  assert.equal(out.mixedScopes, false);
  const mixed = C.combineChips([{ q: "a", scope: "title" }, { q: "b", scope: "supplier" }]);
  assert.equal(mixed.scope, "all");
  assert.equal(mixed.mixedScopes, true);
  assert.equal(C.combineChips([{ q: "a", scope: "title" }, { q: "b", scope: "title" }]).scope, "title");
});

test("termCounts counts rows per term", () => {
  const rows = [row(1, ["a"]), row(2, ["a", "b"])];
  const out = C.termCounts(rows, ["a", "b"], (r, t) => r.matched_terms.includes(t));
  assert.deepEqual(out, [{ term: "a", count: 2 }, { term: "b", count: 1 }]);
});

(async () => {
  let failed = 0;
  for (const [n, f] of tests) {
    try { await f(); console.log(`ok    ${n}`); } catch (e) { failed += 1; console.log(`FAIL  ${n}`); console.log(e.stack || e); }
  }
  console.log(`${tests.length - failed} of ${tests.length} passed`);
  process.exit(failed ? 1 : 0);
})();
