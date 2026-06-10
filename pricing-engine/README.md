# PurchasingCorp — Dynamic Pricing Engine

Pulls recent market prices for every device in the catalog, applies a
configurable margin + guard-rail policy, and emits the **exact**
`window.PRICING` data shape the quote tool already reads. No more
hand-maintaining a price list.

Zero runtime dependencies. Plain Node (>=18), same spirit as `/social`.

```
node build-prices.js            # full run -> writes out/ + data/
node build-prices.js --dry-run  # run the pipeline, write nothing
node build-prices.js --only iphone,consoles --limit 20
npm test                        # node --test (margin math + guards + stats)
```

## How it works

```
catalog.js          reads the device list out of ../pricing-data.js (sandbox eval)
   │                and explodes each row into variants (model + storage + carrier)
   ▼
sources/ebay.js     PRIMARY: eBay comps (Marketplace Insights = sold, or Browse = active proxy)
sources/competitors raw fallback: scrape competitor buyback offers (robots.txt enforced)
   ▼
lib/stats.js        median · trimmed mean (drop top/bottom 10%) · sample size
   ▼
pricing.js          offer = basis × categoryMargin × conditionMultiplier,
                    clamped to [floor, market median]; <5 comps => "needs manual"
   ▼
store.js            data/raw/<timestamp>.json  +  data/latest.json  (audit trail)
output.js           out/pricing-data.generated.js  +  out/pricing.json  (window.PRICING shape)
```

## Setup (you do this once)

1. **Get eBay keys.** Free account at https://developer.ebay.com/my/keys →
   create a **Production** keyset. You need the **App ID (Client ID)** and
   **Cert ID (Client Secret)**. Read-only market data uses
   client-credentials OAuth — no user login or redirect URL.
2. `cp .env.example .env` and paste the two keys. (In CI, set them as
   GitHub Actions **secrets** instead — no `.env` needed there.)
3. `npm test` then `node build-prices.js`. Without keys it still runs and
   emits a valid file where everything is `"Contact"`.

> **Marketplace Insights vs Browse.** True *sold* prices come from eBay's
> Marketplace Insights API, which needs separate access approval. Until
> granted, the engine uses **active listing** prices as a proxy (tagged
> `basis: "active_listing_proxy"` in `data/`). Active asks run higher than
> sold prices — compensate with the margins in `config.js`. Flip
> `EBAY_MARKETPLACE_INSIGHTS=1` once approved.

## Tuning — everything lives in `config.js`

- `margins.byCategory` — phones / laptops / tablets / consoles / …
- `margins.byCondition` — like new / good / fair / broken
- `guards.globalFloor`, `guards.perCategoryFloor` — never offer below $X
- `guards.neverAboveMarketMedian` — never offer above the going price
- `guards.minSampleSize` — < this many comps ⇒ flag "needs manual price"
- `stats.priceBasis` — `median` (default) or `trimmedMean`
- `output.alwaysContact` — categories you insist on pricing by hand

## Going live (deliberate, not automatic)

The engine writes to `pricing-engine/out/` — it does **not** touch the
live `/pricing-data.js` or `/api/pricing`. The quote tool keeps reading
today's data until you promote a generated file on purpose:

**Option A (static snapshot):** copy `out/pricing-data.generated.js` over
the repo's root `pricing-data.js`, eyeball the diff, commit, deploy.

**Option B (live API):** point `api/pricing.js` at `out/pricing.json`
(or have it read from the engine's data store) instead of the Google
Sheet CSVs. The JSON envelope is already `{ ok, data, updated }`, the
same shape that endpoint returns today.

Either way: **review the diff before shipping.** See
`../PRICING_MIGRATION_PLAN.md` for the phased rollout and the full list
of things that break if prices change with no human in the loop.

## What's intentionally NOT done here

- No write to the live site (promotion is manual — see above).
- Competitor adapters ship empty: add verified per-site selectors to
  `config.js` and set `COMPETITORS_ENABLED=1`. robots.txt is obeyed
  regardless; disallowed or unreachable sites are skipped, not scraped.
- Catalog still sourced from `pricing-data.js`. Moving the device list to
  its own file/DB is Phase 1 of the migration plan.
