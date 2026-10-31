# OpenAlex citation graph data

This directory contains the reproducible data path for the AI Research Graph website.

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

To add a bounded set of one-hop referenced works as external nodes, add `--include-external`. This is intentionally capped at 200 fetched references so a broad query cannot unexpectedly crawl the whole API.

OpenAlex currently offers a shared unauthenticated budget. Set `OPENALEX_API_KEY` for a dedicated key and `OPENALEX_MAILTO` for the polite pool contact address when running larger imports.

## Limitations

A search result is a reproducible slice, not a proof that every AI/CS paper has been collected. Production ingestion should use a documented taxonomy, source coverage reports, incremental cursor checkpoints, DOI/arXiv/OpenAlex entity reconciliation, and periodic sampling. Citation counts and references can change as OpenAlex updates.
