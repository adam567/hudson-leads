# hudson-leads

Empty-nester / senior-parent lead-gen SaaS for a single Hudson, Ohio realtor.

**Live:** <https://adam567.github.io/hudson-leads/>

**Stack:** GitHub Pages (frontend) + Supabase (Postgres + Auth + RLS) + GitHub Actions (cron). No build step. Solo-maintainable.

**Architecture:** see `MEMO.md` and the encrypted strategy page at <https://adam567.github.io/hudson-strategy/>.

```
site/                       static frontend, deployed to Pages
  index.html                single-file app, no framework
  app.js                    auth, list, dossier, paste-and-match
  styles.css
  config.js                 injected at build time from repo secrets

scripts/
  refresh_parcels.py        weekly scraper — ArcGIS REST or seed fallback
  requirements.txt

supabase/migrations/
  20260430_001_schema.sql   tables, indexes, pg_trgm
  20260430_002_rls.sql      org-scoped RLS on every user-facing table
  20260430_003_scoring.sql  scoring function + window multiplier + fuzzy matcher

seed/
  seed_households.json      30 plausible Hudson households (clearly synthetic)
  seed_seniors.csv          10 starter seniors (for demo)

.github/workflows/
  deploy-pages.yml          ships site/ to Pages on push
  refresh-data.yml          weekly scraper + manual dispatch
  deploy-db.yml             pushes migrations to Supabase
```

See `SETUP.md` for the 10-minute provisioning steps.
