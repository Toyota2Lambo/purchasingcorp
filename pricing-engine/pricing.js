'use strict';
// ============================================================
// PRICING CORE  —  market price  ->  our buy offer
//
// Pure functions only (no network, no fs) so they're trivially
// testable. See test/pricing.test.js.
//
//   raw   = basis * categoryMargin * conditionMultiplier
//   offer = clamp(raw, floor, marketMedian), then rounded
//
// Guard outcomes:
//   - sampleSize < minSampleSize         -> status 'needs_manual_price'
//   - raw below floor + behavior 'manual'-> status 'needs_manual_price'
//   - raw below floor + behavior 'clamp' -> offer raised to floor (flag)
//   - offer above market median          -> capped to median  (flag)
// ============================================================

const DEFAULTS = require('./config');
const { roundTo } = require('./lib/money');

/** Resolve the margin for a high-level category. */
function categoryMargin(category, cfg) {
  const m = cfg.margins.byCategory[category];
  return typeof m === 'number' ? m : cfg.margins.defaultCategory;
}

/** Resolve the multiplier for a condition tier. */
function conditionMultiplier(condition, cfg) {
  const c = cfg.margins.byCondition[condition];
  return typeof c === 'number' ? c : cfg.margins.defaultCondition;
}

/** Effective floor for a category (max of global and per-category). */
function floorFor(category, cfg) {
  const per = cfg.guards.perCategoryFloor && cfg.guards.perCategoryFloor[category];
  const global = cfg.guards.globalFloor || 0;
  return Math.max(global, typeof per === 'number' ? per : 0);
}

/** Pick the central market estimate per config (median | trimmedMean). */
function basisValue(summary, cfg) {
  const key = cfg.stats.priceBasis === 'trimmedMean' ? 'trimmedMean' : 'median';
  return summary ? summary[key] : null;
}

/**
 * Compute a single offer for one (category, condition) against a stats
 * summary {sampleSize, median, trimmedMean, ...}.
 *
 * Returns:
 * {
 *   status: 'priced' | 'needs_manual_price',
 *   offer: number | null,
 *   reason: string | null,
 *   basis: number | null,            // market estimate used
 *   marketMedian: number | null,     // ceiling reference
 *   applied: { categoryMargin, conditionMultiplier, floor, raw },
 *   flags: { lowSample, floored, ceilinged, belowFloorManual },
 * }
 */
function computeOffer({ summary, category, condition, config } = {}) {
  const cfg = config || DEFAULTS;
  const flags = { lowSample: false, floored: false, ceilinged: false, belowFloorManual: false };

  const n = summary ? summary.sampleSize : 0;
  const marketMedian = summary ? summary.median : null;
  const catM = categoryMargin(category, cfg);
  const condM = conditionMultiplier(condition, cfg);
  const floor = floorFor(category, cfg);

  // --- Guard 1: not enough sold comps -> never guess.
  if (!n || n < cfg.guards.minSampleSize) {
    flags.lowSample = true;
    return {
      status: 'needs_manual_price',
      offer: null,
      reason: `only ${n || 0} sold comps (< ${cfg.guards.minSampleSize})`,
      basis: basisValue(summary, cfg),
      marketMedian,
      applied: { categoryMargin: catM, conditionMultiplier: condM, floor, raw: null },
      flags,
    };
  }

  const basis = basisValue(summary, cfg);
  if (basis == null || !Number.isFinite(basis) || basis <= 0) {
    flags.lowSample = true;
    return {
      status: 'needs_manual_price',
      offer: null,
      reason: 'no usable market price',
      basis,
      marketMedian,
      applied: { categoryMargin: catM, conditionMultiplier: condM, floor, raw: null },
      flags,
    };
  }

  // --- Margin math.
  const raw = basis * catM * condM;

  // --- Guard 2: below floor.
  let offer = raw;
  if (raw < floor) {
    if (cfg.guards.belowFloorBehavior === 'manual') {
      flags.belowFloorManual = true;
      return {
        status: 'needs_manual_price',
        offer: null,
        reason: `computed $${raw.toFixed(2)} below floor $${floor}`,
        basis,
        marketMedian,
        applied: { categoryMargin: catM, conditionMultiplier: condM, floor, raw },
        flags,
      };
    }
    offer = floor; // 'clamp'
    flags.floored = true;
  }

  // --- Guard 3: never above market median.
  if (cfg.guards.neverAboveMarketMedian && marketMedian != null && offer > marketMedian) {
    offer = marketMedian;
    flags.ceilinged = true;
  }

  // --- Round to nice dollars.
  offer = roundTo(offer, cfg.stats.roundTo);

  // A clamp-up to the floor can still round below it if roundTo is coarse;
  // and the median ceiling wins ties. Re-assert the hard floor post-round
  // unless the ceiling forced us lower.
  if (!flags.ceilinged && offer < floor) offer = floor;

  return {
    status: 'priced',
    offer,
    reason: null,
    basis,
    marketMedian,
    applied: { categoryMargin: catM, conditionMultiplier: condM, floor, raw },
    flags,
  };
}

/**
 * Compute offers for every condition tier of a variant in one shot.
 * Returns { byCondition: { 'like new': result, good: result, ... },
 *           headline: { condition, offer, status } }.
 * The headline (config.margins.headlineCondition) is what the existing
 * "Up to $X" quote UI shows.
 */
function computeVariantOffers({ summary, category, config } = {}) {
  const cfg = config || DEFAULTS;
  const byCondition = {};
  for (const condition of Object.keys(cfg.margins.byCondition)) {
    byCondition[condition] = computeOffer({ summary, category, condition, config: cfg });
  }
  const hc = cfg.margins.headlineCondition;
  const h = byCondition[hc] || computeOffer({ summary, category, condition: hc, config: cfg });
  return {
    byCondition,
    headline: { condition: hc, offer: h.offer, status: h.status, reason: h.reason },
  };
}

module.exports = {
  computeOffer,
  computeVariantOffers,
  categoryMargin,
  conditionMultiplier,
  floorFor,
  basisValue,
};
