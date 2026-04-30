#!/usr/bin/env python3
"""Enrichment scraper: fills in last_sale_date / years_owned per parcel by
hitting the public PropertyAccess search at propertyaccess.summitoh.net.

WHY THIS EXISTS
---------------
Summit County's public ArcGIS REST tax-parcels layer publishes ownership,
address, square footage, value, and year built — but not sale dates. The
PropertyAccess web app *does* publish sale history, but only behind a
session-cookie disclaimer page (Tyler Technologies iasWorld). This script
performs the disclaimer dance, then walks each parcel detail page and pulls
the most recent transfer date.

SCOPE
-----
Slow (1 request per parcel + polite 0.4s delay). Designed to run once on the
candidate set the realtor cares about — not on the full 3,000+ Hudson universe
every week. Expected cadence: monthly at most, or on-demand for a specific
slice.

INVOCATION
----------
    SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... ORG_ID=... \
        python scripts/enrich_sales.py [--limit N] [--only-tier A,B]

NOT YET IMPLEMENTED
-------------------
The PropertyAccess HTML parser is intentionally stubbed below. Building it
requires testing against live HTML structure. When you wire it, replace the
TODO blocks. Until then this script is documented infrastructure, not active
code — and `years_owned` stays null in the dashboard.
"""
from __future__ import annotations

import os
import sys
import time
import argparse
import json
from datetime import datetime, date
from typing import Iterable

import requests

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
ORG_ID = os.environ.get("ORG_ID", "")

DISCLAIMER_URL = "https://propertyaccess.summitoh.net/Search/Disclaimer.aspx?FromUrl=../search/commonsearch.aspx?mode=realprop"
DETAIL_URL = "https://propertyaccess.summitoh.net/Datalets/Datalet.aspx?sIndex=0&idx=1&LMparent=20"


def get_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "hudson-leads/0.1 (research; ascavone@gmail.com)"})
    # TODO: walk the Disclaimer page, find the Accept button name, POST it.
    # The session cookie set after acceptance is what unlocks /search/* and /Datalets/*.
    s.get(DISCLAIMER_URL, timeout=30)
    return s


def fetch_sale_date(session: requests.Session, parcel_id: str) -> date | None:
    """Return the most recent transfer date for a parcel, or None."""
    # TODO: fetch the parcel detail page, parse the Sales History table,
    # return the most-recent date. Tyler iasWorld pages typically have a
    # table with class 'DataletSideHead' or 'SalesData'.
    return None


def supabase_request(method: str, path: str, **kwargs) -> requests.Response:
    headers = kwargs.pop("headers", {}) or {}
    headers.update({
        "apikey": SERVICE_KEY,
        "Authorization": f"Bearer {SERVICE_KEY}",
        "Content-Type": "application/json",
    })
    url = f"{SUPABASE_URL}/rest/v1{path}"
    r = requests.request(method, url, headers=headers, timeout=60, **kwargs)
    r.raise_for_status()
    return r


def candidate_parcels(limit: int, only_tier: list[str] | None) -> list[dict]:
    """Return parcels still missing sale data, optionally restricted to a tier set."""
    params = {
        "select": "id,county_parcel_id,situs_address",
        "org_id": f"eq.{ORG_ID}",
        "last_sale_date": "is.null",
        "limit": str(limit),
    }
    r = supabase_request("GET", "/parcels?" + "&".join(f"{k}={v}" for k, v in params.items()))
    return r.json()


def main():
    if not (SUPABASE_URL and SERVICE_KEY and ORG_ID):
        sys.exit("missing SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY / ORG_ID")
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--only-tier", type=str, default="A,B")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    sess = get_session()
    parcels = candidate_parcels(args.limit, args.only_tier.split(","))
    if not parcels:
        print("no candidate parcels missing sale data")
        return

    print(f"enriching {len(parcels)} parcels (gap = sale_date is null)")
    today = date.today()
    updated = 0
    for p in parcels:
        sale = fetch_sale_date(sess, p["county_parcel_id"])
        if not sale:
            continue
        yrs = today.year - sale.year - ((today.month, today.day) < (sale.month, sale.day))
        body = {"last_sale_date": sale.isoformat(), "years_owned": max(0, yrs)}
        if not args.dry_run:
            supabase_request("PATCH", f"/parcels?id=eq.{p['id']}",
                             headers={"Prefer": "return=minimal"},
                             data=json.dumps(body))
        updated += 1
        time.sleep(0.4)
    print(f"updated {updated} parcels")


if __name__ == "__main__":
    main()
