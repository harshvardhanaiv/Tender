/* "Connect Outreach" / "Manage in Outreach" open Outreach's own web app in a small centred window, not a new tab, so the
   user signs in or connects a mailbox without leaving TenderFlow. When that window closes the panel that opened it checks
   Outreach again by itself. Any link marked data-outreach-link gets this; if the browser blocks the window the link just
   opens as a normal new tab. Loaded before the panels; works for Growth Studio and the Email (Outreach) settings panel. */
(function (root) {
  "use strict";

  const WIDTH = 520;
  const HEIGHT = 720;
  const NAME = "tenderflow_outreach";
  const POLL_MS = 700;
  const GIVE_UP_MS = 30 * 60 * 1000;

  // Size and position of the window: centred over the page, and never bigger than the screen.
  function popupFeatures(win) {
    const w = win || root;
    const scr = w.screen || {};
    const width = Math.min(WIDTH, (scr.availWidth || WIDTH) - 40);
    const height = Math.min(HEIGHT, (scr.availHeight || HEIGHT) - 80);
    const outerW = w.outerWidth || scr.availWidth || width;
    const outerH = w.outerHeight || scr.availHeight || height;
    const left = Math.max(0, Math.round((w.screenX || 0) + (outerW - width) / 2));
    const top = Math.max(0, Math.round((w.screenY || 0) + (outerH - height) / 2));
    return `popup=yes,width=${width},height=${height},left=${left},top=${top},resizable=yes,scrollbars=yes`;
  }

  // Only a plain https link is ever opened this way.
  function isHttps(url) {
    return typeof url === "string" && /^https:\/\/[^\s"'<>]+$/i.test(url);
  }

  // What re-checks Outreach after the window closes: the Growth Studio panels' "check again" button if one is on screen,
  // else an event the Email (Outreach) settings panel listens for.
  function recheck(doc) {
    const btn = doc.querySelector('[data-action="outreach-check"]');
    if (btn && !btn.disabled) {
      btn.click();
      return "button";
    }
    const EventCtor = root.CustomEvent || null;
    if (EventCtor) doc.dispatchEvent(new EventCtor("outreach:window-closed"));
    return "event";
  }

  // Opens the window for a clicked link. Returns true when it did (the click must then not also open a tab).
  function open(href, win, doc) {
    const w = win || root;
    const d = doc || root.document;
    if (!isHttps(href)) return false;
    const popup = w.open(href, NAME, popupFeatures(w));
    if (!popup) return false; // blocked: the link opens as a normal tab
    try { popup.focus(); } catch (e) { /* a focus refusal is harmless */ }
    const started = Date.now();
    const timer = w.setInterval(() => {
      let closed = true;
      try { closed = popup.closed; } catch (e) { closed = true; }
      if (closed || Date.now() - started > GIVE_UP_MS) {
        w.clearInterval(timer);
        if (closed) recheck(d);
      }
    }, POLL_MS);
    return true;
  }

  function onClick(event) {
    const a = event.target && event.target.closest ? event.target.closest("a[data-outreach-link]") : null;
    if (!a || event.defaultPrevented) return;
    if (event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return; // let "open in new tab" work
    if (open(a.getAttribute("href"), root, root.document)) event.preventDefault();
  }

  const api = { popupFeatures, isHttps, recheck, open, WIDTH, HEIGHT };
  root.OutreachPopup = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof document !== "undefined") document.addEventListener("click", onClick);
})(typeof window !== "undefined" ? window : globalThis);
