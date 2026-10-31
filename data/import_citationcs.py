#!/usr/bin/env python3
"""Build a bounded AI/CS citation slice from the CitationCS Zenodo archive.

CitationCS uses OpenAlex work identifiers (``W...``) and stores references in
``ref_lst``.  This importer keeps those identifiers on the OpenAlex URL form,
then performs a deterministic, recent-paper-first traversal over the local
archive.  The archive is much larger than the website payload, so the output
is explicitly a reproducible slice and retains source/coverage metadata.
"""
from __future__ import annotations

import argparse
import gzip
import heapq
import json
import re
import sys
import zipfile
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Iterable

try:
    import orjson
except ImportError:  # pragma: no cover - the standard library remains usable
    orjson = None


SOURCE_URL = "https://zenodo.org/records/16537950"
ARCHIVE_MEMBER = "citation_cs/data/cs_paper_data.jsonl"
OPENALEX_PREFIX = "https://openalex.org/"
WINDOW_START = "2016-11-01"
WINDOW_END = "2026-10-31"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, help="Path to citation_cs.zip")
    parser.add_argument("--existing", default="data/ai_cs_graph_citationcs_60000.json.gz", help="Existing graph used for seed IDs")
    parser.add_argument("--out", default="data/citationcs_ai_cs_slice_50000.json.gz")
    parser.add_argument("--from-date", default=WINDOW_START)
    parser.add_argument("--to-date", default=WINDOW_END)
    parser.add_argument("--depth", type=int, default=2, help="Reference traversal depth")
    parser.add_argument("--max-seeds", type=int, default=10000)
    parser.add_argument("--existing-seed-limit", type=int, default=5000)
    parser.add_argument("--seeds-per-year", type=int, default=450)
    parser.add_argument("--max-nodes", type=int, default=50000)
    return parser.parse_args()


def parse_json(line: bytes) -> dict[str, Any]:
    return orjson.loads(line) if orjson else json.loads(line)


def work_key(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        value = value.get("id") or value.get("openalex_id") or value.get("work_id")
    text = str(value).strip()
    if not text:
        return None
    match = re.search(r"(?:openalex\.org/)?(W\d+)$", text, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    return text


def canonical_id(value: Any) -> str | None:
    key = work_key(value)
    return f"{OPENALEX_PREFIX}{key}" if key and key.startswith("W") else key


def date_in_window(pub_date: str | None, start: str, end: str) -> bool:
    return bool(pub_date and start <= pub_date[:10] <= end)


def load_existing_ids(path: str) -> set[str]:
    try:
        if str(path).endswith(".gz"):
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                payload = json.load(handle)
        else:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return set()
    return {key for node in payload.get("nodes", []) if (key := work_key(node.get("id")))}


def iter_records(archive: str) -> Iterable[dict[str, Any]]:
    with zipfile.ZipFile(archive) as zf:
        with zf.open(ARCHIVE_MEMBER) as handle:
            for line in handle:
                if line.strip():
                    yield parse_json(line)


def topic_priority(record: dict[str, Any]) -> int:
    topic = record.get("primary_topic") or {}
    subfield = str(topic.get("subfield") or "").lower()
    name = str(topic.get("name") or "").lower()
    if "artificial intelligence" in subfield:
        return 4
    if any(token in name for token in ("machine learning", "deep learning", "computer vision", "natural language", "robotics", "neural")):
        return 3
    if str(topic.get("field") or "").lower() == "computer science":
        return 2
    return 0


def node_from_record(record: dict[str, Any], depth: int, seed_keys: set[str], start: str, end: str) -> dict[str, Any] | None:
    key = work_key(record.get("id"))
    if not key:
        return None
    topic = record.get("primary_topic") or {}
    authors = []
    for author in record.get("author_lst") or []:
        if isinstance(author, dict):
            name = author.get("name") or author.get("display_name")
            if name:
                authors.append({"id": author.get("openalex_id"), "name": name})
        elif author:
            authors.append(str(author))
    pub_date = str(record.get("pub_date") or "")[:10] or None
    topics = [item for item in (topic.get("name"), topic.get("subfield"), topic.get("field")) if item]
    return {
        "id": f"{OPENALEX_PREFIX}{key}",
        "openalex_id": f"{OPENALEX_PREFIX}{key}",
        "title": record.get("title") or "Untitled work",
        "publication_date": pub_date,
        "publication_year": int(pub_date[:4]) if pub_date and pub_date[:4].isdigit() else None,
        "type": record.get("venue_type") or "article",
        "authors": authors,
        "topics": topics,
        "primary_topic": topic,
        "venue": record.get("venue_name") or "",
        "cited_by_count": 0,
        "referenced_works_count": len(record.get("ref_lst") or []),
        "source": "citationcs",
        "url": f"{OPENALEX_PREFIX}{key}",
        "in_window": date_in_window(pub_date, start, end),
        "is_external_reference": not date_in_window(pub_date, start, end),
        "graph_depth": depth,
        "is_seed": key in seed_keys,
        "provenance": {
            "provider": "CitationCS",
            "dataset": "CitationCS: A Citation Network of Computer Science Publications",
            "snapshot_url": SOURCE_URL,
            "license": "CC BY 4.0",
        },
    }


def choose_seeds(args: argparse.Namespace, existing_ids: set[str]) -> tuple[set[str], dict[str, int]]:
    existing_hits: list[tuple[tuple[int, str, str], str]] = []
    by_year: dict[str, list[tuple[tuple[int, str, str], str]]] = defaultdict(list)
    total_cs = 0
    total_window = 0
    with zipfile.ZipFile(args.archive) as zf, zf.open(ARCHIVE_MEMBER) as handle:
        for line in handle:
            if not line.strip():
                continue
            record = parse_json(line)
            key = work_key(record.get("id"))
            topic = record.get("primary_topic") or {}
            if not key or str(topic.get("field") or "").lower() != "computer science":
                continue
            total_cs += 1
            pub_date = str(record.get("pub_date") or "")[:10]
            if not date_in_window(pub_date, args.from_date, args.to_date):
                continue
            total_window += 1
            priority = (topic_priority(record), pub_date, key)
            if key in existing_ids:
                existing_hits.append((priority, key))
            year = pub_date[:4]
            heap = by_year[year]
            heapq.heappush(heap, (priority, key))
            if len(heap) > max(1, args.seeds_per_year):
                heapq.heappop(heap)
    existing_hits.sort(reverse=True)
    seed_keys = {key for _, key in existing_hits[: max(0, args.existing_seed_limit)]}
    for year in sorted(by_year):
        for _, key in sorted(by_year[year], reverse=True):
            if len(seed_keys) >= args.max_seeds:
                break
            seed_keys.add(key)
    seed_keys = set(sorted(seed_keys)[: max(0, args.max_seeds)])
    return seed_keys, {"dataset_cs_records": total_cs, "dataset_window_cs_records": total_window, "existing_hits": len(existing_hits)}


def traverse(args: argparse.Namespace, seed_keys: set[str]) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]], dict[str, int]]:
    nodes: dict[str, dict[str, Any]] = {}
    refs_by_source: dict[str, list[str]] = {}
    current = list(seed_keys)
    visited: set[str] = set()
    missing = 0
    for depth in range(max(0, args.depth) + 1):
        current_set = {key for key in current if key not in visited}
        if not current_set or len(nodes) >= args.max_nodes:
            break
        next_order: list[str] = []
        next_seen: set[str] = set()
        with zipfile.ZipFile(args.archive) as zf, zf.open(ARCHIVE_MEMBER) as handle:
            for line in handle:
                if not line.strip():
                    continue
                record = parse_json(line)
                key = work_key(record.get("id"))
                if key not in current_set:
                    continue
                visited.add(key)
                if key not in nodes:
                    node = node_from_record(record, depth, seed_keys, args.from_date, args.to_date)
                    if node:
                        nodes[key] = node
                refs: list[str] = []
                for raw_ref in record.get("ref_lst") or []:
                    ref = work_key(raw_ref)
                    if not ref or ref == key:
                        continue
                    refs.append(ref)
                    if depth < args.depth and ref not in visited and ref not in next_seen:
                        next_seen.add(ref)
                        next_order.append(ref)
                refs_by_source[key] = refs
        remaining = max(0, args.max_nodes - len(nodes))
        current = next_order[:remaining]
        if len(next_order) > remaining:
            missing += len(next_order) - remaining
        print(f"depth {depth}: matched={len(current_set)} nodes={len(nodes)} next={len(current)}", file=sys.stderr)
    return nodes, refs_by_source, {"unresolved_or_capped_candidates": missing, "visited_ids": len(visited)}


def main() -> int:
    args = parse_args()
    if args.max_nodes < 1 or args.max_seeds < 1:
        raise SystemExit("--max-nodes and --max-seeds must be positive")
    existing_ids = load_existing_ids(args.existing)
    seed_keys, scan_stats = choose_seeds(args, existing_ids)
    print(f"selected {len(seed_keys)} seeds from {scan_stats}", file=sys.stderr)
    nodes, refs_by_source, traversal_stats = traverse(args, seed_keys)
    node_keys = set(nodes)
    edges: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str]] = set()
    for source, refs in refs_by_source.items():
        for target in refs:
            if target not in node_keys or source == target or (source, target) in seen_edges:
                continue
            seen_edges.add((source, target))
            edges.append(
                {
                    "source": f"{OPENALEX_PREFIX}{source}",
                    "target": f"{OPENALEX_PREFIX}{target}",
                    "relation": "cites",
                    "source_field": "ref_lst",
                    "source_system": "citationcs",
                    "provenance": {"provider": "CitationCS", "dataset_url": SOURCE_URL, "citation_field": "ref_lst"},
                }
            )
    depth_counts = Counter(str(node.get("graph_depth")) for node in nodes.values())
    payload = {
        "schema_version": "0.5",
        "generated_at": date.today().isoformat(),
        "meta": {
            "title": "CitationCS AI/CS reference traversal slice",
            "window": {"from": args.from_date, "to": args.to_date},
            "is_exhaustive": False,
            "dataset_total_records": 3720575,
            "dataset_internal_citations": 22908275,
        },
        "provenance": {
            "provider": "CitationCS",
            "dataset_url": SOURCE_URL,
            "snapshot_description": "Zenodo archive published 2025-07-28; metadata aggregated from OpenAlex, DBLP, CORE, and SCImago",
            "license": "CC BY 4.0",
            "citation_field": "ref_lst",
            "edge_semantics": "source work cites target work",
            "no_inferred_edges": True,
        },
        "coverage": {
            **scan_stats,
            "seed_count": len(seed_keys),
            "traversal_depth": args.depth,
            "max_nodes": args.max_nodes,
            "is_exhaustive": False,
            **traversal_stats,
        },
        "stats": {"nodes": len(nodes), "edges": len(edges), "seed_nodes": sum(1 for node in nodes.values() if node.get("is_seed")), "depths": dict(depth_counts)},
        "nodes": list(nodes.values()),
        "edges": edges,
    }
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    if str(output).endswith(".gz"):
        with gzip.open(output, "wt", encoding="utf-8", compresslevel=9) as handle:
            handle.write(serialized)
    else:
        output.write_text(serialized, encoding="utf-8")
    print(f"wrote {output} ({len(nodes)} nodes, {len(edges)} edges)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
