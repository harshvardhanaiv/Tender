/* TenderFlow buyer workspace: core, Market Radar, dashboard, followed categories, organisation.
   Market engagement lives in buyer-engagement.js and registers its views through window.BW.
   Talks only to /api/market-radar/*, /api/buyer-workspace/* and /api/market-engagement/*, which read
   published award notices already in our database (nothing here waits on an external portal).
   Rendering is string templates per view with one delegated click handler (data-action), and the pure
   helpers are exported on BW so they can be tested without a browser (see tests/buyer_workspace_js_test.js). */
(function () {
  "use strict";

  const root = typeof window !== "undefined" ? window : globalThis;
  const BW = (root.BW = root.BW || {});
  const S = (BW.state = { me: null, csrf: "", org: null, watchlist: [], options: null, insights: false });

  // ── formatting ──────────────────────────────────────────────────────────────────────────────
  function esc(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  // how a buyer or supplier name is shown (web/name-format.js); stored names and search keys are untouched
  function displayName(name) { return typeof NameFormat !== "undefined" ? NameFormat.display(name) : String(name ?? ""); }
  function trimNum(x, digits) { return x.toFixed(digits).replace(/\.0+$/, ""); }
  function fmtInt(n) { return n == null ? "–" : Number(n).toLocaleString("en-GB"); }
  function fmtMoney(n) {
    if (n == null || Number.isNaN(Number(n))) return "–";
    const v = Number(n), a = Math.abs(v);
    // the thresholds sit on the rounding boundaries, so £999,600 reads "£1m" and never "£1000k"
    if (a >= 9.995e8) return "£" + trimNum(v / 1e9, 1) + "bn";
    if (a >= 9.995e5) return "£" + trimNum(v / 1e6, a >= 1e7 ? 0 : 1) + "m";
    if (a >= 1e4) return "£" + Math.round(v / 1e3) + "k";
    return "£" + Math.round(v).toLocaleString("en-GB");
  }
  function fmtDate(iso) {
    if (!iso) return "–";
    const d = new Date(`${String(iso).slice(0, 10)}T00:00:00`);
    if (Number.isNaN(d.getTime())) return "–";
    return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
  }
  function fmtPct(x) { return x == null ? "–" : `${Math.round(x * 100)}%`; }
  function plural(n, one, many) { return `${fmtInt(n)} ${n === 1 ? one : (many || one + "s")}`; }
  function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
  function clamp(x, lo, hi) { return Math.min(hi, Math.max(lo, x)); }
  // Notice links come from many portals: only ever link http(s).
  function safeUrl(value) {
    return typeof value === "string" && /^https?:\/\/\S+$/i.test(value) ? value : null;
  }

  // ── API ─────────────────────────────────────────────────────────────────────────────────────
  class ApiError extends Error {
    constructor(status, message) { super(message); this.status = status; }
  }
  function qs(obj) {
    const p = new URLSearchParams();
    Object.entries(obj).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== "") p.set(k, v); });
    const s = p.toString();
    return s ? `?${s}` : "";
  }
  async function api(path, { method = "GET", body, signal } = {}) {
    const headers = {};
    if (method !== "GET") { headers["Content-Type"] = "application/json"; headers["X-CSRF-Token"] = S.csrf; }
    let res;
    try {
      res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), credentials: "same-origin", signal });
    } catch (err) {
      if (err && err.name === "AbortError") throw err;
      throw new ApiError(0, "Could not reach the server. Check your connection and try again.");
    }
    if (res.status === 401) { root.location.href = "/login.html"; throw new ApiError(401, "Please sign in again."); }
    let data = null;
    try { data = await res.json(); } catch { /* not JSON */ }
    if (!res.ok) {
      const message = res.status === 429 ? "You are searching very quickly. Wait a moment and try again." : (data && data.error) || `Something went wrong (${res.status}).`;
      throw new ApiError(res.status, message);
    }
    return data;
  }

  // ── small UI services ───────────────────────────────────────────────────────────────────────
  let toastTimer;
  function toast(message, isError) {
    const el = document.getElementById("bwToast");
    if (!el) return;
    el.textContent = message;
    el.classList.toggle("is-error", Boolean(isError));
    el.classList.add("is-on");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("is-on"), isError ? 5200 : 3000);
  }

  let modalReturnFocus = null;
  function openModal(html) {
    const modal = document.getElementById("bwModal");
    document.getElementById("bwModalBox").innerHTML = html;
    modalReturnFocus = document.activeElement;
    modal.classList.remove("hidden");
    const first = modal.querySelector("input, select, textarea, button");
    if (first) first.focus();
  }
  function closeModal() {
    document.getElementById("bwModal").classList.add("hidden");
    document.getElementById("bwModalBox").innerHTML = "";
    if (modalReturnFocus && modalReturnFocus.focus) modalReturnFocus.focus();
    modalReturnFocus = null;
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      const area = document.createElement("textarea");
      area.value = text;
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      let ok = false;
      try { ok = document.execCommand("copy"); } catch { /* ignore */ }
      area.remove();
      return ok;
    }
  }
  function download(filename, text, type) {
    const url = URL.createObjectURL(new Blob([text], { type: type || "text/plain" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  // ── routing ─────────────────────────────────────────────────────────────────────────────────
  function parseHash(hash) {
    const raw = String(hash ?? (typeof location !== "undefined" ? location.hash : "")).replace(/^#\/?/, "");
    const [path, query = ""] = raw.split("?");
    return { parts: path.split("/").filter(Boolean).map(decodeURIComponent), params: new URLSearchParams(query) };
  }
  function buildHash(parts, params) {
    const q = params ? [...params].filter(([, v]) => v !== "" && v != null) : [];
    const query = q.length ? "?" + new URLSearchParams(q).toString() : "";
    return "#/" + parts.map(encodeURIComponent).join("/") + query;
  }
  function navigate(hash) { if (location.hash === hash) route(); else location.hash = hash; }
  function replaceHash(hash) { history.replaceState(null, "", hash); }

  const views = {};
  let routeToken = 0;
  let lastViewName = null;
  BW.registerView = (name, fn, title) => { views[name] = { fn, title: title || name }; };

  function setActiveNav(name) {
    const section = name === "engagements" ? "engagements" : name;
    document.querySelectorAll("[data-nav]").forEach((a) => {
      const on = a.dataset.nav === section;
      a.classList.toggle("is-active", on);
      if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
  }

  async function route() {
    const { parts, params } = parseHash();
    const requested = parts[0] || "dashboard";
    const name = views[requested] ? requested : "dashboard";
    const token = ++routeToken;
    const main = document.getElementById("bwMain");
    setActiveNav(name);
    document.title = `${views[name].title} · Buyer workspace · TenderFlow`;
    try {
      await views[name].fn({ parts, params, main, isCurrent: () => token === routeToken });
    } catch (err) {
      if (err && err.name === "AbortError") return;
      if (token === routeToken) main.innerHTML = errorHtml(err);
    }
    if (token === routeToken && name !== lastViewName) {
      lastViewName = name;
      main.scrollTop = 0;
      const h1 = main.querySelector("h1");
      if (h1) { h1.setAttribute("tabindex", "-1"); h1.focus({ preventScroll: true }); }
    }
  }

  function errorHtml(err) {
    return `<div class="bw-page"><div class="bw-card"><div class="bw-empty"><strong>That did not load</strong>${esc(err && err.message ? err.message : "Unexpected error")}
      <div class="bw-actions" style="justify-content:center"><button class="bw-btn" data-action="retry">Try again</button></div></div></div></div>`;
  }
  const skeleton = (rows = 4) => `<div aria-hidden="true">${Array.from({ length: rows }, () => '<span class="bw-skel bw-skel--tall"></span>').join("")}</div>`;

  // ── masthead, theme ─────────────────────────────────────────────────────────────────────────
  function updateMasthead() {
    const chip = document.getElementById("bwOrgChip");
    const text = document.getElementById("bwOrgChipText");
    if (chip && text) {
      text.textContent = S.org ? S.org.name : "Choose your organisation";
      chip.classList.toggle("is-unset", !S.org);
      chip.title = S.org ? `Your organisation: ${S.org.name}` : "Choose your organisation";
    }
    if (S.me) {
      const email = S.me.email || S.me.username || "";
      document.getElementById("bwProfileEmail").textContent = email;
      document.getElementById("bwProfileName").textContent = email.split("@")[0] || "Signed in";
      document.getElementById("bwInitials").textContent = (email[0] || "?").toUpperCase();
    }
  }
  function themeKey() { return `tf_${(S.me && S.me.username) || "guest"}_theme`; }
  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    const label = document.getElementById("bwThemeLabel");
    if (label) label.textContent = theme === "dark" ? "Light mode" : "Dark mode";
  }
  function initTheme() {
    let saved = null;
    try { saved = localStorage.getItem(themeKey()); } catch { /* storage blocked */ }
    const prefersDark = root.matchMedia && root.matchMedia("(prefers-color-scheme: dark)").matches;
    applyTheme(saved || document.documentElement.getAttribute("data-theme") || (prefersDark ? "dark" : "light"));
  }
  function toggleTheme() {
    const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    applyTheme(next);
    try { localStorage.setItem(themeKey(), next); } catch { /* storage blocked */ }
  }

  // ── options, categories ─────────────────────────────────────────────────────────────────────
  async function ensureOptions() {
    if (!S.options) S.options = await api("/api/market-radar/options");
    return S.options;
  }
  function specOf(cat) {
    if (!cat) return null;
    if (cat.preset) return { preset: cat.preset };
    const spec = {};
    if (cat.cpv) spec.cpv = cat.cpv;
    if (cat.q) spec.q = cat.q;
    return spec;
  }
  function specFromParams(params) {
    const preset = params.get("category");
    if (preset) return { preset };
    const cpv = params.get("cpv"), q = params.get("q");
    return cpv || q ? { cpv: cpv || undefined, q: q || undefined } : null;
  }
  // "preset:housing-repairs-gas", "cpv:5072", "q:tree surgery", "cpv:5072|q:boiler" -> {preset} | {cpv, q}
  function specFromKey(key) {
    if (!key) return null;
    if (key.startsWith("preset:")) return { preset: key.slice(7) };
    const spec = {};
    key.split("|").forEach((part) => {
      if (part.startsWith("cpv:")) spec.cpv = part.slice(4);
      else if (part.startsWith("q:")) spec.q = part.slice(2);
    });
    return Object.keys(spec).length ? spec : null;
  }
  function specToParams(spec, params = new URLSearchParams()) {
    ["category", "cpv", "q"].forEach((k) => params.delete(k));
    if (!spec) return params;
    if (spec.preset) params.set("category", spec.preset);
    if (spec.cpv) params.set("cpv", spec.cpv);
    if (spec.q) params.set("q", spec.q);
    return params;
  }
  function followed(cat) { return S.watchlist.some((c) => c.key === cat.key); }
  async function saveWatchlist(list) {
    const res = await api("/api/buyer-workspace/watchlist", { method: "PUT", body: { categories: list.map(specOf) } });
    S.watchlist = res.watchlist;
    return res.watchlist;
  }
  async function toggleFollow(cat) {
    const list = followed(cat) ? S.watchlist.filter((c) => c.key !== cat.key) : [...S.watchlist, cat];
    await saveWatchlist(list);
    toast(followed(cat) ? `Following “${cat.label}”` : `Stopped following “${cat.label}”`);
  }

  // category picker: a combobox with suggestions
  function pickerHtml(id, placeholder) {
    return `<div class="bw-picker" data-picker="${esc(id)}">
      <svg class="bw-picker__icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
      <input class="bw-input bw-picker__input" type="search" role="combobox" aria-expanded="false" aria-controls="${esc(id)}-list" aria-autocomplete="list" autocomplete="off" placeholder="${esc(placeholder)}" aria-label="Search for a category or CPV code">
      <ul class="bw-suggest hidden" id="${esc(id)}-list" role="listbox"></ul></div>`;
  }
  function suggestionItems(data, text) {
    const items = [];
    (data.presets || []).forEach((p) => items.push({ group: "Categories", label: p.label, meta: p.hint, spec: { preset: p.preset } }));
    (data.cpv || []).forEach((c) => items.push({ group: "CPV codes", label: c.label, meta: `${c.cpv} · ${fmtInt(c.awards)} awards`, spec: { cpv: c.cpv } }));
    if (text) items.push({ group: "Search", label: `Search award titles for “${text}”`, meta: "", spec: { q: text } });
    return items;
  }
  function bindPicker(rootEl, onPick) {
    const wrap = rootEl.querySelector("[data-picker]");
    if (!wrap) return;
    const input = wrap.querySelector("input");
    const list = wrap.querySelector("ul");
    let items = [], active = -1, seq = 0;

    const close = () => { list.classList.add("hidden"); input.setAttribute("aria-expanded", "false"); active = -1; };
    const paint = () => {
      if (!items.length) { list.innerHTML = '<li class="bw-suggest__empty">No matching categories. Try other words, or a CPV code.</li>'; }
      else {
        let group = "";
        list.innerHTML = items.map((it, i) => {
          const head = it.group !== group ? `<li class="bw-suggest__group" role="presentation">${esc((group = it.group))}</li>` : "";
          return `${head}<li class="bw-suggest__item" role="option" id="${wrap.dataset.picker}-opt-${i}" data-index="${i}" aria-selected="${i === active}"><span>${esc(it.label)}</span><span class="bw-suggest__meta">${esc(it.meta)}</span></li>`;
        }).join("");
      }
      list.classList.remove("hidden");
      input.setAttribute("aria-expanded", "true");
      if (active >= 0) input.setAttribute("aria-activedescendant", `${wrap.dataset.picker}-opt-${active}`); else input.removeAttribute("aria-activedescendant");
    };
    const pick = (i) => { const it = items[i]; if (it) { close(); input.value = ""; onPick(it.spec, it.label); } };
    const load = debounce(async () => {
      const text = input.value.trim();
      const mine = ++seq;
      try {
        const data = text ? await api(`/api/market-radar/categories${qs({ q: text })}`) : { presets: (await ensureOptions()).presets, cpv: [] };
        if (mine !== seq) return;
        items = suggestionItems(data, text);
        active = -1;
        paint();
      } catch (err) {
        if (mine === seq) { items = []; list.innerHTML = `<li class="bw-suggest__empty">${esc(err.message)}</li>`; list.classList.remove("hidden"); }
      }
    }, 220);

    input.addEventListener("input", load);
    input.addEventListener("focus", () => { if (!items.length) load(); else paint(); });
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        if (list.classList.contains("hidden")) { load(); return; }
        e.preventDefault();
        active = (active + (e.key === "ArrowDown" ? 1 : -1) + items.length) % Math.max(items.length, 1);
        paint();
        list.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
      } else if (e.key === "Enter") {
        if (active >= 0) { e.preventDefault(); pick(active); }
        else if (input.value.trim().length >= 2) { e.preventDefault(); close(); const text = input.value.trim(); input.value = ""; onPick({ q: text }, text); }
      } else if (e.key === "Escape") close();
    });
    list.addEventListener("mousedown", (e) => {
      const li = e.target.closest("[data-index]");
      if (li) { e.preventDefault(); pick(Number(li.dataset.index)); }
    });
    input.addEventListener("blur", () => setTimeout(close, 120));
  }

  // ── Market Radar: pure renderers ────────────────────────────────────────────────────────────
  const ROUTE_HINT = "How the contract was awarded, as stated in the notice";

  function valueCell(v, isCeiling) {
    if (v == null) return '<span class="bw-faint">Value not published</span>';
    return isCeiling ? `${fmtMoney(v)} <span class="bw-badge bw-badge--warn" title="A framework value is a ceiling shared by every supplier appointed, not what one buyer spent">Framework ceiling</span>` : fmtMoney(v);
  }
  function awardLine(a) {
    const bits = [a.supplier ? esc(displayName(a.supplier)) : "Supplier not stated", valueCell(a.value, a.value_is_ceiling)];
    const when = a.started || a.signed;
    // why it is here: the notice's CPV code or a title word; and, when one published value is carried by several supplier
    // rows, that this row is only its share of it (the notice value counts once in every total)
    const shared = a.shared_with > 1 && a.notice_value ? `share of a ${fmtMoney(a.notice_value)} notice with ${fmtInt(a.shared_with)} suppliers` : null;
    const meta = [a.route && a.route !== "Not stated" ? esc(a.route) : null, when ? `started ${fmtDate(when)}` : null, a.ends ? `ends ${fmtDate(a.ends)}` : null,
      shared, a.matched_by ? `in this category by ${esc(a.matched_by)}` : null].filter(Boolean).join(" · ");
    const link = safeUrl(a.url);
    const title = link ? `<a class="bw-link" href="${esc(link)}" target="_blank" rel="noopener noreferrer">${esc(a.title || "Notice")}</a>` : esc(a.title || "Notice");
    return `<li>${title}<span class="bw-sub">${bits.join(" · ")}${meta ? " · " + meta : ""}</span></li>`;
  }

  function peersTableHtml(rows) {
    if (!rows.length) return '<div class="bw-empty"><strong>No buyers match</strong>Try a wider authority type or time window.</div>';
    const body = rows.map((p) => {
      const latest = p.latest || {};
      const titleText = latest.title ? esc(latest.title) : '<span class="bw-faint">Title not published</span>';
      const dateText = p.latest_signed ? `awarded ${fmtDate(p.latest_signed)}` : (latest.started ? `started ${fmtDate(latest.started)}` : '');
      const subBit = [valueCell(latest.value, latest.value_is_ceiling), dateText].filter(Boolean).join(" · ");
      return `<tr class="bw-peer" data-key="${esc(p.key)}">
        <td class="bw-cell-main" data-label="Buyer"><span class="bw-name">${esc(displayName(p.buyer))}</span>${p.is_me ? ' <span class="bw-badge bw-badge--accent">Your organisation</span>' : ""}
          <span class="bw-sub">${esc(p.type_label)}</span></td>
        <td class="num" data-label="Awards">${fmtInt(p.awards)}${p.frameworks ? `<span class="bw-sub">${fmtInt(p.frameworks)} framework${p.frameworks === 1 ? "" : "s"}</span>` : ""}</td>
        <td data-label="Main supplier">${p.main_supplier ? esc(displayName(p.main_supplier)) : '<span class="bw-faint">–</span>'}${p.main_supplier && p.main_supplier_key ? `<button class="bw-btn bw-btn--sm bw-row-btn" type="button" data-action="similar-suppliers" data-supplier="${esc(p.main_supplier_key)}">View similar suppliers</button>` : ""}</td>
        <td data-label="Latest contract">${titleText}
          <span class="bw-sub">${subBit}</span></td>
        <td data-label="Route"><span class="bw-badge" title="${ROUTE_HINT}">${esc(latest.route || "Not stated")}</span></td>
        <td class="num"><div class="bw-row-actions"><button class="bw-link" type="button" data-action="peer-toggle" data-key="${esc(p.key)}" aria-expanded="false">Contracts</button>${S.insights ? `<button class="bw-btn bw-btn--sm" type="button" data-action="peer-insights" data-key="${esc(p.key)}">Insights</button>` : ""}</div></td>
      </tr>
      <tr class="bw-detail hidden" data-detail="${esc(p.key)}"><td colspan="6"><div class="bw-detail__body"></div></td></tr>`;
    }).join("");
    return `<div class="bw-table-wrap"><table class="bw-table bw-table--stack"><thead><tr>
      <th>Buyer</th><th class="num">Awards</th><th>Main supplier</th><th>Latest contract</th><th>Route</th><th></th></tr></thead><tbody>${body}</tbody></table></div>`;
  }

  function pagerHtml(peers) {
    if (peers.pages <= 1) return `<div class="bw-pager"><span>${plural(peers.total, "buyer")}</span></div>`;
    return `<div class="bw-pager"><span>${plural(peers.total, "buyer")} · page ${peers.page} of ${peers.pages}</span>
      <span class="bw-actions" style="margin:0"><button class="bw-btn bw-btn--sm" data-action="peers-page" data-page="${peers.page - 1}" ${peers.page <= 1 ? "disabled" : ""}>Previous</button>
      <button class="bw-btn bw-btn--sm" data-action="peers-page" data-page="${peers.page + 1}" ${peers.page >= peers.pages ? "disabled" : ""}>Next</button></span></div>`;
  }

  function peersPanelHtml(data, view) {
    const p = data.peers;
    return `<div class="bw-toolbar">
        <input class="bw-input" type="search" id="peerSearch" placeholder="Find a buyer by name" value="${esc(view.search || "")}" aria-label="Find a buyer by name">
        <label class="bw-muted" for="peerSort" style="font-size:13px">Sort by</label>
        <select class="bw-select" id="peerSort" aria-label="Sort buyers">
          ${[["recent", "Most recent award"], ["awards", "Most awards"], ["value", "Highest contract value"], ["name", "Name"]].map(([v, l]) => `<option value="${v}" ${p.sort === v ? "selected" : ""}>${l}</option>`).join("")}
        </select>
      </div>
      <div id="peersBody">${peersTableHtml(p.rows)}${pagerHtml(p)}</div>
      <p class="bw-hint">One row per buyer, with spelling variants of a council's name merged. “Contracts” lists that buyer's awards in this category. Framework and call-off values are shared ceilings and are never counted as spend.</p>`;
  }

  function priceBandBadge(band) {
    if (!band) return '<span class="bw-faint">–</span>';
    return `<span class="bw-badge bw-badge--info" title="Relative to the other suppliers in this category (tertiles of average contract value)">${esc(band)}</span>`;
  }
  function suppliersPanelHtml(data) {
    const rows = data.suppliers.rows;
    if (!rows.length) return '<div class="bw-empty"><strong>No suppliers found</strong>There are no named suppliers in this view.</div>';
    const top = rows.slice(0, 12);
    const max = Math.max(...top.map((s) => s.buyers), 1);
    const bars = top.map((s) => `<div class="bw-bar"><div class="bw-bar__label" title="${esc(displayName(s.supplier))}">${esc(displayName(s.supplier))}</div>
      <div class="bw-bar__track"><div class="bw-bar__fill" style="width:${(s.buyers / max * 100).toFixed(1)}%"></div></div><div class="bw-bar__val">${fmtInt(s.buyers)}</div></div>`).join("");
    const hasRepeat = rows.some((s) => s.repeat_rate != null);
    const hasPriceBand = rows.some((s) => s.price_band != null);
    const body = rows.map((s) => `<tr>
      <td class="bw-cell-main" data-label="Supplier"><span class="bw-name">${esc(displayName(s.supplier))}</span>
        ${s.latest_signed ? `<span class="bw-sub">latest award ${fmtDate(s.latest_signed)}</span>` : ""}</td>
      <td class="num" data-label="Buyers">${fmtInt(s.buyers)}</td>
      <td class="num" data-label="Contracts">${fmtInt(s.contracts)}</td>
      <td class="num" data-label="Frameworks">${fmtInt(s.framework_appointments)}</td>
      <td class="num" data-label="Total value">${s.total_value == null ? "–" : fmtMoney(s.total_value)}</td>
      <td class="num" data-label="Average contract">${s.avg_contract == null ? "–" : fmtMoney(s.avg_contract)}${s.valued_contracts ? `<span class="bw-sub">${fmtInt(s.valued_contracts)} with a value</span>` : ""}</td>
      <td class="num" data-label="Average term">${s.avg_term_months == null ? "–" : `${trimNum(s.avg_term_months / 12, 1)} yrs`}</td>
      ${hasRepeat ? `<td class="num" data-label="Repeat business">${s.repeat_rate == null ? "–" : fmtPct(s.repeat_rate)}</td>` : ""}
      ${hasPriceBand ? `<td data-label="Price band">${priceBandBadge(s.price_band)}</td>` : ""}</tr>`).join("");
    return `<div class="bw-card"><h2 class="bw-card__title">Adoption: how many different buyers use each supplier</h2>
        <p class="bw-card__sub">${plural(data.suppliers.total, "supplier")} in this view; the ${top.length} used by the most buyers.</p>${bars}</div>
      <div class="bw-card"><h2 class="bw-card__title">Full comparison</h2>
        <div class="bw-table-wrap"><table class="bw-table bw-table--stack"><thead><tr><th>Supplier</th><th class="num">Buyers</th><th class="num">Contracts</th><th class="num">Frameworks</th>
          <th class="num">Total value</th><th class="num">Avg contract</th><th class="num">Avg term</th>${hasRepeat ? `<th class="num" title="Share of this supplier's buyers that awarded them more than one contract in this category (shown from 3 buyers)">Repeat business</th>` : ""}${hasPriceBand ? `<th>Price band</th>` : ""}</tr></thead>
          <tbody>${body}</tbody></table></div>
        <p class="bw-hint">Totals and averages use only direct contracts with a published value. “Repeat business” is the share of a supplier's buyers who awarded them more than one contract here. Price band compares suppliers within this category; it is not an absolute price tier. There are no ratings: award notices do not contain any.</p></div>`;
  }

  function niceTicks(lo, hi) {
    const all = [];
    for (let k = Math.floor(Math.log10(lo)); k <= Math.ceil(Math.log10(hi)); k++) {
      [1, 2, 5].forEach((m) => { const v = m * Math.pow(10, k); if (v >= lo && v <= hi) all.push(v); });
    }
    // too many labels crowd the axis: keep only the powers of ten
    return all.length > 8 ? all.filter((v) => Math.abs(v / Math.pow(10, Math.floor(Math.log10(v))) - 1) < 1e-9) : all;
  }
  function costPanelHtml(cost, basis) {
    const hasAnnual = Boolean(cost.overall.annual);
    const use = basis === "annual" && !hasAnnual ? "total" : basis;
    const single = cost.by_type.length === 1 ? { ...cost.overall, label: cost.by_type[0].label } : null;
    const blocks = (single ? [single] : [cost.overall, ...cost.by_type]).filter((b) => b[use]);
    const seg = `<div class="bw-seg" role="group" aria-label="Value basis">
      <button type="button" data-action="cost-basis" data-basis="annual" aria-pressed="${use === "annual"}" ${hasAnnual ? "" : "disabled"}>Annual value</button>
      <button type="button" data-action="cost-basis" data-basis="total" aria-pressed="${use === "total"}">Total contract value</button></div>`;
    if (!blocks.length) {
      return `<div class="bw-card"><div class="bw-head__row"><h2 class="bw-card__title">Typical contract values</h2>${seg}</div>
        <div class="bw-empty"><strong>Not enough published values</strong>Fewer than 5 direct contracts in this view state a value, so no range is shown. Try a wider time window or all public bodies.</div></div>`;
    }
    const lo = Math.min(...blocks.map((b) => b[use].p10)) / 1.6;
    const hi = Math.max(...blocks.map((b) => b[use].p90)) * 1.6;
    const L = Math.log10(Math.max(lo, 1)), H = Math.log10(Math.max(hi, 2));
    const pos = (v) => clamp((Math.log10(Math.max(v, 1)) - L) / (H - L) * 100, 0, 100);
    const rows = blocks.map((b) => {
      const r = b[use];
      const n = use === "annual" ? b.annualised : b.contracts;
      return `<div class="bw-range"><div class="bw-range__head"><b>${esc(b.label)}</b>
          <span>${fmtMoney(r.p10)} to ${fmtMoney(r.p90)}${use === "annual" ? " a year" : ""} · typical ${fmtMoney(r.median)}</span></div>
        <div class="bw-range__track" role="img" aria-label="${esc(b.label)}: ${fmtMoney(r.p10)} to ${fmtMoney(r.p90)}, typical ${fmtMoney(r.median)}">
          <div class="bw-range__fill" style="left:${pos(r.p10).toFixed(1)}%;width:${Math.max(pos(r.p90) - pos(r.p10), 0.8).toFixed(1)}%"></div>
          <div class="bw-range__mid" style="left:${pos(r.median).toFixed(1)}%"></div></div>
        <div class="bw-range__foot">${plural(n, "contract")}${use === "annual" ? ` with a stated term (of ${fmtInt(b.contracts)} with a value)` : ""} · smallest ${fmtMoney(r.min)}, largest ${fmtMoney(r.max)}</div></div>`;
    }).join("");
    const axis = niceTicks(Math.pow(10, L), Math.pow(10, H)).map((v) => `<span style="left:${pos(v).toFixed(1)}%">${fmtMoney(v)}</span>`).join("");
    return `<div class="bw-card"><div class="bw-head__row"><h2 class="bw-card__title">${use === "annual" ? "Typical annual value" : "Typical total contract value"}</h2>${seg}</div>
        <p class="bw-card__sub">${use === "annual"
          ? "Notice value divided by the stated contract term, for contracts that publish start and end dates."
          : "The total value published in the award notice, whatever the term."}</p>
        ${rows}<div class="bw-axis" aria-hidden="true">${axis}</div>
        <div class="bw-legend"><span>■ Bar: the middle 80% of contracts (10th to 90th percentile)</span><span>▍ Marker: median</span><span>Log scale</span></div></div>
      <div class="bw-card"><h2 class="bw-card__title">What this does not tell you</h2>
        <p class="bw-card__sub" style="margin:0">Award notices publish one total value. They do not split licence or service charges from support, maintenance, implementation or data migration, so none of those are shown here.
          ${fmtInt(cost.excluded_frameworks)} framework or call-off awards (shared ceilings) and ${fmtInt(cost.excluded_no_value)} awards without a value are left out.${cost.excluded_outliers ? ` The typical range also excludes ${plural(cost.excluded_outliers, "very large award")} of £250m or more.` : ""}
          Ask shortlisted suppliers for a worked total cost of ownership as part of your own process.</p></div>`;
  }

  function kpiHtml(summary) {
    const cov = summary.coverage || {};
    return `<div class="bw-grid bw-grid--4">
      <div class="bw-stat"><div class="bw-stat__label">Awards</div><div class="bw-stat__value">${fmtInt(summary.awards)}</div><div class="bw-stat__hint">${fmtInt(summary.frameworks)} frameworks or call-offs</div></div>
      <div class="bw-stat"><div class="bw-stat__label">Buyers</div><div class="bw-stat__value">${fmtInt(summary.buyers)}</div><div class="bw-stat__hint">distinct public bodies</div></div>
      <div class="bw-stat"><div class="bw-stat__label">Suppliers</div><div class="bw-stat__value">${fmtInt(summary.suppliers)}</div><div class="bw-stat__hint">named in the notices</div></div>
      <div class="bw-stat"><div class="bw-stat__label">Published value</div><div class="bw-stat__value">${summary.total_value == null ? "–" : fmtMoney(summary.total_value)}</div>
        <div class="bw-stat__hint">${cov.with_value == null ? "" : `${fmtPct(cov.with_value)} of awards state a direct value`}</div></div></div>`;
  }

  // ── Market Radar: view ──────────────────────────────────────────────────────────────────────
  const radar = { data: null, spec: null, filters: null, tab: "peers", basis: "annual", peers: { search: "", sort: "recent", page: 1 }, awards: {}, controller: null };

  function defaultAuthority(org) {
    if (!org) return "all";
    return org.type === "local-other" ? "local-government" : org.type;
  }
  function radarFilters(params) {
    return {
      authority: params.get("authority") || defaultAuthority(S.org),
      window: params.get("window") || "3y",
      scope: params.get("scope") || "all",
    };
  }
  function radarQuery(spec, f, extra = {}) {
    return qs({ category: spec.preset, cpv: spec.cpv, q: spec.q, authority: f.authority, window: f.window, scope: f.scope, ...extra });
  }
  function radarParams(spec, f, tab) {
    const p = specToParams(spec);
    p.set("authority", f.authority); p.set("window", f.window); if (f.scope !== "all") p.set("scope", f.scope);
    if (tab && tab !== "peers") p.set("tab", tab);
    return p;
  }

  function selectHtml(id, label, options, value) {
    return `<label class="bw-filters__field"><span>${esc(label)}</span><select class="bw-select" id="${id}">${options.map((o) => `<option value="${esc(o.id)}" ${o.id === value ? "selected" : ""}>${esc(o.label)}</option>`).join("")}</select></label>`;
  }

  function radarHeaderHtml(opts) {
    const chips = [...S.watchlist.map((c) => ({ spec: specOf(c), label: c.label, star: true })), ...opts.presets.map((p) => ({ spec: { preset: p.preset }, label: p.label }))];
    const seen = new Set();
    const unique = chips.filter((c) => { const k = JSON.stringify(c.spec); if (seen.has(k)) return false; seen.add(k); return true; });
    return `<div class="bw-page"><div class="bw-head"><h1>Market Radar</h1>
      <p>Research a category before you write the specification: who else has bought it, from whom, how, and for roughly what. Built from published contract award notices.</p></div>
      <div class="bw-card"><h2 class="bw-card__title">What are you researching?</h2>
        <p class="bw-card__sub">Pick a category, or search for one: “CRM system”, “grounds maintenance”, or a CPV code such as 50720000.</p>
        ${pickerHtml("radarPicker", "Search a category or CPV code…")}
        <div class="bw-chips" style="margin-top:12px">${unique.map((c) => `<button type="button" class="bw-chip" data-action="radar-pick" data-spec='${esc(JSON.stringify(c.spec))}'>${c.star ? "★ " : ""}${esc(c.label)}</button>`).join("")}</div></div>
      <div id="radarResults"></div></div>`;
  }

  function radarResultsHtml(data, opts) {
    const f = radar.filters;
    const cat = data.category;
    const isFollowed = followed(cat);
    const sameType = S.org && f.authority === defaultAuthority(S.org);
    const filters = `<div class="bw-filters">
      <div class="bw-filters__field"><span>Category</span><span class="bw-cat-pill"><span title="${esc(cat.label)}">${esc(cat.label)}</span><button type="button" data-action="radar-clear" aria-label="Clear category">×</button></span></div>
      ${selectHtml("fAuthority", "Authority type", opts.authority_types, f.authority)}
      ${selectHtml("fWindow", "Awarded", opts.windows, f.window)}
      <label class="bw-filters__check"><input type="checkbox" id="fScope" ${f.scope === "direct" ? "checked" : ""}> Direct contracts only</label>
      <span class="bw-filters__grow"></span>
      <button type="button" class="bw-btn" data-action="radar-plan">Plan a market engagement</button>
      <button type="button" class="bw-btn bw-follow" data-action="radar-follow" aria-pressed="${isFollowed}">${isFollowed ? "★ Following" : "☆ Follow category"}</button></div>
      ${S.org && sameType ? `<p class="bw-hint" style="margin:-8px 0 14px">Showing buyers of the same type as ${esc(S.org.name)} (${esc(S.org.type_label)}). Choose “All public bodies” to see everyone.</p>` : ""}`;
    const trunc = data.truncated ? '<div class="bw-note bw-note--warn">This category is very broad, so only the most recent 60,000 awards are analysed. Narrow it with a CPV code or search words for a complete picture.</div>' : "";
    const empty = data.summary.awards === 0
      ? '<div class="bw-card"><div class="bw-empty"><strong>No awards found</strong>Nothing in the published award notices matches this category, authority type and time window. Try a wider window, “All public bodies”, or different words.</div></div>' : "";
    const tabs = [["peers", "Peer benchmarking"], ["suppliers", "Supplier comparison"], ["cost", "Cost benchmark"]];
    return `${filters}${trunc}${kpiHtml(data.summary)}${empty}
      ${data.summary.awards === 0 ? "" : `<div class="bw-tabs" role="tablist" aria-label="Market Radar views">${tabs.map(([id, label]) => `<button type="button" role="tab" class="bw-tab" id="tab-${id}" aria-controls="panel-${id}" aria-selected="${radar.tab === id}" tabindex="${radar.tab === id ? 0 : -1}" data-action="radar-tab" data-tab="${id}">${label}</button>`).join("")}</div>
      <div id="radarPanel" role="tabpanel" aria-labelledby="tab-${radar.tab}"></div>
      <p class="bw-hint" style="margin-top:18px">${esc(data.notes.values)} ${esc(data.notes.size)} Based on notices published ${data.filters.window_label.toLowerCase()}; analysed ${esc(fmtDate(data.computed_at))}.</p>`}`;
  }

  function paintRadarPanel() {
    const panel = document.getElementById("radarPanel");
    if (!panel || !radar.data) return;
    const data = radar.data;
    panel.setAttribute("aria-labelledby", `tab-${radar.tab}`);
    panel.innerHTML = radar.tab === "suppliers" ? suppliersPanelHtml(data) : radar.tab === "cost" ? costPanelHtml(data.cost, radar.basis) : `<div class="bw-card">${peersPanelHtml(data, radar.peers)}</div>`;
    document.querySelectorAll("#bwMain [data-action='radar-tab']").forEach((b) => {
      const on = b.dataset.tab === radar.tab;
      b.setAttribute("aria-selected", String(on));
      b.tabIndex = on ? 0 : -1;
    });
  }

  async function loadRadar(ctx, opts) {
    const results = document.getElementById("radarResults");
    results.innerHTML = `<div class="bw-card">${skeleton(5)}</div>`;
    if (radar.controller) radar.controller.abort();
    radar.controller = new AbortController();
    const { signal } = radar.controller;
    let data;
    try {
      data = await api(`/api/market-radar/analysis${radarQuery(radar.spec, radar.filters, { sort: radar.peers.sort, per_page: 25 })}`, { signal });
    } catch (err) {
      if (err && err.name === "AbortError") return;
      if (ctx.isCurrent()) results.innerHTML = `<div class="bw-card"><div class="bw-empty"><strong>That did not load</strong>${esc(err.message)}<div class="bw-actions" style="justify-content:center"><button class="bw-btn" data-action="retry">Try again</button></div></div></div>`;
      return;
    }
    if (!ctx.isCurrent()) return;
    radar.data = data;
    radar.peers.page = 1;
    radar.awards = {};
    results.innerHTML = radarResultsHtml(data, opts);
    paintRadarPanel();
  }

  async function renderRadar(ctx) {
    const opts = await ensureOptions();
    if (!ctx.isCurrent()) return;
    ctx.main.innerHTML = radarHeaderHtml(opts);
    bindPicker(ctx.main, (spec) => navigate(buildHash(["radar"], radarParams(spec, radarFilters(new URLSearchParams()), "peers"))));
    radar.spec = specFromParams(ctx.params);
    S.lastCategory = radar.spec;   // a new engagement started afterwards begins on this category
    if (!radar.spec) { radar.data = null; return; }
    radar.filters = radarFilters(ctx.params);
    radar.tab = ["peers", "suppliers", "cost"].includes(ctx.params.get("tab")) ? ctx.params.get("tab") : "peers";
    radar.peers = { search: "", sort: "recent", page: 1 };
    radar.basis = "annual";
    await loadRadar(ctx, opts);
  }

  async function refreshPeers() {
    const body = document.getElementById("peersBody");
    if (!body || !radar.data) return;
    body.style.opacity = "0.55";
    try {
      const page = await api(`/api/market-radar/peers${radarQuery(radar.spec, radar.filters, { sort: radar.peers.sort, search: radar.peers.search, page: radar.peers.page, per_page: 25 })}`);
      radar.data.peers = page;
      radar.peers.page = page.page;
      body.innerHTML = peersTableHtml(page.rows) + pagerHtml(page);
    } catch (err) { toast(err.message, true); }
    body.style.opacity = "";
  }

  async function togglePeerAwards(key, button) {
    const row = document.querySelector(`tr[data-detail="${CSS.escape(key)}"]`);
    if (!row) return;
    const open = row.classList.toggle("hidden") === false;
    button.setAttribute("aria-expanded", String(open));
    button.textContent = open ? "Hide" : "Contracts";
    if (!open) return;
    const box = row.querySelector(".bw-detail__body");
    if (!radar.awards[key]) {
      box.innerHTML = skeleton(2);
      try { radar.awards[key] = await api(`/api/market-radar/peers/awards${radarQuery(radar.spec, radar.filters, { buyer: key })}`); }
      catch (err) { box.innerHTML = `<div class="bw-note bw-note--bad">${esc(err.message)}</div>`; return; }
    }
    const d = radar.awards[key];
    box.innerHTML = `<ul class="bw-detail-list">${d.awards.map(awardLine).join("")}</ul>${d.total_awards > d.shown ? `<p class="bw-hint">Showing the ${d.shown} most recent of ${fmtInt(d.total_awards)}.</p>` : ""}`;
  }


  // ── Market Radar: Insights and similar-suppliers drawer ─────────────────────────────────────
  const AUTHORITY_ROUTE = { 1: "Competitive", 0: "Direct award" };

  function statTile(label, value, sub) {
    return `<div class="bw-stat"><div class="bw-stat__label">${esc(label)}</div><div class="bw-stat__value bw-stat__value--sm">${value}</div>${sub ? `<div class="bw-stat__hint">${esc(sub)}</div>` : ""}</div>`;
  }
  function drawerSection(title, inner) { return `<section class="bw-drawer__section"><h3>${esc(title)}</h3>${inner}</section>`; }

  // The factual half of the Insights drawer: Buyer Intelligence profile + the buyer's awards in this category.
  function insightFactsHtml(d) {
    const p = d.profile, st = p && p.stats, c = d.in_category;
    const parts = [];
    if (st) {
      const route = (st.competitive_awards || st.direct_awards) ? `${fmtInt(st.competitive_awards)} competitive · ${fmtInt(st.direct_awards)} direct` : "";
      parts.push(`<div class="bw-drawer__stats">
        ${statTile("Authority type", esc(d.type_label || p.buyer_type || "–"))}
        ${statTile("Contracts on record", fmtInt(st.total_contracts), st.earliest_award ? `${fmtDate(st.earliest_award)} to ${fmtDate(st.latest_award)}` : "")}
        ${statTile("Published spend", st.total_spend ? fmtMoney(st.total_spend) : '<span class="bw-faint">Not published</span>', "excludes framework ceilings")}
        ${statTile("Suppliers used", fmtInt(st.unique_suppliers), route)}</div>`);
    } else {
      parts.push('<div class="bw-note bw-note--info">The full buyer profile could not be loaded, so only this category’s figures are shown.</div>');
    }
    parts.push(drawerSection(`In ${d.category.label}`, `<p class="bw-hint" style="margin:0 0 6px">${plural(c.awards, "award")}${c.frameworks ? ` (${plural(c.frameworks, "framework appointment")})` : ""} from ${plural(c.suppliers, "supplier")}${c.total_value ? `, ${fmtMoney(c.total_value)} published value` : ""}.</p>
      <ul class="bw-detail-list">${c.recent_awards.map(awardLine).join("")}</ul>${c.awards > c.shown ? `<p class="bw-hint">Showing the ${c.shown} most recent; use Contracts for the full list.</p>` : ""}`));
    if (p) {
      if (p.top_suppliers.length) parts.push(drawerSection("Main suppliers", `<ul class="bw-detail-list">${p.top_suppliers.map((s) => `<li>${esc(displayName(s.supplier))}<span class="bw-sub">${plural(s.contracts, "contract")}${s.value ? ` · ${fmtMoney(s.value)}` : ""}${s.framework_appointments ? ` · ${plural(s.framework_appointments, "framework appointment")}` : ""}</span></li>`).join("")}</ul>`));
      if (p.sectors.length) parts.push(drawerSection("Frequent sectors", `<ul class="bw-detail-list">${p.sectors.map((x) => `<li>${esc(x.label || x.cpv)}<span class="bw-sub">${plural(x.awards, "award")}</span></li>`).join("")}</ul>`));
      if (p.recent_awards.length) parts.push(drawerSection("Recent award history (all categories)", `<ul class="bw-detail-list">${p.recent_awards.map((a) => awardLine({ ...a, route: AUTHORITY_ROUTE[a.competitive] || null })).join("")}</ul>`));
    }
    return parts.join("");
  }

  // The AI half. `r` is the /peers/insight reply; null while it is still being written.
  function insightNarrativeHtml(r) {
    const head = "AI insight";
    if (!r) return drawerSection(head, `<div class="bw-insight bw-insight--wait" role="status">Writing a summary from the figures in this panel…</div>`);
    if (r.available) {
      const basis = (r.based_on || []).map((f) => `<li>${esc(f)}</li>`).join("");
      return drawerSection(head, `<div class="bw-insight"><p>${esc(r.narrative)}</p>
        <p class="bw-hint" style="margin:8px 0 0">Written by ${esc(r.provider || "AI")} from published award notices only. Check the figures against the contracts listed here.</p>
        ${basis ? `<details class="bw-insight__basis"><summary>Facts it was given</summary><ul>${basis}</ul></details>` : ""}</div>`);
    }
    if (r.reason === "thin") return drawerSection(head, `<div class="bw-note bw-note--info" style="margin:0">${esc(r.message)}</div>`);
    return drawerSection(head, `<p class="bw-hint" style="margin:0">${esc(r.message || "Insight unavailable right now.")} The figures above are unaffected.</p>`);
  }

  function similarSuppliersHtml(d) {
    const basis = d.basis === "cpv"
      ? `Ranked by awards under the same CPV classes as ${esc(displayName(d.supplier))} in ${esc(d.category.label)}${d.cpv.length ? `: ${d.cpv.slice(0, 5).map((c) => esc(d.cpv_labels && d.cpv_labels[c] ? `${c} ${d.cpv_labels[c]}` : c)).join("; ")}` : ""}.`
      : `${esc(displayName(d.supplier))}’s notices carry no CPV code, so every other supplier in ${esc(d.category.label)} is listed, ranked by awards.`;
    if (!d.rows.length) return `<p class="bw-hint">${basis}</p><div class="bw-empty"><strong>No similar suppliers found</strong>No other supplier in this view has awards under the same CPV classes.</div>`;
    const rows = d.rows.map((r) => `<tr><td class="bw-cell-main" data-label="Supplier"><span class="bw-name">${esc(displayName(r.supplier))}</span>${r.shared_cpv.length ? `<span class="bw-sub">CPV ${esc(r.shared_cpv.join(", "))}</span>` : ""}</td>
      <td class="num" data-label="Awards">${fmtInt(r.shared_awards)}${r.awards > r.shared_awards ? `<span class="bw-sub">of ${fmtInt(r.awards)}</span>` : ""}</td>
      <td class="num" data-label="Value">${r.shared_value ? fmtMoney(r.shared_value) : '<span class="bw-faint">–</span>'}</td>
      <td class="num" data-label="Buyers">${fmtInt(r.buyers)}</td></tr>`).join("");
    return `<p class="bw-hint" style="margin-top:0">${basis}</p>
      <div class="bw-table-wrap"><table class="bw-table bw-table--stack"><thead><tr><th>Supplier</th><th class="num" title="Awards under the shared CPV classes">Awards</th><th class="num" title="Published value of those awards, framework ceilings excluded">Value</th><th class="num">Buyers</th></tr></thead><tbody>${rows}</tbody></table></div>
      <p class="bw-hint">${d.total > d.rows.length ? `Showing ${d.rows.length} of ${fmtInt(d.total)}. ` : ""}Values are published notice values, not invoiced spend.</p>`;
  }

  let drawerReturnFocus = null;
  let drawerSeq = 0;
  function openDrawer(title, html) {
    const el = document.getElementById("bwDrawer");
    if (el.classList.contains("hidden")) drawerReturnFocus = document.activeElement;
    document.getElementById("bwDrawerTitle").textContent = title;
    document.getElementById("bwDrawerBody").innerHTML = html;
    el.classList.remove("hidden");
    el.querySelector("[data-action='drawer-close']").focus();
  }
  function closeDrawer() {
    drawerSeq++;
    const el = document.getElementById("bwDrawer");
    if (!el || el.classList.contains("hidden")) return;
    el.classList.add("hidden");
    document.getElementById("bwDrawerBody").innerHTML = "";
    if (drawerReturnFocus && drawerReturnFocus.focus) drawerReturnFocus.focus();
    drawerReturnFocus = null;
  }

  async function openInsights(key) {
    const seq = ++drawerSeq;
    const peer = (radar.data && radar.data.peers.rows.find((p) => p.key === key)) || {};
    openDrawer(`Insights: ${displayName(peer.buyer || "buyer")}`, skeleton(3));
    const body = () => (seq === drawerSeq ? document.getElementById("bwDrawerBody") : null);
    let d;
    try { d = await api(`/api/market-radar/peers/profile${radarQuery(radar.spec, radar.filters, { buyer: key })}`); }
    catch (err) { const b = body(); if (b) b.innerHTML = `<div class="bw-note bw-note--bad">${esc(err.message)}</div>`; return; }
    if (seq !== drawerSeq) return;
    document.getElementById("bwDrawerTitle").textContent = `Insights: ${displayName(d.buyer)}`;
    const paint = (r) => { const b = body(); if (b) b.innerHTML = insightFactsHtml(d) + insightNarrativeHtml(r); };
    paint(null);
    let r;
    try { r = await api(`/api/market-radar/peers/insight${radarQuery(radar.spec, radar.filters, { buyer: key })}`, { method: "POST" }); }
    catch (err) { r = { available: false, reason: "unavailable", message: "Insight unavailable right now." }; }
    paint(r);
  }

  async function openSimilarSuppliers(supplierKey) {
    const seq = ++drawerSeq;
    const peer = radar.data && radar.data.peers.rows.find((p) => p.main_supplier_key === supplierKey);
    openDrawer(`Similar to ${displayName((peer && peer.main_supplier) || "supplier")}`, skeleton(3));
    try {
      const d = await api(`/api/market-radar/similar-suppliers${radarQuery(radar.spec, radar.filters, { supplier: supplierKey })}`);
      if (seq !== drawerSeq) return;
      document.getElementById("bwDrawerBody").innerHTML = similarSuppliersHtml(d);
    } catch (err) {
      if (seq === drawerSeq) document.getElementById("bwDrawerBody").innerHTML = `<div class="bw-note bw-note--bad">${esc(err.message)}</div>`;
    }
  }

  // ── dashboard ───────────────────────────────────────────────────────────────────────────────
  function renewalsTableHtml(items) {
    const rows = items.map((r) => `<tr>
      <td class="bw-cell-main" data-label="Contract"><span class="bw-name">${safeUrl(r.url) ? `<a class="bw-link" href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">${esc(r.title || "Notice")}</a>` : esc(r.title || "Notice")}</span>
        <span class="bw-sub">${esc(r.supplier || "Supplier not stated")}</span></td>
      <td class="num" data-label="Value">${valueCell(r.value, r.value_is_ceiling)}</td>
      <td data-label="Ends">${fmtDate(r.ends)}<span class="bw-sub">${r.days_left == null ? "" : `in ${plural(r.days_left, "day")}`}</span></td>
      <td data-label="Route"><span class="bw-badge">${esc(r.route || "Not stated")}</span></td>
      <td class="num"><button class="bw-btn bw-btn--sm" type="button" data-action="plan-from" data-title="${esc("Re-procurement: " + (r.title || ""))}" data-cpv="${esc(r.cpv || "")}">Plan engagement</button></td></tr>`).join("");
    return `<div class="bw-table-wrap"><table class="bw-table bw-table--stack"><thead><tr><th>Contract</th><th class="num">Value</th><th>Ends</th><th>Route</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }
  function activityTableHtml(items) {
    const rows = items.map((r) => `<tr>
      <td class="bw-cell-main" data-label="Buyer"><span class="bw-name">${esc(displayName(r.buyer))}</span><span class="bw-sub">${esc(r.title || "")}</span></td>
      <td data-label="Category">${r.category ? `<a class="bw-link" href="${esc(buildHash(["radar"], specToParams(specFromKey(r.category_key))))}">${esc(r.category)}</a>` : "–"}</td>
      <td data-label="Supplier">${esc(r.supplier || "Supplier not stated")}</td>
      <td class="num" data-label="Value">${valueCell(r.value, r.value_is_ceiling)}</td>
      <td data-label="Awarded">${fmtDate(r.signed)}</td></tr>`).join("");
    return `<div class="bw-table-wrap"><table class="bw-table bw-table--stack"><thead><tr><th>Buyer</th><th>Category</th><th>Supplier</th><th class="num">Value</th><th>Awarded</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  async function renderDashboard(ctx) {
    ctx.main.innerHTML = `<div class="bw-page"><div class="bw-head"><h1>Dashboard</h1><p>${S.org ? `Procurement view for ${esc(S.org.name)}.` : "Choose your organisation to see your own contract renewals and the peers you are compared with."}</p></div>${skeleton(4)}</div>`;
    const d = await api("/api/buyer-workspace/dashboard");
    if (!ctx.isCurrent()) return;
    if (d.organisation) S.org = d.organisation;
    const renewals = d.renewals;
    const stat = (label, value, hint, href) => `<${href ? `a href="${href}"` : "div"} class="bw-stat${href ? " bw-stat--link" : ""}"><div class="bw-stat__label">${label}</div><div class="bw-stat__value">${value}</div><div class="bw-stat__hint">${hint}</div></${href ? "a" : "div"}>`;
    const unavailable = new Set(d.unavailable || []);
    const html = [
      `<div class="bw-page"><div class="bw-head"><h1>Dashboard</h1><p>${S.org ? `Procurement view for ${esc(S.org.name)}.` : "Choose your organisation to see your own contract renewals and the peers you are compared with."}</p></div>`,
      S.org ? "" : `<div class="bw-note bw-note--info"><strong>Choose your organisation.</strong> It lets TenderFlow show the contracts you have ending soon and compare you with similar buyers. <a class="bw-link" href="#/settings">Choose it now</a></div>`,
      `<div class="bw-grid bw-grid--4">`,
      stat("Contracts ending in the next 6 months", renewals ? fmtInt(renewals.count) : "–", renewals ? "from published award notices" : (unavailable.has("renewals") ? "could not be loaded" : "choose your organisation"), null),
      stat("Active engagements", fmtInt(d.engagements.active), d.engagements.total ? `${fmtInt(d.engagements.total)} in total` : "none started", "#/engagements"),
      stat("Categories followed", fmtInt(d.watchlist.count), d.watchlist.count ? "you are watching these" : "follow one in Market Radar", "#/watchlist"),
      stat("Peer awards in the last 6 months", S.org && d.watchlist.count ? fmtInt(d.peer_activity.length) + (d.peer_activity.length >= 8 ? "+" : "") : "–", S.org ? "in categories you follow" : "needs an organisation", "#/radar"),
      `</div>`,
    ];
    if (renewals) {
      html.push(`<div class="bw-card"><h2 class="bw-card__title">Contracts ending in the next 6 months</h2><p class="bw-card__sub">Your own awards that publish an end date. Planning market engagement early gives you time to shape the next specification.</p>
        ${renewals.items.length ? renewalsTableHtml(renewals.items) : '<div class="bw-empty">None of your published awards end in the next six months.</div>'}
        ${renewals.count > renewals.items.length ? `<p class="bw-hint">Showing the next ${renewals.items.length} of ${fmtInt(renewals.count)}.</p>` : ""}</div>`);
    }
    if (S.org && d.watchlist.count) {
      html.push(`<div class="bw-card"><h2 class="bw-card__title">Recent awards by peers in your followed categories</h2><p class="bw-card__sub">Buyers of the same type as you, awarded in the last six months.</p>
        ${d.peer_activity.length ? activityTableHtml(d.peer_activity) : `<div class="bw-empty">${unavailable.has("peer_activity") ? "Could not be loaded just now." : "No peer awards in these categories lately."}</div>`}</div>`);
    }
    html.push(`<div class="bw-card bw-card--soft"><div class="bw-head__row"><div><h2 class="bw-card__title">Planning a new purchase?</h2><p class="bw-card__sub" style="margin:0">See which buyers like you have bought it, from whom, and for roughly what, before you write the spec.</p></div>
      <a class="bw-btn bw-btn--primary" href="#/radar">Open Market Radar</a></div></div>`);
    if (d.engagements.recent.length) {
      html.push(`<div class="bw-card"><h2 class="bw-card__title">Recent engagements</h2><ul class="bw-detail-list">${d.engagements.recent.map((e) => `<li><a class="bw-link" href="#/engagements/${e.id}">${esc(e.title)}</a> <span class="bw-badge">${esc(e.status)}</span> <span class="bw-faint">updated ${fmtDate(e.updated_at)}</span></li>`).join("")}</ul></div>`);
    }
    html.push("</div>");
    ctx.main.innerHTML = html.join("");
  }

  // ── followed categories ─────────────────────────────────────────────────────────────────────
  function renderWatchlist(ctx) {
    const list = S.watchlist;
    ctx.main.innerHTML = `<div class="bw-page"><div class="bw-head"><h1>Followed categories</h1>
      <p>Categories you follow feed the peer activity on your dashboard. You can follow up to 12.</p></div>
      <div class="bw-card"><h2 class="bw-card__title">Follow a category</h2>${pickerHtml("watchPicker", "Search a category or CPV code…")}</div>
      <div class="bw-card"><h2 class="bw-card__title">You follow ${plural(list.length, "category", "categories")}</h2>
        ${list.length ? `<ul class="bw-detail-list">${list.map((c) => `<li style="display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap">
            <a class="bw-link" href="${buildHash(["radar"], specToParams(specOf(c)))}">${esc(c.label)}</a>
            <button class="bw-btn bw-btn--sm" data-action="unfollow" data-key="${esc(c.key)}">Stop following</button></li>`).join("")}</ul>`
        : '<div class="bw-empty"><strong>Nothing followed yet</strong>Search above, or follow a category from Market Radar.</div>'}</div></div>`;
    bindPicker(ctx.main, async (spec) => {
      try {
        if (S.watchlist.some((c) => JSON.stringify(specOf(c)) === JSON.stringify(spec))) { toast("You already follow that category."); return; }
        await saveWatchlist([...S.watchlist, spec]);
        toast("Category followed");
        renderWatchlist(ctx);
      } catch (err) { toast(err.message, true); }
    });
  }

  // ── organisation ────────────────────────────────────────────────────────────────────────────
  function renderSettings(ctx) {
    const org = S.org;
    ctx.main.innerHTML = `<div class="bw-page"><div class="bw-head"><h1>My organisation</h1>
      <p>Tell TenderFlow which buyer you work for. It is used to find the buyers you are compared with, list your own contracts that are ending, and fill in new engagements.</p></div>
      <div class="bw-card"><h2 class="bw-card__title">Current organisation</h2>
        ${org ? `<p style="margin:0 0 4px"><b>${esc(displayName(org.name))}</b></p><p class="bw-card__sub" style="margin:0">${esc(org.type_label)}${org.contracts ? ` · ${plural(org.contracts, "published contract")}` : ""}</p>
          <div class="bw-actions"><button class="bw-btn bw-btn--sm" data-action="org-clear">Clear</button></div>` : '<p class="bw-card__sub" style="margin:0">None chosen yet.</p>'}</div>
      <div class="bw-card"><h2 class="bw-card__title">Find your organisation</h2>
        <p class="bw-card__sub">Search by name, for example “Camden” or “Leeds City Council”.</p>
        <input class="bw-input" id="orgSearch" type="search" placeholder="Start typing a buyer's name" autocomplete="off" aria-label="Search for your organisation">
        <div id="orgResults" style="margin-top:12px"></div></div>
      <p class="bw-hint">Your organisation is stored with your account and is not shown to other users. Peer comparisons use published award notices only.</p></div>`;
    const input = ctx.main.querySelector("#orgSearch");
    const box = ctx.main.querySelector("#orgResults");
    let seq = 0;
    const run = debounce(async () => {
      const text = input.value.trim();
      const mine = ++seq;
      if (text.length < 2) { box.innerHTML = ""; return; }
      try {
        const res = await api(`/api/buyer-workspace/organisations${qs({ q: text })}`);
        if (mine !== seq) return;
        box.innerHTML = res.results.length
          ? `<ul class="bw-detail-list">${res.results.map((o) => `<li style="display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap"><span><b>${esc(displayName(o.name))}</b> <span class="bw-sub">${esc(o.type_label)} · ${plural(o.contracts, "published contract")}</span></span>
              <button class="bw-btn bw-btn--sm bw-btn--primary" data-action="org-use" data-name="${esc(o.name)}">Use this organisation</button></li>`).join("")}</ul>`
          : '<div class="bw-empty">No buyers match that name.</div>';
      } catch (err) { if (mine === seq) box.innerHTML = `<div class="bw-note bw-note--bad">${esc(err.message)}</div>`; }
    }, 250);
    input.addEventListener("input", run);
  }

  async function setOrganisation(name) {
    const res = await api("/api/buyer-workspace/organisation", { method: "PUT", body: { name } });
    S.org = res.organisation;
    updateMasthead();
    toast(S.org ? `Organisation set to ${S.org.name}` : "Organisation cleared");
  }

  // ── events ──────────────────────────────────────────────────────────────────────────────────
  async function onClick(e) {
    if (!e.target.closest("#bwProfileWrap")) closeProfileMenu();
    const target = e.target.closest("[data-action]");
    if (!target) return;
    const action = target.dataset.action;
    try {
      switch (action) {
        case "retry": route(); break;
        case "modal-close": closeModal(); break;
        case "radar-pick": navigate(buildHash(["radar"], radarParams(JSON.parse(target.dataset.spec), radarFilters(new URLSearchParams()), "peers"))); break;
        case "radar-clear": navigate("#/radar"); break;
        case "radar-tab": radar.tab = target.dataset.tab; replaceHash(buildHash(["radar"], radarParams(radar.spec, radar.filters, radar.tab))); paintRadarPanel(); break;
        case "radar-follow": await toggleFollow(radar.data.category); radarRefreshFollow(); break;
        case "radar-plan": if (BW.openNewEngagement) BW.openNewEngagement({ category: specOf(radar.data.category), title: `${radar.data.category.label}: market engagement` }); break;
        case "cost-basis": radar.basis = target.dataset.basis; paintRadarPanel(); break;
        case "peer-toggle": await togglePeerAwards(target.dataset.key, target); break;
        case "peer-insights": await openInsights(target.dataset.key); break;
        case "similar-suppliers": await openSimilarSuppliers(target.dataset.supplier); break;
        case "drawer-close": closeDrawer(); break;
        case "peers-page": radar.peers.page = Number(target.dataset.page); await refreshPeers(); break;
        case "unfollow": await saveWatchlist(S.watchlist.filter((c) => c.key !== target.dataset.key)); toast("Stopped following"); if (parseHash().parts[0] === "watchlist") renderWatchlist({ main: document.getElementById("bwMain"), isCurrent: () => true }); break;
        case "org-use": await setOrganisation(target.dataset.name); navigate("#/dashboard"); break;
        case "org-clear": await setOrganisation(null); renderSettings({ main: document.getElementById("bwMain") }); break;
        case "plan-from": if (BW.openNewEngagement) BW.openNewEngagement({ title: target.dataset.title, cpv: target.dataset.cpv }); break;
        default: break;
      }
    } catch (err) {
      if (err && err.name !== "AbortError") toast(err.message || "Something went wrong", true);
    }
  }
  function radarRefreshFollow() {
    const btn = document.querySelector("[data-action='radar-follow']");
    if (!btn || !radar.data) return;
    const on = followed(radar.data.category);
    btn.setAttribute("aria-pressed", String(on));
    btn.textContent = on ? "★ Following" : "☆ Follow category";
  }

  function onChange(e) {
    const id = e.target.id;
    if (!radar.spec) return;
    if (id === "fAuthority" || id === "fWindow" || id === "fScope") {
      const f = { ...radar.filters };
      if (id === "fAuthority") f.authority = e.target.value;
      if (id === "fWindow") f.window = e.target.value;
      if (id === "fScope") f.scope = e.target.checked ? "direct" : "all";
      navigate(buildHash(["radar"], radarParams(radar.spec, f, radar.tab)));
    } else if (id === "peerSort") {
      radar.peers.sort = e.target.value; radar.peers.page = 1; refreshPeers();
    }
  }
  const onPeerSearch = debounce((value) => { radar.peers.search = value.trim(); radar.peers.page = 1; refreshPeers(); }, 300);
  function onInput(e) { if (e.target.id === "peerSearch") onPeerSearch(e.target.value); }

  function onKey(e) {
    if (e.key === "Escape" && !document.getElementById("bwModal").classList.contains("hidden")) closeModal();
    else if (e.key === "Escape") closeDrawer();
    const tab = e.target.closest && e.target.closest("[role='tab']");
    if (tab && (e.key === "ArrowRight" || e.key === "ArrowLeft")) {
      const tabs = [...tab.parentElement.querySelectorAll("[role='tab']")];
      const next = tabs[(tabs.indexOf(tab) + (e.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length];
      next.focus(); next.click(); e.preventDefault();
    }
  }

  function closeProfileMenu() {
    const wrap = document.getElementById("bwProfileWrap");
    if (wrap) { wrap.classList.remove("topbar-dropdown--open"); document.getElementById("bwProfileBtn").setAttribute("aria-expanded", "false"); }
  }
  function bindShell() {
    document.addEventListener("click", onClick);
    document.addEventListener("change", onChange);
    document.addEventListener("input", onInput);
    document.addEventListener("keydown", onKey);
    document.getElementById("bwModal").addEventListener("mousedown", (e) => { if (e.target.id === "bwModal") closeModal(); });
    document.getElementById("bwProfileBtn").addEventListener("click", () => {
      const wrap = document.getElementById("bwProfileWrap");
      const open = wrap.classList.toggle("topbar-dropdown--open");
      document.getElementById("bwProfileBtn").setAttribute("aria-expanded", String(open));
    });
    document.getElementById("bwThemeBtn").addEventListener("click", () => { toggleTheme(); closeProfileMenu(); });
    document.getElementById("bwLogoutBtn").addEventListener("click", async () => {
      try { await fetch("/api/logout", { method: "POST" }); } catch { /* leaving anyway */ }
      try { sessionStorage.removeItem("tf_csrf"); } catch { /* storage blocked */ }
      root.location.href = "/login.html";
    });
    root.addEventListener("hashchange", () => { closeDrawer(); route(); });
  }

  BW.start = async function start() {
    bindShell();
    try {
      S.me = await api("/api/me");
      S.csrf = S.me.csrf_token || "";
      initTheme();
      updateMasthead();
      const ws = await api("/api/buyer-workspace/me");
      S.org = ws.organisation;
      S.watchlist = ws.watchlist;
      S.insights = Boolean(ws.insights);
      updateMasthead();
    } catch (err) {
      if (err.status === 404) { document.getElementById("bwMain").innerHTML = '<div class="bw-page"><div class="bw-card"><div class="bw-empty"><strong>The buyer workspace is not available</strong>It has been switched off for this site.</div></div></div>'; return; }
      document.getElementById("bwMain").innerHTML = errorHtml(err);
      return;
    }
    route();
  };

  BW.registerView("dashboard", renderDashboard, "Dashboard");
  BW.registerView("radar", renderRadar, "Market Radar");
  BW.registerView("watchlist", renderWatchlist, "Followed categories");
  BW.registerView("settings", renderSettings, "My organisation");

  Object.assign(BW, {
    esc, fmtInt, fmtMoney, fmtDate, fmtPct, plural, qs, api, ApiError, ensureOptions, toast, openModal, closeModal, copyText, download,
    parseHash, buildHash, navigate, replaceHash, route, skeleton, errorHtml, pickerHtml, bindPicker, specOf,
    insightFactsHtml, insightNarrativeHtml, similarSuppliersHtml, peersTableHtml, suppliersPanelHtml, costPanelHtml, kpiHtml, renewalsTableHtml, activityTableHtml, niceTicks, valueCell, awardLine,
    specFromParams, specToParams, specFromKey, defaultAuthority, safeUrl, trimNum, clamp,
  });

  if (typeof document !== "undefined" && document.getElementById && document.getElementById("bwMain")) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => BW.start());
    else BW.start();
  }
  if (typeof module !== "undefined" && module.exports) module.exports = BW;
})();
