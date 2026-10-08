/* Growth Studio: state, requests and events for the Growth Studio section of the app shell.
   All markup comes from growth-view.js (pure functions, tested in Node); this file only decides when to
   fetch, what to keep, and what to do with a click. It talks to /api/growth/* only. The global fetch wrapper
   in app.js adds the CSRF token to every non-GET call and shows the credits dialog on a 402. */
(function () {
  "use strict";

  const V = window.GrowthView;
  if (!V) return; // growth-view.js did not load: leave the rest of the app alone

  const byId = (id) => document.getElementById(id);
  const TABS = { signals: "gsTabSignals", campaigns: "gsTabCampaigns", builder: "gsTabBuilder", performance: "gsTabPerformance" };
  const TYPE_CHOICES = { renewal: "Contract renewals", development: "New developments", engagement: "Market engagements" };
  const STALE_MS = 5 * 60 * 1000;
  const PANES = { signals: "gsPaneSignals", campaigns: "gsPaneCampaigns", builder: "gsPaneBuilder", performance: "gsPanePerformance" };

  function freshBuilder() {
    return {
      status: "idle", id: null, detail: null, draft: null, dirty: false, preview: null, previewTarget: null, previewSeq: 0,
      warnings: null, drafting: false, error: null, lastField: "body", aiNote: "",
    };
  }

  const state = {
    ready: false,
    starting: false,
    options: null,
    profiles: [],
    profileId: null,
    filters: null,
    categoryLabel: "",
    customOpen: false,
    suggestions: [],
    tab: "signals",
    signals: { status: "idle", data: null, error: null, selected: [], expanded: [], showDismissed: false, controller: null, at: 0 },
    campaigns: { status: "idle", list: null, error: null, suppressions: [], suppressionsOpen: false },
    builder: freshBuilder(),
    performance: { status: "idle", data: null, error: null, days: "365" },
  };

  // ── requests ──────────────────────────────────────────────────────────────────────────────────
  class ApiError extends Error {
    constructor(status, message) {
      super(message);
      this.status = status;
    }
  }

  async function api(method, url, body, signal) {
    const init = { method, headers: {}, credentials: "same-origin" };
    if (body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    if (signal) init.signal = signal;
    let res;
    try {
      res = await fetch(url, init);
    } catch (err) {
      if (err && err.name === "AbortError") throw err;
      throw new ApiError(0, "Could not reach the server. Check your connection and try again.");
    }
    let data = null;
    try {
      data = await res.json();
    } catch (err) {
      /* not JSON */
    }
    if (!res.ok) {
      const message = res.status === 429 ? "You are going too fast. Wait a moment and try again." : (data && data.error) || `Something went wrong (${res.status}).`;
      throw new ApiError(res.status, message);
    }
    return data;
  }

  // Run a handler; show what went wrong instead of leaving a silent failure. A 402 already opened the credits dialog.
  async function run(fn) {
    try {
      await fn();
    } catch (err) {
      if (err && err.name === "AbortError") return;
      if (err && err.status === 402) return;
      toast((err && err.message) || "Something went wrong.", { error: true });
    }
  }

  function debounce(fn, ms) {
    let timer;
    const wrapped = (...args) => {
      clearTimeout(timer);
      timer = setTimeout(() => fn(...args), ms);
    };
    wrapped.flush = (...args) => {
      clearTimeout(timer);
      fn(...args);
    };
    return wrapped;
  }

  async function confirmDialog(message) {
    if (typeof window.showConfirm === "function") return window.showConfirm(message);
    return window.confirm(message);
  }

  // ── small UI services ─────────────────────────────────────────────────────────────────────────
  let toastTimer;
  function toast(message, opts) {
    const el = byId("gsToast");
    if (!el) return;
    const o = opts || {};
    el.className = "gs-toast is-on" + (o.error ? " is-error" : "");
    el.innerHTML = `<span>${V.esc(message)}</span>` + (o.action ? `<button type="button" class="gs-link" data-toast-action>${V.esc(o.action.label)}</button>` : "");
    el.querySelector("[data-toast-action]")?.addEventListener("click", () => {
      el.classList.remove("is-on");
      o.action.run();
    });
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("is-on"), o.error ? 7000 : o.action ? 8000 : 3500);
  }

  function download(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (err) {
      const area = document.createElement("textarea");
      area.value = text;
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      let ok = false;
      try {
        ok = document.execCommand("copy");
      } catch (e) {
        /* ignore */
      }
      area.remove();
      return ok;
    }
  }

  // Re-rendering replaces the focused element: put focus back on its twin so keyboard users keep their place.
  function selectorFor(el) {
    if (!el || !el.dataset) return null;
    const pairs = [["action", "data-action"], ["key", "data-key"], ["id", "data-id"], ["field", "data-field"], ["format", "data-format"], ["name", "data-name"]];
    const quote = (v) => (window.CSS && CSS.escape ? CSS.escape(String(v)) : String(v).replace(/["\\]/g, "\\$&"));
    const parts = pairs.filter(([k]) => el.dataset[k] != null).map(([k, attr]) => `[${attr}="${quote(el.dataset[k])}"]`);
    return parts.length && (el.dataset.action || el.dataset.field) ? parts.join("") : null;
  }

  function keepFocus(render) {
    const host = byId("growthStudioModal");
    const active = document.activeElement;
    const selector = active && host && host.contains(active) ? selectorFor(active) : null;
    render();
    if (selector) host.querySelector(selector)?.focus();
  }

  // ── rendering ─────────────────────────────────────────────────────────────────────────────────
  function signalsModel() {
    const s = state.signals;
    return {
      filters: state.filters, status: s.status, error: s.error, data: s.data, selected: s.selected, expanded: s.expanded,
      showDismissed: s.showDismissed, suggestions: state.suggestions, campaigns: state.campaigns.list,
      maxPerType: state.options && state.options.max_per_type,
    };
  }

  function renderSignals() {
    keepFocus(() => {
      byId(PANES.signals).innerHTML = V.signalsPane(signalsModel());
    });
    updateTabCounts();
  }

  function renderCampaigns() {
    const c = state.campaigns;
    keepFocus(() => {
      byId(PANES.campaigns).innerHTML = V.campaignsPane({
        status: c.status, error: c.error, list: c.list, suppressions: c.suppressions, suppressionsOpen: c.suppressionsOpen,
      });
    });
    updateTabCounts();
  }

  function renderBuilder() {
    const b = state.builder;
    keepFocus(() => {
      byId(PANES.builder).innerHTML = V.builderPane({
        status: b.status, error: b.error, detail: b.detail, draft: b.draft, dirty: b.dirty, preview: b.preview, previewTarget: b.previewTarget,
        warnings: b.warnings, drafting: b.drafting, options: state.options, aiNote: b.aiNote,
      });
    });
  }

  function renderPerformance() {
    const p = state.performance;
    keepFocus(() => {
      byId(PANES.performance).innerHTML = V.performancePane({ status: p.status, error: p.error, data: p.data });
    });
  }

  function renderAll() {
    renderSignals();
    renderCampaigns();
    renderBuilder();
    renderPerformance();
  }

  function updateTabCounts() {
    const set = (id, n) => {
      const el = byId(id);
      if (el) el.textContent = n ? String(n) : "";
    };
    set("gsCountSignals", state.signals.data ? state.signals.data.total : 0);
    set("gsCountCampaigns", state.campaigns.list ? state.campaigns.list.length : 0);
  }

  // ── the filter bar ────────────────────────────────────────────────────────────────────────────
  function optionsHtml(items, selected) {
    return items
      .map(([value, label]) => `<option value="${V.esc(value)}"${String(value) === String(selected) ? " selected" : ""}>${V.esc(label)}</option>`)
      .join("");
  }

  function typesValue(types) {
    if (!types || types.length === 0 || types.length === 3) return "all";
    return types.length === 1 ? types[0] : "mix";
  }

  function syncFilterBar() {
    const f = state.filters || V.defaultFilters(state.options);
    const o = state.options || { presets: [], authority_types: [], day_choices: [], signal_types: [] };
    const customActive = Boolean(f.cpv || f.q);
    const profileItems = state.profiles.length ? state.profiles.map((p) => [p.id, p.name + (p.is_default ? " (default)" : "")]) : [["", "No company profile yet"]];
    byId("gsProfile").innerHTML = optionsHtml(profileItems, state.profileId);
    byId("gsProfile").disabled = state.profiles.length < 2;
    byId("gsCategory").innerHTML = optionsHtml(
      [["", "Choose a category…"], ...o.presets.map((p) => [p.preset, p.label]), ["__custom", customActive ? `Custom: ${state.categoryLabel || "your search"}` : "Custom category…"]],
      f.preset || (customActive ? "__custom" : "")
    );
    const tv = typesValue(f.types);
    const typeItems = [["all", "All signal types"], ...o.signal_types.map((t) => [t.id, TYPE_CHOICES[t.id] || t.label])];
    if (tv === "mix") typeItems.push(["mix", (f.types || []).map((t) => (V.TYPES[t] || {}).label || t).join(" + ")]);
    byId("gsTypes").innerHTML = optionsHtml(typeItems, tv);
    byId("gsDays").innerHTML = optionsHtml(o.day_choices.map((d) => [d, `${d}-day window`]), f.days);
    byId("gsAuthority").innerHTML = optionsHtml(o.authority_types.map((a) => [a.id, a.label]), f.authority || "all");
    byId("gsFrameworks").checked = f.frameworks !== false;
    const where = byId("gsWhere");
    if (document.activeElement !== where) where.value = f.where || "";
    byId("gsCustomCpv").value = f.cpv || "";
    byId("gsCustomQ").value = f.q || "";
    byId("gsCustomBar").classList.toggle("hidden", !(customActive || state.customOpen));
    const nonDefault = (f.authority && f.authority !== "all") || f.frameworks === false;
    byId("chipGsMore").classList.toggle("is-set", Boolean(nonDefault));
  }

  const saveTargeting = debounce(() => {
    if (!V.hasCategory(state.filters)) return;
    api("PUT", "/api/growth/targeting", { profile_id: state.profileId || null, filters: V.filtersBody(state.filters) })
      .then((res) => {
        if (res && res.category) state.categoryLabel = res.category.label || state.categoryLabel;
      })
      .catch(() => {});
  }, 700);

  function filtersChanged(immediately) {
    state.signals.selected = [];
    syncFilterBar();
    saveTargeting();
    if (immediately) loadSignals();
    else scheduleLoad();
  }

  const scheduleLoad = debounce(() => loadSignals(), 300);

  async function changeProfile(value) {
    const id = value ? Number(value) : null;
    state.profileId = id;
    state.signals.selected = [];
    state.signals.data = null;
    await run(async () => {
      const ctx = await api("GET", "/api/growth/context" + (id ? `?profile_id=${id}` : ""));
      state.suggestions = ctx.suggestions || [];
      if (ctx.filters) {
        state.filters = ctx.filters;
        state.categoryLabel = (ctx.category && ctx.category.label) || "";
      }
      syncFilterBar();
      saveTargeting.flush();
      loadSignals();
    });
  }

  function changeCategory(value) {
    const f = state.filters;
    if (value === "__custom") {
      state.customOpen = true;
      syncFilterBar();
      byId("gsCustomCpv").focus();
      return;
    }
    state.customOpen = false;
    delete f.cpv;
    delete f.q;
    if (value) f.preset = value;
    else delete f.preset;
    state.categoryLabel = "";
    state.signals.data = value ? state.signals.data : null;
    filtersChanged(true);
  }

  function applyCustomCategory() {
    const f = state.filters;
    const cpv = byId("gsCustomCpv").value.trim();
    const q = byId("gsCustomQ").value.trim();
    if (!cpv && !q) {
      toast("Enter a CPV code, some words from the contract title, or both.", { error: true });
      return;
    }
    delete f.preset;
    if (cpv) f.cpv = cpv;
    else delete f.cpv;
    if (q) f.q = q;
    else delete f.q;
    state.customOpen = false;
    state.categoryLabel = "";
    filtersChanged(true);
  }

  function resetFilters() {
    const keep = state.filters || {};
    const fresh = V.defaultFilters(state.options);
    ["preset", "cpv", "q"].forEach((k) => keep[k] && (fresh[k] = keep[k]));
    state.filters = fresh;
    byId("gsWhere").value = "";
    filtersChanged(true);
  }

  // ── signals ───────────────────────────────────────────────────────────────────────────────────
  async function loadSignals() {
    const s = state.signals;
    if (!V.hasCategory(state.filters)) {
      s.status = "idle";
      s.data = null;
      renderSignals();
      return;
    }
    if (s.controller) s.controller.abort();
    const controller = new AbortController();
    s.controller = controller;
    s.status = "loading";
    renderSignals();
    try {
      const data = await api("GET", "/api/growth/signals?" + V.signalsQuery(state.filters, state.profileId, s.showDismissed), undefined, controller.signal);
      if (s.controller !== controller) return;
      s.data = data;
      s.at = Date.now();
      s.status = "ok";
      s.error = null;
      if (data.filters && data.filters.category) state.categoryLabel = data.filters.category.label;
      s.selected = s.selected.filter((key) => data.signals.some((x) => x.key === key));
      syncFilterBar();
    } catch (err) {
      if (err && err.name === "AbortError") return;
      if (s.controller !== controller) return;
      s.status = "error";
      s.error = (err && err.message) || "Could not load signals.";
    }
    renderSignals();
  }

  function updateSelectionUi() {
    const bar = byId("gsSelBar");
    if (bar) bar.innerHTML = V.selectionBar({ selected: state.signals.selected, campaigns: state.campaigns.list });
    document.querySelectorAll("#gsPaneSignals .gs-signal").forEach((card) => card.classList.toggle("is-selected", state.signals.selected.indexOf(card.dataset.key) >= 0));
  }

  function toggleIn(list, value) {
    const i = list.indexOf(value);
    if (i >= 0) list.splice(i, 1);
    else list.push(value);
  }

  async function setDismissed(key, dismissed) {
    await api("PUT", "/api/growth/signals/state", { key, dismissed, profile_id: state.profileId || null });
    await loadSignals();
    if (dismissed) toast("Dismissed. It will stay out of your list.", { action: { label: "Undo", run: () => run(() => setDismissed(key, false)) } });
  }

  // ── campaigns ─────────────────────────────────────────────────────────────────────────────────
  async function loadCampaigns() {
    const c = state.campaigns;
    c.status = c.list ? "ok" : "loading";
    if (!c.list) renderCampaigns();
    try {
      const [list, sup] = await Promise.all([api("GET", "/api/growth/campaigns"), api("GET", "/api/growth/suppressions")]);
      c.list = list.campaigns;
      c.suppressions = sup.suppressions;
      c.status = "ok";
      c.error = null;
    } catch (err) {
      c.status = "error";
      c.error = err.message;
    }
    renderCampaigns();
    if (state.signals.selected.length) updateSelectionUi();
  }

  async function startCampaign(keys) {
    const res = await api("POST", "/api/growth/campaigns", { keys, filters: V.filtersBody(state.filters), profile_id: state.profileId || null });
    state.signals.selected = [];
    state.campaigns.list = null;
    openFromDetail(res);
    toast(skippedText(res.skipped, "Campaign created. Review the message, then export the list."));
    loadSignals();
  }

  function skippedText(skipped, ok) {
    if (!skipped || !skipped.length) return ok;
    const why = Array.from(new Set(skipped.map((s) => s.reason))).join(" ");
    return `${ok} ${skipped.length} could not be added. ${why}`;
  }

  async function addToCampaign(id) {
    if (!id) return;
    const res = await api("POST", `/api/growth/campaigns/${id}/targets`, { keys: state.signals.selected, filters: V.filtersBody(state.filters), profile_id: state.profileId || null });
    state.signals.selected = [];
    openFromDetail(res);
    toast(skippedText(res.skipped, `${V.plural(res.added, "buyer")} added.`));
    loadSignals();
  }

  function pickPreviewTarget(detail, wanted) {
    const choices = detail.targets.filter((t) => !t.opted_out);
    return (choices.find((t) => t.id === wanted) || choices.find((t) => t.included) || choices[0] || {}).id || null;
  }

  function openFromDetail(detail) {
    const b = (state.builder = freshBuilder());
    b.id = detail.campaign.id;
    applyDetail(detail, true);
    setTab("builder");
  }

  function applyDetail(detail, reset) {
    const b = state.builder;
    b.detail = detail;
    b.id = detail.campaign.id;
    b.status = "ok";
    if (reset || !b.dirty || !b.draft) {
      b.draft = { subject: detail.campaign.subject, body: detail.campaign.body };
      b.dirty = false;
    }
    b.previewTarget = pickPreviewTarget(detail, b.previewTarget);
    b.warnings = null;
    renderBuilder();
    refreshPreview();
  }

  async function openCampaign(id) {
    const b = state.builder;
    if (b.dirty && b.id !== id && !(await confirmDialog("Discard the unsaved changes to the message you were editing?"))) return;
    if (b.id !== id) state.builder = freshBuilder();
    state.builder.id = id;
    state.builder.status = "loading";
    state.builder.error = null;
    setTab("builder");
    renderBuilder();
    try {
      applyDetail(await api("GET", `/api/growth/campaigns/${id}`), b.id !== id || !b.dirty);
    } catch (err) {
      state.builder.status = "error";
      state.builder.error = err.message;
      renderBuilder();
    }
  }

  const refreshPreview = debounce(async () => {
    const b = state.builder;
    if (!b.detail || !b.detail.targets.some((t) => !t.opted_out) || !byId("gsPreview")) return;
    const seq = ++b.previewSeq;
    try {
      const p = await api("POST", `/api/growth/campaigns/${b.id}/preview`, { target_id: b.previewTarget, subject: b.draft.subject, body: b.draft.body });
      if (seq !== b.previewSeq || !byId("gsPreview")) return;
      b.preview = p;
      b.previewTarget = p.target_id;
      b.warnings = p.warnings;
      byId("gsPreview").innerHTML = V.previewHtml(p, b.detail.targets, b.previewTarget);
      byId("gsWarnings").innerHTML = V.warningsHtml(p.warnings);
    } catch (err) {
      if (seq === b.previewSeq && byId("gsPreview")) byId("gsPreview").innerHTML = `<p class="gs-hint gs-hint--warn">${V.esc(err.message)}</p>`;
    }
  }, 350);

  function markDirty() {
    const b = state.builder;
    if (b.dirty) return;
    b.dirty = true;
    const save = document.querySelector('#gsPaneBuilder [data-action="save-message"]');
    if (save) save.disabled = false;
    const title = document.querySelector('#gsPaneBuilder .gs-card[aria-label="Message"] .gs-card__title');
    if (title && !title.querySelector(".gs-badge")) title.insertAdjacentHTML("beforeend", ' <span class="gs-badge gs-badge--warn">Unsaved changes</span>');
  }

  async function saveMessage() {
    const b = state.builder;
    const res = await api("PATCH", `/api/growth/campaigns/${b.id}`, { subject: b.draft.subject, body: b.draft.body });
    b.dirty = false;
    applyDetail(res, true);
    toast("Message saved.");
  }

  async function patchTarget(id, fields) {
    const b = state.builder;
    try {
      applyDetail(await api("PATCH", `/api/growth/campaigns/${b.id}/targets/${id}`, fields), false);
    } catch (err) {
      renderBuilder(); // put the control back to what the server has
      throw err;
    }
  }

  // A change that nothing else on the page depends on (an email, the name, the channel) is saved and
  // remembered without re-rendering: replacing the page between a field losing focus and the click that
  // caused it would swallow that click (type an email, press Export).
  async function patchQuietly(fields, targetId) {
    const b = state.builder;
    const url = targetId ? `/api/growth/campaigns/${b.id}/targets/${targetId}` : `/api/growth/campaigns/${b.id}`;
    try {
      const res = await api("PATCH", url, fields);
      b.detail = res;
      const target = targetId && res.targets.find((t) => t.id === targetId);
      const row = target && document.querySelector(`#gsPaneBuilder tr.gs-target[data-id="${targetId}"]`);
      if (row && target) row.outerHTML = V.targetRow(target);
      refreshPreview();
    } catch (err) {
      renderBuilder(); // put the control back to what the server has
      throw err;
    }
  }

  async function draft(mode) {
    const b = state.builder;
    if (b.dirty && !(await confirmDialog("Replace the message in the editor with a new draft? Your unsaved edits will be lost."))) return;
    if (mode === "ai") {
      const cost = state.options.credit_cost_draft;
      if (cost > 0 && !(await confirmDialog(`Writing a draft with AI uses ${cost} ${cost === 1 ? "credit" : "credits"}. You are not charged if it fails. Continue?`))) return;
      b.drafting = true;
      renderBuilder();
    }
    try {
      const body = { mode };
      if (mode === "ai" && b.aiNote.trim()) body.instructions = b.aiNote.trim();
      const res = await api("POST", `/api/growth/campaigns/${b.id}/draft`, body);
      b.draft = { subject: res.subject, body: res.body };
      b.dirty = false;
      markDirty();
      toast(mode === "ai" ? "AI draft ready. Read it, edit it and save it: nothing is saved until you do." : "Template draft ready. Edit it and save it.");
    } finally {
      b.drafting = false;
      renderBuilder();
      refreshPreview();
    }
  }

  async function exportFile(format) {
    const b = state.builder;
    if (b.dirty) await saveMessage(); // exports use the saved message
    const res = await fetch(`/api/growth/campaigns/${b.id}/export?format=${encodeURIComponent(format)}`, { method: "GET", credentials: "same-origin" });
    if (!res.ok) {
      let message = `The export failed (${res.status}).`;
      try {
        message = (await res.json()).error || message;
      } catch (err) {
        /* not JSON */
      }
      throw new ApiError(res.status, message);
    }
    download(await res.blob(), V.filenameFromDisposition(res.headers.get("Content-Disposition"), `growth-${format}.csv`));
    toast(format === "csv" ? "CSV downloaded." : "Mailchimp audience downloaded. Import it as an audience, then paste the message.");
  }

  async function copyMailchimp() {
    const b = state.builder;
    if (b.dirty) await saveMessage();
    const res = await fetch(`/api/growth/campaigns/${b.id}/export?format=mailchimp-message`, { method: "GET", credentials: "same-origin" });
    if (!res.ok) {
      let message = `Could not prepare the message (${res.status}).`;
      try {
        message = (await res.json()).error || message;
      } catch (err) {
        /* not JSON */
      }
      throw new ApiError(res.status, message);
    }
    const text = await res.text();
    if (await copyText(text)) {
      toast("Copied. Paste it into your Mailchimp campaign: the merge tags match the audience columns.");
      return;
    }
    const box = byId("gsMcBox");
    if (box) {
      box.innerHTML = V.mailchimpBox(text);
      const area = byId("gsMcText");
      area.focus();
      area.select();
    }
    toast("The browser would not copy it for you. The text is selected below: press Ctrl+C (Cmd+C on a Mac).");
  }

  // ── performance ───────────────────────────────────────────────────────────────────────────────
  async function loadPerformance() {
    const p = state.performance;
    p.status = p.data ? "ok" : "loading";
    if (!p.data) renderPerformance();
    try {
      p.data = await api("GET", `/api/growth/performance?days=${encodeURIComponent(p.days)}`);
      p.status = "ok";
      p.error = null;
    } catch (err) {
      p.status = "error";
      p.error = err.message;
    }
    renderPerformance();
  }

  // ── tabs ──────────────────────────────────────────────────────────────────────────────────────
  function setTab(tab) {
    state.tab = tab;
    Object.keys(TABS).forEach((t) => {
      const on = t === tab;
      byId(PANES[t]).classList.toggle("hidden", !on);
      const btn = byId(TABS[t]);
      btn.classList.toggle("is-active", on);
      btn.setAttribute("aria-selected", String(on));
      btn.tabIndex = on ? 0 : -1;
    });
    if (tab === "signals" && !state.signals.data && V.hasCategory(state.filters)) loadSignals();
    if (tab === "campaigns") loadCampaigns();
    if (tab === "performance") loadPerformance();
  }

  // ── events ────────────────────────────────────────────────────────────────────────────────────
  const CLICK = {
    tab: (a) => setTab(a.dataset.tab),
    "toggle-fit": (a) => {
      toggleIn(state.signals.expanded, "fit:" + a.dataset.key);
      renderSignals();
    },
    "toggle-more": (a) => {
      toggleIn(state.signals.expanded, "more:" + a.dataset.key);
      renderSignals();
    },
    "toggle-dismissed": () => {
      state.signals.showDismissed = !state.signals.showDismissed;
      loadSignals();
    },
    "pick-category": (a) => {
      state.filters.preset = a.dataset.preset;
      delete state.filters.cpv;
      delete state.filters.q;
      filtersChanged(true);
    },
    "start-campaign": (a) => run(() => startCampaign(V.campaignKeys(a.dataset.key, state.signals.selected))),
    "create-from-selection": () => run(() => startCampaign(state.signals.selected.slice())),
    "clear-selection": () => {
      state.signals.selected = [];
      renderSignals();
    },
    dismiss: (a) => run(() => setDismissed(a.dataset.key, true)),
    restore: (a) => run(() => setDismissed(a.dataset.key, false)),
    "open-campaign": (a) => run(() => openCampaign(Number(a.dataset.id))),
    "reload-signals": () => loadSignals(),
    "reload-campaigns": () => {
      state.campaigns.list = null;
      loadCampaigns();
    },
    "reload-performance": () => loadPerformance(),
    "back-to-campaigns": () => setTab("campaigns"),
    "goto-signals": () => setTab("signals"),
    "apply-custom": () => applyCustomCategory(),
    "reset-filters": () => resetFilters(),
    "remove-suppression": (a) =>
      run(async () => {
        if (!(await confirmDialog("Remove this buyer from your do-not-contact list? Only do this if they have told you they are happy to hear from you."))) return;
        await api("DELETE", `/api/growth/suppressions?key=${encodeURIComponent(a.dataset.key)}`);
        state.campaigns.list = null;
        await loadCampaigns();
        loadSignals();
      }),
    "remove-target": (a) =>
      run(async () => {
        if (!(await confirmDialog("Remove this buyer from the campaign?"))) return;
        applyDetail(await api("DELETE", `/api/growth/campaigns/${state.builder.id}/targets/${a.dataset.id}`), false);
        loadSignals();
      }),
    "mark-sent": () =>
      run(async () => {
        if (!(await confirmDialog("Record that you have sent the message to every included buyer who has not been contacted yet?"))) return;
        const res = await api("POST", `/api/growth/campaigns/${state.builder.id}/mark-sent`, {});
        applyDetail(res, false);
        toast(`${V.plural(res.marked, "buyer")} marked as sent.`);
      }),
    "save-message": () => run(saveMessage),
    "draft-template": () => run(() => draft("template")),
    "draft-ai": () => run(() => draft("ai")),
    export: (a) => run(() => exportFile(a.dataset.format)),
    "copy-mailchimp": () => run(copyMailchimp),
    "delete-campaign": () =>
      run(async () => {
        if (!(await confirmDialog("Delete this campaign and its results? This cannot be undone."))) return;
        await api("DELETE", `/api/growth/campaigns/${state.builder.id}`);
        state.builder = freshBuilder();
        state.campaigns.list = null;
        renderBuilder();
        setTab("campaigns");
        toast("Campaign deleted.");
        loadSignals();
      }),
    "insert-field": (a) => {
      const field = byId(state.builder.lastField === "subject" ? "gsSubject" : "gsBody");
      if (!field) return;
      const out = V.insertAt(field.value, field.selectionStart, field.selectionEnd, "{{" + a.dataset.name + "}}");
      field.value = out.value;
      field.focus();
      field.setSelectionRange(out.caret, out.caret);
      state.builder.draft[state.builder.lastField === "subject" ? "subject" : "body"] = out.value;
      markDirty();
      refreshPreview();
    },
  };

  function onClick(event) {
    const a = event.target.closest("[data-action]");
    if (!a || a.disabled || a.tagName === "INPUT" || a.tagName === "SELECT") return;
    const handler = CLICK[a.dataset.action];
    if (handler) handler(a);
  }

  function onChange(event) {
    const t = event.target;
    const f = state.filters;
    switch (t.id) {
      case "gsProfile":
        return void changeProfile(t.value);
      case "gsCategory":
        return changeCategory(t.value);
      case "gsTypes":
        f.types = t.value === "all" ? V.defaultFilters().types : t.value === "mix" ? f.types : [t.value];
        return filtersChanged(true);
      case "gsDays":
        f.days = Number(t.value);
        return filtersChanged(true);
      case "gsAuthority":
        f.authority = t.value;
        return filtersChanged(true);
      case "gsFrameworks":
        f.frameworks = t.checked;
        return filtersChanged(true);
      case "gsAddTo":
        return void run(() => addToCampaign(t.value));
      case "gsPreviewTarget":
        state.builder.previewTarget = Number(t.value);
        return refreshPreview.flush();
      default:
    }
    const d = t.dataset || {};
    if (d.action === "toggle-select") {
      toggleIn(state.signals.selected, d.key);
      if (state.signals.selected.length && !state.campaigns.list) run(loadCampaigns);
      return updateSelectionUi();
    }
    if (d.action === "target-include") return void run(() => patchTarget(Number(d.id), { included: t.checked }));
    if (d.field === "contact_email") return void run(() => patchQuietly({ contact_email: t.value.trim() }, Number(d.id)));
    if (d.field === "status") {
      const id = Number(d.id);
      return void run(async () => {
        if (t.value === "opted_out" && !(await confirmDialog("Add this buyer to your do-not-contact list? They will be left out of every export and cannot be added to a future campaign."))) {
          return renderBuilder();
        }
        await patchTarget(id, { status: t.value });
      });
    }
    if (d.field === "campaign-name") return void run(() => patchQuietly({ name: t.value }));
    if (d.field === "campaign-channel") return void run(() => patchQuietly({ channel: t.value }));
    if (d.field === "campaign-status") return void run(() => patchQuietly({ status: t.value }));
    if (d.field === "performance-days") {
      state.performance.days = t.value;
      return void loadPerformance();
    }
  }

  const applyWhere = debounce(() => {
    const value = byId("gsWhere").value.replace(/\s+/g, " ").trim();
    if (value.length === 1) return; // the server wants two characters or none
    if (value === (state.filters.where || "")) return;
    state.filters.where = value;
    filtersChanged(true);
  }, 450);

  function onInput(event) {
    const t = event.target;
    if (t.id === "gsWhere") return applyWhere();
    if (t.id === "gsAiNote") {
      state.builder.aiNote = t.value;
      return;
    }
    const field = t.dataset && t.dataset.field;
    if (field === "subject" || field === "body") {
      state.builder.draft[field] = t.value;
      state.builder.lastField = field;
      markDirty();
      refreshPreview();
    }
  }

  function onFocusIn(event) {
    const field = event.target.dataset && event.target.dataset.field;
    if (field === "subject" || field === "body") state.builder.lastField = field;
  }

  function onKeydown(event) {
    const t = event.target;
    if (event.key === "Enter" && t.id === "gsWhere") {
      event.preventDefault();
      applyWhere.flush();
      return;
    }
    if (event.key === "Enter" && (t.id === "gsCustomCpv" || t.id === "gsCustomQ")) {
      event.preventDefault();
      applyCustomCategory();
      return;
    }
    if ((event.key === "ArrowLeft" || event.key === "ArrowRight") && t.getAttribute && t.getAttribute("role") === "tab") {
      const tabs = Object.keys(TABS).filter((k) => !byId(TABS[k]).hidden);
      const next = tabs[(tabs.indexOf(t.dataset.tab) + (event.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
      setTab(next);
      byId(TABS[next]).focus();
    }
  }

  // ── start-up ──────────────────────────────────────────────────────────────────────────────────
  async function start() {
    if (state.ready || state.starting) return;
    state.starting = true;
    byId(PANES.signals).innerHTML = '<div class="gs-loading" role="status">Opening Growth Studio…</div>';
    try {
      const [options, ctx] = await Promise.all([api("GET", "/api/growth/options"), api("GET", "/api/growth/context")]);
      state.options = options;
      state.profiles = ctx.profiles;
      state.profileId = ctx.profile_id;
      state.suggestions = ctx.suggestions || [];
      state.categoryLabel = (ctx.category && ctx.category.label) || "";
      state.filters = ctx.filters || V.defaultFilters(options);
      if (!V.hasCategory(state.filters) && state.suggestions.length) state.filters.preset = state.suggestions[0].preset;
      state.ready = true;
      syncFilterBar();
      renderAll();
      setTab("signals");
      if (V.hasCategory(state.filters) && !ctx.filters) saveTargeting();
    } catch (err) {
      byId(PANES.signals).innerHTML = V.signalsPane({ filters: { preset: "x" }, status: "error", error: err.message || "Could not open Growth Studio.", data: null, selected: [], expanded: [] });
      byId(PANES.signals).querySelector('[data-action="reload-signals"]')?.setAttribute("data-action", "retry-start");
    } finally {
      state.starting = false;
    }
  }

  function openGrowthStudio() {
    byId("growthStudioModal")?.classList.remove("hidden");
    if (state.ready) {
      // Back again: signals older than a few minutes are re-read, the other tabs always are.
      if (state.tab === "signals") {
        if (Date.now() - state.signals.at > STALE_MS) loadSignals();
      } else if (state.tab !== "builder") setTab(state.tab);
    } else run(start);
    byId("growthStudioTitle")?.focus({ preventScroll: true });
  }

  function closeGrowthStudio() {
    byId("growthStudioModal")?.classList.add("hidden");
  }

  window.openGrowthStudio = openGrowthStudio;
  window.closeGrowthStudio = closeGrowthStudio;

  function bind() {
    const host = byId("growthStudioModal");
    if (!host || host.dataset.bound) return;
    host.dataset.bound = "1";
    CLICK["retry-start"] = () => {
      state.ready = false;
      run(start);
    };
    if (!byId("gsToast")) host.querySelector(".growth-page")?.insertAdjacentHTML("beforeend", '<div id="gsToast" class="gs-toast" role="status" aria-live="polite"></div>');
    host.addEventListener("submit", (event) => event.preventDefault()); // the filter bar applies as you go
    host.addEventListener("click", onClick);
    host.addEventListener("change", onChange);
    host.addEventListener("input", onInput);
    host.addEventListener("keydown", onKeydown);
    host.addEventListener("focusin", onFocusIn);
    host.addEventListener("toggle", (event) => {
      if (event.target.classList && event.target.classList.contains("gs-dnc")) state.campaigns.suppressionsOpen = event.target.open;
    }, true);
    byId("growthStudioClose")?.addEventListener("click", closeGrowthStudio);
    byId("btnGrowthStudio")?.addEventListener("click", openGrowthStudio);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bind);
  else bind();
})();
