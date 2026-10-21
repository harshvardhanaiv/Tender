/* Drop-a-pin location picker and "remember my last location" for the three distance panels
   (Supplier Intelligence, Buyer Intelligence, Planning). Each panel keeps its own select / postcode box / GPS button;
   this file adds a "Pin on map" button beside the GPS one and remembers whatever place the reader last chose, so the next
   search (and the next visit) starts from it until they change or clear it.

   How it learns the place without touching the panels' own handlers: every panel rewrites its status line when the place
   changes, and the panel keeps the coordinates in a state object (Supplier, Buyer) or in hidden lat/lng fields (Planning).
   The status line is watched; the coordinates are read from there. */
(function (root) {
  "use strict";
  var LONDON = { lat: 51.5074, lon: -0.1278, label: "London" };
  var PANELS = [
    { name: "supplier", state: function () { return root._supplierFilterState; }, sel: "selSupplierLocationCity", txt: "txtSupplierLocationCustom", lbl: "lblSupplierLocationStatus", gps: "btnSupplierUseBrowserLocation", radius: "rngSupplierRadiusFilter" },
    { name: "buyer", state: function () { return root._buyerFilterState; }, sel: "selBuyerLocationCity", txt: "txtBuyerLocationCustom", lbl: "lblLocationStatus", gps: "btnUseBrowserLocation", radius: "rngRadiusFilter" },
    { name: "planning", form: "planningFilterBar", sel: "selPlanningLocationCity", txt: "txtPlanningLocationCustom", lbl: "lblPlanningLocationStatus", gps: "btnPlanningUseBrowserLocation", radius: "rngPlanningRadiusFilter" }
  ];
  var keyFn = function () { return "tf_last_location"; };

  function valid(loc) {
    return !!loc && isFinite(loc.lat) && isFinite(loc.lon) && Math.abs(loc.lat) <= 90 && Math.abs(loc.lon) <= 180 && String(loc.label || "").length > 0;
  }
  function recall() {
    try { var v = JSON.parse(localStorage.getItem(keyFn()) || "null"); return valid(v) ? v : null; } catch (e) { return null; }
  }
  function remember(loc) {
    if (!valid(loc)) return false;
    try { localStorage.setItem(keyFn(), JSON.stringify({ lat: +loc.lat, lon: +loc.lon, label: String(loc.label).slice(0, 120) })); return true; } catch (e) { return false; }
  }
  function forget() { try { localStorage.removeItem(keyFn()); } catch (e) { /* storage blocked */ } }
  function configure(opts) { if (opts && opts.storageKey) keyFn = opts.storageKey; }

  function byId(id) { return document.getElementById(id); }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function fmt(loc) { return Number(loc.lat).toFixed(2) + ", " + Number(loc.lon).toFixed(2); }

  function read(p) {
    if (p.form) {
      var f = byId(p.form); if (!f || !f.elements.lat) return null;
      return { lat: parseFloat(f.elements.lat.value), lon: parseFloat(f.elements.lng.value) };
    }
    var st = p.state(); return st ? { lat: st.userLat, lon: st.userLon, label: st.locationLabel } : null;
  }
  function write(p, loc) {
    if (p.form) {
      var f = byId(p.form); if (!f || !f.elements.lat) return false;
      f.elements.lat.value = loc.lat; f.elements.lng.value = loc.lon; return true;
    }
    var st = p.state(); if (!st) return false;
    st.userLat = loc.lat; st.userLon = loc.lon; st.locationLabel = loc.label; return true;
  }

  function setPinOption(p, loc) {
    var sel = byId(p.sel), lbl = byId(p.lbl), txt = byId(p.txt);
    if (sel) {
      var opt = sel.querySelector("option[value='pin']");
      if (!opt) { opt = document.createElement("option"); opt.value = "pin"; sel.insertBefore(opt, sel.firstChild); }
      opt.textContent = "📌 " + loc.label;
      sel.value = "pin";
    }
    if (txt) txt.classList.add("hidden");
    if (lbl) lbl.innerHTML = "📌 Measuring distance from <strong>" + esc(loc.label) + "</strong> (" + fmt(loc) + ")";
  }

  // Put a place into a panel. refresh=true re-runs the panel's search the way its own distance slider does.
  function apply(p, loc, refresh) {
    if (!write(p, loc)) return;
    setPinOption(p, loc);
    var r = byId(p.radius);
    if (refresh && r) {
      if (parseFloat(r.value) === 0) {
        r.value = "25"; r.dispatchEvent(new Event("input", { bubbles: true }));   // "Any distance" would ignore the place
      }
      r.dispatchEvent(new Event("change", { bubbles: true }));
    }
  }
  function applyEverywhere(loc, except) {
    PANELS.forEach(function (p) { if (p !== except) { try { apply(p, loc, false); } catch (e) { /* panel not on this page */ } } });
  }

  // ── the map ───────────────────────────────────────────────────────────────────────────────
  function openPicker(start, done) {
    if (!root.L) { if (root.showToast) root.showToast("The map is still loading. Try again in a moment.", "error"); return; }
    var from = valid(start) ? start : LONDON;
    var chosen = null;
    var wrap = document.createElement("div");
    wrap.className = "tf-pin-modal";
    wrap.innerHTML = '<div class="tf-pin-modal__box" role="dialog" aria-modal="true" aria-label="Pin a location">' +
      '<div class="tf-pin-modal__head"><b>Pin your location</b><button type="button" class="tf-pin-modal__x" aria-label="Close">×</button></div>' +
      '<div class="tf-pin-modal__search"><input type="text" class="input" placeholder="Find a postcode or town, or click the map to drop a pin" aria-label="Find a postcode or town">' +
      '<button type="button" class="btn btn--secondary tf-pin-find">Find</button></div>' +
      '<div class="tf-pin-modal__map" tabindex="-1"></div>' +
      '<div class="tf-pin-modal__foot"><span class="tf-pin-modal__hint">Click anywhere to drop a pin. Drag it to adjust.</span>' +
      '<button type="button" class="btn btn--secondary tf-pin-cancel">Cancel</button><button type="button" class="btn btn--primary tf-pin-use" disabled>Use this location</button></div></div>';
    document.body.appendChild(wrap);
    var map = root.L.map(wrap.querySelector(".tf-pin-modal__map")).setView([from.lat, from.lon], valid(start) ? 11 : 6);
    root.L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18, attribution: "© OpenStreetMap contributors" }).addTo(map);
    var marker = null, use = wrap.querySelector(".tf-pin-use"), hint = wrap.querySelector(".tf-pin-modal__hint");
    function drop(lat, lon, label) {
      chosen = { lat: lat, lon: lon, label: label || "Pinned location" };
      if (!marker) {
        marker = root.L.marker([lat, lon], { draggable: true }).addTo(map);
        marker.on("dragend", function () { var ll = marker.getLatLng(); drop(ll.lat, ll.lng); });
      } else marker.setLatLng([lat, lon]);
      use.disabled = false;
      hint.textContent = "📌 " + chosen.label + " (" + fmt(chosen) + ")";
    }
    if (valid(start)) drop(start.lat, start.lon, start.label);
    map.on("click", function (e) { drop(e.latlng.lat, e.latlng.lng); });
    function close() { try { map.remove(); } catch (e) { /* already gone */ } wrap.remove(); document.removeEventListener("keydown", onKey); }
    function onKey(e) { if (e.key === "Escape") close(); }
    document.addEventListener("keydown", onKey);
    function find() {
      var q = wrap.querySelector("input").value.trim();
      if (!q || typeof root.resolveLocationAsync !== "function") return;
      hint.textContent = "Looking up…";
      root.resolveLocationAsync(q).then(function (f) {
        if (!f) { hint.textContent = "Could not find “" + q + "”. Click the map instead."; return; }
        map.setView([f.lat, f.lon], 12);
        drop(f.lat, f.lon, f.name || q);
      });
    }
    wrap.querySelector(".tf-pin-find").addEventListener("click", find);
    wrap.querySelector("input").addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); find(); } });
    wrap.querySelector(".tf-pin-cancel").addEventListener("click", close);
    wrap.querySelector(".tf-pin-modal__x").addEventListener("click", close);
    wrap.addEventListener("mousedown", function (e) { if (e.target === wrap) close(); });
    use.addEventListener("click", function () { var c = chosen; close(); if (c && done) done(c); });
    setTimeout(function () { map.invalidateSize(); }, 50);
  }

  // ── wiring ───────────────────────────────────────────────────────────────────────────────
  function bind(p) {
    var gps = byId(p.gps), lbl = byId(p.lbl), sel = byId(p.sel);
    if (!gps || !lbl || !sel || gps.dataset.pinBound) return;
    gps.dataset.pinBound = "1";
    var pin = document.createElement("button");
    pin.type = "button"; pin.className = "btn btn--secondary btn--icon tf-pin-btn"; pin.title = "Drop a pin on the map"; pin.setAttribute("aria-label", "Drop a pin on the map");
    pin.textContent = "📌";
    gps.insertAdjacentElement("afterend", pin);
    var clear = document.createElement("button");
    clear.type = "button"; clear.className = "btn btn--secondary btn--icon tf-pin-clear hidden"; clear.title = "Forget my saved location"; clear.setAttribute("aria-label", "Forget my saved location"); clear.textContent = "✕";
    pin.insertAdjacentElement("afterend", clear);
    function showClear() { clear.classList.toggle("hidden", !recall()); }

    pin.addEventListener("click", function () {
      var cur = read(p);
      openPicker(valid(Object.assign({ label: "x" }, cur)) ? { lat: cur.lat, lon: cur.lon, label: (cur && cur.label) || "Current location" } : recall(), function (loc) {
        remember(loc); apply(p, loc, true); applyEverywhere(loc, p); showClear();
        if (root.showToast) root.showToast("Location saved. Searches start from here until you change or clear it.", "success");
      });
    });
    clear.addEventListener("click", function () {
      forget(); showClear();
      var london = Object.assign({}, LONDON);
      PANELS.forEach(function (q) {
        var s = byId(q.sel); if (!s) return;
        write(q, london);
        var o = s.querySelector("option[value='pin']"); if (o) o.remove();
        var l = byId(q.lbl); if (l) l.textContent = "Measuring distance from London";
        s.value = s.querySelector("option[value='London']") ? "London" : s.value;
      });
      var r = byId(p.radius); if (r) r.dispatchEvent(new Event("change", { bubbles: true }));
      if (root.showToast) root.showToast("Saved location cleared.", "success");
    });

    // Learn the place from the panel's own status line.
    var timer = null;
    new MutationObserver(function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        var v = sel.value;
        if (v === "custom") return;                       // still typing
        if (v === "profile") { forget(); showClear(); return; }   // went back to the profile default
        var loc = read(p); var label = (loc && loc.label) || (v === "pin" ? "" : sel.options[sel.selectedIndex] ? sel.options[sel.selectedIndex].textContent : "");
        var txt = byId(p.txt);
        if (v === "gps") label = "Your GPS location";
        else if (txt && !txt.classList.contains("hidden") && txt.value.trim()) label = txt.value.trim();
        if (loc && remember({ lat: loc.lat, lon: loc.lon, label: label || "Saved location" })) { applyEverywhere(recall(), p); showClear(); }
      }, 400);
    }).observe(lbl, { childList: true, characterData: true, subtree: true });
    showClear();
  }

  function init() {
    var saved = recall();
    PANELS.forEach(function (p) {
      try { bind(p); if (saved && byId(p.sel)) apply(p, saved, false); } catch (e) { /* a panel that is not on this page */ }
    });
  }

  if (typeof document !== "undefined") {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
  }
  root.TFLocation = { configure: configure, recall: recall, remember: remember, forget: forget, valid: valid, openPicker: openPicker, init: init };
  if (typeof module !== "undefined" && module.exports) module.exports = root.TFLocation;
})(typeof window !== "undefined" ? window : globalThis);
