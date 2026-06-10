#!/usr/bin/env node
'use strict';
// ============================================================
// ORCHESTRATOR — the one command you run (or CI runs):
//
//   node build-prices.js [--dry-run] [--limit N] [--only iphone,consoles]
//
// Pipeline:
//   catalog (pricing-data.js)
//     -> for each variant: pull comps from sources (eBay + competitors)
//     -> stats: median / trimmed mean / sample size
//     -> pricing core: margin + floor/ceiling/sample guards (per condition)
//     -> store raw results (timestamped)  +  write window.PRICING outputs
//
// With no API keys / sources disabled, it still runs end-to-end and
// emits a valid file where every device is "Contact" (the existing
// hand-priced path) — proving the format/contract without network.
// ============================================================

const { loadEnv } = require('./lib/env');
loadEnv();

const CONFIG = require('./config');
const { buildCatalog } = require('./catalog');
const { summarize, median } = require('./lib/stats');
const { computeVariantOffers } = require('./pricing');
const { createEbaySource } = require('./sources/ebay');
const { createBestBuySource } = require('./sources/bestbuy');
const { createCompetitorSource } = require('./sources/competitors');
const { writeRun } = require('./store');
const { buildPricing, writeOutputs } = require('./output');

function parseArgs(argv) {
  const args = { dryRun: false, limit: 0, only: null };
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--dry-run') args.dryRun = true;
    else if (a === '--limit') args.limit = parseInt(argv[++i], 10) || 0;
    else if (a === '--only') args.only = String(argv[++i] || '').split(',').map((s) => s.trim()).filter(Boolean);
  }
  return args;
}

async function run() {
  const args = parseArgs(process.argv);
  const startedAt = new Date().toISOString();

  const catalog = buildCatalog();
  const { PRICING: ORIGINAL } = require('./catalog').loadPricingData();

  let variants = catalog.variants;
  if (args.only) variants = variants.filter((v) => args.only.includes(v.siteCategory));
  if (args.limit > 0) variants = variants.slice(0, args.limit);

  const sources = [
    createEbaySource(CONFIG),
    createBestBuySource(CONFIG),
    createCompetitorSource(CONFIG),
  ].filter((s) => s.enabled);
  console.log(`[build] ${variants.length} variants · sources: ${sources.map((s) => s.name).join(', ') || 'NONE (offline → all Contact)'}`);

  const results = new Map();   // variantKey -> headline result {status, offer}
  const runVariants = [];      // full audit records
  let priced = 0;
  let manual = 0;

  for (const v of variants) {
    // Gather comps from every enabled source. Only basis-eligible
    // sources (eBay used/sold, competitor offers) feed the offer median;
    // reference sources (Best Buy retail) are recorded but excluded so
    // retail-new asks don't inflate the buyback offer.
    const sourceResults = [];
    const allPrices = [];
    for (const s of sources) {
      // eslint-disable-next-line no-await-in-loop
      const r = await s.fetchComps(v);
      sourceResults.push(r);
      if (r.basisEligible !== false) {
        for (const p of r.prices || []) allPrices.push(p);
      }
    }

    const summary = summarize(allPrices, CONFIG.stats.trimFraction);
    const offers = computeVariantOffers({ summary, category: v.marginCategory, config: CONFIG });
    const headline = offers.headline;
    results.set(v.variantKey, { status: headline.status, offer: headline.offer });

    if (headline.status === 'priced') priced++; else manual++;

    runVariants.push({
      variantKey: v.variantKey,
      siteCategory: v.siteCategory,
      marginCategory: v.marginCategory,
      carrier: v.carrier,
      query: v.query,
      summary,
      sources: sourceResults.map((r) => ({
        source: r.source,
        basis: r.basis,
        basisEligible: r.basisEligible !== false,
        count: (r.prices || []).length,
        median: median(r.prices || []), // each source's own median (e.g. Best Buy retail anchor)
        error: r.error || null,
        perSite: r.perSite || undefined,
      })),
      offers: offers.byCondition,  // full per-condition breakdown (audit)
      headline,
    });
  }

  const finishedAt = new Date().toISOString();
  const alwaysContact = new Set(CONFIG.output.alwaysContact || []);
  const PRICING = buildPricing(catalog, results, ORIGINAL, { alwaysContact });

  console.log(`[build] priced=${priced} manual/Contact=${manual}`);

  if (args.dryRun) {
    console.log('[build] --dry-run: no files written.');
    return;
  }

  const stored = writeRun({
    startedAt,
    finishedAt,
    args,
    sources: sources.map((s) => s.name),
    counts: { variants: variants.length, priced, manual },
    config: { margins: CONFIG.margins, guards: CONFIG.guards, stats: CONFIG.stats },
    variants: runVariants,
  });

  const out = writeOutputs(PRICING, catalog.categoryLabels, {
    updated: finishedAt,
    sources: sources.map((s) => s.name).length ? sources.map((s) => s.name) : ['offline'],
  });

  console.log(`[build] raw    -> ${stored.rawPath}`);
  console.log(`[build] output -> ${out.jsPath}`);
  console.log(`[build] output -> ${out.jsonPath}`);
}

run().catch((e) => {
  console.error('[build] FATAL', e);
  process.exit(1);
});
