# Hudson Leads — Setup

This is a SaaS-shaped MVP. The frontend lives on GitHub Pages (free); the database and auth live on Supabase (free tier); the weekly scraper runs in GitHub Actions (free).

The only manual step is provisioning Supabase. Budget 10 minutes.

## 1. Create the Supabase project

1. Go to <https://supabase.com>, sign up (free), click **New Project**.
2. Name it `hudson-leads`. Pick the closest region (US East).
3. Set a **Database Password** — save it in 1Password.
4. After it provisions (~90 sec), grab two things from **Project Settings → API**:
   - `Project URL` (e.g. `https://abcdwxyz.supabase.co`)
   - `anon public` key
   - `service_role` key  ← treat this like a password; it bypasses RLS

## 2. Apply the schema

Two ways. Pick one.

### A) Browser (easy)

In Supabase: **SQL Editor → New query**, paste the contents of these files in order, run each:

1. `supabase/migrations/20260430_001_schema.sql`
2. `supabase/migrations/20260430_002_rls.sql`
3. `supabase/migrations/20260430_003_scoring.sql`

### B) CLI (better long-term)

```bash
npm i -g supabase
supabase login   # opens browser, paste the access token
supabase link --project-ref <your-project-ref>
supabase db push --include-all
```

Add to GitHub repo secrets so future migrations auto-deploy:
- `SUPABASE_ACCESS_TOKEN` — from <https://supabase.com/dashboard/account/tokens>
- `SUPABASE_PROJECT_REF` — the slug from the project URL
- `SUPABASE_DB_PASSWORD` — from step 1.3

## 3. Create the org and invite the realtor

In Supabase **SQL Editor**:

```sql
-- 1. Create the org. Save the returned uuid.
insert into orgs (name) values ('Hudson Realtor — [Client Name]') returning id;

-- 2. Note that org id, you'll need it as ORG_ID below.

-- 3. Invite the realtor. Use her real email.
-- In Supabase: Authentication → Users → "Invite user" — type her email,
-- pick "Magic Link" or just leave the default. She'll receive an email.
-- Tip: also invite yourself as the admin user with the same email
-- you'll use to sign in.

-- 4. After she/you sign in once (so the auth.users row exists), make her
-- a member of the org:
insert into org_members (org_id, user_id, role)
select '<ORG_ID_FROM_STEP_1>', id, 'owner'
  from auth.users where email = 'realtor@example.com';

-- And yourself:
insert into org_members (org_id, user_id, role)
select '<ORG_ID_FROM_STEP_1>', id, 'member'
  from auth.users where email = 'adamscavone@gmail.com';
```

## 4. Configure GitHub repo

Go to <https://github.com/adam567/hudson-leads/settings/secrets/actions> and add:

**Secrets:**
| Name | Value |
|---|---|
| `SUPABASE_URL` | from step 1.4 |
| `SUPABASE_ANON_KEY` | from step 1.4 |
| `SUPABASE_SERVICE_ROLE_KEY` | from step 1.4 |
| `ORG_ID` | the uuid from step 3 |
| `FEATURE_SERVER` | (optional) ArcGIS REST URL ending in `/FeatureServer/0` for Summit County parcels — leave empty to use seed data |

**Variables (under "Actions secrets and variables → Variables"):**
| Name | Default |
|---|---|
| `TARGET_ZIPS` | `44236` |
| `MIN_SQFT` | `2800` |
| `MIN_YEARS_OWNED` | `15` |
| `SEED_FALLBACK` | `true` until you have a real `FEATURE_SERVER` |

Then enable Pages: **Settings → Pages → Source: GitHub Actions**.

## 5. Run it

```bash
# Manually fire the scraper (loads seed data into Supabase the first time)
gh workflow run "Refresh Parcel Data" --ref main

# Watch logs
gh run watch
```

Then visit <https://adam567.github.io/hudson-leads/>. Enter the realtor's email, click **Send code**, paste the 6-digit code, hit **Sign in**.

She should see ranked households immediately. Click any row → dossier drawer with score breakdown, status workflow, notes. Click **Confirm seniors** → paste names from a school PDF → the app fuzzy-matches them against homeowners and re-scores.

## What runs when

- **Every Monday at 6:17 AM ET** the scraper refreshes parcels, re-runs the senior matcher, and recomputes scores. The dashboard reflects the new data on the realtor's next page load.
- **On every push to `main`** that touches `site/**`, GitHub Pages rebuilds (~30 sec).
- **On every push to `main`** that touches `supabase/migrations/**`, the DB migration job runs (if you set up the access token in step 2.B).

## Swapping seed data for real Summit County data

The Summit County Fiscal Office's GIS open-data portal exposes a tax-parcels FeatureServer. As of writing, the public REST endpoint isn't trivially discoverable from the front-door URL — you'll need to dig through the open-data portal at <https://data-summitgis.opendata.arcgis.com/> or contact the Summit County GIS office (`gis@summitoh.net`) for the underlying ArcGIS REST URL. Once you have a URL ending in `/FeatureServer/0`, drop it in the `FEATURE_SERVER` repo secret, set `SEED_FALLBACK=false`, run the workflow manually, and you're on real data.

The scraper is generic over ArcGIS REST — same code works for Portage and Cuyahoga (they expose similar feature services). Add more zips to `TARGET_ZIPS` and re-run.

## Costs

- **Supabase free tier:** 500 MB DB, 50K monthly auth users, 5 GB egress. Easily fits this app.
  - **Caveat:** a free project is paused after 7 days of zero traffic. If the realtor signs in monthly, that's enough to keep it warm. If you want a cron heartbeat to be safe, the weekly Actions job already hits Supabase, so you're covered.
- **GitHub Pages + Actions:** unlimited on public repos.
- **Total:** $0/mo until you outgrow free tier (thousands of households / many users).
