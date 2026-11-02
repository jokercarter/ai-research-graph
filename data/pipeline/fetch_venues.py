#!/usr/bin/env python3
"""Papers from top AI/CS conferences and journals (including ones never posted to arXiv).

Source: Semantic Scholar bulk search (title, abstract, authors, date, DOI / arXiv id), no key
needed. One file per venue and year in data/work/venues/ (AIRG_VENUES overrides the folder):
  venue_<slug>_<year>.jsonl.gz   + venue_state.json (resumable, time-budgeted)

build_simple.py merges these with the arXiv records and removes duplicates: a venue paper that
is on arXiv (same arXiv id, DOI or title) is not added again; instead the arXiv paper is marked
with the venue ("CVPR 2023").

  python fetch_venues.py --years 2021-2026            first run
  python fetch_venues.py --years 2025-2026 --refresh  monthly: refetch the recent years
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(os.environ.get("AIRG_VENUES") or (ROOT / "data" / "work" / "venues"))
API = "https://api.semanticscholar.org/graph/v1/paper/search/bulk"
FIELDS = "paperId,title,abstract,externalIds,venue,year,publicationDate,authors,openAccessPdf"

# query name, exact venue string Semantic Scholar uses, short label, site category, usual month
VENUES = [
    ("NeurIPS", "Neural Information Processing Systems", "NeurIPS", "cs.LG", 12),
    ("ICML", "International Conference on Machine Learning", "ICML", "cs.LG", 7),
    ("ICLR", "International Conference on Learning Representations", "ICLR", "cs.LG", 5),
    ("AISTATS", "International Conference on Artificial Intelligence and Statistics", "AISTATS", "stat.ML", 4),
    ("UAI", "Conference on Uncertainty in Artificial Intelligence", "UAI", "cs.LG", 7),
    ("COLT", "Annual Conference Computational Learning Theory", "COLT", "cs.LG", 7),
    ("AAAI", "AAAI Conference on Artificial Intelligence", "AAAI", "cs.AI", 2),
    ("IJCAI", "International Joint Conference on Artificial Intelligence", "IJCAI", "cs.AI", 8),
    ("CVPR", "Computer Vision and Pattern Recognition", "CVPR", "cs.CV", 6),
    ("ICCV", "IEEE International Conference on Computer Vision", "ICCV", "cs.CV", 10),
    ("ECCV", "European Conference on Computer Vision", "ECCV", "cs.CV", 10),
    ("WACV", "IEEE Workshop/Winter Conference on Applications of Computer Vision", "WACV", "cs.CV", 1),
    ("ACM Multimedia", "ACM Multimedia", "ACM MM", "cs.CV", 10),
    ("ACL", "Annual Meeting of the Association for Computational Linguistics", "ACL", "cs.CL", 7),
    ("EMNLP", "Conference on Empirical Methods in Natural Language Processing", "EMNLP", "cs.CL", 12),
    ("NAACL", "North American Chapter of the Association for Computational Linguistics", "NAACL", "cs.CL", 6),
    ("Findings", "Findings", "Findings of ACL", "cs.CL", 7),
    ("KDD", "Knowledge Discovery and Data Mining", "KDD", "cs.LG", 8),
    ("SIGIR", "Annual International ACM SIGIR Conference on Research and Development in Information Retrieval", "SIGIR", "cs.IR", 7),
    ("WWW", "The Web Conference", "WWW", "cs.IR", 5),
    ("ICRA", "IEEE International Conference on Robotics and Automation", "ICRA", "cs.RO", 5),
    ("IROS", "IEEE/RJS International Conference on Intelligent RObots and Systems", "IROS", "cs.RO", 10),
    ("Robotics: Science and Systems", "Robotics: Science and Systems Conference", "RSS", "cs.RO", 7),
    ("CoRL", "Conference on Robot Learning", "CoRL", "cs.RO", 11),
    ("CHI", "International Conference on Human Factors in Computing Systems", "CHI", "cs.HC", 4),
    ("CCS", "Conference on Computer and Communications Security", "CCS", "cs.CR", 11),
    ("USENIX Security Symposium", "USENIX Security Symposium", "USENIX Security", "cs.CR", 8),
    ("IEEE Symposium on Security and Privacy", "IEEE Symposium on Security and Privacy", "IEEE S&P", "cs.CR", 5),
    ("NDSS", "Network and Distributed System Security Symposium", "NDSS", "cs.CR", 2),
    ("IEEE Transactions on Pattern Analysis and Machine Intelligence", "IEEE Transactions on Pattern Analysis and Machine Intelligence", "TPAMI", "cs.CV", 6),
    ("International Journal of Computer Vision", "International Journal of Computer Vision", "IJCV", "cs.CV", 6),
    ("IEEE Transactions on Image Processing", "IEEE Transactions on Image Processing", "TIP", "eess.IV", 6),
    ("Journal of machine learning research", "Journal of machine learning research", "JMLR", "cs.LG", 6),
    ("Transactions of the Association for Computational Linguistics", "Transactions of the Association for Computational Linguistics", "TACL", "cs.CL", 6),
    ("Artificial Intelligence", "Artificial Intelligence", "AIJ", "cs.AI", 6),
    ("IEEE Transactions on Knowledge and Data Engineering", "IEEE Transactions on Knowledge and Data Engineering", "TKDE", "cs.IR", 6),
    ("IEEE Transactions on Neural Networks and Learning Systems", "IEEE Transactions on Neural Networks and Learning Systems", "TNNLS", "cs.LG", 6),
    ("IEEE Transactions on robotics", "IEEE Transactions on robotics", "T-RO", "cs.RO", 6),
]


def slug(s: str) -> str:
    return "".join(c.lower() if c.isalnum() else "-" for c in s).strip("-")[:40]


def get(params: dict) -> dict:
    url = API + "?" + urllib.parse.urlencode(params)
    for attempt in range(6):  # Semantic Scholar's shared anonymous quota answers 429 when busy
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ai-research-graph/1.0 (monthly index build)"})
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504):
                raise
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(2 + 3 * attempt)
    raise RuntimeError("Semantic Scholar did not answer")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default="2021-2026")
    ap.add_argument("--refresh", action="store_true", help="refetch these years even if done before")
    ap.add_argument("--time-budget", type=float, default=0, help="seconds; 0 = no limit")
    a = ap.parse_args()
    y0, y1 = (int(x) for x in a.years.split("-"))
    OUT.mkdir(parents=True, exist_ok=True)
    state_path = OUT / "venue_state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    t0 = time.time(); fetched = 0
    for q, exact, label, cat, month in VENUES:
        for y in range(y0, y1 + 1):
            key = f"{slug(label)}_{y}"
            st = state.get(key) or {}
            if a.refresh and st.get("done") and st.get("refreshed") != t0:
                st = {}
            if st.get("done"):
                continue
            path = OUT / f"venue_{key}.jsonl.gz"
            if not st.get("token"):
                path.unlink(missing_ok=True); st = {"kept": 0}
            while True:
                if a.time_budget and time.time() - t0 > a.time_budget:
                    state[key] = st; state_path.write_text(json.dumps(state, indent=1))
                    print(f"time budget reached; {fetched} papers this run (run again to continue)")
                    return
                params = {"venue": q, "year": str(y), "fields": FIELDS}
                if st.get("token"):
                    params["token"] = st["token"]
                d = get(params)
                rows = []
                for p in d.get("data") or []:
                    if (p.get("venue") or "").lower() != exact.lower() or not p.get("title"):
                        continue
                    p["_venue"] = label; p["_cat"] = cat; p["_month"] = month; p["_year"] = y
                    rows.append(p)
                with gzip.open(path, "at", encoding="utf-8") as fh:
                    for r in rows:
                        fh.write(json.dumps(r, ensure_ascii=False) + "\n")
                st["kept"] = st.get("kept", 0) + len(rows); fetched += len(rows)
                st["token"] = d.get("token")
                if not st["token"]:
                    st["done"] = True; st["refreshed"] = t0 if a.refresh else st.get("refreshed")
                    break
                time.sleep(1.1)
            state[key] = st; state_path.write_text(json.dumps(state, indent=1))
            print(f"{label} {y}: {st['kept']}", flush=True)
            time.sleep(1.1)
    print(f"all venues done; {fetched} papers this run")


if __name__ == "__main__":
    main()
