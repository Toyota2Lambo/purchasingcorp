'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { parseDollars, formatUSD, roundTo } = require('../lib/money');

test('parseDollars reads a leading $ amount', () => {
  assert.equal(parseDollars('$1,241'), 1241);
  assert.equal(parseDollars('$1,050.50'), 1050.5);
  assert.equal(parseDollars('$75'), 75);
});

test('parseDollars returns null for non-priced cells (matches frontend)', () => {
  assert.equal(parseDollars('Contact'), null);
  assert.equal(parseDollars('50% off MSRP'), null);
  assert.equal(parseDollars('-$100 Active'), null); // no LEADING $ amount
  assert.equal(parseDollars('—'), null);
  assert.equal(parseDollars(''), null);
  assert.equal(parseDollars(null), null);
});

test('formatUSD matches the existing display contract', () => {
  assert.equal(formatUSD(1241), '$1,241');
  assert.equal(formatUSD(75), '$75');
  assert.equal(formatUSD(1050.5), '$1,050.50');
  assert.equal(formatUSD(null), null);
  assert.equal(formatUSD(NaN), null);
});

test('round-trip: a formatted offer parses back to a number', () => {
  for (const n of [20, 75, 300, 893, 1241, 2500]) {
    assert.equal(parseDollars(formatUSD(n)), n);
  }
});

test('roundTo rounds to the nearest step', () => {
  assert.equal(roundTo(892.5, 1), 893);
  assert.equal(roundTo(649.944, 5), 650);
  assert.equal(roundTo(312, 10), 310);
  assert.equal(roundTo(null), null);
});
