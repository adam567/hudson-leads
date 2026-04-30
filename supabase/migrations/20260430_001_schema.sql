-- Hudson Leads schema. Multi-tenant from day one (single org today).

create extension if not exists pg_trgm;
create extension if not exists "uuid-ossp";

-- ── Tenancy ─────────────────────────────────────────────────────────────
create table if not exists orgs (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  active boolean not null default true,
  created_at timestamptz not null default now()
);

create table if not exists profiles (
  user_id uuid primary key references auth.users on delete cascade,
  email text,
  full_name text,
  created_at timestamptz not null default now()
);

create table if not exists org_members (
  org_id uuid not null references orgs(id) on delete cascade,
  user_id uuid not null references auth.users on delete cascade,
  role text not null default 'member' check (role in ('owner','member')),
  created_at timestamptz not null default now(),
  primary key (org_id, user_id)
);
create index if not exists idx_org_members_user on org_members(user_id);

-- ── Property spine ──────────────────────────────────────────────────────
create table if not exists parcels (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references orgs(id) on delete cascade,
  county_parcel_id text not null,
  county text not null,
  situs_address text,
  situs_city text,
  situs_zip text,
  mailing_address text,
  mailing_same_as_situs boolean,
  sqft integer,
  market_value numeric,
  last_sale_date date,
  last_sale_price numeric,
  years_owned integer,
  owner1_raw text,
  owner2_raw text,
  source text,
  source_payload jsonb,
  refreshed_at timestamptz not null default now(),
  unique (org_id, county_parcel_id)
);
create index if not exists idx_parcels_zip on parcels(org_id, situs_zip);
create index if not exists idx_parcels_owner1_trgm on parcels using gin (owner1_raw gin_trgm_ops);

-- ── Household rollup (the working unit shown in the UI) ────────────────
create table if not exists households (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references orgs(id) on delete cascade,
  parcel_id uuid not null references parcels(id) on delete cascade,
  display_name text,
  surname_key text,
  owner_names text[],
  target_zip boolean default false,
  owned_15_plus boolean default false,
  top_quartile_value boolean default false,
  status text not null default 'new' check (status in ('new','reviewing','contacted','paused','won','dropped')),
  notes text default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (org_id, parcel_id)
);
create index if not exists idx_households_status on households(org_id, status);
create index if not exists idx_households_surname_trgm on households using gin (surname_key gin_trgm_ops);

create table if not exists household_owners (
  id uuid primary key default gen_random_uuid(),
  household_id uuid not null references households(id) on delete cascade,
  full_name_raw text not null,
  full_name_norm text,
  first_name text,
  last_name text,
  is_primary boolean default false
);
create index if not exists idx_owner_norm_trgm on household_owners using gin (full_name_norm gin_trgm_ops);
create index if not exists idx_owner_lastname on household_owners(last_name);

-- ── Senior confirmation workflow ───────────────────────────────────────
create table if not exists senior_batches (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references orgs(id) on delete cascade,
  uploaded_by uuid references auth.users,
  source_label text,
  grad_year int,
  raw_text text,
  created_at timestamptz not null default now()
);

create table if not exists confirmed_seniors (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references orgs(id) on delete cascade,
  batch_id uuid references senior_batches(id) on delete set null,
  senior_name_raw text not null,
  senior_name_norm text,
  first_name text,
  last_name text,
  parent_names_raw text,
  school text,
  grad_year int,
  matched_household_id uuid references households(id),
  match_confidence numeric,
  match_status text default 'pending' check (match_status in ('pending','matched','no_match','rejected','manual')),
  reviewed_by uuid references auth.users,
  reviewed_at timestamptz,
  created_at timestamptz not null default now()
);
create index if not exists idx_seniors_lastname on confirmed_seniors(org_id, last_name);
create index if not exists idx_seniors_norm_trgm on confirmed_seniors using gin (senior_name_norm gin_trgm_ops);

-- ── Scoring ─────────────────────────────────────────────────────────────
create table if not exists scores (
  household_id uuid primary key references households(id) on delete cascade,
  org_id uuid not null references orgs(id) on delete cascade,
  tenure_points numeric default 0,
  value_points numeric default 0,
  confirmation_points numeric default 0,
  window_multiplier numeric default 1,
  total_score numeric default 0,
  tier text,
  scoring_version int default 1,
  updated_at timestamptz not null default now()
);
create index if not exists idx_scores_total on scores(org_id, total_score desc);

-- updated_at trigger ----------------------------------------------------
create or replace function set_updated_at() returns trigger as $$
begin new.updated_at = now(); return new; end;
$$ language plpgsql;

drop trigger if exists trg_households_updated on households;
create trigger trg_households_updated before update on households
  for each row execute function set_updated_at();
