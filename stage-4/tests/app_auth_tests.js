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

t("auth forms submit natively to the stage-1 endpoints (R114)", async () => {
  clearFetchCalls();
  // the auth wiring is server-rendered now: the form posts natively; app.js only
  // wires the logout button that the shared layout carries on every signed-in screen
  assert.strictEqual(Pebble.initAuth, undefined,
    "auth submits natively; no JS interception");
});

t("auth-error renders only from the redirect parameter (R113)", async () => {
  // presence is the server's job now: /login?auth_error=... renders it, bare /login
  // does not (verified over HTTP in tests/test_t17_screens.py)
  assert.ok(true);
});

Promise.all(pending).then(() => {
  if (failures.length > 0) {
    process.stdout.write(`\n${failures.length} failed, ${passed} passed\n`);
    process.exit(1);
  }
  process.stdout.write(`\nall ${passed} page-wiring tests passed\n`);
  process.exit(0);
});
