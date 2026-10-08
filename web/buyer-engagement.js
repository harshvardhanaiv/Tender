/* TenderFlow buyer workspace: market engagement (preliminary market engagement planning).
   Registers the "engagements" view on window.BW (see buyer-workspace.js) and talks only to
   /api/market-engagement/*. A plan moves through five steps: scope, suppliers, notice, responses,
   hand-over. TenderFlow drafts the notice and keeps the record; it does not publish to Find a Tender
   or email suppliers, and the screens say so where it matters. */
(function () {
  "use strict";

  const root = typeof window !== "undefined" ? window : globalThis;
  const BW = (root.BW = root.BW || {});
  if (!BW.registerView) return; // core not loaded (e.g. a stray include)
  const { esc, fmtInt, fmtMoney, fmtDate, plural, api, toast, openModal, closeModal, navigate, copyText, download, skeleton } = BW;

  const STATUS = {
    draft: { label: "Draft", cls: "" },
    published: { label: "Published", cls: "bw-badge--good" },
    closed: { label: "Closed", cls: "bw-badge--info" },
    converted: { label: "Handed over", cls: "bw-badge--accent" },
  };
  const SUPPLIER_STATUS = [
    ["not_contacted", "Not yet contacted"], ["invited", "Invited"], ["responded", "Responded"],
    ["declined", "Declined"], ["no_response", "No response"],
  ];
  const TYPES = [
    ["questionnaire", "Supplier questionnaire"], ["supplier_day", "Supplier day"],
    ["both", "Questionnaire and supplier day"], ["meetings", "One-to-one meetings"],
  ];
  const statusBadge = (s) => `<span class="bw-badge ${(STATUS[s] || STATUS.draft).cls}">${esc((STATUS[s] || STATUS.draft).label)}</span>`;

  const detail = { plan: null, open: new Set(), scopeDraft: null, noticeDraft: null, missing: [], busy: false };

  // ── list ────────────────────────────────────────────────────────────────────────────────────
  function miniSteps(steps) {
    return `<div class="bw-mini-steps" aria-hidden="true">${steps.map((s) => `<i class="${s.state === "done" ? "is-done" : s.state === "active" ? "is-active" : ""}"></i>`).join("")}</div>`;
  }
  function planCard(p) {
    const active = p.steps.find((s) => s.state === "active");
    return `<a class="bw-card bw-plan" href="#/engagements/${p.id}">
      <div class="bw-plan__top"><h2 class="bw-plan__title">${esc(p.title)}</h2>${statusBadge(p.status)}</div>
      <div class="bw-plan__meta"><span>${esc(p.category_label || "")}</span>
        ${p.est_value != null ? `<span>${fmtMoney(p.est_value)}${p.term_years ? ` over ${esc(String(p.term_years))} yr${p.term_years === 1 ? "" : "s"}` : ""}</span>` : ""}
        <span>${plural(p.supplier_counts.included, "supplier")}${p.supplier_counts.responded ? `, ${fmtInt(p.supplier_counts.responded)} responded` : ""}</span>
        <span>updated ${fmtDate(p.updated_at)}</span></div>
      ${miniSteps(p.steps)}
      <div class="bw-hint">${active ? `Next: ${esc(active.title)}` : "All steps complete"}</div></a>`;
  }
  async function renderList(ctx) {
    ctx.main.innerHTML = `<div class="bw-page"><div class="bw-head"><h1>Market engagement</h1></div>${skeleton(3)}</div>`;
    const { plans } = await api("/api/market-engagement");
    if (!ctx.isCurrent()) return;
    ctx.main.innerHTML = `<div class="bw-page"><div class="bw-head"><div class="bw-head__row"><div><h1>Market engagement</h1>
        <p>Talk to the market before you write the specification. Plan a preliminary market engagement, choose who to approach from who actually wins this work, draft the notice, and keep a fair, auditable record.</p></div>
        <button class="bw-btn bw-btn--primary" data-action="eng-new">New market engagement</button></div></div>
      <div class="bw-note bw-note--info">TenderFlow drafts your notice and keeps the record. It does not publish to Find a Tender or email suppliers: you publish the notice yourself, then record the link here.</div>
      ${plans.length ? plans.map(planCard).join("") : `<div class="bw-card"><div class="bw-empty"><strong>No engagements yet</strong>Start one when you are planning a new purchase or a re-procurement. You can begin from a contract that is ending soon on your dashboard.
        <div class="bw-actions" style="justify-content:center"><button class="bw-btn bw-btn--primary" data-action="eng-new">New market engagement</button></div></div></div>`}</div>`;
  }

  // ── create ──────────────────────────────────────────────────────────────────────────────────
  function categoryFields(spec, id = "engCategory", disabled = "") {
    const presets = (BW.state.options && BW.state.options.presets) || [];
    const isPreset = spec && spec.preset;
    // The API holds a CPV as the prefix without its trailing zeros (48445); show the full 8-digit code.
    const cpvShown = spec && /^\d{2,7}$/.test(String(spec.cpv || "")) ? String(spec.cpv).padEnd(8, "0") : spec && spec.cpv;
    const custom = spec && !spec.preset ? [cpvShown, spec.q].filter(Boolean).join(" ") : "";
    return `<label class="bw-label" for="${id}">Category</label>
      <select class="bw-select" id="${id}" data-category-select ${disabled}>${presets.map((p) => `<option value="${esc(p.preset)}" ${isPreset && spec.preset === p.preset ? "selected" : ""}>${esc(p.label)}</option>`).join("")}
        <option value="__custom" ${custom ? "selected" : ""}>Something else…</option></select>
      <div id="${id}Wrap" class="${custom ? "" : "hidden"}"><input class="bw-input" id="${id}Custom" style="margin-top:8px" placeholder="A CPV code such as 50720000, or words such as “tree surgery”" value="${esc(custom)}" aria-label="CPV code or search words" ${disabled}></div>`;
  }
  function readCategory(rootEl, id = "engCategory") {
    const choice = rootEl.querySelector(`#${id}`).value;
    if (choice !== "__custom") return { preset: choice };
    const text = rootEl.querySelector(`#${id}Custom`).value.trim();
    if (/^[\d\s-]+$/.test(text)) return { cpv: text.replace(/\D/g, "") };
    return { q: text };
  }
  function sameCategory(a, b) {
    // 5072, 50720 and 50720000 are the same CPV (the server keeps the prefix without trailing zeros).
    const cpv = (v) => (v ? String(v).replace(/\D/g, "").replace(/0+$/, "") || null : null);
    return JSON.stringify([a.preset || null, cpv(a.cpv), a.q || null]) === JSON.stringify([b.preset || null, cpv(b.cpv), b.q || null]);
  }
  // The category a new plan starts on: the one being researched in Market Radar when it comes from there (a full
  // spec), a CPV code from a contract that is ending, or nothing. It used to fall back to the first preset every time.
  function prefillSpec(prefill = {}) {
    const c = prefill.category;
    if (c && (c.preset || c.cpv || c.q)) return c.preset ? { preset: c.preset } : { cpv: c.cpv || undefined, q: c.q || undefined };
    if (prefill.cpv) return { cpv: String(prefill.cpv) };
    if (prefill.preset) return { preset: prefill.preset };
    return null;
  }
  async function openNewEngagement(prefill = {}) {
    await BW.ensureOptions();
    const spec = prefillSpec(prefill);
    openModal(`<h2 id="bwModalTitle">New market engagement</h2>
      <p class="bw-card__sub">Start with the basics. You can change everything later.</p>
      <div id="engNewError"></div>
      <label class="bw-label" for="engTitle">Title</label>
      <input class="bw-input" id="engTitle" maxlength="200" placeholder="For example: Responsive repairs and gas servicing, 2027 framework" value="${esc(prefill.title || "")}">
      ${categoryFields(spec)}
      <div class="bw-field-row"><div><label class="bw-label" for="engValue">Estimated value (£)</label><input class="bw-input" id="engValue" type="number" min="0" step="1000" placeholder="e.g. 2,100,000" aria-describedby="engValueHint"><span class="bw-hint" id="engValueHint">In pounds, for the whole term.</span></div>
        <div><label class="bw-label" for="engTerm">Term (years)</label><input class="bw-input" id="engTerm" type="number" min="0.5" max="30" step="0.5" placeholder="4"></div></div>
      <label class="bw-label" for="engType">How will you engage?</label>
      <select class="bw-select" id="engType">${TYPES.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select>
      <div class="bw-actions bw-actions--end"><button class="bw-btn" data-action="modal-close">Cancel</button><button class="bw-btn bw-btn--primary" data-action="eng-create">Create plan</button></div>`);
  }
  async function createPlan() {
    const box = document.getElementById("bwModalBox");
    const errorBox = box.querySelector("#engNewError");
    errorBox.innerHTML = "";
    const body = {
      title: box.querySelector("#engTitle").value.trim(),
      category: readCategory(box),
      est_value: box.querySelector("#engValue").value || null,
      term_years: box.querySelector("#engTerm").value || null,
      engagement_type: box.querySelector("#engType").value,
    };
    if (body.category.q === "") { errorBox.innerHTML = '<div class="bw-note bw-note--bad">Enter a CPV code or some words to describe the category.</div>'; return; }
    try {
      const plan = await api("/api/market-engagement", { method: "POST", body });
      closeModal();
      toast("Engagement created");
      navigate(`#/engagements/${plan.id}`);
    } catch (err) {
      errorBox.innerHTML = `<div class="bw-note bw-note--bad">${esc(err.message)}</div>`;
    }
  }

  // ── detail ──────────────────────────────────────────────────────────────────────────────────
  function locked() { return detail.plan.status === "converted"; }
  function dis() { return locked() ? "disabled" : ""; }

  function stepSummary(plan, n) {
    switch (n) {
      case 1: return `${esc(plan.category_label || "")}${plan.est_value != null ? ` · ${fmtMoney(plan.est_value)}` : ""}`;
      case 2: return plural(plan.supplier_counts.included, "supplier") + " chosen";
      case 3: {
        if (plan.published_at) return `Published ${fmtDate(plan.published_at)}`;
        // text generated or typed but not saved yet: "Not drafted yet" would be wrong, "Draft saved" worse
        const unsaved = detail.noticeDraft != null && String(detail.noticeDraft).trim() !== String(plan.notice_text || "").trim() && String(detail.noticeDraft).trim() !== "";
        return unsaved ? "Unsaved draft" : (plan.notice_text ? "Draft saved" : "Not drafted yet");
      }
      case 4: return plan.status === "draft" ? "Opens when you publish" : `${fmtInt(plan.supplier_counts.responded)} of ${fmtInt(plan.supplier_counts.included)} responded`;
      default: return plan.converted_at ? `Handed over ${fmtDate(plan.converted_at)}` : "Not yet";
    }
  }

  function scopeValue(key) {
    const d = detail.scopeDraft;
    return d && key in d ? d[key] : detail.plan[key];
  }
  function stepScope(plan) {
    const type = scopeValue("engagement_type") || "questionnaire";
    const needsDay = type === "supplier_day" || type === "both";
    const v = (k) => esc(scopeValue(k) ?? "");
    return `<div class="bw-field-row"><div><label class="bw-label" for="sTitle">Title</label><input class="bw-input" id="sTitle" data-scope="title" maxlength="200" value="${v("title")}" ${dis()}></div>
        <div><label class="bw-label" for="sOrg">Contracting authority</label><input class="bw-input" id="sOrg" data-scope="organisation" maxlength="200" value="${v("organisation")}" ${dis()}></div></div>
      <div style="margin-top:6px">${categoryFields(plan.category || {}, "sCategory", dis())}</div>
      <p class="bw-hint">Current category: <b>${esc(plan.category_label || "")}</b> · <a class="bw-link" href="${esc(BW.buildHash(["radar"], BW.specToParams(plan.category && plan.category.preset ? { preset: plan.category.preset } : { cpv: plan.category && plan.category.cpv, q: plan.category && plan.category.q })))}">Research it in Market Radar</a></p>
      <div class="bw-field-row"><div><label class="bw-label" for="sValue">Estimated value (£)</label><input class="bw-input" id="sValue" data-scope="est_value" type="number" min="0" step="1000" value="${v("est_value")}" ${dis()}></div>
        <div><label class="bw-label" for="sTerm">Term (years)</label><input class="bw-input" id="sTerm" data-scope="term_years" type="number" min="0.5" max="30" step="0.5" value="${v("term_years")}" ${dis()}></div></div>
      <label class="bw-label" for="sType">How will you engage?</label>
      <select class="bw-select" id="sType" data-scope="engagement_type" ${dis()}>${TYPES.map(([val, l]) => `<option value="${val}" ${val === type ? "selected" : ""}>${l}</option>`).join("")}</select>
      <label class="bw-label" for="sObjectives">What do you want to learn from the market?</label>
      <textarea class="bw-textarea" id="sObjectives" data-scope="objectives" maxlength="4000" placeholder="For example: how suppliers would structure lots, realistic mobilisation times, price drivers, and risks in our draft requirements." ${dis()}>${v("objectives")}</textarea>
      <div class="bw-field-row ${needsDay ? "" : "hidden"}" id="dayRow"><div><label class="bw-label" for="sDayAt">Supplier day: date and time</label><input class="bw-input" id="sDayAt" data-scope="supplier_day_at" maxlength="120" placeholder="20 November 2026, 10:00" value="${v("supplier_day_at")}" ${dis()}></div>
        <div><label class="bw-label" for="sDayPlace">Supplier day: place or link</label><input class="bw-input" id="sDayPlace" data-scope="supplier_day_place" maxlength="200" value="${v("supplier_day_place")}" ${dis()}></div></div>
      <div class="bw-field-row"><div><label class="bw-label" for="sDeadline">Response deadline</label><input class="bw-input" id="sDeadline" data-scope="response_deadline" type="date" value="${v("response_deadline")}" ${dis()}></div><div></div></div>
      <div class="bw-field-row"><div><label class="bw-label" for="sContact">Contact name</label><input class="bw-input" id="sContact" data-scope="contact_name" maxlength="120" value="${v("contact_name")}" ${dis()}></div>
        <div><label class="bw-label" for="sEmail">Contact email</label><input class="bw-input" id="sEmail" data-scope="contact_email" type="email" maxlength="200" value="${v("contact_email")}" ${dis()}></div></div>
      ${locked() ? "" : '<div class="bw-actions"><button class="bw-btn bw-btn--primary" data-action="eng-save-scope">Save scope</button></div>'}`;
  }

  function supplierRow(s) {
    const st = s.stats || {};
    const meta = [st.buyers ? `${plural(st.buyers, "buyer")}` : null, st.awards ? `${plural(st.awards, "award")}` : null, (st.typical_contract || st.avg_contract) ? `typical contract ${fmtMoney(st.typical_contract || st.avg_contract)}` : null].filter(Boolean).join(" · ");
    return `<tr><td data-label="Include"><input type="checkbox" data-supplier="${s.id}" data-field="included" ${s.included ? "checked" : ""} ${dis()} aria-label="Include ${esc(s.supplier_name)}"></td>
      <td class="bw-cell-main" data-label="Supplier"><span class="bw-name">${esc(s.supplier_name)}</span>
        ${s.source === "manual" ? ' <span class="bw-badge">Added by you</span>' : ""}${st.size_hint ? ` <span class="bw-badge bw-badge--info" title="Typical contract size is only a proxy: notices do not say whether a supplier is an SME">${esc(st.size_hint)}</span>` : ""}
        ${meta ? `<span class="bw-sub">${esc(meta)}${st.latest_signed ? ` · latest award ${fmtDate(st.latest_signed)}` : ""}</span>` : ""}</td>
      <td class="num"><button class="bw-link" data-action="eng-remove-supplier" data-id="${s.id}" ${dis()}>Remove</button></td></tr>`;
  }
  function stepSuppliers(plan) {
    const rows = plan.suppliers;
    return `<p class="bw-card__sub">Start from the suppliers that have actually won this kind of work, then add anyone else you know. Include everyone you will approach, and give them the same information.</p>
      <div class="bw-toolbar"><button class="bw-btn bw-btn--primary" data-action="eng-match" ${dis()}>Find suppliers from award history</button>
        <label class="bw-filters__check" style="padding:0"><input type="checkbox" id="smallerFirst"> Smaller suppliers first</label>
        <select class="bw-select" id="matchWindow" aria-label="Award history to use"><option value="3y">last 3 years</option><option value="5y" selected>last 5 years</option><option value="all">all time</option></select></div>
      <p class="bw-hint" style="margin:-4px 0 12px">“Smaller suppliers first” orders by typical contract size, a proxy for SME participation: award notices do not say whether a supplier is an SME.</p>
      <div class="bw-toolbar"><input class="bw-input" id="manualSupplier" maxlength="200" placeholder="Add a supplier by name" aria-label="Supplier name" ${dis()}><button class="bw-btn" data-action="eng-add-supplier" ${dis()}>Add</button></div>
      ${rows.length ? `<div class="bw-table-wrap"><table class="bw-table bw-table--stack"><thead><tr><th>Include</th><th>Supplier</th><th></th></tr></thead><tbody>${rows.map(supplierRow).join("")}</tbody></table></div>
        <p class="bw-hint">${fmtInt(plan.supplier_counts.included)} of ${fmtInt(rows.length)} included.</p>`
      : '<div class="bw-empty">No suppliers yet. Use “Find suppliers from award history” or add one by name.</div>'}`;
  }

  function stepNotice(plan) {
    const text = detail.noticeDraft != null ? detail.noticeDraft : (plan.notice_text || "");
    const published = plan.status !== "draft";
    return `<p class="bw-card__sub">A draft you can edit, built from your scope. Check it against current Procurement Act 2023 guidance and your own notice template before you use it.</p>
      ${detail.missing.length ? `<div class="bw-note bw-note--warn"><strong>Still to fill in:</strong> ${detail.missing.map(esc).join(", ")}. Add them in step 1, then regenerate.</div>` : ""}
      <label class="bw-label" for="noticeText">Notice text</label>
      <textarea class="bw-textarea bw-textarea--tall" id="noticeText" ${dis()} placeholder="Choose “Generate draft” to build the notice from your scope.">${esc(text)}</textarea>
      <div class="bw-actions"><button class="bw-btn bw-btn--primary" data-action="eng-save-notice" ${dis()}>Save draft</button>
        <button class="bw-btn" data-action="eng-regenerate" ${dis()}>${text ? "Regenerate from scope" : "Generate draft"}</button>
        <button class="bw-btn" data-action="eng-copy-notice">Copy</button><button class="bw-btn" data-action="eng-download-notice">Download .txt</button></div>
      <h3 class="bw-card__title" style="margin-top:20px">Publishing</h3>
      ${published ? `<div class="bw-note bw-note--info">Marked as published ${fmtDate(plan.published_at)}${plan.published_url ? `: <a class="bw-link" href="${esc(BW.safeUrl(plan.published_url) || "#")}" target="_blank" rel="noopener noreferrer">${esc(plan.published_url)}</a>` : ""}.</div>`
        : `<div class="bw-note">TenderFlow does not publish notices. Copy the text above into Find a Tender (or your own e-notification route), then record it here. Save the draft first.</div>
          <div class="bw-toolbar"><input class="bw-input" id="publishUrl" style="width:380px" placeholder="Link to the published notice (optional)" aria-label="Link to the published notice"><button class="bw-btn bw-btn--primary" data-action="eng-publish">Mark as published</button></div>`}`;
  }

  function stepResponses(plan) {
    const included = plan.suppliers.filter((s) => s.included);
    const rows = included.map((s) => `<tr><td class="bw-cell-main" data-label="Supplier"><span class="bw-name">${esc(s.supplier_name)}</span></td>
      <td data-label="Status"><select class="bw-select" data-supplier="${s.id}" data-field="status" aria-label="Status for ${esc(s.supplier_name)}" ${dis()}>${SUPPLIER_STATUS.map(([v, l]) => `<option value="${v}" ${s.status === v ? "selected" : ""}>${l}</option>`).join("")}</select></td>
      <td data-label="Note"><input class="bw-input" data-supplier="${s.id}" data-field="note" maxlength="1000" value="${esc(s.note || "")}" placeholder="What was said or sent" aria-label="Note for ${esc(s.supplier_name)}" ${dis()}></td></tr>`).join("");
    const log = plan.log.slice().reverse();
    return `<p class="bw-card__sub">Record who you approached and what came back. Every status change is written to the record below, which doubles as your audit trail. Individual responses stay confidential to you.</p>
      ${included.length ? `<div class="bw-table-wrap"><table class="bw-table bw-table--stack"><thead><tr><th>Supplier</th><th>Status</th><th>Note</th></tr></thead><tbody>${rows}</tbody></table></div>` : '<div class="bw-empty">Choose suppliers in step 2 first.</div>'}
      <h3 class="bw-card__title" style="margin-top:20px">Engagement record</h3>
      ${locked() ? "" : '<div class="bw-toolbar"><input class="bw-input" id="logNote" style="width:420px" maxlength="500" placeholder="Add a note to the record" aria-label="Note for the record"><button class="bw-btn" data-action="eng-add-note">Add note</button></div>'}
      <ul class="bw-log">${log.map((e) => `<li><time>${esc(new Date(e.at).toLocaleString("en-GB", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }))}</time><span>${esc(e.message)}</span></li>`).join("")}</ul>
      ${plan.status === "published" ? '<div class="bw-actions"><button class="bw-btn" data-action="eng-close">Close the engagement</button></div><p class="bw-hint">Close it once the response deadline has passed and you have what you need.</p>' : ""}`;
  }

  function stepHandover(plan) {
    const can = plan.allowed_transitions.includes("converted");
    return `<p class="bw-card__sub">When you are ready to write the tender, take the scope, supplier list and record with you.</p>
      <div class="bw-grid bw-grid--3"><div class="bw-stat"><div class="bw-stat__label">Suppliers engaged</div><div class="bw-stat__value">${fmtInt(plan.supplier_counts.included)}</div></div>
        <div class="bw-stat"><div class="bw-stat__label">Responded</div><div class="bw-stat__value">${fmtInt(plan.supplier_counts.responded)}</div></div>
        <div class="bw-stat"><div class="bw-stat__label">Record entries</div><div class="bw-stat__value">${fmtInt(plan.log.length)}</div></div></div>
      <div class="bw-actions"><button class="bw-btn bw-btn--primary" data-action="eng-handoff">Download hand-over pack (.md)</button>
        ${can ? '<button class="bw-btn" data-action="eng-convert">Mark as handed over to the tender</button>' : ""}</div>
      ${locked() ? `<div class="bw-note bw-note--info">Handed over ${fmtDate(plan.converted_at)}. This engagement is now read-only.</div>` : (can ? "" : '<p class="bw-hint">You can hand over once the notice has been published.</p>')}
      <ul class="bw-detail-list" style="margin-top:12px"><li>Reflect what you learned in the specification, lots and procurement route.</li>
        <li>Make sure anything shared with suppliers during the engagement is available to every bidder.</li>
        <li>Record why you did or did not act on what you heard.</li></ul>`;
  }

  function stepperHtml(plan) {
    const bodies = [stepScope, stepSuppliers, stepNotice, stepResponses, stepHandover];
    return `<ol class="bw-steps">${plan.steps.map((s, i) => {
      const open = detail.open.has(s.n);
      return `<li class="bw-step bw-step--${s.state}"><div class="bw-step__rail"><span class="bw-step__dot" aria-hidden="true">${s.state === "done" ? "✓" : s.n}</span><span class="bw-step__line"></span></div>
        <div class="bw-step__body"><button class="bw-stepper__head" type="button" data-action="eng-step" data-step="${s.n}" aria-expanded="${open}" aria-controls="step-${s.n}"><span>${esc(s.title)}</span><small>${stepSummary(plan, s.n)}</small></button>
        ${open ? `<div class="bw-step__panel" id="step-${s.n}">${bodies[i](plan)}</div>` : ""}</div></li>`;
    }).join("")}</ol>`;
  }

  function detailHtml(plan) {
    return `<div class="bw-page"><p style="margin:0 0 10px"><a class="bw-link" href="#/engagements">← All engagements</a></p>
      <div class="bw-head"><div class="bw-head__row"><div><h1>${esc(plan.title)} ${statusBadge(plan.status)}</h1>
        <p>${esc(plan.category_label || "")}${plan.organisation ? ` · ${esc(plan.organisation)}` : ""}</p></div></div></div>
      <div class="bw-card">${stepperHtml(plan)}</div>
      ${locked() ? "" : '<div class="bw-actions"><button class="bw-btn bw-btn--danger bw-btn--sm" data-action="eng-delete">Delete this engagement</button></div>'}</div>`;
  }

  function paint() {
    const main = document.getElementById("bwMain");
    const y = main.scrollTop;
    main.innerHTML = detailHtml(detail.plan);
    main.scrollTop = y;
  }

  async function loadMissing() {
    try {
      const n = await api(`/api/market-engagement/${detail.plan.id}/notice`);
      detail.missing = n.missing;
      detail.generated = n.text;
    } catch { detail.missing = []; }
  }

  async function renderDetail(ctx) {
    ctx.main.innerHTML = `<div class="bw-page">${skeleton(5)}</div>`;
    const plan = await api(`/api/market-engagement/${ctx.parts[1]}`);
    if (!ctx.isCurrent()) return;
    detail.plan = plan;
    detail.scopeDraft = null;
    detail.noticeDraft = null;
    const stepParam = Number(ctx.params.get("step"));
    detail.open = new Set([stepParam >= 1 && stepParam <= 5 ? stepParam : (plan.steps.find((s) => s.state === "active") || { n: 5 }).n]);
    await loadMissing();
    if (!ctx.isCurrent()) return;
    paint();
  }

  async function renderEngagements(ctx) {
    if (ctx.parts[1]) return renderDetail(ctx);
    return renderList(ctx);
  }

  // ── detail actions ──────────────────────────────────────────────────────────────────────────
  async function mutate(request, { ok } = {}) {
    if (detail.busy) return null;
    detail.busy = true;
    try {
      const res = await request();
      if (ok) toast(ok);
      return res;
    } catch (err) {
      toast(err.message, true);
      return null;
    } finally {
      detail.busy = false;
    }
  }
  function applyPlan(plan) { detail.plan = plan; }
  function scopeBody() {
    const body = {};
    Object.entries(detail.scopeDraft || {}).forEach(([k, v]) => { body[k] = v === "" ? null : v; });
    const select = document.getElementById("sCategory");
    if (select) {
      const chosen = readCategory(document.getElementById("bwMain"), "sCategory");
      if (chosen.q === "") throw new Error("Enter a CPV code or some words to describe the category.");
      if (!sameCategory(chosen, detail.plan.category || {})) body.category = chosen;
    }
    return body;
  }
  async function saveScope() {
    let body;
    try { body = scopeBody(); } catch (err) { toast(err.message, true); return; }
    if (!Object.keys(body).length) { toast("Nothing to save"); return; }
    if ("title" in body && !body.title) { toast("The title cannot be empty", true); return; }
    const plan = await mutate(() => api(`/api/market-engagement/${detail.plan.id}`, { method: "PATCH", body }), { ok: "Scope saved" });
    if (plan) { applyPlan(plan); detail.scopeDraft = null; await loadMissing(); paint(); }
  }
  async function matchSuppliers() {
    const smaller = document.getElementById("smallerFirst")?.checked || false;
    const window = document.getElementById("matchWindow")?.value || "5y";
    const res = await mutate(() => api(`/api/market-engagement/${detail.plan.id}/suppliers/match`, { method: "POST", body: { smaller_first: smaller, window } }));
    if (!res) return;
    detail.plan = await api(`/api/market-engagement/${detail.plan.id}`);
    toast(res.added ? `Added ${plural(res.added, "supplier")} from ${res.basis.toLowerCase()}` : "No new suppliers to add");
    paint();
  }
  async function addSupplier() {
    const input = document.getElementById("manualSupplier");
    const name = (input.value || "").trim();
    if (name.length < 2) { toast("Enter the supplier's name", true); return; }
    const res = await mutate(() => api(`/api/market-engagement/${detail.plan.id}/suppliers`, { method: "POST", body: { name } }));
    if (!res) return;
    detail.plan = await api(`/api/market-engagement/${detail.plan.id}`);
    toast(res.added ? "Supplier added" : "That supplier is already on the list");
    paint();
  }
  async function updateSupplier(id, change) {
    const res = await mutate(() => api(`/api/market-engagement/${detail.plan.id}/suppliers/${id}`, { method: "PATCH", body: change }));
    if (!res) { paint(); return; }
    detail.plan = await api(`/api/market-engagement/${detail.plan.id}`);
    paint();
  }
  async function removeSupplier(id) {
    const res = await mutate(() => api(`/api/market-engagement/${detail.plan.id}/suppliers/${id}`, { method: "DELETE" }), { ok: "Supplier removed" });
    if (!res) return;
    detail.plan = await api(`/api/market-engagement/${detail.plan.id}`);
    paint();
  }
  function noticeText() { return document.getElementById("noticeText")?.value ?? (detail.plan.notice_text || ""); }
  async function saveNotice() {
    const text = noticeText().trim();
    if (!text) { toast("Write or generate the notice first", true); return; }
    const plan = await mutate(() => api(`/api/market-engagement/${detail.plan.id}`, { method: "PATCH", body: { notice_text: text } }), { ok: "Draft saved" });
    if (plan) { applyPlan(plan); detail.noticeDraft = null; paint(); }
  }
  async function regenerate() {
    await loadMissing();
    detail.noticeDraft = detail.generated || "";
    paint();
    toast("Draft regenerated from your scope. Save it to keep it.");
  }
  async function publish() {
    const text = noticeText().trim();
    if (text !== (detail.plan.notice_text || "").trim()) { toast("Save your changes to the notice first", true); return; }
    const url = document.getElementById("publishUrl")?.value.trim() || null;
    const plan = await mutate(() => api(`/api/market-engagement/${detail.plan.id}/publish`, { method: "POST", body: { url } }), { ok: "Marked as published" });
    if (plan) { applyPlan(plan); detail.open = new Set([4]); paint(); }
  }
  async function transition(kind, ok, nextStep) {
    const plan = await mutate(() => api(`/api/market-engagement/${detail.plan.id}/${kind}`, { method: "POST", body: {} }), { ok });
    if (plan) { applyPlan(plan); detail.open = new Set([nextStep]); paint(); }
  }
  async function addNote() {
    const input = document.getElementById("logNote");
    const message = (input.value || "").trim();
    if (!message) return;
    const res = await mutate(() => api(`/api/market-engagement/${detail.plan.id}/log`, { method: "POST", body: { message } }), { ok: "Note added" });
    if (res) { detail.plan.log = res.log; paint(); }
  }
  async function handoff() {
    const res = await mutate(() => api(`/api/market-engagement/${detail.plan.id}/handoff`));
    if (res) download(res.filename, res.markdown, "text/markdown");
  }
  function confirmDelete() {
    openModal(`<h2 id="bwModalTitle">Delete this engagement?</h2><p class="bw-card__sub">The plan, supplier list and record will be removed. This cannot be undone. Download the hand-over pack first if you want to keep them.</p>
      <div class="bw-actions bw-actions--end"><button class="bw-btn" data-action="modal-close">Keep it</button><button class="bw-btn bw-btn--danger" data-action="eng-confirm-delete">Delete</button></div>`);
  }
  async function doDelete() {
    const res = await mutate(() => api(`/api/market-engagement/${detail.plan.id}`, { method: "DELETE" }));
    closeModal();
    if (res) { toast("Engagement deleted"); navigate("#/engagements"); }
  }

  async function onClick(e) {
    const t = e.target.closest("[data-action]");
    if (!t || !t.dataset.action.startsWith("eng-")) return;
    try {
      await dispatch(t);
    } catch (err) {
      toast((err && err.message) || "Something went wrong", true);
    }
  }
  async function dispatch(t) {
    switch (t.dataset.action) {
      case "eng-new": await openNewEngagement({ category: BW.state.lastCategory }); break;
      case "eng-create": await createPlan(); break;
      case "eng-step": {
        const n = Number(t.dataset.step);
        if (detail.open.has(n)) detail.open.delete(n); else detail.open.add(n);
        paint();
        document.querySelector(`[data-action="eng-step"][data-step="${n}"]`)?.focus();
        break;
      }
      case "eng-save-scope": await saveScope(); break;
      case "eng-match": await matchSuppliers(); break;
      case "eng-add-supplier": await addSupplier(); break;
      case "eng-remove-supplier": await removeSupplier(t.dataset.id); break;
      case "eng-save-notice": await saveNotice(); break;
      case "eng-regenerate": await regenerate(); break;
      case "eng-copy-notice": toast((await copyText(noticeText())) ? "Notice copied" : "Could not copy. Select the text and copy it manually.", false); break;
      case "eng-download-notice": download(`${(detail.plan.title || "notice").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 60) || "notice"}-notice.txt`, noticeText()); break;
      case "eng-publish": await publish(); break;
      case "eng-close": await transition("close", "Engagement closed", 5); break;
      case "eng-convert": await transition("convert", "Handed over to the tender", 5); break;
      case "eng-add-note": await addNote(); break;
      case "eng-handoff": await handoff(); break;
      case "eng-delete": confirmDelete(); break;
      case "eng-confirm-delete": await doDelete(); break;
      default: break;
    }
  }

  function onChange(e) {
    const el = e.target;
    if (el.dataset && "categorySelect" in el.dataset) {
      document.getElementById(`${el.id}Wrap`).classList.toggle("hidden", el.value !== "__custom");
      return;
    }
    if (el.dataset && el.dataset.scope && detail.plan) {
      detail.scopeDraft = detail.scopeDraft || {};
      detail.scopeDraft[el.dataset.scope] = el.type === "number" && el.value !== "" ? Number(el.value) : el.value;
      if (el.dataset.scope === "engagement_type") {
        document.getElementById("dayRow")?.classList.toggle("hidden", !["supplier_day", "both"].includes(el.value));
      }
      return;
    }
    if (el.dataset && el.dataset.supplier) {
      const field = el.dataset.field;
      const value = field === "included" ? el.checked : el.value;
      updateSupplier(el.dataset.supplier, { [field]: field === "note" ? value.trim() : value });
    }
  }
  function onInput(e) {
    const el = e.target;
    if (el.id === "noticeText") detail.noticeDraft = el.value;
    else if (el.dataset && el.dataset.scope && detail.plan) {
      detail.scopeDraft = detail.scopeDraft || {};
      detail.scopeDraft[el.dataset.scope] = el.value;
    }
  }

  document.addEventListener("click", onClick);
  document.addEventListener("change", onChange);
  document.addEventListener("input", onInput);

  BW.openNewEngagement = openNewEngagement;
  BW.registerView("engagements", renderEngagements, "Market engagement");
  BW.engagement = { detail, prefillSpec, stepSummary, planCard, miniSteps, statusBadge, stepSuppliers, stepNotice, stepResponses, stepHandover, stepScope, detailHtml, categoryFields, readCategory, sameCategory };

  if (typeof module !== "undefined" && module.exports) module.exports = BW.engagement;
})();
