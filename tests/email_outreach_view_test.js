"use strict";
/* Run: node tests/email_outreach_view_test.js
   The "Email (Outreach)" settings panel (web/email-outreach.js): reading the recipients box, what the status line
   says, how a send is described (a campaign is never called "sent"), and that anything that came from the
   server or the user is escaped. Nothing here talks to a server. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const E = require(path.join(__dirname, "..", "web", "email-outreach.js"));

const tests = [];
const test = (n, f) => tests.push([n, f]);

test("recipients: one per line, email then first name then company", () => {
  const out = E.parseRecipients("a@x.co.uk\n\n  b@x.co.uk , Sam \r\nc@x.co.uk\tLee\tAcme, Ltd\n,,\n");
  assert.deepEqual(out, [
    { email: "a@x.co.uk" },
    { email: "b@x.co.uk", first_name: "Sam" },
    { email: "c@x.co.uk", first_name: "Lee", company: "Acme, Ltd" },
  ]);
  assert.deepEqual(E.parseRecipients(""), []);
  assert.deepEqual(E.parseRecipients(null), []);
});

test("status: loading, not allowed, not connected, ready", () => {
  assert.match(E.statusHtml(null), /Checking Outreach/);
  assert.match(E.statusHtml({ can_send: false, problem: "Ask an administrator." }), /Ask an administrator/);
  const off = E.statusHtml({ can_send: true, ready: false, problem: "Outreach is not connected. Ask your administrator to set OUTREACH_API_KEY on the server.", setup_hints: ["Connect a mailbox."] });
  assert.match(off, /OUTREACH_API_KEY/);
  assert.match(off, /Connect a mailbox/);
  const ready = E.statusHtml({ can_send: true, ready: true, mode: "test", key_hint: "sk_test_…", wallet: { credits: 40, connected: { mailboxes: 1 } }, usage: { daily_cap: 25, sent_today: 3 }, setup_hints: [] });
  assert.match(ready, /Test mode/);
  assert.match(ready, /sk_test_…/);
  assert.match(ready, /40 credits/);
  assert.match(ready, /1 mailbox connected/);
  assert.match(ready, /3 of 25 single sends used today/);
  assert.match(E.statusHtml({ can_send: true, ready: true, mode: "live", key_hint: "sk_live_…" }), /Live mode/);
});

test("a campaign is described as approved and not instant, never as sent", () => {
  const html = E.resultHtml({ campaigns: [{ recipients: 3, resumed: false }], sent: [], failed: [], skipped: [] });
  assert.match(html, /3 recipients approved in one campaign/);
  assert.match(html, /sending window/);
  assert.match(html, /not instant/);
  assert.ok(!/ sent now/.test(html));
  assert.match(E.resultHtml({ sent: [{ email: "a@x.co.uk" }, { email: "b@x.co.uk" }] }), /2 emails sent now/);
  assert.match(E.resultHtml({ campaigns: [{ recipients: 2, resumed: true }] }), /earlier campaign was approved/);
});

test("the daily limit, failures, a stop, skips and deferred recipients are all shown with reasons", () => {
  const html = E.resultHtml({
    daily_limited: [{ email: "d@x.co.uk" }],
    failed: [{ email: "f@x.co.uk", step: "approve", reason: "try later" }],
    stopped: "no credits", hint: "Top up credits in Outreach.",
    deferred: [{ email: "g@x.co.uk", reason: "Not attempted: no credits" }],
    skipped: [{ email: "", reason: "No email address." }, { email: "s@x.co.uk", reason: "Opted out or suppressed. Not emailed." }],
  });
  assert.match(html, /1 email not sent: daily limit reached/);
  assert.match(html, /d@x\.co\.uk/);
  assert.match(html, /f@x\.co\.uk \(approve\): try later/);
  assert.match(html, /Stopped: no credits/);
  assert.match(html, /Top up credits/);
  assert.match(html, /g@x\.co\.uk: Not attempted/);
  assert.match(html, /\(no address\): No email address/);
  assert.match(html, /Opted out/);
  assert.match(E.resultHtml({}), /Nothing was sent/);
  assert.equal(E.resultHtml(null), "");
});

test("preview says what would go and how", () => {
  const html = E.previewHtml({ single: ["a@x.co.uk"], campaigns: [{ recipients: ["b@x.co.uk", "c@x.co.uk"] }], resume: [], daily_limited: [{ email: "z@x.co.uk" }], skipped: [{ email: "q", reason: "Not a valid email address." }] });
  assert.match(html, /1 email sent now, one at a time/);
  assert.match(html, /One campaign for 2 recipients/);
  assert.match(html, /1 email will not be sent: daily limit reached/);
  assert.match(html, /Not a valid email address/);
  assert.match(E.previewHtml({ single: [], campaigns: [], skipped: [] }), /Nothing would be sent/);
});

test("history shows each status label and the reason", () => {
  const html = E.historyHtml([
    { email: "a@x.co.uk", mode: "single", label: "Sent", sent_at: "2026-10-15T09:30:12" },
    { email: "b@x.co.uk", mode: "campaign", label: "Approved, sending in the sending window", created_at: "2026-10-15T09:31:00" },
    { email: "c@x.co.uk", mode: "campaign", label: "Bounced", error: "mailbox full", sent_at: "2026-10-15T10:00:00" },
  ]);
  assert.match(html, /2026-10-15 09:30/);
  assert.match(html, /Campaign/);
  assert.match(html, /Approved, sending in the sending window/);
  assert.match(html, /\(mailbox full\)/);
  assert.match(E.historyHtml([]), /Nothing sent yet/);
});

test("anything from the server or the user is escaped", () => {
  const bad = `<img src=x onerror="alert(1)">`;
  const all = E.resultHtml({ failed: [{ email: bad, reason: bad }], stopped: bad, hint: bad, skipped: [{ email: bad, reason: bad }], deferred: [{ email: bad, reason: bad }], daily_limited: [{ email: bad }] })
    + E.statusHtml({ can_send: true, ready: false, problem: bad, hint: bad, setup_hints: [bad] })
    + E.statusHtml({ can_send: true, ready: true, mode: "test", key_hint: bad, wallet: { credits: bad } })
    + E.historyHtml([{ email: bad, mode: "single", label: bad, error: bad }])
    + E.previewHtml({ single: [bad], campaigns: [{ recipients: [bad] }], skipped: [{ email: bad, reason: bad }] });
  assert.ok(!all.includes("<img"), "no raw tag may get through");
  assert.ok(all.includes("&lt;img"));
});

test("the page file never holds a key and the panel only reads a masked hint", () => {
  const src = fs.readFileSync(path.join(__dirname, "..", "web", "email-outreach.js"), "utf8");
  assert.ok(!/sk_(test|live)_[A-Za-z0-9_-]{6,}/.test(src));
  assert.ok(!/OUTREACH_API_KEY/.test(src));
  const html = fs.readFileSync(path.join(__dirname, "..", "web", "index.html"), "utf8");
  assert.match(html, /id="emailOutreachSection"[^>]*display:none/);
  assert.match(html, /src="\/email-outreach\.js\?v=\d+"/);
});

test("connecting Outreach opens the Connections window on the page: a button, never a link out", () => {
  const off = E.statusHtml({ can_send: true, ready: false, configured: false, problem: "Outreach is not connected.", connect_url: "https://outreach.aivhub.com/", manage_url: "https://outreach.aivhub.com/" });
  assert.match(off, /<button type="button" class="eo-link" data-open-connections="outreach">Connect Outreach<\/button>/);
  assert.match(off, /in a window on this page/);
  const on = E.statusHtml({ can_send: true, ready: true, configured: true, mode: "test", key_hint: "sk_test_…", connect_url: "https://outreach.aivhub.com/", manage_url: "https://outreach.aivhub.com/" });
  assert.match(on, /data-open-connections="mail">Connections<\/button>/);
  for (const html of [off, on]) assert.ok(!/<a |href=|target="_blank"|data-outreach-link|Manage in Outreach|outreach\.aivhub\.com/.test(html));
  const src = fs.readFileSync(path.join(__dirname, "..", "web", "email-outreach.js"), "utf8");
  assert.match(src, /document\.addEventListener\("connections:changed", \(\) => loadStatus\(\)\)/, "looks again when Connections is closed after a change");
});

let failures = 0;
for (const [name, fn] of tests) {
  try { fn(); console.log(`ok    ${name}`); } catch (err) { failures++; console.log(`FAIL  ${name}\n${err.stack}`); }
}
console.log(`\n${tests.length - failures} of ${tests.length} passed`);
process.exit(failures ? 1 : 0);
