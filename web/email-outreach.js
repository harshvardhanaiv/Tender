/* Email (Outreach): the settings panel that sends email through Outreach by Aivhub.
   View functions are pure (no DOM, no fetch) so tests/email_outreach_view_test.js can run them in Node; the small
   controller at the bottom wires them to the page. The Outreach key is on the server only: this file only ever
   sees the masked hint the server sends ("sk_test_…"). Exposes window.EmailOutreach (and module.exports for tests). */
(function (root) {
  "use strict";

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

  // "email, first name, company" per line (a comma or a tab between them); a bare address is fine.
  function parseRecipients(text) {
    const out = [];
    String(text || "").split(/\r?\n/).forEach((line) => {
      const parts = line.split(/[,\t;]/).map((p) => p.trim());
      if (!parts[0]) return;
      const rec = { email: parts[0] };
      if (parts[1]) rec.first_name = parts[1];
      if (parts.length > 2 && parts.slice(2).join(", ").trim()) rec.company = parts.slice(2).filter(Boolean).join(", ");
      out.push(rec);
    });
    return out;
  }

  function statusHtml(info) {
    if (!info) return `<p class="eo-hint">Checking Outreach…</p>`;
    if (!info.can_send) return `<p class="eo-hint">${esc(info.problem || "You do not have permission to send email through Outreach.")}</p>`;
    const hints = (info.setup_hints || []).map((h) => `<li>${esc(h)}</li>`).join("");
    if (!info.ready) {
      return `<p class="eo-hint eo-hint--warn">${esc(info.problem || "Outreach is not available.")}</p>` +
        (info.hint ? `<p class="eo-hint">${esc(info.hint)}</p>` : "") +
        (hints ? `<ul class="eo-list">${hints}</ul>` : "");
    }
    const u = info.usage || {};
    const mode = info.mode === "live"
      ? `<strong>Live mode</strong>: emails really go out.`
      : `<strong>Test mode</strong>: Outreach pretends everything and sends nothing real.`;
    const w = info.wallet || {};
    const credits = w.credits != null ? ` · ${esc(w.credits)} credits` : "";
    const boxes = w.connected && typeof w.connected === "object" && !Array.isArray(w.connected) && w.connected.mailboxes != null
      ? ` · ${esc(w.connected.mailboxes)} mailbox${Number(w.connected.mailboxes) === 1 ? "" : "es"} connected` : "";
    const today = u.daily_cap != null ? ` · ${esc(u.sent_today)} of ${esc(u.daily_cap)} single sends used today` : "";
    return `<p class="eo-hint" id="eoReady">Connected. ${mode} Key ${esc(info.key_hint || "")}${credits}${boxes}${today}.</p>` +
      (hints ? `<details class="eo-details"><summary>Before the first send</summary><ul class="eo-list">${hints}</ul></details>` : "");
  }

  function reasonList(items, key = "email") {
    return `<ul class="eo-list">${items.map((i) => `<li>${esc(i[key] || "(no address)")}: ${esc(i.reason)}</li>`).join("")}</ul>`;
  }

  // What happened to a send, in the user's words. A campaign is never described as "sent".
  function resultHtml(r) {
    if (!r) return "";
    const parts = [];
    const sent = r.sent || [];
    if (sent.length) parts.push(`<p class="eo-ok">${esc(plural(sent.length, "email"))} sent now.</p>`);
    (r.campaigns || []).forEach((c) => {
      parts.push(`<p class="eo-ok">${esc(plural(c.recipients, "recipient"))} approved in one campaign${c.resumed ? " (the earlier campaign was approved)" : ""}. ` +
        `Outreach sends it inside its sending window (09:00 to 17:00 UK by default) and after mailbox warm-up, so it is not instant.</p>`);
    });
    const limited = r.daily_limited || [];
    if (limited.length) {
      parts.push(`<p class="eo-warn">${esc(plural(limited.length, "email"))} not sent: daily limit reached. Nothing was queued; send them tomorrow.</p>` +
        `<ul class="eo-list">${limited.map((i) => `<li>${esc(i.email)}</li>`).join("")}</ul>`);
    }
    const failed = r.failed || [];
    if (failed.length) {
      parts.push(`<p class="eo-warn">Not sent (${esc(failed.length)}):</p><ul class="eo-list">${failed.map((f) =>
        `<li>${esc(f.email)} (${esc(f.step || "send")}): ${esc(f.reason)}</li>`).join("")}</ul>`);
    }
    if (r.stopped) parts.push(`<p class="eo-warn">Stopped: ${esc(r.stopped)}</p>`);
    if (r.hint) parts.push(`<p class="eo-hint">${esc(r.hint)}</p>`);
    const deferred = r.deferred || [];
    if (deferred.length) parts.push(`<p class="eo-warn">${esc(plural(deferred.length, "recipient"))} not attempted, so they can be sent again:</p>${reasonList(deferred)}`);
    const skipped = r.skipped || [];
    if (skipped.length) parts.push(`<p class="eo-hint">Skipped (${esc(skipped.length)}):</p>${reasonList(skipped)}`);
    return parts.join("") || `<p class="eo-hint">Nothing was sent.</p>`;
  }

  function previewHtml(p) {
    if (!p) return "";
    const lines = [];
    if ((p.single || []).length) lines.push(`<li>${esc(plural(p.single.length, "email"))} sent now, one at a time.</li>`);
    (p.campaigns || []).forEach((c) => lines.push(`<li>One campaign for ${esc(plural(c.recipients.length, "recipient"))}, approved so it sends in the sending window.</li>`));
    (p.resume || []).forEach((c) => lines.push(`<li>An earlier campaign for ${esc(plural(c.recipients.length, "recipient"))} was made but never approved; it will be approved.</li>`));
    if ((p.daily_limited || []).length) lines.push(`<li>${esc(plural(p.daily_limited.length, "email"))} will not be sent: daily limit reached.</li>`);
    if (!lines.length) lines.push(`<li>Nothing would be sent.</li>`);
    return `<p class="eo-hint">If you press Send:</p><ul class="eo-list">${lines.join("")}</ul>` +
      ((p.skipped || []).length ? `<p class="eo-hint">Skipped (${esc(p.skipped.length)}):</p>${reasonList(p.skipped)}` : "");
  }

  function historyHtml(sends) {
    if (!sends || !sends.length) return `<p class="eo-hint">Nothing sent yet.</p>`;
    const rows = sends.map((s) =>
      `<tr><td>${esc(s.email)}</td><td>${s.mode === "campaign" ? "Campaign" : "Single"}</td>` +
      `<td>${esc(s.label)}${s.error ? ` <span class="eo-hint">(${esc(s.error)})</span>` : ""}</td>` +
      `<td>${esc((s.sent_at || s.created_at || "").replace("T", " ").slice(0, 16))}</td></tr>`).join("");
    return `<table class="eo-table"><thead><tr><th>To</th><th>How</th><th>Status</th><th>When</th></tr></thead><tbody>${rows}</tbody></table>`;
  }

  const api = { esc, parseRecipients, statusHtml, resultHtml, previewHtml, historyHtml };
  root.EmailOutreach = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;

  // ── controller (browser only) ────────────────────────────────────────────────────────────────
  if (typeof document === "undefined") return;

  async function request(method, url, body) {
    const init = { method, credentials: "same-origin", headers: {} };
    if (body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(body); }
    let res;
    try { res = await fetch(url, init); } catch (err) { throw new Error("Could not reach the server. Check your connection and try again."); }
    let data = null;
    try { data = await res.json(); } catch (err) { /* not JSON */ }
    if (!res.ok) {
      const e = new Error(res.status === 429 ? "You are going too fast. Wait a moment and try again." : (data && data.error) || `Something went wrong (${res.status}).`);
      e.data = data;
      throw e;
    }
    return data;
  }

  function init() {
    const section = document.getElementById("emailOutreachSection");
    if (!section) return;
    const $ = (id) => document.getElementById(id);
    const busy = (b) => ["eoPreview", "eoSend", "eoRefresh"].forEach((id) => { const el = $(id); if (el) el.disabled = b; });
    const readForm = () => ({ subject: $("eoSubject").value, body: $("eoBody").value, recipients: parseRecipients($("eoRecipients").value) });

    async function loadHistory() {
      try { $("eoHistory").innerHTML = historyHtml((await request("GET", "/api/email/outreach/sends")).sends); } catch (err) { /* keep what is shown */ }
    }
    async function loadStatus() {
      try {
        const info = await request("GET", "/api/email/outreach/status");
        $("eoStatus").innerHTML = statusHtml(info);
        section.style.display = info.can_send ? "" : "none";
        if (info.can_send) loadHistory();
      } catch (err) { section.style.display = "none"; }
    }
    async function act(kind) {
      const out = $("eoResult");
      const form = readForm();
      if (kind !== "refresh" && !form.recipients.length) { out.innerHTML = `<p class="eo-warn">Add at least one recipient.</p>`; return; }
      busy(true);
      out.innerHTML = `<p class="eo-hint">${kind === "refresh" ? "Checking Outreach…" : "Working…"}</p>`;
      try {
        if (kind === "refresh") {
          const r = await request("POST", "/api/email/outreach/refresh", {});
          out.innerHTML = r.stopped ? `<p class="eo-warn">${esc(r.stopped)}</p>` : `<p class="eo-ok">Checked ${esc(r.checked)}, updated ${esc(r.updated)}.</p>`;
          $("eoHistory").innerHTML = historyHtml(r.sends);
        } else if (kind === "preview") {
          out.innerHTML = previewHtml(await request("POST", "/api/email/outreach/send", Object.assign(form, { dry_run: true })));
        } else {
          const r = await request("POST", "/api/email/outreach/send", form);
          out.innerHTML = resultHtml(r);
          loadHistory();
          loadStatus();
        }
      } catch (err) {
        const skipped = err.data && err.data.skipped;
        out.innerHTML = `<p class="eo-warn">${esc(err.message)}</p>` + (skipped && skipped.length ? reasonList(skipped) : "");
      } finally { busy(false); }
    }
    $("eoPreview").addEventListener("click", () => act("preview"));
    $("eoSend").addEventListener("click", () => act("send"));
    $("eoRefresh").addEventListener("click", () => act("refresh"));
    loadStatus();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})(typeof window !== "undefined" ? window : globalThis);
