import os
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from media_organizer.geography import Places
from media_organizer.metadata import classify, filename_date


@pytest.fixture
def places(tmp_path):
    path = tmp_path / "places.sqlite3"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE cities(id INTEGER PRIMARY KEY,name TEXT,ascii_name TEXT,country TEXT,country_name TEXT,admin_code TEXT,region TEXT,latitude REAL,longitude REAL,population INTEGER);
        CREATE TABLE aliases(alias TEXT COLLATE NOCASE,city_id INTEGER);
        CREATE TABLE info(key TEXT,value TEXT);
        INSERT INTO cities VALUES(1,'杭州','Hangzhou','CN','China','02','Zhejiang',30.29,120.16,1000000);
        INSERT INTO aliases VALUES('杭州',1),('Hangzhou',1);
        INSERT INTO cities VALUES(2,'Dateline','Dateline','FJ','Fiji','01','Eastern',0,-179.9,1000);
        INSERT INTO info VALUES('dataset','fixture');
    """)
    db.commit()
    db.close()
    return Places(path)


def record(tmp_path, places, name, meta=None):
    path = tmp_path / name
    path.write_bytes(b"sample")
    stamp = datetime(2025, 1, 15).timestamp()
    os.utime(path, (stamp, stamp))
    return classify(name, path.stat(), meta or {}, places, {})


def test_photo_evidence_and_image_fallback(tmp_path, places):
    photo = record(tmp_path, places, "photo.jpg", {"IFD0:Make": "Camera", "ExifIFD:DateTimeOriginal": "2026:11:25 00:01:00"})
    assert photo["category"] == "photos"
    assert photo["month"] == "202611"
    assert record(tmp_path, places, "screenshot.png")["category"] == "images"
    assert record(tmp_path, places, "camera.dng")["category"] == "photos"
    assert record(tmp_path, places, "x.unknown")["category"] == "others"


def test_dates_preserve_local_month_and_priority(tmp_path, places):
    result = record(tmp_path, places, "20250101.jpg", {"DateTimeOriginal": "2026-11-01T00:01:00+14:00"})
    assert result["month"] == "202611"
    assert result["date_source"].startswith("metadata:")
    assert record(tmp_path, places, "IMG_20261125_102030.jpg")["month"] == "202611"
    assert record(tmp_path, places, "unknown.txt")["month"] == "202501"
    assert record(tmp_path, places, "audio.mp3", {"Year": "1999"})["month"] == "202501"
    assert record(tmp_path, places, "video.mp4", {"CreateDate": "1904:01:01 00:00:00"})["month"] == "202501"
    assert record(tmp_path, places, "historic.jpg", {"DateTimeOriginal": "1904:01:01 00:00:00"})["month"] == "190401"
    assert record(tmp_path, places, "early.jpg", {"DateTimeOriginal": "0001:01:01 00:00:00"})["month"] == "000101"


def test_ambiguous_and_invalid_filename_dates():
    assert filename_date("20260230.jpg")[0] is None
    assert filename_date("20260101_20261125.jpg")[1] is True
    assert filename_date("a2026-11-25.jpg")[0].month == 11


def test_offline_city_and_gps(tmp_path, places):
    explicit = record(tmp_path, places, "photo.jpg", {"City": "杭州", "CountryCode": "CN"})
    assert explicit["location"] == "CN_Zhejiang_Hangzhou"
    assert explicit["location_source"] == "metadata"
    gps = record(tmp_path, places, "a.jpg", {"GPSLatitude": 30.28, "GPSLongitude": 120.15})
    assert gps["location"] == "CN_Zhejiang_Hangzhou"
    assert gps["location_source"] == "gps_approximate"
    assert places.nearest(0, 179.9)["city"] == "Dateline"
    assert places.nearest(-60, 0) is None


def test_text_and_gps_conflict_is_visible(tmp_path, places):
    result = record(tmp_path, places, "photo.jpg", {"City": "杭州", "CountryCode": "CN", "GPSLatitude": 0, "GPSLongitude": -179.9})
    assert result["location"] == "CN_Zhejiang_Hangzhou"
    assert any("不同" in warning for warning in result["warnings"])


def test_created_location_is_used_and_shown_location_is_not_inferred(tmp_path, places):
    result = record(tmp_path, places, "created.jpg", {"LocationCreatedCity": "杭州", "LocationCreatedCountryCode": "CN", "LocationShownCity": "Other"})
    assert result["location"] == "CN_Zhejiang_Hangzhou"
    shown = record(tmp_path, places, "shown.jpg", {"LocationShownCity": "杭州"})
    assert shown["location"] == "others"
