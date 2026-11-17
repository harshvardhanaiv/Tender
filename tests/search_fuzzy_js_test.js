// Release 7: a title word one letter from the search word is a title match. Run: node tests/search_fuzzy_js_test.js
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
let n = 0;
function ok(c, m) { assert(c, m); n++; }

const ctx = {};
vm.createContext(ctx);
vm.runInContext(grab("oneEditApart") + "\n" + grab("fuzzyTitleWords"), ctx);

ok(ctx.oneEditApart("matthewsgreen", "mathewsgreen"), "a dropped letter");
ok(ctx.oneEditApart("plumbing", "lumbing"), "a missing first letter");
ok(ctx.oneEditApart("housing", "housnig"), "a swap");
ok(!ctx.oneEditApart("housing", "housing"), "identical words are not an edit");
ok(!ctx.oneEditApart("plumbing", "painting"), "different words");
ok(ctx.fuzzyTitleWords("Mathewsgreen Community Centre operator", ["matthewsgreen"]), "the buyer's spelling is a title match");
ok(!ctx.fuzzyTitleWords("Lumbing PPM and reactive repairs", ["gas"]), "short words are never guessed");
ok(ctx.fuzzyTitleWords("lumbing PPM and reactive repairs", ["plumbing"]), "plumbing finds lumbing");
ok(!ctx.fuzzyTitleWords("Print and Signage services", ["matthewsgreen"]), "unrelated titles do not match");
console.log(`search fuzzy: ${n} checks passed`);
