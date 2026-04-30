# hudson-leads

A property-prospecting dashboard for a single Hudson, Ohio realtor. Ranked list of long-tenured, owner-occupied Hudson homes worth a personal touch.

**Live:** <https://adam567.github.io/hudson-leads/>
**Strategy memo:** <https://adam567.github.io/hudson-strategy/>

## What this is — and what it isn't

It is **not** an empty-nester or "graduating senior parents" finder. After two rounds of peer review, that hypothesis turned out to be unbuildable on free data:
- School sites publish senior logistics and academic honors but not stable, machine-usable parent-linked senior rosters.
- Scraping minors' names to match against property records is a privacy / ToS minefield.
- The signal that *would* work — household composition with child-age bands — sits behind paid demographic vendors (Melissa, First American DataTree, Caldwell List) at low hundreds of dollars per pull. Until that's bought, senior targeting is not a thing this app can honestly do.

It **is** a ranked list of likely-established homeowners in the Hudson 44236 service area, scored on signals we can verify from public records:

- **Years owned** — derived from Summit County's `SC706_SALES.zip` bulk-data download (977K sale-transfer records, refreshed daily by the county). Joined to parcels by ID.
- **Market value** — current CAMA market value from the parcel REST.
- **Square footage** — residential floor area.
- **Year built** — supplemental signal for tenure where sale-date is missing.
- **Owner-occupied** — parcels where situs address ≠ mailing address (absentee landlords, LLC rentals, out-of-state owners) are dropped.

Tier A = score ≥ 70 (long-tenured, top-quartile value, large home). Tier B = 45–69. Tier C = under 45. Today's universe: **2,698 owner-occupied Hudson 44236 homes ≥ 2,800 sqft, ~187 Tier A, ~1,228 Tier B**.

## Stack

GitHub Pages (frontend) + Supabase (Postgres + Auth + RLS) + GitHub Actions cron. No build step. Solo-maintainable. $0/mo.

## Layout

```
site/                       static frontend
  index.html                table + map + dossier drawer
  app.js                    auth, list, map, CSV export, keyboard shortcuts
  styles.css
  config.js                 injected at deploy time from GH secrets

scripts/
  refresh_parcels.py        weekly: ArcGIS REST + SC706_SALES.zip + owner-occupied filter
  enrich_sales.py           (deprecated; SC706 covers it)
  requirements.txt

supabase/migrations/
  20260430_001_schema.sql        tables, indexes, pg_trgm
  20260430_002_rls.sql           org-scoped RLS
  20260430_003_scoring.sql       (superseded; senior matching + window mult — kept for history)
  20260430_004_extras.sql        lat/lng, year_built, last_touched, dashboard view
  20260430_005_rescore.sql       (superseded by 006)
  20260430_006_property_only.sql current scoring + dashboard view, no senior

.github/workflows/
  deploy-pages.yml          ships site/ to Pages on push, injects Supabase config
  refresh-data.yml          weekly Mondays 6:17 AM ET + manual dispatch
  deploy-db.yml             pushes migrations to Supabase
```

## Frontend

- **Email-OTP login** via Supabase Auth
- **Ranked table:** tier, score, owner, address, years owned, value, sqft, status, last touched. Click any column to sort. Click any row → dossier drawer.
- **Map view:** Leaflet + OSM tiles + tier-weighted heatmap, clickable markers
- **Dossier drawer:** facts (years owned, last sale, value, sqft, year built, owner-occupied?), score breakdown bars, owners list, status workflow, free-text notes
- **CSV export:** one click, all visible rows with all columns, ready for mail-merge
- **Filters:** tier, status, min-years-owned, free-text owner/address search
- **Top 50 button:** one-click filter to the highest-scoring fifty
- **Keyboard shortcuts in the dossier drawer:** `j` advance status, `k` regress, `n` notes, `esc` close

See `SETUP.md` for the 10-minute Supabase provisioning steps.
