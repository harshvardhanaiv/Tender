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

  // The plain text of a result ("## " headings, "- " bullets): what Copy puts on the clipboard and Download turns into a Word file.
  function toText(kind, r, grant) {
    r = r || {};
    const out = [];
    const sec = (title, body) => {
      const items = Array.isArray(body) ? body.filter(Boolean) : body ? [body] : [];
      if (!items.length) return;
      out.push("## " + title);
      items.forEach((t) => out.push(Array.isArray(body) ? "- " + t : t));
    };
    if (kind === "eligibility") {
      out.push((VERDICT[r.verdict] || VERDICT.unclear)[0] + ": " + (r.headline || ""));
      sec("Each condition", (r.criteria || []).map((c) => (STATUS[c.status] || STATUS.unclear)[0] + ": " + c.criterion + (c.evidence ? " (" + c.evidence + ")" : "")));
      sec("Still needed to be sure", r.missing); sec("Next steps", r.next_steps);
    } else if (kind === "summary") {
      sec("In short", r.overview); sec("What it funds", r.funds); sec("Who can apply", r.who_can_apply); sec("Award", r.award);
      sec("Key dates", r.key_dates); sec("Most likely to rule you out", r.deal_breakers); sec("How to apply", r.how_to_apply);
    } else if (kind === "draft") {
      (r.sections || []).forEach((x) => {
        out.push("## " + x.heading);
        String(x.text || "").split("\n").forEach((line) => { if (line.trim()) out.push(line.trim()); });
        if (x.gaps && x.gaps.length) { out.push("You need to add:"); x.gaps.forEach((g) => out.push("- " + g)); }
      });
      sec("Everything still missing", r.gaps);
    } else if (kind === "analysis") {
      out.push((RECOMMENDATION[r.recommendation] || RECOMMENDATION.maybe)[0] + ": " + (r.reason || ""));
      sec("What the funder wants", r.funder_priorities); sec("Where you fit", r.strengths); sec("Where you do not fit yet", r.weaknesses);
      sec("Competition", r.competition); sec("What an assessor will look for", r.assessment_hints); sec("Risks", r.risks); sec("Next steps", r.next_steps);
    } else if (kind === "questions") {
      sec("Questions for the funder", (r.questions || []).map((q) => q.question));
    } else if (kind === "budget") {
      sec("Funding rate", r.funding_rate_percent ? "The grant pays " + r.funding_rate_percent + "% of project cost. " + (r.rate_note || "") : r.rate_note);
      sec("Match funding", r.match_required); sec("Costs it pays for", r.eligible_costs); sec("Costs it does not pay for", r.ineligible_costs);
      sec("State aid and subsidy control", r.state_aid); sec("Other conditions", r.other_conditions);
    } else if (kind === "evidence") {
      sec("Documents to have ready", (r.items || []).map((i) => i.item + (i.why ? " (" + i.why + ")" : "")));
    }
    return out.join("\n");
  }

  const EDITABLE = ["eligibility", "summary", "draft", "analysis"];

  // The user's own edited text (kept with the result) replaces the AI's wording until they show the AI version again.
  const editedOf = (st) => (st && st.state && typeof st.state.edited === "string" && st.state.edited.trim() && !st.showAi ? st.state.edited : "");

  function editedHtml(text) {
    return String(text).split("\n").map((l) => l.trim()).filter(Boolean).map((l) =>
      l.startsWith("## ") ? `<h4 class="fai-edited__h">${esc(l.slice(3))}</h4>` : l.startsWith("- ") ? `<p class="fai-edited__li">• ${esc(l.slice(2))}</p>` : `<p>${esc(l)}</p>`).join("");
  }

  const RENDER = { eligibility: eligibilityHtml, summary: summaryHtml, draft: draftHtml, analysis: analysisHtml, questions: questionsHtml, budget: budgetHtml, evidence: evidenceHtml };

  function resultHtml(kind, result, state, grant) {
    return RENDER[kind] ? RENDER[kind](result || {}, state || {}, grant || {}) : "";
  }

  function tabsHtml(active) {
    return TOOLS.map(([key, label]) =>
      `<button type="button" role="tab" class="fai-tab${key === active ? " is-active" : ""}" data-fai-tab="${key}" aria-selected="${key === active}">${esc(label)}</button>`).join("");
  }

  function bodyHtml(kind, st, grant) {
    if (st.editing) {
      const text = typeof (st.state || {}).edited === "string" && (st.state || {}).edited.trim() ? st.state.edited : toText(kind, st.result, grant);
      return `<div class="fai-editor"><label class="fai-hint" for="faiEditArea">Edit the text. A line starting with "## " is a heading and "- " is a bullet.</label>` +
        `<textarea id="faiEditArea" class="fai-input fai-editor__area" rows="18" maxlength="19000">${esc(text)}</textarea>` +
        `<div class="fai-actions"><button type="button" class="btn btn--primary" data-fai-act="save-edit">Save my version</button><button type="button" class="btn" data-fai-act="cancel-edit">Cancel</button>` +
        (editedOf({ ...st, editing: false, showAi: false }) ? `<button type="button" class="btn" data-fai-act="reset-edit">Go back to the AI version</button>` : "") + `</div></div>`;
    }
    const mine = editedOf(st);
    if (mine) {
      return `<p class="fai-hint">Your edited version. <button type="button" class="fai-link" data-fai-act="show-ai">Show the AI version</button></p><div class="fai-edited">${editedHtml(mine)}</div>`;
    }
    const ai = (st.state || {}).edited && st.showAi ? `<p class="fai-hint">The AI version. <button type="button" class="fai-link" data-fai-act="show-mine">Back to my edited version</button></p>` : "";
    return ai + resultHtml(kind, st.result, st.state, grant);
  }

  function paneHtml(kind, st, costs, grant) {
    const tool = TOOLS.find((t) => t[0] === kind);
    const cost = (costs || DEFAULT_COSTS)[kind];
    const price = cost > 0 ? `${cost} ${cost === 1 ? "credit" : "credits"}` : "free";
    if (!st || st.status === "loading") return `<p class="fai-hint" role="status">${st && st.running ? "Working on it. This can take up to a minute…" : "Checking for a saved result…"}</p>`;
    if (st.status === "error") return `<p class="fai-error" role="alert">${esc(st.error)}</p><button type="button" class="btn btn--primary" data-fai-run="${kind}">Try again (${esc(price)})</button>`;
    if (st.status === "done") {
      return (
        `<div class="fai-bar"><span class="fai-hint">${st.profile && st.profile.name ? "Based on your profile <strong>" + esc(st.profile.name) + "</strong>. " : ""}${st.cached ? "Saved result" + (st.savedAt ? " from " + esc(String(st.savedAt).slice(0, 10)) : "") + ". " : ""}AI can be wrong: check anything important with the funder.</span>` +
        `<button type="button" class="btn" data-fai-run="${kind}" data-fai-refresh="1">Run again (${esc(price)})</button></div>` +
        `<div class="fai-actions" role="group" aria-label="Use this result">` +
        `<button type="button" class="btn" data-fai-act="copy">Copy</button><button type="button" class="btn" data-fai-act="docx">Download .docx</button>` +
        (kind === "draft" ? `<button type="button" class="btn" data-fai-act="bank" title="Keep each section in your Answer Bank so you can reuse it in other applications">Save to Answer Bank</button>` : "") +
        (EDITABLE.includes(kind) && !st.editing ? `<button type="button" class="btn" data-fai-act="edit">Edit</button>` : "") +
        `<button type="button" class="btn" data-fai-act="attach" title="Keep this as a Word file in your Documents">Attach to Documents</button>` +
        `<span class="fai-hint" id="faiActNote" role="status"></span></div>` + bodyHtml(kind, st, grant)
      );
    }
    // The free rule-based check comes first: when it says this grant is unlikely to suit the company, say why before credits are spent.
    const blocked = grant && grant.fit && grant.fit.verdict === "unlikely" && cost > 0;
    const runAnyway = blocked && kind !== "eligibility";
    const warn = blocked ? `<p class="fai-warn" role="alert"><strong>Unlikely to qualify.</strong> ${esc(grant.fit.blocker || "A condition of this grant does not match your company profile.")} The AI will work from the same facts, so check the full criteria with the funder before spending credits.</p>` : "";
    return `<p>${esc(tool[2])}</p>${warn}<button type="button" class="btn btn--primary" data-fai-run="${kind}">${runAnyway ? "Run anyway" : "Run"}: ${esc(price)}</button>`;
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

  const api = { editedOf, toText, TOOLS, budgetCalc, calcHtml, resultHtml, paneHtml, modalHtml, tabsHtml, eligibilityHtml, summaryHtml, draftHtml, analysisHtml };
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
      ctl.states[kind] = data.none ? { status: "idle" } : { status: "done", result: data.result, cached: true, savedAt: data.saved_at, state: data.state || {}, profile: data.profile || null };
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
      ctl.states[kind] = { status: "done", result: data.result, cached: !!data.cached, savedAt: data.saved_at, state: data.state || {}, profile: data.profile || null };
      if (window.TFFunding && window.TFFunding.track && !data.cached) window.TFFunding.track("funding_ai_run", { tool: kind, credits: data.cost, fit: (grant.fit && grant.fit.verdict) || "" });
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

  function note(text) {
    const el = ctl.el && ctl.el.querySelector("#faiActNote");
    if (el) el.textContent = text;
  }

  function fileTitle(kind) {
    const tool = TOOLS.find((t) => t[0] === kind);
    return (ctl.grant.name || "Grant") + " - " + (tool ? tool[1] : kind);
  }

  const textInUse = (kind, st) => editedOf(st) || toText(kind, st.result, ctl.grant);

  async function copyResult(kind, st) {
    const text = fileTitle(kind) + "\n\n" + textInUse(kind, st);
    try { await navigator.clipboard.writeText(text); note("Copied."); } catch (e) { note("Could not copy. Select the text and copy it instead."); }
  }

  async function downloadResult(kind, st) {
    note("Preparing the file…");
    try {
      const title = fileTitle(kind);
      const res = await fetch("/api/funding/export/docx", {
        method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, text: textInUse(kind, st) }),
      });
      if (!res.ok) throw new Error("status " + res.status);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = title.replace(/[^A-Za-z0-9 _.-]+/g, "").trim().replace(/ +/g, "_").slice(0, 80) + ".docx";
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
      note("Downloaded.");
    } catch (e) { note("Could not make the file. Try again."); }
  }

  async function docxBlob(kind, st) {
    const res = await fetch("/api/funding/export/docx", {
      method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: fileTitle(kind), text: textInUse(kind, st) }),
    });
    if (!res.ok) throw new Error("status " + res.status);
    return res.blob();
  }

  // The result as a Word file in the user's Documents, next to their certificates and policies.
  async function attachResult(kind, st) {
    note("Adding to your Documents…");
    try {
      const blob = await docxBlob(kind, st);
      const name = fileTitle(kind).replace(/[^A-Za-z0-9 _.-]+/g, "").trim().replace(/ +/g, "_").slice(0, 80) + ".docx";
      const form = new FormData();
      form.append("file", new File([blob], name, { type: blob.type || "application/vnd.openxmlformats-officedocument.wordprocessingml.document" }));
      const res = await fetch("/api/user-documents", { method: "POST", credentials: "same-origin", body: form });
      const data = await res.json().catch(() => null);
      if (!res.ok || (data && data.ok === false)) throw new Error((data && data.error) || "status " + res.status);
      note("Added to your Documents as " + name + ".");
    } catch (e) { note("Could not add it to your Documents. Try again."); }
  }

  function setEdited(kind, st, text) {
    st.state = st.state || {};
    if (text) st.state.edited = text; else delete st.state.edited;
    st.editing = false;
    st.showAi = false;
    keep(kind);
    render();
  }

  // Each section of the draft becomes an Answer Bank entry ("<grant>: <section>") so it can be reused in later applications.
  async function saveToBank(kind, st) {
    let sections = ((st.result && st.result.sections) || []).filter((x) => x.heading && String(x.text || "").trim());
    const mine = editedOf(st);
    if (mine) {  // the user's own wording: split their text back into "## heading" sections
      sections = [];
      mine.split("\n").forEach((line) => {
        if (line.startsWith("## ")) sections.push({ heading: line.slice(3).trim(), text: "" });
        else if (sections.length && line.trim() && !line.startsWith("You need to add:") && !line.startsWith("- ")) sections[sections.length - 1].text += (sections[sections.length - 1].text ? "\n" : "") + line.trim();
      });
      sections = sections.filter((x) => x.heading && x.text.trim());
    }
    if (!sections.length) { note("Nothing to save."); return; }
    note("Saving…");
    let saved = 0;
    for (const x of sections) {
      try {
        const res = await fetch("/api/answer-bank", {
          method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ category: "Grants", question: ctl.grant.name + ": " + x.heading, answer: String(x.text).trim(), profile_id: st.profile && st.profile.id ? st.profile.id : null }),
        });
        if (res.ok) saved += 1;
      } catch (e) { /* counted below */ }
    }
    note(saved === sections.length ? "Saved " + saved + (saved === 1 ? " answer" : " answers") + " to your Answer Bank (category Grants)." : "Saved " + saved + " of " + sections.length + ". Try again for the rest.");
  }

  function select(kind) {
    ctl.active = kind;
    if (!ctl.states[kind]) load(kind);
    else render();
  }

  function close() {
    if (ctl.el) ctl.el.classList.add("hidden");
    ctl.grant = null;
    const back = ctl.opener;
    ctl.opener = null;
    if (back && back.isConnected && typeof back.focus === "function") back.focus();  // focus returns to what opened the dialog
  }

  function open(grant) {
    if (!grant || !grant.label) return;
    if (!ctl.el) {
      ctl.el = document.createElement("div");
      ctl.el.id = "fundingAiModal";
      ctl.el.className = "modal-overlay hidden";
      ctl.el.setAttribute("role", "dialog");
      ctl.el.setAttribute("aria-modal", "true");
      ctl.el.setAttribute("tabindex", "-1");
      ctl.el.setAttribute("aria-labelledby", "fundingAiTitle");
      ctl.el.addEventListener("click", (e) => {
        if (e.target === ctl.el || e.target.closest("[data-fai-close]")) return close();
        const tab = e.target.closest("[data-fai-tab]");
        if (tab) return select(tab.getAttribute("data-fai-tab"));
        if (e.target.closest("[data-fai-copy]")) return copyQuestions();
        const act = e.target.closest("[data-fai-act]");
        if (act) {
          const st = ctl.states[ctl.active];
          if (!st || st.status !== "done") return;
          const what = act.getAttribute("data-fai-act");
          if (what === "edit") { st.editing = true; return render(); }
          if (what === "cancel-edit") { st.editing = false; return render(); }
          if (what === "save-edit") { const t = (ctl.el.querySelector("#faiEditArea") || {}).value || ""; return setEdited(ctl.active, st, t.trim() === toText(ctl.active, st.result, ctl.grant).trim() ? "" : t.trim()); }
          if (what === "reset-edit") return setEdited(ctl.active, st, "");
          if (what === "show-ai") { st.showAi = true; return render(); }
          if (what === "show-mine") { st.showAi = false; return render(); }
          if (what === "attach") return attachResult(ctl.active, st);
          return what === "copy" ? copyResult(ctl.active, st) : what === "docx" ? downloadResult(ctl.active, st) : saveToBank(ctl.active, st);
        }
        const go = e.target.closest("[data-fai-run]");
        if (go) run(go.getAttribute("data-fai-run"), go.hasAttribute("data-fai-refresh"));
      });
      ctl.el.addEventListener("input", onEdit);
      ctl.el.addEventListener("change", onEdit);
      document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && ctl.grant) return close();
        if (e.key !== "Tab" || !ctl.grant) return;  // keep Tab inside the dialog
        const items = Array.from(ctl.el.querySelectorAll("button, a[href], input, select, textarea, [tabindex='0']")).filter((n) => !n.disabled && n.offsetParent !== null);
        if (!items.length) return;
        const first = items[0], last = items[items.length - 1];
        if (e.shiftKey && (document.activeElement === first || document.activeElement === ctl.el)) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      });
      document.body.appendChild(ctl.el);
      fetch("/api/funding/ai/costs", { credentials: "same-origin" }).then((r) => (r.ok ? r.json() : null))
        .then((d) => { if (d && d.costs) { ctl.costs = d.costs; if (ctl.grant) render(); } }).catch(() => {});
    }
    ctl.opener = document.activeElement;
    ctl.grant = grant;
    ctl.active = "eligibility";
    ctl.states = {};
    ctl.el.classList.remove("hidden");
    select("eligibility");
    // The dialog itself takes focus: the title is redrawn as results arrive, which would drop the focus back to the page.
    ctl.el.focus();
  }

  window.FundingAI = Object.assign({ open, close }, api);
})();
