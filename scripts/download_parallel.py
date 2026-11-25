"""Build helper for large public resource archives over slow connections."""
from __future__ import annotations

import argparse
import concurrent.futures
import os
import re
import urllib.request
from pathlib import Path


def download(url: str, output: Path, workers: int = 6):
    headers = {"User-Agent": "Mozilla/5.0 MediaOrganizerBuild/0.1"}
    request = urllib.request.Request(url, headers={**headers, "Range": "bytes=0-0"})
    with urllib.request.urlopen(request, timeout=40) as response:
        content_range = response.headers.get("Content-Range", "")
        match = re.fullmatch(r"bytes 0-0/(\d+)", content_range)
        if response.status != 206 or not match:
            raise RuntimeError("Server does not support byte ranges")
        size = int(match.group(1))
    output.parent.mkdir(parents=True, exist_ok=True)
    part_size = (size + workers - 1) // workers

    def part(index: int):
        start, end = index * part_size, min(size - 1, (index + 1) * part_size - 1)
        path = output.with_name(output.name + f".range-{index}")
        if path.is_file() and path.stat().st_size == end - start + 1:
            return path
        for attempt in range(3):
            try:
                request = urllib.request.Request(url, headers={**headers, "Range": f"bytes={start}-{end}"})
                with urllib.request.urlopen(request, timeout=60) as response, path.open("wb") as target:
                    if response.status != 206 or response.headers.get("Content-Range") != f"bytes {start}-{end}/{size}":
                        raise RuntimeError("Range response did not match the requested bytes")
                    while data := response.read(16384):
                        target.write(data)
                if path.stat().st_size != end - start + 1:
                    raise RuntimeError("Incomplete range")
                print(f"{output.name}: part {index + 1}/{workers} ready", flush=True)
                return path
            except OSError:
                if attempt == 2:
                    raise
        raise RuntimeError("Download failed")

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        paths = list(executor.map(part, range(workers)))
    combined = output.with_name(output.name + ".combined")
    with combined.open("wb") as destination:
        for path in paths:
            with path.open("rb") as source:
                while data := source.read(1024 * 1024):
                    destination.write(data)
        destination.flush()
        os.fsync(destination.fileno())
    os.replace(combined, output)
    for path in paths:
        path.unlink()
    print(f"Downloaded {output.name}: {size:,} bytes", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    download(args.url, args.output)
