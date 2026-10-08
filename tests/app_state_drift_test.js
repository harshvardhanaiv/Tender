"use strict";
/* Run: node tests/app_state_drift_test.js   (no packages needed; Node 18 or later)

   Round 27 QA found two things that changed between sessions without the user touching them: the company
   profile ("Civenta (Default)" came back) and the portal selection ("4 of 7": GCA Frameworks was switched
   on). Both are decided inside web/app.js, which cannot be loaded as a whole outside a browser. So this test
   cuts the REAL functions out of app.js by name (nothing is copied) and runs them in a small fake page, then
   checks what the page keeps, what it saves and what it never saves:

     portal selection   a saved selection survives the county list being restored; choosing counties during a
                        visit narrows the portals to those nations but never switches on a portal the user
                        has not chosen; a portal added to the catalogue starts off for an existing account
     company profile    a visit starts on the saved default, the pickers inside other screens never change that
                        default, and the filter-bar picker does                                                  */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const WEB = path.join(__dirname, "..", "web");
const read = (name) => fs.readFileSync(path.join(WEB, name), "utf8").replace(/\r\n/g, "\n");
const appSource = read("app.js");
const appLines = appSource.split("\n");

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

// ── cutting real code out of app.js ──────────────────────────────────────────────────────────
function cutFunction(name) {
  const start = appLines.findIndex((l) => new RegExp(`^(async )?function ${name}\\(`).test(l));
  assert.ok(start >= 0, `app.js has no top-level function ${name}()`);
  let end = start;
  while (end < appLines.length && !/^\}\s*;?\s*$/.test(appLines[end])) end += 1;
  assert.ok(end < appLines.length, `could not find where ${name}() ends`);
  return appLines.slice(start, end + 1).join("\n");
}
function cutStatement(firstLine) {
  const start = appLines.findIndex((l) => l.startsWith(firstLine));
  assert.ok(start >= 0, `app.js has no line starting ${firstLine}`);
  let end = start;
  if (!/;\s*$/.test(appLines[start])) while (!/^[\]})];\s*$/.test(appLines[end])) end += 1;
  return appLines.slice(start, end + 1).join("\n");
}

const FUNCTIONS = [
  "savePortalSelection", "reconcilePortalCatalogue", "countyRegionPortals",
  "applyCountyRegionPortalScoping", "populateFitProfileSelect", "autoSelectFitProfile", "handleCompanyProfileSelectionChange",
];
const CODE = [
  cutStatement("const PORTALS = ["),
  cutStatement("const NEW_PORTAL_IDS = "),
  cutStatement("const REGION_PORTAL_IDS = {"),
  ...FUNCTIONS.map(cutFunction),
].join("\n\n");
new vm.Script(CODE); // everything cut out must at least be valid on its own

// The fake page: only what those functions touch from the rest of the app. Everything the page saves is recorded in `log`.
const PRELUDE = `
const LS_PORTAL = "tf_selected_portals";
const LS_PROFILE = "tf_active_profile";
let _selectedPortals = null;
let _lastAppliedRegionPortalIds;
const state = { selectedUkCounties: [], userPrefs: {}, companyProfiles: [], activeProfileId: "" };
const log = { prefs: [], ls: [], fetches: [], toasts: [], modalOpened: 0, scopeHints: 0 };
const savePrefsToServer = (prefs) => { log.prefs.push(prefs); };
const lsSet = (key, value) => { log.ls.push([key, value]); };
const syncNativeScope = () => {};
const updateSearchPanelSummary = () => {};
const updatePortalScopeHint = () => { log.scopeHints += 1; };
const renderRightmoveFocusFilters = () => {};
const fetch = (url, opts) => { log.fetches.push([url, opts && opts.method]); return Promise.resolve({ ok: true }); };
const openCpModal = () => { log.modalOpened += 1; };
const cpShowToast = (message) => { log.toasts.push(message); };
const fitActive = () => Boolean(state.activeProfileId);
const updateFitControlsVisibility = () => {};
const updateProfileSelectHighlight = () => {};
const renderRows = () => {};
const updateResultsTotal = () => {};
const updateFiltersPanelSummary = () => {};
const refreshFitPanel = () => {};
const loadDeepFit = () => {};
const makeSelect = () => ({
  options: [], value: "", classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } }, offsetWidth: 0,
  set innerHTML(html) { this.options = []; },
  appendChild(option) { this.options.push(option); },
});
const selects = { fitProfileSelect: makeSelect(), rmCompanyProfileSelect: makeSelect() };
const $ = (id) => selects[id] || null;
const document = { createElement: () => ({ value: "", textContent: "" }) };
`;

function newPage() {
  const sandbox = { console, setTimeout: () => 0, JSON, Array, Set, Object, String, Boolean, Promise };
  sandbox.window = sandbox; // uk_counties.js and portal-prefs.js publish on window; the app reads them as globals
  const ctx = vm.createContext(sandbox);
  vm.runInContext(read("uk_counties.js"), ctx);
  vm.runInContext(read("portal-prefs.js"), ctx);
  vm.runInContext(PRELUDE + "\n" + CODE, ctx);
  const run = (code) => vm.runInContext(code, ctx);
  // arrays made inside the fake page have another realm's prototype, which assert.deepEqual rejects: copy them out as JSON
  const json = (code) => JSON.parse(run(`JSON.stringify(${code})`));
  const page = {
    run,
    json,
    selected: () => json("_selectedPortals ? [..._selectedPortals].sort() : null"),
    setSelected: (ids) => run(`_selectedPortals = ${ids ? `new Set(${JSON.stringify(ids)})` : "null"}`),
    setCounties: (names) => run(`state.selectedUkCounties = ${JSON.stringify(names)}`),
    setPrefs: (prefs) => run(`state.userPrefs = ${JSON.stringify(prefs)}`),
    setProfiles: (profiles, active = "") => run(`state.companyProfiles = ${JSON.stringify(profiles)}; state.activeProfileId = ${JSON.stringify(active)}`),
    log: (key) => json(`log.${key}`),
    savedPortalPrefs: () => page.log("prefs").filter((p) => "selected_portals" in p).map((p) => JSON.parse(p.selected_portals)),
    select: (id) => run(`selects.${id}`),
    activeProfile: () => run("state.activeProfileId"),
    defaults: () => json("state.companyProfiles.filter((p) => p.is_default).map((p) => String(p.id))"),
  };
  return page;
}

const countyNames = (region) => JSON.parse(vm.runInContext(`JSON.stringify(UK_COUNTIES.filter((c) => c.region === ${JSON.stringify(region)}).map((c) => c.name))`, (() => {
  const sandbox = { console };
  sandbox.window = sandbox;
  const ctx = vm.createContext(sandbox);
  vm.runInContext(read("uk_counties.js"), ctx);
  return ctx;
})()));
const england = countyNames("England");
const scotland = countyNames("Scotland");
const THREE = ["contracts_finder", "find_tender", "procontract"]; // what the QA account had chosen
const ALL_BUT_NEW = () => newPage().json("PORTALS.map((p) => p.id).filter((id) => !NEW_PORTAL_IDS.includes(id)).sort()");

// ── portal selection ─────────────────────────────────────────────────────────────────────────
test("the catalogue marks GCA Frameworks as the new portal", () => {
  const page = newPage();
  assert.deepEqual(page.json("NEW_PORTAL_IDS"), ["gca_agreements"]);
});

test("a saved portal selection survives the saved counties being restored after the first render", () => {
  const page = newPage();
  page.setSelected(THREE);
  page.run("applyCountyRegionPortalScoping()"); // the first render: no counties restored yet, nothing to narrow
  page.setCounties(england);                    // ... then the counties come back from the saved preferences

  page.run("applyCountyRegionPortalScoping()");  // ... and the next re-render
  assert.deepEqual(page.selected(), THREE);
  assert.deepEqual(page.savedPortalPrefs(), [], "and nothing was saved back over the user's choice");
});

test("even if the counties are there at the first render, the saved selection is kept and not re-saved", () => {
  const page = newPage();
  page.setSelected(THREE);
  page.setCounties(england);
  page.run("applyCountyRegionPortalScoping()");
  assert.deepEqual(page.selected(), THREE, "this used to be replaced by the England set, GCA Frameworks included");
  assert.deepEqual(page.savedPortalPrefs(), []);
  page.run("applyCountyRegionPortalScoping()"); // a later unrelated re-render changes nothing either
  assert.deepEqual(page.selected(), THREE);
});

test("choosing or clearing counties suggests portals via scope hints but leaves selectedPortals unchanged", () => {
  const page = newPage();
  page.setSelected(THREE);

  page.setCounties(scotland);
  page.run("applyCountyRegionPortalScoping()");
  assert.deepEqual(page.selected(), THREE, "selectedPortals is unchanged when counties are picked");
  assert.equal(page.log("scopeHints"), 1, "portal scope hint updated");

  page.setCounties([]);
  page.run("applyCountyRegionPortalScoping()");
  assert.deepEqual(page.selected(), THREE, "clearing counties leaves selectedPortals unchanged");
});

test("a re-render with the same counties leaves a portal the user toggled by hand alone", () => {
  const page = newPage();
  page.setSelected(THREE);
  page.setCounties(england);

  page.setSelected(["find_tender"]); // the user clicked the other portals off
  page.run("applyCountyRegionPortalScoping()");
  assert.deepEqual(page.selected(), ["find_tender"]);
  assert.deepEqual(page.savedPortalPrefs(), []);
});

test("an account that chose its portals does not get a new portal switched on, and what it was shown is remembered", () => {
  const page = newPage();
  page.setPrefs({ selected_portals: JSON.stringify(THREE), managed_countries: "UK" });
  page.setSelected(THREE);
  page.run("reconcilePortalCatalogue()");
  assert.deepEqual(page.selected(), THREE);
  const known = page.log("prefs").filter((p) => "known_portals" in p).map((p) => JSON.parse(p.known_portals));
  assert.equal(known.length, 1);
  assert.ok(known[0].includes("gca_agreements") && known[0].includes("find_tender"), "every portal shown so far is now known");
  assert.deepEqual(page.savedPortalPrefs(), [], "the selection itself was not touched");
});

test("an account on 'all portals' keeps the new portal off", () => {
  const page = newPage();
  page.setPrefs({ managed_countries: "UK" }); // chose countries earlier, never narrowed the portals
  page.setSelected(null);
  page.run("reconcilePortalCatalogue()");
  assert.deepEqual(page.selected(), ALL_BUT_NEW());
  assert.deepEqual(page.savedPortalPrefs().length, 1, "and the narrowed list is saved, so it stays that way");
});

test("a brand-new account gets every portal, and once the catalogue is known nothing more happens", () => {
  const page = newPage();
  page.setPrefs({});
  page.setSelected(null);
  page.run("reconcilePortalCatalogue()");
  assert.equal(page.selected(), null, "everything on");
  const first = page.log("prefs").length;
  assert.equal(first, 1, "just the record of what was shown");
  page.setPrefs({ known_portals: JSON.stringify(page.json("PORTALS.map((p) => p.id)")), selected_portals: JSON.stringify(THREE) });
  page.setSelected(THREE);
  page.run("reconcilePortalCatalogue()");
  assert.equal(page.log("prefs").length, first, "a known catalogue changes nothing and saves nothing");
  assert.deepEqual(page.selected(), THREE);
});

// ── company profile ──────────────────────────────────────────────────────────────────────────
const PROFILES = () => [
  { id: 1, name: "Civenta", is_default: true },
  { id: 2, name: "Other Ltd", is_default: false },
  { id: 3, name: "Third Co", is_default: false },
];
const optionLabels = (page) => Array.from(page.select("fitProfileSelect").options).map((o) => o.textContent);

test("a visit starts on the saved default profile, even when the remembered active one is another", () => {
  const page = newPage();
  page.setProfiles(PROFILES(), "2"); // "2" is a stale active_profile from some earlier visit
  page.run("populateFitProfileSelect()");
  assert.equal(page.activeProfile(), "1");
  assert.equal(page.select("fitProfileSelect").value, "1");
  assert.equal(page.select("rmCompanyProfileSelect").value, "1");
  assert.ok(optionLabels(page).includes("Civenta (Default)"));
});

test("with no default, the first profile is used; with no profiles, nothing is selected", () => {
  const page = newPage();
  page.setProfiles(PROFILES().map((p) => ({ ...p, is_default: false })), "");
  page.run("populateFitProfileSelect()");
  assert.equal(page.activeProfile(), "1");
  page.setProfiles([], "9");
  page.run("populateFitProfileSelect()");
  assert.equal(page.activeProfile(), "");
});

test("the picker inside another screen changes the profile for this visit only", () => {
  const page = newPage();
  page.setProfiles(PROFILES(), "");
  page.run("populateFitProfileSelect()");
  page.run('handleCompanyProfileSelectionChange("2", { makeDefault: false })');
  assert.equal(page.activeProfile(), "2");
  assert.deepEqual(page.log("prefs"), [], "no preference saved");
  assert.deepEqual(page.log("ls"), [], "nothing remembered in the browser");
  assert.deepEqual(page.log("fetches"), [], "the account default is not touched on the server");
  assert.deepEqual(page.defaults(), ["1"], "Civenta is still the default");
  assert.equal(page.select("fitProfileSelect").value, "2", "the filter-bar picker follows");
  page.run("populateFitProfileSelect()"); // e.g. the profile list is refreshed after a save
  assert.equal(page.activeProfile(), "2", "the pick stands for the rest of the visit");
});

test("the filter-bar picker makes the profile the account default", () => {
  const page = newPage();
  page.setProfiles(PROFILES(), "");
  page.run("populateFitProfileSelect()");
  page.run('handleCompanyProfileSelectionChange("3")');
  assert.equal(page.activeProfile(), "3");
  assert.deepEqual(page.log("prefs"), [{ active_profile: "3" }]);
  assert.deepEqual(page.log("ls"), [["tf_active_profile", "3"]]);
  assert.deepEqual(page.log("fetches"), [["/api/company-profiles/3/set-default", "POST"]]);
  assert.deepEqual(page.defaults(), ["3"]);
  assert.ok(optionLabels(page).length > 0);
  assert.equal(page.log("toasts").length, 1);
});

test("choosing 'Add new profile' opens the form and leaves everything as it was", () => {
  const page = newPage();
  page.setProfiles(PROFILES(), "");
  page.run("populateFitProfileSelect()");
  page.run('handleCompanyProfileSelectionChange("__add_new__")');
  assert.equal(page.log("modalOpened"), 1);
  assert.equal(page.activeProfile(), "1");
  assert.equal(page.select("fitProfileSelect").value, "1");
  assert.deepEqual(page.log("prefs"), []);
  assert.deepEqual(page.log("fetches"), []);
});

test("a profile that has just been saved as the default is selected as the default", () => {
  const page = newPage();
  page.setProfiles(PROFILES(), "");
  page.run("populateFitProfileSelect()");
  page.run('autoSelectFitProfile(2)'); // what the save handlers call when the saved profile becomes the default
  assert.equal(page.activeProfile(), "2");
  assert.deepEqual(page.defaults(), ["2"]);
  assert.deepEqual(page.log("fetches"), [["/api/company-profiles/2/set-default", "POST"]]);
});

// What the tests above cannot reach: the callers. Every picker that is not the filter bar must say
// makeDefault: false, and a saved profile is only auto-selected when it is becoming the default.
test("every picker outside the filter bar asks for makeDefault: false", () => {
  const callers = [];
  appLines.forEach((line, i) => {
    if (/^function handleCompanyProfileSelectionChange\(/.test(line)) return;
    if (/handleCompanyProfileSelectionChange\(/.test(line) && !/^\s*\/\//.test(line)) callers.push({ n: i + 1, line: line.trim() });
  });
  const keepsDefault = callers.filter((c) => !c.line.includes("makeDefault: false"));
  assert.deepEqual(
    keepsDefault.map((c) => c.line.replace(/\s+/g, " ")).sort(),
    [
      'handleCompanyProfileSelectionChange(idStr);',
      '$("fitProfileSelect")?.addEventListener("change", (e) => handleCompanyProfileSelectionChange(e.target.value));',
      '$("rmCompanyProfileSelect")?.addEventListener("change", (e) => handleCompanyProfileSelectionChange(e.target.value));',
    ].sort(),
    "only the autoSelect helper and the two filter-bar pickers may change the account default; got:\n" + keepsDefault.map((c) => `  line ${c.n}: ${c.line}`).join("\n"),
  );
  assert.equal(callers.length - keepsDefault.length, 5, "bids, analysis, history, answer bank and documents each pass makeDefault: false");
});

test("saving a profile auto-selects it only when it is becoming the default", () => {
  const calls = [];
  appLines.forEach((line, i) => { if (/autoSelectFitProfile\(result\.id\)/.test(line)) calls.push(i); });
  assert.equal(calls.length, 2, "the two profile-save handlers");
  for (const i of calls) {
    const indent = (s) => s.match(/^\s*/)[0].length;
    let j = i - 1;
    while (j > 0 && !(/^\s*if \(/.test(appLines[j]) && indent(appLines[j]) < indent(appLines[i]))) j -= 1;
    assert.ok(/isDefaultChecked \|\| result\.is_default \|\| !state\.activeProfileId/.test(appLines[j]),
      `line ${i + 1}: autoSelectFitProfile(result.id) must sit inside the "becoming the default" check, found: ${appLines[j].trim()}`);
  }
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
