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
    assert.ok(!/<a\b/.test(html), "no links");
    assert.ok(!/data-outreach-link|target="_blank"|href=/.test(html));
  }
});

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
  assert.match(html, /Connecting your own Outreach account is not switched on for this server\. Ask your administrator to set OUTREACH_CLIENT_ID \(sign in\) or MAIL_CRED_KEY \(paste a key\), or use your organisation's connection/);
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

test("without OUTREACH_CONSENT_VERSION the sign-in and sign-up forms say what to set and cannot be sent", () => {
  for (const mode of ["signin", "signup"]) {
    const html = C.outreachCard(partner({ partner_consent_version: false }, { mode }));
    assert.match(html, /role="alert">Outreach needs the version of the consent text with every sign-in\. Ask your administrator to set OUTREACH_CONSENT_VERSION/);
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

const html = fs.readFileSync(path.join(__dirname, "..", "web", "index.html"), "utf8");
test("index.html has the top bar button, the Account menu item and loads the files", () => {
  assert.match(html, /<button id="btnConnections" class="topbar-btn topbar-btn--connections" type="button">/);
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
const account = { provider: "gmail", from_name: "Sam Lee", from_email: "sam@acme.example", host: "smtp.gmail.com", port: 465, security: "ssl",
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
  const html = C.mailCard(mailModel({ info: { available: true, account, presets }, form: form({ from_email: "sam@acme.example", username: "sam@acme.example" }) }));
  assert.match(html, /Password \(leave blank to keep the saved one\)/);
  assert.match(html, />Save changes<\/button>/);
  assert.match(html, /data-cx="mail-cancel"[^>]*>Cancel<\/button>/);
});

test("when the server cannot keep an account, or cannot be reached, the card says so", () => {
  const off = C.mailCard(mailModel({ info: { available: false, account: null, presets } }));
  assert.match(off, /not switched on for this server\. Ask your administrator to set MAIL_CRED_KEY/);
  assert.ok(!off.includes("<input") && !off.includes("<form"));
  const err = C.mailCard({ status: "error", info: null, error: "Could not reach the server." });
  assert.match(err, /role="alert">Could not reach the server\./);
  assert.match(err, /data-cx="mail-reload">Try again</);
  assert.match(C.mailCard(undefined), /Checking…/);
});

test("the window shows both cards, Outreach first, and neither opens anything", () => {
  const html = C.connectionsHtml(Object.assign(connected(), { mail: mailModel() }));
  assert.ok(html.indexOf('aria-label="Outreach"') > 0 && html.indexOf('aria-label="Email account"') > html.indexOf('aria-label="Outreach"'));
  assert.match(html, /data-card="outreach"/);
  assert.match(html, /data-card="mail"/);
  assert.ok(!/<a\b|href=|target="_blank"/.test(html));
  assert.match(C.connectionsHtml(connected()), /aria-label="Email account"/, "a model with no mail state still draws the card");
});

test("the controller sends the password only to the server, clears it at once, asks before disconnecting the account and announces a change", () => {
  assert.match(src, /call\("PUT", "\/api\/growth\/email-account", body\)/);
  assert.match(src, /call\("POST", "\/api\/growth\/email-account\/test", \{\}\)/);
  assert.match(src, /call\("DELETE", "\/api\/growth\/email-account"\)/);
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
  assert.match(src, /if \(!\(await ask\("Disconnect this email account\?/);
  assert.match(src, /f\.key = ""/, "the key is cleared from the page once used");
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
