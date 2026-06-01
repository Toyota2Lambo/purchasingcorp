#!/usr/bin/env node
// ============================================================
// PURCHASINGCORP — pricing-data.js  ->  social/pricing.json
// ============================================================
// The site's prices live in /pricing-data.js as a browser global
// (window.PRICING + window.CATEGORY_LABELS) — JS object literals with
// unquoted keys, single quotes, trailing commas and comments, so they
// are NOT valid JSON. This script evaluates that file in a tiny
// sandbox (a fake `window`) and emits a clean pricing.json the Python
// generator can read.
//
// It also computes a `top` payout per category (the highest real dollar
// figure and the model it belongs to). That single number is what the
// generator is allowed to headline with — it can never invent a price,
// only quote one that exists here.
//
// Categories that are quote-only ("Contact", "50% off MSRP", etc.) get
// top: null, signalling the generator to say "Contact for your number"
// instead of fabricating a figure.
//
// Run from anywhere:  node social/dump_pricing.js
// ============================================================

'use strict';

const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const SRC = path.join(ROOT, 'pricing-data.js');
const OUT = path.join(__dirname, 'pricing.json');

// Pull a numeric dollar amount out of a cell like "$1,241" -> 1241.
// Returns null for "Contact", "50% off MSRP", "—", "-$100 Active", etc.
// (We only treat a leading "$" amount as a real, quotable payout.)
function dollars(s) {
  const m = String(s == null ? '' : s).match(/^\s*\$\s*([\d,]+(?:\.\d+)?)/);
  return m ? Number(m[1].replace(/,/g, '')) : null;
}

function main() {
  if (!fs.existsSync(SRC)) {
    console.error(`[dump_pricing] source not found: ${SRC}`);
    process.exit(1);
  }

  const code = fs.readFileSync(SRC, 'utf8');

  // Evaluate the site file with a fake `window`. pricing-data.js does
  // `window.PRICING = {...}` / `window.CATEGORY_LABELS = {...}`, so after
  // running the body our local object is populated. Using new Function
  // (instead of polluting the global scope) keeps this contained.
  const win = {};
  try {
    // eslint-disable-next-line no-new-func
    new Function('window', code)(win);
  } catch (err) {
    console.error('[dump_pricing] failed to evaluate pricing-data.js:', err.message);
    process.exit(1);
  }

  const PRICING = win.PRICING || {};
  const LABELS = win.CATEGORY_LABELS || {};

  const categories = {};
  for (const [slug, data] of Object.entries(PRICING)) {
    const headers = Array.isArray(data.headers) ? data.headers : [];
    const rows = Array.isArray(data.rows) ? data.rows : [];

    // Find the single highest real dollar figure anywhere in this
    // category (scan every price column, skip the model column 0).
    let top = null;
    for (const row of rows) {
      for (let i = 1; i < row.length; i++) {
        const v = dollars(row[i]);
        if (v != null && (top == null || v > top.value)) {
          top = {
            model: row[0],
            price: row[i],
            value: v,
            column: headers[i] || `col${i}`,
          };
        }
      }
    }

    categories[slug] = {
      label: LABELS[slug] || slug,
      headers,
      row_count: rows.length,
      quote_only: top == null, // true for iPad / accessories ("Contact")
      top,                      // null when the whole category is quote-only
      rows,
    };
  }

  const out = {
    generated_at: new Date().toISOString(),
    source: 'pricing-data.js',
    category_count: Object.keys(categories).length,
    category_labels: LABELS,
    categories,
  };

  fs.writeFileSync(OUT, JSON.stringify(out, null, 2));

  const withPrices = Object.values(categories).filter((c) => !c.quote_only).length;
  console.log(
    `[dump_pricing] wrote ${path.relative(ROOT, OUT)} — ` +
    `${out.category_count} categories (${withPrices} with live dollar prices)`
  );
}

main();
