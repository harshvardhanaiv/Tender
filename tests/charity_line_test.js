// Run: node tests/charity_line_test.js   (no packages)
// charityLineHtml shows a supplier's Charity Commission record on its profile.
const fs = require("fs");
const vm = require("vm");
const assert = require("assert");

const src = fs.readFileSync(__dirname + "/../web/app.js", "utf8");
const m = src.match(/function charityLineHtml\(c\) \{[\s\S]*?\n\}\n/);
assert(m, "charityLineHtml not found in web/app.js");
const ctx = {
  esc: (s) => String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"),
  formatCurrencyVal: (v) => "£" + Math.round(v).toLocaleString("en-GB"),
  safeHttpUrl: (u) => (/^https?:\/\//i.test(String(u || "")) ? u : ""),
};
vm.createContext(ctx);
vm.runInContext(m[0], ctx);
const line = ctx.charityLineHtml;
const charity = { charity_number: 1083995, url: "https://register-of-charities.charitycommission.gov.uk/en/charity-search/-/charity-details/1083995",
  income: 283275000, year_end: "2026-03-31", website: "https://www.mha.org.uk", email: "info@mha.example", phone: "0300 1", activities: "Care homes" };
let pass = 0;
const test = (name, fn) => { fn(); pass++; console.log("ok   " + name); };

test("no charity gives nothing", () => {
  assert.strictEqual(line(null), "");
  assert.strictEqual(line({}), "");
});
test("a charity shows its number as a register link, its income and its contact details", () => {
  const html = line(charity);
  assert(html.includes("Registered charity") && html.includes("#1083995 ↗") && html.includes("charity-details/1083995"));
  assert(html.includes("£283,275,000") && html.includes("year to 2026-03-31"));
  assert(html.includes("mha.org.uk") && html.includes("info@mha.example") && html.includes("Contact on the register"));
});
test("missing income or contacts leave those parts out, and hostile text and links are neutralised", () => {
  const html = line({ charity_number: 5, url: "javascript:alert(1)", income: null, website: "javascript:alert(2)", email: "<img src=x onerror=alert(3)>", activities: "<script>x</script>" });
  assert(!html.includes("latest income") && !html.includes("javascript:") && !html.includes("<img") && !html.includes("<script>"));
  assert(html.includes("#5") && !html.includes("href="), "an unsafe register link is not rendered as a link");
});
console.log(`\n${pass} of ${pass} passed`);
