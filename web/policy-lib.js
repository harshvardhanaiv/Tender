/* Policy templates (saved into Documents) and Answer Bank starter packs. Data comes from /api/policy-templates and /api/answer-starters.
   Nothing runs the AI until the user presses Write; starter packs are free. */
(function () {
  "use strict";

  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const credits = (n) => (n > 0 ? `${n} ${n === 1 ? "credit" : "credits"}` : "free");

  function templateCards(templates) {
    return (templates || []).map((t) =>
      `<button type="button" class="pl-card" data-pl-pick="${esc(t.key)}"><strong>${esc(t.title)}</strong><span class="pl-hint">${esc(t.summary)}</span></button>`).join("");
  }

  function formHtml(t, opts) {
    const months = (opts.reviewMonths || [12]).map((m) => `<option value="${m}"${m === opts.defaultReview ? " selected" : ""}>${m === 12 ? "1 year" : m + " months"}</option>`).join("");
    const facts = (t.facts || []).map((f) =>
      `<label class="pl-field">${esc(f.label)}<input type="text" class="pl-input" data-pl-fact="${esc(f.key)}" maxlength="400" placeholder="${esc(f.hint || "")}"></label>`).join("");
    return (
      `<button type="button" class="pl-back" data-pl-back>← All policies</button>` +
      `<h3>${esc(t.title)}</h3><p class="pl-hint">${esc(t.summary)}</p>` +
      `<p class="pl-hint">Written for <strong>${esc(opts.company || "your default company profile")}</strong> from its profile and the facts below. Anything it cannot know is left as a [bracketed] gap for you to fill. It is a starting point, not legal advice.</p>` +
      facts +
      `<label class="pl-field">Approved by (name and job title)<input type="text" class="pl-input" data-pl-approved maxlength="120" placeholder="Leave blank to fill in later"></label>` +
      `<label class="pl-field">Review again in <select class="pl-input" data-pl-review>${months}</select></label>` +
      `<div class="pl-actions"><button type="button" class="btn btn--primary" data-pl-write="${esc(t.key)}">Write policy: ${esc(credits(opts.cost))}</button></div>`
    );
  }

  function resultHtml(r) {
    const d = r.document || {};
    const c = r.content || {};
    const gaps = (c.gaps || []).length ? `<div class="pl-gaps"><strong>Fill these in before you use it:</strong><ul>${c.gaps.map((g) => `<li>${esc(g)}</li>`).join("")}</ul></div>` : "";
    const secs = (c.sections || []).map((s) =>
      `<section class="pl-sec"><h4>${esc(s.heading)}</h4>${(s.paragraphs || []).map((p) => `<p>${esc(p)}</p>`).join("")}` +
      ((s.bullets || []).length ? `<ul>${s.bullets.map((b) => `<li>${esc(b)}</li>`).join("")}</ul>` : "") + `</section>`).join("");
    return (
      `<div class="pl-done"><strong>Saved to your Documents</strong> as ${esc(d.filename)}${d.company_name ? " (" + esc(d.company_name) + ")" : ""}. ` +
      `You will be reminded to review it by ${esc(d.expiry_date)}.</div>` +
      `<div class="pl-actions"><a class="btn btn--primary" href="${esc(d.download_url)}" target="_blank" rel="noopener noreferrer">Download Word file</a>` +
      `<button type="button" class="btn" data-pl-back>Write another</button></div>` + gaps + `<div class="pl-preview">${secs}</div>`
    );
  }

  function packsHtml(packs) {
    return (packs || []).map((p) =>
      `<div class="pl-pack"><div><strong>${esc(p.title)}</strong> <span class="pl-hint">${esc(p.count)} answers</span><div class="pl-hint">${esc(p.summary)}</div></div>` +
      `<button type="button" class="btn btn--primary" data-pl-pack="${esc(p.key)}">Add to my Answer Bank</button></div>`).join("") +
    `<p class="pl-hint">Free. Each answer is an editable starting point with [bracketed] gaps for facts only you know. Questions you already have are left alone.</p>`;
  }

  function guideCards(guides) {
    return (guides || []).map((g) =>
      `<button type="button" class="pl-card" data-pl-guide="${esc(g.key)}"><strong>${esc(g.title)}</strong><span class="pl-hint">${esc(g.short)}</span>` +
      `<span class="pl-tags"><span class="pl-tag">${esc(g.level)}</span>${(g.have || []).length ? '<span class="pl-tag pl-tag--have">In your Documents</span>' : ""}</span></button>`).join("");
  }

  const list = (items) => (items && items.length ? `<ul>${items.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>` : "");

  function guideHtml(g, note, policyTitles) {
    const steps = (g.steps || []).map((s) => `<li><strong>${esc(s.title)}</strong><div class="pl-hint">${esc(s.detail)}</div></li>`).join("");
    const pols = (g.policies || []).filter((k) => policyTitles && policyTitles[k]).map((k) => `<button type="button" class="btn" data-pl-policy="${esc(k)}">Write: ${esc(policyTitles[k])}</button>`).join(" ");
    const links = (g.links || []).map((l) => `<a href="${esc(l.url)}" target="_blank" rel="noopener noreferrer">${esc(l.label)} ↗</a>`).join(" · ");
    return (
      `<button type="button" class="pl-back" data-pl-back-certs>← All certificates</button><h3>${esc(g.title)}</h3>` +
      ((g.have || []).length ? `<div class="pl-done">Looks like you already have this in Documents: ${g.have.map(esc).join(", ")}. Check it is in date.</div>` : "") +
      `<p>${esc(g.what)}</p><p class="pl-hint"><strong>Why tenders ask:</strong> ${esc(g.why)}</p>` +
      `<div class="pl-facts"><div><span class="pl-hint">Who issues it</span>${esc(g.issuer)}</div><div><span class="pl-hint">How long</span>${esc(g.time)}</div>` +
      `<div><span class="pl-hint">Roughly what it costs</span>${esc(g.cost)}</div><div><span class="pl-hint">Renewal</span>${esc(g.renewal)}</div></div>` +
      `<h4>Steps</h4><ol class="pl-steps">${steps}</ol><h4>Have ready</h4>${list(g.needs)}<h4>Common mistakes</h4>${list(g.pitfalls)}` +
      (pols ? `<h4>Help with the preparation</h4><p class="pl-hint">TenderFlow can write these for you from your company profile:</p><div class="pl-actions">${pols}</div>` : "") +
      `<p class="pl-hint">When you have it, upload the certificate in <strong>My Documents</strong> and add its expiry date so you are reminded to renew.</p>` +
      (links ? `<p class="pl-hint">${links}</p>` : "") + `<p class="pl-hint">${esc(note || "")}</p>`
    );
  }

  const api = { guideCards, guideHtml, templateCards, formHtml, resultHtml, packsHtml, credits };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof document === "undefined") return;

  // ── controller ────────────────────────────────────────────────────────────────────────────────
  const ctl = { certs: null, el: null, mode: null, lib: null, packs: null, busy: false, profileId: null, company: "", done: null };

  function profileOf() {
    const sel = document.getElementById("userDocsCompanyFilter");
    const st = typeof state !== "undefined" ? state : null; // app.js keeps its state in a top-level const, not on window
    const v = ctl.mode === "policies" && sel && /^\d+$/.test(sel.value) ? sel.value : (st && (st.activeProfileId || st.defaultProfileId)) || "";
    const list = (st && st.companyProfiles) || [];
    const p = list.find((x) => String(x.id) === String(v));
    return { id: v || null, name: p ? p.name : "" };
  }

  async function api2(method, url, body) {
    const res = await fetch(url, { method, credentials: "same-origin", headers: body !== undefined ? { "Content-Type": "application/json" } : {}, body: body !== undefined ? JSON.stringify(body) : undefined });
    let data = null;
    try { data = await res.json(); } catch (e) { /* not JSON */ }
    if (res.status === 402) throw new Error("You do not have enough credits for this. Top up in Billing & credits.");
    if (!res.ok || !data || data.ok === false) throw new Error((data && (data.error || data.message)) || "Something went wrong (" + res.status + "). Try again.");
    return data;
  }

  function shell(title, inner) {
    return `<div class="profile-modal pl-page" role="document"><div class="profile-header"><div class="profile-header__left"><h2 class="profile-header__title" id="plTitle" tabindex="-1">${esc(title)}</h2></div>` +
      `<button type="button" class="profile-header__close" data-pl-close aria-label="Close">×</button></div><div class="pl-body" id="plBody">${inner}</div></div>`;
  }

  function show(title, inner) {
    ctl.el.innerHTML = shell(title, inner);
  }

  function showList() {
    ctl.done = null;
    show("Generate a policy", `<p class="pl-hint">Pick a policy. It is written for your company from its profile, saved into Documents with a review date, and can be downloaded as a Word file.</p><div class="pl-cards">${templateCards(ctl.lib.templates)}</div>`);
  }

  async function openPolicies() {
    ensure("policies");
    show("Generate a policy", `<p class="pl-hint">Loading…</p>`);
    try {
      if (!ctl.lib) ctl.lib = await api2("GET", "/api/policy-templates");
      showList();
    } catch (e) { show("Generate a policy", `<p class="pl-error" role="alert">${esc(e.message)}</p>`); }
  }

  async function openCerts() {
    ensure("certs");
    show("Get a certificate", `<p class="pl-hint">Loading…</p>`);
    try {
      ctl.certs = await api2("GET", "/api/certifications");
      showCerts();
    } catch (e) { show("Get a certificate", `<p class="pl-error" role="alert">${esc(e.message)}</p>`); }
  }

  function showCerts() {
    show("Get a certificate", `<p class="pl-hint">Step-by-step help to get the certificates and evidence tenders ask for. Free.</p><div class="pl-cards">${guideCards(ctl.certs.guides)}</div>`);
  }

  async function openPolicyForm(key) {
    try {
      if (!ctl.lib) ctl.lib = await api2("GET", "/api/policy-templates");
    } catch (e) { return; }
    const t = ctl.lib.templates.find((x) => x.key === key);
    if (t) show(t.title, formHtml(t, { company: ctl.company, cost: ctl.lib.cost, reviewMonths: ctl.lib.review_months, defaultReview: ctl.lib.default_review_months }));
  }

  async function openStarters() {
    ensure("starters");
    show("Answer Bank starter packs", `<p class="pl-hint">Loading…</p>`);
    try {
      if (!ctl.packs) ctl.packs = (await api2("GET", "/api/answer-starters")).packs;
      show("Answer Bank starter packs", `<p class="pl-hint">Standard tender questions with an editable first answer, to add to your Answer Bank in one click.</p>${packsHtml(ctl.packs)}<div id="plNote" class="pl-note" role="status"></div>`);
    } catch (e) { show("Answer Bank starter packs", `<p class="pl-error" role="alert">${esc(e.message)}</p>`); }
  }

  async function write(key, btn) {
    if (ctl.busy) return;
    const body = ctl.el.querySelector("#plBody");
    const facts = {};
    ctl.el.querySelectorAll("[data-pl-fact]").forEach((i) => { if (i.value.trim()) facts[i.getAttribute("data-pl-fact")] = i.value.trim(); });
    const payload = { profile_id: ctl.profileId, facts, approved_by: (ctl.el.querySelector("[data-pl-approved]") || {}).value || "", review_months: Number((ctl.el.querySelector("[data-pl-review]") || {}).value) || 12 };
    ctl.busy = true;
    btn.disabled = true;
    btn.textContent = "Writing… this can take up to a minute";
    try {
      const r = await api2("POST", "/api/policy-templates/" + encodeURIComponent(key) + "/generate", payload);
      ctl.done = r;
      body.innerHTML = resultHtml(r);
      if (typeof window.refreshCredits === "function") window.refreshCredits();
      if (typeof window.loadUserDocuments === "function") window.loadUserDocuments();
    } catch (e) {
      btn.disabled = false;
      btn.textContent = "Write policy: " + credits(ctl.lib.cost);
      body.insertAdjacentHTML("beforeend", `<p class="pl-error" role="alert">${esc(e.message)}</p>`);
    }
    ctl.busy = false;
  }

  async function addPack(key, btn) {
    btn.disabled = true;
    const note = ctl.el.querySelector("#plNote");
    try {
      const r = await api2("POST", "/api/answer-starters/" + encodeURIComponent(key) + "/add", { profile_id: ctl.profileId });
      if (note) note.textContent = r.added + " added" + (r.skipped ? ", " + r.skipped + " already in your bank" : "") + ". Open the Answer Bank to edit them.";
      if (typeof window.loadAnswerBank === "function") window.loadAnswerBank();
    } catch (e) {
      if (note) note.textContent = e.message;
    }
    btn.disabled = false;
  }

  function ensure(mode) {
    ctl.mode = mode;
    const p = profileOf();
    ctl.profileId = p.id;
    ctl.company = p.name;
    if (!ctl.el) {
      ctl.el = document.createElement("div");
      ctl.el.id = "policyLibModal";
      ctl.el.className = "modal-overlay hidden";
      ctl.el.setAttribute("role", "dialog");
      ctl.el.setAttribute("aria-modal", "true");
      ctl.el.setAttribute("aria-labelledby", "plTitle");
      ctl.el.addEventListener("click", (e) => {
        if (e.target === ctl.el || e.target.closest("[data-pl-close]")) { ctl.el.classList.add("hidden"); return; }
        const gd = e.target.closest("[data-pl-guide]");
        if (gd && ctl.certs) {
          const g = ctl.certs.guides.find((x) => x.key === gd.getAttribute("data-pl-guide"));
          if (g) {
            const titles = {};
            ((ctl.lib && ctl.lib.templates) || []).forEach((t) => { titles[t.key] = t.title; });
            show(g.title, guideHtml(g, ctl.certs.note, titles));
            if (!ctl.lib) api2("GET", "/api/policy-templates").then((l) => { ctl.lib = l; const t2 = {}; l.templates.forEach((t) => { t2[t.key] = t.title; }); if (ctl.el && ctl.el.querySelector(".pl-steps")) show(g.title, guideHtml(g, ctl.certs.note, t2)); }).catch(() => {});
          }
          return;
        }
        if (e.target.closest("[data-pl-back-certs]")) return showCerts();
        const pol = e.target.closest("[data-pl-policy]");
        if (pol) return openPolicyForm(pol.getAttribute("data-pl-policy"));
        const pick = e.target.closest("[data-pl-pick]");
        if (pick && ctl.lib) {
          const t = ctl.lib.templates.find((x) => x.key === pick.getAttribute("data-pl-pick"));
          if (t) show(t.title, formHtml(t, { company: ctl.company, cost: ctl.lib.cost, reviewMonths: ctl.lib.review_months, defaultReview: ctl.lib.default_review_months }));
          return;
        }
        if (e.target.closest("[data-pl-back]")) return showList();
        const w = e.target.closest("[data-pl-write]");
        if (w) return write(w.getAttribute("data-pl-write"), w);
        const pk = e.target.closest("[data-pl-pack]");
        if (pk) addPack(pk.getAttribute("data-pl-pack"), pk);
      });
      document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !ctl.el.classList.contains("hidden")) ctl.el.classList.add("hidden"); });
      document.body.appendChild(ctl.el);
    }
    ctl.el.classList.remove("hidden");
  }

  document.addEventListener("click", (e) => {
    if (e.target.closest("#btnGeneratePolicy")) openPolicies();
    else if (e.target.closest("#abStartersBtn")) openStarters();
    else if (e.target.closest("#btnCertGuides")) openCerts();
  });
  window.PolicyLib = Object.assign({ openPolicies, openStarters, openCerts }, api);
})();
