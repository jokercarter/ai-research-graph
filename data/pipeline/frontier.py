#!/usr/bin/env python3
"""Research directions and frontier signals.

1. Topics: spherical k-means over paper embeddings (centroids are saved, so the
   monthly run only assigns new papers and topic ids stay stable; --refit
   rebuilds them, e.g. once a year).
2. Areas: topics grouped into broad research areas.
3. Labels: class-based TF-IDF keywords over titles and abstracts.
4. Signals per topic, relative to the whole corpus so overall arXiv growth does
   not make every topic look hot:
     growth     share of the last 6 months vs share of the 6 months before
     momentum   citations of 6-18-month-old papers vs the corpus median
     emerging   share of all topic papers that appeared in the last 12 months
   frontier_score combines them; every number is shipped so the UI can explain it.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import time
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import numpy as np

from common import WORK
from related import assign_all, kmeans, load_embeddings

DB = Path(os.environ.get("AIRG_DB") or (WORK / "corpus.sqlite"))
EMB = WORK / "embeddings"
STATE = WORK / "topics"

AREA_NAMES = {
    "cs.CV": "计算机视觉", "cs.CL": "自然语言处理", "cs.LG": "机器学习", "stat.ML": "机器学习",
    "cs.AI": "人工智能", "cs.RO": "机器人", "cs.IR": "信息检索与推荐", "cs.CR": "安全与隐私",
    "cs.HC": "人机交互", "cs.MA": "多智能体", "cs.NE": "神经与进化计算", "cs.SD": "语音与音频",
    "eess.AS": "语音与音频", "eess.IV": "图像与视频处理",
}
GENERIC = set("""paper propose proposed approach method methods results result model models based using use used
show shows novel new existing work task tasks performance framework learning data datasets dataset
problem problems study studies different various also however well state art state-of-the-art demonstrate
experiments experimental achieve achieves significantly improve improves improved effective efficient
large small high low first two three one many recent recently paper proposes introduce present
proposed method propose novel extensive experiments outperforms baseline baselines code available github
com https http www significant compared furthermore moreover""".split())


def months_back(snapshot: date, n: int) -> str:
    y, m = snapshot.year, snapshot.month - n
    while m <= 0:
        m += 12; y -= 1
    return f"{y:04d}-{m:02d}"


def label_topics(texts_by_topic: dict[int, list[str]], k: int) -> dict[int, list[str]]:
    from sklearn.feature_extraction.text import TfidfVectorizer
    docs = [" ".join(texts_by_topic.get(t, [])) for t in range(k)]
    vec = TfidfVectorizer(ngram_range=(1, 2), stop_words="english", sublinear_tf=True, min_df=1, max_df=0.25,
                          max_features=300000, token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z0-9\-]{2,}\b")
    M = vec.fit_transform(docs)
    vocab = np.array(vec.get_feature_names_out())
    out = {}
    for t in range(k):
        row = M[t].toarray().ravel()
        terms = []
        for j in np.argsort(-row)[:80]:
            term = vocab[j]
            words = term.split()
            if any(w in GENERIC for w in words):
                continue
            if any(term in x or x in term for x in terms):
                continue
            terms.append(term)
            if len(terms) == 6:
                break
        out[t] = terms
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", required=True, help="Snapshot date YYYY-MM-DD")
    ap.add_argument("--topics", type=int, default=300)
    ap.add_argument("--areas", type=int, default=28)
    ap.add_argument("--refit", action="store_true")
    args = ap.parse_args()
    snap = date.fromisoformat(args.snapshot)
    STATE.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    n_pid = con.execute("SELECT MAX(pid) FROM papers").fetchone()[0] + 1
    t0 = time.time()

    X, row_of, pid_of = load_embeddings(EMB, n_pid)
    cpath, apath = STATE / "topic_centroids.npy", STATE / "area_of_topic.npy"
    if args.refit or not cpath.exists():
        arxiv_rows = np.array([row_of[p] for (p,) in con.execute("SELECT pid FROM papers WHERE source='arxiv'") if row_of[p] >= 0])
        C = kmeans(X[arxiv_rows], args.topics, sample=min(len(arxiv_rows), 200000), seed=1, iters=30)
        A = kmeans(C, args.areas, sample=len(C), seed=2, iters=50)
        area_of = np.argmax(C @ A.T, axis=1).astype(np.int32)
        np.save(cpath, C.astype(np.float32)); np.save(apath, area_of)
    C = np.load(cpath); area_of = np.load(apath)
    k = len(C)
    top2 = assign_all(X, C, top=1)[:, 0]
    sims = np.einsum("ij,ij->i", X.astype(np.float32), C[top2])
    topic_of = np.full(n_pid, -1, dtype=np.int32); topic_of[pid_of] = top2
    sim_of = np.zeros(n_pid, dtype=np.float32); sim_of[pid_of] = sims
    del X
    print(f"assigned {len(pid_of)} papers to {k} topics ({time.time() - t0:.0f}s)", flush=True)

    con.execute("DROP TABLE IF EXISTS topic_assign")
    con.execute("CREATE TABLE topic_assign (pid INTEGER PRIMARY KEY, topic INTEGER, sim REAL)")
    con.executemany("INSERT INTO topic_assign VALUES(?,?,?)", ((int(p), int(topic_of[p]), float(sim_of[p])) for p in pid_of))
    con.commit()

    m_recent, m_prev, m_12 = months_back(snap, 5), months_back(snap, 11), months_back(snap, 11)
    m_mom_lo, m_mom_hi = months_back(snap, 17), months_back(snap, 6)
    timeline_start = months_back(snap, 35)
    monthly = defaultdict(Counter)
    size = Counter(); recent = Counter(); prev = Counter(); last12 = Counter(); new_snap = Counter()
    cats = defaultdict(Counter)
    mom = defaultdict(list); all_mom = []
    papers = defaultdict(list)  # topic -> (pid, cites, influential, month, sim, is_new)
    texts = defaultdict(list)
    for pid, title, abstract, d, cites, infl, prim, first, source in con.execute(
            "SELECT pid, title, abstract, date, cites, influential, primary_cat, first_snapshot, source FROM papers"):
        t = int(topic_of[pid])
        if t < 0:
            continue
        month = (d or "")[:7]
        size[t] += 1
        if source == "arxiv":
            cats[t][prim] += 1
        if month >= timeline_start:
            monthly[t][month] += 1
        if month >= m_recent: recent[t] += 1
        elif month >= m_prev: prev[t] += 1
        if month >= m_12: last12[t] += 1
        if first == args.snapshot and source == "arxiv": new_snap[t] += 1
        if m_mom_lo <= month <= m_mom_hi:
            mom[t].append(cites or 0); all_mom.append(cites or 0)
        papers[t].append((pid, cites or 0, infl or 0, month, float(sim_of[pid]), first == args.snapshot))
        if len(texts[t]) < 1500:
            texts[t].append(title or "")
            if abstract and len(texts[t]) < 700:
                texts[t].append(abstract[:600])
    print(f"scanned corpus ({time.time() - t0:.0f}s)", flush=True)

    labels = label_topics(texts, k)
    tot_recent = sum(recent.values()) or 1; tot_prev = sum(prev.values()) or 1
    med_all = float(np.median(all_mom)) if all_mom else 0.0
    rows = []
    for t in range(k):
        share_r = recent[t] / tot_recent; share_p = prev[t] / tot_prev
        growth = (share_r + 1e-5) / (share_p + 1e-5)
        momentum = (float(np.mean(mom[t])) + 1) / (float(np.mean(all_mom) if all_mom else 0) + 1) if mom[t] else 0.0
        emerging = last12[t] / size[t] if size[t] else 0.0
        rows.append(dict(t=t, growth=growth, momentum=momentum, emerging=emerging))
    def z(vals):
        v = np.array(vals, dtype=float); return (v - v.mean()) / (v.std() + 1e-9)
    zs = 1.0 * z([np.log(r["growth"]) for r in rows]) + 0.6 * z([np.log1p(r["momentum"]) for r in rows]) \
        + 0.6 * z([r["emerging"] for r in rows]) + 0.3 * z([np.log1p(recent[r["t"]]) for r in rows])

    # Foundation papers: most-cited targets of each topic's papers.
    found = defaultdict(Counter)
    for s, d in con.execute("SELECT src, dst FROM edges"):
        ts = int(topic_of[s]) if s < n_pid else -1
        if ts >= 0:
            found[ts][d] += 1

    topics = []
    for r, score in zip(rows, zs):
        t = r["t"]
        plist = papers[t]
        key = sorted(plist, key=lambda x: -(x[1] + 3 * x[2]))[:12]
        hot = sorted([x for x in plist if x[3] >= m_12], key=lambda x: -(x[1] + 3 * x[2] + 2 * x[4]))[:12]
        newest = sorted([x for x in plist if x[5]], key=lambda x: (-x[4], x[0]))[:12]
        rep = sorted([x for x in plist if x[1] >= 1 or x[5]], key=lambda x: -x[4])[:6]
        main_cat = cats[t].most_common(1)[0][0] if cats[t] else "cs.AI"
        topics.append({
            "id": t, "area": int(area_of[t]), "keywords": labels[t], "main_category": main_cat,
            "category_mix": [[c, n] for c, n in cats[t].most_common(4)],
            "size": size[t], "recent_6m": recent[t], "prev_6m": prev[t], "last_12m": last12[t], "new_this_snapshot": new_snap[t],
            "growth": round(r["growth"], 3), "momentum": round(r["momentum"], 3), "emerging": round(r["emerging"], 3),
            "frontier_score": round(float(score), 3),
            "monthly": sorted(monthly[t].items()),
            "key_papers": [x[0] for x in key], "hot_papers": [x[0] for x in hot],
            "new_papers": [x[0] for x in newest], "representative": [x[0] for x in rep],
            "foundations": [p for p, _ in found[t].most_common(10)],
        })
    areas = []
    for a in range(int(area_of.max()) + 1):
        ts = [x for x in topics if x["area"] == a]
        if not ts:
            continue
        cat = Counter()
        for x in ts:
            for c, n in x["category_mix"]:
                cat[c] += n
        main = cat.most_common(1)[0][0]
        kw = Counter()
        for x in sorted(ts, key=lambda x: -x["size"])[:6]:
            for w in x["keywords"][:2]:
                kw[w] += x["size"]
        areas.append({"id": a, "name": AREA_NAMES.get(main, main), "main_category": main,
                      "keywords": [w for w, _ in kw.most_common(4)], "topics": [x["id"] for x in ts],
                      "size": sum(x["size"] for x in ts), "recent_6m": sum(x["recent_6m"] for x in ts)})
    # Disambiguate areas that share a Chinese category name with their top keyword.
    seen = Counter(a["name"] for a in areas)
    for a in areas:
        if seen[a["name"]] > 1 and a["keywords"]:
            a["name"] = f"{a['name']} · {a['keywords'][0]}"
    out = {"snapshot": args.snapshot, "windows": {"recent_from": m_recent, "prev_from": m_prev, "momentum": [m_mom_lo, m_mom_hi]},
           "corpus_momentum_median": med_all, "topics": topics, "areas": areas}
    (STATE / "frontier.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    best = sorted(topics, key=lambda x: -x["frontier_score"])[:12]
    for x in best:
        print(f"{x['frontier_score']:+.2f} growth={x['growth']:.2f} recent={x['recent_6m']} {x['main_category']} {' · '.join(x['keywords'][:3])}")
    print(f"frontier done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
