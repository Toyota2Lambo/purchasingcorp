# PurchasingCorp — Instagram + Threads automation

Generates and publishes PurchasingCorp's daily social content end to end:
the buyback brand's offers, payout boards, competitor comparisons, and
educational/lifestyle posts — rendered as on-brand PNGs and posted to
Instagram and Threads on a schedule.

It only ever quotes **real payout numbers** pulled from the site's own
pricing data. The generator is hard-blocked from inventing a price;
quote-only categories (iPad, accessories) say "Contact for your number"
instead of showing a figure.

---

## Pipeline

```
pricing-data.js                     (the live site's prices)
   │  node social/dump_pricing.js
   ▼
social/pricing.json                 (clean JSON + a "top" payout per category)
   │  python social/social_generator.py     ← Anthropic API (writes the copy)
   ▼
social/<date>/content.json          (the unified content model: posts + stories)
   │  node social/renderer.js               ← Puppeteer screenshots the templates
   ▼
social/<date>/*.png + manifest.json
   │  git commit + push  →  Vercel deploy    (PNGs become public URLs)
   ▼
python social/ig_publisher.py        ← Instagram Graph API (creates + publishes)
python social/threads_publisher.py   ← Threads API (same PNGs; feed posts)
```

A post with **one** slide is a single image; **2+** slides is a carousel.
Stories are always 1080×1920.

---

## APIs / secrets you need to add

Add these in the GitHub repo under **Settings → Secrets and variables → Actions**.

### Required

| Name | Kind | Used by | What it is |
|------|------|---------|------------|
| `ANTHROPIC_API_KEY` | secret | generate / backfill | Anthropic API key — writes the captions & on-image copy. Get it at <https://console.anthropic.com> → API Keys. |
| `IG_ACCESS_TOKEN` | secret | publish | Instagram Graph API access token (see setup below). |
| `IG_BUSINESS_ACCOUNT_ID` | secret | publish | The Instagram **Business/Creator** account's numeric ID. |
| `THREADS_ACCESS_TOKEN` | secret | threads | Threads API access token — separate from the IG token (see setup below). |
| `THREADS_USER_ID` | secret | threads | The numeric **Threads** user ID to publish to. |

### Optional (sensible defaults if omitted)

| Name | Kind | Default | What it does |
|------|------|---------|--------------|
| `UNSPLASH_ACCESS_KEY` | secret | — | Enables real Unsplash photos on lifestyle/cover posts. Without it, the pipeline uses deterministic Picsum placeholders. Get it at <https://unsplash.com/developers>. |
| `DISCORD_WEBHOOK_URL` | secret | — | Posts a publish summary to a Discord channel. |
| `IG_PUBLIC_BASE_URL` | variable | `https://purchasingcorp.com` | Public base URL the rendered PNGs are served from. Override if the domain changes. |
| `ANTHROPIC_MODEL` | variable | `claude-sonnet-4-5` | Override the generation model. |

> Use **Secrets** for tokens/keys and **Variables** for non-sensitive config
> (`IG_PUBLIC_BASE_URL`, `ANTHROPIC_MODEL`).

### Getting the Instagram credentials

Instagram posting requires the **Graph API** (not a personal login):

1. The Instagram account must be a **Business** or **Creator** account, linked
   to a **Facebook Page**.
2. Create an app at <https://developers.facebook.com> → add **Instagram Graph
   API**.
3. Generate a **long-lived access token** (~60 days) with the
   `instagram_basic` and `instagram_content_publish` permissions → set it as
   `IG_ACCESS_TOKEN`.
4. Find the Instagram **business account ID** (e.g. via the Graph API Explorer:
   `me/accounts` → the Page → `?fields=instagram_business_account`) → set it as
   `IG_BUSINESS_ACCOUNT_ID`.

> **Token expiry:** long-lived tokens last ~60 days. Refresh before they lapse
> (re-exchange via the Graph API) and update the `IG_ACCESS_TOKEN` secret, or
> publishing will start failing with an auth error.

### Getting the Threads credentials

Threads uses its **own** API (`graph.threads.net`) and its **own** token — the
Instagram token does **not** work for it:

1. The account must be a **Threads** account (sharing the Instagram login is
   fine).
2. At <https://developers.facebook.com>, add the **Threads API** use case to your
   app and request the `threads_basic` and `threads_content_publish` scopes.
3. Run the Threads OAuth flow to mint a **long-lived access token** (~60 days) →
   set it as `THREADS_ACCESS_TOKEN`.
4. Get your numeric **Threads user ID**
   (`GET https://graph.threads.net/v1.0/me?fields=id`) → set it as
   `THREADS_USER_ID`.

> Threads is a text/image feed with **no stories**, so `social · threads`
> publishes the feed **posts** only by default; story images are skipped unless
> you opt in (`include_stories` input / `THREADS_INCLUDE_STORIES=1`), in which
> case each is posted as a plain image. Post text is capped at 500 characters
> (the Threads limit). The same ~60-day token-refresh caveat applies.

---

## GitHub Actions

| Workflow | Trigger | Does |
|----------|---------|------|
| `social · daily` | cron `0 13 * * *` + manual | dump pricing → generate → render → commit (Vercel deploys) |
| `social · publish` | cron `0 16 * * *` + manual | publish the day to Instagram (waits for the PNG URLs to go live first) |
| `social · threads` | cron `0 17 * * *` + manual | publish the day to **Threads** — feed posts (stories optional) |
| `social · backfill` | manual only | generate + render a **range** of days in one run |

Publishing is a separate workflow scheduled a few hours after generation so the
Vercel deploy has time to land. The publisher also polls each PNG URL until it
returns `200` (up to ~3 min), so a little overlap is harmless.

**Commit identity:** the workflows commit as `Toyota2Lambo`
(`258973343+Toyota2Lambo@users.noreply.github.com`) — this must match the
author Vercel is configured to deploy, or the deploy is skipped.

**Staggering:** `social · publish` accepts an `only` input
(`post:1`, `story:2`, `posts`, `stories`) so you can spread one day's content
across multiple runs. `social · threads` takes the same `only` input (feed
posts only) plus an `include_stories` toggle.

**Two channels, independent:** Instagram and Threads publish from the *same*
rendered PNGs but in separate workflows, so one can fail or be re-run without
touching the other.

---

## Running locally

Requires Node 20+ and Python 3.9+.

```bash
cd social
npm install                 # puppeteer (downloads a headless Chromium)
pip install anthropic       # only the generator needs this

# 1. refresh pricing.json from the live site data
node dump_pricing.js

# 2. generate a day's content (needs ANTHROPIC_API_KEY in your env)
export ANTHROPIC_API_KEY=sk-ant-...
python social_generator.py --date 2026-06-01

# 3. render the PNGs
node renderer.js --content 2026-06-01/content.json

# 4. (optional) publish — dry run first
IG_DRY_RUN=1 python ig_publisher.py --date 2026-06-01
THREADS_DRY_RUN=1 python threads_publisher.py --date 2026-06-01
```

### No API keys handy?

```bash
python social_generator.py --self-test --out /tmp/c.json   # uses the fixture, no API
node renderer.js --sample                                   # renders sample-payloads.json
IG_DRY_RUN=1 python ig_publisher.py --sample                # plans publish, no API
THREADS_DRY_RUN=1 python threads_publisher.py --sample      # plans threads, no API
```

### Backfill a range

```bash
python backfill_generator.py --start 2026-06-01 --end 2026-06-07
python backfill_generator.py --days 7            # last 7 days
```

---

## Files

| File | Role |
|------|------|
| `dump_pricing.js` | `pricing-data.js` → `pricing.json` (+ a quotable `top` per category) |
| `social_generator.py` | writes one day's `content.json` via the Anthropic API (honesty rules live here) |
| `backfill_generator.py` | runs the generator across a date range |
| `photo_fetcher.py` | resolves `PHOTO: <scene>` markers to a public image URL (Unsplash → Picsum) |
| `renderer.js` | Puppeteer renders each slide/story to PNG + writes `manifest.json` |
| `ig_publisher.py` | reads the manifest and publishes to Instagram (single / carousel / story) |
| `threads_publisher.py` | reads the same manifest and publishes to Threads (single / carousel; stories optional) |
| `templates-registry.js` | maps each template to its fields + builds the repeating HTML chunks |
| `templates/*.html` | the 12 post/story designs; `_shared.css` is the design system |
| `sample-payloads.json` | a full fixture exercising all 12 templates (used by `--self-test` / `--sample`) |

### The 12 templates

`offer`, `board`, `payout`, `compare`, `stat`, `quote`, `index`, `carousel`,
`cover`, `photo-cover`, `lifestyle`, `meme`.

---

## Notes on staying honest

- `dump_pricing.js` computes one `top` real dollar figure per category. The
  generator may headline only with figures that exist in `pricing.json` — it
  cannot invent one.
- Quote-only categories (no leading `$` in the data — iPad, accessories,
  MacBook Pro's "50% off MSRP") are flagged `quote_only` and never get a
  fabricated number.
- Competitor figures are always framed as estimates ("~", "typical"), never
  stated as exact quotes.
