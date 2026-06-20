'use strict';
// ============================================================
// Competitor buyback source (FALLBACK).
//
// Scrapes publicly listed buyback OFFERS from competitor sites to
// corroborate eBay. Each site needs an adapter in config
// (sources.competitors.sites). Adapters are OFF until you add them.
//
// robots.txt is ENFORCED for every site, every run:
//   - we fetch /robots.txt and check our path against it;
//   - if disallowed (or robots is unreachable / 5xx), the site is
//     SKIPPED with a logged reason, never scraped.
//
// This is best-effort corroboration. Verify each site's markup AND
// terms of service before enabling. Selectors/patterns live in the
// adapter's extract() so they're easy to fix when a site changes.
// ============================================================

const { getText } = require('../lib/http');
const { fetchRobots } = require('../lib/robots');
const { extractDollarsFromHtml } = require('../lib/money');

function createCompetitorSource(cfg) {
  const cc = (cfg && cfg.sources && cfg.sources.competitors) || {};
  const userAgent = cc.userAgent || 'PurchasingCorpPriceBot/1.0';
  const sites = Array.isArray(cc.sites) ? cc.sites : [];
  const enabled = !!cc.enabled && sites.length > 0;

  // Cache one robots checker per site host for the run.
  const robotsCache = new Map();
  async function checkerFor(site) {
    if (robotsCache.has(site.name)) return robotsCache.get(site.name);
    const p = fetchRobots(site.baseUrl, userAgent).catch(() => null);
    robotsCache.set(site.name, p);
    return p;
  }

  async function scrapeSite(site, variant) {
    let url;
    try {
      url = site.buildUrl(variant);
    } catch (e) {
      return { site: site.name, prices: [], skipped: 'buildUrl_error', error: e.message };
    }

    const robots = await checkerFor(site);
    if (!robots) return { site: site.name, prices: [], skipped: 'robots_unavailable' };
    if (!robots.isAllowed(url)) {
      return { site: site.name, prices: [], skipped: `robots_disallow (${robots.mode})`, url };
    }

    try {
      const res = await getText(url, { userAgent, retries: 1, timeoutMs: 12000 });
      if (!res.ok) return { site: site.name, prices: [], skipped: `http_${res.status}`, url };
      const extract = typeof site.extract === 'function' ? site.extract : extractDollarsFromHtml;
      const prices = (extract(res.body, variant) || []).filter((n) => Number.isFinite(n) && n > 0);
      return { site: site.name, prices, url };
    } catch (e) {
      return { site: site.name, prices: [], skipped: 'fetch_error', url, error: e.message };
    }
  }

  return {
    name: 'competitors',
    enabled,
    /** Returns { source, prices:number[], basis, perSite:[...] } */
    async fetchComps(variant) {
      if (!enabled) return { source: 'competitors', prices: [], basis: 'disabled', perSite: [] };
      const perSite = [];
      const prices = [];
      for (const site of sites) {
        const r = await scrapeSite(site, variant);
        perSite.push(r);
        for (const p of r.prices) prices.push(p);
      }
      return { source: 'competitors', prices, basis: 'competitor_offer', perSite };
    },
  };
}

module.exports = { createCompetitorSource };
