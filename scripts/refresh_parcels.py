#!/usr/bin/env python3
"""Refresh parcel data from a configurable ArcGIS REST FeatureServer.

Pulls Hudson-area (or any zip-filtered) parcels with the standard CAMA fields,
applies the v0 filters (15+ yrs owned, 2800+ sqft, target zips), and upserts
into Supabase via the service-role key.

Configuration (env vars):
    SUPABASE_URL                Supabase project URL
    SUPABASE_SERVICE_ROLE_KEY   Service role key (bypasses RLS)
    ORG_ID                      uuid of the org to write into
    FEATURE_SERVER              base FeatureServer/0 URL (no trailing /query)
    TARGET_ZIPS                 comma-sep, e.g. "44236,44067"
    ZIP_FIELD                   field name in source for zip       (default ZIPCODE)
    ADDR_FIELD                  field name for situs address       (default SITUS_ADDR)
    OWNER_FIELD                 field name for primary owner       (default OWNER)
    OWNER2_FIELD                second owner field, optional
    SQFT_FIELD                  finished area field                (default FIN_AREA)
    VALUE_FIELD                 market value field                 (default TOT_MKT_VAL)
    SALE_DATE_FIELD             last sale date field               (default SALEDATE)
    SALE_PRICE_FIELD            last sale price field              (default SALEPRICE)
    PARCEL_ID_FIELD             parcel id                          (default PARCEL_ID)
    CITY_FIELD                  city                               (default SITUS_CITY)
    MIN_SQFT                    default 2800
    MIN_YEARS_OWNED             default 15
    SEED_FALLBACK               "1" to load seed/seed_households.json instead of remote

If FEATURE_SERVER is unset OR SEED_FALLBACK=1, this script loads
seed/seed_households.json — a clearly-labeled demo dataset — so the dashboard
has something visible while the real source is being wired up.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

import requests

ROOT = Path(__file__).resolve().parent.parent
SEED_PATH = ROOT / "seed" / "seed_households.json"

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
ORG_ID = os.environ.get("ORG_ID", "")
FEATURE_SERVER = os.environ.get("FEATURE_SERVER", "").rstrip("/")
TARGET_ZIPS = [z.strip() for z in os.environ.get("TARGET_ZIPS", "44236").split(",") if z.strip()]
SEED_FALLBACK = os.environ.get("SEED_FALLBACK", "").lower() in ("1", "true", "yes")
MIN_SQFT = int(os.environ.get("MIN_SQFT", "2800"))
MIN_YEARS_OWNED = int(os.environ.get("MIN_YEARS_OWNED", "15"))

F = {
    "zip":         os.environ.get("ZIP_FIELD", "ZIPCODE"),
    "addr":        os.environ.get("ADDR_FIELD", "SITUS_ADDR"),
    "city":        os.environ.get("CITY_FIELD", "SITUS_CITY"),
    "owner":       os.environ.get("OWNER_FIELD", "OWNER"),
    "owner2":      os.environ.get("OWNER2_FIELD", ""),
    "sqft":        os.environ.get("SQFT_FIELD", "FIN_AREA"),
    "value":       os.environ.get("VALUE_FIELD", "TOT_MKT_VAL"),
    "sale_date":   os.environ.get("SALE_DATE_FIELD", "SALEDATE"),
    "sale_price":  os.environ.get("SALE_PRICE_FIELD", "SALEPRICE"),
    "parcel_id":   os.environ.get("PARCEL_ID_FIELD", "PARCEL_ID"),
}


def supabase_required() -> None:
    missing = [k for k, v in {
        "SUPABASE_URL": SUPABASE_URL,
        "SUPABASE_SERVICE_ROLE_KEY": SERVICE_KEY,
        "ORG_ID": ORG_ID,
    }.items() if not v]
    if missing:
        sys.exit(f"missing env vars: {', '.join(missing)}")


def supabase_request(method: str, path: str, **kwargs) -> requests.Response:
    headers = kwargs.pop("headers", {}) or {}
    headers.update({
        "apikey": SERVICE_KEY,
        "Authorization": f"Bearer {SERVICE_KEY}",
        "Content-Type": "application/json",
    })
    url = f"{SUPABASE_URL}/rest/v1{path}"
    r = requests.request(method, url, headers=headers, timeout=60, **kwargs)
    if not r.ok:
        sys.exit(f"supabase {method} {path} -> {r.status_code}: {r.text[:500]}")
    return r


def fetch_arcgis(zip_code: str) -> Iterable[dict]:
    """Stream features from an ArcGIS FeatureServer/0 with paging."""
    if not FEATURE_SERVER:
        return []
    out_fields = ",".join(v for v in [
        F["parcel_id"], F["addr"], F["city"], F["zip"], F["owner"],
        F["owner2"] or None, F["sqft"], F["value"], F["sale_date"], F["sale_price"],
    ] if v)
    where = f"{F['zip']}='{zip_code}' AND {F['sqft']}>={MIN_SQFT}"
    offset = 0
    page = 1000
    seen = 0
    while True:
        params = {
            "where": where,
            "outFields": out_fields,
            "returnGeometry": "false",
            "f": "json",
            "resultOffset": offset,
            "resultRecordCount": page,
        }
        r = requests.get(f"{FEATURE_SERVER}/query", params=params, timeout=60)
        r.raise_for_status()
        data = r.json()
        feats = data.get("features", []) or []
        if not feats:
            return
        for feat in feats:
            yield feat.get("attributes", {})
            seen += 1
        if len(feats) < page or not data.get("exceededTransferLimit"):
            return
        offset += page
        time.sleep(0.2)


def epoch_to_date(v: Any) -> date | None:
    if v is None or v == "":
        return None
    try:
        if isinstance(v, (int, float)) and v > 1e10:
            return datetime.utcfromtimestamp(v / 1000).date()
        if isinstance(v, str):
            for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d"):
                try:
                    return datetime.strptime(v[:10], fmt).date()
                except ValueError:
                    continue
    except Exception:
        return None
    return None


def years_owned(sale_date: date | None) -> int | None:
    if not sale_date:
        return None
    today = date.today()
    yrs = today.year - sale_date.year - ((today.month, today.day) < (sale_date.month, sale_date.day))
    return max(0, yrs)


def normalize_owner(name: str | None) -> dict:
    if not name:
        return {"raw": None, "norm": None, "first": None, "last": None}
    raw = name.strip()
    cleaned = re.sub(r"\s+", " ", raw.upper())
    cleaned = re.sub(r"\b(JR|SR|II|III|IV|TRUSTEE|TR|ETAL|ET\s*UX|ET\s*AL)\b\.?", "", cleaned).strip()
    last, first = None, None
    if "," in cleaned:
        parts = [p.strip() for p in cleaned.split(",", 1)]
        last, first = parts[0], parts[1] if len(parts) > 1 else None
    else:
        toks = cleaned.split()
        if len(toks) >= 2:
            first, last = toks[0], toks[-1]
        elif toks:
            last = toks[0]
    return {"raw": raw, "norm": cleaned, "first": first, "last": last}


def to_row(attrs: dict) -> dict | None:
    sale = epoch_to_date(attrs.get(F["sale_date"]))
    yrs = years_owned(sale)
    if yrs is None or yrs < MIN_YEARS_OWNED:
        return None
    sqft = attrs.get(F["sqft"])
    if sqft is None or sqft < MIN_SQFT:
        return None
    parcel_id = str(attrs.get(F["parcel_id"]) or "").strip()
    if not parcel_id:
        return None
    return {
        "org_id": ORG_ID,
        "county_parcel_id": parcel_id,
        "county": "Summit",
        "situs_address": attrs.get(F["addr"]),
        "situs_city": attrs.get(F["city"]),
        "situs_zip": attrs.get(F["zip"]),
        "sqft": int(sqft),
        "market_value": float(attrs.get(F["value"]) or 0) or None,
        "last_sale_date": sale.isoformat() if sale else None,
        "last_sale_price": float(attrs.get(F["sale_price"]) or 0) or None,
        "years_owned": yrs,
        "owner1_raw": attrs.get(F["owner"]),
        "owner2_raw": attrs.get(F["owner2"]) if F["owner2"] else None,
        "source": "arcgis",
        "source_payload": attrs,
        "refreshed_at": datetime.utcnow().isoformat() + "Z",
    }


def load_seed_rows() -> list[dict]:
    if not SEED_PATH.exists():
        return []
    with SEED_PATH.open() as f:
        records = json.load(f)
    rows: list[dict] = []
    for rec in records:
        rec = dict(rec)
        rec["org_id"] = ORG_ID
        rec.setdefault("source", "seed")
        rec.setdefault("refreshed_at", datetime.utcnow().isoformat() + "Z")
        rows.append(rec)
    return rows


def upsert_parcels(rows: list[dict]) -> list[dict]:
    if not rows:
        return []
    r = supabase_request(
        "POST",
        "/parcels?on_conflict=org_id,county_parcel_id",
        headers={"Prefer": "resolution=merge-duplicates,return=representation"},
        data=json.dumps(rows),
    )
    return r.json()


def upsert_households(parcels: list[dict]) -> None:
    if not parcels:
        return
    households: list[dict] = []
    owners: list[dict] = []
    for p in parcels:
        primary = normalize_owner(p.get("owner1_raw"))
        secondary = normalize_owner(p.get("owner2_raw"))
        owner_names = [n for n in [primary["raw"], secondary["raw"]] if n]
        display = primary["raw"] or "Unknown"
        households.append({
            "org_id": ORG_ID,
            "parcel_id": p["id"],
            "display_name": display,
            "surname_key": primary["last"] or "",
            "owner_names": owner_names,
            "target_zip": (p.get("situs_zip") in TARGET_ZIPS),
            "owned_15_plus": (p.get("years_owned") or 0) >= MIN_YEARS_OWNED,
            "top_quartile_value": False,
        })

    r = supabase_request(
        "POST",
        "/households?on_conflict=org_id,parcel_id",
        headers={"Prefer": "resolution=merge-duplicates,return=representation"},
        data=json.dumps(households),
    )
    inserted = r.json()
    by_parcel = {h["parcel_id"]: h["id"] for h in inserted}

    for p in parcels:
        primary = normalize_owner(p.get("owner1_raw"))
        secondary = normalize_owner(p.get("owner2_raw"))
        hh_id = by_parcel.get(p["id"])
        if not hh_id:
            continue
        if primary["raw"]:
            owners.append({
                "household_id": hh_id,
                "full_name_raw": primary["raw"],
                "full_name_norm": primary["norm"],
                "first_name": primary["first"],
                "last_name": primary["last"],
                "is_primary": True,
            })
        if secondary["raw"]:
            owners.append({
                "household_id": hh_id,
                "full_name_raw": secondary["raw"],
                "full_name_norm": secondary["norm"],
                "first_name": secondary["first"],
                "last_name": secondary["last"],
                "is_primary": False,
            })

    if owners:
        # Drop existing then re-insert (simpler than upsert since we don't have a stable PK on raw name).
        # Per RLS, service-role bypasses; we scope by household_ids.
        for hh_id in by_parcel.values():
            supabase_request("DELETE", f"/household_owners?household_id=eq.{hh_id}")
        supabase_request(
            "POST",
            "/household_owners",
            headers={"Prefer": "return=minimal"},
            data=json.dumps(owners),
        )


def call_rpc(name: str, payload: dict) -> Any:
    headers = {
        "apikey": SERVICE_KEY,
        "Authorization": f"Bearer {SERVICE_KEY}",
        "Content-Type": "application/json",
    }
    url = f"{SUPABASE_URL}/rest/v1/rpc/{name}"
    r = requests.post(url, headers=headers, data=json.dumps(payload), timeout=60)
    if not r.ok:
        sys.exit(f"rpc {name} -> {r.status_code}: {r.text[:500]}")
    return r.json() if r.text else None


def main() -> None:
    supabase_required()

    rows: list[dict] = []
    if SEED_FALLBACK or not FEATURE_SERVER:
        print(f"[seed] loading from {SEED_PATH}")
        rows = load_seed_rows()
    else:
        for zip_code in TARGET_ZIPS:
            print(f"[arcgis] querying {FEATURE_SERVER} for zip {zip_code}")
            for attrs in fetch_arcgis(zip_code):
                row = to_row(attrs)
                if row:
                    rows.append(row)
        print(f"[arcgis] {len(rows)} parcels passed filters")

    if not rows:
        print("no parcels to write")
        return

    upserted = upsert_parcels(rows)
    print(f"[supabase] upserted {len(upserted)} parcels")
    upsert_households(upserted)
    print(f"[supabase] households synced")

    n_matched = call_rpc("match_seniors", {"target_org": ORG_ID})
    print(f"[supabase] matched {n_matched} seniors via pg_trgm")
    call_rpc("recompute_scores", {"target_org": ORG_ID})
    print(f"[supabase] scores recomputed")


if __name__ == "__main__":
    main()
