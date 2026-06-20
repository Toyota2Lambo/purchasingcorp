'use strict';
// ============================================================
// CIRCUIT BREAKER, gate auto-publishing of refreshed offers.
//
//   node check-deltas.js <old-offers.js> <new-offers.js>
//
// Compares two generated api/_pricing/offers.generated.js files and
// exits non-zero (blocking the CI commit) when the new run looks like
// a bad scrape rather than a market move:
//
//   - any single offer moved more than MAX_PCT (default 30%)
//   - the number of priced (offer-bearing) variants dropped more than
//     MAX_SHRINK (default 20%)
//
// Exit codes: 0 publish · 1 tripped · 2 usage/parse error.
// Thresholds via env: PRICE_MAX_PCT_DELTA, PRICE_MAX_SHRINK_PCT.
// ============================================================

const fs = require('fs');
const vm = require('vm');

const MAX_PCT = Number(process.env.PRICE_MAX_PCT_DELTA || 30);
const MAX_SHRINK = Number(process.env.PRICE_MAX_SHRINK_PCT || 20);

function loadOffers(file) {
  // The artifact is an ES module (`export default {...}`); evaluate the
  // object literal without needing an ESM loader.
  const src = fs.readFileSync(file, 'utf8');
  const m = src.match(/export default (\{[\s\S]*\});\s*$/);
  if (!m) throw new Error(`${file}: not a generated offers module`);
  return vm.runInNewContext(`(${m[1]})`);
}

/** Flatten to "category|row|slot|tier" -> offer (numbers only). */
function flatten(offers) {
  const out = new Map();
  for (const [cat, rows] of Object.entries(offers.categories || {})) {
    for (const [row, slots] of Object.entries(rows)) {
      for (const [slot, tiers] of Object.entries(slots)) {
        for (const [tier, val] of Object.entries(tiers)) {
          if (typeof val === 'number') out.set(`${cat}|${row}|${slot}|${tier}`, val);
        }
      }
    }
  }
  return out;
}

function main() {
  const [oldFile, newFile] = process.argv.slice(2);
  if (!oldFile || !newFile) {
    console.error('usage: node check-deltas.js <old-offers.js> <new-offers.js>');
    process.exit(2);
  }
  const oldMap = flatten(loadOffers(oldFile));
  const newMap = flatten(loadOffers(newFile));

  let worst = { key: null, pct: 0 };
  let compared = 0;
  for (const [key, oldVal] of oldMap) {
    const newVal = newMap.get(key);
    if (newVal == null) continue;
    compared++;
    const pct = Math.abs((newVal - oldVal) / oldVal) * 100;
    if (pct > Math.abs(worst.pct)) worst = { key, pct: ((newVal - oldVal) / oldVal) * 100, oldVal, newVal };
  }

  const shrinkPct = oldMap.size ? Math.max(0, ((oldMap.size - newMap.size) / oldMap.size) * 100) : 0;

  console.log(`[deltas] offers: ${oldMap.size} -> ${newMap.size} (shrink ${shrinkPct.toFixed(1)}%)`);
  console.log(`[deltas] compared ${compared}; worst move ${worst.pct.toFixed(1)}% (${worst.key || 'n/a'}${worst.key ? `: $${worst.oldVal} -> $${worst.newVal}` : ''})`);

  const trippedDelta = Math.abs(worst.pct) > MAX_PCT;
  const trippedShrink = shrinkPct > MAX_SHRINK;
  if (trippedDelta || trippedShrink) {
    if (trippedDelta) console.error(`[deltas] TRIPPED: |${worst.pct.toFixed(1)}%| > ${MAX_PCT}% on ${worst.key}`);
    if (trippedShrink) console.error(`[deltas] TRIPPED: priced offers shrank ${shrinkPct.toFixed(1)}% > ${MAX_SHRINK}%`);
    console.error('[deltas] NOT publishing, review the run artifact, then re-run manually or raise thresholds.');
    process.exit(1);
  }
  console.log('[deltas] ok to publish');
}

main();
