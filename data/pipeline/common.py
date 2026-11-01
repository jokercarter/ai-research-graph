"""Shared helpers for the monthly AI Research Graph pipeline.

Sources: arXiv OAI-PMH (metadata + abstracts), Semantic Scholar Graph API
(citation counts and references), and the offline CitationCS snapshot already
in this repository. The OpenAlex API is intentionally not used.
"""
from __future__ import annotations

import gzip
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Iterator

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / "data" / "work"
WORK.mkdir(parents=True, exist_ok=True)

# arXiv categories treated as AI research for this site.
AI_CATEGORIES = {
    "cs.AI", "cs.LG", "cs.CL", "cs.CV", "cs.RO", "cs.IR", "cs.MA", "cs.NE",
    "cs.HC", "cs.CR", "cs.SD", "eess.AS", "eess.IV", "stat.ML",
}
# A paper is kept when its primary category is here, or when it is cross-listed
# to at least one of the core categories.
CORE_CATEGORIES = {"cs.AI", "cs.LG", "cs.CL", "cs.CV", "cs.RO", "cs.IR", "cs.MA", "cs.NE", "stat.ML"}
WINDOW_START = "2016-11-01"


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    if not path.exists():
        return
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def append_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with gzip.open(path, "at", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            count += 1
    return count


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    tmp = path.with_suffix(path.suffix + ".tmp")
    count = 0
    with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=5) as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            count += 1
    os.replace(tmp, path)
    return count


def load_state(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return dict(default)


def save_state(path: Path, state: dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def http(url: str, *, data: bytes | None = None, headers: dict[str, str] | None = None,
         retries: int = 8, timeout: int = 90) -> bytes:
    hdrs = {"User-Agent": "AI-Research-Graph/2.0 (monthly research index)"}
    hdrs.update(headers or {})
    delay = 3.0
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers=hdrs, method="POST" if data else "GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as err:
            if err.code in (429, 500, 502, 503, 504) and attempt + 1 < retries:
                wait = float(err.headers.get("Retry-After") or delay)
                time.sleep(min(wait, 60))
                delay = min(delay * 1.7, 60)
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt + 1 >= retries:
                raise
            time.sleep(delay)
            delay = min(delay * 1.7, 60)
    raise RuntimeError("unreachable")


class Budget:
    """Stop long steps cleanly so they can resume (``--time-budget`` seconds; 0 = unlimited)."""

    def __init__(self, seconds: float):
        self.deadline = time.time() + seconds if seconds and seconds > 0 else None

    def exhausted(self) -> bool:
        return self.deadline is not None and time.time() >= self.deadline
