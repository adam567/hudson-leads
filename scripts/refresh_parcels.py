#!/usr/bin/env python3
"""Refresh Hudson parcel data — real public records only.

Two sources joined per parcel:
  1. ArcGIS REST FeatureServer at scgis.summitoh.net — owner, address,
     sqft, market value, year_built, geometry.
  2. SC706_SALES.zip from fiscaloffice.summitoh.net's bulk-data downloads
     — every recorded transfer in Summit County (~977K rows). Joined to
     parcels by parcel id; we keep the most recent dated sale per parcel.

Filters applied:
  - In Hudson postal area (pstlcity LIKE 'HUDSON%' AND pstlzip5='44236')
  - Residential floor area >= MIN_SQFT
  - Owner-occupied: normalized siteaddress matches normalized pstladdress
    (drops absentee landlords, LLC-held rentals, and out-of-state owners)

No senior data, no manual confirmation, no synthetic anything.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import sys
import time
import zipfile
from datetime import date, datetime
from typing import Any, Iterable

import requests

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
ORG_ID = os.environ.get("ORG_ID", "")
FEATURE_SERVER = (os.environ.get("FEATURE_SERVER") or
    "https://scgis.summitoh.net/hosted/rest/services/parcels_web_GEODATA_Tax_Parcels/FeatureServer/0"
).rstrip("/")
SALES_ZIP_URL = (os.environ.get("SALES_ZIP_URL") or
    "https://fiscaloffice.summitoh.net/index.php/documents-a-forms/finish/10-cama/237-sc706sales"
)
TARGET_ZIPS = [z.strip() for z in os.environ.get("TARGET_ZIPS", "44236").split(",") if z.strip()]
TARGET_CITY = os.environ.get("TARGET_CITY", "HUDSON").upper()
MIN_SQFT = int(os.environ.get("MIN_SQFT", "2800"))
ALLOW_ABSENTEE = os.environ.get("ALLOW_ABSENTEE", "").lower() in ("1", "true", "yes")

F = {
    "zip":         os.environ.get("ZIP_FIELD", "pstlzip5"),
    "city":        os.environ.get("CITY_FIELD", "pstlcity"),
    "addr":        os.environ.get("ADDR_FIELD", "siteaddress"),
    "mail":        os.environ.get("MAIL_FIELD", "pstladdress"),
    "owner":       os.environ.get("OWNER_FIELD", "ownernme1"),
    "owner2":      os.environ.get("OWNER2_FIELD", "ownernme2"),
    "sqft":        os.environ.get("SQFT_FIELD", "resflrarea"),
    "value":       os.environ.get("VALUE_FIELD", "cntmarval"),
    "year_built":  os.environ.get("YEAR_BUILT_FIELD", "resyrblt"),
    "class":       os.environ.get("CLASS_FIELD", "classdscrp"),
    "parcel_id":   os.environ.get("PARCEL_ID_FIELD", "parcelid"),
}

ADDR_NORMALIZE = re.compile(r"\s+")


def normalize_address(s: str | None) -> str:
    if not s:
        return ""
    return ADDR_NORMALIZE.sub(" ", s.strip().upper()).rstrip(", ")


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
    r = requests.request(method, url, headers=headers, timeout=120, **kwargs)
    if not r.ok:
        sys.exit(f"supabase {method} {path} -> {r.status_code}: {r.text[:500]}")
    return r


def polygon_centroid(geom: dict) -> tuple[float | None, float | None]:
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
        F["parcel_id"], F["addr"], F["city"], F["zip"], F["mail"],
        F["owner"], F["owner2"],
        F["sqft"], F["value"], F["year_built"], F["class"],
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


def fetch_sales_index() -> dict[str, dict]:
    """Return {parcel_id: {'sale_date': date, 'sale_price': int|None}}."""
    print(f"[sales] downloading {SALES_ZIP_URL}")
    sess = requests.Session()
    sess.headers["User-Agent"] = "Mozilla/5.0 (hudson-leads)"
    sess.get("https://fiscaloffice.summitoh.net/", timeout=30)
    r = sess.get(SALES_ZIP_URL, timeout=180)
    r.raise_for_status()
    print(f"[sales] {len(r.content)//1024} KB downloaded")
    z = zipfile.ZipFile(io.BytesIO(r.content))
    csv_name = next(n for n in z.namelist() if n.upper().endswith(".CSV"))
    latest: dict[str, dict] = {}
    rows = 0
    with z.open(csv_name) as raw:
        text = io.TextIOWrapper(raw, encoding="latin-1", newline="")
        for row in csv.DictReader(text):
            rows += 1
            pid = (row.get("PARCEL") or "").strip()
            date_s = (row.get("SALEDATE") or "").strip()
            if not pid or not date_s:
                continue
            try:
                dt = datetime.strptime(date_s, "%d-%b-%Y").date()
            except ValueError:
                continue
            existing = latest.get(pid)
            if not existing or dt > existing["sale_date"]:
                price_s = (row.get("PRICE") or "").strip()
                try:
                    price = int(price_s) if price_s else None
                except ValueError:
                    price = None
                latest[pid] = {"sale_date": dt, "sale_price": price}
    print(f"[sales] parsed {rows} transfer rows; {len(latest)} parcels with dated sales")
    return latest


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


def to_row(attrs: dict, sales: dict[str, dict]) -> dict | None:
    sqft = attrs.get(F["sqft"])
    if sqft is None or sqft < MIN_SQFT:
        return None
    parcel_id = str(attrs.get(F["parcel_id"]) or "").strip()
    if not parcel_id:
        return None

    site = normalize_address(attrs.get(F["addr"]))
    mail = normalize_address(attrs.get(F["mail"]))
    occupied = bool(site and mail and site == mail)
    if not occupied and not ALLOW_ABSENTEE:
        return None

    sale_info = sales.get(parcel_id) or {}
    sale_date: date | None = sale_info.get("sale_date")
    yrs = years_owned(sale_date)

    return {
        "org_id": ORG_ID,
        "county_parcel_id": parcel_id,
        "county": "Summit",
        "situs_address": (attrs.get(F["addr"]) or "").strip() or None,
        "situs_city": (attrs.get(F["city"]) or "").title() or None,
        "situs_zip": str(attrs.get(F["zip"]) or "")[:5] or None,
        "mailing_address": (attrs.get(F["mail"]) or "").strip() or None,
        "mailing_same_as_situs": occupied,
        "sqft": int(sqft),
        "market_value": float(attrs.get(F["value"]) or 0) or None,
        "last_sale_date": sale_date.isoformat() if sale_date else None,
        "last_sale_price": sale_info.get("sale_price"),
        "years_owned": yrs,
        "owner1_raw": attrs.get(F["owner"]),
        "owner2_raw": attrs.get(F["owner2"]),
        "year_built": attrs.get(F["year_built"]) or None,
        "property_class": attrs.get(F["class"]) or None,
        "lat": attrs.get("__lat"),
        "lng": attrs.get("__lng"),
        "source": "summit-fo",
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
        DEL_CHUNK = 100
        ids = list(parcel_hh_ids)
        for i in range(0, len(ids), DEL_CHUNK):
            ids_csv = ",".join(ids[i:i + DEL_CHUNK])
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


def delete_parcels_not_in(keep_ids: set[str]) -> int:
    """Drop households+parcels for this org that aren't in the new keep set
    (so absentee/sold/no-longer-matching properties leave the dashboard)."""
    r = supabase_request("GET", f"/parcels?org_id=eq.{ORG_ID}&select=id,county_parcel_id")
    existing = r.json()
    to_delete = [p["id"] for p in existing if p["county_parcel_id"] not in keep_ids]
    if not to_delete:
        return 0
    DEL_CHUNK = 50
    for i in range(0, len(to_delete), DEL_CHUNK):
        ids_csv = ",".join(to_delete[i:i + DEL_CHUNK])
        supabase_request("DELETE", f"/parcels?id=in.({ids_csv})")
    return len(to_delete)


def main() -> None:
    supabase_required()

    sales = fetch_sales_index()

    rows: list[dict] = []
    seen_parcel_ids: set[str] = set()
    skipped_absentee = 0
    for zip_code in TARGET_ZIPS:
        print(f"[arcgis] {FEATURE_SERVER} where zip={zip_code} city LIKE {TARGET_CITY}%")
        gross = 0
        for attrs in fetch_arcgis(zip_code):
            gross += 1
            pid = str(attrs.get(F["parcel_id"]) or "").strip()
            row = to_row(attrs, sales)
            if row is None:
                if pid:
                    site = normalize_address(attrs.get(F["addr"]))
                    mail = normalize_address(attrs.get(F["mail"]))
                    if site and mail and site != mail:
                        skipped_absentee += 1
                continue
            rows.append(row)
            seen_parcel_ids.add(pid)
        print(f"[arcgis] zip {zip_code}: {gross} returned, {len(rows)} kept, {skipped_absentee} skipped (absentee)")

    if not rows:
        sys.exit("no parcels passed filters; refusing to write empty dataset")

    sale_known = sum(1 for r in rows if r["last_sale_date"])
    print(f"[join] {sale_known}/{len(rows)} parcels have a known last-sale-date")

    n_dropped = delete_parcels_not_in(seen_parcel_ids)
    print(f"[supabase] dropped {n_dropped} parcels that no longer match filters")

    upserted = upsert_parcels(rows)
    print(f"[supabase] upserted {len(upserted)} parcels")
    upsert_households(upserted)
    print(f"[supabase] households synced")

    call_rpc("recompute_scores", {"target_org": ORG_ID})
    print(f"[supabase] scores recomputed")


if __name__ == "__main__":
    main()
