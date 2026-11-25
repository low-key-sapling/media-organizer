"""Opt-in large-inventory check; all files are created inside this workspace's cache."""
from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from pathlib import Path

from media_organizer.engine import Organizer

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--files", type=int, default=100000)
    args = parser.parse_args()
    root = ROOT / ".cache" / ("benchmark-" + uuid.uuid4().hex[:8])
    source, target = root / "source", root / "target"
    source.mkdir(parents=True)
    started = time.monotonic()
    for number in range(args.files):
        directory = source / f"batch-{number // 1000:03d}"
        if number % 1000 == 0:
            directory.mkdir()
        (directory / f"sample-{number:06d}.bin").touch()
        if (number + 1) % 20000 == 0:
            print(f"Created {number+1:,} inventory fixtures", flush=True)
    creation = time.monotonic() - started
    service = Organizer(root / "state")
    try:
        started = time.monotonic()
        job = service.create(str(source), str(target), background=False)
        elapsed = time.monotonic() - started
        assert job["status"] == "ready" and job["file_count"] == args.files, job
        started = time.monotonic()
        listing = service.store.entries(job["id"], page=max(1, args.files // 50), page_size=50)
        page_ms = (time.monotonic() - started) * 1000
        assert listing["total"] == args.files and len(listing["items"]) <= 50
        result = {"files": args.files, "fixture_creation_seconds": round(creation, 2), "scan_seconds": round(elapsed, 2), "last_page_query_ms": round(page_ms, 2), "state_database_bytes": service.store.path.stat().st_size, "root": str(root), "scope": "empty ordinary-file inventory and pagination, not a TB throughput benchmark"}
        (ROOT / ".cache/benchmark-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    finally:
        service.close()


if __name__ == "__main__":
    main()
