/* T16 app.js logic tests — run under Node from tests/test_t16_appjs.py.
 * DOM-less: exercises the pure logic of the shared module (decimal parser, amount
 * formatter, refresh guard, sha256/deriveKey). Exits nonzero on any failure. */
"use strict";

const assert = require("node:assert");
const Pebble = require("../src/static/app.js");

const pending = [];
const failures = [];
let passed = 0;

function t(name, fn) {
  pending.push(
    Promise.resolve()
      .then(fn)
      .then(() => {
        passed += 1;
        process.stdout.write(`ok ${name}\n`);
      })
      .catch((err) => {
        failures.push({ name, err });
        process.stdout.write(`FAILED ${name}: ${err && err.message}\n`);
      })
  );
}

/* --- parseDecimalAmount (R118/R119) --- */
t("parse 15 -> 1500", () => {
  assert.deepStrictEqual(Pebble.parseDecimalAmount("15", 2), { ok: true, minor: 1500 });
});
t("parse 15.00 -> 1500", () => {
  assert.deepStrictEqual(Pebble.parseDecimalAmount("15.00", 2), { ok: true, minor: 1500 });
});
t("parse 15.5 -> 1550", () => {
  assert.strictEqual(Pebble.parseDecimalAmount("15.5", 2).minor, 1550);
});
t("parse 15.50 -> 1550", () => {
  assert.strictEqual(Pebble.parseDecimalAmount("15.50", 2).minor, 1550);
});
t("parse 0.05 -> 5", () => {
  assert.strictEqual(Pebble.parseDecimalAmount("0.05", 2).minor, 5);
});
t("parse trims whitespace", () => {
  assert.strictEqual(Pebble.parseDecimalAmount(" 15.5 ", 2).minor, 1550);
});
t("reject 15.005 rather than rounding", () => {
  assert.strictEqual(Pebble.parseDecimalAmount("15.005", 2).ok, false);
});
t("reject nonnumeric input", () => {
  assert.strictEqual(Pebble.parseDecimalAmount("fifteen", 2).ok, false);
  assert.strictEqual(Pebble.parseDecimalAmount("1e3", 2).ok, false);
  assert.strictEqual(Pebble.parseDecimalAmount("-5", 2).ok, false);
  assert.strictEqual(Pebble.parseDecimalAmount("15,50", 2).ok, false);
});
t("reject empty", () => {
  assert.strictEqual(Pebble.parseDecimalAmount("", 2).ok, false);
});
t("reject 15. and .5", () => {
  assert.strictEqual(Pebble.parseDecimalAmount("15.", 2).ok, false);
  assert.strictEqual(Pebble.parseDecimalAmount(".5", 2).ok, false);
});
t("minor_units 0: 15 -> 15, no decimals accepted", () => {
  assert.strictEqual(Pebble.parseDecimalAmount("15", 0).minor, 15);
  assert.strictEqual(Pebble.parseDecimalAmount("15.0", 0).ok, false);
  assert.strictEqual(Pebble.parseDecimalAmount("1200", 0).minor, 1200);
});
t("minor_units 3: 15.005 -> 15005", () => {
  assert.strictEqual(Pebble.parseDecimalAmount("15.005", 3).minor, 15005);
});
t("rejection is purely local: pure function, no I/O (R119)", () => {
  assert.strictEqual(typeof Pebble.parseDecimalAmount("x", 2).error, "string");
});

/* --- formatAmount (R120/R121) --- */
t("format 100.00 EUR", () => {
  assert.strictEqual(Pebble.formatAmount(10000, 2, "EUR"), "100.00 EUR");
});
t("format 1200 JPY without decimal point", () => {
  assert.strictEqual(Pebble.formatAmount(1200, 0, "JPY"), "1200 JPY");
});
t("format 0.05 EUR", () => {
  assert.strictEqual(Pebble.formatAmount(5, 2, "EUR"), "0.05 EUR");
});
t("format 0", () => {
  assert.strictEqual(Pebble.formatAmount(0, 2, "EUR"), "0.00 EUR");
  assert.strictEqual(Pebble.formatAmount(0, 0, "JPY"), "0 JPY");
});
t("never emits a minus sign (R121)", () => {
  assert.strictEqual(Pebble.formatAmount(-5, 2, "EUR").includes("-"), false);
  assert.strictEqual(Pebble.formatAmount(-1, 0, "JPY").includes("-"), false);
});
t("exactly minor_units decimal places", () => {
  assert.strictEqual(Pebble.formatAmount(5, 3, "XYZ"), "0.005 XYZ");
  assert.strictEqual(Pebble.formatAmount(1234, 2, "EUR"), "12.34 EUR");
});

/* --- createRefreshGuard (R131) --- */
t("later response applied then delayed earlier response dropped", () => {
  const guard = Pebble.createRefreshGuard();
  const first = guard.issue();
  const second = guard.issue();
  assert.ok(second > first);
  assert.strictEqual(guard.arrive(second), true); // later refresh arrives first
  assert.strictEqual(guard.arrive(first), false); // delayed earlier read dropped
});
t("in-order responses both applied", () => {
  const guard = Pebble.createRefreshGuard();
  const first = guard.issue();
  const second = guard.issue();
  assert.strictEqual(guard.arrive(first), true);
  assert.strictEqual(guard.arrive(second), true);
});
t("same sequence never applied twice", () => {
  const guard = Pebble.createRefreshGuard();
  const seq = guard.issue();
  assert.strictEqual(guard.arrive(seq), true);
  assert.strictEqual(guard.arrive(seq), false);
});
t("guards are independent per resource", () => {
  const balance = Pebble.createRefreshGuard();
  const requests = Pebble.createRefreshGuard();
  const b1 = balance.issue();
  assert.strictEqual(balance.arrive(b1), true);
  const r1 = requests.issue();
  assert.strictEqual(requests.arrive(r1), true);
  assert.strictEqual(balance.highestApplied(), b1);
  assert.strictEqual(requests.highestApplied(), r1);
});

/* --- sha256 / deriveKey (R116, R135) --- */
/* Expected digests pinned from Python hashlib.sha256 so the JS and Python sides can
 * never silently diverge. */
const VECTORS = [
  ["", "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"],
  ["abc", "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"],
  ["pebble-test", "e4ddb8f6b5ec64f1cf186742b4a0eb24f39b632d3c09e3e982197bf48a8d7540"],
];
t("sha256Hex matches reference vectors", async () => {
  for (const [input, expected] of VECTORS) {
    assert.strictEqual(await Pebble.sha256Hex(input), expected);
  }
});
t("deriveKey matches sha256(formId|JSON) from Python hashlib", async () => {
  assert.strictEqual(
    await Pebble.deriveKey("pay-form", { to_handle: "bob", amount: "15.00" }),
    "e04fb0befef2cd35e56927e21ffc0b719473af36192fe2884a571aa2d414234e");
  assert.strictEqual(
    await Pebble.deriveKey("pay-form", { to_handle: "bob", amount: "15.50" }),
    "44ba8889d72f52220f43c4a50031a73c3cde4443e8b434129178e98a7aee011a");
});
t("key changes only when a field changes", async () => {
  const a = await Pebble.deriveKey("pay-form", { to_handle: "bob", amount: "15.00" });
  const b = await Pebble.deriveKey("pay-form", { to_handle: "bob", amount: "15.00" });
  const c = await Pebble.deriveKey("pay-form", { to_handle: "bob", amount: "16.00" });
  const d = await Pebble.deriveKey("pay-form2", { to_handle: "bob", amount: "15.00" });
  assert.strictEqual(a, b);
  assert.notStrictEqual(a, c);
  assert.notStrictEqual(a, d);
});
t("deriveKey stable for non-ASCII field values (UTF-8)", async () => {
  const a = await Pebble.deriveKey("pay-form", { note: "café ✓" });
  const b = await Pebble.deriveKey("pay-form", { note: "café ✓" });
  assert.strictEqual(a, b);
  assert.match(a, /^[0-9a-f]{64}$/);
});

Promise.all(pending).then(() => {
  if (failures.length > 0) {
    process.stdout.write(`\n${failures.length} failed, ${passed} passed\n`);
    process.exit(1);
  }
  process.stdout.write(`\nall ${passed} app.js logic tests passed\n`);
  process.exit(0);
});