"""Exercise a frozen build with developer runtime paths removed from the environment."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import io
import socket
import time
import urllib.request
from pathlib import Path
from zipfile import ZipFile


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
        archive_buffer = io.BytesIO()
        with ZipFile(archive_buffer, "w") as archive:
            archive.writestr("note.txt", "Hello, archived world!")
        originals["资料 20261125.ZIP"] = archive_buffer.getvalue()
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
        document = next(entry for entry in files if entry["source_rel"] == "旅途 20261125.txt")
        assert document["target_rel"] == "documents/202611/txt/旅途 20261125.txt", document
        assert not document["location"], document
        archive = next(entry for entry in files if entry["source_rel"] == "资料 20261125.ZIP")
        assert archive["target_rel"] == "archives/202611/zip/资料 20261125.ZIP", archive
        assert not archive["location"], archive
        for entry in files:
            expected = originals[entry["source_rel"]]
            assert (source / entry["source_rel"]).read_bytes() == expected
            assert hashlib.sha256((target / entry["target_rel"]).read_bytes()).hexdigest() == hashlib.sha256(expected).hexdigest()
        quick = command("run", str(source), str(target), "--verification", "quick", "--yes")
        assert not quick["safe_to_delete"], quick
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        server = subprocess.Popen([str(executable), "--state-dir", str(root / "server-state"), "serve", "--no-browser", "--port", str(port)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            deadline = time.monotonic() + 20
            while True:
                try:
                    with opener.open(f"http://127.0.0.1:{port}/api/meta", timeout=2) as response:
                        assert json.load(response)["offline"] is True
                    break
                except OSError:
                    if server.poll() is not None or time.monotonic() >= deadline:
                        raise AssertionError("Frozen local web service did not start")
                    time.sleep(0.1)
            with opener.open(f"http://127.0.0.1:{port}/", timeout=5) as response:
                assert b'id="app"' in response.read()
            with opener.open(f"http://127.0.0.1:{port}/api/jobs", timeout=5) as response:
                assert json.load(response) == []
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
    print("PASS: frozen CLI and web service, bundled tools/data, Unicode paths, offline GPS, copy, SHA-256, source preservation, quick-level deletion gate", flush=True)


if __name__ == "__main__":
    main()
