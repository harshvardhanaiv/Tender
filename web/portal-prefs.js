/* TenderFlow Tender Search: the two rules that decide which portals a user is searching.

   Round 27 found a saved portal choice that came back different in the next session: the county filter
   re-chose the portals for the user every time the page restored the saved counties, and a portal added
   to the catalogue (GCA Frameworks) appeared in the selection by itself. Both rules live here as pure
   functions (no DOM, no storage) so they can be tested in Node (tests/portal_prefs_test.js).

   Selections are an array of portal ids, or null meaning "all portals". */
(function (root) {
  "use strict";

  /* A portal added to the catalogue after a user last chose theirs starts switched OFF for that user;
     only an account with no saved preferences yet gets everything.

       allIds         every portal in the catalogue now
       newIds         portals added recently (the ones an existing user cannot have met yet)
       known          the portal ids this user has already been shown (their saved `known_portals`), or null
       selected       the user's saved selection (array of ids) or null for "all"
       hasSavedPrefs  whether the account has saved anything before

     Returns { known, selected, changed }: the known list to save (always the whole catalogue from now on),
     the selection to use, and whether either differs from what was saved. */
  function reconcileCatalogue({ allIds, newIds = [], known = null, selected = null, hasSavedPrefs = false }) {
    const fresh = new Set(newIds);
    const knownBefore = new Set(known || (hasSavedPrefs ? allIds.filter((id) => !fresh.has(id)) : allIds));
    const unseen = allIds.filter((id) => !knownBefore.has(id));
    let nextSelected = selected;
    if (unseen.length && selected === null) {
      // "all" would silently grow to include them: pin the selection to what the user has seen
      nextSelected = allIds.filter((id) => knownBefore.has(id));
    }
    const nextKnown = allIds.slice();
    const sameKnown = Array.isArray(known) && known.length === nextKnown.length && nextKnown.every((id) => known.includes(id));
    return { known: nextKnown, selected: nextSelected, changed: !sameKnown || nextSelected !== selected };
  }

  /* The portals that go with a county selection (picking only Scottish counties drops the English,
     Welsh and Irish portals). `counties` is [{ name, region }], `regionPortalIds` maps a region to its
     portal ids. An empty selection, or one that still touches every region, narrows nothing: portalIds is
     null. A portal in `optInIds` (added recently) is never switched on by this: it stays in only if the
     user already has it selected. */
  function regionPortals({ selectedCounties, counties, regionPortalIds, currentSelected = null, optInIds = [] }) {
    const selected = Array.isArray(selectedCounties) ? selectedCounties : [];
    if (!selected.length) return { portalIds: null, regions: new Set() };
    const regionByName = new Map(counties.map((c) => [c.name, c.region]));
    const present = new Set(selected.map((name) => regionByName.get(name)).filter(Boolean));
    const available = new Set(counties.map((c) => c.region).filter(Boolean));
    if (!present.size || present.size >= available.size) return { portalIds: null, regions: new Set() };
    const optIn = new Set(optInIds);
    const current = currentSelected === null ? null : new Set(currentSelected);
    const ids = new Set();
    for (const region of present) {
      (regionPortalIds[region] || []).forEach((id) => {
        // current === null means "all portals", which already includes it
        if (!optIn.has(id) || current === null || current.has(id)) ids.add(id);
      });
    }
    return ids.size ? { portalIds: ids, regions: present } : { portalIds: null, regions: new Set() };
  }

  /* A stable text for a county-derived portal set, to tell whether the county choice itself changed. */
  function regionKey(portalIds) {
    return portalIds ? [...portalIds].sort().join(",") : null;
  }

  const api = { reconcileCatalogue, regionPortals, regionKey };
  root.PortalPrefs = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
