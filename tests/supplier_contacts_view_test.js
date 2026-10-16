"use strict";
/* Run: node tests/supplier_contacts_view_test.js
   The supplier panel shows email / phone / website found on the company's own website instead of an
   "Unlisted on notice" note and a Google link. This cuts the real supplierContactDisplays() out of web/app.js. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const lines = fs.readFileSync(path.join(__dirname, "..", "web", "app.js"), "utf8").replace(/\r\n/g, "\n").split("\n");
const start = lines.findIndex((l) => l.startsWith("function supplierContactDisplays("));
assert.ok(start >= 0, "app.js has no supplierContactDisplays()");
let end = start;
while (!/^}/.test(lines[end])) end += 1;
const ctx = vm.createContext({ esc: (v) => String(v == null ? "" : v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;"), encodeURIComponent });
vm.runInContext(lines.slice(start, end + 1).join("\n") + "\nthis.f = supplierContactDisplays;", ctx);
const show = (s, o) => ctx.f(s, o);

const tests = [];
const test = (n, f) => tests.push([n, f]);

test("the notice's own contact details win and say nothing about a website lookup", () => {
  const r = show({ name: "Acme", email: "a@acme.com", phone: "01788 567890", website: "acme.com", found_contacts: { status: "found", emails: ["b@other.com"], phones: ["+44 1"], website: "https://x.com" } });
  assert.match(r.email, /mailto:a@acme\.com/);
  assert.match(r.phone, /tel:01788 567890/);
  assert.match(r.website, /href="https:\/\/acme\.com"/);
  assert.ok(!/from website/.test(r.email + r.phone + r.website));
});

test("when the notice has nothing, what the server found is shown and marked 'from website'", () => {
  const r = show({ name: "Acme", email: "", phone: "Not available", website: "", found_contacts: {
    status: "found", website: "https://www.acme.co.uk", emails: ["info@acme.co.uk", "bids@acme.co.uk"], phones: ["+44 1788 567890"], social: { linkedin: "https://www.linkedin.com/company/acme" } } });
  assert.match(r.email, /mailto:info@acme\.co\.uk/);
  assert.match(r.email, /\+1/);                    // the second address is counted, listed in the tooltip
  assert.match(r.email, /bids@acme\.co\.uk/);
  assert.match(r.phone, /tel:\+441788567890/);      // no spaces in the tel: link
  assert.match(r.website, /www\.acme\.co\.uk ↗/);
  assert.match(r.email + r.phone + r.website, /from website/);
  assert.equal(r.linkedinUrl, "https://www.linkedin.com/company/acme");
  assert.equal(r.linkedinExact, true);
});

test("before the lookup has run the panel says it is looking up, not 'Unlisted'", () => {
  const r = show({ name: "Acme", email: "", phone: "", website: "" });
  assert.equal(r.lookingUp, true);
  assert.match(r.email, /Looking up/);
  assert.match(r.website, /Looking up/);
  assert.ok(!/Unlisted/.test(r.email + r.phone + r.website));
});

test("when nothing was found it says so and keeps a way to search for the site", () => {
  const r = show({ name: "Quiet & Co", email: "", phone: "", website: "", found_contacts: { status: "not_found", emails: [], phones: [] } });
  assert.match(r.email, /Not found/);
  assert.match(r.phone, /Not found/);
  assert.match(r.website, /Find Website/);
  assert.match(r.website, /Quiet%20%26%20Co%20UK%20website/);
  assert.match(r.linkedinUrl, /linkedin\.com\/search\/results\/companies\/\?keywords=Quiet%20%26%20Co/);
  assert.equal(r.linkedinExact, false);
});

test("scraped text is escaped", () => {
  const r = show({ name: "X", found_contacts: { status: "found", emails: ['"><img src=x>@e.com'], phones: [], website: "" } });
  assert.ok(!/<img/.test(r.email));
});

let failed = 0;
for (const [name, fn] of tests) {
  try { fn(); console.log("PASS", name); } catch (e) { failed += 1; console.log("FAIL", name, "->", e.message); }
}
console.log(failed ? `${failed} failed` : "all passed");
process.exit(failed ? 1 : 0);
