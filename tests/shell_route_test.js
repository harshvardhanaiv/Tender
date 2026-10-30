"use strict";
/* Run: node tests/shell_route_test.js
   Re-test 3 BN4: /buyers highlighted "Pipeline" and rendered the Pipeline board behind the page. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const shell = require(path.join(__dirname, "..", "web", "shell.js"));
const app = fs.readFileSync(path.join(__dirname, "..", "web", "app.js"), "utf8");
let passed = 0, failed = 0;
function test(name, fn) { try { fn(); passed++; console.log("ok   " + name); } catch (e) { failed++; console.log("FAIL " + name + "\n  " + e.message); } }

test("the section the address names is highlighted even when Pipeline is also open", () => {
  const open = new Set(["dashboardModal", "buyerIntelligenceModal"]);
  assert.equal(shell.pickActive("/buyers", (id) => open.has(id))[0], "btnBuyerIntelligence");
  assert.equal(shell.pickActive("/pipeline", (id) => open.has(id))[0], "btnDashboard");
});
test("with no matching route the first open section is used, and nothing open means no section", () => {
  assert.equal(shell.pickActive("/", (id) => id === "historyModal")[0], "btnHistory");
  assert.equal(shell.pickActive("/", () => false), null);
});
test("every route maps to a real sidebar button", () => {
  const navs = new Set(shell.SECTIONS.map((s) => s[0]));
  Object.values(shell.ROUTE_NAV).forEach((id) => assert.ok(navs.has(id), id));
});
test("the Pipeline board is only built while it is open", () => {
  const fn = app.slice(app.indexOf("function renderPipelineBoard()"));
  assert.ok(/classList\.contains\("hidden"\)\) \{ updatePipelineKindCounts\(\); return; \}/.test(fn.slice(0, 600)));
  const open = app.slice(app.indexOf("function openDashboard()"), app.indexOf("function closeDashboard()"));
  assert.ok(open.indexOf('classList.remove("hidden")') < open.indexOf("renderPipelineBoard()"), "unhide first, then render");
});
test("FN5: a direct link to a section shows a neutral loading state, not Tender search, and clears when it opens", () => {
  const html = fs.readFileSync(path.join(__dirname, "..", "web", "index.html"), "utf8");
  const head = html.slice(0, html.indexOf("</head>"));
  assert.ok(/classList\.add\("tf-routing"\)/.test(head) && /funding\|planning\|buyers/.test(head));
  const css = fs.readFileSync(path.join(__dirname, "..", "web", "theme.css"), "utf8");
  assert.ok(/html\.tf-routing body\.tf-shell #mainContent > \*\s*\{\s*visibility: hidden/.test(css));
  assert.ok(fs.readFileSync(path.join(__dirname, "..", "web", "shell.js"), "utf8").includes('classList.remove("tf-routing")'));
});
console.log(`\n${passed} of ${passed + failed} passed`);
process.exit(failed ? 1 : 0);
