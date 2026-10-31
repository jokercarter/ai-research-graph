#!/usr/bin/env python3
"""Union recursive, catalogue, and optional topic-slice citation graphs.

The recursive snapshot provides a connected relevance-first view.  The
catalogue slice adds a deterministic sample from every year in the exact
Computer Science field/type window and carries its OpenAlex denominator.  The
union keeps only real OpenAlex ``referenced_works`` edges and is explicit
that it is a bounded sample rather than an exhaustive import.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recursive", default="data/recursive_graph_10000.json")
    parser.add_argument("--catalog", default="data/cs_field_catalog_2200.json")
    parser.add_argument("--topic-slice", action="append", default=[], help="Optional topic-slice JSON; repeatable")
    parser.add_argument("--out", default="data/ai_cs_graph_coverage_9500.json")
    return parser.parse_args()


def read_json(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> int:
    args = parse_args()
    recursive = read_json(args.recursive)
    catalog = read_json(args.catalog)
    topic_payloads = [(path, read_json(path)) for path in args.topic_slice]

    nodes: dict[str, dict[str, Any]] = {}
    for raw in recursive.get("nodes", []):
        if raw.get("id"):
            nodes[str(raw["id"])] = dict(raw)

    catalog_ids: set[str] = set()
    new_catalog_ids: set[str] = set()
    for raw in catalog.get("nodes", []):
        work_id = raw.get("id")
        if not work_id:
            continue
        work_id = str(work_id)
        catalog_ids.add(work_id)
        if work_id in nodes:
            nodes[work_id]["catalog_membership"] = True
            nodes[work_id]["catalog_seed"] = True
            nodes[work_id].setdefault("catalog_slices", [])
            slice_meta = raw.get("catalog_slice")
            if slice_meta and slice_meta not in nodes[work_id]["catalog_slices"]:
                nodes[work_id]["catalog_slices"].append(slice_meta)
            continue
        node = dict(raw)
        node.update(
            {
                "catalog_membership": True,
                "catalog_seed": True,
                "graph_depth": 0,
                "is_seed": False,
                "in_window": True,
                "is_external_reference": False,
            }
        )
        nodes[work_id] = node
        new_catalog_ids.add(work_id)

    topic_slice_ids: set[str] = set()
    new_topic_slice_ids: set[str] = set()
    for path, topic_payload in topic_payloads:
        for raw in topic_payload.get("nodes", []):
            work_id = raw.get("id")
            if not work_id:
                continue
            work_id = str(work_id)
            topic_slice_ids.add(work_id)
            if work_id in nodes:
                nodes[work_id]["topic_slice_membership"] = True
                nodes[work_id]["topic_slice_seed"] = True
                nodes[work_id].setdefault("topic_slice_sources", [])
                if path not in nodes[work_id]["topic_slice_sources"]:
                    nodes[work_id]["topic_slice_sources"].append(path)
                continue
            node = dict(raw)
            node.update(
                {
                    "topic_slice_membership": True,
                    "topic_slice_seed": True,
                    "topic_slice_sources": [path],
                    "graph_depth": 0,
                    "is_seed": False,
                    "in_window": True,
                    "is_external_reference": False,
                }
            )
            nodes[work_id] = node
            new_topic_slice_ids.add(work_id)

    edge_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    edge_sources: list[tuple[str, dict[str, Any]]] = [("recursive", recursive), ("catalog", catalog)]
    edge_sources.extend((f"topic_slice:{path}", payload) for path, payload in topic_payloads)
    for source_name, payload in edge_sources:
        for raw in payload.get("edges", []):
            source = str(raw.get("source"))
            target = str(raw.get("target"))
            if source not in nodes or target not in nodes or source == target:
                continue
            key = (source, target)
            if key in edge_by_key:
                edge_by_key[key].setdefault("provenance_sources", []).append(source_name)
                continue
            edge = dict(raw)
            edge.update(
                {
                    "source": source,
                    "target": target,
                    "relation": edge.get("relation", "cites"),
                    "source_field": edge.get("source_field", "referenced_works"),
                    "source_system": edge.get("source_system", "openalex"),
                    "provenance_sources": [source_name],
                }
            )
            edge_by_key[key] = edge

    now = datetime.now(timezone.utc).isoformat()
    catalog_meta = catalog.get("meta") or {}
    catalog_coverage = catalog.get("coverage") or {}
    recursive_stats = recursive.get("stats") or {}
    catalog_expected = catalog_meta.get("expected_count") or catalog_coverage.get("expected_count")
    core_types = {"article", "conference-paper", "preprint", "review"}
    type_counts = Counter(node.get("type") for node in nodes.values())
    depth_counts = Counter(str(node.get("graph_depth")) for node in nodes.values())
    payload = {
        "schema_version": "0.4",
        "generated_at": now,
        "meta": {
            "title": "AI and Computer Science citation graph with coverage sample",
            "description": "Union of a two-hop OpenAlex citation traversal, a year-balanced Computer Science catalogue sample, and optional topic slices.",
            "window": {"from": "2016-11-01", "to": "2026-10-31"},
            "is_exhaustive": False,
            "core_publication_types": sorted(core_types),
        },
        "provenance": {
            "provider": "OpenAlex",
            "api": "https://api.openalex.org",
            "works_endpoint": "https://api.openalex.org/works",
            "citation_field": "referenced_works",
            "edge_semantics": "source work cites target work",
            "expansion": "recent keyword seeds -> referenced_works -> referenced_works, unioned with yearly Computer Science catalogue slices and optional topic slices",
            "documentation": "https://docs.openalex.org/",
            "no_inferred_edges": True,
        },
        "coverage": {
            "catalog_filter": catalog.get("provenance", {}).get("catalog_filter_contract"),
            "catalog_expected_count": catalog_expected,
            "catalog_sample_count": len(catalog_ids),
            "catalog_new_nodes_in_union": len(new_catalog_ids),
            "catalog_sample_fraction": (len(catalog_ids) / catalog_expected) if catalog_expected else None,
            "catalog_manifest": catalog_coverage.get("slices", []),
            "topic_slice_sources": [path for path, _ in topic_payloads],
            "is_exhaustive": False,
        },
        "query": {
            "recursive_snapshot": args.recursive,
            "catalog_snapshot": args.catalog,
            "topic_slice_snapshots": args.topic_slice,
            "recursive_stats": recursive_stats,
            "catalog_stats": catalog.get("stats", {}),
        },
        "stats": {
            "nodes": len(nodes),
            "in_window_nodes": sum(1 for node in nodes.values() if node.get("in_window", True)),
            "external_nodes": sum(1 for node in nodes.values() if node.get("is_external_reference")),
            "edges": len(edge_by_key),
            "seed_nodes": sum(1 for node in nodes.values() if node.get("is_seed")),
            "catalog_nodes": len(catalog_ids),
            "catalog_new_nodes": len(new_catalog_ids),
            "depths": dict(depth_counts),
            "types": dict(type_counts),
            "recursive_nodes": len(recursive.get("nodes", [])),
            "recursive_edges": len(recursive.get("edges", [])),
            "catalog_edges": len(catalog.get("edges", [])),
            "topic_slice_nodes": len(topic_slice_ids),
            "topic_slice_new_nodes": len(new_topic_slice_ids),
            "topic_slice_edges": sum(len(payload.get("edges", [])) for _, payload in topic_payloads),
        },
        "nodes": list(nodes.values()),
        "edges": list(edge_by_key.values()),
    }
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(
        f"wrote {output} ({payload['stats']['nodes']} nodes, "
        f"{payload['stats']['edges']} edges; catalog {len(catalog_ids)} / {catalog_expected}; "
        f"topic slices {len(topic_slice_ids)})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
