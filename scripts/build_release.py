from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*args, cwd=ROOT):
    print("Build:", " ".join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), cwd=cwd, check=True)


def main():
    parser = argparse.ArgumentParser(description="Build a self-contained offline release for the current OS")
    parser.add_argument("--skip-frontend", action="store_true")
    parser.add_argument("--skip-resources", action="store_true")
    args = parser.parse_args()
    platform = "windows" if os.name == "nt" else "linux"
    if not args.skip_resources:
        run(sys.executable, ROOT / "scripts/prepare_resources.py", "--platform", platform)
    if platform == "linux" and not (ROOT / "resources/vendor/perl/bin/perl").is_file():
        raise SystemExit("Run bash scripts/build_perl.sh on the Linux build machine first. Never require Perl from the end user.")
    if not args.skip_frontend:
        npm = "npm.cmd" if os.name == "nt" else "npm"
        run(npm, "ci", "--legacy-peer-deps", cwd=ROOT / "frontend")
        run(npm, "run", "build", cwd=ROOT / "frontend")
    for required in ("frontend/dist/index.html", "resources/data/places.sqlite3", "resources/vendor/exiftool/" + ("exiftool.exe" if platform == "windows" else "exiftool")):
        if not (ROOT / required).is_file():
            raise SystemExit(f"Missing offline resource: {required}")
    vendor_stage = ROOT / ".cache" / ("vendor-stage-" + uuid.uuid4().hex[:8])
    shutil.copytree(ROOT / "resources/vendor", vendor_stage, ignore=shutil.ignore_patterns("t", "__pycache__"))
    if (ROOT / "dist/media-organizer/user-data").exists():
        raise SystemExit("dist contains runtime user data; move it to a safe location before rebuilding")
    separator = os.pathsep
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--name", "media-organizer", "--paths", str(ROOT / "src"), "--collect-all", "uvicorn", "--collect-all", "fastapi", "--collect-all", "starlette", "--collect-all", "PIL", "--hidden-import", "media_organizer.api", "--add-data", f"{ROOT / 'frontend/dist'}{separator}web", "--add-data", f"{ROOT / 'resources/data'}{separator}data", "--add-data", f"{vendor_stage}{separator}vendor", str(ROOT / "launcher.py")]
    run(*command)
    name = f"media-organizer-0.1.0-{platform}-x64"
    release_root = ROOT / "release"
    release_root.mkdir(exist_ok=True)
    destination = release_root / name
    number = 2
    while destination.exists():
        destination = release_root / f"{name}-{number}"
        number += 1
    shutil.copytree(ROOT / "dist/media-organizer", destination)
    for item in ("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(ROOT / item, destination / item)
    shutil.copytree(ROOT / "docs", destination / "docs")
    from collect_licenses import collect
    collect(destination, ROOT)
    if platform == "linux":
        for executable in (destination / "media-organizer", destination / "_internal/vendor/perl/bin/perl", destination / "_internal/vendor/exiftool/exiftool"):
            executable.chmod(0o755)
    executable = destination / ("media-organizer.exe" if platform == "windows" else "media-organizer")
    run(sys.executable, ROOT / "scripts/smoke_release.py", executable)
    archive = shutil.make_archive(str(destination), "zip" if platform == "windows" else "gztar", root_dir=release_root, base_dir=destination.name)
    print(f"OFFLINE RELEASE READY: {archive}", flush=True)


if __name__ == "__main__":
    main()
