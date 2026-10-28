// Run: node tests/policy_lib_view_test.js
const assert = require("assert");
const f = require("../web/policy-lib.js");

const cards = f.templateCards([{ key: "anti_bribery", title: "Anti-bribery <b>", summary: "Asked in most tenders" }]);
assert(!cards.includes("<b>") && cards.includes('data-pl-pick="anti_bribery"') && cards.includes("Asked in most tenders"));

const t = { key: "health_safety", title: "Health and safety policy", summary: "s", facts: [{ key: "responsible", label: "Who is responsible", hint: "Name", required: false }, { key: "employees", label: "Number of employees", hint: "e.g. 24" }] };
const form = f.formHtml(t, { company: "Acme <i>Ltd", cost: 3, reviewMonths: [6, 12, 24], defaultReview: 12 });
assert(!form.includes("<i>") && form.includes("Acme &lt;i&gt;Ltd") && form.includes('data-pl-fact="employees"') && form.includes('data-pl-fact="responsible"'));
assert(form.includes('data-pl-write="health_safety"') && form.includes("Write policy: 3 credits") && form.includes('<option value="12" selected>1 year</option>') && form.includes("6 months"));
assert(form.includes("not legal advice") && form.includes("data-pl-approved"));
assert(f.formHtml(t, { company: "", cost: 0, reviewMonths: [12], defaultReview: 12 }).includes("Write policy: free"));
assert.strictEqual(f.credits(1), "1 credit");

const done = f.resultHtml({
  document: { filename: "Acme_Policy_v1.docx", company_name: "Acme", expiry_date: "2027-10-28", download_url: "/api/company-profiles/1/documents/2/download" },
  content: { sections: [{ heading: "Purpose <x>", paragraphs: ["We do not tolerate bribery."], bullets: ["Employees"] }], gaps: ["name of the person responsible"] },
});
assert(done.includes("Saved to your Documents") && done.includes("review it by 2027-10-28") && done.includes('href="/api/company-profiles/1/documents/2/download"'));
assert(!done.includes("<x>") && done.includes("Fill these in before you use it") && done.includes("name of the person responsible") && done.includes("<li>Employees</li>"));
assert(!f.resultHtml({ document: {}, content: { sections: [], gaps: [] } }).includes("Fill these in"));

const packs = f.packsHtml([{ key: "general", title: "General starter pack", summary: "The usual questions", count: 14 }]);
assert(packs.includes('data-pl-pack="general"') && packs.includes("14 answers") && packs.includes("Free."));
console.log("policy lib view ok");

// certificate guides
const g = { key: "iso9001", title: "ISO 9001 <b>", short: "Quality", level: "asked in most tenders", have: ["ACME_ISO9001.pdf"], what: "w", why: "y", issuer: "UKAS body", time: "3 to 9 months", cost: "low thousands", renewal: "3 years",
  steps: [{ title: "Decide the scope", detail: "d" }], needs: ["A named owner"], pitfalls: ["Templates"], policies: ["complaints", "missing"], links: [{ label: "Overview", url: "https://www.iso.org/x" }] };
const gc = f.guideCards([g]);
assert(!gc.includes("<b>") && gc.includes('data-pl-guide="iso9001"') && gc.includes("In your Documents") && !f.guideCards([{ ...g, have: [] }]).includes("In your Documents"));
const gh = f.guideHtml(g, "Get quotes", { complaints: "Complaints policy" });
assert(!gh.includes("<b>") && gh.includes("already have this in Documents: ACME_ISO9001.pdf") && gh.includes("Decide the scope") && gh.includes("A named owner"));
assert(gh.includes('data-pl-policy="complaints"') && !gh.includes('data-pl-policy="missing"') && gh.includes('href="https://www.iso.org/x"') && gh.includes("Get quotes") && gh.includes("upload the certificate"));
assert(!f.guideHtml({ ...g, have: [], policies: [] }, "", {}).includes("Help with the preparation") && f.guideHtml({ ...g, have: [] }, "", {}).includes("pl-back") );
console.log("certificate guides ok");
