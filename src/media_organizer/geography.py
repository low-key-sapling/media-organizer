from __future__ import annotations

import math
import re
import sqlite3
import unicodedata
from pathlib import Path

from .paths import resource


def slug(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")[:70] or "others"


def distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    a, b = math.radians(lat1), math.radians(lat2)
    dlat, dlon = b - a, math.radians(lon2 - lon1)
    h = math.sin(dlat / 2) ** 2 + math.cos(a) * math.cos(b) * math.sin(dlon / 2) ** 2
    return 6371.0088 * 2 * math.asin(min(1.0, math.sqrt(h)))


class Places:
    def __init__(self, path: Path | None = None):
        self.path = path or resource("data") / "places.sqlite3"

    @property
    def available(self) -> bool:
        return self.path.is_file()

    def connect(self):
        db = sqlite3.connect(f"{self.path.as_uri()}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def result(row, source: str, km: float | None = None) -> dict:
        return {"location": "_".join((row["country"], slug(row["region"]), slug(row["ascii_name"]))), "location_source": source, "city": row["name"], "distance_km": round(km, 1) if km is not None else None}

    def city(self, name: str, country: str = "", region: str = "") -> dict | None:
        if not self.available or not name:
            return None
        with self.connect() as db:
            rows = db.execute("SELECT c.* FROM cities c JOIN aliases a ON a.city_id=c.id WHERE a.alias=? COLLATE NOCASE LIMIT 100", (name.strip(),)).fetchall()
        if country:
            normalized = {"中国": "CN", "china": "CN", "united states": "US", "usa": "US"}.get(country.casefold(), country.upper())
            matched = [r for r in rows if r["country"] == normalized or r["country_name"].casefold() == country.casefold()]
            rows = matched
        if region and len(rows) > 1:
            matched = [r for r in rows if r["region"].casefold() == region.casefold() or r["admin_code"].casefold() == region.casefold()]
            if matched:
                rows = matched
        unique = {r["id"]: r for r in rows}
        if len(unique) == 1:
            return self.result(next(iter(unique.values())), "metadata")
        return None

    def nearest(self, latitude: float, longitude: float, max_km: float = 100) -> dict | None:
        if not self.available or not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            return None
        lat_span = max_km / 110.0
        max_lat = min(89.999, abs(latitude) + lat_span)
        lon_span = min(180.0, max_km / (110.0 * max(0.00001, math.cos(math.radians(max_lat)))))
        with self.connect() as db:
            intervals = [(max(-180, longitude - lon_span), min(180, longitude + lon_span))]
            if longitude - lon_span < -180:
                intervals.append((longitude - lon_span + 360, 180))
            if longitude + lon_span > 180:
                intervals.append((-180, longitude + lon_span - 360))
            has_rtree = db.execute("SELECT 1 FROM sqlite_master WHERE name='city_points'").fetchone()
            if has_rtree:
                conditions = " OR ".join("(p.max_lon>=? AND p.min_lon<=?)" for _ in intervals)
                bounds = [latitude - lat_span, latitude + lat_span, *[x for interval in intervals for x in interval]]
                rows = db.execute(f"SELECT c.* FROM city_points p JOIN cities c ON c.id=p.id WHERE p.max_lat>=? AND p.min_lat<=? AND ({conditions})", bounds)
            else:
                rows = db.execute("SELECT * FROM cities WHERE latitude BETWEEN ? AND ?", (latitude - lat_span, latitude + lat_span))
            best, best_km = None, max_km
            for row in rows:
                delta = abs((row["longitude"] - longitude + 180) % 360 - 180)
                if delta > lon_span:
                    continue
                km = distance(latitude, longitude, row["latitude"], row["longitude"])
                if km <= best_km:
                    best, best_km = row, km
        return self.result(best, "gps_approximate", best_km) if best is not None else None

    def info(self) -> dict:
        if not self.available:
            return {"available": False, "source": "GeoNames", "license": "CC BY 4.0"}
        with self.connect() as db:
            info = dict(db.execute("SELECT key,value FROM info"))
        return {**info, "available": True, "source": "GeoNames", "license": "CC BY 4.0"}
