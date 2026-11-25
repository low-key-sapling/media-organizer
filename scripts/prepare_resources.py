"""Build-time only: fetch official resources. The shipped application never downloads."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import sys
import tarfile
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".cache" / "resources"
DATA = ROOT / "resources" / "data"
VENDOR = ROOT / "resources" / "vendor"
MANIFEST = {}


def fetch(url: str, refresh: bool = False) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    destination = CACHE / Path(urllib.parse.urlparse(url).path).name
    if refresh or not destination.is_file():
        print(f"Downloading {url}", flush=True)
        for attempt in range(3):
            try:
                request = urllib.request.Request(url, headers={"User-Agent": "MediaOrganizerBuild/0.1 (+https://exiftool.org/)"})
                with urllib.request.urlopen(request, timeout=90) as response, destination.with_suffix(destination.suffix + ".part").open("wb") as output:
                    shutil.copyfileobj(response, output)
                os.replace(destination.with_suffix(destination.suffix + ".part"), destination)
                break
            except OSError:
                if attempt == 2:
                    raise
                time.sleep(2)
    with destination.open("rb") as downloaded:
        MANIFEST[destination.name] = {"url": url, "sha256": hashlib.file_digest(downloaded, "sha256").hexdigest()}
    return destination


def geodata(refresh: bool = False):
    DATA.mkdir(parents=True, exist_ok=True)
    output = DATA / "places.sqlite3"
    if output.is_file() and not refresh:
        print(f"Using {output}", flush=True)
        return
    archive = fetch("https://download.geonames.org/export/dump/cities500.zip", refresh)
    admins = fetch("https://download.geonames.org/export/dump/admin1CodesASCII.txt", refresh)
    countries = fetch("https://download.geonames.org/export/dump/countryInfo.txt", refresh)
    regions = {}
    for row in csv.reader(admins.read_text(encoding="utf-8").splitlines(), delimiter="\t"):
        if len(row) >= 3:
            regions[row[0]] = row[2] or row[1]
    country_names = {}
    for row in csv.reader((line for line in countries.read_text(encoding="utf-8").splitlines() if not line.startswith("#")), delimiter="\t"):
        if len(row) > 4:
            country_names[row[0]] = row[4]
    temporary = output.with_suffix(".building.sqlite3")
    temporary.unlink(missing_ok=True)
    db = sqlite3.connect(temporary)
    db.executescript("""
        PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
        CREATE TABLE cities(id INTEGER PRIMARY KEY,name TEXT,ascii_name TEXT,country TEXT,country_name TEXT,admin_code TEXT,region TEXT,latitude REAL,longitude REAL,population INTEGER);
        CREATE TABLE aliases(alias TEXT COLLATE NOCASE,city_id INTEGER,PRIMARY KEY(alias,city_id)) WITHOUT ROWID;
        CREATE TABLE info(key TEXT PRIMARY KEY,value TEXT);
    """)
    count = 0
    with zipfile.ZipFile(archive) as zipped:
        with io.TextIOWrapper(zipped.open("cities500.txt"), encoding="utf-8") as source:
            for row in csv.reader(source, delimiter="\t", quoting=csv.QUOTE_NONE):
                if len(row) < 19:
                    continue
                city_id, name, ascii_name, alternatives = int(row[0]), row[1], row[2], row[3]
                country, admin = row[8], row[10]
                db.execute("INSERT INTO cities VALUES(?,?,?,?,?,?,?,?,?,?)", (city_id, name, ascii_name, country, country_names.get(country, country), admin, regions.get(country + "." + admin, admin or "others"), float(row[4]), float(row[5]), int(row[14] or 0)))
                aliases = {name, ascii_name, *alternatives.split(",")}
                db.executemany("INSERT OR IGNORE INTO aliases VALUES(?,?)", [(value, city_id) for value in aliases if value])
                count += 1
                if count % 20000 == 0:
                    db.commit()
                    print(f"Indexed {count:,} cities", flush=True)
    db.execute("CREATE INDEX latitude_idx ON cities(latitude)")
    db.execute("CREATE VIRTUAL TABLE city_points USING rtree(id,min_lat,max_lat,min_lon,max_lon)")
    db.execute("INSERT INTO city_points SELECT id,latitude,latitude,longitude,longitude FROM cities")
    info = {"dataset": "cities500", "city_count": str(count), "built_at": datetime.now(timezone.utc).isoformat(), "license": "CC BY 4.0", "source": "https://www.geonames.org/", "modified": "Selected fields, indexed aliases and SQLite conversion"}
    db.executemany("INSERT INTO info VALUES(?,?)", info.items())
    db.commit()
    db.close()
    os.replace(temporary, output)
    print(f"City database ready: {count:,} cities", flush=True)


def extract_zip(archive: Path, destination: Path):
    with zipfile.ZipFile(archive) as zipped:
        for item in zipped.infolist():
            path = (destination / item.filename).resolve()
            if not path.is_relative_to(destination.resolve()):
                raise RuntimeError("Unsafe archive path")
            if (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise RuntimeError("Archive symlinks are not accepted")
        zipped.extractall(destination)


def exiftool(platform: str, refresh: bool = False):
    destination = VENDOR / "exiftool"
    executable = destination / ("exiftool.exe" if platform == "windows" else "exiftool")
    if executable.is_file() and not refresh:
        print(f"Using {executable}", flush=True)
        return
    homepage = fetch("https://exiftool.org/index.html", refresh).read_text(encoding="utf-8")
    pattern = r'href=[\"\']([^\"\']*exiftool-[\d.]+_64\.zip)' if platform == "windows" else r'href=[\"\']([^\"\']*Image-ExifTool-[\d.]+\.tar\.gz)'
    match = re.search(pattern, homepage, re.IGNORECASE)
    if not match:
        raise RuntimeError("Official ExifTool download link not found; inspect https://exiftool.org/")
    url = urllib.parse.urljoin("https://exiftool.org/", match.group(1))
    if platform == "windows" and "sourceforge.net" in urllib.parse.urlparse(url).netloc:
        # Official Windows packager mirror; avoids redirecting to unavailable SF mirrors.
        url = "https://oliverbetz.de/cms/files/Artikel/ExifTool-for-Windows/" + Path(urllib.parse.urlparse(url).path).name
    archive = fetch(url, refresh)
    unpacked = CACHE / ("exiftool-" + platform)
    unpacked.mkdir(parents=True, exist_ok=True)
    if platform == "windows":
        extract_zip(archive, unpacked)
        source = next(unpacked.rglob("exiftool(-k).exe"), None) or next(unpacked.rglob("exiftool.exe"), None)
        if source is None:
            raise RuntimeError("ExifTool executable not present in official archive")
        shutil.copytree(source.parent, destination, dirs_exist_ok=True)
        copied = destination / source.name
        if copied.name != "exiftool.exe":
            copied.replace(destination / "exiftool.exe")
    else:
        with tarfile.open(archive) as tar:
            tar.extractall(unpacked, filter="data")
        source = next(unpacked.rglob("exiftool"))
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, executable)
        shutil.copytree(source.parent / "lib", destination / "lib", dirs_exist_ok=True)
        for license_name in ("README", "Changes"):
            if (source.parent / license_name).is_file():
                shutil.copy2(source.parent / license_name, destination / license_name)
        executable.chmod(0o755)
    print(f"ExifTool ready: {executable}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", choices=("windows", "linux"), default="windows" if os.name == "nt" else "linux")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--only", choices=("all", "geo", "exiftool"), default="all")
    args = parser.parse_args()
    if args.only in ("all", "exiftool"):
        exiftool(args.platform, args.refresh)
    if args.only in ("all", "geo"):
        geodata(args.refresh)
    DATA.mkdir(parents=True, exist_ok=True)
    manifest_path = DATA / "resources.json"
    previous = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    manifest_path.write_text(json.dumps({**previous, **MANIFEST}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
