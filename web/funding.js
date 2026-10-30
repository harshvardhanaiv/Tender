/* Funding: open government grants from Find a Grant (GOV.UK). Data comes from /api/funding; each card carries an
   eligibility guide (likely / check / unlikely) worked out on the server from the chosen company profile. */
(function () {
  "use strict";

  const state = { initialised: false, page: 1, timer: null, controller: null, profileId: "", profiles: null, touchedApplicant: false, items: {} };
  const byId = (id) => document.getElementById(id);
  const LS_KEY = "tf_funding_profile";

  const FIT = {
    likely: { cls: "likely", label: "Likely eligible", line: "why", head: "Why" },
    check: { cls: "check", label: "Check eligibility", line: "check", head: "Check" },
    unlikely: { cls: "unlikely", label: "Unlikely to qualify", line: "blocker", head: "Blocker" },
  };

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  }

  function money(n) {
    if (n == null) return "";
    if (n >= 1e6) return "£" + (n / 1e6).toFixed(n % 1e6 ? 1 : 0).replace(/\.0$/, "") + "m";
    if (n >= 1e4) return "£" + (n / 1e3).toFixed(n % 1e3 ? 1 : 0).replace(/\.0$/, "") + "k";
    return "£" + Math.round(n).toLocaleString("en-GB");
  }

  function awardText(g) {
    if (g.min_award != null && g.max_award != null && g.min_award !== g.max_award) return money(g.min_award) + " to " + money(g.max_award);
    const one = g.max_award != null ? g.max_award : g.min_award;
    if (one != null) return (g.max_award != null && g.min_award == null ? "Up to " : g.max_award == null ? "From " : "") + money(one);
    return g.total_award != null ? "Pot " + money(g.total_award) : "Award size not stated";
  }

  function dayText(iso) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || "");
    if (!m) return "";
    const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    return Number(m[3]) + " " + months[Number(m[2]) - 1] + " " + m[1];
  }

  // "11:59pm", "11:00am", "12:00pm" from the UK local "YYYY-MM-DDTHH:MM" the server stores.
  function timeText(at) {
    const m = /T(\d{2}):(\d{2})$/.exec(at || "");
    if (!m) return "";
    const h = Number(m[1]);
    return (h % 12 || 12) + ":" + m[2] + (h >= 12 ? "pm" : "am");
  }

  function closingHtml(g) {
    if (g.ongoing) return '<span class="funding-tag">Rolling, no fixed deadline</span>';
    if (!g.close_date) return '<span class="funding-tag">No closing date</span>';
    const soon = g.days_left != null && g.days_left <= 14;
    const when = g.days_left != null && g.days_left >= 0 ? (g.days_left === 0 ? "closes today" : g.days_left + (g.days_left === 1 ? " day left" : " days left")) : "";
    return '<span class="funding-tag' + (soon ? " funding-tag--soon" : "") + '">Closes ' + esc(dayText(g.close_date)) + (timeText(g.close_at) ? ", " + esc(timeText(g.close_at)) : "") + (when ? " · " + esc(when) : "") + "</span>";
  }

  function whereText(g) {
    const locs = g.locations || [];
    return locs.includes("National") ? "UK-wide" : locs.join(", ");
  }

  function fitPill(g) {
    const f = g.fit && FIT[g.fit.verdict];
    return f ? '<span class="funding-fit funding-fit--' + f.cls + '">' + f.label + "</span>" : "";
  }

  function fitLine(g) {
    const f = g.fit && FIT[g.fit.verdict];
    const text = f && g.fit[f.line];
    return text ? '<p class="funding-card__line"><strong>' + f.head + ":</strong> " + esc(text) + "</p>" : "";
  }

  // The drawer lists every blocker, check and reason, not just the first.
  function fitDetail(g) {
    const f = g.fit && FIT[g.fit.verdict];
    if (!f) return "";
    const rows = [];
    (g.fit.blockers || []).forEach((t) => rows.push(["Blocker", t, "no"]));
    (g.fit.checks || []).forEach((t) => rows.push(["Check", t, "maybe"]));
    (g.fit.reasons || []).forEach((t) => rows.push(["Fits", t, "yes"]));
    if (!rows.length) return fitLine(g);
    return '<ul class="funding-fitlist" aria-label="Why this fit">' + rows.map((r) => '<li class="funding-fitlist__' + r[2] + '"><strong>' + r[0] + ":</strong> " + esc(r[1]) + "</li>").join("") + "</ul>";
  }

  function cardHtml(g) {
    const where = whereText(g);
    return (
      '<article class="funding-card" data-fit="' + esc((g.fit && g.fit.verdict) || "") + '">' +
      '<div class="funding-card__top"><h3 class="funding-card__name">' + esc(g.name) + "</h3>" +
      '<span class="funding-card__award">' + esc(awardText(g)) + "</span></div>" +
      '<div class="funding-card__funder">' + esc(g.funder || "Funder not stated") + "</div>" +
      '<div class="funding-card__tags">' + fitPill(g) + closingHtml(g) + (where ? '<span class="funding-tag">' + esc(where) + "</span>" : "") + "</div>" +
      fitLine(g) +
      '<div class="funding-card__actions">' +
      '<button type="button" class="funding-btn funding-btn--primary" data-details="' + esc(g.label || "") + '" title="Eligibility, summary, application help and AI tools for this grant">View details</button>' +
      '<button type="button" class="funding-btn' + (g.saved ? " is-saved" : "") + '" data-save="' + esc(g.label || "") + '" aria-pressed="' + (g.saved ? "true" : "false") + '">' + (g.saved ? "Saved" : "Save") + "</button>" +
      "</div></article>"
    );
  }

  function pagerHtml(data) {
    if (!data || data.pages <= 1) return "";
    return (
      '<button type="button" class="funding-btn" data-page="' + (data.page - 1) + '"' + (data.page <= 1 ? " disabled" : "") + ">Previous</button>" +
      '<span class="funding-pager__at">Page ' + data.page + " of " + data.pages + "</span>" +
      '<button type="button" class="funding-btn" data-page="' + (data.page + 1) + '"' + (data.page >= data.pages ? " disabled" : "") + ">Next</button>"
    );
  }

  function metaText(data) {
    if (!data.imported) return "Grants have not been loaded yet. An administrator can run python -m tender_app.fag_import.";
    if (!data.total) return "No open grants match these filters.";
    const of = data.open_total != null && data.open_total !== data.total ? data.total + " of " + data.open_total + " open grants" : data.total + (data.total === 1 ? " open grant" : " open grants");
    const likely = data.has_profile && data.likely ? " · " + data.likely + " likely eligible" : "";
    const profile = data.has_profile ? "" : " · Add a company profile to see how well each grant fits";
    const closed = data.closed_today ? " · " + data.closed_today + (data.closed_today === 1 ? " grant closed today and is hidden" : " grants closed today and are hidden") : "";
    return of + likely + profile + closed;
  }

  // The filters that are set, each removable: [label, how to clear it].
  function activeChips() {
    const chips = [];
    const sel = (id) => byId(id);
    const text = (id) => { const el = sel(id); return el && el.options ? el.options[el.selectedIndex].text.replace(/ /g, "").trim() : ""; };
    if ((sel("txtFundingSearch")?.value || "").trim()) chips.push(['"' + sel("txtFundingSearch").value.trim() + '"', () => { sel("txtFundingSearch").value = ""; }]);
    if (sel("selFundingApplicant")?.value) chips.push([text("selFundingApplicant"), () => { sel("selFundingApplicant").value = ""; state.touchedApplicant = true; }]);
    if (sel("selFundingLocation")?.value) chips.push([text("selFundingLocation"), () => { sel("selFundingLocation").value = ""; }]);
    if (sel("selFundingAward")?.value) chips.push([text("selFundingAward"), () => { sel("selFundingAward").value = ""; }]);
    [["chkFundingHideUnlikely", "Hiding unlikely"], ["chkFundingSoon", "Closing within 30 days"], ["chkFundingSaved", "Saved only"]].forEach(([id, label]) => {
      if (sel(id)?.checked) chips.push([label, () => { sel(id).checked = false; }]);
    });
    return chips;
  }

  function renderChips() {
    const box = byId("fundingChips");
    if (!box) return;
    state.chips = activeChips();
    box.innerHTML = state.chips.map((c, i) => '<span class="funding-chip">' + esc(c[0]) + ' <button type="button" data-chip="' + i + '" aria-label="Remove filter ' + esc(c[0]) + '">×</button></span>').join("");
  }

  function params() {
    const p = new URLSearchParams();
    const set = (key, id) => { const v = (byId(id)?.value || "").trim(); if (v) p.set(key, v); };
    set("q", "txtFundingSearch");
    set("where", "selFundingLocation");
    const band = (byId("selFundingAward")?.value || "").split("-");
    if (band.length === 2) { if (band[0]) p.set("need_min", band[0]); if (band[1]) p.set("need_max", band[1]); }
    set("sort", "selFundingSort");
    if (state.touchedApplicant) p.set("applicant", byId("selFundingApplicant")?.value || ""); // untouched: the server uses the profile's own type
    if (!byId("chkFundingUkWide")?.checked) p.set("include_uk_wide", "0");
    if (byId("chkFundingSoon")?.checked) p.set("closing_soon", "1");
    if (byId("chkFundingHideUnlikely")?.checked) p.set("hide_unlikely", "1");
    if (byId("chkFundingSaved")?.checked) p.set("saved_only", "1");
    if (state.profileId) p.set("profile_id", state.profileId);
    p.set("page", String(state.page));
    return p;
  }

  async function load() {
    const list = byId("fundingList");
    if (!list) return;
    state.controller?.abort();
    state.controller = new AbortController();
    list.setAttribute("aria-busy", "true");
    try {
      const resp = await fetch("/api/funding?" + params().toString(), { credentials: "same-origin", signal: state.controller.signal });
      if (!resp.ok) throw new Error("status " + resp.status);
      const data = await resp.json();
      if (!(data.items || []).length && state.page > 1 && data.pages >= 1 && state.page > data.pages) { state.page = data.pages; return load(); }
      if (data.applied && !state.touchedApplicant && byId("selFundingApplicant")) byId("selFundingApplicant").value = data.applied.applicant;
      byId("fundingMeta").textContent = metaText(data);
      state.items = {};
      (data.items || []).forEach((g) => { if (g.label) state.items[g.label] = g; });
      list.innerHTML = (data.items || []).map(cardHtml).join("");
      byId("fundingPager").innerHTML = pagerHtml(data);
      renderChips();
      renderEmpty(data);
      syncUrl();
      updateFiltersButton();
      const q = (byId("txtFundingSearch")?.value || "").trim();
      if (q && q !== state.lastTracked) { state.lastTracked = q; track("funding_search", { q: q.slice(0, 60), results: data.total }); }
      const refreshed = byId("fundingRefreshed");
      if (refreshed) refreshed.textContent = data.refreshed ? "Last refreshed " + dayText(data.refreshed.slice(0, 10)) + "." : "";
    } catch (err) {
      if (err && err.name === "AbortError") return;
      byId("fundingMeta").textContent = "Could not load grants. Try again in a moment.";
      list.innerHTML = "";
    } finally {
      list.removeAttribute("aria-busy");
    }
  }

  function reload(delay) {
    state.page = 1;
    clearTimeout(state.timer);
    state.timer = setTimeout(load, delay || 0);
  }

  // Nothing matched: say which filters are set and let each be dropped, instead of a dead end.
  // Below 640px the filters sit behind a "Filters (n)" button instead of filling the screen above the grants.
  function updateFiltersButton() {
    const btn = byId("btnFundingFilters");
    if (btn) btn.textContent = "Filters" + ((state.chips || []).length ? " (" + state.chips.length + ")" : "");
  }

  function renderEmpty(data) {
    const box = byId("fundingEmpty");
    if (!box) return;
    if (!data.imported || data.total) { box.classList.add("hidden"); box.innerHTML = ""; return; }
    const chips = state.chips || [];
    box.innerHTML = '<p class="funding-empty__t">' + esc(metaText(data)) + "</p>" +
      (chips.length ? '<p class="funding-empty__s">Try removing a filter:</p><div class="funding-empty__b">' +
        chips.map((c, i) => '<button type="button" class="funding-btn" data-empty-chip="' + i + '">Remove ' + esc(c[0]) + "</button>").join("") + "</div>" : "") +
      '<button type="button" class="funding-btn funding-btn--primary" data-empty-clear="1">Clear all filters</button>';
    box.classList.remove("hidden");
  }

  // The page's state lives in the address (/funding?where=Scotland&q=ai, /funding/<grant>) so a reload, a bookmark or a
  // shared link returns to it.
  const CHECKS = { uk: ["chkFundingUkWide", "0"], soon: ["chkFundingSoon", "1"], hide: ["chkFundingHideUnlikely", "1"], saved: ["chkFundingSaved", "1"] };

  function urlState() {
    const p = new URLSearchParams();
    const val = (id) => (byId(id)?.value || "").trim();
    if (val("txtFundingSearch")) p.set("q", val("txtFundingSearch"));
    if (state.touchedApplicant) p.set("applicant", val("selFundingApplicant") || "any");
    if (val("selFundingLocation")) p.set("where", val("selFundingLocation"));
    if (val("selFundingAward")) p.set("size", val("selFundingAward"));
    if (val("selFundingSort") && val("selFundingSort") !== "relevance") p.set("sort", val("selFundingSort"));
    if (!byId("chkFundingUkWide")?.checked) p.set("uk", "0");
    ["soon", "hide", "saved"].forEach((k) => { if (byId(CHECKS[k][0])?.checked) p.set(k, "1"); });
    if (state.profileId) p.set("profile", state.profileId);
    return p;
  }

  function syncUrl() {
    if (state.restoring) return;
    const qs = urlState().toString();
    const path = "/funding" + (state.detail ? "/" + encodeURIComponent(state.detail) : "") + (qs ? "?" + qs : "");
    try { history.replaceState(null, "", path); } catch (err) { /* no history */ }
  }

  // The grant and filters in the address: /funding/<grant>?where=Scotland, or the older #funding/<grant>?where=Scotland.
  function parseHash() {
    const p = /^\/funding(?:\/([^/?#]+))?\/?$/.exec(location.pathname || "");
    if (p) return { label: p[1] ? decodeURIComponent(p[1]) : "", params: new URLSearchParams(location.search || "") };
    const m = /^#funding(?:\/([^?]*))?(?:\?(.*))?$/.exec(location.hash || "");
    if (!m) return null;
    return { label: m[1] ? decodeURIComponent(m[1]) : "", params: new URLSearchParams(m[2] || "") };
  }

  function applyParams(p) {
    const set = (id, v) => { const el = byId(id); if (el && v != null) el.value = v; };
    set("txtFundingSearch", p.get("q") || "");
    if (p.has("applicant")) { state.touchedApplicant = true; set("selFundingApplicant", p.get("applicant") === "any" ? "" : p.get("applicant")); }
    set("selFundingLocation", p.get("where") || "");
    set("selFundingAward", p.get("size") || "");
    set("selFundingSort", p.get("sort") || "relevance");
    if (byId("chkFundingUkWide")) byId("chkFundingUkWide").checked = p.get("uk") !== "0";
    ["soon", "hide", "saved"].forEach((k) => { if (byId(CHECKS[k][0])) byId(CHECKS[k][0]).checked = p.get(k) === "1"; });
  }

  function profileLabel(p) {
    let meta = {};
    try { meta = JSON.parse(p.meta_json || "{}"); } catch (e) { meta = {}; }
    return [p.name || meta.org_name || "Untitled profile", meta.city].filter(Boolean).join(" · ");
  }

  async function loadProfiles() {
    const sel = byId("selFundingProfile");
    if (!sel) return;
    try {
      const resp = await fetch("/api/company-profiles", { credentials: "same-origin" });
      const data = resp.ok ? await resp.json() : null;
      state.profiles = data && data.profiles ? data.profiles : [];
    } catch (err) { state.profiles = []; }
    if (!state.profiles.length) {
      sel.innerHTML = '<option value="">No company profile yet</option>';
      state.profileId = "";
      return;
    }
    let want = state.wantProfile || "";
    try { want = want || localStorage.getItem(LS_KEY) || localStorage.getItem("tf_active_profile") || ""; } catch (err) { want = ""; }
    const chosen = state.profiles.find((p) => String(p.id) === want) || state.profiles.find((p) => p.is_default) || state.profiles[0];
    state.profileId = String(chosen.id);
    sel.innerHTML = state.profiles.map((p) => '<option value="' + esc(p.id) + '">' + esc(profileLabel(p)) + "</option>").join("");
    sel.value = state.profileId;
  }

  function clearFilters() {
    ["txtFundingSearch", "selFundingLocation", "selFundingAward"].forEach((id) => { if (byId(id)) byId(id).value = ""; });
    if (byId("selFundingApplicant")) byId("selFundingApplicant").value = "";
    state.touchedApplicant = true;
    if (byId("chkFundingUkWide")) byId("chkFundingUkWide").checked = true;
    ["chkFundingHideUnlikely", "chkFundingSoon", "chkFundingSaved"].forEach((id) => { if (byId(id)) byId(id).checked = false; });
    reload(0);
  }

  async function toggleSave(label, btn) {
    const g = state.items[label];
    if (!g) return;
    const next = !g.saved;
    g.saved = next;
    track("funding_saved", { grant: label, saved: next });
    btn.classList.toggle("is-saved", next);
    btn.textContent = next ? "Saved" : "Save";
    btn.setAttribute("aria-pressed", next ? "true" : "false");
    try {
      const resp = await fetch("/api/funding/save", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ label, saved: next }) });
      if (!resp.ok) throw new Error("status " + resp.status);
      if (window.TFGrantsBoard) window.TFGrantsBoard.reset();  // the Pipeline board reads the list again next time
      if (typeof window._showToast === "function") window._showToast(next ? "Saved to Pipeline › Grants. We remind you 7 days before it closes." : "Removed from Pipeline › Grants.", "success");
      if (!next && byId("chkFundingSaved")?.checked) load();
    } catch (err) {
      g.saved = !next;
      btn.classList.toggle("is-saved", !next);
      btn.textContent = !next ? "Saved" : "Save";
      btn.setAttribute("aria-pressed", !next ? "true" : "false");
    }
  }

  const TITLE = "Funding · TenderFlow";

  // Usage events (see funding_track.EVENTS): a few words each, never the text of a grant or profile.
  function track(event, detail) {
    try {
      fetch("/api/funding/event", { method: "POST", credentials: "same-origin", keepalive: true, headers: { "Content-Type": "application/json" }, body: JSON.stringify({ event, detail: detail || {} }) }).catch(() => {});
    } catch (err) { /* usage events never get in the way */ }
  }

  // ── Weekly email of new grants that fit the profile (off until switched on) ──
  async function loadWeekly() {
    const box = byId("chkFundingWeekly");
    if (!box) return;
    try {
      const resp = await fetch("/api/funding/alerts", { credentials: "same-origin" });
      if (resp.ok) box.checked = !!(await resp.json()).enabled;
    } catch (err) { /* the box stays off */ }
  }

  async function setWeekly(enabled) {
    const note = byId("fundingWeeklyNote");
    try {
      const resp = await fetch("/api/funding/alerts", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled }) });
      if (!resp.ok) throw new Error("status " + resp.status);
      if (note) note.textContent = enabled ? "On. The first email comes in a week, with grants that appear from now on." : "Off. You will not get the weekly email.";
    } catch (err) {
      if (byId("chkFundingWeekly")) byId("chkFundingWeekly").checked = !enabled;
      if (note) note.textContent = "Could not change that. Try again.";
    }
  }

  async function openGrant(label) {
    await openFunding();
    if (label) await openDetail(label);
  }

  // ── Grant detail drawer: everything the service says about one grant, without leaving the app ──
  function paragraphs(text) {
    return String(text || "").split("\n").map((t) => t.trim()).filter(Boolean).map((t) => "<p>" + esc(t) + "</p>").join("");
  }

  function detailHtml(g) {
    const facts = [
      ["Award", awardText(g)],
      ["Closes", g.ongoing ? "Rolling, no fixed deadline" : g.close_date ? dayText(g.close_date) + (timeText(g.close_at) ? ", " + timeText(g.close_at) : "") + (g.days_left != null && g.days_left >= 0 ? " (" + (g.days_left === 0 ? "today" : g.days_left + (g.days_left === 1 ? " day left" : " days left")) + ")" : "") : "No closing date"],
      ["Opened", g.open_date ? dayText(g.open_date) : ""],
      ["Where", whereText(g)],
      ["Who can apply", (g.applicants || []).join(", ")],
      ["Total pot", g.total_award != null ? money(g.total_award) : ""],
    ].filter((f) => f[1]);
    const apply = g.apply_url || g.url;
    return (
      '<div class="funding-drawer__head"><div><h2 id="fundingDrawerTitle" tabindex="-1">' + esc(g.name) + "</h2>" +
      '<div class="funding-card__funder">' + esc(g.funder || "Funder not stated") + "</div></div>" +
      '<button type="button" class="profile-header__close" data-drawer-close aria-label="Close details">×</button></div>' +
      '<div class="funding-drawer__body">' +
      '<div class="funding-card__tags">' + fitPill(g) + closingHtml(g) + "</div>" + fitDetail(g) +
      '<dl class="funding-facts">' + facts.map((f) => "<div><dt>" + esc(f[0]) + "</dt><dd>" + esc(f[1]) + "</dd></div>").join("") + "</dl>" +
      (g.summary ? "<h3>About this grant</h3>" + paragraphs(g.summary) : "") +
      (g.eligibility ? "<h3>Who can apply</h3>" + paragraphs(g.eligibility) : '<p class="funding-card__line">The service does not list eligibility details for this grant. Check the full page.</p>') +
      "</div>" +
      '<div class="funding-drawer__foot">' +
      '<a class="funding-btn funding-btn--primary" href="' + esc(apply) + '" target="_blank" rel="noopener noreferrer">Apply on GOV.UK ↗</a>' +
      (g.apply_url && g.url ? '<a class="funding-btn" href="' + esc(g.url) + '" target="_blank" rel="noopener noreferrer">Full page ↗</a>' : "") +
      (g.label ? '<button type="button" class="funding-btn" data-drawer-ai="' + esc(g.label) + '">AI tools</button>' : "") +
      (g.label ? '<button type="button" class="funding-btn' + (g.saved ? " is-saved" : "") + '" data-drawer-save="' + esc(g.label) + '" aria-pressed="' + (g.saved ? "true" : "false") + '">' + (g.saved ? "Saved" : "Save") + "</button>" : "") +
      "</div>"
    );
  }

  async function openDetail(label, opener) {
    const box = byId("fundingDrawer");
    if (!box || !label) return;
    let g = state.items[label];
    if (!g) {  // a link to a grant that is not on this page of results
      try {
        const resp = await fetch("/api/funding/item/" + encodeURIComponent(label) + (state.profileId ? "?profile_id=" + encodeURIComponent(state.profileId) : ""), { credentials: "same-origin" });
        g = resp.ok ? (await resp.json()).item : null;
      } catch (err) { g = null; }
      if (g) state.items[label] = g;
    }
    if (!g) { state.detail = ""; syncUrl(); return; }
    state.detail = label;
    track("funding_card_opened", { grant: label, fit: (g.fit && g.fit.verdict) || "" });
    state.opener = opener || document.activeElement;
    box.innerHTML = detailHtml(g);
    box.classList.remove("hidden");
    box.focus();
    syncUrl();
  }

  function closeDetail() {
    const box = byId("fundingDrawer");
    if (!box || box.classList.contains("hidden")) return;
    box.classList.add("hidden");
    state.detail = "";
    syncUrl();
    if (state.opener && state.opener.isConnected) state.opener.focus();
  }

  async function openFunding() {
    byId("fundingModal")?.classList.remove("hidden");
    if (document.title !== TITLE) state.prevTitle = document.title;
    document.title = TITLE;
    if (state.initialised) {
      const link = parseHash();  // opened from a link (for example the Pipeline board) onto one grant
      if (link && link.label) openDetail(link.label); else syncUrl();
      return;
    }
    state.initialised = true;
    const fromUrl = parseHash();
    state.restoring = true;
    if (fromUrl) state.wantProfile = fromUrl.params.get("profile") || "";
    await loadProfiles();
    if (fromUrl) applyParams(fromUrl.params);
    state.restoring = false;
    await load();
    loadWeekly();
    if (fromUrl && fromUrl.label) openDetail(fromUrl.label);
  }

  function closeFunding() {
    closeDetail();
    byId("fundingModal")?.classList.add("hidden");
    if (state.prevTitle) document.title = state.prevTitle;
    try { if (parseHash()) history.replaceState(null, "", "/"); } catch (err) { /* no history */ }
  }

  function bind() {
    byId("btnFunding")?.addEventListener("click", openFunding);
    byId("fundingClose")?.addEventListener("click", closeFunding);
    byId("fundingFilterBar")?.addEventListener("submit", (e) => { e.preventDefault(); reload(0); });
    // The filters sit behind the "Filters" button at every width; the grants use the full width until it is pressed.
    const setFiltersOpen = (open) => {
      byId("fundingFilterBar")?.classList.toggle("is-open", open);
      document.querySelector("#fundingModal .funding-layout")?.classList.toggle("has-filters-open", open);
      byId("btnFundingFilters")?.setAttribute("aria-expanded", open ? "true" : "false");
      try { localStorage.setItem("tf_funding_filters_open", open ? "1" : "0"); } catch (_) { /* private mode */ }
    };
    byId("btnFundingFilters")?.addEventListener("click", () => setFiltersOpen(true));
    byId("btnFundingFiltersHide")?.addEventListener("click", () => setFiltersOpen(false));
    // Open on a wide screen, hidden on a small one, until the reader chooses; the choice is remembered.
    let wantOpen = window.innerWidth > 900;
    try { const saved = localStorage.getItem("tf_funding_filters_open"); if (saved === "1" || saved === "0") wantOpen = saved === "1"; } catch (_) { /* private mode */ }
    setFiltersOpen(wantOpen);
    byId("txtFundingSearch")?.addEventListener("input", () => reload(300));
    ["selFundingLocation", "selFundingAward", "selFundingSort", "chkFundingUkWide", "chkFundingHideUnlikely", "chkFundingSoon", "chkFundingSaved"].forEach((id) => {
      byId(id)?.addEventListener("change", () => { track("funding_filter_changed", { filter: id.replace(/^(sel|chk)Funding/, "").toLowerCase(), value: String(byId(id).type === "checkbox" ? byId(id).checked : byId(id).value).slice(0, 40) }); reload(0); });
    });
    byId("selFundingApplicant")?.addEventListener("change", () => { state.touchedApplicant = true; track("funding_filter_changed", { filter: "applicant", value: byId("selFundingApplicant").value }); reload(0); });
    byId("selFundingProfile")?.addEventListener("change", (e) => {
      state.profileId = e.target.value;
      state.touchedApplicant = false;
      try { localStorage.setItem(LS_KEY, state.profileId); } catch (err) { /* private window */ }
      reload(0);
    });
    byId("btnFundingClear")?.addEventListener("click", clearFilters);
    byId("chkFundingWeekly")?.addEventListener("change", (e) => setWeekly(e.target.checked));
    byId("fundingChips")?.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-chip]");
      const chip = btn && state.chips && state.chips[Number(btn.dataset.chip)];
      if (!chip) return;
      chip[1]();
      reload(0);
    });
    byId("fundingEmpty")?.addEventListener("click", (e) => {
      if (e.target.closest("[data-empty-clear]")) return clearFilters();
      const btn = e.target.closest("button[data-empty-chip]");
      const chip = btn && state.chips && state.chips[Number(btn.dataset.emptyChip)];
      if (chip) { chip[1](); reload(0); }
    });
    byId("fundingList")?.addEventListener("click", (e) => {
      const save = e.target.closest("button[data-save]");
      if (save) { toggleSave(save.getAttribute("data-save"), save); return; }
      const details = e.target.closest("button[data-details]");
      if (details) openDetail(details.getAttribute("data-details"), details);
    });
    byId("fundingDrawer")?.addEventListener("click", (e) => {
      if (e.target.closest("[data-drawer-close]")) return closeDetail();
      const apply = e.target.closest("a.funding-btn--primary");
      if (apply) track("funding_apply_clicked", { grant: state.detail });
      const ai = e.target.closest("button[data-drawer-ai]");
      if (ai && window.FundingAI) { window.FundingAI.open(state.items[ai.getAttribute("data-drawer-ai")]); return; }
      const save = e.target.closest("button[data-drawer-save]");
      if (save) {
        const label = save.getAttribute("data-drawer-save");
        toggleSave(label, save).then(() => {
          const card = byId("fundingList")?.querySelector('button[data-save="' + label + '"]');
          const g = state.items[label];
          if (card && g) { card.classList.toggle("is-saved", !!g.saved); card.textContent = g.saved ? "Saved" : "Save"; card.setAttribute("aria-pressed", g.saved ? "true" : "false"); }
        });
      }
    });
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape" || byId("fundingModal")?.classList.contains("hidden")) return;
      const ai = byId("fundingAiModal");
      if (ai && !ai.classList.contains("hidden")) return; // the AI dialog handles its own Escape
      closeDetail();
    });
    byId("fundingPager")?.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-page]");
      if (!btn || btn.disabled) return;
      state.page = Number(btn.dataset.page) || 1;
      load();
      byId("fundingModal")?.querySelector(".funding-body")?.scrollTo({ top: 0 });
    });
  }

  const api = { track, openGrant, detailHtml, parseHash, cardHtml, awardText, money, closingHtml, pagerHtml, metaText, fitPill, fitLine, whereText };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof window !== "undefined") {
    window.openFunding = openFunding;
    window.closeFunding = closeFunding;
    window.TFFunding = api;
    const init = () => {
      if (!byId("fundingModal")) return;
      bind();
      // A link such as /funding?where=Scotland opens the page where it was left.
      if (parseHash()) window.addEventListener("load", () => setTimeout(() => (byId("btnFunding") || { click: openFunding }).click(), 400));
    };
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
    else init();
  }
})();
