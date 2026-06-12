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
    // Owner's target: ~20% profit on each buy. Buy at B, resell at the
    // estimated market price R => profit = R - B = 0.2·B, so B = R / 1.2.
    // (The active-listing haircut below converts asks -> est. resale R
    // BEFORE this is applied, so the 20% is against realistic resale,
    // not inflated asks.)
    profitTarget: 0.20,
    // Per high-level category. Site categories map to these in catalog.js
    // (iphone->phones, macbook*/mac-mini->laptops, ipad->tablets, etc.).
    // All categories currently use the uniform profit target; override a
    // category here if it needs a different spread.
    byCategory: {
      phones: 1 / 1.2,
      laptops: 1 / 1.2,
      tablets: 1 / 1.2,
      consoles: 1 / 1.2,
      watches: 1 / 1.2,
      audio: 1 / 1.2,
      other: 1 / 1.2,
    },
    // Fallback when a category isn't listed above.
    defaultCategory: 1 / 1.2,

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

    // Carrier-locked iPhones: priced as a fraction of the UNLOCKED market
    // price, not from their own eBay query — keyword search for "carrier
    // locked" mostly matches "Unlocked - any carrier" listings and skews
    // high. ~0.72 reproduces the spread baked into the manual sheet.
    carrierLockedFactor: 0.72,
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
      // When the basis is ACTIVE listings (no Marketplace Insights yet),
      // asks run hotter than what items actually sell for. This factor
      // converts ask -> estimated sold price before margins apply, so the
      // profit target is computed against realistic resale. Ignored once
      // EBAY_MARKETPLACE_INSIGHTS=1 (true sold comps need no haircut).
      activeListingHaircut: 0.90,
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
    // Customer-facing first offer (success screen): only shown when the
    // engine offer is at least this fraction of the sheet's "Up to $X"
    // estimate the customer just saw. Below that the success screen keeps
    // the "we'll confirm and reply fast" message (no bait-and-switch),
    // while the owner STILL gets the engine number in the notification.
    customerDisplayMinRatio: 0.75,
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
