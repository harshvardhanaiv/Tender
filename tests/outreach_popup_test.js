"use strict";
/* Run: node tests/outreach_popup_test.js
   The small Outreach window (web/outreach-popup.js): its size and position, that only a plain https link is opened this
   way, that a blocked window falls back to the normal link, that "open in new tab" clicks are left alone, and that the
   panel that opened it checks Outreach again once the window closes. No browser: window and document are stubs. */
const assert = require("node:assert/strict");
const path = require("node:path");
const P = require(path.join(__dirname, "..", "web", "outreach-popup.js"));

const tests = [];
const test = (n, f) => tests.push([n, f]);

const fakeWin = (over) => Object.assign({ screen: { availWidth: 1920, availHeight: 1040 }, outerWidth: 1366, outerHeight: 768, screenX: 100, screenY: 50 }, over);

test("the window is small, and centred over the page", () => {
  const f = P.popupFeatures(fakeWin());
  assert.match(f, /width=520/);
  assert.match(f, /height=720/);
  assert.match(f, /left=523/); // 100 + (1366 - 520) / 2
  assert.match(f, /top=74/); // 50 + (768 - 720) / 2
  assert.match(f, /popup=yes/);
  assert.ok(!/noopener/.test(f), "the page must keep the handle, or it cannot tell when the window closes");
});

test("on a small screen the window shrinks to fit and is never placed off-screen", () => {
  const f = P.popupFeatures(fakeWin({ screen: { availWidth: 400, availHeight: 500 }, outerWidth: 400, outerHeight: 500, screenX: 0, screenY: 0 }));
  assert.match(f, /width=360/);
  assert.match(f, /height=420/);
  assert.match(f, /left=20/);
  const off = P.popupFeatures(fakeWin({ screenX: -3000, screenY: -3000, outerWidth: 100, outerHeight: 100 }));
  assert.match(off, /left=0,top=0/);
});

test("only a plain https link is opened in the window", () => {
  assert.equal(P.isHttps("https://outreach.aivhub.com/mailboxes"), true);
  for (const bad of ["http://outreach.aivhub.com", "javascript:alert(1)", "data:text/html,x", "https://x.example/a b", 'https://x.example/"', "", null, undefined, 5]) {
    assert.equal(P.isHttps(bad), false, String(bad));
  }
});

// a window that can be closed, a clock the test controls, and a document with one "check again" button
function rig({ blocked = false, button = true } = {}) {
  const log = { opened: [], clicked: 0, events: [], intervals: [] };
  const popup = { closed: false, focus() { log.focused = true; } };
  const win = Object.assign(fakeWin(), {
    open: (url, name, features) => { log.opened.push([url, name, features]); return blocked ? null : popup; },
    setInterval: (fn) => { log.intervals.push(fn); return log.intervals.length; },
    clearInterval: (id) => { log.cleared = id; },
    CustomEvent: class { constructor(type) { this.type = type; } },
  });
  const doc = {
    querySelector: () => (button ? { disabled: false, click: () => { log.clicked++; } } : null),
    dispatchEvent: (e) => { log.events.push(e.type); },
  };
  return { log, popup, win, doc };
}

test("a Connect link opens the named small window and the click does not also open a tab", () => {
  const { log, win, doc } = rig();
  assert.equal(P.open("https://outreach.aivhub.com/", win, doc), true);
  assert.equal(log.opened.length, 1);
  assert.equal(log.opened[0][0], "https://outreach.aivhub.com/");
  assert.equal(log.opened[0][1], "tenderflow_outreach", "a second click reuses the same window");
  assert.match(log.opened[0][2], /width=520/);
  assert.equal(log.focused, true);
});

test("when the browser blocks the window the link is left to open as a normal tab", () => {
  const { log, win, doc } = rig({ blocked: true });
  assert.equal(P.open("https://outreach.aivhub.com/", win, doc), false);
  assert.equal(log.intervals.length, 0, "nothing is watched");
});

test("a link that is not https is never opened", () => {
  const { log, win, doc } = rig();
  assert.equal(P.open("javascript:alert(1)", win, doc), false);
  assert.equal(P.open("http://insecure.example", win, doc), false);
  assert.equal(log.opened.length, 0);
});

test("closing the window makes the Growth Studio panel check again, once", () => {
  const { log, popup, win, doc } = rig();
  P.open("https://outreach.aivhub.com/", win, doc);
  const tick = log.intervals[0];
  tick();
  assert.equal(log.clicked, 0, "still open: nothing happens");
  popup.closed = true;
  tick();
  assert.equal(log.clicked, 1);
  assert.ok(log.cleared, "the watching stops");
});

test("with no Growth Studio button on screen the Email panel is told instead", () => {
  const { log, popup, win, doc } = rig({ button: false });
  P.open("https://outreach.aivhub.com/", win, doc);
  popup.closed = true;
  log.intervals[0]();
  assert.deepEqual(log.events, ["outreach:window-closed"]);
  assert.equal(log.clicked, 0);
});

test("a disabled check button (a check is already running) is not clicked; the event goes out instead", () => {
  const seen = [];
  const doc = { querySelector: () => ({ disabled: true, click: () => seen.push("clicked") }), dispatchEvent: (e) => seen.push(e.type) };
  assert.equal(P.recheck(doc), "event");
  assert.deepEqual(seen, ["outreach:window-closed"]);
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
