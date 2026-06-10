'use strict';
// ============================================================
// Money parsing / formatting.
//
// The frontend (form.html priceNum(), pricing.js, social/dump_pricing.js)
// reads prices by regex on strings like "$1,234". The exact display
// contract MUST be preserved or the quote tool silently shows "Contact".
// formatUSD() below produces that exact shape.
// ============================================================

/**
 * Parse a leading dollar amount out of a cell.
 *   "$1,241"        -> 1241
 *   "$1,050.50"     -> 1050.5
 *   "Contact"       -> null
 *   "50% off MSRP"  -> null
 *   "-$100 Active"  -> null   (no LEADING $ amount)
 */
function parseDollars(s) {
  const m = String(s == null ? '' : s).match(/^\s*\$\s*([\d,]+(?:\.\d+)?)/);
  return m ? Number(m[1].replace(/,/g, '')) : null;
}

/**
 * Format a number the way the existing data does: "$1,234" (no cents
 * when whole, grouped thousands). Matches api/pricing.js formatPrice().
 */
function formatUSD(n) {
  if (n == null || !Number.isFinite(Number(n))) return null;
  const num = Number(n);
  const whole = Math.round(num) === num;
  return (
    '$' +
    num.toLocaleString('en-US', {
      minimumFractionDigits: whole ? 0 : 2,
      maximumFractionDigits: whole ? 0 : 2,
    })
  );
}

/** Round to the nearest `step` dollars (step=1 → nearest dollar). */
function roundTo(n, step = 1) {
  if (n == null || !Number.isFinite(Number(n))) return null;
  const s = Number.isFinite(step) && step > 0 ? step : 1;
  return Math.round(Number(n) / s) * s;
}

/**
 * Best-effort extraction of ALL dollar amounts embedded anywhere in a
 * blob of HTML/text — used by competitor adapters that don't have a
 * precise selector. Returns number[] (may be empty).
 * NOTE: deliberately conservative; verify per-site before trusting.
 */
function extractDollarsFromHtml(html) {
  const out = [];
  const re = /\$\s*([\d,]{1,7}(?:\.\d{1,2})?)/g;
  let m;
  while ((m = re.exec(String(html || ''))) !== null) {
    const v = Number(m[1].replace(/,/g, ''));
    if (Number.isFinite(v)) out.push(v);
  }
  return out;
}

module.exports = { parseDollars, formatUSD, roundTo, extractDollarsFromHtml };
