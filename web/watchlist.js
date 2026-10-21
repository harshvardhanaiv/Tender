/* Global watchlist of buyers and suppliers (API: /api/watchlist, see tender_app/entity_watchlist.py).

   Any section puts a button in with TFWatch.buttonHtml(kind, key, name, from); one delegated click handler adds or removes
   the entry, and every button on the page is kept in step with the list (a MutationObserver re-syncs after each render).
   The app.js fetch wrapper adds the CSRF header. */
(function (root) {
  "use strict";
  var SOURCES = {
    supplier_intelligence: "Supplier Intelligence", buyer_intelligence: "Buyer Intelligence", growth_studio: "Growth Studio",
    open_opportunity: "Open opportunity", tender_search: "Tender search", market_radar: "Market Radar", map: "Map"
  };
  var items = [];
  var loaded = false;
  var listeners = [];

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  // Buyers are keyed by upper-cased name everywhere (the same key Growth Studio uses); suppliers by id.
  function keyFor(kind, key) { return kind === "buyer" ? String(key || "").trim().toUpperCase() : String(key == null ? "" : key).trim(); }
  function has(kind, key) { var k = keyFor(kind, key); return items.some(function (i) { return i.kind === kind && i.entity_key === k; }); }
  function sourceLabel(src) { return SOURCES[src] || "Other"; }

  function buttonHtml(kind, key, name, from, opts) {
    var on = has(kind, key);
    var label = (opts && opts.label) || "";
    return '<button type="button" class="tf-watch' + (on ? " is-on" : "") + '" data-watch="1" data-kind="' + esc(kind) + '" data-key="' + esc(keyFor(kind, key)) +
      '" data-name="' + esc(name) + '" data-from="' + esc(from) + '" aria-pressed="' + (on ? "true" : "false") + '" title="' + (on ? "On your watchlist. Click to remove" : "Add to your watchlist") + '">' +
      '<span class="tf-watch__star" aria-hidden="true">' + (on ? "★" : "☆") + "</span>" + (label ? '<span class="tf-watch__label">' + esc(on ? "Watching" : "Watch") + "</span>" : "") + "</button>";
  }

  function sync(scope) {
    var nodes = (scope || document).querySelectorAll("button[data-watch]");
    for (var i = 0; i < nodes.length; i++) {
      var b = nodes[i], on = has(b.dataset.kind, b.dataset.key);
      if (b.classList.contains("is-on") === on && b.getAttribute("aria-pressed") === String(on)) continue;
      b.classList.toggle("is-on", on);
      b.setAttribute("aria-pressed", String(on));
      b.title = on ? "On your watchlist. Click to remove" : "Add to your watchlist";
      var star = b.querySelector(".tf-watch__star"); if (star) star.textContent = on ? "★" : "☆";
      var lab = b.querySelector(".tf-watch__label"); if (lab) lab.textContent = on ? "Watching" : "Watch";
    }
  }
  function changed() { sync(); listeners.forEach(function (fn) { try { fn(items); } catch (e) { /* a listener must not break the others */ } }); }

  function load() {
    return fetch("/api/watchlist").then(function (r) { return r.ok ? r.json() : { items: [] }; }).then(function (d) {
      items = d.items || []; loaded = true; changed(); return items;
    }).catch(function () { return items; });
  }
  function add(kind, key, name, from) {
    return fetch("/api/watchlist", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: kind, entity_key: keyFor(kind, key), name: name, added_from: from }) })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (x) {
        if (!x.ok) { if (root.showToast) root.showToast(x.d.error || "Could not add to the watchlist", "error"); return false; }
        if (!has(kind, key)) items.unshift(x.d.item);
        changed();
        if (root.showToast) root.showToast("Added " + name + " to your watchlist (" + sourceLabel(from) + ")", "success");
        return true;
      });
  }
  function remove(kind, key) {
    var k = keyFor(kind, key);
    return fetch("/api/watchlist/" + encodeURIComponent(kind) + "/" + encodeURIComponent(k), { method: "DELETE" }).then(function (r) {
      if (!r.ok) return false;
      items = items.filter(function (i) { return !(i.kind === kind && i.entity_key === k); });
      changed();
      return true;
    });
  }
  function toggle(kind, key, name, from) { return has(kind, key) ? remove(kind, key) : add(kind, key, name, from); }

  if (typeof document !== "undefined") {
    document.addEventListener("click", function (e) {
      var b = e.target.closest && e.target.closest("button[data-watch]");
      if (!b) return;
      e.preventDefault(); e.stopPropagation();
      b.disabled = true;
      toggle(b.dataset.kind, b.dataset.key, b.dataset.name, b.dataset.from).then(function () { b.disabled = false; }, function () { b.disabled = false; });
    }, true);
    var timer = null;
    new MutationObserver(function () { clearTimeout(timer); timer = setTimeout(function () { if (loaded) sync(); }, 60); })
      .observe(document.documentElement, { childList: true, subtree: true });
    document.addEventListener("DOMContentLoaded", load);
  }

  root.TFWatch = { buttonHtml: buttonHtml, has: has, load: load, add: add, remove: remove, toggle: toggle, sync: sync, sourceLabel: sourceLabel, keyFor: keyFor,
    items: function () { return items.slice(); }, onChange: function (fn) { listeners.push(fn); }, _setItems: function (x) { items = x; loaded = true; } };
  if (typeof module !== "undefined" && module.exports) module.exports = root.TFWatch;
})(typeof window !== "undefined" ? window : globalThis);
