#!/usr/bin/env python3
"""Precompute related papers for every paper in the corpus.

Four independent signals are kept separately so the website can say *why* a
paper is related:
  sem   content similarity (cosine of abstract embeddings, clustered ANN search)
  coc   co-citation: later papers often cite both (Salton-normalised count)
  cpl   bibliographic coupling: both cite many of the same references
  (direct references / cited-by come from the edges table at site build time)
Results go to the ``related`` table in the work database.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from pathlib import Path

import numpy as np
import scipy.sparse as sp

from common import WORK

DB = Path(os.environ.get("AIRG_DB") or (WORK / "corpus.sqlite"))
EMB = WORK / "embeddings"
TOPK = 10


def load_embeddings(emb_dir: Path, n_pid: int):
    """Return (matrix float16 [rows,384], row_of_pid int32 [n_pid], pid_of_row)."""
    pid_files = sorted(emb_dir.glob("pid_*.npy"))
    pids = np.concatenate([np.load(p) for p in pid_files])
    mats = np.concatenate([np.load(emb_dir / p.name.replace("pid_", "emb_")) for p in pid_files]).astype(np.float16)
    row_of = np.full(n_pid, -1, dtype=np.int32)
    row_of[pids] = np.arange(len(pids), dtype=np.int32)
    return mats, row_of, pids


def kmeans(X: np.ndarray, k: int, sample: int, seed: int = 0, iters: int = 25) -> np.ndarray:
    """Spherical k-means on a sample (numpy only, fp32)."""
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(X), size=min(sample, len(X)), replace=False)
    S = X[idx].astype(np.float32)
    C = S[rng.choice(len(S), size=k, replace=False)].copy()
    for _ in range(iters):
        assign = np.empty(len(S), dtype=np.int32)
        for i in range(0, len(S), 20000):
            assign[i:i + 20000] = np.argmax(S[i:i + 20000] @ C.T, axis=1)
        sums = np.zeros_like(C)
        np.add.at(sums, assign, S)
        counts = np.bincount(assign, minlength=k)
        empty = counts == 0
        sums[empty] = S[rng.choice(len(S), size=int(empty.sum()), replace=False)]
        C = sums / (np.linalg.norm(sums, axis=1, keepdims=True) + 1e-9)
    return C


def assign_all(X: np.ndarray, C: np.ndarray, top: int = 1) -> np.ndarray:
    out = np.empty((len(X), top), dtype=np.int32)
    for i in range(0, len(X), 20000):
        sims = X[i:i + 20000].astype(np.float32) @ C.T
        if top == 1:
            out[i:i + 20000, 0] = np.argmax(sims, axis=1)
        else:
            part = np.argpartition(-sims, top, axis=1)[:, :top]
            order = np.take_along_axis(sims, part, axis=1).argsort(axis=1)[:, ::-1]
            out[i:i + 20000] = np.take_along_axis(part, order, axis=1)
    return out


def semantic_neighbours(X: np.ndarray, k_clusters: int, probes: int, topk: int):
    t = time.time()
    C = kmeans(X, k_clusters, sample=min(len(X), 150000))
    assign = assign_all(X, C, top=1)[:, 0]
    cc = C @ C.T
    near = np.argsort(-cc, axis=1)[:, :probes]  # includes itself first
    members = [np.where(assign == c)[0] for c in range(k_clusters)]
    print(f"  ann clusters ready in {time.time() - t:.0f}s", flush=True)
    nbr = np.full((len(X), topk), -1, dtype=np.int32)
    sim = np.zeros((len(X), topk), dtype=np.float16)
    for c in range(k_clusters):
        q = members[c]
        if len(q) == 0:
            continue
        cand = np.concatenate([members[j] for j in near[c]])
        Q = X[q].astype(np.float32); K = X[cand].astype(np.float32)
        for i in range(0, len(q), 2000):
            s = Q[i:i + 2000] @ K.T
            kk = min(topk + 1, s.shape[1])
            part = np.argpartition(-s, kk - 1, axis=1)[:, :kk]
            ps = np.take_along_axis(s, part, axis=1)
            order = np.argsort(-ps, axis=1)
            part = np.take_along_axis(part, order, axis=1); ps = np.take_along_axis(ps, order, axis=1)
            rows = q[i:i + 2000]
            for r in range(len(rows)):
                ids = cand[part[r]]; ss = ps[r]
                keep = ids != rows[r]
                ids, ss = ids[keep][:topk], ss[keep][:topk]
                nbr[rows[r], :len(ids)] = ids; sim[rows[r], :len(ids)] = ss
    print(f"  semantic neighbours in {time.time() - t:.0f}s", flush=True)
    return nbr, sim


def salton_topk(M: sp.csr_matrix, deg: np.ndarray, min_shared: int, topk: int, chunk: int = 2000):
    """Top-k of M·Mᵀ per row, normalised by sqrt(deg_i·deg_j); returns (ids, score, shared) arrays, -1 padded."""
    MT = M.T.tocsr()
    n = M.shape[0]
    ids = np.full((n, topk), -1, dtype=np.int32)
    score = np.zeros((n, topk), dtype=np.float16)
    shared = np.zeros((n, topk), dtype=np.int32)
    for i in range(0, n, chunk):
        block = (M[i:i + chunk] @ MT).tocsr()
        block.setdiag(0); block.eliminate_zeros()
        for r in range(block.shape[0]):
            s, e = block.indptr[r], block.indptr[r + 1]
            if s == e:
                continue
            cols = block.indices[s:e]; vals = block.data[s:e]
            keep = vals >= min_shared
            if not keep.any():
                continue
            cols, vals = cols[keep], vals[keep]
            sc = vals / np.sqrt(deg[i + r] * deg[cols] + 1e-9)
            top = np.argsort(-sc)[:topk]
            ids[i + r, :len(top)] = cols[top]; score[i + r, :len(top)] = sc[top]; shared[i + r, :len(top)] = vals[top]
    return ids, score, shared


def rows_json(ids, score, shared=None, r=None):
    out = []
    for j in range(ids.shape[1]):
        if ids[r, j] < 0:
            break
        item = [int(ids[r, j]), round(float(score[r, j]), 3)]
        if shared is not None:
            item.append(int(shared[r, j]))
        out.append(item)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clusters", type=int, default=1024)
    ap.add_argument("--probes", type=int, default=3)
    ap.add_argument("--skip-semantic", action="store_true")
    ap.add_argument("--max-ref-df", type=int, default=2000, help="Ignore references cited by more papers than this for coupling")
    args = ap.parse_args()
    con = sqlite3.connect(DB)
    n_pid = con.execute("SELECT MAX(pid) FROM papers").fetchone()[0] + 1
    con.execute("CREATE TABLE IF NOT EXISTS related (pid INTEGER PRIMARY KEY, sem TEXT, coc TEXT, cpl TEXT)")
    t0 = time.time()

    sem_ids = np.full((n_pid, TOPK), -1, dtype=np.int32)
    sem_sim = np.zeros((n_pid, TOPK), dtype=np.float16)
    if not args.skip_semantic:
        X, row_of, pid_of = load_embeddings(EMB, n_pid)
        print(f"embeddings: {X.shape}", flush=True)
        nbr, sim = semantic_neighbours(X, args.clusters, args.probes, TOPK)
        mapped = np.where(nbr >= 0, pid_of[np.maximum(nbr, 0)], -1)
        sem_ids[pid_of] = mapped; sem_sim[pid_of] = sim
        del X, nbr, sim, mapped

    # Co-citation from in-corpus edges (A[src, dst] = 1).
    ed = np.array(con.execute("SELECT src, dst, origin='citationcs' FROM edges").fetchall(), dtype=np.int64).reshape(-1, 3)
    src, dst, is_ccs = ed[:, 0], ed[:, 1], ed[:, 2].astype(bool)
    A = sp.csr_matrix((np.ones(len(src), dtype=np.float32), (src, dst)), shape=(n_pid, n_pid))
    A.data[:] = 1
    AT = A.T.tocsr()
    indeg = np.asarray(AT.sum(axis=1)).ravel()
    coc = salton_topk(AT, indeg, min_shared=2, topk=TOPK)
    print(f"co-citation rows: {int((coc[0][:, 0] >= 0).sum())} ({time.time() - t0:.0f}s)", flush=True)
    del A, AT

    # Bibliographic coupling over all references (including ones outside the corpus).
    keys: dict[int, int] = {}
    row_parts, col_parts = [], []
    buf_r, buf_c = [], []
    for pid, refs in con.execute("SELECT pid, refs FROM papers WHERE refs IS NOT NULL"):
        for corpus, _ in json.loads(refs):
            buf_r.append(pid); buf_c.append(keys.setdefault(int(corpus), len(keys)))
        if len(buf_r) > 2_000_000:
            row_parts.append(np.array(buf_r, dtype=np.int32)); col_parts.append(np.array(buf_c, dtype=np.int32)); buf_r, buf_c = [], []
    row_parts.append(np.array(buf_r, dtype=np.int32)); col_parts.append(np.array(buf_c, dtype=np.int32))
    off = len(keys)
    del keys
    # CitationCS edges have no S2 reference list; use the in-corpus target as the reference key.
    row_parts.append(src[is_ccs].astype(np.int32)); col_parts.append((off + dst[is_ccs]).astype(np.int32))
    rows = np.concatenate(row_parts); cols = np.concatenate(col_parts)
    del row_parts, col_parts
    B = sp.csr_matrix((np.ones(len(rows), dtype=np.float32), (rows, cols)), shape=(n_pid, off + n_pid))
    del rows, cols
    B.sum_duplicates(); B.data[:] = 1
    df = np.asarray(B.sum(axis=0)).ravel()
    keepcols = np.where((df >= 2) & (df <= args.max_ref_df))[0]
    B = B[:, keepcols].tocsr()
    outdeg = np.asarray(B.sum(axis=1)).ravel()
    cpl = salton_topk(B, outdeg, min_shared=3, topk=TOPK)
    print(f"coupling rows: {int((cpl[0][:, 0] >= 0).sum())} ({time.time() - t0:.0f}s)", flush=True)

    con.execute("DELETE FROM related")
    def gen():
        for p in range(n_pid):
            if sem_ids[p, 0] < 0 and coc[0][p, 0] < 0 and cpl[0][p, 0] < 0:
                continue
            yield (p, json.dumps(rows_json(sem_ids, sem_sim, r=p)), json.dumps(rows_json(*coc, r=p)), json.dumps(rows_json(*cpl, r=p)))
    con.executemany("INSERT INTO related(pid, sem, coc, cpl) VALUES(?,?,?,?)", gen())
    con.commit()
    total = con.execute("SELECT COUNT(*) FROM related").fetchone()[0]
    print(f"related rows written: {total} in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
