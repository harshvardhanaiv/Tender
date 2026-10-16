/* Growth Studio "Inbox": the replies to emails sent through Outreach, read in TenderFlow. Pure view functions first (no
   DOM, no fetch; tests/growth_inbox_test.js runs them in Node), then a small controller that mounts them into
   #gsPaneInbox. Every value from the server goes through esc(); email text is shown as plain text, never as markup.
   Nothing is sent from here: "Reply by email" opens the user's own mail program. */
(function (root) {
  "use strict";

  const V = typeof module !== "undefined" && module.exports ? require("./growth-view.js") : root.GrowthView;
  if (!V) return;
  const { esc } = V;

  const FILTERS = [["replies", "Replies"], ["sent", "Sent"], ["bounced", "Bounced"], ["all", "All"]];
  const STATUS = { replied: ["Replied", "good"], sent: ["Sent", "muted"], bounced: ["Bounced", "bad"], failed: ["Failed", "bad"] };
  const EMPTY = {
    replies: ["No replies yet", "Replies to the emails you send through Outreach appear here. A reply also marks that buyer as replied in your campaign."],
    sent: ["Nothing sent yet", "Emails you send through Outreach from Growth Studio appear here."],
    bounced: ["No bounced emails", "An address that could not be delivered to would be listed here."],
    all: ["Nothing here yet", "Emails you send through Outreach appear here, with what became of each."],
  };

  const stamp = (iso) => {
    const d = V.fmtDate(iso);
    return d ? esc(d) : "";
  };

  // A mail link only for something that is plainly one address (nothing that could add a header or a second recipient).
  function mailtoHref(address, subject) {
    const a = String(address || "").trim();
    if (!/^[^\s@<>"?&#%,;]+@[^\s@<>"?&#%,;]+\.[^\s@<>"?&#%,;]+$/.test(a) || a.length > 254) return null;
    const s = String(subject || "").trim();
    return "mailto:" + a + (s ? "?subject=" + encodeURIComponent(/^re:/i.test(s) ? s : "Re: " + s) : "");
  }

  function filterBar(m) {
    const pills = FILTERS.map(([v, l]) => `<button type="button" class="gs-btn gs-btn--sm${m.filter === v ? " gs-btn--primary" : ""}" data-action="in-filter" data-filter="${v}" aria-pressed="${m.filter === v}">${esc(l)}</button>`).join("");
    return (
      `<div class="gs-toolbar gs-in-toolbar"><div class="gs-actions gs-in-pills">${pills}</div>` +
      `<label class="gs-inline">Search <input type="search" class="gs-input gs-input--sm" data-field="in-q" maxlength="100" value="${esc(m.q || "")}" placeholder="address or subject"></label>` +
      `<button type="button" class="gs-btn gs-btn--sm" data-action="in-search">Search</button>` +
      `<button type="button" class="gs-btn gs-btn--sm" data-action="in-refresh"${m.loading ? " disabled" : ""}>${m.loading ? "Checking…" : "Refresh"}</button></div>`
    );
  }

  function detailHtml(d) {
    if (!d) return `<div class="gs-in-detail">${V.loading("Opening…")}</div>`;
    if (d.error) return `<div class="gs-in-detail">${V.note(esc(d.error), "bad")}</div>`;
    const msg = d.message || {};
    const replies = (msg.replies || []).map((r) =>
      `<div class="gs-in-reply"><div class="gs-hint">${r.from ? `<strong>${esc(r.from)}</strong>` : "Reply"}${r.at ? " · " + stamp(r.at) : ""}${r.subject ? " · " + esc(r.subject) : ""}</div>` +
      (r.text ? `<p class="gs-ch-caption">${esc(r.text)}</p>` : `<p class="gs-hint">The reply has no text.</p>`) + `</div>`).join("");
    const href = mailtoHref(d.reply_to, msg.subject);
    const canMark = d.target_id && d.campaign && d.campaign.id;
    const status = String(d.target_status || "");
    return (
      `<div class="gs-in-detail">` +
      (d.opt_out_hint ? V.note(`This reply looks like a request to stop. Add ${esc(d.buyer || "this buyer")} to your do-not-contact list so they are not emailed again.`, "warn") : "") +
      (replies || (msg.status === "replied" ? `<p class="gs-hint">Outreach has marked this as replied but has not given the reply text. Open it in Outreach to read it.</p>` : "")) +
      (msg.bounce_reason ? V.note(`Bounced: ${esc(msg.bounce_reason)}`, "bad") : "") +
      (!replies && msg.status !== "replied" && !msg.bounce_reason ? `<p class="gs-hint">No reply yet.</p>` : "") +
      (msg.original ? `<details class="gs-notes"><summary>The email you sent</summary><p class="gs-ch-caption">${esc(msg.original)}</p></details>` : "") +
      `<div class="gs-actions">` +
      (href ? `<a class="gs-btn gs-btn--primary gs-btn--sm" href="${esc(href)}">Reply by email</a>` : "") +
      (canMark && (status === "sent" || status === "replied") ? `<button type="button" class="gs-btn gs-btn--sm" data-action="in-meeting" data-target="${esc(d.target_id)}" data-campaign="${esc(d.campaign.id)}">Meeting booked</button>` : "") +
      (canMark && status !== "opted_out" ? `<button type="button" class="gs-btn gs-btn--sm" data-action="in-optout" data-target="${esc(d.target_id)}" data-campaign="${esc(d.campaign.id)}">Add to do-not-contact</button>` : "") +
      (status === "opted_out" ? V.badge("On your do-not-contact list", "muted") : "") +
      (msg.id ? `<button type="button" class="gs-btn gs-btn--sm gs-btn--danger" data-action="in-delete" data-id="${esc(msg.id)}">Delete</button>` : "") +
      `</div></div>`
    );
  }

  function rowHtml(m, open) {
    const st = STATUS[m.status] || [String(m.status || "unknown").replace(/_/g, " "), "muted"];
    const who = m.buyer || m.to || "Unknown";
    const when = stamp(m.replied_at || m.sent_at);
    return (
      `<li class="gs-in-item${m.unread ? " is-unread" : ""}" data-message="${esc(m.id)}">` +
      `<div class="gs-in-line">` +
      `<button type="button" class="gs-in-row" data-action="in-open" data-id="${esc(m.id)}" aria-expanded="${open ? "true" : "false"}">` +
      `<span class="gs-in-dot" aria-hidden="true"></span><span class="gs-in-who"><strong>${esc(who)}</strong>${m.buyer && m.to ? ` <span class="gs-hint">${esc(m.to)}</span>` : ""}</span>` +
      `<span class="gs-in-subject">${esc(m.subject || "(no subject)")}${m.campaign_name ? ` <span class="gs-hint">· ${esc(m.campaign_name)}</span>` : ""}</span>` +
      `<span class="gs-in-meta">${V.badge(esc(st[0]), st[1])}${m.unread ? " " + V.badge("New", "accent") : ""} <span class="gs-hint">${when}</span></span></button>` +
      `<button type="button" class="gs-in-del" data-action="in-delete" data-id="${esc(m.id)}" aria-label="Delete this email from your inbox" title="Delete">Delete</button></div>` +
      (open ? detailHtml(open === true ? null : open) : "") +
      `</li>`
    );
  }

  function inboxPane(m) {
    if (!m || m.status === "idle" || (m.status === "loading" && !m.info)) return V.loading("Checking Outreach…");
    const info = m.info || {};
    if (!info.ready) return V.note(esc(info.problem || m.error || "Outreach is not available."), "bad") + V.outreachConnectBox(info);
    const items = m.items || [];
    let list;
    if (m.loading && !items.length) list = V.loading("Reading your emails from Outreach…");
    else if (m.error && !items.length) list = V.errorBox(m.error, "in-refresh");
    else if (!items.length) list = V.empty(EMPTY[m.filter][0], esc(EMPTY[m.filter][1]));
    else list = `<ul class="gs-in-list">${items.map((x) => rowHtml(x, m.openId === x.id ? (m.details || {})[x.id] || true : false)).join("")}</ul>`;
    return (
      filterBar(m) +
      (m.marked && m.marked.length ? V.note(`${esc(m.marked.join(", "))} replied. ${m.marked.length === 1 ? "That buyer is" : "Those buyers are"} now marked as replied in ${m.marked.length === 1 ? "its" : "their"} campaign.`, "info") : "") +
      list +
      (m.error && items.length ? `<p class="gs-hint gs-hint--warn">${esc(m.error)}</p>` : "") +
      (m.nextCursor ? `<div class="gs-actions"><button type="button" class="gs-btn gs-btn--sm" data-action="in-more"${m.loadingMore ? " disabled" : ""}>${m.loadingMore ? "Loading…" : "Look further back"}</button></div>` : "") +
      `<p class="gs-hint">Shows the emails you sent through Outreach and the replies to them. Outreach holds the full conversation; answer from your own mail program with Reply by email. Delete removes an email from this list only: Outreach keeps its own record and the email stays sent.</p>`
    );
  }

  const api = { FILTERS, mailtoHref, filterBar, detailHtml, rowHtml, inboxPane };
  root.GrowthInbox = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;

  // ── controller (browser only) ─────────────────────────────────────────────────────────────────
  if (typeof document === "undefined") return;

  const byId = (id) => document.getElementById(id);
  const model = { status: "idle", info: null, error: null, filter: "replies", q: "", items: [], nextCursor: null, loading: false, loadingMore: false,
    openId: null, details: {}, marked: [], opened: false, open: false };

  async function fetchJson(method, url, body) {
    let res;
    try {
      res = await fetch(url, { method, credentials: "same-origin", headers: body !== undefined ? { "Content-Type": "application/json" } : {}, body: body !== undefined ? JSON.stringify(body) : undefined });
    } catch (e) {
      throw new Error("Could not reach the server. Check your connection and try again.");
    }
    let data = null;
    try { data = await res.json(); } catch (e) { /* not JSON */ }
    if (!res.ok) throw new Error(res.status === 429 && !(data && data.error) ? "You are going too fast. Wait a moment and try again." : (data && data.error) || `Something went wrong (${res.status}).`);
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
  const confirmIt = async (message) => (typeof root.showConfirm === "function" ? root.showConfirm(message) : root.confirm(message));

  function updateTabCount() {
    const el = byId("gsCountInbox");
    if (!el) return;
    const n = model.items.filter((x) => x.unread).length;
    el.textContent = n ? String(n) : "";
  }

  function render() {
    const pane = byId("gsPaneInbox");
    if (!pane) return;
    const active = document.activeElement;
    const field = active && pane.contains(active) ? active.getAttribute("data-field") : null;
    const caret = field && active.selectionStart != null ? [active.selectionStart, active.selectionEnd] : null;
    pane.innerHTML = inboxPane(model);
    if (field) {
      const again = pane.querySelector(`[data-field="${field}"]`);
      if (again) {
        again.focus();
        if (caret && again.setSelectionRange) try { again.setSelectionRange(caret[0], caret[1]); } catch (e) { /* not a text field */ }
      }
    }
    updateTabCount();
  }

  async function loadStatus() {
    model.status = "loading";
    render();
    try {
      model.info = await fetchJson("GET", "/api/growth/outreach/status");
      model.status = "ok";
    } catch (e) {
      model.status = "error";
      model.error = e.message;
      model.info = { ready: false, problem: e.message };
    }
    render();
    if (model.info && model.info.ready) await load(false);
  }

  async function load(more) {
    if (model.loading || model.loadingMore) return;
    if (more) model.loadingMore = true; else { model.loading = true; model.nextCursor = null; }
    model.error = null;
    render();
    try {
      const qs = `?filter=${encodeURIComponent(model.filter)}` + (model.q ? `&q=${encodeURIComponent(model.q)}` : "") + (more && model.nextCursor ? `&cursor=${encodeURIComponent(model.nextCursor)}` : "");
      const res = await fetchJson("GET", "/api/growth/inbox" + qs);
      model.items = more ? model.items.concat(res.items) : res.items;
      model.nextCursor = res.next_cursor || null;
      model.marked = res.marked || [];
    } catch (e) {
      model.error = e.message;
    }
    model.loading = false;
    model.loadingMore = false;
    render();
  }

  async function openMessage(id) {
    if (model.openId === id) { model.openId = null; render(); return; }
    model.openId = id;
    render();
    if (!model.details[id]) {
      try {
        model.details[id] = await fetchJson("GET", `/api/growth/inbox/${encodeURIComponent(id)}`);
        const row = model.items.find((x) => x.id === id);
        if (row) row.unread = false;
        if (model.details[id].target_status && row) row.buyer = row.buyer || model.details[id].buyer;
      } catch (e) {
        model.details[id] = { error: e.message };
      }
    }
    render();
  }

  async function setBuyerStatus(campaign, target, status, ask) {
    if (ask && !(await confirmIt(ask))) return;
    try {
      await fetchJson("PATCH", `/api/growth/campaigns/${encodeURIComponent(campaign)}/targets/${encodeURIComponent(target)}`, { status });
      const d = model.details[model.openId];
      if (d) d.target_status = status;
      toast(status === "opted_out" ? "Added to your do-not-contact list." : "Marked as meeting booked.");
    } catch (e) {
      toast(e.message, true);
    }
    render();
  }

  // Delete hides the email from this user's list (Outreach cannot erase a sent email); the toast offers Undo.
  async function deleteMessage(id) {
    const row = model.items.find((x) => x.id === id);
    const what = row && row.status === "replied" ? "this reply and the email it answers" : "this email";
    if (!(await confirmIt(`Delete ${what} from your inbox? It stays sent: Outreach keeps its own record. You can undo straight after.`))) return;
    await fetchJson("DELETE", `/api/growth/inbox/${encodeURIComponent(id)}`);
    model.items = model.items.filter((x) => x.id !== id);
    delete model.details[id];
    if (model.openId === id) model.openId = null;
    model.lastDeleted = id;
    render();
    undoToast("Deleted from your inbox.");
  }
  async function undoDelete() {
    const id = model.lastDeleted;
    if (!id) return;
    model.lastDeleted = null;
    try {
      await fetchJson("POST", `/api/growth/inbox/${encodeURIComponent(id)}/restore`, {});
      toast("Brought back.");
      await load(false);
    } catch (e) {
      toast(e.message, true);
    }
  }
  function undoToast(message) {
    const el = byId("gsToast");
    if (!el) return;
    el.className = "gs-toast is-on";
    el.innerHTML = `<span>${esc(message)}</span> <button type="button" class="gs-btn gs-btn--sm" data-action="in-undo">Undo</button>`;
    clearTimeout(toast.t);
    toast.t = setTimeout(() => { el.classList.remove("is-on"); model.lastDeleted = null; }, 9000);
  }
  function onToastClick(event) {
    const b = event.target.closest && event.target.closest('#gsToast [data-action="in-undo"]');
    if (b) undoDelete();
  }

  function onClick(event) {
    const a = event.target.closest("[data-action]");
    const pane = byId("gsPaneInbox");
    if (!a || !pane || !pane.contains(a)) return;
    const act = a.getAttribute("data-action");
    const run = (p) => p.catch((e) => toast((e && e.message) || "Something went wrong.", true));
    if (act === "in-filter") { model.filter = a.dataset.filter; model.openId = null; run(load(false)); }
    else if (act === "in-search" || act === "in-refresh") { model.details = {}; run(load(false)); }
    else if (act === "in-more") run(load(true));
    else if (act === "in-open") run(openMessage(a.dataset.id));
    else if (act === "in-delete") run(deleteMessage(a.dataset.id));
    else if (act === "in-optout") run(setBuyerStatus(a.dataset.campaign, a.dataset.target, "opted_out", "Add this buyer to your do-not-contact list? They will not be emailed again from Growth Studio."));
    else if (act === "in-meeting") run(setBuyerStatus(a.dataset.campaign, a.dataset.target, "meeting", null));
    else if (act === "outreach-check") run(loadStatus());
  }

  function onInput(event) {
    if (event.target.getAttribute && event.target.getAttribute("data-field") === "in-q") model.q = event.target.value.trim();
  }

  function onKeydown(event) {
    if (event.key === "Enter" && event.target.getAttribute && event.target.getAttribute("data-field") === "in-q") {
      event.preventDefault();
      model.details = {};
      load(false);
    }
  }

  // Called by growth-studio.js when the tab is shown / hidden.
  api.open = function () {
    model.open = true;
    if (!model.opened) {
      model.opened = true;
      const pane = byId("gsPaneInbox");
      if (pane) {
        pane.addEventListener("click", onClick);
        pane.addEventListener("input", onInput);
        pane.addEventListener("keydown", onKeydown);
      }
      document.addEventListener("click", onToastClick);
      loadStatus();
    } else if (model.info && model.info.ready) {
      model.details = {};
      load(false);
    }
  };
  api.close = function () { model.open = false; };
})(typeof window !== "undefined" ? window : globalThis);
