"""Search functions shaped as tools for an LLM agent (JSON in, JSON out).

Each tool takes keyword arguments and returns plain dicts/lists that can be sent to a
model as-is. TOOLS lists them with JSON-schema parameters, so they can be registered
with any function-calling framework (LangGraph / LangChain later) or called over HTTP
through serve_local.py (`POST /api/tools/<name>`).

Paper ids are the public keys used in site URLs: arXiv ids ("2401.01234") or
"s2-<Semantic Scholar id>" for conference/journal papers that are not on arXiv.
"""
from __future__ import annotations

from typing import Callable, Optional

from .search import SearchIndex, default_index, has_cjk, local_rewrite

# Optional query embedder (text -> 256-d vector), set by the host process.
# Without it, semantic search uses the vectors of the best keyword matches.
EMBED: dict[str, Optional[Callable]] = {"fn": None}


def _ix() -> SearchIndex:
    return default_index()


def _clamp(v, lo, hi, default):
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def _dir_names(ix: SearchIndex, sid: int) -> list[dict]:
    out = []
    for d in ix.dirs_of(sid):
        t = ix.direction(d)
        if t:
            out.append({"id": d, "name": t.get("name"), "name_en": t.get("name_en")})
    return out


def _card(ix: SearchIndex, sid: int, extra: Optional[dict] = None) -> dict:
    p = ix.lite(sid)
    card = {"id": p["key"], "title": p["title"], "date": p["date"], "venue": p["venue"] or ("arXiv" if p["isArxiv"] else ""),
            "category": p["cat"], "authors": p["authors"], "snippet": p["snippet"], "directions": _dir_names(ix, sid)}
    if extra:
        card.update(extra)
    return card


def _english(query: str) -> tuple[str, str]:
    """Chinese queries are mapped to English terms with the built-in dictionary (the agent
    should normally write English queries itself)."""
    if has_cjk(query):
        return local_rewrite(query), "dictionary"
    return query, "none"


def search_papers(query: str, year_from: Optional[int] = None, year_to: Optional[int] = None, category: Optional[str] = None,
                  direction: Optional[int] = None, published_only: bool = False, sort: str = "relevance", limit: int = 10) -> dict:
    ix = _ix()
    english, via = _english(str(query or ""))
    cat = ix.cat_index(category)
    if category and cat is None:
        return {"error": f"unknown category {category!r}; use one of {ix.manifest['categories']}"}
    r = ix.fuzzy_search(english, embed=EMBED["fn"], sort="recent" if sort == "recent" else "relevance", limit=_clamp(limit, 1, 50, 10),
                        year_from=int(year_from or 0), year_to=int(year_to or 9999), cat=cat,
                        dir=None if direction is None else int(direction), pub=bool(published_only))
    return {"query": r["query"], "translated_from": query if via != "none" else None, "spelling_fixes": r["fixes"],
            "total_matches": r["total"], "semantic": r["semantic"],
            "papers": [_card(ix, h["sid"], {"semantic_only": True} if h.get("semOnly") else None) for h in r["hits"]]}


def get_paper(paper_id: str) -> dict:
    ix = _ix()
    sid = ix.sid_of_key(str(paper_id).strip())
    if sid is None:
        return {"error": f"paper {paper_id!r} is not in the index"}
    d = ix.detail(sid)
    key = d.get("ax") or paper_id
    links = {"site": f"#/paper/{key}"}
    if ix.lite(sid)["isArxiv"]:
        links.update(arxiv=f"https://arxiv.org/abs/{key}", pdf=f"https://arxiv.org/pdf/{key}")
    if d.get("doi"):
        links["doi"] = f"https://doi.org/{d['doi']}"
    if d.get("url"):
        links["url"] = d["url"]
    return {"id": key, "title": d.get("t"), "authors": d.get("au"), "n_authors": d.get("na"), "date": d.get("d"), "updated": d.get("u"),
            "categories": d.get("cat"), "venue": d.get("vn") or "", "journal_ref": d.get("jr") or "", "comment": d.get("cm") or "",
            "abstract": d.get("ab"), "directions": _dir_names(ix, sid), "links": links}


def related_papers(paper_id: str, limit: int = 10) -> dict:
    ix = _ix()
    sid = ix.sid_of_key(str(paper_id).strip())
    if sid is None:
        return {"error": f"paper {paper_id!r} is not in the index"}
    p = ix.lite(sid)
    hits = ix.similar(sid, p["title"] + " " + p["snippet"], _clamp(limit, 1, 30, 10))
    return {"paper": {"id": p["key"], "title": p["title"]},
            "related": [_card(ix, h["sid"], {"similarity": round(h["sim"], 3)} if "sim" in h else None) for h in hits]}


def find_evidence(question: str, queries: list[str], pinned_paper_id: Optional[str] = None) -> dict:
    """The site's Q&A retrieval: searches the whole index with several English queries,
    finds the research directions the question belongs to, scans them by meaning and
    returns ~20 diverse papers (first 8 with abstract excerpts) plus an overview."""
    qs = [str(q).strip() for q in (queries or []) if str(q).strip()][:4] or [_english(str(question))[0]]
    return _ix().retrieve(str(question), qs, pinned_key=pinned_paper_id, embed=EMBED["fn"])


def _dir_summary(t: dict) -> dict:
    return {"id": t["id"], "name": t.get("name"), "name_en": t.get("name_en"), "description": t.get("desc"), "keywords": (t.get("keywords") or [])[:8],
            "papers": t.get("size"), "last_3_months": t.get("recent"), "growth": t.get("growth"), "main_category": t.get("main_category")}


def list_directions(sort: str = "rising", limit: int = 20) -> dict:
    T = _ix().trends()
    ds = list(T["directions"])
    if sort == "size":
        ds.sort(key=lambda t: -(t.get("size") or 0))
    else:  # rising: the site's own ranking score
        ds.sort(key=lambda t: -(t.get("score") or 0))
    return {"total": len(ds), "window": T.get("window"), "directions": [_dir_summary(t) for t in ds[:_clamp(limit, 1, 150, 20)]]}


def get_direction(direction_id: int, n_papers: int = 8) -> dict:
    t = _ix().direction(int(direction_id))
    if not t:
        return {"error": f"unknown direction {direction_id}"}
    n = _clamp(n_papers, 1, 30, 8)
    paper = lambda r: {"id": r[3], "title": r[1], "date": r[2], "category": r[4]}  # noqa: E731
    return {**_dir_summary(t), "monthly": t.get("monthly"), "latest": [paper(r) for r in (t.get("latest") or [])[:n]],
            "representative": [paper(r) for r in (t.get("representative") or [])[:n]]}


def latest_in_category(category: str, limit: int = 10) -> dict:
    ix = _ix()
    cat = ix.cat_index(category)
    if cat is None:
        return {"error": f"unknown category {category!r}; use one of {ix.manifest['categories']}"}
    lo, hi = ix.category_range(cat)
    return {"category": category, "name": ix.cat_name(category), "total": hi - lo,
            "papers": [_card(ix, sid) for sid in range(lo, min(hi, lo + _clamp(limit, 1, 50, 10)))]}


def index_info() -> dict:
    m = _ix().manifest
    return {k: m.get(k) for k in ("version", "built_at", "papers", "window", "new_papers", "new_month", "venue_only", "with_venue", "directions", "category_names")}


_STR, _INT, _BOOL = {"type": "string"}, {"type": "integer"}, {"type": "boolean"}

TOOLS: dict[str, dict] = {
    "search_papers": {"fn": search_papers, "description":
        "Fuzzy search over all indexed AI/CS papers (keywords + abstract meaning, typo correction). Write the query in English, as words that appear in titles/abstracts.",
        "parameters": {"type": "object", "required": ["query"], "properties": {
            "query": _STR, "year_from": _INT, "year_to": _INT,
            "category": {**_STR, "description": "arXiv primary category, e.g. cs.CV"},
            "direction": {**_INT, "description": "research direction id (see list_directions)"},
            "published_only": {**_BOOL, "description": "only papers published at a top conference/journal"},
            "sort": {"type": "string", "enum": ["relevance", "recent"]}, "limit": {**_INT, "description": "1-50, default 10"}}}},
    "get_paper": {"fn": get_paper, "description": "Full record of one paper: abstract, authors, venue, research directions, links.",
        "parameters": {"type": "object", "required": ["paper_id"], "properties": {"paper_id": {**_STR, "description": "arXiv id or s2-<id>"}}}},
    "related_papers": {"fn": related_papers, "description": "Papers closest in meaning to one paper (abstract embeddings).",
        "parameters": {"type": "object", "required": ["paper_id"], "properties": {"paper_id": _STR, "limit": _INT}}},
    "find_evidence": {"fn": find_evidence, "description":
        "Collect ~20 diverse papers to answer a research question: whole-index search with 1-4 English queries, then a full scan of the matching research directions.",
        "parameters": {"type": "object", "required": ["question", "queries"], "properties": {
            "question": _STR, "queries": {"type": "array", "items": _STR, "description": "1-4 English search queries"},
            "pinned_paper_id": {**_STR, "description": "a paper that must be included"}}}},
    "list_directions": {"fn": list_directions, "description": "The 150 research directions (topic clusters), ranked by recent growth or by size.",
        "parameters": {"type": "object", "properties": {"sort": {"type": "string", "enum": ["rising", "size"]}, "limit": _INT}}},
    "get_direction": {"fn": get_direction, "description": "One research direction: description, keywords, monthly paper counts, latest and representative papers.",
        "parameters": {"type": "object", "required": ["direction_id"], "properties": {"direction_id": _INT, "n_papers": _INT}}},
    "latest_in_category": {"fn": latest_in_category, "description": "Newest papers of one arXiv primary category.",
        "parameters": {"type": "object", "required": ["category"], "properties": {"category": _STR, "limit": _INT}}},
    "index_info": {"fn": index_info, "description": "What the index covers: snapshot date, paper counts, time window, categories.",
        "parameters": {"type": "object", "properties": {}}},
}


def tool_specs() -> list[dict]:
    """Name, description and JSON-schema parameters of every tool (for function calling)."""
    return [{"name": n, "description": t["description"], "parameters": t["parameters"]} for n, t in TOOLS.items()]


def call_tool(name: str, args: Optional[dict] = None) -> dict:
    t = TOOLS.get(name)
    if not t:
        return {"error": f"unknown tool {name!r}"}
    allowed = t["parameters"].get("properties", {})
    args = {k: v for k, v in (args or {}).items() if k in allowed and v is not None}
    missing = [k for k in t["parameters"].get("required", []) if k not in args]
    if missing:
        return {"error": f"missing arguments: {', '.join(missing)}"}
    try:
        return t["fn"](**args)
    except (TypeError, ValueError) as e:
        return {"error": f"bad arguments: {e}"}
