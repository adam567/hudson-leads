# hudson-leads

Empty-nester / senior-parent lead-gen dashboard for a single Hudson, Ohio realtor.

**Live:** <https://adam567.github.io/hudson-leads/>
**Strategy memo (encrypted):** <https://adam567.github.io/hudson-strategy/>

**Stack:** GitHub Pages (frontend) + Supabase (Postgres + Auth + RLS) + GitHub Actions (cron). No build step. Solo-maintainable. Real data only — no synthetic seed.

## Data sources

| Layer | Source | Status |
|---|---|---|
| Parcels (owner, address, sqft, value, year-built, lat/lng) | Summit County Fiscal Office ArcGIS REST: [`parcels_web_GEODATA_Tax_Parcels/FeatureServer/0`](https://scgis.summitoh.net/hosted/rest/services/parcels_web_GEODATA_Tax_Parcels/FeatureServer/0) | Live, weekly refresh |
| Sale-history / years-owned | propertyaccess.summitoh.net (per-parcel HTML) | Stubbed (`scripts/enrich_sales.py`) — needs the disclaimer-session dance and HTML parser implemented before it's active |
| Senior confirmation | Realtor pastes names from school PDFs (NHS, college signing day, banquets) | Live; pg_trgm fuzzy-matched against owner surnames |

## Layout

```
site/                       static frontend, deployed to Pages
  index.html                table + map + dossier drawer + senior-paste modal
  app.js                    auth, list, map (Leaflet+heat), CSV export, keyboard shortcuts
  styles.css
  config.js                 injected at build time from repo secrets

scripts/
  refresh_parcels.py        weekly ArcGIS REST scraper, real data only
  enrich_sales.py           per-parcel sale-history enrichment (stub)
  requirements.txt

supabase/migrations/
  20260430_001_schema.sql      tables, indexes, pg_trgm
  20260430_002_rls.sql         org-scoped RLS on every user-facing table
  20260430_003_scoring.sql     window multiplier + fuzzy matcher + initial scoring fn
  20260430_004_extras.sql      lat/lng, year_built, last_touched, dashboard view
  20260430_005_rescore.sql     scoring updated for null years_owned (year_built fallback)

.github/workflows/
  deploy-pages.yml             ships site/ to Pages on push, injects Supabase config
  refresh-data.yml             weekly scraper + manual dispatch
  deploy-db.yml                pushes migrations to Supabase
```

## Frontend features

- **Email-OTP login** via Supabase Auth (no passwords, no magic links)
- **Ranked table view:** tier (A/B/C), score, owner, address, years owned, value, senior confirmed, status, last touched
- **Map view:** Leaflet + OSM tiles + heatmap of A/B households, weighted by tier; clickable markers open the dossier
- **Dossier drawer:** facts, score breakdown bars, owners list, status workflow, free-text notes
- **CSV export:** one click, downloads visible rows with all columns for mail-merge / direct mail
- **Senior confirmation:** paste names from a school PDF, fuzzy-match against owners (pg_trgm, ≥0.45 threshold), auto-promote matched households to top tier, recompute scores
- **Window pill** in the header: tells the realtor where today falls (silent-house / planning / reactivation / avoid / off-window) — the scoring multiplier reflects the same calendar
- **Keyboard shortcuts** in the dossier drawer:
  - `j` — advance status (new → reviewing → contacted → paused → won → dropped)
  - `k` — regress status
  - `n` — focus the notes field
  - `esc` — close

## See `SETUP.md`

For the 10-minute Supabase provisioning steps, repo secrets, and how to fire the first scraper run.
