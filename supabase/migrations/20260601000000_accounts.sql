-- PurchasingCorp · user accounts MVP
-- Run this ONCE in the Supabase SQL editor (Dashboard → SQL → New query).
--
-- What it does:
--   1. Adds the columns the customer dashboard needs to the existing
--      `inquiries` table: account link (user_id), the contact email we can
--      match on, a secret claim token, a created_at timestamp, plus the
--      money + shipping-label fields you fill in as a quote progresses.
--   2. Turns on Row Level Security and adds a single owner-read policy so a
--      signed-in customer (browser, anon key) can read ONLY their own quotes.
--
-- Safe to run more than once — every statement is guarded with IF NOT EXISTS
-- or drop-then-create.
--
-- IMPORTANT: /api/inquiry and /api/claim talk to Supabase with the
-- service_role key, which BYPASSES RLS — so inserting new quotes and the
-- claim/linking flow keep working regardless of the policy below. RLS only
-- governs the browser's anon-key reads on the dashboard.

-- 1. Columns -------------------------------------------------------------
alter table public.inquiries
  add column if not exists user_id uuid references auth.users(id) on delete set null;

alter table public.inquiries
  add column if not exists email text;

alter table public.inquiries
  add column if not exists claim_token uuid default gen_random_uuid();

alter table public.inquiries
  add column if not exists created_at timestamptz not null default now();

-- Money + fulfillment fields (you set these as a quote moves along).
alter table public.inquiries
  add column if not exists offer_amount numeric;

alter table public.inquiries
  add column if not exists shipping_label_url text;

alter table public.inquiries
  add column if not exists tracking_number text;

alter table public.inquiries
  add column if not exists carrier text;

-- 2. Indexes -------------------------------------------------------------
create index if not exists inquiries_user_id_idx on public.inquiries (user_id);
create index if not exists inquiries_email_idx   on public.inquiries (lower(email));
create index if not exists inquiries_claim_idx   on public.inquiries (claim_token);

-- 3. Row Level Security --------------------------------------------------
alter table public.inquiries enable row level security;

-- A signed-in customer can SELECT only the rows that belong to their account.
-- No insert/update/delete policies on purpose: those happen only through the
-- service-role endpoints (which bypass RLS). With RLS on and no permissive
-- policy for them, the anon / authenticated roles cannot read anyone else's
-- data or write at all.
drop policy if exists "inquiries: owner can read" on public.inquiries;
create policy "inquiries: owner can read"
  on public.inquiries
  for select
  to authenticated
  using (auth.uid() = user_id);
