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

t("pay submit sends minor units with a derived idempotency key", async () => {
  fetchCalls.length = 0;
  const page = homePage();
  byTestid("pay-handle").value = "bob";
  byTestid("pay-amount").value = "15.5";
  setFetchImpl(async () => ({ status: 201, text: async () => JSON.stringify({ payment_id: "p_1" }) }));
  Pebble.initHome(BOOT);
  await page.paySubmit.listeners.click();
  const call = findFetch("/payments");
  assert.ok(call, "POST /payments sent");
  assert.deepStrictEqual(JSON.parse(call.init.body), {
    to_handle: "bob", amount: 1550, note: "", visibility: "public" });
  assert.strictEqual(call.init.headers["Idempotency-Key"],
    await Pebble.deriveKey("pay-form", ["bob", "15.5", "", "public"]));
  assert.ok(call.init.headers.Authorization.startsWith("Bearer "), "API stays bearer-token");
});

t("pay refused: pay-error shown, inputs preserved, balance/feed refreshed (R133)", async () => {
  fetchCalls.length = 0;
  const page = homePage();
  byTestid("pay-handle").value = "bob";
  byTestid("pay-amount").value = "15.00";
  setFetchImpl(async (url) => {
    if (url === "/payments") {
      return { status: 409, text: async () => JSON.stringify({ error: { code: "insufficient_funds", message: "insufficient funds" } }) };
    }
    if (url === "/me") {
      return { status: 200, text: async () => JSON.stringify({ balance: 8500, total: 8500, available: 8500, held: 0, currency: "EUR", minor_units: 2 }) };
    }
    return { status: 200, text: async () => JSON.stringify({ payments: [{ payment_id: "p_9", from_handle: "ada", to_handle: "bob", amount: 1500, note: "", visibility: "public" }] }) };
  });
  Pebble.initHome(BOOT);
  fetchCalls.length = 0; // drop the initial refresh reads
  await page.paySubmit.listeners.click().catch((e) => console.log("CLICK-ERR", e && e.message));
  console.log("CALLS", fetchCalls.map((c) => [c.url, c.init && c.init.method, c.init && c.init.body]));
  const error = byTestid("pay-error");
  assert.ok(error, "pay-error present");
  assert.strictEqual(error.textContent, "insufficient funds");
  assert.strictEqual(byTestid("pay-handle").value, "bob", "inputs preserved");
  assert.strictEqual(byTestid("pay-amount").value, "15.00", "inputs preserved");
  assert.ok(fetchCalls.some((c) => c.url === "/me"), "balance refreshed");
  assert.ok(fetchCalls.some((c) => c.url.startsWith("/activity")), "feed refreshed");
  assert.strictEqual(page.available.textContent, "85.00 EUR", "wallet re-rendered");
  assert.ok(page.activityList.children.some((c) => c.getAttribute("data-testid") === "activity-item-p_9"),
    "feed re-rendered after refusal");
});

t("pay lost response: pay-uncertain, no pay-error; retry same fields reuses the key (R135)", async () => {
  fetchCalls.length = 0;
  const page = homePage();
  byTestid("pay-handle").value = "bob";
  byTestid("pay-amount").value = "15.00";
  let failNext = true;
  setFetchImpl(async (url) => {
    if (url === "/payments" && failNext) {
      throw new Error("connection reset");
    }
    if (url === "/payments") {
      return { status: 200, text: async () => JSON.stringify({ payment_id: "p_1" }) };
    }
    return { status: 200, text: async () => JSON.stringify({ payments: [] }) };
  });
  Pebble.initHome(BOOT);
  fetchCalls.length = 0;
  await page.paySubmit.listeners.click();
  assert.ok(byTestid("pay-uncertain"), "pay-uncertain present");
  assert.strictEqual(byTestid("pay-uncertain").textContent.length > 0, true, "nonempty text");
  assert.ok(!byTestid("pay-error"), "no pay-error for unknown outcomes");
  const first = findFetch("/payments");
  failNext = false;
  await page.paySubmit.listeners.click(); // retry with unchanged fields
  const retry = findFetch("/payments");
  assert.strictEqual(retry.init.headers["Idempotency-Key"],
    first.init.headers["Idempotency-Key"], "same key on retry");
  assert.deepStrictEqual(JSON.parse(retry.init.body), JSON.parse(first.init.body),
    "same body on retry");
  assert.ok(!byTestid("pay-uncertain"), "uncertainty removed on success");
  assert.ok(!byTestid("pay-error"), "no error after successful retry");
});

t("resubmit without change reuses the key; changing a field makes a new key (R116)", async () => {
  fetchCalls.length = 0;
  const page = homePage();
  byTestid("pay-handle").value = "bob";
  byTestid("pay-amount").value = "15.00";
  byTestid("pay-note").value = "coffee";
  setFetchImpl(async () => ({ status: 200, text: async () => JSON.stringify({ payment_id: "p_1" }) }));
  Pebble.initHome(BOOT);
  fetchCalls.length = 0;
  await page.paySubmit.listeners.click();
  const first = findFetch("/payments");
  await page.paySubmit.listeners.click(); // unchanged: replay, no new payment
  const second = findFetch("/payments");
  assert.strictEqual(second.init.headers["Idempotency-Key"], first.init.headers["Idempotency-Key"]);
  byTestid("pay-amount").value = "16.00"; // changed: new payment request
  await page.paySubmit.listeners.click();
  const third = findFetch("/payments");
  assert.notStrictEqual(third.init.headers["Idempotency-Key"], first.init.headers["Idempotency-Key"]);
  assert.strictEqual(JSON.parse(third.init.body).amount, 1600);
});

t("wallet-refresh: delayed earlier read never overwrites the later one (R131)", async () => {
  fetchCalls.length = 0;
  const page = homePage();
  let meStatus = 1;
  setFetchImpl(async (url) => {
    if (url === "/me") {
      meStatus += 1;
      if (meStatus === 2) {
        await new Promise((r) => setTimeout(r, 60)); // the earlier read resolves late
        return { status: 200, text: async () => JSON.stringify({ balance: 5000, total: 5000, available: 5000, held: 0, currency: "EUR", minor_units: 2 }) };
      }
      return { status: 200, text: async () => JSON.stringify({ balance: 9900, total: 9900, available: 9900, held: 0, currency: "EUR", minor_units: 2 }) };
    }
    return { status: 200, text: async () => JSON.stringify({ payments: [] }) };
  });
  Pebble.initHome(BOOT);
  fetchCalls.length = 0;
  const first = page.refresh.listeners.click(); // issued first, resolves last
  await new Promise((r) => setTimeout(r, 5));
  await page.refresh.listeners.click(); // issued second, resolves first
  await first;
  assert.strictEqual(page.available.textContent, "99.00 EUR",
    "the later refresh wins: the delayed earlier read was dropped");
});

t("wallet-held appears only while held is positive (R187/R132)", async () => {
  fetchCalls.length = 0;
  const page = homePage();
  let held = 2000;
  setFetchImpl(async (url) => {
    if (url === "/me") {
      return { status: 200, text: async () => JSON.stringify({ balance: 10000, total: 10000, available: 10000 - held, held, currency: "EUR", minor_units: 2 }) };
    }
    return { status: 200, text: async () => JSON.stringify({ payments: [] }) };
  });
  Pebble.initHome(BOOT);
  await new Promise((r) => setTimeout(r, 5));
  assert.ok(byTestid("wallet-held"), "held chip created while held > 0");
  assert.strictEqual(byTestid("wallet-held").getAttribute("data-amount"), "2000");
  held = 0;
  await page.refresh.listeners.click();
  assert.ok(!byTestid("wallet-held"), "held chip removed at zero");
});

Promise.all(pending).then(() => {
  if (failures.length > 0) {
    process.stdout.write(`\n${failures.length} failed, ${passed} passed\n`);
    process.exit(1);
  }
  process.stdout.write(`\nall ${passed} page-wiring tests passed\n`);
  process.exit(0);
});
