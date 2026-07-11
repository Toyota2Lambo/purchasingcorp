-- ============================================================
-- PurchasingCorp — Admin panel + customer chat
-- ============================================================
-- Run this in the Supabase SQL editor AFTER sql/accounts.sql.
-- Idempotent: safe to run more than once.
--
-- What it adds:
--   1. public.admins        — registry of which auth users are admins.
--   2. public.is_admin()     — RLS helper (security definer).
--   3. inquiries offer-lock columns + an updated_at touch trigger.
--   4. RLS policies so admins can read/update EVERY inquiry while
--      customers keep seeing only their own (owner-read from accounts.sql).
--   5. public.messages      — per-inquiry chat between admin and customer,
--      with RLS scoping a customer to their own inquiries' threads.
--
-- IMPORTANT — bootstrap your first admin at the very bottom of this file.
-- ============================================================

-- 1. Admins registry --------------------------------------------------------
create table if not exists public.admins (
  user_id    uuid primary key references auth.users(id) on delete cascade,
  created_at timestamptz not null default now()
);

alter table public.admins enable row level security;

-- An admin may confirm their own membership; nobody can list the table.
drop policy if exists "admins: self read" on public.admins;
create policy "admins: self read"
  on public.admins for select
  to authenticated
  using (user_id = auth.uid());

-- 2. is_admin() helper ------------------------------------------------------
-- security definer so it can read public.admins regardless of the caller's
-- RLS; auth.uid() still reflects the caller's JWT inside the function.
create or replace function public.is_admin()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (select 1 from public.admins a where a.user_id = auth.uid());
$$;

grant execute on function public.is_admin() to authenticated;

-- 3. Offer-lock columns + updated_at ---------------------------------------
alter table public.inquiries
  add column if not exists offer_locked_at   timestamptz,
  add column if not exists offer_note        text,
  add column if not exists offer_response_at timestamptz,
  add column if not exists updated_at         timestamptz not null default now();

create or replace function public.touch_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists inquiries_touch_updated_at on public.inquiries;
create trigger inquiries_touch_updated_at
  before update on public.inquiries
  for each row execute function public.touch_updated_at();

-- 3b. Customer accept/decline of a locked offer ----------------------------
-- Customers must NOT be able to set their own price, so we never give them a
-- column-level UPDATE on inquiries. Instead this security-definer function
-- runs with owner rights but is hard-scoped to the CALLER'S OWN inquiry
-- (auth.uid() = user_id) and can only ever flip status to accepted/declined
-- on a row that already has a locked offer. The dollar amount is untouchable.
create or replace function public.respond_to_offer(p_inquiry_id uuid, p_decision text)
returns public.inquiries
language plpgsql
security definer
set search_path = public
as $$
declare
  v_row public.inquiries;
begin
  if p_decision not in ('accepted', 'declined') then
    raise exception 'decision must be accepted or declined';
  end if;

  update public.inquiries
     set status = p_decision,
         offer_response_at = now()
   where id = p_inquiry_id
     and user_id = auth.uid()
     and offer_locked_at is not null
     and status in ('quoted', 'accepted', 'declined')
  returning * into v_row;

  if v_row.id is null then
    raise exception 'no locked offer is available to respond to';
  end if;

  return v_row;
end;
$$;

grant execute on function public.respond_to_offer(uuid, text) to authenticated;

-- 4. Inquiry RLS for admins -------------------------------------------------
-- (The owner-read SELECT policy from accounts.sql still applies for customers;
--  these ADD admin-wide access. Postgres ORs together multiple permissive
--  policies for the same command, so admins get all rows, customers their own.)
drop policy if exists "inquiries: admin read all" on public.inquiries;
create policy "inquiries: admin read all"
  on public.inquiries for select
  to authenticated
  using (public.is_admin());

drop policy if exists "inquiries: admin update all" on public.inquiries;
create policy "inquiries: admin update all"
  on public.inquiries for update
  to authenticated
  using (public.is_admin())
  with check (public.is_admin());

-- 5. Messages (per-inquiry chat) -------------------------------------------
-- Chat photos reuse the PUBLIC `inquiry-photos` Storage bucket (no new bucket
-- needed); their public URLs are stored in `attachments`. A message must carry
-- text, at least one photo, or both.
create table if not exists public.messages (
  id          uuid primary key default gen_random_uuid(),
  inquiry_id  uuid not null references public.inquiries(id) on delete cascade,
  sender_role text not null check (sender_role in ('admin', 'customer')),
  sender_id   uuid references auth.users(id) on delete set null,
  body        text,
  attachments jsonb not null default '[]'::jsonb,
  created_at  timestamptz not null default now(),
  read_at     timestamptz,
  constraint messages_content_check check (
    (body is not null and char_length(body) between 1 and 4000)
    or jsonb_array_length(attachments) > 0
  )
);

create index if not exists messages_inquiry_idx on public.messages (inquiry_id, created_at);

-- Migration for installs created before chat photos (idempotent):
alter table public.messages add column if not exists attachments jsonb not null default '[]'::jsonb;
alter table public.messages alter column body drop not null;
alter table public.messages drop constraint if exists messages_body_check;
alter table public.messages drop constraint if exists messages_content_check;
alter table public.messages add constraint messages_content_check check (
  (body is not null and char_length(body) between 1 and 4000)
  or jsonb_array_length(attachments) > 0
);

alter table public.messages enable row level security;

-- Customer: read messages on inquiries they own.
drop policy if exists "messages: customer read own" on public.messages;
create policy "messages: customer read own"
  on public.messages for select
  to authenticated
  using (
    exists (
      select 1 from public.inquiries i
      where i.id = messages.inquiry_id and i.user_id = auth.uid()
    )
  );

-- Customer: send a message (as themselves) on an inquiry they own.
drop policy if exists "messages: customer insert own" on public.messages;
create policy "messages: customer insert own"
  on public.messages for insert
  to authenticated
  with check (
    sender_role = 'customer'
    and sender_id = auth.uid()
    and exists (
      select 1 from public.inquiries i
      where i.id = messages.inquiry_id and i.user_id = auth.uid()
    )
  );

-- Admin: read every thread.
drop policy if exists "messages: admin read all" on public.messages;
create policy "messages: admin read all"
  on public.messages for select
  to authenticated
  using (public.is_admin());

-- Admin: post as 'admin' on any thread.
drop policy if exists "messages: admin insert" on public.messages;
create policy "messages: admin insert"
  on public.messages for insert
  to authenticated
  with check (public.is_admin() and sender_role = 'admin');

-- Admin: update (used to stamp read_at on customer messages).
drop policy if exists "messages: admin update" on public.messages;
create policy "messages: admin update"
  on public.messages for update
  to authenticated
  using (public.is_admin())
  with check (public.is_admin());

-- ============================================================
-- 6. ADMIN: RESOLVE ACCOUNT EMAILS
-- ------------------------------------------------------------
-- The browser uses the anon key and cannot read auth.users, so this
-- SECURITY DEFINER function lets an admin map inquiry user_ids back to
-- the login email of the account that submitted them.
-- ============================================================
create or replace function public.admin_account_emails(p_user_ids uuid[])
returns table (user_id uuid, email text)
language sql
stable
security definer
set search_path = public
as $$
  select distinct u.id, u.email::text
  from auth.users u
  where public.is_admin()
    and u.id = any(p_user_ids)
    and exists (select 1 from public.inquiries i where i.user_id = u.id);
$$;

grant execute on function public.admin_account_emails(uuid[]) to authenticated;

-- ============================================================
-- BOOTSTRAP YOUR FIRST ADMIN
-- ------------------------------------------------------------
-- 1. Create a normal account on /account (sign up + confirm email).
-- 2. Then run the insert below with that account's email.
-- ============================================================
-- insert into public.admins (user_id)
-- select id from auth.users where email = 'you@purchasingcorp.com'
-- on conflict (user_id) do nothing;
