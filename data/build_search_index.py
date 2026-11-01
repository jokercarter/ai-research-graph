#!/usr/bin/env python3
"""Build a compact, provider-aware search index from one or more graph snapshots.

The index is deliberately separate from the citation graph. It carries only
public metadata needed for retrieval and keeps the source IDs and completeness
signals needed to audit an evidence-backed answer. The browser can continue to
use the graph snapshot as a fallback when this artifact is not deployed.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


def load(path: Path) -> dict[str, Any]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))


def text(value: Any) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", str(value or "").lower())).strip()


def tokens(value: Any) -> set[str]:
    return {token for token in TOKEN_RE.findall(text(value)) if len(token) >= 2}


def openalex_id(value: Any) -> str:
    match = re.search(r"W\d+$", str(value or ""), re.I)
    return match.group(0).upper() if match else ""


def doi(value: Any) -> str:
    return re.sub(r"^https?://(dx\.)?doi\.org/", "", str(value or "").strip().lower())


def arxiv(value: Any) -> str:
    match = re.search(r"(?:arxiv:|arxiv\.org/(?:abs|pdf)/)([0-9]{4}\.\d{4,5}(?:v\d+)?)", str(value or ""), re.I)
    return match.group(1).lower() if match else ""


def canonical_key(node: dict[str, Any]) -> str:
    """Prefer stable IDs, then auditable DOI/arXiv/title-author-year keys."""
    oid = openalex_id(node.get("openalex_id") or node.get("id"))
    if oid:
        return f"openalex:{oid}"
    normalized_doi = doi(node.get("doi"))
    if normalized_doi:
        return f"doi:{normalized_doi}"
    normalized_arxiv = arxiv(node.get("arxiv_id") or node.get("url"))
    if normalized_arxiv:
        return f"arxiv:{normalized_arxiv}"
    author = text((node.get("authors") or [""])[0])
    title = re.sub(r"[^\w\u4e00-\u9fff]+", " ", text(node.get("title")))
    return f"title:{title}|author:{author}|year:{node.get('publication_year') or node.get('year') or ''}"


def completeness(node: dict[str, Any]) -> float:
    if node.get("metadata_completeness") is not None:
        try:
            return round(max(0.0, min(1.0, float(node["metadata_completeness"]))), 3)
        except (TypeError, ValueError):
            pass
    fields = ["title", "abstract", "authors", "topics", "venue", "publication_year", "cited_by_count", "url"]
    present = sum(bool(node.get(field)) for field in fields)
    return round(present / len(fields), 3)


def normalize_node(node: dict[str, Any], generated_at: str = "") -> dict[str, Any]:
    authors = [item if isinstance(item, str) else item.get("name") or item.get("display_name") for item in node.get("authors") or []]
    authors = [str(item) for item in authors if item]
    topics = [item if isinstance(item, str) else item.get("name") or item.get("display_name") for item in node.get("topics") or []]
    topics = [str(item) for item in topics if item]
    return {
        "id": str(node.get("id") or node.get("openalex_id") or ""),
        "openalex_id": node.get("openalex_id") or node.get("id") or "",
        "doi": doi(node.get("doi")),
        "arxiv_id": arxiv(node.get("arxiv_id") or node.get("url")),
        "title": str(node.get("title") or ""),
        "abstract": str(node.get("abstract") or ""),
        "authors": authors[:12],
        "topics": topics[:12],
        "venue": str(node.get("venue") or ""),
        "year": int(node.get("publication_year") or node.get("year") or 0),
        "publication_type": str(node.get("publication_type") or node.get("type") or ""),
        "citations_count": int(node.get("cited_by_count") or node.get("citations_count") or 0),
        "url": str(node.get("url") or node.get("landing_page_url") or ""),
        "metadata_source": node.get("metadata_source") or node.get("source") or "unknown",
        "metadata_updated_at": node.get("metadata_updated_at") or generated_at,
        "metadata_completeness": completeness(node),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", required=True, type=Path, help="Graph JSON/JSON.GZ; repeat for split snapshots")
    parser.add_argument("--output", type=Path, default=Path("data/search_index.json.gz"))
    parser.add_argument("--max-postings", type=int, default=1200, help="Keep the most useful bounded postings per token")
    args = parser.parse_args()

    by_key: dict[str, dict[str, Any]] = {}
    generated_at = ""
    for path in args.input:
        snapshot = load(path)
        generated_at = generated_at or str(snapshot.get("generated_at") or snapshot.get("provenance", {}).get("generated_at") or "")
        for raw in snapshot.get("nodes", []):
            node = normalize_node(raw, generated_at)
            key = canonical_key(raw)
            if key not in by_key or node["metadata_completeness"] > by_key[key]["metadata_completeness"]:
                by_key[key] = node

    records = list(by_key.values())
    postings: dict[str, list[str]] = defaultdict(list)
    for record in records:
        fields = [record["title"], record["abstract"], record["venue"], " ".join(record["authors"]), " ".join(record["topics"])]
        for token in set().union(*(tokens(field) for field in fields)):
            postings[token].append(record["id"])

    # Stable ordering makes diffs and cache invalidation reproducible.
    for token, ids in postings.items():
        ids.sort()
        postings[token] = ids[: max(1, args.max_postings)]
    payload = {
        "schema_version": "search-index-v1",
        "generated_at": generated_at,
        "stats": {"records": len(records), "tokens": len(postings)},
        "records": sorted(records, key=lambda item: item["id"]),
        "postings": dict(sorted(postings.items())),
    }
    write(args.output, payload)
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()[:16]
    print(f"wrote {args.output} ({len(records):,} records, {len(postings):,} tokens, sha256:{digest})")


if __name__ == "__main__":
    main()
