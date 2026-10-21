/* "Recently viewed": the tenders, buyers and suppliers the user opened, newest first, so they can find their way back.
   Stored in this browser only (localStorage, one list per signed-in user), never sent to the server, and cleared by the
   Clear button or on logout. A repeat view moves the entry to the top and counts it. */
(function (root) {
  "use strict";
  const KINDS = ["tender", "buyer", "supplier"];
  const LIMITS = { tender: 40, buyer: 40, supplier: 40 };
  let keyFn = () => "tf_recent_views";
  let storage = typeof localStorage !== "undefined" ? localStorage : null;

  function configure(opts) {
    if (opts && opts.storageKey) keyFn = opts.storageKey;
    if (opts && opts.storage) storage = opts.storage;
  }
  function read() {
    try { const v = JSON.parse(storage.getItem(keyFn()) || "[]"); return Array.isArray(v) ? v.filter((e) => e && KINDS.includes(e.kind) && e.key && e.title) : []; } catch (e) { return []; }
  }
  function write(list) {
    try { storage.setItem(keyFn(), JSON.stringify(list)); return true; } catch (e) { return false; }
  }

  // payload: a small object the page needs to reopen the item (a tender row without its long text, say)
  function record(kind, key, title, sub, payload, now) {
    if (!KINDS.includes(kind) || !key || !String(title || "").trim()) return false;
    const list = read();
    const same = list.find((e) => e.kind === kind && e.key === String(key));
    const entry = { kind, key: String(key), title: String(title).trim().slice(0, 200), sub: String(sub || "").trim().slice(0, 160),
      ts: now || Date.now(), views: (same ? same.views : 0) + 1, payload: payload || (same && same.payload) || null };
    const rest = list.filter((e) => e !== same);
    const ofKind = rest.filter((e) => e.kind === kind).slice(0, LIMITS[kind] - 1);   // the newest of this kind, room for the new one
    const keep = new Set(ofKind);
    const merged = [entry, ...rest.filter((e) => e.kind !== kind || keep.has(e))].sort((a, b) => b.ts - a.ts);
    return write(merged);
  }
  function list(kind) { const all = read().sort((a, b) => b.ts - a.ts); return kind ? all.filter((e) => e.kind === kind) : all; }
  function clear() { try { storage.removeItem(keyFn()); } catch (e) { /* storage blocked */ } }
  function forget(kind, key) { write(read().filter((e) => !(e.kind === kind && e.key === String(key)))); }

  // A trimmed copy of a tender row: enough to reopen it, without the long description.
  function slimRow(row) {
    const out = {};
    ["source", "resource_id", "ocid", "id", "title", "contracting_authority", "notice_url", "url", "submission_deadline", "published", "estimated_value", "estimated_value_eur", "status", "tender_key"].forEach((k) => {
      if (row && row[k] != null && row[k] !== "") out[k] = typeof row[k] === "string" ? row[k].slice(0, 400) : row[k];
    });
    if (row && row.description) out.description = String(row.description).slice(0, 600);
    return out;
  }

  // "2 minutes ago", "yesterday", "3 Oct"
  function ago(ts, now) {
    const s = Math.max(0, Math.round(((now || Date.now()) - ts) / 1000));
    if (s < 60) return "just now";
    if (s < 3600) return `${Math.floor(s / 60)} min ago`;
    if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
    if (s < 172800) return "yesterday";
    return new Date(ts).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
  }

  const api = { configure, record, list, clear, forget, slimRow, ago, KINDS };
  root.RecentViews = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
