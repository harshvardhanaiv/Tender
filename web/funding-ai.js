/* Funding AI tools for one grant: Eligibility, Summary, Application draft, Deep analysis. Data comes from /api/funding/ai/<tool>.
   Opening a tab only shows a saved result; the AI runs (and uses credits) only when the user presses the Run button. */
(function () {
  "use strict";

  const TOOLS = [
    ["eligibility", "Am I eligible?", "Compares the grant's conditions with your company profile."],
    ["summary", "Summary", "What the grant pays for, who can apply, key dates and what is most likely to rule you out."],
    ["draft", "Application draft", "A first draft from your profile and Answer Bank. Anything it cannot evidence is left as a gap for you to fill."],
    ["analysis", "Deep analysis", "Funder priorities, your strengths and weaknesses, competition, risks and a go or no-go view."],
    ["questions", "Questions for the funder", "What the call leaves unclear (match funding, eligible costs, state aid, deadlines), as questions you can send. Track which you asked and what they answered."],
    ["budget", "Budget and match", "What the grant pays for and its funding rate, then work out your grant and the match you need from your project cost."],
    ["evidence", "Evidence checklist", "The documents the funder is likely to ask for, ticked off against the files in your Documents."],
  ];
  const DEFAULT_COSTS = { eligibility: 1, summary: 2, draft: 6, analysis: 5, questions: 2, budget: 2, evidence: 2 };
  const QSTATUS = [["todo", "To ask"], ["asked", "Asked"], ["answered", "Answered"]];
  const VERDICT = {
    eligible: ["Eligible", "good"], probably: ["Probably eligible", "good"], unclear: ["Unclear", "warn"],
    unlikely: ["Unlikely", "bad"], not_eligible: ["Not eligible", "bad"],
  };
  const STATUS = { met: ["Met", "good"], unclear: ["Unclear", "warn"], not_met: ["Not met", "bad"] };
  const RECOMMENDATION = { go: ["Go: apply", "good"], maybe: ["Maybe: needs a closer look", "warn"], no_go: ["No-go: not worth applying", "bad"] };

  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const pill = (pair) => `<span class="fai-pill fai-pill--${pair[1]}">${esc(pair[0])}</span>`;
  const list = (items) => (items && items.length ? `<ul class="fai-list">${items.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>` : "");
  const block = (title, body) => (body ? `<section class="fai-block"><h4>${esc(title)}</h4>${body}</section>` : "");
  const para = (text) => (text ? `<p>${esc(text)}</p>` : "");

  function eligibilityHtml(r) {
    const rows = (r.criteria || []).map((c) => {
      const st = STATUS[c.status] || STATUS.unclear;
      return `<li class="fai-crit"><div>${pill(st)} <strong>${esc(c.criterion)}</strong></div>${c.evidence ? `<div class="fai-hint">${esc(c.evidence)}</div>` : ""}</li>`;
    }).join("");
    return (
      `<div class="fai-verdict">${pill(VERDICT[r.verdict] || VERDICT.unclear)} <span>${esc(r.headline)}</span></div>` +
      block("Each condition", rows ? `<ul class="fai-crits">${rows}</ul>` : "") +
      block("Still needed to be sure", list(r.missing)) + block("Next steps", list(r.next_steps))
    );
  }

  function summaryHtml(r) {
    return (
      block("In short", para(r.overview)) + block("What it funds", list(r.funds)) + block("Who can apply", list(r.who_can_apply)) +
      block("Award", para(r.award)) + block("Key dates", list(r.key_dates)) + block("Most likely to rule you out", list(r.deal_breakers)) +
      block("How to apply", list(r.how_to_apply))
    );
  }

  function draftHtml(r) {
    const sections = (r.sections || []).map((s) =>
      `<section class="fai-block"><h4>${esc(s.heading)}</h4><div class="fai-draft">${esc(s.text).replace(/\n/g, "<br>")}</div>` +
      (s.gaps && s.gaps.length ? `<div class="fai-gaps"><strong>You need to add:</strong>${list(s.gaps)}</div>` : "") + `</section>`).join("");
    return (
      `<p class="fai-hint">A starting point, not a finished application. Check every statement is true for your organisation before you use it.</p>` +
      sections + block("Everything still missing", list(r.gaps))
    );
  }

  function analysisHtml(r) {
    return (
      `<div class="fai-verdict">${pill(RECOMMENDATION[r.recommendation] || RECOMMENDATION.maybe)} <span>${esc(r.reason)}</span></div>` +
      block("What the funder wants", list(r.funder_priorities)) + block("Where you fit", list(r.strengths)) + block("Where you do not fit yet", list(r.weaknesses)) +
      block("Competition", para(r.competition)) + block("What an assessor will look for", list(r.assessment_hints)) +
      block("Risks", list(r.risks)) + block("Next steps", list(r.next_steps))
    );
  }

  const keyOf = (text) => String(text || "").slice(0, 80);

  function questionsHtml(r, state) {
    const kept = (state && state.q) || {};
    const rows = (r.questions || []).map((q) => {
      const k = keyOf(q.question);
      const cur = kept[k] || {};
      const opts = QSTATUS.map(([v, l]) => `<option value="${v}"${(cur.status || "todo") === v ? " selected" : ""}>${l}</option>`).join("");
      return (
        `<li class="fai-q" data-fai-q="${esc(k)}"><div class="fai-q__top"><strong>${esc(q.question)}</strong> <span class="fai-pill">${esc(q.topic)}</span></div>` +
        (q.why ? `<div class="fai-hint">${esc(q.why)}</div>` : "") +
        `<div class="fai-q__track"><label>Status <select data-fai-qstatus>${opts}</select></label>` +
        `<input type="text" class="fai-input" data-fai-qnote maxlength="400" placeholder="What the funder answered" value="${esc(cur.note || "")}" aria-label="Funder's answer"></div></li>`
      );
    }).join("");
    const done = (r.questions || []).filter((q) => (kept[keyOf(q.question)] || {}).status === "answered").length;
    return (
      `<div class="fai-bar"><span class="fai-hint" id="faiQProgress">${done} of ${(r.questions || []).length} answered</span>` +
      `<button type="button" class="btn" data-fai-copy="questions">Copy all questions</button></div>` +
      (rows ? `<ul class="fai-qs">${rows}</ul>` : `<p class="fai-hint">The grant text answers the usual questions. Nothing to ask.</p>`)
    );
  }

  const gbp = (n) => "£" + Math.round(n).toLocaleString("en-GB");

  // The arithmetic of the budget helper: what the grant pays and what the applicant has to find, from the project cost.
  function budgetCalc(grant, r, cost) {
    const total = Number(cost);
    if (!Number.isFinite(total) || total <= 0) return null;
    const rate = r && r.funding_rate_percent ? r.funding_rate_percent / 100 : null;
    const wanted = rate ? total * rate : total;
    const cap = grant && grant.max_award != null ? grant.max_award : null;
    const floor = grant && grant.min_award != null ? grant.min_award : null;
    const grantAmount = cap != null ? Math.min(wanted, cap) : wanted;
    const notes = [];
    if (cap != null && wanted > cap) notes.push(`The grant is capped at ${gbp(cap)} per project, so the cap sets the grant, not the funding rate.`);
    if (floor != null && grantAmount < floor) notes.push(`The smallest award is ${gbp(floor)}: a project this size is below it.`);
    if (!rate) notes.push("The call states no funding rate, so this assumes the grant could pay all of your cost up to the cap. Ask the funder.");
    return { total, grant: grantAmount, match: total - grantAmount, share: Math.round((grantAmount / total) * 100), notes };
  }

  function calcHtml(grant, r, cost) {
    const c = budgetCalc(grant, r, cost);
    if (!c) return `<p class="fai-hint">Enter your total project cost to see the grant and the match you need.</p>`;
    return (
      `<div class="fai-calc"><div><span class="fai-hint">Grant</span><strong>${gbp(c.grant)}</strong><span class="fai-hint">${c.share}% of cost</span></div>` +
      `<div><span class="fai-hint">You find</span><strong>${gbp(c.match)}</strong><span class="fai-hint">${100 - c.share}% of cost</span></div></div>` +
      list(c.notes)
    );
  }

  function budgetHtml(r, state, grant) {
    const cost = (state && state.cost) || "";
    return (
      block("Funding rate", para(r.funding_rate_percent ? `The grant pays ${r.funding_rate_percent}% of project cost. ${r.rate_note}` : r.rate_note || "Not stated")) +
      `<section class="fai-block"><h4>Your numbers</h4><label class="fai-field">Total project cost (£) <input type="number" min="0" step="100" class="fai-input" data-fai-cost value="${esc(cost)}" placeholder="e.g. 80000"></label>` +
      `<div id="faiCalc">${calcHtml(grant, r, cost)}</div></section>` +
      block("Match funding", para(r.match_required)) + block("Costs it pays for", list(r.eligible_costs)) + block("Costs it does not pay for", list(r.ineligible_costs)) +
      block("State aid and subsidy control", para(r.state_aid)) + block("Other conditions", list(r.other_conditions))
    );
  }

  const STAGE = { application: "To apply", award: "If awarded", claim: "To claim" };

  function evidenceHtml(r, state) {
    const have = (state && state.have) || {};
    const items = r.items || [];
    const ready = items.filter((i) => have[keyOf(i.item)] || (i.documents && i.documents.some((d) => !d.expired))).length;
    const rows = items.map((i) => {
      const k = keyOf(i.item);
      const docs = (i.documents || []).map((d) => `<span class="fai-doc${d.expired ? " is-expired" : ""}">${esc(d.filename)}${d.expired ? " (expired " + esc(d.expiry_date) + ")" : ""}</span>`).join(" ");
      return (
        `<li class="fai-ev"><label><input type="checkbox" data-fai-have="${esc(k)}"${have[k] ? " checked" : ""}> <strong>${esc(i.item)}</strong></label> ` +
        `<span class="fai-pill">${esc(STAGE[i.stage] || STAGE.application)}</span>${i.required ? "" : ` <span class="fai-hint">likely</span>`}` +
        (i.why ? `<div class="fai-hint">${esc(i.why)}</div>` : "") +
        (docs ? `<div class="fai-hint">In your Documents: ${docs}</div>` : "") + `</li>`
      );
    }).join("");
    return `<div class="fai-bar"><span class="fai-hint" id="faiEvProgress">${ready} of ${items.length} ready</span></div>` + (rows ? `<ul class="fai-evs">${rows}</ul>` : `<p class="fai-hint">No documents are named in the call.</p>`);
  }

  const RENDER = { eligibility: eligibilityHtml, summary: summaryHtml, draft: draftHtml, analysis: analysisHtml, questions: questionsHtml, budget: budgetHtml, evidence: evidenceHtml };

  function resultHtml(kind, result, state, grant) {
    return RENDER[kind] ? RENDER[kind](result || {}, state || {}, grant || {}) : "";
  }

  function tabsHtml(active) {
    return TOOLS.map(([key, label]) =>
      `<button type="button" role="tab" class="fai-tab${key === active ? " is-active" : ""}" data-fai-tab="${key}" aria-selected="${key === active}">${esc(label)}</button>`).join("");
  }

  function paneHtml(kind, st, costs, grant) {
    const tool = TOOLS.find((t) => t[0] === kind);
    const cost = (costs || DEFAULT_COSTS)[kind];
    const price = cost > 0 ? `${cost} ${cost === 1 ? "credit" : "credits"}` : "free";
    if (!st || st.status === "loading") return `<p class="fai-hint" role="status">${st && st.running ? "Working on it. This can take up to a minute…" : "Checking for a saved result…"}</p>`;
    if (st.status === "error") return `<p class="fai-error" role="alert">${esc(st.error)}</p><button type="button" class="btn btn--primary" data-fai-run="${kind}">Try again (${esc(price)})</button>`;
    if (st.status === "done") {
      return (
        `<div class="fai-bar"><span class="fai-hint">${st.cached ? "Saved result" + (st.savedAt ? " from " + esc(String(st.savedAt).slice(0, 10)) : "") + ". " : ""}AI can be wrong: check anything important with the funder.</span>` +
        `<button type="button" class="btn" data-fai-run="${kind}" data-fai-refresh="1">Run again (${esc(price)})</button></div>` + resultHtml(kind, st.result, st.state, grant)
      );
    }
    return `<p>${esc(tool[2])}</p><button type="button" class="btn btn--primary" data-fai-run="${kind}">Run: ${esc(price)}</button>`;
  }

  function modalHtml(grant, active, states, costs) {
    return (
      `<div class="profile-modal funding-ai-page" role="document">` +
      `<div class="profile-header"><div class="profile-header__left"><div class="profile-header__title-row"><h2 class="profile-header__title" id="fundingAiTitle" tabindex="-1">${esc(grant.name)}</h2>` +
      `<span class="profile-header__scope">AI tools</span></div><span class="profile-header__sub">${esc(grant.funder || "")}</span></div>` +
      `<button type="button" class="profile-header__close" data-fai-close aria-label="Close">×</button></div>` +
      `<div class="fai-tabs" role="tablist" aria-label="AI tools">${tabsHtml(active)}</div>` +
      `<div class="fai-body" id="fundingAiBody">${paneHtml(active, states[active], costs, grant)}</div></div>`
    );
  }

  const api = { TOOLS, budgetCalc, calcHtml, resultHtml, paneHtml, modalHtml, tabsHtml, eligibilityHtml, summaryHtml, draftHtml, analysisHtml };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof document === "undefined") return;

  // ── controller ────────────────────────────────────────────────────────────────────────────────
  const ctl = { grant: null, active: "eligibility", states: {}, costs: DEFAULT_COSTS, el: null };

  function render() {
    if (!ctl.el) return;
    ctl.el.innerHTML = modalHtml(ctl.grant, ctl.active, ctl.states, ctl.costs);
  }

  async function post(kind, extra) {
    const res = await fetch("/api/funding/ai/" + kind, {
      method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.assign({ label: ctl.grant.label }, extra)),
    });
    let data = null;
    try { data = await res.json(); } catch (e) { /* not JSON */ }
    if (res.status === 402) throw new Error("You do not have enough credits for this. Top up in Billing & credits.");
    if (!res.ok || !data || data.ok === false) throw new Error((data && (data.error || data.message)) || "Something went wrong (" + res.status + "). Try again.");
    return data;
  }

  async function load(kind) {
    const grant = ctl.grant;
    ctl.states[kind] = { status: "loading" };
    render();
    try {
      const data = await post(kind, { saved_only: true });
      if (ctl.grant !== grant) return;
      ctl.states[kind] = data.none ? { status: "idle" } : { status: "done", result: data.result, cached: true, savedAt: data.saved_at, state: data.state || {} };
    } catch (e) {
      if (ctl.grant !== grant) return;
      ctl.states[kind] = { status: "idle" };
    }
    render();
  }

  async function run(kind, refresh) {
    const grant = ctl.grant;
    ctl.states[kind] = { status: "loading", running: true };
    render();
    try {
      const data = await post(kind, { refresh: !!refresh });
      if (ctl.grant !== grant) return;
      ctl.states[kind] = { status: "done", result: data.result, cached: !!data.cached, savedAt: data.saved_at, state: data.state || {} };
      if (typeof window.refreshCredits === "function") window.refreshCredits();
    } catch (e) {
      if (ctl.grant !== grant) return;
      ctl.states[kind] = { status: "error", error: e.message };
    }
    render();
  }

  // What the user ticks or types (question status and answers, evidence ticks, project cost) is kept per grant and tool.
  let saveTimer = null;
  function keep(kind) {
    const st = ctl.states[kind];
    if (!st || !ctl.grant) return;
    const label = ctl.grant.label;
    const state = st.state || {};
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      fetch("/api/funding/ai/" + kind + "/state", {
        method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ label, state }),
      }).catch(() => { /* the page still shows what was typed */ });
    }, 500);
  }

  function onEdit(e) {
    const st = ctl.states[ctl.active];
    if (!st || st.status !== "done") return;
    const t = e.target;
    st.state = st.state || {};
    const row = t.closest("[data-fai-q]");
    if (row && (t.matches("[data-fai-qstatus]") || t.matches("[data-fai-qnote]"))) {
      const k = row.getAttribute("data-fai-q");
      st.state.q = st.state.q || {};
      const cur = (st.state.q[k] = st.state.q[k] || {});
      if (t.matches("[data-fai-qstatus]")) cur.status = t.value; else cur.note = t.value.slice(0, 400);
      const n = (st.result.questions || []).filter((q) => ((st.state.q[keyOf(q.question)] || {}).status) === "answered").length;
      const p = ctl.el.querySelector("#faiQProgress");
      if (p) p.textContent = n + " of " + (st.result.questions || []).length + " answered";
      keep(ctl.active);
    } else if (t.matches("[data-fai-cost]")) {
      st.state.cost = t.value;
      const out = ctl.el.querySelector("#faiCalc");
      if (out) out.innerHTML = calcHtml(ctl.grant, st.result, t.value);
      keep(ctl.active);
    } else if (t.matches("[data-fai-have]")) {
      st.state.have = st.state.have || {};
      st.state.have[t.getAttribute("data-fai-have")] = t.checked;
      const items = st.result.items || [];
      const n = items.filter((i) => st.state.have[keyOf(i.item)] || (i.documents || []).some((d) => !d.expired)).length;
      const p = ctl.el.querySelector("#faiEvProgress");
      if (p) p.textContent = n + " of " + items.length + " ready";
      keep(ctl.active);
    }
  }

  function copyQuestions() {
    const st = ctl.states.questions;
    const text = ((st && st.result && st.result.questions) || []).map((q, i) => (i + 1) + ". " + q.question).join("\n");
    if (navigator.clipboard && text) navigator.clipboard.writeText(text).catch(() => {});
  }

  function select(kind) {
    ctl.active = kind;
    if (!ctl.states[kind]) load(kind);
    else render();
  }

  function close() {
    if (ctl.el) ctl.el.classList.add("hidden");
    ctl.grant = null;
  }

  function open(grant) {
    if (!grant || !grant.label) return;
    if (!ctl.el) {
      ctl.el = document.createElement("div");
      ctl.el.id = "fundingAiModal";
      ctl.el.className = "modal-overlay hidden";
      ctl.el.setAttribute("role", "dialog");
      ctl.el.setAttribute("aria-modal", "true");
      ctl.el.setAttribute("aria-labelledby", "fundingAiTitle");
      ctl.el.addEventListener("click", (e) => {
        if (e.target === ctl.el || e.target.closest("[data-fai-close]")) return close();
        const tab = e.target.closest("[data-fai-tab]");
        if (tab) return select(tab.getAttribute("data-fai-tab"));
        if (e.target.closest("[data-fai-copy]")) return copyQuestions();
        const go = e.target.closest("[data-fai-run]");
        if (go) run(go.getAttribute("data-fai-run"), go.hasAttribute("data-fai-refresh"));
      });
      ctl.el.addEventListener("input", onEdit);
      ctl.el.addEventListener("change", onEdit);
      document.addEventListener("keydown", (e) => { if (e.key === "Escape" && ctl.grant) close(); });
      document.body.appendChild(ctl.el);
      fetch("/api/funding/ai/costs", { credentials: "same-origin" }).then((r) => (r.ok ? r.json() : null))
        .then((d) => { if (d && d.costs) { ctl.costs = d.costs; if (ctl.grant) render(); } }).catch(() => {});
    }
    ctl.grant = grant;
    ctl.active = "eligibility";
    ctl.states = {};
    ctl.el.classList.remove("hidden");
    select("eligibility");
    ctl.el.querySelector("#fundingAiTitle")?.focus();
  }

  window.FundingAI = Object.assign({ open, close }, api);
})();
