/* Planning leads: UK planning applications as pre-tender private-sector opportunities.
   Kept out of app.js on purpose. Talks only to /api/planning/*, which serves data harvested
   nightly into Postgres, so nothing here waits on an external portal.
   Pricing (see planning_bp._is_free_request): the unfiltered list and paging are free; a keyword
   or filter search costs credits. The global fetch wrapper in app.js shows the credits dialog on 402. */
(function () {
  "use strict";

  const byId = (id) => document.getElementById(id);
  const PER_PAGE = 25;

  const SIZE_LABEL = { Large: "Major", Medium: "Medium", Small: "Small" };
  const STATE_LABEL = {
    Permitted: "Approved",
    Conditions: "Approved with conditions",
    Undecided: "Awaiting decision",
    Rejected: "Refused",
    Withdrawn: "Withdrawn",
    Referred: "Referred",
    Unresolved: "Closed without decision",
    Other: "Status unknown",
  };
  const TYPE_LABEL = {
    Full: "Full application",
    Outline: "Outline application",
    Amendment: "Amendment",
    Conditions: "Discharge of conditions",
    Heritage: "Heritage",
    Trees: "Trees",
    Advertising: "Advertising",
    Telecoms: "Telecoms",
    Other: "Other",
  };
  // [breakdown key, label, maximum points] — mirrors etenders_scraper/planning/scoring.py
  const SCORE_PARTS = [
    ["size", "Scheme size", 35],
    ["state", "Decision", 22],
    ["type", "Application stage", 13],
    ["dwellings", "Dwellings", 17],
    ["recency", "Recency", 13],
  ];

  const view = {
    initialised: false,
    page: 1,
    pages: 0,
    total: 0,
    rows: [],
    selectedId: null,
    mode: "list", // "list" | "map"
    creditCost: null,
    creditsWarningShown: false,
    searchController: null,
    detailController: null,
  };

  // ── Formatting ──────────────────────────────────────────────────────────
  function esc(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // Upstream URLs come from ~400 council systems; only ever link http(s).
  function safeUrl(value) {
    if (!value) return null;
    try {
      const url = new URL(value);
      return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
    } catch {
      return null;
    }
  }

  function fmtDate(iso) {
    if (!iso) return null;
    const d = new Date(`${String(iso).slice(0, 10)}T00:00:00`);
    if (Number.isNaN(d.getTime())) return null;
    return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
  }

  function fmtInt(n) {
    return n == null ? null : Number(n).toLocaleString("en-GB");
  }

  function scoreClass(score) {
    if (score >= 75) return "planning-score planning-score--high";
    if (score >= 45) return "planning-score planning-score--mid";
    return "planning-score";
  }

  function stateTagClass(state) {
    if (state === "Permitted" || state === "Conditions") return "planning-tag planning-tag--approved";
    if (state === "Rejected") return "planning-tag planning-tag--refused";
    return "planning-tag";
  }

  function tags(row) {
    const out = [];
    if (row.app_size) {
      const cls = row.app_size === "Large" ? "planning-tag planning-tag--major" : "planning-tag";
      out.push(`<span class="${cls}">${esc(SIZE_LABEL[row.app_size] || row.app_size)}</span>`);
    }
    if (row.app_state) {
      out.push(`<span class="${stateTagClass(row.app_state)}">${esc(STATE_LABEL[row.app_state] || row.app_state)}</span>`);
    }
    if (row.app_type) out.push(`<span class="planning-tag">${esc(TYPE_LABEL[row.app_type] || row.app_type)}</span>`);
    if (row.low_value_reason === "minor_works") out.push(`<span class="planning-tag" title="Householder-scale works that PlanIt sized as a larger scheme">Minor works</span>`);
    else if (row.low_value_reason === "paperwork") out.push(`<span class="planning-tag" title="Follow-up paperwork on an existing scheme">Paperwork</span>`);
    return out.join("");
  }

  function leadParty(row) {
    return row.agent_company || row.applicant_company || row.agent_name || row.applicant_name || null;
  }

  // ── Filters ─────────────────────────────────────────────────────────────
  function isoDaysAgo(days) {
    const d = new Date();
    d.setDate(d.getDate() - days);
    return d.toISOString().slice(0, 10);
  }

  // Resolved fresh off the live "measure from" control every time it's needed (search params,
  // the map's radius circle), rather than trusting the hidden lat/lng fields
  // bindDistanceFilter's own change handler is supposed to have kept in sync: selecting a city
  // updated the chip label correctly (syncChips() reads the select's live value) while the
  // hidden fields could still be stale, so a search silently kept using whichever location
  // (usually London, the "no value yet" fallback) they last held — the chip could say "Within
  // 50 mi of Dublin" while the request still went out for London. A single shared resolver
  // means there's only one copy of this logic left to keep in sync with itself.
  // Returns null when there's no active radius filter.
  function resolveDistanceFilter() {
    const form = byId("planningFilterBar");
    const radiusKm = parseFloat(form.elements.radius_km.value || "0");
    if (!(radiusKm > 0)) return null;

    const cityEl = byId("selPlanningLocationCity");
    const cityVal = cityEl ? cityEl.value : "";
    let lat = form.elements.lat.value;
    let lng = form.elements.lng.value;
    if (cityVal === "custom") {
      const txt = (byId("txtPlanningLocationCustom")?.value || "").trim();
      // The custom-location handler below has already resolved the text into the hidden lat/lng fields;
      // keep those rather than guessing again (and never fall back to London).
      const found = txt && !(lat && lng) ? resolveCustomBuyerLocation(txt) : null;
      if (found) { lat = found.lat; lng = found.lon; }
    } else if (cityVal && cityVal !== "gps") {
      // "gps" has no live source beyond what the Geolocation callback already wrote into the
      // hidden fields, so it keeps using those (lat/lng above).
      const preset = UK_BUYER_CITY_COORDS[cityVal.toLowerCase()] || UK_BUYER_CITY_COORDS["london"];
      lat = preset.lat;
      lng = preset.lon;
    }
    lat = parseFloat(lat) || 51.5074;
    lng = parseFloat(lng) || -0.1278;
    return { lat, lng, radiusKm };
  }

  function buildParams(page) {
    const form = byId("planningFilterBar");
    const params = new URLSearchParams();
    const add = (key, value) => {
      const v = (value ?? "").toString().trim();
      if (v) params.set(key, v);
    };

    add("q", form.elements.q.value);
    add("developer", form.elements.developer.value);
    add("country", form.elements.country.value);
    add("size", form.elements.size.value);
    add("state", form.elements.state.value);
    add("authority", form.elements.authority.value);

    const type = form.elements.type.value;
    if (type === "__all__") params.set("include_low_value", "1");
    else add("type", type);

    const dwellings = parseInt(form.elements.min_dwellings.value, 10);
    if (dwellings > 0) params.set("min_dwellings", String(dwellings));

    const within = form.querySelector('input[name="submitted_within"]:checked')?.value;
    if (within) params.set("submitted_from", isoDaysAgo(parseInt(within, 10)));

    const sort = form.elements.sort.value;
    if (sort && sort !== "lead_score") params.set("sort", sort);

    // Only sent once a radius is actually set — the backend rejects lat/lng given with
    // radius_km<=0, and resolveDistanceFilter() itself returns null for that case.
    const distance = resolveDistanceFilter();
    if (distance) {
      add("lat", String(distance.lat));
      add("lng", String(distance.lng));
      add("radius_km", String(distance.radiusKm));
    }

    params.set("page", String(page));
    params.set("per_page", String(PER_PAGE));
    return params;
  }

  // Mirrors the server rule: anything beyond page/per_page/sort is a paid search.
  function isFilteredSearch(params) {
    for (const key of params.keys()) {
      if (!["page", "per_page", "sort"].includes(key)) return true;
    }
    return false;
  }

  function setChip(id, text, isSet) {
    const chip = byId(id);
    if (!chip) return;
    const label = chip.querySelector(".tf-chip__text");
    if (label) label.textContent = text;
    chip.classList.toggle("is-set", Boolean(isSet));
  }

  function syncChips() {
    const form = byId("planningFilterBar");
    const dwellings = parseInt(form.elements.min_dwellings.value, 10) || 0;
    byId("lblPlanningDwellings").textContent = dwellings > 0 ? `${dwellings}+` : "Any";
    setChip("chipPlanningDwellings", dwellings > 0 ? `${dwellings}+ dwellings` : "Any dwellings", dwellings > 0);

    const checked = form.querySelector('input[name="submitted_within"]:checked');
    const within = checked?.value;
    setChip("chipPlanningDates", within ? `Submitted: ${checked.parentElement.textContent.trim().toLowerCase()}` : "Submitted any time", Boolean(within));

    const authority = form.elements.authority.value.trim();
    setChip("chipPlanningAuthority", authority || "All councils", Boolean(authority));

    const radius = parseFloat(byId("rngPlanningRadiusFilter")?.value || "0"); // slider is in miles
    const city = byId("selPlanningLocationCity");
    let place = city && city.selectedIndex >= 0 ? city.options[city.selectedIndex].text.replace(" (Default)", "") : "London";
    if (city && city.value === "custom") place = (byId("txtPlanningLocationCustom")?.value || "").trim() || "your postcode";
    if (city && city.value === "gps") place = "your location";
    setChip("chipPlanningDistance", radius > 0 ? `Within ${radius} mi of ${place}` : "Any distance", radius > 0);
  }

  function updateCreditNote() {
    const note = byId("planningCreditNote");
    if (!note || view.creditCost == null) return;
    note.textContent = view.creditCost > 0
      ? `Browsing free · filtered search ${view.creditCost} credit${view.creditCost === 1 ? "" : "s"}`
      : "";
  }

  // ── Results ─────────────────────────────────────────────────────────────
  function showListMessage(html, isError) {
    byId("planningResults").innerHTML = `<div class="planning-alert${isError ? " planning-alert--error" : ""}">${html}</div>`;
    byId("planningPager").classList.add("hidden");
  }

  async function errorMessage(res) {
    let data = {};
    try { data = await res.json(); } catch { /* not JSON */ }
    if (res.status === 402) {
      return `You need ${esc(data.required ?? view.creditCost ?? 1)} credit${data.required === 1 ? "" : "s"} for a filtered search and have ${esc(data.balance ?? 0)}. Clearing the filters shows the ranked list for free.`;
    }
    if (res.status === 403 && data.code === "PLAN_REQUIRED") {
      const upgrade = typeof window.openPricingModal === "function"
        ? `<div><button type="button" class="tf-chip" data-planning-action="upgrade">See plans</button></div>`
        : "";
      return `Planning leads is part of the ${esc(data.required_plan)} plan and above. You are on ${esc(data.current_plan)}.${upgrade}`;
    }
    if (res.status === 429) return "Too many searches in a short time. Wait a moment and try again.";
    if (res.status === 400) return esc(data.error || "That search could not be run.");
    return `Planning leads could not be loaded (error ${res.status}). <div><button type="button" class="tf-chip" data-planning-action="retry">Try again</button></div>`;
  }

  function renderResults() {
    const list = byId("planningResults");
    const count = byId("planningResultsCount");
    count.textContent = view.total === 0
      ? "No matching applications"
      : `${fmtInt(view.total)} application${view.total === 1 ? "" : "s"}`;

    if (!view.rows.length) {
      showListMessage("No applications match these filters. Try a wider date range, another nation, or clear the filters.");
      return;
    }

    list.innerHTML = view.rows.map((row) => {
      const meta = [
        row.authority,
        fmtDate(row.start_date) && `Submitted ${fmtDate(row.start_date)}`,
        row.n_dwellings ? `${fmtInt(row.n_dwellings)} dwellings` : null,
      ].filter(Boolean).map((m) => `<span>${esc(m)}</span>`).join("");
      const party = leadParty(row);
      const active = row.id === view.selectedId;
      return `
        <button type="button" class="planning-lead${active ? " is-active" : ""}" data-id="${esc(row.id)}" aria-pressed="${active}">
          <span class="planning-lead__top">
            <span class="${scoreClass(row.lead_score)}" title="Lead score out of 100">${esc(row.lead_score ?? "–")}</span>
            ${tags(row)}
          </span>
          <span class="planning-lead__desc">${esc(row.description || row.address || "No description published")}</span>
          <span class="planning-lead__meta">${meta}</span>
          ${party ? `<span class="planning-lead__agent">${esc(party)}</span>` : ""}
        </button>`;
    }).join("");

    const pager = byId("planningPager");
    pager.classList.toggle("hidden", view.pages <= 1);
    byId("planningPageLabel").textContent = `Page ${fmtInt(view.page)} of ${fmtInt(view.pages)}`;
    byId("btnPlanningPrev").disabled = view.page <= 1;
    byId("btnPlanningNext").disabled = view.page >= view.pages;
  }

  async function search(page = 1) {
    syncChips();
    const params = buildParams(page);
    const filtered = isFilteredSearch(params);

    // The summary banner ("X schemes across the UK...") used to come only from the unfiltered
    // /api/planning/stats call made once at load (loadStats()) and just got hidden whenever a
    // filter narrowed the list below, rather than show a stale UK-wide figure. /api/planning/stats
    // now takes the same filters /api/planning/search does, so refresh it with the current filters
    // (or restore the unfiltered banner once they're cleared) instead of hiding it. Not awaited --
    // this is a secondary figure and shouldn't delay the actual results rendering below.
    updateLeadsBanner(filtered, params);

    // A filtered search charges a credit the instant it runs (see _is_free_request in
    // planning_bp.py) -- previously with no warning before that happened, so credits could
    // disappear without the user realising a keyword/filter (rather than browsing/paging) was
    // what triggered it. Ask once per Planning Leads session, not on every filter tweak after
    // that -- re-confirming each time would be far more annoying than it's worth.
    if (page === 1 && filtered && view.creditCost > 0 && !view.creditsWarningShown) {
      view.creditsWarningShown = true; // set before awaiting so a second search fired while this
                                        // dialog is still open doesn't stack another one
      const proceed = await window.showConfirm(
        `Filtered searches cost ${view.creditCost} credit${view.creditCost === 1 ? "" : "s"} each. ` +
        `Browsing the unfiltered list and paging through it stays free.`,
        "This search will use credits",
        { okText: "Search anyway", cancelText: "Clear filters instead", danger: false }
      );
      if (!proceed) {
        view.creditsWarningShown = false; // they didn't actually search yet -- ask again next time
        byId("planningFilterBar").reset();
        return search(1);
      }
    }

    view.searchController?.abort();
    const controller = new AbortController();
    view.searchController = controller;

    const list = byId("planningResults");
    list.setAttribute("aria-busy", "true");
    byId("planningResultsCount").innerHTML = `<svg class="spin-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" style="vertical-align:-2px; margin-right:5px;"><circle cx="12" cy="12" r="10" stroke-opacity="0.25"></circle><path d="M12 2 a10 10 0 0 1 10 10"></path></svg>Searching…`;

    try {
      const res = await fetch(`/api/planning/search?${params}`, { signal: controller.signal });
      if (controller !== view.searchController) return;
      if (!res.ok) {
        byId("planningResultsCount").textContent = "";
        showListMessage(await errorMessage(res), true);
        return;
      }
      const data = await res.json();
      if (controller !== view.searchController) return;
      Object.assign(view, { rows: data.results, total: data.total, page: data.page, pages: data.pages });
      renderResults();
      list.scrollTop = 0;
      if (view.mode === "map" && page === 1) loadMap();
      if (page === 1 && filtered && view.creditCost > 0 && typeof window.refreshCredits === "function") {
        window.refreshCredits();
      }
    } catch (err) {
      if (err.name === "AbortError") return;
      byId("planningResultsCount").textContent = "";
      showListMessage(`Could not reach TenderFlow. Check your connection. <div><button type="button" class="tf-chip" data-planning-action="retry">Try again</button></div>`, true);
    } finally {
      if (controller === view.searchController) list.removeAttribute("aria-busy");
    }
  }

  // ── Detail ──────────────────────────────────────────────────────────────
  function fact(label, value) {
    if (value == null || value === "") return "";
    return `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`;
  }

  function renderScore(app) {
    const b = app.lead_score_breakdown || {};
    const rows = SCORE_PARTS.map(([key, label, max]) => {
      const pts = b[key] || 0;
      const pct = Math.max(0, Math.min(100, (pts / max) * 100));
      return `
        <div class="planning-score-row">
          <span class="planning-score-row__label">${esc(label)}</span>
          <span class="planning-score-row__bar" aria-hidden="true"><span class="planning-score-row__fill" style="width:${pct}%"></span></span>
          <span class="planning-score-row__pts">${pts}</span>
        </div>`;
    });
    if (b.cap) {
      const capLabel = app.low_value_reason === "minor_works" ? "Minor works cap"
        : app.low_value_reason === "paperwork" ? "Paperwork cap"
        : "Capped at 100";
      rows.push(`
        <div class="planning-score-row planning-score-row--cap">
          <span class="planning-score-row__label">${capLabel}</span>
          <span></span>
          <span class="planning-score-row__pts">${b.cap}</span>
        </div>`);
    }
    return `
      <section class="planning-section">
        <h4>Lead score: ${esc(app.lead_score)} / 100</h4>
        <div class="planning-score-rows">${rows.join("")}</div>
        <p class="planning-score-note">A ranking, not a value estimate. Larger, approved, early-stage and recent schemes rank higher. Householder-scale works and follow-up paperwork such as discharge of conditions are capped at 30.</p>
      </section>`;
  }

  function renderDetail(app) {
    const links = [
      [safeUrl(app.detail_url), "View on council portal", true],
      [safeUrl(app.docs_url), "Documents", false],
      [safeUrl(app.planit_url), "PlanIt record", false],
    ].filter(([href]) => href).map(([href, label, primary]) =>
      `<a class="planning-link${primary ? " planning-link--primary" : ""}" href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(label)} ↗</a>`
    ).join("");

    const location = [app.address, app.postcode && !String(app.address || "").includes(app.postcode) ? app.postcode : null]
      .filter(Boolean).join(", ");
    const people = [
      fact("Agent", app.agent_company),
      fact("Agent contact", app.agent_name),
      fact("Agent address", app.agent_address),
      fact("Applicant", app.applicant_company || app.applicant_name),
      fact("Case officer", app.case_officer),
    ].join("");

    const nearby = (app.nearby || []).map((n) => `
      <li><button type="button" data-id="${esc(n.id)}">
        <span class="planning-nearby__dist">${esc((n.distance_km / 1.609344).toFixed(1))} mi</span>
        <span class="planning-nearby__desc">${esc(n.description || "No description")}</span>
      </button></li>`).join("");

    // Proposals run to hundreds of words. The heading is clamped to three lines; when it
    // would be cut, the full text follows as ordinary body copy.
    const description = app.description || "No description published";
    const proposal = description.length > 220
      ? `<section class="planning-section"><h4>Proposal</h4><p class="planning-proposal">${esc(description)}</p></section>`
      : "";

    byId("planningDetail").innerHTML = `
      <h3 class="planning-detail__title">${esc(description)}</h3>
      <div class="planning-detail__tags">
        <span class="${scoreClass(app.lead_score)}" title="Lead score out of 100">${esc(app.lead_score ?? "–")}</span>
        ${tags(app)}
      </div>
      ${links ? `<div class="planning-detail__actions">${links}</div>` : ""}
      ${proposal}

      <section class="planning-section">
        <h4>Scheme</h4>
        <dl class="planning-facts">
          ${fact("Location", location)}
          ${fact("Planning authority", app.authority)}
          ${fact("Nation", app.country)}
          ${fact("Ward", app.ward_name)}
          ${fact("Dwellings", fmtInt(app.n_dwellings))}
          ${fact("Council reference", app.uid)}
          ${fact("Planning Portal reference", app.planning_portal_id)}
          ${fact("Decision", app.decision)}
        </dl>
      </section>

      <section class="planning-section">
        <h4>Timeline</h4>
        <dl class="planning-facts">
          ${fact("Submitted", fmtDate(app.start_date))}
          ${fact("Consultation ends", fmtDate(app.consultation_end_date))}
          ${fact("Target decision", fmtDate(app.target_decision_date))}
          ${fact("Decided", fmtDate(app.decided_date))}
        </dl>
      </section>

      ${people ? `<section class="planning-section"><h4>Who is involved</h4><dl class="planning-facts">${people}</dl></section>` : ""}
      ${renderScore(app)}
      ${nearby ? `<section class="planning-section"><h4>Other schemes within 1.2 mi</h4><ul class="planning-nearby">${nearby}</ul></section>` : ""}

      <p class="planning-source">Source: UK PlanIt, from the council's public planning register. Planning registers do not publish contract values, so none is shown or estimated. Check the council portal before relying on any detail.</p>`;
    byId("planningDetail").scrollTop = 0;
  }

  async function showDetail(id) {
    view.selectedId = id;
    byId("planningResults").querySelectorAll(".planning-lead").forEach((el) => {
      const active = el.dataset.id === id;
      el.classList.toggle("is-active", active);
      el.setAttribute("aria-pressed", String(active));
    });

    view.detailController?.abort();
    const controller = new AbortController();
    view.detailController = controller;
    const panel = byId("planningDetail");
    panel.setAttribute("aria-busy", "true");

    try {
      // Encode each segment but keep the slashes: PlanIt ids look like "Leeds/26/01234/FU".
      const path = id.split("/").map(encodeURIComponent).join("/");
      const res = await fetch(`/api/planning/application/${path}`, { signal: controller.signal });
      if (controller !== view.detailController) return;
      if (!res.ok) {
        panel.innerHTML = `<div class="planning-alert planning-alert--error">${res.status === 404
          ? "This application is no longer available."
          : await errorMessage(res)}</div>`;
        return;
      }
      renderDetail(await res.json());
      // On narrow screens the detail panel sits below the list; bring it into view.
      if (window.matchMedia("(max-width: 760px)").matches) panel.scrollIntoView({ block: "start", behavior: "smooth" });
    } catch (err) {
      if (err.name !== "AbortError") {
        panel.innerHTML = `<div class="planning-alert planning-alert--error">Could not load this application. Check your connection.</div>`;
      }
    } finally {
      if (controller === view.detailController) panel.removeAttribute("aria-busy");
    }
  }

  // ── Map ─────────────────────────────────────────────────────────────────
  // Pins come from /api/planning/map: the same filters as the list, capped at the server's
  // max_points, optionally limited to the visible box ("Search this area").
  const mapState = { leaflet: null, layer: null, controller: null, moving: false, radiusCircle: null };

  function scoreBand(score) {
    return score >= 75 ? "high" : score >= 45 ? "mid" : "low";
  }

  function pinIcon(point) {
    const size = point.app_size === "Large" ? 22 : 15;
    return L.divIcon({
      className: `planning-pin planning-pin--${scoreBand(point.lead_score)}`,
      html: '<span class="planning-pin__dot"></span>',
      iconSize: [size, size],
      iconAnchor: [size / 2, size / 2],
    });
  }

  function popupHtml(point) {
    const meta = [point.authority, point.agent_company].filter(Boolean).map(esc).join(" · ");
    return `
      <div class="planning-popup">
        <div class="planning-popup__top"><span class="${scoreClass(point.lead_score)}" title="Lead score out of 100">${esc(point.lead_score ?? "–")}</span>${tags(point)}</div>
        <div class="planning-popup__desc">${esc(point.description || point.address || "No description published")}</div>
        <div class="planning-popup__meta">${esc(point.address || "")}${meta ? `<br>${meta}` : ""}</div>
        <button type="button" class="planning-popup__open" data-map-open="${esc(point.id)}">Open details</button>
      </div>`;
  }

  function setMapStatus(text) {
    byId("planningMapStatus").textContent = text || "";
  }

  function ensureMap() {
    if (mapState.leaflet) return true;
    if (typeof L === "undefined") {
      setMapStatus("The map library failed to load. Check your connection and reload the page.");
      return false;
    }
    // Framed to the UK (not a Europe-wide view) so the map always opens where the data
    // actually is -- matters most here because, unlike the Buyer/Supplier maps, there can be a
    // visible gap between this initial paint and loadMap()'s fitBounds() resolving (network
    // latency, or a load that errors/aborts before it fits).
    //
    // A fixed center+zoom was tried here first, but planningMapCard's actual size varies with
    // the browser window (and Windows display scaling), and a wide window showed as much
    // Scandinavia as the earlier fitBounds() attempt did -- a fixed zoom number only suits the
    // one aspect ratio it was picked for. fitBounds() itself was the very first thing tried and
    // was ruled out for a different reason: Leaflet's default zoomSnap (1) rounds the computed
    // zoom *down* to the nearest whole level to guarantee the box still fits, and for the
    // UK+IE box's aspect ratio that rounding alone lost almost a full zoom level (opened on
    // France/Germany/Scandinavia too). zoomSnap: 0.25 removes that rounding penalty, and
    // dropping Shetland from the default box (a small, remote archipelago ~2° further north
    // than mainland Scotland, that stretches the box's height far more than its width) keeps
    // the box's own aspect ratio closer to a typical wide container, so fitBounds can zoom in
    // further before it runs out of the box's shorter dimension. Confirmed across three
    // container shapes (this session's ~700px-wide card, an earlier ~600px-wide one, and a
    // deliberately extra-wide 900px one): the UK is the clear, dominant focus in all three,
    // with only a sliver of the nearest coastline beyond it -- real applications are never
    // limited to this box, since loadMap()'s own fitBounds() to the actual pins (below) takes
    // over the moment data loads, Shetland included if that's where the results are.
    const leaflet = L.map("planningMap", { scrollWheelZoom: true, minZoom: 5, zoomSnap: 0.25 })
      .fitBounds([[49.85, -7.8], [59.4, 2.0]]);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 18,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors',
    }).addTo(leaflet);
    mapState.layer = typeof L.markerClusterGroup === "function"
      ? L.markerClusterGroup({ maxClusterRadius: 40, showCoverageOnHover: false })
      : L.layerGroup();
    mapState.layer.addTo(leaflet);
    // After the user pans or zooms, offer to re-query for what is now on screen.
    leaflet.on("moveend", () => {
      if (mapState.moving || view.mode !== "map") return;
      byId("btnPlanningSearchArea").classList.remove("hidden");
    });
    leaflet.getContainer().addEventListener("click", (event) => {
      const id = event.target.closest("[data-map-open]")?.dataset.mapOpen;
      if (!id) return;
      setMode("list");
      showDetail(id);
    });
    mapState.leaflet = leaflet;
    return true;
  }

  function mapParams(useBounds) {
    const params = buildParams(1);
    ["page", "per_page", "sort"].forEach((key) => params.delete(key));
    if (useBounds) {
      const b = mapState.leaflet.getBounds();
      params.set("south", b.getSouth().toFixed(5));
      params.set("west", b.getWest().toFixed(5));
      params.set("north", b.getNorth().toFixed(5));
      params.set("east", b.getEast().toFixed(5));
    }
    return params;
  }

  async function loadMap({ useBounds = false } = {}) {
    if (!ensureMap()) return;
    mapState.controller?.abort();
    const controller = new AbortController();
    mapState.controller = controller;
    byId("btnPlanningSearchArea").classList.add("hidden");
    setMapStatus("Loading map…");
    try {
      const res = await fetch(`/api/planning/map?${mapParams(useBounds)}`, { signal: controller.signal });
      if (controller !== mapState.controller) return;
      if (!res.ok) {
        const text = await errorMessage(res);
        setMapStatus(text.replace(/<[^>]*>/g, "").trim());
        return;
      }
      const data = await res.json();
      if (controller !== mapState.controller) return;

      mapState.layer.clearLayers();
      const latlngs = [];
      for (const point of data.points) {
        const label = `${point.lead_score ?? "–"} score: ${point.address || point.description || point.id}`;
        const marker = L.marker([point.latitude, point.longitude], {
          icon: pinIcon(point), keyboard: true, title: label, alt: label, riseOnHover: true,
        });
        // Pan far enough that the popup clears the status pill and "Search this area" button.
        marker.bindPopup(popupHtml(point), { maxWidth: 280, autoPanPaddingTopLeft: [10, 96], autoPanPaddingBottomRight: [10, 10] });
        mapState.layer.addLayer(marker);
        latlngs.push([point.latitude, point.longitude]);
      }

      if (!data.points.length) {
        setMapStatus(useBounds
          ? "No applications with a location in this area. Zoom out or move the map."
          : "No applications with a location match these filters.");
      } else if (data.capped) {
        setMapStatus(`Showing the top ${fmtInt(data.shown)} of ${fmtInt(data.total_with_location)} with a location. Zoom in and press “Search this area” to see more.`);
      } else {
        setMapStatus(`${fmtInt(data.total_with_location)} application${data.total_with_location === 1 ? "" : "s"} with a location`);
      }

      // Draw the "measure from" radius as a circle, same idea as the Buyer/Supplier maps' own
      // search-area overlay, so it's visible which pins are actually inside the filter vs. just
      // near it.
      if (mapState.radiusCircle) {
        mapState.leaflet.removeLayer(mapState.radiusCircle);
        mapState.radiusCircle = null;
      }
      const distance = resolveDistanceFilter();
      if (distance) {
        mapState.radiusCircle = L.circle([distance.lat, distance.lng], {
          radius: distance.radiusKm * 1000, // km -> metres
          color: "#2563eb",
          weight: 1.5,
          fillColor: "#2563eb",
          fillOpacity: 0.06,
        }).addTo(mapState.leaflet);
      }

      // A fresh filter search fits the pins (and the radius circle, so it's never cut off);
      // "Search this area" keeps the view where the user put it.
      if (!useBounds && latlngs.length) {
        const fitTo = mapState.radiusCircle
          ? L.latLngBounds(latlngs).extend(mapState.radiusCircle.getBounds())
          : L.latLngBounds(latlngs);
        mapState.moving = true;
        mapState.leaflet.once("moveend", () => { mapState.moving = false; });
        mapState.leaflet.fitBounds(fitTo, { padding: [30, 30], maxZoom: 13 });
        if (mapState.leaflet.getZoom() < 6) {
          mapState.leaflet.setZoom(6);
        }
      }
    } catch (err) {
      if (err.name !== "AbortError") setMapStatus("Could not load the map. Check your connection and try again.");
    }
  }

  function setMode(mode) {
    view.mode = mode;
    const isMap = mode === "map";
    byId("planningMapCard").classList.toggle("hidden", !isMap);
    document.querySelector(".planning-workspace")?.classList.toggle("is-map-mode", isMap);
    byId("planningDetail").classList.toggle("hidden", isMap);
    for (const [id, m] of [["btnPlanningViewList", "list"], ["btnPlanningViewMap", "map"]]) {
      const btn = byId(id);
      btn.classList.toggle("is-active", m === mode);
      btn.setAttribute("aria-pressed", String(m === mode));
    }
    if (isMap) {
      if (!ensureMap()) return;
      // Leaflet measures its box once; it was hidden until now, so ensureMap()'s own fitBounds
      // (called the instant the box became visible, before the browser has reflowed) computed
      // its zoom against a stale/zero-size box -- opening zoomed out to Europe instead of the
      // UK. invalidateSize() alone only fixes tile rendering for the corrected size; it doesn't
      // redo that fit, so re-run the same UK-framing fitBounds once the box has a real size.
      setTimeout(() => {
        mapState.leaflet.invalidateSize();
        mapState.leaflet.fitBounds([[49.85, -7.8], [59.4, 2.0]]);
      }, 0);
      loadMap();
    }
  }

  // ── Setup ───────────────────────────────────────────────────────────────
  function renderLeadsBannerText(data, scopeLabel) {
    if (!data.total) return;
    const updated = data.last_updated ? ` · updated ${fmtDate(data.last_updated)}` : "";
    byId("planningLeadsSub").textContent =
      `${fmtInt(data.total)} major and medium schemes ${scopeLabel} · ${fmtInt(data.new_last_7_days)} submitted and ${fmtInt(data.approved_last_7_days)} approved in the last 7 days${updated}`;
    byId("planningLeadsSub").classList.remove("hidden");
  }

  // A plain "country=England" (or a handful of nations) reads naturally as "in England"; any
  // other filter combination (keyword, authority, dwellings, dates...) falls back to a generic
  // label rather than trying to describe every possible combination.
  function leadsBannerScopeLabel(params) {
    const nonCountryKeys = [...params.keys()].filter((k) => !["page", "per_page", "sort", "country"].includes(k));
    const countries = params.getAll("country");
    if (nonCountryKeys.length === 0 && countries.length > 0) {
      return `in ${countries.join(" and ")}`;
    }
    return "matching your filters";
  }

  async function loadStats() {
    try {
      const res = await fetch("/api/planning/stats");
      if (!res.ok) return;
      const data = await res.json();
      view.creditCost = data.credit_cost_per_search;
      updateCreditNote();
      renderLeadsBannerText(data, "across the UK");
    } catch { /* the subheading keeps its default text */ }
  }

  // Keeps the summary banner in sync with the same filters the results list below uses --
  // called on every search() rather than only hidden under a filter, so it shows a real,
  // correctly-scoped figure (e.g. "8,422 ... in England") instead of going blank.
  async function updateLeadsBanner(filtered, params) {
    if (!filtered) {
      loadStats();
      return;
    }
    try {
      const res = await fetch(`/api/planning/stats?${params}`);
      if (!res.ok) return;
      const data = await res.json();
      renderLeadsBannerText(data, leadsBannerScopeLabel(params));
    } catch { /* leave whatever the banner last showed */ }
  }

  async function loadAuthorities() {
    try {
      const res = await fetch("/api/planning/authorities");
      if (!res.ok) return;
      const { authorities } = await res.json();
      byId("dlPlanningAuthorities").innerHTML = authorities
        .map((a) => `<option value="${esc(a.authority)}">${esc(a.country || "")}</option>`)
        .join("");
    } catch { /* typing a council name still works without suggestions */ }
  }

  function bind() {
    const form = byId("planningFilterBar");

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      search(1);
    });
    // Dropdowns, the dwellings slider (on release) and date presets apply at once.
    form.addEventListener("change", (event) => {
      if (event.target.matches("select, input[type=range], input[type=radio]")) search(1);
    });
    form.addEventListener("input", (event) => {
      if (event.target.matches("input[type=range]")) syncChips();
    });

    byId("btnPlanningClear").addEventListener("click", () => {
      form.reset();
      search(1);
    });

    byId("btnPlanningViewList").addEventListener("click", () => setMode("list"));
    byId("btnPlanningViewMap").addEventListener("click", () => setMode("map"));
    byId("btnPlanningSearchArea").addEventListener("click", () => loadMap({ useBounds: true }));

    byId("btnPlanningPrev").addEventListener("click", () => search(view.page - 1));
    byId("btnPlanningNext").addEventListener("click", () => search(view.page + 1));

    byId("planningResults").addEventListener("click", (event) => {
      const lead = event.target.closest(".planning-lead");
      if (lead) showDetail(lead.dataset.id);
    });
    byId("planningDetail").addEventListener("click", (event) => {
      const nearby = event.target.closest(".planning-nearby button");
      if (nearby) showDetail(nearby.dataset.id);
    });

    document.getElementById("planningLeadsModal").addEventListener("click", (event) => {
      const action = event.target.closest("[data-planning-action]")?.dataset.planningAction;
      if (action === "retry") search(view.page || 1);
      if (action === "upgrade") window.openPricingModal?.();
    });

    byId("planningLeadsClose").addEventListener("click", closePlanningLeads);
    byId("btnPlanningLeads")?.addEventListener("click", openPlanningLeads);

    bindDistanceFilter(form);
  }

  // ── DISTANCE FILTER (mirrors Buyer/Supplier Intelligence's location picker;
  //    UK_BUYER_CITY_COORDS / resolveCustomBuyerLocation are defined in app.js, which loads
  //    before this file and shares its top-level scope) ──────────────────────
  function bindDistanceFilter(form) {
    const rngRadius = byId("rngPlanningRadiusFilter");
    const lblRadius = byId("lblPlanningRadiusVal");
    const btnGps = byId("btnPlanningUseBrowserLocation");
    const selCity = byId("selPlanningLocationCity");
    const txtCustom = byId("txtPlanningLocationCustom");
    const lblStatus = byId("lblPlanningLocationStatus");
    const hidLat = form.elements.lat;
    const hidLng = form.elements.lng;
    const hidRadius = form.elements.radius_km;

    if (rngRadius) {
      rngRadius.addEventListener("input", () => {
        const val = parseFloat(rngRadius.value || "0"); // slider is in miles
        hidRadius.value = val > 0 ? window.milesToKm(val) : 0; // radius_km stays in km for the API
        if (lblRadius) lblRadius.textContent = val > 0 ? `${val} mi` : "Any distance";
      });
    }

    if (btnGps) {
      btnGps.addEventListener("click", () => {
        tfRequestGpsLocation(btnGps, lblStatus, (pos) => {
          const lat = pos.coords.latitude, lon = pos.coords.longitude;
          hidLat.value = lat;
          hidLng.value = lon;
          if (lblStatus) lblStatus.innerHTML = `📍 Active: <strong>Your GPS</strong> (${lat.toFixed(2)}, ${lon.toFixed(2)})`;

          let gpsOpt = selCity?.querySelector("option[value='gps']");
          if (!gpsOpt && selCity) {
            gpsOpt = document.createElement("option");
            gpsOpt.value = "gps";
            gpsOpt.textContent = "📍 Current GPS Location";
            selCity.insertBefore(gpsOpt, selCity.firstChild);
          }
          if (selCity) selCity.value = "gps";
          if (txtCustom) txtCustom.classList.add("hidden");

          if (rngRadius && parseFloat(rngRadius.value) === 0) {
            rngRadius.value = "25";
            hidRadius.value = window.milesToKm(25);
            if (lblRadius) lblRadius.textContent = "25 mi";
          }

          window.showToast?.("Location updated from your GPS!", "success");
          search(1);
        });
      });
    }

    if (selCity) {
      selCity.addEventListener("change", (event) => {
        const val = selCity.value;
        if (val === "custom") {
          if (txtCustom) {
            txtCustom.classList.remove("hidden");
            txtCustom.focus();
          }
          if (lblStatus) lblStatus.textContent = "Enter a UK postcode or city name below";
          event.stopPropagation(); // don't search yet -- wait for the postcode/city text
        } else if (val === "gps") {
          if (txtCustom) txtCustom.classList.add("hidden");
          if (lblStatus) lblStatus.innerHTML = `📍 Active: <strong>Your GPS</strong> (${parseFloat(hidLat.value).toFixed(2)}, ${parseFloat(hidLng.value).toFixed(2)})`;
        } else {
          if (txtCustom) txtCustom.classList.add("hidden");
          const preset = UK_BUYER_CITY_COORDS[val.toLowerCase()] || UK_BUYER_CITY_COORDS["london"];
          hidLat.value = preset.lat;
          hidLng.value = preset.lon;
          if (lblStatus) lblStatus.innerHTML = `Measuring distance from <strong>${esc(preset.name)}</strong>`;
        }
      });
    }

    if (txtCustom) {
      const handleCustomLocation = async () => {
        const txt = txtCustom.value.trim();
        if (!txt) return;
        if (lblStatus) lblStatus.textContent = "Looking up location…";
        const found = await window.resolveLocationAsync(txt);
        if (found) {
          hidLat.value = found.lat;
          hidLng.value = found.lon;
          if (lblStatus) lblStatus.innerHTML = `📍 Centered on <strong>${esc(found.name)}</strong> (${esc(txt)})`;
          search(1);
        } else if (lblStatus) {
          lblStatus.innerHTML = `⚠️ Couldn’t find “${esc(txt)}”. Enter a UK postcode (e.g. CM9 5ED) or a city name.`;
        }
      };
      txtCustom.addEventListener("change", handleCustomLocation);
      txtCustom.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          handleCustomLocation();
        }
      });
    }
  }

  function openPlanningLeads() {
    byId("planningLeadsModal")?.classList.remove("hidden");
    if (view.initialised) return;
    view.initialised = true;
    loadStats();
    loadAuthorities();
    search(1);
  }

  function closePlanningLeads() {
    byId("planningLeadsModal")?.classList.add("hidden");
    view.creditsWarningShown = false; // ask again fresh next time they open Planning Leads
  }

  window.openPlanningLeads = openPlanningLeads;
  window.closePlanningLeads = closePlanningLeads;

  function init() {
    if (!byId("planningLeadsModal")) return;
    bind();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
