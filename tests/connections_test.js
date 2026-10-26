"use strict";
/* Run: node tests/connections_test.js
   The Connections window (web/connections.js): the Outreach card in each state (checking, could not check, not connected,
   disconnected, needs attention, connected through the organisation's key or the user's own), the Connect form and the
   Disconnect button, that nothing opens another window, that no key is ever drawn back onto the page, and that nothing from
   the server can inject markup. No browser: only the pure view is run. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const C = require(path.join(__dirname, "..", "web", "connections.js"));

const wallet = { credits: 40, mailboxes: [{ id: "mb_1", email: "sales@tvd.example", can_send: true, status: "active" }, { id: "mb_2", email: "bids@tvd.example", can_send: false, status: "warming_up" }],
  social_channels: ["linkedin", "facebook", "x"], phone_numbers: 2 };
const connected = (o, extra) => Object.assign({ status: "ok", error: null, busy: false, form: { key: "", busy: false, error: null },
  info: Object.assign({ ready: true, configured: true, mode: "test", source: "shared", shared_available: true, can_store_key: true, key_hint: "sk_test_…", wallet }, o) }, extra);
const notConnected = (o, extra) => Object.assign({ status: "ok", error: null, busy: false, form: { key: "", busy: false, error: null },
  info: Object.assign({ ready: false, configured: false, source: "none", shared_available: false, can_store_key: true, problem: "Outreach is not connected." }, o) }, extra);

const tests = [];
const test = (n, f) => tests.push([n, f]);

test("the state follows the status answer", () => {
  assert.equal(C.outreachState(undefined), "loading");
  assert.equal(C.outreachState({ status: "idle" }), "loading");
  assert.equal(C.outreachState({ status: "loading", info: null }), "loading");
  assert.equal(C.outreachState({ status: "error", info: null, error: "x" }), "error");
  assert.equal(C.outreachState(notConnected()), "none");
  assert.equal(C.outreachState(notConnected({ source: "disconnected", shared_available: true })), "off");
  assert.equal(C.outreachState(notConnected({ configured: true, problem: "bad key" })), "attention");
  assert.equal(C.outreachState(notConnected({ source: "own", configured: false })), "attention", "a saved key that cannot be read needs attention");
  assert.equal(C.outreachState(connected()), "ok");
  assert.equal(C.outreachState(connected({}, { status: "loading" })), "ok", "a refresh keeps showing what it knew");
});

test("the window says it connects and disconnects without leaving the page, and that the key is never shown again", () => {
  const html = C.connectionsHtml(connected());
  assert.match(html, /<h2 id="connectionsTitle"[^>]*>Connections<\/h2>/);
  assert.match(html, /aria-label="Close connections"/);
  assert.match(html, /Connect or disconnect the apps TenderFlow works with, without leaving this page/);
  assert.match(html, /Your key and your password are kept encrypted and are never shown again/);
});

test("nothing in the window opens another window or leaves the page", () => {
  for (const m of [connected(), connected({ source: "own" }), notConnected(), notConnected({ source: "disconnected", shared_available: true }),
    notConnected({ configured: true, problem: "bad" }), { status: "loading", info: null }, { status: "error", info: null, error: "x" }]) {
    const html = C.connectionsHtml(m);
    onlyOutreachLinks(html);
  }
});

// The one deliberate exception: linking a social account or a phone number happens on Outreach's own site.
function onlyOutreachLinks(html) {
  for (const a of html.match(/<a\b[^>]*>/g) || []) {
    assert.match(a, /href="https:\/\/outreach\.aivhub\.com(\/#\/(social|voice))?"/, "only Outreach");
    assert.match(a, /rel="noopener"/);
  }
  assert.ok(!/data-outreach-link/.test(html));
}

test("connected through the organisation's key: says so, lists email, social, calls, and offers Refresh and Disconnect", () => {
  const html = C.outreachCard(connected());
  assert.match(html, /cx-card--ok/);
  assert.match(html, />Connected</);
  assert.match(html, /Connected through your organisation's Outreach key/);
  assert.match(html, /Test key/);
  assert.match(html, /nothing is sent, posted or charged/);
  assert.match(html, /<strong>40<\/strong> <span class="cx-sub">credits left/);
  assert.match(html, /sales@tvd\.example<\/span> <span class="cx-pill cx-pill--good">Can send/);
  assert.match(html, /bids@tvd\.example<\/span> <span class="cx-pill cx-pill--warn">Cannot send yet<\/span> <span class="cx-sub">\(warming up\)/);
  assert.match(html, /LinkedIn, Facebook, X/);
  assert.match(html, /2 phone numbers/);
  assert.ok(!html.includes("mb_1"), "mailbox ids are not shown");
  assert.match(html, /data-action="outreach-check">Refresh<\/button>/);
  assert.match(html, /<button type="button" class="cx-btn cx-btn--danger" data-cx="disconnect"[^>]*>Disconnect<\/button>/);
  assert.ok(!html.includes("cx-form") && !html.includes("<input"), "no key box while connected");
  assert.match(html, /Mailboxes, social channels and phone numbers in your Outreach account show here\. To send email without an Outreach mailbox, set up your own email account below/);
});

test("connected with the user's own key: says so with the first characters only", () => {
  const html = C.outreachCard(connected({ source: "own", key_hint: "sk_test_…" }));
  assert.match(html, /Connected with your own Outreach key \(<code>sk_test_…<\/code>\)/);
  assert.match(html, /data-cx="disconnect"/);
  assert.match(C.outreachCard(connected({ mode: "live" })), /Live key[\s\S]*Approving a post or sending an email is real/);
});

test("connected with nothing set up in Outreach says so plainly", () => {
  const html = C.outreachCard(connected({ wallet: { credits: 0, mailboxes: [], social_channels: [], phone_numbers: 0 } }));
  assert.match(html, /No email account is connected in your Outreach account yet/);
  assert.match(html, /No social channel connected/);
  assert.match(html, /No phone number connected/);
  assert.match(C.outreachCard(connected({ wallet: { mailboxes: [] } })), /Outreach did not say/);
});

test("not connected: a Connect form takes the key in a password box, and nothing is pre-filled from the server", () => {
  const html = C.outreachCard(notConnected());
  assert.match(html, /cx-card--none/);
  assert.match(html, />Not connected</);
  assert.match(html, /<form class="cx-form" data-cx="connect-form" data-mode="key" autocomplete="off">/);
  assert.match(html, /<input id="cxKey" class="cx-input" type="password" name="outreach_key" data-cx="key" autocomplete="off" spellcheck="false" maxlength="200" placeholder="sk_test_…" value="">/);
  assert.match(html, /<button type="submit" class="cx-btn cx-btn--primary" data-cx="connect">Connect<\/button>/);
  assert.match(html, /TenderFlow checks it with Outreach, keeps it encrypted and never shows it again/);
  assert.ok(!html.includes('data-cx="use-shared"'), "no organisation connection to fall back on");
  assert.ok(!html.includes('data-cx="disconnect"'), "nothing to disconnect");
});

test("disconnected: says Growth Studio is off, and offers the key box and, when there is one, the organisation's connection", () => {
  const html = C.outreachCard(notConnected({ source: "disconnected", shared_available: true }));
  assert.match(html, /cx-card--off/);
  assert.match(html, />Disconnected</);
  assert.match(html, /You disconnected Outreach\. Growth Studio will not send, post or call until you connect it again/);
  assert.match(html, /data-cx="connect">Connect</);
  assert.match(html, /<button type="button" class="cx-btn" data-cx="use-shared"[^>]*>Use my organisation's connection<\/button>/);
});

test("when the server cannot store keys, the form says why instead of offering a box that cannot work", () => {
  const html = C.outreachCard(notConnected({ can_store_key: false, shared_available: true }));
  assert.match(html, /Connecting Outreach isn't available on your plan yet\. You can still build campaigns and export the list to send from your own email, or use your organisation's connection/);
  assert.ok(!html.includes("<input") && !html.includes('data-cx="connect"'));
  assert.match(html, /data-cx="use-shared"/);
  const none = C.outreachCard(notConnected({ can_store_key: false, shared_available: false }));
  assert.ok(!none.includes("or use your organisation"));
});

test("the form shows what is happening and why it failed, and never puts the typed key into the page text", () => {
  const busy = C.outreachCard(notConnected({}, { form: { key: "sk_test_secret123", busy: true, error: null } }));
  assert.match(busy, /data-cx="connect" disabled>Connecting…/);
  const failed = C.outreachCard(notConnected({}, { form: { key: "", busy: false, error: "Outreach did not accept that key. Check you copied all of it." } }));
  assert.match(failed, /role="alert">Outreach did not accept that key/);
  // a key typed but not yet sent is kept in the box while the card redraws (value attribute), and only there
  const typed = C.outreachCard(notConnected({}, { form: { key: "sk_test_abc", busy: false, error: "x" } }));
  assert.equal((typed.match(/sk_test_abc/g) || []).length, 1);
  assert.match(typed, /value="sk_test_abc"/);
});

const partner = (o, form) => notConnected(Object.assign({ partner_enabled: true, partner_signup: true }, o), form ? { form: Object.assign({ mode: null, key: "", email: "", password: "", company: "", name: "", consent: false, busy: false, error: null, notice: null }, form) } : {});

test("with the partner programme on, the user can sign in, create an account or paste a key, and signing in is first", () => {
  const html = C.outreachCard(partner());
  assert.deepEqual(C.modesFor({ partner_enabled: true, partner_signup: true, can_store_key: true }), ["signin", "signup", "key"]);
  assert.deepEqual(C.modesFor({ partner_enabled: true, partner_signup: false, can_store_key: true }), ["signin", "key"]);
  assert.deepEqual(C.modesFor({ partner_enabled: false, can_store_key: true }), ["key"]);
  assert.deepEqual(C.modesFor({ partner_enabled: true, partner_signup: true, can_store_key: false }), ["signin", "signup"]);
  assert.deepEqual([...html.matchAll(/data-cx="mode" data-mode="(\w+)"/g)].map((m) => m[1]), ["signin", "signup", "key"]);
  assert.match(html, /data-mode="signin" aria-selected="true">Sign in</);
  assert.match(html, /data-mode="signin" autocomplete="off"/);
});

test("without OUTREACH_CONSENT_VERSION the sign-in and sign-up forms show a plain message and cannot be sent", () => {
  for (const mode of ["signin", "signup"]) {
    const html = C.outreachCard(partner({ partner_consent_version: false }, { mode }));
    assert.match(html, /role=\"alert\">Signing in to Outreach isn't available just now/);
    assert.match(html, /data-cx="connect" disabled>/);
  }
  assert.ok(!C.outreachCard(partner({ partner_consent_version: true }, { mode: "signin" })).includes("OUTREACH_CONSENT_VERSION"));
  assert.match(C.outreachCard(partner({ partner_consent_version: false }, { mode: "key" })), /data-cx="connect">Connect/, "the key form does not need it");
});

test("sign in asks for the Outreach email and password, says the password is not kept, and never fills the password back in", () => {
  const html = C.outreachCard(partner({}, { mode: "signin", email: "sam@acme.example", password: "hunter2-secret" }));
  assert.match(html, /<input id="cxEmail" class="cx-input" type="email" name="outreach_email" data-cx="email" autocomplete="username" maxlength="254" value="sam@acme\.example">/);
  assert.match(html, /<input id="cxPass" class="cx-input" type="password" name="outreach_password" data-cx="password" autocomplete="current-password" maxlength="200" value="">/);
  assert.ok(!html.includes("hunter2-secret"), "the password is never drawn back onto the page");
  assert.match(html, /Your password goes straight to Outreach over a secure connection\. TenderFlow does not keep it/);
  assert.match(html, /<input type="checkbox" data-cx="consent"> I agree to connect my Outreach account to TenderFlow/, "linking an account is a permission the user gives");
  assert.ok(!/data-cx="consent" checked/.test(html));
  assert.match(C.outreachCard(partner({}, { mode: "signin", consent: true })), /data-cx="consent" checked/);
  assert.match(html, /data-cx="forgot"[^>]*>Forgot your password\?/);
  assert.match(html, /Signed up to Outreach with Google\? Signing in from another app asks for an email and password, not a Google button/);
  assert.match(html, /data-cx="resend"[^>]*>Send the confirmation email again/);
  assert.match(html, /<button type="submit" class="cx-btn cx-btn--primary" data-cx="connect">Sign in<\/button>/);
});

test("create account asks for company, name, email, a new password and the consent box, which is unticked until the user ticks it", () => {
  const html = C.outreachCard(partner({}, { mode: "signup" }));
  for (const f of ['data-cx="company"', 'data-cx="name"', 'data-cx="email"', 'autocomplete="new-password"']) assert.ok(html.includes(f), f);
  assert.match(html, /<input type="checkbox" data-cx="consent"> I agree that an Outreach account is created for my company/);
  assert.ok(!/data-cx="consent" checked/.test(html));
  assert.match(C.outreachCard(partner({}, { mode: "signup", consent: true })), /data-cx="consent" checked/);
  assert.match(html, /Outreach may email you to confirm your address/);
  assert.match(html, />Create account<\/button>/);
});

test("without the signup setting only Sign in and Use a key are offered, and a missing MAIL_CRED_KEY hides the key tab", () => {
  const html = C.outreachCard(partner({ partner_signup: false }));
  assert.ok(!html.includes('data-mode="signup"'));
  const noStore = C.outreachCard(partner({ partner_signup: false, can_store_key: false }));
  assert.ok(!noStore.includes('data-cx="mode"'), "one way only, so no tabs");
  assert.match(noStore, /data-mode="signin"/);
});

test("after a signup that needs the email confirmed, the form moves to sign in with a note and the email kept", () => {
  const html = C.outreachCard(partner({}, { mode: "signin", email: "sam@acme.example", notice: "We sent a confirmation email to sam@acme.example. Confirm it, then come back here and sign in." }));
  assert.match(html, /role="status">We sent a confirmation email to sam@acme\.example\. Confirm it/);
  assert.match(html, /value="sam@acme\.example"/);
  assert.match(html, /data-mode="signin" aria-selected="true"/);
});

test("the partner forms show progress and errors, and everything typed or returned is escaped", () => {
  assert.match(C.outreachCard(partner({}, { mode: "signin", busy: true })), /data-cx="connect" disabled>Signing in…/);
  assert.match(C.outreachCard(partner({}, { mode: "signup", busy: true })), /data-cx="connect" disabled>Creating…/);
  assert.match(C.outreachCard(partner({}, { mode: "signin", error: "That email and password do not match an Outreach account." })), /role="alert">That email and password do not match/);
  const evil = '"><img src=x onerror=alert(1)>';
  const html = C.outreachCard(partner({}, { mode: "signup", company: evil, name: evil, email: evil, error: evil, notice: evil }));
  assert.ok(!/<img src=x/.test(html));
});

test("the controller sends the password only to the server, clears it at once, and Disconnect also tells Outreach through the server", () => {
  assert.match(src, /f\.password = "";\s*render\(\);\s*try \{\s*const res = await call\("POST", url, body\)/, "cleared before the request returns");
  assert.match(src, /"\/api\/growth\/outreach\/signin", \{ email: f\.email\.trim\(\), password: f\.password, consent: true \}/, "sign-in carries the user's consent");
  assert.match(src, /"\/api\/growth\/outreach\/signin"/);
  assert.match(src, /"\/api\/growth\/outreach\/signup"/);
  assert.match(src, /"\/api\/growth\/outreach\/forgot"/);
  assert.match(src, /"\/api\/growth\/outreach\/resend"/);
  assert.match(src, /It only sends one for an account created through TenderFlow that is not confirmed yet, and at most 5 times an hour/, "Outreach answers ok even when it sends nothing, so the note does not promise an email");
  assert.match(src, /If \$\{f\.email\.trim\(\)\} has an Outreach account/, "nor does it say whether an account exists");
  assert.ok(!/localStorage|sessionStorage|document\.cookie/.test(src), "nothing typed is kept in the browser");
  assert.match(src, /searchParams\.get\("outreach"\) === "connected"/, "the page Outreach sends users back to opens this window");
});

test("needs attention: the reason is shown with the form to connect again, Check again and Disconnect", () => {
  const html = C.outreachCard(notConnected({ configured: true, source: "own", problem: "Outreach did not accept the API key." }));
  assert.match(html, /cx-card--attention/);
  assert.match(html, />Needs attention</);
  assert.match(html, /role="alert">Outreach did not accept the API key/);
  assert.match(html, /data-cx="key"/);
  assert.match(html, /data-action="outreach-check">Check again/);
  assert.match(html, /data-cx="disconnect"/);
});

test("while it disconnects the button says so and cannot be pressed twice", () => {
  assert.match(C.outreachCard(connected({}, { busy: true })), /data-cx="disconnect" disabled[^>]*>Disconnecting…/);
});

test("while it asks, and when the server cannot be reached", () => {
  const loading = C.outreachCard({ status: "loading", info: null });
  assert.match(loading, /Checking…/);
  assert.match(loading, /Asking Outreach/);
  assert.ok(!loading.includes("<form") && !loading.includes("data-cx=\"disconnect\""));
  const failed = C.outreachCard({ status: "error", info: null, error: "Could not reach the server. Check your connection and try again." });
  assert.match(failed, /Could not check/);
  assert.match(failed, /role="alert">Could not reach the server/);
  assert.match(failed, /data-action="outreach-check">Try again/);
});

test("everything from the server is escaped", () => {
  const evil = '"><img src=x onerror=alert(1)>';
  const html = C.connectionsHtml(connected({ key_hint: evil, source: "own", wallet: { credits: evil, mailboxes: [{ email: evil, can_send: true, status: evil }], social_channels: [evil], phone_numbers: 1 } }));
  assert.ok(!/<img src=x/.test(html), "no markup got through");
  assert.ok(!/onerror="alert/.test(html.replace(/&quot;/g, "")));
  assert.ok(!/<img src=x/.test(C.outreachCard(notConnected({ configured: true, problem: evil }))));
  assert.ok(!/<img src=x/.test(C.outreachCard(notConnected({}, { form: { key: evil, error: evil } }))));
  assert.ok(!/<img src=x/.test(C.outreachCard({ status: "error", info: null, error: evil })));
});

test("each way to connect has its own box with a title, under tabs that look like the channel tabs", () => {
  const f = C.hubHtml(notConnected({ partner_signup: true, partner_enabled: true, can_key: true }), "email");
  assert.match(f, /class="cx-tabpanel cx-formbox"[^>]*data-mode-pane="signin"/);
  assert.match(f, /Sign in with your Outreach account/);
  assert.match(f, /data-cx="mode" data-mode="signup"/, "the other ways stay one click away");
});

const html = fs.readFileSync(path.join(__dirname, "..", "web", "index.html"), "utf8");
test("index.html has the Account menu item, no top bar button, and loads the files", () => {
  assert.ok(!/id="btnConnections"/.test(html), "Connections is opened from the side menu, not the top bar");
  assert.match(html, /<button id="btnConnectionsNav" class="tf-nav" type="button" title="Connected apps">/);
  assert.match(html, /<link rel="stylesheet" href="\/connections\.css\?v=\d+" \/>/);
  assert.match(html, /<script src="\/connections\.js\?v=\d+"><\/script>/);
  assert.ok(fs.existsSync(path.join(__dirname, "..", "web", "connections.css")));
});

// ── a partner sign-in, and the Email account card ───────────────────────────────────────────────

test("connected through a partner sign-in it says it is the user's Outreach account, not a pasted key", () => {
  const html = C.outreachCard(connected({ source: "own", via: "partner", key_hint: "sk_test_…" }));
  assert.match(html, /Connected to your Outreach account \(<code>sk_test_…<\/code>\)/);
  assert.ok(!/your own Outreach key/.test(html));
});

const presets = [
  { id: "gmail", label: "Gmail / Google Workspace", host: "smtp.gmail.com", port: 465, security: "ssl", help: "Turn on 2-step verification, then create an app password and use it here." },
  { id: "custom", label: "Other (custom SMTP)", host: "", port: 587, security: "starttls", help: "Use the SMTP host your provider gave you." },
];
const account = { id: 7, is_default: true, provider: "gmail", from_name: "Sam Lee", from_email: "sam@acme.example", host: "smtp.gmail.com", port: 465, security: "ssl",
  username: "sam@acme.example", reply_to: "", daily_cap: 100, verified: true, sent_today: 4, last_error: null };
const form = (o) => Object.assign({ provider: "gmail", from_name: "", from_email: "", username: "", password: "", host: "smtp.gmail.com", port: 465, security: "ssl",
  reply_to: "", daily_cap: 100, busy: false, error: null, notice: null }, o);
const mailModel = (o) => Object.assign({ status: "ok", info: { available: true, account: null, presets }, form: form(), busy: null, notice: null, error: null }, o);

test("the email account state follows the answer", () => {
  assert.equal(C.mailState(undefined), "loading");
  assert.equal(C.mailState({ status: "idle" }), "loading");
  assert.equal(C.mailState({ status: "error", info: null }), "error");
  assert.equal(C.mailState(mailModel()), "none");
  assert.equal(C.mailState(mailModel({ info: { available: false, account: null, presets } })), "unavailable");
  assert.equal(C.mailState(mailModel({ info: { available: true, account, presets } })), "ok");
  assert.equal(C.mailState(mailModel({ info: { available: false, account, presets } })), "ok", "an account that is there is shown even if the server cannot use it now");
});

test("not set up: the form asks for the provider, address, login, password, server and limit, and keeps no password on the page", () => {
  const html = C.mailCard(mailModel({ form: form({ from_email: "sam@acme.example", password: "never-drawn-back" }) }));
  assert.match(html, /Email account<\/span> <span class="cx-pill cx-pill--muted">Not set up/);
  assert.match(html, /<select id="cxmProvider" class="cx-input" data-cxm="provider"><option value="gmail" selected>Gmail \/ Google Workspace<\/option><option value="custom">Other \(custom SMTP\)<\/option><\/select>/);
  assert.match(html, /Turn on 2-step verification, then create an app password/, "the provider's own advice is shown");
  for (const k of ["from_name", "from_email", "username", "password", "host", "port", "security", "daily_cap", "reply_to"]) assert.ok(html.includes(`data-cxm="${k}"`), k);
  assert.match(html, /<input id="cxmFrom" class="cx-input" data-cxm="from_email" type="email" autocomplete="email" maxlength="254" value="sam@acme\.example">/);
  assert.match(html, /type="password" name="mail_password" data-cxm="password" autocomplete="new-password" maxlength="200" value="">/);
  assert.ok(!html.includes("never-drawn-back"), "the password is never drawn back onto the page");
  assert.match(html, /<option value="465" selected>465<\/option>/);
  assert.match(html, /<option value="ssl" selected>SSL\/TLS<\/option>/);
  assert.match(html, /TenderFlow checks the login with your mail server before it keeps anything/);
  assert.match(html, /<button type="submit" class="cx-btn cx-btn--primary" data-cx="mail-save">Connect email account<\/button>/);
  assert.ok(!html.includes("mail-cancel"), "nothing to cancel back to");
  assert.ok(!/<a\b|href=|target="_blank"/.test(html));
});

test("the form shows progress and errors, and everything typed or returned is escaped", () => {
  assert.match(C.mailCard(mailModel({ form: form({ busy: true }) })), /data-cx="mail-save" disabled>Checking the login…/);
  assert.match(C.mailCard(mailModel({ form: form({ error: "The mailbox rejected the login." }) })), /role="alert">The mailbox rejected the login\./);
  const evil = '"><img src=x onerror=alert(1)>';
  const html = C.mailCard(mailModel({ form: form({ from_name: evil, from_email: evil, username: evil, host: evil, reply_to: evil, error: evil, notice: evil }) }));
  assert.ok(!/<img src=x/.test(html));
});

test("set up: it names the address and server, the count sent in the last day, and offers Send test email, Edit and Disconnect", () => {
  const html = C.mailCard(mailModel({ info: { available: true, account, presets }, form: null, notice: "Email account connected." }));
  assert.match(html, /cx-card--ok/);
  assert.match(html, /cx-pill--good">Connected</);
  assert.match(html, /Sam Lee &lt;sam@acme\.example&gt;<\/span> <span class="cx-pill cx-pill--good">Login checked/);
  assert.match(html, /Sends through smtp\.gmail\.com \(port 465\) · 4 of 100 sent in the last 24 hours/);
  assert.match(html, /role="status">Email account connected\./);
  assert.match(html, /data-cx="mail-test"[^>]*>Send test email<\/button>/);
  assert.match(html, /data-cx="mail-edit"[^>]*>Edit<\/button>/);
  assert.match(html, /<button type="button" class="cx-btn cx-btn--danger" data-cx="mail-delete"[^>]*>Disconnect<\/button>/);
  assert.ok(!html.includes("<input"), "no form while it is set up");
  const busy = C.mailCard(mailModel({ info: { available: true, account, presets }, form: null, busy: "test" }));
  assert.match(busy, /data-cx="mail-test" disabled[^>]*>Sending…/);
  assert.match(busy, /data-cx="mail-edit" disabled/);
  assert.match(C.mailCard(mailModel({ info: { available: true, account, presets }, form: null, busy: "delete" })), />Disconnecting…</);
});

test("a send that failed marks the card as needing attention and says why", () => {
  const html = C.mailCard(mailModel({ info: { available: true, account: Object.assign({}, account, { last_error: "The mailbox rejected the login." }), presets }, form: null }));
  assert.match(html, /cx-card--attention/);
  assert.match(html, /cx-pill--warn">Needs attention</);
  assert.match(html, /The last send failed: The mailbox rejected the login\./);
});

test("editing keeps the saved password unless a new one is typed, and can be cancelled", () => {
  const html = C.mailCard(mailModel({ info: { available: true, account, presets }, form: form({ editId: 7, from_email: "sam@acme.example", username: "sam@acme.example" }) }));
  assert.match(html, /Password \(leave blank to keep the saved one\)/);
  assert.match(html, />Save changes<\/button>/);
  assert.match(html, /data-cx="mail-cancel"[^>]*>Cancel<\/button>/);
  assert.match(html, /Changing <span class="cx-mail">sam@acme\.example<\/span>/, "it says which account is being changed");
  assert.ok(!html.includes("Add another address"));
});

// ── several accounts ──────────────────────────────────────────────────────────────────────────

const bids = Object.assign({}, account, { id: 9, is_default: false, from_name: "", from_email: "bids@acme.example", username: "bids@acme.example", sent_today: 0, daily_cap: 50 });
const two = (o) => mailModel(Object.assign({ info: { available: true, account, accounts: [account, bids], max_accounts: 5, presets }, form: null }, o));

test("several accounts: each is its own block with its own buttons, the default is marked and only the others can be made the default", () => {
  const html = C.mailCard(two());
  assert.match(html, /aria-label="Email accounts"/);
  assert.match(html, /Email accounts<\/span> <span class="cx-pill cx-pill--good">Connected</);
  assert.equal((html.match(/class="cx-acct"/g) || []).length, 2);
  assert.match(html, /Sam Lee &lt;sam@acme\.example&gt;<\/span> <span class="cx-pill cx-pill--good">Login checked<\/span> <span class="cx-pill cx-pill--muted">Default</);
  assert.match(html, /<span class="cx-mail">bids@acme\.example<\/span> <span class="cx-pill cx-pill--good">Login checked<\/span><\/p>/, "the second is not marked default");
  assert.match(html, /4 of 100 sent in the last 24 hours/);
  assert.match(html, /0 of 50 sent in the last 24 hours/, "each has its own limit");
  for (const kind of ["mail-test", "mail-edit", "mail-delete"]) {
    assert.match(html, new RegExp(`data-cx="${kind}"[^>]* data-id="7"`));
    assert.match(html, new RegExp(`data-cx="${kind}"[^>]* data-id="9"`));
  }
  assert.equal((html.match(/data-cx="mail-default"/g) || []).length, 1);
  assert.match(html, /data-cx="mail-default"[^>]* data-id="9"[^>]*>Make default</, "the default one has no such button");
  assert.match(html, /the default one is used\./);
  assert.match(html, /data-cx="mail-add"[^>]*>＋ Add another email account</);
  assert.ok(!html.includes("<input"));
});

test("one account looks as it did, with Add another beside it and no default marking", () => {
  const html = C.mailCard(mailModel({ info: { available: true, account, accounts: [account], max_accounts: 5, presets }, form: null }));
  assert.match(html, /aria-label="Email account"/);
  assert.ok(!html.includes("Default") && !html.includes("mail-default"));
  assert.match(html, /data-cx="mail-add"/);
  assert.match(html, /Replies arrive in this mailbox/);
});

test("at the limit there is no Add button, only a reason", () => {
  const five = [account, ...[1, 2, 3, 4].map((n) => Object.assign({}, bids, { id: 20 + n, from_email: `box${n}@acme.example` }))];
  const html = C.mailCard(mailModel({ info: { available: true, accounts: five, max_accounts: 5, presets }, form: null }));
  assert.ok(!html.includes("mail-add"));
  assert.match(html, /most email accounts you can connect \(5\)/);
});

test("adding another account shows a blank form that can be cancelled, and saving it says Connect", () => {
  const html = C.mailCard(two({ form: form({ editId: null }) }));
  assert.match(html, /Add another address to send from\. It has its own daily limit\./);
  assert.match(html, /data-cx="mail-save">Connect email account</);
  assert.match(html, /Password or app password/, "a new account always needs its password");
  assert.match(html, /data-cx="mail-cancel"/);
  assert.ok(!html.includes("Changing"));
});

test("while one account is tested or changed only its button shows progress, but nothing else can be pressed", () => {
  const html = C.mailCard(two({ busy: "test", busyId: 9 }));
  assert.match(html, /data-cx="mail-test" disabled data-id="9"[^>]*>Sending…/);
  assert.match(html, /data-cx="mail-test" disabled data-id="7"[^>]*>Send test email</);
  assert.match(html, /data-cx="mail-add" disabled/);
  assert.match(C.mailCard(two({ busy: "default", busyId: 9 })), /data-cx="mail-default" disabled data-id="9"[^>]*>Saving…/);
  assert.match(C.mailCard(two({ busy: "delete", busyId: 7 })), /data-cx="mail-delete" disabled data-id="7"[^>]*>Disconnecting…[\s\S]*data-cx="mail-delete" disabled data-id="9"[^>]*>Disconnect</);
});

test("one failing account marks the whole card as needing attention and says which, and every address is escaped", () => {
  const html = C.mailCard(two({ info: { available: true, accounts: [account, Object.assign({}, bids, { last_error: "The mailbox rejected the login." })], max_accounts: 5, presets } }));
  assert.match(html, /cx-card--attention/);
  assert.match(html, /cx-pill--warn">Needs attention</);
  assert.equal((html.match(/The last send failed/g) || []).length, 1);
  const evil = '"><img src=x onerror=alert(1)>';
  const bad = C.mailCard(two({ info: { available: true, accounts: [account, Object.assign({}, bids, { from_email: evil, from_name: evil, host: evil })], max_accounts: 5, presets } }));
  assert.ok(!/<img src=x/.test(bad));
});

test("the Email row in the list shows the default address and how many more", () => {
  const row = (accounts) => C.channelStatus(C.CHANNELS[0], { mail: mailModel({ info: { available: true, accounts, max_accounts: 5, presets }, form: null }) });
  assert.deepEqual(row([account]), { on: true, text: "sam@acme.example", attention: false });
  assert.deepEqual(row([account, bids]), { on: true, text: "sam@acme.example +1 more", attention: false });
  assert.equal(row([account, Object.assign({}, bids, { last_error: "x" })]).attention, true);
  assert.deepEqual(C.accountsOf({ account }), [account], "an older answer with only the default account still works");
  assert.deepEqual(C.accountsOf({ account: null }), []);
});

test("when the server cannot keep an account, or cannot be reached, the card says so", () => {
  const off = C.mailCard(mailModel({ info: { available: false, account: null, presets } }));
  assert.match(off, /Connecting an email account isn't available yet/);
  assert.ok(!off.includes("<input") && !off.includes("<form"));
  const err = C.mailCard({ status: "error", info: null, error: "Could not reach the server." });
  assert.match(err, /role="alert">Could not reach the server\./);
  assert.match(err, /data-cx="mail-reload">Try again</);
  assert.match(C.mailCard(undefined), /Checking…/);
});

test("the page has the Outreach card on the left and Outreach with its three steps on the right, and nothing opens another window", () => {
  const html = C.connectionsHtml(Object.assign(connected(), { mail: mailModel() }));
  assert.match(html, /data-card="outreach"/);
  assert.match(html, /Outreach: 0 of 3 channels set up|Outreach: \d of 3 channels set up/);
  assert.ok(html.indexOf("cx-hub__list") < html.indexOf("cx-hub__detail"), "the summary is on the left, Outreach on the right");
  for (const t of ["Sign in to Outreach", "Choose how Outreach reaches people", "Use Outreach in Growth Studio"]) assert.ok(html.includes(t), t);
  assert.ok(html.indexOf("Sign in to Outreach") < html.indexOf("Choose how Outreach reaches people") && html.indexOf("Choose how Outreach reaches people") < html.indexOf("Use Outreach in Growth Studio"), "steps in order");
  // the three channels are tabs, Email open first, and the email account is what the Email tab holds
  for (const c of ["email", "calls", "social"]) assert.match(html, new RegExp(`class="cx-tab[^"]*" data-cx="row" data-view="${c}"`));
  assert.match(html, /class="cx-tab is-on" data-cx="row" data-view="email"/);
  assert.match(html, /data-pane="email"/);
  assert.match(html, /data-cxm="from_email"/, "the form to add an email account is in the Email tab");
  assert.ok(!/data-pane="calls"|data-pane="social"/.test(html), "one channel is open at a time");
  assert.match(html, /Calendar[\s\S]*Coming soon[\s\S]*CRM[\s\S]*Coming soon/);
  onlyOutreachLinks(html);
  assert.match(C.connectionsHtml(connected()), /data-pane="email"/, "a model with no mail state still draws the tab");
});

test("the right panel has Refresh and Disconnect only while Outreach is connected, and a test key gets one clear note", () => {
  const live = C.hubHtml(connected({ mode: "live" }), "social");
  assert.match(live, /cx-mainhead__act">[\s\S]*data-action="outreach-check"[\s\S]*data-cx="disconnect"/);
  assert.ok(!/Test mode/.test(live), "no note for a live key");
  const test = C.hubHtml(connected({ mode: "test" }), "social");
  assert.match(test, /role="note"><strong>Test mode\.<\/strong> Your Outreach key is a test key, so nothing real is sent, posted or connected/);
  assert.equal((test.match(/Test mode\./g) || []).length, 1, "said once, in step 1");
  assert.ok(!/cx-mainhead__act/.test(C.hubHtml(notConnected(), "email")), "nothing to refresh or disconnect before connecting");
});

test("the left side counts the channels that are set up, with a dot for each", () => {
  const none = C.hubHtml(notConnected(), "email");
  assert.match(none, /Outreach: 0 of 3 channels set up/);
  assert.match(none, /Set up email first so campaigns can be sent\./);
  assert.equal((none.match(/class="cx-dot is-on"/g) || []).length, 0);
  const all = connected({ wallet: { credits: 1, mailboxes: [{ id: "mb", email: "a@x.example", can_send: true }], social_channels: ["linkedin"], phone_numbers: 1 } });
  const html = C.hubHtml(all, "email");
  assert.match(html, /Outreach: 3 of 3 channels set up/);
  assert.match(html, /Everything you can connect is connected\./);
  assert.equal((html.match(/class="cx-dot is-on"/g) || []).length, 3);
  assert.match(html, /width:100%/);
});

test("the controller sends the password only to the server, clears it at once, asks before disconnecting the account and announces a change", () => {
  assert.match(src, /call\("PUT", "\/api\/growth\/email-account", body\)/);
  assert.match(src, /call\("POST", "\/api\/growth\/email-account\/test", id == null \? \{\} : \{ id \}\)/);
  assert.match(src, /call\("POST", "\/api\/growth\/email-account\/default", \{ id \}\)/);
  assert.match(src, /call\("DELETE", "\/api\/growth\/email-account" \+ \(id == null \? "" : `\?id=\$\{id\}`\)\)/);
  assert.match(src, /if \(editing\) body\.id = f\.editId;/, "a change names the account; an addition does not");
  assert.match(src, /f\.password = ""; \/\/ gone from the page the moment it is sent[\s\S]*render\(\);\s*try \{\s*mm\.info = await call\("PUT"/, "cleared before the request returns");
  assert.match(src, /new root\.CustomEvent\("connections:changed"\)/, "the panels are told to look again");
  assert.match(src, /closest\("\[data-open-connections\]"\)/, "any button marked for it opens this window");
  assert.match(src, /open\(b, b\.getAttribute\("data-open-connections"\)\)/);
  assert.ok(!/localStorage|sessionStorage|document\.cookie/.test(src));
});

const src = fs.readFileSync(path.join(__dirname, "..", "web", "connections.js"), "utf8");
test("the controller never opens a window, never stores the key in the browser and asks before disconnecting", () => {
  assert.ok(!/window\.open|\.open\(\s*["'`]http|localStorage|sessionStorage/.test(src.replace(/function open\(from, card\)/, "")), "no popup, no browser storage");
  assert.match(src, /call\("POST", "\/api\/growth\/outreach\/connect", body\)/);
  assert.match(src, /call\("DELETE", "\/api\/growth\/outreach\/connect"\)/);
  assert.match(src, /showConfirm[\s\S]*root\.confirm\(text\)/, "a confirmation comes first");
  assert.match(src, /if \(!\(await ask\("Disconnect Outreach\?/);
  assert.match(src, /if \(!\(await ask\(`Disconnect this email account\?/);
  assert.match(src, /f\.key = ""/, "the key is cleared from the page once used");
});

test("Calls and Social set up in steps: sign in here, link the account on Outreach, then open it in Growth Studio", () => {
  const out = notConnected();
  const social = C.hubHtml({ status: "ready", info: out }, "social");
  assert.match(social, /cx-steps[\s\S]*data-pane="social"/);
  assert.match(social, /Available once you have signed in to Outreach/, "the channel waits for step 1");
  assert.match(social, /Sign in to Outreach/);
  assert.match(social, /data-cx="connect-form"/, "the sign-in form is in the Social pane itself");
  assert.equal((social.match(/data-cx="connect-form"/g) || []).length, 1, "one form on the page, so its ids are unique");
  assert.ok(!/data-cx="open-growth"/.test(social), "nothing to open until it is set up");
  const calls = C.hubHtml({ status: "ready", info: out }, "outreach");
  assert.equal((calls.match(/data-cx="connect-form"/g) || []).length, 1);

  const linked = connected();
  linked.info.wallet = Object.assign({}, linked.info.wallet, { social_channels: ["linkedin"], phone_numbers: 0 });
  const html = C.hubHtml(linked, "social");
  assert.match(html, /LinkedIn<\/span> <span class="cx-pill cx-pill--good">Linked/);
  assert.match(html, /Facebook<\/span> <span class="cx-pill cx-pill--muted">Not linked/);
  assert.match(html, /data-cx="open-growth" data-tab="social"/);
  assert.ok(!/<a|target="_blank"/.test(html), "no links");
  assert.match(html, /data-cx="connect-social" data-network="Facebook"/);
  assert.ok(!/data-network="LinkedIn"/.test(html), "a linked network has no Connect button");
  const phone = C.hubHtml(linked, "calls");
  assert.match(phone, /No phone number in your Outreach account yet/);
  assert.ok(!/data-cx="open-growth" data-tab="calls"/.test(phone));
});

// ── connecting social accounts from here (Outreach's channel.* actions) ───────────────────────────────────────
const providers = [{ provider: "linkedin", label: "LinkedIn", available: true }, { provider: "facebook", label: "Facebook", available: true },
  { provider: "instagram", label: "Instagram", available: false }, { provider: "x", label: "X", available: true }, { provider: "threads", label: "Threads", available: true }];
const social = (o) => Object.assign({ status: "ok", supported: true, providers, channels: [], busy: null, notice: null, error: null, origin: "https://outreach.aivhub.com" }, o);

test("each network that can be connected from here has a Connect button, and one that Outreach has not switched on says so", () => {
  const html = C.socialList(social());
  for (const p of ["linkedin", "facebook", "x", "threads"]) assert.match(html, new RegExp(`data-cx="social-connect" data-provider="${p}"`));
  assert.ok(!/data-provider="instagram"/.test(html), "no button that cannot work");
  assert.match(html, /Instagram<\/span> <span class="cx-pill cx-pill--muted">Not available yet<\/span>/);
  assert.match(html, /Connect LinkedIn/);
  assert.match(html, /You sign in on the network's own page, so TenderFlow never sees your password/);
  assert.match(html, /data-cx="social-reload"/);
  assert.ok(!/<a /.test(html) && !/outreach\.aivhub\.com/.test(html), "nothing opens Outreach itself");
});

test("a connected account shows its name and can be disconnected; one that needs reconnecting offers Reconnect", () => {
  const html = C.socialList(social({ channels: [
    { id: "ch_1", provider: "linkedin", label: "LinkedIn", name: "Acme Ltd", status: "connected" },
    { id: "ch_2", provider: "facebook", label: "Facebook", name: "Acme Page", status: "needs_reauth" },
    { id: "ch_3", provider: "x", label: "X", name: "@acme", status: "error" }] }));
  assert.match(html, /LinkedIn · Acme Ltd<\/span> <span class="cx-pill cx-pill--good">Connected<\/span>/);
  assert.match(html, /Facebook · Acme Page<\/span> <span class="cx-pill cx-pill--warn">Needs reconnecting<\/span>/);
  assert.match(html, /X · @acme<\/span> <span class="cx-pill cx-pill--warn">Problem<\/span>/);
  assert.match(html, /data-cx="social-disconnect" data-id="ch_1"/);
  assert.equal((html.match(/>Reconnect</g) || []).length, 2, "only the two that are not working");
  assert.ok(!/data-provider="linkedin"/.test(html), "a working account has no Connect button");
  assert.match(html, /data-provider="threads"/, "networks with nothing connected are still offered");
});

test("while one is opening every button waits, and a notice or an error is shown in its place", () => {
  const html = C.socialList(social({ busy: "linkedin", notice: "LinkedIn is connected.", error: "Outreach said no." }));
  assert.match(html, /Opening…/);
  assert.equal((html.match(/data-cx="social-connect"[^>]* disabled/g) || []).length, 4, "every Connect (four networks are on) is disabled while one is open");
  assert.match(html, /role="status">LinkedIn is connected\./);
  assert.match(html, /role="alert">Outreach said no\./);
  assert.match(C.socialList(social({ status: "loading" })), /Checking…/);
});

test("a test key's sample accounts are never listed as connected", () => {
  // the server sends no channels for a test key, so every network shows as not connected and can be tried
  const none = C.socialList(social({ simulated: true, channels: [] }));
  assert.equal((none.match(/Not connected</g) || []).length, 4, "the four networks that are on");
  assert.match(none, /data-cx="social-connect" data-provider="linkedin"/);
  assert.ok(!/>Try connect</.test(none) && !/Test LinkedIn/.test(none), "no sample account is shown as connected");
  const src = fs.readFileSync(path.join(__dirname, "..", "web", "connections.js"), "utf8");
  assert.match(src, /simulated: !!r\.simulated/);
  assert.match(src, /Nothing real was disconnected/, "after Disconnect on samples it does not claim anything was removed");
});

test("the Social pane uses the real list when Outreach supports it, and the older steps when it does not", () => {
  const out = connected({ wallet: { credits: 1, mailboxes: [], social_channels: [], phone_numbers: 0 } });
  const withSocial = Object.assign({}, out, { social: social({ channels: [{ id: "ch_1", provider: "linkedin", label: "LinkedIn", name: "Acme", status: "connected" }] }) });
  const html = C.hubHtml(withSocial, "social");
  assert.match(html, /data-cx="social-connect" data-provider="facebook"/);
  assert.ok(!/data-cx="connect-social"/.test(html), "the old open-Outreach button is gone");
  assert.match(html, /data-cx="open-growth" data-tab="social"/, "one account is enough to start using Social");
  const unsupported = Object.assign({}, out, { social: social({ supported: false, providers: [], channels: [] }) });
  assert.match(C.hubHtml(unsupported, "social"), /data-cx="connect-social" data-network="Facebook"/, "the older steps still work until Outreach has shipped it");
  assert.match(C.hubHtml(out, "social"), /data-cx="connect-social"/, "and before it has been asked");
  const noAccount = Object.assign({}, out, { social: social() });
  assert.ok(!/data-cx="open-growth" data-tab="social"/.test(C.hubHtml(noAccount, "social")), "nothing to open until an account is connected");
});

test("the Social row in the list shows the connected networks, and warns when one is not working", () => {
  const out = connected({ wallet: { credits: 1, mailboxes: [], social_channels: [], phone_numbers: 0 } });
  const row = (sc) => C.channelStatus(C.CHANNELS.find((c) => c.id === "social"), Object.assign({}, out, { social: sc }));
  assert.deepEqual(row(social()), { on: false, text: "Not connected" });
  const ok = row(social({ channels: [{ id: "a", provider: "linkedin", label: "LinkedIn", status: "connected" }, { id: "b", provider: "x", label: "X", status: "connected" }] }));
  assert.equal(ok.on, true); assert.equal(ok.text, "Outreach · LinkedIn, X"); assert.ok(!ok.attention);
  assert.equal(row(social({ channels: [{ id: "a", provider: "linkedin", label: "LinkedIn", status: "needs_reauth" }] })).attention, true);
  assert.equal(row(social({ supported: false })).on, false);
});

test("everything from Outreach in the social list is escaped", () => {
  const evil = `<img src=x onerror=alert(1)>"'`;
  const html = C.socialList(social({ providers: [{ provider: "linkedin", label: evil, available: true }, { provider: "x", label: evil, available: false }],
    channels: [{ id: evil, provider: "linkedin", label: evil, name: evil, status: "connected" }], notice: evil, error: evil }));
  assert.ok(!/<img src=x/.test(html) && !/onerror=alert\(1\)>"/.test(html), "no markup from the server survives");
});

test("the controller opens the sign-in in a small window, never a tab, checks who a message came from, and refreshes after", () => {
  const src = fs.readFileSync(path.join(__dirname, "..", "web", "connections.js"), "utf8");
  assert.match(src, /root\.open\("about:blank", "outreachSocial" \+ Date\.now\(\), "popup=yes,width=560,height=760"\)/, "opened inside the click so a blocker allows it, under a fresh name so an old window is never reused");
  assert.match(src, /new root\.BroadcastChannel\("tf-outreach-channel"\)/, "the finished window is also heard through a same-site broadcast");
  assert.match(src, /e\.data\.type === "tf\.outreach\.channel"\) channelDone\(e\.data\)/);
  assert.match(src, /w\.location\.href = r\.auth_url/);
  assert.match(src, /e\.origin === model\.social\.origin \|\| e\.origin === OUTREACH_WEB/, "Outreach's message is only trusted from Outreach");
  assert.match(src, /d\.type === "tf\.outreach\.channel" && e\.origin === root\.location\.origin/, "and our own page's message only from this origin");
  assert.match(src, /afterChannelWindow\(\)/);
  const gone = src.slice(src.indexOf("async function disconnectChannel"), src.indexOf("The sign-in window tells this page"));
  assert.match(gone, /sc\.error = e\.message/);
  assert.match(gone, /await loadSocial\(true, true\)/, "the refresh after a failed Disconnect must not wipe the reason it failed");
  assert.match(src, /\/api\/growth\/social\/channels\/connect/);
  assert.ok(!/password|token/i.test(src.slice(src.indexOf("async function connectChannel"), src.indexOf("function afterChannelWindow"))), "no credential is handled here");
});

const numbersState = (o) => Object.assign({ status: "ok", supported: true, numbers: [], busy: null, notice: null, error: null }, o);
test("the Calls tab offers to get a number, and lists each number with its status and a Release button", () => {
  const none = C.numbersList(numbersState());
  assert.match(none, /No phone number in your Outreach account yet/);
  assert.match(none, /data-cx="number-connect"/);
  assert.match(none, /Get a phone number/);
  assert.doesNotMatch(none, /number-release/);
  const some = C.numbersList(numbersState({ numbers: [
    { id: "num_1", number: "+441132000111", status: "active" },
    { id: "num_2", number: "+441132000222", status: "pending" },
    { id: "num_3", number: "+441132000333", status: "rejected", reason: "<b>Bill too old</b>" } ] }));
  assert.match(some, /Ready to call/);
  assert.match(some, /Being checked/);
  assert.match(some, /Not approved/);
  assert.match(some, /Add another number/);
  assert.equal((some.match(/data-cx="number-release"/g) || []).length, 3);
  assert.doesNotMatch(some, /<b>Bill too old/, "what Outreach says is escaped");
  assert.match(some, /5 Voice credits/);
});

let failed = 0;
for (const [name, fn] of tests) {
  try {
    fn();
    console.log("ok    " + name);
  } catch (err) {
    failed++;
    console.log("FAIL  " + name + "\n" + err.message);
  }
}
console.log(`\n${tests.length - failed} of ${tests.length} passed`);
process.exit(failed ? 1 : 0);
