'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const {
  createBestBuySource,
  buildSearchUrl,
  extractPrices,
  tokenize,
} = require('../sources/bestbuy');

test('tokenize splits a display name into lowercase alphanumeric search terms', () => {
  assert.deepEqual(tokenize('iPhone 17 Pro Max 256GB'), ['iphone', '17', 'pro', 'max', '256gb']);
  assert.deepEqual(tokenize('MacBook Pro 14" M3 — 512GB'), ['macbook', 'pro', '14', 'm3', '512gb']);
  assert.deepEqual(tokenize(''), []);
  assert.deepEqual(tokenize(null), []);
});

test('buildSearchUrl ANDs search clauses in double parens and adds the condition filter', () => {
  const { url, query } = buildSearchUrl('iPhone 17 Pro 256GB', { apiKey: 'KEY123', condition: 'new' });
  assert.ok(url.startsWith('https://api.bestbuy.com/v1/products('));
  assert.ok(url.includes('search=iphone'));
  assert.ok(url.includes('search=17'));
  assert.ok(url.includes('search=256gb'));
  assert.ok(url.includes('condition=new'));
  assert.ok(url.includes('apiKey=KEY123'));
  assert.ok(url.includes('format=json'));
  // the redacted query description must NOT leak the apiKey
  assert.ok(!query.includes('KEY123'));
  assert.equal(query, 'iphone 17 pro 256gb [new]');
});

test('buildSearchUrl omits the condition clause when condition is null', () => {
  const { url } = buildSearchUrl('PS5 Pro', { apiKey: 'K', condition: null });
  assert.ok(!url.includes('condition='));
});

test('extractPrices prefers salePrice, falls back to regularPrice, drops out-of-window junk', () => {
  const json = {
    products: [
      { salePrice: 999.99, regularPrice: 1099 },
      { salePrice: 0, regularPrice: 1299 }, // salePrice falsy -> use regularPrice
      { salePrice: 2 }, // below minP -> dropped
      { salePrice: 99999 }, // above maxP -> dropped
      { regularPrice: 'nope' }, // non-numeric -> dropped
    ],
  };
  assert.deepEqual(extractPrices(json, { minP: 5, maxP: 6000 }), [999.99, 1299]);
});

test('extractPrices is empty for a missing/blank payload', () => {
  assert.deepEqual(extractPrices(null), []);
  assert.deepEqual(extractPrices({}), []);
  assert.deepEqual(extractPrices({ products: [] }), []);
});

test('source disables itself with no API key (no crash, no prices)', async () => {
  const src = createBestBuySource({ sources: { bestbuy: { enabled: true } } }, {});
  assert.equal(src.enabled, false);
  const r = await src.fetchComps({ displayName: 'iPhone 17' });
  assert.deepEqual(r.prices, []);
  assert.equal(r.basis, 'disabled');
  assert.equal(r.basisEligible, false);
});

test('Best Buy is a REFERENCE source by default (basisEligible=false)', () => {
  const ref = createBestBuySource(
    { sources: { bestbuy: { enabled: true } } },
    { BESTBUY_API_KEY: 'x' }
  );
  assert.equal(ref.enabled, true);
  assert.equal(ref.basisEligible, false); // retail-new must not move the offer by default

  const asBasis = createBestBuySource(
    { sources: { bestbuy: { enabled: true, useAsBasis: true } } },
    { BESTBUY_API_KEY: 'x' }
  );
  assert.equal(asBasis.basisEligible, true);
});
