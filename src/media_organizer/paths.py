from __future__ import annotations

import os
import re
import sys
from pathlib import Path

CATEGORIES = ("photos", "images", "videos", "audio", "documents", "archives", "others")
EXTENSION_CATEGORIES = ("documents", "archives")
LEVELS = ("quick", "sampled", "full")


def resource(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return base / name if getattr(sys, "frozen", False) else base / "resources" / name


def web_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "web"
    return Path(__file__).resolve().parents[2] / "frontend" / "dist"


def state_directory() -> Path:
    if os.environ.get("MEDIA_ORGANIZER_HOME"):
        return Path(os.environ["MEDIA_ORGANIZER_HOME"]).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "user-data"
    return Path(__file__).resolve().parents[2] / ".local"


def is_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def validate_roots(source: str, target: str) -> tuple[Path, Path]:
    raw_source, raw_target = Path(source).expanduser(), Path(target).expanduser()
    if is_link(raw_source) or is_link(raw_target):
        raise ValueError("源目录和目标目录不能是符号链接或目录联接")
    src, dst = raw_source.resolve(), raw_target.resolve()
    if not src.is_dir():
        raise ValueError("源目录不存在或不是目录")
    if dst.exists() and not dst.is_dir():
        raise ValueError("目标路径不是目录")
    if src == dst or src.is_relative_to(dst) or dst.is_relative_to(src):
        raise ValueError("源目录和目标目录必须相互独立，不能相同或互相包含")
    return src, dst


def safe_name(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).rstrip(" .") or "unnamed"
    stem, suffix = os.path.splitext(cleaned)
    if stem.upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}:
        stem = "_" + stem
    return stem[:160] + suffix[:24]


def contained(root: Path, relative: str) -> Path:
    parts = relative.replace("\\", "/").split("/")
    if not parts or any(p in ("", ".", "..") for p in parts) or Path(relative).is_absolute():
        raise ValueError("无效的相对路径")
    candidate = root.joinpath(*parts)
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise ValueError("目标路径通过链接指向了目标目录之外")
    cursor = root
    for part in parts:
        cursor = cursor / part
        if is_link(cursor):
            raise ValueError("目标路径包含符号链接或目录联接")
    return candidate


def validate_override(category: str, month: str, location: str) -> None:
    if category not in CATEGORIES:
        raise ValueError("无效的文件类型")
    if not re.fullmatch(r"\d{4}(0[1-9]|1[0-2])", month) or int(month[:4]) == 0:
        raise ValueError("月份格式必须为 YYYYMM")
    if category not in EXTENSION_CATEGORIES and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,159}", location):
        raise ValueError("地点需使用英文字母、数字、下划线或短横线")


def relative_target(entry: dict) -> str:
    source = Path(entry["source_rel"])
    category = entry["category"]
    folder = entry["location"]
    if category in EXTENSION_CATEGORIES:
        extension = source.suffix.removeprefix(".").lower()
        if (
            category == "archives"
            and extension in ("gz", "bz2", "xz", "zst")
            and source.name.lower().endswith(f".tar.{extension}")
        ):
            extension = f"tar.{extension}"
        folder = safe_name(extension) if extension else "others"
    return "/".join((category, entry["month"], folder, safe_name(source.name)))
