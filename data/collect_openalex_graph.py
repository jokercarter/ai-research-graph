#!/usr/bin/env python3
"""Collect a reproducible AI/CS citation graph from the OpenAlex API.

Examples:
  python data/collect_openalex_graph.py --seed-arxiv 1706.03762,1810.04805 --out data/sample_graph.json
  python data/collect_openalex_graph.py --search "artificial intelligence" --from-year 2016 --to-year 2026 --pages 5 --out data/ai_graph.json

The graph contains only citation edges that are present in OpenAlex's
``referenced_works`` field. It never invents semantic or citation relations.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_ROOT = "https://api.openalex.org"
DEFAULT_UA = "AI-Research-Graph/0.1 (mailto:research@example.com)"
DEFAULT_SEEDS = [
    "1706.03762",  # Attention Is All You Need
    "1810.04805",  # BERT
    "2005.14165",  # GPT-3
    "2103.00020",  # CLIP
    "2102.12092",  # DALL-E
    "2106.09685",  # LoRA
    "2203.02155",  # InstructGPT
    "2302.13971",  # LLaMA
    "2010.11929",  # ViT
    "2006.11239",  # DDPM
    "2005.11401",  # RAG
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--search", help="OpenAlex full-text search query")
    parser.add_argument("--seed-arxiv", help="Comma-separated arXiv IDs to fetch exactly")
    parser.add_argument("--from-year", type=int, default=2016)
    parser.add_argument("--to-year", type=int, default=2026)
    parser.add_argument("--pages", type=int, default=1, help="Maximum pages for --search")
    parser.add_argument("--per-page", type=int, default=50, help="OpenAlex page size (1-200)")
    parser.add_argument("--out", default="data/openalex_graph.json")
    parser.add_argument("--mailto", default=os.getenv("OPENALEX_MAILTO", "research@example.com"))
    parser.add_argument("--include-external", action="store_true", help="Add one-hop references as lightweight external nodes")
    return parser.parse_args()


class OpenAlexClient:
    def __init__(self, mailto: str, api_key: str | None = None, user_agent: str = DEFAULT_UA) -> None:
        self.mailto = mailto
        self.api_key = api_key
        self.user_agent = user_agent

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        query = dict(params or {})
        query.setdefault("mailto", self.mailto)
        if self.api_key:
            query["api_key"] = self.api_key
        url = f"{API_ROOT}/{path.lstrip('/')}"
        if query:
            url += "?" + urlencode(query, doseq=True)
        request = Request(url, headers={"User-Agent": self.user_agent, "Accept": "application/json"})
        last_error: Exception | None = None
        for attempt in range(4):
            try:
                with urlopen(request, timeout=45) as response:
                    return json.load(response)
            except (HTTPError, URLError, TimeoutError) as exc:
                last_error = exc
                if isinstance(exc, HTTPError) and exc.code not in (429, 500, 502, 503, 504):
                    raise
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"OpenAlex request failed after retries: {url}: {last_error}")

    def fetch_arxiv(self, arxiv_id: str) -> dict[str, Any]:
        doi = f"10.48550/arXiv.{arxiv_id.strip()}"
        return self.get(f"works/https://doi.org/{doi}")

    def search(self, query: str, from_year: int, to_year: int, pages: int, per_page: int) -> list[dict[str, Any]]:
        per_page = max(1, min(200, per_page))
        start_date = f"{from_year:04d}-01-01"
        end_date = f"{to_year:04d}-12-31"
        works: list[dict[str, Any]] = []
        cursor = "*"
        for _ in range(max(1, pages)):
            payload = self.get(
                "works",
                {
                    "search": query,
                    "filter": f"from_publication_date:{start_date},to_publication_date:{end_date}",
                    "per-page": per_page,
                    "cursor": cursor,
                    "sort": "cited_by_count:desc",
                },
            )
            batch = payload.get("results", [])
            works.extend(batch)
            cursor = payload.get("meta", {}).get("next_cursor")
            if not batch or not cursor:
                break
        return works


def compact_author(author: dict[str, Any]) -> dict[str, Any]:
    person = author.get("author") or {}
    return {"id": person.get("id"), "name": person.get("display_name")}


def normalise_work(work: dict[str, Any], *, external: bool = False) -> dict[str, Any]:
    ids = work.get("ids") or {}
    primary = work.get("primary_location") or {}
    source = primary.get("source") or {}
    doi = work.get("doi") or ids.get("doi")
    landing = primary.get("landing_page_url")
    arxiv_id = None
    for candidate in (doi, landing):
        if not candidate:
            continue
        lower = str(candidate).lower()
        marker = "arxiv."
        if marker in lower:
            arxiv_id = str(candidate)[lower.index(marker) + len(marker) :].split("/", 1)[0].split("?", 1)[0]
            break
        if "arxiv.org/abs/" in lower:
            arxiv_id = str(candidate).split("arxiv.org/abs/", 1)[1].split("/", 1)[0].split("?", 1)[0]
            break
    topics = []
    for topic in (work.get("topics") or [])[:5]:
        if topic:
            topics.append({"id": topic.get("id"), "display_name": topic.get("display_name"), "score": topic.get("score")})
    return {
        "id": work.get("id"),
        "openalex_id": work.get("id"),
        "title": work.get("display_name") or work.get("title") or "Untitled work",
        "publication_year": work.get("publication_year"),
        "publication_date": work.get("publication_date"),
        "type": work.get("type"),
        "doi": doi,
        "arxiv_id": arxiv_id,
        "landing_page_url": landing,
        "open_access_url": (work.get("open_access") or {}).get("oa_url"),
        "authors": [compact_author(a) for a in (work.get("authorships") or [])[:8]],
        "primary_topic": topics[0] if topics else None,
        "topics": topics,
        "cited_by_count": work.get("cited_by_count", 0),
        "referenced_works_count": len(work.get("referenced_works") or []),
        "is_external_reference": external,
        "source": "openalex",
    }


def build_graph(works: Iterable[dict[str, Any]], *, include_external: bool = False, client: OpenAlexClient | None = None) -> dict[str, Any]:
    selected = list(works)
    node_by_id: dict[str, dict[str, Any]] = {}
    for work in selected:
        if work.get("id"):
            node_by_id[work["id"]] = normalise_work(work)

    # If requested, hydrate one-hop references. This is bounded to references of
    # the selected works and preserves them as external nodes.
    if include_external and client:
        missing: list[str] = []
        for work in selected:
            for ref in (work.get("referenced_works") or []):
                if ref not in node_by_id:
                    missing.append(ref)
        # OpenAlex accepts comma-separated OpenAlex IDs in filters. Keep this
        # bounded so a broad search cannot accidentally trigger a huge crawl.
        for start in range(0, min(len(missing), 200), 50):
            chunk = missing[start : start + 50]
            filt = "|".join(x.rsplit("/", 1)[-1] for x in chunk)
            try:
                payload = client.get("works", {"filter": f"openalex:{filt}", "per-page": len(chunk)})
            except Exception:
                continue
            for work in payload.get("results", []):
                if work.get("id") and work["id"] not in node_by_id:
                    node_by_id[work["id"]] = normalise_work(work, external=True)

    edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for work in selected:
        source = work.get("id")
        if not source:
            continue
        for target in work.get("referenced_works") or []:
            if target not in node_by_id:
                continue
            pair = (source, target)
            if pair in seen:
                continue
            seen.add(pair)
            edges.append({"source": source, "target": target, "relation": "cites", "source_field": "referenced_works", "source_system": "openalex"})

    generated_at = datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": "0.1",
        "generated_at": generated_at,
        "meta": {
            "title": "AI Research Graph sample",
            "description": "Real OpenAlex works and citation edges for a small, inspectable graph slice.",
            "generated_at": generated_at,
            "window": "2016-11-01 to 2026-10-31 (roadmap target; sample is a small slice)",
        },
        "provenance": {
            "provider": "OpenAlex",
            "api": API_ROOT,
            "works_endpoint": f"{API_ROOT}/works",
            "citation_field": "referenced_works",
            "edge_semantics": "source work cites target work",
            "documentation": "https://docs.openalex.org/",
        },
        "source": {"provider": "OpenAlex", "api": API_ROOT, "citation_field": "referenced_works"},
        "scope": {"from_year": min((w.get("publication_year") or 9999 for w in selected), default=None), "to_year": max((w.get("publication_year") or 0 for w in selected), default=None)},
        "stats": {"nodes": len(node_by_id), "edges": len(edges), "external_nodes": sum(1 for n in node_by_id.values() if n.get("is_external_reference"))},
        "nodes": list(node_by_id.values()),
        "edges": edges,
    }


def main() -> int:
    args = parse_args()
    if not args.search and not args.seed_arxiv:
        args.seed_arxiv = ",".join(DEFAULT_SEEDS)
    client = OpenAlexClient(args.mailto, os.getenv("OPENALEX_API_KEY"))
    works: list[dict[str, Any]] = []
    query_meta: dict[str, Any] = {}
    if args.seed_arxiv:
        for arxiv_id in [x.strip() for x in args.seed_arxiv.split(",") if x.strip()]:
            try:
                works.append(client.fetch_arxiv(arxiv_id))
            except Exception as exc:
                print(f"warning: unable to fetch arXiv:{arxiv_id}: {exc}", file=sys.stderr)
        query_meta["seed_arxiv"] = [x.strip() for x in args.seed_arxiv.split(",") if x.strip()]
    if args.search:
        works.extend(client.search(args.search, args.from_year, args.to_year, args.pages, args.per_page))
        query_meta.update({"search": args.search, "from_year": args.from_year, "to_year": args.to_year, "pages": args.pages, "per_page": args.per_page})

    # Stable de-duplication by OpenAlex work ID.
    unique: dict[str, dict[str, Any]] = {}
    for work in works:
        if work.get("id"):
            unique[work["id"]] = work
    graph = build_graph(unique.values(), include_external=args.include_external, client=client)
    graph["query"] = query_meta
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {output} ({graph['stats']['nodes']} nodes, {graph['stats']['edges']} citation edges)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
