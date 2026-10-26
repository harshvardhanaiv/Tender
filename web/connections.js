/* "Connections": one small window, opened from the top bar, the Account menu or any Growth Studio panel (a button marked
   data-open-connections), that shows what TenderFlow sends through and lets the user connect and disconnect it without leaving
   the page. Two cards: Outreach (email, social posts and calls) and the user's own Email account.
   Outreach: sign in to (or create) the user's own Outreach account right here (Outreach's partner programme; the password goes
   straight to Outreach and is not kept), or paste an Outreach key, or use the organisation's connection; Disconnect forgets the
   key (and tells Outreach) and switches Outreach off for that user.
   Email account: the user's own SMTP login (Gmail, Outlook, Zoho or any provider), checked before it is kept, so Growth Studio can
   send from it when Outreach has no mailbox.
   Nothing opens in another window. When the window is closed after a change, a "connections:changed" event tells the panels to
   check again. Pure view first (tests/connections_test.js runs it in Node), then a small controller. Everything from the server
   goes through esc(). */
(function (root) {
  "use strict";

  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const plural = (n, one, many) => `${n} ${n === 1 ? one : many || one + "s"}`;
  const CHANNEL = { linkedin: "LinkedIn", facebook: "Facebook", instagram: "Instagram", x: "X", twitter: "X" };
  const channelName = (c) => CHANNEL[String(c || "").toLowerCase()] || String(c || "").replace(/^./, (m) => m.toUpperCase());

  // What state the Outreach card is in, from the answer of /api/growth/outreach/status.
  //   loading   not asked yet / asking
  //   error     the page could not reach TenderFlow's server
  //   none      no Outreach key anywhere: not connected
  //   off       the user disconnected
  //   attention a key is there but Outreach did not accept it, or could not be reached
  //   ok        connected
  function outreachState(m) {
    if (!m || m.status === "idle" || (m.status === "loading" && !m.info)) return "loading";
    if (m.status === "error" && !m.info) return "error";
    const info = m.info || {};
    if (info.ready) return "ok";
    if (info.source === "disconnected") return "off";
    return info.configured || info.source === "own" ? "attention" : "none";
  }

  const PILL = { ok: ["Connected", "good"], none: ["Not connected", "muted"], off: ["Disconnected", "muted"], attention: ["Needs attention", "warn"], loading: ["Checking…", "muted"], error: ["Could not check", "warn"] };

  function via(info) {
    if (info.source === "own" && info.via === "partner") return `Connected to your Outreach account${info.key_hint ? ` (<code>${esc(info.key_hint)}</code>)` : ""}.`;
    if (info.source === "own") return `Connected with your own Outreach key${info.key_hint ? ` (<code>${esc(info.key_hint)}</code>)` : ""}.`;
    if (info.source === "shared") return "Connected through your organisation's Outreach key.";
    return "";
  }

  function detailsHtml(info) {
    const w = info.wallet || {};
    const boxes = (Array.isArray(w.mailboxes) ? w.mailboxes : []).filter((b) => b && b.email);
    const social = (Array.isArray(w.social_channels) ? w.social_channels : []).filter(Boolean);
    const phones = Number.isFinite(w.phone_numbers) ? w.phone_numbers : null;
    const mode = info.mode === "live"
      ? `<span class="cx-pill cx-pill--warn">Live key</span> <span class="cx-sub">Approving a post or sending an email is real.</span>`
      : `<span class="cx-pill cx-pill--good">Test key</span> <span class="cx-sub">Outreach pretends everything: nothing is sent, posted or charged.</span>`;
    const rows = boxes.map((b) => `<li><span class="cx-mail">${esc(b.email)}</span> ` +
      (b.can_send === false ? `<span class="cx-pill cx-pill--warn">Cannot send yet</span>` : `<span class="cx-pill cx-pill--good">Can send</span>`) +
      (b.status && !/^(ok|active|connected)$/i.test(String(b.status)) ? ` <span class="cx-sub">(${esc(String(b.status).replace(/_/g, " "))})</span>` : "") + `</li>`).join("");
    return (
      `<p class="cx-line">${via(info)}</p>` +
      `<p class="cx-line">${mode}</p>` +
      (w.credits != null ? `<p class="cx-line"><strong>${esc(w.credits)}</strong> <span class="cx-sub">credits left in Outreach</span></p>` : "") +
      `<div class="cx-label">Email</div>` +
      (boxes.length
        ? `<ul class="cx-list">${rows}</ul>`
        : `<p class="cx-warn">No email account is connected in your Outreach account yet, so nothing can be sent.</p>`) +
      `<div class="cx-actions cx-actions--flush"><button type="button" class="cx-btn cx-btn--sm" data-cx="open-outreach" data-tip="Opens Outreach in a small window so you can add a mailbox. Come back here when you are done.">＋ Add email in Outreach</button>` +
      `<button type="button" class="cx-btn cx-btn--sm" data-cx="goto" data-view="email" data-tip="Send from your own address instead, without an Outreach mailbox.">Use my own email account</button></div>` +
      `<div class="cx-label">Social posts</div>` +
      `<p class="cx-line">${social.length ? esc(social.map(channelName).join(", ")) : `<span class="cx-sub">No social channel connected.</span>`}</p>` +
      `<div class="cx-actions cx-actions--flush"><button type="button" class="cx-btn cx-btn--sm" data-cx="goto" data-view="social">＋ Add social account</button></div>` +
      `<div class="cx-label">Calls</div>` +
      `<p class="cx-line">${phones == null ? `<span class="cx-sub">Outreach did not say.</span>` : phones > 0 ? esc(plural(phones, "phone number")) : `<span class="cx-sub">No phone number connected.</span>`}</p>` +
      `<div class="cx-actions cx-actions--flush"><button type="button" class="cx-btn cx-btn--sm" data-cx="open-outreach" data-tip="Opens Outreach in a small window so you can add a phone number. Come back here when you are done.">＋ Add phone number in Outreach</button></div>`
    );
  }

  // The forms that connect Outreach: sign in to the user's own account, create one, paste a key, or use the organisation's
  // connection. Which of them are offered depends on what the server is set up for.
  const MODE_LABEL = { signin: "Sign in", signup: "Create account", key: "Use a key" };
  const MODE_TITLE = { signin: "Sign in with your Outreach account", signup: "Create an Outreach account", key: "Connect with an API key" };
  const MODE_HELP = { signin: "Already use Outreach? Enter the email and password you use there.", signup: "New to Outreach? We create the account for your company.", key: "For advanced use: paste a key from your Outreach account." };

  function modesFor(info) {
    const out = [];
    if (info.partner_enabled) out.push("signin");
    if (info.partner_signup) out.push("signup");
    if (info.can_store_key !== false) out.push("key");
    return out;
  }

  const field = (id, label, input) => `<label class="cx-label cx-label--field" for="${id}">${esc(label)}</label>${input}`;

  function connectForm(info, form) {
    const f = form || {};
    const modes = modesFor(info);
    const mode = modes.includes(f.mode) ? f.mode : modes[0] || "key";
    const busy = !!f.busy;
    const err = f.error ? `<p class="cx-warn" role="alert">${esc(f.error)}</p>` : "";
    const note = f.notice ? `<p class="cx-note" role="status">${esc(f.notice)}</p>` : "";
    const shared = info.shared_available
      ? `<button type="button" class="cx-btn" data-cx="use-shared"${busy ? " disabled" : ""} data-tip="Use the Outreach connection your organisation set up, instead of an account or key of your own.">Use my organisation's connection</button>`
      : "";
    if (!modes.length) {
      return `<form class="cx-form" data-cx="connect-form" autocomplete="off"><p class="cx-warn">Connecting Outreach isn't available on your plan yet. You can still build campaigns and export the list to send from your own email${info.shared_available ? ", or use your organisation's connection" : ""}.</p>` +
        (shared ? `<div class="cx-actions cx-actions--flush">${shared}</div>` : "") + `</form>`;
    }
    const tabs = modes.length > 1
      ? `<div class="cx-modes" role="tablist" aria-label="How to connect">` + modes.map((k) =>
        `<button type="button" role="tab" class="cx-mode${k === mode ? " is-active" : ""}" data-cx="mode" data-mode="${k}" aria-selected="${k === mode}">${MODE_LABEL[k]}</button>`).join("") + `</div>`
      : "";
    const emailInput = (cur) => `<input id="cxEmail" class="cx-input" type="email" name="outreach_email" data-cx="email" autocomplete="${cur ? "username" : "email"}" maxlength="254" value="${esc(f.email || "")}">`;
    const passInput = (cur) => `<input id="cxPass" class="cx-input" type="password" name="outreach_password" data-cx="password" autocomplete="${cur ? "current-password" : "new-password"}" maxlength="200" value="">`;
    const needVersion = info.partner_consent_version === false && (mode === "signin" || mode === "signup");
    let fields;
    let submit;
    if (mode === "signin") {
      fields = field("cxEmail", "Outreach email", emailInput(true)) + field("cxPass", "Password", passInput(true)) +
        `<p class="cx-sub">Your password goes straight to Outreach over a secure connection. TenderFlow does not keep it.</p>` +
        `<label class="cx-check"><input type="checkbox" data-cx="consent"${f.consent ? " checked" : ""}> I agree to connect my Outreach account to TenderFlow, so it can send email, post and make calls for me. I can disconnect at any time.</label>` +
        `<p class="cx-sub cx-google">Signed up to Outreach with Google? Signing in from another app asks for an email and password, not a Google button. Choose "Forgot your password?" once to set a password for the same email, then sign in.</p>` +
        `<p class="cx-sub"><button type="button" class="cx-link" data-cx="forgot"${busy ? " disabled" : ""}>Forgot your password?</button> · ` +
        `<button type="button" class="cx-link" data-cx="resend"${busy ? " disabled" : ""}>Send the confirmation email again</button></p>`;
      submit = busy ? "Signing in…" : "Sign in";
    } else if (mode === "signup") {
      fields = field("cxCompany", "Company", `<input id="cxCompany" class="cx-input" data-cx="company" autocomplete="organization" maxlength="120" value="${esc(f.company || "")}">`) +
        field("cxName", "Your name", `<input id="cxName" class="cx-input" data-cx="name" autocomplete="name" maxlength="120" value="${esc(f.name || "")}">`) +
        field("cxEmail", "Email", emailInput(false)) + field("cxPass", "Choose a password (8 or more characters)", passInput(false)) +
        `<label class="cx-check"><input type="checkbox" data-cx="consent"${f.consent ? " checked" : ""}> I agree that an Outreach account is created for my company, and that Outreach's terms and privacy notice apply to it.</label>` +
        `<p class="cx-sub">Your password goes straight to Outreach over a secure connection. TenderFlow does not keep it. Outreach may email you to confirm your address.</p>`;
      submit = busy ? "Creating…" : "Create account";
    } else {
      fields = `<label class="cx-label" for="cxKey">Your Outreach API key</label>` +
        `<input id="cxKey" class="cx-input" type="password" name="outreach_key" data-cx="key" autocomplete="off" spellcheck="false" maxlength="200" placeholder="sk_test_…" value="${esc(f.key || "")}">` +
        `<p class="cx-sub">Copy it from the Keys page of your Outreach account. TenderFlow checks it with Outreach, keeps it encrypted and never shows it again.</p>`;
      submit = busy ? "Connecting…" : "Connect";
    }
    return (
      `<form class="cx-form" data-cx="connect-form" data-mode="${mode}" autocomplete="off">` + tabs +
      `<div class="cx-tabpanel cx-formbox" role="tabpanel" data-mode-pane="${mode}"><div class="cx-chname">${MODE_TITLE[mode]}</div><p class="cx-sub">${MODE_HELP[mode]}</p>` + note + fields + err +
      (needVersion ? `<p class="cx-warn" role="alert">Signing in to Outreach isn't available just now. Try pasting an API key instead, or export your campaign list and send it yourself.</p>` : "") +
      `<div class="cx-actions cx-actions--flush"><button type="submit" class="cx-btn cx-btn--primary" data-cx="connect"${busy || needVersion ? " disabled" : ""}>${submit}</button>${shared}</div></div></form>`
    );
  }

  function outreachParts(m, hideForm) {
    const formFor = (info, f) => (hideForm ? "" : connectForm(info, f));
    const state = outreachState(m);
    const info = (m && m.info) || {};
    const [pillText, tone] = PILL[state];
    const busy = !!(m && m.busy);
    let body;
    let actions = "";
    const disconnect = `<button type="button" class="cx-btn cx-btn--danger" data-cx="disconnect"${busy ? " disabled" : ""} data-tip="Forget your Outreach connection in TenderFlow. Nothing already sent or posted is affected, and you can connect again any time.">${busy ? "Disconnecting…" : "Disconnect"}</button>`;
    if (state === "loading") body = `<p class="cx-sub" role="status">Asking Outreach…</p>`;
    else if (state === "error") {
      body = `<p class="cx-warn" role="alert">${esc((m && m.error) || "Could not reach the server.")}</p>`;
      actions = `<button type="button" class="cx-btn" data-action="outreach-check">Try again</button>`;
    } else if (state === "none") {
      body = `<p class="cx-sub">Connect Outreach to send email, post to social media and make calls from Growth Studio.</p>` + formFor(info, m && m.form);
    } else if (state === "off") {
      body = `<p class="cx-sub">You disconnected Outreach. Growth Studio will not send, post or call until you connect it again.</p>` + formFor(info, m && m.form);
    } else if (state === "attention") {
      body = `<p class="cx-warn" role="alert">${esc(info.problem || "Outreach did not accept the connection.")}</p>` + formFor(info, m && m.form);
      actions = `<button type="button" class="cx-btn" data-action="outreach-check">Check again</button>` + disconnect;
    } else {
      body = (m && m.error ? `<p class="cx-warn" role="alert">${esc(m.error)}</p>` : "") + detailsHtml(info) + `<p class="cx-sub cx-hint">Mailboxes, social channels and phone numbers in your Outreach account show here. To send email without an Outreach mailbox, set up your own email account below.</p>`;
      actions = `<button type="button" class="cx-btn" data-action="outreach-check"${m.status === "loading" ? " disabled" : ""}>${m.status === "loading" ? "Checking…" : "Refresh"}</button>` + disconnect;
    }
    return { state, tone, pillText, body, actions };
  }

  function outreachCard(m, hideForm) {
    const { state, tone, pillText, body, actions } = outreachParts(m, hideForm);
    return (
      `<section class="cx-card cx-card--${state}" aria-label="Outreach">` +
      `<div class="cx-card__head"><span class="cx-ico" aria-hidden="true"><i class="ti ti-send"></i></span>` +
      `<div class="cx-card__title"><span class="cx-name">Outreach</span> <span class="cx-pill cx-pill--${tone}">${esc(pillText)}</span>` +
      `<div class="cx-sub">Send email, post to social media and make calls from Growth Studio.</div></div></div>` +
      `<div class="cx-card__body">${body}</div>` +
      (actions ? `<div class="cx-actions">${actions}</div>` : "") +
      `</section>`
    );
  }

  // ── the user's own email account (Gmail, Outlook, Zoho or any SMTP login) ─────────────────────────────────────────────────
  // Outreach cannot add a mailbox from outside its own account, so an email account is set up here and Growth Studio sends from it.
  // The password goes to TenderFlow's server, is checked against the mail server, is kept encrypted and is never shown again.
  // A user can connect several (up to info.max_accounts); the first is the default. `account` alone (the default one) is what an
  // older server answers with.
  //   loading / error / unavailable (the server cannot keep one) / none / ok
  const accountsOf = (info) => (info && Array.isArray(info.accounts) ? info.accounts : info && info.account ? [info.account] : []);
  const MAX_MAIL_ACCOUNTS = 5;

  function mailState(m) {
    if (!m || m.status === "idle" || (m.status === "loading" && !m.info)) return "loading";
    if (m.status === "error" && !m.info) return "error";
    const info = m.info || {};
    if (accountsOf(info).length) return "ok";
    return info.available === false ? "unavailable" : "none";
  }

  const MAIL_PILL = { loading: ["Checking…", "muted"], error: ["Could not check", "warn"], unavailable: ["Not available", "muted"], none: ["Not set up", "muted"], ok: ["Connected", "good"] };

  const ports = [465, 587, 25, 2525];

  function mailForm(info, f, editing) {
    const presets = info.presets || [];
    const preset = presets.find((p) => p.id === f.provider) || {};
    const busy = !!f.busy;
    const input = (id, key, label, attrs) => field(id, label, `<input id="${id}" class="cx-input" data-cxm="${key}" ${attrs || ""} value="${esc(f[key] == null ? "" : f[key])}">`);
    const cell = (html) => `<div class="cx-cell">${html}</div>`; // a label and its box stay together in the grid
    const select = (id, key, label, options) => field(id, label,
      `<select id="${id}" class="cx-input" data-cxm="${key}">` + options.map(([v, t]) => `<option value="${esc(v)}"${String(f[key]) === String(v) ? " selected" : ""}>${esc(t)}</option>`).join("") + `</select>`);
    const others = accountsOf(info).length;
    return (
      `<form class="cx-form" data-cx="mail-form" autocomplete="off">` +
      (f.notice ? `<p class="cx-note" role="status">${esc(f.notice)}</p>` : "") +
      (editing && f.from_email ? `<p class="cx-line">Changing <span class="cx-mail">${esc(f.from_email)}</span></p>` : "") +
      (!editing && others ? `<p class="cx-line">Add another address to send from. It has its own daily limit.</p>` : "") +
      select("cxmProvider", "provider", "Email provider", presets.map((p) => [p.id, p.label])) +
      (preset.help ? `<p class="cx-sub">${esc(preset.help)}</p>` : "") +
      input("cxmName", "from_name", "Your name (shown to the buyer)", 'autocomplete="name" maxlength="120"') +
      input("cxmFrom", "from_email", "Send from this email address", 'type="email" autocomplete="email" maxlength="254"') +
      input("cxmUser", "username", "Login (usually the same address)", 'name="mail_login" autocomplete="section-smtp username" autocapitalize="off" spellcheck="false" maxlength="254"') +
      field("cxmPass", editing ? "Password (leave blank to keep the saved one)" : "Password or app password",
        `<input id="cxmPass" class="cx-input" type="password" name="mail_password" data-cxm="password" autocomplete="new-password" maxlength="200" value="">`) +
      input("cxmHost", "host", "Mail server", 'autocomplete="off" maxlength="253" placeholder="smtp.yourcompany.com"') +
      `<div class="cx-row">` +
      cell(select("cxmPort", "port", "Port", ports.map((p) => [p, String(p)]))) +
      cell(select("cxmSecurity", "security", "Security", [["ssl", "SSL/TLS"], ["starttls", "STARTTLS"]])) +
      cell(input("cxmCap", "daily_cap", "Most emails a day", 'type="number" min="1" max="500" inputmode="numeric"')) +
      `</div>` +
      input("cxmReply", "reply_to", "Replies go to (optional, if different)", 'type="email" autocomplete="off" maxlength="254"') +
      `<p class="cx-sub">TenderFlow checks the login with your mail server before it keeps anything.</p>` +
      (f.error ? `<p class="cx-warn" role="alert">${esc(f.error)}</p>` : "") +
      `<div class="cx-actions cx-actions--flush"><button type="submit" class="cx-btn cx-btn--primary" data-cx="mail-save"${busy ? " disabled" : ""}>${busy ? "Checking the login…" : editing ? "Save changes" : "Connect email account"}</button>` +
      (accountsOf(info).length ? `<button type="button" class="cx-btn" data-cx="mail-cancel"${busy ? " disabled" : ""}>Cancel</button>` : "") + `</div></form>`
    );
  }

  // One connected account: who it sends as, what it has sent today, and what can be done with it. `busy` is the action under way
  // for it ("test" or "delete"); every button is off while any account is busy, but only the busy account's label changes.
  function accountBlock(a, many, mm) {
    const busyNow = mm.busy || null;
    const mine = busyNow && (mm.busyId == null || mm.busyId === a.id) ? busyNow : null;
    const id = a.id != null ? ` data-id="${esc(a.id)}"` : "";
    const off = busyNow ? " disabled" : "";
    const who = a.from_name ? `${a.from_name} <${a.from_email}>` : a.from_email;
    return (
      `<div class="cx-acct">` +
      `<p class="cx-line"><span class="cx-mail">${esc(who)}</span> ${a.verified ? `<span class="cx-pill cx-pill--good">Login checked</span>` : `<span class="cx-pill cx-pill--warn">Not checked</span>`}` +
      `${many && a.is_default ? ` <span class="cx-pill cx-pill--muted">Default</span>` : ""}</p>` +
      `<p class="cx-line cx-sub">Sends through ${esc(a.host)} (port ${esc(a.port)}) · ${esc(a.sent_today || 0)} of ${esc(a.daily_cap)} sent in the last 24 hours.</p>` +
      (a.last_error ? `<p class="cx-warn" role="alert">The last send failed: ${esc(a.last_error)}</p>` : "") +
      `<div class="cx-actions cx-actions--flush">` +
      `<button type="button" class="cx-btn" data-cx="mail-test"${off}${id} data-tip="Send a short test email from this account to itself, to check that it lands.">${mine === "test" ? "Sending…" : "Send test email"}</button>` +
      `<button type="button" class="cx-btn" data-cx="mail-edit"${off}${id} data-tip="Change the server, the address, the daily limit or the password.">Edit</button>` +
      (many && !a.is_default ? `<button type="button" class="cx-btn" data-cx="mail-default"${off}${id} data-tip="Use this address when you do not choose one under Send from.">${mine === "default" ? "Saving…" : "Make default"}</button>` : "") +
      `<button type="button" class="cx-btn cx-btn--danger" data-cx="mail-delete"${off}${id} data-tip="Forget this email account in TenderFlow. Emails already sent are not affected.">${mine === "delete" ? "Disconnecting…" : "Disconnect"}</button>` +
      `</div></div>`
    );
  }

  function mailParts(m) {
    const mm = m || {};
    const state = mailState(m);
    const info = mm.info || {};
    const list = accountsOf(info);
    const many = list.length > 1;
    const failing = list.some((x) => x.last_error);
    const [pillText, tone0] = MAIL_PILL[state];
    const tone = state === "ok" && failing ? "warn" : tone0;
    const text = state === "ok" && failing ? "Needs attention" : pillText;
    let body;
    let actions = "";
    if (state === "loading") body = `<p class="cx-sub" role="status">Checking…</p>`;
    else if (state === "error") {
      body = `<p class="cx-warn" role="alert">${esc(mm.error || "Could not reach the server.")}</p>`;
      actions = `<button type="button" class="cx-btn" data-cx="mail-reload">Try again</button>`;
    } else if (state === "unavailable") {
      body = `<p class="cx-sub">Connecting an email account isn't available yet. Export your campaign list and send it from your own mailbox in the meantime.</p>`;
    } else if (state === "none") {
      body = `<p class="cx-sub">Send Growth Studio email from your own address. Outreach does not need to have a mailbox for this.</p>` + mailForm(info, mm.form || {}, false);
    } else if (mm.form) {
      body = mailForm(info, mm.form, mm.form.editId != null);
    } else {
      const max = info.max_accounts || MAX_MAIL_ACCOUNTS;
      const a = list[0];
      body =
        (mm.notice ? `<p class="cx-note" role="status">${esc(mm.notice)}</p>` : "") +
        (mm.error ? `<p class="cx-warn" role="alert">${esc(mm.error)}</p>` : "") +
        list.map((x) => accountBlock(x, many, mm)).join("") +
        (list.length < max
          ? `<div class="cx-actions cx-actions--flush"><button type="button" class="cx-btn" data-cx="mail-add"${mm.busy ? " disabled" : ""} data-tip="Connect another address to send from. Each has its own daily limit.">＋ Add another email account</button></div>`
          : `<p class="cx-sub">That is the most email accounts you can connect (${esc(max)}). Disconnect one to add another.</p>`) +
        (many
          ? `<p class="cx-sub cx-hint">Choose the address under Send from when you send mail in Growth Studio. When you do not choose, the default one is used. Replies arrive in the mailbox an email was sent from.</p>`
          : `<p class="cx-sub cx-hint">Choose this address under Send from when you send mail in Growth Studio. Replies arrive in this mailbox${a.reply_to ? ` (replies are asked to go to ${esc(a.reply_to)})` : ""}.</p>`);
    }
    return { state, tone, text, body, actions, failing, many };
  }

  function mailCard(m) {
    const { state, tone, text, body, actions, failing, many } = mailParts(m);
    return (
      `<section class="cx-card cx-card--${state === "ok" && !failing ? "ok" : state === "ok" ? "attention" : state}" data-card="mail" aria-label="${many ? "Email accounts" : "Email account"}">` +
      `<div class="cx-card__head"><span class="cx-ico" aria-hidden="true"><i class="ti ti-mail"></i></span>` +
      `<div class="cx-card__title"><span class="cx-name">${many ? "Email accounts" : "Email account"}</span> <span class="cx-pill cx-pill--${tone}">${esc(text)}</span>` +
      `<div class="cx-sub">Send Growth Studio email from your own address${many ? "es" : ""} (Gmail, Outlook or any provider).</div></div></div>` +
      `<div class="cx-card__body">${body}</div>` +
      (actions ? `<div class="cx-actions">${actions}</div>` : "") +
      `</section>`
    );
  }

  // ── the page: what is set up on the left, Outreach with its three steps on the right ───────────────────────────────────────
  // Outreach is one sign-in for email, calls and social posts, so it is one panel: step 1 signs in, step 2 sets up each channel
  // (Email, Calls, Social as tabs), step 3 points at Growth Studio. Email also works without Outreach (the user's own account).
  const CHANNELS = [
    { id: "email", name: "Email", needed: true, unlock: "Send campaign emails and read replies." },
    { id: "calls", name: "Calls", unlock: "Run call steps in campaigns and log the outcome." },
    { id: "social", name: "Social", unlock: "Schedule and publish company posts after you approve them." },
  ];
  // Apps TenderFlow cannot connect yet are listed so nobody hunts for a button that does nothing.
  const OTHER_APPS = [["Calendar", "Add a booking link to messages and spot booked meetings."], ["CRM", "Copy contacts, activity and deals to your CRM."]];

  // What each channel is connected through right now, from the answers the status calls gave.
  function channelStatus(c, m) {
    const info = (m && m.info) || {};
    const w = info.wallet || {};
    const out = outreachState(m) === "ok";
    if (c.id === "email") {
      const mm = (m && m.mail) || {};
      const mine = mailState(mm) === "ok" ? accountsOf(mm.info) : [];
      if (mine.length) return { on: true, text: mine.length > 1 ? `${mine[0].from_email} +${mine.length - 1} more` : mine[0].from_email, attention: mine.some((x) => x.last_error) };
      const boxes = (Array.isArray(w.mailboxes) ? w.mailboxes : []).filter((x) => x && x.email);
      if (out && boxes.length) return { on: true, text: "Outreach mailbox" };
    } else if (c.id === "calls") {
      const ready = w.phone_status && Number.isFinite(w.phone_status.active) ? w.phone_status.active : w.phone_numbers;
      if (out && Number.isFinite(ready) && ready > 0) return { on: true, text: `Outreach · ${plural(ready, "number")}` };
      if (out && w.phone_status && (w.phone_status.pending > 0 || w.phone_status.verification_pending > 0)) return { on: false, text: "Number being checked" };
    } else if (c.id === "social") {
      const sc = m && m.social;
      if (out && sc && sc.supported) {
        const all = sc.channels || [];
        const sick = all.some((x) => x.status !== "connected");
        if (all.length) return { on: true, text: `Outreach · ${Array.from(new Set(all.map((x) => x.label))).join(", ")}`, attention: sick };
      }
      const social = (Array.isArray(w.social_channels) ? w.social_channels : []).filter(Boolean);
      if (out && social.length) return { on: true, text: `Outreach · ${social.map(channelName).join(", ")}` };
    }
    return { on: false, text: "Not connected" };
  }

  const chanPill = (st) => st.on
    ? `<span class="cx-pill cx-pill--${st.attention ? "warn" : "good"}">${st.attention ? "Needs attention" : "Connected"}</span>`
    : `<span class="cx-pill cx-pill--muted">Not connected</span>`;

  const OUTREACH_WEB = "https://outreach.aivhub.com";
  const NETWORKS = ["LinkedIn", "Facebook", "Instagram", "X"];

  function step(n, done, title, inner) {
    return `<li class="cx-step${done ? " is-done" : ""}"><span class="cx-step__n" aria-hidden="true">${done ? `<i class="ti ti-check"></i>` : n}</span>` +
      `<div class="cx-step__body"><div class="cx-step__title">${esc(title)}${done ? ` <span class="cx-pill cx-pill--good">Done</span>` : ""}</div>${inner}</div></li>`;
  }

  const CH_PILL = { connected: ["Connected", "good"], needs_reauth: ["Needs reconnecting", "warn"], error: ["Problem", "warn"] };

  // The networks Outreach can connect from here, one row per network (a row per account once connected). Each Connect opens the
  // network's own sign-in in a small window; nothing is typed here, so no password or token passes through TenderFlow.
  function socialList(sc) {
    const connectBtn = (p, label) => `<button type="button" class="cx-btn cx-btn--sm" data-cx="social-connect" data-provider="${esc(p.provider)}"${sc.busy ? " disabled" : ""} ` +
      `data-tip="Opens ${esc(p.label)} in a small window so you can approve the connection. You never type a password here.">${sc.busy === p.provider ? "Opening…" : esc(label)}</button>`;
    const rows = (sc.providers || []).map((p) => {
      const mine = (sc.channels || []).filter((c) => c.provider === p.provider);
      if (!mine.length) {
        return `<li><span class="cx-mail">${esc(p.label)}</span> ` + (p.available
          ? `<span class="cx-pill cx-pill--muted">Not connected</span> ${connectBtn(p, "Connect " + p.label)}`
          : `<span class="cx-pill cx-pill--muted">Not available yet</span> <span class="cx-sub">Outreach has not switched ${esc(p.label)} on.</span>`) + `</li>`;
      }
      return mine.map((c) => {
        const [text, tone] = CH_PILL[c.status] || CH_PILL.error;
        return `<li><span class="cx-mail">${esc(p.label)}${c.name ? ` · ${esc(c.name)}` : ""}</span> <span class="cx-pill cx-pill--${tone}">${esc(text)}</span> ` +
          (c.status !== "connected" && p.available ? connectBtn(p, "Reconnect") + " " : "") +
          `<button type="button" class="cx-btn cx-btn--sm" data-cx="social-disconnect" data-id="${esc(c.id)}"${sc.busy ? " disabled" : ""} data-tip="Stop TenderFlow posting to this account. You can connect it again later.">Disconnect</button></li>`;
      }).join("");
    }).join("");
    return `<ul class="cx-list">${rows}</ul>` +
      (sc.notice ? `<p class="cx-line" role="status">${esc(sc.notice)}</p>` : "") +
      (sc.error ? `<p class="cx-line cx-line--bad" role="alert">${esc(sc.error)}</p>` : "") +
      `<div class="cx-actions cx-actions--flush"><button type="button" class="cx-btn" data-cx="social-reload"${sc.status === "loading" ? " disabled" : ""}>${sc.status === "loading" ? "Checking…" : "Check again"}</button></div>` +
      `<p class="cx-sub">You sign in on the network's own page, so TenderFlow never sees your password. Posts still wait for your approval before they go live.</p>`;
  }

  const NUM_PILL = { active: ["Ready to call", "good"], pending: ["Being checked", "warn"], rejected: ["Not approved", "warn"] };

  // The user's phone numbers. Getting one happens in Outreach's own small window (search, buy, upload the business documents),
  // so no document or card detail passes through TenderFlow. A number is only "Ready to call" once Outreach has approved it.
  function numbersList(nm) {
    const busy = !!nm.busy;
    const rows = (nm.numbers || []).map((n) => {
      const [text, tone] = NUM_PILL[n.status] || NUM_PILL.pending;
      return `<li><span class="cx-mail">${esc(n.number)}</span> <span class="cx-pill cx-pill--${tone}">${esc(text)}</span>` +
        (n.status === "rejected" && n.reason ? ` <span class="cx-sub">${esc(n.reason)}</span>` : "") + ` ` +
        `<button type="button" class="cx-btn cx-btn--sm" data-cx="number-release" data-id="${esc(n.id)}"${busy ? " disabled" : ""} data-tip="Give this number back to Outreach. Calls can no longer go out from it.">Release</button></li>`;
    }).join("");
    const has = (nm.numbers || []).length > 0;
    return (has ? `<ul class="cx-list">${rows}</ul>` : `<p class="cx-line">No phone number in your Outreach account yet.</p>`) +
      (nm.notice ? `<p class="cx-line" role="status">${esc(nm.notice)}</p>` : "") +
      (nm.error ? `<p class="cx-line cx-line--bad" role="alert">${esc(nm.error)}</p>` : "") +
      `<div class="cx-actions cx-actions--flush"><button type="button" class="cx-btn${has ? "" : " cx-btn--primary"}" data-cx="number-connect"${busy ? " disabled" : ""} ` +
      `data-tip="Opens Outreach in a small window to choose a UK number and upload your business documents. You never type a card or document detail here.">${nm.busy === "connect" ? "Opening…" : has ? "＋ Add another number" : "＋ Get a phone number"}</button>` +
      `<button type="button" class="cx-btn" data-cx="number-reload"${nm.status === "loading" ? " disabled" : ""}>${nm.status === "loading" ? "Checking…" : "Check again"}</button></div>` +
      `<p class="cx-sub">Outreach's AI makes the calls from that number, to UK landlines only. On Outreach's page choose the type Local and pick a number starting 01, 02 or 03: mobile numbers are not supported here. A number costs 5 Voice credits to set up, then 1 a month. UK rules mean Outreach checks who will use it (company certificate or photo ID, plus a utility bill under 3 months old): this usually takes up to 3 days, and the number shows Ready to call once approved.</p>`;
  }

  // The body of the open channel tab.
  function channelBody(c, m) {
    const out = outreachState(m) === "ok";
    const info = (m && m.info) || {};
    const w = info.wallet || {};
    const checking = m && m.status === "loading";
    const check = `<div class="cx-actions cx-actions--flush"><button type="button" class="cx-btn" data-action="outreach-check"${checking ? " disabled" : ""}>${checking ? "Checking…" : "Check again"}</button></div>`;
    if (c.id === "email") {
      const p = mailParts(m && m.mail);
      const boxes = (Array.isArray(w.mailboxes) ? w.mailboxes : []).filter((x) => x && x.email);
      return p.body + (p.actions ? `<div class="cx-actions">${p.actions}</div>` : "") +
        (out ? `<p class="cx-sub cx-hint">${boxes.length
          ? `Outreach is connected, so Growth Studio can also send through its mailbox${boxes.length > 1 ? "es" : ""}: ${boxes.map((b) => esc(b.email)).join(", ")}.`
          : "Your Outreach account has no mailbox, so email goes out from your own account."}</p>` : "");
    }
    if (!out) return `<p class="cx-sub">Available once you have signed in to Outreach (step 1).</p>`;
    if (c.id === "calls" && m && m.numbers && m.numbers.supported) return numbersList(m.numbers);
    if (c.id === "calls") {
      const phones = Number.isFinite(w.phone_numbers) ? w.phone_numbers : 0;
      const add = `<button type="button" class="cx-btn${phones > 0 ? "" : " cx-btn--primary"}" data-cx="open-outreach" data-path="/voice/numbers" data-tip="Opens Outreach's Phone numbers page in a small window so you can ${phones > 0 ? "add another" : "get a"} phone number. Come back here and press Check again.">\uff0b ${phones > 0 ? "Add another number" : "Add phone number"} in Outreach</button>`;
      return `<p class="cx-line">${phones > 0 ? esc(plural(phones, "phone number")) + " in your Outreach account." : "No phone number in your Outreach account yet."}</p>` +
        `<div class="cx-actions cx-actions--flush">${add}<button type="button" class="cx-btn" data-action="outreach-check"${checking ? " disabled" : ""}>${checking ? "Checking\u2026" : "Check again"}</button></div>` +
        `<p class="cx-sub">Outreach's AI makes the calls from that number, to UK landlines only. To get one: choose a number on Outreach (5 Voice credits to set up, then 1 a month), upload your business documents there (UK rules; they check within about 3 days), then press Check again.</p>`;
    }
    const sc = m && m.social && m.social.supported ? m.social : null;
    if (sc) return socialList(sc);
    const social = (Array.isArray(w.social_channels) ? w.social_channels : []).filter(Boolean);
    const have = (net) => social.some((s) => channelName(s) === net);
    // Outreach has not shipped the connect actions (or has not been asked yet): the older way, on Outreach's own site
    return `<ul class="cx-list">` + NETWORKS.map((n) => `<li><span class="cx-mail">${n}</span> ` +
      (have(n) ? `<span class="cx-pill cx-pill--good">Linked</span>` : `<span class="cx-pill cx-pill--muted">Not linked</span> <button type="button" class="cx-btn cx-btn--sm" data-cx="connect-social" data-network="${esc(n)}" data-tip="Opens Outreach in a small window so you can approve ${esc(n)}. Come back here when you are done.">Connect ${esc(n)}</button>`) + `</li>`).join("") + `</ul>` +
      check + `<p class="cx-sub">Social accounts linked to your Outreach account show here.</p>`;
  }

  function hubHtml(m, view) {
    const tab = CHANNELS.some((c) => c.id === view) ? view : "email";
    const info = (m && m.info) || {};
    const state = outreachState(m);
    const out = state === "ok";
    const [pillText, tone] = PILL[state];
    const statuses = CHANNELS.map((c) => [c, channelStatus(c, m)]);
    const done = statuses.filter(([, st]) => st.on).length;
    const emailOn = statuses[0][1].on;
    const busy = !!(m && m.busy);

    // left: how far along, the Outreach card, the apps that are not here yet
    const progress =
      `<div class="cx-progress"><div class="cx-progress__top"><strong>Outreach: ${done} of ${CHANNELS.length} channels set up</strong>` +
      `<span class="cx-sub">${emailOn ? (done === CHANNELS.length ? "Everything you can connect is connected." : "Email is ready. Calls and social are optional.") : "Set up email first so campaigns can be sent."}</span></div>` +
      `<div class="cx-bar"><i style="width:${Math.round((done / CHANNELS.length) * 100)}%"></i></div></div>`;
    const chips = statuses.map(([c, st]) => `<span class="cx-chip"><span class="cx-dot${st.on ? " is-on" : ""}" aria-hidden="true">●</span> ${esc(c.name)}</span>`).join("");
    const card =
      `<section class="cx-ocard" data-card="outreach" aria-label="Outreach"><div class="cx-ocard__head"><span class="cx-ico" aria-hidden="true"><i class="ti ti-send"></i></span>` +
      `<div class="cx-ocard__txt"><div class="cx-ocard__row"><span class="cx-name">Outreach</span><span class="cx-pill cx-pill--${tone}">${esc(pillText)}</span></div>` +
      `<div class="cx-sub">${out ? "Email, calls and social posts, all in one connection." : "One sign-in covers email, AI calls and social posts."}</div></div></div>` +
      `<div class="cx-chips">${chips}</div></section>`;
    const others =
      `<div class="cx-label">Other apps</div><ul class="cx-other">` +
      OTHER_APPS.map(([name, text]) => `<li><div><div class="cx-mail">${esc(name)}</div><div class="cx-sub">${esc(text)}</div></div><span class="cx-pill cx-pill--muted">Coming soon</span></li>`).join("") + `</ul>`;

    // right: Outreach, with Refresh and Disconnect once it is connected
    const disconnect = `<button type="button" class="cx-btn" data-cx="disconnect"${busy ? " disabled" : ""} data-tip="Forget your Outreach connection in TenderFlow. Nothing already sent or posted is affected, and you can connect again any time.">${busy ? "Disconnecting…" : "Disconnect"}</button>`;
    const refresh = `<button type="button" class="cx-btn" data-action="outreach-check"${m && m.status === "loading" ? " disabled" : ""}>${m && m.status === "loading" ? "Checking…" : "Refresh"}</button>`;
    const head =
      `<div class="cx-mainhead"><div><div class="cx-mainhead__t"><h3 class="cx-pane__title" tabindex="-1">Outreach</h3> <span class="cx-pill cx-pill--${tone}">${esc(pillText)}</span></div>` +
      `<p class="cx-sub">One sign-in powers campaign emails, call steps and social posts.</p></div>` +
      (out ? `<div class="cx-mainhead__act">${refresh}${disconnect}</div>` : "") + `</div>`;

    // step 1
    const testNote = `<p class="cx-line cx-line--note" role="note"><strong>Test mode.</strong> Your Outreach key is a test key, so nothing real is sent, posted or connected. ` +
      `Connect only pretends, to let you try the window. A live key connects and lists your real accounts.</p>`;
    let s1;
    if (out) {
      s1 = step(1, true, "Sign in to Outreach", `<p class="cx-sub">${via(info) || "Connected."}</p>` +
        (m && m.error ? `<p class="cx-warn" role="alert">${esc(m.error)}</p>` : "") + (info.mode === "test" ? testNote : ""));
    } else {
      const p = outreachParts(m);
      s1 = step(1, false, "Sign in to Outreach", p.body + (p.actions ? `<div class="cx-actions cx-actions--flush">${p.actions}</div>` : ""));
    }

    // step 2: Email, Calls and Social as tabs
    const tabs = `<div class="cx-tabs" role="tablist" aria-label="Channels">` + statuses.map(([c, st]) =>
      `<button type="button" role="tab" class="cx-tab${c.id === tab ? " is-on" : ""}" data-cx="row" data-view="${c.id}" aria-selected="${c.id === tab}">${esc(c.name)} ${chanPill(st)}</button>`).join("") + `</div>`;
    const cur = CHANNELS.find((c) => c.id === tab);
    const curSt = statuses.find(([c]) => c.id === tab)[1];
    const panel =
      `<div class="cx-tabpanel" role="tabpanel" data-pane="${tab}"><div class="cx-chhead"><div><div class="cx-chname">${esc(cur.name)} ` +
      `<span class="cx-flag${cur.needed ? " cx-flag--need" : ""}">${cur.needed ? "Needed to send" : "Optional"}</span></div><p class="cx-sub">${esc(cur.unlock)}</p></div>${chanPill(curSt)}</div>` +
      `<div class="cx-chbody">${channelBody(cur, m)}</div></div>`;
    const s2 = step(2, done === CHANNELS.length, "Choose how Outreach reaches people", `<p class="cx-sub">Set up each channel below. Only email is needed to send.</p>${tabs}${panel}`);

    // step 3
    const social = statuses[2][1].on;
    const calls = statuses[1][1].on;
    const go = (done ? `<button type="button" class="cx-btn cx-btn--primary" data-cx="open-growth" data-tab="home">Open Growth Studio</button>` : "") +
      (social ? `<button type="button" class="cx-btn" data-cx="open-growth" data-tab="social">Open Social</button>` : "") +
      (calls ? `<button type="button" class="cx-btn" data-cx="open-growth" data-tab="calls">Open Calls</button>` : "");
    const s3 = step(3, false, "Use Outreach in Growth Studio",
      `<p class="cx-sub">Build a campaign with email, call and social steps, check the preview, then approve it to go out.</p>` + (go ? `<div class="cx-actions cx-actions--flush">${go}</div>` : ""));

    return `<div class="cx-hub"><div class="cx-hub__list">${progress}${card}${others}</div>` +
      `<div class="cx-hub__detail">${head}<ol class="cx-steps">${s1}${s2}${s3}</ol></div></div>`;
  }

  function connectionsHtml(m, view) {
    return (
      `<div class="cx-head"><div><h2 id="connectionsTitle" class="cx-title" tabindex="-1">Connections</h2>` +
      `<p class="cx-sub">Connect or disconnect the apps TenderFlow works with, without leaving this page.</p></div>` +
      `<button type="button" class="cx-x" data-cx="close" aria-label="Close connections">×</button></div>` +
      hubHtml(m, view) +
      `<p class="cx-foot"><i class="ti ti-lock" aria-hidden="true"></i> Your key and your password are kept encrypted and are never shown again, not even to you.</p>`
    );
  }

  const api = { esc, CHANNELS, socialList, numbersList, channelStatus, hubHtml, outreachState, outreachCard, connectionsHtml, detailsHtml, connectForm, modesFor, PILL, mailState, mailCard, mailForm, accountsOf };
  root.TFConnections = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof document === "undefined") return;

  // ── controller (browser only) ─────────────────────────────────────────────────────────────────
  const freshForm = () => ({ mode: null, key: "", email: "", password: "", company: "", name: "", consent: false, busy: false, error: null, notice: null });
  // The email account's form: filled from the saved account being changed (editId), else blank from the Gmail preset to add one.
  const freshMailForm = (info, editId) => {
    const a = editId != null ? accountsOf(info).find((x) => x.id === editId) : null;
    const gmail = ((info && info.presets) || []).find((x) => x.id === "gmail") || {};
    const base = { password: "", busy: false, error: null, notice: null, editId: a ? a.id : null };
    return a
      ? Object.assign(base, { provider: a.provider, from_name: a.from_name || "", from_email: a.from_email, username: a.username, host: a.host, port: a.port,
          security: a.security, reply_to: a.reply_to || "", daily_cap: a.daily_cap, userTouched: true })
      : Object.assign(base, { provider: "gmail", from_name: "", from_email: "", username: "", host: gmail.host || "", port: gmail.port || 465,
          security: gmail.security || "ssl", reply_to: "", daily_cap: 100, userTouched: false });
  };
  // The id on the button that was clicked (an account), or null.
  const idOf = (el) => {
    const v = el && el.getAttribute ? el.getAttribute("data-id") : null;
    return v != null && /^\d+$/.test(v) ? Number(v) : null;
  };
  const model = {
    open: false, view: null, status: "idle", info: null, error: null, busy: false, form: freshForm(), changed: false,
    mail: { status: "idle", info: null, form: null, busy: null, busyId: null, notice: null, error: null },
    // the social networks Outreach can connect from here (supported stays null until asked, false when Outreach has not shipped it)
    social: { status: "idle", supported: null, providers: [], channels: [], busy: null, notice: null, error: null, origin: "" },
    // the user's phone numbers (supported stays null until asked, false when Outreach has not shipped the numbers actions)
    numbers: { status: "idle", supported: null, numbers: [], busy: null, notice: null, error: null },
  };
  let overlay = null;
  let opener = null;

  async function call(method, url, body) {
    const headers = body !== undefined ? { "Content-Type": "application/json" } : {}; // app.js adds the CSRF token to every fetch
    let res;
    try {
      res = await fetch(url, { method, credentials: "same-origin", headers, body: body !== undefined ? JSON.stringify(body) : undefined });
    } catch (e) {
      throw new Error("Could not reach the server. Check your connection and try again.");
    }
    const data = await res.json().catch(() => null);
    if (!res.ok) throw new Error((data && data.error) || (res.status === 429 ? "You are going too fast. Wait a moment and try again." : `Something went wrong (${res.status}).`));
    return data;
  }

  function ensure() {
    if (overlay) return overlay;
    overlay = document.createElement("div");
    overlay.id = "connectionsModal";
    overlay.className = "cx-overlay hidden";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    overlay.setAttribute("aria-labelledby", "connectionsTitle");
    overlay.innerHTML = `<div class="cx-panel" id="connectionsPanel"></div>`;
    document.body.appendChild(overlay);
    document.dispatchEvent(new root.CustomEvent("connections:created"));
    overlay.addEventListener("click", (e) => {
      const t = e.target;
      const hit = (sel) => t.closest && t.closest(sel);
      if (t === overlay || hit('[data-cx="close"]')) close();
      else if (hit('[data-cx="row"]')) setView(hit('[data-cx="row"]').getAttribute("data-view"));
      else if (hit('[data-cx="goto"]')) setView(hit('[data-cx="goto"]').getAttribute("data-view"));
      else if (hit('[data-cx="back"]')) setView(null);
      else if (hit('[data-cx="social-connect"]')) connectChannel(hit('[data-cx="social-connect"]').getAttribute("data-provider"));
      else if (hit('[data-cx="social-disconnect"]')) disconnectChannel(hit('[data-cx="social-disconnect"]').getAttribute("data-id"));
      else if (hit('[data-cx="social-reload"]')) { load(); loadSocial(); }
      else if (hit('[data-cx="number-connect"]')) connectNumber();
      else if (hit('[data-cx="number-release"]')) releaseNumber(hit('[data-cx="number-release"]').getAttribute("data-id"));
      else if (hit('[data-cx="number-reload"]')) { load(); loadNumbers(); }
      else if (hit('[data-cx="open-outreach"]')) openOutreach(hit('[data-cx="open-outreach"]').getAttribute("data-path"));
      else if (hit('[data-cx="connect-social"]')) connectSocial(hit('[data-cx="connect-social"]').getAttribute("data-network"));
      else if (hit('[data-cx="open-growth"]')) openGrowth(hit('[data-cx="open-growth"]').getAttribute("data-tab"));
      else if (hit('[data-action="outreach-check"]')) load();
      else if (hit('[data-cx="disconnect"]')) disconnect();
      else if (hit('[data-cx="use-shared"]')) connect({ use_shared: true });
      else if (hit('[data-cx="mode"]')) { model.form.mode = hit('[data-cx="mode"]').getAttribute("data-mode"); model.form.error = null; model.form.notice = null; render(); }
      else if (hit('[data-cx="forgot"]')) mailAction("forgot");
      else if (hit('[data-cx="resend"]')) mailAction("resend");
      else if (hit('[data-cx="mail-reload"]')) loadMail();
      else if (hit('[data-cx="mail-edit"]')) {
        const first = accountsOf(model.mail.info)[0];
        const eid = idOf(hit('[data-cx="mail-edit"]'));
        model.mail.form = freshMailForm(model.mail.info, eid != null ? eid : first ? first.id : null);
        model.mail.notice = model.mail.error = null;
        render();
      }
      else if (hit('[data-cx="mail-add"]')) { model.mail.form = freshMailForm(model.mail.info, null); model.mail.notice = model.mail.error = null; render(); }
      else if (hit('[data-cx="mail-cancel"]')) { model.mail.form = null; render(); }
      else if (hit('[data-cx="mail-test"]')) testMail(idOf(hit('[data-cx="mail-test"]')));
      else if (hit('[data-cx="mail-default"]')) defaultMail(idOf(hit('[data-cx="mail-default"]')));
      else if (hit('[data-cx="mail-delete"]')) deleteMail(idOf(hit('[data-cx="mail-delete"]')));
    });
    const FIELDS = { key: "key", email: "email", password: "password", company: "company", name: "name" };
    const onMailField = (e, k) => {
      const f = model.mail.form;
      if (!f) return;
      f.error = null;
      if (k === "provider") {
        if (e.type !== "change") return;
        const preset = ((model.mail.info && model.mail.info.presets) || []).find((x) => x.id === e.target.value);
        f.provider = e.target.value;
        f.password = ""; // the box is blank after the re-render, so the held value must be too
        if (preset && preset.id !== "custom") Object.assign(f, { host: preset.host, port: preset.port, security: preset.security });
        render();
        return;
      }
      f[k] = e.target.value;
      if (k === "username") f.userTouched = true;
      if (k === "from_email" && !f.userTouched) { // the login is nearly always the address itself
        f.username = e.target.value;
        const u = overlay.querySelector('[data-cxm="username"]');
        if (u) u.value = e.target.value;
      }
    };
    const onField = (e) => {
      const m = e.target.getAttribute && e.target.getAttribute("data-cxm");
      if (m) return onMailField(e, m);
      const k = e.target.getAttribute && e.target.getAttribute("data-cx");
      if (k === "consent") { model.form.consent = !!e.target.checked; model.form.error = null; }
      else if (k && FIELDS[k]) { model.form[FIELDS[k]] = e.target.value; model.form.error = null; }
    };
    overlay.addEventListener("input", onField);
    overlay.addEventListener("change", onField);
    overlay.addEventListener("submit", (e) => {
      e.preventDefault();
      if (e.target.getAttribute && e.target.getAttribute("data-cx") === "mail-form") submitMail();
      else submitForm();
    });
    return overlay;
  }

  // Open one channel's panel (null goes back to the list). The panel's heading takes focus so a keyboard user lands in it.
  // Each network is approved on Outreach's own page (its public API cannot link one), so open it in a small window;
  // coming back to this tab re-reads the account.
  function outreachBase() {
    const info = model.info || {};
    return /^https:\/\//.test(String(info.web_url || "")) ? info.web_url : OUTREACH_WEB;
  }

  // Outreach's own site, for the things its public API cannot add (a mailbox, a phone number); coming back re-reads the account.
  function openOutreach(path) {
    let url = outreachBase();
    // a page inside Outreach (Phone numbers: choose a number and upload the business documents there); same site only
    if (/^\/[a-z\/-]+$/.test(String(path || ""))) { try { url = new URL(path, url).href; } catch (_) { /* keep the home page */ } }
    const w = root.open(url, "outreachSite", "popup=yes,width=960,height=760");
    if (!w) root.open(url, "_blank", "noopener");
  }

  function connectSocial(network) {
    const base = outreachBase();
    const url = /#/.test(base) ? base : `${base.replace(/\/+$/, "")}/#/social`;
    const w = root.open(url, "outreachSocial", "popup=yes,width=560,height=760");
    if (!w) root.open(url, "_blank", "noopener");
  }

  // keepMessage: read the list again but leave the notice or error that the click just produced on screen
  async function loadSocial(quiet, keepMessage) {
    const sc = model.social;
    if (sc.status === "loading") return;
    sc.status = "loading";
    if (!quiet) render();
    try {
      const r = await call("GET", "/api/growth/social/channels");
      Object.assign(sc, { status: "ok", supported: r.supported !== false, providers: r.providers || [], channels: r.channels || [], origin: r.outreach_origin || "", simulated: !!r.simulated, error: keepMessage ? sc.error : null });
    } catch (e) {
      Object.assign(sc, { status: "error", supported: null, error: null }); // Outreach is not connected or not reachable: the older steps still show
    }
    render();
  }

  // Open the network's sign-in in a small window. The blank window is opened inside the click so a pop-up blocker allows it;
  // the sign-in link is put in it once Outreach has answered. When the window closes, or tells us it is done, the list is read again.
  async function connectChannel(provider) {
    const sc = model.social;
    if (sc.busy) return;
    sc.busy = provider;
    sc.error = sc.notice = null;
    render();
    const w = root.open("about:blank", "outreachSocial" + Date.now(), "popup=yes,width=560,height=760");
    try {
      const r = await call("POST", "/api/growth/social/channels/connect", { provider });
      if (w && !w.closed) {
        w.location.href = r.auth_url;
        const timer = root.setInterval(() => {
          if (!w.closed) return;
          root.clearInterval(timer);
          afterChannelWindow();
        }, 700);
      } else {
        root.open(r.auth_url, "_blank", "noopener");
        sc.notice = "Your browser blocked the small window, so it opened in a new tab. When you have finished there, come back here and choose Check again.";
      }
    } catch (e) {
      if (w && !w.closed) w.close();
      sc.error = e.message;
    }
    sc.busy = null;
    render();
  }

  async function loadNumbers(quiet, keepMessage) {
    const nm = model.numbers;
    if (nm.status === "loading") return;
    nm.status = "loading";
    if (!quiet) render();
    try {
      const r = await call("GET", "/api/growth/numbers");
      Object.assign(nm, { status: "ok", supported: r.supported !== false, numbers: r.numbers || [], error: keepMessage ? nm.error : null });
    } catch (e) {
      Object.assign(nm, { status: "error", supported: null, error: null }); // Outreach is not connected or not reachable: the older steps still show
    }
    render();
  }

  // Open Outreach's number page in a small window (opened inside the click so a pop-up blocker allows it); the link is put in it
  // once Outreach has answered. When the window closes, or tells us it is done, the numbers are read again.
  async function connectNumber() {
    const nm = model.numbers;
    if (nm.busy) return;
    nm.busy = "connect";
    nm.error = nm.notice = null;
    render();
    const w = root.open("about:blank", "outreachNumber" + Date.now(), "popup=yes,width=640,height=820");
    try {
      const r = await call("POST", "/api/growth/numbers/connect", {});
      if (w && !w.closed) {
        w.location.href = r.auth_url;
        const timer = root.setInterval(() => {
          if (!w.closed) return;
          root.clearInterval(timer);
          afterChannelWindow();
        }, 700);
      } else {
        root.open(r.auth_url, "_blank", "noopener");
        nm.notice = "Your browser blocked the small window, so it opened in a new tab. When you have finished there, come back here and choose Check again.";
      }
    } catch (e) {
      if (w && !w.closed) w.close();
      nm.error = e.message;
    }
    nm.busy = null;
    render();
  }

  async function releaseNumber(id) {
    const nm = model.numbers;
    if (nm.busy || !id) return;
    if (root.confirm && !root.confirm("Release this phone number? Calls can no longer go out from it, and Outreach may not give it back.")) return;
    nm.busy = id;
    nm.error = nm.notice = null;
    render();
    try {
      await call("DELETE", `/api/growth/numbers/${encodeURIComponent(id)}`);
      nm.notice = "Released.";
      model.changed = true;
    } catch (e) {
      nm.error = e.message;
    }
    nm.busy = null;
    await loadNumbers(true, true);
    load();
  }

  // Outreach was connected or disconnected: what was read about its social channels and numbers belongs to the old connection
  function resetSocial() {
    Object.assign(model.social, { status: "idle", supported: null, providers: [], channels: [], busy: null, notice: null, error: null });
    Object.assign(model.numbers, { status: "idle", supported: null, numbers: [], busy: null, notice: null, error: null });
  }

  function afterChannelWindow() {
    model.changed = true;
    loadSocial(true);
    loadNumbers(true);
    load();
  }

  async function disconnectChannel(id) {
    const sc = model.social;
    if (sc.busy || !id) return;
    sc.busy = id;
    sc.error = sc.notice = null;
    render();
    try {
      await call("DELETE", `/api/growth/social/channels/${encodeURIComponent(id)}`);
      sc.notice = sc.simulated
        ? "Outreach accepted that, but in test mode its sample accounts always show. Nothing real was disconnected."
        : "Disconnected. You can connect it again whenever you like.";
      model.changed = true;
    } catch (e) {
      sc.error = e.message;
    }
    sc.busy = null;
    await loadSocial(true, true);
    load();
  }

  // The sign-in window tells this page when it is finished: Outreach's own message, or the page TenderFlow shows at the end.
  function channelDone(d) {
    if (d.provider === "number") {
      model.numbers.notice = d.status === "connected" ? "Your phone number request was received. It shows Ready to call once Outreach has checked your business details." : "The phone number could not be set up. Try again.";
      afterChannelWindow();
      return;
    }
    const name = channelName(d.provider);
    model.social.notice = d.status === "connected" ? `${name} is connected.` : `${name} could not be connected. Try again.`;
    afterChannelWindow();
  }
  root.addEventListener("message", (e) => {
    const d = e && e.data;
    if (!d || typeof d !== "object" || typeof d.type !== "string") return;
    const fromOutreach = d.type === "outreach.channel" && (e.origin === model.social.origin || e.origin === OUTREACH_WEB);
    const fromHere = d.type === "tf.outreach.channel" && e.origin === root.location.origin;
    if (fromOutreach || fromHere) channelDone(d);
  });
  // The page the window ends on also broadcasts to every tab of this site (BroadcastChannel never leaves the origin), so the answer
  // arrives even when the browser has cut the window's link back to this page.
  if (root.BroadcastChannel) {
    try {
      new root.BroadcastChannel("tf-outreach-channel").onmessage = (e) => {
        if (e && e.data && e.data.type === "tf.outreach.channel") channelDone(e.data);
      };
    } catch (err) {
      /* an older browser: the window's own message and the closing of the window still refresh the list */
    }
  }

  // leave Connections and land on the Social or Calls tab of Growth Studio
  function openGrowth(tab) {
    close();
    const byId = (id) => document.getElementById(id);
    const btn = byId("btnGrowthStudio");
    if (btn) btn.click();
    const id = tab === "calls" ? "gsTabCalls" : tab === "social" ? "gsTabSocial" : null; // "home" just opens Growth Studio where it was
    if (id) root.setTimeout(() => { const t = byId(id); if (t) t.click(); }, 150);
  }

  function setView(view) {
    model.view = view;
    render();
    if (view === "social" && outreachState(model) === "ok" && model.social.status !== "loading") loadSocial(true);
    if (view === "calls" && outreachState(model) === "ok" && model.numbers.status !== "loading") loadNumbers(true);
    const panel = document.getElementById("connectionsPanel");
    const target = panel && (panel.querySelector(`.cx-tab[data-view="${view}"]`) || panel.querySelector(".cx-pane__title"));
    if (target) target.focus();
  }

  function render() {
    const panel = document.getElementById("connectionsPanel");
    if (!panel) return;
    const active = document.activeElement;
    const keep = panel.contains(active) ? active.getAttribute("data-cx") || active.getAttribute("data-action") : null;
    panel.innerHTML = connectionsHtml(model, model.view);
    // a password typed before this redraw (a status answer arriving) is put back into the box; it is never written into the markup
    const held = [['[data-cxm="password"]', model.mail.form && model.mail.form.password], ['[data-cx="password"]', model.form.password]];
    for (const [sel, value] of held) {
      const box = value && panel.querySelector(sel);
      if (box) box.value = value;
    }
    if (keep) {
      const again = panel.querySelector(`[data-cx="${keep}"], [data-action="${keep}"]`);
      if (again && !again.disabled) again.focus();
    }
  }

  async function load() {
    if (model.status === "loading") return;
    model.status = "loading";
    model.error = null;
    render();
    try {
      model.info = await call("GET", "/api/growth/outreach/status");
      model.status = "ok";
      if (outreachState(model) === "ok" && model.social.status === "idle") loadSocial(true);
      if (outreachState(model) === "ok" && model.numbers.status === "idle") loadNumbers(true);
    } catch (e) {
      model.status = "error";
      model.error = e.message;
      model.info = null;
    }
    render();
  }

  async function loadMail() {
    const mm = model.mail;
    if (mm.status === "loading") return;
    mm.status = "loading";
    mm.error = null;
    render();
    try {
      mm.info = await call("GET", "/api/growth/email-account");
      mm.status = "ok";
      if (!accountsOf(mm.info).length && !mm.form) mm.form = freshMailForm(mm.info, null);
    } catch (e) {
      mm.status = "error";
      mm.error = e.message;
      mm.info = null;
    }
    render();
  }

  const ask = async (text) => (typeof root.showConfirm === "function" ? root.showConfirm(text) : root.confirm(text));

  async function submitMail() {
    const mm = model.mail;
    const f = mm.form;
    if (!f || f.busy) return;
    const editing = f.editId != null;
    if (!String(f.from_email).trim() || !String(f.username).trim()) { f.error = "Enter the address to send from, and the login."; render(); return; }
    if (!String(f.host).trim()) { f.error = "Enter the mail server."; render(); return; }
    if (!editing && !f.password) { f.error = "Enter the password (an app password, where your provider asks for one)."; render(); return; }
    const body = {
      provider: f.provider, from_name: String(f.from_name).trim(), from_email: String(f.from_email).trim(), username: String(f.username).trim(),
      password: f.password, host: String(f.host).trim(), port: Number(f.port), security: f.security, reply_to: String(f.reply_to).trim(), daily_cap: Number(f.daily_cap),
    };
    if (editing) body.id = f.editId;
    f.busy = true;
    f.error = null;
    f.password = ""; // gone from the page the moment it is sent, whatever the answer
    render();
    try {
      mm.info = await call("PUT", "/api/growth/email-account", body);
      mm.status = "ok";
      mm.form = null;
      mm.error = null;
      mm.notice = editing ? "Changes saved." : "Email account connected. Send a test email to check that it lands.";
      model.changed = true;
    } catch (e) {
      f.busy = false;
      f.error = e.message;
    }
    render();
  }

  async function testMail(id) {
    const mm = model.mail;
    if (mm.busy) return;
    mm.busy = "test";
    mm.busyId = id;
    mm.error = mm.notice = null;
    render();
    try {
      const res = await call("POST", "/api/growth/email-account/test", id == null ? {} : { id });
      mm.notice = `Test email sent to ${res.sent_to}. Check that inbox (and your spam folder).`;
    } catch (e) {
      mm.error = e.message;
    }
    try { mm.info = await call("GET", "/api/growth/email-account"); } catch (e) { /* what is shown stays */ }
    mm.busy = mm.busyId = null;
    render();
  }

  async function defaultMail(id) {
    const mm = model.mail;
    if (mm.busy || id == null) return;
    mm.busy = "default";
    mm.busyId = id;
    mm.error = mm.notice = null;
    render();
    try {
      mm.info = await call("POST", "/api/growth/email-account/default", { id });
      const now = accountsOf(mm.info).find((x) => x.id === id);
      mm.notice = now ? `${now.from_email} is now the default. Growth Studio sends from it unless you choose another address.` : null;
      model.changed = true;
    } catch (e) {
      mm.error = e.message;
    }
    mm.busy = mm.busyId = null;
    render();
  }

  async function deleteMail(id) {
    const mm = model.mail;
    if (mm.busy) return;
    const target = accountsOf(mm.info).find((x) => x.id === id) || accountsOf(mm.info)[0];
    const addr = target ? target.from_email : "it";
    if (!(await ask(`Disconnect this email account? Growth Studio will not send from ${addr} until you set it up again. Emails already sent are not affected.`))) return;
    mm.busy = "delete";
    mm.busyId = id;
    mm.error = mm.notice = null;
    render();
    try {
      mm.info = await call("DELETE", "/api/growth/email-account" + (id == null ? "" : `?id=${id}`));
      mm.form = accountsOf(mm.info).length ? null : freshMailForm(mm.info, null);
      model.changed = true;
    } catch (e) {
      mm.error = e.message;
    }
    mm.busy = mm.busyId = null;
    render();
  }

  // What the form sends, by mode. A password is cleared from the page the moment it has been sent, whatever the answer.
  async function submitForm() {
    const f = model.form;
    const mode = (document.querySelector('[data-cx="connect-form"]') || {}).getAttribute ? document.querySelector('[data-cx="connect-form"]').getAttribute("data-mode") : f.mode;
    if (f.busy) return;
    if (mode === "signin") {
      if (!f.email.trim() || !f.password) { f.error = "Enter your Outreach email and password."; render(); return; }
      if (!f.consent) { f.error = "Tick the box to agree to connect your account."; render(); return; }
      await send("/api/growth/outreach/signin", { email: f.email.trim(), password: f.password, consent: true }, "email");
    } else if (mode === "signup") {
      if (!f.company.trim() || !f.name.trim() || !f.email.trim() || !f.password) { f.error = "Fill in every box."; render(); return; }
      if (!f.consent) { f.error = "Tick the box to agree before creating the account."; render(); return; }
      await send("/api/growth/outreach/signup", { company: f.company.trim(), name: f.name.trim(), email: f.email.trim(), password: f.password, consent: true }, "signup");
    } else {
      if (!f.key.trim()) { f.error = "Paste your Outreach API key."; render(); return; }
      await connect({ api_key: f.key.trim() });
    }
  }

  async function send(url, body, kind) {
    const f = model.form;
    f.busy = true;
    f.error = null;
    f.notice = null;
    f.password = "";
    render();
    try {
      const res = await call("POST", url, body);
      if (res.verify_email) {
        f.mode = "signin";
        f.notice = `We sent a confirmation email to ${res.email || body.email}. Confirm it, then come back here and sign in.`;
      } else {
        model.info = res;
        model.status = "ok";
        model.changed = true;
        resetSocial();
        if (outreachState(model) === "ok") { loadSocial(true); loadNumbers(true); }
        f.email = f.company = f.name = "";
        f.consent = false;
      }
    } catch (e) {
      f.error = e.message;
      if (kind === "email") f.mode = "signin";
    }
    f.busy = false;
    render();
  }

  // "Forgot your password?" and "Send the confirmation email again": Outreach emails the user; nothing changes here.
  async function mailAction(which) {
    const f = model.form;
    if (f.busy) return;
    if (!f.email.trim()) { f.error = "Enter your Outreach email first."; render(); return; }
    f.busy = true;
    f.error = null;
    f.notice = null;
    render();
    try {
      await call("POST", which === "forgot" ? "/api/growth/outreach/forgot" : "/api/growth/outreach/resend", { email: f.email.trim() });
      f.notice = which === "forgot"
        ? `If ${f.email.trim()} has an Outreach account, Outreach has emailed it a link to choose a new password. The link opens an Outreach page; when you have set it, come back here and sign in.`
        : `Asked Outreach to send the confirmation email again. It only sends one for an account created through TenderFlow that is not confirmed yet, and at most 5 times an hour, so if nothing arrives check your spam, or sign in if you have already confirmed.`;
    } catch (e) {
      f.error = e.message;
    }
    f.busy = false;
    render();
  }

  // Connect with the user's own key, or go back to the organisation's connection. The key is cleared from the page once used.
  async function connect(body) {
    const f = model.form;
    if (f.busy) return;
    f.busy = true;
    f.error = null;
    f.notice = null;
    render();
    try {
      model.info = await call("POST", "/api/growth/outreach/connect", body);
      model.status = "ok";
      model.changed = true;
      f.key = "";
      resetSocial();
      if (outreachState(model) === "ok") { loadSocial(true); loadNumbers(true); }
    } catch (e) {
      f.error = e.message;
    }
    f.busy = false;
    render();
  }

  async function disconnect() {
    if (model.busy) return;
    if (!(await ask("Disconnect Outreach? Growth Studio will not send, post or call until you connect it again. Anything already sent or posted is not affected."))) return;
    model.busy = true;
    render();
    try {
      model.info = await call("DELETE", "/api/growth/outreach/connect");
      model.status = "ok";
      model.changed = true;
      model.error = null;
      model.form.error = null;
      resetSocial();
    } catch (e) {
      model.error = e.message;
      model.form.error = e.message;
    }
    model.busy = false;
    render();
  }

  // `card` ("outreach" or "mail") scrolls to that card once the window has read its state.
  function open(from, card) {
    ensure();
    opener = from || document.activeElement;
    model.open = true;
    overlay.classList.remove("hidden");
    render();
    const title = document.getElementById("connectionsTitle");
    if (title) title.focus();
    // a wide window starts on a channel (the one asked for, else Email); a phone starts on the list
    const wide = root.matchMedia && root.matchMedia("(min-width: 820px)").matches;
    model.view = card === "mail" ? "email" : card === "outreach" ? "outreach" : wide ? "email" : null;
    render();
    Promise.all([load(), loadMail()]);
  }

  function close() {
    if (!overlay) return;
    model.open = false;
    overlay.classList.add("hidden");
    if (opener && opener.focus) try { opener.focus(); } catch (e) { /* the opener went away */ }
    if (model.changed) {
      // something was connected or disconnected: the panels that show how mail goes out look again
      model.changed = false;
      document.dispatchEvent(new root.CustomEvent("connections:changed"));
    }
  }

  function start() {
    try {
      const url = new URL(root.location.href);
      if (url.searchParams.get("outreach") === "connected") {
        url.searchParams.delete("outreach");
        root.history.replaceState(null, "", url.pathname + (url.search || "") + url.hash);
        model.form.mode = "signin";
        model.form.notice = "Your email is confirmed. Sign in with your Outreach email and password to finish connecting.";
        setTimeout(() => open(null), 300);
      }
    } catch (e) { /* an unusual address: just do not open */ }
    for (const id of ["btnConnections", "btnConnectionsNav"]) {
      const b = document.getElementById(id);
      if (b) b.addEventListener("click", () => open(b));
    }
    // back from Outreach's own site after linking an account or number: look again without a click
    root.addEventListener("focus", () => {
      if (!model.open || !["social", "calls", "email", "outreach"].includes(model.view)) return;
      if (model.status !== "loading") load();
      if (model.view === "social" && model.social.status !== "loading") loadSocial(true);
      if (model.view === "calls" && model.numbers.status !== "loading") loadNumbers(true);
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && model.open) { e.stopPropagation(); close(); }
    }, true);
    // any button marked data-open-connections (Growth Studio panels, the Email settings) opens this window
    document.addEventListener("click", (e) => {
      const b = e.target && e.target.closest ? e.target.closest("[data-open-connections]") : null;
      if (!b || b.disabled) return;
      e.preventDefault();
      open(b, b.getAttribute("data-open-connections"));
    });
  }
  api.open = open;
  api.close = close;
  root.closeConnections = close; // shell.js closes it like the other sections
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})(typeof window !== "undefined" ? window : globalThis);
