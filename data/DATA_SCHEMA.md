# AI/CS citation graph data

This directory contains the reproducible data path for the AI Research Graph website.

The current website snapshot is `ai_cs_graph_citationcs_60000.json.gz`: a 56,832-work union with 330,245 directed citation edges. It combines the 7,916-work OpenAlex two-layer recursive graph, a 2,200-work year-balanced Computer Science catalogue sample, the multi-topic AI/CS slice, and a 47,954-work two-layer CitationCS traversal. `recursive_graph_10000.json` remains the recursive source, `cs_field_catalog_2200.json` records the auditable field-filtered sample, `citationcs_ai_cs_slice_50000.json.gz` records the offline traversal, and `sample_graph.json` remains the smoke-test fixture.

## What is authoritative

`collect_openalex_graph.py` reads metadata and citation edges from the public [OpenAlex API](https://docs.openalex.org/). A directed edge `A -> B` exists only when OpenAlex lists work `B` in work `A`'s `referenced_works` field. `import_citationcs.py` reads CitationCS `ref_lst` edges from the local Zenodo archive. The collectors never infer a citation from title similarity and never invent an edge.

The output JSON has:

- `nodes`: paper metadata, stable OpenAlex ID, optional DOI/arXiv link, topic and author summaries.
- `edges`: directed citation facts with `source`, `target`, `relation: "cites"`, and provenance fields.
- `scope`, `query`, `stats`, `generated_at`: reproducibility and coverage metadata.

The project roadmap targets November 2016 through October 2026 as the main window. The script defaults to calendar years 2016–2026; pass exact dates later if the ingestion service needs a rolling ten-year window.

## Quick start

From the project root:

```powershell
python data/collect_openalex_graph.py --out data/sample_graph.json
```

With a search query and multiple pages:

```powershell
python data/collect_openalex_graph.py `
  --search "artificial intelligence machine learning" `
  --from-year 2016 --to-year 2026 --pages 10 --per-page 100 `
  --out data/ai_graph.json
```

To run the bounded multi-topic seed set used for the larger snapshots:

```powershell
python data/collect_topic_slices.py `
  --topics-file data/topic_queries.txt `
  --from-year 2016 --to-year 2026 --pages 1 --per-page 200
```

To add a bounded set of one-hop referenced works as external nodes, add `--include-external`. This is intentionally capped at 200 fetched references so a broad query cannot unexpectedly crawl the whole API.

OpenAlex currently offers a shared unauthenticated budget. Set `OPENALEX_API_KEY` for a dedicated key and `OPENALEX_MAILTO` for the polite pool contact address when running larger imports.

## Limitations

A search result is a reproducible slice, not a proof that every AI/CS paper has been collected. Production ingestion should use a documented taxonomy, source coverage reports, incremental cursor checkpoints, DOI/arXiv/OpenAlex entity reconciliation, and periodic sampling. Citation counts and references can change as OpenAlex updates.

## Recursive expansion (1000+ nodes)

`collect_recursive_graph.py` starts with recent (default 2020 onward) search results, then walks each work's OpenAlex `referenced_works` list. It hydrates reference IDs in batches and repeats the process by layer. The main window defaults to 2016-11-01 through 2026-10-31. Nodes outside that window can be kept as context with `--include-out-of-window`; they are marked `in_window: false` and are not expanded.

Generate a large graph:

```powershell
python data/collect_recursive_graph.py `
  --target-nodes 7500 `
  --seed-per-page 200 --seed-limit 1000 `
  --max-depth 2 --max-fetch-per-layer 6000 `
  --out data/recursive_graph_10000.json
```

The checked-in/generated `data/recursive_graph_10000.json` is a real run of the collector: it contains 7,916 in-window nodes and 33,031 directed citation edges (704 recent seeds, 3,480 first-layer nodes, and 3,732 second-layer nodes). Counts can change when OpenAlex updates its index or when the seed query/window is changed.

The output records `graph_depth`, `is_seed`, `in_window`, `window_start`, `window_end`, `parent_count`, per-node provenance, and edge provenance. `stats.layers` reports how many references were requested and hydrated at every expansion depth. Edges are emitted only when both endpoint records were fetched; incomplete reference hydration is reflected in the layer statistics rather than being presented as a citation fact.

The website union is reproducible with:

```powershell
python data/merge_recursive_catalog.py `
  --recursive data/recursive_graph_10000.json `
  --catalog data/cs_field_catalog_2200.json `
  --topic-slice data/ai_cs_graph_expanded.json `
  --citationcs data/citationcs_ai_cs_slice_50000.json.gz `
  --out data/ai_cs_graph_citationcs_60000.json.gz
```

The union retains `coverage.catalog_expected_count`, `coverage.catalog_sample_count`, and `coverage.catalog_sample_fraction`. It is explicitly marked `meta.is_exhaustive: false`; the denominator is a measurement for planning a larger import, not a claim that the JSON contains every eligible work.

## Broad Computer Science catalogue coverage

The recursive collector is a relevance-first traversal. For a broader and auditable catalogue slice, use `collect_openalex_catalog.py`. It uses OpenAlex field `17` (Computer Science) plus core publication types (`article`, `conference-paper`, `preprint`, `review`), splits the exact date window into yearly slices, and records the API denominator for every slice. Optional `--topic-id`, `--concept-id`, and `--search` clauses narrow or specialize the catalogue.

For an AI-concept catalogue that is broader than the Computer Science field, use OpenAlex concept `C154945302` and remove the field clause:

```powershell
python data/collect_openalex_catalog.py `
  --no-field --concept-id C154945302 `
  --from-date 2016-11-01 --to-date 2026-10-31 `
  --per-slice 200 --max-works 2200 `
  --out data/ai_concept_catalog_2200.json
```

```powershell
python data/collect_openalex_catalog.py `
  --from-date 2016-11-01 --to-date 2026-10-31 `
  --per-slice 200 --max-works 2200 --per-page 200 `
  --out data/cs_field_catalog_2200.json `
  --manifest-out data/cs_field_catalog_2200.manifest.json
```

The verified run in this workspace retrieved 2,200 nodes (200 from each of 11 yearly slices) and 15,757 citation edges. OpenAlex reported an expected denominator of 5,435,295 matching works for the exact field/type/window filter, so the run's measured coverage is 0.0405%. The output marks `meta.is_exhaustive: false` and preserves `coverage.slices`; complete coverage requires repeated resumable imports with an API key and storage budget.

## CitationCS offline traversal

CitationCS is a static Zenodo archive ([10.5281/zenodo.16537950](https://zenodo.org/records/16537950)) containing 3,720,575 papers and 22,908,275 internal citations. Records use OpenAlex `W...` identifiers and a `ref_lst` list. The downloader supports HTTP range requests and MD5 validation; the importer filters Computer Science primary topics, selects recent/AI-prioritized seeds, and follows `ref_lst` for a bounded depth.

```powershell
python data/download_citationcs.py `
  --out D:\research\citation_cache\citation_cs.zip
python data/import_citationcs.py `
  --archive D:\research\citation_cache\citation_cs.zip `
  --existing data/ai_cs_graph_citationcs_60000.json.gz `
  --depth 2 --max-seeds 10000 --max-nodes 50000 `
  --out data/citationcs_ai_cs_slice_50000.json.gz
```

The verified local run emitted 47,954 nodes and 285,003 edges. It is marked
`meta.is_exhaustive: false`; the archive is a useful static CS base, not a
real-time or complete ten-year AI/CS census. CitationCS edges retain
`source_system: "citationcs"` and are not relabeled as OpenAlex relations.

## Semantic Scholar supplement when OpenAlex API quota is exhausted

`collect_semanticscholar_graph.py` provides a bounded, provider-separated fallback
for citation expansion. It uses Semantic Scholar bulk search (optional) and the
paper `references` endpoint, then hydrates reference metadata through the batch
endpoint. IDs are emitted as `s2:<paperId>` and every edge has
`source_system: "semanticscholar"`; no title-similarity or other inferred edges
are added. A run is explicitly marked `meta.is_exhaustive: false`.

```powershell
python data/collect_semanticscholar_graph.py `
  --query "machine learning" --year 2016-2026 `
  --seed-limit 100 --max-references-per-seed 200 `
  --out data/s2_ml_graph.json
```

For an exact seed, use `--seed-id ARXIV:1706.03762` or `--seed-id DOI:10...`.
Set `SEMANTIC_SCHOLAR_API_KEY` when available and keep the delay conservative;
unauthenticated traffic is shared and can return HTTP 429. For corpus-scale
imports, prefer the monthly S2AG datasets through the Datasets API rather than
issuing one request per paper. Reconcile S2 IDs to OpenAlex IDs only through
DOI/arXiv identifiers (or an auditable title-author-year match), and retain the
provider on each node and edge.
