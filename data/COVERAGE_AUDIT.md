# AI and Computer Science Coverage Audit

## Scope

This audit compares the existing keyword/recursive collectors with a broader, field-filtered catalogue mode and a local CitationCS traversal. It uses the exact roadmap window (`2016-11-01` through `2026-10-31`). The website now uses the reproducible union `ai_cs_graph_citationcs_60000.json.gz`, which combines OpenAlex recursive/catalogue/topic slices with a CitationCS two-layer subgraph.

## Current website snapshot

The current union contains **56,832 nodes** and **330,245 directed citation edges**. It includes the 7,916-node OpenAlex recursive source, 2,200 field-filtered catalogue nodes, 1,063 multi-topic slice nodes, and a 47,954-node CitationCS traversal. The CitationCS traversal adds 46,961 nodes to the union and contributes 285,003 unique edges before cross-source de-duplication. The website carries `source_system` and `provenance_sources` on edges so the two providers remain distinguishable.

CitationCS is a static 2025-07-28 Zenodo archive with 3,720,575 papers and 22,908,275 internal citations. Its records use OpenAlex `W...` identifiers and `ref_lst`; the importer filters Computer Science primary topics, chooses recent/AI-prioritized seeds, and follows references to depth 2. The traversal is a bounded slice, not a claim that the archive or the ten-year AI/CS corpus is fully rendered.

## What the existing collectors actually cover

### `collect_recursive_graph.py`

- Seeds come from eight full-text queries: artificial intelligence, machine learning, deep learning, large language model, natural language processing, computer vision, reinforcement learning, and robotics.
- The default seed interval starts in 2020, not the full ten-year window.
- Search results are ranked by citation count, then capped by pages, per-page size, and a global seed limit. The verified 10k run used one 200-result page per query and produced 704 unique seed nodes after deduplication.
- Expansion follows OpenAlex `referenced_works` only. The 10k run expanded two layers, capped references per node and hydrated reference IDs per layer.
- Verified output: `recursive_graph_10000.json` contains 7,916 in-window nodes and 33,031 citation edges (704 seeds, 3,480 depth-1 nodes, 3,732 depth-2 nodes).
- This is a connected relevance-first traversal, not a catalogue. It does not have a denominator for the number of eligible AI/CS works, so its coverage fraction cannot be claimed.

### `collect_topic_slices.py` and merged search slices

- Topic slices use one broad full-text query page per topic by default. Search ranking makes them useful for discovery, but a query page is not a complete field catalogue.
- The merged `ai_cs_graph_expanded.json` contains 1,063 nodes and 2,376 edges from 14 bounded text-query slices. The input slices have no OpenAlex `meta.count` coverage denominator.
- Broad search terms admit application-domain literature and can omit relevant works whose titles/abstracts use different terminology.

## Implemented broader coverage mode

`collect_openalex_catalog.py` adds a transparent catalogue path:

1. Applies OpenAlex field `17` (`Computer Science`) and core publication types (`article`, `conference-paper`, `preprint`, `review`) by default.
2. Splits the exact date window into 11 yearly slices, including the partial 2016 and 2026 slices.
3. Uses cursor pagination per slice and records `meta.count` as the expected denominator.
4. Stores the exact filter, dates, pages, retrieved count, and unique additions in `coverage.slices`.
5. Emits real citation edges only when both endpoint works were retrieved from OpenAlex.
6. Supports optional topic/concept/search clauses for an AI-focused subset without changing the denominator contract.

Command used for the verified run:

```powershell
python data/collect_openalex_catalog.py `
  --from-date 2016-11-01 --to-date 2026-10-31 `
  --per-slice 200 --max-works 2200 --per-page 200 `
  --out data/cs_field_catalog_2200.json `
  --manifest-out data/cs_field_catalog_2200.manifest.json
```

Verified output:

| Measure | Value |
|---|---:|
| Yearly slices | 11 |
| Retrieved unique nodes | 2,200 |
| Retrieved per slice | 200 × 11 |
| Citation edges | 15,757 |
| OpenAlex expected denominator | 5,435,295 |
| Measured coverage | 0.0405% |
| Core publication types | article, conference-paper, preprint, review |
| Date scope | 2016-11-01 to 2026-10-31 |
| Exhaustive claim | false, recorded in output metadata |

The 5,435,295 denominator is the sum of the per-year OpenAlex counts for the exact field/type/date filter. It shows why a 2,200-node snapshot cannot be described as “all” AI/CS papers, while still providing a reproducible path to expand coverage.

## Recommended product/data strategy

- Use the field-filtered catalogue as the broad index and expose its coverage fraction in the website.
- Use the recursive graph as a relevance-first connected view and label its seed/expansion depth.
- Keep the raw OpenAlex ID, exact filter, retrieval time, and source field on every record.
- For an AI-only view, maintain a reviewed topic/concept allowlist on top of the Computer Science field rather than relying only on free-text keywords.
- Build a resumable importer around yearly slices and cursors, with a stored checkpoint per slice. Run it with an OpenAlex API key and a durable database; the unauthenticated shared budget is not sufficient for 5.4 million works in one session.
- Reconcile DOI, arXiv ID, OpenAlex ID, and version records before counting coverage. Preserve application-domain references as a context tier instead of deleting them by keyword.

## Conclusion

The current project has a working citation graph and two reproducible expansion paths: OpenAlex API traversal/catalogue sampling and the local CitationCS archive traversal. The combined website comfortably exceeds the 1,000-node requirement and now exposes a much larger relation graph. A complete all-record import remains an ingestion project measured against the 5.4 million-work OpenAlex denominator; neither this bounded JSON snapshot nor the CitationCS subgraph should be described as the full ten-year AI/CS census.
