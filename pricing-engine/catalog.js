'use strict';
// ============================================================
// CATALOG — the list of devices we price.
//
// The set of devices (the model list) still comes from the site's
// /pricing-data.js — we evaluate it in a fake `window` sandbox (the
// same trick social/dump_pricing.js uses) and turn each display row
// into one or more structured VARIANTS (model + storage + carrier).
//
// Only the PRICES become dynamic; the catalog of which models exist
// is read from pricing-data.js until it's moved to its own file/DB
// (see PRICING_MIGRATION_PLAN.md phase 1).
// ============================================================

const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const PRICING_DATA = path.join(ROOT, 'pricing-data.js');

// site category slug -> margin category (config.margins.byCategory key)
const MARGIN_CATEGORY = {
  iphone: 'phones',
  'macbook-pro': 'laptops',
  'macbook-air': 'laptops',
  'mac-mini': 'laptops',
  'apple-watch': 'watches',
  ipad: 'tablets',
  consoles: 'consoles',
  accessories: 'audio',
};

/** Evaluate pricing-data.js and return { PRICING, CATEGORY_LABELS }. */
function loadPricingData(file) {
  const src = file || PRICING_DATA;
  if (!fs.existsSync(src)) throw new Error(`pricing-data.js not found at ${src}`);
  const code = fs.readFileSync(src, 'utf8');
  const win = {};
  // eslint-disable-next-line no-new-func
  new Function('window', code)(win);
  return { PRICING: win.PRICING || {}, CATEGORY_LABELS: win.CATEGORY_LABELS || {} };
}

/** Best-effort storage size (prefers TB, then the largest GB token). */
function extractStorage(name) {
  const tokens = String(name).match(/(\d+(?:\.\d+)?)\s?(TB|GB)/gi) || [];
  if (!tokens.length) return null;
  const tb = tokens.find((t) => /TB/i.test(t));
  if (tb) return tb.replace(/\s+/g, '').toUpperCase();
  // largest GB
  let best = null;
  let bestN = -1;
  for (const t of tokens) {
    const n = parseFloat(t);
    if (n > bestN) { bestN = n; best = t; }
  }
  return best ? best.replace(/\s+/g, '').toUpperCase() : null;
}

/** Rough model string = name with trailing storage stripped. */
function extractModel(name, storage) {
  if (!storage) return name;
  return String(name).replace(new RegExp(`\\s*${storage.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}.*$`, 'i'), '').trim() || name;
}

/**
 * Build the variant list from PRICING.
 * iPhones produce TWO variants (unlocked -> output col 1, locked -> col 2).
 * Every other category produces ONE variant (price -> col 1, note kept).
 */
function buildCatalog(opts = {}) {
  const { PRICING, CATEGORY_LABELS } = opts.pricing || loadPricingData(opts.file);
  const variants = [];

  for (const [siteCategory, data] of Object.entries(PRICING)) {
    const rows = (data && Array.isArray(data.rows)) ? data.rows : [];
    const marginCategory = MARGIN_CATEGORY[siteCategory] || 'other';

    for (const row of rows) {
      const displayName = String(row[0] || '').trim();
      if (!displayName) continue;
      const storage = extractStorage(displayName);
      const model = extractModel(displayName, storage);

      if (siteCategory === 'iphone') {
        variants.push(makeVariant({
          siteCategory, marginCategory, displayName, model, storage,
          carrier: 'unlocked', outputColumn: 1,
          query: `${displayName} unlocked`,
          sourcePrice: row[1], sourceNote: null,
        }));
        variants.push(makeVariant({
          siteCategory, marginCategory, displayName, model, storage,
          carrier: 'locked', outputColumn: 2,
          query: `${displayName} carrier locked`,
          sourcePrice: row[2], sourceNote: null,
        }));
      } else {
        variants.push(makeVariant({
          siteCategory, marginCategory, displayName, model, storage,
          carrier: 'n/a', outputColumn: 1,
          query: displayName,
          sourcePrice: row[1],
          sourceNote: row[2] != null ? String(row[2]) : null, // preserve note col
        }));
      }
    }
  }

  return { variants, categoryLabels: CATEGORY_LABELS, siteCategories: Object.keys(PRICING) };
}

function makeVariant(v) {
  return {
    variantKey: `${v.siteCategory}|${v.displayName}|${v.carrier}`,
    rowName: v.displayName, // groups the two iPhone carriers into one output row
    ...v,
  };
}

module.exports = { loadPricingData, buildCatalog, extractStorage, extractModel, MARGIN_CATEGORY };
