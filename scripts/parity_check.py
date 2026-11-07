"""Checks that the Python search (airg/search.py) gives the same results as the browser
(public/research/core.js + retrieve() in ask.js) on the current data snapshot.

    python -X utf8 scripts/parity_check.py          # needs Node 22+ and numpy

Runs every case through scripts/parity_check.mjs (the real core.js in Node) and through
airg.search.SearchIndex, then compares hit order (top 50), scores, totals, typo fixes and
Q&A sources. Exits with status 1 if any case differs. Run it after changing core.js,
ask.js, airg/search.py or the data format.
"""
from __future__ import annotations

import gzip
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from airg.search import SearchIndex  # noqa: E402

TOL = 1e-6
CAMEL = {"yearFrom": "year_from", "yearTo": "year_to"}


def build_cases(ix: SearchIndex) -> list[dict]:
    first = json.loads(gzip.decompress((ix.data / "l" / "0000.json.gz").read_bytes()))
    keys = [r[5] for r in first if r[5][:1].isdigit()][:3]
    cv = ix.manifest["categories"].index("cs.CV")
    did = ix.trends()["directions"][0]["id"]
    cases = [
        {"kind": "fuzzy", "q": "diffusion model for video generation"},
        {"kind": "fuzzy", "q": "retrieval augmented generation evaluation benchmark"},
        {"kind": "fuzzy", "q": "transfomer architecure"},
        {"kind": "fuzzy", "q": "reinforcment lerning from human feedbak"},
        {"kind": "fuzzy", "q": "RLHF reward hacking"},
        {"kind": "fuzzy", "q": "VLA policy for robot manipulation"},
        {"kind": "fuzzy", "q": "graph neural network", "opts": {"yearFrom": 2024, "yearTo": 2025}},
        {"kind": "fuzzy", "q": "object detection", "opts": {"cat": cv}},
        {"kind": "fuzzy", "q": "evaluation", "opts": {"dir": did}},
        {"kind": "fuzzy", "q": "large language model reasoning", "opts": {"pub": True}},
        {"kind": "fuzzy", "q": "federated learning privacy", "opts": {"sort": "recent"}},
        {"kind": "fuzzy", "q": "qqqzzx xxyyq"},
        {"kind": "search", "q": "gaussian splatting slam"},
        {"kind": "search", "q": "mixture of experts", "opts": {"sort": "recent", "limit": 100}},
        {"kind": "correct", "q": "transfomer architecure with atention"},
        {"kind": "correct", "q": "RLHF and VLA for difusion modles"},
        *[{"kind": "similar", "key": k} for k in keys],
        *[{"kind": "sidOfKey", "key": k} for k in keys],
        {"kind": "sidOfKey", "key": "0000.00000"},
        {"kind": "retrieve", "question": "How to reduce hallucination in multimodal LLMs?", "queries": ["multimodal large language model hallucination mitigation"]},
        {"kind": "retrieve", "question": "What are alternatives to RLHF?", "queries": ["direct preference optimization", "RLHF alternatives preference learning"]},
        {"kind": "retrieve", "question": "What builds on this paper?", "queries": ["proactive agent evaluation"], "pinned": keys[0]},
    ]
    return cases


def run_python(ix: SearchIndex, c: dict):
    opts = {CAMEL.get(k, k): v for k, v in (c.get("opts") or {}).items()}
    hits = lambda hs: [{"sid": h["sid"], "score": h.get("score"), "sim": h.get("sim"), "semOnly": bool(h.get("semOnly"))} for h in hs[:50]]  # noqa: E731
    k = c["kind"]
    if k == "search":
        r = ix.search(c["q"], **opts)
        return {"hits": hits(r["hits"]), "total": r["total"]}
    if k == "fuzzy":
        r = ix.fuzzy_search(c["q"], **opts)
        return {"hits": hits(r["hits"]), "total": r["total"], "fixes": r["fixes"], "query": r["query"], "semantic": r["semantic"], "keywordTotal": r["keywordTotal"]}
    if k == "correct":
        return ix.correct(c["q"])
    if k == "sidOfKey":
        return {"sid": ix.sid_of_key(c["key"])}
    if k == "similar":
        sid = ix.sid_of_key(c["key"])
        return {"sid": sid, "hits": hits(ix.similar(sid, ix.lite(sid)["title"], c.get("limit", 10)))}
    if k == "retrieve":
        r = ix.retrieve(c["question"], c["queries"], pinned_key=c.get("pinned"))
        return {"keys": [s["key"] for s in r["sources"]], "sources": r["sources"], "overview": r["overview"], "dirs": r["dirs"], "matched": r["matched"]}
    raise ValueError(k)


def close(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return math.isclose(a, b, rel_tol=TOL, abs_tol=1e-12)


def compare_hits(js: list, py: list) -> list[str]:
    errs = []
    a = [h["sid"] for h in js]
    b = [h["sid"] for h in py]
    if a != b:
        first = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
        errs.append(f"hit order differs at #{first} (overlap {len(set(a) & set(b))}/{max(len(a), len(b))}): js={a[first:first + 3]} py={b[first:first + 3]}")
        return errs
    for i, (x, y) in enumerate(zip(js, py)):
        for f in ("score", "sim"):
            if not close(x[f], y[f]):
                errs.append(f"hit #{i} {f}: js={x[f]} py={y[f]}")
                break
        if x["semOnly"] != y["semOnly"]:
            errs.append(f"hit #{i} semOnly: js={x['semOnly']} py={y['semOnly']}")
        if len(errs) >= 3:
            break
    return errs


def compare(kind: str, js: dict, py: dict) -> list[str]:
    errs = []
    if "hits" in js:
        errs += compare_hits(js["hits"], py["hits"])
    for f in ("total", "fixes", "query", "semantic", "keywordTotal", "text", "sid", "keys", "overview", "dirs", "matched", "sources"):
        if f in js and js[f] != py.get(f):
            if f == "sources" and js.get("keys") != py.get("keys"):
                continue  # already reported via keys
            errs.append(f"{f}: js={json.dumps(js[f], ensure_ascii=False)[:300]} py={json.dumps(py.get(f), ensure_ascii=False)[:300]}")
    return errs


def main() -> int:
    ix = SearchIndex()
    cases = build_cases(ix)
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "cases.json"
        f.write_text(json.dumps(cases), encoding="utf-8")
        p = subprocess.run(["node", str(ROOT / "scripts" / "parity_check.mjs"), str(f)], capture_output=True, text=True, encoding="utf-8")
    if p.returncode != 0:
        print("node failed:\n" + p.stderr[-2000:])
        return 2
    js_all = json.loads(p.stdout)
    fails = 0
    for c, js in zip(cases, js_all):
        label = f"{c['kind']:<9} {c.get('q') or c.get('question') or c.get('key')}" + (f"  {c['opts']}" if c.get("opts") else "")
        if not js["ok"]:
            print(f"FAIL  {label}\n      js error: {js['error'][:400]}")
            fails += 1
            continue
        try:
            py = run_python(ix, c)
        except Exception as e:  # noqa: BLE001
            print(f"FAIL  {label}\n      python error: {e!r}")
            fails += 1
            continue
        errs = compare(c["kind"], js["result"], py)
        print(("FAIL  " if errs else "PASS  ") + label)
        for e in errs:
            print("      " + e)
        fails += bool(errs)
    print(f"\n{len(cases) - fails}/{len(cases)} cases match")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
