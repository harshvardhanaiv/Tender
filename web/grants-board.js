/* Pipeline > Grants: the grants a user saved on the Funding page, as a board by stage. Data: /api/funding/tracked.
   Grants are kept apart from tenders (own table), so they never reach tender counts, totals or tender emails. */
(function () {
  "use strict";

  const state = { items: null, stages: [], loading: false, failed: false };
  const STAGE_CLASS = { interested: "stage--watching", applying: "stage--bidding", submitted: "stage--submitted", awarded: "stage--won", not_funded: "stage--lost" };
  const DEFAULT_STAGES = [["interested", "Interested"], ["applying", "Applying"], ["submitted", "Submitted"], ["awarded", "Awarded"], ["not_funded", "Not funded"]];
  const byId = (id) => document.getElementById(id);

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  }

  function money(n) {
    if (n >= 1e6) return "£" + (n / 1e6).toFixed(n % 1e6 ? 1 : 0).replace(/\.0$/, "") + "m";
    if (n >= 1e4) return "£" + (n / 1e3).toFixed(n % 1e3 ? 1 : 0).replace(/\.0$/, "") + "k";
    return "£" + Math.round(n).toLocaleString("en-GB");
  }

  function awardText(g) {
    if (g.min_award != null && g.max_award != null && g.min_award !== g.max_award) return money(g.min_award) + " to " + money(g.max_award);
    const one = g.max_award != null ? g.max_award : g.min_award;
    if (one != null) return (g.max_award != null && g.min_award == null ? "Up to " : g.max_award == null ? "From " : "") + money(one);
    return g.total_award != null ? "Pot " + money(g.total_award) : "";
  }

  function closesHtml(g) {
    if (!g.listed) return '<span class="gb-chip gb-chip--off">No longer listed</span>';
    if (g.ongoing) return '<span class="gb-chip">Rolling, no fixed deadline</span>';
    if (!g.close_date) return "";
    const d = g.days_left;
    const cls = d != null && d <= 7 ? " gb-chip--soon" : "";
    const when = d == null ? "" : d < 0 ? " · closed" : d === 0 ? " · closes today" : " · " + d + (d === 1 ? " day left" : " days left");
    return '<span class="gb-chip' + cls + '">Closes ' + esc(g.close_date) + esc(when) + "</span>";
  }

  function cardHtml(g, stages) {
    const options = stages.map(([v, l]) => '<option value="' + esc(v) + '"' + (g.stage === v ? " selected" : "") + ">" + esc(l) + "</option>").join("");
    return (
      '<div class="dash-card gb-card" data-label="' + esc(g.label) + '">' +
      '<div class="dash-card__top" style="display:flex;align-items:center;gap:4px;flex-wrap:wrap;">' + closesHtml(g) +
      '<button class="dash-card__remove dash-card__remove--top" data-gb-remove="' + esc(g.label) + '" aria-label="Stop tracking ' + esc(g.name) + '" title="Stop tracking this grant">×</button></div>' +
      '<div class="dash-card__title">' + esc(g.name) + "</div>" +
      (g.funder ? '<div class="dash-card__auth">' + esc(g.funder) + "</div>" : "") +
      (awardText(g) ? '<div class="dash-card__auth">' + esc(awardText(g)) + "</div>" : "") +
      '<textarea class="gb-notes" data-gb-notes="' + esc(g.label) + '" rows="2" maxlength="2000" placeholder="Notes: who you spoke to, what is left to do" aria-label="Notes for ' + esc(g.name) + '">' + esc(g.notes) + "</textarea>" +
      '<label class="gb-remind"><input type="checkbox" data-gb-remind="' + esc(g.label) + '"' + (g.remind ? " checked" : "") + (["interested", "applying"].includes(g.stage) ? "" : " disabled") + "> Email me before it closes</label>" +
      '<div class="dash-card__actions"><select class="dash-card__stage-select" data-gb-stage="' + esc(g.label) + '" aria-label="Stage">' + options + "</select>" +
      '<button class="btn btn--xs" data-gb-open="' + esc(g.label) + '" title="Open this grant on the Funding page">View</button></div></div>'
    );
  }

  function visible() {
    const q = (byId("pipelineSearchInput")?.value || "").toLowerCase().trim();
    return (state.items || []).filter((g) => !q || (g.name + " " + (g.funder || "")).toLowerCase().includes(q));
  }

  function statsHtml(items) {
    const soon = items.filter((g) => g.listed && !g.ongoing && g.days_left != null && g.days_left >= 0 && g.days_left <= 14 && ["interested", "applying"].includes(g.stage)).length;
    const stat = (n, label, cls) => '<div class="dash-stat' + (cls || "") + '"><span class="dash-stat__num">' + n + '</span><span class="dash-stat__label">' + label + "</span></div>";
    return '<div class="dash-stats">' + stat(items.length, "Grants tracked") + stat(soon, "Closing ≤14 days", " dash-stat--urgent") +
      stat(items.filter((g) => g.stage === "applying").length, "Applying") + stat(items.filter((g) => g.stage === "awarded").length, "Awarded") + "</div>";
  }

  function boardHtml() {
    const stages = state.stages.length ? state.stages : DEFAULT_STAGES;
    const items = visible();
    if (state.failed) return '<div class="dash-col__empty">Could not load your grants. Close and reopen Pipeline to try again.</div>';
    if (!(state.items || []).length) {
      return '<div class="dash-section"><div class="dash-col__empty">No grants yet. On the Funding page, press <strong>Save</strong> on a grant to track it here with a stage, notes and a deadline reminder.</div>' +
        '<p><button type="button" class="btn" data-gb-funding="1">Find grants</button></p></div>';
    }
    const cols = stages.map(([v, l]) => {
      const mine = items.filter((g) => g.stage === v);
      return '<div class="dash-col" data-stage="' + esc(v) + '"><div class="dash-col__head ' + (STAGE_CLASS[v] || "") + '"><span>' + esc(l) + '</span><span class="dash-col__count">' + mine.length + "</span></div>" +
        '<div class="dash-col__body">' + (mine.map((g) => cardHtml(g, stages)).join("") || '<div class="dash-col__empty">No grants</div>') + "</div></div>";
    }).join("");
    return statsHtml(items) + '<div class="dash-section"><div class="dash-section__title">Grants board</div></div><div class="dash-kanban">' + cols + "</div>";
  }

  async function load() {
    if (state.loading) return;
    state.loading = true;
    try {
      const resp = await fetch("/api/funding/tracked", { credentials: "same-origin" });
      if (!resp.ok) throw new Error("status " + resp.status);
      const data = await resp.json();
      state.items = data.items || [];
      state.stages = data.stages || [];
      state.failed = false;
    } catch (err) {
      state.failed = true;
      state.items = state.items || [];
    } finally {
      state.loading = false;
    }
    if (typeof window.renderPipelineBoard === "function") window.renderPipelineBoard();
  }

  async function patch(label, change) {
    const resp = await fetch("/api/funding/tracked/" + encodeURIComponent(label), {
      method: "PATCH", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(change),
    });
    if (!resp.ok) throw new Error("status " + resp.status);
  }

  function wire(body) {
    body.querySelectorAll("[data-gb-stage]").forEach((el) => el.addEventListener("change", async () => {
      const g = (state.items || []).find((x) => x.label === el.dataset.gbStage);
      if (!g) return;
      const before = g.stage;
      g.stage = el.value;
      try { await patch(g.label, { stage: g.stage }); } catch (err) { g.stage = before; }
      body.innerHTML = boardHtml();
      wire(body);
    }));
    body.querySelectorAll("[data-gb-remind]").forEach((el) => el.addEventListener("change", async () => {
      const g = (state.items || []).find((x) => x.label === el.dataset.gbRemind);
      if (!g) return;
      g.remind = el.checked;
      try { await patch(g.label, { remind: g.remind }); } catch (err) { g.remind = !el.checked; el.checked = g.remind; }
    }));
    body.querySelectorAll("[data-gb-notes]").forEach((el) => el.addEventListener("blur", async () => {
      const g = (state.items || []).find((x) => x.label === el.dataset.gbNotes);
      if (!g || g.notes === el.value) return;
      const before = g.notes;
      g.notes = el.value;
      try { await patch(g.label, { notes: g.notes }); } catch (err) { g.notes = before; }
    }));
    body.querySelectorAll("[data-gb-remove]").forEach((el) => el.addEventListener("click", async () => {
      const label = el.dataset.gbRemove;
      try {
        const resp = await fetch("/api/funding/save", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ label, saved: false }) });
        if (!resp.ok) throw new Error("status " + resp.status);
        state.items = (state.items || []).filter((g) => g.label !== label);
        if (typeof window.renderPipelineBoard === "function") window.renderPipelineBoard();
      } catch (err) { /* the card stays */ }
    }));
    body.querySelectorAll("[data-gb-open]").forEach((el) => el.addEventListener("click", () => openGrant(el.dataset.gbOpen)));
    body.querySelectorAll("[data-gb-funding]").forEach((el) => el.addEventListener("click", () => openGrant("")));
  }

  // Opens the Funding page on the grant (its address is /funding/<grant>); the Funding page reads the address when it opens.
  function openGrant(label) {
    try { history.replaceState(null, "", "/funding" + (label ? "/" + encodeURIComponent(label) : "")); } catch (err) { /* no history */ }
    // The menu button does the page switch and then opens Funding, which reads the address for the grant.
    const nav = byId("btnFunding");
    if (nav) nav.click();
    else if (window.TFFunding && window.TFFunding.openGrant) window.TFFunding.openGrant(label);
  }

  // Called by the Pipeline page when the Grants kind is chosen.
  function render(body) {
    if (state.items === null) {
      body.innerHTML = '<div class="dash-col__empty">Loading your grants…</div>';
      load();
      return;
    }
    body.innerHTML = boardHtml();
    wire(body);
  }

  const api = { cardHtml, closesHtml, awardText, boardHtml, render, load, count: () => (state.items ? state.items.length : 0), ensure: () => { if (state.items === null && !state.loading) load(); }, reset: () => { state.items = null; } };
  state.api = api;
  api._state = state;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof window !== "undefined") window.TFGrantsBoard = api;
})();
