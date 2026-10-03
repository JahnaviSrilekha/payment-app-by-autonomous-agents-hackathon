/* T21 authorizations-screen wiring tests under Node with the shared DOM shim. */
"use strict";

const assert = require("node:assert");
const Pebble = require("../src/static/app.js");
const shim = require("./dom_shim.js");
const { fakeEl, document, registerPage, fetchCalls, setFetchImpl,
  clearFetchCalls, byTestid, findFetch } = shim;

const findPost = (url) => {
  for (let i = fetchCalls.length - 1; i >= 0; i--) {
    if (fetchCalls[i].init && fetchCalls[i].init.method === "POST"
      && (fetchCalls[i].url === url || fetchCalls[i].url.startsWith(url))) {
      return fetchCalls[i];
    }
  }
  return null;
};

const BOOT = {
  screen: "authorizations", signed_in: true, handle: "ada",
  minor_units: 2, currency: "EUR",
};

document.cookie = "pebble_token=tok-1";

const pending = [];
const failures = [];
let passed = 0;
let chain = Promise.resolve();

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

function authzPage(rows) {
  const main = fakeEl("main");
  const mk = (testid, tag) => {
    const el = fakeEl(tag || "input");
    el.setAttribute("data-testid", testid);
    return el;
  };
  mk("wallet-available", "p");
  const balance = mk("wallet-balance", "p");
  balance.parentNode = main; main.children.push(balance);
  mk("wallet-refresh", "button");
  const form = fakeEl("form"); form.setAttribute("id", "authorize-form");
  const submit = mk("authorize-submit", "button");
  form.children.push(submit); submit.parentNode = form;
  mk("authorize-handle"); mk("authorize-amount").value = "20.00";
  mk("authorize-note"); mk("authorize-visibility").value = "public";
  const list = mk("authorization-list", "ul");
  const card = fakeEl("section"); card.setAttribute("id", "authorizations-card");
  card.appendChild(list); list.parentNode = card;
  card.parentNode = main; main.children.push(card);
  registerPage({ main, ids: { "authorize-form": form, "authorizations-card": card } });
  return { main, submit, list, card };
}

function standardFetch(rows) {
  return setFetchImpl(async (url) => {
    if (url === "/me") {
      return { status: 200, text: async () => JSON.stringify({ balance: 8000, total: 8000, available: 6800, held: 1200, currency: "EUR", minor_units: 2 }) };
    }
    if (url.startsWith("/activity")) {
      return { status: 200, text: async () => JSON.stringify({ payments: [] }) };
    }
    if (url === "/authorizations?limit=50") {
      return { status: 200, text: async () => JSON.stringify({ authorizations: rows, has_more: false }) };
    }
    if (url.startsWith("/authorizations/a_1/capture")) {
      return { status: 201, text: async () => JSON.stringify({ payment_id: "p_9" }) };
    }
    if (url.startsWith("/authorizations/a_2/void")) {
      return { status: 200, text: async () => JSON.stringify({ status: "voided" }) };
    }
    if (url === "/authorizations") {
      return { status: 201, text: async () => JSON.stringify({ authorization_id: "a_9" }) };
    }
    return { status: 404, text: async () => JSON.stringify({ error: { code: "not_found", message: "no such resource" } }) };
  });
}

const ROWS = [
  { authorization_id: "a_2", from_handle: "ada", to_handle: "bob", amount: 2000,
    captured_amount: 0, remaining_amount: 2000, note: "", status: "open",
    expires_at: "2099-01-01T00:00:00+00:00" },
  { authorization_id: "a_1", from_handle: "bob", to_handle: "ada", amount: 1200,
    captured_amount: 0, remaining_amount: 1200, note: "deposit", status: "open",
    expires_at: "2099-01-01T00:00:00+00:00" },
];

t("authorize submit posts minor units with a derived key and refreshes", async () => {
  clearFetchCalls();
  const page = authzPage(ROWS);
  byTestid("authorize-handle").value = "bob";
  byTestid("authorize-amount").value = "20.00";
  byTestid("authorize-note").value = "deposit";
  standardFetch(ROWS);
  Pebble.initAuthorizations(BOOT);
  clearFetchCalls();
  await page.submit.listeners.click();
  const call = findPost("/authorizations");
  assert.ok(call, "POST /authorizations sent");
  assert.strictEqual(JSON.parse(call.init.body).amount, 2000);
  assert.strictEqual(call.init.headers["Idempotency-Key"],
    await Pebble.deriveKey("authorize-form", ["bob", "20.00", "deposit", "public"]));
  assert.ok(fetchCalls.some((c) => c.url === "/me"), "wallet refreshed");
  assert.ok(fetchCalls.some((c) => c.url === "/authorizations?limit=50"), "list refreshed");
});

t("capture input is pre-filled with the live remaining amount (R190)", async () => {
  clearFetchCalls();
  const page = authzPage(ROWS);
  standardFetch(ROWS);
  Pebble.initAuthorizations(BOOT);
  await new Promise((r) => setTimeout(r, 5));
  const input = byTestid("authorization-capture-amount-a_1");
  assert.ok(input, "capture input present on incoming open row");
  assert.strictEqual(input.value, "12.00"); // 1200 minor units as a decimal string
  const voidBtn = byTestid("authorization-void-a_2");
  assert.ok(voidBtn, "void button on outgoing open row");
  assert.ok(!byTestid("authorization-capture-a_2"), "no capture on outgoing");
  assert.ok(!byTestid("authorization-void-a_1"), "no void on incoming");
});

t("capture posts the parsed amount with a derived key", async () => {
  clearFetchCalls();
  globalThis.__TRACE__ = null;
  const page = authzPage(ROWS);
  standardFetch(ROWS);
  Pebble.initAuthorizations(BOOT);
  await new Promise((r) => setTimeout(r, 5));
  const button = byTestid("authorization-capture-a_1");
  await page.main.listeners.click({ target: button });
  const call = findPost("/authorizations/a_1/capture");
  assert.ok(call, "POST capture sent");
  assert.deepStrictEqual(JSON.parse(call.init.body), { amount: 1200 });
  assert.strictEqual(call.init.headers["Idempotency-Key"],
    await Pebble.deriveKey("authorization-capture", ["a_1", "12.00"]));
  const err = byTestid("authorization-error");
  assert.ok(!err || err.hidden !== false || !err.textContent, "no error on success");
});

t("refused capture shows authorization-error and refreshes", async () => {
  clearFetchCalls();
  const page = authzPage(ROWS);
  setFetchImpl(async (url) => {
    if (url.startsWith("/authorizations/a_1/capture")) {
      return { status: 422, text: async () => JSON.stringify({ error: { code: "capture_exceeds_authorization", message: "capture exceeds the remaining authorized amount" } }) };
    }
    if (url === "/me") {
      return { status: 200, text: async () => JSON.stringify({ balance: 8000, total: 8000, available: 6800, held: 1200, currency: "EUR", minor_units: 2 }) };
    }
    if (url.startsWith("/activity")) {
      return { status: 200, text: async () => JSON.stringify({ payments: [] }) };
    }
    return { status: 200, text: async () => JSON.stringify({ authorizations: ROWS, has_more: false }) };
  });
  Pebble.initAuthorizations(BOOT);
  await new Promise((r) => setTimeout(r, 5));
  const button = byTestid("authorization-capture-a_1");
  await page.main.listeners.click({ target: button });
  const err = byTestid("authorization-error");
  assert.ok(err, "authorization-error present");
  assert.strictEqual(err.textContent, "capture exceeds the remaining authorized amount");
});

t("void posts without an idempotency key (R176)", async () => {
  clearFetchCalls();
  const page = authzPage(ROWS);
  standardFetch(ROWS);
  Pebble.initAuthorizations(BOOT);
  await new Promise((r) => setTimeout(r, 5));
  await page.main.listeners.click({ target: byTestid("authorization-void-a_2") });
  const call = findPost("/authorizations/a_2/void");
  assert.ok(call, "POST void sent");
  assert.ok(!call.init.headers["Idempotency-Key"], "no key on void");
});

t("refused authorize shows authorize-error inside the authorize form (R188)", async () => {
  clearFetchCalls();
  const page = authzPage(ROWS);
  setFetchImpl(async (url) => {
    if (url === "/authorizations") {
      return { status: 404, text: async () => JSON.stringify({ error: { code: "not_found", message: "no user has that handle" } }) };
    }
    if (url === "/me") {
      return { status: 200, text: async () => JSON.stringify({ balance: 8000, total: 8000, available: 6800, held: 1200, currency: "EUR", minor_units: 2 }) };
    }
    if (url.startsWith("/activity")) {
      return { status: 200, text: async () => JSON.stringify({ payments: [] }) };
    }
    return { status: 200, text: async () => JSON.stringify({ authorizations: ROWS, has_more: false }) };
  });
  Pebble.initAuthorizations(BOOT);
  await new Promise((r) => setTimeout(r, 5));
  byTestid("authorize-handle").value = "ghost";
  byTestid("authorize-amount").value = "10.00";
  await page.submit.listeners.click();
  const err = byTestid("authorize-error");
  assert.ok(err, "authorize-error present");
  assert.strictEqual(err.textContent, "no user has that handle");
  assert.strictEqual(err.parentNode, document.getElementById("authorize-form"),
    "authorize-error anchors to the authorize form, not the wallet card");
});

t("refused capture shows authorization-error inside the authorizations card (R190)", async () => {
  clearFetchCalls();
  const page = authzPage(ROWS);
  setFetchImpl(async (url) => {
    if (url.startsWith("/authorizations/a_1/capture")) {
      return { status: 422, text: async () => JSON.stringify({ error: { code: "capture_exceeds_authorization", message: "capture exceeds the remaining authorized amount" } }) };
    }
    if (url === "/me") {
      return { status: 200, text: async () => JSON.stringify({ balance: 8000, total: 8000, available: 6800, held: 1200, currency: "EUR", minor_units: 2 }) };
    }
    if (url.startsWith("/activity")) {
      return { status: 200, text: async () => JSON.stringify({ payments: [] }) };
    }
    return { status: 200, text: async () => JSON.stringify({ authorizations: ROWS, has_more: false }) };
  });
  Pebble.initAuthorizations(BOOT);
  await new Promise((r) => setTimeout(r, 5));
  const card = document.getElementById("authorizations-card");
  assert.ok(card, "the authorizations card carries the error host id");
  await page.main.listeners.click({ target: byTestid("authorization-capture-a_1") });
  const err = byTestid("authorization-error");
  assert.ok(err, "authorization-error present");
  assert.strictEqual(err.parentNode, card,
    "authorization-error anchors to the authorizations card, not the wallet card");
});

Promise.all(pending).then(() => {
  if (failures.length > 0) {
    process.stdout.write(`\n${failures.length} failed, ${passed} passed\n`);
    process.exit(1);
  }
  process.stdout.write(`\nall ${passed} page-wiring tests passed\n`);
  process.exit(0);
});