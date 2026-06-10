'use strict';
// ============================================================
// PurchasingCorp — Pricing Engine CONFIG
// ------------------------------------------------------------
// THIS is the one file you edit to tune offers. No code changes
// needed elsewhere. Everything here is read by pricing.js.
//
//   our_offer = median_sold * categoryMargin * conditionMultiplier
//   then clamped into [ floor , market_median ] and rounded.
//
// If we have fewer than `guards.minSampleSize` sold comps for a
// device, we DO NOT guess — it is flagged "needs manual price"
// and rendered as "Contact" in the quote tool (which already has
// a hand-pricing path for that string).
// ============================================================

module.exports = {
  // ----------------------------------------------------------
  // 1) MARGIN — what fraction of the market price we offer.
  // ----------------------------------------------------------
  margins: {
    // Per high-level category. Site categories map to these in catalog.js
    // (iphone->phones, macbook*/mac-mini->laptops, ipad->tablets, etc.).
    byCategory: {
      phones: 0.75,
      laptops: 0.72,
      tablets: 0.70,
      consoles: 0.70,
      watches: 0.68,
      audio: 0.65,
      other: 0.65,
    },
    // Fallback when a category isn't listed above.
    defaultCategory: 0.68,

    // Per condition tier. Multiplies on top of the category margin.
    // These are the four tiers the requirement asked for. The quote
    // tool's wizard collects 6 condition labels; catalog.js maps them
    // onto these four (see CONDITION_ALIASES below).
    byCondition: {
      'like new': 1.0,
      good: 0.9,
      fair: 0.75,
      broken: 0.4,
    },
    defaultCondition: 0.9, // used if a tier is missing

    // Which condition tier is the public "Up to $X" headline shown in
    // the existing quote tool / pricing table (best realistic case).
    headlineCondition: 'like new',
  },

  // ----------------------------------------------------------
  // 2) GUARD RAILS
  // ----------------------------------------------------------
  guards: {
    // Never make an automated offer below this many dollars.
    globalFloor: 20,
    // Optional per-category floors (override globalFloor when higher).
    perCategoryFloor: {
      phones: 25,
      laptops: 40,
      consoles: 30,
    },
    // Never offer above the market median (you don't pay more than the
    // going sold price). Set false to allow margins > 1.0 to exceed it.
    neverAboveMarketMedian: true,
    // Below this many sold comps, flag "needs manual price" instead of
    // guessing. Requirement: < 5 sold listings => manual.
    minSampleSize: 5,
    // If the computed offer lands below the floor, what to do:
    //   'clamp'  -> raise the offer up to the floor (default; literal
    //               reading of "never offer below $X")
    //   'manual' -> flag it "needs manual price" instead
    belowFloorBehavior: 'clamp',
  },

  // ----------------------------------------------------------
  // 3) STATS
  // ----------------------------------------------------------
  stats: {
    // Fraction trimmed off EACH end for the trimmed mean.
    trimFraction: 0.10, // drop top 10% and bottom 10%
    // Which central estimate becomes the basis for the offer:
    //   'median' (default, robust) or 'trimmedMean'
    priceBasis: 'median',
    // Round final offers to the nearest this-many dollars (1 = exact $).
    roundTo: 1,
  },

  // ----------------------------------------------------------
  // 4) SOURCES
  // ----------------------------------------------------------
  sources: {
    ebay: {
      enabled: true,
      // 'production' | 'sandbox' — selects eBay API base URLs.
      env: process.env.EBAY_ENV || 'production',
      // Marketplace Insights returns *sold* comps but needs special
      // access approval. When false (or no access), we fall back to the
      // Browse API's ACTIVE listings as a proxy and tag the data
      // priceBasis:'active_listing_proxy' so you know it runs hotter
      // than true sold prices (compensate via margins above).
      useMarketplaceInsights:
        String(process.env.EBAY_MARKETPLACE_INSIGHTS || '').trim() === '1',
      marketplaceId: process.env.EBAY_MARKETPLACE_ID || 'EBAY_US',
      // eBay conditionIds used as the market BASIS (we then scale by our
      // own condition multipliers). Default = used + refurb.
      // 1000 New · 2000/2500 refurb · 3000 Used · 7000 For parts.
      conditionIds: '3000|2000|2500',
      // Cap comps pulled per variant.
      maxResults: 60,
      // Only keep listings within this price sanity window (USD) to drop
      // junk/accessory listings that pollute a device search.
      minItemPrice: 5,
      maxItemPrice: 6000,
    },
    bestbuy: {
      // Best Buy Products API. Returns CURRENT NEW RETAIL prices — an
      // authoritative anchor, but NOT a used/sold comp.
      //
      // By default this is a REFERENCE source: prices are fetched and
      // recorded in the audit trail (data/) but `useAsBasis:false` keeps
      // them OUT of the median that drives the offer (so retail-new asks
      // don't inflate buyback offers). eBay's used/sold median stays the
      // basis. Flip `useAsBasis:true` only if you deliberately want an
      // "offer = % of current retail" model.
      enabled: String(process.env.BESTBUY_ENABLED || '').trim() === '1',
      useAsBasis: false,
      // Narrow to a condition attribute: 'new' | 'refurbished' | 'pre-owned'
      // | null (don't filter). If a filtered query returns nothing, the
      // adapter relaxes the filter once automatically.
      condition: 'new',
      // Catalog rows pulled per device.
      pageSize: 20,
      // Drop junk/accessory hits outside this USD window.
      minItemPrice: 5,
      maxItemPrice: 6000,
    },
    competitors: {
      // OFF by default: each adapter must be verified against the live
      // site's markup and ToS before enabling. robots.txt is ALWAYS
      // enforced regardless of this flag (see sources/competitors.js).
      enabled:
        String(process.env.COMPETITORS_ENABLED || '').trim() === '1',
      // The User-Agent we send and check robots.txt against.
      userAgent: 'PurchasingCorpPriceBot/1.0 (+https://purchasingcorp.com/bot)',
      // Per-site adapters. Selectors/patterns MUST be confirmed per site.
      // `disallowOverride: false` means we obey robots.txt; we never
      // override it. Sites are skipped automatically if robots disallows.
      sites: [
        // {
        //   name: 'example-buyback',
        //   baseUrl: 'https://www.example-buyback.com',
        //   // Build the search/offer URL for a catalog variant.
        //   buildUrl: (v) => `https://www.example-buyback.com/sell/${encodeURIComponent(v.query)}`,
        //   // Extract dollar offers from the fetched HTML. Return number[].
        //   extract: (html) => require('./lib/money').extractDollarsFromHtml(html),
        // },
      ],
    },
  },

  // ----------------------------------------------------------
  // 5) OUTPUT
  // ----------------------------------------------------------
  output: {
    // Site categories that should ALWAYS render as "Contact" regardless of
    // market data — i.e. devices you insist on pricing by hand. Empty by
    // default. Example: ['accessories'].
    alwaysContact: [],
  },

  // Map the quote wizard's 6 condition labels (form.html CONDS) onto our
  // 4 pricing tiers. Anything unmapped uses defaultCondition.
  CONDITION_ALIASES: {
    'new — sealed / unopened': 'like new',
    'like new': 'like new',
    'good — light wear': 'good',
    'fair — visible wear': 'fair',
    'damaged / for parts': 'broken',
    'not sure': 'good', // conservative middle when unknown
  },
};
