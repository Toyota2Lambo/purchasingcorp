'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { computeOffer, computeVariantOffers } = require('../pricing');

// A deterministic config so these tests assert the MATH + GUARD logic,
// not the (editable) business numbers in config.js.
function baseCfg() {
  return {
    margins: {
      byCategory: { phones: 0.75, laptops: 0.72, consoles: 0.7 },
      defaultCategory: 0.68,
      byCondition: { 'like new': 1.0, good: 0.9, fair: 0.75, broken: 0.4 },
      defaultCondition: 0.9,
      headlineCondition: 'like new',
    },
    guards: {
      globalFloor: 20,
      perCategoryFloor: { phones: 25, consoles: 30 },
      neverAboveMarketMedian: true,
      minSampleSize: 5,
      belowFloorBehavior: 'clamp',
    },
    stats: { trimFraction: 0.1, priceBasis: 'median', roundTo: 1 },
  };
}
const summary = (sampleSize, median) => ({ sampleSize, median, trimmedMean: median });

// ---------------------------------------------------------------
// MARGIN MATH
// ---------------------------------------------------------------
test('margin math: offer = median * categoryMargin * conditionMultiplier', () => {
  const r = computeOffer({ summary: summary(20, 1000), category: 'phones', condition: 'like new', config: baseCfg() });
  assert.equal(r.status, 'priced');
  assert.equal(r.offer, 750); // 1000 * 0.75 * 1.0
  assert.equal(r.applied.raw, 750);
});

test('condition tiers scale the offer down monotonically', () => {
  const cfg = baseCfg();
  const ln = computeOffer({ summary: summary(20, 1000), category: 'phones', condition: 'like new', config: cfg }).offer;
  const gd = computeOffer({ summary: summary(20, 1000), category: 'phones', condition: 'good', config: cfg }).offer;
  const br = computeOffer({ summary: summary(20, 1000), category: 'phones', condition: 'broken', config: cfg }).offer;
  assert.equal(gd, 675); // 1000 * 0.75 * 0.9
  assert.equal(br, 300); // 1000 * 0.75 * 0.4
  assert.ok(ln > gd && gd > br);
});

test('category margin is selected correctly (laptops != phones)', () => {
  const cfg = baseCfg();
  const phone = computeOffer({ summary: summary(20, 1000), category: 'phones', condition: 'like new', config: cfg }).offer;
  const laptop = computeOffer({ summary: summary(20, 1000), category: 'laptops', condition: 'like new', config: cfg }).offer;
  assert.equal(phone, 750);
  assert.equal(laptop, 720); // 1000 * 0.72
});

test('unknown category falls back to defaultCategory margin', () => {
  const r = computeOffer({ summary: summary(20, 1000), category: 'mystery', condition: 'like new', config: baseCfg() });
  assert.equal(r.offer, 680); // 1000 * 0.68
});

test('priceBasis=trimmedMean uses the trimmed mean instead of median', () => {
  const cfg = baseCfg();
  cfg.stats.priceBasis = 'trimmedMean';
  const r = computeOffer({
    summary: { sampleSize: 20, median: 1000, trimmedMean: 800 },
    category: 'phones', condition: 'like new', config: cfg,
  });
  assert.equal(r.basis, 800);
  assert.equal(r.offer, 600); // 800 * 0.75 * 1.0
});

test('offers round to config.stats.roundTo', () => {
  const cfg = baseCfg();
  cfg.stats.roundTo = 5;
  const r = computeOffer({ summary: summary(20, 1003), category: 'laptops', condition: 'good', config: cfg });
  // raw = 1003 * 0.72 * 0.9 = 649.944 -> nearest 5 -> 650
  assert.equal(r.offer, 650);
});

// ---------------------------------------------------------------
// GUARD RAILS
// ---------------------------------------------------------------
test('guard: fewer than minSampleSize sold comps => needs manual price', () => {
  const r = computeOffer({ summary: summary(4, 1000), category: 'phones', condition: 'like new', config: baseCfg() });
  assert.equal(r.status, 'needs_manual_price');
  assert.equal(r.offer, null);
  assert.equal(r.flags.lowSample, true);
  assert.match(r.reason, /comps/);
});

test('guard: exactly minSampleSize is allowed (boundary)', () => {
  const r = computeOffer({ summary: summary(5, 1000), category: 'phones', condition: 'like new', config: baseCfg() });
  assert.equal(r.status, 'priced');
  assert.equal(r.offer, 750);
});

test('guard: zero samples => needs manual price', () => {
  const r = computeOffer({ summary: summary(0, null), category: 'phones', condition: 'good', config: baseCfg() });
  assert.equal(r.status, 'needs_manual_price');
  assert.equal(r.flags.lowSample, true);
});

test('guard: enough samples but no usable market price => needs manual', () => {
  const r = computeOffer({ summary: { sampleSize: 10, median: null, trimmedMean: null }, category: 'phones', condition: 'good', config: baseCfg() });
  assert.equal(r.status, 'needs_manual_price');
  assert.match(r.reason, /no usable market price/);
});

test('guard: floor clamp raises a too-low offer up to the floor', () => {
  // consoles floor 30; median 100, broken 0.4, margin 0.7 -> raw 28 (< 30)
  const r = computeOffer({ summary: summary(20, 100), category: 'consoles', condition: 'broken', config: baseCfg() });
  assert.equal(r.status, 'priced');
  assert.equal(r.offer, 30);
  assert.equal(r.flags.floored, true);
  assert.ok(r.applied.raw < 30);
});

test('guard: belowFloorBehavior="manual" flags instead of clamping', () => {
  const cfg = baseCfg();
  cfg.guards.belowFloorBehavior = 'manual';
  const r = computeOffer({ summary: summary(20, 100), category: 'consoles', condition: 'broken', config: cfg });
  assert.equal(r.status, 'needs_manual_price');
  assert.equal(r.offer, null);
  assert.equal(r.flags.belowFloorManual, true);
  assert.match(r.reason, /below floor/);
});

test('guard: never offer above the market median', () => {
  const cfg = baseCfg();
  cfg.margins.byCategory.phones = 1.1; // intentionally > 1 to push raw over median
  const r = computeOffer({ summary: summary(20, 500), category: 'phones', condition: 'like new', config: cfg });
  assert.equal(r.applied.raw, 550);
  assert.equal(r.offer, 500); // capped to median
  assert.equal(r.flags.ceilinged, true);
});

test('guard: neverAboveMarketMedian=false lets the offer exceed median', () => {
  const cfg = baseCfg();
  cfg.margins.byCategory.phones = 1.1;
  cfg.guards.neverAboveMarketMedian = false;
  const r = computeOffer({ summary: summary(20, 500), category: 'phones', condition: 'like new', config: cfg });
  assert.equal(r.offer, 550);
  assert.equal(r.flags.ceilinged, false);
});

test('property: a priced offer is always within [floor, marketMedian]', () => {
  const cfg = baseCfg();
  for (const median of [40, 100, 350, 999, 2500]) {
    for (const condition of ['like new', 'good', 'fair', 'broken']) {
      for (const category of ['phones', 'laptops', 'consoles']) {
        const r = computeOffer({ summary: summary(30, median), category, condition, config: cfg });
        if (r.status !== 'priced') continue;
        assert.ok(r.offer <= r.marketMedian + 1e-9, `offer ${r.offer} <= median ${r.marketMedian}`);
        if (!r.flags.ceilinged) {
          assert.ok(r.offer >= r.applied.floor, `offer ${r.offer} >= floor ${r.applied.floor}`);
        }
      }
    }
  }
});

// ---------------------------------------------------------------
// VARIANT (all tiers + headline)
// ---------------------------------------------------------------
test('computeVariantOffers returns every tier and a like-new headline', () => {
  const cfg = baseCfg();
  const v = computeVariantOffers({ summary: summary(20, 1000), category: 'phones', config: cfg });
  assert.deepEqual(Object.keys(v.byCondition).sort(), ['broken', 'fair', 'good', 'like new']);
  assert.equal(v.headline.condition, 'like new');
  assert.equal(v.headline.offer, 750);
  assert.equal(v.headline.offer, v.byCondition['like new'].offer);
});

test('computeVariantOffers headline is manual when samples are too few', () => {
  const v = computeVariantOffers({ summary: summary(2, 1000), category: 'phones', config: baseCfg() });
  assert.equal(v.headline.status, 'needs_manual_price');
  assert.equal(v.headline.offer, null);
});
