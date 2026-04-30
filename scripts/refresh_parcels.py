#!/usr/bin/env python3
"""Refresh parcel data from the Summit County tax-parcels FeatureServer (or any
ArcGIS REST FeatureServer of the same shape).

Defaults to the official Summit County Fiscal Office endpoint:
    https://scgis.summitoh.net/hosted/rest/services/parcels_web_GEODATA_Tax_Parcels/FeatureServer/0

Override with FEATURE_SERVER env var. Field names map to Summit's CAMA schema
by default; override individual *_FIELD env vars for other counties.

Note: the public REST layer exposes ownernme1, siteaddress, resflrarea,
cntmarval, resyrblt — but NOT sale dates. Until the propertyaccess
enrichment scraper is wired, years_owned is null and the scorer falls
back to year_built as the tenure proxy. No synthetic data, ever.
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

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
ORG_ID = os.environ.get("ORG_ID", "")
FEATURE_SERVER = (os.environ.get("FEATURE_SERVER") or
    "https://scgis.summitoh.net/hosted/rest/services/parcels_web_GEODATA_Tax_Parcels/FeatureServer/0"
).rstrip("/")
TARGET_ZIPS = [z.strip() for z in os.environ.get("TARGET_ZIPS", "44236").split(",") if z.strip()]
TARGET_CITY = os.environ.get("TARGET_CITY", "HUDSON").upper()
MIN_SQFT = int(os.environ.get("MIN_SQFT", "2800"))

# Summit County field names by default.
F = {
    "zip":         os.environ.get("ZIP_FIELD", "pstlzip5"),
    "city":        os.environ.get("CITY_FIELD", "pstlcity"),
    "addr":        os.environ.get("ADDR_FIELD", "siteaddress"),
    "owner":       os.environ.get("OWNER_FIELD", "ownernme1"),
    "owner2":      os.environ.get("OWNER2_FIELD", "ownernme2"),
    "sqft":        os.environ.get("SQFT_FIELD", "resflrarea"),
    "value":       os.environ.get("VALUE_FIELD", "cntmarval"),
    "sale_date":   os.environ.get("SALE_DATE_FIELD", "lglstartdt"),
    "year_built":  os.environ.get("YEAR_BUILT_FIELD", "resyrblt"),
    "class":       os.environ.get("CLASS_FIELD", "classdscrp"),
    "parcel_id":   os.environ.get("PARCEL_ID_FIELD", "parcelid"),
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


def polygon_centroid(geom: dict) -> tuple[float | None, float | None]:
    """Compute centroid from a polygon's outer ring (good enough for a heatmap)."""
    if not geom:
        return None, None
    rings = geom.get("rings") or []
    if not rings:
        return None, None
    ring = rings[0]
    if not ring:
        return None, None
    xs = [pt[0] for pt in ring]
    ys = [pt[1] for pt in ring]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def fetch_arcgis(zip_code: str) -> Iterable[dict]:
    out_fields = [v for v in [
        F["parcel_id"], F["addr"], F["city"], F["zip"], F["owner"], F["owner2"],
        F["sqft"], F["value"], F["sale_date"], F["year_built"], F["class"],
    ] if v]

    where = (
        f"{F['zip']}='{zip_code}' AND "
        f"{F['city']} LIKE '{TARGET_CITY}%' AND "
        f"{F['sqft']}>={MIN_SQFT}"
    )
    offset = 0
    page = 1000
    while True:
        params = {
            "where": where,
            "outFields": ",".join(out_fields),
            "returnGeometry": "true",
            "outSR": 4326,
            "f": "json",
            "resultOffset": offset,
            "resultRecordCount": page,
        }
        r = requests.get(f"{FEATURE_SERVER}/query", params=params, timeout=60)
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            sys.exit(f"arcgis error: {data['error']}")
        feats = data.get("features", []) or []
        if not feats:
            return
        for feat in feats:
            attrs = dict(feat.get("attributes", {}))
            lng, lat = polygon_centroid(feat.get("geometry"))
            attrs["__lat"] = lat
            attrs["__lng"] = lng
            yield attrs
        if not data.get("exceededTransferLimit") and len(feats) < page:
            return
        offset += len(feats)
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
    yrs = years_owned(sale)  # may be None — Summit's public layer doesn't carry sale date
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
        "situs_address": (attrs.get(F["addr"]) or "").strip() or None,
        "situs_city": (attrs.get(F["city"]) or "").title() or None,
        "situs_zip": str(attrs.get(F["zip"]) or "")[:5] or None,
        "sqft": int(sqft),
        "market_value": float(attrs.get(F["value"]) or 0) or None,
        "last_sale_date": sale.isoformat() if sale else None,
        "years_owned": yrs,
        "owner1_raw": attrs.get(F["owner"]),
        "owner2_raw": attrs.get(F["owner2"]),
        "year_built": attrs.get(F["year_built"]) or None,
        "property_class": attrs.get(F["class"]) or None,
        "lat": attrs.get("__lat"),
        "lng": attrs.get("__lng"),
        "source": "arcgis",
        "source_payload": {k: v for k, v in attrs.items() if not k.startswith("__")},
        "refreshed_at": datetime.utcnow().isoformat() + "Z",
    }


def upsert_parcels(rows: list[dict]) -> list[dict]:
    if not rows:
        return []
    BATCH = 500
    out: list[dict] = []
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        r = supabase_request(
            "POST",
            "/parcels?on_conflict=org_id,county_parcel_id",
            headers={"Prefer": "resolution=merge-duplicates,return=representation"},
            data=json.dumps(batch),
        )
        out.extend(r.json())
    return out


def upsert_households(parcels: list[dict]) -> None:
    if not parcels:
        return
    BATCH = 500
    households: list[dict] = []
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
            "owned_15_plus": (p.get("years_owned") or 0) >= 15,
        })

    inserted: list[dict] = []
    for i in range(0, len(households), BATCH):
        batch = households[i:i + BATCH]
        r = supabase_request(
            "POST",
            "/households?on_conflict=org_id,parcel_id",
            headers={"Prefer": "resolution=merge-duplicates,return=representation"},
            data=json.dumps(batch),
        )
        inserted.extend(r.json())
    by_parcel = {h["parcel_id"]: h["id"] for h in inserted}

    owners: list[dict] = []
    parcel_hh_ids = set()
    for p in parcels:
        primary = normalize_owner(p.get("owner1_raw"))
        secondary = normalize_owner(p.get("owner2_raw"))
        hh_id = by_parcel.get(p["id"])
        if not hh_id:
            continue
        parcel_hh_ids.add(hh_id)
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
        # Wipe owners for these households then re-insert. Service-role bypasses RLS.
        ids_csv = ",".join(parcel_hh_ids)
        supabase_request("DELETE", f"/household_owners?household_id=in.({ids_csv})")
        for i in range(0, len(owners), BATCH):
            supabase_request(
                "POST",
                "/household_owners",
                headers={"Prefer": "return=minimal"},
                data=json.dumps(owners[i:i + BATCH]),
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
    for zip_code in TARGET_ZIPS:
        print(f"[arcgis] {FEATURE_SERVER} where zip={zip_code} city LIKE {TARGET_CITY}%")
        count = 0
        for attrs in fetch_arcgis(zip_code):
            row = to_row(attrs)
            if row:
                rows.append(row)
                count += 1
        print(f"[arcgis] {count} parcels passed filters for {zip_code}")

    if not rows:
        sys.exit("no parcels returned — refusing to write empty dataset; check FEATURE_SERVER + filters")

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
