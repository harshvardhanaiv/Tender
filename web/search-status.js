/* TenderFlow Tender Search: what each portal did in the current search, and the words for it.

   The server describes every portal of a search in `meta.portal_results` (status ok / empty / error /
   timeout / stopped / running / pending, with its row count, reason and timing). Everything the page
   says about progress and outcome is worked out here from that one list, so the progress line, the
   per-portal summary, the results header and the empty state can never disagree with each other (Round
   27: the progress line named a portal that had already delivered, the search finished without a word
   about the portal that returned nothing, and the header counted portals that were selected, not
   portals that answered).

   Pure functions only, no DOM and no page state, so they run in Node (tests/search_status_test.js). */
(function (root) {
  "use strict";

  const WAITING = new Set(["pending", "running"]);
  const FAILED = new Set(["error", "timeout"]);

  function fmtInt(n) { return Number(n || 0).toLocaleString("en-GB"); }
  function plural(n, one, many) { return `${fmtInt(n)} ${n === 1 ? one : (many || one + "s")}`; }
  function defaultEsc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  /* The portals of the search, in the order the server lists them, each with a status. `meta` is the
     response's meta; `labelOf(id)` names a portal the server gave no label for. When the server sent no
     portal_results (an older response, a saved search) the same statuses are worked out from the
     per-portal progress, errors and counts it did send. */
  function portalList(meta, labelOf) {
    const label = labelOf || ((id) => id);
    meta = meta || {};
    const results = meta.portal_results;
    if (results && typeof results === "object" && Object.keys(results).length) {
      return Object.keys(results).map((id) => {
        const r = results[id] || {};
        return {
          id,
          label: r.label || label(id),
          status: r.status || "pending",
          count: r.count || 0,
          shown: r.shown == null ? null : r.shown,
          stored: r.stored || 0,
          hiddenByCounty: r.hidden_by_county || 0,
          droppedInvalid: r.dropped_invalid || 0,
          droppedDuplicate: r.dropped_duplicate || 0,
          durationMs: r.durationMs == null ? null : r.durationMs,
          message: r.message || null,
          partial: Boolean(r.partial),
          available: r.available == null ? null : r.available,
          late: Boolean(r.late),
        };
      });
    }
    const progress = meta.source_progress || {};
    const errors = meta.errors || {};
    const counts = meta.source_counts || {};
    return Object.keys(progress).map((id) => {
      const state = progress[id];
      const count = counts[id] || 0;
      const message = errors[id] ? String(errors[id]) : null;
      let status;
      if (state === "pending") status = "pending";
      else if (state === "loading" || state === "first_page" || state === "fetching_all") status = "running";
      else if (state === "timeout") status = "timeout";
      else if (state === "stopped") status = "stopped";
      else if (state === "error") status = count ? "ok" : "error";
      else status = count ? "ok" : (message ? "error" : "empty");
      return {
        id, label: label(id), status, count, shown: count, stored: 0, hiddenByCounty: 0, durationMs: null,
        message, partial: Boolean(count && message), available: null, late: false,
      };
    });
  }

  /* "N of M finished" and the portals still being waited for. */
  function progress(list) {
    const waiting = list.filter((p) => WAITING.has(p.status));
    return { total: list.length, settled: list.length - waiting.length, waiting };
  }

  /* " — waiting on Find a Tender" / " — waiting on A and B" / " — waiting on 3 portals" (or ""). */
  function waitingNote(list, esc) {
    const e = esc || defaultEsc;
    const waiting = progress(list).waiting;
    if (!waiting.length) return "";
    if (waiting.length <= 2) return ` — waiting on ${waiting.map((p) => e(p.label)).join(" and ")}`;
    return ` — waiting on ${waiting.length} portals`;
  }

  /* The portals that did not give an answer (failed or timed out) once the search is over. */
  function failedPortals(list) {
    return list.filter((p) => FAILED.has(p.status));
  }

  /* The results header's second line. Once the search is over it counts the portals that answered, not
     the portals that were selected: "Across 2 of 3 portals in 1 country". */
  function headerSub(list, selectedCount, countriesCount, searching) {
    const total = list.length || selectedCount;
    let n = total;
    let text;
    if (list.length && !searching) {
      n = list.filter((p) => p.status === "ok" || p.status === "empty").length;
      text = n < total ? `Across ${n} of ${plural(total, "portal")}` : `Across ${plural(total, "portal")}`;
    } else {
      text = `Across ${plural(total, "portal")}`;
    }
    return `${text} in ${countriesCount} countr${countriesCount === 1 ? "y" : "ies"}`;
  }

  /* The results header's first line. `shown` is what is left after the page's own filters. The number
     loaded before those filters is not shown (it read as a second, unexplained total).
     There is no "sample" line: a portal holding more notices than were fetched is in that portal's chip tooltip. */
  function resultsHeadline(shown) {
    return { main: plural(shown, "tender") };
  }

  /* How the numbers on the page add up, from the server's own count identity (`meta.reconciliation.total`):
     fetched - unreadable - duplicates + saved earlier - hidden by the county filter = sent to the page, and the
     page's other filters (date, value, ...) then leave `pageShown`. "" when the server sent no identity.
     "Saved earlier" is a row on screen that this search did not return again, so it can fall when a retry
     returns more live rows (a row the portal now returns counts as fetched, not as saved). */
  function reconciliationText(rec, pageShown) {
    const t = rec && rec.total;
    if (!t || t.fetched == null) return "";
    const parts = [`${fmtInt(t.fetched)} fetched`];
    if (t.dropped_invalid > 0) parts.push(`− ${fmtInt(t.dropped_invalid)} unreadable`);
    if (t.dropped_duplicate > 0) parts.push(`− ${fmtInt(t.dropped_duplicate)} duplicate${t.dropped_duplicate === 1 ? "" : "s"}`);
    if (t.saved_added > 0) parts.push(`+ ${fmtInt(t.saved_added)} saved earlier`);
    if (t.hidden_by_county > 0) parts.push(`− ${fmtInt(t.hidden_by_county)} hidden by the county filter`);
    let text = `${parts.join(" ")} = ${fmtInt(t.shown)} sent to the page`;
    if (pageShown != null && pageShown < t.shown) text += `; your other filters leave ${fmtInt(pageShown)}`;
    if (t.balanced === false) text += " (these counts do not add up: please report this search)";
    return text;
  }

  /* A notice published by a contractor looking for sub-contractors ("Balfour Beatty Civil Engineering Limited") is a
     private supply-chain opportunity, not a public buyer: it has no award history to show under "Buyer intel". Only a
     company-style name that also reads as a contractor counts, and anything that reads as a public body or a utility
     never does, so a real buyer is not hidden behind the label. Returns the label, or "". */
  const COMPANY_NAME = /\b(ltd|limited|plc|llp|l\.l\.p)\.?$/i;
  const CONTRACTOR_WORDS = /\b(construction|civil engineering|contractors?|building (services|contractors)|developments?|homes|interiors|groundworks)\b|balfour beatty|kier|morgan sindall|laing o.?rourke|bam (construct|nuttall)|skanska|willmott dixon|galliford|costain|vinci|mace\b/i;
  const PUBLIC_WORDS = /\b(council|nhs|trust|university|college|school|academy|police|fire|authority|borough|department|ministry|government|agency|commission|board|hospital|housing association|network rail|national highways|environment agency|national grid|water|energy|electricity|gas|transport for)\b/i;
  function supplyChainLabel(authority) {
    const name = String(authority || "").trim();
    if (!name || !COMPANY_NAME.test(name) || PUBLIC_WORDS.test(name) || !CONTRACTOR_WORDS.test(name)) return "";
    return "Private / supply-chain opportunity";
  }

  /* A short reason for the chip; the full message is shown underneath. */
  function shortReason(message) {
    const m = String(message || "");
    if (/429|rate.?limit/i.test(m)) return "rate-limited";
    if (/captcha/i.test(m)) return "captcha";
    if (/did not respond|timed? ?out|did not answer/i.test(m)) return "no answer";
    if (/something went wrong|error page|could not read/i.test(m)) return "error page";
    return "failed";
  }

  function seconds(ms) { return ms == null ? "" : `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)} s`; }

  function chipText(p) {
    const bits = [];
    switch (p.status) {
      case "ok":
        bits.push(p.available && p.available > p.count ? `${fmtInt(p.count)} of ${fmtInt(p.available)}` : fmtInt(p.count));
        if (p.partial) bits.push("incomplete");
        break;
      case "empty": bits.push("0 · no results"); break;
      case "timeout": bits.push(p.count ? `${fmtInt(p.count)} shown · timed out` : "timed out"); break;
      case "error": bits.push(shortReason(p.message)); break;
      case "stopped": bits.push("stopped"); break;
      case "running": bits.push("searching…"); break;
      default: bits.push("waiting");
    }
    if (p.stored > 0) bits.push(`+${fmtInt(p.stored)} saved earlier`);
    return bits.join(" · ");
  }

  function chipTitle(p) {
    const lines = [p.label];
    if (p.message) lines.push(p.message);
    if (p.durationMs != null) lines.push(`${seconds(p.durationMs)}${p.status === "running" ? " so far" : ""}`);
    if (p.available) lines.push(`${fmtInt(p.available)} notices match on the portal`);
    if (p.late) lines.push("answered after the time limit");
    if (p.stored > 0) lines.push(`${fmtInt(p.stored)} of the rows shown were saved by earlier searches`);
    if (p.hiddenByCounty > 0) lines.push(`${fmtInt(p.hiddenByCounty)} hidden by the county filter`);
    if (p.droppedDuplicate > 0) lines.push(`${fmtInt(p.droppedDuplicate)} duplicate${p.droppedDuplicate === 1 ? "" : "s"} removed`);
    if (p.droppedInvalid > 0) lines.push(`${fmtInt(p.droppedInvalid)} unreadable row${p.droppedInvalid === 1 ? "" : "s"} dropped`);
    return lines.join("\n");
  }

  const ICON = { ok: "✓", empty: "○", error: "⚠", timeout: "⏱", stopped: "⏹", running: "◐", pending: "○" };

  /* "<b>Name</b> did not respond in time" when the portal's own message already starts with its name
     (the server words them that way), otherwise "<b>Name</b>: message". */
  function reasonLine(p, e) {
    const message = String(p.message);
    const name = String(p.label).replace(/\s*\([^)]*\)\s*$/, "");
    if (name && message.toLowerCase().startsWith(name.toLowerCase())) {
      return `<b>${e(message.slice(0, name.length))}</b>${e(message.slice(name.length))}`;
    }
    return `<b>${e(p.label)}</b>: ${e(message)}`;
  }

  /* A portal that needs the reader's attention: it failed, timed out, was stopped, or answered only in part. */
  function needsAttention(p) {
    return FAILED.has(p.status) || p.status === "stopped" || (p.status === "ok" && p.partial);
  }

  /* One chip per portal that needs attention, then the reason for each portal that failed. Portals that answered
     normally get no chip: a row of green ticks read as filters that had been applied. Returns "" when there is
     nothing to show. Failed and timed-out portals get a Retry button; stopped ones a Resume button. */
  function summaryHtml(list, esc) {
    const e = esc || defaultEsc;
    const shown = list.filter(needsAttention);
    if (!shown.length) return "";
    const chips = shown.map((p) => {
      const action = FAILED.has(p.status)
        ? `<button type="button" class="portal-summary__action" data-portal-action="retry" data-portal="${e(p.id)}">Retry</button>`
        : p.status === "stopped"
          ? `<button type="button" class="portal-summary__action" data-portal-action="resume" data-portal="${e(p.id)}">Resume</button>`
          : "";
      return `<span class="portal-summary__chip portal-summary__chip--${e(p.status)}" data-portal="${e(p.id)}" title="${e(chipTitle(p))}">`
        + `<span class="portal-summary__icon" aria-hidden="true">${ICON[p.status] || "○"}</span>`
        + `<span class="portal-summary__name">${e(p.label)}</span>`
        + `<span class="portal-summary__text">${e(chipText(p))}</span>${action}</span>`;
    }).join("");
    const reasons = shown
      .filter((p) => (FAILED.has(p.status) || (p.status === "ok" && p.partial)) && p.message)
      .map((p) => `<div class="portal-summary__reason">${reasonLine(p, e)}</div>`)
      .join("");
    return `<div class="portal-summary__chips">${chips}</div>${reasons}`;
  }

  /* The line under the chips about rows the county filter removed, so a portal that answered but lost
     every row to the filter is not mistaken for one that returned nothing. */
  function countyNoteHtml(countyFilter, selectedCounties, labelOf, esc) {
    const e = esc || defaultEsc;
    const label = labelOf || ((id) => id);
    if (!countyFilter || !(countyFilter.hidden_total > 0) || !(selectedCounties > 0)) return "";
    const breakdown = Object.entries(countyFilter.hidden_by_source || {})
      .sort((a, b) => b[1] - a[1])
      .map(([id, n]) => `${e(label(id))} ${fmtInt(n)}`)
      .join(" · ");
    const n = countyFilter.hidden_total;
    return `<strong>${fmtInt(n)} result${n === 1 ? "" : "s"} hidden by your county filter</strong> (${breakdown}). `
      + `A notice is kept only when it names a place in your ${countyFilter.counties} selected ${countyFilter.counties === 1 ? "county" : "counties"}; `
      + "widen or clear the County filter to see the rest.";
  }

  /* What to say when the list is empty. `ctx`: { searching, hasQuery, query, rowsFromServer (rows before the
     page's own filters), hiddenByCounty (rows the county filter removed) }. Returns { icon, title, msg } (msg may
     contain the escaped query) or null to leave the page's own wording. */
  function emptyState(list, ctx, esc) {
    const e = esc || defaultEsc;
    if (ctx.searching || !ctx.hasQuery || !list.length) return null;
    if (ctx.rowsFromServer > 0) return null; // rows exist but the page's filters hide them: its own wording fits
    const failed = failedPortals(list);
    const names = (items) => items.map((p) => e(p.label)).join(", ");
    const query = e(ctx.query);
    const hidden = ctx.hiddenByCounty > 0
      ? ` ${plural(ctx.hiddenByCounty, "result")} ${ctx.hiddenByCounty === 1 ? "was" : "were"} hidden by your county filter.` : "";
    if (failed.length) {
      const all = failed.length === list.length;
      return {
        icon: "⚠️",
        title: all ? (list.length === 1 ? `${failed[0].label} did not answer` : "No portal answered") : `${plural(failed.length, "portal")} did not answer`,
        msg: `${names(failed)} did not answer for “${query}”, so “no results” would be wrong. Use Retry above.${hidden}`,
      };
    }
    const answered = list.filter((p) => p.status === "ok" || p.status === "empty");
    if (!answered.length) return null;
    return {
      icon: "📭",
      title: "No tenders found",
      msg: list.length === 1
        ? `${e(list[0].label)} returned 0 results for “${query}”.${hidden} Try a broader keyword.`
        : `${names(answered)} returned 0 results for “${query}”.${hidden} Try a broader keyword or a different portal.`,
    };
  }

  const api = { portalList, progress, waitingNote, failedPortals, headerSub, resultsHeadline, reconciliationText, supplyChainLabel, shortReason, chipText, summaryHtml, countyNoteHtml, emptyState, fmtInt, plural };
  root.SearchStatus = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
