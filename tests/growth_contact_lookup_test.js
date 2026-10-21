"use strict";
/* Run: node tests/growth_contact_lookup_test.js
   The campaign builder fills a buyer's contact inbox from the server instead of sending the user to the notice.
   This checks what each target row says while it looks, when it found one, and when nothing is published. */
const assert = require("node:assert/strict");
const path = require("node:path");
const V = require(path.join(__dirname, "..", "web", "growth-view.js"));

const base = {
  id: 7, buyer_name: "Royal Borough of Greenwich", signal_label: "Contract renewal", signal_type: "renewal", headline: "x", included: true, status: "not_sent",
  opted_out: false, contact_email: "", personal_email: false, links: [{ label: "Notice", url: "https://www.find-tender.service.gov.uk/Notice/1" }],
};
const tests = [];
const test = (n, f) => tests.push([n, f]);

test("while the server looks, the row says so and offers no link to hunt on the notice", () => {
  const html = V.targetRow({ ...base, _lookup: "looking" });
  assert.match(html, /Looking for the buyer's contact/);
  assert.ok(!/Find the contact on the notice/.test(html));
});

test("a found address sits in the box with where it came from", () => {
  const html = V.targetRow({ ...base, contact_email: "procurement@royalgreenwich.gov.uk", contact_found: { source: "notice", url: "https://www.find-tender.service.gov.uk/Notice/1" } });
  assert.match(html, /value="procurement@royalgreenwich\.gov\.uk"/);
  assert.match(html, /Found on <a[^>]*>the notice ↗<\/a>\. Check it before you send/);
  assert.ok(!/No address yet/.test(html));
  const site = V.targetRow({ ...base, contact_email: "tenders@x.gov.uk", contact_found: { source: "website", url: "https://www.x.gov.uk/procurement" } });
  assert.match(site, /the buyer's website ↗/);
});

test("a typed address shows no 'found' note", () => {
  const html = V.targetRow({ ...base, contact_email: "mine@x.gov.uk" });
  assert.ok(!/Found on/.test(html));
});

test("when nothing is published it says so and keeps the notice as the manual route", () => {
  const html = V.targetRow({ ...base, _lookup: "not_found" });
  assert.match(html, /No public inbox found\./);
  assert.match(html, /Check the notice ↗/);
  const noLink = V.targetRow({ ...base, links: [], _lookup: "not_found" });
  assert.match(noLink, /No public inbox found\. Look on the buyer's website/);
});

test("a named person's address is not offered; the user is told to add a role inbox", () => {
  const html = V.targetRow({ ...base, _lookup: "personal_only" });
  assert.match(html, /Only a named person's address is published\. Add a role inbox yourself/);
});

test("a named contact from the notice is offered with one click to use it, never filled in by itself", () => {
  const sg = { email: "charlotteknowles@swale.gov.uk", source: "notice", url: "https://www.contractsfinder.service.gov.uk/Notice/x" };
  const html = V.targetRow({ ...base, _lookup: "personal_only", _suggest: sg });
  assert.match(html, /The notice names a contact: <b>charlotteknowles@swale\.gov\.uk<\/b> \(a named person, not a shared inbox\)/);
  assert.match(html, /<button type="button"[^>]*data-action="use-suggestion" data-id="7" data-email="charlotteknowles@swale\.gov\.uk">Use this address<\/button>/);
  assert.match(html, /or type a role inbox yourself/);
  assert.ok(/value=""/.test(html), "the box stays empty until the user accepts");
  assert.match(V.targetRow({ ...base, _lookup: "personal_only", _suggest: { ...sg, source: "website" } }), /The buyer(?:'|&#39;)s website names a contact/);
});

test("a suggested address is escaped and a missing suggestion falls back to the old message", () => {
  const evil = V.targetRow({ ...base, _lookup: "personal_only", _suggest: { email: '"><img src=x onerror=alert(1)>@x.gov.uk', source: "notice" } });
  assert.ok(!/<img src=x/.test(evil));
  assert.match(V.targetRow({ ...base, _lookup: "personal_only", _suggest: null }), /Only a named person's address is published/);
  assert.match(V.targetRow({ ...base, _lookup: "personal_only", _suggest: { email: "" } }), /Only a named person's address is published/);
});

test("before any lookup the old hint still shows", () => {
  assert.match(V.targetRow(base), /Find the contact on the notice ↗/);
});

test("a hostile notice link or source URL cannot open markup or a javascript: link", () => {
  const evil = V.targetRow({ ...base, links: [{ url: "javascript:alert(1)" }], contact_email: "a@b.gov.uk", contact_found: { source: "notice", url: "javascript:alert(2)" } });
  assert.ok(!/javascript:/.test(evil));
  const markup = V.targetRow({ ...base, contact_email: '"><img src=x onerror=alert(1)>@b.gov.uk', contact_found: { source: "website", url: "https://x.gov.uk/\"><script>" } });
  assert.ok(!/<img src=x/.test(markup) && !/<script>/.test(markup));
});

let failed = 0;
for (const [name, fn] of tests) {
  try { fn(); console.log("PASS", name); } catch (e) { failed += 1; console.log("FAIL", name, "->", e.message); }
}
console.log(failed ? `${failed} failed` : "all passed");
process.exit(failed ? 1 : 0);
