"use strict";
/* Run: node tests/portal_hits_test.js
   Local check of "construction": the page showed 1,171 of 2,762 rows. The portals had returned all 2,762 for that word, but the page
   re-checked the word against the fields it holds (title, supplier, buyer, description). eTenders Ireland and Northern Ireland rows carry
   only a title and a buyer, so 1,418 + 165 genuine hits ("Extension works to existing Primary School", "Design and Build Contract ...")
   were dropped without any filter being visible. A row the portal returned for this search now stays, filed under "Returned by the portal". */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const appSrc = fs.readFileSync(path.join(__dirname, "..", "web", "app.js"), "utf8").replace(/\r\n/g, "\n");
const lines = appSrc.split("\n");
function cut(first, lastPattern) {
  const start = lines.findIndex((l) => l.startsWith(first));
  assert.ok(start >= 0, `app.js has no line starting ${first}`);
  let end = start;
  while (!lastPattern.test(lines[end])) end += 1;
  return lines.slice(start, end + 1).join("\n");
}
const ctx = vm.createContext({});
vm.runInContext([cut("const MATCH_ROLES = {", /^};/), cut("function getRowMatchRole(", /^}/), "this.getRowMatchRole = getRowMatchRole; this.MATCH_ROLES = MATCH_ROLES;"].join("\n"), ctx);

const tests = [];
const test = (n, f) => tests.push([n, f]);

test("a portal hit is its own match group, listed after the rows where the word is visible", () => {
  assert.equal(ctx.getRowMatchRole({ _match_reason: "portal" }), "portal");
  const roles = ctx.MATCH_ROLES;
  assert.equal(roles.portal.label, "Returned by the portal");
  for (const other of ["title", "supplier", "buyer", "scope"]) assert.ok(roles.portal.order > roles[other].order, `portal group must sort after ${other}`);
  assert.equal(ctx.getRowMatchRole({ _match_reason: "title" }), "title");
  assert.equal(ctx.getRowMatchRole({}), null);
});

test("the keyword filter keeps a portal hit on an all-fields search only", () => {
  const block = appSrc.slice(appSrc.indexOf("rows = rows.filter((row) => {\n      if (matchRowToKeyword("), appSrc.indexOf("} else {\n    for (const r of rows) {"));
  assert.ok(block.includes('parsedKeyword.scope === "all" && row._portal_hit'), "a title / supplier / buyer search must still filter strictly");
  assert.ok(block.includes('row._match_reason = "portal"'));
});

test("the server marks the rows the portals returned for this search, on both the progressive and the single-source path", () => {
  const server = fs.readFileSync(path.join(__dirname, "..", "server.py"), "utf8").replace(/\r\n/g, "\n");
  assert.ok(server.includes('r["_portal_hit"] = True'));
  assert.equal((server.match(/live_tender_ids = \{generate_tender_id\(r\) for r in rows\}/g) || []).length, 2, "both paths must define live_tender_ids");
  assert.ok(server.indexOf("live_tender_ids = {generate_tender_id(r) for r in rows}") < server.indexOf('r["_portal_hit"] = True'));
});

(() => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { fn(); console.log(`ok    ${name}`); } catch (err) { failed += 1; console.log(`FAIL  ${name}`); console.log(err && err.stack ? err.stack : err); }
  }
  console.log(`\n${tests.length - failed} of ${tests.length} passed`);
  process.exitCode = failed ? 1 : 0;
})();
