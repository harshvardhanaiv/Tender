"use strict";
/* Run: node tests/growth_channels_test.js
   The Growth Studio Social and Calls tabs (web/growth-channels.js): what each says while it checks and when Outreach is
   not connected; on Social, the composer, the post list and the preview / schedule / approval panel on the right, and
   when each button is available; on Calls, the form with both confirmations and the call history; what is sent to the
   server; and that data from the server is escaped. Nothing here talks to a server. */
const assert = require("node:assert/strict");
const path = require("node:path");
const C = require(path.join(__dirname, "..", "web", "growth-channels.js"));

const ready = { ready: true, mode: "test", wallet: { credits: 40, social_channels: ["linkedin", "facebook"], phone_numbers: 1 }, call_cap: 20 };
const postForm = (o) => Object.assign({ prompt: "Announce our new Leeds office opening", channels: ["linkedin"], publishAt: "", pictureOn: true, pictureNote: "", attempt: "abc12345abc12345", busy: false, error: null }, o);
const callForm = (o) => Object.assign({ buyer: "Leeds City Council", number: "0113 496 0000", goal: "Ask who handles gas servicing", scriptId: "", instructions: "", questionsText: "", business: true, tps: true, attempt: "abc12345abc12345", busy: false, error: null, lookupBusy: false, lookupNote: "" }, o);
const post = (o) => Object.assign({ id: 1, status: "needs_approval", channels: ["linkedin", "facebook"], prompt: "Announce it", caption: "Our new Leeds office opens 1 November", mode: "test",
  publish_at: "2026-11-01T09:00:00Z", image_url: "https://outreach.example/img/office.png", preview_url: "https://outreach.example/api/p/abc", published_url: null, variants: [], error: null }, o);
const social = (o) => Object.assign({ status: "ok", posts: [post()], form: postForm(), refreshing: false, selectedId: 1, edit: { time: C.toLocalInput("2026-11-01T09:00:00Z"), kind: "text", note: "", busy: false, error: null } }, o);
const model = (o) => Object.assign({ status: "ok", info: ready, social: social(), calls: { status: "ok", list: [], form: callForm(), refreshing: false, scripts: [], details: {} } }, o);
const button = (html, action, extra) => (html.match(new RegExp(`<button[^>]*data-action="${action}"${extra ? "[^>]*" + extra : ""}[^>]*>`)) || [])[0] || "";

const tests = [];
const test = (n, f) => tests.push([n, f]);

// ── both tabs ───────────────────────────────────────────────────────────────────────────────────
test("while the status loads each tab says so and shows no forms", () => {
  for (const fn of [C.socialPane, C.callsPane]) {
    assert.match(fn({ status: "loading", info: null }), /Checking Outreach/);
    assert.match(fn(undefined), /Checking Outreach/);
    assert.ok(!fn({ status: "loading", info: null }).includes("data-field"));
  }
});

test("when Outreach is not connected each tab gives the reason with the connect button and no forms", () => {
  const info = { ready: false, configured: false, problem: "Outreach is not connected. Ask your administrator to set OUTREACH_API_KEY.", connect_url: "https://outreach.example/start" };
  for (const fn of [C.socialPane, C.callsPane]) {
    const html = fn(model({ info }));
    assert.match(html, /OUTREACH_API_KEY/);
    assert.match(html, /Connect Outreach/);
    assert.ok(!html.includes("ch-post-create") && !html.includes("ch-call-make"));
  }
});

test("each tab's strip says what a test key and a live key mean for that tab", () => {
  const s = C.socialPane(model());
  assert.match(s, /Test mode/);
  assert.match(s, /Nothing is published and nothing is charged/);
  assert.match(s, /40 credits/);
  assert.match(s, /LinkedIn, Facebook/);
  assert.match(C.socialPane(model({ info: Object.assign({}, ready, { mode: "live" }) })), /approving a post publishes it/);
  const c = C.callsPane(model());
  assert.match(c, /Nobody is phoned and nothing is charged/);
  assert.ok(!c.includes("Social channels"));
  assert.match(C.callsPane(model({ info: Object.assign({}, ready, { mode: "live" }) })), /a call really rings the buyer/);
});

// ── social: the left side ───────────────────────────────────────────────────────────────────────
test("the Social tab has the composer and the post list on the left and the preview on the right", () => {
  const html = C.socialPane(model());
  assert.match(html, /gs-sp-left/);
  assert.match(html, /gs-sp-right/);
  assert.ok(html.indexOf("gs-sp-left") < html.indexOf("gs-sp-right"));
  assert.ok(html.indexOf('data-field="ch-prompt"') < html.indexOf("gs-sp-right"), "the composer is on the left");
  assert.ok(html.indexOf("gs-sp-preview") > html.indexOf("gs-sp-right"), "the preview is on the right");
  assert.ok(!html.includes("ch-call-make"), "no call form on the Social tab");
  assert.match(html, /Nothing is published until you read the preview and approve it/);
});

test("the post can be written only with a prompt and at least one channel", () => {
  assert.ok(!/disabled/.test(button(C.socialComposer(model()), "ch-post-create")));
  for (const o of [{ prompt: "short" }, { prompt: "" }, { channels: [] }, { busy: true }]) {
    assert.match(button(C.socialComposer(model({ social: social({ form: postForm(o) }) })), "ch-post-create"), /disabled/, JSON.stringify(o));
  }
  assert.match(C.socialComposer(model({ social: social({ form: postForm({ busy: true }) }) })), />Writing…</);
  assert.match(C.socialComposer(model({ social: social({ form: postForm({ error: "Not connected in Outreach: instagram." }) }) })), /role="alert">Not connected in Outreach: instagram\./);
});

test("channels come from Outreach when it lists them, and the usual ones otherwise", () => {
  const some = C.socialComposer(model());
  assert.match(some, /value="linkedin" checked/);
  assert.match(some, /value="facebook"/);
  assert.ok(!some.includes('value="instagram"'));
  const none = C.socialComposer(model({ info: { ready: true, mode: "test", wallet: { social_channels: [] } } }));
  assert.match(none, /value="instagram"/);
  assert.match(none, /did not list your connected channels/);
});

test("the post list marks the selected post and each post can be picked", () => {
  const posts = [post({ id: 1 }), post({ id: 2, status: "writing", caption: null, prompt: "Second idea" })];
  const html = C.socialHistory(model({ social: social({ posts, selectedId: 2 }) }));
  assert.match(html, /data-action="ch-post-select" data-id="1" aria-pressed="false"/);
  assert.match(html, /data-action="ch-post-select" data-id="2" aria-pressed="true"/);
  assert.match(html, /is-selected/);
  assert.match(html, /Second idea/);
  assert.match(html, /Being written/);
  assert.match(C.socialHistory(model({ social: social({ posts: [] }) })), /No posts yet/);
  assert.match(button(C.socialHistory(model({ social: social({ refreshing: true }) })), "ch-post-refresh"), /disabled/);
});

test("with nothing selected the newest post is shown on the right", () => {
  const posts = [post({ id: 7, caption: "Newest caption" }), post({ id: 3, caption: "Older caption" })];
  assert.equal(C.selectedPost({ posts, selectedId: null }).id, 7);
  assert.equal(C.selectedPost({ posts, selectedId: 3 }).id, 3);
  assert.equal(C.selectedPost({ posts, selectedId: 99 }).id, 7);
  assert.equal(C.selectedPost({ posts: [], selectedId: 1 }), null);
  assert.match(C.socialPreview(model({ social: social({ posts, selectedId: null }) })), /Newest caption/);
});

// ── social: the preview, schedule and approval panel ────────────────────────────────────────────
test("with no post the right side says what will appear there and offers nothing to press", () => {
  const html = C.socialPreview(model({ social: social({ posts: [], selectedId: null }) }));
  assert.match(html, /appears here with its picture, caption and schedule/);
  assert.ok(!html.includes("data-action"));
});

test("the preview shows the picture, the caption, the channels and Outreach's own preview link", () => {
  const html = C.socialPreview(model());
  assert.match(html, /<img src="https:\/\/outreach\.example\/img\/office\.png"/);
  assert.match(html, /referrerpolicy="no-referrer"/);
  assert.match(html, /Our new Leeds office opens 1 November/);
  assert.match(html, /LinkedIn, Facebook/);
  assert.match(html, /Ready to approve/);
  assert.match(html, /<a href="https:\/\/outreach\.example\/api\/p\/abc" target="_blank" rel="noopener noreferrer">Open Outreach(?:'|&#39;)s full preview<\/a>/);
});

test("a post still being written says so where the picture and caption will be", () => {
  const html = C.socialPreview(model({ social: social({ posts: [post({ status: "writing", image_url: null, caption: null })]}) }));
  assert.match(html, /drawing the picture/);
  assert.match(html, /caption appears here/);
  assert.match(C.socialPreview(model({ social: social({ posts: [post({ image_url: null })] }) })), /No picture for this post/);
});

test("how the post reads on each channel is shown when Outreach gives it", () => {
  const html = C.socialPreview(model({ social: social({ posts: [post({ variants: [{ channel: "linkedin", text: "Long text" }, { channel: "x", text: "Short" }] })] }) }));
  assert.match(html, /How it reads on each channel/);
  assert.match(html, /<strong>LinkedIn<\/strong><p class="gs-ch-caption">Long text/);
  assert.match(html, /<strong>X<\/strong>/);
  assert.ok(!C.socialPreview(model()).includes("How it reads on each channel"));
});

test("the schedule shows when the post goes out and lets the time be changed only once it differs", () => {
  const tm = (o) => social(Object.assign({ panel: "time" }, o));
  const html = C.socialPreview(model({ social: tm() }));
  assert.match(html, /Goes out <strong>/);
  assert.match(html, /test mode: nothing really goes out/);
  assert.match(html, /data-field="ch-edit-time"/);
  assert.match(button(html, "ch-post-time"), /disabled/, "unchanged time: nothing to save");
  assert.match(html, /sends the post back for approval/);
  const changed = C.socialPreview(model({ social: tm({ edit: { time: "2026-12-24T10:30", kind: "text", note: "", busy: false, error: null } }) }));
  assert.ok(!/disabled/.test(button(changed, "ch-post-time")));
  assert.match(button(changed, "ch-post-time"), /data-id="1"/);
  assert.match(C.socialPreview(model({ social: tm({ edit: { time: "2026-12-24T10:30", busy: "time" } }) })), />Saving…</);
  assert.match(C.socialPreview(model({ social: tm({ posts: [post({ publish_at: null })], edit: { time: "" } }) })), /No publish time set/);
});

test("times are shown and edited in local time and sent as UTC", () => {
  const iso = "2026-11-01T09:00:00Z";
  const local = C.toLocalInput(iso);
  assert.match(local, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/);
  assert.equal(new Date(C.publishIso(local)).getTime(), new Date(iso).getTime(), "local input -> the same UTC moment again");
  assert.equal(C.toLocalInput(null), "");
  assert.equal(C.toLocalInput("not a date"), "");
  assert.equal(C.whenText(null), null);
  assert.match(C.whenText(iso), /2026/);
  assert.equal(C.timeChanged(post(), { time: local }), false);
  assert.equal(C.timeChanged(post(), { time: "" }), false);
  assert.equal(C.timeChanged(post(), { time: "2030-01-01T00:00" }), true);
  assert.equal(C.timeChanged(post({ publish_at: null }), { time: "2030-01-01T00:00" }), true);
});

test("a ready post offers approve, reject and cancel, and a new time adds Approve for the new time", () => {
  const html = C.socialPreview(model());
  for (const d of ["approve", "reject", "cancel"]) assert.match(html, new RegExp(`data-decision="${d}"`));
  assert.match(html, /Approve and publish/);
  assert.ok(!html.includes("data-newtime"));
  const changed = C.socialPreview(model({ social: social({ edit: { time: "2030-01-01T00:00", kind: "text", note: "", busy: false, error: null } }) }));
  assert.match(changed, /data-decision="approve" data-newtime="1">Approve for the new time</);
  assert.match(html, /Approving is the only thing that publishes a post/);
});

test("a post being written cannot be approved or rejected, only cancelled", () => {
  const html = C.socialPreview(model({ social: social({ posts: [post({ status: "writing" })] }) }));
  assert.ok(!html.includes('data-decision="approve"') && !html.includes('data-decision="reject"'));
  assert.match(html, /data-decision="cancel"/);
  assert.match(html, /once Outreach has finished writing/);
});

test("an approved post can only be cancelled and says when it goes out", () => {
  const html = C.socialPreview(model({ social: social({ posts: [post({ status: "approved" })] }) }));
  assert.ok(!html.includes('data-decision="approve"') && !html.includes('data-decision="reject"'));
  assert.match(html, /data-decision="cancel"/);
  assert.match(html, /Approved\. Outreach publishes it/);
});

test("a finished post has no schedule editor, no rewrite and no buttons", () => {
  for (const s of ["published", "rejected", "cancelled", "failed", "expired"]) {
    const html = C.socialPreview(model({ social: social({ posts: [post({ status: s, published_url: s === "published" ? "https://www.linkedin.com/feed/update/1" : null })] }) }));
    assert.ok(!/data-action="(?!ch-post-reuse)/.test(html), s + ": only the start-a-new-post button is left");
    assert.ok(!html.includes("data-field") && !html.includes("ch-panel") && !html.includes("data-decision"), s);
    assert.match(html, /Nothing more can be done with it/);
    assert.match(button(html, "ch-post-reuse"), /data-id="1"/);
    assert.match(html, /Start a new post from this/);
  }
  assert.match(C.socialPreview(model({ social: social({ posts: [post({ status: "published", published_url: "https://www.linkedin.com/feed/update/1" })] }) })), /See the live post/);
});

test("the AI can be asked to write the text, the picture or both again, with a note", () => {
  const html = C.socialPreview(model({ social: social({ panel: "ai", edit: { time: "", kind: "picture", note: "Shorter <b>x</b>", busy: false, error: null } }) }));
  assert.match(html, /data-panel="ai"[^>]*aria-selected="true">Improve with AI</);
  assert.match(html, /<option value="picture" selected>The picture<\/option>/);
  for (const v of ["text", "picture", "both"]) assert.match(html, new RegExp(`<option value="${v}"`));
  assert.match(html, /value="Shorter &lt;b&gt;x&lt;\/b&gt;"/);
  assert.match(button(html, "ch-post-rewrite"), /data-id="1"/);
  assert.match(html, /never publishes by itself/);
  assert.match(button(C.socialPreview(model({ social: social({ panel: "ai", edit: { time: "", kind: "text", note: "", busy: "rewrite" } }) })), "ch-post-rewrite"), /disabled/);
});

test("a refused change or approval shows the reason on the preview", () => {
  assert.match(C.socialPreview(model({ social: social({ edit: { time: "", kind: "text", note: "", busy: false, error: "Choose a publish time at least 5 minutes from now." } }) })), /role="alert">Choose a publish time/);
});

test("post text from the server is escaped and only https pictures and links are used", () => {
  const html = C.socialPreview(model({ social: social({ posts: [post({ caption: "<script>alert(1)</script>", prompt: "<img src=x onerror=alert(1)>",
    image_url: "javascript:alert(1)", preview_url: "http://insecure.example/x", error: "<b>bad</b>", variants: [{ channel: "x", text: "<i>v</i>" }] })] }) }));
  assert.ok(!html.includes("<script>") && !html.includes("<img src=x") && !html.includes("<b>bad") && !html.includes("<i>v"));
  assert.ok(!html.includes("javascript:") && !html.includes("insecure.example"));
  const quoted = C.socialPreview(model({ social: social({ posts: [post({ id: 'a"b', image_url: 'https://x.example/a.png" onerror="alert(1)' })] }) }));
  assert.ok(!/onerror="alert/.test(quoted.replace(/&quot;/g, "")) || !quoted.includes('" onerror="'));
  assert.ok(!quoted.includes('id="a"b"'));
});

test("the post request carries the prompt, channels, an ISO time and the picture choice", () => {
  const body = C.postBody(postForm({ publishAt: "2026-10-20T09:00", pictureNote: " A bright office " }));
  assert.equal(body.prompt, "Announce our new Leeds office opening");
  assert.deepEqual(body.channels, ["linkedin"]);
  assert.equal(body.publish_at, new Date("2026-10-20T09:00").toISOString(), "the time typed is local; the server gets UTC");
  assert.deepEqual(body.picture, { mode: "ai", instruction: "A bright office" });
  assert.equal(body.attempt, "abc12345abc12345");
  const plain = C.postBody(postForm({ pictureKind: "none" }));
  assert.deepEqual(plain.picture, { mode: "none" });
  assert.ok(!("publish_at" in plain));
  assert.equal(C.publishIso("not a date"), undefined);
  assert.deepEqual(C.postBody(postForm({ pictureOn: false })).picture, { mode: "none" }, "the older flag still means no AI picture");
});

const PNG = "data:image/png;base64,iVBORw0KGgo=";

test("the composer offers the AI or the user's own caption, and shows the matching box", () => {
  const ai = C.socialComposer(model());
  assert.match(ai, /Let the AI write it/);
  assert.match(ai, /I&#39;ll write the caption|I'll write the caption/);
  assert.ok(ai.includes('data-field="ch-prompt"') && !ai.includes('data-field="ch-caption"'));
  assert.match(ai, /<input type="radio" name="ch-how" data-field="ch-how" value="ai" checked>/);
  const mine = C.socialComposer(model({ social: social({ form: postForm({ how: "mine", caption: "Hello <b>Leeds</b>", prompt: "" }) }) }));
  assert.ok(mine.includes('data-field="ch-caption"') && !mine.includes('data-field="ch-prompt"') && !mine.includes('data-field="ch-starter"'));
  assert.ok(mine.includes("Hello &lt;b&gt;Leeds&lt;/b&gt;") && !mine.includes("<b>Leeds"));
  assert.match(mine, /value="mine" checked/);
  assert.match(mine, /<span id="gsChCount">18<\/span> \/ 1,500/);
  assert.match(mine, /Your words are used as written/);
  assert.match(C.socialComposer(model({ social: social({ form: postForm({ how: "mine", caption: "Hello Leeds", polish: true }) }) })), /Let the AI polish it[\s\S]*improves your wording/);
});

test("a caption of the user's own needs only a few words; the AI idea needs a sentence", () => {
  const ready = (o) => !/disabled/.test(button(C.socialComposer(model({ social: social({ form: postForm(o) }) })), "ch-post-create"));
  assert.equal(ready({ how: "mine", caption: "Hi all", prompt: "" }), true);
  assert.equal(ready({ how: "mine", caption: "Hi", prompt: "A long enough idea for the AI" }), false, "the idea box is not used in this mode");
  assert.equal(ready({ how: "ai", prompt: "Hi" }), false);
  assert.equal(C.postReady(postForm({ how: "mine", caption: "Hi all", prompt: "", channels: [] })), false);
});

test("the request carries the caption (and polish) in write-it-myself mode, never the prompt", () => {
  const exact = C.postBody(postForm({ how: "mine", caption: "  Our Leeds office is open.  ", prompt: "ignored" }));
  assert.equal(exact.caption, "Our Leeds office is open.");
  assert.ok(!("prompt" in exact) && !("polish" in exact));
  assert.equal(C.postBody(postForm({ how: "mine", caption: "Our Leeds office is open.", polish: true })).polish, true);
  assert.equal(C.postBody(postForm()).prompt, "Announce our new Leeds office opening");
  assert.ok(!("caption" in C.postBody(postForm())));
});

test("the picture is the AI's, the user's own, or none; an own picture is never sent with the new post", () => {
  const html = (o) => C.socialComposer(model({ social: social({ form: postForm(o) }) }));
  assert.match(html({}), /name="ch-picture-kind"[^>]*value="ai" checked/);
  assert.ok(html({}).includes('data-field="ch-picture-note"') && !html({}).includes("ch-picture-file"));
  const own = html({ pictureKind: "mine" });
  assert.ok(own.includes('type="file"') && own.includes("image/png,image/jpeg,image/webp") && !own.includes("ch-picture-note"));
  assert.match(own, /up to 8 MB/);
  assert.match(button(own, "ch-post-create"), /disabled/, "an own picture has to be chosen first");
  const chosen = html({ pictureKind: "mine", pictureData: PNG });
  assert.ok(chosen.includes(`src="${PNG}"`) && chosen.includes("ch-picture-clear") && /Choose another/.test(chosen));
  assert.ok(!/disabled/.test(button(chosen, "ch-post-create")));
  assert.deepEqual(C.postBody(postForm({ pictureKind: "mine", pictureData: PNG })).picture, { mode: "none" });
  assert.ok(!JSON.stringify(C.postBody(postForm({ pictureKind: "mine", pictureData: PNG }))).includes("base64"));
  assert.match(html({ pictureKind: "mine", pictureError: "Use a PNG, JPG or WebP picture." }), /role="alert">Use a PNG/);
  // only a real picture data URL is ever put in an <img>
  assert.ok(!html({ pictureKind: "mine", pictureData: 'data:text/html;base64,AAAA" onerror="x' }).includes("onerror"));
  assert.ok(!html({ pictureKind: "mine", pictureData: "javascript:alert(1)" }).includes("javascript:"));
});

test("the preview lets the user edit the caption by hand and use their own picture", () => {
  const edit = (o) => Object.assign({ time: "", kind: "text", note: "", caption: null, pictureData: null, pictureError: null, busy: false, error: null }, o);
  const html = C.socialPreview(model({ social: social({ panel: "edit", edit: edit() }) }));
  assert.match(html, /data-panel="edit"[^>]*aria-selected="true">Edit</);
  assert.match(html, /<textarea id="gsChEditCaption"[^>]*data-field="ch-edit-caption"[^>]*>Our new Leeds office opens 1 November<\/textarea>/);
  assert.match(button(html, "ch-post-caption"), /disabled/, "nothing to save until the caption changes");
  assert.ok(html.includes('data-field="ch-edit-picture-file"') && !html.includes("ch-post-picture"));
  const typed = C.socialPreview(model({ social: social({ panel: "edit", edit: edit({ caption: "A better <i>caption</i>" }) }) }));
  assert.ok(!/disabled/.test(button(typed, "ch-post-caption")) && typed.includes("A better &lt;i&gt;caption&lt;/i&gt;") && !typed.includes("<i>caption"));
  for (const bad of ["", "  ", "ab", "Our new Leeds office opens 1 November", "x".repeat(3001)]) {
    assert.match(button(C.socialPreview(model({ social: social({ panel: "edit", edit: edit({ caption: bad }) }) })), "ch-post-caption"), /disabled/, JSON.stringify(bad.slice(0, 12)));
  }
  assert.match(button(C.socialPreview(model({ social: social({ panel: "edit", edit: edit({ caption: "New words here", busy: "caption" }) }) })), "ch-post-caption"), /disabled/);
  const pic = C.socialPreview(model({ social: social({ panel: "edit", edit: edit({ pictureData: PNG }) }) }));
  assert.ok(pic.includes(`src="${PNG}"`) && /data-action="ch-post-picture" data-id="1"/.test(pic) && /Use this picture/.test(pic));
});

test("while the AI is still writing, the hand edit waits (it would be overwritten); a finished post cannot be edited", () => {
  const writing = C.socialPreview(model({ social: social({ panel: "edit", posts: [post({ status: "writing" })] }) }));
  assert.match(writing, /once Outreach has finished writing/);
  assert.ok(!writing.includes("ch-post-caption") && !writing.includes("ch-edit-picture-file"));
  const done = C.socialPreview(model({ social: social({ posts: [post({ status: "published" })] }) }));
  assert.ok(!done.includes("Edit by hand") && !done.includes("ch-edit-caption"));
});

test("an open post has tabs under the caption (Approve, Edit, Improve with AI, Schedule) and shows one panel at a time", () => {
  const html = C.socialPreview(model());
  assert.deepEqual([...html.matchAll(/data-action="ch-panel" data-panel="(\w+)"/g)].map((m) => m[1]), ["approve", "edit", "ai", "time"]);
  assert.match(html, /role="tablist"/);
  assert.match(html, /data-panel="approve" aria-selected="true">Approve</, "Approve is the one open at first");
  assert.ok(html.indexOf("gs-sp-caption") < html.indexOf("gs-sp-tabs"), "the tabs sit under the caption");
  assert.ok(html.includes("ch-post-decide") && !html.includes("ch-rewrite-note") && !html.includes("ch-edit-caption") && !html.includes("ch-edit-time"));
  for (const [panel, field] of [["edit", "ch-edit-caption"], ["ai", "ch-rewrite-note"], ["time", "ch-edit-time"]]) {
    const one = C.socialPreview(model({ social: social({ panel }) }));
    assert.ok(one.includes(field) && !one.includes("ch-post-decide"), panel);
    assert.match(one, new RegExp(`data-panel="${panel}" aria-selected="true"`));
  }
  assert.equal(C.panelOf({ panel: "bogus" }), "approve");
  assert.equal(C.panelOf({ panel: "ai" }), "ai");
  assert.match(C.socialPreview(model({ social: social({ posts: [post({ status: "writing" })] }) })), /data-panel="edit"/, "the tabs are there while the AI writes, and say what waits");
  assert.match(C.socialPreview(model({ social: social({ posts: [post({ status: "approved" })] }) })), /data-panel="time"/);
});

test("the caption is shown in a box of its own, escaped", () => {
  const html = C.socialPreview(model({ social: social({ posts: [post({ caption: "Line one\n<b>two</b>" })] }) }));
  assert.match(html, /<div class="gs-sp-caption"><p class="gs-ch-caption">Line one\n&lt;b&gt;two&lt;\/b&gt;<\/p><\/div>/);
});

test("the composer's choices are segmented buttons and channel chips, each still a real radio or checkbox", () => {
  const html = C.socialComposer(model());
  assert.equal((html.match(/class="gs-seg"/g) || []).length, 2, "how to write it, and the picture");
  assert.equal((html.match(/<label class="gs-seg__opt"><input type="radio"/g) || []).length, 5);
  assert.match(html, /<div class="gs-picks"><label class="gs-pick"><input type="checkbox" data-field="ch-channel" value="linkedin" checked>/);
});

test("a finished post can start a new one: its words are the way to edit or improve them", () => {
  const html = C.socialPreview(model({ social: social({ posts: [post({ status: "cancelled", caption: "We signed a new contract <3" })] }) }));
  assert.match(html, /start a new post from it/);
  assert.match(button(html, "ch-post-reuse"), /data-id="1"/);
  assert.ok(!C.socialPreview(model({ social: social({ posts: [post({ status: "cancelled", caption: "", prompt: "" })] }) })).includes("ch-post-reuse"), "nothing to start from");
});

test("a picture is scaled down to fit, never up, keeping its shape", () => {
  assert.deepEqual(C.fitSize(4000, 2000, 1600), [1600, 800]);
  assert.deepEqual(C.fitSize(2000, 4000, 1600), [800, 1600]);
  assert.deepEqual(C.fitSize(800, 600, 1600), [800, 600]);
  assert.deepEqual(C.fitSize(0, 0, 1600), [1, 1]);
});

test("the caption box counts as changed only when it differs from the post and has real words", () => {
  const p = { caption: "Hello there" };
  assert.equal(C.captionChanged(p, { caption: null }), false);
  assert.equal(C.captionChanged(p, { caption: "Hello there  " }), false);
  assert.equal(C.captionChanged(p, { caption: "Hello again" }), true);
  assert.equal(C.captionChanged(p, { caption: "no" }), false);
  assert.equal(C.captionChanged({ caption: null }, { caption: "First words" }), true);
});

test("starter ideas fill the prompt with placeholders to complete", () => {
  for (const k of ["win", "insight", "service", "event"]) assert.match(C.STARTER_TEXT[k], /\[/);
});

// ── social: posting as the user's company ───────────────────────────────────────────────────────
const company = (o) => Object.assign({ status: "ok", profiles: [{ id: 4, name: "TVD Driveways Ltd", is_default: true, has_description: true }, { id: 9, name: "Thin Co", is_default: false, has_description: false }],
  brief: { name: "TVD Driveways Limited", about: "We lay driveways for councils.", where: "Winchester, Hampshire", website: "https://tvd.example" }, hints: [] }, o);
const withCompany = (co, formOver) => model({ social: social({ company: co, form: postForm(Object.assign({ profileId: 4 }, formOver)) }) });

test("while the companies load the composer says so", () => {
  assert.match(C.companyField(model({ social: social({ company: { status: "idle" } }) })), /Loading your companies/);
});

test("with no company set up the composer says how to add one and still allows a general post", () => {
  const html = C.companyField(withCompany({ status: "ok", profiles: [], brief: null, hints: ["No company is set up yet. Add one under Company profiles and posts are written as your company."] }, { profileId: null }));
  assert.match(html, /No company is set up yet/);
  assert.match(button(html, "ch-open-profiles"), /ch-open-profiles/);
  assert.match(html, /Set up your company/);
  assert.match(html, /still write a general post/);
  assert.ok(!html.includes('data-field="ch-company"'));
  assert.ok(!/disabled/.test(button(C.socialComposer(withCompany({ status: "ok", profiles: [], brief: null, hints: [] }, { profileId: null })), "ch-post-create")));
});

test("the user's companies are offered, the chosen one is selected and the default is marked", () => {
  const html = C.companyField(withCompany(company()));
  assert.match(html, /<option value="">No company \(a general post\)<\/option>/);
  assert.match(html, /<option value="4" selected>TVD Driveways Ltd \(default\)<\/option>/);
  assert.match(html, /<option value="9">Thin Co<\/option>/);
  const none = C.companyField(withCompany(company(), { profileId: null }));
  assert.match(none, /<option value="" selected>No company/);
  assert.match(none, /Nothing about a company is sent, so the post is general/);
  assert.ok(!none.includes("What Outreach is told"));
});

test("the panel shows exactly what Outreach is told about the company, escaped, with hints to improve the profile", () => {
  const html = C.companyField(withCompany(company({ brief: { name: "<b>Evil</b> Ltd", about: "<script>x</script> We build.", where: "Leeds", website: "https://e.example" }, hints: ["Add your website to the profile so a post can point people to it."] })));
  assert.match(html, /What Outreach is told about &lt;b&gt;Evil&lt;\/b&gt; Ltd/);
  assert.ok(!html.includes("<b>Evil") && !html.includes("<script>"));
  assert.match(html, /<strong>Based in:<\/strong> Leeds/);
  assert.match(html, /<strong>What it does:<\/strong>/);
  assert.match(html, /<strong>Website:<\/strong> https:\/\/e\.example/);
  assert.match(html, /may only use these facts/);
  assert.match(html, /Add your website to the profile/);
  assert.match(html, /Open Company profiles/);
  const bare = C.companyField(withCompany(company({ brief: { name: "Acme", about: "", where: "", website: "" } })));
  assert.ok(!bare.includes("Based in") && !bare.includes("What it does") && !bare.includes("Website:"));
});

test("the post request names the chosen company or none", () => {
  assert.equal(C.postBody(postForm({ profileId: 4 })).profile_id, 4);
  assert.equal(C.postBody(postForm({ profileId: null })).profile_id, null);
  assert.equal(C.postBody(postForm()).profile_id, null);
});

test("a post written as a company says so on the preview", () => {
  assert.match(C.socialPreview(model({ social: social({ posts: [post({ company_name: "TVD <b>Driveways</b>" })] }) })), /· as TVD &lt;b&gt;Driveways&lt;\/b&gt;/);
  assert.ok(!C.socialPreview(model({ social: social({ posts: [post({ company_name: null })] }) })).includes("· as "));
});

test("there is a starter that introduces the company", () => {
  assert.match(C.STARTER_TEXT.about, /\[company\]/);
  assert.match(C.socialComposer(model()), /Introduce our company/);
});

// ── calls ───────────────────────────────────────────────────────────────────────────────────────
test("the Calls tab has the form on the left and the call history on the right, and no post form", () => {
  const html = C.callsPane(model());
  assert.match(html, /New call/);
  assert.match(html, /Your calls/);
  assert.ok(html.indexOf("New call") < html.indexOf("Your calls"));
  assert.ok(!html.includes("ch-post-create") && !html.includes("gs-sp-grid"));
  assert.match(html, /TPS or CTPS/);
  assert.match(html, /Only landlines starting 01, 02 or 03/);
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
  assert.match(button(C.callForm(model({ calls: { status: "ok", list: [], form: callForm({ tps: false }), scripts: [], details: {} } })), "ch-call-make"), /disabled/);
  assert.ok(!/disabled/.test(button(C.callForm(model()), "ch-call-make")));
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

test("the call form says when Outreach has no phone number and offers saved scripts only when there are some", () => {
  assert.match(C.callForm(model({ info: Object.assign({}, ready, { wallet: { phone_numbers: 0 } }) })), /Your Outreach account has no phone number/);
  assert.ok(!C.callForm(model()).includes("has no phone number"));
  assert.ok(!C.callForm(model()).includes("saved script"));
  const withScripts = C.callForm(model({ calls: { status: "ok", list: [], form: callForm({ scriptId: "script_1" }), scripts: [{ id: "script_1", name: "Intro <b>" }], details: {} } }));
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

test("the call history lists calls and says what appears there when there are none", () => {
  assert.match(C.callHistory(model()), /No calls yet/);
  const calls = { status: "ok", list: [{ id: 1, status: "ringing", buyer_name: "Leeds", number_masked: "+4411****0000", mode: "test" }], form: callForm(), scripts: [], details: {} };
  assert.match(C.callHistory(model({ calls })), /Leeds/);
  assert.match(button(C.callHistory(model({ calls: Object.assign({}, calls, { refreshing: true }) })), "ch-call-refresh"), /disabled/);
});

test("a refused call shows the reason in the form", () => {
  assert.match(C.callForm(model({ calls: { status: "ok", list: [], form: callForm({ error: "That is a mobile number." }), scripts: [], details: {} } })), /role="alert">That is a mobile number\./);
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
