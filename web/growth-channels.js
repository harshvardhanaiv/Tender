/* Growth Studio "Social & calls": write a social post with Outreach's AI and approve it, and have Outreach's AI phone
   one buyer. Pure view functions first (no DOM, no fetch; tests/growth_channels_test.js runs them in Node), then a small
   controller that mounts them into #gsPaneChannels. Every value from the server or the user goes through esc().
   Nothing here publishes or dials by itself: a post is only approved, and a call only placed, by the button for it,
   after a confirmation, and the server refuses anything that breaks the rules (see tender_app/outreach_channels.py). */
(function (root) {
  "use strict";

  const V = typeof module !== "undefined" && module.exports ? require("./growth-view.js") : root.GrowthView;
  if (!V) return;
  const { esc, plural } = V;

  // ── what the words mean ───────────────────────────────────────────────────────────────────────
  const POST_STATUS = {
    writing: ["Being written", "info"], needs_approval: ["Ready to approve", "warn"], pending_approval: ["Ready to approve", "warn"],
    awaiting_approval: ["Ready to approve", "warn"], draft: ["Ready to approve", "warn"], ready: ["Ready to approve", "warn"],
    approved: ["Approved", "good"], scheduled: ["Scheduled", "good"], published: ["Published", "good"],
    rejected: ["Rejected", "muted"], cancelled: ["Cancelled", "muted"], canceled: ["Cancelled", "muted"], failed: ["Failed", "bad"], expired: ["Expired", "muted"],
  };
  const FINAL_POST = ["published", "rejected", "cancelled", "canceled", "failed", "expired"];
  const CALL_STATUS = {
    queued: ["Starting", "info"], ringing: ["Ringing", "info"], in_progress: ["In progress", "info"], "in-progress": ["In progress", "info"],
    answered: ["In progress", "info"], completed: ["Completed", "good"], no_answer: ["No answer", "muted"], busy: ["Busy", "muted"],
    voicemail: ["Voicemail", "muted"], failed: ["Failed", "bad"], ended: ["Ended", "muted"], cancelled: ["Cancelled", "muted"],
  };
  const LIVE_CALL = ["queued", "ringing", "in_progress", "in-progress", "answered", "dialing", "connecting"];
  const DEFAULT_CHANNELS = ["linkedin", "facebook", "instagram", "x"];
  const CHANNEL_LABEL = { linkedin: "LinkedIn", facebook: "Facebook", instagram: "Instagram", x: "X", twitter: "X" };

  const STARTERS = [
    ["", "Start from an idea…"],
    ["win", "A contract we won"],
    ["insight", "A public-sector buying insight"],
    ["service", "A new service or capability"],
    ["event", "An event or open day"],
  ];
  const STARTER_TEXT = {
    win: "Announce that we have been awarded a contract by [buyer] to deliver [service]. Keep it factual and modest, and thank the team and the buyer.",
    insight: "Share one practical insight about winning public-sector contracts in [sector] in the UK, in plain language, ending with an invitation to get in touch.",
    service: "Introduce our new [service] for public-sector buyers: who it helps, the problem it solves, and how to find out more.",
    event: "Invite local public-sector buyers and partners to [event] on [date] at [place], and say what they will get from coming.",
  };

  const label = (map, key, fallback) => (map[String(key || "").toLowerCase()] || [fallback || String(key || "Unknown").replace(/_/g, " "), "muted"]);
  const badge = (pair) => V.badge(esc(pair[0]), pair[1]);
  const httpsLink = (url, text) => {
    const u = V.safeUrl(url);
    return u && /^https:/i.test(u) ? `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(text)}</a>` : "";
  };
  const chLabel = (c) => CHANNEL_LABEL[c] || String(c || "").replace(/^./, (m) => m.toUpperCase());

  // ── validity (the same rules the server applies; the server is the one that enforces them) ───
  function postReady(f) {
    return !!f && String(f.prompt || "").trim().length >= 10 && (f.channels || []).length > 0 && !f.busy;
  }
  function callReady(f) {
    return !!f && !!String(f.buyer || "").trim() && !!String(f.number || "").trim() && (!!String(f.goal || "").trim() || !!f.scriptId)
      && f.business === true && f.tps === true && !f.busy;
  }
  function publishIso(local) {
    if (!local) return undefined;
    const t = new Date(local);
    return Number.isNaN(t.getTime()) ? undefined : t.toISOString();
  }
  function postBody(f) {
    const body = { prompt: String(f.prompt || "").trim(), channels: f.channels.slice(), attempt: f.attempt };
    const when = publishIso(f.publishAt);
    if (when) body.publish_at = when;
    body.picture = f.pictureOn ? { mode: "ai", instruction: String(f.pictureNote || "").trim() || undefined } : { mode: "none" };
    return body;
  }
  function callBody(f) {
    return {
      buyer: String(f.buyer || "").trim(), number: String(f.number || "").trim(), goal: String(f.goal || "").trim(),
      script_id: f.scriptId || undefined, instructions: String(f.instructions || "").trim() || undefined,
      questions: String(f.questionsText || "").split("\n").map((q) => q.trim()).filter(Boolean),
      business_line: f.business === true, tps_checked: f.tps === true, confirm: true, attempt: f.attempt,
    };
  }

  // ── social posts ──────────────────────────────────────────────────────────────────────────────
  function postRow(p) {
    const st = label(POST_STATUS, p.status);
    const status = String(p.status || "").toLowerCase();
    const final = FINAL_POST.indexOf(status) >= 0;
    const readyToApprove = !final && status !== "writing" && status !== "approved" && status !== "scheduled";
    const channels = (p.channels || []).map(chLabel).join(", ");
    const links = [httpsLink(p.preview_url, "Open the preview"), httpsLink(p.published_url, "See the live post")].filter(Boolean).join(" · ");
    const when = p.publish_at ? ` · for ${esc(p.publish_at.replace("T", " ").replace(/:00Z$/, "Z"))}` : "";
    return (
      `<li class="gs-ch-item" data-post="${esc(p.id)}">` +
      `<div class="gs-ch-item__head">${badge(st)} <strong>${esc(channels || "No channel")}</strong><span class="gs-hint">${when}${p.mode === "live" ? "" : " · test mode"}</span></div>` +
      (p.caption ? `<p class="gs-ch-caption">${esc(p.caption)}</p>` : `<p class="gs-hint">${status === "writing" ? "The AI is writing it. Press Refresh in a moment." : "No caption yet."}</p>`) +
      `<p class="gs-hint">From your idea: ${esc(String(p.prompt || "").slice(0, 160))}${String(p.prompt || "").length > 160 ? "…" : ""}</p>` +
      (links ? `<p class="gs-hint">${links}</p>` : "") +
      (p.error ? `<p class="gs-hint gs-hint--warn">${esc(p.error)}</p>` : "") +
      (final
        ? ""
        : `<div class="gs-actions">` +
          (readyToApprove ? `<button type="button" class="gs-btn gs-btn--primary gs-btn--sm" data-action="ch-post-decide" data-id="${esc(p.id)}" data-decision="approve">Approve and publish</button>` : "") +
          (readyToApprove ? `<button type="button" class="gs-btn gs-btn--sm" data-action="ch-post-decide" data-id="${esc(p.id)}" data-decision="reject">Reject</button>` : "") +
          `<button type="button" class="gs-btn gs-btn--sm" data-action="ch-post-decide" data-id="${esc(p.id)}" data-decision="cancel">Cancel post</button></div>`) +
      `</li>`
    );
  }

  function socialCard(m) {
    const s = m.social || {};
    const f = s.form || {};
    const reported = ((m.info && m.info.wallet && m.info.wallet.social_channels) || []).filter(Boolean);
    const choices = reported.length ? reported : DEFAULT_CHANNELS;
    const checks = choices.map((c) => `<label class="gs-inline"><input type="checkbox" data-field="ch-channel" value="${esc(c)}"${(f.channels || []).indexOf(c) >= 0 ? " checked" : ""}> ${esc(chLabel(c))}</label>`).join("");
    const posts = s.posts || [];
    return (
      `<div class="gs-card" id="gsChSocial"><div class="gs-card__title">Social post</div>` +
      `<p class="gs-hint gs-hint--block">Describe the post and Outreach's AI writes the caption and the picture. <strong>Nothing is published until you read the preview and approve it here.</strong> Do not include a buyer's or a customer's confidential details.</p>` +
      `<label class="gs-label" for="gsChStarter">Idea</label>` +
      `<select id="gsChStarter" class="gs-select gs-select--sm" data-field="ch-starter">${STARTERS.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join("")}</select>` +
      `<label class="gs-label" for="gsChPrompt">What should the post say?</label>` +
      `<textarea id="gsChPrompt" class="gs-textarea gs-textarea--mid" data-field="ch-prompt" maxlength="1500" rows="4" placeholder="e.g. Announce our new Leeds office opening on 1 November">${esc(f.prompt || "")}</textarea>` +
      `<div class="gs-label">Post to</div><div class="gs-ch-checks">${checks}</div>` +
      (reported.length ? "" : `<p class="gs-hint">Outreach did not list your connected channels, so the usual ones are shown. Outreach refuses one that is not connected.</p>`) +
      `<div class="gs-ch-row"><label class="gs-inline">Publish at <input type="datetime-local" class="gs-input gs-input--sm" data-field="ch-publish" value="${esc(f.publishAt || "")}"></label>` +
      `<label class="gs-inline"><input type="checkbox" data-field="ch-picture"${f.pictureOn ? " checked" : ""}> AI picture</label></div>` +
      (f.pictureOn ? `<label class="gs-label" for="gsChPic">Picture idea (optional)</label><input id="gsChPic" class="gs-input gs-input--sm" data-field="ch-picture-note" maxlength="300" value="${esc(f.pictureNote || "")}" placeholder="e.g. A bright modern office">` : "") +
      `<p class="gs-hint">Leave the time empty to choose it in Outreach. A time needs to be at least 5 minutes ahead so there is time to approve.</p>` +
      (f.error ? `<p class="gs-hint gs-hint--warn" role="alert">${esc(f.error)}</p>` : "") +
      `<div class="gs-actions"><button type="button" class="gs-btn gs-btn--primary gs-btn--sm" data-action="ch-post-create"${postReady(f) ? "" : " disabled"}>${f.busy ? "Writing…" : "Write the post"}</button>` +
      `<button type="button" class="gs-btn gs-btn--sm" data-action="ch-post-refresh"${s.refreshing ? " disabled" : ""}>${s.refreshing ? "Checking…" : "Refresh"}</button></div>` +
      (s.status === "loading" && !posts.length ? V.loading("Loading posts…") : "") +
      (posts.length ? `<ul class="gs-ch-list">${posts.map(postRow).join("")}</ul>` : s.status === "ok" ? `<p class="gs-hint">No posts yet.</p>` : "") +
      `</div>`
    );
  }

  // ── phone calls ───────────────────────────────────────────────────────────────────────────────
  function answersHtml(d) {
    if (!d) return "";
    const answers = (d.answers || []).map((a) => `<li><strong>${esc(a.key.replace(/_/g, " "))}:</strong> ${esc(a.answer)}</li>`).join("");
    const talk = (d.transcript || []).map((t) => `<li><em>${esc(t.who || "")}</em> ${esc(t.text)}</li>`).join("");
    if (!answers && !talk) return `<p class="gs-hint">No answers or transcript yet.</p>`;
    return (
      (answers ? `<div class="gs-label">Answers</div><ul class="gs-ch-answers">${answers}</ul>` : "") +
      (talk ? `<details class="gs-notes"><summary>Transcript</summary><ul class="gs-ch-answers">${talk}</ul></details>` : "") +
      (d.has_recording ? `<p class="gs-hint">A recording is available in Outreach.</p>` : "")
    );
  }

  function callRow(c, detail) {
    const status = String(c.status || "").toLowerCase();
    const st = label(CALL_STATUS, status);
    const live = LIVE_CALL.indexOf(status) >= 0;
    const secs = c.duration_seconds != null ? ` · ${Math.floor(c.duration_seconds / 60)}m ${String(c.duration_seconds % 60).padStart(2, "0")}s` : "";
    return (
      `<li class="gs-ch-item" data-call="${esc(c.id)}">` +
      `<div class="gs-ch-item__head">${badge(st)} <strong>${esc(c.buyer_name)}</strong><span class="gs-hint"> · ${esc(c.number_masked || "")}${secs}${c.mode === "live" ? "" : " · test mode"}</span></div>` +
      (c.outcome ? `<p class="gs-hint">Outcome: ${esc(String(c.outcome).replace(/_/g, " "))}${c.meeting_booked ? " " + V.badge("Meeting booked", "good") : ""}</p>` : "") +
      (c.goal ? `<p class="gs-hint">Goal: ${esc(String(c.goal).slice(0, 160))}</p>` : "") +
      (c.error ? `<p class="gs-hint gs-hint--warn">${esc(c.error)}</p>` : "") +
      answersHtml(detail) +
      `<div class="gs-actions">` +
      (live ? `<button type="button" class="gs-btn gs-btn--sm" data-action="ch-call-end" data-id="${esc(c.id)}">End call</button>` : "") +
      `<button type="button" class="gs-btn gs-btn--sm" data-action="ch-call-details" data-id="${esc(c.id)}">${detail ? "Reload details" : "What was said"}</button></div>` +
      `</li>`
    );
  }

  function callCard(m) {
    const c = m.calls || {};
    const f = c.form || {};
    const scripts = c.scripts || [];
    const calls = c.list || [];
    const phones = m.info && m.info.wallet ? m.info.wallet.phone_numbers : null;
    return (
      `<div class="gs-card" id="gsChCalls"><div class="gs-card__title">Phone call</div>` +
      `<p class="gs-hint gs-hint--block">Outreach's AI phones <strong>one</strong> number when you press Call. In the UK a marketing call may go to an organisation, not to an individual (most mobiles and sole traders), and never to a number on the TPS or CTPS. Only landlines starting 01, 02 or 03 are accepted here.</p>` +
      (phones === 0 ? `<p class="gs-hint gs-hint--warn">No phone number is connected in Outreach yet, so the AI has no line to call from. Add one in Outreach first.</p>` : "") +
      `<label class="gs-label" for="gsChBuyer">Buyer</label>` +
      `<div class="gs-ch-row"><input id="gsChBuyer" class="gs-input gs-input--sm" data-field="ch-buyer" maxlength="120" autocomplete="off" value="${esc(f.buyer || "")}" placeholder="e.g. Leeds City Council">` +
      `<button type="button" class="gs-btn gs-btn--sm" data-action="ch-lookup"${f.lookupBusy || String(f.buyer || "").trim().length < 3 ? " disabled" : ""}>${f.lookupBusy ? "Looking…" : "Find the number on its notices"}</button></div>` +
      (f.lookupNote ? `<p class="gs-hint">${esc(f.lookupNote)}</p>` : "") +
      `<label class="gs-label" for="gsChNumber">Phone number</label>` +
      `<input id="gsChNumber" class="gs-input gs-input--sm" data-field="ch-number" maxlength="25" autocomplete="off" inputmode="tel" value="${esc(f.number || "")}" placeholder="e.g. 0113 496 0000">` +
      `<label class="gs-label" for="gsChGoal">What is the call for?</label>` +
      `<textarea id="gsChGoal" class="gs-textarea gs-textarea--mid" data-field="ch-goal" maxlength="400" rows="2" placeholder="e.g. Ask who handles gas servicing and when the contract is next reviewed">${esc(f.goal || "")}</textarea>` +
      (scripts.length
        ? `<label class="gs-label" for="gsChScript">Or use a saved script</label><select id="gsChScript" class="gs-select gs-select--sm" data-field="ch-script"><option value="">No saved script</option>${scripts.map((s) => `<option value="${esc(s.id)}"${f.scriptId === s.id ? " selected" : ""}>${esc(s.name)}</option>`).join("")}</select>`
        : "") +
      `<label class="gs-label" for="gsChQuestions">Questions to ask (one per line, optional)</label>` +
      `<textarea id="gsChQuestions" class="gs-textarea gs-textarea--mid" data-field="ch-questions" rows="3" placeholder="How many sites do you manage?">${esc(f.questionsText || "")}</textarea>` +
      `<label class="gs-label" for="gsChInstr">How should it sound? (optional)</label>` +
      `<input id="gsChInstr" class="gs-input gs-input--sm" data-field="ch-instructions" maxlength="1000" value="${esc(f.instructions || "")}" placeholder="e.g. Be brief and friendly. Do not talk about pricing.">` +
      `<div class="gs-ch-consent"><label class="gs-inline"><input type="checkbox" data-field="ch-business"${f.business ? " checked" : ""}> This is an organisation's line, not an individual's</label>` +
      `<label class="gs-inline"><input type="checkbox" data-field="ch-tps"${f.tps ? " checked" : ""}> I have checked this number against the TPS / CTPS</label></div>` +
      (f.error ? `<p class="gs-hint gs-hint--warn" role="alert">${esc(f.error)}</p>` : "") +
      `<div class="gs-actions"><button type="button" class="gs-btn gs-btn--primary gs-btn--sm" data-action="ch-call-make"${callReady(f) ? "" : " disabled"}>${f.busy ? "Calling…" : "Call now"}</button>` +
      `<button type="button" class="gs-btn gs-btn--sm" data-action="ch-call-refresh"${c.refreshing ? " disabled" : ""}>${c.refreshing ? "Checking…" : "Refresh"}</button></div>` +
      `<p class="gs-hint">Limited to ${esc((m.info && m.info.call_cap) || 20)} calls a day. The same number is not called twice in 24 hours unless nobody answered.</p>` +
      (c.status === "loading" && !calls.length ? V.loading("Loading calls…") : "") +
      (calls.length ? `<ul class="gs-ch-list">${calls.map((x) => callRow(x, (c.details || {})[x.id])).join("")}</ul>` : c.status === "ok" ? `<p class="gs-hint">No calls yet.</p>` : "") +
      `</div>`
    );
  }

  function channelsPane(m) {
    if (!m || m.status === "idle" || (m.status === "loading" && !m.info)) return V.loading("Checking Outreach…");
    const info = m.info || {};
    if (!info.ready) {
      return (
        V.note(`${esc(info.problem || m.error || "Outreach is not available.")}`, "bad") +
        V.outreachConnectBox(info)
      );
    }
    const mode = info.mode === "live" ? V.badge("Live key", "warn") : V.badge("Test mode", "good");
    const credits = info.wallet && info.wallet.credits != null ? ` · ${esc(info.wallet.credits)} credits in Outreach` : "";
    const chans = (info.wallet && info.wallet.social_channels) || [];
    return (
      `<p class="gs-hint gs-hint--block">${mode} Connected${credits}.` +
      (chans.length ? ` Social channels: <strong>${esc(chans.map(chLabel).join(", "))}</strong>.` : "") +
      (info.mode === "live"
        ? " Live key: approving a post publishes it, and a call really rings the buyer."
        : " Test key: Outreach pretends everything. Nothing is published, nobody is phoned, and nothing is charged.") +
      `</p>` +
      V.outreachConnectBox(info) +
      `<div class="gs-ch-grid">${socialCard(m)}${callCard(m)}</div>`
    );
  }

  const api = {
    POST_STATUS, CALL_STATUS, STARTER_TEXT, postReady, callReady, publishIso, postBody, callBody, postRow, socialCard, answersHtml,
    callRow, callCard, channelsPane, plural,
  };
  root.GrowthChannels = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;

  // ── controller (browser only) ─────────────────────────────────────────────────────────────────
  if (typeof document === "undefined") return;

  const byId = (id) => document.getElementById(id);
  const newAttempt = () => {
    const a = new Uint8Array(8);
    (root.crypto || {}).getRandomValues ? root.crypto.getRandomValues(a) : a.forEach((_, i) => (a[i] = Math.floor(Math.random() * 256)));
    return Array.from(a, (b) => b.toString(16).padStart(2, "0")).join("");
  };
  const freshPostForm = () => ({ prompt: "", channels: [], publishAt: "", pictureOn: true, pictureNote: "", attempt: newAttempt(), busy: false, error: null });
  const freshCallForm = () => ({ buyer: "", number: "", goal: "", scriptId: "", instructions: "", questionsText: "", business: false, tps: false,
    attempt: newAttempt(), busy: false, error: null, lookupBusy: false, lookupNote: "" });

  const model = {
    status: "idle", info: null, error: null, opened: false, open: false,
    social: { status: "idle", posts: [], form: freshPostForm(), refreshing: false },
    calls: { status: "idle", list: [], form: freshCallForm(), refreshing: false, scripts: [], details: {} },
  };
  let pollTimer = null;

  async function api2(method, url, body) {
    let res;
    try {
      res = await fetch(url, { method, credentials: "same-origin", headers: body !== undefined ? { "Content-Type": "application/json" } : {}, body: body !== undefined ? JSON.stringify(body) : undefined });
    } catch (e) {
      const err = new Error("Could not reach the server. Check your connection and try again.");
      err.status = 0;
      throw err;
    }
    let data = null;
    try { data = await res.json(); } catch (e) { /* not JSON */ }
    if (!res.ok) {
      const err = new Error(res.status === 429 && !(data && data.error) ? "You are going too fast. Wait a moment and try again." : (data && data.error) || `Something went wrong (${res.status}).`);
      err.status = res.status;
      throw err;
    }
    return data;
  }

  function toast(message, error) {
    const el = byId("gsToast");
    if (!el) return;
    el.className = "gs-toast is-on" + (error ? " is-error" : "");
    el.innerHTML = `<span>${esc(message)}</span>`;
    clearTimeout(toast.t);
    toast.t = setTimeout(() => el.classList.remove("is-on"), error ? 7000 : 3500);
  }

  async function confirmIt(message) {
    return typeof root.showConfirm === "function" ? root.showConfirm(message) : root.confirm(message);
  }

  function render() {
    const pane = byId("gsPaneChannels");
    if (!pane) return;
    const active = document.activeElement;
    const field = active && pane.contains(active) ? active.getAttribute("data-field") : null;
    const start = field && active.selectionStart != null ? [active.selectionStart, active.selectionEnd] : null;
    pane.innerHTML = channelsPane(model);
    if (field) {
      const again = pane.querySelector(`[data-field="${field}"]`);
      if (again && again.tagName !== "SELECT") {
        again.focus();
        if (start && again.setSelectionRange) try { again.setSelectionRange(start[0], start[1]); } catch (e) { /* not a text field */ }
      }
    }
  }

  // Typing must not rebuild the form (it would lose the caret): only the two main buttons are kept in step.
  function syncButtons() {
    const pane = byId("gsPaneChannels");
    if (!pane) return;
    const a = pane.querySelector('[data-action="ch-post-create"]');
    const b = pane.querySelector('[data-action="ch-call-make"]');
    const c = pane.querySelector('[data-action="ch-lookup"]');
    if (a) a.disabled = !postReady(model.social.form);
    if (b) b.disabled = !callReady(model.calls.form);
    if (c) c.disabled = model.calls.form.lookupBusy || model.calls.form.buyer.trim().length < 3;
  }

  async function loadStatus() {
    model.status = "loading";
    render();
    try {
      model.info = await api2("GET", "/api/growth/outreach/status");
      model.status = "ok";
    } catch (e) {
      model.status = "error";
      model.error = e.message;
      model.info = { ready: false, problem: e.message };
    }
    render();
    if (model.info && model.info.ready) {
      await Promise.all([loadPosts(false), loadCalls(false), loadScripts()]);
    }
  }

  async function loadPosts(refresh) {
    const s = model.social;
    s.status = s.status === "ok" ? "ok" : "loading";
    s.refreshing = !!refresh;
    render();
    try {
      const res = await api2("GET", "/api/growth/social/posts" + (refresh ? "?refresh=1" : ""));
      s.posts = res.posts || [];
      s.status = "ok";
      if (res.stopped) toast(res.stopped, true);
    } catch (e) {
      s.status = "ok";
      toast(e.message, true);
    }
    s.refreshing = false;
    render();
    schedulePoll();
  }

  async function loadCalls(refresh) {
    const c = model.calls;
    c.status = c.status === "ok" ? "ok" : "loading";
    c.refreshing = !!refresh;
    render();
    try {
      const res = await api2("GET", "/api/growth/voice/calls" + (refresh ? "?refresh=1" : ""));
      c.list = res.calls || [];
      c.status = "ok";
      if (res.stopped) toast(res.stopped, true);
    } catch (e) {
      c.status = "ok";
      toast(e.message, true);
    }
    c.refreshing = false;
    render();
    schedulePoll();
  }

  async function loadScripts() {
    try {
      model.calls.scripts = (await api2("GET", "/api/growth/voice/scripts")).scripts || [];
      render();
    } catch (e) { /* the saved-script choice simply is not offered */ }
  }

  // While a post is being written or a call is in progress, look again every few seconds (the pane must be open).
  function schedulePoll() {
    clearTimeout(pollTimer);
    if (!model.open) return;
    const writing = model.social.posts.some((p) => String(p.status).toLowerCase() === "writing");
    const live = model.calls.list.some((c) => LIVE_CALL.indexOf(String(c.status).toLowerCase()) >= 0);
    if (!writing && !live) return;
    pollTimer = setTimeout(async () => {
      if (!model.open) return;
      if (writing) await loadPosts(true);
      if (live) await loadCalls(true);
    }, 7000);
  }

  async function createPost() {
    const f = model.social.form;
    if (!postReady(f)) return;
    f.busy = true;
    f.error = null;
    render();
    try {
      const res = await api2("POST", "/api/growth/social/posts", postBody(f));
      model.social.posts.unshift(res.post);
      model.social.form = Object.assign(freshPostForm(), { channels: f.channels.slice() });
      toast("Outreach is writing the post. Read the preview, then approve it.");
    } catch (e) {
      f.busy = false;
      f.error = e.message;
      f.attempt = newAttempt(); // a new try is a new request, not a replay of this refusal
    }
    render();
    schedulePoll();
  }

  async function decidePost(id, decision) {
    const post = model.social.posts.find((p) => String(p.id) === String(id));
    if (!post) return;
    const when = post.publish_at ? ` at ${post.publish_at}` : "";
    if (decision === "approve" && !(await confirmIt(`Publish this post to ${(post.channels || []).map(chLabel).join(" and ") || "your channels"}${when}? Once approved it goes live.`))) return;
    if (decision === "cancel" && !(await confirmIt("Cancel this post? It will not be published."))) return;
    try {
      const res = await api2("POST", `/api/growth/social/posts/${encodeURIComponent(id)}/decision`, { decision, confirm: decision === "approve" ? true : undefined, attempt: newAttempt() });
      const i = model.social.posts.findIndex((p) => String(p.id) === String(id));
      if (i >= 0) model.social.posts[i] = res.post;
      toast(decision === "approve" ? "Approved. Outreach will publish it." : decision === "reject" ? "Post rejected." : "Post cancelled.");
    } catch (e) {
      toast(e.message, true);
      loadPosts(true);
    }
    render();
  }

  async function lookupNumber() {
    const f = model.calls.form;
    f.lookupBusy = true;
    f.lookupNote = "";
    render();
    try {
      const res = await api2("GET", "/api/growth/voice/lookup?buyer=" + encodeURIComponent(f.buyer.trim()));
      if (!res.found) f.lookupNote = "No phone number is published on the notices we hold for this buyer. Type one in.";
      else if (res.number) { f.number = res.phone; f.lookupNote = `Found on a notice: ${res.phone}. Check it is still right before you call.`; }
      else f.lookupNote = `A notice lists ${res.phone}, but it cannot be called from here: ${res.problem}`;
    } catch (e) {
      f.lookupNote = e.message;
    }
    f.lookupBusy = false;
    render();
  }

  async function makeCall() {
    const f = model.calls.form;
    if (!callReady(f)) return;
    if (!(await confirmIt(`Phone ${f.buyer.trim()} on ${f.number.trim()} now? The AI will ring the number straight away.`))) return;
    f.busy = true;
    f.error = null;
    render();
    try {
      const res = await api2("POST", "/api/growth/voice/calls", callBody(f));
      model.calls.list.unshift(res.call);
      model.calls.form = Object.assign(freshCallForm(), { scriptId: f.scriptId });
      toast("The call has started. Outreach will report what happens.");
    } catch (e) {
      f.busy = false;
      f.error = e.message;
      f.attempt = newAttempt();
    }
    render();
    schedulePoll();
  }

  async function endCall(id) {
    try {
      const res = await api2("DELETE", `/api/growth/voice/calls/${encodeURIComponent(id)}`);
      const i = model.calls.list.findIndex((c) => String(c.id) === String(id));
      if (i >= 0) model.calls.list[i] = res.call;
      toast("Call ended.");
    } catch (e) {
      toast(e.message, true);
    }
    render();
  }

  async function callDetails(id) {
    try {
      model.calls.details[id] = (await api2("GET", `/api/growth/voice/calls/${encodeURIComponent(id)}`)).call;
    } catch (e) {
      toast(e.message, true);
    }
    render();
  }

  function onClick(event) {
    const a = event.target.closest("[data-action]");
    if (!a || !byId("gsPaneChannels").contains(a)) return;
    const act = a.getAttribute("data-action");
    const run = (p) => p.catch((e) => toast((e && e.message) || "Something went wrong.", true));
    if (act === "ch-post-create") run(createPost());
    else if (act === "ch-post-refresh") run(loadPosts(true));
    else if (act === "ch-post-decide") run(decidePost(a.dataset.id, a.dataset.decision));
    else if (act === "ch-call-make") run(makeCall());
    else if (act === "ch-call-refresh") run(loadCalls(true));
    else if (act === "ch-call-end") run(endCall(a.dataset.id));
    else if (act === "ch-call-details") run(callDetails(a.dataset.id));
    else if (act === "ch-lookup") run(lookupNumber());
    else if (act === "outreach-check") run(loadStatus());
  }

  function onInput(event) {
    const t = event.target;
    const field = t.getAttribute && t.getAttribute("data-field");
    if (!field || !field.startsWith("ch-")) return;
    const s = model.social.form;
    const c = model.calls.form;
    if (field === "ch-prompt") s.prompt = t.value;
    else if (field === "ch-publish") s.publishAt = t.value;
    else if (field === "ch-picture-note") s.pictureNote = t.value;
    else if (field === "ch-buyer") { c.buyer = t.value; c.lookupNote = ""; }
    else if (field === "ch-number") c.number = t.value;
    else if (field === "ch-goal") c.goal = t.value;
    else if (field === "ch-questions") c.questionsText = t.value;
    else if (field === "ch-instructions") c.instructions = t.value;
    else return;
    syncButtons();
  }

  function onChange(event) {
    const t = event.target;
    const field = t.getAttribute && t.getAttribute("data-field");
    if (!field || !field.startsWith("ch-")) return;
    const s = model.social.form;
    const c = model.calls.form;
    if (field === "ch-starter") {
      if (t.value && STARTER_TEXT[t.value]) s.prompt = STARTER_TEXT[t.value];
      render();
      return;
    }
    if (field === "ch-channel") {
      s.channels = t.checked ? s.channels.concat(t.value).filter((v, i, a) => a.indexOf(v) === i) : s.channels.filter((v) => v !== t.value);
    } else if (field === "ch-picture") { s.pictureOn = t.checked; render(); return; }
    else if (field === "ch-script") c.scriptId = t.value;
    else if (field === "ch-business") c.business = t.checked;
    else if (field === "ch-tps") c.tps = t.checked;
    else return;
    syncButtons();
  }

  // Called by growth-studio.js when the tab is shown / hidden.
  api.open = function () {
    model.open = true;
    if (!model.opened) {
      model.opened = true;
      const pane = byId("gsPaneChannels");
      if (pane) {
        pane.addEventListener("click", onClick);
        pane.addEventListener("input", onInput);
        pane.addEventListener("change", onChange);
      }
      loadStatus();
    } else {
      render();
      loadPosts(false);
      loadCalls(false);
    }
  };
  api.close = function () {
    model.open = false;
    clearTimeout(pollTimer);
  };
})(typeof window !== "undefined" ? window : globalThis);
