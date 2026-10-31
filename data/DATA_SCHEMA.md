# OpenAlex citation graph data

This directory contains the reproducible data path for the AI Research Graph website.

The current website snapshot is `recursive_graph_1000.json`: a 3,191-work OpenAlex graph with 5,109 directed citation edges, built from 704 recent seeds plus 2,487 first-layer `referenced_works` results. `ai_cs_graph_expanded.json` remains a merged multi-query slice, and `sample_graph.json` remains the smaller smoke-test fixture.

## What is authoritative

`collect_openalex_graph.py` reads metadata and citation edges from the public [OpenAlex API](https://docs.openalex.org/). A directed edge `A -> B` exists only when OpenAlex lists work `B` in work `A`'s `referenced_works` field. The collector never infers a citation from title similarity and never invents an edge.

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
  --target-nodes 1000 `
  --max-depth 2 `
  --include-out-of-window `
  --out data/recursive_graph.json
```

The checked-in/generated `data/recursive_graph_1000.json` is a real run of the collector: it contains 3,191 in-window nodes and 5,109 directed citation edges (704 recent seeds plus 2,487 first-layer references). Counts can change when OpenAlex updates its index or when the seed query/window is changed.

The output records `graph_depth`, `is_seed`, `in_window`, `window_start`, `window_end`, `parent_count`, per-node provenance, and edge provenance. `stats.layers` reports how many references were requested and hydrated at every expansion depth. Edges are emitted only when both endpoint records were fetched; incomplete reference hydration is reflected in the layer statistics rather than being presented as a citation fact.
