/* TenderFlow shell: sidebar highlighting and one section open at a time.
   Sections still open through their existing open…() functions in app.js
   (Planning leads: planning.js); this file only coordinates them. */
(function () {
  "use strict";

  // [sidebar button id, section element id, close function in app.js]
  const SECTIONS = [
    ["btnConnectionsNav", "connectionsModal", "closeConnections"],
    ["btnDashboard", "dashboardModal", "closeDashboard"],
    ["btnFunding", "fundingModal", "closeFunding"],
    ["btnAivDashboard", "aivDashboardModal", "closeAivDashboard"],
    ["btnPlanningLeads", "planningLeadsModal", "closePlanningLeads"],
    ["btnGrowthStudio", "growthStudioModal", "closeGrowthStudio"],
    ["btnBuyerIntelligence", "buyerIntelligenceModal", "closeBuyerIntelligenceModal"],
    ["btnSupplierIntelligence", "supplierIntelligenceModal", "closeSupplierIntelligenceModal"],
    ["btnAnswerBank", "abModal", "closeAnswerBank"],
    ["btnDropdownCompanyProfiles", "cpModal", "closeCpModal"],
    ["btnUserDocuments", "userDocumentsModal", "closeUserDocuments"],
    ["btnHistory", "historyModal", "closeHistoryModal"],
    ["btnProfile", "profileModal", "closeProfile"],
    ["navBilling", "pricingModal", "closePricingModal"],
  ];

  const byId = (id) => document.getElementById(id);
  const isOpen = (el) => Boolean(el) && !el.classList.contains("hidden");

  function closeSection(sectionId) {
    const el = byId(sectionId);
    if (!isOpen(el)) return;
    const entry = SECTIONS.find((s) => s[1] === sectionId);
    const close = entry && window[entry[2]];
    if (typeof close === "function") close();
    else el.classList.add("hidden");
  }

  // The buyer profile drawer floats above every section, so a sidebar click must dismiss it too.
  function closeBuyerDrawer() {
    byId("buyerProfileDrawerModal")?.classList.add("hidden");
  }

  function closeAllExcept(keepId) {
    closeBuyerDrawer();
    SECTIONS.forEach(([, sectionId]) => {
      if (sectionId !== keepId) closeSection(sectionId);
    });
  }

  // The page the address names is the one highlighted, even if another section is also open (re-test 3 BN4: /buyers lit "Pipeline").
  const ROUTE_NAV = {
    buyers: "btnBuyerIntelligence", suppliers: "btnSupplierIntelligence", funding: "btnFunding", planning: "btnPlanningLeads",
    growth: "btnGrowthStudio", answers: "btnAnswerBank", documents: "btnUserDocuments", history: "btnHistory", pipeline: "btnDashboard",
  };
  function pickActive(path, isSectionOpen) {
    const open = SECTIONS.filter(([, sectionId]) => isSectionOpen(sectionId));
    const routeNav = ROUTE_NAV[(String(path || "").split("/")[1] || "").toLowerCase()];
    return open.find(([navId]) => navId === routeNav) || open[0] || null;
  }

  function syncActive() {
    const open = pickActive(location.pathname, (sectionId) => isOpen(byId(sectionId)));
    const activeId = open ? open[0] : "navSearch";
    document.body.classList.toggle("tf-section-open", Boolean(open));
    if (open) document.documentElement.classList.remove("tf-routing");   // the section the address named has opened
    document.querySelectorAll(".tf-nav").forEach((el) => {
      const active = el.id === activeId;
      el.classList.toggle("is-active", active);
      if (active) el.setAttribute("aria-current", "page");
      else el.removeAttribute("aria-current");
    });
  }

  // ── landing page: any sidebar page can be the one the app opens on ──────────────────────────────
  // Kept in the user's server preferences ("landing_page" = a sidebar button id). Hover a menu item and press its star
  // (or right-click the item) to make it the landing page; press it again to go back to Tender search.
  const LANDING_KEY = "landing_page";
  const LANDING_NAVS = ["navSearch"].concat(SECTIONS.map((s) => s[0]));
  let landingId = "navSearch";

  function savePref(value) {
    fetch("/api/user-prefs", {
      method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prefs: { [LANDING_KEY]: value } }),
    }).catch(() => { /* the star still shows the choice for this visit */ });
  }

  function paintLanding() {
    LANDING_NAVS.forEach((id) => {
      const star = byId(id)?.querySelector(".tf-nav__pin");
      if (!star) return;
      const on = id === landingId;
      star.textContent = on ? "★" : "☆";
      star.classList.toggle("is-on", on);
      star.setAttribute("aria-pressed", on ? "true" : "false");
      star.title = on ? "This is your landing page. Press to go back to Tender search." : "Open the app on this page";
    });
  }

  function setLanding(id) {
    landingId = landingId === id ? "navSearch" : id;
    paintLanding();
    savePref(landingId);
  }

  function addLandingStars() {
    LANDING_NAVS.forEach((id) => {
      const nav = byId(id);
      if (!nav || nav.querySelector(".tf-nav__pin")) return;
      const star = document.createElement("span");
      star.className = "tf-nav__pin";
      star.setAttribute("role", "button");
      star.setAttribute("aria-label", "Make this my landing page");
      star.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); setLanding(id); });
      nav.appendChild(star);
      nav.addEventListener("contextmenu", (e) => { e.preventDefault(); setLanding(id); });
    });
    paintLanding();
  }

  // Opens the chosen page once, unless the address already points somewhere or something is already open.
  async function openLanding() {
    let saved = null;
    try {
      const res = await fetch("/api/user-prefs", { credentials: "same-origin" });
      if (!res.ok) return;
      const data = await res.json();
      saved = data && data.prefs ? data.prefs[LANDING_KEY] : null;
    } catch (e) { return; }
    if (!saved || !LANDING_NAVS.includes(saved)) return;
    landingId = saved;
    paintLanding();
    if (saved === "navSearch" || location.search || location.hash) return;
    for (let i = 0; i < 20; i++) { // Growth Studio's button appears once the server config has loaded
      if (SECTIONS.some(([, sectionId]) => isOpen(byId(sectionId)))) return;
      const btn = byId(saved);
      if (btn && !btn.classList.contains("hidden")) { btn.click(); return; }
      await new Promise((r) => setTimeout(r, 250));
    }
  }

  function init() {
    if (!document.body.classList.contains("tf-shell")) return;
    addLandingStars();
    openLanding();

    // Capture phase: close the current section before the button's own handler opens the next one.
    SECTIONS.forEach(([navId, sectionId]) => {
      byId(navId)?.addEventListener("click", () => closeAllExcept(sectionId), true);
    });
    byId("navSearch")?.addEventListener("click", () => closeAllExcept(null));
    byId("navBilling")?.addEventListener("click", () => {
      if (typeof window.openPricingModal === "function") window.openPricingModal();
    });

    const observer = new MutationObserver(syncActive);
    const watch = (el) => el && observer.observe(el, { attributes: true, attributeFilter: ["class"] });
    SECTIONS.forEach(([, sectionId]) => watch(byId(sectionId)));
    // Connections builds its page the first time it is opened
    document.addEventListener("connections:created", () => { watch(byId("connectionsModal")); syncActive(); });
    syncActive();
  }

  if (typeof module !== "undefined" && module.exports) module.exports = { pickActive, ROUTE_NAV, SECTIONS };
  if (typeof document === "undefined") return;
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
