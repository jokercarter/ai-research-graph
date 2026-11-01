#!/usr/bin/env python3
"""Build a deterministic recent AI/CS catalogue from the local CitationCS archive.

The archive contains a much larger Computer Science corpus than the website
needs to render.  This builder selects an auditable, year-balanced 200k-paper
AI-priority slice from the exact ten-year window, keeps only real ``ref_lst``
citation edges whose endpoints are in the slice, and optionally enriches
overlapping records with metadata from the previous website snapshot.
"""
from __future__ import annotations

import argparse
import gzip
import heapq
import json
import re
import zipfile
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Iterable

try:
    import orjson
except ImportError:  # pragma: no cover
    orjson = None


SOURCE_URL = "https://zenodo.org/records/16537950"
ARCHIVE_MEMBER = "citation_cs/data/cs_paper_data.jsonl"
OPENALEX_PREFIX = "https://openalex.org/"
DEFAULT_FROM = "2016-11-01"
DEFAULT_TO = "2026-10-31"
AI_NAME_TOKENS = (
    "machine learning",
    "deep learning",
    "computer vision",
    "natural language",
    "robotics",
    "neural",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, help="Path to citation_cs.parallel.zip")
    parser.add_argument("--existing", default="data/ai_cs_graph_citationcs_60000.json.gz")
    parser.add_argument("--out", default="data/ai_cs_graph_200000.json.gz")
    parser.add_argument("--from-date", default=DEFAULT_FROM)
    parser.add_argument("--to-date", default=DEFAULT_TO)
    parser.add_argument("--target-nodes", type=int, default=200000)
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
    return match.group(1).upper() if match else text


def date_in_window(pub_date: str | None, start: str, end: str) -> bool:
    return bool(pub_date and start <= pub_date[:10] <= end)


def topic_priority(record: dict[str, Any]) -> int:
    topic = record.get("primary_topic") or {}
    subfield = str(topic.get("subfield") or "").lower()
    name = str(topic.get("name") or "").lower()
    if "artificial intelligence" in subfield:
        return 4
    if any(token in name for token in AI_NAME_TOKENS):
        return 3
    if str(topic.get("field") or "").lower() == "computer science":
        return 2
    return 0


def is_recent_cs(record: dict[str, Any], start: str, end: str) -> bool:
    topic = record.get("primary_topic") or {}
    return (
        date_in_window(str(record.get("pub_date") or "")[:10], start, end)
        and str(topic.get("field") or "").lower() == "computer science"
    )


def is_ai_priority(record: dict[str, Any], start: str, end: str) -> bool:
    return is_recent_cs(record, start, end) and topic_priority(record) >= 3


def iter_records(archive: str) -> Iterable[dict[str, Any]]:
    with zipfile.ZipFile(archive) as zf:
        with zf.open(ARCHIVE_MEMBER) as handle:
            for line in handle:
                if line.strip():
                    yield parse_json(line)


def allocate_quotas(counts: dict[str, int], target: int) -> dict[str, int]:
    """Allocate target slots proportionally with a deterministic remainder."""
    total = sum(counts.values())
    if total < target:
        raise ValueError(f"only {total:,} eligible records are available; need {target:,}")
    raw = {year: target * count / total for year, count in counts.items()}
    quotas = {year: min(counts[year], int(raw[year])) for year in counts}
    remaining = target - sum(quotas.values())
    order = sorted(counts, key=lambda year: (raw[year] - int(raw[year]), counts[year], year), reverse=True)
    for year in order:
        if not remaining:
            break
        if quotas[year] < counts[year]:
            quotas[year] += 1
            remaining -= 1
    if remaining:
        raise ValueError(f"could not allocate {remaining} slots")
    return quotas


def load_existing(path: str) -> dict[str, dict[str, Any]]:
    candidate = Path(path)
    if not candidate.exists():
        return {}
    if str(candidate).endswith(".gz"):
        with gzip.open(candidate, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
    else:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    return {str(node.get("id")): node for node in payload.get("nodes", []) if node.get("id")}


def authors_from_record(record: dict[str, Any]) -> list[dict[str, Any]]:
    authors: list[dict[str, Any]] = []
    for author in record.get("author_lst") or []:
        if isinstance(author, dict):
            name = author.get("name") or author.get("display_name")
            if name:
                authors.append({"id": author.get("openalex_id"), "name": name})
        elif author:
            authors.append({"name": str(author)})
    return authors


def node_from_record(record: dict[str, Any], seed: bool, start: str, end: str) -> dict[str, Any] | None:
    key = work_key(record.get("id"))
    if not key:
        return None
    topic = record.get("primary_topic") or {}
    pub_date = str(record.get("pub_date") or "")[:10] or None
    topics = [item for item in (topic.get("name"), topic.get("subfield"), topic.get("field")) if item]
    return {
        "id": f"{OPENALEX_PREFIX}{key}",
        "openalex_id": f"{OPENALEX_PREFIX}{key}",
        "title": record.get("title") or "Untitled work",
        "publication_date": pub_date,
        "publication_year": int(pub_date[:4]) if pub_date and pub_date[:4].isdigit() else None,
        "type": record.get("venue_type") or "article",
        "authors": authors_from_record(record),
        "topics": topics,
        "primary_topic": topic,
        "venue": record.get("venue_name") or "",
        "cited_by_count": 0,
        "referenced_works_count": len(record.get("ref_lst") or []),
        "source": "citationcs",
        "url": f"{OPENALEX_PREFIX}{key}",
        "in_window": date_in_window(pub_date, start, end),
        "is_external_reference": False,
        "graph_depth": 0,
        "is_seed": seed,
        "citationcs_membership": True,
        "citationcs_catalog_seed": True,
        "inclusion_reason": "recent_ai_priority_computer_science",
        "provenance": {
            "provider": "CitationCS",
            "dataset": "CitationCS: A Citation Network of Computer Science Publications",
            "snapshot_url": SOURCE_URL,
            "license": "CC BY 4.0",
        },
    }


def enrich_node(node: dict[str, Any], existing: dict[str, Any] | None) -> dict[str, Any]:
    if not existing:
        return node
    merged = dict(existing)
    # Keep richer OpenAlex fields (abstract, DOI, awards, citation count), then
    # overwrite the fields that establish this release's exact inclusion scope.
    for key, value in node.items():
        if key not in merged or merged.get(key) in (None, "", [], {}):
            merged[key] = value
    merged.update(
        {
            "id": node["id"],
            "openalex_id": node["openalex_id"],
            "primary_topic": node["primary_topic"],
            "topics": node["topics"],
            "venue": node["venue"],
            "type": node["type"],
            "referenced_works_count": node["referenced_works_count"],
            "publication_date": node["publication_date"],
            "publication_year": node["publication_year"],
            "in_window": True,
            "is_external_reference": False,
            "citationcs_membership": True,
            "citationcs_catalog_seed": True,
            "inclusion_reason": node["inclusion_reason"],
            "provenance": node["provenance"],
        }
    )
    merged["metadata_sources"] = sorted(set((existing.get("metadata_sources") or []) + [existing.get("source"), "citationcs"]) - {None, ""})
    return merged


def main() -> int:
    args = parse_args()
    if args.target_nodes < 1:
        raise SystemExit("--target-nodes must be positive")

    # First pass: measure the available AI-priority records by year.  A
    # proportional quota keeps the decade visible while prioritising AI topics.
    counts: Counter[str] = Counter()
    cs_counts: Counter[str] = Counter()
    for record in iter_records(args.archive):
        if is_recent_cs(record, args.from_date, args.to_date):
            year = str(record.get("pub_date"))[:4]
            cs_counts[year] += 1
            if is_ai_priority(record, args.from_date, args.to_date):
                counts[year] += 1

    candidate_counts = counts
    selection_rule = "AI-priority topics (Artificial Intelligence subfield or ML/DL/CV/NLP/robotics/neural topic), proportional by publication year"
    if sum(candidate_counts.values()) < args.target_nodes:
        # This branch keeps the builder usable if a future archive has fewer AI
        # records: fill the remaining slots with general recent Computer Science.
        candidate_counts = cs_counts
        selection_rule = "Recent Computer Science, proportional by publication year; AI-priority topics are ranked first within each year"
    quotas = allocate_quotas(dict(candidate_counts), args.target_nodes)

    # Second pass: retain only the top deterministic records per year.  The
    # heap keeps memory bounded while the final set remains reproducible.
    heaps: dict[str, list[tuple[tuple[int, str, str], str]]] = defaultdict(list)
    for record in iter_records(args.archive):
        if not is_recent_cs(record, args.from_date, args.to_date):
            continue
        year = str(record.get("pub_date"))[:4]
        priority = topic_priority(record)
        if candidate_counts is counts and priority < 3:
            continue
        if year not in quotas:
            continue
        key = work_key(record.get("id"))
        if not key:
            continue
        score = (priority, str(record.get("pub_date") or ""), key)
        heap = heaps[year]
        heapq.heappush(heap, (score, key))
        if len(heap) > quotas[year]:
            heapq.heappop(heap)
    selected_keys = {key for heap in heaps.values() for _, key in heap}
    if len(selected_keys) != args.target_nodes:
        raise RuntimeError(f"selected {len(selected_keys):,} unique records, expected {args.target_nodes:,}")
    selected_ids = {f"{OPENALEX_PREFIX}{key}" for key in selected_keys}

    existing = load_existing(args.existing)
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    edge_keys: set[tuple[str, str]] = set()
    indegree: Counter[str] = Counter()
    selected_years: Counter[str] = Counter()
    selected_priorities: Counter[str] = Counter()

    # Third pass: materialise the selected records and their real citation edges.
    for record in iter_records(args.archive):
        key = work_key(record.get("id"))
        if not key or key not in selected_keys:
            continue
        priority = topic_priority(record)
        node = node_from_record(record, priority >= 3, args.from_date, args.to_date)
        if node is None:
            continue
        node = enrich_node(node, existing.get(node["id"]))
        nodes[node["id"]] = node
        year = str(node.get("publication_year") or "")
        selected_years[year] += 1
        selected_priorities["ai_priority" if priority >= 3 else "cs_context"] += 1
        for raw_ref in record.get("ref_lst") or []:
            ref_key = work_key(raw_ref)
            if not ref_key:
                continue
            target = f"{OPENALEX_PREFIX}{ref_key}"
            source = node["id"]
            pair = (source, target)
            if target not in selected_ids or pair in edge_keys or source == target:
                continue
            edge_keys.add(pair)
            indegree[target] += 1
            edges.append(
                {
                    "source": source,
                    "target": target,
                    "relation": "cites",
                    "source_field": "ref_lst",
                    "source_system": "citationcs",
                    "provenance": {"provider": "CitationCS", "dataset_url": SOURCE_URL, "citation_field": "ref_lst"},
                }
            )

    if len(nodes) != args.target_nodes:
        raise RuntimeError(f"materialised {len(nodes):,} records, expected {args.target_nodes:,}")

    # Add any old provider edges that connect two selected works.  This keeps
    # the overlap with OpenAlex auditable without adding nodes outside scope.
    old_payload: dict[str, Any] = {}
    existing_path = Path(args.existing)
    if existing_path.exists():
        if str(existing_path).endswith(".gz"):
            with gzip.open(existing_path, "rt", encoding="utf-8") as handle:
                old_payload = json.load(handle)
        else:
            old_payload = json.loads(existing_path.read_text(encoding="utf-8"))
    for raw_edge in old_payload.get("edges", []):
        source = str(raw_edge.get("source"))
        target = str(raw_edge.get("target"))
        pair = (source, target)
        if source not in nodes or target not in nodes or source == target or pair in edge_keys:
            continue
        edge = dict(raw_edge)
        edge.update({"source": source, "target": target})
        edge.setdefault("provenance_sources", []).append("existing_snapshot")
        edge_keys.add(pair)
        indegree[target] += 1
        edges.append(edge)

    for node_id, node in nodes.items():
        node["cited_by_count"] = max(int(node.get("cited_by_count") or 0), indegree[node_id])

    topic_count = len({
        json.dumps(topic, ensure_ascii=False, sort_keys=True) if isinstance(topic, dict) else str(topic)
        for node in nodes.values()
        for topic in node.get("topics", [])
    })
    payload = {
        "schema_version": "0.6",
        "generated_at": date.today().isoformat(),
        "meta": {
            "title": "Recent AI and Computer Science citation graph",
            "description": "A deterministic 200,000-paper AI-priority slice from the CitationCS Computer Science archive, restricted to the requested ten-year window.",
            "window": {"from": args.from_date, "to": args.to_date},
            "is_exhaustive": False,
            "selection_rule": selection_rule,
            "core_publication_types": sorted({node.get("type") for node in nodes.values() if node.get("type")}),
        },
        "provenance": {
            "provider": "CitationCS" if not existing else "CitationCS + OpenAlex enrichment",
            "dataset_url": SOURCE_URL,
            "citation_field": "ref_lst",
            "edge_semantics": "source work cites target work",
            "selection": "year-proportional AI-priority records from primary_topic.field=Computer Science",
            "no_inferred_edges": True,
        },
        "coverage": {
            "source_dataset_total_records": 3720575,
            "source_dataset_internal_citations": 22908275,
            "source_window_cs_records": sum(cs_counts.values()),
            "source_window_ai_priority_records": sum(counts.values()),
            "selected_sample_count": len(nodes),
            "selected_sample_fraction_of_window_ai_priority": len(nodes) / sum(counts.values()) if counts else None,
            "selected_year_counts": dict(sorted(selected_years.items())),
            "selected_priority_counts": dict(selected_priorities),
            "is_exhaustive": False,
        },
        "query": {
            "archive": args.archive,
            "existing_enrichment": args.existing,
            "from_date": args.from_date,
            "to_date": args.to_date,
            "target_nodes": args.target_nodes,
        },
        "stats": {
            "nodes": len(nodes),
            "edges": len(edges),
            "in_window_nodes": sum(1 for node in nodes.values() if node.get("in_window")),
            "external_nodes": sum(1 for node in nodes.values() if node.get("is_external_reference")),
            "seed_nodes": sum(1 for node in nodes.values() if node.get("is_seed")),
            "topics": topic_count,
            "citationcs_nodes": len(nodes),
            "citationcs_edges": sum(1 for edge in edges if edge.get("source_system") == "citationcs"),
            "existing_snapshot_edges": sum(1 for edge in edges if edge.get("source_system") != "citationcs"),
        },
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
    print(f"wrote {output} ({len(nodes):,} nodes, {len(edges):,} edges)")
    print(f"year counts: {dict(sorted(selected_years.items()))}")
    print(f"priority counts: {dict(selected_priorities)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
