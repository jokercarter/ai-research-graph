#!/usr/bin/env python3
"""Collect a bounded citation graph from the Semantic Scholar Academic Graph API.

This is a provider-specific supplement for periods when the OpenAlex API budget
is exhausted. It keeps Semantic Scholar IDs as the canonical IDs in the output
(`s2:<paperId>`) and only emits citation edges returned by the API's
``references`` endpoint. It does not merge or infer edges from title similarity.

Examples:
  python data/collect_semanticscholar_graph.py \
    --query "machine learning" --year 2016-2026 --seed-limit 100 \
    --max-references-per-seed 100 --out data/s2_ml_graph.json

  python data/collect_semanticscholar_graph.py \
    --seed-id DOI:10.48550/arXiv.1706.03762 --max-references-per-seed 250

An API key is optional for small runs. Set ``SEMANTIC_SCHOLAR_API_KEY`` for a
dedicated quota and keep ``--sleep-seconds`` >= 1 for a polite, resumable crawl.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import requests

API_ROOT = "https://api.semanticscholar.org/graph/v1"
DEFAULT_FIELDS = (
    "paperId,corpusId,title,year,publicationDate,authors,externalIds,"
    "openAccessPdf,url,fieldsOfStudy,publicationTypes,citationCount,referenceCount"
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--query", action="append", help="Semantic Scholar bulk-search query (repeatable)")
    p.add_argument("--year", default="2016-2026", help="Search year filter, e.g. 2016-2026 or 2020-")
    p.add_argument("--seed-id", action="append", help="Exact S2 paper ID, DOI:..., or ARXIV:... (repeatable)")
    p.add_argument("--seed-limit", type=int, default=100, help="Maximum unique search seeds")
    p.add_argument("--max-references-per-seed", type=int, default=200, help="Reference edges read for each seed")
    p.add_argument("--max-reference-nodes", type=int, default=5000, help="Maximum reference papers hydrated")
    p.add_argument("--page-size", type=int, default=100, help="Bulk-search page size (S2 currently caps this)")
    p.add_argument("--sleep-seconds", type=float, default=1.0, help="Delay between API calls; use >=1 with an API key")
    p.add_argument("--timeout", type=float, default=45.0)
    p.add_argument("--out", default="data/semanticscholar_graph.json")
    p.add_argument("--api-key", default=os.getenv("SEMANTIC_SCHOLAR_API_KEY"))
    return p.parse_args()


class S2Client:
    def __init__(self, api_key: str | None, sleep_seconds: float, timeout: float) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "AI-Research-Graph/0.1", "Accept": "application/json"})
        if api_key:
            self.session.headers["x-api-key"] = api_key
        self.sleep_seconds = max(0.0, sleep_seconds)
        self.timeout = timeout

    def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if self.sleep_seconds:
            time.sleep(self.sleep_seconds)
        url = f"{API_ROOT}/{path.lstrip('/')}"
        last: Exception | None = None
        for attempt in range(5):
            try:
                response = self.session.request(method, url, timeout=self.timeout, **kwargs)
                if response.status_code == 429 or response.status_code >= 500:
                    retry_after = response.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after and retry_after.isdigit() else 2.0 * (attempt + 1)
                    time.sleep(delay)
                    continue
                response.raise_for_status()
                payload = response.json()
                return payload if isinstance(payload, dict) else {"data": payload}
            except (requests.RequestException, ValueError) as exc:
                last = exc
                if attempt < 4:
                    time.sleep(2.0 * (attempt + 1))
        raise RuntimeError(f"Semantic Scholar request failed: {url}: {last}")

    def bulk_search(self, query: str, year: str, limit: int, page_size: int) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        token: str | None = None
        while len(found) < limit:
            params: dict[str, Any] = {
                "query": query,
                "year": year,
                "fields": DEFAULT_FIELDS,
                "limit": max(1, min(page_size, limit - len(found))),
            }
            if token:
                params["token"] = token
            payload = self.request("GET", "paper/search/bulk", params=params)
            found.extend(payload.get("data") or [])
            token = payload.get("token")
            if not token or not payload.get("data"):
                break
        return found[:limit]

    def paper(self, paper_id: str) -> dict[str, Any] | None:
        try:
            payload = self.request("GET", f"paper/{paper_id}", params={"fields": DEFAULT_FIELDS})
        except Exception as exc:
            print(f"warning: paper {paper_id} failed: {exc}", file=sys.stderr)
            return None
        return payload if payload.get("paperId") else None

    def references(self, paper_id: str, limit: int) -> list[dict[str, Any]]:
        try:
            payload = self.request(
                "GET",
                f"paper/{paper_id}/references",
                params={"fields": "citedPaper.paperId,citedPaper.corpusId,citedPaper.title,citedPaper.year", "limit": max(1, min(limit, 1000))},
            )
        except Exception as exc:
            print(f"warning: references for {paper_id} failed: {exc}", file=sys.stderr)
            return []
        return payload.get("data") or []

    def batch_papers(self, paper_ids: Iterable[str]) -> list[dict[str, Any]]:
        ids = list(dict.fromkeys(x for x in paper_ids if x))
        out: list[dict[str, Any]] = []
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            try:
                payload = self.request("POST", "paper/batch", params={"fields": DEFAULT_FIELDS}, json={"ids": chunk})
                out.extend(x for x in payload.get("data") or [] if x and x.get("paperId"))
            except Exception as exc:
                print(f"warning: paper batch {start}:{start + len(chunk)} failed: {exc}", file=sys.stderr)
        return out


def s2_id(value: str) -> str:
    return f"s2:{value}"


def arxiv_from_external_ids(ids: dict[str, Any] | None) -> str | None:
    if not ids:
        return None
    value = ids.get("ArXiv") or ids.get("arXiv") or ids.get("ARXIV")
    if value:
        return str(value)
    return None


def normalise_paper(paper: dict[str, Any], *, external: bool = False) -> dict[str, Any]:
    ext = paper.get("externalIds") or {}
    doi = ext.get("DOI") or ext.get("doi")
    return {
        "id": s2_id(str(paper.get("paperId"))),
        "semanticscholar_id": paper.get("paperId"),
        "corpus_id": paper.get("corpusId"),
        "title": paper.get("title") or "Untitled work",
        "publication_year": paper.get("year"),
        "publication_date": paper.get("publicationDate"),
        "doi": doi,
        "arxiv_id": arxiv_from_external_ids(ext),
        "url": paper.get("url"),
        "open_access_url": (paper.get("openAccessPdf") or {}).get("url"),
        "authors": [{"id": a.get("authorId"), "name": a.get("name")} for a in (paper.get("authors") or [])[:8]],
        "fields_of_study": paper.get("fieldsOfStudy") or [],
        "publication_types": paper.get("publicationTypes") or [],
        "cited_by_count": paper.get("citationCount", 0),
        "referenced_works_count": paper.get("referenceCount", 0),
        "is_external_reference": external,
        "source": "semanticscholar",
    }


def exact_seed_id(value: str) -> str:
    # API accepts DOI:<doi>, ARXIV:<id>, PMID:<id>, corpusId:<id>, or paperId.
    value = value.strip()
    if re.match(r"^[A-Za-z]+:", value):
        return value
    if value.lower().startswith("10."):
        return "DOI:" + value
    return value


def build_graph(args: argparse.Namespace) -> dict[str, Any]:
    client = S2Client(args.api_key, args.sleep_seconds, args.timeout)
    seeds: dict[str, dict[str, Any]] = {}
    for query in args.query or []:
        try:
            batch = client.bulk_search(query, args.year, args.seed_limit, args.page_size)
        except Exception as exc:
            print(f"warning: bulk search {query!r} failed: {exc}", file=sys.stderr)
            batch = []
        for paper in batch:
            if paper.get("paperId"):
                seeds[str(paper["paperId"])] = paper
    for raw_id in args.seed_id or []:
        paper = client.paper(exact_seed_id(raw_id))
        if paper and paper.get("paperId"):
            seeds[str(paper["paperId"])] = paper
    seed_list = list(seeds.values())[: max(1, args.seed_limit)]
    if not seed_list:
        raise RuntimeError("No Semantic Scholar seeds resolved; check the query/ID, API key, or HTTP 429 throttling")
    raw_by_id = {str(p["paperId"]): p for p in seed_list}
    edges: set[tuple[str, str]] = set()
    reference_ids: list[str] = []
    for index, seed in enumerate(seed_list, 1):
        pid = str(seed["paperId"])
        refs = client.references(pid, args.max_references_per_seed)
        for row in refs[: args.max_references_per_seed]:
            cited = row.get("citedPaper") or {}
            target = cited.get("paperId")
            if not target or target == pid:
                continue
            edges.add((pid, str(target)))
            if target not in raw_by_id:
                reference_ids.append(str(target))
        print(f"seed {index}/{len(seed_list)} {pid}: {len(refs)} references", file=sys.stderr)
    reference_ids = list(dict.fromkeys(reference_ids))[: max(0, args.max_reference_nodes)]
    hydrated = client.batch_papers(reference_ids)
    for paper in hydrated:
        if paper.get("paperId"):
            raw_by_id[str(paper["paperId"])] = paper

    nodes = {pid: normalise_paper(paper, external=pid not in seeds) for pid, paper in raw_by_id.items()}
    output_edges = []
    for source, target in sorted(edges):
        if s2_id(source) not in {n["id"] for n in nodes.values()} or s2_id(target) not in {n["id"] for n in nodes.values()}:
            continue
        output_edges.append({
            "source": s2_id(source),
            "target": s2_id(target),
            "relation": "cites",
            "source_field": "references",
            "source_system": "semanticscholar",
            "provenance": {"provider": "Semantic Scholar", "api": API_ROOT, "citation_field": "references"},
        })
    generated_at = datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": "0.1",
        "generated_at": generated_at,
        "meta": {"title": "AI Research Graph Semantic Scholar supplement", "is_exhaustive": False, "window": args.year},
        "provenance": {"provider": "Semantic Scholar", "api": API_ROOT, "citation_field": "references", "edge_semantics": "source paper references target paper", "no_inferred_edges": True, "queries": args.query or [], "seed_ids": args.seed_id or []},
        "scope": {"year": args.year},
        "stats": {"nodes": len(nodes), "edges": len(output_edges), "seed_nodes": len(seed_list), "external_nodes": sum(1 for n in nodes.values() if n.get("is_external_reference"))},
        "query": {"queries": args.query or [], "year": args.year, "seed_limit": args.seed_limit, "max_references_per_seed": args.max_references_per_seed},
        "nodes": list(nodes.values()),
        "edges": output_edges,
    }


def main() -> int:
    args = parse_args()
    if not args.query and not args.seed_id:
        raise SystemExit("provide --query and/or --seed-id")
    try:
        graph = build_graph(args)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {output} ({graph['stats']['nodes']} nodes, {graph['stats']['edges']} citation edges)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
