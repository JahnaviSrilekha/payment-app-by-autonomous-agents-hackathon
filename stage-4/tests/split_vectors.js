/* Cross-language vector checks (run from Python with vectors as argv[2]).
 * Usage: node split_vectors.js '<json>'
 *   - array of [amount, count, shares[]]  -> splitShares must match exactly
 *   - {"format": [[minor, minor_units, currency, expected]]} -> formatAmount matches */
"use strict";

const assert = require("node:assert");
const Pebble = require("../src/static/app.js");

const raw = process.argv[2];
let vectors = [];
let formatVectors = null;
if (raw.trim().startsWith("{")) {
  const parsed = JSON.parse(raw);
  formatVectors = parsed.format;
} else {
  vectors = JSON.parse(raw);
}

if (vectors.length) {
  for (const [amount, count, expected] of vectors) {
    const got = Pebble.splitShares(amount, count);
    assert.deepStrictEqual(
      got, expected, `splitShares(${amount}, ${count}) = ${JSON.stringify(got)}`
      + ` != server ${JSON.stringify(expected)}`);
  }
  process.stdout.write(`splitShares: ${vectors.length} vectors match\n`);
}

if (formatVectors) {
  for (const [minor, minorUnits, currency, expected] of formatVectors) {
    const got = Pebble.formatAmount(minor, minorUnits, currency);
    assert.strictEqual(got, expected, `formatAmount(${minor}, ${minorUnits}, `
      + `${currency}) = ${got} != server ${expected}`);
  }
  process.stdout.write(`formatAmount: ${formatVectors.length} vectors match\n`);
}

process.exit(0);