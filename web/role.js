/* Buyer and supplier views of the same account.
   The login page remembers which portal was chosen (localStorage "tf_role"). Supplier is the full app, unchanged.
   Buyer shows only: Buyer workspace, Buyer intelligence, Planning leads and Funding, with no link across to the supplier view.
   This is a view choice, not a permission: the data is the same and the server still checks every request. */
(function () {
  "use strict";
  var role = "supplier";
  try { role = localStorage.getItem("tf_role") === "buyer" ? "buyer" : "supplier"; } catch (e) { /* storage blocked */ }
  window.tfRole = role;
  window.tfSwitchRole = function (next) {
    try { localStorage.setItem("tf_role", next === "buyer" ? "buyer" : "supplier"); } catch (e) { /* storage blocked */ }
    location.href = next === "buyer" ? "/buyer-workspace" : "/";
  };
  if (role !== "buyer") return;

  var path = (location.pathname.replace(/\/+$/, "") || "/");
  var BUYER_PATH = /^\/(buyer-workspace|buyers|planning|funding)(\/|$)/;
  if (!BUYER_PATH.test(path)) { location.replace("/buyer-workspace"); return; }

  document.documentElement.classList.add("role-buyer");

  // Closing a section sends the app back to "/" (tender search). A buyer goes back to the buyer workspace instead.
  ["replaceState", "pushState"].forEach(function (fn) {
    var orig = history[fn].bind(history);
    history[fn] = function (state, title, url) {
      try {
        if (url != null && new URL(url, location.href).pathname === "/" && location.pathname !== "/") { location.replace("/buyer-workspace"); return; }
      } catch (e) { /* fall through */ }
      return orig(state, title, url);
    };
  });

  var css = [
    "html.role-buyer #navSearch, html.role-buyer #btnDashboard, html.role-buyer #btnSupplierIntelligence,",
    "html.role-buyer #btnAivDashboard, html.role-buyer #navGrowthGroup, html.role-buyer #btnGrowthStudio,",
    "html.role-buyer #btnAnswerBank, html.role-buyer #btnDropdownCompanyProfiles, html.role-buyer #btnUserDocuments,",
    "html.role-buyer #btnHistory, html.role-buyer #btnProfile, html.role-buyer #btnConnectionsNav, html.role-buyer #navBilling,",
    "html.role-buyer #linkBuyerWorkspace,",
    "html.role-buyer .tf-rail__group:has(> [data-i18n=\"group_bid_content\"]),",
    "html.role-buyer .tf-rail__group:has(> [data-i18n=\"group_account\"]) { display: none !important; }"
  ].join("\n");

  var ICON_WS = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4.5"/><path d="M12 12l6-6"/></svg>';
  var ICON_BUYER = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 21h18M5 21V9l7-5 7 5v12M9 21v-6h6v6"/></svg>';
  var ICON_PLAN = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 21h18M6 21V11l6-4 6 4v10M10 21v-5h4v5M12 3v4"/></svg>';
  var ICON_FUND = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8"/><path d="M14.5 9.5c-.6-.8-1.5-1.2-2.5-1.2-1.4 0-2.5.8-2.5 1.9 0 2.6 5 1.3 5 3.9 0 1.1-1.1 1.9-2.5 1.9-1.1 0-2-.5-2.6-1.3M12 6.5v1.8M12 15.7v1.8"/></svg>';

  function link(href, icon, label, title) {
    var a = document.createElement("a");
    a.className = "tf-nav"; a.href = href; a.title = title || label;
    a.innerHTML = icon + "<span></span>"; a.lastChild.textContent = label;
    return a;
  }
  function group(label) {
    var g = document.createElement("div"); g.className = "tf-rail__group";
    var s = document.createElement("span"); s.className = "tf-rail__group-text"; s.textContent = label; g.appendChild(s);
    return g;
  }

  document.addEventListener("DOMContentLoaded", function () {
    var st = document.createElement("style"); st.textContent = css; document.head.appendChild(st);
    var list = document.querySelector(".tf-rail__list");
    if (!list) return;

    if (path.indexOf("/buyer-workspace") === 0) {
      // Buyer workspace page: add the other buyer sections and swap the supplier link for a switch back.
      var sw = [].find.call(list.querySelectorAll("a.tf-nav"), function (a) { return a.getAttribute("href") === "/"; });
      var swGroup = sw && sw.previousElementSibling;
      var anchor = swGroup || null;
      var add = [group("Market intelligence"), link("/buyers", ICON_BUYER, "Buyer intelligence"), link("/planning", ICON_PLAN, "Planning leads"), link("/funding", ICON_FUND, "Funding")];
      add.forEach(function (el) { list.insertBefore(el, anchor); });
      // Buyers have no way across to the supplier workspace from here (buyer pricing will be separate).
      if (sw) sw.remove();
      if (swGroup) swGroup.remove();
      return;
    }

    // Main app: relabel the first group, then put the buyer workspace first and a switch at the bottom.
    var top = list.querySelector(".tf-rail__group--top .tf-rail__group-text");
    if (top) { top.removeAttribute("data-i18n"); top.textContent = "Buyer portal"; }
    var first = list.querySelector("#navSearch");
    list.insertBefore(link("/buyer-workspace", ICON_WS, "Buyer workspace", "Market Radar and Market engagement"), first);
  });
})();
