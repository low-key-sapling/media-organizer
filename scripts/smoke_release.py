"""Exercise a frozen build with developer runtime paths removed from the environment."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import io
from pathlib import Path


def main():
    executable = Path(sys.argv[1]).resolve()
    env = {key: value for key, value in os.environ.items() if not key.startswith(("PYTHON", "PERL", "NODE", "VIRTUAL_ENV", "CONDA"))}
    env["PATH"] = str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32") if os.name == "nt" else "/usr/bin:/bin"
    env["HTTP_PROXY"] = env["HTTPS_PROXY"] = env["ALL_PROXY"] = "http://127.0.0.1:1"
    with tempfile.TemporaryDirectory(prefix="media-organizer-offline-smoke-") as temporary:
        root = Path(temporary)
        source, target = root / "待整理 照片", root / "整理结果 库"
        source.mkdir()
        originals = {"旅途 20261125.txt": b"Hello, offline world!", ".hidden.bin": bytes(range(256)), "zero.dat": b""}
        from PIL import Image
        image = Image.new("RGB", (16, 16), (113, 145, 92))
        exif = Image.Exif()
        exif[271], exif[272], exif[36867] = "Smoke Camera", "Offline Test", "2026:11:25 12:00:00"
        from PIL.TiffImagePlugin import IFDRational
        exif[34853] = {1: "N", 2: (IFDRational(30), IFDRational(17), IFDRational(24)), 3: "E", 4: (IFDRational(120), IFDRational(9), IFDRational(36))}
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", exif=exif)
        originals["相机 照片.jpg"] = buffer.getvalue()
        for name, data in originals.items():
            (source / name).write_bytes(data)
        def command(*args):
            result = subprocess.run([str(executable), "--state-dir", str(root / "state"), *args], capture_output=True, text=True, encoding="utf-8", env=env, timeout=120, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode:
                raise AssertionError(f"Frozen process failed: {result.stdout}\n{result.stderr}")
            return json.loads(result.stdout)
        doctor = command("doctor")
        assert doctor["metadata"] != "unavailable", doctor
        assert doctor["places"]["available"], doctor
        vendor = executable.parent / "_internal/vendor"
        tool = [str(vendor / "exiftool/exiftool.exe")] if os.name == "nt" else [str(vendor / "perl/bin/perl"), str(vendor / "exiftool/exiftool")]
        tool_env = {**env, "LC_ALL": "C", "LC_CTYPE": "C", "LANG": "C"}
        if os.name != "nt":
            tool_env["LD_LIBRARY_PATH"] = str(vendor / "perl/lib/runtime")
        probe = subprocess.run([*tool, "-config", "", "-ver"], env=tool_env, capture_output=True, timeout=30)
        assert probe.returncode == 0 and probe.stdout.strip(), probe.stderr
        job = command("run", str(source), str(target), "--verification", "full", "--yes")
        assert job["safe_to_delete"] is True, job
        assert job["verified_count"] == len(originals), job
        files = command("files", job["id"])["items"]
        photo = next(entry for entry in files if entry["source_rel"] == "相机 照片.jpg")
        assert photo["category"] == "photos"
        assert photo["location"].startswith("CN_Zhejiang_"), photo
        for entry in files:
            expected = originals[entry["source_rel"]]
            assert (source / entry["source_rel"]).read_bytes() == expected
            assert hashlib.sha256((target / entry["target_rel"]).read_bytes()).hexdigest() == hashlib.sha256(expected).hexdigest()
        quick = command("run", str(source), str(target), "--verification", "quick", "--yes")
        assert not quick["safe_to_delete"], quick
    print("PASS: frozen startup, bundled tools/data, copy, SHA-256, source preservation, quick-level deletion gate", flush=True)


if __name__ == "__main__":
    main()
