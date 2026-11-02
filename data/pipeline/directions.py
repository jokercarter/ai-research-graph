#!/usr/bin/env python3
"""Research directions from abstract content (semantic embeddings), and their trends.

Steps
  export   write title+abstract texts for papers that have no vector yet
           (data/work/sem/text/*.jsonl.gz; then run embed_gpu.py on a GPU machine)
  build    merge new vectors into the cache, cluster papers into research directions
           (or reuse the saved directions), assign every paper to 1-3 directions by
           content similarity, measure growth, and write the site files:
             trends.json.gz   directions with names, keywords, metrics, papers
             dir.bin.gz       3 bytes per paper: its direction ids (255 = none)
Directions are fitted once and kept stable month to month (--refit to redo them).
Names live in data/pipeline/direction_names.json, keyed by the directions' fingerprint.
"""
from __future__ import annotations

import argparse
import glob
import gzip
import hashlib
import json
import os
import pickle
import time
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
SEM = ROOT / "data" / "work" / "sem"
TMP = Path(os.environ.get("AIRG_TMP") or (Path.home() / "airg_work"))
OUT = Path(os.environ.get("AIRG_OUT") or (ROOT / "public" / "research" / "data"))
NAMES = Path(__file__).resolve().parent / "direction_names.json"
# Where the id-keyed vector cache and fitted directions live (override to a fast local disk if needed).
CACHE = Path(os.environ.get("AIRG_SEM_CACHE") or SEM)
K_DEFAULT = 150
MAX_LABELS = 3
SECONDARY_GAP = 0.015         # a 2nd/3rd direction must be almost as close as the best one
CHUNK = 5000
GENERIC = set("""paper propose proposed approach method methods model models results show shows novel new framework based
using use used work task tasks performance data dataset datasets existing state art experiments experimental demonstrate
achieve achieves significantly improve improves improved effective efficient large small high low first two three one many
recent recently present introduce study studies problem problems different various however well also significant superior
extensive code available github https www com http compared furthermore moreover additionally specifically particular
respectively key important able including challenging challenge challenges remain remains limited lack address learning
training trained train benchmark benchmarks evaluation evaluate evaluated outperforms baseline baselines across while
paper's yet often even still thus within without via""".split())


def load_docs():
    """Only the fields this script uses, so 500k papers fit in a few GB of memory."""
    import gc
    with open(TMP / "docs.pkl", "rb") as fh:
        docs = pickle.load(fh)
    for i, r in enumerate(docs):
        docs[i] = {"arxiv_id": r["arxiv_id"], "title": r["title"], "abstract": r["abstract"], "created": r["created"], "categories": r["categories"][:1]}
    gc.collect()
    return docs


# ---------------------------------------------------------------- vector cache (keyed by arXiv id)
def load_cache():
    ids_path, vec_path = CACHE / "vector_ids.txt", CACHE / "vectors.f16.npy"
    if ids_path.exists() and vec_path.exists():
        ids = ids_path.read_text(encoding="utf-8").split("\n")
        return ids, np.load(vec_path, mmap_mode="r")
    return [], None


def merge_chunks(docs):
    """Fold data/work/sem/emb chunks into the id-keyed cache."""
    ids, vecs = load_cache()
    have = {k: i for i, k in enumerate(ids)}
    new_ids, new_vecs = [], []
    merged_path = SEM / "merged_chunks.txt"
    merged = set(merged_path.read_text().split()) if merged_path.exists() else set()
    for emb_path in sorted(glob.glob(str(SEM / "emb" / "emb_*.npy"))):
        idx = os.path.basename(emb_path)[4:9]
        if idx in merged:
            continue  # already in the cache (and row-number keys would point at a different docs.pkl now)
        keys = np.load(SEM / "emb" / f"pid_{idx}.npy", allow_pickle=True)
        if keys.dtype.kind in "iu":   # first build: chunk lines were keyed by row number in docs.pkl
            keys = [docs[int(s)]["arxiv_id"] for s in keys]
        E = np.load(emb_path)
        for k, v in zip(keys, E):
            k = str(k)
            if k not in have:
                have[k] = len(ids) + len(new_ids); new_ids.append(k); new_vecs.append(v)
        merged.add(idx)
    if new_ids:
        allv = np.concatenate([np.asarray(vecs) if vecs is not None else np.zeros((0, len(new_vecs[0])), np.float16),
                               np.array(new_vecs, dtype=np.float16)])
        ids = ids + new_ids
        np.save(CACHE / "vectors.f16.npy", allv)
        (CACHE / "vector_ids.txt").write_text("\n".join(ids), encoding="utf-8")
        print(f"vector cache: +{len(new_ids)} -> {len(ids)}")
    merged_path.write_text("\n".join(sorted(merged)))
    return load_cache()


def phase_export(a):
    docs = load_docs()
    ids, _ = load_cache()
    have = set(ids)
    todo = [r for r in docs if r["arxiv_id"] not in have]
    out = SEM / "text"; out.mkdir(parents=True, exist_ok=True)
    existing = len(glob.glob(str(out / "text_*.jsonl.gz")))
    for n, i in enumerate(range(0, len(todo), CHUNK)):
        with gzip.open(out / f"text_{existing + n:05d}.jsonl.gz", "wt", encoding="utf-8") as fh:
            for r in todo[i:i + CHUNK]:
                fh.write(json.dumps([r["arxiv_id"], r["title"] + ". " + r["abstract"][:600]], ensure_ascii=False) + "\n")
    print(f"export: {len(todo)} papers need vectors ({(len(todo) + CHUNK - 1) // CHUNK} chunks)")


# ---------------------------------------------------------------- clustering
def spherical_kmeans(X, k, iters=30, seed=7, sample=160000):
    rng = np.random.default_rng(seed)
    S = X[rng.choice(len(X), size=min(sample, len(X)), replace=False)].astype(np.float32)
    # k-means++ style seeding on a subsample for well-spread starting centroids
    C = [S[rng.integers(len(S))]]
    d = 1 - S @ C[0]
    for _ in range(k - 1):
        p = np.maximum(d, 0) ** 2; p /= p.sum()
        C.append(S[rng.choice(len(S), p=p)])
        d = np.minimum(d, 1 - S @ C[-1])
    C = np.array(C)
    for it in range(iters):
        assign = np.concatenate([np.argmax(S[i:i + 20000] @ C.T, axis=1) for i in range(0, len(S), 20000)])
        sums = np.zeros_like(C); np.add.at(sums, assign, S)
        cnt = np.bincount(assign, minlength=k)
        empty = cnt == 0
        if empty.any():
            sums[empty] = S[rng.choice(len(S), size=int(empty.sum()), replace=False)]
        C = sums / (np.linalg.norm(sums, axis=1, keepdims=True) + 1e-9)
    return C.astype(np.float32)


def fingerprint(C):
    return hashlib.sha1(np.round(C[:, :16], 3).tobytes()).hexdigest()[:12]


def keywords(docs, primary, k, per=250, top=8):
    from sklearn.feature_extraction.text import TfidfVectorizer
    rng = np.random.default_rng(0)
    texts = []
    for t in range(k):
        members = np.flatnonzero(primary == t)
        pick = rng.choice(members, size=min(per, len(members)), replace=False) if len(members) else []
        texts.append(" ".join(docs[i]["title"] + " " + docs[i]["abstract"] for i in pick))
    vec = TfidfVectorizer(ngram_range=(1, 2), stop_words="english", sublinear_tf=True, max_df=0.12, min_df=2,
                          max_features=200000, token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z0-9\-]{1,}\b")
    M = vec.fit_transform(texts)
    vocab = np.array(vec.get_feature_names_out())
    out = []
    for t in range(k):
        row = M[t].toarray().ravel()
        terms = []
        for j in np.argsort(-row)[:150]:
            term = vocab[j]
            if any(w in GENERIC for w in term.split()) or any(term in x or x in term for x in terms):
                continue
            terms.append(term)
            if len(terms) == top:
                break
        out.append(terms)
    return out


PCA_DIM = 256


def fit_pca(X, dim=PCA_DIM, sample=60000, seed=3):
    """Projection kept in the cache folder so the same projection is reused while the vectors don't change."""
    path = CACHE / "pca.npz"
    if path.exists():
        z = np.load(path)
        if z["Vt"].shape[0] >= dim and z["mu"].shape[0] == X.shape[1]:
            return z["mu"].astype(np.float32), z["Vt"][:dim].astype(np.float32)
    rng = np.random.default_rng(seed)
    idx = np.sort(rng.choice(len(X), min(sample, len(X)), replace=False))
    S = np.asarray(X[idx], dtype=np.float32)
    mu = S.mean(axis=0)
    _, _, Vt = np.linalg.svd(S - mu, full_matrices=False)
    np.savez(path, mu=mu, Vt=Vt[:dim].astype(np.float32))
    return mu.astype(np.float32), Vt[:dim].astype(np.float32)


def project(V, mu, P):
    Y = (np.asarray(V, dtype=np.float32) - mu) @ P.T
    Y /= np.maximum(np.linalg.norm(Y, axis=1, keepdims=True), 1e-6)
    return Y


def write_vector_index(X, C, primary):
    """Compact meaning vectors for semantic ("fuzzy") search in the browser.

    The 768-d abstract vectors are reduced to 256 dimensions (PCA) and stored as int8.
    v/pca.bin.gz       : uint32 in_dim, uint32 out_dim, float32 mu[in_dim], float32 P[out_dim][in_dim]
                         (used by the local query embedder to project a query into the same space)
    v/centroids.bin.gz : uint32 k, uint32 dim, float32 scale[k], int8 centroid[k][dim]   (projected directions)
    v/NNN.bin.gz       : papers whose main direction is NNN, by paper number:
                         uint32 n, uint32 dim, int32 sid[n], float32 scale[n], int8 vec[n][dim]
    A paper's vector is vec * scale; the browser probes the closest directions and scores their papers.
    """
    vdir = OUT / "v"; vdir.mkdir(parents=True, exist_ok=True)
    for old in vdir.glob("*.bin.gz"):
        old.unlink()
    mu, P = fit_pca(X)
    k, dim = C.shape[0], P.shape[0]
    with gzip.open(vdir / "pca.bin.gz", "wb") as fh:
        fh.write(np.array([P.shape[1], dim], dtype=np.uint32).tobytes() + mu.tobytes() + P.astype(np.float32).tobytes())

    def q8(Y):
        sc = (np.abs(Y).max(axis=1) / 127.0).astype(np.float32)
        sc[sc == 0] = 1.0
        return sc, np.round(Y / sc[:, None]).astype(np.int8)

    sc, q = q8(project(C, mu, P))
    with gzip.open(vdir / "centroids.bin.gz", "wb") as fh:
        fh.write(np.array([k, dim], dtype=np.uint32).tobytes() + sc.tobytes() + q.tobytes())
    total = 0
    for t in range(k):
        sids = np.flatnonzero(primary == t).astype(np.int32)
        sc, q = q8(project(X[sids], mu, P)) if len(sids) else (np.zeros(0, np.float32), np.zeros((0, dim), np.int8))
        with gzip.open(vdir / f"{t:03d}.bin.gz", "wb", compresslevel=6) as fh:
            fh.write(np.array([len(sids), dim], dtype=np.uint32).tobytes() + sids.tobytes() + sc.tobytes() + q.tobytes())
        total += len(sids)
    print(f"vector index: {total} papers in {k} shards, {dim}-d int8")


def phase_build(a):
    t0 = time.time()
    docs = load_docs(); N = len(docs)
    ids, cache = merge_chunks(docs)
    pos = {k: i for i, k in enumerate(ids)}
    rows = np.array([pos.get(r["arxiv_id"], -1) for r in docs])
    missing = int((rows < 0).sum())
    if missing:
        print(f"warning: {missing} papers have no vector yet (run `directions.py export` + embed_gpu.py); they get no direction")
    dim = cache.shape[1]
    X = np.zeros((N, dim), dtype=np.float16)
    ok = rows >= 0
    X[ok] = np.asarray(cache)[rows[ok]]
    print(f"vectors ready: {N} papers, dim {dim} ({time.time() - t0:.0f}s)", flush=True)

    model_path = CACHE / "directions_model.npy"
    if a.refit or not model_path.exists():
        C = spherical_kmeans(X[ok], a.k)
        np.save(model_path, C)
        print(f"fitted {len(C)} directions ({time.time() - t0:.0f}s)", flush=True)
    C = np.load(model_path); k = len(C)
    fp = fingerprint(C)

    # Assign: best direction always; up to two more that are almost as close.
    labels = np.full((N, MAX_LABELS), 255, dtype=np.uint8)
    best_sim = np.zeros(N, dtype=np.float32)
    for i in range(0, N, 20000):
        S = X[i:i + 20000].astype(np.float32) @ C.T
        top = np.argsort(-S, axis=1)[:, :MAX_LABELS]
        ts = np.take_along_axis(S, top, axis=1)
        keep = ts >= (ts[:, :1] - SECONDARY_GAP)
        keep[:, 0] = True
        lab = np.where(keep, top, 255).astype(np.uint8)
        lab[~ok[i:i + 20000]] = 255
        labels[i:i + 20000] = lab
        best_sim[i:i + 20000] = ts[:, 0]
    primary = labels[:, 0].astype(np.int32); primary[primary == 255] = -1
    print(f"assigned; avg directions per paper {(labels != 255).sum(1)[ok].mean():.2f} ({time.time() - t0:.0f}s)", flush=True)

    # Monthly counts per direction (a paper counts for every direction it belongs to).
    months = sorted({r["created"][:7] for r in docs})
    mi = {m: j for j, m in enumerate(months)}
    month_of = np.array([mi[r["created"][:7]] for r in docs])
    month_docs = np.bincount(month_of, minlength=len(months))
    counts = np.zeros((k, len(months)), dtype=np.int32)
    for j in range(MAX_LABELS):
        sel = labels[:, j] != 255
        np.add.at(counts, (labels[sel, j].astype(np.int64), month_of[sel]), 1)
    last = len(months) - 1
    recent = slice(last - 2, last + 1); base = slice(max(0, last - 14), last - 2)
    r_docs, b_docs = month_docs[recent].sum(), month_docs[base].sum()
    rc, bc = counts[:, recent].sum(1), counts[:, base].sum(1)
    growth = (rc / r_docs + 1e-4) / (bc / b_docs + 1e-4)
    score = np.log(growth) * np.log1p(rc)

    kw = keywords(docs, primary, k)
    names = json.loads(NAMES.read_text(encoding="utf-8")) if NAMES.exists() else {}
    named = names.get("directions", {}) if names.get("fingerprint") == fp else {}
    if not named:
        print(f"note: no names for fingerprint {fp}; using keywords (see sem/name_candidates.json)")

    def card(i):
        r = docs[i]; return [int(i), r["title"], r["created"], r["arxiv_id"], r["categories"][0]]

    directions, candidates = [], []
    recent_mask = month_of >= last - 2
    for t in range(k):
        members = np.flatnonzero((labels == t).any(1))
        core = members[np.argsort(-(X[members].astype(np.float32) @ C[t]))]
        rep = core[:12]
        latest_pool = core[recent_mask[core]][:60]
        latest = latest_pool[np.argsort([docs[i]["created"] for i in latest_pool])[::-1]][:15]
        nm = named.get(str(t), {})
        cats = Counter(docs[i]["categories"][0] for i in members)
        directions.append({
            "id": t, "name": nm.get("zh") or " · ".join(kw[t][:3]), "name_en": nm.get("en", ""), "desc": nm.get("desc", ""),
            "keywords": kw[t], "size": int(len(members)), "recent": int(rc[t]), "base": int(bc[t]),
            "growth": round(float(growth[t]), 3), "score": round(float(score[t]), 3),
            "main_category": cats.most_common(1)[0][0] if cats else "",
            "monthly": [[months[j], int(counts[t, j])] for j in range(len(months))],
            "representative": [card(i) for i in rep], "latest": [card(i) for i in latest],
        })
        candidates.append({"id": t, "size": int(len(members)), "keywords": kw[t], "titles": [docs[i]["title"] for i in core[:15]]})
    (SEM / "name_candidates.json").write_text(json.dumps({"fingerprint": fp, "directions": candidates}, ensure_ascii=False, indent=1), encoding="utf-8")

    trends_path = OUT / "trends.json.gz"
    old = json.loads(gzip.open(trends_path, "rt", encoding="utf-8").read()) if trends_path.exists() else {}
    out = {"method": "semantic", "fingerprint": fp,
           "window": {"recent": [months[last - 2], months[last]], "base": [months[base.start], months[last - 3]]},
           "months": months, "month_docs": month_docs.tolist(), "categories": old.get("categories", []),
           "directions": sorted(directions, key=lambda d: -d["score"])}
    with gzip.open(trends_path, "wt", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    with gzip.open(OUT / "dir.bin.gz", "wb") as fh:
        fh.write(labels.tobytes())
    write_vector_index(X, C, primary)
    man = json.loads((OUT / "manifest.json").read_text(encoding="utf-8"))
    man["directions"] = k
    (OUT / "manifest.json").write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {k} directions ({time.time() - t0:.0f}s)")
    for d in out["directions"][:15]:
        print(f"  {d['score']:+.2f} x{d['growth']:<5} recent={d['recent']:<5} {d['name']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=("export", "build"))
    ap.add_argument("--refit", action="store_true")
    ap.add_argument("--k", type=int, default=K_DEFAULT)
    a = ap.parse_args()
    {"export": phase_export, "build": phase_build}[a.phase](a)


if __name__ == "__main__":
    main()
