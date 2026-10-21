"use strict";
/* Run: node tests/recent_views_test.js   (fake storage; the recently viewed list behind History) */
const assert = require("node:assert/strict");
const R = require("../web/recent-views.js");

const store = {};
const storage = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } };
R.configure({ storage, storageKey: () => "tf_u1_recent_views" });

assert.deepEqual(R.list(), []);
assert.ok(R.record("tender", "t1", "Boiler servicing", "Leeds City Council", { title: "Boiler servicing" }, 1000));
assert.ok(R.record("buyer", "LEEDS", "Leeds City Council", "", null, 2000));
assert.ok(R.record("supplier", "42", "Acme Ltd", "", null, 3000));
assert.deepEqual(R.list().map((e) => e.key), ["42", "LEEDS", "t1"], "newest first");
assert.deepEqual(R.list("buyer").map((e) => e.key), ["LEEDS"]);

R.record("tender", "t1", "Boiler servicing", "Leeds City Council", null, 4000);
const top = R.list()[0];
assert.deepEqual([top.key, top.views], ["t1", 2], "a repeat view moves it up and counts it");
assert.deepEqual(top.payload, { title: "Boiler servicing" }, "the stored payload survives a repeat view without one");
assert.equal(R.list().length, 3, "no duplicate");

assert.equal(R.record("tender", "", "x"), false);
assert.equal(R.record("company", "k", "x"), false, "unknown kind");
assert.equal(R.record("tender", "k", "   "), false, "no title");

for (let i = 0; i < 60; i++) R.record("supplier", "s" + i, "Supplier " + i, "", null, 10000 + i);
assert.equal(R.list("supplier").length, 40, "each kind keeps its newest 40");
assert.equal(R.list("buyer").length, 1, "a busy kind does not push the others out");
assert.equal(R.list("supplier")[0].key, "s59");

R.forget("buyer", "LEEDS");
assert.equal(R.list("buyer").length, 0);
store["tf_u1_recent_views"] = "{not json";
assert.deepEqual(R.list(), [], "corrupt storage reads as empty");
R.record("tender", "a", "A", "", null, 1);
R.clear();
assert.deepEqual(R.list(), []);

const slim = R.slimRow({ source: "fts", resource_id: "1", title: "T", description: "d".repeat(5000), junk: "x", url: "https://x" });
assert.equal(slim.description.length, 600);
assert.ok(!("junk" in slim));
assert.equal(R.ago(Date.now() - 30000), "just now");
assert.equal(R.ago(Date.now() - 5 * 60000), "5 min ago");
assert.equal(R.ago(Date.now() - 26 * 3600000), "yesterday");
console.log("recent_views_test: ok");
