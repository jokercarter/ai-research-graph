#!/usr/bin/env python3
"""Harvest arXiv AI papers (metadata + abstracts) through OAI-PMH.

Incremental: the harvest state remembers the last completed datestamp, so the
monthly run only fetches records created or updated since then. Resumable via
the OAI resumption token when a run is interrupted.
"""
from __future__ import annotations

import argparse
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from common import AI_CATEGORIES, CORE_CATEGORIES, WINDOW_START, WORK, Budget, append_jsonl, http, load_state, save_state

OAI = "https://oaipmh.arxiv.org/oai"
NS = {"oai": "http://www.openarchives.org/OAI/2.0/", "ax": "http://arxiv.org/OAI/arXiv/"}


def clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def created_from_id(arxiv_id: str) -> str | None:
    m = re.match(r"^(\d{2})(\d{2})\.\d{4,5}$", arxiv_id)
    if not m:
        return None
    return f"20{m.group(1)}-{m.group(2)}"


def parse(xml_bytes: bytes):
    root = ET.fromstring(xml_bytes)
    rows = []
    for rec in root.iterfind(".//oai:record", NS):
        meta = rec.find("oai:metadata/ax:arXiv", NS)
        if meta is None:
            continue
        aid = clean(meta.findtext("ax:id", namespaces=NS))
        cats = clean(meta.findtext("ax:categories", namespaces=NS)).split()
        if not cats:
            continue
        month = created_from_id(aid)
        if month is None or month < WINDOW_START[:7]:
            continue
        primary = cats[0]
        if primary not in AI_CATEGORIES and not (set(cats) & CORE_CATEGORIES):
            continue
        authors = []
        for au in meta.iterfind("ax:authors/ax:author", NS):
            name = " ".join(x for x in [clean(au.findtext("ax:forenames", namespaces=NS)), clean(au.findtext("ax:keyname", namespaces=NS))] if x)
            if name:
                authors.append(name)
        rows.append({
            "arxiv_id": aid,
            "title": clean(meta.findtext("ax:title", namespaces=NS)),
            "abstract": clean(meta.findtext("ax:abstract", namespaces=NS)),
            "authors": authors[:25],
            "n_authors": len(authors),
            "categories": cats,
            "created": clean(meta.findtext("ax:created", namespaces=NS)),
            "updated": clean(meta.findtext("ax:updated", namespaces=NS)),
            "comments": clean(meta.findtext("ax:comments", namespaces=NS))[:300],
            "journal_ref": clean(meta.findtext("ax:journal-ref", namespaces=NS))[:300],
            "doi": clean(meta.findtext("ax:doi", namespaces=NS)).lower(),
        })
    token_el = root.find(".//oai:resumptionToken", NS)
    token = clean(token_el.text) if token_el is not None and token_el.text else ""
    err = root.find("oai:error", NS)
    if err is not None and err.get("code") != "noRecordsMatch":
        raise RuntimeError(f"OAI error {err.get('code')}: {err.text}")
    return rows, token, len(root.findall(".//oai:record", NS))


def harvest_range(name: str, start: str, until: str, budget: Budget) -> bool:
    """Harvest one datestamp range into its own file. Returns True when complete."""
    state_path = WORK / f"arxiv_harvest_{name}.json"
    out = WORK / f"arxiv_records_{name}.jsonl.gz"
    state = load_state(state_path, {"from": start, "until": until, "token": "", "done": False, "kept": 0, "seen": 0})
    if state["done"]:
        return True
    if state["token"]:
        params = {"verb": "ListRecords", "resumptionToken": state["token"]}
    else:
        params = {"verb": "ListRecords", "metadataPrefix": "arXiv", "set": "cs", "from": start, "until": until}
    while not budget.exhausted():
        body = http(OAI + "?" + urllib.parse.urlencode(params), timeout=180)
        rows, token, seen = parse(body)
        append_jsonl(out, rows)
        state["kept"] += len(rows); state["seen"] += seen; state["token"] = token
        if not token:
            state["done"] = True
        save_state(state_path, state)
        if state["done"]:
            return True
        params = {"verb": "ListRecords", "resumptionToken": token}
        time.sleep(1.5)
    return False


def split_ranges(start: str, until: str, parts: int) -> list[tuple[str, str]]:
    a, b = date.fromisoformat(start), date.fromisoformat(until)
    span = (b - a).days + 1
    edges = [a + timedelta(days=round(span * i / parts)) for i in range(parts + 1)]
    return [(edges[i].isoformat(), (edges[i + 1] - timedelta(days=1)).isoformat()) for i in range(parts)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-date", default=None, help="Override start datestamp (YYYY-MM-DD)")
    ap.add_argument("--until", default=None, help="End datestamp; default today")
    ap.add_argument("--parts", type=int, default=1, help="Split the date range for a parallel first harvest")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--time-budget", type=float, default=0)
    args = ap.parse_args()
    budget = Budget(args.time_budget)
    plan_path = WORK / "arxiv_harvest_plan.json"
    plan = load_state(plan_path, {"completed_until": None, "ranges": []})
    if not plan["ranges"]:
        start = args.from_date or plan.get("completed_until") or WINDOW_START
        until = args.until or date.today().isoformat()
        if start > until:
            print("nothing to harvest"); return
        tag = until.replace("-", "")
        plan["ranges"] = [{"name": f"{tag}_{i:02d}", "from": f, "until": u} for i, (f, u) in enumerate(split_ranges(start, until, args.parts))]
        plan["run_until"] = until
        save_state(plan_path, plan)
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        done = list(pool.map(lambda r: harvest_range(r["name"], r["from"], r["until"], budget), plan["ranges"]))
    kept = sum(load_state(WORK / f"arxiv_harvest_{r['name']}.json", {"kept": 0})["kept"] for r in plan["ranges"])
    if all(done):
        plan["completed_until"] = plan["run_until"]
        plan.setdefault("history", []).append({"until": plan["run_until"], "ranges": [r["name"] for r in plan["ranges"]]})
        plan["ranges"] = []
        save_state(plan_path, plan)
        print(f"harvest complete through {plan['completed_until']}; kept={kept}")
    else:
        print(f"paused: {sum(done)}/{len(done)} ranges done; kept={kept}")


if __name__ == "__main__":
    main()
