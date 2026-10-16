/* Growth Studio "Social" and "Calls" tabs. Social: write a post with Outreach's AI on the left; on the right, the post as
   Outreach made it (picture, caption, how it reads per channel), its schedule, and the approval choices. Calls: have
   Outreach's AI phone one buyer, with the call history alongside. Pure view functions first (no DOM, no fetch;
   tests/growth_channels_test.js runs them in Node), then a small controller that mounts them into #gsPaneSocial and
   #gsPaneCalls. Every value from the server or the user goes through esc(). Nothing here publishes or dials by itself:
   a post is only approved, and a call only placed, by the button for it, after a confirmation, and the server refuses
   anything that breaks the rules (see tender_app/outreach_channels.py). */
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
  const REWRITE = [["text", "The text"], ["picture", "The picture"], ["both", "Text and picture"]];

  const STARTERS = [
    ["", "Start from an idea…"],
    ["about", "Introduce our company"],
    ["win", "A contract we won"],
    ["insight", "A public-sector buying insight"],
    ["service", "A new service or capability"],
    ["event", "An event or open day"],
  ];
  const STARTER_TEXT = {
    about: "Introduce [company] to public-sector buyers: what we do, who we help and how to get in touch.",
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

  // ── times: Outreach gives UTC, the user thinks in local time ──────────────────────────────────
  function toLocalInput(iso) {
    const d = new Date(iso || "");
    if (!iso || Number.isNaN(d.getTime())) return "";
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
  }
  function whenText(iso) {
    const d = new Date(iso || "");
    return !iso || Number.isNaN(d.getTime()) ? null : d.toLocaleString("en-GB", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
  }
  function publishIso(local) {
    if (!local) return undefined;
    const t = new Date(local);
    return Number.isNaN(t.getTime()) ? undefined : t.toISOString();
  }

  // ── validity (the same rules the server applies; the server is the one that enforces them) ───
  const MAX_DRAFT = 1500; // the caption a user writes is also the prompt of the new post (see och.MAX_DRAFT_CAPTION)
  const MAX_EDIT = 3000;
  const MAX_PICTURE_MB = 8; // Outreach's own limit; a larger picture is shrunk in the browser before it is sent
  const PICTURE_EDGE = 1600;
  const PICTURE_KEEP = 700 * 1024; // a picture this small is sent as it is
  const howOf = (f) => (f && f.how === "mine" ? "mine" : "ai");
  const pictureKindOf = (f) => (f && f.pictureKind) || (f && f.pictureOn === false ? "none" : "ai");
  const draftText = (f) => String((howOf(f) === "mine" ? f.caption : f.prompt) || "").trim();
  function postReady(f) {
    if (!f || f.busy || !(f.channels || []).length) return false;
    if (pictureKindOf(f) === "mine" && !f.pictureData) return false;
    return draftText(f).length >= (howOf(f) === "mine" ? 3 : 10);
  }
  // A picture's size after it is scaled down to fit `max` pixels on its longer side (never scaled up).
  function fitSize(w, h, max) {
    const k = Math.min(1, max / Math.max(w || 1, h || 1));
    return [Math.max(1, Math.round((w || 1) * k)), Math.max(1, Math.round((h || 1) * k))];
  }
  // The caption box on a post differs from what the post has now, and is long enough to save.
  function captionChanged(post, edit) {
    if (!edit || edit.caption == null) return false;
    const t = String(edit.caption).trim();
    return t.length >= 3 && t !== String((post && post.caption) || "").trim();
  }
  function callReady(f) {
    return !!f && !!String(f.buyer || "").trim() && !!String(f.number || "").trim() && (!!String(f.goal || "").trim() || !!f.scriptId)
      && f.business === true && f.tps === true && !f.busy;
  }
  function postBody(f) {
    const body = { channels: f.channels.slice(), attempt: f.attempt, profile_id: f.profileId || null };
    if (howOf(f) === "mine") {
      body.caption = String(f.caption || "").trim();
      if (f.polish) body.polish = true; // the AI improves the caption instead of using it as written
    } else {
      body.prompt = String(f.prompt || "").trim();
    }
    const when = publishIso(f.publishAt);
    if (when) body.publish_at = when;
    // the user's own picture is not part of the new post: it is set on the post by hand once Outreach has opened it
    body.picture = pictureKindOf(f) === "ai" ? { mode: "ai", instruction: String(f.pictureNote || "").trim() || undefined } : { mode: "none" };
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
  // The time field differs from the one the post already has: it can be saved, or used to approve at a new time.
  function timeChanged(post, edit) {
    return !!edit && !!edit.time && edit.time !== toLocalInput(post && post.publish_at);
  }

  // ── the strip above both tabs ─────────────────────────────────────────────────────────────────
  function infoBar(m, which) {
    const info = m.info || {};
    const mode = info.mode === "live" ? V.badge("Live key", "warn") : V.badge("Test mode", "good");
    const credits = info.wallet && info.wallet.credits != null ? ` · ${esc(info.wallet.credits)} credits in Outreach` : "";
    const chans = (info.wallet && info.wallet.social_channels) || [];
    const live = which === "social"
      ? (info.mode === "live" ? " Live key: approving a post publishes it." : " Test key: Outreach pretends everything. Nothing is published and nothing is charged.")
      : (info.mode === "live" ? " Live key: a call really rings the buyer." : " Test key: Outreach pretends everything. Nobody is phoned and nothing is charged.");
    return (
      `<p class="gs-hint gs-hint--block">${mode} Connected${credits}.` +
      (which === "social" && chans.length ? ` Social channels: <strong>${esc(chans.map(chLabel).join(", "))}</strong>.` : "") +
      live + `</p>` + V.outreachConnectBox(info)
    );
  }

  function gate(m, which) {
    if (!m || m.status === "idle" || (m.status === "loading" && !m.info)) return V.loading("Checking Outreach…");
    const info = m.info || {};
    if (!info.ready) return V.note(esc(info.problem || m.error || "Outreach is not available."), "bad") + V.outreachConnectBox(info);
    return null;
  }

  // ── social: the post list ─────────────────────────────────────────────────────────────────────
  function postListItem(p, selected) {
    const st = label(POST_STATUS, p.status);
    const channels = (p.channels || []).map(chLabel).join(", ");
    const text = String(p.caption || p.prompt || "").replace(/\s+/g, " ");
    const when = whenText(p.publish_at);
    return (
      `<li class="gs-ch-item gs-sp-item${selected ? " is-selected" : ""}" data-post="${esc(p.id)}">` +
      `<button type="button" class="gs-sp-pick" data-action="ch-post-select" data-id="${esc(p.id)}" aria-pressed="${selected ? "true" : "false"}">` +
      `<span class="gs-sp-pick__head">${badge(st)} <strong>${esc(channels || "No channel")}</strong>${when ? ` <span class="gs-hint">· ${esc(when)}</span>` : ""}</span>` +
      `<span class="gs-sp-pick__text">${esc(text.slice(0, 110))}${text.length > 110 ? "…" : ""}</span></button></li>`
    );
  }

  // "Post as": the user's company profiles from TenderFlow, and exactly what the AI is told about the chosen one.
  function companyField(m) {
    const s = m.social || {};
    const co = s.company || {};
    const f = s.form || {};
    const profiles = co.profiles || [];
    if (co.status !== "ok") return `<div class="gs-sp-company"><div class="gs-label">Post as</div><p class="gs-hint">Loading your companies…</p></div>`;
    if (!profiles.length) {
      return (
        `<div class="gs-sp-company"><div class="gs-label">Post as</div>` +
        `<p class="gs-hint gs-hint--warn">${esc((co.hints || [])[0] || "No company is set up yet.")}</p>` +
        `<button type="button" class="gs-btn gs-btn--sm" data-action="ch-open-profiles">Set up your company</button>` +
        `<p class="gs-hint">You can still write a general post below.</p></div>`
      );
    }
    const opts = profiles.map((p) => `<option value="${esc(p.id)}"${String(f.profileId) === String(p.id) ? " selected" : ""}>${esc(p.name)}${p.is_default ? " (default)" : ""}</option>`).join("");
    const b = co.brief;
    const chosen = f.profileId && b;
    return (
      `<div class="gs-sp-company"><label class="gs-label" for="gsChCompany">Post as</label>` +
      `<select id="gsChCompany" class="gs-select gs-select--sm" data-field="ch-company"><option value=""${f.profileId ? "" : " selected"}>No company (a general post)</option>${opts}</select>` +
      (chosen
        ? `<details class="gs-notes"><summary>What Outreach is told about ${esc(b.name)}</summary>` +
          `<p class="gs-hint">The AI writes as this company and may only use these facts. Edit them in your company profile.</p>` +
          `<ul class="gs-ch-answers"><li><strong>Name:</strong> ${esc(b.name)}</li>` +
          (b.where ? `<li><strong>Based in:</strong> ${esc(b.where)}</li>` : "") +
          (b.about ? `<li><strong>What it does:</strong> ${esc(b.about)}</li>` : "") +
          (b.website ? `<li><strong>Website:</strong> ${esc(b.website)}</li>` : "") + `</ul>` +
          `<button type="button" class="gs-btn gs-btn--sm" data-action="ch-open-profiles">Open Company profiles</button></details>` +
          (co.hints || []).map((h) => `<p class="gs-hint gs-hint--warn">${esc(h)}</p>`).join("")
        : `<p class="gs-hint">Nothing about a company is sent, so the post is general.</p>`) +
      `</div>`
    );
  }

  function socialComposer(m) {
    const s = m.social || {};
    const f = s.form || {};
    const reported = ((m.info && m.info.wallet && m.info.wallet.social_channels) || []).filter(Boolean);
    const choices = reported.length ? reported : DEFAULT_CHANNELS;
    const checks = choices.map((c) => `<label class="gs-inline"><input type="checkbox" data-field="ch-channel" value="${esc(c)}"${(f.channels || []).indexOf(c) >= 0 ? " checked" : ""}> ${esc(chLabel(c))}</label>`).join("");
    const how = howOf(f);
    const kind = pictureKindOf(f);
    const radio = (field, value, checked, text) =>
      `<label class="gs-inline"><input type="radio" name="${field}" data-field="${field}" value="${value}"${checked ? " checked" : ""}> ${text}</label>`;
    const writing = how === "mine"
      ? `<label class="gs-label" for="gsChCaption">Your caption</label>` +
        `<textarea id="gsChCaption" class="gs-textarea gs-textarea--mid" data-field="ch-caption" maxlength="${MAX_DRAFT}" rows="5" placeholder="Write the caption exactly as it should read">${esc(f.caption || "")}</textarea>` +
        `<p class="gs-hint"><span id="gsChCount">${String(f.caption || "").length}</span> / ${MAX_DRAFT.toLocaleString("en-GB")} characters. X allows 280; LinkedIn and Facebook allow far more.</p>` +
        `<label class="gs-inline"><input type="checkbox" data-field="ch-polish"${f.polish ? " checked" : ""}> Let the AI polish it (same meaning and facts, nothing added)</label>` +
        `<p class="gs-hint">${f.polish ? "Outreach's AI improves your wording and you read the result before approving. You can still edit it by hand on the preview."
          : "Your words are used as written: once Outreach has opened the post, your caption is set on it by hand."}</p>`
      : `<label class="gs-label" for="gsChStarter">Idea</label>` +
        `<select id="gsChStarter" class="gs-select gs-select--sm" data-field="ch-starter">${STARTERS.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join("")}</select>` +
        `<label class="gs-label" for="gsChPrompt">What should the post say?</label>` +
        `<textarea id="gsChPrompt" class="gs-textarea gs-textarea--mid" data-field="ch-prompt" maxlength="1500" rows="4" placeholder="e.g. Announce our new Leeds office opening on 1 November">${esc(f.prompt || "")}</textarea>`;
    const picture =
      `<div class="gs-label">Picture</div><div class="gs-ch-checks gs-sp-how">` +
      radio("ch-picture-kind", "ai", kind === "ai", "AI picture") + radio("ch-picture-kind", "mine", kind === "mine", "My own picture") + radio("ch-picture-kind", "none", kind === "none", "No picture") + `</div>` +
      (kind === "ai" ? `<label class="gs-label" for="gsChPic">Picture idea (optional)</label><input id="gsChPic" class="gs-input gs-input--sm" data-field="ch-picture-note" maxlength="300" value="${esc(f.pictureNote || "")}" placeholder="e.g. A bright modern office">` : "") +
      (kind === "mine" ? pictureChooser(f.pictureData, f.pictureError, "ch-picture-file", "ch-picture-clear") : "");
    return (
      `<div class="gs-card" id="gsChSocial"><div class="gs-card__title">New post</div>` +
      companyField(m) +
      `<div class="gs-label">How do you want to write it?</div><div class="gs-ch-checks gs-sp-how">` +
      radio("ch-how", "ai", how === "ai", "Let the AI write it") + radio("ch-how", "mine", how === "mine", "I'll write the caption") + `</div>` +
      `<p class="gs-hint gs-hint--block">${how === "mine" ? "Write the caption yourself. " : "Describe the post and Outreach's AI writes the caption. "}<strong>Nothing is published until you read the preview and approve it.</strong> Do not include a buyer's or a customer's confidential details.</p>` +
      writing +
      `<div class="gs-label">Post to</div><div class="gs-ch-checks">${checks}</div>` +
      (reported.length ? "" : `<p class="gs-hint">Outreach did not list your connected channels, so the usual ones are shown. Outreach refuses one that is not connected.</p>`) +
      `<div class="gs-ch-row"><label class="gs-inline">Publish at <input type="datetime-local" class="gs-input gs-input--sm" data-field="ch-publish" value="${esc(f.publishAt || "")}"></label></div>` +
      picture +
      `<p class="gs-hint">You can leave the time empty and set it on the preview. A time needs to be at least 5 minutes ahead so there is time to approve.</p>` +
      (f.error ? `<p class="gs-hint gs-hint--warn" role="alert">${esc(f.error)}</p>` : "") +
      `<div class="gs-actions"><button type="button" class="gs-btn gs-btn--primary gs-btn--sm" data-action="ch-post-create"${postReady(f) ? "" : " disabled"}>${f.busy ? (how === "mine" ? "Working…" : "Writing…") : how === "mine" ? (f.polish ? "Polish and create the post" : "Create the post") : "Write the post"}</button></div>` +
      `</div>`
    );
  }

  // Choosing a picture of one's own: the file box, a thumbnail of the one chosen, and why a file was refused.
  function pictureChooser(data, problem, fileField, clearAction) {
    const src = typeof data === "string" && /^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$/.test(data) ? data : "";
    return (
      `<div class="gs-sp-own">` +
      (src ? `<img class="gs-sp-thumb" src="${src}" alt="The picture you chose">` : "") +
      `<label class="gs-btn gs-btn--sm gs-sp-file">${src ? "Choose another" : "Choose a picture"}<input type="file" accept="image/png,image/jpeg,image/webp" data-field="${fileField}" class="gs-sp-file__input"></label>` +
      (src ? ` <button type="button" class="gs-btn gs-btn--sm" data-action="${clearAction}">Remove</button>` : "") +
      `<p class="gs-hint">PNG, JPG or WebP up to ${MAX_PICTURE_MB} MB. A large picture is scaled down before it is sent. Use only pictures you have the right to post.</p>` +
      (problem ? `<p class="gs-hint gs-hint--warn" role="alert">${esc(problem)}</p>` : "") +
      `</div>`
    );
  }

  function socialHistory(m) {
    const s = m.social || {};
    const posts = s.posts || [];
    const sel = selectedPost(s);
    return (
      `<div class="gs-card"><div class="gs-card__title">Your posts</div>` +
      `<div class="gs-actions gs-actions--tight"><button type="button" class="gs-btn gs-btn--sm" data-action="ch-post-refresh"${s.refreshing ? " disabled" : ""}>${s.refreshing ? "Checking…" : "Refresh"}</button></div>` +
      (s.status === "loading" && !posts.length ? V.loading("Loading posts…") : "") +
      (posts.length ? `<ul class="gs-ch-list">${posts.map((p) => postListItem(p, sel && String(sel.id) === String(p.id))).join("")}</ul>` : s.status === "ok" ? `<p class="gs-hint">No posts yet.</p>` : "") +
      `</div>`
    );
  }

  function selectedPost(s) {
    const posts = (s && s.posts) || [];
    return posts.find((p) => String(p.id) === String(s.selectedId)) || posts[0] || null;
  }

  // ── social: the preview, schedule and approval panel (right-hand side) ────────────────────────
  function pictureBlock(p, status) {
    const src = V.safeUrl(p.image_url);
    if (src && /^https:/i.test(src)) {
      return `<div class="gs-sp-image"><img src="${esc(src)}" alt="The picture Outreach made for this post" loading="lazy" referrerpolicy="no-referrer"></div>`;
    }
    return `<div class="gs-sp-image gs-sp-image--empty">${status === "writing" ? "The AI is writing the post and drawing the picture…" : "No picture for this post."}</div>`;
  }

  function scheduleBlock(p, edit, final, status) {
    const when = whenText(p.publish_at);
    const changed = timeChanged(p, edit);
    return (
      `<div class="gs-sp-section"><div class="gs-label">Schedule</div>` +
      `<p class="gs-sp-when">${when ? `Goes out <strong>${esc(when)}</strong>` : "No publish time set."}${p.mode === "live" ? "" : ` <span class="gs-hint">(test mode: nothing really goes out)</span>`}</p>` +
      (final ? "" :
        `<div class="gs-ch-row"><label class="gs-inline">Publish at <input type="datetime-local" class="gs-input gs-input--sm" data-field="ch-edit-time" value="${esc((edit && edit.time) || "")}"></label>` +
        `<button type="button" class="gs-btn gs-btn--sm" data-action="ch-post-time" data-id="${esc(p.id)}"${changed && !edit.busy ? "" : " disabled"}>${edit && edit.busy === "time" ? "Saving…" : "Change time"}</button></div>` +
        `<p class="gs-hint">Changing the time sends the post back for approval, so you approve it again at the new time. Approval closes at the publish time.</p>`) +
      `</div>`
    );
  }

  // Edit the caption by hand, or replace the picture with the user's own. Not while the AI is still writing: it would overwrite the change.
  function handBlock(p, edit, status) {
    if (status === "writing") {
      return `<div class="gs-sp-section"><div class="gs-label">Edit by hand</div><p class="gs-hint">You can change the caption or use your own picture once Outreach has finished writing. Press Refresh in a moment.</p></div>`;
    }
    const busy = edit && edit.busy;
    const text = edit && edit.caption != null ? edit.caption : String(p.caption || "");
    const over = text.length > MAX_EDIT;
    return (
      `<div class="gs-sp-section"><div class="gs-label">Edit by hand</div>` +
      `<label class="gs-label gs-label--sub" for="gsChEditCaption">Caption</label>` +
      `<textarea id="gsChEditCaption" class="gs-textarea gs-textarea--mid" data-field="ch-edit-caption" maxlength="${MAX_EDIT}" rows="5">${esc(text)}</textarea>` +
      `<div class="gs-ch-row"><button type="button" class="gs-btn gs-btn--sm" data-action="ch-post-caption" data-id="${esc(p.id)}"${captionChanged(p, edit) && !busy && !over ? "" : " disabled"}>${busy === "caption" ? "Saving…" : "Save my caption"}</button>` +
      `<span class="gs-hint">${text.length.toLocaleString("en-GB")} / ${MAX_EDIT.toLocaleString("en-GB")}</span></div>` +
      `<div class="gs-label gs-label--sub">Picture</div>` +
      pictureChooser(edit && edit.pictureData, edit && edit.pictureError, "ch-edit-picture-file", "ch-edit-picture-clear") +
      (edit && edit.pictureData ? `<div class="gs-ch-row"><button type="button" class="gs-btn gs-btn--primary gs-btn--sm" data-action="ch-post-picture" data-id="${esc(p.id)}"${busy ? " disabled" : ""}>${busy === "picture" ? "Sending…" : "Use this picture"}</button></div>` : "") +
      `<p class="gs-hint">A change you make by hand sends the post back for approval. You read it again, then approve.</p></div>`
    );
  }

  function rewriteBlock(p, edit) {
    return (
      `<div class="gs-sp-section"><div class="gs-label">Or ask the AI to improve it</div>` +
      `<div class="gs-ch-row"><select class="gs-select gs-select--sm" data-field="ch-rewrite-kind" aria-label="What to write again">${REWRITE.map(([v, l]) => `<option value="${v}"${((edit && edit.kind) || "text") === v ? " selected" : ""}>${esc(l)}</option>`).join("")}</select>` +
      `<input class="gs-input gs-input--sm" data-field="ch-rewrite-note" maxlength="300" value="${esc((edit && edit.note) || "")}" placeholder="e.g. Shorter and friendlier">` +
      `<button type="button" class="gs-btn gs-btn--sm" data-action="ch-post-rewrite" data-id="${esc(p.id)}"${edit && edit.busy ? " disabled" : ""}>${edit && edit.busy === "rewrite" ? "Asking…" : "Write again"}</button></div>` +
      `<p class="gs-hint">Outreach writes a new version and the post comes back for approval. It never publishes by itself.</p></div>`
    );
  }

  function approvalBlock(p, edit, status, final) {
    if (final) {
      return `<div class="gs-sp-section"><div class="gs-label">Approval</div><p class="gs-hint">This post is ${esc((label(POST_STATUS, status)[0] || status).toLowerCase())}. Nothing more can be done with it.</p></div>`;
    }
    const writing = status === "writing";
    const done = status === "approved" || status === "scheduled";
    const changed = timeChanged(p, edit);
    const when = whenText(p.publish_at);
    return (
      `<div class="gs-sp-section"><div class="gs-label">Approval</div>` +
      (done ? `<p class="gs-hint">Approved${when ? `. Outreach publishes it ${esc(when)}` : ""}. You can still cancel it before then.</p>` : "") +
      (writing ? `<p class="gs-hint">Approving is possible once Outreach has finished writing. Press Refresh in a moment.</p>` : "") +
      `<div class="gs-actions">` +
      (!done && !writing ? `<button type="button" class="gs-btn gs-btn--primary gs-btn--sm" data-action="ch-post-decide" data-id="${esc(p.id)}" data-decision="approve">Approve and publish</button>` : "") +
      (!done && !writing && changed ? `<button type="button" class="gs-btn gs-btn--primary gs-btn--sm" data-action="ch-post-decide" data-id="${esc(p.id)}" data-decision="approve" data-newtime="1">Approve for the new time</button>` : "") +
      (!done && !writing ? `<button type="button" class="gs-btn gs-btn--sm" data-action="ch-post-decide" data-id="${esc(p.id)}" data-decision="reject">Reject</button>` : "") +
      `<button type="button" class="gs-btn gs-btn--sm" data-action="ch-post-decide" data-id="${esc(p.id)}" data-decision="cancel">Cancel post</button></div>` +
      `<p class="gs-hint">Approving is the only thing that publishes a post, and you are asked to confirm first.</p></div>`
    );
  }

  function socialPreview(m) {
    const s = m.social || {};
    const p = selectedPost(s);
    if (!p) {
      return (
        `<div class="gs-card gs-sp-preview"><div class="gs-card__title">Preview</div>` +
        `<div class="gs-sp-image gs-sp-image--empty">Your post appears here with its picture, caption and schedule, so you can read it, change it and approve it.</div></div>`
      );
    }
    const status = String(p.status || "").toLowerCase();
    const final = FINAL_POST.indexOf(status) >= 0;
    const channels = (p.channels || []).map(chLabel).join(", ");
    const variants = (p.variants || []).filter((v) => v && v.text);
    const links = [httpsLink(p.preview_url, "Open Outreach's full preview"), httpsLink(p.published_url, "See the live post")].filter(Boolean).join(" · ");
    return (
      `<div class="gs-card gs-sp-preview" data-preview="${esc(p.id)}">` +
      `<div class="gs-card__title">Preview</div>` +
      `<div class="gs-ch-item__head">${badge(label(POST_STATUS, status))} <strong>${esc(channels || "No channel")}</strong>${p.company_name ? ` <span class="gs-hint">· as ${esc(p.company_name)}</span>` : ""}</div>` +
      pictureBlock(p, status) +
      (p.caption ? `<p class="gs-ch-caption">${esc(p.caption)}</p>` : `<p class="gs-hint">${status === "writing" ? "The caption appears here when Outreach has written it. Press Refresh." : "No caption yet."}</p>`) +
      (variants.length ? `<details class="gs-notes"><summary>How it reads on each channel</summary>${variants.map((v) => `<div class="gs-sp-variant"><strong>${esc(chLabel(v.channel))}</strong><p class="gs-ch-caption">${esc(v.text)}</p></div>`).join("")}</details>` : "") +
      (links ? `<p class="gs-hint">${links}</p>` : "") +
      (p.error ? `<p class="gs-hint gs-hint--warn">${esc(p.error)}</p>` : "") +
      scheduleBlock(p, s.edit, final, status) +
      (final ? "" : handBlock(p, s.edit, status) + rewriteBlock(p, s.edit)) +
      approvalBlock(p, s.edit, status, final) +
      ((s.edit && s.edit.error) ? `<p class="gs-hint gs-hint--warn" role="alert">${esc(s.edit.error)}</p>` : "") +
      `<p class="gs-hint">Your idea: ${esc(String(p.prompt || "").slice(0, 160))}${String(p.prompt || "").length > 160 ? "…" : ""}</p>` +
      `</div>`
    );
  }

  function socialPane(m) {
    return gate(m, "social") || (
      infoBar(m, "social") +
      `<div class="gs-sp-grid"><div class="gs-sp-left">${socialComposer(m)}${socialHistory(m)}</div><div class="gs-sp-right">${socialPreview(m)}</div></div>`
    );
  }

  // ── calls ─────────────────────────────────────────────────────────────────────────────────────
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

  function callForm(m) {
    const c = m.calls || {};
    const f = c.form || {};
    const scripts = c.scripts || [];
    const phones = m.info && m.info.wallet ? m.info.wallet.phone_numbers : null;
    return (
      `<div class="gs-card" id="gsChCalls"><div class="gs-card__title">New call</div>` +
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
      `<div class="gs-actions"><button type="button" class="gs-btn gs-btn--primary gs-btn--sm" data-action="ch-call-make"${callReady(f) ? "" : " disabled"}>${f.busy ? "Calling…" : "Call now"}</button></div>` +
      `<p class="gs-hint">Limited to ${esc((m.info && m.info.call_cap) || 20)} calls a day. The same number is not called twice in 24 hours unless nobody answered.</p>` +
      `</div>`
    );
  }

  function callHistory(m) {
    const c = m.calls || {};
    const calls = c.list || [];
    return (
      `<div class="gs-card"><div class="gs-card__title">Your calls</div>` +
      `<div class="gs-actions gs-actions--tight"><button type="button" class="gs-btn gs-btn--sm" data-action="ch-call-refresh"${c.refreshing ? " disabled" : ""}>${c.refreshing ? "Checking…" : "Refresh"}</button></div>` +
      (c.status === "loading" && !calls.length ? V.loading("Loading calls…") : "") +
      (calls.length ? `<ul class="gs-ch-list">${calls.map((x) => callRow(x, (c.details || {})[x.id])).join("")}</ul>` : c.status === "ok" ? `<p class="gs-hint">No calls yet. The outcome, answers and a transcript appear here after each call.</p>` : "") +
      `</div>`
    );
  }

  function callsPane(m) {
    return gate(m, "calls") || (
      infoBar(m, "calls") + `<div class="gs-ch-grid">${callForm(m)}${callHistory(m)}</div>`
    );
  }

  const api = {
    POST_STATUS, CALL_STATUS, STARTER_TEXT, postReady, callReady, publishIso, postBody, callBody, toLocalInput, whenText, timeChanged, selectedPost,
    howOf, pictureKindOf, fitSize, captionChanged, pictureChooser, handBlock,
    postListItem, companyField, socialComposer, socialHistory, socialPreview, socialPane, answersHtml, callRow, callForm, callHistory, callsPane, plural,
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
  const freshPostForm = () => ({ how: "ai", prompt: "", caption: "", polish: false, profileId: null, channels: [], publishAt: "", pictureKind: "ai", pictureNote: "",
    pictureData: null, pictureError: null, attempt: newAttempt(), busy: false, error: null });
  const freshCallForm = () => ({ buyer: "", number: "", goal: "", scriptId: "", instructions: "", questionsText: "", business: false, tps: false,
    attempt: newAttempt(), busy: false, error: null, lookupBusy: false, lookupNote: "" });
  const freshEdit = (post) => ({ time: post ? toLocalInput(post.publish_at) : "", kind: "text", note: "", caption: null, pictureData: null, pictureError: null, busy: false, error: null });

  const model = {
    status: "idle", info: null, error: null, opened: false, open: { social: false, calls: false },
    social: { status: "idle", posts: [], form: freshPostForm(), refreshing: false, selectedId: null, edit: freshEdit(null), pending: {}, applying: false,
      company: { status: "idle", profiles: [], brief: null, hints: [] } },
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
    const panes = [["gsPaneSocial", socialPane], ["gsPaneCalls", callsPane]];
    const active = document.activeElement;
    for (const [id, fn] of panes) {
      const pane = byId(id);
      if (!pane) continue;
      const mine = active && pane.contains(active);
      const field = mine ? active.getAttribute("data-field") : null;
      const start = field && active.selectionStart != null ? [active.selectionStart, active.selectionEnd] : null;
      pane.innerHTML = fn(model);
      if (field) {
        const again = pane.querySelector(`[data-field="${field}"]`);
        if (again && again.tagName !== "SELECT") {
          again.focus();
          if (start && again.setSelectionRange) try { again.setSelectionRange(start[0], start[1]); } catch (e) { /* not a text field */ }
        }
      }
    }
  }

  // Typing must not rebuild the form (it would lose the caret): only the buttons that depend on it are kept in step.
  function syncButtons() {
    const q = (sel) => document.querySelector(sel);
    const a = q('#gsPaneSocial [data-action="ch-post-create"]');
    const b = q('#gsPaneCalls [data-action="ch-call-make"]');
    const c = q('#gsPaneCalls [data-action="ch-lookup"]');
    const t = q('#gsPaneSocial [data-action="ch-post-time"]');
    const cap = q('#gsPaneSocial [data-action="ch-post-caption"]');
    const count = q("#gsChCount");
    if (count) count.textContent = String(model.social.form.caption.length);
    if (cap) {
      const e = model.social.edit;
      cap.disabled = !captionChanged(selectedPost(model.social), e) || !!e.busy || String(e.caption).length > MAX_EDIT;
    }
    if (a) a.disabled = !postReady(model.social.form);
    if (b) b.disabled = !callReady(model.calls.form);
    if (c) c.disabled = model.calls.form.lookupBusy || model.calls.form.buyer.trim().length < 3;
    if (t) t.disabled = !timeChanged(selectedPost(model.social), model.social.edit) || !!model.social.edit.busy;
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
      await Promise.all([loadPosts(false), loadCalls(false), loadScripts(), loadCompany(null)]);
    }
  }

  // The user's companies from TenderFlow. With no id the answer picks the one Growth Studio uses (else the default).
  async function loadCompany(id) {
    const co = model.social.company;
    try {
      const res = await api2("GET", "/api/growth/social/company" + (id ? `?profile_id=${encodeURIComponent(id)}` : ""));
      co.profiles = res.profiles || [];
      co.brief = res.brief || null;
      co.hints = res.hints || [];
      co.status = "ok";
      if (id === null && !model.social.form.companyTouched) model.social.form.profileId = res.profile_id || null;
    } catch (e) {
      co.status = "ok";
      co.hints = [e.message];
    }
    render();
  }

  function select(id) {
    model.social.selectedId = id;
    model.social.edit = freshEdit(selectedPost(model.social));
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
      const was = s.edit && s.edit.time;
      if (!selectedPost(s) || String(selectedPost(s).id) !== String(s.selectedId)) select(s.posts[0] ? s.posts[0].id : null);
      else if (!was) s.edit = Object.assign(s.edit, { time: toLocalInput(selectedPost(s).publish_at) });
      if (res.stopped) toast(res.stopped, true);
    } catch (e) {
      s.status = "ok";
      toast(e.message, true);
    }
    s.refreshing = false;
    render();
    await applyPending();
    schedulePoll();
  }

  // The user's own caption and picture are set on a new post by hand, but only once Outreach has finished writing it
  // (an edit made while the AI is writing would be overwritten). Done here, each time the posts are looked at.
  async function applyPending() {
    const s = model.social;
    if (s.applying) return;
    s.applying = true;
    try {
      for (const id of Object.keys(s.pending)) {
        const post = s.posts.find((p) => String(p.id) === id);
        if (!post) { delete s.pending[id]; continue; }
        const st = String(post.status || "").toLowerCase();
        if (st === "writing") continue;
        const job = s.pending[id];
        delete s.pending[id];
        if (FINAL_POST.indexOf(st) >= 0) continue;
        const body = {};
        if (job.caption) body.caption = job.caption;
        if (job.pictureData) body.picture_data = job.pictureData;
        if (!Object.keys(body).length) continue;
        const ok = await changePost(id, body, "hand", "Your own " + (body.caption && body.picture_data ? "caption and picture are" : body.caption ? "caption is" : "picture is") + " on the post. Read the preview, then approve it.");
        if (!ok && String(s.selectedId) === id) {
          // keep what the user wrote, so it can be sent again with the buttons on the preview
          s.edit.caption = job.caption || s.edit.caption;
          s.edit.pictureData = job.pictureData || s.edit.pictureData;
          render();
        }
      }
    } finally {
      s.applying = false;
    }
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

  // While a post is being written or a call is in progress, look again every few seconds (a tab must be open).
  function schedulePoll() {
    clearTimeout(pollTimer);
    if (!model.open.social && !model.open.calls) return;
    const writing = model.open.social && model.social.posts.some((p) => String(p.status).toLowerCase() === "writing");
    const live = model.open.calls && model.calls.list.some((c) => LIVE_CALL.indexOf(String(c.status).toLowerCase()) >= 0);
    if (!writing && !live) return;
    pollTimer = setTimeout(async () => {
      if (writing && model.open.social) await loadPosts(true);
      if (live && model.open.calls) await loadCalls(true);
    }, 7000);
  }

  function replacePost(post) {
    const s = model.social;
    const i = s.posts.findIndex((p) => String(p.id) === String(post.id));
    if (i >= 0) s.posts[i] = post;
    else s.posts.unshift(post);
    if (String(s.selectedId) === String(post.id)) s.edit = Object.assign(freshEdit(post), { kind: s.edit.kind, note: s.edit.note });
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
      select(res.post.id);
      // what is set by hand once Outreach has written the post: the caption as written (unless the AI polishes it) and the user's own picture
      const job = { caption: howOf(f) === "mine" && !f.polish ? String(f.caption || "").trim() : "", pictureData: pictureKindOf(f) === "mine" ? f.pictureData : null };
      if (job.caption || job.pictureData) model.social.pending[res.post.id] = job;
      model.social.form = Object.assign(freshPostForm(), { channels: f.channels.slice(), profileId: f.profileId, companyTouched: f.companyTouched, how: f.how });
      toast(job.caption || job.pictureData ? "Outreach is opening the post. Your own " + (job.caption ? "caption" : "picture") + " is added as soon as it is ready; then read it and approve it."
        : "Outreach is writing the post. The preview appears on the right; read it, then approve it.");
    } catch (e) {
      f.busy = false;
      f.error = e.message;
      f.attempt = newAttempt(); // a new try is a new request, not a replay of this refusal
    }
    render();
    await applyPending();
    schedulePoll();
  }

  async function decidePost(id, decision, withNewTime) {
    const post = model.social.posts.find((p) => String(p.id) === String(id));
    if (!post) return;
    const edit = model.social.edit;
    const newIso = withNewTime ? publishIso(edit.time) : undefined;
    const whenLabel = newIso ? whenText(newIso) : whenText(post.publish_at);
    const channels = (post.channels || []).map(chLabel).join(" and ") || "your channels";
    if (decision === "approve" && !(await confirmIt(`Publish this post to ${channels}${whenLabel ? " on " + whenLabel : ""}? Once approved it goes live.`))) return;
    if (decision === "cancel" && !(await confirmIt("Cancel this post? It will not be published."))) return;
    edit.error = null;
    try {
      const res = await api2("POST", `/api/growth/social/posts/${encodeURIComponent(id)}/decision`, {
        decision, confirm: decision === "approve" ? true : undefined, publish_at: newIso, attempt: newAttempt() });
      replacePost(res.post);
      toast(decision === "approve" ? "Approved. Outreach will publish it." : decision === "reject" ? "Post rejected." : "Post cancelled.");
    } catch (e) {
      edit.error = e.message;
      toast(e.message, true);
      loadPosts(true);
    }
    render();
  }

  async function changePost(id, body, busyKind, doneMessage) {
    const edit = model.social.edit;
    edit.busy = busyKind;
    edit.error = null;
    render();
    try {
      const res = await api2("PATCH", `/api/growth/social/posts/${encodeURIComponent(id)}`, Object.assign({ attempt: newAttempt() }, body));
      replacePost(res.post);
      model.social.edit.busy = false;
      toast(doneMessage);
      render();
      schedulePoll();
      return true;
    } catch (e) {
      model.social.edit.busy = false;
      model.social.edit.error = e.message;
      toast(e.message, true);
    }
    render();
    schedulePoll();
    return false;
  }

  // ── a picture of the user's own ───────────────────────────────────────────────────────────────
  const readDataUrl = (file) => new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result || ""));
    r.onerror = () => reject(new Error("That picture could not be read. Try another file."));
    r.readAsDataURL(file);
  });
  const loadImage = (src) => new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("That picture could not be opened. Try another file."));
    img.src = src;
  });
  // The chosen file as a data URL. A big one is scaled down (and saved as JPEG) so it travels quickly and is never refused for size.
  async function pictureFromFile(file) {
    if (!/^image\/(png|jpe?g|webp)$/i.test(file.type)) throw new Error("Use a PNG, JPG or WebP picture.");
    if (file.size > MAX_PICTURE_MB * 1024 * 1024) throw new Error(`That picture is over ${MAX_PICTURE_MB} MB. Pick a smaller one.`);
    const url = await readDataUrl(file);
    const img = await loadImage(url);
    const [w, h] = fitSize(img.naturalWidth, img.naturalHeight, PICTURE_EDGE);
    if (file.size <= PICTURE_KEEP && w === img.naturalWidth && h === img.naturalHeight) return url;
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#fff"; // JPEG has no transparency
    ctx.fillRect(0, 0, w, h);
    ctx.drawImage(img, 0, 0, w, h);
    return canvas.toDataURL("image/jpeg", 0.85);
  }
  async function choosePicture(input, target, dataKey, errorKey) {
    const file = input.files && input.files[0];
    input.value = "";
    if (!file) return;
    target[errorKey] = null;
    try {
      target[dataKey] = await pictureFromFile(file);
    } catch (e) {
      target[errorKey] = e.message;
    }
    render();
    syncButtons();
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

  function inChannelPane(node) {
    return !!node && !!node.closest && !!node.closest("#gsPaneSocial, #gsPaneCalls");
  }

  function onClick(event) {
    const a = event.target.closest("[data-action]");
    if (!a || !inChannelPane(a)) return;
    const act = a.getAttribute("data-action");
    const run = (p) => p.catch((e) => toast((e && e.message) || "Something went wrong.", true));
    const edit = model.social.edit;
    if (act === "ch-post-create") run(createPost());
    else if (act === "ch-post-refresh") run(loadPosts(true));
    else if (act === "ch-post-select") { select(a.dataset.id); render(); }
    else if (act === "ch-post-decide") run(decidePost(a.dataset.id, a.dataset.decision, a.dataset.newtime === "1"));
    else if (act === "ch-post-time") run(changePost(a.dataset.id, { publish_at: publishIso(edit.time) }, "time", "Time changed. Read the preview and approve the post again."));
    else if (act === "ch-post-rewrite") run(changePost(a.dataset.id, { regenerate: edit.kind || "text", instruction: edit.note || undefined }, "rewrite", "Outreach is writing it again. Read the new version, then approve it."));
    else if (act === "ch-post-caption") run(changePost(a.dataset.id, { caption: String(edit.caption || "").trim() }, "caption", "Your caption is saved. Read the preview, then approve the post."));
    else if (act === "ch-post-picture") run(changePost(a.dataset.id, { picture_data: edit.pictureData }, "picture", "Your picture is on the post. Read the preview, then approve it."));
    else if (act === "ch-picture-clear") { model.social.form.pictureData = null; model.social.form.pictureError = null; render(); }
    else if (act === "ch-edit-picture-clear") { edit.pictureData = null; edit.pictureError = null; render(); }
    else if (act === "ch-call-make") run(makeCall());
    else if (act === "ch-call-refresh") run(loadCalls(true));
    else if (act === "ch-call-end") run(endCall(a.dataset.id));
    else if (act === "ch-call-details") run(callDetails(a.dataset.id));
    else if (act === "ch-lookup") run(lookupNumber());
    else if (act === "ch-open-profiles") {
      // Company profiles is its own screen: close Growth Studio so it is not left underneath
      if (typeof root.closeGrowthStudio === "function") root.closeGrowthStudio();
      const btn = byId("btnDropdownCompanyProfiles");
      if (btn) btn.click();
    }
    else if (act === "outreach-check") run(loadStatus());
  }

  function onInput(event) {
    const t = event.target;
    const field = t.getAttribute && t.getAttribute("data-field");
    if (!field || !field.startsWith("ch-")) return;
    const s = model.social.form;
    const c = model.calls.form;
    const e = model.social.edit;
    if (field === "ch-prompt") s.prompt = t.value;
    else if (field === "ch-caption") s.caption = t.value;
    else if (field === "ch-edit-caption") e.caption = t.value;
    else if (field === "ch-publish") s.publishAt = t.value;
    else if (field === "ch-picture-note") s.pictureNote = t.value;
    else if (field === "ch-edit-time") e.time = t.value;
    else if (field === "ch-rewrite-note") e.note = t.value;
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
    const e = model.social.edit;
    if (field === "ch-starter") {
      if (t.value && STARTER_TEXT[t.value]) {
        const co = model.social.company.brief;
        s.prompt = STARTER_TEXT[t.value].replace("[company]", s.profileId && co ? co.name : "our company");
      }
      render();
      return;
    }
    if (field === "ch-company") {
      s.companyTouched = true;
      s.profileId = t.value ? Number(t.value) : null;
      model.social.company.brief = null;
      render();
      if (s.profileId) loadCompany(s.profileId);
      return;
    }
    if (field === "ch-how") { s.how = t.value === "mine" ? "mine" : "ai"; render(); return; }
    if (field === "ch-polish") { s.polish = t.checked; render(); return; }
    if (field === "ch-picture-kind") { s.pictureKind = ["ai", "mine", "none"].indexOf(t.value) >= 0 ? t.value : "ai"; s.pictureError = null; render(); return; }
    if (field === "ch-picture-file") { choosePicture(t, s, "pictureData", "pictureError"); return; }
    if (field === "ch-edit-picture-file") { choosePicture(t, e, "pictureData", "pictureError"); return; }
    if (field === "ch-edit-time") { e.time = t.value; render(); return; } // the "Approve for the new time" button depends on it
    if (field === "ch-rewrite-kind") { e.kind = t.value; return; }
    if (field === "ch-channel") {
      s.channels = t.checked ? s.channels.concat(t.value).filter((v, i, a) => a.indexOf(v) === i) : s.channels.filter((v) => v !== t.value);
    } else if (field === "ch-script") c.scriptId = t.value;
    else if (field === "ch-business") c.business = t.checked;
    else if (field === "ch-tps") c.tps = t.checked;
    else return;
    syncButtons();
  }

  // Called by growth-studio.js when a tab is shown ("social" or "calls") and when any other tab is shown.
  api.open = function (which) {
    model.open.social = which === "social";
    model.open.calls = which === "calls";
    if (!model.opened) {
      model.opened = true;
      for (const id of ["gsPaneSocial", "gsPaneCalls"]) {
        const pane = byId(id);
        if (!pane) continue;
        pane.addEventListener("click", onClick);
        pane.addEventListener("input", onInput);
        pane.addEventListener("change", onChange);
      }
      loadStatus();
    } else {
      render();
      if (which === "social") loadPosts(false);
      else loadCalls(false);
    }
  };
  api.close = function () {
    model.open.social = false;
    model.open.calls = false;
    clearTimeout(pollTimer);
  };
})(typeof window !== "undefined" ? window : globalThis);
