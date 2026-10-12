/* Small page behaviours that go with responsive-fixes.css:
   - a Filters button that opens / closes the filter chips on a phone;
   - accessible names for icon buttons and form fields that had none (QA: 7 buttons, 15 fields),
     and a page heading. Names are taken from the control's own title, placeholder or id. */
(function () {
  "use strict";

  function humanise(text) {
    return String(text || "")
      .replace(/^(btn|chk|txt|sel|chip|rm|lbl|inp)(?=[A-Z_-])/, "")
      .replace(/[_-]+/g, " ")
      .replace(/([a-z])([A-Z])/g, "$1 $2")
      .replace(/\s+/g, " ")
      .trim()
      .replace(/^./, (c) => c.toUpperCase());
  }

  function hasName(el) {
    if (el.getAttribute("aria-label") || el.getAttribute("aria-labelledby")) return true;
    if (el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`)) return true;
    if (el.closest("label")) return true;
    if (el.tagName === "BUTTON" && el.textContent.trim()) return true;
    return false;
  }

  function nameFor(el) {
    return el.getAttribute("title") || el.getAttribute("placeholder") || humanise(el.id || el.getAttribute("name") || el.className.split(" ")[0]);
  }

  function labelControls(root) {
    (root || document).querySelectorAll("button, input:not([type=hidden]), select, textarea").forEach((el) => {
      if (hasName(el)) return;
      const name = nameFor(el);
      if (name) el.setAttribute("aria-label", name);
    });
  }

  function addHeading() {
    if (document.querySelector("h1")) return;
    const main = document.getElementById("mainContent") || document.querySelector("main") || document.body;
    const h1 = document.createElement("h1");
    h1.className = "sr-only";
    h1.textContent = "TenderFlow: public tender search";
    main.insertBefore(h1, main.firstChild);
  }

  function addFiltersButton() {
    const bar = document.getElementById("focusPillBar");
    if (!bar || document.getElementById("btnMobileFilters")) return;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.id = "btnMobileFilters";
    btn.className = "tf-mobile-filters-btn";
    btn.setAttribute("aria-expanded", "false");
    btn.setAttribute("aria-controls", "focusPillBar");
    btn.textContent = "Filters";
    btn.addEventListener("click", () => {
      const open = bar.classList.toggle("is-open");
      btn.setAttribute("aria-expanded", String(open));
    });
    const keyword = bar.querySelector(".tf-keyword");
    if (keyword && keyword.nextSibling) bar.insertBefore(btn, keyword.nextSibling);
    else bar.appendChild(btn);
  }

  function init() {
    addHeading();
    addFiltersButton();
    labelControls();
    // content drawn later (dialogs, result rows, popovers): label it as it appears
    let timer = null;
    new MutationObserver(() => {
      clearTimeout(timer);
      timer = setTimeout(() => labelControls(), 400);
    }).observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
