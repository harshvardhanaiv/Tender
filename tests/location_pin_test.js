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

// ── wiring rules that were wrong (checked on the source: the panels need a browser to run) ──
const fs = require("fs");
const path = require("path");
const read = (f) => fs.readFileSync(path.join(__dirname, "..", "web", f), "utf8").replace(/\r\n/g, "\n");
const pinSrc = read("location-pin.js");
const planSrc = read("planning.js");
// the map dialog moves in the panel's own steps (25 miles), so the circle drawn is the distance applied
assert(/stepMiles: r && parseFloat\(r\.step\) > 0 \? parseFloat\(r\.step\) : 1/.test(pinSrc), "the panel's step is passed to the dialog");
assert(/min="' \+ step \+ '" max="' \+ maxMiles \+ '" step="' \+ step \+ '"/.test(pinSrc), "the dialog slider uses it");
// a place is remembered only when the reader chose it in that panel; the page, another panel and ✕ never are
assert(/if \(Date\.now\(\) < quietUntil \|\| !touched\[p\.name\]\) return;/.test(pinSrc));
assert(/v === "custom" \|\| v === "pin"/.test(pinSrc), "a place that is already the pin is not learned again");
assert(/touched = \{\}; quietUntil = Date\.now\(\) \+ 2000;\s*forget\(\);/.test(pinSrc), "✕ must not be undone by the status line it rewrites");
// one ✕ list for all three panels, refreshed when the user's own storage key becomes known and after the panels are set up
assert(/function syncClears\(\)/.test(pinSrc) && /keyFn = opts\.storageKey; syncClears\(\)/.test(pinSrc) && /\n    syncClears\(\);\n  \}/.test(pinSrc));
// Planning leads treats a pin like GPS: the hidden lat/lng are the place (it used to fall back to London)
assert(/cityVal !== "gps" && cityVal !== "pin"/.test(planSrc));
assert(/else if \(val === "pin"\)/.test(planSrc));
console.log("location_pin_test: wiring ok");
