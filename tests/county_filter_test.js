"use strict";
/* Run: node tests/county_filter_test.js   (no packages needed; Node 18 or later)

   The county filter decides which notices stay in an England-only (or Scotland-only ...) search.
   web/uk_counties.js matches county names in a notice's text, and (Round 27) also keeps a notice whose
   LOCATION names only a nation, such as Find a Tender's "UKD - North West (England)", when every county
   of that nation is selected. Without that, an England-only search lost every Find a Tender notice. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const ctx = { window: {}, console };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "web", "uk_counties.js"), "utf8"), ctx);
const { UK_COUNTIES, matchRowToCounties, fullySelectedNations } = ctx.window;

const tests = [];
const test = (name, fn) => tests.push([name, fn]);
const regionNames = (region) => UK_COUNTIES.filter((c) => c.region === region).map((c) => c.name);
const england = regionNames("England");
const scotland = regionNames("Scotland");
const row = (over) => ({ source: "find_tender", title: "Construction works", contracting_authority: "A Body", description: "", ...over });

test("the nations of the county list are the ones the filter knows about", () => {
  assert.ok(england.length >= 40 && scotland.length >= 25);
  const nations = (selected) => Array.from(fullySelectedNations(selected)); // arrays from the vm context have another prototype
  assert.deepEqual(nations(england), ["England"]);
  assert.deepEqual(nations([...england, ...scotland]), ["England", "Scotland"]);
  assert.deepEqual(nations(england.slice(1)), [], "one county short is not the whole nation");
  assert.deepEqual(nations(["Kent"]), []);
  assert.deepEqual(nations([]), []);
  assert.deepEqual(nations(undefined), []);
  assert.deepEqual(nations(regionNames("Ireland")), [], "Ireland has no nation hint: its notices name their county");
});

test("a Find a Tender notice located only by nation or region is kept when the whole nation is selected", () => {
  for (const location of ["UKD - North West (England)", "UKI - London", "UKC - North East (England)", "UKE - Yorkshire and the Humber",
    "UKH - East of England", "UKJ - South East (England)", "UKK - South West (England)", "UKF - East Midlands (England)", "UKG - West Midlands (England)", "England"]) {
    assert.equal(matchRowToCounties(row({ location }), england), true, location);
  }
});

test("it is not kept for a nation that is not fully selected, or a different nation", () => {
  assert.equal(matchRowToCounties(row({ location: "UKD - North West (England)" }), england.slice(1)), false, "one English county short");
  assert.equal(matchRowToCounties(row({ location: "UKD - North West (England)" }), ["Kent"]), false);
  assert.equal(matchRowToCounties(row({ location: "UKM - Scotland" }), england), false);
  assert.equal(matchRowToCounties(row({ location: "UKM - Scotland" }), scotland), true);
  assert.equal(matchRowToCounties(row({ location: "UKL - Wales" }), regionNames("Wales")), true);
  assert.equal(matchRowToCounties(row({ location: "UKN - Northern Ireland" }), regionNames("Northern Ireland")), true);
  assert.equal(matchRowToCounties(row({ location: "UKM - Scotland" }), [...england, ...scotland]), true);
});

test("only the location and region fields place a notice by nation", () => {
  assert.equal(matchRowToCounties(row({ title: "North West framework for England" }), england), true, "a notice with no location stated is kept in Location not stated group");
  assert.equal(matchRowToCounties(row({ description: "delivered across London", location: "Fife" }), england.filter((n) => n !== "Greater London")), false);
  assert.equal(matchRowToCounties(row({ region: "England" }), england), true, "the region field counts");
  assert.equal(matchRowToCounties(row({}), england), true, "a notice with no location at all is kept in Location not stated group");
});

test("county names in the text still place a notice, as before", () => {
  assert.equal(matchRowToCounties(row({ contracting_authority: "Kent County Council" }), ["Kent"]), true);
  assert.equal(matchRowToCounties(row({ contracting_authority: "Manchester City Council" }), ["Greater Manchester"]), true);
  assert.equal(matchRowToCounties(row({ contracting_authority: "Swan Housing", location: "Surrey" }), ["Kent"]), false, "a notice with a stated location in another county is hidden");
});

test("the portal rules are unchanged", () => {
  for (const source of ["procontract", "gca_agreements", "contracts_finder", "find_tender", "pcs", "sell2wales", "etenders_ni", "etenders_ie"]) {
    assert.equal(matchRowToCounties({ source, title: "Kent works" }, ["Kent"]), true, source);
  }
  assert.equal(matchRowToCounties({ source: "boamp", title: "Kent works" }, ["Kent"]), false, "a non-UK portal is hidden by a county filter");
  assert.equal(matchRowToCounties({ source: "brand_new_uk_portal", title: "Kent works" }, ["Kent"]), false, "so every new UK portal must be registered");
  assert.equal(matchRowToCounties({ source: "boamp", title: "x" }, []), true, "no counties selected means no filtering");
  assert.equal(matchRowToCounties({ source: "boamp", title: "x" }, UK_COUNTIES.map((c) => c.name)), true, "every county selected means no filtering");
  assert.equal(matchRowToCounties({ source: "boamp", location: "England" }, england), false, "the nation rule does not rescue a non-UK portal");
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
