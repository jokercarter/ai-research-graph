#!/usr/bin/env python3
"""Enrich the 200k graph snapshot with public paper metadata.

The graph itself is never inferred or rewritten. This script only merges
metadata returned by OpenAlex and, optionally, Semantic Scholar. Missing
abstracts remain empty so downstream UI code can show an explicit caveat.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


OPENALEX_URL = "https://api.openalex.org/works"
S2_URL = "https://api.semanticscholar.org/graph/v1/paper/batch"


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def load_inputs(paths: list[Path]) -> dict[str, Any]:
    """Load a single snapshot or the two hosted split snapshots."""
    snapshots = [load_json(path) for path in paths]
    if len(snapshots) == 1:
        return snapshots[0]
    nodes_by_id: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    for snapshot in snapshots:
        for node in snapshot.get("nodes", []):
            node_id = str(node.get("id") or node.get("openalex_id") or "")
            if node_id:
                nodes_by_id[node_id] = node
        edges.extend(snapshot.get("edges", []))
    unique_edges = {(str(edge.get("source")), str(edge.get("target"))): edge for edge in edges if edge.get("source") and edge.get("target")}
    merged = dict(snapshots[0])
    merged["nodes"] = list(nodes_by_id.values())
    merged["edges"] = list(unique_edges.values())
    merged.setdefault("stats", {})["nodes"] = len(merged["nodes"])
    merged.setdefault("stats", {})["edges"] = len(merged["edges"])
    merged.setdefault("provenance", {})["input_parts"] = [str(path) for path in paths]
    return merged


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", compresslevel=6) if path.suffix == ".gz" else open(path, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))


def request_json(url: str, *, method: str = "GET", payload: Any = None, headers: dict[str, str] | None = None, retries: int = 4) -> Any:
    request_headers = {"User-Agent": "AI-Research-Graph/1.0 (metadata enrichment)"}
    request_headers.update(headers or {})
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
            with urllib.request.urlopen(request, timeout=45) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            if attempt + 1 >= retries:
                raise RuntimeError(f"metadata request failed after {retries} attempts: {url}: {error}") from error
            retry_after = 0
            if isinstance(error, urllib.error.HTTPError):
                retry_after = int(error.headers.get("Retry-After", "0") or 0)
            time.sleep(max(retry_after, 2 ** attempt))
    raise RuntimeError("unreachable")


def openalex_key(value: Any) -> str:
    text = str(value or "")
    match = re.search(r"W\d+$", text, re.I)
    return match.group(0).upper() if match else ""


def normalize_doi(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"^https?://(dx\.)?doi\.org/", "", text)
    return text


def reconstruct_abstract(index: Any) -> str:
    if not isinstance(index, dict):
        return ""
    positions: list[tuple[int, str]] = []
    for word, offsets in index.items():
        if isinstance(offsets, list):
            positions.extend((int(offset), str(word)) for offset in offsets if isinstance(offset, int))
    return " ".join(word for _, word in sorted(positions))


def first_arxiv(value: Any) -> str:
    text = str(value or "")
    match = re.search(r"(?:arxiv:|arxiv\.org/(?:abs|pdf)/)([0-9]{4}\.[0-9]{4,5}(?:v\d+)?)", text, re.I)
    return match.group(1) if match else ""


def merge_openalex(node: dict[str, Any], record: dict[str, Any]) -> None:
    abstract = reconstruct_abstract(record.get("abstract_inverted_index"))
    if abstract:
        node["abstract"] = abstract
    doi = normalize_doi(record.get("doi"))
    if doi:
        node["doi"] = doi
    authors = [item.get("author", {}).get("display_name") for item in record.get("authorships", []) if item.get("author", {}).get("display_name")]
    if authors:
        node["authors"] = authors
    topics = [item.get("display_name") for item in record.get("topics", []) if item.get("display_name")]
    if not topics:
        topics = [item.get("display_name") for item in record.get("concepts", []) if item.get("display_name")]
    if topics:
        node["topics"] = topics[:12]
    source = record.get("primary_location", {}).get("source") or {}
    if source.get("display_name"):
        node["venue"] = source["display_name"]
    if record.get("cited_by_count") is not None:
        node["cited_by_count"] = int(record.get("cited_by_count") or 0)
    if record.get("publication_year"):
        node["publication_year"] = int(record["publication_year"])
    if record.get("publication_date"):
        node["publication_date"] = record["publication_date"]
    if record.get("type"):
        node["type"] = record["type"]
    location = record.get("best_oa_location") or record.get("primary_location") or {}
    node["url"] = location.get("landing_page_url") or location.get("pdf_url") or node.get("url") or record.get("id", "")
    node["openalex_id"] = record.get("id") or node.get("openalex_id")
    node["metadata_source"] = sorted(set((node.get("metadata_source") or []) + ["openalex"]))


def merge_semantic_scholar(node: dict[str, Any], record: dict[str, Any]) -> None:
    if not isinstance(record, dict):
        return
    if record.get("abstract") and not node.get("abstract"):
        node["abstract"] = record["abstract"]
    if record.get("title") and not node.get("title"):
        node["title"] = record["title"]
    authors = [item.get("name") for item in record.get("authors", []) if item.get("name")]
    if authors and not node.get("authors"):
        node["authors"] = authors
    if record.get("venue") and not node.get("venue"):
        node["venue"] = record["venue"]
    if record.get("year") and not node.get("publication_year"):
        node["publication_year"] = record["year"]
    if record.get("citationCount") is not None and not node.get("cited_by_count"):
        node["cited_by_count"] = int(record.get("citationCount") or 0)
    oa = record.get("openAccessPdf") or {}
    if oa.get("url") and not node.get("url"):
        node["url"] = oa["url"]
    node["semantic_scholar_id"] = record.get("paperId") or node.get("semantic_scholar_id")
    node["metadata_source"] = sorted(set((node.get("metadata_source") or []) + ["semantic_scholar"]))


def completeness(node: dict[str, Any]) -> float:
    fields = ["title", "abstract", "authors", "topics", "venue", "publication_year", "cited_by_count", "url"]
    present = 0
    for field in fields:
        value = node.get(field)
        if value not in (None, "", [], {}):
            present += 1
    return round(present / len(fields), 3)


def chunks(items: list[str], size: int):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def openalex_batch(ids: list[str], mailto: str = "") -> list[dict[str, Any]]:
    filters = "|".join(ids)
    params = {
        "filter": f"openalex_id:{filters}",
        "per-page": str(min(100, len(ids))),
        "select": "id,title,doi,authorships,topics,concepts,primary_location,best_oa_location,abstract_inverted_index,cited_by_count,publication_year,publication_date,type,ids",
    }
    if mailto:
        params["mailto"] = mailto
    response = request_json(f"{OPENALEX_URL}?{urllib.parse.urlencode(params)}")
    return response.get("results", []) if isinstance(response, dict) else []


def semantic_batch(ids: list[str], api_key: str = "") -> list[dict[str, Any]]:
    headers = {"content-type": "application/json"}
    if api_key:
        headers["x-api-key"] = api_key
    fields = "paperId,title,abstract,authors,year,venue,citationCount,openAccessPdf,externalIds"
    response = request_json(f"{S2_URL}?fields={urllib.parse.quote(fields)}", method="POST", payload={"ids": ids}, headers=headers)
    return [item for item in response if isinstance(item, dict)] if isinstance(response, list) else []


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", type=Path, help="Input snapshot; repeat for split snapshots")
    parser.add_argument("--output", type=Path, default=Path("data/ai_cs_graph_200000.enriched.json.gz"))
    parser.add_argument("--checkpoint", type=Path, default=Path("data/metadata_enrichment.checkpoint.json"))
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--max-nodes", type=int, default=0, help="Only enrich the first N pending nodes; 0 means all")
    parser.add_argument("--provider", choices=("openalex", "both"), default="both")
    parser.add_argument("--delay", type=float, default=0.15)
    parser.add_argument("--mailto", default=os.environ.get("OPENALEX_MAILTO", ""))
    args = parser.parse_args()

    input_paths = args.input or [
        Path("data/ai_cs_graph_200000.json.gz") if Path("data/ai_cs_graph_200000.json.gz").exists() else Path("data/ai_cs_graph_200000.part1.json.gz"),
    ]
    if args.input is None and input_paths[0].name.endswith("part1.json.gz"):
        part2 = input_paths[0].with_name(input_paths[0].name.replace("part1", "part2"))
        if part2.exists():
            input_paths.append(part2)
    snapshot = load_inputs(input_paths)
    nodes = snapshot.get("nodes", [])
    by_openalex = {openalex_key(node.get("openalex_id") or node.get("id")): node for node in nodes if openalex_key(node.get("openalex_id") or node.get("id"))}
    by_doi = {normalize_doi(node.get("doi")): node for node in nodes if normalize_doi(node.get("doi"))}
    checkpoint: dict[str, Any] = {}
    if args.checkpoint.exists():
        try:
            checkpoint = json.loads(args.checkpoint.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            checkpoint = {}
    done = set(checkpoint.get("done", []))
    pending = [key for key in by_openalex if key not in done]
    if args.max_nodes:
        pending = pending[:args.max_nodes]
    print(f"metadata enrichment: {len(nodes):,} nodes, {len(pending):,} pending")

    for batch in chunks(pending, max(1, min(args.batch_size, 100))):
        for record in openalex_batch(batch, args.mailto):
            key = openalex_key(record.get("id"))
            if key in by_openalex:
                merge_openalex(by_openalex[key], record)
        if args.provider == "both":
            s2_ids: list[str] = []
            s2_key_by_id: dict[str, str] = {}
            for key in batch:
                node = by_openalex[key]
                doi = normalize_doi(node.get("doi"))
                arxiv = first_arxiv(node.get("arxiv_id") or node.get("url"))
                if doi:
                    s2_id = f"DOI:{doi}"
                elif arxiv:
                    s2_id = f"ARXIV:{arxiv}"
                else:
                    continue
                s2_ids.append(s2_id)
                s2_key_by_id[s2_id] = key
            try:
                for requested_id, record in zip(s2_ids, semantic_batch(s2_ids, os.environ.get("SEMANTIC_SCHOLAR_API_KEY", ""))):
                    external = record.get("externalIds") or {}
                    key = s2_key_by_id.get(requested_id) or openalex_key(external.get("OpenAlexId"))
                    if not key:
                        key = openalex_key(by_doi.get(normalize_doi(external.get("DOI")), {}).get("openalex_id"))
                    if key in by_openalex:
                        merge_semantic_scholar(by_openalex[key], record)
            except RuntimeError as error:
                print(f"Semantic Scholar supplement skipped for batch: {error}")
        for node in (by_openalex[key] for key in batch):
            node["metadata_updated_at"] = now()
            node["metadata_completeness"] = completeness(node)
        done.update(batch)
        args.checkpoint.write_text(json.dumps({"updated_at": now(), "done": sorted(done)}, ensure_ascii=False), encoding="utf-8")
        print(f"enriched {len(done):,}/{len(by_openalex):,}")
        time.sleep(max(0, args.delay))

    snapshot["nodes"] = nodes
    snapshot.setdefault("provenance", {})["metadata_enrichment"] = {
        "providers": ["openalex"] + (["semantic_scholar"] if args.provider == "both" else []),
        "updated_at": now(),
        "abstracts_are_source_fields": True,
        "missing_fields_are_left_empty": True,
    }
    write_json(args.output, snapshot)
    print(f"wrote {args.output} ({len(nodes):,} nodes)")


if __name__ == "__main__":
    main()
