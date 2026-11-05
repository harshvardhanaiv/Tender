/* "My profile" page furniture (section nav, AI provider cards, unsaved note) and the "Bid on custom tender" 3-step page.
   The profile keeps every control it had; this file only adds navigation around them. The custom tender page collects the
   details and files, then hands over to the existing bid assistant through window.startCustomBid (app.js). */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const isHidden = (el) => !el || el.classList.contains("hidden");

  // ── My profile ─────────────────────────────────────────────────────────────────────────────────
  function initProfile() {
    const modal = $("profileModal");
    const body = modal && modal.querySelector(".profile-body");
    if (!modal || !body || body.querySelector(".pf-nav")) return;

    const nav = document.createElement("nav");
    nav.className = "pf-nav";
    nav.setAttribute("aria-label", "Profile sections");
    body.insertBefore(nav, body.firstChild);

    const sections = () => Array.from(body.querySelectorAll(":scope > [data-pf-nav]")).filter((s) => s.offsetParent !== null || s.style.display !== "none");
    function buildNav() {
      nav.textContent = "";
      sections().forEach((s) => {
        const a = document.createElement("a");
        a.textContent = s.getAttribute("data-pf-nav");
        a.setAttribute("role", "button");
        a.tabIndex = 0;
        const go = () => s.scrollIntoView({ behavior: "smooth", block: "start" });
        a.addEventListener("click", go);
        a.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
        a._section = s;
        nav.appendChild(a);
      });
      spy();
    }
    function spy() {
      const top = body.getBoundingClientRect().top + 60;
      let current = null;
      Array.from(nav.children).forEach((a) => { if (a._section.getBoundingClientRect().top <= top) current = a; });
      current = current || nav.firstElementChild;
      Array.from(nav.children).forEach((a) => a.classList.toggle("is-active", a === current));
    }
    body.addEventListener("scroll", spy, { passive: true });

    // AI provider cards, driven by the select (which stays the source of truth for the existing save logic)
    const sel = $("aiProviderSelect");
    let cards = null;
    function buildCards() {
      if (!sel) return;
      if (!cards) {
        cards = document.createElement("div");
        cards.className = "pf-ai";
        sel.parentElement.parentElement.insertBefore(cards, sel.parentElement);
        sel.addEventListener("change", paintCards);
      }
      cards.textContent = "";
      Array.from(sel.options).forEach((o) => {
        const m = /^(.*?)\s*\((.*)\)\s*$/.exec(o.textContent.trim());
        const b = document.createElement("button");
        b.type = "button";
        b.dataset.value = o.value;
        const name = document.createElement("b"); name.textContent = m ? m[1] : o.textContent.trim();
        const sub = document.createElement("small"); sub.textContent = m ? m[2] : "";
        b.append(name, sub);
        b.addEventListener("click", () => { sel.value = o.value; sel.dispatchEvent(new Event("change", { bubbles: true })); paintCards(); });
        cards.appendChild(b);
      });
      paintCards();
    }
    function paintCards() {
      if (!cards) return;
      Array.from(cards.children).forEach((b) => b.classList.toggle("is-on", b.dataset.value === sel.value));
    }

    // "Unsaved changes will be lost" shows while the Save button is live
    const save = $("btnSaveProfileGlobal"), note = $("profileUnsavedNote");
    if (save && note) {
      const sync = () => note.classList.toggle("hidden", save.disabled);
      new MutationObserver(sync).observe(save, { attributes: true, attributeFilter: ["disabled"] });
      sync();
    }

    const refresh = () => { if (!isHidden(modal)) { buildNav(); buildCards(); } };
    new MutationObserver(refresh).observe(modal, { attributes: true, attributeFilter: ["class"] });
    refresh();
    // sections that appear later (admin, outreach) change the list
    // (only the sections themselves count: the nav and cards toggle classes of their own)
    new MutationObserver((muts) => {
      if (isHidden(modal) || !muts.some((m) => m.target.hasAttribute && m.target.hasAttribute("data-pf-nav"))) return;
      const labels = sections().map((s) => s.getAttribute("data-pf-nav")).join("|");
      if (labels !== Array.from(nav.children).map((a) => a.textContent).join("|")) buildNav();
    }).observe(body, { attributes: true, subtree: true, attributeFilter: ["class", "style"] });
  }

  // ── Bid on custom tender ───────────────────────────────────────────────────────────────────────
  const MAX_FILE = 5 * 1024 * 1024;
  const ct = { step: 1, files: [] };

  function initCustomTender() {
    const modal = $("customTenderModal");
    if (!modal) return;
    const q = (s) => modal.querySelector(s);
    const qa = (s) => Array.from(modal.querySelectorAll(s));
    const err = $("ctError");

    function showError(msg) { err.textContent = msg || ""; err.hidden = !msg; }
    function detailsOk() {
      if (!$("ctInTitle").value.trim()) { showError("Add a tender title to continue."); $("ctInTitle").focus(); return false; }
      showError("");
      return true;
    }
    function fmtDate(v) {
      if (!v) return "Not set";
      const [y, m, d] = v.split("-");
      return d && m && y ? `${d}/${m}/${y}` : v;
    }
    function review() {
      const val = $("ctInValue").value;
      const rows = [
        ["Tender title", $("ctInTitle").value.trim()],
        ["Buyer", $("ctInBuyer").value.trim() || "Not set"],
        ["Submission deadline", fmtDate($("ctInDeadline").value)],
        ["Estimated value", val ? "£" + Number(val).toLocaleString("en-GB") : "Not set"],
        ["Link to notice", $("ctInLink").value.trim() || "Not set"],
        ["Documents", ct.files.length ? ct.files.map((f) => f.filename).join(", ") : ($("ctInText").value.trim() ? "Pasted details" : "None added")],
      ];
      const dl = $("ctReview");
      dl.textContent = "";
      rows.forEach(([k, v]) => { const dt = document.createElement("dt"), dd = document.createElement("dd"); dt.textContent = k; dd.textContent = v; dl.append(dt, dd); });
    }
    function go(step) {
      if (step > 1 && !detailsOk()) step = 1;
      ct.step = step;
      qa("[data-ct-panel]").forEach((p) => p.classList.toggle("is-active", Number(p.dataset.ctPanel) === step));
      qa("[data-ct-step]").forEach((b) => {
        const n = Number(b.dataset.ctStep);
        b.classList.toggle("is-active", n === step);
        b.classList.toggle("is-done", n < step);
      });
      if (step === 3) review();
    }
    qa("[data-ct-step]").forEach((b) => b.addEventListener("click", () => go(Number(b.dataset.ctStep))));
    qa("[data-ct-next]").forEach((b) => b.addEventListener("click", () => go(Number(b.dataset.ctNext))));
    modal.addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target.matches("input") && ct.step === 1) { e.preventDefault(); go(2); } });

    function renderFiles() {
      const ul = $("ctFileList");
      ul.textContent = "";
      ct.files.forEach((f, i) => {
        const li = document.createElement("li");
        const name = document.createElement("span"); name.textContent = f.filename;
        const rm = document.createElement("button"); rm.type = "button"; rm.textContent = "Remove"; rm.setAttribute("aria-label", "Remove " + f.filename);
        rm.addEventListener("click", () => { ct.files.splice(i, 1); renderFiles(); });
        li.append(name, rm);
        ul.appendChild(li);
      });
    }
    function addFiles(list) {
      Array.from(list || []).forEach((file) => {
        if (file.size > MAX_FILE) { showError(`${file.name} is larger than 5 MB.`); return; }
        if (ct.files.some((f) => f.filename === file.name)) return;
        const reader = new FileReader();
        reader.onload = (e) => {
          let bin = "";
          const bytes = new Uint8Array(e.target.result);
          for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
          ct.files.push({ filename: file.name, content: btoa(bin) });
          renderFiles();
        };
        reader.readAsArrayBuffer(file);
      });
    }
    $("ctFiles").addEventListener("change", (e) => { showError(""); addFiles(e.target.files); e.target.value = ""; });
    const drop = $("ctDrop");
    ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("is-over"); }));
    ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("is-over"); }));
    drop.addEventListener("drop", (e) => addFiles(e.dataTransfer && e.dataTransfer.files));

    function close() { modal.classList.add("hidden"); }
    function open() {
      ["ctInTitle", "ctInBuyer", "ctInDeadline", "ctInValue", "ctInLink", "ctInText"].forEach((id) => { $(id).value = ""; });
      $("ctInPipeline").checked = true;
      ct.files = [];
      renderFiles();
      showError("");
      go(1);
      modal.classList.remove("hidden");
      setTimeout(() => $("ctInTitle").focus(), 50);
    }
    $("ctCancel").addEventListener("click", close);
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !isHidden(modal) && !document.querySelector(".modal-overlay:not(.hidden):not(#customTenderModal)[style*='z-index']")) close(); });
    // a sidebar click leaves this page
    document.querySelectorAll(".tf-nav").forEach((n) => n.addEventListener("click", close, true));

    $("ctStart").addEventListener("click", () => {
      if (!detailsOk()) { go(1); return; }
      const details = {
        title: $("ctInTitle").value.trim(), buyer: $("ctInBuyer").value.trim(), deadline: $("ctInDeadline").value,
        value: $("ctInValue").value, link: $("ctInLink").value.trim(), text: $("ctInText").value.trim(),
        files: ct.files.slice(), addToPipeline: $("ctInPipeline").checked,
      };
      close();
      if (typeof window.startCustomBid === "function") window.startCustomBid(details);
    });
    window.openCustomTenderPage = open;
  }

  // "Upload document" in the Documents header uses the same file picker as the drop strip
  function initDocuments() {
    const btn = $("btnDocsUploadTop");
    if (btn) btn.addEventListener("click", () => { const z = $("profileUploadZone"); if (z) z.click(); });
  }

  // ── Chip filters: a select shown as a row of chips. The select stays the source of truth (every existing handler and the
  // "Clear all" reset keep working); a click sets it and fires its change event, and the chips repaint when it changes. ──
  function shortLabel(text) {
    return String(text).replace(/^All UK nations$/, "All UK").replace(/^Any (size|decision)$/, "Any").replace(/\s+schemes$/i, "");
  }
  function chipify(selectId, label, opts) {
    const sel = $(selectId);
    if (!sel || sel._pgChips) return;
    const options = Array.from(sel.options);
    if (options.length < 2) return;
    const wrap = document.createElement("div");
    wrap.className = "pg-chipset" + (opts && opts.segmented ? " pg-chipset--seg" : "");
    if (label) {
      const l = document.createElement("div");
      l.className = "pg-chipset__label";
      l.textContent = label;
      wrap.appendChild(l);
    }
    const row = document.createElement("div");
    row.className = "pg-chips";
    row.setAttribute("role", "group");
    row.setAttribute("aria-label", label || sel.getAttribute("aria-label") || "Filter");
    wrap.appendChild(row);
    options.forEach((o) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "pg-chipbtn";
      b.dataset.v = o.value;
      b.textContent = opts && opts.short ? shortLabel(o.textContent.trim()) : o.textContent.trim();
      b.addEventListener("click", () => {
        if (sel.value === o.value) return;
        sel.value = o.value;
        sel.dispatchEvent(new Event("change", { bubbles: true }));
        paint();
      });
      row.appendChild(b);
    });
    function paint() {
      row.querySelectorAll(".pg-chipbtn").forEach((b) => {
        const on = b.dataset.v === sel.value;
        b.classList.toggle("is-on", on);
        b.setAttribute("aria-pressed", String(on));
      });
    }
    sel.parentNode.insertBefore(wrap, sel.nextSibling);
    sel.classList.add("pg-chip-src");
    sel.addEventListener("change", paint);
    sel._pgChips = true;
    paint();
    // a script that resets the select (Clear all, a saved search) does not fire change: repaint while the page is open
    setInterval(() => { if (sel.offsetParent !== null || wrap.offsetParent !== null) paint(); }, 600);
  }

  // ── Planning leads: the three figures above the list ──
  function initPlanning() {
    chipify("selPlanningCountry", "Nation", { short: true });
    chipify("selPlanningSize", "Size", { short: true, segmented: true });
    chipify("selPlanningState", "Decision", { short: true });
    chipify("selPlanningType", "Scheme type", { short: true });
    const modal = $("planningLeadsModal");
    if (!modal) return;
    let loaded = false;
    const fmt = (n) => (Number.isFinite(n) ? n.toLocaleString("en-GB") : "–");
    function load() {
      if (loaded || isHidden(modal)) return;
      loaded = true;
      fetch("/api/planning/stats", { credentials: "same-origin" })
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => {
          const set = (id, v) => { const el = $(id); if (el) el.textContent = fmt(v); };
          if (!d) { loaded = false; $("planningStats") && $("planningStats").classList.add("is-empty"); return; }
          set("plStatTotal", d.total); set("plStatSubmitted", d.new_last_7_days); set("plStatApproved", d.approved_last_7_days);
        })
        .catch(() => { loaded = false; });
    }
    new MutationObserver(load).observe(modal, { attributes: true, attributeFilter: ["class"] });
    load();
  }

  // ── Buyer intelligence: the buyer-type select as chips ──
  function initBuyer() {
    chipify("selBuyerTypeFilter", "", {});
    // The spend / distance / signal filters and Clear all sit on the right of the tab row (same line as Map view).
    // The elements are moved, not copied, so their ids and handlers keep working.
    const bar = $("buyerFilterBar");
    const tabs = document.querySelector("#buyerIntelligenceModal .buyer-tabs");
    if (!bar || !tabs || tabs.querySelector(".buyer-tabs__filters")) return;
    const box = document.createElement("div");
    box.className = "buyer-tabs__filters";
    Array.from(bar.children).filter((el) => el.matches(".tf-chip-wrap, .tf-clearall")).forEach((el) => box.appendChild(el));
    if (box.children.length) tabs.appendChild(box);
  }

  // ── Supplier intelligence: the List / Map switch sits at the right of the filter card ──
  function initSupplier() {
    const bar = $("supplierFilterBar");
    const tabs = document.querySelector("#supplierIntelligenceModal .buyer-tabs");
    if (!bar || !tabs || bar.contains(tabs)) return;
    tabs.classList.add("sp-viewswitch");
    bar.appendChild(tabs);
  }

  // ── Pipeline: the search box sits at the right of the page header ──
  function initPipeline() {
    const head = document.querySelector("#dashboardModal .modal__header--dashboard");
    const body = $("dashboardBody");
    const bar = $("pipelineFilterBar");
    if (!head || !body || !bar) return;
    const search = bar.querySelector(".tf-search");
    if (search && !head.contains(search)) head.insertBefore(search, head.querySelector(".dashboard-header-right") || null);
    // The type chips and the portal filter sit under the figures, above the board. The board is redrawn from scratch, which
    // drops anything placed inside it, so the bar is put back after every redraw (the same element, so its handlers stay).
    function place() {
      if (body.contains(bar)) return;
      const stats = body.querySelector(".dash-stats");
      if (stats) stats.after(bar); else body.prepend(bar);
    }
    new MutationObserver(place).observe(body, { childList: true });
    place();
  }

  // ── App shell: brand into the side panel, an account card at its foot, a breadcrumb in the top bar ──
  function initShell() {
    const rail = document.querySelector(".tf-rail");
    const mast = document.querySelector(".tf-masthead");
    if (!rail || !mast || rail.querySelector(".tf-brand")) return;
    const brand = mast.querySelector(".tf-brand");
    if (brand) rail.insertBefore(brand, rail.firstChild);

    const crumb = document.createElement("div");
    crumb.className = "tf-crumb";
    crumb.id = "tfCrumb";
    mast.insertBefore(crumb, mast.querySelector(".tf-masthead__right"));

    const text = (id) => { const el = $(id); return el ? el.textContent.trim() : ""; };
    function refresh() {
      let parts = null;
      const open = Array.from(document.querySelectorAll(".modal-overlay:not(.hidden)")).reverse().find((m) => m.querySelector(".pg-crumb"));
      if (open) {
        const c = open.querySelector(".pg-crumb");
        parts = [c.querySelector("span") && c.querySelector("span").textContent, c.querySelector("b") && c.querySelector("b").textContent];
      } else {
        const nav = document.querySelector(".tf-nav.is-active");
        if (nav) {
          let g = nav.previousElementSibling;
          while (g && !g.classList.contains("tf-rail__group")) g = g.previousElementSibling;
          parts = [g ? g.textContent.trim() : "Find work", (nav.getAttribute("title") || nav.textContent || "").replace(/\s*[★☆].*$/, "").trim()];
        }
      }
      if (parts && parts[1]) {
        const key = parts[0] + "|" + parts[1];
        if (crumb.dataset.k !== key) {
          crumb.dataset.k = key;
          crumb.textContent = "";
          const a = document.createElement("span"); a.textContent = parts[0] || "";
          const sep = document.createElement("i"); sep.textContent = "/";
          const b = document.createElement("b"); b.textContent = parts[1];
          crumb.append(a, sep, b);
        }
      }
    }
    refresh();
    setInterval(refresh, 500);
  }

  // ── Tender search: the Table / Cards switch joins the result count row, so the table gets the height ──
  function initResults() {
    const head = document.querySelector(".card--main .tf-cardhead");
    const toggle = document.querySelector(".card--main .view-toggle");
    if (!head || !toggle || head.contains(toggle)) return;
    toggle.classList.add("pg-viewtoggle-inline");
    head.appendChild(toggle);
  }

  function init() { initResults(); initShell(); initPipeline(); initSupplier(); initProfile(); initCustomTender(); initDocuments(); initPlanning(); initBuyer(); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
