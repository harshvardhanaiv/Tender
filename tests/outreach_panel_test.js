"use strict";
/* Run: node tests/outreach_panel_test.js
   The "Send to Outreach" panel in the Growth Studio campaign builder (web/growth-view.js): what it says while it
   checks, when Outreach is not connected, when it is, what the buttons need, and that data from the server
   (buyer names, error text) is escaped. Nothing here talks to a server. */
const assert = require("node:assert/strict");
const path = require("node:path");
const V = require(path.join(__dirname, "..", "web", "growth-view.js"));

const ready = {
  ready: true, mode: "test", key_hint: "sk_test_…", wallet: { credits: 40 }, max_per_push: 25, problem: null,
};
const base = { status: "ok", info: ready, drafts: [], last: null, pushing: false, refreshing: false, personal: false, error: null };
const panel = (o, eligible = 2, dirty = false) => V.outreachPanel(Object.assign({}, base, o), eligible, dirty);
const button = (html, action) => (html.match(new RegExp(`<button[^>]*data-action="${action}"[^>]*>`)) || [])[0] || "";

const tests = [];
const test = (n, f) => tests.push([n, f]);

test("while the status loads it says so and has no buttons", () => {
  const html = V.outreachPanel({ status: "loading", info: null }, 2, false);
  assert.match(html, /Checking Outreach/);
  assert.ok(!html.includes("data-action"));
  assert.match(V.outreachPanel(undefined, 2, false), /Checking Outreach/);
});

test("when Outreach is not connected the reason is shown and nothing can be sent", () => {
  const html = panel({ info: { ready: false, configured: false, problem: "Outreach is not connected. Ask your administrator to set OUTREACH_API_KEY on the server." } });
  assert.match(html, /OUTREACH_API_KEY/);
  assert.ok(!html.includes('data-action="outreach-send"'));
});

test("a connected test key says nothing real happens, shows credits and offers Send mail", () => {
  const html = panel({});
  assert.match(html, /Test mode/);
  assert.match(html, /40 credits in Outreach/);
  assert.match(html, /charges nothing and sends nothing real/);
  assert.ok(!/disabled/.test(button(html, "outreach-send")));
  assert.match(html, />Send mail</);
  assert.ok(!/Create drafts/.test(html), "there is no create-drafts button any more");
  assert.ok(!/Open Outreach/.test(html), "no link out to Outreach");
  assert.ok(/disabled/.test(button(html, "outreach-refresh")), "nothing to refresh before anything was sent");
});

test("a live key is flagged as live", () => {
  const html = panel({ info: Object.assign({}, ready, { mode: "live" }) });
  assert.match(html, /Live key/);
  assert.match(html, /emails the buyers/);
});

test("push is off with nobody eligible, with unsaved changes, and while it runs", () => {
  assert.match(button(panel({}, 0, false), "outreach-send"), /disabled/);
  const dirty = panel({}, 2, true);
  assert.match(button(dirty, "outreach-send"), /disabled/);
  assert.match(dirty, /Save the message first/);
  const busy = panel({ pushing: true });
  assert.match(button(busy, "outreach-send"), /disabled/);
  assert.match(busy, /Sending…/);
});

test("buyers are listed with whether they were sent, their state, approval and counts", () => {
  const html = panel({
    drafts: [
      { target_id: 1, buyer: "Alpha Council", outreach_id: "cp_aaa111", status: "sending", approval: "approved", sent: 3, replies: 1, error: null },
      { target_id: 2, buyer: "Beta Council", outreach_id: "cp_bbb222", status: "created", approval: null, sent: null, replies: null, error: "This key is not allowed to do that." },
    ],
  });
  assert.match(html, /cp_aaa111/);
  assert.match(html, /gs-badge--good[^>]*>sending/);
  assert.match(html, /gs-badge--warn[^>]*>Made, not sent/);
  assert.ok(!/disabled/.test(button(html, "outreach-refresh")));
});

test("a buyer that could not be sent is shown with its reason", () => {
  const html = panel({ drafts: [{ target_id: 1, buyer: "Alpha Council", outreach_id: null, status: "failed", approval: null, error: "leads[0].email was refused" }] });
  assert.match(html, /Not sent/);
  assert.match(html, /leads\[0\]\.email was refused/);
  assert.ok(/disabled/.test(button(html, "outreach-refresh")), "an email that was never made has nothing to refresh");
});

test("the result of a send lists sent, failed, stopped, left-out and waiting buyers", () => {
  const last = {
    mode: "test", sent: [{ buyer: "A" }, { buyer: "B" }], failed: [{ buyer: "C", reason: "refused" }],
    stopped: "You need 1 more credit.", deferred: 3, skipped: [{ buyer: "D", reason: "No contact email." }],
  };
  const html = panel({ last });
  assert.match(html, /2 emails handed to Outreach to send \(test mode\)/);
  assert.match(html, /1 email could not be sent\. The reason is shown against the buyer below\./);
  assert.match(html, /Stopped: You need 1 more credit\./);
  assert.match(html, /3 buyers still to send\. Choose Send mail again/);
  assert.match(html, /D was left out: No contact email\./);
});

test("text from the server is escaped", () => {
  const evil = '<img src=x onerror="alert(1)">';
  const html = panel({
    drafts: [{ target_id: 1, buyer: evil, outreach_id: evil, status: evil, approval: evil, error: evil }],
    last: { mode: "test", sent: [], failed: [{ buyer: evil, reason: evil }], stopped: evil, deferred: 0, skipped: [{ buyer: evil, reason: evil }] },
  });
  assert.ok(!html.includes("<img"), "raw markup must not appear");
  assert.match(html, /&lt;img/);
  const bad = V.outreachPanel({ status: "ok", info: { ready: false, problem: evil } }, 1, false);
  assert.ok(!bad.includes("<img"));
});

test("buyers without an address are counted and the reader is sent to the address box", () => {
  const none = panel({}, 2, false);
  assert.ok(!none.includes("no email address yet"), "nothing to warn about when everyone has an address");
  const html = V.outreachPanel(Object.assign({}, base), 2, false, 3);
  assert.match(html, /3 buyers have no email address yet and will be left out/);
  assert.match(html, /data-action="focus-email"/);
  assert.match(V.outreachPanel(Object.assign({}, base), 2, false, 1), /1 buyer has no email address yet/);
});

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

test("the builder shows the Send mail panel and the note says what sending does", () => {
  const detail = {
    campaign: { id: 1, name: "C", channel: "email", channel_label: "Email", status: "draft", subject: "S", body: "B", category_label: "Cat", profile_name: "P" },
    targets: [{ id: 1, buyer_name: "Alpha", signal_type: "renewal", included: true, status: "not_sent", contact_email: "a@b.example", opted_out: false }],
    sender: { company_name: "Me" }, warnings: [], eligible: 1,
  };
  const html = V.builderPane({ status: "ok", detail, draft: { subject: "S", body: "B" }, dirty: false, outreach: base, options: { channels: [], merge_fields: [] } });
  assert.match(html, /Send to Outreach/);
  assert.match(html, /data-action="outreach-send"/);
  assert.match(html, /use Send mail to send through Outreach/);
  assert.ok(!/TenderFlow itself sends nothing/.test(html), "the old claim is gone");
});

let failed = 0;
for (const [name, fn] of tests) {
  try { fn(); console.log(`ok    ${name}`); } catch (err) { failed += 1; console.log(`FAIL  ${name}`); console.log(err && err.stack ? err.stack : err); }
}
console.log(`\n${tests.length - failed} of ${tests.length} passed`);
process.exitCode = failed ? 1 : 0;
