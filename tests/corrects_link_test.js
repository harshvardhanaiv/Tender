"use strict";
/* Run: node tests/corrects_link_test.js
   A Find a Tender corrigendum names the notice it changes (corrects_notice_id). The Status cell and the result card show "Corrects <id>" as a link to
   that notice, and only ever link to a notice page on find-tender.service.gov.uk. Cuts the real functions out of web/app.js. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const lines = fs.readFileSync(path.join(__dirname, "..", "web", "app.js"), "utf8").replace(/\r\n/g, "\n").split("\n");
function cut(first, lastPattern) {
  const start = lines.findIndex((l) => l.startsWith(first));
  assert.ok(start >= 0, `app.js has no line starting ${first}`);
  let end = start;
  while (!lastPattern.test(lines[end])) end += 1;
  return lines.slice(start, end + 1).join("\n");
}
const ctx = vm.createContext({ esc: (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;") });
vm.runInContext([cut("function correctsNotice(", /^}/), cut("function correctsLinkHtml(", /^}/), "this.correctsNotice = correctsNotice; this.correctsLinkHtml = correctsLinkHtml;"].join("\n"), ctx);

const tests = [];
const test = (n, f) => tests.push([n, f]);

test("a row that names the original notice gives a link to that notice", () => {
  const c = ctx.correctsNotice({ corrects_notice_id: "025108-2024" });
  assert.equal(c.id, "025108-2024");
  assert.equal(c.url, "https://www.find-tender.service.gov.uk/Notice/025108-2024");
  const html = ctx.correctsLinkHtml({ corrects_notice_id: "025108-2024" });
  assert.match(html, /Corrects 025108-2024/);
  assert.match(html, /href="https:\/\/www\.find-tender\.service\.gov\.uk\/Notice\/025108-2024"/);
  assert.match(html, /rel="noreferrer noopener"/);
});

test("a row without one, or with something that is not a notice id, gives nothing", () => {
  assert.equal(ctx.correctsNotice({}), null);
  assert.equal(ctx.correctsNotice(null), null);
  assert.equal(ctx.correctsLinkHtml({ corrects_notice_id: "" }), "");
  assert.equal(ctx.correctsLinkHtml({ corrects_notice_id: "javascript:alert(1)" }), "");
  assert.equal(ctx.correctsLinkHtml({ corrects_notice_id: '025108-2024"><script>' }), "");
});

test("the link is built from the id, never from the row's own corrects_url", () => {
  const html = ctx.correctsLinkHtml({ corrects_notice_id: "025108-2024", corrects_url: "https://evil.example/x" });
  assert.ok(!html.includes("evil.example"));
});

test("the table cell and the card both print it", () => {
  const src = lines.join("\n");
  assert.ok(src.includes('class="td--corrects"'), "the Status cell does not show the link");
  assert.ok(src.includes("correctsLinkHtml(row) ? ` · ${correctsLinkHtml(row)}`"), "the result card does not show the link");
});

let failed = 0;
for (const [name, fn] of tests) {
  try { fn(); console.log(`ok    ${name}`); } catch (err) { failed += 1; console.log(`FAIL  ${name}`); console.log(err && err.stack ? err.stack : err); }
}
console.log(`\n${tests.length - failed} of ${tests.length} passed`);
process.exitCode = failed ? 1 : 0;
