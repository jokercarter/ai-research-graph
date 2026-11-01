#!/usr/bin/env python3
"""Simple site build: recent arXiv AI papers -> sharded static search site.

Phases (each runs in a few minutes; run them in order, or `all`):
  docs     read harvested arXiv records, keep papers created in the window, de-duplicate
  cards    result cards, paper detail shards, key map, meta.bin
  index    BM25 postings (title words weighted 3x, title bigrams for phrases)
  shards   write the posting shards
  trends1  count technical phrases per month
  trends2  pick rising phrases, attach example papers, write trends + manifest
Output: public/research/data (read by public/research/*.js).
"""
from __future__ import annotations

import argparse
import glob
import gzip
import json
import math
import os
import pickle
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from textutil import STOP, bigrams, fnv1a, tokens  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / "data" / "work"
TMP = Path(os.environ.get("AIRG_TMP") or (Path.home() / "airg_work"))
OUT = Path(os.environ.get("AIRG_OUT") or (ROOT / "public" / "research" / "data"))
LITE_PER, DETAIL_PER, TERM_SHARDS, KEY_SHARDS, POSTING_CAP, YEAR0 = 400, 200, 2048, 128, 1500, 2000
CATS = ["cs.CL", "cs.CV", "cs.LG", "cs.AI", "cs.RO", "cs.IR", "cs.CR", "cs.HC", "cs.MA", "cs.NE", "stat.ML", "cs.SD",
        "eess.AS", "eess.IV", "other"]
CAT_ZH = {"cs.CL": "自然语言处理", "cs.CV": "计算机视觉", "cs.LG": "机器学习", "cs.AI": "人工智能", "cs.RO": "机器人",
          "cs.IR": "信息检索与推荐", "cs.CR": "安全与隐私", "cs.HC": "人机交互", "cs.MA": "多智能体", "cs.NE": "神经与进化计算",
          "stat.ML": "统计机器学习", "cs.SD": "声音处理", "eess.AS": "语音与音频", "eess.IV": "图像与视频处理", "other": "其他"}


def wjson(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wb", compresslevel=6) as fh:
        fh.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def load_docs():
    with open(TMP / "docs.pkl", "rb") as fh:
        return pickle.load(fh)


def phase_docs(a):
    best = {}
    lo, hi = a.start[:7], a.end[:7]
    for path in sorted(glob.glob(str(WORK / "arxiv_records_*.jsonl.gz"))):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                r = json.loads(line)
                # The OAI "created" field follows the latest version; the arXiv id carries the v1 month.
                m = re.match(r"^(\d{2})(\d{2})\.", r["arxiv_id"])
                if not m:
                    continue
                month = f"20{m.group(1)}-{m.group(2)}"
                if month < lo or month > hi:
                    continue
                c = r.get("created") or ""
                r["created"] = c if c[:7] == month else month
                old = best.get(r["arxiv_id"])
                if old is None or (r.get("updated") or "") >= (old.get("updated") or ""):
                    best[r["arxiv_id"]] = r
    def cat_idx(r):
        p = r["categories"][0]
        return CATS.index(p) if p in CATS else len(CATS) - 1
    docs = sorted(best.values(), key=lambda r: (r["created"][:7], r["created"]), reverse=True)  # newest first
    docs.sort(key=cat_idx)  # stable: grouped by category, newest first inside
    TMP.mkdir(parents=True, exist_ok=True)
    with open(TMP / "docs.pkl", "wb") as fh:
        pickle.dump(docs, fh, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"docs: {len(docs)} papers created {a.start}..{a.end}")


def phase_cards(a):
    docs = load_docs(); N = len(docs)
    meta = np.zeros((N, 4), dtype=np.uint8)
    keys = defaultdict(dict)
    snap_month = a.end[:7]
    for i, r in enumerate(docs):
        y, m = int(r["created"][:4]), int(r["created"][5:7])
        p = r["categories"][0]
        meta[i] = (y - YEAR0, m, CATS.index(p) if p in CATS else len(CATS) - 1, 1 if r["created"][:7] == snap_month else 0)
        keys[fnv1a(r["arxiv_id"]) % KEY_SHARDS][r["arxiv_id"]] = i
    OUT.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT / "meta.bin.gz", "wb") as fh:
        fh.write(meta.tobytes())
    for s in range(KEY_SHARDS):
        wjson(OUT / "k" / f"{s:02x}.json.gz", keys.get(s, {}))
    for start in range(0, N, LITE_PER):
        block = []
        for r in docs[start:start + LITE_PER]:
            au = r["authors"]; n = r.get("n_authors") or len(au)
            ab = r["abstract"]
            block.append([r["title"], r["created"], r["categories"][0], ", ".join(au[:3]) + (" 等" if n > 3 else ""),
                          ab[:230] + ("…" if len(ab) > 230 else ""), r["arxiv_id"]])
        wjson(OUT / "l" / f"{start // LITE_PER:04d}.json.gz", block)
    for start in range(0, N, DETAIL_PER):
        block = [{"t": r["title"], "au": r["authors"][:15], "na": r.get("n_authors") or len(r["authors"]), "d": r["created"],
                  "u": r.get("updated") or "", "cat": r["categories"][:6], "ab": r["abstract"], "cm": r.get("comments") or "",
                  "jr": r.get("journal_ref") or "", "doi": r.get("doi") or "", "ax": r["arxiv_id"]} for r in docs[start:start + DETAIL_PER]]
        wjson(OUT / "p" / f"{start // DETAIL_PER:04d}.json.gz", block)
    print(f"cards: {N} papers, {math.ceil(N / LITE_PER)} result shards, {math.ceil(N / DETAIL_PER)} detail shards")


def phase_index(a):
    docs = load_docs(); N = len(docs)
    vocab: dict[str, int] = {}
    T, D, F = [], [], []
    lens = np.zeros(N, dtype=np.float32)
    bt, bd, bf = [], [], []
    for i, r in enumerate(docs):
        tt = tokens(r["title"]); ta = tokens(r["abstract"])
        tf = Counter()
        for w in tt: tf[w] += 3.0
        for w in ta: tf[w] += 1.0
        for b in set(bigrams(tt)): tf[b] += 3.0
        lens[i] = 3 * len(tt) + len(ta)
        for w, f in tf.items():
            j = vocab.get(w)
            if j is None:
                j = vocab[w] = len(vocab)
            bt.append(j); bd.append(i); bf.append(f)
        if len(bt) > 4_000_000:
            T.append(np.array(bt, np.int32)); D.append(np.array(bd, np.int32)); F.append(np.array(bf, np.float32)); bt, bd, bf = [], [], []
    T.append(np.array(bt, np.int32)); D.append(np.array(bd, np.int32)); F.append(np.array(bf, np.float32))
    T = np.concatenate(T); D = np.concatenate(D); F = np.concatenate(F)
    df = np.bincount(T, minlength=len(vocab)).astype(np.float32)
    k1, b = 1.2, 0.75
    norm = k1 * (1 - b + b * lens / float(lens.mean()))
    idf = np.log(1 + (N - df + 0.5) / (df + 0.5))
    S = idf[T] * F * (k1 + 1) / (F + norm[D])
    words = np.empty(len(vocab), dtype=object)
    for w, j in vocab.items():
        words[j] = w
    keep = df[T] >= 2
    np.savez(TMP / "postings.npz", T=T[keep], D=D[keep], S=S[keep].astype(np.float32), df=df.astype(np.int32))
    with open(TMP / "words.pkl", "wb") as fh:
        pickle.dump(list(words), fh, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"index: {len(vocab)} terms, {int(keep.sum())} postings")


def phase_shards(a):
    z = np.load(TMP / "postings.npz"); T, D, S, df = z["T"], z["D"], z["S"], z["df"]
    with open(TMP / "words.pkl", "rb") as fh:
        words = pickle.load(fh)
    order = np.lexsort((-S, T)); T, D, S = T[order], D[order], S[order]
    scale = 255.0 / float(S.max())
    bounds = np.flatnonzero(np.diff(T)) + 1
    starts = np.concatenate([[0], bounds]); ends = np.concatenate([bounds, [len(T)]])
    shards = defaultdict(dict)
    for st, en in zip(starts.tolist(), ends.tolist()):
        j = int(T[st]); en = min(en, st + POSTING_CAP)
        d = D[st:en]; q = np.maximum(1, np.rint(S[st:en] * scale)).astype(np.int64)
        o = np.argsort(d, kind="stable"); d = d[o].astype(np.int64); q = q[o]
        flat = np.empty(2 * len(d), dtype=np.int64); flat[0::2] = np.diff(d, prepend=0); flat[1::2] = q
        w = words[j]
        shards[fnv1a(w) % TERM_SHARDS][w] = [int(df[j])] + flat.tolist()
    for s in range(TERM_SHARDS):
        wjson(OUT / "t" / f"{s:04d}.json.gz", shards.get(s, {}))
    (TMP / "scale.txt").write_text(str(scale))
    print(f"shards: {len(starts)} terms in {TERM_SHARDS} files")


PHRASE_STOP = STOP | set("""paper propose proposed approach method methods model models results show shows novel new framework based
using use work task tasks performance data dataset datasets existing state art experiments experimental demonstrate achieve
achieves significantly improve improves improved effective efficient large small high low first two three one many recent
recently present introduce study studies problem problems different various however well also significant superior extensive
code available github https www com http compared furthermore moreover additionally specifically particular respectively
key important able across including including challenging challenge challenges remain remains limited lack address""".split())
WORD = re.compile(r"[a-z][a-z0-9\-]+")


def phrase_list(text: str):
    ws = [w.strip("-") for w in WORD.findall(text.lower())]
    out = set()
    for x, y in zip(ws, ws[1:]):
        if len(x) < 3 or len(y) < 2 or x in PHRASE_STOP or y in PHRASE_STOP:
            continue
        out.add(f"{x} {y}")
    return out


def h64(s: str) -> int:
    return fnv1a(s) * 1000003 + len(s)


def phase_vocab(a):
    """vocab.txt.gz: single-word search terms (already stemmed) with their document counts,
    most common first. The browser uses it to correct typos ("transfomer" -> "transformer")."""
    z = np.load(TMP / "postings.npz"); df = z["df"]
    with open(TMP / "words.pkl", "rb") as fh:
        words = pickle.load(fh)
    rows = [(w, int(df[j])) for j, w in enumerate(words)
            if df[j] >= 8 and " " not in w and w.isalpha() and 3 <= len(w) <= 24]
    rows.sort(key=lambda r: -r[1])
    with gzip.open(OUT / "vocab.txt.gz", "wt", encoding="utf-8", compresslevel=9) as fh:
        fh.write("\n".join(f"{w}\t{n}" for w, n in rows))
    print(f"vocab: {len(rows)} words")


def phase_trends1(a):
    docs = load_docs()
    ym0 = int(a.start[:4]) * 12 + int(a.start[5:7]) - 1
    H, M = [], []
    hb, mb = [], []
    for r in docs:
        mi = int(r["created"][:4]) * 12 + int(r["created"][5:7]) - 1 - ym0
        for p in phrase_list(r["title"] + " . " + r["abstract"]):
            hb.append(h64(p)); mb.append(mi)
        if len(hb) > 4_000_000:
            H.append(np.array(hb, np.int64)); M.append(np.array(mb, np.int16)); hb, mb = [], []
    H.append(np.array(hb, np.int64)); M.append(np.array(mb, np.int16))
    np.savez(TMP / "phrases.npz", H=np.concatenate(H), M=np.concatenate(M))
    print("trends1: phrase occurrences counted")


def phase_trends2(a):
    docs = load_docs(); N = len(docs)
    z = np.load(TMP / "phrases.npz"); H, M = z["H"], z["M"]
    n_months = int(M.max()) + 1
    month_docs = np.zeros(n_months, dtype=np.int64)
    ym0 = int(a.start[:4]) * 12 + int(a.start[5:7]) - 1
    for r in docs:
        month_docs[int(r["created"][:4]) * 12 + int(r["created"][5:7]) - 1 - ym0] += 1
    uniq, inv, tot = np.unique(H, return_inverse=True, return_counts=True)
    cand = tot >= 80
    idx = np.flatnonzero(cand)
    pos = -np.ones(len(uniq), dtype=np.int64); pos[idx] = np.arange(len(idx))
    sel = pos[inv] >= 0
    counts = np.zeros((len(idx), n_months), dtype=np.int32)
    np.add.at(counts, (pos[inv][sel], M[sel]), 1)
    last = n_months - 1
    recent = slice(last - 2, last + 1); base = slice(max(0, last - 14), last - 2)
    r_docs = month_docs[recent].sum(); b_docs = month_docs[base].sum()
    rc = counts[:, recent].sum(1); bc = counts[:, base].sum(1)
    r_share = rc / max(r_docs, 1); b_share = bc / max(b_docs, 1)
    growth = (r_share + 2 / max(r_docs, 1)) / (b_share + 2 / max(b_docs, 1))
    score = np.log(growth) * np.log1p(rc)
    ok = rc >= 60
    order = [i for i in np.argsort(-score) if ok[i]][:2500]
    want = {int(uniq[idx[i]]): i for i in order}
    # Recover surface text and example papers.
    text_of, papers = {}, defaultdict(list)
    for sid, r in enumerate(docs):
        for p in phrase_list(r["title"] + " . " + r["abstract"]):
            hv = h64(p)
            if hv in want:
                text_of[hv] = p
                in_title = p in r["title"].lower()
                papers[hv].append((sid, in_title, r["created"]))
    def months_label(i):
        y, m = divmod(ym0 + i, 12); return f"{y}-{m + 1:02d}"
    # Drop phrases that are fragments of a better-scoring longer pattern (shared word + mostly same papers).
    from textutil import stem
    trends, seen_sets, seen_keys = [], [], set()
    for hv, i in sorted(want.items(), key=lambda kv: -score[kv[1]]):
        plist = papers[hv]
        n_title = sum(1 for x in plist if x[1])
        if n_title < 12 or n_title < 0.04 * len(plist):
            continue  # writing patterns ("held fixed", "percentage points") rarely appear in titles
        skey = " ".join(stem(w) for w in text_of[hv].replace("-", " ").split())
        if skey in seen_keys:
            continue
        seen_keys.add(skey)
        sids = set(s for s, _, _ in plist)
        if any(len(sids & s2) > 0.6 * len(sids) for s2 in seen_sets):
            continue
        seen_sets.append(sids)
        title_hits = sorted([x for x in plist if x[1]], key=lambda x: x[2], reverse=True)
        trends.append({"id": len(trends), "phrase": text_of[hv], "recent": int(rc[i]), "base": int(bc[i]),
                       "growth": round(float(growth[i]), 2), "score": round(float(score[i]), 2),
                       "monthly": [[months_label(m), int(c)] for m, c in enumerate(counts[i])],
                       "papers": [s for s, _, _ in title_hits[:30]] or [s for s, _, _ in sorted(plist, key=lambda x: x[2], reverse=True)[:30]],
                       "total": len(plist)})
        if len(trends) >= 80:
            break
    # Monthly totals per arXiv category.
    cat_month = np.zeros((len(CATS), n_months), dtype=np.int32)
    meta = np.frombuffer(gzip.open(OUT / "meta.bin.gz").read(), dtype=np.uint8).reshape(-1, 4)
    for sid in range(N):
        y, m, c = int(meta[sid, 0]) + YEAR0, int(meta[sid, 1]), int(meta[sid, 2])
        cat_month[c, y * 12 + m - 1 - ym0] += 1
    def card(sid):
        r = docs[sid]; return [sid, r["title"], r["created"], r["arxiv_id"], r["categories"][0]]
    out = {"window": {"recent": [months_label(last - 2), months_label(last)], "base": [months_label(base.start), months_label(last - 3)]},
           "months": [months_label(i) for i in range(n_months)], "month_docs": month_docs.tolist(),
           "categories": [{"id": c, "name": CAT_ZH[c], "monthly": cat_month[k].tolist(), "total": int(cat_month[k].sum())} for k, c in enumerate(CATS)],
           "trends": [dict(t, papers=[card(s) for s in t["papers"]]) for t in trends]}
    wjson(OUT / "trends.json.gz", out)
    manifest = {"version": a.end, "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "papers": N,
                "window": [a.start, a.end], "new_papers": int(month_docs[last]), "new_month": months_label(last),
                "lite_per": LITE_PER, "detail_per": DETAIL_PER, "term_shards": TERM_SHARDS, "key_shards": KEY_SHARDS,
                "year0": YEAR0, "categories": CATS, "category_names": CAT_ZH,
                "sources": [{"name": "arXiv", "use": "论文元数据与摘要（OAI-PMH）", "url": "https://arxiv.org"}]}
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"trends2: {len(trends)} rising phrases; manifest written")
    for t in trends[:25]:
        print(f"  {t['phrase']:<40} recent={t['recent']:<5} base={t['base']:<5} x{t['growth']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=("docs", "cards", "index", "shards", "vocab", "trends1", "trends2", "all"))
    ap.add_argument("--start", default="2024-11-01")
    ap.add_argument("--end", default="2026-10-31")
    a = ap.parse_args()
    phases = ["docs", "cards", "index", "shards", "vocab", "trends1", "trends2"] if a.phase == "all" else [a.phase]
    for ph in phases:
        t = time.time(); globals()[f"phase_{ph}"](a); print(f"[{ph}] {time.time() - t:.0f}s", flush=True)


if __name__ == "__main__":
    main()
