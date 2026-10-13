"use strict";
/* Run: node tests/session_expiry_test.js
   D06: when the session ends mid-search or mid-page the server answers 401 ({"reason": "session_expired"} after the
   idle timeout). The page used to jump to /login.html with no explanation, and a stale CSRF token (session renewed in
   another tab) made every save fail silently with a 403. The fetch wrapper now sends the reader to sign in once with a
   note the sign-in page shows, and renews the CSRF token and repeats a write once when the 403 is "Invalid CSRF token". */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const appSrc = fs.readFileSync(path.join(__dirname, "..", "web", "app.js"), "utf8").replace(/\r\n/g, "\n");
const startIdx = appSrc.indexOf("// Global fetch interceptor");
const endIdx = appSrc.indexOf("async function showCreditsModal(");
assert.ok(startIdx === 0 && endIdx > startIdx, "the fetch interceptor block moved");
const block = appSrc.slice(startIdx, endIdx);

function resp(status, body) {
  return {
    status, ok: status >= 200 && status < 300,
    clone() { return this; },
    async json() { if (body === undefined) throw new Error("not json"); return body; },
  };
}

function makePage(handler) {
  const calls = [];
  const store = {};
  const hrefs = [];
  const win = {
    location: { set href(v) { hrefs.push(v); }, get href() { return hrefs[hrefs.length - 1] || ""; } },
    fetch: async (url, opts = {}) => { calls.push({ url, opts }); return handler(url, opts, calls.length); },
  };
  const sandbox = {
    window: win, console,
    sessionStorage: { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } },
    localStorage: { getItem: () => null },
    showCreditsModal() {},
  };
  vm.createContext(sandbox);
  vm.runInContext(block + "\nthis.wrapped = window.fetch;", sandbox);
  return { fetch: sandbox.wrapped, calls, store, hrefs };
}

const tests = [];
const test = (n, f) => tests.push([n, f]);

test("an idle-timeout 401 sends the reader to sign in once, with a note saying the session expired", async () => {
  const page = makePage(() => resp(401, { error: "Unauthorized", reason: "session_expired" }));
  await page.fetch("/api/search?q=software");
  await page.fetch("/api/me"); // a second request that was already in flight
  assert.deepEqual(page.hrefs, ["/login.html?expired=1"], "one redirect, to the sign-in page, flagged as expired");
  assert.equal(page.store.tf_login_notice, "expired");
});

test("a 401 without a reason (signed out elsewhere) still redirects, with the plainer note", async () => {
  const page = makePage(() => resp(401, undefined));
  await page.fetch("/api/pipeline");
  assert.deepEqual(page.hrefs, ["/login.html?expired=1"]);
  assert.equal(page.store.tf_login_notice, "signin");
});

test("a stale CSRF token is renewed once and the write is repeated with the new token", async () => {
  const page = makePage((url, opts, n) => {
    if (url === "/api/me") return resp(200, { csrf_token: "fresh-token" });
    if (opts.headers && opts.headers["X-CSRF-Token"] === "fresh-token") return resp(200, { ok: true });
    return resp(403, { error: "Invalid CSRF token" });
  });
  const r = await page.fetch("/api/recent-searches", { method: "POST", body: "{}" });
  assert.equal(r.status, 200);
  assert.equal(page.calls.length, 3, "write, token refresh, write again");
  assert.equal(page.calls[2].opts.headers["X-CSRF-Token"], "fresh-token");
  assert.equal(page.store.tf_csrf, "fresh-token");
});

test("a 403 that is not a CSRF failure (a viewer cannot write) is returned untouched", async () => {
  const page = makePage(() => resp(403, { error: "Forbidden: viewers cannot modify data" }));
  const r = await page.fetch("/api/pipeline", { method: "POST", body: "{}" });
  assert.equal(r.status, 403);
  assert.equal(page.calls.length, 1, "no retry");
  assert.deepEqual(page.hrefs, []);
});

test("a CSRF failure is retried only once", async () => {
  const page = makePage((url) => (url === "/api/me" ? resp(200, { csrf_token: "t2" }) : resp(403, { error: "Invalid CSRF token" })));
  const r = await page.fetch("/api/pipeline", { method: "POST", body: "{}" });
  assert.equal(r.status, 403);
  assert.equal(page.calls.length, 3, "write, token refresh, one more write, then the 403 is returned");
});

test("GET requests are not retried and a normal response passes through", async () => {
  const page = makePage(() => resp(200, { ok: true }));
  const r = await page.fetch("/api/search?q=a");
  assert.equal(r.status, 200);
  assert.equal(page.calls.length, 1);
  assert.deepEqual(page.hrefs, []);
});

test("the sign-in page shows the note", () => {
  const login = fs.readFileSync(path.join(__dirname, "..", "web", "login.html"), "utf8");
  assert.ok(login.includes("tf_login_notice") && login.includes("Your session expired"));
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { await fn(); console.log(`ok    ${name}`); } catch (err) { failed += 1; console.log(`FAIL  ${name}`); console.log(err && err.stack ? err.stack : err); }
  }
  console.log(`\n${tests.length - failed} of ${tests.length} passed`);
  process.exitCode = failed ? 1 : 0;
})();
