# PurchasingCorp — Setup Checklist

_Last updated 2026-06-20._

## ✅ Done (verified this session)
- **Homepage hero rewritten + live** — now states plainly "We buy your phone,
  laptop & tablet — for cash, today." (badge, subhead, `<title>`, OG/Twitter
  cards all updated). This was the main lead-loss cause: the old hero ("Turn
  idle devices into instant cash") never said you *buy* gear.
- **GitHub Actions secrets set** — `EBAY_CLIENT_ID` + `EBAY_CLIENT_SECRET` added.
  The 6-hourly `pricing-refresh` workflow now authenticates and runs.
- **Prices refreshed & published** — were frozen at 2026-06-12; now current.
  Engine self-corrected several variants UP toward the public sheet (e.g.
  iPhone 17 PM 256GB fair $437→$650, fixing the long-standing "offers too low").
- **Catalog query fix live + validated** — Apple Watch / Mac mini / MacBook now
  pull eBay comps; priced variants went 233 → 243 (manual 33 → 23).
- **Supabase confirmed configured in Vercel** — `GET /api/config` returns
  `configured: true`. Leads ARE being saved (no silent drop).

## ⬜ Remaining (need your accounts — optional / lower priority)

### Customer emails via Resend — REQUIRED for confirmation + chat emails
Confirmation emails on quote submit, and chat-reply emails, send through Resend.
Until these env vars are set in Vercel, sends are silent no-ops (nothing breaks —
leads and messages still save; customers just don't get an email).
- [ ] Sign up at **resend.com**, add domain **purchasingcorp.com**, and add the
      DNS records it gives you (MX/TXT/DKIM) in **Route 53**. Wait for "Verified".
- [ ] Create an API key, then set these in Vercel → Settings → Environment Variables:
      - `RESEND_API_KEY` — the Resend API key (required to send anything)
      - `EMAIL_FROM` — e.g. `PurchasingCorp <hello@purchasingcorp.com>` (must be on the verified domain)
      - `EMAIL_REPLY_TO` — e.g. `hello@purchasingcorp.com` (where customer replies land)
      - `OWNER_EMAIL` — your inbox; you get a copy when a customer replies in chat
      - `SITE_URL` — `https://purchasingcorp.com` (used for links in emails; optional, this is the default)
- [ ] Redeploy so the edge functions pick up the new env vars.


### eBay Marketplace Insights — better comp accuracy
Engine currently uses active-listing asks × 0.90 haircut as a sold-price proxy.
Insights gives true sold comps.
- [ ] Apply for **Marketplace Insights API** access at developer.ebay.com
- [ ] Once granted, set repo **variable** `EBAY_MARKETPLACE_INSIGHTS=1`
      (Settings → Secrets and variables → Actions → Variables)

### Rotate the eBay Cert ID — security hygiene
The production Cert ID sits in `pricing-engine/.env` and has been pasted in chat.
- [ ] developer.ebay.com → My Keys → regenerate the **Cert ID (Client Secret)**
- [ ] Update `pricing-engine/.env` AND re-run the secret set:
      `grep '^EBAY_CLIENT_SECRET=' pricing-engine/.env | cut -d= -f2- | tr -d '\n' | gh secret set EBAY_CLIENT_SECRET -R Toyota2Lambo/purchasingcorp`

## ⏳ Self-resolving (no action)
- ~23 brand-new SKUs (iPhone 17 Air, M5 MacBooks, iPad Pro M4/M5, AirPods Pro 3)
  have <5 eBay comps and show "Contact"; they'll price automatically as listings
  accumulate and the 6h refresh keeps the data fresh.

## Note on the circuit-breaker
The price refresh has a safety breaker (`PRICE_MAX_PCT_DELTA=30`,
`PRICE_MAX_SHRINK_PCT=20`). After a long gap it may trip on legitimate catch-up
moves and hold publishing. To publish anyway after reviewing: re-run the
`pricing · refresh` workflow with **force_publish=true**.
