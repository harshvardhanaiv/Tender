// Run: node tests/location_pin_test.js   (plain asserts; localStorage is a fake)
const assert = require("assert");
const store = {};
global.localStorage = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } };
const T = require("../web/location-pin.js");

T.configure({ storageKey: () => "tf_u1_last_location" });
assert.strictEqual(T.recall(), null, "nothing remembered at first");
assert(T.remember({ lat: 53.48, lon: -2.24, label: "Manchester" }));
assert.deepStrictEqual(T.recall(), { lat: 53.48, lon: -2.24, label: "Manchester" });
assert("tf_u1_last_location" in store);
T.configure({ storageKey: () => "tf_u2_last_location" });
assert.strictEqual(T.recall(), null, "another user does not see it");
T.configure({ storageKey: () => "tf_u1_last_location" });
for (const bad of [null, {}, { lat: 91, lon: 0, label: "x" }, { lat: 1, lon: 181, label: "x" }, { lat: "a", lon: 0, label: "x" }, { lat: 1, lon: 1, label: "" }]) {
  assert.strictEqual(T.remember(bad), false, JSON.stringify(bad));
}
assert.strictEqual(T.recall().label, "Manchester", "a bad value never overwrites a good one");
store["tf_u1_last_location"] = "{not json";
assert.strictEqual(T.recall(), null, "corrupt storage reads as nothing");
T.remember({ lat: 51.5, lon: -0.12, label: "London" }); T.forget();
assert.strictEqual(T.recall(), null);
console.log("location_pin_test: ok");
