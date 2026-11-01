#!/usr/bin/env python3
"""Query embedder used by serve_local.py for semantic search (local use only).

Reads one JSON object per line on stdin: {"q": "text"}
Writes one JSON line per request:        {"v": [256 floats]}  or {"error": "..."}

The query is embedded with the same model as the abstracts (bge-base-en-v1.5, with the
retrieval instruction prefix bge recommends for short queries), then projected with
public/research/data/v/pca.bin.gz into the space of the browser's vector index.
Runs on CPU: one short query takes ~50 ms, and the GPU stays free.
"""
import gzip
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
ROOT = Path(__file__).resolve().parents[2]
MODEL = os.environ.get("AIRG_EMBED_MODEL", "BAAI/bge-base-en-v1.5")
PREFIX = "Represent this sentence for searching relevant passages: "


def main():
    import numpy as np
    import torch
    from transformers import AutoModel, AutoTokenizer

    raw = gzip.open(ROOT / "public/research/data/v/pca.bin.gz", "rb").read()
    din, dout = np.frombuffer(raw[:8], dtype=np.uint32)
    mu = np.frombuffer(raw[8:8 + 4 * din], dtype=np.float32)
    P = np.frombuffer(raw[8 + 4 * din:], dtype=np.float32).reshape(dout, din)
    try:
        tok = AutoTokenizer.from_pretrained(MODEL, local_files_only=True)
        model = AutoModel.from_pretrained(MODEL, local_files_only=True, attn_implementation="eager")
    except Exception:  # noqa: BLE001  (not cached yet: download, falling back to the mirror)
        try:
            tok = AutoTokenizer.from_pretrained(MODEL); model = AutoModel.from_pretrained(MODEL, attn_implementation="eager")
        except Exception:  # noqa: BLE001
            os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
            tok = AutoTokenizer.from_pretrained(MODEL); model = AutoModel.from_pretrained(MODEL, attn_implementation="eager")
    model.eval(); torch.set_num_threads(max(1, min(4, os.cpu_count() or 1)))
    print(json.dumps({"ready": True, "dim": int(dout)}), flush=True)
    for line in sys.stdin:
        try:
            q = (json.loads(line).get("q") or "").strip()[:500]
            with torch.inference_mode():
                enc = tok([PREFIX + q], padding=True, truncation=True, max_length=96, return_tensors="pt")
                v = model(**enc).last_hidden_state[:, 0].float()
                v = torch.nn.functional.normalize(v, dim=-1)[0].numpy()
            y = (v - mu) @ P.T
            y /= max(float(np.linalg.norm(y)), 1e-6)
            print(json.dumps({"v": [round(float(x), 5) for x in y]}), flush=True)
        except Exception as e:  # noqa: BLE001
            print(json.dumps({"error": str(e)[:200]}), flush=True)


if __name__ == "__main__":
    main()
