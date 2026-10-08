"use strict";
/* Run: node tests/portal_prefs_test.js   (no packages needed; Node 18 or later)

   web/portal-prefs.js holds the two rules behind "which portals am I searching". Round 27: a saved portal
   choice came back different in the next session. The county filter re-chose the portals every time the
   saved counties were restored, and a portal added to the catalogue turned itself on. These tests pin the
   rules that stop both. */
const assert = require("node:assert/strict");
const path = require("node:path");

const P = require(path.join(__dirname, "..", "web", "portal-prefs.js"));

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

const ALL = ["etenders_ie", "find_tender", "contracts_finder", "etenders_ni", "pcs", "sell2wales", "procontract", "gca_agreements"];
const NEW = ["gca_agreements"];
const THREE = ["find_tender", "contracts_finder", "procontract"];

test("an existing user on 'all portals' keeps what they have seen: the new portal stays off", () => {
  const r = P.reconcileCatalogue({ allIds: ALL, newIds: NEW, known: null, selected: null, hasSavedPrefs: true });
  assert.deepEqual(r.selected, ALL.filter((id) => id !== "gca_agreements"));
  assert.deepEqual(r.known, ALL);
  assert.equal(r.changed, true);
});

test("an existing user's explicit choice is left exactly as it was", () => {
  const r = P.reconcileCatalogue({ allIds: ALL, newIds: NEW, known: null, selected: THREE, hasSavedPrefs: true });
  assert.deepEqual(r.selected, THREE);
  assert.deepEqual(r.known, ALL, "it is remembered as shown, so it is not 'new' again");
  assert.equal(r.changed, true, "the known list needs saving once");
  const withGca = P.reconcileCatalogue({ allIds: ALL, newIds: NEW, known: null, selected: [...THREE, "gca_agreements"], hasSavedPrefs: true });
  assert.deepEqual(withGca.selected, [...THREE, "gca_agreements"], "a portal the user already chose is kept");
});

test("a brand-new account gets every portal", () => {
  const r = P.reconcileCatalogue({ allIds: ALL, newIds: NEW, known: null, selected: null, hasSavedPrefs: false });
  assert.equal(r.selected, null);
  assert.deepEqual(r.known, ALL);
  assert.equal(r.changed, true);
});

test("once the catalogue is known nothing changes on later visits", () => {
  for (const selected of [null, THREE, ALL]) {
    const r = P.reconcileCatalogue({ allIds: ALL, newIds: NEW, known: ALL, selected, hasSavedPrefs: true });
    assert.equal(r.selected, selected);
    assert.equal(r.changed, false);
  }
});

test("a portal added later is off for everyone who has already seen the catalogue", () => {
  const grown = [...ALL, "newest_portal"];
  const pinned = P.reconcileCatalogue({ allIds: grown, newIds: [], known: ALL, selected: null, hasSavedPrefs: true });
  assert.deepEqual(pinned.selected, ALL, "'all' does not quietly grow");
  assert.deepEqual(pinned.known, grown);
  assert.equal(pinned.changed, true);
  const explicit = P.reconcileCatalogue({ allIds: grown, newIds: [], known: ALL, selected: THREE, hasSavedPrefs: true });
  assert.deepEqual(explicit.selected, THREE);
  assert.equal(explicit.changed, true, "only the known list is saved");
  const again = P.reconcileCatalogue({ allIds: grown, newIds: [], known: grown, selected: explicit.selected, hasSavedPrefs: true });
  assert.equal(again.changed, false);
});

const COUNTIES = [
  ...["Kent", "Essex", "Surrey"].map((name) => ({ name, region: "England" })),
  ...["Fife", "Moray"].map((name) => ({ name, region: "Scotland" })),
  { name: "Cardiff", region: "Wales" }, { name: "Antrim", region: "Northern Ireland" }, { name: "Cork", region: "Ireland" },
];
const REGION_PORTAL_IDS = {
  Ireland: ["etenders_ie"],
  England: ["find_tender", "contracts_finder", "gca_agreements", "procontract"],
  Scotland: ["find_tender", "contracts_finder", "gca_agreements", "pcs"],
  Wales: ["find_tender", "contracts_finder", "gca_agreements", "sell2wales"],
  "Northern Ireland": ["find_tender", "contracts_finder", "gca_agreements", "etenders_ni"],
};
const region = (selectedCounties, currentSelected, optInIds = NEW) => P.regionPortals({ selectedCounties, counties: COUNTIES, regionPortalIds: REGION_PORTAL_IDS, currentSelected, optInIds });

test("regionPortals narrows the portals to the nations of the chosen counties", () => {
  const scotland = region(["Fife", "Moray"], null);
  assert.deepEqual([...scotland.portalIds].sort(), ["contracts_finder", "find_tender", "gca_agreements", "pcs"]);
  assert.deepEqual([...scotland.regions], ["Scotland"]);
  const two = region(["Kent", "Fife"], null);
  assert.deepEqual([...two.portalIds].sort(), ["contracts_finder", "find_tender", "gca_agreements", "pcs", "procontract"]);
  assert.deepEqual([...two.regions].sort(), ["England", "Scotland"]);
});

test("regionPortals never switches a new portal on for a user who has not chosen it", () => {
  const england = region(["Kent", "Essex"], THREE);
  assert.deepEqual([...england.portalIds].sort(), ["contracts_finder", "find_tender", "procontract"], "GCA stays off");
  const chosen = region(["Kent"], [...THREE, "gca_agreements"]);
  assert.ok(chosen.portalIds.has("gca_agreements"), "kept when the user already has it");
  const everything = region(["Kent"], null);
  assert.ok(everything.portalIds.has("gca_agreements"), "'all portals' includes it, as before");
  assert.ok(region(["Kent"], THREE, []).portalIds.has("gca_agreements"), "with nothing marked new it behaves as before");
});

test("regionPortals narrows nothing for an empty selection or one touching every nation", () => {
  assert.equal(region([], null).portalIds, null);
  assert.equal(region(undefined, null).portalIds, null);
  assert.equal(region(COUNTIES.map((c) => c.name), null).portalIds, null);
  assert.equal(region(["Atlantis"], null).portalIds, null, "names that are not counties are ignored");
});

test("regionKey is stable", () => {
  assert.equal(P.regionKey(new Set(["b", "a"])), "a,b");
  assert.equal(P.regionKey(null), null);
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try {
      await fn();
      console.log(`ok    ${name}`);
    } catch (err) {
      failed += 1;
      console.log(`FAIL  ${name}`);
      console.log(err && err.stack ? err.stack : err);
    }
  }
  console.log(`\n${tests.length - failed} of ${tests.length} passed`);
  process.exitCode = failed ? 1 : 0;
})();
