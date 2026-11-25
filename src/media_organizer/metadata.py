from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from .geography import Places
from .paths import CATEGORIES, relative_target, resource

EXTENSIONS = {
    "photos": "dng nef nrw cr2 cr3 arw srf sr2 orf rw2 pef raf raw rwl srw 3fr erf kdc mef mos mrw x3f",
    "images": "jpg jpeg jpe png webp gif avif heic heif bmp tif tiff svg ico jxl psd eps ai apng jp2",
    "videos": "mp4 mov m4v mkv webm avi wmv flv mpg mpeg mts m2ts ts 3gp vob ogv asf rm rmvb",
    "audio": "mp3 flac wav m4a aac ogg opus wma aif aiff alac amr ape mid midi dsf mka",
    "documents": "pdf txt md markdown csv tsv json xml yaml yml rtf doc docx xls xlsx ppt pptx odt ods odp epub mobi azw azw3 html htm log ini toml",
    "archives": "zip rar 7z tar gz bz2 xz zst tgz tbz2 txz iso cab",
}
DEFAULT_MAPPING = {ext: category for category, extensions in EXTENSIONS.items() for ext in extensions.split()}


def exiftool_path() -> Path | None:
    candidates = [resource("vendor") / "exiftool" / ("exiftool.exe" if os.name == "nt" else "exiftool"), resource("vendor") / "exiftool" / "exiftool"]
    for path in candidates:
        if path.is_file():
            return path
    found = shutil.which("exiftool")
    return Path(found) if found else None


class MetadataReader:
    TAGS = ("DateTimeOriginal", "CreateDate", "CreationDate", "RecordingTime", "OffsetTimeOriginal", "GPSLatitude", "GPSLongitude", "GPSLatitudeRef", "GPSLongitudeRef", "Make", "Model", "LensModel", "ExposureTime", "FNumber", "ISO", "FileSource", "SceneType", "City", "Province-State", "Country", "CountryCode", "Country-PrimaryLocationName")

    def __init__(self):
        self.executable = exiftool_path()

    def read_batch(self, paths: list[Path]) -> dict[str, dict]:
        results: dict[str, dict] = {}
        if self.executable and paths:
            command = [str(self.executable), "-config", "", "-json", "-G1", "-n", "-charset", "filename=UTF8", "-api", "QuickTimeUTC=0", *["-" + tag for tag in self.TAGS], *map(str, paths)]
            env = os.environ.copy()
            for variable in ("PERL5LIB", "PERL5OPT", "PERLLIB", "PERL5DB"):
                env.pop(variable, None)
            if os.name == "nt":
                env.update(LC_ALL="C", LC_CTYPE="C", LANG="C")
            perl = resource("vendor") / "perl" / "bin" / "perl"
            if os.name != "nt" and perl.is_file():
                command.insert(0, str(perl))
                env["LD_LIBRARY_PATH"] = str(resource("vendor") / "perl" / "lib" / "runtime") + os.pathsep + env.get("LD_LIBRARY_PATH", "")
            try:
                run = subprocess.run(command, capture_output=True, timeout=120, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), env=env)
                raw = json.loads(run.stdout.decode("utf-8", "replace"))
                for item in raw:
                    results[os.path.normcase(os.path.abspath(item["SourceFile"]))] = item
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass
        for path in paths:
            key = os.path.normcase(os.path.abspath(path))
            if key not in results:
                results[key] = self.pillow(path)
        return results

    @staticmethod
    def pillow(path: Path) -> dict:
        if path.suffix.lower().lstrip(".") not in EXTENSIONS["images"].split():
            return {}
        try:
            from PIL import Image, ExifTags
            with Image.open(path) as image:
                raw = image.getexif()
                metadata = {ExifTags.TAGS.get(k, str(k)): v for k, v in raw.items()}
                try:
                    metadata.update({ExifTags.TAGS.get(k, str(k)): v for k, v in raw.get_ifd(34665).items()})
                    gps = {ExifTags.GPSTAGS.get(k, str(k)): v for k, v in raw.get_ifd(34853).items()}
                    for key in ("GPSLatitude", "GPSLongitude"):
                        if key in gps:
                            d, m, s = map(float, gps[key])
                            metadata[key] = (d + m / 60 + s / 3600) * (-1 if gps.get(key + "Ref") in ("S", "W") else 1)
                except (KeyError, TypeError, ValueError, ZeroDivisionError):
                    pass
                return metadata
        except Exception:
            return {}


def tag(metadata: dict, name: str):
    for key, value in metadata.items():
        if key.split(":")[-1] == name and value not in (None, "", "0000:00:00 00:00:00"):
            return value
    return None


def parse_date(value) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    text = re.sub(r"^(\d{4}):(\d{2}):(\d{2})", r"\1-\2-\3", text)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.year == 1904 and parsed.month == parsed.day == 1:
            return None
        return parsed
    except ValueError:
        return None


def filename_date(name: str) -> tuple[datetime | None, bool]:
    found = set()
    for match in re.finditer(r"(?<!\d)((?:19|20)\d{2})[-_]?([01]\d)[-_]?([0-3]\d)(?!\d)", Path(name).stem):
        try:
            found.add(datetime(*map(int, match.groups())))
        except ValueError:
            pass
    return (next(iter(found)), False) if len(found) == 1 else (None, len(found) > 1)


def classify(source_rel: str, stat, metadata: dict, places: Places, settings: dict) -> dict:
    extension = Path(source_rel).suffix.lower().lstrip(".")
    mapping = {**DEFAULT_MAPPING, **settings.get("extension_mapping", {})}
    category = mapping.get(extension, "others")
    if category not in CATEGORIES:
        category = "others"
    warnings = []
    if tag(metadata, "Error"):
        warnings.append(f"媒体元数据无法完整读取：{tag(metadata, 'Error')}")
    if category == "images":
        camera = bool(tag(metadata, "Make") or tag(metadata, "Model") or tag(metadata, "LensModel"))
        exposure = sum(tag(metadata, k) is not None for k in ("ExposureTime", "FNumber", "ISO")) >= 2
        if camera or exposure or tag(metadata, "FileSource") == 3 or tag(metadata, "SceneType") == 1:
            category, type_source = "photos", "拍摄元数据线索"
        else:
            type_source = "图片格式，未发现充分拍摄线索"
    else:
        type_source = "文件扩展名规则"
    date = None
    date_source = "modified"
    for name in ("DateTimeOriginal", "RecordingTime", "CreationDate"):
        date = parse_date(tag(metadata, name))
        if date:
            date_source = f"metadata:{name}"
            break
    # Container creation times are not silently equated with recording times.
    if date is None:
        date, ambiguous = filename_date(source_rel)
        if date:
            date_source = "filename"
        if ambiguous:
            warnings.append("文件名存在多个日期，已回退到修改时间")
    if date is None:
        date = datetime.fromtimestamp(stat.st_mtime)
        if tag(metadata, "CreateDate"):
            warnings.append("存在创建或数字化时间，未将其直接当作拍摄/录制时间")
    if date.replace(tzinfo=None) > datetime.now() + timedelta(days=2):
        warnings.append("归档日期在未来，请检查")
    place = None
    city = str(tag(metadata, "City") or "")
    if city:
        place = places.city(city, str(tag(metadata, "CountryCode") or tag(metadata, "Country") or tag(metadata, "Country-PrimaryLocationName") or ""), str(tag(metadata, "Province-State") or ""))
        if not place:
            warnings.append("地点文字无法唯一匹配到城市")
    lat, lon = tag(metadata, "GPSLatitude"), tag(metadata, "GPSLongitude")
    gps_place = None
    if lat is not None and lon is not None:
        try:
            latitude, longitude = float(lat), float(lon)
            if str(tag(metadata, "GPSLatitudeRef")).upper() == "S":
                latitude = -abs(latitude)
            if str(tag(metadata, "GPSLongitudeRef")).upper() == "W":
                longitude = -abs(longitude)
            gps_place = places.nearest(latitude, longitude, settings.get("max_city_distance_km", 100))
        except (ValueError, TypeError):
            warnings.append("GPS 坐标无法读取")
    if place and gps_place and place["location"] != gps_place["location"]:
        warnings.append("地点文字与 GPS 推算结果不同，已优先采用地点文字")
    place = place or gps_place or {"location": "others", "location_source": "unknown", "city": "", "distance_km": None}
    if place["location_source"] == "gps_approximate":
        warnings.append("地点根据 GPS 近似匹配")
    if not places.available:
        warnings.append("离线城市库不可用，地点归类能力受限")
    result = {"source_rel": source_rel, "kind": "file", "status": "planned", "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "ctime_ns": stat.st_ctime_ns, "mode": stat.st_mode, "category": category, "month": date.strftime("%Y%m"), "date_source": date_source, "original_date": date.isoformat(), "type_source": type_source, **place, "warnings": warnings, "error": "", "source_hash": "", "target_hash": "", "content_verified": False, "manual": False, "extra_issues": []}
    result["target_rel"] = relative_target(result)
    result.update(birthtime_ns=getattr(stat, "st_birthtime_ns", None), uid=getattr(stat, "st_uid", None), gid=getattr(stat, "st_gid", None))
    return result
