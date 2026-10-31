#!/usr/bin/env python3
"""Collect one bounded OpenAlex slice per AI/CS topic.

The default topic list intentionally uses one 200-work page per query. This
keeps an unauthenticated OpenAlex run resumable under shared budgets; add an
API key and increase --pages when the full catalogue is available.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from collect_openalex_graph import OpenAlexClient, build_graph

DEFAULT_TOPICS = [
    "artificial intelligence",
    "machine learning",
    "deep learning",
    "computer vision",
    "natural language processing",
    "robotics artificial intelligence",
    "reinforcement learning",
    "generative models",
    "computer science machine learning",
    "data science artificial intelligence",
    "information retrieval natural language processing",
    "computer graphics computer vision",
    "software engineering machine learning",
    "neural networks deep learning",
]


def slug(text: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return value[:60] or "topic"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topics-file", type=Path)
    parser.add_argument("--from-year", type=int, default=2016)
    parser.add_argument("--to-year", type=int, default=2026)
    parser.add_argument("--pages", type=int, default=1)
    parser.add_argument("--per-page", type=int, default=200)
    parser.add_argument("--out-dir", type=Path, default=Path("data"))
    parser.add_argument("--include-external", action="store_true")
    args = parser.parse_args()
    if args.topics_file:
        topics = [line.strip() for line in args.topics_file.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]
    else:
        topics = DEFAULT_TOPICS
    args.out_dir.mkdir(parents=True, exist_ok=True)
    client = OpenAlexClient(os.getenv("OPENALEX_MAILTO", "research@example.com"), os.getenv("OPENALEX_API_KEY"))
    failed = 0
    for topic in topics:
        output = args.out_dir / f"{slug(topic)}_graph_{args.pages * args.per_page}.json"
        try:
            works = client.search(topic, args.from_year, args.to_year, args.pages, args.per_page)
            graph = build_graph(works, include_external=args.include_external, client=client)
            graph["query"] = {"search": topic, "from_year": args.from_year, "to_year": args.to_year, "pages": args.pages, "per_page": args.per_page}
            output.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")
            print(f"{topic}: {graph['stats']['nodes']} nodes, {graph['stats']['edges']} edges -> {output}")
        except Exception as exc:
            failed += 1
            print(f"warning: {topic}: {exc}", file=sys.stderr)
    return 1 if failed == len(topics) else 0


if __name__ == "__main__":
    raise SystemExit(main())
