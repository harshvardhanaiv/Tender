/* Filter bars (tender search, Pipeline, Buyer and Supplier Intelligence, History): each chip opens its
   filter controls in a dropdown and shows the filter's current value. Filtering itself stays in app.js. */
(function () {
  "use strict";

  const byId = (id) => document.getElementById(id);
  const DEFAULT_COUNTRIES = ["United Kingdom", "Ireland"];
  const BUYER_SIGNALS = ["chkRenewalsSoon", "chkOpenToNew", "chkFrameworksOnly"];
  const refreshers = [];
  let openChip = null;
  let openPopoverResizeObserver = null;

  function shortMoney(value) {
    if (value >= 1e6) return `€${Number((value / 1e6).toFixed(1))}m`;
    if (value >= 1e3) return `€${Math.round(value / 1e3)}k`;
    return `€${value}`;
  }

  function setChip(id, text, isSet) {
    const chip = byId(id);
    if (!chip) return;
    const label = chip.querySelector(".tf-chip__text");
    if (label && label.textContent !== text) label.textContent = text;
    chip.classList.toggle("is-set", Boolean(isSet));
  }

  function summariseSearch() {
    if (typeof state === "undefined") return;

    // Notice type: keep the segmented control in step with state, which other screens can change
    const status = state.statusFilter === "awards" ? "awarded" : state.statusFilter || "opportunities";
    byId("rmStatusPills")?.querySelectorAll(".rm-pill").forEach((pill) => {
      pill.classList.toggle("active", pill.dataset.status === status);
    });

    // Company profile and AI fit threshold
    const profile = state.activeProfileId && typeof getActiveProfile === "function" ? getActiveProfile() : null;
    let profileText = state.activeProfileId ? (profile ? profile.name : "Profile selected") : "No profile";
    if (state.fitMin) profileText = state.activeProfileId ? `${profileText} · ${state.fitMin}%+` : `Fit ${state.fitMin}%+`;
    setChip("chipProfile", profileText, state.activeProfileId || state.fitMin);

    // Countries, plus counties when the UK or Ireland is narrowed down
    const countries = Array.isArray(state.managedCountries) ? state.managedCountries : [];
    const short = (name) => (name === "United Kingdom" ? "UK" : name);
    let countryText = "All countries";
    if (countries.length > 2) countryText = `${countries.slice(0, 2).map(short).join(", ")} +${countries.length - 2}`;
    else if (countries.length) countryText = countries.map(short).join(", ");
    const isDefault = countries.length === DEFAULT_COUNTRIES.length && DEFAULT_COUNTRIES.every((c) => countries.includes(c));

    const countyGroup = byId("rmUkCountyGroup");
    const countiesOffered = countyGroup && !countyGroup.classList.contains("hidden");
    const totalCounties = typeof UK_COUNTIES !== "undefined" ? UK_COUNTIES.length : 0;
    const counties = Array.isArray(state.selectedUkCounties) ? state.selectedUkCounties : [];
    const countiesFiltered = countiesOffered && counties.length > 0 && totalCounties > 0 && counties.length < totalCounties;
    if (countiesFiltered) countryText += counties.length === 1 ? ` · ${counties[0]}` : ` · ${counties.length} counties`;
    setChip("chipCountries", countryText, !isDefault || countiesFiltered);

    // Portals available for the selected countries
    if (typeof PORTALS !== "undefined") {
      let available = PORTALS;
      if (countries.length) {
        const matching = PORTALS.filter((p) => countries.includes(p.country));
        if (matching.length) available = matching;
      }
      const selected = typeof _selectedPortals === "undefined" || !_selectedPortals
        ? available.length
        : available.filter((p) => _selectedPortals.has(p.id)).length;
      const allPortals = selected === available.length;
      setChip("chipPortals", allPortals ? "All portals" : `${selected} of ${available.length} portals`, !allPortals);
    }

    // Published date
    const preset = state.dateFilterPreset || "all";
    let publishedText = "Any date";
    if (preset === "custom") publishedText = "Custom dates";
    else if (preset !== "all") publishedText = `Last ${preset} days`;
    setChip("chipPublished", publishedText, preset !== "all");

    // Estimated value
    const min = state.valueMin;
    const max = state.valueMax;
    let valueText = "Any value";
    if (min != null && max != null) valueText = `${shortMoney(min)}–${shortMoney(max)}`;
    else if (min != null) valueText = `${shortMoney(min)}+`;
    else if (max != null) valueText = `Up to ${shortMoney(max)}`;
    setChip("chipValue", valueText, min != null || max != null);
  }

  function summariseBuyers() {
    const minSpend = parseFloat(byId("rngMinSpendFilter")?.value || "0");
    const spendLabel = (byId("lblMinSpendVal")?.textContent || "").trim();
    setChip("chipBuyerSpend", minSpend > 0 ? `Spend ${spendLabel}` : "Any spend", minSpend > 0);

    const radius = parseFloat(byId("rngRadiusFilter")?.value || "0");
    const city = byId("selBuyerLocationCity");
    let place = city && city.selectedIndex >= 0 ? city.options[city.selectedIndex].text.replace(" (Default)", "") : "London";
    if (city && city.value === "custom") place = (byId("txtBuyerLocationCustom")?.value || "").trim() || "your postcode";
    if (city && city.value === "gps") place = "your location";
    setChip("chipBuyerDistance", radius > 0 ? `Within ${radius} mi of ${place}` : "Any distance", radius > 0);

    const checked = BUYER_SIGNALS.map(byId).filter((box) => box && box.checked);
    let signalText = "Any signal";
    if (checked.length === 1) signalText = checked[0].closest("label")?.textContent.trim() || "1 signal";
    else if (checked.length > 1) signalText = `${checked.length} signals`;
    setChip("chipBuyerSignals", signalText, checked.length > 0);
  }

  function summariseSuppliers() {
    const years = parseInt(byId("rngSupplierDateSlider")?.value || "0", 10);
    setChip("chipSupplierDates", years > 0 ? `Awarded in the last ${years} year${years > 1 ? "s" : ""}` : "Awarded any time", years > 0);

    const radius = parseFloat(byId("rngSupplierRadiusFilter")?.value || "0");
    const city = byId("selSupplierLocationCity");
    let place = city && city.selectedIndex >= 0 ? city.options[city.selectedIndex].text.replace(" (Default)", "") : "London";
    if (city && city.value === "custom") place = (byId("txtSupplierLocationCustom")?.value || "").trim() || "your postcode";
    if (city && city.value === "gps") place = "your location";
    setChip("chipSupplierDistance", radius > 0 ? `Within ${radius} mi of ${place}` : "Any distance", radius > 0);
  }

  const SUMMARIES = {
    focusPillBar: summariseSearch,
    buyerFilterBar: summariseBuyers,
    supplierFilterBar: summariseSuppliers,
  };

  function refreshAll() {
    refreshers.forEach((refresh) => refresh());
  }

  function clearBuyerFilters() {
    const setValue = (id, value) => {
      const el = byId(id);
      if (el) el.value = value;
    };
    setValue("txtBuyerSearchSidebar", "");
    setValue("selBuyerTypeFilter", "all");
    setValue("rngMinSpendFilter", "0");
    setValue("rngRadiusFilter", "0");
    BUYER_SIGNALS.forEach((id) => {
      const box = byId(id);
      if (box) box.checked = false;
    });
    if (typeof refreshBuyerIntelligenceView === "function") refreshBuyerIntelligenceView();
    refreshAll();
  }

  function closePopover() {
    if (!openChip) return;
    byId(openChip.getAttribute("aria-controls"))?.classList.add("hidden");
    openChip.setAttribute("aria-expanded", "false");
    openChip.classList.remove("is-open");
    openChip = null;
    if (openPopoverResizeObserver) {
      openPopoverResizeObserver.disconnect();
      openPopoverResizeObserver = null;
    }
  }

  // Keeps the dropdown inside the filter bar, any clipping ancestor (a modal card, e.g.
  // #supplierIntelModalCard, sets overflow:hidden and is narrower than the browser window —
  // clamping to window.innerWidth alone let the popover spill past the modal's own edge and
  // get clipped there, well before it reached the actual window edge) AND the viewport itself.
  function clampPopoverPosition(popover, bar) {
    popover.style.left = "0px";
    let clipRight = window.innerWidth;
    for (let node = popover.parentElement; node && node !== document.body; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (style.overflowX === "hidden" || style.overflowX === "auto" || style.overflowX === "scroll" ||
          style.overflow === "hidden" || style.overflow === "auto" || style.overflow === "scroll") {
        clipRight = Math.min(clipRight, node.getBoundingClientRect().right);
      }
    }
    const rect = popover.getBoundingClientRect();
    const overflow = Math.max(
      rect.right - bar.getBoundingClientRect().right,
      rect.right - clipRight + 8
    );
    if (overflow > 0) popover.style.left = `${-overflow}px`;
    // A wide popover shifted far enough left can in turn poke past the left edge of that same
    // clipping ancestor — pull it back in rather than let the left side go unreadable instead.
    const clipLeft = 8;
    const shifted = popover.getBoundingClientRect();
    if (shifted.left < clipLeft) {
      popover.style.left = `${parseFloat(popover.style.left || "0") + (clipLeft - shifted.left)}px`;
    }
  }

  function openPopover(chip, bar) {
    closePopover();
    const popover = byId(chip.getAttribute("aria-controls"));
    if (!popover) return;
    popover.classList.remove("hidden");
    chip.setAttribute("aria-expanded", "true");
    chip.classList.add("is-open");
    openChip = chip;
    clampPopoverPosition(popover, bar);
    // Content can grow after this initial layout — e.g. a "location blocked" message that
    // only appears once geolocation is actually requested — which left the position fixed
    // from before that growth and let the popover spill off the right edge. Re-clamp on
    // every resize for as long as this popover stays open.
    openPopoverResizeObserver = new ResizeObserver(() => clampPopoverPosition(popover, bar));
    openPopoverResizeObserver.observe(popover);
  }

  function bindBar(bar, summarise) {
    let queued = false;
    const refresh = () => {
      if (queued) return;
      queued = true;
      // A timer rather than requestAnimationFrame, which stops firing while the page is not being drawn
      setTimeout(() => {
        queued = false;
        summarise();
        // A dropdown chip is set whenever it is not on its first option ("All ...")
        bar.querySelectorAll("select.tf-chip--select").forEach((select) => {
          select.classList.toggle("is-set", select.selectedIndex > 0);
        });
      }, 30);
    };

    bar.addEventListener("click", (event) => {
      const chip = event.target.closest(".tf-chip[aria-controls]");
      if (chip) {
        if (chip === openChip) closePopover();
        else openPopover(chip, bar);
      }
      refresh();
    });
    bar.addEventListener("change", refresh);
    bar.addEventListener("input", refresh);
    new MutationObserver(refresh).observe(bar, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ["class"],
    });
    refreshers.push(refresh);
    refresh();
  }

  function init() {
    document.querySelectorAll(".tf-filterbar").forEach((bar) => {
      bindBar(bar, SUMMARIES[bar.id] || (() => {}));
    });
    byId("btnBuyerFiltersClear")?.addEventListener("click", clearBuyerFilters);
    if (!refreshers.length) return;

    // Close on outside clicks. composedPath() still lists pill buttons that app.js
    // re-renders during the click, so choosing a country does not close its dropdown.
    document.addEventListener("click", (event) => {
      if (openChip && !event.composedPath().some((node) => node.classList && node.classList.contains("tf-chip-wrap"))) {
        closePopover();
      }
      // Filters can also change from other screens (saved searches, reset, profile load, feed chips)
      setTimeout(refreshAll, 250);
    }, true);
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && openChip) {
        const chip = openChip;
        closePopover();
        chip.focus();
      }
    });

    // Preferences, profiles and pipeline portals load asynchronously after start-up
    setTimeout(refreshAll, 1500);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
