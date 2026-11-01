#!/usr/bin/env python3
"""Fetch citation counts and references from the Semantic Scholar Graph API.

Modes
  refs     full record (counts + venue + reference list) for papers not fetched yet
  counts   refresh citationCount / influentialCitationCount for every paper (monthly)

The queue is fixed when a job starts, split across workers, and each worker
remembers its position, so an interrupted run resumes without re-requesting.
Set SEMANTIC_SCHOLAR_API_KEY to use a personal key (optional).
"""
from __future__ import annotations

import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

from common import WORK, Budget, append_jsonl, http, load_state, read_jsonl, save_state

API = "https://api.semanticscholar.org/graph/v1/paper/batch"
FIELDS = {
    "refs": "externalIds,citationCount,influentialCitationCount,venue,publicationVenue,publicationDate,references.externalIds",
    "counts": "externalIds,citationCount,influentialCitationCount",
}
BATCH = {"refs": 200, "counts": 500}


def compact(rec: dict, mode: str) -> dict:
    ext = rec.get("externalIds") or {}
    row = {
        "arxiv_id": ext.get("ArXiv"),
        "corpus_id": ext.get("CorpusId"),
        "cites": rec.get("citationCount") or 0,
        "influential": rec.get("influentialCitationCount") or 0,
    }
    if mode == "refs":
        pv = rec.get("publicationVenue") or {}
        venue = pv.get("name") or rec.get("venue") or ""
        row["venue"] = "" if venue.lower() in ("arxiv.org", "arxiv") else venue
        row["pub_date"] = rec.get("publicationDate")
        refs = []
        for ref in rec.get("references") or []:
            rext = (ref or {}).get("externalIds") or {}
            if rext.get("CorpusId"):
                refs.append([rext.get("CorpusId"), rext.get("ArXiv")])
        row["refs"] = refs
    return row


def worker(job: str, mode: str, wid: int, ids: list[str], budget: Budget, headers: dict) -> bool:
    state_path = WORK / f"s2_{job}_w{wid}.json"
    out = WORK / f"s2_{job}_w{wid}.jsonl.gz"
    state = load_state(state_path, {"pos": 0})
    step = BATCH[mode]
    while state["pos"] < len(ids):
        if budget.exhausted():
            return False
        chunk = ids[state["pos"]: state["pos"] + step]
        body = json.dumps({"ids": [f"ARXIV:{x}" for x in chunk]}).encode()
        data = json.loads(http(f"{API}?fields={FIELDS[mode]}", data=body, headers=headers, retries=12, timeout=150))
        rows = []
        for aid, rec in zip(chunk, data):
            if rec:
                row = compact(rec, mode)
                row["arxiv_id"] = aid
                rows.append(row)
        append_jsonl(out, rows)
        state["pos"] += len(chunk)
        save_state(state_path, state)
        time.sleep(0.5)
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("refs", "counts"), required=True)
    ap.add_argument("--ids", help="Text file with one arXiv id per line (required when a new job starts)")
    ap.add_argument("--job", required=True, help="Job name, e.g. refs_2026-10 or counts_2026-11")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--time-budget", type=float, default=0)
    args = ap.parse_args()
    budget = Budget(args.time_budget)
    job_path = WORK / f"s2_{args.job}_job.json"
    job = load_state(job_path, {})
    if not job:
        if not args.ids:
            raise SystemExit("--ids is required to start a job")
        ids = [line.strip() for line in open(args.ids, encoding="utf-8") if line.strip()]
        job = {"mode": args.mode, "workers": args.workers, "total": len(ids), "done": False,
               "slices": [ids[i::args.workers] for i in range(args.workers)]}
        save_state(job_path, job)
    if job["done"]:
        print(f"job {args.job} already complete"); return
    headers = {"Content-Type": "application/json"}
    if os.environ.get("SEMANTIC_SCHOLAR_API_KEY"):
        headers["x-api-key"] = os.environ["SEMANTIC_SCHOLAR_API_KEY"]
    with ThreadPoolExecutor(max_workers=len(job["slices"])) as pool:
        done = list(pool.map(lambda w: worker(args.job, job["mode"], w, job["slices"][w], budget, headers), range(len(job["slices"]))))
    pos = sum(load_state(WORK / f"s2_{args.job}_w{w}.json", {"pos": 0})["pos"] for w in range(len(job["slices"])))
    if all(done):
        job["done"] = True
        save_state(job_path, job)
    print(f"s2 {args.job}: {pos}/{job['total']} {'complete' if all(done) else 'paused'}")


def load_results(job: str) -> dict[str, dict]:
    job_state = load_state(WORK / f"s2_{job}_job.json", {"slices": []})
    out: dict[str, dict] = {}
    for w in range(len(job_state["slices"])):
        for row in read_jsonl(WORK / f"s2_{job}_w{w}.jsonl.gz"):
            out[row["arxiv_id"]] = row
    return out


if __name__ == "__main__":
    main()
