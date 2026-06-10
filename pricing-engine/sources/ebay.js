'use strict';
// ============================================================
// eBay source.
//
// PRIMARY market signal. Two modes:
//
//   1) Marketplace Insights API (buy/marketplace_insights/v1_beta) ->
//      REAL sold/completed prices. Requires special access approval
//      from eBay. Enable with EBAY_MARKETPLACE_INSIGHTS=1 once granted.
//
//   2) Browse API (buy/browse/v1/item_summary/search) -> ACTIVE
//      listing prices, used as a PROXY when Insights isn't available.
//      Active asks run hotter than sold prices; tagged
//      basis:'active_listing_proxy' so you can compensate via margins.
//
// Auth: OAuth2 client-credentials. You register an app at
// developer.ebay.com and paste the keys into pricing-engine/.env:
//   EBAY_CLIENT_ID=...        (a.k.a. App ID / Client ID)
//   EBAY_CLIENT_SECRET=...    (a.k.a. Cert ID / Client Secret)
// No keys -> source disables itself gracefully (no crash).
// ============================================================

const { getJson, getText } = require('../lib/http');

const BASES = {
  production: 'https://api.ebay.com',
  sandbox: 'https://api.sandbox.ebay.com',
};

function createEbaySource(cfg, env = process.env) {
  const ebayCfg = (cfg && cfg.sources && cfg.sources.ebay) || {};
  const base = BASES[ebayCfg.env === 'sandbox' ? 'sandbox' : 'production'];
  const clientId = env.EBAY_CLIENT_ID || '';
  const clientSecret = env.EBAY_CLIENT_SECRET || '';
  const marketplaceId = ebayCfg.marketplaceId || 'EBAY_US';
  const useInsights = !!ebayCfg.useMarketplaceInsights;
  const conditionIds = ebayCfg.conditionIds || '3000|2000|2500'; // used + refurb
  const maxResults = ebayCfg.maxResults || 60;
  const minP = ebayCfg.minItemPrice ?? 5;
  const maxP = ebayCfg.maxItemPrice ?? 6000;

  const enabled = !!(ebayCfg.enabled && clientId && clientSecret);
  let tokenCache = { token: null, exp: 0 };

  async function getToken() {
    const now = Date.now();
    if (tokenCache.token && now < tokenCache.exp - 60000) return tokenCache.token;
    const basic = Buffer.from(`${clientId}:${clientSecret}`).toString('base64');
    const body = new URLSearchParams({
      grant_type: 'client_credentials',
      scope: 'https://api.ebay.com/oauth/api_scope',
    }).toString();
    const res = await getText(`${base}/identity/v1/oauth2/token`, {
      method: 'POST',
      headers: {
        authorization: `Basic ${basic}`,
        'content-type': 'application/x-www-form-urlencoded',
      },
      body,
      retries: 1,
    });
    if (!res.ok) throw new Error(`eBay token failed: HTTP ${res.status} ${res.body.slice(0, 160)}`);
    const json = JSON.parse(res.body);
    tokenCache = { token: json.access_token, exp: now + (json.expires_in || 7200) * 1000 };
    return tokenCache.token;
  }

  function priceFilter() {
    return `price:[${minP}..${maxP}],priceCurrency:USD,conditionIds:{${conditionIds}}`;
  }

  async function fetchBrowse(query) {
    const token = await getToken();
    const url =
      `${base}/buy/browse/v1/item_summary/search` +
      `?q=${encodeURIComponent(query)}` +
      `&limit=${Math.min(maxResults, 200)}` +
      `&filter=${encodeURIComponent(priceFilter())}`;
    const json = await getJson(url, {
      headers: {
        authorization: `Bearer ${token}`,
        'X-EBAY-C-MARKETPLACE-ID': marketplaceId,
      },
      retries: 1,
    });
    const items = Array.isArray(json.itemSummaries) ? json.itemSummaries : [];
    const prices = items
      .map((it) => Number(it.price && it.price.value))
      .filter((n) => Number.isFinite(n) && n >= minP && n <= maxP);
    return { prices, basis: 'active_listing_proxy', count: prices.length };
  }

  async function fetchInsights(query) {
    const token = await getToken();
    const url =
      `${base}/buy/marketplace_insights/v1_beta/item_sales/search` +
      `?q=${encodeURIComponent(query)}` +
      `&limit=${Math.min(maxResults, 200)}` +
      `&filter=${encodeURIComponent(priceFilter())}`;
    const json = await getJson(url, {
      headers: {
        authorization: `Bearer ${token}`,
        'X-EBAY-C-MARKETPLACE-ID': marketplaceId,
      },
      retries: 1,
    });
    const items = Array.isArray(json.itemSales) ? json.itemSales : [];
    const prices = items
      .map((it) => Number(it.lastSoldPrice && it.lastSoldPrice.value))
      .filter((n) => Number.isFinite(n) && n >= minP && n <= maxP);
    return { prices, basis: 'sold', count: prices.length };
  }

  return {
    name: 'ebay',
    enabled,
    /** Returns { source, prices:number[], basis, query, error? } */
    async fetchComps(variant) {
      if (!enabled) return { source: 'ebay', prices: [], basis: 'disabled', query: variant.query };
      const query = variant.query;
      try {
        const out = useInsights ? await fetchInsights(query) : await fetchBrowse(query);
        return { source: 'ebay', prices: out.prices, basis: out.basis, query };
      } catch (e) {
        // If Insights is denied (403), fall back to Browse proxy automatically.
        if (useInsights && /\b40[13]\b/.test(String(e.message))) {
          try {
            const out = await fetchBrowse(query);
            return { source: 'ebay', prices: out.prices, basis: out.basis, query, note: 'insights_denied_fallback' };
          } catch (e2) {
            return { source: 'ebay', prices: [], basis: 'error', query, error: e2.message };
          }
        }
        return { source: 'ebay', prices: [], basis: 'error', query, error: e.message };
      }
    },
  };
}

module.exports = { createEbaySource, BASES };
