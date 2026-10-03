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

t("split preview mirrors the server rounding for uneven splits (R128)", async () => {
  fetchCalls.length = 0;
  const main = fakeEl("main");
  const form = fakeEl("form"); form.setAttribute("id", "split-form");
  const submit = fakeEl("button"); submit.setAttribute("data-testid", "split-submit");
  form.children.push(submit);
  const preview = fakeEl("section"); preview.setAttribute("data-testid", "split-preview");
  preview.parentNode = main; main.children.push(preview);
  const reg = (t, value) => {
    const e = fakeEl("input");
    e.setAttribute("data-testid", t);
    if (value !== undefined) e.value = value;
    return e;
  };
  reg("split-amount", "10.01");
  reg("split-handles", "ada, bob, cyd");
  reg("split-note", "");
  registerPage({ main, ids: { "split-form": form } });
  Pebble.initSplit({ screen: "split", signed_in: true, handle: "ada", minor_units: 2, currency: "EUR" });
  byTestid("split-amount").listeners.input();
  assert.strictEqual(preview.hidden, false, "preview visible while inputs are valid");
  const share = document._byTestid["split-share-ada"];
  assert.ok(share, "split-share-ada present");
  assert.strictEqual(share.getAttribute("data-amount"), "334"); // 1001 -> 334/334/333
  assert.strictEqual(share.textContent, "3.34 EUR");
});

t("split submit posts minor units; refusal keeps inputs and shows split-error", async () => {
  fetchCalls.length = 0;
  const main = fakeEl("main");
  const form = fakeEl("form"); form.setAttribute("id", "split-form");
  const submit = fakeEl("button"); submit.setAttribute("data-testid", "split-submit");
  form.children.push(submit);
  const reg = (t, value) => {
    const e = fakeEl("input");
    e.setAttribute("data-testid", t);
    if (value !== undefined) e.value = value;
    return e;
  };
  reg("split-amount", "15.005");
  reg("split-handles", "ada, bob");
  reg("split-note", "");
  registerPage({ main, ids: { "split-form": form } });
  Pebble.initSplit({ screen: "split", signed_in: true, handle: "ada", minor_units: 2, currency: "EUR" });
  await submit.listeners.click();
  assert.strictEqual(fetchCalls.length, 0, "too many decimals: no request sent (R119)");
  const error = byTestid("split-error");
  assert.ok(error, "split-error shown without sending a request");
  byTestid("split-amount").value = "15.00";
  setFetchImpl(async () => ({ status: 409, text: async () => JSON.stringify({ error: { code: "validation_failed", message: "validation failed" } }) }));
  await submit.listeners.click();
  assert.strictEqual(JSON.parse(findFetch("/splits").init.body).amount, 1500);
  assert.ok(byTestid("split-error"), "split-error on refusal");
  assert.strictEqual(byTestid("split-handles").value, "ada, bob", "inputs preserved");
});

Promise.all(pending).then(() => {
  if (failures.length > 0) {
    process.stdout.write(`\n${failures.length} failed, ${passed} passed\n`);
    process.exit(1);
  }
  process.stdout.write(`\nall ${passed} page-wiring tests passed\n`);
  process.exit(0);
});
