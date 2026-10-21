/* Answer Bank assistant: suggests a category for an uploaded document, flags answers and documents that need attention, and
   keeps a checklist of the documents most tenders ask for, marking which the bank already covers.

   Everything here is deterministic keyword and date rules, so it costs no AI tokens, answers instantly and gives the same
   result every time. Recommending documents from what similar companies keep is a different thing: it needs data pooled across
   users, so it is not done here (see docs/roadmap-feedback.md for how it could be done without exposing anyone's documents).

   Pure functions only (tests/answer_bank_assist_test.js); the page code in app.js draws the result. */
(function (root) {
  "use strict";

  const DAY = 86400000;
  // Whole-word match at the start (so "equality policy" is not "quality policy"); short codes such as "chas" or "rams" must also
  // end on a word edge, longer phrases may continue ("sustainab" finds "sustainable").
  function has(text, phrase) {
    const core = phrase.trim();
    if (!core) return false;
    const esc = core.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    return new RegExp("(?<![a-z0-9])" + esc + (core.length <= 5 ? "(?![a-z0-9])" : ""), "i").test(text);
  }
  const norm = (s) => String(s == null ? "" : s).toLowerCase().replace(/[’‘`]/g, "'").replace(/\s+/g, " ");

  // Rules for sorting a document or answer into a category. First rule with a hit wins, in this order, so
  // "public liability insurance" lands in Insurance even though it also mentions "health".
  const CATEGORY_RULES = [
    { category: "Insurance & Certificates", words: ["public liability", "employers liability", "employer's liability", "employers' liability", "professional indemnity", "insurance certificate", "certificate of insurance", "insurance schedule", "policy schedule", "iso 9001", "iso 14001", "iso 45001", "iso 27001", "cyber essentials", "chas", "safecontractor", "constructionline", "accreditation certificate", "certificate of registration"] },
    { category: "Cyber & Data Security", words: ["gdpr", "data protection", "information security", "cyber", "privacy notice", "data retention", "penetration test", "dpa 2018"] },
    { category: "Health & Safety", words: ["health and safety", "health & safety", "risk assessment", "method statement", "rams", "coshh", "riddor", "accident", "first aid", "cdm", "safeguarding"] },
    { category: "Policies & Compliance", words: ["modern slavery", "anti-bribery", "bribery", "equality", "diversity", "inclusion", "whistleblow", "code of conduct", "business continuity", "disaster recovery", "complaints procedure", "conflict of interest", "tax compliance"] },
    { category: "Environmental & Sustainability", words: ["environmental", "carbon", "net zero", "sustainab", "waste management plan", "recycling", "biodiversity", "ems "] },
    { category: "Social Value", words: ["social value", "community benefit", "apprentice", "local employment", "volunteering", "charity", "community"] },
    { category: "Pricing", words: ["pricing", "price schedule", "rate card", "day rate", "fee proposal", "quotation", "cost breakdown", "tender price"] },
    { category: "Financial Standing", words: ["annual accounts", "financial statements", "balance sheet", "turnover", "profit and loss", "bank reference", "credit rating", "audited accounts", "financial standing"] },
    { category: "Team", words: ["curriculum vitae", " cv ", "organisation chart", "organization chart", "key personnel", "team structure", "staff profile", "biography", "bios"] },
    { category: "Experience", words: ["case study", "case studies", "reference letter", "testimonial", "previous contract", "track record", "similar projects", "client reference"] },
    { category: "Quality", words: ["quality management", "quality assurance", "qms", "quality policy", "kpi", "performance monitoring", "continuous improvement"] },
    { category: "Methodology", words: ["methodology", "approach to delivery", "implementation plan", "mobilisation", "mobilization", "project plan", "work programme", "delivery plan", "transition plan"] },
    { category: "Company Overview", words: ["company profile", "about us", "company overview", "executive summary", "corporate history", "capability statement", "mission", "values"] },
  ];

  // The category for a file name and the text extracted from it. File name counts triple: people name documents well.
  function suggestCategory(fileName, text, question) {
    const name = " " + norm(fileName).replace(/[_\-.]+/g, " ") + " ";
    const body = " " + norm(String(text || "").slice(0, 6000)) + " " + norm(question) + " ";
    let best = null;
    for (const rule of CATEGORY_RULES) {
      let score = 0;
      const hits = [];
      for (const w of rule.words) {
        const inName = has(name, w), inBody = has(body, w);
        if (inName) { score += 3; hits.push(w); } else if (inBody) { score += 1; hits.push(w); }
      }
      if (score > 0 && (!best || score > best.score)) best = { category: rule.category, score, hits };
    }
    if (!best) return { category: "General", confidence: "none", reason: "" };
    return {
      category: best.category,
      confidence: best.score >= 3 ? "high" : "low",
      reason: `Mentions ${best.hits.slice(0, 3).map((h) => `“${h.trim()}”`).join(", ")}`,
    };
  }

  // ── attention flags ────────────────────────────────────────────────────────────────────────
  const MONTHS = { jan: 0, feb: 1, mar: 2, apr: 3, may: 4, jun: 5, jul: 6, aug: 7, sep: 8, oct: 9, nov: 10, dec: 11 };
  const PLACEHOLDER = /\b(tbc|tbd|xxx+|lorem ipsum|to be confirmed|to be completed)\b|\[(?:insert|add|your|company|name|date)[^\]]*\]|<insert[^>]*>/i;
  const EXPIRY_CUE = /(expir\w*|valid (?:until|to|through)|renewal(?: date)?|renews?|end(?:s)? on|period of insurance[^.]{0,30}to)\b(?:[^0-9a-z]|\b(?:on|by|at|is|date)\b){0,16}/ig;
  // A date at the start of `text`: UK day-first numeric (31/12/2025), ISO, "31 December 2025" or "December 2025" (end of that month).
  function parseDateAt(text) {
    const mk = (y, mo, d) => (mo >= 0 && mo <= 11 && d >= 1 && d <= 31 && y >= 2000 && y <= 2100 ? new Date(Date.UTC(y, mo, d)) : null);
    let m = /^(\d{1,2})[\/.\-](\d{1,2})[\/.\-](\d{2,4})/.exec(text);
    if (m) return mk(+m[3] < 100 ? +m[3] + 2000 : +m[3], +m[2] - 1, +m[1]);
    m = /^(\d{4})-(\d{1,2})-(\d{1,2})/.exec(text);
    if (m) return mk(+m[1], +m[2] - 1, +m[3]);
    m = /^(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]{3})[a-z]*\.?,?\s+(\d{4})/.exec(text);
    if (m && MONTHS[m[2]] != null) return mk(+m[3], MONTHS[m[2]], +m[1]);
    m = /^([a-z]{3})[a-z]*\.?\s+(\d{4})/.exec(text);
    if (m && MONTHS[m[1]] != null && +m[2] >= 2000 && +m[2] <= 2100) return new Date(Date.UTC(+m[2], MONTHS[m[1]] + 1, 0));
    return null;
  }

  // The earliest-looking expiry date stated in the text after a cue like "expires" or "valid until" (or null).
  function findExpiry(text) {
    const src = norm(text);
    let found = null, m;
    EXPIRY_CUE.lastIndex = 0;
    while ((m = EXPIRY_CUE.exec(src))) {
      const d = parseDateAt(src.slice(m.index + m[0].length, m.index + m[0].length + 40));
      if (d && (!found || d < found)) found = d;
    }
    return found;
  }

  const TIME_SENSITIVE = /(insurance|certificate|polic|financial|compliance|accredit|iso )/i;

  // Reasons an answer or document needs a look: [{code, label, detail}]. `item` is an Answer Bank row.
  function attentionFlags(item, now) {
    const t = (now instanceof Date ? now : new Date(now || Date.now())).getTime();
    const flags = [];
    const answer = String(item.answer || ""), file = String(item.file_content || "");
    const all = answer + " " + file;
    const expiry = findExpiry(all);
    if (expiry) {
      const days = Math.round((expiry.getTime() - t) / DAY);
      if (days < 0) flags.push({ code: "expired", label: "Expired", detail: `Says it ended on ${expiry.toISOString().slice(0, 10)}. Upload the current one.` });
      else if (days <= 60) flags.push({ code: "expiring", label: "Expires soon", detail: `Ends on ${expiry.toISOString().slice(0, 10)} (${days} days).` });
    }
    if (PLACEHOLDER.test(all)) flags.push({ code: "placeholder", label: "Needs completing", detail: "Contains a placeholder such as TBC or [insert …]." });
    const trimmed = answer.trim();
    if (!item.file_name && trimmed.length > 0 && trimmed.length < 80) flags.push({ code: "thin", label: "Very short", detail: "Under 80 characters. Tender questions usually need a fuller answer." });
    if (!trimmed && !item.file_name) flags.push({ code: "empty", label: "Empty", detail: "No answer and no document." });
    const updated = Date.parse(String(item.updated_at || "").replace(" ", "T") + (/[zZ]|[+-]\d\d:?\d\d$/.test(String(item.updated_at || "")) ? "" : "Z"));
    if (isFinite(updated)) {
      const months = (t - updated) / (DAY * 30.4);
      const limit = TIME_SENSITIVE.test(`${item.category} ${item.question} ${item.file_name || ""}`) ? 12 : 24;
      if (months > limit) flags.push({ code: "stale", label: "Out of date?", detail: `Last updated ${Math.round(months)} months ago.` });
    }
    return flags;
  }

  // ── the checklist ──────────────────────────────────────────────────────────────────────────
  const CHECKLIST = [
    { id: "pli", label: "Public liability insurance certificate", category: "Insurance & Certificates", level: "essential", why: "Asked for in almost every public tender, usually with a minimum cover.", keywords: ["public liability"] },
    { id: "eli", label: "Employers' liability insurance certificate", category: "Insurance & Certificates", level: "essential", why: "A legal requirement if you employ anyone, and a standard pass/fail check.", keywords: ["employers liability", "employer's liability", "employers' liability"] },
    { id: "pi", label: "Professional indemnity insurance", category: "Insurance & Certificates", level: "common", why: "Needed for advice, design or consultancy work.", keywords: ["professional indemnity"] },
    { id: "hs", label: "Health & safety policy", category: "Health & Safety", level: "essential", why: "Standard selection-stage evidence, often with an accident record.", keywords: ["health and safety policy", "health & safety policy", "h&s policy", "health and safety"] },
    { id: "env", label: "Environmental policy", category: "Environmental & Sustainability", level: "common", why: "Scored in most public tenders; carbon reduction plans are required above certain values.", keywords: ["environmental policy", "environmental management", "carbon reduction plan", "net zero"] },
    { id: "edi", label: "Equality, diversity & inclusion policy", category: "Policies & Compliance", level: "essential", why: "Routinely required to show compliance with the Equality Act.", keywords: ["equality", "diversity"] },
    { id: "ms", label: "Modern slavery statement", category: "Policies & Compliance", level: "essential", why: "Required of larger suppliers and asked of most.", keywords: ["modern slavery"] },
    { id: "gdpr", label: "Data protection / GDPR policy", category: "Cyber & Data Security", level: "essential", why: "Needed whenever you handle personal data for a buyer.", keywords: ["gdpr", "data protection"] },
    { id: "qms", label: "Quality management policy or ISO 9001", category: "Quality", level: "common", why: "Evidence for the quality section.", keywords: ["iso 9001", "quality management", "quality policy", "quality assurance"] },
    { id: "bcp", label: "Business continuity plan", category: "Policies & Compliance", level: "common", why: "Buyers ask how service continues if something goes wrong.", keywords: ["business continuity", "disaster recovery"] },
    { id: "fin", label: "Financial standing: last 2 to 3 years' accounts", category: "Financial Standing", level: "essential", why: "Used to test turnover against contract value.", keywords: ["annual accounts", "financial statements", "audited accounts", "balance sheet", "financial standing", "turnover"] },
    { id: "ref", label: "Case studies and client references", category: "Experience", level: "essential", why: "Past performance is scored in nearly every tender.", keywords: ["case study", "case studies", "reference letter", "testimonial", "client reference", "similar projects"] },
    { id: "sv", label: "Social value commitments", category: "Social Value", level: "common", why: "Public bodies must weigh social value; a ready answer saves hours.", keywords: ["social value", "community benefit", "apprenticeship"] },
    { id: "abc", label: "Anti-bribery & corruption policy", category: "Policies & Compliance", level: "common", why: "A standard integrity declaration.", keywords: ["bribery", "corruption", "code of conduct"] },
    { id: "team", label: "Organisation chart and key staff CVs", category: "Team", level: "common", why: "Shows who will deliver the work.", keywords: ["organisation chart", "organization chart", "key personnel", "curriculum vitae", " cv "] },
    { id: "ce", label: "Cyber Essentials certificate", category: "Insurance & Certificates", level: "common", why: "Required by central government for contracts handling personal or sensitive data.", keywords: ["cyber essentials"] },
  ];

  // Extra documents for the kind of work the user tenders for, spotted from words in their profile and tracked tenders.
  const SECTOR_ADDONS = [
    { sector: "Construction and building works", words: ["construction", "building works", "refurbish", "groundwork", "civil engineering", "roofing", "scaffold", "demolition"],
      items: [
        { id: "chas", label: "CHAS, SafeContractor or Constructionline accreditation", category: "Insurance & Certificates", level: "essential", why: "Pre-qualification for most construction tenders.", keywords: ["chas", "safecontractor", "constructionline", "safe contractor"] },
        { id: "rams", label: "Sample risk assessment and method statement (RAMS)", category: "Health & Safety", level: "common", why: "Buyers ask to see how you plan work safely.", keywords: ["method statement", "risk assessment", "rams"] },
        { id: "iso45", label: "ISO 45001 (occupational health and safety)", category: "Insurance & Certificates", level: "common", why: "Strengthens the health and safety score.", keywords: ["iso 45001"] },
      ] },
    { sector: "IT and software", words: ["software", "cloud", "cyber", "it services", "digital", "hosting", "saas", "data platform"],
      items: [
        { id: "iso27", label: "ISO 27001 (information security)", category: "Insurance & Certificates", level: "common", why: "Frequently required for IT and data contracts.", keywords: ["iso 27001"] },
        { id: "cep", label: "Cyber Essentials Plus certificate", category: "Insurance & Certificates", level: "common", why: "Higher assurance than Cyber Essentials, needed for some government work.", keywords: ["cyber essentials plus"] },
        { id: "infosec", label: "Information security policy", category: "Cyber & Data Security", level: "essential", why: "Standard in IT tenders.", keywords: ["information security"] },
      ] },
    { sector: "Care, health and support services", words: ["care", "support services", "supported living", "health services", "nursing", "domiciliary", "mental health"],
      items: [
        { id: "cqc", label: "CQC registration and latest inspection report", category: "Quality", level: "essential", why: "Mandatory evidence for regulated care.", keywords: ["cqc", "care quality commission"] },
        { id: "safe", label: "Safeguarding policy", category: "Health & Safety", level: "essential", why: "Required wherever you work with vulnerable people.", keywords: ["safeguarding"] },
      ] },
    { sector: "Cleaning and facilities", words: ["cleaning", "facilities management", "janitorial", "grounds maintenance", "waste collection"],
      items: [
        { id: "coshh", label: "COSHH assessments", category: "Health & Safety", level: "common", why: "Shows safe handling of cleaning and chemical products.", keywords: ["coshh"] },
        { id: "iso14", label: "ISO 14001 (environmental management)", category: "Insurance & Certificates", level: "common", why: "Frequently scored for waste, grounds and cleaning contracts.", keywords: ["iso 14001"] },
      ] },
    { sector: "Transport and logistics", words: ["transport", "logistics", "haulage", "fleet", "courier", "passenger"],
      items: [
        { id: "oper", label: "Operator's licence and fleet safety records", category: "Insurance & Certificates", level: "essential", why: "A legal requirement for most haulage and passenger work.", keywords: ["operator's licence", "operator licence", "operators licence", "fleet safety"] },
      ] },
  ];

  function detectSectors(text) {
    const src = norm(text);
    return SECTOR_ADDONS.filter((s) => s.words.some((w) => has(src, w)));
  }

  function itemHaystack(item) {
    return norm(`${item.category || ""} ${item.question || ""} ${item.file_name || ""} ${String(item.answer || "").slice(0, 3000)} ${String(item.file_content || "").slice(0, 1500)}`);
  }

  // Each checklist entry with a status: "covered", "attention" (covered, but a matching answer is flagged) or "missing".
  function checklistStatus(items, opts) {
    opts = opts || {};
    const sectors = detectSectors(opts.context || "");
    const entries = CHECKLIST.concat(...sectors.map((s) => s.items.map((i) => ({ ...i, sector: s.sector }))));
    const hay = (items || []).map((it) => ({ it, h: itemHaystack(it) }));
    return entries.map((e) => {
      const matches = hay.filter(({ h }) => e.keywords.some((k) => has(h, k)));
      const flags = matches.flatMap(({ it }) => attentionFlags(it, opts.now).map((f) => ({ ...f, itemId: it.id })));
      const real = flags.filter((f) => f.code !== "thin" && f.code !== "stale");
      const status = !matches.length ? "missing" : real.length ? "attention" : "covered";
      return { ...e, status, matches: matches.map(({ it }) => it.id), flags };
    });
  }

  function summary(statuses) {
    const total = statuses.length;
    const covered = statuses.filter((s) => s.status !== "missing").length;
    return { total, covered, missing: total - covered, attention: statuses.filter((s) => s.status === "attention").length,
      essentialMissing: statuses.filter((s) => s.status === "missing" && s.level === "essential").length };
  }

  const api = { suggestCategory, findExpiry, attentionFlags, checklistStatus, summary, detectSectors, CHECKLIST, SECTOR_ADDONS, CATEGORY_RULES };
  root.AnswerBankAssist = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
