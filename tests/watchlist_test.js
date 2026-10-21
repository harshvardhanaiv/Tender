// Run: node tests/watchlist_test.js   (plain asserts; no browser, no network)
const assert = require("assert");
const W = require("../web/watchlist.js");

(function buttons() {
  W._setItems([{ kind: "supplier", entity_key: "42", name: "Acme", added_from: "growth_studio" }]);
  const on = W.buttonHtml("supplier", 42, "Acme", "supplier_intelligence", { label: true });
  assert(on.includes("is-on") && on.includes("★") && on.includes("Watching") && on.includes('aria-pressed="true"'));
  const off = W.buttonHtml("buyer", "Leeds City Council", 'Leeds "City" <Council>', "buyer_intelligence");
  assert(!off.includes("is-on") && off.includes("☆") && !off.includes("Watching"));
  assert(off.includes('data-key="LEEDS CITY COUNCIL"'), "buyers are keyed upper-case");
  assert(off.includes("&quot;City&quot;") && off.includes("&lt;Council&gt;"), "names are escaped");
})();

(function lookups() {
  W._setItems([{ kind: "buyer", entity_key: "LEEDS CITY COUNCIL", name: "Leeds City Council", added_from: "buyer_intelligence" }]);
  assert(W.has("buyer", "  leeds city council "));
  assert(!W.has("supplier", "LEEDS CITY COUNCIL"), "a buyer is not a supplier");
  assert.strictEqual(W.sourceLabel("growth_studio"), "Growth Studio");
  assert.strictEqual(W.sourceLabel("whatever"), "Other");
})();

console.log("watchlist_test: ok");
