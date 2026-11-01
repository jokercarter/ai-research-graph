#!/usr/bin/env python3
"""Load harvested sources into one SQLite work database (data/work/corpus.sqlite).

Steps (idempotent; safe to re-run every month):
  arxiv       upsert arXiv records from data/work/arxiv_records_*.jsonl.gz
  citationcs  attach the offline CitationCS snapshot (title match to arXiv, else keep as its own record)
  s2          apply Semantic Scholar counts / venues / references from finished jobs
  edges       rebuild directed citation edges inside the corpus
  queue       write arXiv ids that still need a Semantic Scholar reference fetch
"""
from __future__ import annotations

import argparse
import os
import glob
import gzip
import html
import json
import re
import sqlite3
from pathlib import Path

from common import ROOT, WORK, read_jsonl

DB = Path(os.environ.get("AIRG_DB") or (WORK / "corpus.sqlite"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
  pid INTEGER PRIMARY KEY,
  key TEXT UNIQUE NOT NULL,
  arxiv_id TEXT UNIQUE,
  openalex_id TEXT UNIQUE,
  title TEXT NOT NULL,
  norm_title TEXT,
  abstract TEXT,
  authors TEXT,
  n_authors INTEGER,
  date TEXT,
  year INTEGER,
  categories TEXT,
  primary_cat TEXT,
  venue TEXT,
  journal_ref TEXT,
  comments TEXT,
  doi TEXT,
  cites INTEGER DEFAULT 0,
  influential INTEGER DEFAULT 0,
  corpus_id INTEGER,
  refs TEXT,
  s2_done INTEGER DEFAULT 0,
  source TEXT,
  first_snapshot TEXT
);
CREATE INDEX IF NOT EXISTS papers_norm ON papers(norm_title);
CREATE INDEX IF NOT EXISTS papers_corpus ON papers(corpus_id);
CREATE TABLE IF NOT EXISTS edges (src INTEGER, dst INTEGER, origin TEXT, PRIMARY KEY (src, dst)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(SCHEMA)
    return con


def norm_title(title: str) -> str:
    title = re.sub(r"<[^>]+>", " ", html.unescape(title or "")).lower()
    return re.sub(r"[^a-z0-9]+", "", title)


def meta_get(con, key, default=None):
    row = con.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
    return row[0] if row else default


def meta_set(con, key, value):
    con.execute("INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, str(value)))


def load_arxiv(con, snapshot: str) -> None:
    files = sorted(glob.glob(str(WORK / "arxiv_records_*.jsonl.gz")))
    loaded = json.loads(meta_get(con, "arxiv_files_loaded", "{}"))
    n = 0
    for path in files:
        size = Path(path).stat().st_size
        if loaded.get(path) == size:
            continue
        for r in read_jsonl(Path(path)):
            cats = r["categories"]
            date = r.get("created") or ""
            con.execute(
                """INSERT INTO papers(key, arxiv_id, title, norm_title, abstract, authors, n_authors, date, year,
                       categories, primary_cat, journal_ref, comments, doi, source, first_snapshot)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'arxiv', ?)
                   ON CONFLICT(arxiv_id) DO UPDATE SET title=excluded.title, norm_title=excluded.norm_title,
                       abstract=excluded.abstract, authors=excluded.authors, n_authors=excluded.n_authors,
                       categories=excluded.categories, primary_cat=excluded.primary_cat,
                       journal_ref=excluded.journal_ref, comments=excluded.comments,
                       doi=COALESCE(NULLIF(excluded.doi,''), papers.doi)""",
                (f"arxiv:{r['arxiv_id']}", r["arxiv_id"], r["title"], norm_title(r["title"]), r["abstract"],
                 json.dumps(r["authors"], ensure_ascii=False), r.get("n_authors"), date, int(date[:4]) if date[:4].isdigit() else None,
                 " ".join(cats), cats[0], r.get("journal_ref"), r.get("comments"), r.get("doi"), snapshot))
            n += 1
        loaded[path] = size
        meta_set(con, "arxiv_files_loaded", json.dumps(loaded))
        con.commit()
    print(f"arxiv rows upserted: {n}")


def load_citationcs(con, snapshot: str) -> None:
    if meta_get(con, "citationcs_loaded"):
        print("citationcs already loaded"); return
    parts = [ROOT / "data" / "ai_cs_graph_200000.part1.json.gz", ROOT / "data" / "ai_cs_graph_200000.part2.json.gz"]
    matched = added = 0
    wmap = {}
    for part in parts:
        data = json.load(gzip.open(part, "rt", encoding="utf-8"))
        for node in data.get("nodes", []):
            wid = str(node.get("id", "")).rsplit("/", 1)[-1]
            if not wid or wid in wmap:
                continue
            title = re.sub(r"<[^>]+>", "", html.unescape(node.get("title") or "")).strip()
            nt = norm_title(title)
            year = node.get("publication_year")
            hit = None
            if len(nt) >= 20:
                for pid, y in con.execute("SELECT pid, year FROM papers WHERE norm_title=? AND source='arxiv'", (nt,)):
                    if y is None or year is None or abs(y - year) <= 2:
                        hit = pid
                        break
            venue = node.get("venue") or ""
            if hit:
                con.execute("UPDATE papers SET openalex_id=?, venue=COALESCE(NULLIF(venue,''), ?), cites=MAX(cites, ?) WHERE pid=?",
                            (wid, venue, node.get("cited_by_count") or 0, hit))
                wmap[wid] = hit
                matched += 1
            else:
                authors = [a.get("name") if isinstance(a, dict) else str(a) for a in node.get("authors") or []]
                prim = node.get("primary_topic") or {}
                cur = con.execute(
                    """INSERT OR IGNORE INTO papers(key, openalex_id, title, norm_title, abstract, authors, n_authors, date, year,
                           categories, primary_cat, venue, cites, source, first_snapshot)
                       VALUES(?,?,?,?,'',?,?,?,?,?,?,?,?, 'citationcs', ?)""",
                    (f"oa:{wid}", wid, title, nt, json.dumps(authors[:25], ensure_ascii=False), len(authors),
                     node.get("publication_date") or "", year, "; ".join(node.get("topics") or []),
                     prim.get("name") or "", venue, node.get("cited_by_count") or 0, "2026-10-31"))
                row = con.execute("SELECT pid FROM papers WHERE openalex_id=?", (wid,)).fetchone()
                wmap[wid] = row[0]
                added += cur.rowcount
        for e in data.get("edges", []):
            s = wmap.get(str(e["source"]).rsplit("/", 1)[-1]); t = wmap.get(str(e["target"]).rsplit("/", 1)[-1])
            if s and t and s != t:
                con.execute("INSERT OR IGNORE INTO edges(src,dst,origin) VALUES(?,?, 'citationcs')", (s, t))
        con.commit()
        del data
    meta_set(con, "citationcs_loaded", f"matched={matched} added={added}")
    con.commit()
    print(f"citationcs: matched to arXiv={matched}, kept as own record={added}")


def apply_s2(con, jobs: list[str]) -> None:
    """Stream finished Semantic Scholar job files into the papers table."""
    for job in jobs:
        job_state = json.loads((WORK / f"s2_{job}_job.json").read_text(encoding="utf-8"))
        n = 0
        for w in range(len(job_state["slices"])):
            for row in read_jsonl(WORK / f"s2_{job}_w{w}.jsonl.gz"):
                aid = row["arxiv_id"]
                if "refs" in row:
                    con.execute("""UPDATE papers SET cites=?, influential=?, corpus_id=?,
                                   venue=COALESCE(NULLIF(?,''), venue), refs=?, s2_done=1 WHERE arxiv_id=?""",
                                (row["cites"], row["influential"], row["corpus_id"], row.get("venue") or "",
                                 json.dumps(row["refs"], separators=(",", ":")), aid))
                else:
                    con.execute("UPDATE papers SET cites=?, influential=?, corpus_id=COALESCE(corpus_id, ?) WHERE arxiv_id=?",
                                (row["cites"], row["influential"], row["corpus_id"], aid))
                n += 1
                if n % 50000 == 0:
                    con.commit()
        con.commit()
        print(f"applied s2 job {job}: {n} rows")


def rebuild_edges(con) -> None:
    corpus_to_pid = {c: p for p, c in con.execute("SELECT pid, corpus_id FROM papers WHERE corpus_id IS NOT NULL")}
    arxiv_to_pid = {a: p for p, a in con.execute("SELECT pid, arxiv_id FROM papers WHERE arxiv_id IS NOT NULL")}
    con.execute("DELETE FROM edges WHERE origin='s2'")
    n = 0
    batch = []
    for pid, refs in con.execute("SELECT pid, refs FROM papers WHERE refs IS NOT NULL"):
        for corpus, arx in json.loads(refs):
            dst = corpus_to_pid.get(corpus) or (arxiv_to_pid.get(arx) if arx else None)
            if dst and dst != pid:
                batch.append((pid, dst))
        if len(batch) > 200000:
            con.executemany("INSERT OR IGNORE INTO edges(src,dst,origin) VALUES(?,?,'s2')", batch); n += len(batch); batch = []
    con.executemany("INSERT OR IGNORE INTO edges(src,dst,origin) VALUES(?,?,'s2')", batch); n += len(batch)
    con.commit()
    total = con.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    print(f"edges: {total} total ({n} candidate S2 edges)")


def write_queue(con, path: Path) -> None:
    rows = [r[0] for r in con.execute("SELECT arxiv_id FROM papers WHERE arxiv_id IS NOT NULL AND s2_done=0 ORDER BY arxiv_id")]
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    print(f"queue: {len(rows)} ids -> {path}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("steps", nargs="+", choices=("arxiv", "citationcs", "s2", "edges", "queue", "stats"))
    ap.add_argument("--snapshot", default="2026-10-31", help="Snapshot label stored on newly inserted papers")
    ap.add_argument("--s2-job", action="append", default=[])
    ap.add_argument("--queue-out", type=Path, default=WORK / "s2_queue.txt")
    args = ap.parse_args()
    con = connect()
    for step in args.steps:
        if step == "arxiv": load_arxiv(con, args.snapshot)
        elif step == "citationcs": load_citationcs(con, args.snapshot)
        elif step == "s2": apply_s2(con, args.s2_job)
        elif step == "edges": rebuild_edges(con)
        elif step == "queue": write_queue(con, args.queue_out)
        elif step == "stats":
            for q in ["SELECT source, COUNT(*) FROM papers GROUP BY source", "SELECT year, COUNT(*) FROM papers GROUP BY year ORDER BY year",
                      "SELECT COUNT(*) FROM papers WHERE abstract<>''", "SELECT COUNT(*), SUM(s2_done) FROM papers", "SELECT origin, COUNT(*) FROM edges GROUP BY origin"]:
                print(q, con.execute(q).fetchall())


if __name__ == "__main__":
    main()
