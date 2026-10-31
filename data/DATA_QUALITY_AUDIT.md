# Recursive Graph Data Quality Audit

## Website snapshot

The current website uses `data/ai_cs_graph_coverage_10000.json`, a union of the recursive source, `data/cs_field_catalog_2200.json`, and the existing multi-topic AI/CS slice. The union contains **9,871 nodes** and **46,231 directed citation edges** after OpenAlex ID de-duplication. It adds **1,582** catalog-only nodes and **373** topic-slice-only nodes; the catalogue contributes **11,498** unique edges touching catalog additions, and the topic slice contributes **944** additional unique edges. The catalog denominator is **5,435,295** works for the exact Computer Science field/type/date filter; the 2,200-work catalog sample is **0.0405%** of that denominator and is marked non-exhaustive.

The union was validated with no missing edge endpoints, duplicate endpoint pairs, or self-loops. Every edge remains a copied OpenAlex `referenced_works` relation. `coverage.catalog_expected_count`, `coverage.catalog_sample_count`, and `coverage.catalog_sample_fraction` are carried in the JSON so the UI can show the coverage boundary.

## Recursive source snapshot audited

- File: `data/recursive_graph_10000.json`
- Generated from: OpenAlex Works API
- Main window: `2016-11-01` through `2026-10-31`
- Expansion: recent seed search → `referenced_works` → `referenced_works` (two citation hops)
- Audit scope: read-only structural and relevance checks; no citation edges were inferred

The recursive source is still the connected relevance-first view. The older `recursive_graph_1000.json` remains in the repository as a smaller reproducibility fixture.

## Executive result

The current snapshot is structurally usable for the citation graph. It contains **7,916 unique works** and **33,031 directed citation edges**. Every emitted edge points to two works in the snapshot, with no duplicate endpoint pair or self-loop. All works are inside the requested date window; 704 are recent search seeds, 3,480 are first-layer references, and 3,732 are second-layer references.

The main remaining risk is relevance precision. The seed queries are broad and OpenAlex's topic labels include application-domain literature such as health, dementia, education, and genomics. Those works may be legitimate AI/CS applications or citation context. The dataset therefore describes a bounded OpenAlex AI/CS-oriented slice, not an exhaustive census of every AI/CS paper in the decade.

## Structural checks

| Check | Result | Assessment |
|---|---:|---|
| Nodes | 7,916 | Matches `stats.nodes` |
| Directed edges | 33,031 | Matches `stats.edges` |
| Unique OpenAlex node IDs | 7,916 | Pass |
| Missing or malformed OpenAlex node IDs | 0 | Pass |
| Edges with a missing endpoint | 0 | Pass |
| Duplicate `(source, target)` pairs | 0 | Pass |
| Self-loops | 0 | Pass |
| Edge relation values | 33,031 `cites` | Consistent |
| Empty titles | 0 | Pass |
| Missing publication year | 0 | Pass |
| Missing primary topic | 11 | Review or retain with a quality flag |
| Missing authors | 56 | Metadata incompleteness |
| Missing DOI | 167 | Normal for preprints or incomplete records |
| Missing landing/open-access link | 0 | Pass |
| Isolated nodes | 157 | Expected in a bounded two-hop slice; inspect in UI |
| `arxiv_id == "org"` | 0 | Parser repair verified |

The median `referenced_works_count` is 38 and the maximum is 1,843. A node with few or no in-snapshot edges is therefore a traversal boundary, not evidence that the work has no references.

## Time window and depth

All 7,916 records have `in_window: true`. The depth distribution is:

| Graph depth | Meaning | Nodes |
|---:|---|---:|
| 0 | Recent seed search results | 704 |
| 1 | References of seed works | 3,480 |
| 2 | References of first-layer works | 3,732 |

The publication-year distribution is skewed toward the recent seed period and its references:

| Year | Nodes |
|---:|---:|
| 2016 | 89 |
| 2017 | 660 |
| 2018 | 929 |
| 2019 | 1,045 |
| 2020 | 1,279 |
| 2021 | 1,189 |
| 2022 | 1,126 |
| 2023 | 1,302 |
| 2024 | 262 |
| 2025 | 32 |
| 2026 | 3 |

## Publication types

The four core research types account for 7,634 nodes (96.4%):

| Type | Nodes |
|---|---:|
| article | 2,883 |
| conference-paper | 2,721 |
| preprint | 1,399 |
| review | 631 |

The remaining 282 records are books, chapters, editorials, paratext, datasets, reports, letters, and other context types. The UI can default to the four core types while retaining the raw records for traceability.

## Relevance signals

A title-keyword review found potentially cross-domain signals (counts overlap and are review flags, not automatic exclusions):

| Signal | Nodes | Core research types |
|---|---:|---:|
| Medical/health/disease terms | 1,068 | 1,037 |
| Bioinformatics/molecular terms | 253 | 248 |
| Business/economics terms | 91 | 90 |
| Earth/agriculture/energy terms | 54 | 49 |
| Education/social terms | 1,287 | 1,248 |

The most frequent primary topics include `Topic Modeling` (1,395), `Domain Adaptation and Few-Shot Learning` (382), `Advanced Neural Network Applications` (358), `Natural Language Processing Techniques` (318), and `Ethics and Social Impacts of AI` (251). These signals support displaying topic and provenance metadata in the UI instead of silently deleting application-domain papers.

## Entity and metadata notes

- OpenAlex IDs are unique in the snapshot; no endpoint is missing from an emitted edge.
- The collector now extracts arXiv identifiers from `arxiv.org/abs/...`, `arxiv.org/pdf/...`, and `10.48550/arXiv...` forms before the generic DOI branch. Existing snapshots were repaired and validated; malformed URL-suffix artifacts were removed.
- Exact-title duplicates may still represent versions or separate venue records. DOI/arXiv/OpenAlex reconciliation should happen before treating title duplicates as the same work.
- Topic and author fields are source metadata and can be absent or incomplete. Missing values are retained rather than guessed.

## Safe filtering and next ingestion steps

1. Default the primary view to `article`, `conference-paper`, `review`, and `preprint`; retain other types as context records.
2. Keep the explicit date window visible in the UI.
3. Do not delete records solely from medical, education, climate, or other application keywords; those can be valid AI/CS work.
4. For a stricter AI/CS release, add a documented OpenAlex topic/field allowlist and store `inclusion_reason`, `seed_query`, `topic_id`, and `relevance_status` per node.
5. Deduplicate DOI, arXiv ID, OpenAlex ID, title, author, and year before deeper crawling; preserve the mapping for auditability.
6. Expand beyond two hops only after relevance controls and coverage reporting are in place.

## Audit conclusion

The website snapshot is safe to render as a provenance-aware exploratory graph centered on real OpenAlex citation edges. It meets the 1,000-node requirement by a wide margin and makes the two-hop traversal boundary explicit. It should be described as a reproducible, bounded recursive slice rather than a complete inventory of all AI/CS papers from the last decade.
