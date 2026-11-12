// Phase 1 front-end helpers: alert titles (fix plan E2) and the placeholder count (G9).
// Run: node tests/bid_phase1_js_test.js
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

const src = fs.readFileSync(path.join(__dirname, "..", "web", "app.js"), "utf8");
function grab(name) {
  const start = src.indexOf(`function ${name}(`);
  assert(start >= 0, `${name} not found`);
  let depth = 0, i = src.indexOf("{", start);
  for (; i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}" && --depth === 0) break;
  }
  return src.slice(start, i + 1);
}

const ctx = { SOURCES_LABEL: { pcs: "Public Contracts Scotland", sam_gov: "SAM.gov" } };
vm.createContext(ctx);
vm.runInContext(grab("cleanAlertTitle") + "\n" + grab("countUnresolvedPlaceholdersInText"), ctx);

let n = 0;
const eq = (a, b, m) => { assert.strictEqual(a, b, m); n++; };

eq(ctx.cleanAlertTitle("Matthewsgreen Community Centre", "procontract:DN1"), "Matthewsgreen Community Centre", "real title kept");
eq(ctx.cleanAlertTitle("SAM.gov https://alpha.sam.gov/opp/123", "sam_gov:123"), "SAM.gov", "URL stripped");
eq(ctx.cleanAlertTitle("pcs:SEP564280", "pcs:SEP564280"), "A tracked tender on Public Contracts Scotland", "raw key replaced");
eq(ctx.cleanAlertTitle("View Notice", "pcs:SEP1"), "A tracked tender on Public Contracts Scotland", "link text replaced");
eq(ctx.cleanAlertTitle("", "find_tender:1"), "A tracked tender on find tender", "unknown portal label falls back to its id");

eq(ctx.countUnresolvedPlaceholdersInText("A [NEEDS INPUT: x] b [Add 1-2 case studies] c [to be inserted] [link](http://a) [1] [ ]"), 3, "same placeholders as the server");
eq(ctx.countUnresolvedPlaceholdersInText(""), 0, "empty");

console.log(`bid_phase1_js_test: ${n} checks passed`);
