// Run: node tests/grants_panel_test.js   (no packages)
// grantsPanelHtml turns the grants block of /api/suppliers/<id> and /api/buyers/<name> into HTML.
const fs = require("fs");
const vm = require("vm");
const assert = require("assert");

const src = fs.readFileSync(__dirname + "/../web/app.js", "utf8");
const m = src.match(/function grantsPanelHtml\(g, mode\) \{[\s\S]*?\n\}\n/);
assert(m, "grantsPanelHtml not found in web/app.js");
const ctx = {
  esc: (s) => String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"),
  formatCurrencyVal: (v) => "£" + Math.round(v).toLocaleString("en-GB"),
  safeHttpUrl: (u) => (/^https?:\/\//i.test(String(u || "")) ? u : ""),
};
vm.createContext(ctx);
vm.runInContext(m[0], ctx);
const panel = ctx.grantsPanelHtml;

const item = { title: "Smart <boiler> controls", funder: "Innovate UK", supplier_name: "Acme Ltd", programme: "Investment Accelerator",
  value_gbp: 249900, start_date: "2026-06-30", end_date: "2027-12-31", status: "Active", url: "https://gtr.ukri.org/projects?ref=1" };
let pass = 0;
const test = (name, fn) => { fn(); pass++; console.log("ok   " + name); };

test("nothing to show gives no panel", () => {
  assert.strictEqual(panel(null, "supplier"), "");
  assert.strictEqual(panel({ count: 0, items: [] }, "supplier"), "");
});

test("a supplier panel lists grants, links the funder to its buyer profile and says they are not contracts", () => {
  const html = panel({ count: 1, total_value: 249900, items: [item] }, "supplier");
  assert(html.includes("1 grant ·") && html.includes("£249,900 awarded"));
  assert(html.includes('data-authority-name="Innovate UK"') && html.includes("openBuyerProfileDrawer"));
  assert(html.includes("not counted in the contract totals or the date filter"));
  assert(html.includes("Smart &lt;boiler&gt; controls"), "titles are escaped");
});

test("a funder panel names the recipients and the largest ones", () => {
  const html = panel({ count: 2, recipients: 2, total_value: 400000, items: [item], top_recipients: [{ supplier_name: "Acme Ltd", total_value: 300000 }] }, "funder");
  assert(html.includes("2 grants to 2 organisations"));
  assert(html.includes("Largest recipients") && html.includes("Acme Ltd (£300,000)"));
  assert(!html.includes("or the date filter"));
  assert(html.includes("Showing the newest 1 of 2 grants."));
});

test("a missing value and an unsafe link are handled", () => {
  const html = panel({ count: 1, total_value: 0, items: [{ ...item, value_gbp: null, url: "javascript:alert(1)" }] }, "supplier");
  assert(html.includes("Value not stated"));
  assert(!html.includes("javascript:"), "only http(s) links are rendered");
});

console.log(`\n${pass} of ${pass} passed`);
