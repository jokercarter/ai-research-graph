"""Python port of the search in public/research/core.js (and the Q&A retrieval in ask.js).

Reads the same static, sharded files the website uses (public/research/data, built by
data/pipeline/build_simple.py), so a server-side process (serve_local.py, an agent)
gets exactly the results the browser shows. Keep this file in step with core.js:
scripts/parity_check.mjs compares the two on a set of queries.

Only the standard library and numpy are needed.
"""
from __future__ import annotations

import gzip
import importlib.util
import json
import math
import re
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Callable, Iterable, Optional

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public" / "research" / "data"

# Tokenizer shared with the index builder (data/pipeline/textutil.py) and core.js.
_spec = importlib.util.spec_from_file_location("airg_textutil", ROOT / "data" / "pipeline" / "textutil.py")
_tu = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_tu)
STOP: set = _tu.STOP
stem = _tu.stem
tokens = _tu.tokens
fnv1a = _tu.fnv1a

_ARXIV = re.compile(r"^\d{4}\.\d{4,5}$")
_CJK = re.compile(r"[㐀-鿿]")


def is_arxiv_key(k) -> bool:
    return bool(_ARXIV.match(k or ""))


def has_cjk(s) -> bool:
    return bool(_CJK.search(s or ""))


def _load_zh_terms():
    """The Chinese -> English term list is kept in one place: core.js (ZH_TERMS)."""
    src = (ROOT / "public" / "research" / "core.js").read_text(encoding="utf-8")
    block = re.search(r"const ZH_TERMS = \[(.*?)\n\];", src, re.S)
    if not block:
        return []
    return [(re.compile(p, re.I), en) for p, en in re.findall(r'\["([^"]+)",\s*"([^"]+)"\]', block.group(1))]


ZH_TERMS = _load_zh_terms()


def local_rewrite(q: str) -> str:
    """Chinese research question -> English search terms with the built-in dictionary."""
    terms = [en for pat, en in ZH_TERMS if pat.search(q)]
    latin = " ".join(re.findall(r"[A-Za-z][A-Za-z0-9\-]+", q))
    out = []
    for x in [*terms, latin]:
        if x and x not in out:
            out.append(x)
    return " ".join(out)


def edit_distance(a: str, b: str, mx: int) -> int:
    if abs(len(a) - len(b)) > mx:
        return mx + 1
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i]
        best = i
        for j in range(1, len(b) + 1):
            v = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (0 if a[i - 1] == b[j - 1] else 1))
            cur.append(v)
            if v < best:
                best = v
        if best > mx:
            return mx + 1
        prev = cur
    return prev[len(b)]


def _js_round(x: float) -> int:
    return math.floor(x + 0.5)


def _cos(a, b) -> float:
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    return float(a @ b) / (math.sqrt(float(a @ a) * float(b @ b)) or 1.0)


def excerpt(text: str, terms: Iterable[str], mx: int = 460) -> str:
    """First sentence + the sentences sharing most words with the query (ask.js excerpt)."""
    sents = [m.group(0) for m in re.finditer(r"[^.!?]+[.!?]+(\s|$)|[^.!?]+$", re.sub(r"\s+", " ", str(text or "")))]
    if not sents:
        return ""
    terms = list(terms)
    scored = []
    for i, t in enumerate(sents):
        w = set(tokens(t))
        scored.append({"t": t.strip(), "i": i, "k": sum(1 for x in terms if x in w)})
    keep = {0}
    for x in sorted(scored[1:], key=lambda x: (-x["k"], x["i"])):
        if x["k"] == 0 or len(keep) >= 3:
            break
        keep.add(x["i"])
    out = ""
    for x in scored:
        if x["i"] in keep:
            if len(out + " " + x["t"]) > mx:
                break
            out += (" … " if out else "") + x["t"]
    return out or sents[0][:mx]


class _LRU:
    def __init__(self, size: int):
        self.size = size
        self.d: OrderedDict = OrderedDict()
        self.lock = threading.Lock()

    def get(self, key, make):
        with self.lock:
            if key in self.d:
                self.d.move_to_end(key)
                return self.d[key]
        val = make()
        with self.lock:
            self.d[key] = val
            if len(self.d) > self.size:
                self.d.popitem(last=False)
        return val


class SearchIndex:
    """All search and lookup functions of core.js, over one data snapshot."""

    def __init__(self, data_dir: Path | str = DATA):
        self.data = Path(data_dir)
        self.manifest = json.loads((self.data / "manifest.json").read_text(encoding="utf-8"))
        self.version = self.manifest["version"]
        self._lock = threading.Lock()
        self._meta = None
        self._dirs = False  # False = not loaded yet, None = file missing
        self._trends = None
        self._vocab = False
        self._cent = False
        self._pca = False
        self._terms = _LRU(512)   # term posting shards
        self._blocks = _LRU(256)  # lite / detail / key shards
        self._vecs = _LRU(160)    # vector shards (one per research direction)

    # ---- raw files ------------------------------------------------------------------
    def _read(self, path: str) -> bytes:
        raw = (self.data / path).read_bytes()
        return gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw

    def _json(self, path: str):
        return json.loads(self._read(path).decode("utf-8"))

    @property
    def meta(self) -> np.ndarray:
        if self._meta is None:
            self._meta = np.frombuffer(self._read("meta.bin.gz"), dtype=np.uint8)
        return self._meta

    @property
    def dirs(self) -> Optional[np.ndarray]:
        if self._dirs is False:
            try:
                self._dirs = np.frombuffer(self._read("dir.bin.gz"), dtype=np.uint8)
            except OSError:
                self._dirs = None
        return self._dirs

    def N(self) -> int:
        return len(self.meta) // 4

    def cat_name(self, c: str) -> str:
        return self.manifest["category_names"].get(c, c)

    def cat_index(self, c) -> Optional[int]:
        if c is None or c == "":
            return None
        if isinstance(c, int):
            return c
        cats = self.manifest["categories"]
        return cats.index(c) if c in cats else None

    # ---- per-paper metadata (meta.bin: year, month, category, flags) ----------------
    def year(self, sid: int) -> int:
        return int(self.meta[sid * 4]) + self.manifest["year0"]

    def ym(self, sid: int) -> int:
        return self.year(sid) * 100 + int(self.meta[sid * 4 + 1])

    def cat(self, sid: int) -> int:
        return int(self.meta[sid * 4 + 2])

    def is_new(self, sid: int) -> bool:
        return (int(self.meta[sid * 4 + 3]) & 1) == 1

    def has_venue(self, sid: int) -> bool:
        return (int(self.meta[sid * 4 + 3]) & 2) == 2

    def dirs_of(self, sid: int) -> list[int]:
        d = self.dirs
        if d is None:
            return []
        return [int(v) for v in d[sid * 3: sid * 3 + 3] if v != 255]

    def main_dir(self, sid: int) -> int:
        d = self.dirs
        return int(d[sid * 3]) if d is not None else 255

    # ---- trends / research directions ------------------------------------------------
    def trends(self) -> dict:
        if self._trends is None:
            t = self._json("trends.json.gz")
            if not t.get("directions"):
                t["directions"] = [{**x, "name": x.get("phrase"), "keywords": [x.get("phrase")], "latest": x.get("papers"),
                                    "representative": [], "size": x.get("total"), "score": x.get("score")} for x in t.get("trends", [])]
            t["byId"] = {x["id"]: x for x in t["directions"]}
            self._trends = t
        return self._trends

    def direction(self, did: int) -> Optional[dict]:
        return self.trends()["byId"].get(did)

    def dir_papers(self, did: int) -> list[dict]:
        d = self.dirs
        if d is None:
            return []
        d3 = d.reshape(-1, 3)
        idx = np.nonzero((d3 == did).any(axis=1))[0]
        return [{"sid": int(s), "ym": self.ym(int(s)), "main": int(d3[s, 0]) == did} for s in idx]

    def category_range(self, cat_idx: int) -> tuple[int, int]:
        cats = self.meta.reshape(-1, 4)[:, 2]
        idx = np.nonzero(cats == cat_idx)[0]
        if not len(idx):
            return (0, 0)
        lo = int(idx[0])
        hi = lo
        while hi + 1 < len(cats) and cats[hi + 1] == cat_idx:  # contiguous run, like core.js
            hi += 1
        return (lo, hi + 1)

    # ---- paper records ---------------------------------------------------------------
    def lite(self, sid: int) -> dict:
        per = self.manifest["lite_per"]
        path = f"l/{sid // per:04d}.json.gz"
        r = self._blocks.get(path, lambda: self._json(path))[sid % per]
        return {"sid": sid, "title": r[0], "date": r[1], "year": int(r[1][:4]), "cat": r[2], "authors": r[3], "snippet": r[4],
                "key": r[5], "venue": (r[6] if len(r) > 6 else "") or "", "isArxiv": is_arxiv_key(r[5]),
                "isNew": r[1][:7] == self.manifest["new_month"]}

    def detail(self, sid: int) -> dict:
        per = self.manifest["detail_per"]
        path = f"p/{sid // per:04d}.json.gz"
        return {"sid": sid, **self._blocks.get(path, lambda: self._json(path))[sid % per]}

    def sid_of_key(self, key: str) -> Optional[int]:
        path = f"k/{fnv1a(key) % self.manifest['key_shards']:02x}.json.gz"
        return self._blocks.get(path, lambda: self._json(path)).get(key)

    def _term_shard(self, t: str) -> dict:
        path = f"t/{fnv1a(t) % self.manifest['term_shards']:04d}.json.gz"

        def make():
            try:
                return self._json(path)
            except OSError:
                return {}
        return self._terms.get(path, make)

    def _passes(self, sid, year_from, year_to, cat, dir, pub) -> bool:
        y = self.year(sid)
        if y < year_from or y > year_to:
            return False
        if cat is not None and self.cat(sid) != cat:
            return False
        if dir is not None and dir not in self.dirs_of(sid):
            return False
        if pub and not self.has_venue(sid):
            return False
        return True

    # ---- keyword search (field-weighted BM25 from the term shards) -------------------
    def search(self, english: str, year_from=0, year_to=9999, cat=None, dir=None, pub=False, sort="relevance",
               exclude=-1, limit=400) -> dict:
        uni = tokens(english)
        uniq = list(dict.fromkeys(uni))
        bis = list(dict.fromkeys(f"{a} {b}" for a, b in zip(uni, uni[1:])))
        terms = ([(t, 1.0) for t in uniq] + [(t, 1.6) for t in bis])[:24]
        if not terms:
            return {"hits": [], "total": 0}
        score: dict[int, float] = {}
        matched: dict[int, int] = {}
        for t, w in terms:
            p = self._term_shard(t).get(t)
            if not p:
                continue
            sid = 0
            for i in range(1, len(p), 2):
                sid += p[i]
                score[sid] = score.get(sid, 0) + p[i + 1] * w
                if w == 1.0:
                    matched[sid] = matched.get(sid, 0) + 1
        n_uni = len(uniq)
        hits = []
        for sid, s in score.items():
            if sid == exclude or not self._passes(sid, year_from, year_to, cat, dir, pub):
                continue
            cover = (matched.get(sid, 0) / n_uni) if n_uni else 1
            hits.append({"sid": sid, "score": s * (0.3 + 0.7 * cover * cover), "ym": self.ym(sid), "cat": self.cat(sid), "dir": self.main_dir(sid)})
        hits.sort(key=lambda h: -h["score"])
        top = hits[:limit]
        if sort == "recent":
            top = sorted(top[:200], key=lambda h: (-h["ym"], -h["score"]))
        return {"hits": top, "total": len(hits)}

    # ---- typo correction --------------------------------------------------------------
    def vocab(self):
        if self._vocab is False:
            try:
                words = []
                for line in self._read("vocab.txt.gz").decode("utf-8").split("\n"):
                    w, _, n = line.partition("\t")
                    if w:
                        words.append((w, float(n) if n else float("nan")))
                self._vocab = (words, dict(words))
            except OSError:
                self._vocab = None
        return self._vocab

    def correct(self, english: str) -> dict:
        """"transfomer architecure" -> "transformer architecture" (rare or unknown words only)."""
        voc = self.vocab()
        if not voc:
            return {"text": english, "fixes": []}
        words, lookup = voc
        fixes = []

        def fix(m):
            word = m.group(0)
            if word == word.upper():
                return word  # acronyms (RLHF, VLA) stay as typed
            w = stem(word.lower())
            have = lookup.get(w, 0) or 0
            if have >= 15 or word.lower() in STOP:
                return word
            mx = 1 if len(w) <= 5 else 2
            best, best_d, best_n = None, mx + 1, 0
            floor = max(50, have * 20)
            for cand, n in words:
                if n < floor:
                    break  # vocab is sorted by frequency
                if abs(len(cand) - len(w)) > mx or (cand[:1] != w[:1] and cand[1:2] != w[1:2]):
                    continue
                d = edit_distance(w, cand, mx)
                if d < best_d or (d == best_d and n > best_n):
                    best, best_d, best_n = cand, d, n
            if not best or best_d > mx:
                return word
            fixes.append([word, best])
            return best

        return {"text": re.sub(r"[A-Za-z]{4,}", fix, str(english)), "fixes": fixes}

    # ---- meaning vectors (256-d int8 per paper, sharded by main research direction) --
    def centroids(self):
        if self._cent is False:
            try:
                buf = self._read("v/centroids.bin.gz")
                k, dim = (int(x) for x in np.frombuffer(buf, dtype="<u4", count=2))
                self._cent = {"k": k, "dim": dim, "scale": np.frombuffer(buf, dtype="<f4", count=k, offset=8),
                              "q": np.frombuffer(buf, dtype=np.int8, count=k * dim, offset=8 + 4 * k).reshape(k, dim)}
            except OSError:
                self._cent = None
        return self._cent

    def vec_shard(self, t: int):
        path = f"v/{t:03d}.bin.gz"

        def make():
            try:
                buf = self._read(path)
            except OSError:
                return None
            n, dim = (int(x) for x in np.frombuffer(buf, dtype="<u4", count=2))
            return {"n": n, "dim": dim, "sid": np.frombuffer(buf, dtype="<i4", count=n, offset=8),
                    "scale": np.frombuffer(buf, dtype="<f4", count=n, offset=8 + 4 * n),
                    "q": np.frombuffer(buf, dtype=np.int8, count=n * dim, offset=8 + 8 * n).reshape(n, dim)}
        return self._vecs.get(path, make)

    def paper_vec(self, sid: int) -> Optional[np.ndarray]:
        t = self.main_dir(sid)
        if t == 255:
            return None
        sh = self.vec_shard(t)
        if not sh:
            return None
        i = int(np.searchsorted(sh["sid"], sid))
        if i >= sh["n"] or int(sh["sid"][i]) != sid:
            return None
        return (sh["q"][i].astype(np.float64) * float(sh["scale"][i])).astype(np.float32)

    def project_raw(self, raw) -> Optional[np.ndarray]:
        """768-d model output -> the 256-d space of data/v (same projection as the pipeline)."""
        if self._pca is False:
            try:
                buf = self._read("v/pca.bin.gz")
                din, dout = (int(x) for x in np.frombuffer(buf, dtype="<u4", count=2))
                self._pca = {"din": din, "dout": dout, "mu": np.frombuffer(buf, dtype="<f4", count=din, offset=8),
                             "P": np.frombuffer(buf, dtype="<f4", count=din * dout, offset=8 + 4 * din).reshape(dout, din)}
            except OSError:
                self._pca = None
        p = self._pca
        raw = np.asarray(raw, dtype=np.float64)
        if not p or len(raw) != p["din"]:
            return None
        n = math.sqrt(float(raw @ raw)) or 1.0
        c = (raw / n - p["mu"].astype(np.float64)).astype(np.float32)
        return (p["P"].astype(np.float64) @ c.astype(np.float64)).astype(np.float32)

    def feedback_vec(self, hits: list[dict]) -> Optional[np.ndarray]:
        """No query embedder: average the vectors of the best keyword matches."""
        vecs = [v for v in (self.paper_vec(h["sid"]) for h in hits[:6]) if v is not None]
        if not vecs:
            return None
        out = np.zeros(len(vecs[0]), dtype=np.float32)
        for i, v in enumerate(vecs):
            out = (out.astype(np.float64) + v.astype(np.float64) * (1 / (1 + i * 0.3))).astype(np.float32)
        return out

    def semantic(self, qv, year_from=0, year_to=9999, cat=None, dir=None, pub=False, exclude=-1, probe=8, limit=300,
                 dirs: Optional[list[int]] = None) -> list[dict]:
        C = self.centroids()
        if not C or qv is None:
            return []
        qv = np.asarray(qv, dtype=np.float64)
        norm = math.sqrt(float(qv @ qv)) or 1.0
        q = (qv / norm).astype(np.float32).astype(np.float64)
        cs = (C["q"].astype(np.float64) @ q) * C["scale"].astype(np.float64)
        order = np.argsort(-cs, kind="stable")
        pick = list(dirs) if dirs else [int(t) for t in order[:probe]]
        if dir is not None and dir not in pick:
            pick.insert(0, dir)
        scores, sids = [], []
        for t in pick:
            sh = self.vec_shard(t)
            if not sh:
                continue
            scores.append((sh["q"].astype(np.float64) @ q) * sh["scale"].astype(np.float64))
            sids.append(sh["sid"])
        if not scores:
            return []
        s_all = np.concatenate(scores)
        sid_all = np.concatenate(sids)
        if exclude >= 0:
            keep = sid_all != exclude
            s_all, sid_all = s_all[keep], sid_all[keep]
        hits = []
        for i in np.argsort(-s_all, kind="stable"):
            sid = int(sid_all[i])
            if not self._passes(sid, year_from, year_to, cat, dir, pub):
                continue
            hits.append({"sid": sid, "sim": float(s_all[i]), "ym": self.ym(sid), "cat": self.cat(sid), "dir": self.main_dir(sid)})
            if len(hits) >= limit:
                break
        return hits

    # ---- main search: keywords + meaning, fused by reciprocal rank -------------------
    def embed_query(self, text: str, embed: Optional[Callable[[str], Optional[list]]]) -> Optional[np.ndarray]:
        if not embed or not text.strip():
            return None
        try:
            v = embed(text)
        except Exception:  # noqa: BLE001  (embedder down: fall back like the browser does)
            return None
        if not v:
            return None
        v = np.asarray(v, dtype=np.float32)
        return v if len(v) != 768 else self.project_raw(v)

    def fuzzy_search(self, english: str, embed: Optional[Callable] = None, sort="relevance", limit=400, **opts) -> dict:
        fixed = self.correct(english)
        kw = self.search(fixed["text"], **{**opts, "sort": "relevance", "limit": 600})
        qv = self.embed_query(fixed["text"], embed)
        mode = "embed" if qv is not None else "feedback"
        if qv is None and kw["hits"]:
            qv = self.feedback_vec(kw["hits"])
        sem_opts = {k: v for k, v in opts.items() if k in ("year_from", "year_to", "cat", "dir", "pub", "exclude")}
        sem = self.semantic(qv, **sem_opts, limit=300) if qv is not None else []
        K = 60
        w_kw, w_sem = (1, 1.2) if mode == "embed" else (1.3, 1)
        fused: dict[int, dict] = {}
        for i, h in enumerate(kw["hits"]):
            fused[h["sid"]] = {**h, "kwRank": i, "score": w_kw / (K + i)}
        for i, h in enumerate(sem):
            f = fused.get(h["sid"])
            if f:
                f["score"] += w_sem / (K + i)
                f["semRank"] = i
                f["sim"] = h["sim"]
            elif h["sim"] > 0.35 or i < 60:
                fused[h["sid"]] = {**h, "semRank": i, "score": w_sem / (K + i), "semOnly": True}
        hits = sorted(fused.values(), key=lambda h: -h["score"])
        top = hits[:limit]
        if sort == "recent":
            top = sorted(top[:200], key=lambda h: (-h["ym"], -h["score"]))
        return {"hits": top, "total": len(hits), "fixes": fixed["fixes"], "query": fixed["text"],
                "semantic": mode if sem else "none", "keywordTotal": kw["total"]}

    def similar(self, sid: int, text: str, limit=10) -> list[dict]:
        """Papers closest in meaning to one paper (falls back to keywords)."""
        v = self.paper_vec(sid)
        if v is not None:
            hits = self.semantic(v, exclude=sid, probe=4, limit=limit)
            if hits:
                return hits
        return self.search(text, exclude=sid, limit=limit)["hits"]

    # ---- Q&A retrieval (ask.js retrieve): whole index -> directions -> diverse sources
    def retrieve(self, question: str, queries: list[str], pinned_key: Optional[str] = None,
                 embed: Optional[Callable] = None, n_key: int = 8, n_more: int = 12) -> dict:
        T = self.trends()
        results = [self.fuzzy_search(q, embed=embed, limit=160) for q in queries]
        pool: dict[int, float] = {}
        for r in results:
            for rank, h in enumerate(r["hits"]):
                pool[h["sid"]] = pool.get(h["sid"], 0) + 1 / (20 + rank)
        matched = max([max(r["total"], r.get("keywordTotal") or 0) for r in results] + [len(pool)])
        d_score: dict[int, float] = {}
        d_sum = 0.0
        for sid, sc in pool.items():
            for j, d in enumerate(self.dirs_of(sid)):
                w = sc * (0.5 if j else 1)
                d_score[d] = d_score.get(d, 0) + w
                d_sum += w
        ranked = sorted(d_score.items(), key=lambda x: -x[1])
        chosen = [d for i, (d, w) in enumerate(ranked) if i == 0 or w / d_sum >= 0.1][:4]
        pool_ranked = [{"sid": sid} for sid, _ in sorted(pool.items(), key=lambda x: -x[1])]
        qv = self.embed_query(". ".join(queries)[:400], embed)
        if qv is None:
            qv = self.feedback_vec(pool_ranked[:8])
        if qv is not None:
            for rank, h in enumerate(self.semantic(qv, dirs=chosen, limit=250)):
                pool[h["sid"]] = pool.get(h["sid"], 0) + 1.2 / (20 + rank)
        cands = [{"sid": sid, "sc": sc} for sid, sc in sorted(pool.items(), key=lambda x: -x[1])[:70]]
        top = cands[0]["sc"] if cands else 1
        for c in cands:
            c["v"] = self.paper_vec(c["sid"])
            c["rel"] = 0.55 * _cos(qv, c["v"]) + 0.45 * (c["sc"] / top) if qv is not None and c["v"] is not None else c["sc"] / top
        picked = []
        if pinned_key:
            pin = self.sid_of_key(pinned_key)
            if pin is not None:
                picked.append({"sid": pin, "v": self.paper_vec(pin), "rel": 1})
                cands = [c for c in cands if c["sid"] != pin]
        rest = list(cands)
        while len(picked) < n_key + n_more and rest:
            bi, bs = -1, -1e9
            for i, c in enumerate(rest):
                dup = max([0.0] + [_cos(c["v"], p["v"]) for p in picked if p["v"] is not None]) if c["v"] is not None else 0
                if dup > 0.9:
                    continue  # near-duplicate of a chosen paper
                s = 0.65 * c["rel"] - 0.35 * dup
                if s > bs:
                    bs, bi = s, i
            if bi < 0:
                break
            picked.append(rest.pop(bi))
        terms = list(dict.fromkeys(t for q in queries for t in tokens(q)))
        sources = []
        for i, c in enumerate(picked):
            if i < n_key:
                d = self.detail(c["sid"])
                sources.append({"n": i + 1, "key": d["ax"], "title": d["t"], "venue": d["vn"], "date": f"{d['vn']}, {d['d']}" if d["vn"] else d["d"],
                                "authors": ", ".join(d["au"][:3]) + (" et al." if d["na"] > 3 else ""), "abstract": excerpt(d["ab"], terms), "cat": d["cat"][0]})
            else:
                d = self.lite(c["sid"])
                sources.append({"n": i + 1, "key": d["key"], "title": d["title"], "venue": d["venue"], "date": f"{d['venue']}, {d['date']}" if d["venue"] else d["date"],
                                "authors": d["authors"], "abstract": "", "cat": d["cat"]})
        dir_info = [T["byId"][d] for d in chosen if d in T["byId"]]
        years: dict[str, int] = {}
        for sid in pool:
            y = str(self.year(sid))
            years[y] = years.get(y, 0) + 1

        def grow(t):
            return f"{'+' if t['growth'] >= 1 else ''}{_js_round((t['growth'] - 1) * 100)}%"
        overview = (f"searched all {self.manifest['papers']} papers; the question falls in these research directions: "
                    + "; ".join(f"{t.get('name_en') or t['name']} ({t['size']} papers in index, {t['recent']} in last 3 months, share {grow(t)} vs prior year)" for t in dir_info)
                    + f". Top matches by year: {', '.join(f'{y}: {n}' for y, n in sorted(years.items()))}.")
        return {"sources": sources, "overview": overview, "dirs": [{"id": t["id"], "name": t.get("name") or t.get("name_en")} for t in dir_info], "matched": matched}


_DEFAULT: Optional[SearchIndex] = None
_DEFAULT_LOCK = threading.Lock()


def default_index() -> SearchIndex:
    """One shared index over public/research/data (reloaded when the snapshot changes)."""
    global _DEFAULT
    with _DEFAULT_LOCK:
        if _DEFAULT is None:
            _DEFAULT = SearchIndex()
        else:
            try:
                built = json.loads((DATA / "manifest.json").read_text(encoding="utf-8")).get("built_at")
                if built != _DEFAULT.manifest.get("built_at"):
                    _DEFAULT = SearchIndex()
            except OSError:
                pass
        return _DEFAULT
