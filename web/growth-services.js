/* Growth Studio: the services picker. Several services can be ticked at once; the list is the built-in services (grouped),
   the user's own services, services an administrator shared with everyone, and a search of every CPV branch in the awards
   data, so the choice is never limited to a fixed list. A service the user adds is private; "offer to everyone" only
   queues it for administrator approval.

   The pure helpers (grouping, summary, ids) run in Node (tests/growth_services_view_test.js); the widget needs the page. */
(function (root) {
  "use strict";

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  // "a,b,service:3" <-> ["a","b","service:3"]
  function idsOf(preset) { return String(preset || "").split(",").map((s) => s.trim()).filter(Boolean); }
  function presetOf(ids) { return ids.join(","); }

  // Everything pickable, as [{id, label, hint, group, mine, custom}]: built-ins, then the user's own, then shared ones.
  function catalogue(presets, services) {
    const out = (presets || []).map((p) => ({ id: p.preset, label: p.label, hint: p.hint || "", group: p.group || "Other", custom: false, mine: false }));
    (services || []).forEach((s) => out.push({ id: s.id, label: s.label, hint: [...(s.keywords || []), ...(s.cpv || []).map((c) => "CPV " + c)].join(", "), group: s.group || (s.mine ? "Your services" : "Shared by other users"), custom: true, mine: Boolean(s.mine), visibility: s.visibility }));
    return out;
  }

  // Groups in a fixed order (the user's own first), filtered by what was typed; a group with no match is dropped.
  function grouped(items, query) {
    const q = String(query || "").trim().toLowerCase();
    const words = q.split(/\s+/).filter(Boolean);
    const hit = (it) => !words.length || words.every((w) => (it.label + " " + it.hint + " " + it.group).toLowerCase().includes(w));
    const order = [];
    const byGroup = new Map();
    items.filter(hit).forEach((it) => {
      if (!byGroup.has(it.group)) { byGroup.set(it.group, []); order.push(it.group); }
      byGroup.get(it.group).push(it);
    });
    const rank = (g) => (g === "Your services" ? 0 : g === "Shared by other users" ? 2 : 1);
    order.sort((a, b) => rank(a) - rank(b));
    return order.map((g) => ({ group: g, items: byGroup.get(g) }));
  }

  function summary(ids, items) {
    if (!ids.length) return "Choose services…";
    const label = (id) => { const f = items.find((i) => i.id === id); return f ? f.label : id; };
    return ids.length === 1 ? label(ids[0]) : `${ids.length} services`;
  }

  function toggle(ids, id, max) {
    if (ids.includes(id)) return ids.filter((x) => x !== id);
    return ids.length >= max ? ids : ids.concat(id);
  }

  // ── the widget ────────────────────────────────────────────────────────────────────────────
  function init(ctx) {
    const $ = (id) => document.getElementById(id);
    const btn = $("gsServicesBtn"), pop = $("gsServicesPop");
    if (!btn || !pop) return null;
    const st = { services: [], query: "", cpv: [], loaded: false };

    const items = () => catalogue(ctx.options().presets, st.services);
    const max = () => ctx.options().max_services || 12;

    function render() {
      const ids = idsOf(ctx.filters().preset);
      const customActive = Boolean(ctx.filters().cpv || ctx.filters().q);
      $("gsServicesText").textContent = customActive && !ids.length ? "Custom category" : summary(ids, items());
      btn.classList.toggle("is-set", ids.length > 0 || customActive);
      const list = grouped(items(), st.query);
      $("gsServicesList").innerHTML = list.length
        ? list.map((g) => `<div class="gs-svc__group"><div class="gs-svc__ghead">${esc(g.group)}</div>` + g.items.map((it) =>
          `<label class="gs-svc__row" title="${esc(it.hint)}"><input type="checkbox" data-svc="${esc(it.id)}"${ids.includes(it.id) ? " checked" : ""}> <span>${esc(it.label)}</span>` +
          (it.mine ? `<button type="button" class="gs-svc__del" data-del="${esc(it.id)}" aria-label="Remove ${esc(it.label)}" title="Remove this service">×</button>` : "") +
          (it.mine && it.visibility === "suggested" ? '<em class="gs-svc__tag">awaiting approval</em>' : "") + `</label>`).join("") + "</div>").join("")
        : '<div class="gs-hint">No service matches. Try the CPV search below or add your own.</div>';
      $("gsServicesCpv").innerHTML = st.query.trim().length >= 3
        ? (st.cpv.length
          ? '<div class="gs-svc__ghead">From the contracts data (any CPV branch)</div>' + st.cpv.map((r) =>
            `<div class="gs-svc__row"><span>${esc(r.label)} <em class="gs-svc__tag">CPV ${esc(r.cpv)} · ${Number(r.awards).toLocaleString()} awards</em></span><button type="button" class="gs-btn gs-btn--ghost gs-btn--sm" data-addcpv="${esc(r.cpv)}" data-name="${esc(r.label)}">Add</button></div>`).join("")
          : '<div class="gs-hint">No CPV description matches those words.</div>')
        : "";
      $("gsServicesCount").textContent = ids.length ? `${ids.length} of ${max()} selected` : "";
    }

    function pick(ids) { ctx.setPreset(presetOf(ids)); render(); }

    async function loadServices() {
      try { const r = await ctx.api("GET", "/api/growth/services"); st.services = r.services || []; st.loaded = true; } catch (e) { /* the built-in list still works */ }
      render();
    }
    let searchTimer = null;
    function searchCpv() {
      clearTimeout(searchTimer);
      const q = st.query.trim();
      if (q.length < 3) { st.cpv = []; render(); return; }
      searchTimer = setTimeout(async () => {
        try { const r = await ctx.api("GET", "/api/growth/services/search?q=" + encodeURIComponent(q)); st.cpv = r.results || []; } catch (e) { st.cpv = []; }
        render();
      }, 300);
    }
    async function create(body) {
      const r = await ctx.api("POST", "/api/growth/services", body);
      await loadServices();
      pick(toggle(idsOf(ctx.filters().preset), r.service.id, max()));
      return r.service;
    }

    const open = () => { pop.classList.remove("hidden"); btn.setAttribute("aria-expanded", "true"); $("gsServicesFilter").focus(); if (!st.loaded) loadServices(); };
    const close = () => { pop.classList.add("hidden"); btn.setAttribute("aria-expanded", "false"); };
    btn.addEventListener("click", (e) => { e.stopPropagation(); pop.classList.contains("hidden") ? open() : close(); });
    document.addEventListener("click", (e) => { if (!pop.classList.contains("hidden") && !pop.contains(e.target) && e.target !== btn) close(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !pop.classList.contains("hidden")) { close(); btn.focus(); } });
    $("gsServicesFilter").addEventListener("input", (e) => { st.query = e.target.value; render(); searchCpv(); });
    $("gsServicesClear").addEventListener("click", () => pick([]));
    $("gsServicesCustom").addEventListener("click", () => { close(); ctx.openCustom(); });

    pop.addEventListener("change", (e) => {
      const id = e.target.dataset && e.target.dataset.svc;
      if (!id) return;
      const ids = idsOf(ctx.filters().preset);
      const next = toggle(ids, id, max());
      if (next.length === ids.length && !ids.includes(id)) { e.target.checked = false; ctx.toast(`You can choose up to ${max()} services at once.`, { error: true }); return; }
      pick(next);
    });
    pop.addEventListener("click", async (e) => {
      const del = e.target.closest("[data-del]");
      if (del) {
        e.preventDefault();
        const id = del.dataset.del;
        // the app's own dialog (the browser's native box is blocked in some embedded views and looks out of place)
        const ask = ctx.confirm || ((m) => root.confirm(m));
        if (!(await ask("Remove this service? It is also removed from your selection."))) return;
        try {
          await ctx.api("DELETE", "/api/growth/services/" + id.split(":")[1]);
          if (idsOf(ctx.filters().preset).includes(id)) pick(idsOf(ctx.filters().preset).filter((x) => x !== id));
          await loadServices();
        } catch (err) { ctx.toast(err.message || "Could not remove it", { error: true }); }
        return;
      }
      const add = e.target.closest("[data-addcpv]");
      if (add) {
        try { await create({ name: add.dataset.name.slice(0, 60), cpv: [add.dataset.addcpv] }); ctx.toast("Added to your services."); } catch (err) { ctx.toast(err.message || "Could not add it", { error: true }); }
      }
    });
    // Not a <form>: the picker sits inside the filter bar's form, and forms cannot nest.
    const formEl = $("gsServicesForm");
    const field = (name) => formEl.querySelector(`[name="${name}"]`);
    async function saveService() {
      const share = field("share").checked;
      try {
        await create({ name: field("name").value, keywords: field("keywords").value, cpv: field("cpv").value, share });
        ["name", "keywords", "cpv"].forEach((n) => { field(n).value = ""; });
        field("share").checked = false;
        ctx.toast("Service added. It is private to you" + (share ? " until an administrator approves it for everyone." : "."));
      } catch (err) { ctx.toast(err.message || "Could not add the service", { error: true }); }
    }
    $("gsServicesSave").addEventListener("click", saveService);
    formEl.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); saveService(); } });
    loadServices();
    return { render, close };
  }

  const api = { idsOf, presetOf, catalogue, grouped, summary, toggle, init };
  root.GrowthServices = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
