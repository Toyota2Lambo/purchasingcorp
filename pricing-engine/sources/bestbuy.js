'use strict';
// ============================================================
// Best Buy source.
//
// Best Buy's Developer API (Products API) returns CURRENT RETAIL
// prices for NEW (and sometimes open-box / refurb) inventory:
//   https://api.bestbuy.com/v1/products(<filter>)?apiKey=...&format=json
//
// IMPORTANT, what this signal IS and ISN'T:
//   * It is an authoritative *new retail* anchor for a device.
//   * It is NOT a used/sold comp. A buyback offer should sit well
//     below it. Dumping retail-new prices into the same median pool
//     as eBay used/sold comps would inflate every offer.
//
// So by default Best Buy is a REFERENCE source: its prices are
// fetched and RECORDED (audit trail in data/) but flagged
// basisEligible:false, so they do NOT move the offer. eBay's
// used/sold median stays the basis. Flip config.sources.bestbuy
// .useAsBasis = true only if you deliberately want a
// "offer = % of current retail" model.
//
// Auth: a single API key passed as the ?apiKey= query parameter
// (Best Buy's required mechanism, there is no header/OAuth option):
//   BESTBUY_API_KEY=...    (from https://developer.bestbuy.com)
// No key -> source disables itself gracefully (no crash).
// ============================================================

const { getJson } = require('../lib/http');

const BASE = 'https://api.bestbuy.com/v1/products';
const SHOW = 'sku,name,salePrice,regularPrice,condition,modelNumber';

/** Break a display name into Best Buy search tokens (AND-matched). */
function tokenize(name) {
  return String(name == null ? '' : name)
    .toLowerCase()
    .match(/[a-z0-9]+/g) || [];
}

/**
 * Build the Products API request URL. The API ANDs multiple
 * `search=` clauses, wrapped in the documented double parens:
 *   /v1/products((search=iphone&search=17&search=pro))?apiKey=...
 * An optional `condition` attribute narrows to new/refurb/pre-owned.
 *
 * Returns { url, query } where `query` is a redacted description
 * (the search terms, NOT the apiKey) safe to log / store in audit.
 */
function buildSearchUrl(name, { apiKey, condition = null, pageSize = 20, maxTokens = 8 } = {}) {
  const tokens = tokenize(name).slice(0, maxTokens);
  const clauses = tokens.map((t) => `search=${encodeURIComponent(t)}`);
  if (condition) clauses.push(`condition=${encodeURIComponent(condition)}`);
  const filter = `(${clauses.join('&')})`;
  const qs = new URLSearchParams({
    apiKey: apiKey || '',
    format: 'json',
    show: SHOW,
    pageSize: String(pageSize),
    sort: 'salePrice.dsc',
  }).toString();
  return {
    url: `${BASE}(${filter})?${qs}`,
    query: tokens.join(' ') + (condition ? ` [${condition}]` : ''),
  };
}

/** Pull usable prices out of a Products API JSON payload. */
function extractPrices(json, { minP = 5, maxP = 6000 } = {}) {
  const products = Array.isArray(json && json.products) ? json.products : [];
  return products
    .map((p) => {
      const sale = Number(p && p.salePrice);
      if (Number.isFinite(sale) && sale > 0) return sale;
      return Number(p && p.regularPrice);
    })
    .filter((n) => Number.isFinite(n) && n >= minP && n <= maxP);
}

function createBestBuySource(cfg, env = process.env) {
  const bbCfg = (cfg && cfg.sources && cfg.sources.bestbuy) || {};
  const apiKey = env.BESTBUY_API_KEY || '';
  const condition = bbCfg.condition === undefined ? 'new' : bbCfg.condition;
  const pageSize = bbCfg.pageSize || 20;
  const minP = bbCfg.minItemPrice ?? 5;
  const maxP = bbCfg.maxItemPrice ?? 6000;
  // Reference by default: recorded for audit but it does NOT move the offer.
  const useAsBasis = !!bbCfg.useAsBasis;

  const enabled = !!(bbCfg.enabled && apiKey);

  // One run-scoped cache keyed by the search description, so the two
  // iPhone carrier variants (same retail device) don't double-spend
  // Best Buy's rate limit on an identical query.
  const cache = new Map();

  async function lookup(name) {
    const primary = buildSearchUrl(name, { apiKey, condition, pageSize });
    if (cache.has(primary.query)) return cache.get(primary.query);

    const p = (async () => {
      let json = await getJson(primary.url, { retries: 1 });
      let prices = extractPrices(json, { minP, maxP });
      let note;
      // If a condition filter zeroed it out, relax once (mirrors eBay's
      // insights->browse fallback) so a too-strict filter isn't fatal.
      if (prices.length === 0 && condition) {
        const relaxed = buildSearchUrl(name, { apiKey, condition: null, pageSize });
        json = await getJson(relaxed.url, { retries: 1 });
        prices = extractPrices(json, { minP, maxP });
        note = 'condition_filter_relaxed';
      }
      return { prices, query: primary.query, note };
    })();

    cache.set(primary.query, p);
    return p;
  }

  return {
    name: 'bestbuy',
    enabled,
    basisEligible: useAsBasis,
    /** Returns { source, prices:number[], basis, basisEligible, query, error? } */
    async fetchComps(variant) {
      if (!enabled) {
        return { source: 'bestbuy', prices: [], basis: 'disabled', basisEligible: false, query: '' };
      }
      // Use the clean device name (no carrier suffix), Best Buy retail
      // is carrier-agnostic; variant.query carries eBay-specific suffixes.
      const name = variant.displayName || variant.rowName || variant.query;
      try {
        const out = await lookup(name);
        return {
          source: 'bestbuy',
          prices: out.prices,
          basis: 'bestbuy_retail',
          basisEligible: useAsBasis,
          query: out.query,
          note: out.note,
        };
      } catch (e) {
        return {
          source: 'bestbuy',
          prices: [],
          basis: 'error',
          basisEligible: false,
          query: name,
          error: e.message,
        };
      }
    },
  };
}

module.exports = { createBestBuySource, buildSearchUrl, extractPrices, tokenize, BASE };
