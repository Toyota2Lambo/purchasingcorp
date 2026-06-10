'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { median, mean, trimmedMean, sampleSize, summarize } = require('../lib/stats');

test('median — odd count is the middle value', () => {
  assert.equal(median([3, 1, 2]), 2);
  assert.equal(median([10, 50, 30, 20, 40]), 30);
});

test('median — even count averages the two middles', () => {
  assert.equal(median([1, 2, 3, 4]), 2.5);
  assert.equal(median([100, 200]), 150);
});

test('median — empty array is null', () => {
  assert.equal(median([]), null);
});

test('median — ignores non-numeric junk', () => {
  assert.equal(median([10, 'x', null, 20, undefined, 30]), 20);
});

test('sampleSize counts only finite numbers', () => {
  assert.equal(sampleSize([1, 2, 'nope', NaN, null, 3]), 3);
  assert.equal(sampleSize([]), 0);
});

test('trimmedMean drops top & bottom 10% by count', () => {
  // n=10, drop floor(10*0.1)=1 each end -> keep middle 8 (2..9)
  const v = [1, 2, 3, 4, 5, 6, 7, 8, 9, 100]; // 100 is an outlier dropped
  // kept = [2,3,4,5,6,7,8,9] -> mean = 5.5
  assert.equal(trimmedMean(v, 0.1), 5.5);
});

test('trimmedMean with no trimming (small n) equals plain mean', () => {
  // n=4, floor(4*0.1)=0 dropped -> equals mean
  assert.equal(trimmedMean([2, 4, 6, 8], 0.1), mean([2, 4, 6, 8]));
});

test('trimmedMean never returns null/NaN for non-empty tiny input', () => {
  assert.equal(trimmedMean([42], 0.5), 42);
  assert.ok(Number.isFinite(trimmedMean([5, 9], 0.5)));
});

test('summarize returns the full shape', () => {
  const s = summarize([10, 20, 30, 40, 1000], 0.1);
  assert.equal(s.sampleSize, 5);
  assert.equal(s.median, 30);
  assert.equal(s.min, 10);
  assert.equal(s.max, 1000);
  assert.ok(typeof s.trimmedMean === 'number');
});
