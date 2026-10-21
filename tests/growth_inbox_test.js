"use strict";
/* Run: node tests/growth_inbox_test.js
   The Growth Studio Inbox tab (web/growth-inbox.js): what it says while it loads and when Outreach is not connected, the
   list and the filters, an opened reply, the actions it offers, and that everything from the server is escaped and only a
   plain single-address mail link is ever made. Nothing here talks to a server. */
const assert = require("node:assert/strict");
const path = require("node:path");
const I = require(path.join(__dirname, "..", "web", "growth-inbox.js"));

const ready = { ready: true, mode: "test" };
const item = (o) => Object.assign({ id: "m1", to: "jo@alpha.example", subject: "Gas servicing", status: "replied", sent_at: "2026-11-03T10:30:00Z", replied_at: null,
  buyer: "Alpha Council", campaign_name: "Gas renewals", unread: true }, o);
const model = (o) => Object.assign({ status: "ok", info: ready, filter: "replies", q: "", items: [item()], nextCursor: null, loading: false, loadingMore: false,
  error: null, openId: null, details: {}, marked: [] }, o);
const detail = (o) => Object.assign({
  message: { id: "m1", subject: "Gas servicing", status: "replied", original: "Hello Alpha", bounce_reason: null,
    replies: [{ text: "Thanks, please call Monday.", from: "sam@alpha.example", at: "2026-11-04T09:00:00Z", subject: null }] },
  buyer: "Alpha Council", campaign: { id: 5, name: "Gas renewals" }, target_id: 9, target_status: "replied", reply_to: "sam@alpha.example", opt_out_hint: false }, o);

const tests = [];
const test = (n, f) => tests.push([n, f]);

test("while the status loads it says so; when Outreach is not connected it gives the reason and no list", () => {
  assert.match(I.inboxPane({ status: "loading", info: null }), /Checking Outreach/);
  assert.match(I.inboxPane(undefined), /Checking Outreach/);
  const html = I.inboxPane(model({ info: { ready: false, configured: false, problem: "Outreach is not connected. Ask your administrator to set OUTREACH_API_KEY.", connect_url: "https://outreach.example/x" } }));
  assert.match(html, /OUTREACH_API_KEY/);
  assert.ok(!html.includes("gs-in-list") && !html.includes("in-filter"));
});

test("the four filters are offered and the chosen one is pressed", () => {
  const html = I.inboxPane(model({ filter: "bounced" }));
  for (const f of ["replies", "sent", "bounced", "all"]) assert.match(html, new RegExp(`data-filter="${f}"`));
  assert.match(html, /data-filter="bounced"[^>]*aria-pressed="true"|aria-pressed="true"[^>]*data-filter="bounced"/);
  assert.match(html, /data-filter="replies"[^>]*aria-pressed="false"/);
});

test("the Inbox lists the Outreach mailboxes and the user's own email account, with a button that opens Connections on this page", () => {
  const info = { ready: true, configured: true,
    wallet: { mailboxes: [{ id: "mb_1", email: "sales@tvd.example", can_send: true, status: "active" }, { id: "mb_2", email: "bids@tvd.example", can_send: false, status: "warming_up" }] } };
  const html = I.accountsCard(info);
  assert.match(html, /Email accounts/);
  assert.match(html, /sales@tvd\.example<\/span> <span class="gs-badge gs-badge--muted">Outreach<\/span> <span class="gs-badge gs-badge--good">Can send/);
  assert.match(html, /bids@tvd\.example<\/span> <span class="gs-badge gs-badge--muted">Outreach<\/span> <span class="gs-badge gs-badge--warn">Cannot send yet<\/span> <span class="gs-hint">\(warming up\)/);
  assert.ok(!html.includes("mb_1"), "ids are not shown");
  assert.match(html, /<button type="button" class="gs-btn gs-btn--sm gs-btn--primary"[^>]*data-open-connections="mail"|<button type="button" class="gs-btn gs-btn--sm" data-open-connections="mail"/);
  assert.match(html, /＋ Add an email account<\/button>/);
  assert.ok(!/<a |href=|target="_blank"|data-outreach-link/.test(html), "nothing opens in another tab or window");
  assert.match(html, /data-action="outreach-check"[^>]*>Refresh accounts</);
  assert.match(I.inboxPane(model({ info })), /gs-in-accounts/);
  const own = I.accountsCard(Object.assign({}, info, { mail_account: { from_email: "sam@acme.example", verified: true, sent_today: 3, daily_cap: 100 } }));
  assert.match(own, /sam@acme\.example<\/span> <span class="gs-badge gs-badge--accent">Your email account<\/span> <span class="gs-badge gs-badge--good">Can send<\/span> <span class="gs-hint">3 of 100 sent in the last 24 hours/);
  assert.match(own, />Email account settings<\/button>/, "with an account set up the button changes it");
});

test("with no email account anywhere it says nothing can be sent and makes Add the main button", () => {
  const html = I.accountsCard({ ready: true, configured: true, wallet: { mailboxes: [] } });
  assert.match(html, /No email account is set up yet, so nothing can be sent/);
  assert.match(html, /class="gs-btn gs-btn--sm gs-btn--primary"[^>]*data-open-connections="mail"/);
  const notYet = I.accountsCard({ ready: false, configured: false, wallet: null });
  assert.match(notYet, /data-open-connections="mail"/);
});

test("names are escaped, and an unverified account is not shown as able to send", () => {
  const bad = I.accountsCard({ configured: true, wallet: { mailboxes: [{ email: '"><img src=x onerror=alert(1)>@x.example' }] }, mail_account: { from_email: '"><b>@x.example', verified: false, daily_cap: 5 } });
  assert.ok(!/<img src=x/.test(bad) && !/<b>@x/.test(bad));
  assert.match(bad, /Not checked yet/);
  assert.equal(I.accountsCard(undefined).includes("Email accounts"), true);
});

test("when Outreach is not connected the Inbox still offers the email account and a way to connect Outreach", () => {
  const html = I.inboxPane(model({ info: { ready: false, configured: false, problem: "Outreach is not connected." } }));
  assert.match(html, /Outreach is not connected\./);
  assert.match(html, /gs-in-accounts/);
  assert.match(html, /data-open-connections="outreach"[^>]*>Connect Outreach</);
  assert.ok(!html.includes("target=\"_blank\""));
});

test("every row has a Delete button beside it (not inside the row button), and an opened email has one too", () => {
  const html = I.rowHtml(item(), false);
  assert.match(html, /<button type="button" class="gs-in-del" data-action="in-delete" data-id="m1"[^>]*>Delete<\/button>/);
  const row = html.match(/<button type="button" class="gs-in-row"[\s\S]*?<\/button>/)[0];
  assert.ok(!row.includes("in-delete"), "a button cannot sit inside another button");
  const open = I.rowHtml(item(), detail());
  assert.equal((open.match(/data-action="in-delete"/g) || []).length, 2, "the row's and the opened email's");
  assert.match(open, /gs-btn--danger" data-action="in-delete" data-id="m1">Delete</);
  assert.ok(!I.detailHtml({ message: { status: "replied", replies: [] } }).includes("in-delete"), "no id, nothing to delete");
});

test("the footnote says Delete only removes it from this list, because Outreach keeps its own record", () => {
  const html = I.inboxPane(model());
  assert.match(html, /Delete removes an email from this list only: Outreach keeps its own record and the email stays sent/);
});

test("a hostile id cannot break out of the delete button", () => {
  const html = I.rowHtml(item({ id: 'a"><img src=x onerror=alert(1)>' }), false);
  assert.ok(!/<img src=x/.test(html));
});

test("a row shows the buyer, the address, the subject, the campaign, a status and New for an unread reply", () => {
  const html = I.rowHtml(item(), false);
  assert.match(html, /Alpha Council/);
  assert.match(html, /jo@alpha\.example/);
  assert.match(html, /Gas servicing/);
  assert.match(html, /Gas renewals/);
  assert.match(html, /Replied/);
  assert.match(html, />New</);
  assert.match(html, /is-unread/);
  assert.match(html, /3 Nov 2026/);
  const read = I.rowHtml(item({ unread: false, buyer: null, campaign_name: null, subject: null, replied_at: "2026-11-05T08:00:00Z" }), false);
  assert.ok(!read.includes(">New<") && !read.includes("is-unread"));
  assert.match(read, /jo@alpha\.example/);
  assert.match(read, /\(no subject\)/);
  assert.match(read, /5 Nov 2026/);
});

test("an empty list explains itself for each filter", () => {
  assert.match(I.inboxPane(model({ items: [] })), /No replies yet/);
  assert.match(I.inboxPane(model({ items: [], filter: "bounced" })), /No bounced emails/);
  assert.match(I.inboxPane(model({ items: [], loading: true })), /Reading your emails from Outreach/);
  assert.match(I.inboxPane(model({ items: [], error: "Outreach said no." })), /Outreach said no\./);
});

test("an opened reply shows the text, who sent it, our email and the actions", () => {
  const html = I.inboxPane(model({ openId: "m1", details: { m1: detail() } }));
  assert.match(html, /Thanks, please call Monday\./);
  assert.match(html, /sam@alpha\.example/);
  assert.match(html, /The email you sent/);
  assert.match(html, /Hello Alpha/);
  assert.match(html, /href="mailto:sam@alpha\.example\?subject=Re%3A%20Gas%20servicing"/);
  assert.match(html, /data-action="in-meeting" data-target="9" data-campaign="5"/);
  assert.match(html, /data-action="in-optout"/);
  assert.match(html, /aria-expanded="true"/);
  assert.ok(!html.includes("looks like a request to stop"));
});

test("an opened row that is still loading says so, and a failed one shows why", () => {
  assert.match(I.inboxPane(model({ openId: "m1", details: {} })), /Opening…/);
  assert.match(I.inboxPane(model({ openId: "m1", details: { m1: { error: "No such email." } } })), /No such email\./);
});

test("a reply that asks to stop is flagged; one already on the list offers no second opt-out", () => {
  assert.match(I.detailHtml(detail({ opt_out_hint: true })), /looks like a request to stop/);
  const done = I.detailHtml(detail({ target_status: "opted_out" }));
  assert.ok(!done.includes('data-action="in-optout"') && !done.includes('data-action="in-meeting"'));
  assert.match(done, /On your do-not-contact list/);
});

test("a bounce shows its reason, and a reply with no text says so", () => {
  const bounced = I.detailHtml(detail({ message: { status: "bounced", bounce_reason: "Mailbox full <b>x</b>", replies: [], original: null } }));
  assert.match(bounced, /Bounced: Mailbox full &lt;b&gt;x&lt;\/b&gt;/);
  assert.ok(!bounced.includes("<b>x"));
  assert.match(I.detailHtml(detail({ message: { status: "replied", replies: [] } })), /has not given the reply text/);
  assert.match(I.detailHtml(detail({ message: { status: "sent", replies: [] } })), /No reply yet/);
});

test("everything from the server is escaped", () => {
  const evil = "<img src=x onerror=alert(1)>";
  const row = I.rowHtml(item({ id: "m\"1", buyer: evil, to: evil, subject: evil, campaign_name: evil }), false);
  assert.ok(!row.includes("<img") && !row.includes('m"1'));
  const html = I.detailHtml(detail({ buyer: evil, message: { subject: evil, status: "replied", original: evil,
    replies: [{ text: "<script>alert(1)</script>", from: evil, at: evil, subject: evil }] }, opt_out_hint: true }));
  assert.ok(!html.includes("<script>") && !html.includes("<img"));
});

test("a mail link is made only for one plain address", () => {
  assert.equal(I.mailtoHref("a@b.example", "Hi"), "mailto:a@b.example?subject=Re%3A%20Hi");
  assert.equal(I.mailtoHref("a@b.example", "RE: Hi"), "mailto:a@b.example?subject=RE%3A%20Hi");
  assert.equal(I.mailtoHref("a@b.example", ""), "mailto:a@b.example");
  for (const bad of ["", null, "no address", "a@b", "a@b.example,c@d.example", "a@b.example?bcc=x@y.z", "a b@c.example", "<a@b.example>", "a@b.example\nBcc: x@y.z", "javascript:alert(1)"]) {
    assert.equal(I.mailtoHref(bad, "x"), null, String(bad));
  }
  const html = I.detailHtml(detail({ reply_to: "a@b.example?bcc=x@y.z" }));
  assert.ok(!html.includes("mailto:"));
});

test("a buyer who just replied is announced, and further pages can be asked for", () => {
  const html = I.inboxPane(model({ marked: ["Alpha Council"], nextCursor: "c2" }));
  assert.match(html, /Alpha Council replied\. That buyer is now marked as replied/);
  assert.match(html, /data-action="in-more"/);
  assert.ok(!I.inboxPane(model()).includes("in-more"));
});

let failed = 0;
for (const [name, fn] of tests) {
  try {
    fn();
    console.log("ok    " + name);
  } catch (err) {
    failed++;
    console.log("FAIL  " + name + "\n" + err.stack);
  }
}
console.log(`\n${tests.length - failed} of ${tests.length} passed`);
process.exit(failed ? 1 : 0);
