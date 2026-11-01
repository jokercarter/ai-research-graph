#!/usr/bin/env python3
"""Evaluate the local retrieval fallback against a small auditable query set.

The default labels are term based because the repository does not yet contain
human relevance judgments. The report names this explicitly and supports
adding expected_ids later for Recall@K and precision style measurements.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import re
from pathlib import Path
from typing import Any


ALIASES = {
    "视觉语言模型": ["vision language", "visual language", "multimodal", "cross modal"],
    "对齐方法": ["alignment", "align", "grounding", "contrastive"],
    "联邦学习": ["federated learning", "federated"],
    "隐私": ["privacy", "private", "differential privacy", "secure"],
    "通信效率": ["communication efficiency", "communication efficient", "communication", "bandwidth"],
    "图神经网络": ["graph neural", "graph convolution", "gnn"],
    "节点分类": ["node classification", "node prediction"],
    "自然语言处理": ["natural language", "nlp", "language processing"],
    "预训练语言模型": ["pretrain", "language model", "transformer"],
}


def load_json(path: Path) -> dict[str, Any]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def load_inputs(paths: list[Path]) -> list[dict[str, Any]]:
    nodes: dict[str, dict[str, Any]] = {}
    for path in paths:
        snapshot = load_json(path)
        for node in snapshot.get("nodes", []):
            node_id = str(node.get("id") or node.get("openalex_id") or "")
            if node_id:
                nodes[node_id] = node
    return list(nodes.values())


def normalize(value: Any) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", str(value or "").lower())).strip()


def terms_for(query: str) -> list[str]:
    terms = re.findall(r"[a-z0-9][a-z0-9+.#-]*", query.lower())
    for key, aliases in ALIASES.items():
        if key in query:
            terms.extend(aliases)
    chinese = [char for char in query if "\u4e00" <= char <= "\u9fff"]
    terms.extend("".join(chinese[i : i + 2]) for i in range(max(0, len(chinese) - 1)))
    return sorted({normalize(term) for term in terms if len(normalize(term)) >= 2})


def score(node: dict[str, Any], terms: list[str]) -> tuple[float, list[str]]:
    title = normalize(node.get("title"))
    text = normalize(" ".join([
        str(node.get("title") or ""),
        str(node.get("abstract") or ""),
        str(node.get("venue") or ""),
        " ".join(str(item) for item in node.get("topics") or []),
        " ".join(str(item.get("name") if isinstance(item, dict) else item) for item in node.get("authors") or []),
    ]))
    hits = [term for term in terms if term in text]
    title_hits = sum(1 for term in hits if term in title)
    citations = math.log1p(float(node.get("cited_by_count") or node.get("citations_count") or 0))
    year = float(node.get("publication_year") or node.get("year") or 0)
    return title_hits * 10 + len(hits) * 2 + citations * 0.22 + max(0, year - 2015) * 0.05, hits


def evaluate(nodes: list[dict[str, Any]], queries: list[dict[str, Any]], top_k: int) -> dict[str, Any]:
    reports = []
    for query in queries:
        terms = terms_for(str(query.get("query") or ""))
        ranked = []
        for node in nodes:
            value, hits = score(node, terms)
            ranked.append((value, node, hits))
        ranked.sort(key=lambda item: (item[0], int(item[1].get("cited_by_count") or item[1].get("citations_count") or 0)), reverse=True)
        selected = ranked[:top_k]
        expected = [normalize(term) for term in query.get("expected_terms") or []]
        top_text = [normalize(" ".join([str(item[1].get("title") or ""), str(item[1].get("abstract") or ""), " ".join(map(str, item[1].get("topics") or []))])) for item in selected]
        covered_terms = [term for term in expected if any(term in text for text in top_text)]
        hit_rate = sum(bool(item[2]) for item in selected) / max(1, len(selected))
        expected_id_set = set(str(item) for item in query.get("expected_ids") or [])
        returned_ids = [str(item[1].get("id")) for item in selected]
        reports.append({
            "id": query.get("id"),
            "query": query.get("query"),
            "terms": terms,
            "top_k": [{"id": str(node.get("id")), "title": node.get("title"), "score": round(value, 4), "matched_terms": hits} for value, node, hits in selected],
            "metrics": {
                "term_coverage": round(len(covered_terms) / max(1, len(expected)), 4),
                "top_k_hit_rate": round(hit_rate, 4),
                "covered_terms": covered_terms,
                "expected_terms": expected,
                "recall_at_k": round(len(expected_id_set.intersection(returned_ids)) / max(1, len(expected_id_set)), 4) if expected_id_set else None,
            },
        })
    return {
        "schema_version": "retrieval-eval-v1",
        "label_type": "term_heuristic_with_optional_expected_ids",
        "nodes": len(nodes),
        "top_k": top_k,
        "queries": reports,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", type=Path, help="Snapshot JSON/JSON.GZ; repeat for split snapshots")
    parser.add_argument("--queries", type=Path, default=Path("data/retrieval_eval_queries.json"))
    parser.add_argument("--output", type=Path, default=Path("outputs/retrieval-eval.json"))
    parser.add_argument("--top-k", type=int, default=20)
    args = parser.parse_args()
    input_paths = args.input or [Path("data/ai_cs_graph_200000.part1.json.gz"), Path("data/ai_cs_graph_200000.part2.json.gz")]
    result = evaluate(load_inputs(input_paths), json.loads(args.queries.read_text(encoding="utf-8")), max(1, args.top_k))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    averages = [query["metrics"]["term_coverage"] for query in result["queries"]]
    print(f"wrote {args.output} ({len(result['queries'])} queries, mean term coverage {sum(averages) / max(1, len(averages)):.3f})")


if __name__ == "__main__":
    main()
