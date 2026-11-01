#!/usr/bin/env python3
"""Embed paper abstracts with a GPU (falls back to CPU) for research-direction clustering.

Input : data/work/sem/text/text_XXXXX.jsonl.gz   lines of [key, "title. abstract..."]
Output: data/work/sem/emb/emb_XXXXX.npy (float16, L2-normalised) + pid_XXXXX.npy
Model : BAAI/bge-base-en-v1.5 (768-d, CLS pooling). Needs torch + transformers.

Robustness (some GPU/driver combinations crash with "illegal memory access" on
particular batches): the GPU runs in full precision (fp32) with plain attention,
every batch is synchronised so a crash is attributed to the right batch, and the
work runs in a child process. If the child crashes, the parent records the batch
that was in flight and starts a new child, which computes that one batch on the CPU
and the rest on the GPU. Completed chunks are skipped, so nothing is redone.
"""
import glob
import gzip
import json
import os
import subprocess
import sys
import time

# Anaconda's MKL and PyTorch each ship an OpenMP runtime; allow both (sanity check guards results).
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import numpy as np  # noqa: E402

MODEL = os.environ.get("AIRG_EMBED_MODEL", "BAAI/bge-base-en-v1.5")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TEXT = os.path.join(ROOT, "data", "work", "sem", "text")
OUT = os.path.join(ROOT, "data", "work", "sem", "emb")
INFLIGHT = os.path.join(OUT, "_inflight.json")
BAD = os.path.join(OUT, "_cpu_batches.json")     # batches that crashed the GPU -> computed on CPU
MAX_BAD = 12
BATCH = int(os.environ.get("AIRG_BATCH", "64"))


def load_model():
    from transformers import AutoModel, AutoTokenizer
    try:
        return AutoTokenizer.from_pretrained(MODEL), AutoModel.from_pretrained(MODEL, attn_implementation="eager")
    except Exception as err:  # noqa: BLE001 - e.g. huggingface.co unreachable
        if os.environ.get("HF_ENDPOINT"):
            raise
        print(f"从 huggingface.co 下载模型失败（{type(err).__name__}），改用镜像 hf-mirror.com …", flush=True)
        os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
        sys.exit(subprocess.call([sys.executable, os.path.abspath(__file__), *sys.argv[1:]], env=os.environ))


def encode(tok, model, device, texts):
    import torch
    enc = tok(texts, padding=True, truncation=True, max_length=256, return_tensors="pt").to(device)
    out = model(**enc).last_hidden_state[:, 0]            # CLS pooling (bge)
    out = torch.nn.functional.normalize(out.float(), dim=-1)
    return out.cpu().numpy()                                # also synchronises the GPU


def sanity_check(tok, model, device):
    import torch
    texts = ["Diffusion models for high-fidelity video generation.",
             "A text-to-video diffusion model that generates realistic clips.",
             "Federated learning with differential privacy for medical records."]
    with torch.inference_mode():
        v = encode(tok, model, device, texts)
    ok = bool(np.isfinite(v).all()) and float(v[0] @ v[1]) > float(v[0] @ v[2]) + 0.1
    print(f"自检：相似句子 {float(v[0] @ v[1]):.2f}，不相关句子 {float(v[0] @ v[2]):.2f} -> {'通过' if ok else '失败'}", flush=True)
    if not ok:
        sys.exit("自检失败：向量结果不正常，已停止。请把上面的输出发给 Claude。")


def load_bad():
    try:
        return {tuple(x) for x in json.load(open(BAD, encoding="utf-8"))}
    except (OSError, ValueError):
        return set()


def worker():
    """Embed every remaining chunk. Exits non-zero if the GPU crashes (the parent handles it)."""
    import torch
    torch.backends.cuda.matmul.allow_tf32 = False
    chunks = sorted(glob.glob(os.path.join(TEXT, "text_*.jsonl.gz")))
    todo = [c for c in chunks if not os.path.exists(os.path.join(OUT, "emb_" + os.path.basename(c)[5:10] + ".npy"))]
    print(f"共 {len(chunks)} 个分块，待计算 {len(todo)} 个", flush=True)
    if not todo:
        return 0
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"使用 {'GPU：' + torch.cuda.get_device_name(0) if device == 'cuda' else 'CPU（没有可用的 CUDA GPU，会比较慢）'}（全精度 fp32）", flush=True)
    tok, model = load_model()
    model = model.to(device).eval()
    sanity_check(tok, model, device)
    bad = load_bad()
    cpu_model = None
    t_all = time.time(); done = 0
    for k, path in enumerate(todo, 1):
        idx = os.path.basename(path)[5:10]
        rows = [json.loads(line) for line in gzip.open(path, "rt", encoding="utf-8")]
        texts = [r[1] for r in rows]
        order = np.argsort([len(t) for t in texts], kind="stable")
        vecs = np.zeros((len(texts), model.config.hidden_size), dtype=np.float32)
        t = time.time(); n_cpu = 0
        with torch.inference_mode():
            for start in range(0, len(texts), BATCH):
                sel = order[start:start + BATCH]
                batch = [texts[j] for j in sel]
                if (idx, start) in bad:
                    if cpu_model is None:
                        cpu_model = model.to("cpu") if device == "cpu" else type(model).from_pretrained(MODEL, attn_implementation="eager").eval()
                    vecs[sel] = encode(tok, cpu_model, "cpu", batch); n_cpu += 1
                    continue
                with open(INFLIGHT, "w", encoding="utf-8") as fh:
                    json.dump([idx, start], fh)
                vecs[sel] = encode(tok, model, device, batch)
        norms = np.linalg.norm(vecs, axis=1)
        if not np.isfinite(vecs).all() or np.abs(norms - 1).max() > 1e-2:
            print(f"分块 {idx} 的结果不正常，已停止，未保存。", flush=True)
            return 3
        np.save(os.path.join(OUT, f"pid_{idx}.npy"), np.array([r[0] for r in rows]))
        tmp = os.path.join(OUT, f"emb_{idx}.tmp.npy")
        np.save(tmp, vecs.astype(np.float16))
        os.replace(tmp, os.path.join(OUT, f"emb_{idx}.npy"))
        if os.path.exists(INFLIGHT):
            os.remove(INFLIGHT)
        done += len(rows)
        rate = done / (time.time() - t_all)
        left = (len(todo) - k) * 5000 / max(rate, 1e-6)
        extra = f"，其中 {n_cpu} 批改用 CPU" if n_cpu else ""
        print(f"[{k}/{len(todo)}] 分块 {idx}：{len(rows)} 篇，用时 {time.time() - t:.0f}s{extra}，速度 {rate:.0f} 篇/秒，预计还需 {left / 60:.1f} 分钟", flush=True)
    print(f"全部完成，共 {done} 篇，用时 {(time.time() - t_all) / 60:.1f} 分钟。", flush=True)
    return 0


def supervise():
    """Run the worker; if the GPU crashes on a batch, send that batch to the CPU and continue."""
    env = dict(os.environ, AIRG_EMBED_WORKER="1", CUDA_LAUNCH_BLOCKING="1")
    while True:
        if os.path.exists(INFLIGHT):
            os.remove(INFLIGHT)  # stale marker from an interrupted run (e.g. window closed)
        code = subprocess.call([sys.executable, os.path.abspath(__file__)], env=env)
        if code == 0:
            return 0
        if not os.path.exists(INFLIGHT):
            print(f"\n计算出错（不是某一批引起的），已停止。请把上面的报错发给 Claude。", flush=True)
            return code
        failed = tuple(json.load(open(INFLIGHT, encoding="utf-8")))
        bad = load_bad()
        if failed in bad:
            print(f"\n分块 {failed[0]} 第 {failed[1]} 条起的这一批改用 CPU 后仍然出错，已停止。请把上面的报错发给 Claude。", flush=True)
            return code
        bad.add(failed)
        if len(bad) > MAX_BAD:
            print(f"\nGPU 已在 {len(bad)} 批上出错，说明显卡环境本身不稳定，已停止。请把上面的报错发给 Claude。", flush=True)
            return code
        json.dump(sorted(bad), open(BAD, "w", encoding="utf-8"))
        print(f"\nGPU 在分块 {failed[0]} 第 {failed[1]} 条起的一批（{BATCH} 篇）上出错：这一批改用 CPU 计算，其余继续用 GPU……\n", flush=True)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    sys.exit(worker() if os.environ.get("AIRG_EMBED_WORKER") else supervise())
