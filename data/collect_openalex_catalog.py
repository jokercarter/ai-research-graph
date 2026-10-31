#!/usr/bin/env python3
"""Collect a reproducible, year-balanced Computer Science catalogue slice.

OpenAlex contains millions of Computer Science works in the roadmap window, so
the word "all" needs an explicit coverage contract. This collector uses the
OpenAlex Computer Science field filter, splits the exact date window into
yearly slices, records the expected count for every slice, and downloads a
bounded number of records per slice. The output is a real graph slice, not a
claim that the public unauthenticated API run is exhaustive.

Example:
  python data/collect_openalex_catalog.py --per-slice 200 --max-works 2200 \
    --out data/cs_field_catalog_2200.json

AI-oriented topic or concept filters can be supplied in addition to the CS
field filter with --topic-id or --concept-id. Use --no-field to remove the
Computer Science field clause when building an explicit topic catalogue.
"""
from __future__ import annotations

import argparse
import calendar
import json
import os
import sys
import time
from datetime import date
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from collect_openalex_graph import API_ROOT, OpenAlexClient, normalise_work
except ModuleNotFoundError:
    from data.collect_openalex_graph import API_ROOT, OpenAlexClient, normalise_work

DEFAULT_FROM_DATE = "2016-11-01"
DEFAULT_TO_DATE = "2026-10-31"
CORE_TYPES = ["article", "conference-paper", "preprint", "review"]
# OpenAlex exposes the Computer Science field as field 17. These subfields are
# accepted as an optional narrower public filter when a user wants to target a
# particular CS area without changing the field-level default.
CS_SUBFIELD_IDS = [
    "1702",  # Artificial Intelligence
    "1703",  # Computational Theory and Mathematics
    "1704",  # Computer Graphics and Computer-Aided Design
    "1705",  # Computer Networks and Communications
    "1706",  # Computer Science Applications
    "1707",  # Computer Vision and Pattern Recognition
    "1708",  # Hardware and Architecture
    "1709",  # Human-Computer Interaction
    "1710",  # Information Systems
    "1711",  # Signal Processing
    "1712",  # Software
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-date", default=DEFAULT_FROM_DATE)
    parser.add_argument("--to-date", default=DEFAULT_TO_DATE)
    parser.add_argument("--field-id", default="17", help="OpenAlex field ID; 17 is Computer Science")
    parser.add_argument("--no-field", action="store_true", help="Do not add a Computer Science field clause")
    parser.add_argument("--subfield-id", action="append", default=[], help="OpenAlex topic subfield ID; repeatable")
    parser.add_argument("--topic-id", action="append", default=[], help="OpenAlex topic ID; repeatable")
    parser.add_argument("--concept-id", action="append", default=[], help="OpenAlex concept ID; repeatable")
    parser.add_argument("--search", help="Optional OpenAlex full-text search term")
    parser.add_argument("--type", dest="types", action="append", default=[], help="Work type; repeatable (default: core types)")
    parser.add_argument("--all-types", action="store_true", help="Do not restrict publication type")
    parser.add_argument("--per-page", type=int, default=200, help="OpenAlex page size (1-200)")
    parser.add_argument("--per-slice", type=int, default=500, help="Maximum records retrieved per yearly slice")
    parser.add_argument("--max-works", type=int, default=5000, help="Global record cap; 0 means no cap")
    parser.add_argument("--sort", default="cited_by_count:desc", choices=["cited_by_count:desc", "publication_date:desc", "publication_date:asc"])
    parser.add_argument("--out", default="data/cs_field_catalog.json")
    parser.add_argument("--manifest-out", help="Optional separate coverage manifest JSON")
    parser.add_argument("--mailto", default=os.getenv("OPENALEX_MAILTO", "research@example.com"))
    parser.add_argument("--sleep-seconds", type=float, default=0.05)
    return parser.parse_args()


def iter_year_slices(start_text: str, end_text: str) -> list[tuple[str, str]]:
    start = date.fromisoformat(start_text)
    end = date.fromisoformat(end_text)
    if start > end:
        raise ValueError("--from-date must be <= --to-date")
    slices: list[tuple[str, str]] = []
    for year in range(start.year, end.year + 1):
        first = date(year, 1, 1)
        last = date(year, 12, calendar.monthrange(year, 12)[1])
        left = max(start, first)
        right = min(end, last)
        if left <= right:
            slices.append((left.isoformat(), right.isoformat()))
    return slices


def id_value(value: str) -> str:
    return value.rsplit("/", 1)[-1]


def make_filter(args: argparse.Namespace, start: str, end: str) -> str:
    clauses = [f"from_publication_date:{start}", f"to_publication_date:{end}"]
    if not args.no_field and args.field_id:
        subfields = [id_value(v) for v in args.subfield_id]
        if subfields:
            clauses.append("topics.subfield.id:" + "|".join(subfields))
        else:
            clauses.append(f"primary_topic.field.id:{id_value(args.field_id)}")
    if args.topic_id:
        clauses.append("topics.id:" + "|".join(id_value(v) for v in args.topic_id))
    if args.concept_id:
        clauses.append("concepts.id:" + "|".join(id_value(v) for v in args.concept_id))
    types = args.types or ([] if args.all_types else CORE_TYPES)
    if types:
        clauses.append("type:" + "|".join(types))
    return ",".join(clauses)


def fetch_slice(client: OpenAlexClient, args: argparse.Namespace, start: str, end: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    filter_text = make_filter(args, start, end)
    requested_limit = args.per_slice if args.per_slice > 0 else 2_147_483_647
    results: list[dict[str, Any]] = []
    cursor = "*"
    pages = 0
    expected_count: int | None = None
    while len(results) < requested_limit:
        per_page = min(max(1, args.per_page), 200, requested_limit - len(results))
        params: dict[str, Any] = {"filter": filter_text, "per-page": per_page, "cursor": cursor, "sort": args.sort}
        if args.search:
            params["search"] = args.search
        payload = client.get("works", params)
        meta = payload.get("meta") or {}
        if expected_count is None:
            expected_count = meta.get("count")
        batch = payload.get("results") or []
        results.extend(batch)
        pages += 1
        next_cursor = meta.get("next_cursor")
        if not batch or not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
        if args.sleep_seconds:
            time.sleep(args.sleep_seconds)
    return results[:requested_limit], {
        "from_date": start,
        "to_date": end,
        "filter": filter_text,
        "search": args.search,
        "expected_count": expected_count,
        "retrieved_count": len(results[:requested_limit]),
        "pages": pages,
        "sort": args.sort,
    }


def main() -> int:
    args = parse_args()
    slices = iter_year_slices(args.from_date, args.to_date)
    client = OpenAlexClient(args.mailto, os.getenv("OPENALEX_API_KEY"))
    node_by_id: dict[str, dict[str, Any]] = {}
    raw_by_id: dict[str, dict[str, Any]] = {}
    manifest: list[dict[str, Any]] = []
    total_cap = args.max_works if args.max_works > 0 else 2_147_483_647

    for start, end in slices:
        remaining = total_cap - len(node_by_id)
        if remaining <= 0:
            break
        original_limit = args.per_slice
        args.per_slice = min(original_limit if original_limit > 0 else remaining, remaining)
        try:
            works, stats = fetch_slice(client, args, start, end)
        except Exception as exc:
            print(f"warning: slice {start}..{end} failed: {exc}", file=sys.stderr)
            stats = {"from_date": start, "to_date": end, "filter": make_filter(args, start, end), "error": str(exc), "expected_count": None, "retrieved_count": 0, "pages": 0}
            works = []
        args.per_slice = original_limit
        new_nodes = 0
        for work in works:
            work_id = work.get("id")
            if not work_id:
                continue
            raw_by_id[work_id] = work
            if work_id in node_by_id:
                continue
            node = normalise_work(work)
            node.update(
                {
                    "in_window": True,
                    "catalog_slice": {"from_date": start, "to_date": end},
                    "catalog_filter": stats.get("filter"),
                    "provenance": {
                        "provider": "OpenAlex",
                        "retrieval": "field_or_topic_catalog_slice",
                        "works_endpoint": f"{API_ROOT}/works",
                        "filter": stats.get("filter"),
                    },
                }
            )
            node_by_id[work_id] = node
            new_nodes += 1
        stats["unique_nodes_added"] = new_nodes
        manifest.append(stats)
        print(f"slice {start}..{end}: expected={stats.get('expected_count')} fetched={stats.get('retrieved_count')} unique_added={new_nodes}", file=sys.stderr)

    edges: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str]] = set()
    for source_id, work in raw_by_id.items():
        if source_id not in node_by_id:
            continue
        for target_id in work.get("referenced_works") or []:
            pair = (source_id, target_id)
            if source_id == target_id:
                continue
            if target_id not in node_by_id or pair in seen_edges:
                continue
            seen_edges.add(pair)
            edges.append(
                {
                    "source": source_id,
                    "target": target_id,
                    "relation": "cites",
                    "source_field": "referenced_works",
                    "source_system": "openalex",
                    "provenance": {"provider": "OpenAlex", "api": API_ROOT, "citation_field": "referenced_works"},
                }
            )

    generated_at = datetime.now(timezone.utc).isoformat()
    expected_total = sum(int(s["expected_count"]) for s in manifest if isinstance(s.get("expected_count"), int))
    retrieved_total = len(node_by_id)
    payload = {
        "schema_version": "0.3",
        "generated_at": generated_at,
        "meta": {
            "title": "OpenAlex Computer Science catalogue slice",
            "description": "Year-balanced, bounded slice of OpenAlex Computer Science works with a transparent coverage denominator.",
            "window": {"from": args.from_date, "to": args.to_date},
            "field": {"id": args.field_id, "name": "Computer Science"} if not args.no_field else None,
            "expected_count": expected_total,
            "retrieved_count": retrieved_total,
            "coverage_fraction": retrieved_total / expected_total if expected_total else None,
            "is_exhaustive": False,
        },
        "provenance": {
            "provider": "OpenAlex",
            "api": API_ROOT,
            "works_endpoint": f"{API_ROOT}/works",
            "citation_field": "referenced_works",
            "edge_semantics": "source work cites target work",
            "documentation": "https://docs.openalex.org/",
            "catalog_filter_contract": "primary_topic.field.id:17 plus core publication types by default",
            "no_inferred_edges": True,
        },
        "query": {
            "from_date": args.from_date,
            "to_date": args.to_date,
            "field_id": args.field_id if not args.no_field else None,
            "subfield_ids": args.subfield_id or (CS_SUBFIELD_IDS if not args.no_field and id_value(args.field_id) == "17" else []),
            "topic_ids": args.topic_id,
            "concept_ids": args.concept_id,
            "types": args.types or ([] if args.all_types else CORE_TYPES),
            "search": args.search,
            "per_page": args.per_page,
            "per_slice": args.per_slice,
            "max_works": args.max_works,
            "sort": args.sort,
        },
        "coverage": {"slices": manifest, "expected_count": expected_total, "retrieved_unique_count": retrieved_total},
        "stats": {"nodes": retrieved_total, "edges": len(edges), "slices": len(manifest)},
        "nodes": list(node_by_id.values()),
        "edges": edges,
    }
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.manifest_out:
        Path(args.manifest_out).write_text(json.dumps({"generated_at": generated_at, "coverage": payload["coverage"], "query": payload["query"]}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {output} ({retrieved_total} nodes, {len(edges)} edges, expected denominator {expected_total})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
