/* TenderFlow Tender Search: results kept in the browser so a search you have run before shows at once.

   Entries are keyed by search terms, portal scope and county selection (the server filters by county, so rows
   cached for one county choice are not valid for another). A comma-separated search ("software, dashboard") is
   stored whole AND per term, so "software, cyber" can be assembled from earlier "software" and "cyber" searches.
   The cache lives in memory (read synchronously by the search) and is mirrored to IndexedDB so it survives a
   reload. The pure helpers have no DOM or storage and run in Node (tests/search_cache_test.js). */
(function (root) {
  "use strict";

  const MAX_TERMS = 5;
  const MAX_ENTRIES = 20;
  const MAX_AGE_MS = 24 * 60 * 60 * 1000;
  const DB_NAME = "tf_search_cache";
  const STORE = "entries";
  const PREFIX = /^(?:supplier|winner|contractor|buyer|authority|title):\s*/i;

  /* Same rule as tender_app/search_terms.py: commas separate terms; words with no comma stay together. */
  function splitTerms(query, limit = MAX_TERMS) {
    const text = String(query == null ? "" : query).trim().replace(PREFIX, "");
    const out = [];
    for (const part of text.split(/[,;]+/)) {
      const term = part.trim().replace(/\s+/g, " ").toLowerCase();
      if (term && term !== "all" && !out.includes(term)) out.push(term);
      if (out.length >= limit) break;
    }
    return out;
  }

  function entryKey(scope, countiesKey, terms) {
    return `${scope || "all"}|${countiesKey || ""}|${[...terms].sort().join(",")}`;
  }

  function rowIdentity(row) {
    if (!row) return "";
    return String(row.tender_key || row.resource_id || row.detail_url || row.url || "")
      || `${row.source || ""}|${String(row.title || "").toLowerCase()}|${String(row.contracting_authority || "").toLowerCase()}`;
  }

  /* Union of several row lists, each row once; a row found by more of the lists comes first. */
  function mergeRows(lists) {
    const byId = new Map();
    const hits = new Map();
    lists.forEach((rows) => {
      (rows || []).forEach((row) => {
        const id = rowIdentity(row);
        if (!id) return;
        if (!byId.has(id)) byId.set(id, row);
        hits.set(id, (hits.get(id) || 0) + 1);
      });
    });
    return [...byId.keys()]
      .map((id, i) => ({ id, i }))
      .sort((a, b) => (hits.get(b.id) - hits.get(a.id)) || (a.i - b.i))
      .map((x) => byId.get(x.id));
  }

  const memory = new Map(); // key -> { key, at, rows, total, terms }
  let db = null;

  function lookupIn(map, query, scope, countiesKey, now, maxAgeMs) {
    const terms = splitTerms(query);
    if (!terms.length) return null;
    const fresh = (e) => e && now - e.at <= maxAgeMs && Array.isArray(e.rows) && e.rows.length;
    const exact = map.get(entryKey(scope, countiesKey, terms));
    if (fresh(exact)) return { rows: exact.rows, at: exact.at, total: exact.total, assembled: false };
    if (terms.length > 1) {
      const parts = terms.map((t) => map.get(entryKey(scope, countiesKey, [t])));
      if (parts.every(fresh)) {
        const rows = mergeRows(parts.map((p) => p.rows));
        return { rows, at: Math.min(...parts.map((p) => p.at)), total: rows.length, assembled: true };
      }
    }
    return null;
  }

  function lookup(query, scope, countiesKey, opts) {
    const o = opts || {};
    return lookupIn(memory, query, scope, countiesKey, o.now == null ? Date.now() : o.now, o.maxAgeMs == null ? MAX_AGE_MS : o.maxAgeMs);
  }

  function prune(map) {
    if (map.size <= MAX_ENTRIES) return [];
    const old = [...map.values()].sort((a, b) => a.at - b.at).slice(0, map.size - MAX_ENTRIES);
    old.forEach((e) => map.delete(e.key));
    return old.map((e) => e.key);
  }

  /* Remember a finished search (and, for a comma search, each term's own rows). Returns the entries written. */
  function storeIn(map, query, scope, countiesKey, rows, total, now) {
    const terms = splitTerms(query);
    if (!terms.length || !Array.isArray(rows) || !rows.length) return [];
    const written = [];
    const put = (ts, rs, tot) => {
      const key = entryKey(scope, countiesKey, ts);
      const entry = { key, at: now, rows: rs, total: tot == null ? rs.length : tot, terms: ts };
      map.set(key, entry);
      written.push(entry);
    };
    put(terms, rows, total);
    if (terms.length > 1) {
      terms.forEach((t) => {
        const own = rows.filter((r) => Array.isArray(r.matched_terms) && r.matched_terms.includes(t));
        // a word's own finished search is the better copy: only fill a gap or replace a smaller one
        const prev = map.get(entryKey(scope, countiesKey, [t]));
        if (own.length && !(prev && Array.isArray(prev.rows) && prev.rows.length >= own.length)) put([t], own, own.length);
      });
    }
    return written;
  }

  function store(query, scope, countiesKey, rows, total) {
    const written = storeIn(memory, query, scope, countiesKey, rows, total, Date.now());
    const dropped = prune(memory);
    persist(written, dropped);
    return written.length;
  }

  // ── IndexedDB mirror (best effort: the page works without it) ──
  function openDb() {
    return new Promise((resolve) => {
      try {
        if (!root.indexedDB) return resolve(null);
        const req = root.indexedDB.open(DB_NAME, 1);
        req.onupgradeneeded = () => req.result.createObjectStore(STORE, { keyPath: "key" });
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => resolve(null);
      } catch (e) { resolve(null); }
    });
  }

  function persist(written, dropped) {
    if (!db) return;
    try {
      const tx = db.transaction(STORE, "readwrite");
      const os = tx.objectStore(STORE);
      (dropped || []).forEach((k) => os.delete(k));
      (written || []).forEach((e) => os.put(e));
    } catch (e) { /* ignore */ }
  }

  async function init() {
    db = await openDb();
    if (!db) return;
    try {
      await new Promise((resolve) => {
        const req = db.transaction(STORE, "readonly").objectStore(STORE).getAll();
        req.onsuccess = () => {
          (req.result || []).forEach((e) => { if (e && e.key && !memory.has(e.key)) memory.set(e.key, e); });
          resolve();
        };
        req.onerror = () => resolve();
      });
    } catch (e) { /* ignore */ }
  }

  function clear() {
    memory.clear();
    try { if (db) db.transaction(STORE, "readwrite").objectStore(STORE).clear(); } catch (e) { /* ignore */ }
  }

  /* The saved/recent chips ticked together become one comma search. The scope is kept only when every chip
     shares it; otherwise "all" (a title search and a supplier search cannot be one query). */
  function combineChips(chips) {
    const terms = [];
    const scopes = new Set();
    (chips || []).forEach((c) => {
      scopes.add(c.scope || "all");
      splitTerms(c.q).forEach((t) => { if (!terms.includes(t) && terms.length < MAX_TERMS) terms.push(t); });
    });
    return { q: terms.join(", "), scope: scopes.size === 1 ? [...scopes][0] : "all", terms, mixedScopes: scopes.size > 1 };
  }

  /* For the term chips above the results: how many of `rows` each term accounts for. */
  function termCounts(rows, terms, matches) {
    return terms.map((term) => ({ term, count: (rows || []).filter((r) => matches(r, term)).length }));
  }

  const api = {
    splitTerms, entryKey, rowIdentity, mergeRows, lookup, store, init, clear, combineChips, termCounts,
    _lookupIn: lookupIn, _storeIn: storeIn, MAX_AGE_MS, MAX_ENTRIES,
  };
  root.SearchCache = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof document !== "undefined") init();
})(typeof window !== "undefined" ? window : globalThis);
