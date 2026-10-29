/* Growth Studio: everything that turns data into HTML or into request arguments, as pure functions.
   No DOM, no fetch, no state, so tests/growth_view_test.js can run it in Node and growth-studio.js can
   stay a thin controller. Every value that came from a public notice or from the user goes through esc().
   Loaded before growth-studio.js; exposes window.GrowthView (and module.exports for the tests). */
(function (root) {
  "use strict";

  // ── formatting ────────────────────────────────────────────────────────────────────────────────
  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // Links come from ~400 council systems and from buyers: only ever link http(s).
  function safeUrl(value) {
    if (!value) return null;
    try {
      const url = new URL(String(value));
      return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
    } catch (err) {
      return null;
    }
  }

  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  function fmtDate(iso) {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ""));
    if (!m) return null;
    const month = MONTHS[Number(m[2]) - 1];
    return month ? `${Number(m[3])} ${month} ${m[1]}` : null;
  }

  function fmtMoney(n) {
    if (n == null || !isFinite(n)) return null;
    return "£" + Math.round(n).toLocaleString("en-GB");
  }

  function fmtMoneyShort(n) {
    if (n == null || !isFinite(n)) return null;
    const abs = Math.abs(n);
    if (abs >= 1e6) return "£" + (n / 1e6).toFixed(abs >= 1e7 ? 0 : 2).replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "") + "m";
    if (abs >= 1e4) return "£" + Math.round(n / 1e3) + "k";
    return fmtMoney(n);
  }

  function plural(n, one, many) {
    return `${n} ${n === 1 ? one : many || one + "s"}`;
  }

  function inDays(days) {
    if (days == null) return "";
    if (days <= 0) return "today";
    if (days === 1) return "tomorrow";
    return `in ${days} days`;
  }

  // The server stores UTC without a zone; read it as UTC so "2 days ago" is the same everywhere.
  function parseServerTime(iso) {
    if (!iso) return null;
    const text = String(iso);
    const t = Date.parse(/(Z|[+-]\d{2}:?\d{2})$/.test(text) ? text : text + "Z");
    return Number.isNaN(t) ? null : t;
  }

  function timeAgo(iso, now) {
    const t = parseServerTime(iso);
    if (t == null) return "Never";
    const minutes = Math.max(0, Math.round(((now == null ? Date.now() : now) - t) / 60000));
    if (minutes < 2) return "Just now";
    if (minutes < 60) return `${minutes} minutes ago`;
    const hours = Math.round(minutes / 60);
    if (hours < 24) return plural(hours, "hour") + " ago";
    const days = Math.round(hours / 24);
    if (days < 60) return plural(days, "day") + " ago";
    return plural(Math.round(days / 30), "month") + " ago";
  }

  function fitBand(score) {
    return score >= 75 ? "high" : score >= 50 ? "mid" : "low";
  }

  function filenameFromDisposition(header, fallback) {
    const m = /filename="?([^";]+)"?/i.exec(String(header || ""));
    return m ? m[1] : fallback;
  }

  // Splice text into a field's value at the caret (or over the selection).
  function insertAt(value, start, end, text) {
    const s = Math.max(0, Math.min(start == null ? value.length : start, value.length));
    const e = Math.max(s, Math.min(end == null ? s : end, value.length));
    return { value: value.slice(0, s) + text + value.slice(e), caret: s + text.length };
  }

  // ── request arguments ─────────────────────────────────────────────────────────────────────────
  const DEFAULT_TYPES = ["renewal", "development", "engagement"];

  function hasCategory(f) {
    return Boolean(f && (f.preset || f.cpv || f.q));
  }

  function defaultFilters(options) {
    return { types: DEFAULT_TYPES.slice(), days: (options && options.default_days) || 180, authority: "all", where: "", frameworks: true };
  }

  // The JSON the API takes for filters (PUT targeting, POST campaigns).
  function filtersBody(f) {
    const out = {};
    if (f.preset) out.preset = f.preset;
    else {
      if (f.cpv) out.cpv = f.cpv;
      if (f.q) out.q = f.q;
    }
    out.types = (f.types && f.types.length ? f.types : DEFAULT_TYPES).slice();
    out.days = Number(f.days) || 180;
    out.authority = f.authority || "all";
    out.where = f.where || "";
    out.frameworks = f.frameworks !== false;
    if (Number(f.min_fit) > 0) out.min_fit = Number(f.min_fit);
    return out;
  }

  // The same filters as a query string (GET signals).
  function signalsQuery(f, profileId, showDismissed) {
    const body = filtersBody(f);
    const p = new URLSearchParams();
    ["preset", "cpv", "q"].forEach((k) => body[k] && p.set(k, body[k]));
    p.set("types", body.types.join(","));
    p.set("days", String(body.days));
    if (body.authority !== "all") p.set("authority", body.authority);
    if (body.where) p.set("where", body.where);
    p.set("frameworks", body.frameworks ? "1" : "0");
    if (body.min_fit) p.set("min_fit", String(body.min_fit));
    if (profileId) p.set("profile_id", String(profileId));
    if (showDismissed) p.set("show_dismissed", "1");
    return p.toString();
  }

  // Which signals "Start campaign" on one card means: the ticked ones when that card is ticked, else just it.
  function campaignKeys(clicked, selected) {
    return selected.indexOf(clicked) >= 0 && selected.length ? selected.slice() : [clicked];
  }

  // ── building blocks ───────────────────────────────────────────────────────────────────────────
  const ICONS = {
    renewal: '<svg class="gs-ico" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/></svg>',
    development: '<svg class="gs-ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 21h18M6 21V11l6-4 6 4v10M10 21v-5h4v5"/></svg>',
    engagement: '<svg class="gs-ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 10v4a1 1 0 0 0 1 1h2l5 4V5L7 9H5a1 1 0 0 0-1 1z"/><path d="M16 9a4 4 0 0 1 0 6"/></svg>',
    funding: '<svg class="gs-ico" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8"/><path d="M12 7v10M9.5 9.5C9.5 8.5 10.7 8 12 8s2.5.6 2.5 1.6c0 2.3-5 1.3-5 3.8 0 1 1.2 1.6 2.5 1.6s2.5-.6 2.5-1.6"/></svg>',
  };
  const TYPES = {
    renewal: { label: "Contract renewal", tone: "warn", cta: "Start campaign" },
    development: { label: "New development", tone: "good", cta: "Start campaign" },
    engagement: { label: "Market engagement open", tone: "info", cta: "Respond to engagement" },
    funding: { label: "Recently funded", tone: "good", cta: "Start campaign" },
  };
  const TARGET_STATUS = {
    not_sent: "Not sent",
    sent: "Sent",
    replied: "Replied",
    meeting: "Meeting booked",
    opted_out: "Opted out: never contact",
  };
  const CAMPAIGN_STATUS = { draft: "Draft", active: "Active", paused: "Paused", completed: "Completed" };
  const CAMPAIGN_TONE = { draft: "muted", active: "good", paused: "warn", completed: "info" };

  function badge(text, tone, extra) {
    return `<span class="gs-badge${tone ? " gs-badge--" + tone : ""}"${extra || ""}>${text}</span>`;
  }

  function note(html, tone) {
    return `<div class="gs-note${tone ? " gs-note--" + tone : ""}">${html}</div>`;
  }

  function empty(title, text, actions) {
    return `<div class="gs-empty"><strong>${esc(title)}</strong>${text ? `<p>${text}</p>` : ""}${actions || ""}</div>`;
  }

  function loading(text) {
    return `<div class="gs-loading" role="status">${esc(text || "Loading…")}</div>`;
  }

  function errorBox(message, retryAction) {
    return note(
      `${esc(message || "Something went wrong.")} ${retryAction ? `<button type="button" class="gs-link" data-action="${esc(retryAction)}">Try again</button>` : ""}`,
      "bad"
    );
  }

  // ── signals ───────────────────────────────────────────────────────────────────────────────────
  function fitParts(parts) {
    return (
      '<ul class="gs-parts">' +
      (parts || [])
        .map((p) => {
          const pct = p.max ? Math.round((100 * p.points) / p.max) : 0;
          return (
            `<li><span class="gs-parts__label">${esc(p.label)}</span>` +
            `<span class="gs-parts__bar" aria-hidden="true"><i style="width:${pct}%"></i></span>` +
            `<span class="gs-parts__pts">${p.points}/${p.max}</span>` +
            `<span class="gs-parts__note">${esc(p.note)}</span></li>`
          );
        })
        .join("") +
      "</ul>"
    );
  }

  function contractLine(c) {
    const bits = [];
    bits.push(`${c.framework ? "Framework · " : ""}ends ${esc(fmtDate(c.ends) || "")}${c.days_left != null ? ` (${esc(inDays(c.days_left))})` : ""}`);
    if (c.suppliers && c.suppliers.length) {
      const extra = (c.supplier_count || c.suppliers.length) - c.suppliers.length;
      bits.push(`${c.framework ? "Appointed" : "Incumbent"}: ${esc(c.suppliers.slice(0, 3).join(", "))}${extra > 0 ? ` +${extra}` : ""}`);
    }
    if (c.value != null) bits.push(`${esc(fmtMoney(c.value))}${c.annual_value ? ` (about ${esc(fmtMoney(c.annual_value))} a year)` : ""}`);
    return `<li class="gs-contract"><span class="gs-contract__title">${esc(c.title)}</span><span class="gs-contract__meta">${bits.join(" · ")}</span></li>`;
  }

  function contractsBlock(s, open) {
    const list = s.contracts || [];
    const total = list.length + (s.more_contracts || 0);
    if (total <= 1) return "";
    return (
      `<div class="gs-contracts"><button type="button" class="gs-link" data-action="toggle-more" data-key="${esc(s.key)}" aria-expanded="${open}">` +
      `${open ? "Hide" : "Show"} all ${total} contracts</button>` +
      (open ? `<ul>${list.map(contractLine).join("")}</ul>${s.more_contracts ? `<p class="gs-hint">${plural(s.more_contracts, "more contract")} not shown: narrow the filters to see them.</p>` : ""}` : "") +
      "</div>"
    );
  }

  function linksBlock(links) {
    const items = (links || [])
      .map((l) => ({ label: l.label, url: safeUrl(l.url) }))
      .filter((l) => l.url)
      .map((l) => `<a class="gs-link" href="${esc(l.url)}" target="_blank" rel="noopener noreferrer">${esc(l.label)} ↗</a>`);
    return items.length ? `<span class="gs-signal__links">${items.join("")}</span>` : "";
  }

  function signalCard(s, ctx) {
    const t = TYPES[s.type] || TYPES.renewal;
    const target = s.target || {};
    const state = s.state || {};
    const selected = ctx.selected.indexOf(s.key) >= 0;
    const fitOpen = ctx.expanded.indexOf("fit:" + s.key) >= 0;
    const moreOpen = ctx.expanded.indexOf("more:" + s.key) >= 0;
    const chips = [badge(`${ICONS[s.type] || ""}${esc(t.label)}`, t.tone)];
    if (state.opted_out) chips.push(badge("Do not contact", "bad"));
    if (state.dismissed) chips.push(badge("Dismissed", "muted"));
    // one tag per card: the most recent campaign, with "+N" when the signal is in others too
    const inCampaigns = (state.campaigns || []).filter((c, i, all) => all.findIndex((x) => x.id === c.id) === i);
    if (inCampaigns.length) {
      const first = inCampaigns[0];
      const more = inCampaigns.length - 1;
      chips.push(
        `<button type="button" class="gs-badge gs-badge--accent gs-badge--link" data-action="open-campaign" data-id="${esc(first.id)}" title="${more ? "Also in " + more + " other campaign" + (more > 1 ? "s" : "") + ". " : ""}Open this campaign">Buyer already in: ${esc(first.name)}${more ? " +" + more : ""}</button>`
      );
    }
    const select = target.targetable
      ? `<label class="gs-signal__select"><input type="checkbox" data-action="toggle-select" data-key="${esc(s.key)}"${selected ? " checked" : ""}><span class="visually-hidden">Select ${esc(s.buyer)}</span></label>`
      : `<span class="gs-signal__select gs-signal__select--off" aria-hidden="true"></span>`;
    const cta = target.targetable
      ? `<button type="button" class="gs-btn gs-btn--primary gs-btn--sm" data-action="start-campaign" data-key="${esc(s.key)}">${esc(t.cta)} →</button>`
      : `<button type="button" class="gs-btn gs-btn--primary gs-btn--sm" disabled title="${esc(target.reason || "")}">${esc(t.cta)} →</button>`;
    const restore = state.dismissed
      ? `<button type="button" class="gs-btn gs-btn--ghost gs-btn--sm" data-action="restore" data-key="${esc(s.key)}">Restore</button>`
      : `<button type="button" class="gs-btn gs-btn--ghost gs-btn--sm" data-action="dismiss" data-key="${esc(s.key)}">Dismiss</button>`;
    return (
      `<article class="gs-signal gs-signal--${esc(s.type)}${selected ? " is-selected" : ""}${state.dismissed ? " is-dismissed" : ""}" data-key="${esc(s.key)}">` +
      `<div class="gs-signal__top">${select}<div class="gs-signal__meta"><div class="gs-signal__chips">${chips.join("")}</div>` +
      (s.services && s.services.length ? `<div class="gs-signal__services" title="The services you chose that this buyer's contracts fall under">${s.services.map((n) => `<span class="gs-badge">${esc(n)}</span>`).join(" ")}</div>` : "") +
      `<div class="gs-signal__buyer">${esc(s.buyer)}</div>${s.authority ? `<div class="gs-signal__authority">${esc(s.authority)}</div>` : ""}</div>` +
      `<button type="button" class="gs-fit gs-fit--${esc(s.band || fitBand(s.fit))}" data-action="toggle-fit" data-key="${esc(s.key)}" aria-expanded="${fitOpen}" title="Why this score?">Fit <b>${esc(s.fit)}%</b></button></div>` +
      `<h3 class="gs-signal__headline">${esc(s.headline)}</h3>` +
      (s.detail ? `<p class="gs-signal__detail">${esc(s.detail)}</p>` : "") +
      (fitOpen ? `<div class="gs-signal__fit">${fitParts(s.fit_parts)}</div>` : "") +
      contractsBlock(s, moreOpen) +
      (target.targetable ? "" : `<p class="gs-signal__why">${esc(target.reason || "")}</p>`) +
      `<div class="gs-signal__foot">${linksBlock(s.links)}<span class="gs-signal__actions">${restore}${cta}</span></div>` +
      "</article>"
    );
  }

  // "26 renewals · 25 of 400 new developments": a type that was cut says how much of it you are looking at.
  function countsLine(data) {
    const c = data.counts || {};
    const s = data.shown_by_type || {};
    const bit = (type, one, many) => {
      const n = c[type] || 0;
      if (!n) return null;
      const shown = s[type] == null ? n : s[type];
      return shown < n ? `${shown} of ${n} ${n === 1 ? one : many}` : plural(n, one, many);
    };
    const hidden = data.below_min_fit && data.filters && data.filters.min_fit
      ? `${plural(data.below_min_fit, "signal")} hidden below ${data.filters.min_fit}% fit` : null;
    return [bit("renewal", "renewal", "renewals"), bit("development", "new development", "new developments"), bit("engagement", "market engagement", "market engagements"), bit("funding", "recently funded company", "recently funded companies"), hidden]
      .filter(Boolean)
      .join(" · ");
  }

  function suggestionChips(suggestions) {
    if (!suggestions || !suggestions.length) return "";
    return (
      '<div class="gs-chips" aria-label="Suggested categories"><span class="gs-chips__label">Suggested from your profile:</span>' +
      suggestions
        .map((s) => `<button type="button" class="gs-chip" data-action="pick-category" data-preset="${esc(s.preset)}" title="Matches: ${esc((s.matches || []).join(", "))}">${esc(s.label)}</button>`)
        .join("") +
      "</div>"
    );
  }

  function selectionBar(m) {
    const n = m.selected.length;
    if (!n) return "";
    const open = (m.campaigns || []).filter((c) => c.status !== "completed");
    return (
      `<div class="gs-selbar" role="region" aria-label="Selected signals"><span><b>${n}</b> selected</span>` +
      `<button type="button" class="gs-btn gs-btn--primary gs-btn--sm" data-action="create-from-selection">Create campaign</button>` +
      (open.length
        ? `<select class="gs-select gs-select--sm" id="gsAddTo" aria-label="Add the selected signals to an existing campaign"><option value="">Add to an existing campaign…</option>${open
            .map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`)
            .join("")}</select>`
        : "") +
      `<button type="button" class="gs-link" data-action="clear-selection">Clear selection</button></div>`
    );
  }

  function signalsPane(m) {
    const f = m.filters || {};
    if (!hasCategory(f)) {
      return empty(
        "Choose a category to see signals",
        "Signals are contracts about to end, planning approvals and market engagements in the category you sell into. Pick one above" +
          (m.suggestions && m.suggestions.length ? ", or start from a suggestion." : "."),
        suggestionChips(m.suggestions)
      );
    }
    if (m.status === "loading" && !m.data) return loading("Finding signals…");
    if (m.status === "error") return errorBox(m.error, "reload-signals");
    const d = m.data;
    if (!d) return loading("Finding signals…");
    const ctx = { selected: m.selected || [], expanded: m.expanded || [] };
    const parts = [];
    if (m.status === "loading") parts.push('<div class="gs-refresh" role="status">Updating…</div>');
    parts.push(
      `<div class="gs-summary"><span class="gs-summary__count" aria-live="polite">${
        d.total ? `${esc(plural(d.total, "signal"))}: ${esc(countsLine(d))}` : "No signals"
      }</span>` +
        (d.counts && d.counts.dismissed
          ? `<button type="button" class="gs-link" data-action="toggle-dismissed" aria-pressed="${Boolean(m.showDismissed)}">${m.showDismissed ? "Hide" : "Show"} ${d.counts.dismissed} dismissed</button>`
          : "") +
        "</div>"
    );
    if (d.total > d.shown) {
      parts.push(`<p class="gs-hint gs-hint--block">The best ${esc(m.maxPerType || 25)} of each type are shown. Narrow the filters to see others.</p>`);
    }
    parts.push('<div id="gsSelBar">' + selectionBar(m) + "</div>");
    if (d.notes && d.notes.development && (f.types || []).indexOf("development") >= 0) parts.push(note(esc(d.notes.development), "info"));
    if (d.notes && d.notes.funding && (f.types || []).indexOf("funding") >= 0) parts.push(note(esc(d.notes.funding), "info"));
    if (d.notes && d.notes.truncated) parts.push(note(esc(d.notes.truncated), "warn"));
    if (!d.signals.length) {
      parts.push(
        empty(
          "No signals match these filters",
          "Try a longer time window, a wider signal type, or clear the place filter. Contracts only appear when their award notice gives an end date.",
          suggestionChips(m.suggestions)
        )
      );
    } else {
      parts.push(`<div class="gs-list">${d.signals.map((s) => signalCard(s, ctx)).join("")}</div>`);
    }
    const notes = ["fit", "renewal", "values", "where"].filter((k) => d.notes && d.notes[k]);
    if (notes.length) {
      parts.push(
        `<details class="gs-notes"><summary>How to read these signals</summary><ul>${notes.map((k) => `<li>${esc(d.notes[k])}</li>`).join("")}</ul></details>`
      );
    }
    return parts.join("");
  }

  // ── campaigns ─────────────────────────────────────────────────────────────────────────────────
  function funnelText(counts) {
    const c = counts || {};
    return `${c.sent || 0} sent · ${c.replied || 0} replied · ${c.meetings || 0} ${(c.meetings || 0) === 1 ? "meeting" : "meetings"}`;
  }

  function suppressionsBlock(list, open) {
    if (!list || !list.length) return "";
    return (
      `<details class="gs-notes gs-dnc"${open ? " open" : ""}><summary>Do-not-contact list (${list.length})</summary>` +
      `<p class="gs-hint">These buyers asked not to be contacted. They are left out of every export and cannot be added to a campaign.</p><ul class="gs-dnc__list">` +
      list
        .map(
          (s) =>
            `<li><span>${esc(s.name || s.key)}</span><span class="gs-hint">${esc(fmtDate(s.created_at) || "")}</span>` +
            `<button type="button" class="gs-link" data-action="remove-suppression" data-key="${esc(s.key)}">Remove</button></li>`
        )
        .join("") +
      "</ul></details>"
    );
  }

  function campaignsPane(m) {
    if (m.status === "loading" && !m.list) return loading("Loading campaigns…");
    if (m.status === "error") return errorBox(m.error, "reload-campaigns");
    const list = m.list || [];
    if (!list.length) {
      return (
        empty("No campaigns yet", "Start one from a signal: tick the buyers you want to approach and choose Create campaign. You export the list and send it from your own mail or Mailchimp, or send it through Outreach.") +
        suppressionsBlock(m.suppressions, m.suppressionsOpen)
      );
    }
    return (
      '<div class="gs-card"><div class="gs-tablewrap"><table class="gs-table"><thead><tr><th>Campaign</th><th>Status</th><th>Channel</th><th class="gs-num">Buyers</th><th>Funnel</th><th>Last activity</th><th><span class="visually-hidden">Open</span></th></tr></thead><tbody>' +
      list
        .map(
          (c) =>
            `<tr><td><button type="button" class="gs-link gs-link--strong" data-action="open-campaign" data-id="${esc(c.id)}">${esc(c.name)}</button>` +
            `<div class="gs-hint">${esc(c.category_label || "")}${c.profile_name ? ` · ${esc(c.profile_name)}` : ""}</div></td>` +
            `<td>${badge(esc(CAMPAIGN_STATUS[c.status] || c.status), CAMPAIGN_TONE[c.status])}</td>` +
            `<td>${esc(c.channel_label || c.channel)}</td><td class="gs-num">${esc((c.counts || {}).targets || 0)}</td>` +
            `<td class="gs-mono">${esc(funnelText(c.counts))}</td><td>${esc(timeAgo(c.last_activity_at || c.created_at))}</td>` +
            `<td><button type="button" class="gs-btn gs-btn--ghost gs-btn--sm" data-action="open-campaign" data-id="${esc(c.id)}">Open →</button></td></tr>`
        )
        .join("") +
      "</tbody></table></div></div>" +
      suppressionsBlock(m.suppressions, m.suppressionsOpen)
    );
  }

  // ── campaign builder ──────────────────────────────────────────────────────────────────────────
  function statusOptions(current) {
    return Object.keys(TARGET_STATUS)
      .map((k) => `<option value="${k}"${k === current ? " selected" : ""}>${esc(TARGET_STATUS[k])}</option>`)
      .join("");
  }

  // The server looks the buyer's published inbox up (the notice, then the buyer's website) and fills the box. This
  // only says where that stands. The notice link stays as the manual fallback when nothing was published.
  function noticeLink(t) {
    if (t._lookup === "looking") return `<div class="gs-hint">Looking for the buyer's contact…</div>`;
    const link = (t.links || []).map((l) => safeUrl(l && l.url)).find(Boolean);
    const manual = link
      ? ` <a class="gs-link" href="${esc(link)}" target="_blank" rel="noreferrer noopener">Check the notice ↗</a>`
      : " Look on the buyer's website for a procurement inbox.";
    if (t._lookup === "personal_only") {
      const sg = t._suggest;
      const from = sg && sg.source === "website" ? "the buyer's website" : "the notice";
      return sg && sg.email
        ? `<div class="gs-hint">${esc(from[0].toUpperCase() + from.slice(1))} names a contact: <b>${esc(sg.email)}</b> (a named person, not a shared inbox). ` +
          `<button type="button" class="gs-btn gs-btn--sm" data-action="use-suggestion" data-id="${esc(t.id)}" data-email="${esc(sg.email)}">Use this address</button> ` +
          `or type a role inbox yourself.${manual}</div>`
        : `<div class="gs-hint">Only a named person's address is published. Add a role inbox yourself.${manual}</div>`;
    }
    if (t._lookup === "not_found") return `<div class="gs-hint">No public inbox found.${manual}</div>`;
    return link
      ? `<div class="gs-hint">No address yet. <a class="gs-link" href="${esc(link)}" target="_blank" rel="noreferrer noopener">Find the contact on the notice ↗</a></div>`
      : `<div class="gs-hint">No address yet. Look on the buyer's website or the notice for a procurement inbox.</div>`;
  }

  // Where an address that the server filled in came from, so the user can check it.
  function foundNote(t) {
    const f = t.contact_found;
    if (!f || !t.contact_email) return "";
    const url = safeUrl(f.url);
    const where = f.source === "notice" ? "the notice" : "the buyer's website";
    return `<div class="gs-hint">Found on ${url ? `<a class="gs-link" href="${esc(url)}" target="_blank" rel="noreferrer noopener">${where} ↗</a>` : where}. Check it before you send.</div>`;
  }

  // The global watchlist button (web/watchlist.js); absent when that script is not loaded (the Node tests).
  function watchButton(t) {
    return typeof window !== "undefined" && window.TFWatch ? " " + window.TFWatch.buttonHtml("buyer", t.buyer_name, t.buyer_name, "growth_studio") : "";
  }

  // One buyer: who and why on the left, the address in the middle, status and Remove on the right.
  // opts: previewing (the row whose message is in the preview), hidden (filtered out of the list), kind (ready / needs / off).
  function targetRow(t, opts) {
    const o = opts || {};
    const dim = !t.included || t.opted_out;
    return (
      `<div class="gs-target${dim ? " is-off" : ""}${o.previewing ? " is-previewing" : ""}" data-id="${esc(t.id)}" data-kind="${esc(o.kind || "")}"${o.hidden ? " hidden" : ""}>` +
      `<input type="checkbox" class="gs-target__on" data-action="target-include" data-id="${esc(t.id)}"${t.included ? " checked" : ""}${t.opted_out ? " disabled" : ""} aria-label="Include ${esc(t.buyer_name)} in this campaign">` +
      `<div class="gs-target__main">` +
      `<div class="gs-target__info" data-action="preview-buyer" data-id="${esc(t.id)}" title="Show the message this buyer receives"><span class="gs-target__who"><b>${esc(t.buyer_name)}</b>${watchButton(t)}${t.opted_out ? " " + badge("Do not contact", "bad") : ""}</span>` +
      `<div class="gs-target__sig">${badge(esc(t.signal_label), (TYPES[t.signal_type] || {}).tone)} <span>${esc(t.headline || "")}</span></div></div>` +
      `<div class="gs-target__email">` +
      `<input type="email" class="gs-input gs-input--sm" data-field="contact_email" data-id="${esc(t.id)}" value="${esc(t.contact_email || "")}" placeholder="procurement@…" autocomplete="off" aria-label="Contact email for ${esc(t.buyer_name)}">` +
      (t.personal_email ? `<div class="gs-hint gs-hint--warn">Looks like a named person's address. Use a role-based inbox such as procurement@ where you can.</div>` : "") +
      foundNote(t) +
      (!t.contact_email && !t.opted_out ? noticeLink(t) : "") +
      `</div>` +
      `<span class="gs-target__ctl"><select class="gs-select gs-select--sm" data-field="status" data-id="${esc(t.id)}" aria-label="Status for ${esc(t.buyer_name)}">${statusOptions(t.status)}</select>` +
      `<button type="button" class="gs-icon-btn" data-action="remove-target" data-id="${esc(t.id)}" aria-label="Remove ${esc(t.buyer_name)} from this campaign" title="Remove">×</button></span>` +
      `</div></div>`
    );
  }

  function warningsHtml(warnings) {
    return (warnings || []).length ? `<ul class="gs-warnings">${warnings.map((w) => `<li>${esc(w.text)}</li>`).join("")}</ul>` : "";
  }

  function previewHtml(p, targets, selectedId) {
    const choices = (targets || []).filter((t) => !t.opted_out);
    if (!choices.length) return '<p class="gs-hint">Add a buyer to see the message as they will read it.</p>';
    const picker = choices.length
      ? `<select id="gsPreviewTarget" class="gs-select gs-select--sm" aria-label="Preview the message for">${choices
          .map((t) => `<option value="${esc(t.id)}"${t.id === selectedId ? " selected" : ""}>${esc(t.buyer_name)}</option>`)
          .join("")}</select>`
      : "";
    const at = Math.max(0, choices.findIndex((t) => t.id === selectedId));
    const pager = `<span class="gs-pager"><span class="gs-hint">${at + 1} of ${choices.length}</span>` +
      `<button type="button" class="gs-icon-btn" data-action="preview-step" data-dir="-1" aria-label="Previous buyer"${choices.length < 2 ? " disabled" : ""}>‹</button>` +
      `<button type="button" class="gs-icon-btn" data-action="preview-step" data-dir="1" aria-label="Next buyer"${choices.length < 2 ? " disabled" : ""}>›</button></span>`;
    if (!p) return `<div class="gs-preview__head"><span>Preview for</span>${picker}${pager}</div>${loading("Preparing the preview…")}`;
    const issues = [];
    if (p.missing && p.missing.length) issues.push(`This buyer has no value for ${p.missing.map((n) => "{{" + esc(n) + "}}").join(", ")}, so that part will be blank.`);
    if (p.unknown && p.unknown.length) issues.push(`Not a merge field: ${p.unknown.map((n) => "{{" + esc(n) + "}}").join(", ")}.`);
    return (
      `<div class="gs-preview__head"><span>Preview for</span>${picker}${pager}</div>` +
      `<div class="gs-mail"><div class="gs-mail__hd">` +
      `<div class="gs-preview__to"><span class="gs-mail__k">To</span><span>${p.to ? esc(p.to) : `<i>no email address yet</i> <button type="button" class="gs-link" data-action="focus-email" data-id="${esc(p.target_id)}">Add one</button>`}</span></div>` +
      `<div class="gs-preview__subject"><span class="gs-mail__k">Subject</span><span><b>${esc(p.subject)}</b></span></div>` +
      ((p.attachments || []).length
        ? `<div class="gs-preview__files"><span class="gs-mail__k">Files</span><span>${p.attachments.map((f) => esc(f.name)).join(", ")}</span></div>`
        : "") +
      `</div>` +
      (p.html ? `<div class="gs-preview__rich" data-rich="${esc(p.html)}"></div>` : "") +
      `<pre class="gs-preview__body">${esc(p.body)}</pre></div>` +
      (issues.length ? `<ul class="gs-warnings">${issues.map((i) => `<li>${i}</li>`).join("")}</ul>` : "")
    );
  }

  function mergeChips(fields, types) {
    const want = new Set(types && types.length ? types : DEFAULT_TYPES);
    return (fields || [])
      .filter((f) => (f.types || []).some((t) => want.has(t)))
      .map((f) => `<button type="button" class="gs-chip gs-chip--code" data-action="insert-field" data-name="${esc(f.name)}" title="${esc(f.label)}">{{${esc(f.name)}}}</button>`)
      .join("");
  }

  // ── a formatted message: the editor's toolbar, what is allowed in it, and the files that travel with it ──
  const RICH_TAGS = new Set(["P", "BR", "B", "STRONG", "I", "EM", "U", "S", "A", "UL", "OL", "LI", "H2", "H3", "BLOCKQUOTE", "HR"]);
  const RICH_ALIAS = { DIV: "P", H1: "H2", H4: "H3", H5: "H3", H6: "H3", STRIKE: "S", DEL: "S" };
  const RICH_SKIP = new Set(["SCRIPT", "STYLE", "HEAD", "TITLE", "IFRAME", "OBJECT", "EMBED", "TEMPLATE", "NOSCRIPT", "SVG", "MATH"]);

  // The same allow-list the server applies (tender_app/email_rich.py), applied again before anything is shown: only these tags, no
  // attributes except a web or mail address on a link. Without a browser (the Node tests) it falls back to the text alone.
  function safeHtml(html) {
    const src = String(html == null ? "" : html);
    if (typeof DOMParser === "undefined" || typeof document === "undefined") return esc(src.replace(/<[^>]*>/g, ""));
    const doc = new DOMParser().parseFromString("<body>" + src, "text/html");
    const out = document.createElement("div");
    const walk = (from, into) => {
      from.childNodes.forEach((n) => {
        if (n.nodeType === 3) return void into.appendChild(document.createTextNode(n.nodeValue));
        if (n.nodeType !== 1 || RICH_SKIP.has(n.tagName)) return;
        const tag = RICH_ALIAS[n.tagName] || n.tagName;
        if (!RICH_TAGS.has(tag)) return void walk(n, into);
        if (tag === "A") {
          const href = (n.getAttribute("href") || "").trim();
          if (!/^(https?:\/\/|mailto:)[^\s<>"']+$/i.test(href)) return void walk(n, into);
          const a = document.createElement("a");
          a.setAttribute("href", href);
          a.setAttribute("target", "_blank");
          a.setAttribute("rel", "noopener noreferrer");
          walk(n, a);
          return void into.appendChild(a);
        }
        const el = document.createElement(tag.toLowerCase());
        if (tag !== "BR" && tag !== "HR") walk(n, el);
        into.appendChild(el);
      });
    };
    walk(doc.body, out);
    return out.innerHTML;
  }

  // A message written as plain text, as paragraphs for the editor.
  function textToHtml(text) {
    return String(text == null ? "" : text)
      .replace(/\r\n?/g, "\n")
      .trim()
      .split(/\n\s*\n/)
      .filter((p) => p.trim())
      .map((p) => "<p>" + esc(p.replace(/^\n+|\n+$/g, "")).replace(/\n/g, "<br>") + "</p>")
      .join("");
  }

  // Fill the editor and the formatted previews, which carry their HTML in an attribute so the markup here never holds any.
  function fillRich(root) {
    if (!root || !root.querySelectorAll) return;
    root.querySelectorAll("[data-html]").forEach((el) => {
      el.innerHTML = safeHtml(el.getAttribute("data-html"));
      el.removeAttribute("data-html");
    });
    root.querySelectorAll("[data-rich]").forEach((el) => {
      el.innerHTML = safeHtml(el.getAttribute("data-rich"));
      el.removeAttribute("data-rich");
      if (el.parentNode && el.parentNode.classList) el.parentNode.classList.add("has-rich");
    });
  }

  function richToolbar() {
    const b = (cmd, label, tip, extra) =>
      `<button type="button" class="gs-btn gs-btn--sm gs-rich__btn" data-action="rich-cmd" data-cmd="${cmd}" data-tip="${esc(tip)}" aria-label="${esc(tip)}"${extra || ""}>${label}</button>`;
    return (
      `<div class="gs-richbar" role="toolbar" aria-label="Format the message">` +
      b("bold", "<b>B</b>", "Make the selected text bold.") + b("italic", "<i>I</i>", "Make the selected text italic.") + b("underline", "<u>U</u>", "Underline the selected text.") +
      b("ul", "• List", "Turn the selected lines into a bulleted list.") + b("ol", "1. List", "Turn the selected lines into a numbered list.") +
      b("link", "Link", "Turn the selected text into a link to a web or email address.") + b("clear", "Clear", "Remove the formatting from the selected text.") +
      `</div>`
    );
  }

  const fileSize = (n) => (n >= 1048576 ? (n / 1048576).toFixed(1).replace(/\.0$/, "") + " MB" : Math.max(1, Math.round(n / 1024)) + " KB");

  // Files that go with every email of the campaign. They are sent from the user's own email account only.
  function attachmentsBlock(files) {
    const list = Array.isArray(files) ? files : [];
    return (
      `<div class="gs-attach"><div class="gs-attach__row"><button type="button" class="gs-btn gs-btn--sm" data-action="attach-files">Attach files</button>` +
      `<input type="file" id="gsAttachInput" class="visually-hidden" multiple tabindex="-1" aria-label="Choose files to attach">` +
      `<span class="gs-hint">${list.length ? esc(plural(list.length, "file")) + " attached" : "Up to 10 files, 10 MB together. Sent from your own email account."}</span></div>` +
      (list.length
        ? `<ul class="gs-attach__list">${list
            .map(
              (f) =>
                `<li class="gs-attach__item"><span class="gs-attach__name">${esc(f.name)}</span> <span class="gs-hint">${esc(fileSize(f.size || 0))}</span>` +
                `<button type="button" class="gs-icon-btn" data-action="remove-attachment" data-id="${esc(f.id)}" aria-label="Remove ${esc(f.name)}" title="Remove">×</button></li>`
            )
            .join("")}</ul>`
        : "") +
      `</div>`
    );
  }

  // Which company the campaign speaks for (its name, contact and signature). Only offered when the user has company profiles.
  function companySelect(c, profiles) {
    const list = Array.isArray(profiles) ? profiles.filter((p) => p && p.id != null) : [];
    if (!list.length) return "";
    const known = list.some((p) => p.id === c.profile_id);
    const opts =
      (c.profile_id == null || !known ? `<option value="" selected>${esc(c.profile_name || "No company")}</option>` : "") +
      list.map((p) => `<option value="${esc(p.id)}"${p.id === c.profile_id ? " selected" : ""}>${esc(p.name)}</option>`).join("");
    return `<label class="gs-inline">Company <select class="gs-select gs-select--sm" data-field="campaign-profile">${opts}</select></label>`;
  }

  // Shown at the top of the builder when no email can be sent yet: the same way to connect as the Inbox tab offers.
  function emailConnectNotice(o) {
    const info = o && o.info;
    if (!info || (o.status !== "ok" && o.status !== "error")) return "";
    if (info.ready || ownAccount(info)) return "";
    return (
      `<section class="gs-card gs-conn-card" aria-label="Email is not connected"><div class="gs-conn-card__txt"><div class="gs-card__title">Email is not connected</div>` +
      `<p class="gs-hint gs-hint--warn">No email account is set up yet, so nothing can be sent. Add your own email account (Gmail, Outlook or any SMTP login) or connect Outreach. The window opens on this page.</p></div>` +
      `<div class="gs-actions gs-actions--tight">${openConnections("mail", "＋ Add an email account", true, "Open Connections on this page to add your own email account (Gmail, Outlook or any other provider).")}` +
      `${openConnections("outreach", "Connect Outreach", false, "Sign in to Outreach, or create an account, in a window on this page.")}` +
      `<button type="button" class="gs-btn gs-btn--sm" data-action="outreach-check" data-tip="Ask again which email accounts you have.">Refresh accounts</button></div></section>`
    );
  }

  // The same box for the Social, Calls and Inbox tabs (kind "social" | "calls" | "mail"). Shown while that channel cannot be used.
  // Outreach not connected: a connect button; connected but no number / social account yet: the Connections button.
  function channelConnectNotice(kind, o) {
    if (kind === "mail") return emailConnectNotice(o);
    const info = o && o.info;
    if (!info || (o.status !== "ok" && o.status !== "error")) return "";
    const w = info.wallet || {};
    let title, text;
    if (kind === "calls") {
      const phones = w.phone_status && Number.isFinite(w.phone_status.active) ? w.phone_status.active : Number(w.phone_numbers) || 0;
      if (info.ready && phones > 0) return "";
      title = "Calls are not connected";
      text = info.ready
        ? "Your Outreach account has no phone number yet, so the AI has no line to call from. Phone numbers belong to the Outreach account: once it has one, open Connections and press Refresh."
        : "Outreach is not connected, so no calls can be made. Connect Outreach, then add a phone number to it. The window opens on this page.";
    } else {
      const nets = (Array.isArray(w.social_channels) ? w.social_channels : []).filter(Boolean);
      if (info.ready && nets.length) return "";
      title = "Social is not connected";
      text = info.ready
        ? "No social account is connected yet, so posts cannot be published. Open Connections to connect LinkedIn, Facebook, Instagram, X or Threads."
        : "Outreach is not connected, so nothing can be posted. Connect Outreach, then connect your social accounts. The window opens on this page.";
    }
    return (
      `<section class="gs-card gs-conn-card" aria-label="${esc(title)}"><div class="gs-conn-card__txt"><div class="gs-card__title">${esc(title)}</div>` +
      `<p class="gs-hint gs-hint--warn">${esc(text)}</p></div>` +
      `<div class="gs-actions gs-actions--tight">${info.ready
        ? openConnections("outreach", "Open Connections", true, "Open Connections on this page to connect it.")
        : openConnections("outreach", "Connect Outreach", true, "Sign in to Outreach, or create an account, in a window on this page.")}` +
      `<button type="button" class="gs-btn gs-btn--sm" data-action="outreach-check" data-tip="Ask again what is connected.">Refresh accounts</button></div></section>`
    );
  }

  // The chips to the right of the Growth Studio tabs: what is connected (and to what) and what is not. Each opens Connections.
  function connectionChips(conn) {
    const info = conn && conn.info;
    if (!info) return conn && conn.status === "loading" ? `<span class="gs-hint">Checking connections…</span>` : "";
    const w = info.wallet || {};
    const own = ownAccounts(info);
    const boxes = (Array.isArray(w.mailboxes) ? w.mailboxes : []).filter((b) => b && b.email);
    const phones = w.phone_status && Number.isFinite(w.phone_status.active) ? w.phone_status.active : Number(w.phone_numbers) || 0;
    const nets = (Array.isArray(w.social_channels) ? w.social_channels : []).filter(Boolean).map((n) => String(n).replace(/^./, (c) => c.toUpperCase()));
    const items = [
      ["Email", "mail", own.length ? own[0].from_email : boxes.length ? boxes[0].email : ""],
      ["Calls", "outreach", info.ready && phones > 0 ? plural(phones, "number") : ""],
      ["Social", "outreach", info.ready && nets.length ? nets.join(", ") : ""],
    ];
    return items.map(([name, which, detail]) => {
      const on = !!detail;
      const tip = on ? `${name} is connected. Open Connections to change it.` : `${name} is not connected. Open Connections to connect it.`;
      return `<button type="button" class="gs-conn gs-conn--${on ? "on" : "off"}" data-open-connections="${which}" data-tip="${esc(tip)}">` +
        `<i class="gs-dot${on ? " gs-dot--on" : ""}" aria-hidden="true"></i> <b>${name}</b> <span>${on ? esc(detail) : "not connected"}</span></button>`;
    }).join("");
  }

  // The three buyer filters above the list: all of them, the ones ready to send, the ones still missing an address.
  function buyerKind(t) {
    return t.opted_out || !t.included ? "off" : t.contact_email ? "ready" : "needs";
  }

  function builderPane(m) {
    if (m.status === "loading" && !m.detail) return loading("Opening the campaign…");
    if (m.status === "error") return errorBox(m.error, "back-to-campaigns");
    const d = m.detail;
    if (!d) return empty("No campaign open", "Open one from the Campaigns tab, or start one from a signal.");
    const c = d.campaign;
    const draft = m.draft || { subject: c.subject, body: c.body, html: c.body_html || "" };
    const types = Array.from(new Set(d.targets.map((t) => t.signal_type)));
    const channels = (m.options && m.options.channels) || [{ id: c.channel, label: c.channel_label }];
    const cost = m.options && m.options.credit_cost_draft;
    const eligible = d.eligible;
    const unsigned = !(d.sender && (d.sender.company_name || d.sender.sender_name));
    const live = d.targets.filter((t) => t.included && !t.opted_out);
    const missing = live.filter((t) => !t.contact_email).length;
    const buyersOk = live.length > 0 && !missing;
    const msgOk = !m.dirty;
    const allOk = buyersOk && msgOk;
    const files = (d.attachments || []).length;
    const firstTodo = [buyersOk, msgOk, allOk, allOk].findIndex((x) => !x); // the step the reader should do next
    const step = (n, id, label, sub, ok) =>
      `<button type="button" class="gs-step ${ok ? "is-done" : "is-todo"}${n - 1 === firstTodo ? " is-current" : ""}" data-action="goto-step" data-target="${id}"><span class="gs-step__dot">${ok ? "✓" : n}</span>` +
      `<span class="gs-step__txt"><b>${label}</b><small>${esc(sub)}</small></span></button>`;
    const steps =
      `<nav class="gs-steps" aria-label="Campaign steps">` +
      step(1, "gsBuyersCard", "Buyers", `${live.length - missing} of ${live.length} ready`, buyersOk) +
      step(2, "gsMessageCard", "Message", msgOk ? "Saved" : "Unsaved changes", msgOk) +
      step(3, "gsPreviewCard", "Preview", live.length ? "Check each buyer" : "No buyers yet", allOk) +
      step(4, "gsSendCard", "Send", allOk ? `${live.length - missing} ready to send` : "Not ready yet", allOk) +
      `</nav>`;
    const exportMenu =
      `<details class="gs-menu"><summary class="gs-btn" data-tip="Download the list or copy the message to use in your own mail tool.">Export</summary><div class="gs-menu__list">` +
      `<button type="button" class="gs-btn gs-btn--ghost gs-btn--sm" data-action="export" data-format="csv"${eligible ? "" : " disabled"}>Export CSV</button>` +
      `<button type="button" class="gs-btn gs-btn--ghost gs-btn--sm" data-action="export" data-format="mailchimp"${eligible ? "" : " disabled"}>Mailchimp audience (CSV)</button>` +
      `<button type="button" class="gs-btn gs-btn--ghost gs-btn--sm" data-action="copy-mailchimp">Copy message for Mailchimp</button></div></details>`;
    // the channel, company and status of the campaign, and Delete, behind one ⋯ menu so the header stays quiet
    const moreMenu =
      `<details class="gs-menu gs-menu--settings"><summary class="gs-btn gs-btn--ghost" aria-label="Campaign settings" data-tip="Channel, company and status of this campaign, and Delete.">⋯</summary><div class="gs-menu__list gs-menu__list--form">` +
      `<label class="gs-inline">Channel <select class="gs-select gs-select--sm" data-field="campaign-channel">${channels
        .map((ch) => `<option value="${esc(ch.id)}"${ch.id === c.channel ? " selected" : ""}>${esc(ch.label)}</option>`)
        .join("")}</select></label>` +
      companySelect(c, m.profiles) +
      `<label class="gs-inline">Status <select class="gs-select gs-select--sm" data-field="campaign-status">${Object.keys(CAMPAIGN_STATUS)
        .map((k) => `<option value="${k}"${k === c.status ? " selected" : ""}>${esc(CAMPAIGN_STATUS[k])}</option>`)
        .join("")}</select></label>` +
      `<button type="button" class="gs-btn gs-btn--ghost gs-btn--danger gs-btn--sm" data-action="delete-campaign">Delete campaign</button></div></details>`;

    // who it goes from and whether there is a way to send (the Send button is in the header, and in the Sending card)
    const o = m.outreach;
    const oinfo = o && o.info;
    const canSend = !!(oinfo && (oinfo.ready || ownAccount(oinfo)));
    let fromLine = "";
    let sendBtn = "";
    if (canSend) {
      const choice = senderChoice(oinfo, o.mailbox);
      const own = isOwnChoice(choice) ? accountForChoice(oinfo, choice) : null;
      const boxes = (oinfo.wallet && Array.isArray(oinfo.wallet.mailboxes) ? oinfo.wallet.mailboxes : []).filter((b) => b && b.email);
      const picked = own ? own.from_email : (boxes.find((b) => String(b.id || b.email) === choice) || boxes[0] || {}).email;
      const filesOk = !files || !!own; // Outreach cannot carry attached files
      const sendable = live.length - missing;
      const ready = sendable > 0 && msgOk && filesOk && !o.pushing;
      const why = !live.length ? "Tick at least one buyer." : !sendable ? "No buyer has an email address yet." : !msgOk ? "Save the message first."
        : !filesOk ? "Attached files can only be sent from your own email account." : "";
      const keyMode = own ? "" : oinfo.mode === "live" ? " · Live key" : " · Test mode";
      fromLine = picked ? `<span class="gs-bhead__from"><i class="gs-dot gs-dot--on" aria-hidden="true"></i> Sending from ${esc(picked)}${keyMode}</span>` : "";
      sendBtn = `<div class="gs-bhead__go">${ready || !why ? "" : `<span class="gs-hint">${esc(why)}</span>`}` +
        `<button type="button" class="gs-btn gs-btn--gold" data-action="outreach-send"${ready ? "" : " disabled"}>${o.pushing ? "Sending…" : sendable > 0 ? `Send ${plural(sendable, "email")}` : "Send mail"}</button></div>`;
    }
    const subLine = `<div class="gs-bhead__sub"><span>${esc(c.channel_label || "Email")} campaign${c.profile_name ? " · " + esc(c.profile_name) : ""}</span>${fromLine}</div>`;
    const missingLine = missing
      ? `<p class="gs-bhead__warn" role="status">⚠ ${missing} selected ${missing === 1 ? "buyer has" : "buyers have"} no email address and will be left out.</p>`
      : "";

    // The builder shows the campaign that is open. Changing the category filter does not change it: say so, and offer Signals.
    const other = m.filterCategory && c.category_label && m.filterCategory !== c.category_label;
    // A campaign made while several categories were selected is named "A + B + C": the filter may be one of them
    const parts = String(c.category_label || "").split(" + ").map((x) => x.trim()).filter(Boolean);
    const within = other && parts.length > 1 && parts.includes(m.filterCategory);
    const notice = within
      ? `<div class="gs-hint gs-hint--warn gs-builder__notice">This campaign was made with ${parts.length} categories selected (${esc(c.category_label)}), so its buyers come from all of them. ` +
        `The category filter only changes the Signals list, never a campaign that is already open. To email only ${esc(m.filterCategory)} buyers, choose them in Signals and start a new campaign. ` +
        `<button type="button" class="gs-btn gs-btn--sm" data-action="goto-signals">Choose ${esc(m.filterCategory)} buyers in Signals</button></div>`
      : other
        ? `<div class="gs-hint gs-hint--warn gs-builder__notice">This campaign is for ${esc(c.category_label)}. Your filter is ${esc(m.filterCategory)}, which does not change a campaign that is already open. ` +
          `<button type="button" class="gs-btn gs-btn--sm" data-action="goto-signals">Choose ${esc(m.filterCategory)} buyers in Signals</button></div>`
        : "";

    const head =
      `<section class="gs-card gs-bhead" aria-label="Campaign"><div class="gs-bhead__top"><div class="gs-bhead__who">` +
      `<div class="gs-bhead__name"><label class="visually-hidden" for="gsCampaignName">Campaign name</label>` +
      `<input id="gsCampaignName" class="gs-input gs-input--title" data-field="campaign-name" value="${esc(c.name)}" maxlength="120">` +
      `<span class="gs-badge gs-badge--${c.status === "active" ? "good" : "muted"}">${esc(CAMPAIGN_STATUS[c.status] || c.status)}</span></div>${subLine}</div>` +
      `<div class="gs-bhead__act">${exportMenu}${moreMenu}${sendBtn}</div></div>` +
      `<div id="gsMcBox"></div>${steps}${missingLine}</section>`;

    // buyers, with the All / Ready / Needs address filter
    const kinds = d.targets.map(buyerKind);
    const nReady = kinds.filter((k) => k === "ready").length;
    const nNeeds = kinds.filter((k) => k === "needs").length;
    const bf = ["ready", "needs"].includes(m.buyerFilter) ? m.buyerFilter : "all";
    const chip = (id, label, n) => `<button type="button" class="gs-chipbtn${bf === id ? " is-on" : ""}" data-action="buyer-filter" data-filter="${id}" aria-pressed="${bf === id}">${label} <span>${n}</span></button>`;
    const rows = d.targets.map((t, i) => targetRow(t, { previewing: t.id === m.previewTarget, hidden: bf !== "all" && kinds[i] !== bf, kind: kinds[i] })).join("");
    const buyers =
      `<section class="gs-card gs-card--buyers" id="gsBuyersCard" aria-label="Buyers in this campaign"><div class="gs-buyers__head"><div><div class="gs-card__title">Buyers</div>` +
      `<p class="gs-hint">${eligible} of ${d.targets.length} will be exported${live.length ? ` · ${live.length - missing} ready` : ""}. Untick a buyer to leave them out. Add a published, role-based inbox for each, then record what happens after you send.</p></div>` +
      `<div class="gs-actions gs-actions--flush"><button type="button" class="gs-btn gs-btn--sm" data-action="mark-sent"${eligible ? "" : " disabled"}>Mark all as sent</button>` +
      `<button type="button" class="gs-btn gs-btn--sm" data-action="goto-signals">Add buyers from Signals</button></div></div>` +
      (d.targets.length
        ? `<div class="gs-chipbar" role="group" aria-label="Show buyers">${chip("all", "All", d.targets.length)}${chip("ready", "Ready", nReady)}${chip("needs", "Needs address", nNeeds)}</div>` +
          `<div class="gs-targets gs-targets--scroll">${rows}</div>`
        : empty("No buyers left", "Add buyers from the Signals tab.")) +
      `</section>`;

    // the message editor, and the preview of it for one buyer, as two tabs of one card
    const message =
      `<section class="gs-card" id="gsMessageCard" aria-label="Message"><div class="gs-card__title">Message${m.dirty ? ' <span class="gs-badge gs-badge--warn">Unsaved changes</span>' : ""}</div>` +
      `<label class="gs-label" for="gsSubject">Subject</label><input id="gsSubject" class="gs-input" data-field="subject" maxlength="200" value="${esc(draft.subject)}">` +
      `<span class="gs-label" id="gsBodyLabel">Message</span>` + richToolbar() +
      `<div id="gsBodyRich" class="gs-rich" contenteditable="true" role="textbox" aria-multiline="true" aria-labelledby="gsBodyLabel" data-field="body-rich" data-html="${esc(draft.html || textToHtml(draft.body))}"></div>` +
      // the plain-text twin of the message: what the page falls back on, and what the checks read, when the editor cannot run
      `<textarea id="gsBody" class="gs-textarea" data-field="body" rows="13" maxlength="6000" hidden>${esc(draft.body)}</textarea>` +
      attachmentsBlock(d.attachments) +
      (unsigned ? `<p class="gs-hint gs-hint--warn">Your company profile has no company or contact name, so this message is not signed. Add them under Company profiles, or type them in above.</p>` : "") +
      `<div class="gs-actions gs-msgtools"><details class="gs-menu gs-menu--fields"><summary class="gs-btn gs-btn--sm" data-tip="Put a merge field into the message at the cursor.">Insert field ▾</summary>` +
      `<div class="gs-chips gs-menu__list" aria-label="Insert a merge field">${mergeChips(m.options && m.options.merge_fields, types)}</div></details>` +
      `<button type="button" class="gs-btn gs-btn--sm" data-action="draft-template">Draft from template</button>` +
      `<button type="button" class="gs-btn gs-btn--sm" data-action="draft-ai"${m.drafting ? " disabled" : ""}>${m.drafting ? "Writing…" : "Write with AI"}${cost ? ` <span class="gs-hint">${cost} ${cost === 1 ? "credit" : "credits"}</span>` : ""}</button>` +
      `<span class="gs-spacer"></span>` +
      `<button type="button" class="gs-btn gs-btn--primary gs-btn--sm" data-action="save-message"${m.dirty ? "" : " disabled"}>Save message</button></div>` +
      `<input id="gsAiNote" class="gs-input gs-input--sm gs-ainote" maxlength="500" value="${esc(m.aiNote || "")}" placeholder="Optional: what should the AI mention? e.g. 24-hour call-out, a local team" aria-label="Anything the AI should mention">` +
      `<div id="gsWarnings">${warningsHtml(m.warnings || d.warnings)}</div></section>`;
    const preview =
      `<section class="gs-card gs-card--preview" id="gsPreviewCard" aria-label="Preview"><div class="gs-card__title">Preview <span class="gs-hint">What this buyer receives</span></div>` +
      `<div class="gs-preview" id="gsPreview" aria-live="polite">${previewHtml(m.preview, d.targets, m.previewTarget)}</div></section>`;
    const tab = m.rightTab === "message" ? "message" : "preview";
    const tabBtn = (id, label) => `<button type="button" role="tab" class="gs-tabbtn${tab === id ? " is-on" : ""}" data-action="builder-tab" data-tab="${id}" aria-selected="${tab === id}">${label}</button>`;
    const side =
      `<section class="gs-card gs-card--tabs"><div class="gs-tabbar" role="tablist" aria-label="Preview or message">${tabBtn("preview", "Preview")}${tabBtn("message", "Message" + (m.dirty ? " •" : ""))}</div>` +
      `<div class="gs-tabpanel" data-panel="preview" role="tabpanel"${tab === "preview" ? "" : " hidden"}>${preview}</div>` +
      `<div class="gs-tabpanel" data-panel="message" role="tabpanel"${tab === "message" ? "" : " hidden"}>${message}</div></section>`;
    const sending =
      `<section class="gs-card gs-card--send" id="gsSendCard" aria-label="Send">` +
      outreachPanel(m.outreach, eligible, m.dirty, missing, files) +
      `<details class="gs-guidance"><summary>Sending guidance</summary>` +
      note("Send only to published, role-based public sector inboxes (such as procurement@), keep a clear way to opt out in every message, and never add a named person's address you found elsewhere. This is guidance, not legal advice. Export the list and send it from your own mail or Mailchimp, or use Send mail to send through Outreach or your own email account; either way, record replies here.", "info") +
      `</details></section>`;
    return emailConnectNotice(m.outreach) + head + notice + `<div class="gs-grid gs-grid--builder"><div class="gs-col">${buyers}</div><aside class="gs-col gs-col--side">${side}${sending}</aside></div>`;
  }

  // ── Send mail through Outreach (one email campaign per buyer, made and approved so Outreach sends it) ──
  function outreachDraftRow(d) {
    const approval = String(d.approval || "").toLowerCase();
    const sentOut = d.outreach_id && (approval === "approved" || approval === "sent_now");
    const state = !d.outreach_id ? badge("Not sent", "warn") : sentOut ? badge(esc(d.status || "Sent"), "good") : badge("Made, not sent", "warn");
    return (
      `<tr><td>${esc(d.buyer)}</td><td>${d.mode === "smtp" ? "Your email account" : `<code>${esc(d.outreach_id || "")}</code>`}</td><td>${state}</td>` +
      `<td>${esc(approval === "sent_now" ? "Sent at once" : d.approval || "–")}</td><td class="gs-num">${d.sent == null ? "–" : esc(d.sent)}</td>` +
      `<td class="gs-num">${d.replies == null ? "–" : esc(d.replies)}</td></tr>` +
      (d.error ? `<tr><td colspan="6" class="gs-hint gs-hint--warn">${esc(d.error)}</td></tr>` : "")
    );
  }

  function outreachResult(r) {
    if (!r) return "";
    const lines = [];
    if (r.sent && r.sent.length && r.mode === "smtp") {
      lines.push(`<li>${esc(plural(r.sent.length, "email"))} sent from <strong>${esc(r.mailbox || "your email account")}</strong>. Replies come back to that mailbox.</li>`);
    } else if (r.sent && r.sent.length) {
      const m = r.mode === "live" ? "live" : "test";
      lines.push(r.route === "single"
        ? `<li>1 email sent now through Outreach (${m} mode)${r.sent[0].from ? ` from <strong>${esc(r.sent[0].from)}</strong>` : ""}. A single email goes out straight away.</li>`
        : `<li>${esc(plural(r.sent.length, "email"))} handed to Outreach to send (${m} mode). Campaigns send inside Outreach's sending hours, so they can take a while.</li>`);
    }
    // the reason for each failure is shown against its buyer in the table below, so it is not repeated here
    if (r.failed && r.failed.length) lines.push(`<li>${esc(plural(r.failed.length, "email"))} could not be sent. The reason is shown against ${r.failed.length === 1 ? "the buyer" : "each buyer"} below.</li>`);
    (r.warnings || []).forEach((w) => lines.push(`<li class="gs-hint gs-hint--warn">${esc(w)}</li>`));
    if (r.stopped && !(r.failed || []).some((f) => f.reason === r.stopped)) lines.push(`<li>Stopped: ${esc(r.stopped)}</li>`);
    if (r.deferred) lines.push(`<li>${esc(plural(r.deferred, "buyer"))} still to send. Choose Send mail again.</li>`);
    (r.skipped || []).forEach((s) => lines.push(`<li class="gs-hint">${esc(s.buyer)} was left out: ${esc(s.reason)}</li>`));
    return lines.length ? `<ul class="gs-warnings gs-outreach__result">${lines.join("")}</ul>` : "";
  }

  // The user's own email accounts whose login has been checked (Connections, "Email account"): the default first. A server that
  // lists only `mail_account` has the one.
  const ownAccounts = (info) => {
    const list = info && Array.isArray(info.mail_accounts) ? info.mail_accounts : info && info.mail_account ? [info.mail_account] : [];
    return list.filter((a) => a && a.verified);
  };
  // The account Send from picks when nothing else is chosen.
  const ownAccount = (info) => {
    const list = ownAccounts(info);
    return list.find((a) => a.is_default) || list[0] || null;
  };
  // A "Send from" value that means one of the user's own accounts: "smtp" (the default one) or "smtp:<id>".
  const isOwnChoice = (choice) => choice === "smtp" || /^smtp:\d+$/.test(String(choice));
  // The account a "Send from" value stands for (null when it is gone or not checked).
  function accountForChoice(info, choice) {
    if (choice === "smtp") return ownAccount(info);
    const m = /^smtp:(\d+)$/.exec(String(choice));
    return m ? ownAccounts(info).find((a) => String(a.id) === m[1]) || null : null;
  }
  // The value for one account. With only one account it stays "smtp", so a choice remembered by this browser keeps working.
  const ownChoice = (info, account) => (ownAccounts(info).length > 1 && account.id != null ? `smtp:${account.id}` : "smtp");

  // What an email goes out through: "smtp" / "smtp:<id>" (one of the user's own email accounts) or "" / a mailbox reference
  // (Outreach). `selected` is what the user picked; with no pick the default own account is used when there is one. Only a choice
  // that can be used is returned: an account that is gone gives way to the default, or to Outreach when there is none.
  function senderChoice(info, selected) {
    const own = ownAccount(info);
    const meantOwn = /^smtp(:|$)/.test(String(selected)); // also a value that names no account: never passed on as an Outreach mailbox
    const pick = meantOwn ? accountForChoice(info, selected) || own : null;
    if (!(info && info.ready)) return own ? ownChoice(info, pick || own) : "";
    if (meantOwn || selected == null) return pick || own ? ownChoice(info, pick || own) : "";
    return String(selected);
  }

  // "Connect Outreach" and "Add an email account" open the Connections window on this page (web/connections.js), where the user
  // signs in to Outreach or sets up an email account. Nothing opens in another tab. When the window is closed after a change
  // the panel that is showing checks again by itself. opts.mail === false leaves the email account button out (social, calls).
  function openConnections(which, label, primary, tip) {
    return `<button type="button" class="gs-btn gs-btn--sm${primary ? " gs-btn--primary" : ""}" data-open-connections="${which}" data-tip="${esc(tip)}">${esc(label)}</button>`;
  }

  function outreachConnectBox(info, opts) {
    const i = info || {};
    const withMail = !(opts && opts.mail === false);
    if (i.ready) {
      return `<div class="gs-actions gs-outreach__connect">${openConnections("outreach", "Connections", false, "Open Connections to see or change your Outreach connection and your email account.")}</div>`;
    }
    const own = withMail && ownAccount(i);
    return (
      `<div class="gs-actions gs-outreach__connect">` +
      openConnections("outreach", "Connect Outreach", !own, "Sign in to Outreach, or create an account, in a window on this page.") +
      (withMail && !own ? openConnections("mail", "Add an email account", false, "Send from your own email account instead of Outreach. You set it up in a window on this page.") : "") +
      `<button type="button" class="gs-btn gs-btn--sm" data-action="outreach-check" data-tip="Ask Outreach again whether you are connected.">Check again</button>` +
      `</div>` +
      `<p class="gs-hint">${withMail ? "Sign in to Outreach, or add your own email account, in the Connections window." : "Sign in to Outreach in the Connections window."} It opens on this page; nothing opens in another tab.</p>`
    );
  }

  // "Sending from a@b.example": the mailbox(es) Outreach says are connected (read from its balance answer).
  function outreachMailboxLine(wallet) {
    const boxes = (wallet && Array.isArray(wallet.mailboxes) ? wallet.mailboxes : []).filter((m) => m && m.email);
    if (!boxes.length) return "";
    const names = boxes.map((m) => esc(m.email)).join(", ");
    const blocked = boxes.filter((m) => m.can_send === false);
    return (
      ` Sending from <strong>${names}</strong>.` +
      (blocked.length
        ? ` <span class="gs-hint--warn">${esc(blocked.map((m) => m.email).join(", "))} cannot send yet. Finish its setup in your Outreach account (warm-up or connection test).</span>`
        : "") +
      (boxes.length > 1 ? ` Pick one below, or let Outreach choose.` : "")
    );
  }

  // "Send from": the user's own email account and/or the mailboxes Outreach has. Shown when there is more than one thing to
  // choose; the chosen value is posted with Send mail ("smtp" is the own account).
  function senderPicker(info, selected0, disabled) {
    const accounts = ownAccounts(info);
    const selected = isOwnChoice(selected0) ? senderChoice(info, selected0) : selected0; // "smtp" means the default when there are several
    const wallet = info && info.ready ? info.wallet : null;
    const boxes = (wallet && Array.isArray(wallet.mailboxes) ? wallet.mailboxes : []).filter((m) => m && m.email);
    const ref = (m) => (m.id ? String(m.id) : String(m.email));
    // Outreach looks a mailbox up by id: one it reports without an id cannot be chosen (it would be refused)
    const noId = (m) => !m.id && !(wallet && wallet.select_by_address);
    const opt = (value, text, off) => `<option value="${esc(value)}"${value === selected && !off ? " selected" : ""}${off ? " disabled" : ""}>${esc(text)}</option>`;
    const opts = [];
    accounts.forEach((a) => opts.push(opt(ownChoice(info, a), `${a.from_email} (your email account${accounts.length > 1 && a.is_default ? ", default" : ""})`, false)));
    if (info && info.ready) {
      if (boxes.length >= 2) {
        opts.push(opt("", "Let Outreach choose", false));
        boxes.forEach((m) => {
          const off = m.can_send === false || noId(m);
          const note = m.can_send === false ? " (cannot send yet)" : noId(m) ? " (Outreach gives no id for this mailbox)" : "";
          opts.push(opt(ref(m), `${m.email}${note}`, off));
        });
      } else {
        opts.push(opt("", boxes.length ? `${boxes[0].email} (through Outreach)` : "Through Outreach", false));
      }
    }
    if (opts.length < 2) return "";
    return (
      `<label class="gs-inline gs-outreach__from">Send from ` +
      `<select data-field="outreach-mailbox"${disabled ? " disabled" : ""}>${opts.join("")}</select></label>` +
      (boxes.length >= 2 && boxes.some(noId) ? `<p class="gs-hint">Outreach does not give an id for some mailboxes yet, so only "Let Outreach choose" works for them. It sends from its default mailbox.</p>` : "")
    );
  }

  // What Outreach's own record of a sent email says about the mailbox (read by Refresh status): the id to use for
  // choosing that mailbox, or the field names it does report when there is none.
  function outreachSeenNote(seen) {
    const items = (Array.isArray(seen) ? seen : []).filter((s) => s && (s.mailbox || s.mailbox_id || (s.fields || []).length));
    if (!items.length) return "";
    return (
      `<ul class="gs-warnings gs-outreach__seen">` +
      items.map((s) => s.mailbox_id
        ? `<li>Outreach says the email to ${esc(s.buyer)} went from <strong>${esc(s.mailbox || "a mailbox")}</strong>, mailbox id <code>${esc(s.mailbox_id)}</code>.</li>`
        : `<li class="gs-hint">The email to ${esc(s.buyer)} went from <strong>${esc(s.mailbox || "a mailbox")}</strong>. Outreach's record has no mailbox id (fields: ${esc((s.fields || []).join(", ") || "none")}).</li>`
      ).join("") +
      `</ul>`
    );
  }

  function outreachPanel(o, eligible, dirty, noEmail, files) {
    const head = `<div class="gs-card__title gs-card__title--sub">Send to buyers</div>`;
    if (!o || o.status === "idle" || (o.status === "loading" && !o.info)) {
      return head + `<p class="gs-hint" id="gsOutreach">Checking how you can send…</p>`;
    }
    const info = o.info || {};
    const own = ownAccount(info);
    const drafts = o.drafts || [];
    const table = drafts.length
      ? `<div class="gs-tablewrap"><table class="gs-table gs-table--outreach"><thead><tr><th>Buyer</th><th>Sent through</th><th>State</th><th>Approval</th><th class="gs-num">Sent</th><th class="gs-num">Replies</th></tr></thead><tbody>${drafts.map(outreachDraftRow).join("")}</tbody></table></div>`
      : "";
    if (!info.ready && !own) {
      return (
        head +
        `<div id="gsOutreach"><p class="gs-hint gs-hint--warn">${esc(info.problem || o.error || "Outreach is not available.")}</p>${outreachConnectBox(info)}${table}</div>`
      );
    }
    const choice = senderChoice(info, o.mailbox);
    const sender = isOwnChoice(choice) ? accountForChoice(info, choice) : null; // the own account chosen to send from
    const viaOwn = !!sender;
    const cap = viaOwn ? Math.min(sender.daily_cap || 25, info.max_per_push || 25) : info.max_per_push || 25;
    const canPush = eligible > 0 && !dirty && !o.pushing && (!files || viaOwn);
    let status;
    let how;
    if (viaOwn) {
      status = `${badge("Your email account", "good")} Sending from <strong>${esc(sender.from_email)}</strong>. ${esc(sender.sent_today || 0)} of ${esc(sender.daily_cap)} sent in the last 24 hours. ` +
        `Replies go to ${esc(sender.reply_to || sender.from_email)}, so read them there.`;
      how = `Each buyer gets their own email, filled in for them and sent from ${esc(sender.from_email)} (up to ${esc(cap)} at a time, within your daily limit). A buyer is never emailed twice.`;
    } else {
      const mode = info.mode === "live" ? badge("Live key", "warn") : badge("Test mode", "good");
      const credits = info.wallet && info.wallet.credits != null ? ` · ${esc(info.wallet.credits)} credits in Outreach` : "";
      status = `${mode} Connected${credits}.${outreachMailboxLine(info.wallet)} ` +
        (info.mode === "live"
          ? "Live key: Send mail emails the buyers from your connected Outreach mailbox. One buyer is sent straight away; several are sent as a campaign inside Outreach's sending hours."
          : "Test key: Outreach pretends everything, charges nothing and sends nothing real.");
      how = `Each buyer gets their own email, filled in for them and sent from your connected Outreach mailbox (up to ${esc(cap)} at a time). A buyer is never emailed twice, and Outreach's daily limits and warm-up decide when each one actually goes.`;
    }
    const noMailbox = info.ready && !viaOwn && !own && !((info.wallet && info.wallet.mailboxes) || []).length;
    return (
      head +
      `<div id="gsOutreach"><p class="gs-hint">${status}</p>` +
      (noMailbox ? `<p class="gs-hint gs-hint--warn">Your Outreach account has no email account yet, so Outreach has nothing to send from. Add your own email account in Connections to send from it.</p>` : "") +
      senderPicker(info, choice, o.pushing) +
      `<label class="gs-inline"><input type="checkbox" data-field="outreach-personal"${o.personal ? " checked" : ""}> Include addresses that look like a named person's</label>` +
      `<div class="gs-actions"><button type="button" class="gs-btn gs-btn--primary gs-btn--sm" data-action="outreach-send"${canPush ? "" : " disabled"}>${o.pushing ? "Sending…" : "Send mail"}</button>` +
      `<button type="button" class="gs-btn gs-btn--sm" data-action="outreach-refresh"${drafts.some((d) => d.outreach_id && d.mode !== "smtp") && !o.refreshing ? "" : " disabled"}>${o.refreshing ? "Checking…" : "Refresh status"}</button>` +
      `</div>` +
      outreachConnectBox(info) +
      (dirty ? `<p class="gs-hint gs-hint--warn">Save the message first: the saved message is what gets sent.</p>` : "") +
      (files && !viaOwn ? `<p class="gs-hint gs-hint--warn">This message has attached files, and Outreach cannot carry files. Choose your own email account under Send from, or remove the files.</p>` : "") +
      (noEmail
        ? `<p class="gs-hint gs-hint--warn">${esc(plural(noEmail, "buyer"))} ${noEmail === 1 ? "has" : "have"} no email address yet and will be left out. ` +
          `<button type="button" class="gs-link" data-action="focus-email">Add addresses</button> in the Buyers table above.</p>`
        : "") +
      `<p class="gs-hint">${how}</p>` +
      outreachResult(o.last) +
      outreachSeenNote(o.seen) +
      table +
      `</div>`
    );
  }

  // ── performance ───────────────────────────────────────────────────────────────────────────────
  function funnelBars(funnel) {
    const max = Math.max.apply(null, (funnel || []).map((f) => f.value).concat([1]));
    return (
      '<div class="gs-funnel">' +
      (funnel || [])
        .map((f) => `<div class="gs-funnel__row"><span class="gs-funnel__label">${esc(f.label)}</span><span class="gs-funnel__track"><i style="width:${f.value ? Math.max(3, Math.round((100 * f.value) / max)) : 0}%"></i></span><span class="gs-funnel__value">${esc(f.value)}</span></div>`)
        .join("") +
      "</div>"
    );
  }

  function stat(label, value, hint) {
    return `<div class="gs-stat"><div class="gs-stat__label">${esc(label)}</div><div class="gs-stat__value">${value}</div>${hint ? `<div class="gs-stat__hint">${esc(hint)}</div>` : ""}</div>`;
  }

  function performancePane(m) {
    if (m.status === "loading" && !m.data) return loading("Loading results…");
    if (m.status === "error") return errorBox(m.error, "reload-performance");
    const d = m.data;
    if (!d) return loading("Loading results…");
    const t = d.tiles;
    const windows = [["30", "Last 30 days"], ["90", "Last 90 days"], ["365", "Last 12 months"], ["all", "All time"]];
    const picker = `<label class="gs-inline">Show <select class="gs-select gs-select--sm" data-field="performance-days" aria-label="Time window">${windows
      .map(([v, l]) => `<option value="${v}"${v === d.window ? " selected" : ""}>${l}</option>`)
      .join("")}</select></label>`;
    if (!d.campaigns.length) {
      return `<div class="gs-toolbar">${picker}</div>` + empty("Nothing to report yet", "Results appear once you have a campaign and record messages as sent. Replies, meetings and Pipeline tenders build the picture from there.");
    }
    return (
      `<div class="gs-toolbar">${picker}</div>` +
      `<div class="gs-stats">${stat("Campaigns run", esc(t.campaigns_run), plural(t.sent, "message") + " recorded as sent")}` +
      stat("Buyers reached", esc(t.buyers_reached)) +
      stat("Reply rate", t.reply_rate == null ? "–" : esc(Math.round(t.reply_rate * 100)) + "%", t.sent ? `${t.replied} of ${t.sent} replied` : "No messages sent yet") +
      stat("Pipeline value influenced", t.pipeline_value == null ? "–" : esc(fmtMoneyShort(t.pipeline_value)),
        `${plural(t.tenders_tracked, "tender")} tracked${t.tenders_tracked ? `, ${t.tenders_with_value} with a value` : ""}`) +
      "</div>" +
      `<div class="gs-card"><div class="gs-card__title">Response funnel</div>${funnelBars(d.funnel)}</div>` +
      `<div class="gs-card"><div class="gs-card__title">Campaign → Pipeline outcomes</div><div class="gs-tablewrap"><table class="gs-table"><thead><tr><th>Campaign</th><th class="gs-num">Sent</th><th class="gs-num">Replied</th><th class="gs-num">Meetings</th><th>Tracked in Pipeline</th></tr></thead><tbody>` +
      d.campaigns
        .map(
          (c) =>
            `<tr><td><button type="button" class="gs-link gs-link--strong" data-action="open-campaign" data-id="${esc(c.id)}">${esc(c.name)}</button></td>` +
            `<td class="gs-num">${esc(c.sent)}</td><td class="gs-num">${esc(c.replied)}</td><td class="gs-num">${esc(c.meetings)}</td>` +
            `<td>${c.tenders ? badge(esc(plural(c.tenders, "tender")), "good") : c.sent ? badge("None yet", "muted") : badge("Not sent", "muted")}</td></tr>`
        )
        .join("") +
      "</tbody></table></div></div>" +
      (d.notes ? `<details class="gs-notes"><summary>How these figures are worked out</summary><ul>${Object.keys(d.notes).map((k) => `<li>${esc(d.notes[k])}</li>`).join("")}</ul></details>` : "")
    );
  }

  // Shown when the browser will not let the page copy: the text is there to select by hand.
  function mailchimpBox(text) {
    return (
      `<label class="gs-label" for="gsMcText">Message with Mailchimp merge tags: select all and copy</label>` +
      `<textarea id="gsMcText" class="gs-textarea gs-textarea--short" readonly rows="8">${esc(text)}</textarea>` +
      `<p class="gs-hint">Paste it into your Mailchimp campaign. Import the audience CSV first so the *|TAGS|* match its columns.</p>`
    );
  }

  const api = {
    esc, safeUrl, fmtDate, fmtMoney, fmtMoneyShort, plural, inDays, parseServerTime, timeAgo, fitBand, filenameFromDisposition, insertAt,
    hasCategory, defaultFilters, filtersBody, signalsQuery, campaignKeys, safeHtml, textToHtml, fillRich, richToolbar, attachmentsBlock, companySelect,
    signalCard, signalsPane, selectionBar, campaignsPane, builderPane, connectionChips, emailConnectNotice, channelConnectNotice,previewHtml, warningsHtml, mailchimpBox, targetRow, mergeChips, performancePane,
    badge, note, empty, loading, errorBox,
    outreachPanel, outreachConnectBox, openConnections, outreachMailboxLine, senderPicker, senderChoice, ownAccount, ownAccounts, isOwnChoice, accountForChoice, outreachSeenNote, outreachDraftRow, outreachResult,
    suppressionsBlock, fitParts, funnelBars, countsLine, TYPES, TARGET_STATUS, CAMPAIGN_STATUS,
  };
  root.GrowthView = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
