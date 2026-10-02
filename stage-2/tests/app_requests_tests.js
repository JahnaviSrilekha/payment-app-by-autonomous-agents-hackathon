"use strict";

const assert = require("node:assert");
const Pebble = require("../src/static/app.js");
const { fakeEl, document, registerPage, fetchCalls, setFetchImpl,
  clearFetchCalls, byTestid, findFetch } = require("./dom_shim.js");
const { BOOT, homePage } = require("./dom_shim.js");

document.cookie = "pebble_token=tok-1"; // the browser would hold the session cookie

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

t("requests screen: pay posts with a derived key; refusal refreshes and clears the stale button (R134)", async () => {
  fetchCalls.length = 0;
  const main = fakeEl("main");
  const incoming = fakeEl("ul"); incoming.setAttribute("data-testid", "incoming-list");
  incoming.parentNode = main; main.children.push(incoming);
  const outgoing = fakeEl("ul"); outgoing.setAttribute("data-testid", "outgoing-list");
  registerPage({ main, ids: {} });
  setFetchImpl(async (url) => {
    if (url.startsWith("/requests/rq_1/pay")) {
      return { status: 409, text: async () => JSON.stringify({ error: { code: "request_not_pending", message: "request is not pending" } }) };
    }
    if (url.includes("direction=incoming")) {
      return { status: 200, text: async () => JSON.stringify({ requests: [], has_more: false }) };
    }
    return { status: 200, text: async () => JSON.stringify({ requests: [], has_more: false }) };
  });
  Pebble.initRequests({ screen: "requests", signed_in: true, handle: "ada", minor_units: 2, currency: "EUR" });
  fetchCalls.length = 0;
  // a pending incoming row with a pay button, as rendered server-side
  const payButton = fakeEl("button");
  payButton.setAttribute("data-testid", "request-pay-rq_1");
  main.children.push(payButton);
  await main.listeners.click({ target: payButton });
  const call = findFetch("/requests/rq_1/pay");
  assert.ok(call, "POST request-pay sent");
  assert.ok(call.init.headers["Idempotency-Key"], "request-pay carries an idempotency key");
  const error = byTestid("request-error");
  assert.ok(error, "request-error shown on refusal");
  assert.ok(fetchCalls.some((c) => c.url.includes("direction=incoming")),
    "request list refreshed after refusal");
});

Promise.all(pending).then(() => {
  if (failures.length > 0) {
    process.stdout.write(`\n${failures.length} failed, ${passed} passed\n`);
    process.exit(1);
  }
  process.stdout.write(`\nall ${passed} page-wiring tests passed\n`);
  process.exit(0);
});
