"use strict";
/* Run: node tests/growth_channels_test.js
   The Growth Studio "Social & calls" tab (web/growth-channels.js): what it says while it checks and when Outreach is not
   connected, when each button is available, that a post can only be approved when it is ready, that a call needs both
   confirmations, what is sent to the server, and that data from the server is escaped. Nothing here talks to a server. */
const assert = require("node:assert/strict");
const path = require("node:path");
const C = require(path.join(__dirname, "..", "web", "growth-channels.js"));

const ready = { ready: true, mode: "test", wallet: { credits: 40, social_channels: ["linkedin", "facebook"], phone_numbers: 1 }, call_cap: 20 };
const postForm = (o) => Object.assign({ prompt: "Announce our new Leeds office opening", channels: ["linkedin"], publishAt: "", pictureOn: true, pictureNote: "", attempt: "abc12345abc12345", busy: false, error: null }, o);
const callForm = (o) => Object.assign({ buyer: "Leeds City Council", number: "0113 496 0000", goal: "Ask who handles gas servicing", scriptId: "", instructions: "", questionsText: "", business: true, tps: true, attempt: "abc12345abc12345", busy: false, error: null, lookupBusy: false, lookupNote: "" }, o);
const model = (o) => Object.assign({ status: "ok", info: ready, social: { status: "ok", posts: [], form: postForm(), refreshing: false }, calls: { status: "ok", list: [], form: callForm(), refreshing: false, scripts: [], details: {} } }, o);
const button = (html, action, extra) => (html.match(new RegExp(`<button[^>]*data-action="${action}"${extra ? "[^>]*" + extra : ""}[^>]*>`)) || [])[0] || "";

const tests = [];
const test = (n, f) => tests.push([n, f]);

test("while the status loads it says so and shows no forms", () => {
  assert.match(C.channelsPane({ status: "loading", info: null }), /Checking Outreach/);
  assert.match(C.channelsPane(undefined), /Checking Outreach/);
  assert.ok(!C.channelsPane({ status: "loading", info: null }).includes("data-field"));
});

test("when Outreach is not connected the reason is shown with the connect button and no forms", () => {
  const html = C.channelsPane(model({ info: { ready: false, configured: false, problem: "Outreach is not connected. Ask your administrator to set OUTREACH_API_KEY.", connect_url: "https://outreach.example/start" } }));
  assert.match(html, /OUTREACH_API_KEY/);
  assert.match(html, /Connect Outreach/);
  assert.ok(!html.includes("ch-post-create") && !html.includes("ch-call-make"));
});

test("a connected test key says nothing real is published or dialled; a live key says it is real", () => {
  const test_ = C.channelsPane(model());
  assert.match(test_, /Test mode/);
  assert.match(test_, /Nothing is published, nobody is phoned/);
  assert.match(test_, /40 credits/);
  assert.match(test_, /LinkedIn, Facebook/);
  const live = C.channelsPane(model({ info: Object.assign({}, ready, { mode: "live" }) }));
  assert.match(live, /Live key/);
  assert.match(live, /approving a post publishes it, and a call really rings the buyer/);
});

test("both cards explain the rule that matters before the first click", () => {
  const html = C.channelsPane(model());
  assert.match(html, /Nothing is published until you read the preview and approve it here/);
  assert.match(html, /TPS or CTPS/);
  assert.match(html, /Only landlines starting 01, 02 or 03/);
});

test("the post can be written only with a prompt and at least one channel", () => {
  assert.ok(!/disabled/.test(button(C.socialCard(model()), "ch-post-create")));
  for (const o of [{ prompt: "short" }, { prompt: "" }, { channels: [] }, { busy: true }]) {
    assert.match(button(C.socialCard(model({ social: { status: "ok", posts: [], form: postForm(o) } })), "ch-post-create"), /disabled/, JSON.stringify(o));
  }
  assert.match(C.socialCard(model({ social: { status: "ok", posts: [], form: postForm({ busy: true }) } })), />Writing…</);
});

test("channels come from Outreach when it lists them, and the usual ones otherwise", () => {
  const some = C.socialCard(model());
  assert.match(some, /value="linkedin" checked/);
  assert.match(some, /value="facebook"/);
  assert.ok(!some.includes('value="instagram"'));
  const none = C.socialCard(model({ info: { ready: true, mode: "test", wallet: { social_channels: [] } } }));
  assert.match(none, /value="instagram"/);
  assert.match(none, /did not list your connected channels/);
});

test("a post being written has no approve button; a ready one has approve, reject and cancel", () => {
  const writing = C.postRow({ id: 1, status: "writing", channels: ["linkedin"], prompt: "x", caption: null });
  assert.match(writing, /Being written/);
  assert.ok(!writing.includes('data-decision="approve"') && !writing.includes('data-decision="reject"'));
  assert.match(writing, /data-decision="cancel"/);
  const ready_ = C.postRow({ id: 2, status: "needs_approval", channels: ["linkedin", "facebook"], prompt: "x", caption: "Hello" });
  assert.match(ready_, /Ready to approve/);
  for (const d of ["approve", "reject", "cancel"]) assert.match(ready_, new RegExp(`data-decision="${d}"`));
  assert.match(ready_, /Approve and publish/);
  assert.match(ready_, /LinkedIn, Facebook/);
});

test("a finished post has no buttons and an approved one can only be cancelled", () => {
  for (const s of ["published", "rejected", "cancelled", "failed", "expired"]) {
    assert.ok(!C.postRow({ id: 3, status: s, channels: [], prompt: "x" }).includes("data-action"), s);
  }
  const approved = C.postRow({ id: 4, status: "approved", channels: ["x"], prompt: "x", caption: "c" });
  assert.ok(!approved.includes('data-decision="approve"') && /data-decision="cancel"/.test(approved));
});

test("post text from the server is escaped and only https links are made", () => {
  const html = C.postRow({ id: 5, status: "needs_approval", channels: ["linkedin"], prompt: "<img src=x onerror=alert(1)>", caption: "<script>alert(1)</script>",
    preview_url: "javascript:alert(1)", published_url: "http://insecure.example/x", error: "<b>bad</b>" });
  assert.ok(!html.includes("<script>") && !html.includes("<img src") && !html.includes("<b>bad"));
  assert.ok(!html.includes("javascript:") && !html.includes("insecure.example"));
  const ok = C.postRow({ id: 6, status: "needs_approval", channels: [], prompt: "x", preview_url: "https://outreach.example/p/abc" });
  assert.match(ok, /<a href="https:\/\/outreach\.example\/p\/abc" target="_blank" rel="noopener noreferrer">Open the preview<\/a>/);
});

test("the post request carries the prompt, channels, an ISO time and the picture choice", () => {
  const body = C.postBody(postForm({ publishAt: "2026-10-20T09:00", pictureNote: " A bright office " }));
  assert.equal(body.prompt, "Announce our new Leeds office opening");
  assert.deepEqual(body.channels, ["linkedin"]);
  assert.equal(body.publish_at, new Date("2026-10-20T09:00").toISOString(), "the time typed is local; the server gets UTC");
  assert.deepEqual(body.picture, { mode: "ai", instruction: "A bright office" });
  assert.equal(body.attempt, "abc12345abc12345");
  const plain = C.postBody(postForm({ pictureOn: false }));
  assert.deepEqual(plain.picture, { mode: "none" });
  assert.ok(!("publish_at" in plain));
  assert.equal(C.publishIso("not a date"), undefined);
});

test("a call needs the buyer, a number, a goal or script, and BOTH confirmations", () => {
  assert.equal(C.callReady(callForm()), true);
  assert.equal(C.callReady(callForm({ business: false })), false);
  assert.equal(C.callReady(callForm({ tps: false })), false);
  assert.equal(C.callReady(callForm({ business: "yes" })), false, "only a real tick counts");
  assert.equal(C.callReady(callForm({ buyer: " " })), false);
  assert.equal(C.callReady(callForm({ number: "" })), false);
  assert.equal(C.callReady(callForm({ goal: "" })), false);
  assert.equal(C.callReady(callForm({ goal: "", scriptId: "script_1" })), true);
  assert.equal(C.callReady(callForm({ busy: true })), false);
  assert.match(button(C.callCard(model({ calls: { status: "ok", list: [], form: callForm({ tps: false }), scripts: [], details: {} } })), "ch-call-make"), /disabled/);
  assert.ok(!/disabled/.test(button(C.callCard(model()), "ch-call-make")));
});

test("the call request is one number with the confirmations and a confirm flag", () => {
  const body = C.callBody(callForm({ questionsText: "How many sites?\n\n  Which system?  ", instructions: " Be brief. " }));
  assert.deepEqual(body.questions, ["How many sites?", "Which system?"]);
  assert.equal(body.instructions, "Be brief.");
  assert.equal(body.business_line, true);
  assert.equal(body.tps_checked, true);
  assert.equal(body.confirm, true);
  assert.equal(body.number, "0113 496 0000");
  assert.equal(body.script_id, undefined);
});

test("a call card says when Outreach has no phone number and offers saved scripts only when there are some", () => {
  assert.match(C.callCard(model({ info: Object.assign({}, ready, { wallet: { phone_numbers: 0 } }) })), /No phone number is connected in Outreach/);
  assert.ok(!C.callCard(model()).includes("No phone number is connected"));
  assert.ok(!C.callCard(model()).includes("saved script"));
  const withScripts = C.callCard(model({ calls: { status: "ok", list: [], form: callForm({ scriptId: "script_1" }), scripts: [{ id: "script_1", name: "Intro <b>" }], details: {} } }));
  assert.match(withScripts, /<option value="script_1" selected>Intro &lt;b&gt;<\/option>/);
});

test("a call row shows the masked number, never the full one, and End call only while it is live", () => {
  const live = C.callRow({ id: 1, status: "ringing", buyer_name: "Leeds <b>City</b>", number_masked: "+4411****0000", mode: "test" });
  assert.match(live, /\+4411\*\*\*\*0000/);
  assert.match(live, /Ringing/);
  assert.match(live, /data-action="ch-call-end"/);
  assert.ok(!live.includes("<b>City"));
  const done = C.callRow({ id: 2, status: "completed", buyer_name: "A", number_masked: "+4411****0000", outcome: "meeting_booked", meeting_booked: true, duration_seconds: 192 });
  assert.ok(!done.includes("ch-call-end"));
  assert.match(done, /Completed/);
  assert.match(done, /Meeting booked/);
  assert.match(done, /3m 12s/);
  assert.match(C.callRow({ id: 3, status: "no_answer", buyer_name: "A" }), /No answer/);
});

test("the answers and transcript of a call are escaped and the transcript is collapsed", () => {
  const html = C.callRow({ id: 1, status: "completed", buyer_name: "A" }, {
    answers: [{ key: "current_system", answer: "<i>Acuity</i>" }], transcript: [{ who: "ai", text: "Hello <script>x</script>" }], has_recording: true });
  assert.match(html, /current system/);
  assert.ok(!html.includes("<i>Acuity") && !html.includes("<script>"));
  assert.match(html, /<details class="gs-notes"><summary>Transcript<\/summary>/);
  assert.match(html, /recording is available in Outreach/);
  assert.match(C.answersHtml({ answers: [], transcript: [] }), /No answers or transcript yet/);
});

test("a refused post or call shows the reason in the form", () => {
  assert.match(C.socialCard(model({ social: { status: "ok", posts: [], form: postForm({ error: "Not connected in Outreach: instagram." }) } })), /role="alert">Not connected in Outreach: instagram\./);
  assert.match(C.callCard(model({ calls: { status: "ok", list: [], form: callForm({ error: "That is a mobile number." }), scripts: [], details: {} } })), /role="alert">That is a mobile number\./);
});

test("starter ideas fill the prompt with placeholders to complete", () => {
  for (const k of ["win", "insight", "service", "event"]) assert.match(C.STARTER_TEXT[k], /\[/);
});

let failed = 0;
for (const [name, fn] of tests) {
  try {
    fn();
    console.log("ok    " + name);
  } catch (err) {
    failed++;
    console.log("FAIL  " + name + "\n" + err.stack);
  }
}
console.log(`\n${tests.length - failed} of ${tests.length} passed`);
process.exit(failed ? 1 : 0);
