#!/usr/bin/env python3
"""Download the CitationCS archive with resumable HTTP range requests.

The source archive is close to 1 GB and is intentionally kept outside the
repository. This helper stores one temporary part per byte range, so a
network interruption only retries incomplete parts.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests


DEFAULT_URL = "https://zenodo.org/api/records/16537950/files/citation_cs.zip/content"
DEFAULT_MD5 = "ad3d119e4a8628d49f83f6941d5689e3"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--out", required=True, help="Final archive path")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--chunk-mb", type=int, default=8)
    parser.add_argument("--md5", default=DEFAULT_MD5)
    parser.add_argument("--keep-parts", action="store_true")
    return parser.parse_args()


def remote_size(url: str) -> int:
    response = requests.head(url, allow_redirects=True, timeout=45)
    response.raise_for_status()
    size = response.headers.get("content-length")
    if not size:
        raise RuntimeError("source did not provide Content-Length")
    return int(size)


def download_part(url: str, part: Path, start: int, end: int) -> Path:
    expected = end - start + 1
    if part.exists() and part.stat().st_size == expected:
        return part
    temporary = part.with_suffix(part.suffix + ".tmp")
    for attempt in range(1, 6):
        try:
            with requests.get(
                url,
                headers={"Range": f"bytes={start}-{end}"},
                stream=True,
                timeout=(30, 180),
            ) as response:
                response.raise_for_status()
                if response.status_code != 206:
                    raise RuntimeError(f"range request returned HTTP {response.status_code}")
                content_range = response.headers.get("content-range", "")
                if not content_range.startswith(f"bytes {start}-{end}/"):
                    raise RuntimeError(f"unexpected Content-Range: {content_range}")
                with temporary.open("wb") as handle:
                    for block in response.iter_content(chunk_size=1024 * 1024):
                        if block:
                            handle.write(block)
            if temporary.stat().st_size != expected:
                raise RuntimeError(f"expected {expected} bytes, got {temporary.stat().st_size}")
            temporary.replace(part)
            return part
        except Exception:
            if temporary.exists():
                temporary.unlink()
            if attempt == 5:
                raise
            time.sleep(attempt * 2)
    raise AssertionError("unreachable")


def md5sum(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    args = parse_args()
    workers = max(1, min(args.workers, 32))
    chunk_size = max(1, args.chunk_mb) * 1024 * 1024
    output = Path(args.out).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    total = remote_size(args.url)
    part_dir = output.with_name(output.name + ".parts")
    part_dir.mkdir(parents=True, exist_ok=True)
    ranges = [(start, min(total - 1, start + chunk_size - 1)) for start in range(0, total, chunk_size)]
    print(f"source={total} bytes, parts={len(ranges)}, workers={workers}", file=sys.stderr)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(download_part, args.url, part_dir / f"part_{index:05d}", start, end): index
            for index, (start, end) in enumerate(ranges)
        }
        completed = 0
        for future in as_completed(futures):
            index = futures[future]
            future.result()
            completed += 1
            if completed == 1 or completed % 10 == 0 or completed == len(ranges):
                print(f"parts {completed}/{len(ranges)} (latest {index})", file=sys.stderr)

    temporary_output = output.with_suffix(output.suffix + ".tmp")
    with temporary_output.open("wb") as target:
        for index in range(len(ranges)):
            with (part_dir / f"part_{index:05d}").open("rb") as source:
                shutil.copyfileobj(source, target, length=8 * 1024 * 1024)
    temporary_output.replace(output)
    if output.stat().st_size != total:
        raise RuntimeError(f"assembled file has {output.stat().st_size} bytes; expected {total}")
    if args.md5:
        actual = md5sum(output)
        print(f"md5={actual}", file=sys.stderr)
        if actual.lower() != args.md5.lower():
            raise RuntimeError(f"MD5 mismatch: expected {args.md5}, got {actual}")
    if not args.keep_parts:
        for part in part_dir.glob("part_*"):
            part.unlink()
        part_dir.rmdir()
    print(f"wrote {output} ({output.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
