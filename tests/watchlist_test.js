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

// Adding and removing both say so with a toast (window.showToast); it used to be undefined, so nothing ever appeared.
(async function toasts() {
  const said = [];
  globalThis.showToast = (msg, type) => said.push([msg, type]);
  globalThis.document = { querySelectorAll: () => [] }; // the buttons on a page; there are none here
  globalThis.fetch = async (url, init) => (init && init.method === "POST"
    ? { ok: true, json: async () => ({ item: { kind: "buyer", entity_key: "ACME", name: "Acme", added_from: "buyer_intelligence" } }) }
    : { ok: true, json: async () => ({}) });
  W._setItems([]);
  assert.strictEqual(await W.add("buyer", "Acme", "Acme", "buyer_intelligence"), true);
  assert.deepStrictEqual(said.pop(), ["Added Acme to your watchlist (Buyer Intelligence)", "success"]);
  assert.strictEqual(await W.remove("buyer", "Acme"), true);
  assert.deepStrictEqual(said.pop(), ["Removed Acme from your watchlist", undefined]);
  assert(!W.has("buyer", "Acme"));
  globalThis.fetch = async () => ({ ok: false, json: async () => ({}) });
  W._setItems([{ kind: "buyer", entity_key: "ACME", name: "Acme", added_from: "x" }]);
  assert.strictEqual(await W.remove("buyer", "Acme"), false);
  assert.deepStrictEqual(said.pop(), ["Could not remove it from the watchlist", "error"]);
  assert(W.has("buyer", "Acme"), "a failed removal keeps it");
  console.log("watchlist_test: ok");
})().catch((e) => { console.error(e); process.exit(1); });
