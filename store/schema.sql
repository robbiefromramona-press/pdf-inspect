-- PDF extraction store — per-user, shared by every PDF tool in the family.
--
-- STATUS: DESIGN ONLY. NOT DEPLOYED.
-- This table is scoped to a signed-in user (auth.users). There are no user
-- accounts on any of the sites yet, so nothing writes here today. PDF-Inspect
-- currently saves the same rows to the browser only (js/store.js).
--
-- Follows the qr-point pattern: row level security on, and the browser never
-- sees the service-role key.

create table if not exists public.pdf_extractions (
  id              bigint generated always as identity primary key,
  user_id         uuid not null references auth.users (id) on delete cascade,
  file_sha256     text not null check (file_sha256 ~ '^[0-9a-f]{64}$'),  -- the join key every app can compute
  upload_id       uuid,                                                   -- optional per-app upload session
  file_name       text,
  source_app      text not null default 'pdf-inspect',                    -- which tool extracted it
  field_key       text not null,                                          -- engine/fields.json key, or 'calibration'
  status          text not null check (status in ('found', 'absent', 'error', 'locked')),
  value           jsonb not null,                                         -- { summary, detail, notes, flags, data }
  engine_version  text,
  extracted_at    timestamptz not null default now(),
  -- RETENTION (decided 2026-09-16):
  --   * Now: 30 days after extraction (the default below).
  --   * Once memberships are active: life of the account. Write rows for
  --     members with expires_at = null; they are removed when the account is
  --     deleted (on delete cascade above).
  expires_at      timestamptz default (now() + interval '30 days'),
  unique (user_id, file_sha256, field_key)
);

create index if not exists pdf_extractions_lookup_idx on public.pdf_extractions (user_id, file_sha256);
create index if not exists pdf_extractions_expiry_idx on public.pdf_extractions (expires_at) where expires_at is not null;

alter table public.pdf_extractions enable row level security;

-- A signed-in user can only see and change their own rows.
create policy "own rows: read" on public.pdf_extractions
  for select using (auth.uid() = user_id);
create policy "own rows: insert" on public.pdf_extractions
  for insert with check (auth.uid() = user_id);
create policy "own rows: update" on public.pdf_extractions
  for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "own rows: delete" on public.pdf_extractions
  for delete using (auth.uid() = user_id);

-- CoordXY's lookup: "has this file already been inspected, and what scale or
-- coordinate data is on file?" Returns null when there is nothing usable.
create or replace function public.lookup_calibration(p_file_sha256 text)
returns jsonb
language sql
stable
security invoker  -- runs under the caller's row level security
as $$
  select value -> 'data'
  from public.pdf_extractions
  where user_id = auth.uid()
    and file_sha256 = p_file_sha256
    and field_key = 'calibration'
    and status = 'found'
    and (expires_at is null or expires_at > now())
  order by extracted_at desc
  limit 1;
$$;

-- Purge expired rows. Schedule it when this table is deployed, e.g. with pg_cron:
--   select cron.schedule('purge-pdf-extractions', '17 * * * *', 'select public.purge_expired_pdf_extractions()');
create or replace function public.purge_expired_pdf_extractions()
returns integer
language sql
as $$
  with gone as (
    delete from public.pdf_extractions where expires_at is not null and expires_at <= now() returning 1
  )
  select count(*)::integer from gone;
$$;

comment on table public.pdf_extractions is 'Per-user PDF metadata extracted by PDF-Inspect and reused by CoordXY and future tools. Keyed by file SHA-256.';
