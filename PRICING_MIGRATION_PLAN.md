# Pricing Migration Plan — Static Sheet → Dynamic Pricing Engine

**Status:** Proposal / audit only. No code changed.
**Date:** 2026-06-09
**Goal:** Replace the manually-maintained Google Sheet price list with an engine that pulls market data automatically, while preserving the current quote UX and the owner's margin control.

---

## Part 1 — How the instant-quote tool works today (audit)

### 1.1 Where the price data lives

There are **three** copies of the price list, in priority order:

| Layer | File | Role | Freshness |
|---|---|---|---|
| Source of truth | Google Sheet `1sXN7aWSZpFU3rxQopXIYJGhRR-I99PR2BhZJBLaG7Ew` | Owner edits by hand. GID `0` = Apple, GID `1876227864` = Consoles. | Manual |
| Live read | [api/pricing.js](api/pricing.js) (Vercel Edge fn) | Fetches both CSV exports, parses → normalized JSON. CDN-cached `s-maxage=300, stale-while-revalidate=3600`. | ~5 min |
| Static fallback | [pricing-data.js](pricing-data.js) → `window.PRICING` | Hardcoded snapshot used when `/api/pricing` fails. | Stale (file mtime May 26) |

There is also a **fourth, fully-detached copy**: the homepage "you could get" teaser in [index.html:371](index.html:371)–397 hardcodes dollar figures (`$855 / +$205`, etc.) with **no connection** to `window.PRICING` or the API.

### 1.2 How the frontend reads prices

Two consumers, identical pattern — load the static snapshot first, then try to overwrite it from the API:

- **`pricing.html`** ("Cashout Rates" table) loads [pricing-data.js](pricing-data.js) then [pricing.js](pricing.js). On load, [pricing.js:13](pricing.js:13) does `fetch('/api/pricing', {cache:'no-store'})`; on success it replaces `window.PRICING` and re-renders; on any failure it silently keeps the snapshot. Tab + search UI only — read-only display.
- **`form.html`** — the actual **instant-quote wizard** (Device → Model → Condition → Offer). Loads [pricing-data.js](form.html:69), builds the wizard off `window.PRICING`, then refreshes via `/api/pricing` at [form.html:859](form.html:859).

Price extraction is **regex on display strings**: [form.html:555](form.html:555) `priceNum()` matches `/\$\s*([0-9][0-9,]*)/`. Anything not formatted as `$1,234` (e.g. `"Contact"`, `"50% off MSRP"`) returns `null` and the wizard shows *"We price this one by hand / Fast quote"* ([form.html:736](form.html:736)).

### 1.3 Device catalog structure

It is a **flat display table per category**, not a normalized catalog:

```js
window.PRICING = {
  iphone: { headers: ["Model","Unlocked / Apple","Carrier / Locked"], rows: [["iPhone 17 Pro Max 2TB","$1,241","$788"], ...] },
  "macbook-pro": { headers: ["Model","Discount on MSRP","Activation bonus"], rows: [['14" M5 24GB 1TB SSD 2025',"50% off MSRP","-$100 Active"], ...] },
  ...
}
```

- Categories: `iphone`, `macbook-pro`, `macbook-air`, `mac-mini`, `apple-watch`, `ipad`, `consoles`, `accessories`.
- Each = `{ headers: [3], rows: [[col0, col1, col2]] }`.
- **Model/storage/year are fused into one string** (`"iPhone 17 Pro Max 2TB"`, `'14" M5 24GB 1TB SSD 2025'`). There is no structured brand/model/storage field to join market data against.
- **Column 1 ("price") is not uniform:** absolute `$` for most, but `"50% off MSRP"` for MacBook Pro and `"Contact"` for all iPads, most accessories, and several consoles.
- **Column 2 varies by category:** carrier-locked price (iPhone), activation bonus, or a free-text note.

> **Condition tiers do NOT exist in the data.** The wizard collects Condition (Sealed / Like new / Good / Fair / Damaged / Not sure) and Carrier, but the estimate at [form.html:714](form.html:714) is just `priceNum(row[1])` (or `row[2]` for carrier-locked iPhones). Condition is forwarded to `/api/inquiry` as **text only** and never changes the number. The single sheet price is implicitly "top / best-case," shown as **"Up to $X."**

### 1.4 Backend / framework stack

- **No framework, no build step.** Static HTML + Tailwind (CDN) + vanilla JS. (Per repo convention: do not introduce a Node/build toolchain.)
- **Backend = Vercel Edge Functions** in `/api`: `pricing.js` (sheet→JSON), `inquiry.js` (quote submit → Supabase Storage + `inquiries` table), `subscribe.js` (email), `claim.js` / `config.js` (account/claim flow). All `runtime: 'edge'`.
- **Data store:** Supabase (Postgres + Storage) via REST using the `service_role` key. No price table exists today — prices never touch Postgres.
- **Deploy:** Vercel, `cleanUrls`. Edge fns can't run locally.

---

## Part 2 — Proposed dynamic pricing engine

### 2.1 Target architecture

Keep the **frontend contract identical** (`window.PRICING` shape + `/api/pricing` JSON), so neither `pricing.js` nor the wizard needs rewriting. Swap out only what produces the JSON.

```
  market sources (eBay sold comps / SellCell / swappa-style feeds / carrier trade-in APIs)
        │  scheduled pull (cron)
        ▼
  normalize → map to SKU catalog → apply margin/haircut + floors → round
        │  write versioned rows
        ▼
  Supabase: device_catalog + price_points (+ price_runs audit)
        │  read
        ▼
  api/pricing.js  ──emits same JSON shape──▶  pricing.js / form.html  (unchanged)
```

### 2.2 New data model (Supabase)

Introduce a real catalog so market data has something to join to:

```sql
-- one row per sellable SKU
device_catalog(
  sku_id uuid pk, category text, brand text, model text,
  storage text, variant text,            -- e.g. carrier, finish, year
  display_name text,                      -- the exact string the UI shows today
  manual_only bool default false,         -- true = always "Contact", never auto-priced
  active bool default true
)

-- current published buy price per SKU + tier
price_points(
  sku_id uuid fk, condition text, carrier text,
  buy_price numeric,                      -- what WE pay (after margin)
  market_ref numeric,                     -- raw comp the price came from
  source text, floor numeric, ceiling numeric,
  updated_at timestamptz, run_id uuid
)

-- audit / rollback
price_runs(
  run_id uuid pk, started_at, source, rows_changed int,
  status text, max_pct_delta numeric      -- for the circuit-breaker (see 3.x)
)
```

`api/pricing.js` then reads `device_catalog` + latest `price_points` and re-assembles the existing per-category `{headers, rows}` shape — including emitting `"Contact"` for `manual_only` SKUs and the carrier-locked column for iPhones.

### 2.3 Market → buy-price math (the core)

Market feeds give **resale/sold prices**, not buy prices. The engine must never publish a raw market number. Pipeline per SKU:

```
buy_price = round( market_ref × margin_factor × condition_multiplier × carrier_factor )
clamped to [floor, ceiling]
```

- `margin_factor` — owner-set per category (the spread that today is baked into the manual numbers).
- `condition_multiplier` — **new**; lets the wizard's condition tiers finally affect price (e.g. Sealed 1.0, Good 0.85, Damaged 0.4).
- `carrier_factor` — reproduces the existing ~0.70–0.75 locked discount for iPhones.
- `floor` / `ceiling` — guardrails so a bad scrape can't post an absurd quote.

### 2.4 Update mechanism

A scheduled job (Vercel Cron or GitHub Action — repo already uses GH Actions for `/social`) runs the pull → math → write, then bumps a `price_runs` row. `api/pricing.js` stays a thin reader. This keeps the edge fn fast and avoids scraping on the request path.

---

## Part 3 — What will BREAK if prices start auto-updating (flags)

> These are the reasons not to point a raw feed at the live site without the guardrails above.

1. **`"Contact"` / hand-priced SKUs get clobbered.** All iPads, all accessories, and several consoles are deliberately `"Contact"`, and the wizard has a designed *"We price this one by hand"* path. An engine that fills every SKU destroys that intentional human-touch flow. **Mitigation:** `manual_only` flag; never auto-price those rows.

2. **Non-numeric price columns have semantics the parser can't take.** MacBook Pro col 1 is `"50% off MSRP"` (relative), and `formatPrice()` in [api/pricing.js:196](api/pricing.js:196) has a dedicated `%` branch. The wizard's `priceNum()` returns `null` for it on purpose. Replacing with an absolute `$` silently changes the column's meaning and the MSRP-discount display logic.

3. **Homepage teaser goes stale and contradicts the tool.** [index.html:371](index.html:371)–397 is hardcoded. Once live prices drift, the homepage advertises one number and the quote tool returns another → "bait and switch" perception. **Mitigation:** wire the teaser to the API or regenerate it in the same job.

4. **No condition tiers means "Up to $X" is the only price.** The headline is best-case. If a feed returns a generic "used-good" comp and we publish it as the single number, every quote silently drops below what the site advertises. **Mitigation:** the `condition_multiplier` model (2.3) — decide explicitly whether market maps to the top tier.

5. **Carrier-locked column is a second hand-set price.** iPhone `row[2]` is ~25–30% below unlocked (business rule). A feed must reproduce this via `carrier_factor`, or the locked column breaks.

6. **Margin disappears.** Today's numbers are *buy* prices with margin baked in. A raw resale/sold feed is sell-side; buying at market = zero/negative margin. **The engine must apply `margin_factor` — this is the single most important guardrail.**

7. **No floor, no audit, no rollback today.** Prices live only in a sheet + ephemeral CDN cache; the only record of a quoted price is the `Est:` text stuffed into `inquiries.details` ([form.html:785](form.html:785)). A bad scrape would go live with nothing to revert to. **Mitigation:** `price_runs` audit + `floor/ceiling` clamps + a circuit-breaker that pauses publish if `max_pct_delta` exceeds a threshold.

8. **The parser is shaped to the exact sheet layout.** [api/pricing.js](api/pricing.js) uses regex section detection, iPad sub-section logic, and a sanity check (`iphone.rows < 10 || consoles.rows < 5 → throw`). Changing the source means rewriting all of it; the sanity thresholds assume Apple+console volume.

9. **Display-string contract is load-bearing.** Both consumers parse `$1,234` via regex. If engine output deviates (cents, currency symbol, `USD` suffix), `priceNum()` returns `null` and items silently fall to "Contact." Keep the exact format `formatPrice()` produces.

10. **Stale fallback risk grows.** [pricing-data.js](pricing-data.js) is the outage fallback and is already ~2 weeks stale. With live pricing, an API outage drops users to old prices with no warning. **Mitigation:** regenerate the snapshot from the engine each run, or surface an "as of" timestamp.

---

## Part 4 — Suggested phasing (low-risk first)

1. **Catalog backfill (no behavior change).** Build `device_catalog` from the current sheet rows; add `manual_only`. Keep `api/pricing.js` reading the sheet. Pure prep.
2. **Read from DB.** Point `api/pricing.js` at `device_catalog` + `price_points` seeded from today's numbers. Output JSON byte-compatible with current shape. Verify `pricing.html` + wizard unchanged.
3. **Engine offline / shadow mode.** Run the market-pull job writing to `market_ref` only (not `buy_price`). Compare engine output vs. manual prices in a report; tune `margin_factor` until parity.
4. **Condition multipliers live.** Wire the wizard's existing condition/carrier selections into the price (they're already collected — just unused).
5. **Go live with guardrails.** Enable auto-publish behind floors/ceilings + circuit-breaker + audit. Keep a manual-override that always wins.
6. **Fix the detached copies.** Regenerate `pricing-data.js` snapshot and the `index.html` teaser from the engine.

---

## Part 5 — Open questions for the owner

- Which **market sources** are acceptable/affordable? (eBay sold comps, SellCell, carrier trade-in APIs each have very different licensing + cost.)
- What **margin** per category, and is it a flat % or a curve by price band?
- Should condition multipliers be **applied to the quote** (changes the offer) or just **logged**?
- Which SKUs must **stay `manual_only`** regardless of available data?
- Acceptable **update cadence** (daily? hourly?) and who approves a publish — fully automatic, or queued for owner sign-off?
