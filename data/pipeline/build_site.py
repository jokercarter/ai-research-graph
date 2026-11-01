#!/usr/bin/env python3
"""Build the static, sharded data the website reads (public/research/data).

Visitors never download the whole corpus. The browser fetches only:
  manifest.json            snapshot version, counts, shard sizes
  meta.bin.gz              4 bytes per paper (year, topic, citation bucket) for filters and ranking
  t/XXXX.json.gz           search postings for the query terms (term hash -> shard)
  l/XXXX.json.gz           result cards for a block of papers (papers are numbered topic by topic,
                           so the results of one query usually fall in a few blocks)
  p/XXXX.json.gz           full paper detail + related papers, references and cited-by cards
  k/XX.json.gz             stable paper key (arXiv id / OpenAlex id) -> number in this snapshot
  topics.json.gz, topic/N.json.gz   research areas, frontier topics and their paper lists
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import re
import shutil
import sqlite3
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from common import ROOT, WORK
from textutil import bigrams, fnv1a, tokens

DB = Path(os.environ.get("AIRG_DB") or (WORK / "corpus.sqlite"))
LITE_PER = 400
DETAIL_PER = 250
TERM_SHARDS = 4096
KEY_SHARDS = 256
POSTING_CAP = 1500
YEAR0 = 2000


def wjson(path: Path, obj) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with gzip.open(path, "wb", compresslevel=9) as fh:
        fh.write(data)
    return path.stat().st_size


def short_venue(v: str) -> str:
    v = (v or "").strip()
    return v[:80]


def key_of(arxiv_id, openalex_id) -> str:
    return arxiv_id if arxiv_id else openalex_id


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--out", type=Path, default=ROOT / "public" / "research" / "data")
    ap.add_argument("--frontier", type=Path, default=WORK / "topics" / "frontier.json")
    args = ap.parse_args()
    t0 = time.time()
    out = args.out
    tmp = out.with_name(out.name + ".building")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    con = sqlite3.connect(DB)
    frontier = json.loads(args.frontier.read_text(encoding="utf-8"))

    topic_of = dict(con.execute("SELECT pid, topic FROM topic_assign"))
    sim_of = dict(con.execute("SELECT pid, sim FROM topic_assign"))
    rows = con.execute("""SELECT pid, arxiv_id, openalex_id, title, abstract, authors, n_authors, date, year, categories,
                                 primary_cat, venue, journal_ref, doi, cites, influential, first_snapshot, source FROM papers""").fetchall()
    rows.sort(key=lambda r: (topic_of.get(r[0], 10**6), -(r[14] or 0), r[0]))
    sid_of = {r[0]: i for i, r in enumerate(rows)}
    N = len(rows)
    print(f"{N} papers ordered ({time.time() - t0:.0f}s)", flush=True)

    # ---- per-paper compact arrays -------------------------------------------------
    year = np.zeros(N, dtype=np.uint8); topic = np.zeros(N, dtype=np.uint16); cb = np.zeros(N, dtype=np.uint8)
    title_of = [None] * N; cites_of = np.zeros(N, dtype=np.int32); yr_of = np.zeros(N, dtype=np.int16)
    for i, r in enumerate(rows):
        y = r[8] or 0
        year[i] = max(0, min(255, y - YEAR0)) if y else 0
        topic[i] = topic_of.get(r[0], 65535) if topic_of.get(r[0], -1) >= 0 else 65535
        c = r[14] or 0
        cites_of[i] = c
        cb[i] = min(255, int(round(math.log1p(c) * 20)))
        title_of[i] = r[3]; yr_of[i] = y
    meta = np.zeros((N, 4), dtype=np.uint8)
    meta[:, 0] = year; meta[:, 1] = topic & 0xFF; meta[:, 2] = topic >> 8; meta[:, 3] = cb
    with gzip.open(tmp / "meta.bin.gz", "wb", compresslevel=9) as fh:
        fh.write(meta.tobytes())

    key_by_sid = [key_of(r[1], r[2]) for r in rows]
    new_by_sid = bytearray(1 if (r[16] == args.snapshot and r[17] == "arxiv") else 0 for r in rows)

    def card(sid: int):
        t = title_of[sid]
        return [sid, t if len(t) <= 140 else t[:137] + "…", int(yr_of[sid]), int(cites_of[sid]), key_by_sid[sid], new_by_sid[sid]]

    # ---- lite result cards ----------------------------------------------------------
    for start in range(0, N, LITE_PER):
        block = []
        for i in range(start, min(N, start + LITE_PER)):
            r = rows[i]
            authors = json.loads(r[5] or "[]")
            names = ", ".join(authors[:3]) + (" 等" if (r[6] or len(authors)) > 3 else "")
            ab = r[4] or ""
            block.append([r[3], r[8] or 0, short_venue(r[11] or r[12] or ""), r[14] or 0, names,
                          ab[:220] + ("…" if len(ab) > 220 else ""), int(topic[i]) if topic[i] != 65535 else -1,
                          key_of(r[1], r[2]), 1 if (r[16] == args.snapshot and r[17] == "arxiv") else 0, (r[7] or "")[:7]])
        wjson(tmp / "l" / f"{start // LITE_PER:04d}.json.gz", block)
    print(f"lite cards ({time.time() - t0:.0f}s)", flush=True)

    # ---- key map ------------------------------------------------------------------------
    keys = defaultdict(dict)
    for i, r in enumerate(rows):
        k = key_of(r[1], r[2])
        keys[fnv1a(k) % KEY_SHARDS][k] = i
    for s in range(KEY_SHARDS):
        wjson(tmp / "k" / f"{s:02x}.json.gz", keys.get(s, {}))
    del keys

    # ---- citation lists -------------------------------------------------------------
    refs = defaultdict(list); cited = defaultdict(list)
    for s, d in con.execute("SELECT src, dst FROM edges"):
        if s in sid_of and d in sid_of:
            a, b = sid_of[s], sid_of[d]
            refs[a].append(b); cited[b].append(a)
    print(f"edges indexed ({time.time() - t0:.0f}s)", flush=True)

    related = {}
    for pid, sem, coc, cpl in con.execute("SELECT pid, sem, coc, cpl FROM related"):
        related[sid_of[pid]] = (json.loads(sem), json.loads(coc), json.loads(cpl))
    print(f"related loaded ({time.time() - t0:.0f}s)", flush=True)

    # ---- paper detail shards ---------------------------------------------------------
    REASON = {"sem": 1, "coc": 2, "cpl": 4}
    W = {"sem": 1.0, "coc": 0.9, "cpl": 0.8}
    for start in range(0, N, DETAIL_PER):
        block = []
        for i in range(start, min(N, start + DETAIL_PER)):
            r = rows[i]
            comb: dict[int, list] = {}
            sem, coc, cpl = related.get(i, ([], [], []))
            for name, lst in (("sem", sem), ("coc", coc), ("cpl", cpl)):
                for rank, item in enumerate(lst):
                    j = sid_of.get(item[0])
                    if j is None or j == i:
                        continue
                    e = comb.setdefault(j, [0.0, 0])
                    e[0] += W[name] * (1 - rank / 12); e[1] |= REASON[name]
            rset, cset = set(refs.get(i, ())), set(cited.get(i, ()))
            for j in comb:
                if j in rset:
                    comb[j][1] |= 8
                if j in cset:
                    comb[j][1] |= 16
            best = sorted(comb.items(), key=lambda kv: -kv[1][0])[:12]
            rr = sorted(refs.get(i, []), key=lambda j: -cites_of[j])
            cc = sorted(cited.get(i, []), key=lambda j: -cites_of[j])
            authors = json.loads(r[5] or "[]")
            block.append({
                "t": r[3], "au": authors[:12], "na": r[6] or len(authors), "d": r[7] or "", "y": r[8] or 0,
                "v": short_venue(r[11] or ""), "jr": r[12] or "", "cat": (r[9] or "").split()[:6] if r[17] == "arxiv" else [],
                "kw": (r[9] or "") if r[17] != "arxiv" else "",
                "ab": r[4] or "", "ax": r[1] or "", "oa": r[2] or "", "doi": r[13] or "", "c": r[14] or 0, "inf": r[15] or 0,
                "tp": int(topic[i]) if topic[i] != 65535 else -1, "new": 1 if (r[16] == args.snapshot and r[17] == "arxiv") else 0,
                "src": r[17], "k": key_of(r[1], r[2]),
                "rel": [card(j) + [round(s_, 2), m] for j, (s_, m) in best],
                "refs": [card(j) for j in rr[:10]], "nrefs": len(rr),
                "cited": [card(j) for j in cc[:10]], "ncited": len(cc),
            })
        wjson(tmp / "p" / f"{start // DETAIL_PER:04d}.json.gz", block)
    print(f"detail shards ({time.time() - t0:.0f}s)", flush=True)
    del related

    # ---- search postings (field-weighted BM25 impacts) ----------------------------------
    # Pass 1: document frequencies and lengths. Title words count 3x; title bigrams are indexed for phrases.
    def doc_tf(r):
        tt = tokens(r[3]); ta = tokens(r[4] or "")
        tf = Counter()
        for w in tt: tf[w] += 3.0
        for w in ta: tf[w] += 1.0
        for bg in set(bigrams(tt)): tf[bg] += 3.0
        return tf, 3 * len(tt) + len(ta)
    df = Counter(); lens = np.zeros(N, dtype=np.float32)
    for i, r in enumerate(rows):
        tf, ln = doc_tf(r); lens[i] = ln; df.update(tf.keys())
    vocab = {w: j for j, (w, n) in enumerate(df.items()) if n >= 2 or " " not in w}
    words = [None] * (max(vocab.values()) + 1 if vocab else 0)
    for w, j in vocab.items():
        words[j] = w
    dfa = np.zeros(len(words), dtype=np.int32)
    for w, j in vocab.items():
        dfa[j] = df[w]
    del df
    print(f"vocabulary: {len(vocab)} terms ({time.time() - t0:.0f}s)", flush=True)
    avg = float(lens.mean()) or 1.0
    k1, bpar = 1.2, 0.75
    T, D, S = [], [], []
    bt, bd, bs = [], [], []
    for i, r in enumerate(rows):
        tf, _ = doc_tf(r)
        norm = k1 * (1 - bpar + bpar * lens[i] / avg)
        for w, f in tf.items():
            j = vocab.get(w)
            if j is None:
                continue
            n = dfa[j]
            bt.append(j); bd.append(i); bs.append(math.log(1 + (N - n + 0.5) / (n + 0.5)) * f * (k1 + 1) / (f + norm))
        if len(bt) > 3_000_000:
            T.append(np.array(bt, dtype=np.int32)); D.append(np.array(bd, dtype=np.int32)); S.append(np.array(bs, dtype=np.float32)); bt, bd, bs = [], [], []
    T.append(np.array(bt, dtype=np.int32)); D.append(np.array(bd, dtype=np.int32)); S.append(np.array(bs, dtype=np.float32))
    T = np.concatenate(T); D = np.concatenate(D); S = np.concatenate(S)
    del vocab
    print(f"postings: {len(T)} ({time.time() - t0:.0f}s)", flush=True)
    order = np.lexsort((-S, T))  # by term, then impact descending
    T, D, S = T[order], D[order], S[order]
    del order
    scale = 255.0 / float(S.max() or 1)
    bounds = np.flatnonzero(np.diff(T)) + 1
    starts = np.concatenate([[0], bounds]); ends = np.concatenate([bounds, [len(T)]])
    shards = defaultdict(dict)
    for st, en in zip(starts, ends):
        j = int(T[st]); en = min(en, st + POSTING_CAP)
        d = D[st:en]; sc = S[st:en]
        o = np.argsort(d, kind="stable"); d = d[o]; sc = sc[o]
        delta = np.diff(d, prepend=0)
        q = np.maximum(1, np.rint(sc * scale)).astype(np.int32)
        flat = np.empty(2 * len(d), dtype=np.int64); flat[0::2] = delta; flat[1::2] = q
        w = words[j]
        shards[fnv1a(w) % TERM_SHARDS][w] = [int(dfa[j])] + flat.tolist()
    del T, D, S
    for s_ in range(TERM_SHARDS):
        wjson(tmp / "t" / f"{s_:04d}.json.gz", shards.get(s_, {}))
    del shards
    print(f"term shards ({time.time() - t0:.0f}s)", flush=True)

    # ---- topics -------------------------------------------------------------------------
    tsum, areas = [], frontier["areas"]
    for tp in frontier["topics"]:
        sids = lambda lst: [sid_of[p] for p in lst if p in sid_of]
        full = dict(tp)
        for f in ("key_papers", "hot_papers", "new_papers", "representative", "foundations"):
            full[f] = [card(j) for j in sids(tp[f])]
        wjson(tmp / "topic" / f"{tp['id']}.json.gz", full)
        tsum.append({k: tp[k] for k in ("id", "area", "keywords", "main_category", "size", "recent_6m", "prev_6m", "last_12m",
                                         "new_this_snapshot", "growth", "momentum", "emerging", "frontier_score")}
                    | {"spark": [n for _, n in tp["monthly"]][-24:], "rep": [card(j) for j in sids(tp["representative"])[:3]]})
    wjson(tmp / "topics.json.gz", {"snapshot": args.snapshot, "windows": frontier["windows"], "areas": areas, "topics": tsum})

    stats = dict(con.execute("SELECT source, COUNT(*) FROM papers GROUP BY source").fetchall())
    n_edges = con.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    years = dict(con.execute("SELECT year, COUNT(*) FROM papers GROUP BY year").fetchall())
    new_count = con.execute("SELECT COUNT(*) FROM papers WHERE first_snapshot=? AND source='arxiv'", (args.snapshot,)).fetchone()[0]
    manifest = {
        "version": args.snapshot, "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "papers": N, "arxiv_papers": stats.get("arxiv", 0), "citationcs_papers": stats.get("citationcs", 0),
        "edges": n_edges, "new_papers": new_count, "years": {str(k): v for k, v in years.items() if k},
        "lite_per": LITE_PER, "detail_per": DETAIL_PER, "term_shards": TERM_SHARDS, "key_shards": KEY_SHARDS,
        "year0": YEAR0, "impact_scale": scale, "topics": len(tsum), "areas": len(areas),
        "sources": [
            {"name": "arXiv", "use": "论文元数据与摘要", "url": "https://arxiv.org"},
            {"name": "Semantic Scholar", "use": "引用数与参考文献", "url": "https://www.semanticscholar.org"},
            {"name": "CitationCS (Zenodo 16537950)", "use": "2016–2024 年会议/期刊论文及其引用边", "url": "https://zenodo.org/records/16537950"},
        ],
    }
    (tmp / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    if out.exists():
        old = out.with_name(out.name + ".previous")
        if old.exists():
            shutil.rmtree(old)
        out.rename(old)
    tmp.rename(out)
    total = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"site data written to {out}: {sum(1 for _ in out.rglob('*.gz'))} files, {total / 1e6:.0f} MB ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
