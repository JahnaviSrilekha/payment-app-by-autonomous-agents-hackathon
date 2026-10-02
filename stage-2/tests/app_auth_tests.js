"use strict";

const assert = require("node:assert");
const Pebble = require("../src/static/app.js");
const { fakeEl, document, registerPage, fetchCalls, setFetchImpl,
  clearFetchCalls, byTestid, findFetch, lastFetch } = require("./dom_shim.js");
const { BOOT, homePage } = require("./dom_shim.js");

const pending = [];
const failures = [];
let passed = 0;
let chain = Promise.resolve(); // sequential: shared fetch shim state

function t(name, fn) {
  pending.push(
    chain = chain.then(fn)
      .then(() => {
        passed += 1;
        process.stdout.write(`ok ${name}\n`);
      })
      .catch((err) => {
        failures.push({ name, err });
        process.stdout.write(`FAILED ${name}: ${err && err.stack ? err.stack.split("\n").slice(0, 4).join(" | ") : err}\n`);
      })
  );
}

t("initAuth signup posts to the stage-1 endpoint and stores the token", async () => {
  clearFetchCalls();
  fetchCalls.length = 0;
  const main = fakeEl("main");
  const form = fakeEl("form"); form.setAttribute("id", "signup-form");
  const submit = fakeEl("button"); submit.setAttribute("data-testid", "signup-submit");
  form.children.push(submit);
  const reg = (t, tag, value) => {
    const e = fakeEl(tag || "input");
    e.setAttribute("data-testid", t);
    if (value !== undefined) e.value = value;
    return e;
  };
  reg("signup-email", undefined, "ada@example.com");
  reg("signup-password", undefined, "correct horse");
  reg("signup-display-name", undefined, "Ada");
  registerPage({ main, ids: { "signup-form": form } });
  setFetchImpl(async () => ({ status: 201, text: async () => JSON.stringify({ user_id: "u_1", display_name: "Ada", token: "tok-1" }) }));
  Pebble.initAuth({ screen: "auth", mode: "signup" });
  await submit.listeners.click();
  const call = lastFetch();
  assert.strictEqual(call.url, "/auth/signup");
  assert.strictEqual(call.init.method, "POST");
  assert.deepStrictEqual(JSON.parse(call.init.body), {
    email: "ada@example.com", password: "correct horse", display_name: "Ada" });
  assert.ok(document.cookie.includes("pebble_token=tok-1"), "cookie set from the API token");
  assert.strictEqual(globalThis.window.location.href, "/");
});

t("initAuth failed login shows auth-error without navigation", async () => {
  clearFetchCalls();
  fetchCalls.length = 0;
  globalThis.window.location.href = "";
  const main = fakeEl("main");
  const form = fakeEl("form"); form.setAttribute("id", "login-form");
  const submit = fakeEl("button"); submit.setAttribute("data-testid", "login-submit");
  form.children.push(submit);
  const reg = (t, value) => {
    const e = fakeEl("input");
    e.setAttribute("data-testid", t);
    e.value = value;
    return e;
  };
  reg("login-email", "ada@example.com");
  reg("login-password", "wrong");
  registerPage({ main, ids: { "login-form": form } });
  setFetchImpl(async () => ({ status: 401, text: async () => JSON.stringify({ error: { code: "unauthenticated", message: "missing, malformed or unknown bearer token" } }) }));
  Pebble.initAuth({ screen: "auth", mode: "login" });
  await submit.listeners.click();
  const error = byTestid("auth-error");
  assert.ok(error, "auth-error element created on refusal");
  assert.strictEqual(error.textContent, "missing, malformed or unknown bearer token");
  assert.strictEqual(globalThis.window.location.href, "", "no navigation on failure");
});

Promise.all(pending).then(() => {
  if (failures.length > 0) {
    process.stdout.write(`\n${failures.length} failed, ${passed} passed\n`);
    process.exit(1);
  }
  process.stdout.write(`\nall ${passed} page-wiring tests passed\n`);
  process.exit(0);
});
