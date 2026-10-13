"use strict";
/* Run: node tests/growth_email_hints_test.js
   Growth Studio campaign builder (web/growth-view.js): what the reader sees when a buyer has no email address yet.
   TenderFlow has no buyer email data of its own, so the reader types one in; these hints point them to the box and to
   the notice where the contact is published. Nothing here talks to a server. */
const assert = require("node:assert/strict");
const path = require("node:path");
const V = require(path.join(__dirname, "..", "web", "growth-view.js"));

const tests = [];
const test = (n, f) => tests.push([n, f]);

test("a buyer with no address gets a link to the notice, or a hint when there is none", () => {
  const t = { id: 5, buyer_name: "Alpha", signal_type: "renewal", signal_label: "Renewal", included: true, status: "not_sent", contact_email: "", opted_out: false,
    links: [{ label: "Notice", url: "https://www.contractsfinder.service.gov.uk/Notice/abc" }] };
  const withLink = V.targetRow(t);
  assert.match(withLink, /Find the contact on the notice/);
  assert.match(withLink, /href="https:\/\/www\.contractsfinder\.service\.gov\.uk\/Notice\/abc"/);
  assert.match(withLink, /rel="noreferrer noopener"/);
  assert.match(V.targetRow(Object.assign({}, t, { links: [] })), /Look on the buyer's website/);
  assert.ok(!V.targetRow(Object.assign({}, t, { contact_email: "procurement@alpha.example" })).includes("No address yet"));
  assert.ok(!V.targetRow(Object.assign({}, t, { links: [{ url: "javascript:alert(1)" }] })).includes("javascript:"));
});

test("the preview offers Add one when the buyer has no address", () => {
  const targets = [{ id: 5, buyer_name: "Alpha", opted_out: false }];
  const html = V.previewHtml({ target_id: 5, buyer: "Alpha", to: null, subject: "S", body: "B", missing: [], unknown: [], warnings: [] }, targets, 5);
  assert.match(html, /no email address yet/);
  assert.match(html, /data-action="focus-email" data-id="5"/);
  const has = V.previewHtml({ target_id: 5, buyer: "Alpha", to: "p@alpha.example", subject: "S", body: "B", missing: [], unknown: [], warnings: [] }, targets, 5);
  assert.ok(!has.includes("focus-email"));
});

test("the builder note says what Send mail does and shows the Outreach panel with no create-draft button", () => {
  const detail = {
    campaign: { id: 1, name: "C", channel: "email", channel_label: "Email", status: "draft", subject: "S", body: "B", category_label: "Cat", profile_name: "P" },
    targets: [{ id: 1, buyer_name: "Alpha", signal_type: "renewal", included: true, status: "not_sent", contact_email: "a@b.example", opted_out: false }],
    sender: { company_name: "Me" }, warnings: [], eligible: 1,
  };
  const html = V.builderPane({ status: "ok", detail, draft: { subject: "S", body: "B" }, dirty: false, options: { channels: [], merge_fields: [] } });
  assert.match(html, /use Send mail to send through Outreach/);
  assert.match(html, /Send to Outreach/);
  assert.ok(!/TenderFlow sends nothing/.test(html), "the old claim is gone");
  assert.ok(!html.includes("outreach-push"), "there is no create-draft button any more");
  assert.ok(!/Create drafts/.test(html));
});

let failed = 0;
for (const [name, fn] of tests) {
  try { fn(); console.log(`ok    ${name}`); } catch (err) { failed += 1; console.log(`FAIL  ${name}`); console.log(err && err.stack ? err.stack : err); }
}
console.log(`\n${tests.length - failed} of ${tests.length} passed`);
process.exitCode = failed ? 1 : 0;
