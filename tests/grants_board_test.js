// Run: node tests/grants_board_test.js
const assert = require("assert");

global.document = { getElementById: () => null };
const calls = [];
global.fetch = async (url) => {
  calls.push(url);
  return { ok: true, json: async () => ({ items: items, stages: [["interested", "Interested"], ["applying", "Applying"], ["submitted", "Submitted"], ["awarded", "Awarded"], ["not_funded", "Not funded"]] }) };
};
let rendered = 0;
global.window = { renderPipelineBoard: () => { rendered += 1; } };
const g = require("../web/grants-board.js");

const items = [
  { label: "a", name: 'Frontier <AI>', funder: "Innovate UK", stage: "applying", notes: 'Call "Sam"', remind: true, listed: true, ongoing: false, close_date: "2026-11-02", days_left: 5, min_award: 150000, max_award: 350000 },
  { label: "b", name: "Old grant", funder: null, stage: "interested", notes: "", remind: false, listed: false, ongoing: false, close_date: null, days_left: null },
  { label: "c", name: "Rolling", funder: "F", stage: "submitted", notes: "", remind: true, listed: true, ongoing: true, close_date: "2030-01-01", days_left: null, total_award: 3800000 },
  { label: "d", name: "Won one", funder: "F", stage: "awarded", notes: "", remind: true, listed: true, ongoing: false, close_date: "2026-12-01", days_left: 34 },
];

(async () => {
  assert.strictEqual(g.count(), 0);
  const body = { innerHTML: "", querySelectorAll: () => [] };
  g.render(body);
  assert(body.innerHTML.includes("Loading your grants"));
  await new Promise((r) => setTimeout(r, 20));
  assert.deepStrictEqual(calls, ["/api/funding/tracked"]);
  assert.strictEqual(rendered, 1, "the Pipeline page is asked to draw again once the list arrives");
  assert.strictEqual(g.count(), 4);

  g.render(body);
  const html = body.innerHTML;
  assert(html.includes("Grants board") && html.includes("Interested") && html.includes("Not funded"));
  assert(html.includes("Frontier &lt;AI&gt;") && !html.includes("<AI>"), "grant text is escaped");
  assert(html.includes("Call &quot;Sam&quot;"), "notes are escaped");
  assert(html.includes("Closes 2026-11-02 · 5 days left") && html.includes("gb-chip--soon"), "closing within 7 days is flagged");
  assert(html.includes("No longer listed") && html.includes("Rolling, no fixed deadline"));
  assert(html.includes("£150k to £350k") && html.includes("Pot £3.8m"));
  assert(/data-gb-remind="c"[^>]*disabled/.test(html), "a submitted grant has no reminder switch");
  assert(!/data-gb-remind="a"[^>]*disabled/.test(html) && /data-gb-remind="a" checked/.test(html));
  assert(html.includes("Closing ≤14 days") && /dash-stat__num">1<\/span><span class="dash-stat__label">Closing ≤14 days/.test(html), "one active grant closes within 14 days");
  assert(/dash-stat__num">4<\/span><span class="dash-stat__label">Grants tracked/.test(html));
  assert(html.includes('data-gb-stage="a"') && html.includes('<option value="applying" selected>'));

  g.reset();
  assert.strictEqual(g.count(), 0, "reset makes the next view read the list again");
  const empty = require("../web/grants-board.js");
  assert(empty.boardHtml().includes("Could not load") === false);
  console.log("grants board ok");
})().catch((e) => { console.error(e); process.exit(1); });
