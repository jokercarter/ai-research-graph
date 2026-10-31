#!/usr/bin/env python3
"""Merge OpenAlex graph slices and annotate a bounded citation traversal.

Each input slice is produced by collect_openalex_graph.py. Nodes are merged by
OpenAlex ID and edges are kept only once. The output remains honest about its
coverage: it is a union of public search slices, not a claim of exhaustive
coverage of every AI/CS work.
"""
from __future__ import annotations

import argparse
import glob
import json
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--inputs", nargs="+", help="JSON files or glob patterns")
    p.add_argument("--out", default="data/ai_cs_graph_expanded.json")
    p.add_argument("--seed-from-year", type=int, default=2023)
    return p.parse_args()


def expand_inputs(values: list[str]) -> list[Path]:
    found: list[Path] = []
    seen: set[Path] = set()
    for value in values:
        matches = [Path(value)] if Path(value).is_file() else [Path(x) for x in glob.glob(value)]
        for match in sorted(matches):
            if match.is_file() and match.suffix.lower() == ".json" and match not in seen:
                seen.add(match)
                found.append(match)
    if not found:
        raise SystemExit("No JSON inputs found")
    return found


def main() -> int:
    args = parse_args()
    files = expand_inputs(args.inputs or ["data/*_graph_*.json"])
    node_by_id: dict[str, dict[str, Any]] = {}
    node_queries: dict[str, set[str]] = {}
    edge_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    searches: list[dict[str, Any]] = []

    for path in files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        query = payload.get("query", {}).get("search") or path.stem
        query_meta = payload.get("query", {})
        searches.append({
            "search": query,
            "from_year": query_meta.get("from_year", 2016),
            "to_year": query_meta.get("to_year", 2026),
            "nodes": len(payload.get("nodes", [])),
        })
        for node in payload.get("nodes", []):
            node_id = node.get("id")
            if not node_id:
                continue
            node_by_id.setdefault(node_id, node)
            node_queries.setdefault(node_id, set()).add(query)
        for edge in payload.get("edges", []):
            source, target = str(edge.get("source")), str(edge.get("target"))
            if source and target and source != "None" and target != "None":
                edge_by_key.setdefault((source, target), {
                    **edge,
                    "source": source,
                    "target": target,
                    "relation": edge.get("relation", "cites"),
                    "source_field": edge.get("source_field", "referenced_works"),
                    "source_system": edge.get("source_system", "openalex"),
                })

    # Use recent AI/ML results as seeds, then follow source -> cited target.
    seed_queries = {"artificial intelligence", "machine learning"}
    seed_ids = {
        node_id for node_id, node in node_by_id.items()
        if (node.get("publication_year") or 0) >= args.seed_from_year
        and node_queries.get(node_id, set()) & seed_queries
    }
    adjacency: dict[str, list[str]] = {}
    for source, target in edge_by_key:
        adjacency.setdefault(source, []).append(target)
    depth: dict[str, int] = {node_id: 0 for node_id in seed_ids}
    queue = deque(seed_ids)
    while queue:
        source = queue.popleft()
        for target in adjacency.get(source, []):
            if target in node_by_id and target not in depth:
                depth[target] = depth[source] + 1
                queue.append(target)

    for node_id, node in node_by_id.items():
        node["discovery_queries"] = sorted(node_queries.get(node_id, set()))
        node["traversal_depth"] = depth.get(node_id)
        node["is_recent_seed"] = node_id in seed_ids

    now = datetime.now(timezone.utc).isoformat()
    edges = list(edge_by_key.values())
    payload = {
        "schema_version": "0.2",
        "meta": {
            "title": "AI Research Graph expanded OpenAlex snapshot",
            "description": "Merged public OpenAlex search slices across AI and computer-science topics; citation edges are retained only when both endpoint works are present.",
            "generated_at": now,
            "window": "2016-11-01 to 2026-10-31",
            "seed_strategy": f"recent ({args.seed_from_year}-2026) AI/ML works, then outgoing referenced_works traversal within the merged snapshot",
        },
        "provenance": {
            "provider": "OpenAlex",
            "api": "https://api.openalex.org",
            "citation_field": "referenced_works",
            "edge_semantics": "source work cites target work",
            "documentation": "https://docs.openalex.org/",
            "searches": searches,
        },
        "source": {"provider": "OpenAlex", "api": "https://api.openalex.org", "citation_field": "referenced_works"},
        "scope": {"from_year": 2016, "to_year": 2026},
        "stats": {
            "nodes": len(node_by_id),
            "edges": len(edges),
            "external_nodes": sum(1 for node in node_by_id.values() if node.get("is_external_reference")),
            "seed_nodes": len(seed_ids),
            "reachable_nodes": len(depth),
            "max_traversal_depth": max(depth.values(), default=0),
        },
        "query": {
            "merged_files": [str(path) for path in files],
            "searches": searches,
            "seed_rule": f"publication_year >= {args.seed_from_year} from artificial intelligence or machine learning slices",
        },
        "nodes": list(node_by_id.values()),
        "edges": edges,
    }
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {output} ({payload['stats']['nodes']} nodes, {payload['stats']['edges']} citation edges, max traversal depth {payload['stats']['max_traversal_depth']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
