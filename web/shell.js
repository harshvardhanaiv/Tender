/* TenderFlow shell: sidebar highlighting and one section open at a time.
   Sections still open through their existing open…() functions in app.js
   (Planning leads: planning.js); this file only coordinates them. */
(function () {
  "use strict";

  // [sidebar button id, section element id, close function in app.js]
  const SECTIONS = [
    ["btnDashboard", "dashboardModal", "closeDashboard"],
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

  function syncActive() {
    const open = SECTIONS.find(([, sectionId]) => isOpen(byId(sectionId)));
    const activeId = open ? open[0] : "navSearch";
    document.body.classList.toggle("tf-section-open", Boolean(open));
    document.querySelectorAll(".tf-nav").forEach((el) => {
      const active = el.id === activeId;
      el.classList.toggle("is-active", active);
      if (active) el.setAttribute("aria-current", "page");
      else el.removeAttribute("aria-current");
    });
  }

  function init() {
    if (!document.body.classList.contains("tf-shell")) return;

    // Capture phase: close the current section before the button's own handler opens the next one.
    SECTIONS.forEach(([navId, sectionId]) => {
      byId(navId)?.addEventListener("click", () => closeAllExcept(sectionId), true);
    });
    byId("navSearch")?.addEventListener("click", () => closeAllExcept(null));
    byId("navBilling")?.addEventListener("click", () => {
      if (typeof window.openPricingModal === "function") window.openPricingModal();
    });

    const observer = new MutationObserver(syncActive);
    SECTIONS.forEach(([, sectionId]) => {
      const el = byId(sectionId);
      if (el) observer.observe(el, { attributes: true, attributeFilter: ["class"] });
    });
    syncActive();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
