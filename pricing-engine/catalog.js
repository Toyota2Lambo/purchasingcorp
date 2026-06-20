'use strict';
// ============================================================
// CATALOG, the list of devices we price.
//
// The set of devices (the model list) still comes from the site's
// /pricing-data.js, we evaluate it in a fake `window` sandbox (the
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

// Product-family anchors prepended to the eBay query when the sheet's
// display name omits them. Watch / Mac mini / MacBook rows are written as
// bare specs ("ULTRA 3 GEN 49MM ...", "MCX44LL/A PRO 24GB Memory 512GB"),
// so an eBay search has no product to anchor on and returns nothing.
// iPhone / iPad / accessories names already carry their family and price
// fine, so they are intentionally left untouched (see buildQuery).
const QUERY_FAMILY = {
  'macbook-pro': 'MacBook Pro',
  'macbook-air': 'MacBook Air',
  'mac-mini': 'Mac mini',
  'apple-watch': 'Apple Watch',
};

// Strip marketing/noise tokens that don't appear in real eBay listing
// titles and only shrink the comp set. Storage, screen size, chip and
// "(Nth gen)" are deliberately preserved (those narrow correctly).
function cleanQueryName(name) {
  let s = String(name);
  // Apple order/part numbers ("MCX44LL/A") almost never appear in eBay
  // listing titles — drop them before they poison the search. The {4,8}
  // length and trailing "/A"|"A" keep this from ever eating an "M4"/"M5"
  // chip token.
  s = s.replace(/\bM[A-Z0-9]{4,8}\/?A\b/g, ' ');
  s = s.replace(/\(\s*new model\s*\)/gi, ' '); // "( NEW MODEL)"
  s = s.replace(/\b20\d{2}\b/g, ' ');          // standalone year e.g. 2025
  s = s.replace(/\bmemory\b/gi, ' ');          // "24GB Memory 512GB"
  s = s.replace(/\bgeneration\b/gi, ' ');
  s = s.replace(/\b\d+gen\b/gi, ' ');          // redundant "3gen" next to "3RD"
  // standalone "GEN" but NOT the "gen" inside "(6th gen)" (preserved).
  s = s.replace(/\bgen\b(?!\s*\))/gi, ' ');
  s = s.replace(/\s*\/\s*/g, ' ');             // "BLACK / NATURAL" -> spaces
  s = s.replace(/\s{2,}/g, ' ').trim();
  return s;
}

// Build the eBay search query for a variant. Only the families in
// QUERY_FAMILY are normalized; every other category keeps its exact
// historical query so the 200+ already-working variants don't regress.
function buildQuery(siteCategory, displayName) {
  const family = QUERY_FAMILY[siteCategory];
  if (!family) return displayName;
  const cleaned = cleanQueryName(displayName);
  const hasFamily = new RegExp(family.replace(/\s+/g, '\\s+'), 'i').test(cleaned);
  return (hasFamily ? cleaned : `${family} ${cleaned}`).trim();
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
          query: `${buildQuery(siteCategory, displayName)} unlocked`,
          sourcePrice: row[1], sourceNote: null,
        }));
        variants.push(makeVariant({
          siteCategory, marginCategory, displayName, model, storage,
          carrier: 'locked', outputColumn: 2,
          query: `${buildQuery(siteCategory, displayName)} carrier locked`,
          sourcePrice: row[2], sourceNote: null,
        }));
      } else {
        variants.push(makeVariant({
          siteCategory, marginCategory, displayName, model, storage,
          carrier: 'n/a', outputColumn: 1,
          query: buildQuery(siteCategory, displayName),
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
