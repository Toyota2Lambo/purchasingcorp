'use strict';
// ============================================================
// Pure statistics helpers. No I/O. Heavily unit-tested.
// ============================================================

/**
 * Coerce to a finite number or return null. Strict on purpose:
 * null/undefined/''/booleans/objects are NOT prices (Number(null)===0
 * and Number('')===0 would otherwise leak in as bogus $0 samples).
 */
function toNum(x) {
  if (x === null || x === undefined) return null;
  if (typeof x === 'number') return Number.isFinite(x) ? x : null;
  if (typeof x === 'string') {
    const t = x.trim();
    if (t === '') return null;
    const n = Number(t);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

/** Keep only finite numbers from an array. */
function clean(values) {
  return (Array.isArray(values) ? values : [])
    .map(toNum)
    .filter((n) => n != null);
}

/** Number of usable samples. */
function sampleSize(values) {
  return clean(values).length;
}

/**
 * Median of an array. Average of the two middle values when the
 * count is even. Returns null for an empty array.
 */
function median(values) {
  const a = clean(values).sort((x, y) => x - y);
  const n = a.length;
  if (n === 0) return null;
  const mid = Math.floor(n / 2);
  return n % 2 ? a[mid] : (a[mid - 1] + a[mid]) / 2;
}

/** Plain arithmetic mean, or null for empty. */
function mean(values) {
  const a = clean(values);
  if (a.length === 0) return null;
  return a.reduce((s, n) => s + n, 0) / a.length;
}

/**
 * Trimmed mean: drop floor(n * trimFraction) values from EACH end
 * (after sorting), then average what remains.
 *
 *  - trimFraction defaults to 0.10 (drop top & bottom 10%).
 *  - If trimming would remove every element, we fall back to the
 *    plain mean of the original cleaned set (never return NaN/null
 *    for a non-empty input just because it's tiny).
 */
function trimmedMean(values, trimFraction = 0.1) {
  const a = clean(values).sort((x, y) => x - y);
  const n = a.length;
  if (n === 0) return null;

  const f = Number.isFinite(trimFraction) ? Math.min(Math.max(trimFraction, 0), 0.5) : 0.1;
  const drop = Math.floor(n * f);
  const kept = a.slice(drop, n - drop);
  if (kept.length === 0) return mean(a); // too few to trim → plain mean
  return kept.reduce((s, n2) => s + n2, 0) / kept.length;
}

/**
 * One call that returns the full summary used by the pricing core.
 * { sampleSize, median, trimmedMean, min, max }
 */
function summarize(values, trimFraction = 0.1) {
  const a = clean(values);
  return {
    sampleSize: a.length,
    median: median(a),
    trimmedMean: trimmedMean(a, trimFraction),
    min: a.length ? Math.min(...a) : null,
    max: a.length ? Math.max(...a) : null,
  };
}

module.exports = { toNum, clean, sampleSize, median, mean, trimmedMean, summarize };
