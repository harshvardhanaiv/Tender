/* Funding: open government grants from Find a Grant (GOV.UK). Data comes from /api/funding. */
(function () {
  "use strict";

  const state = { initialised: false, page: 1, timer: null, controller: null };
  const byId = (id) => document.getElementById(id);

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  }

  function money(n) {
    if (n == null) return "";
    if (n >= 1e6) return "£" + (n / 1e6).toFixed(n % 1e6 ? 1 : 0).replace(/\.0$/, "") + "m";
    if (n >= 1e3) return "£" + (n / 1e3).toFixed(n % 1e3 ? 1 : 0).replace(/\.0$/, "") + "k";
    return "£" + Math.round(n);
  }

  function awardText(g) {
    if (g.min_award != null && g.max_award != null && g.min_award !== g.max_award) return money(g.min_award) + " to " + money(g.max_award);
    const one = g.max_award != null ? g.max_award : g.min_award;
    return one != null ? (g.max_award != null ? "Up to " : "") + money(one) : "Award size not stated";
  }

  function dayText(iso) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || "");
    if (!m) return "";
    const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    return Number(m[3]) + " " + months[Number(m[2]) - 1] + " " + m[1];
  }

  function closingHtml(g) {
    if (g.ongoing) return '<span class="funding-tag funding-tag--open">Open all year</span>';
    if (!g.close_date) return '<span class="funding-tag">No closing date</span>';
    const soon = g.days_left != null && g.days_left <= 30;
    const when = g.days_left != null && g.days_left >= 0 ? (g.days_left === 0 ? "closes today" : g.days_left + (g.days_left === 1 ? " day left" : " days left")) : "";
    return '<span class="funding-tag' + (soon ? " funding-tag--soon" : "") + '">Closes ' + esc(dayText(g.close_date)) + (when ? " · " + esc(when) : "") + "</span>";
  }

  function fitHtml(g) {
    if (!g.matches || !g.matches.length) return "";
    const shown = g.matches.slice(0, 5).map(esc).join(", ");
    return '<span class="funding-fit funding-fit--' + esc(g.band || "low") + '" title="Words your company profile shares with this grant">Matches your profile: ' + shown + "</span>";
  }

  function cardHtml(g) {
    const where = (g.locations || []).join(", ");
    const who = (g.applicants || []).join(", ");
    const apply = g.apply_url || g.url;
    return (
      '<article class="funding-card">' +
      '<div class="funding-card__top"><h3 class="funding-card__name">' + esc(g.name) + "</h3>" +
      '<span class="funding-card__award">' + esc(awardText(g)) + "</span></div>" +
      '<div class="funding-card__funder">' + esc(g.funder || "Funder not stated") + "</div>" +
      '<div class="funding-card__tags">' + closingHtml(g) + fitHtml(g) + "</div>" +
      (g.summary ? '<p class="funding-card__summary">' + esc(g.summary.split("\n")[0]) + "</p>" : "") +
      '<dl class="funding-card__facts">' +
      (who ? "<div><dt>Who can apply</dt><dd>" + esc(who) + "</dd></div>" : "") +
      (where ? "<div><dt>Where</dt><dd>" + esc(where) + "</dd></div>" : "") +
      (g.total_award != null ? "<div><dt>Total pot</dt><dd>" + esc(money(g.total_award)) + "</dd></div>" : "") +
      "</dl>" +
      '<div class="funding-card__actions">' +
      (g.label ? '<button type="button" class="btn funding-ai-btn" data-ai-label="' + esc(g.label) + '" title="Check eligibility, summarise, draft an application or analyse this grant with AI">AI tools</button>' : "") +
      '<a class="btn btn--primary funding-apply" href="' + esc(apply) + '" target="_blank" rel="noopener noreferrer">Apply on GOV.UK ↗</a>' +
      (g.apply_url && g.url ? '<a class="funding-more" href="' + esc(g.url) + '" target="_blank" rel="noopener noreferrer">Full details</a>' : "") +
      "</div></article>"
    );
  }

  function pagerHtml(data) {
    if (!data || data.pages <= 1) return "";
    return (
      '<button type="button" class="btn" data-page="' + (data.page - 1) + '"' + (data.page <= 1 ? " disabled" : "") + ">Previous</button>" +
      '<span class="funding-pager__at">Page ' + data.page + " of " + data.pages + "</span>" +
      '<button type="button" class="btn" data-page="' + (data.page + 1) + '"' + (data.page >= data.pages ? " disabled" : "") + ">Next</button>"
    );
  }

  function metaText(data) {
    if (!data.imported) return "Grants have not been loaded yet. An administrator can run python -m tender_app.fag_import.";
    if (!data.total) return "No open grants match these filters.";
    const refreshed = data.refreshed ? " Updated " + dayText(data.refreshed.slice(0, 10)) + "." : "";
    const profile = data.has_profile ? "" : " Add a company profile to sort by how well a grant matches your business.";
    return data.total + (data.total === 1 ? " open grant." : " open grants.") + refreshed + profile;
  }

  function params() {
    const p = new URLSearchParams();
    const set = (key, id) => { const v = (byId(id)?.value || "").trim(); if (v) p.set(key, v); };
    set("q", "txtFundingSearch");
    set("applicant", "selFundingApplicant");
    set("location", "selFundingLocation");
    set("min_award", "selFundingAward");
    set("sort", "selFundingSort");
    if (byId("chkFundingSoon")?.checked) p.set("closing_soon", "1");
    p.set("page", String(state.page));
    return p;
  }

  function fillLocations(list) {
    const sel = byId("selFundingLocation");
    if (!sel || sel.options.length > 1 || !list.length) return;
    list.forEach((name) => sel.add(new Option(name, name)));
  }

  // "Best matches for your profile": the grants sharing the most words with the company profile, closing soonest first.
  // Shown above the list only while no search or filter is set and on the first page.
  function bestHtml(items) {
    if (!items || !items.length) return "";
    const cards = items.map((g) => cardHtml(g).replace('<article class="funding-card">', '<article class="funding-card funding-card--best">' + (g.closing_soon ? '<span class="funding-flag">Closing soon</span>' : ""))).join("");
    return '<h3 class="funding-best__title">Best matches for your profile</h3><div class="funding-list">' + cards + "</div>" +
      '<h3 class="funding-best__title funding-best__title--all">All open grants</h3>';
  }

  function filtersActive() {
    const p = params();
    p.delete("page");
    p.delete("sort");
    return Array.from(p.keys()).length > 0 || (byId("selFundingSort")?.value || "relevance") !== "relevance";
  }

  async function showBest() {
    const box = byId("fundingBest");
    if (!box) return;
    if (state.page !== 1 || filtersActive()) { box.innerHTML = ""; return; }
    if (state.best === undefined) {
      state.best = null;
      try {
        const resp = await fetch("/api/funding/best", { credentials: "same-origin" });
        const data = resp.ok ? await resp.json() : null;
        state.best = data && data.items ? data.items : [];
      } catch (err) { state.best = []; }
      state.bestItems = {};
      (state.best || []).forEach((g) => { if (g.label) { state.bestItems[g.label] = g; (state.items = state.items || {})[g.label] = g; } });
    }
    if (state.page === 1 && !filtersActive()) box.innerHTML = bestHtml(state.best);
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
      fillLocations(data.locations || []);
      byId("fundingMeta").textContent = metaText(data);
      state.items = Object.assign({}, state.bestItems);
      (data.items || []).forEach((g) => { if (g.label) state.items[g.label] = g; });
      list.innerHTML = (data.items || []).map(cardHtml).join("");
      showBest();
      byId("fundingPager").innerHTML = pagerHtml(data);
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

  function openFunding() {
    byId("fundingModal")?.classList.remove("hidden");
    if (state.initialised) return;
    state.initialised = true;
    load();
  }

  function closeFunding() {
    byId("fundingModal")?.classList.add("hidden");
  }

  function bind() {
    byId("btnFunding")?.addEventListener("click", openFunding);
    byId("fundingClose")?.addEventListener("click", closeFunding);
    byId("fundingFilterBar")?.addEventListener("submit", (e) => { e.preventDefault(); reload(0); });
    byId("txtFundingSearch")?.addEventListener("input", () => reload(300));
    ["selFundingApplicant", "selFundingLocation", "selFundingAward", "selFundingSort", "chkFundingSoon"].forEach((id) => {
      byId(id)?.addEventListener("change", () => reload(0));
    });
    byId("fundingBest")?.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-ai-label]");
      const grant = btn && state.items && state.items[btn.getAttribute("data-ai-label")];
      if (grant && window.FundingAI) window.FundingAI.open(grant);
    });
    byId("fundingList")?.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-ai-label]");
      const grant = btn && state.items && state.items[btn.getAttribute("data-ai-label")];
      if (grant && window.FundingAI) window.FundingAI.open(grant);
    });
    byId("fundingPager")?.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-page]");
      if (!btn || btn.disabled) return;
      state.page = Number(btn.dataset.page) || 1;
      load();
      byId("fundingModal")?.querySelector(".funding-body")?.scrollTo({ top: 0 });
    });
  }

  const api = { bestHtml, cardHtml,awardText, money, closingHtml, pagerHtml, metaText };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof window !== "undefined") {
    window.openFunding = openFunding;
    window.closeFunding = closeFunding;
    window.TFFunding = api;
    const init = () => { if (byId("fundingModal")) bind(); };
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
    else init();
  }
})();
