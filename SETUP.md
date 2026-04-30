# Hudson Leads — Setup

Frontend on GitHub Pages (free), database + auth on Supabase (free), weekly scraper on GitHub Actions (free). Total: $0/mo.

The only manual step is provisioning Supabase. Budget 10 minutes.

## What it actually does (read first)

**Real data:** The scraper hits the Summit County Fiscal Office's public ArcGIS REST endpoint and pulls every parcel in Hudson 44236 with `resflrarea ≥ 2800` sqft. That's roughly 3,300 households of plausibly empty-nester-sized homes. Owner names, situs address, market value, year built, building square footage, and centroid lat/lng are all real, refreshed weekly.

**Honest gap:** The public REST layer doesn't expose sale-transfer dates. So `years_owned` starts out null for every record. The scorer falls back to building age (today minus `year_built`) as the tenure proxy — a 1985 house is statistically more likely to have a long-tenured owner than a 2020 house. It's not perfect, but it's directionally useful and it's based on real data.

**To unlock real `years_owned`,** wire `scripts/enrich_sales.py`. It's documented but stubbed — the disclaimer-page session dance for `propertyaccess.summitoh.net` and the HTML-parsing for the Sales History table need ~half a day of focused work. Until then, the dashboard ranks on age + value + senior confirmation.

**No synthetic data anywhere.** If the scraper can't reach Summit, it errors out. If the dashboard is empty, it's because the scraper hasn't run yet.

## 1. Create the Supabase project

1. Go to <https://supabase.com>, sign up (free), click **New Project**.
2. Name it `hudson-leads`. Pick the closest region (US East).
3. Set a **Database Password** — save it in 1Password.
4. After it provisions (~90 sec), grab three things from **Project Settings → API**:
   - `Project URL` (e.g. `https://abcdwxyz.supabase.co`)
   - `anon public` key
   - `service_role` key  ← treat this like a password; it bypasses RLS

## 2. Apply the schema

In Supabase: **SQL Editor → New query**, paste each of these in order, run:

1. `supabase/migrations/20260430_001_schema.sql`
2. `supabase/migrations/20260430_002_rls.sql`
3. `supabase/migrations/20260430_003_scoring.sql`
4. `supabase/migrations/20260430_004_extras.sql`
5. `supabase/migrations/20260430_005_rescore.sql`

Or via the CLI for future automation:

```bash
npm i -g supabase
supabase login
supabase link --project-ref <your-project-ref>
supabase db push --include-all
```

## 3. Create the org and invite the realtor

In Supabase **SQL Editor**:

```sql
insert into orgs (name) values ('Hudson Realtor — [Client Name]') returning id;
-- Save the returned uuid as ORG_ID.
```

In **Authentication → Users → "Invite user"** invite both:
- Yourself (`adamscavone@gmail.com`)
- The realtor (her email)

Once each user has signed in once (so the auth.users row exists), grant org membership:

```sql
insert into org_members (org_id, user_id, role)
select '<ORG_ID>', id, 'owner'
  from auth.users where email = 'realtor@example.com';

insert into org_members (org_id, user_id, role)
select '<ORG_ID>', id, 'member'
  from auth.users where email = 'adamscavone@gmail.com';
```

## 4. Configure GitHub repo

<https://github.com/adam567/hudson-leads/settings/secrets/actions>

**Secrets:**

| Name | Value |
|---|---|
| `SUPABASE_URL` | from step 1.4 |
| `SUPABASE_ANON_KEY` | from step 1.4 |
| `SUPABASE_SERVICE_ROLE_KEY` | from step 1.4 |
| `ORG_ID` | the uuid from step 3 |

**Variables (under "Actions secrets and variables → Variables"):**

| Name | Default |
|---|---|
| `TARGET_ZIPS` | `44236` |
| `TARGET_CITY` | `HUDSON` |
| `MIN_SQFT` | `2800` |

`FEATURE_SERVER` is hardcoded to the Summit County endpoint in the scraper. Override by setting it as a secret only if you point at Portage or Cuyahoga later.

Then enable Pages: **Settings → Pages → Source: GitHub Actions**.

## 5. Run it

```bash
gh workflow run "Refresh Parcel Data" --ref main
gh run watch
```

The first run takes ~30 sec and pulls every Hudson 44236 parcel ≥ 2800 sqft. You should see `[arcgis] N parcels passed filters for 44236` and `[supabase] upserted N parcels` (where N is in the low thousands).

Then visit <https://adam567.github.io/hudson-leads/>. Sign in with the realtor's email, paste the 6-digit code. She sees the ranked household table immediately.

## What runs automatically

- **Every Monday at 6:17 AM ET** the scraper refreshes parcels, re-runs the senior matcher, and recomputes scores.
- **Every push to `main` touching `site/**`** rebuilds Pages (~30 sec).
- **Every push to `main` touching `supabase/migrations/**`** pushes DB migrations (if `SUPABASE_ACCESS_TOKEN` is set).

## Extending to Portage / Cuyahoga

The scraper is generic over ArcGIS REST. Both Portage and Cuyahoga publish similar parcel feature services. Set the `FEATURE_SERVER` secret to the alternate URL, adjust `*_FIELD` env vars in the workflow if their CAMA field names differ, run `gh workflow run "Refresh Parcel Data"`. Done.

## Costs

- **Supabase free:** 500 MB DB, 50K monthly auth users, 5 GB egress. Easily fits this app for years.
  - **Note:** free projects pause after 7 days of zero traffic. The weekly scraper job hits the DB, so this is moot.
- **GitHub Pages + Actions:** unlimited on public repos.
- **Total:** $0/mo until you outgrow free tier.
