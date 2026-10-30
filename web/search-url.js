/* The Tender search state that lives in the address (re-test 3 T6): /?q=roofing&mode=awards&portals=find_tender,pcs&scope=title.
   The notice-type "mode" defaults to Opportunities (T4), so it is only written when it is something else; the same goes for
   "all portals" and the default keyword scope. A reload or a shared link returns to the same search. */
(function (root) {
  "use strict";
  const MODES = { opportunities: "opportunities", awards: "awards", awarded: "awards", all: "all" };
  const SCOPES = ["title", "supplier", "buyer"];

  // state: { q, statusFilter, keywordScope }; portals: array of selected portal ids, or null for all; validPortals: ids that exist.
  function toParams(state, portals) {
    const p = new URLSearchParams();
    if (state.q) p.set("q", state.q);
    if (state.statusFilter && state.statusFilter !== "opportunities" && state.statusFilter !== "none") p.set("mode", state.statusFilter);
    if (portals && portals.length) p.set("portals", portals.join(","));
    if (state.keywordScope && SCOPES.includes(state.keywordScope)) p.set("scope", state.keywordScope);
    return p;
  }

  function fromSearch(search, validPortals) {
    const p = new URLSearchParams(search || "");
    const out = { mode: "opportunities", portals: null, scope: null };
    const mode = MODES[(p.get("mode") || "").toLowerCase()];
    if (mode) out.mode = mode;
    const ids = (p.get("portals") || "").split(",").map((s) => s.trim()).filter(Boolean);
    const known = validPortals ? ids.filter((id) => validPortals.includes(id)) : ids;
    if (known.length) out.portals = known;
    const scope = (p.get("scope") || "").toLowerCase();
    if (SCOPES.includes(scope)) out.scope = scope;
    return out;
  }

  const api = { toParams, fromSearch };
  root.SearchUrl = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
