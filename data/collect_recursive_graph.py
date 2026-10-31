#!/usr/bin/env python3
"""Build a layered AI/CS citation graph from recent works into their references.

The collector starts with recent OpenAlex search results, then recursively fetches
``referenced_works`` in bounded batches. Every edge in the output is copied from
OpenAlex; no citation or semantic relationship is inferred.

Examples:
  python data/collect_recursive_graph.py --target-nodes 1000 --out data/recursive_graph.json
  python data/collect_recursive_graph.py --target-nodes 2500 --max-depth 3 --seed-pages 2
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    from collect_openalex_graph import API_ROOT, OpenAlexClient, normalise_work
except ModuleNotFoundError:  # Allow importing from the project root as well as running the file directly.
    from data.collect_openalex_graph import API_ROOT, OpenAlexClient, normalise_work

DEFAULT_QUERIES = [
    "artificial intelligence",
    "machine learning",
    "deep learning",
    "large language model",
    "natural language processing",
    "computer vision",
    "reinforcement learning",
    "robotics",
]
DEFAULT_FROM_DATE = "2016-11-01"
DEFAULT_TO_DATE = "2026-10-31"
DEFAULT_SEED_FROM_DATE = "2020-01-01"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", action="append", dest="queries", help="Recent seed query; repeatable")
    parser.add_argument("--seed-pages", type=int, default=1, help="Pages per seed query")
    parser.add_argument("--seed-per-page", type=int, default=100, help="Works per seed page (1-200)")
    parser.add_argument("--seed-limit", type=int, default=800, help="Maximum unique seed works")
    parser.add_argument("--seed-from-date", default=DEFAULT_SEED_FROM_DATE)
    parser.add_argument("--from-date", default=DEFAULT_FROM_DATE, help="Main window start (inclusive)")
    parser.add_argument("--to-date", default=DEFAULT_TO_DATE, help="Main window end (inclusive)")
    parser.add_argument("--target-nodes", type=int, default=1000, help="Stop once this many in-window nodes exist")
    parser.add_argument("--max-depth", type=int, default=2, help="Maximum reference expansion depth")
    parser.add_argument("--max-references-per-node", type=int, default=250, help="Reference IDs read from each work")
    parser.add_argument("--max-fetch-per-layer", type=int, default=5000, help="Reference IDs hydrated in one layer")
    parser.add_argument("--batch-size", type=int, default=50, help="OpenAlex IDs per batch request")
    parser.add_argument("--include-out-of-window", action="store_true", help="Keep older/out-of-window reference nodes (not expanded)")
    parser.add_argument("--out", default="data/recursive_graph.json")
    parser.add_argument("--mailto", default=os.getenv("OPENALEX_MAILTO", "research@example.com"))
    parser.add_argument("--sleep-seconds", type=float, default=0.05, help="Delay between batch requests")
    return parser.parse_args()


def date_in_window(work: dict[str, Any], start: str, end: str) -> bool:
    value = work.get("publication_date")
    if value:
        return start <= str(value)[:10] <= end
    year = work.get("publication_year")
    return year is not None and int(start[:4]) <= int(year) <= int(end[:4])


def id_tail(value: str) -> str:
    return value.rsplit("/", 1)[-1]


def search_recent(client: OpenAlexClient, query: str, start: str, end: str, pages: int, per_page: int) -> list[dict[str, Any]]:
    """Search recent works, prioritizing cited works, using cursor pagination."""
    result: list[dict[str, Any]] = []
    cursor = "*"
    for _ in range(max(1, pages)):
        payload = client.get(
            "works",
            {
                "search": query,
                "filter": f"from_publication_date:{start},to_publication_date:{end}",
                "sort": "cited_by_count:desc",
                "per-page": max(1, min(200, per_page)),
                "cursor": cursor,
            },
        )
        batch = payload.get("results") or []
        result.extend(batch)
        cursor = (payload.get("meta") or {}).get("next_cursor")
        if not batch or not cursor:
            break
    return result


def fetch_by_ids(client: OpenAlexClient, ids: Iterable[str], batch_size: int, sleep_seconds: float) -> list[dict[str, Any]]:
    """Hydrate OpenAlex works by IDs in bounded batches."""
    unique = list(dict.fromkeys(id_tail(x) for x in ids if x))
    result: list[dict[str, Any]] = []
    batch_size = max(1, min(100, batch_size))
    for start in range(0, len(unique), batch_size):
        chunk = unique[start : start + batch_size]
        try:
            payload = client.get("works", {"filter": "openalex:" + "|".join(chunk), "per-page": len(chunk)})
            result.extend(payload.get("results") or [])
        except Exception as exc:
            print(f"warning: reference batch {start}:{start + len(chunk)} failed: {exc}", file=sys.stderr)
        if sleep_seconds:
            time.sleep(sleep_seconds)
    return result


def add_node(
    node_by_id: dict[str, dict[str, Any]],
    raw_by_id: dict[str, dict[str, Any]],
    work: dict[str, Any],
    *,
    depth: int,
    in_window: bool,
    parent_ids: Iterable[str] = (),
    seed: bool = False,
    window_start: str,
    window_end: str,
) -> None:
    work_id = work.get("id")
    if not work_id:
        return
    raw_by_id[work_id] = work
    parents = set(parent_ids)
    if work_id in node_by_id:
        node = node_by_id[work_id]
        node["graph_depth"] = min(int(node.get("graph_depth", depth)), depth)
        node["parent_count"] = int(node.get("parent_count", 0)) + len(parents)
        if seed:
            node["is_seed"] = True
        return
    node = normalise_work(work, external=not in_window)
    node.update(
        {
            "graph_depth": depth,
            "is_seed": seed,
            "in_window": in_window,
            "window_start": window_start,
            "window_end": window_end,
            "parent_count": len(parents),
            "provenance": {
                "provider": "OpenAlex",
                "work_id": work_id,
                "retrieval": "recent_seed_search" if seed else "referenced_works_recursive_expansion",
                "citation_field": "referenced_works",
            },
        }
    )
    node_by_id[work_id] = node


def build_graph(args: argparse.Namespace) -> dict[str, Any]:
    client = OpenAlexClient(args.mailto, os.getenv("OPENALEX_API_KEY"))
    queries = args.queries or DEFAULT_QUERIES
    seed_raw: dict[str, dict[str, Any]] = {}
    for query in queries:
        try:
            works = search_recent(client, query, args.seed_from_date, args.to_date, args.seed_pages, args.seed_per_page)
        except Exception as exc:
            print(f"warning: seed query failed ({query!r}): {exc}", file=sys.stderr)
            continue
        for work in works:
            if work.get("id"):
                seed_raw[work["id"]] = work
        print(f"seed query {query!r}: {len(works)} results, {len(seed_raw)} unique seeds", file=sys.stderr)

    seeds = sorted(
        seed_raw.values(),
        key=lambda work: (str(work.get("publication_date") or ""), int(work.get("cited_by_count") or 0), work.get("id") or ""),
        reverse=True,
    )[: max(1, args.seed_limit)]

    node_by_id: dict[str, dict[str, Any]] = {}
    raw_by_id: dict[str, dict[str, Any]] = {}
    edge_pairs: set[tuple[str, str]] = set()
    edge_depth: dict[tuple[str, str], int] = {}
    frontier: list[str] = []
    for work in seeds:
        work_id = work.get("id")
        if not work_id:
            continue
        add_node(
            node_by_id,
            raw_by_id,
            work,
            depth=0,
            in_window=date_in_window(work, args.from_date, args.to_date),
            seed=True,
            window_start=args.from_date,
            window_end=args.to_date,
        )
        frontier.append(work_id)

    layer_stats: list[dict[str, Any]] = [{"depth": 0, "new_nodes": len(node_by_id), "frontier": len(frontier)}]
    for depth in range(0, max(0, args.max_depth)):
        if not frontier:
            break
        pending: list[str] = []
        parent_for_ref: dict[str, str] = {}
        for source_id in frontier:
            source = raw_by_id.get(source_id) or {}
            references = list(source.get("referenced_works") or [])[: max(1, args.max_references_per_node)]
            if len(source.get("referenced_works") or []) > len(references):
                node_by_id[source_id]["references_truncated"] = True
                node_by_id[source_id]["references_total"] = len(source.get("referenced_works") or [])
            for target_id in references:
                if target_id == source_id:
                    continue
                pair = (source_id, target_id)
                edge_pairs.add(pair)
                edge_depth[pair] = depth + 1
                if target_id not in raw_by_id:
                    pending.append(target_id)
                    parent_for_ref.setdefault(target_id, source_id)
        pending = list(dict.fromkeys(pending))[: max(1, args.max_fetch_per_layer)]
        fetched = fetch_by_ids(client, pending, args.batch_size, args.sleep_seconds)
        next_frontier: list[str] = []
        before = len(node_by_id)
        for work in fetched:
            work_id = work.get("id")
            if not work_id:
                continue
            in_window = date_in_window(work, args.from_date, args.to_date)
            if not in_window and not args.include_out_of_window:
                continue
            parent_id = parent_for_ref.get(work_id)
            add_node(
                node_by_id,
                raw_by_id,
                work,
                depth=depth + 1,
                in_window=in_window,
                parent_ids=[parent_id] if parent_id else (),
                window_start=args.from_date,
                window_end=args.to_date,
            )
            if in_window and work_id not in next_frontier:
                next_frontier.append(work_id)
        layer_stats.append({"depth": depth + 1, "new_nodes": len(node_by_id) - before, "frontier": len(next_frontier), "requested_references": len(pending), "fetched_references": len(fetched)})
        print(f"depth {depth + 1}: fetched {len(fetched)} works, graph now {len(node_by_id)} nodes ({sum(1 for n in node_by_id.values() if n.get('in_window'))} in-window)", file=sys.stderr)
        frontier = next_frontier
        if sum(1 for node in node_by_id.values() if node.get("in_window")) >= args.target_nodes:
            break

    edges: list[dict[str, Any]] = []
    for source_id, target_id in sorted(edge_pairs):
        if source_id not in node_by_id or target_id not in node_by_id:
            continue
        pair = (source_id, target_id)
        edges.append(
            {
                "source": source_id,
                "target": target_id,
                "relation": "cites",
                "source_field": "referenced_works",
                "source_system": "openalex",
                "expansion_depth": edge_depth.get(pair, 1),
                "provenance": {"provider": "OpenAlex", "api": API_ROOT, "citation_field": "referenced_works"},
            }
        )

    generated_at = datetime.now(timezone.utc).isoformat()
    in_window_count = sum(1 for node in node_by_id.values() if node.get("in_window"))
    external_count = sum(1 for node in node_by_id.values() if not node.get("in_window"))
    return {
        "schema_version": "0.2",
        "generated_at": generated_at,
        "meta": {
            "title": "AI Research Graph recursive citation expansion",
            "description": "Recent AI/CS seed papers expanded through OpenAlex referenced_works.",
            "generated_at": generated_at,
            "window": {"from": args.from_date, "to": args.to_date},
            "seed_window": {"from": args.seed_from_date, "to": args.to_date},
            "target_in_window_nodes": args.target_nodes,
        },
        "provenance": {
            "provider": "OpenAlex",
            "api": API_ROOT,
            "works_endpoint": f"{API_ROOT}/works",
            "citation_field": "referenced_works",
            "edge_semantics": "source work cites target work",
            "expansion": "recent seed search -> referenced_works -> referenced_works",
            "documentation": "https://docs.openalex.org/",
            "no_inferred_edges": True,
        },
        "query": {
            "seed_queries": queries,
            "seed_pages": args.seed_pages,
            "seed_per_page": args.seed_per_page,
            "seed_limit": args.seed_limit,
            "max_depth": args.max_depth,
            "max_references_per_node": args.max_references_per_node,
            "include_out_of_window": args.include_out_of_window,
        },
        "stats": {
            "nodes": len(node_by_id),
            "in_window_nodes": in_window_count,
            "external_nodes": external_count,
            "edges": len(edges),
            "seed_nodes": sum(1 for node in node_by_id.values() if node.get("is_seed")),
            "depths": dict(Counter(str(node.get("graph_depth")) for node in node_by_id.values())),
            "layers": layer_stats,
        },
        "nodes": list(node_by_id.values()),
        "edges": edges,
    }


def main() -> int:
    args = parse_args()
    if args.target_nodes < 1:
        raise SystemExit("--target-nodes must be positive")
    if args.from_date > args.to_date:
        raise SystemExit("--from-date must be <= --to-date")
    graph = build_graph(args)
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {output} ({graph['stats']['nodes']} nodes, {graph['stats']['in_window_nodes']} in-window, {graph['stats']['edges']} citation edges)")
    if graph["stats"]["in_window_nodes"] < args.target_nodes:
        print(f"warning: target not reached; got {graph['stats']['in_window_nodes']} in-window nodes", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
