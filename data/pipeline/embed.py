#!/usr/bin/env python3
"""Paper embeddings for related-paper search and frontier clustering.

export   write pid + text (title + start of abstract) in pid order, 5,000 per chunk
run      embed chunks; each chunk becomes emb_XXXXX.npy (float16, L2-normalised).
         Chunks already present are skipped, so several machines can share the work
         (--order asc / desc) and an interrupted run resumes.
Model: snowflake/snowflake-arctic-embed-xs (384-d, 128 tokens) via fastembed (ONNX, CPU).
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sqlite3
import time
from pathlib import Path

import numpy as np

from common import WORK, Budget

MODEL = "snowflake/snowflake-arctic-embed-xs"
CHUNK = 5000


def export(db: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    rows = con.execute("SELECT pid, title, abstract FROM papers ORDER BY pid")
    chunk, idx = [], 0
    def flush():
        nonlocal chunk, idx
        if chunk:
            with gzip.open(out_dir / f"text_{idx:05d}.jsonl.gz", "wt", encoding="utf-8") as fh:
                for pid, text in chunk:
                    fh.write(json.dumps([pid, text], ensure_ascii=False) + "\n")
            idx += 1; chunk = []
    for pid, title, abstract in rows:
        chunk.append((pid, (title or "") + ". " + (abstract or "")[:700]))
        if len(chunk) == CHUNK:
            flush()
    flush()
    print(f"exported {idx} chunks to {out_dir}")


def run(text_dir: Path, emb_dir: Path, order: str, budget: Budget, threads: int | None, skip: set[str]) -> None:
    from fastembed import TextEmbedding
    emb_dir.mkdir(parents=True, exist_ok=True)
    model = TextEmbedding(MODEL, max_length=128, threads=threads)
    chunks = sorted(text_dir.glob("text_*.jsonl.gz"))
    if order == "desc":
        chunks.reverse()
    done = 0
    for path in chunks:
        if budget.exhausted():
            break
        idx = path.name[5:10]
        target = emb_dir / f"emb_{idx}.npy"
        if target.exists() or idx in skip:
            continue
        rows = [json.loads(line) for line in gzip.open(path, "rt", encoding="utf-8")]
        t = time.time()
        vecs = np.array(list(model.embed([r[1] for r in rows], batch_size=64)), dtype=np.float32)
        vecs /= np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9
        tmp = emb_dir / f"emb_{idx}.tmp.npy"
        np.save(tmp, vecs.astype(np.float16))
        np.save(emb_dir / f"pid_{idx}.npy", np.array([r[0] for r in rows], dtype=np.int32))
        os.replace(tmp, target)
        done += 1
        print(f"chunk {idx}: {len(rows)} docs in {time.time() - t:.0f}s", flush=True)
    left = sum(1 for p in chunks if not (emb_dir / f"emb_{p.name[5:10]}.npy").exists())
    print(f"embedded {done} chunks this run; {left} chunks remaining")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=("export", "run"))
    ap.add_argument("--db", type=Path, default=Path(os.environ.get("AIRG_DB") or (WORK / "corpus.sqlite")))
    ap.add_argument("--text-dir", type=Path, default=WORK / "embed_text")
    ap.add_argument("--emb-dir", type=Path, default=WORK / "embeddings")
    ap.add_argument("--order", choices=("asc", "desc"), default="asc")
    ap.add_argument("--threads", type=int, default=None)
    ap.add_argument("--time-budget", type=float, default=0)
    ap.add_argument("--skip", default="", help="Comma-separated chunk numbers another machine is handling")
    args = ap.parse_args()
    if args.step == "export":
        export(args.db, args.text_dir)
    else:
        run(args.text_dir, args.emb_dir, args.order, Budget(args.time_budget), args.threads, {f"{int(x):05d}" for x in args.skip.split(",") if x.strip()})


if __name__ == "__main__":
    main()
