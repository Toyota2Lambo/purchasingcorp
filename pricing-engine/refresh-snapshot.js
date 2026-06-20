'use strict';
// ============================================================
// REFRESH SNAPSHOT, re-generate the site's static fallback
// (/pricing-data.js) from the LIVE /api/pricing endpoint.
//
// Why: pricing-data.js is what the quote tool falls back to when the
// API is down. It was hand-maintained and goes stale (migration plan,
// flag #10). This keeps it in lockstep with the sheet without touching
// the engine's market-data path at all.
//
//   node refresh-snapshot.js                      # fetch + write ../pricing-data.js
//   node refresh-snapshot.js --dry-run            # fetch + validate, write nothing
//   node refresh-snapshot.js --url http://...     # non-default endpoint
//
// Zero dependencies. Node >= 18 (global fetch).
// ============================================================

const fs = require('fs');
const path = require('path');
const { toPricingDataJs } = require('./output');

const DEFAULT_URL = 'https://purchasingcorp.com/api/pricing';
const TARGET = path.join(__dirname, '..', 'pricing-data.js');

// Same labels the current pricing-data.js ships; consumers read
// window.CATEGORY_LABELS for tab names.
const CATEGORY_LABELS = {
  iphone: 'iPhone',
  'macbook-pro': 'MacBook Pro',
  'macbook-air': 'MacBook Air',
  'mac-mini': 'Mac Mini',
  'apple-watch': 'Apple Watch',
  ipad: 'iPad',
  consoles: 'Consoles',
  accessories: 'Accessories',
};

/** Mirror api/pricing.js's sanity thresholds so a broken parse can't
 *  overwrite a good snapshot. Throws with a reason on failure. */
function validate(data) {
  if (!data || typeof data !== 'object') throw new Error('no data object in response');
  for (const cat of Object.keys(CATEGORY_LABELS)) {
    const c = data[cat];
    if (!c || !Array.isArray(c.headers) || !Array.isArray(c.rows)) {
      throw new Error(`category "${cat}" missing or malformed`);
    }
    for (const row of c.rows) {
      if (!Array.isArray(row) || row.length !== 3 || typeof row[0] !== 'string') {
        throw new Error(`category "${cat}" has a malformed row: ${JSON.stringify(row)}`);
      }
    }
  }
  if (data.iphone.rows.length < 10) throw new Error(`only ${data.iphone.rows.length} iPhone rows`);
  if (data.consoles.rows.length < 5) throw new Error(`only ${data.consoles.rows.length} console rows`);
}

async function main() {
  const args = process.argv.slice(2);
  const dryRun = args.includes('--dry-run');
  const urlIx = args.indexOf('--url');
  const url = urlIx !== -1 ? args[urlIx + 1] : DEFAULT_URL;

  console.log(`fetching ${url} ...`);
  const r = await fetch(url, { headers: { accept: 'application/json' } });
  if (!r.ok) throw new Error(`${url} returned HTTP ${r.status}`);
  const body = await r.json();
  if (!body.ok) throw new Error(`API reported failure: ${body.error || 'unknown'}`);

  validate(body.data);
  const rowCount = Object.values(body.data).reduce((n, c) => n + c.rows.length, 0);
  console.log(`ok: ${rowCount} rows across ${Object.keys(body.data).length} categories (updated ${body.updated})`);

  const js = toPricingDataJs(body.data, CATEGORY_LABELS, {
    updated: body.updated || new Date().toISOString(),
    sources: ['live /api/pricing (Google Sheet)'],
  });

  if (dryRun) {
    console.log(`dry-run: would write ${js.length} bytes to ${TARGET}`);
    return;
  }
  fs.writeFileSync(TARGET, js);
  console.log(`wrote ${TARGET}`);
}

main().catch((e) => {
  console.error(`refresh-snapshot failed: ${e.message}`);
  process.exit(1);
});
