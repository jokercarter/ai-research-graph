"""Literature review agent (LangGraph): topic -> structured review with citations + BibTeX.

    scope ─► survey ─► outline ─► approve ─┬─► research (one per section, in parallel) ─► number ─┬─► write (one per section, in parallel) ─► synthesize ─► assemble
                                  (pause:  └─► research ...                                       └─► write ...
                                   the user edits the outline)

- scope      (fast model)  review title, language and 4-6 English search queries for the topic
- survey     (tools)       searches the whole index with every query; keeps ~100 candidates,
                           the research directions they fall in and their years
- outline    (main model)  groups the candidates into 3-5 sections (technical routes / themes)
- approve    (human)       `interrupt`: the run pauses and the page shows the outline; the user
                           renames, removes or adds sections, then the run resumes from here
- research   (tools)       per section, targeted retrieval (same as the Q&A) -> up to 10 papers
- number                   one global, numbered source list; abstract excerpts for each paper
- write      (main model)  per section, a cited section draft
- synthesize (main model)  introduction + trends and open problems
- assemble                 checks citations, renumbers them by first use, builds the reference
                           list, Markdown and BibTeX

Fan-out uses LangGraph `Send`, so section research and section writing run concurrently.
State is checkpointed per review (thread_id), which is what lets the run pause at the
outline and continue in a later request. The model is injected exactly as in agent.py:
`llm(system, messages, tier, max_tokens) -> str`, tier "fast" or "main".
"""
from __future__ import annotations

import operator
import re
from typing import Annotated, Callable, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send, interrupt

from . import tools
from .agent import _clean_query, _json, memory
from .search import default_index, excerpt, has_cjk, is_arxiv_key, local_rewrite, tokens

N_CANDIDATES = 100     # survey candidates shown to the outline step
PER_SECTION = 10       # papers per section given to the writer
MAX_SECTIONS = 6

LLM = Callable[[str, list, str, int], str]


class ReviewState(TypedDict, total=False):
    # input
    topic: str
    year_from: int
    year_to: int
    confirm: bool                  # pause for the user to edit the outline
    # scope / survey
    language: str
    title: str
    queries: list
    pool: list                     # [{key, title, year, venue}] ranked candidates
    dirs: list                     # [{id, name, name_en, size, recent, growth}]
    years: dict                    # {year: n} over the candidate pool
    matched: int
    # outline -> sections
    outline: dict                  # {title, sections:[{id, title, focus, queries, papers:[key]}]}
    picks: Annotated[list, operator.add]     # [{idx, keys}] from research branches
    sources: list                  # global numbered list
    sec_sources: list              # per section: [n, ...]
    drafts: Annotated[list, operator.add]    # [{idx, text}] from write branches
    intro: str
    outlook: str
    # result
    body: str
    markdown: str
    references: list
    bibtex: str
    cited: int
    steps: Annotated[list, operator.add]     # [{node, text}] shown in the UI


class SectionTask(TypedDict, total=False):
    idx: int
    section: dict
    topic: str
    year_from: int
    year_to: int


class WriteTask(TypedDict, total=False):
    idx: int
    section: dict
    sources: list
    others: list
    topic: str
    title: str
    language: str


def _english(text: str) -> str:
    return _clean_query(local_rewrite(text) if has_cjk(text) else text)


def _lang_name(lang: str) -> str:
    return "English" if lang == "en" else "Simplified Chinese"


SCOPE_SYSTEM = """You plan a literature review over an index of ~550k AI/CS papers (2021-12 to now; arXiv plus top venues). The index matches English words in titles and abstracts, plus meaning.
Given a review topic, output strict JSON only:
{"language": "zh" | "en",
 "title": "<a concise review title, in the topic's language>",
 "queries": ["<4-6 English keyword queries, 3-8 words each, technical terms papers would use; together they cover the topic's different angles: core methods, main variants, applications, benchmarks/evaluation, recent trends>"]}"""

OUTLINE_SYSTEM = """You design the structure of a literature review. You get the topic and candidate papers found in a paper index (numbered, with year and venue) plus the research directions they fall in.
Group the work into 3-5 sections. Each section is one distinct technical route, problem or theme (for example "retrieval-side improvements", "benchmarks and evaluation"), not a single paper. Order them from foundations to the frontier. Output strict JSON only:
{"title": "<review title in {lang}>",
 "sections": [{"title": "<short section title in {lang}>", "focus": "<one sentence in {lang}: what this section covers>", "queries": ["<1-2 English keyword queries to find more papers for this section>"], "papers": [<numbers of the candidates that belong to this section, 3-12>]}]}
Every section needs papers. Candidates that fit nowhere may be left out."""

SECTION_SYSTEM = """You write one section of a literature review on "{topic}" (review title: "{title}").
This section: "{section}" — {focus}
Other sections of the review (leave their content to them): {others}

Rules: use only the papers below and cite them as [n] with the given numbers right after each claim. Never invent papers, numbers or results; if the abstracts do not say something, do not claim it.
Write in {lang}, keeping paper titles, method names and technical terms in English. Length: 300-500 words (in Chinese 500-800 characters). Cover: (1) the problem this line of work addresses and its core idea; (2) the representative methods, grouped by approach, and how they differ from each other; (3) strengths, limitations and what remains open. Plain paragraphs, optionally one short bullet list. No headings, no section title, no reference list.

Papers:
{papers}"""

SYNTH_SYSTEM = """You write the opening and the closing of a literature review titled "{title}" on "{topic}". You get the drafts of its sections and statistics from the paper index.
Output exactly these two parts, in {lang}, keeping technical terms in English, no headings inside the parts:
===INTRO===
<120-220 words (in Chinese 200-350 characters): why the topic matters, the scope of this review (papers from the index, time span) and how the review is organized (name the sections).>
===OUTLOOK===
<200-350 words (in Chinese 350-550 characters): trends across the sections (use the statistics: growth, recent years), how the routes relate or converge, and 3-5 concrete open problems and future directions. Cite papers as [n], using only numbers that appear in the drafts.>"""

LABELS = {"zh": {"intro": "引言", "outlook": "趋势与开放问题", "refs": "参考文献"},
          "en": {"intro": "Introduction", "outlook": "Trends and Open Problems", "refs": "References"}}

CITE = re.compile(r"\[(\d+(?:\s*[,，、]\s*\d+)*)\]")


def build_graph(llm: LLM, checkpointer=None):
    ix = default_index()
    try:  # each model call becomes a child run in LangSmith (no-op unless tracing is on)
        from langsmith import traceable
        llm = traceable(run_type="llm", name="claude")(llm)
    except ImportError:
        pass

    def years_ok(sid, y0, y1):
        y = ix.year(sid)
        return (not y0 or y >= y0) and (not y1 or y <= y1)

    # ---- scope: title, language, search queries -------------------------------------
    def scope(state: ReviewState):
        topic = state["topic"]
        try:
            j = _json(llm(SCOPE_SYSTEM, [{"role": "user", "content": f"Review topic: {topic}"}], "fast", 500))
        except Exception:  # noqa: BLE001  (fall back to the topic itself)
            j = {}
        queries = list(dict.fromkeys(q for q in (_clean_query(x) for x in (j.get("queries") or [])[:6]) if q))
        if not queries:
            queries = [_english(topic) or topic]
        lang = j.get("language") if j.get("language") in ("zh", "en") else ("zh" if has_cjk(topic) else "en")
        title = str(j.get("title") or topic)[:80]
        return {"language": lang, "title": title, "queries": queries,
                "steps": [{"node": "scope", "text": f"综述题目：{title}；从 {len(queries)} 个角度检索：{' / '.join(queries)}"}]}

    # ---- survey: whole index -> candidate pool, directions, years ---------------------
    def survey(state: ReviewState):
        y0, y1 = state.get("year_from") or 0, state.get("year_to") or 9999
        pool: dict[int, float] = {}
        matched = 0
        for q in state["queries"]:
            r = ix.fuzzy_search(q, embed=tools.EMBED["fn"], limit=80, year_from=y0, year_to=y1)
            matched = max(matched, r["total"], r.get("keywordTotal") or 0)
            for rank, h in enumerate(r["hits"]):
                pool[h["sid"]] = pool.get(h["sid"], 0) + 1 / (20 + rank)
        ranked = sorted(pool.items(), key=lambda x: -x[1])[:N_CANDIDATES]
        cands, years, d_score = [], {}, {}
        for sid, sc in ranked:
            p = ix.lite(sid)
            cands.append({"key": p["key"], "title": p["title"], "year": p["year"], "venue": p["venue"]})
            years[str(p["year"])] = years.get(str(p["year"]), 0) + 1
            for j, d in enumerate(ix.dirs_of(sid)):
                d_score[d] = d_score.get(d, 0) + sc * (0.5 if j else 1)
        total = sum(d_score.values()) or 1
        dirs = []
        for d, w in sorted(d_score.items(), key=lambda x: -x[1])[:6]:
            t = ix.direction(d)
            if t and (not dirs or w / total >= 0.05):
                dirs.append({"id": d, "name": t.get("name"), "name_en": t.get("name_en"), "size": t.get("size"),
                             "recent": t.get("recent"), "growth": t.get("growth")})
        text = f"在全部 {ix.manifest['papers']:,} 篇论文中检索，选出 {len(cands)} 篇候选"
        if dirs:
            text += f"，分布在方向：{'、'.join(str(d['name']) for d in dirs)}"
        return {"pool": cands, "dirs": dirs, "years": dict(sorted(years.items())), "matched": matched,
                "steps": [{"node": "survey", "text": text}]}

    # ---- outline: group candidates into sections ---------------------------------------
    def outline(state: ReviewState):
        pool = state["pool"]
        if not pool:
            raise RuntimeError("没有检索到相关论文，换个主题或放宽时间范围试试")
        lang = state.get("language", "zh")
        lines = [f"Topic: {state['topic']}", "Research directions: " + "; ".join(f"{d['name_en'] or d['name']} ({d['size']} papers)" for d in state.get("dirs") or []),
                 "", "Candidate papers:"]
        lines += [f"{i + 1}. {p['title']} ({p['year']}{', ' + p['venue'] if p['venue'] else ''})" for i, p in enumerate(pool)]
        try:
            j = _json(llm(OUTLINE_SYSTEM.replace("{lang}", _lang_name(lang)), [{"role": "user", "content": "\n".join(lines)}], "main", 1500))
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"生成大纲失败：{e}") from e
        sections = []
        for s in (j.get("sections") or [])[:MAX_SECTIONS]:
            if not isinstance(s, dict) or not str(s.get("title") or "").strip():
                continue
            keys = []
            for n in s.get("papers") or []:
                try:
                    k = pool[int(n) - 1]["key"]
                except (ValueError, TypeError, IndexError):
                    continue
                if k not in keys:
                    keys.append(k)
            queries = [q for q in (_clean_query(x) for x in (s.get("queries") or [])[:2]) if q] or [_english(str(s["title"]))]
            sections.append({"id": len(sections), "title": str(s["title"]).strip()[:60], "focus": str(s.get("focus") or "")[:200],
                             "queries": queries, "papers": keys[:12]})
        if not sections:
            raise RuntimeError("模型没有给出可用的大纲，请重试")
        out = {"title": str(j.get("title") or state["title"])[:80], "sections": sections}
        return {"outline": out, "title": out["title"],
                "steps": [{"node": "outline", "text": f"拟定 {len(sections)} 节：" + "；".join(s["title"] for s in sections)}]}

    # ---- approve: pause for the user (human in the loop) --------------------------------
    def approve(state: ReviewState):
        if not state.get("confirm"):
            return {"steps": [{"node": "approve", "text": "按拟定的大纲继续"}]}
        by_key = {p["key"]: p for p in state["pool"]}
        edited = interrupt({
            "title": state["outline"]["title"],
            "sections": [{**s, "papers": [{"key": k, "title": by_key[k]["title"], "year": by_key[k]["year"]} for k in s["papers"] if k in by_key]}
                         for s in state["outline"]["sections"]],
        })
        merged = merge_outline(state["outline"], edited, state["queries"])
        return {"outline": merged, "title": merged["title"],
                "steps": [{"node": "approve", "text": f"大纲已确认：{len(merged['sections'])} 节"}]}

    def to_research(state: ReviewState):
        return [Send("research", {"idx": i, "section": s, "topic": state["topic"],
                                  "year_from": state.get("year_from") or 0, "year_to": state.get("year_to") or 0})
                for i, s in enumerate(state["outline"]["sections"])]

    # ---- research (one branch per section) --------------------------------------------
    def research(task: SectionTask):
        s = task["section"]
        y0, y1 = task.get("year_from") or 0, task.get("year_to") or 0
        keys = [k for k in s.get("papers", [])[:4]]  # the outline's best matches go first
        r = ix.retrieve(f"{task['topic']}: {s['title']}. {s.get('focus', '')}", s["queries"][:3], embed=tools.EMBED["fn"], n_key=0, n_more=20)
        for src in r["sources"]:
            sid = ix.sid_of_key(src["key"])
            if sid is not None and years_ok(sid, y0, y1) and src["key"] not in keys:
                keys.append(src["key"])
        for k in s.get("papers", [])[4:]:
            if k not in keys:
                keys.append(k)
        keys = keys[:PER_SECTION]
        return {"picks": [{"idx": task["idx"], "keys": keys}],
                "steps": [{"node": "research", "text": f"「{s['title']}」检索到 {len(keys)} 篇"}]}

    # ---- number: one global source list ------------------------------------------------
    def number(state: ReviewState):
        picks = {p["idx"]: p["keys"] for p in state.get("picks") or []}
        sections = state["outline"]["sections"]
        n_of, sources, sec_sources = {}, [], []
        for i, s in enumerate(sections):
            terms = list(dict.fromkeys(t for q in s["queries"] for t in tokens(q)))
            mine = []
            for k in picks.get(i, []):
                if k not in n_of:
                    sid = ix.sid_of_key(k)
                    if sid is None:
                        continue
                    d = ix.detail(sid)
                    n_of[k] = len(sources) + 1
                    sources.append({"n": n_of[k], "key": d.get("ax") or k, "title": d["t"], "authors": d.get("au") or [], "n_authors": d.get("na") or 0,
                                    "date": d["d"], "year": int(d["d"][:4]), "venue": d.get("vn") or "", "doi": d.get("doi") or "",
                                    "abstract": excerpt(d.get("ab") or "", terms, 600)})
                mine.append(n_of[k])
            sec_sources.append(mine)
        return {"sources": sources, "sec_sources": sec_sources,
                "steps": [{"node": "number", "text": f"整理出 {len(sources)} 篇不重复的论文，按节分配"}]}

    def to_write(state: ReviewState):
        by_n = {s["n"]: s for s in state["sources"]}
        titles = [s["title"] for s in state["outline"]["sections"]]
        return [Send("write", {"idx": i, "section": s, "sources": [by_n[n] for n in state["sec_sources"][i] if n in by_n],
                               "others": [t for j, t in enumerate(titles) if j != i], "topic": state["topic"],
                               "title": state["title"], "language": state.get("language", "zh")})
                for i, s in enumerate(state["outline"]["sections"])]

    # ---- write (one branch per section) -------------------------------------------------
    def write(task: WriteTask):
        s = task["section"]
        if not task["sources"]:
            return {"drafts": [{"idx": task["idx"], "text": ""}], "steps": [{"node": "write", "text": f"「{s['title']}」没有找到论文，跳过"}]}
        papers = "\n\n".join(f"[{p['n']}] {p['title']} ({p['venue'] + ', ' if p['venue'] else ''}{p['date']})\n{p['abstract']}" for p in task["sources"])
        system = (SECTION_SYSTEM.replace("{topic}", task["topic"]).replace("{title}", task["title"]).replace("{section}", s["title"])
                  .replace("{focus}", s.get("focus") or s["title"]).replace("{others}", "; ".join(task["others"]) or "(none)")
                  .replace("{lang}", _lang_name(task["language"])).replace("{papers}", papers))
        msg = [{"role": "user", "content": f"Write the section \"{s['title']}\"."}]
        try:
            text = llm(system, msg, "main", 1600)
        except Exception:  # noqa: BLE001  (one retry: a single failed call should not lose the review)
            text = llm(system, msg, "main", 1600)
        return {"drafts": [{"idx": task["idx"], "text": _strip_heading(text, s["title"])}],
                "steps": [{"node": "write", "text": f"写完「{s['title']}」（{len(task['sources'])} 篇论文）"}]}

    # ---- synthesize: introduction + outlook --------------------------------------------
    def synthesize(state: ReviewState):
        lang = state.get("language", "zh")
        drafts = {d["idx"]: d["text"] for d in state.get("drafts") or []}
        secs = state["outline"]["sections"]
        stats = [f"Candidate papers by year: {', '.join(f'{y}: {n}' for y, n in (state.get('years') or {}).items())}.",
                 "Research directions: " + "; ".join(
                     f"{d['name_en'] or d['name']} ({d['size']} papers, {d['recent']} in the last 3 months, share {'+' if (d['growth'] or 1) >= 1 else ''}{round(((d['growth'] or 1) - 1) * 100)}% vs prior year)"
                     for d in state.get("dirs") or [])]
        body = "\n\n".join(f"## {s['title']}\n{drafts.get(i, '')}" for i, s in enumerate(secs))
        system = SYNTH_SYSTEM.replace("{title}", state["title"]).replace("{topic}", state["topic"]).replace("{lang}", _lang_name(lang))
        text = llm(system, [{"role": "user", "content": "\n".join(stats) + "\n\nSection drafts:\n\n" + body}], "main", 1800)
        intro, outlook = _split_parts(text)
        return {"intro": intro, "outlook": outlook, "steps": [{"node": "synthesize", "text": "写完引言、趋势与开放问题"}]}

    # ---- assemble: check citations, renumber, references, BibTeX -------------------------
    def assemble(state: ReviewState):
        lang = state.get("language", "zh")
        L = LABELS.get(lang, LABELS["zh"])
        sources = {s["n"]: s for s in state["sources"]}
        drafts = {d["idx"]: d["text"] for d in state.get("drafts") or []}
        secs = state["outline"]["sections"]
        parts = [(L["intro"], state.get("intro", ""))] + [(s["title"], drafts.get(i, "")) for i, s in enumerate(secs)] + [(L["outlook"], state.get("outlook", ""))]
        new_n: dict[int, int] = {}
        dropped = 0

        def renum(m):
            nonlocal dropped
            out = []
            for x in re.split(r"\s*[,，、]\s*", m.group(1)):
                n = int(x)
                if n not in sources:
                    dropped += 1
                    continue
                new_n.setdefault(n, len(new_n) + 1)
                if new_n[n] not in out:
                    out.append(new_n[n])
            return f"[{', '.join(map(str, out))}]" if out else ""
        blocks = []
        for i, (head, text) in enumerate(parts):
            text = CITE.sub(renum, text or "").strip()
            if text:
                blocks.append(f"## {len(blocks) + 1} {head}\n\n{text}")
        body = "\n\n".join(blocks)
        refs = []
        for old, n in sorted(new_n.items(), key=lambda x: x[1]):
            s = sources[old]
            refs.append({"n": n, "key": s["key"], "title": s["title"], "authors": _authors(s), "year": s["year"], "venue": s["venue"],
                         "date": s["date"], "url": _url(s)})
        ref_lines = [_ref_line(r) for r in refs]
        markdown = f"# {state['title']}\n\n{body}\n\n## {L['refs']}\n\n" + "\n".join(ref_lines) + "\n"
        bib = _bibtex([sources[old] for old, _ in sorted(new_n.items(), key=lambda x: x[1])])
        note = f"核对引用：正文引用了 {len(refs)} 篇论文" + (f"，删除了 {dropped} 个无效编号" if dropped else "，全部有效") + "；已生成参考文献和 BibTeX"
        return {"body": body, "markdown": markdown, "references": refs, "bibtex": bib, "cited": len(refs),
                "steps": [{"node": "assemble", "text": note}]}

    g = StateGraph(ReviewState)
    for name, fn in (("scope", scope), ("survey", survey), ("outline", outline), ("approve", approve), ("research", research),
                     ("number", number), ("write", write), ("synthesize", synthesize), ("assemble", assemble)):
        g.add_node(name, fn)
    g.add_edge(START, "scope")
    g.add_edge("scope", "survey")
    g.add_edge("survey", "outline")
    g.add_edge("outline", "approve")
    g.add_conditional_edges("approve", to_research, ["research"])
    g.add_edge("research", "number")
    g.add_conditional_edges("number", to_write, ["write"])
    g.add_edge("write", "synthesize")
    g.add_edge("synthesize", "assemble")
    g.add_edge("assemble", END)
    return g.compile(checkpointer=checkpointer)


# ---- helpers ------------------------------------------------------------------------
def merge_outline(orig: dict, edited, topic_queries: list) -> dict:
    """Applies the user's outline edits. Sections keep their search queries and papers by
    id; renamed ones also search for the new title; new ones search for their title."""
    if not isinstance(edited, dict) or not isinstance(edited.get("sections"), list):
        return orig
    by_id = {s["id"]: s for s in orig["sections"]}
    out = []
    for e in edited["sections"][:MAX_SECTIONS]:
        if not isinstance(e, dict):
            continue
        title = re.sub(r"\s+", " ", str(e.get("title") or "")).strip()[:60]
        if not title:
            continue
        base = by_id.get(e.get("id")) if isinstance(e.get("id"), int) else None
        focus = re.sub(r"\s+", " ", str(e.get("focus") or (base or {}).get("focus") or "")).strip()[:200]
        if base:
            queries = list(base["queries"])
            if title != base["title"] or focus != base.get("focus"):
                extra = _english(f"{title} {focus}")
                if extra and extra not in queries:
                    queries.append(extra)
            papers = list(base["papers"])
        else:
            q = _english(f"{title} {focus}")
            queries = [q] if q else list(topic_queries[:1])
            papers = []
        out.append({"id": len(out), "title": title, "focus": focus, "queries": queries[:3], "papers": papers})
    if not out:
        return orig
    return {"title": re.sub(r"\s+", " ", str(edited.get("title") or orig["title"])).strip()[:80] or orig["title"], "sections": out}


def _strip_heading(text: str, title: str) -> str:
    lines = (text or "").strip().splitlines()
    while lines and (re.match(r"^\s*#{1,6}\s", lines[0]) or lines[0].strip().strip("*") == title):
        lines.pop(0)
    return "\n".join(lines).strip()


def _split_parts(text: str) -> tuple[str, str]:
    m = re.search(r"===\s*INTRO\s*===(.*?)===\s*OUTLOOK\s*===(.*)", text or "", re.S | re.I)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    paras = [p for p in (text or "").split("\n\n") if p.strip()]
    return (paras[0].strip(), "\n\n".join(paras[1:]).strip()) if paras else ("", "")


def _authors(s: dict) -> str:
    au = s.get("authors") or []
    if not au:
        return "Anonymous"
    return ", ".join(au[:3]) + (" et al." if max(len(au), s.get("n_authors") or 0) > 3 else "")


def _ref_line(r: dict) -> str:
    venue = r["venue"] or "arXiv"
    where = venue if re.search(r"\b\d{4}$", venue) else f"{venue}, {r['year']}"
    return f"[{r['n']}] {r['authors'].rstrip('.')}. {r['title'].rstrip('.')}. {where}." + (f" {r['url']}" if r["url"] else "")


def _url(s: dict) -> str:
    if is_arxiv_key(s["key"]):
        return f"https://arxiv.org/abs/{s['key']}"
    if s.get("doi"):
        return f"https://doi.org/{s['doi']}"
    return ""


def _bibtex(sources: list) -> str:
    """Same entry format as the site's BibTeX button (app.js bibtex())."""
    out, used = [], set()
    for s in sources:
        au = s.get("authors") or []
        first = re.sub(r"[^a-z]", "", (au[0].split()[-1] if au else "anon").lower()) or "anon"
        word = (re.search(r"[a-z]{4,}", s["title"].lower()) or [None])[0] or "paper"
        key = base = f"{first}{s['year']}{word}"
        i = 0
        while key in used:
            i += 1
            key = base + chr(ord("a") + i - 1) if i <= 26 else f"{base}{i}"
        used.add(key)
        authors = " and ".join(au) + (" and others" if (s.get("n_authors") or 0) > len(au) and au else "")
        lines = [f"  title = {{{s['title']}}}"]
        if authors:
            lines.append(f"  author = {{{authors}}}")
        lines.append(f"  year = {{{s['year']}}}")
        if s.get("venue"):
            venue = re.sub(r" \d{4}$", "", s["venue"])
            lines.append(f"  booktitle = {{{venue}}}")
        if is_arxiv_key(s["key"]):
            lines.append(f"  eprint = {{{s['key']}}},\n  archivePrefix = {{arXiv}}")
        if s.get("doi"):
            lines.append(f"  doi = {{{s['doi']}}}")
        out.append(f"@{'inproceedings' if s.get('venue') else 'misc'}{{{key},\n" + ",\n".join(lines) + "\n}")
    return "\n\n".join(out) + ("\n" if out else "")


# ---- running ------------------------------------------------------------------------
def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": 40, "max_concurrency": 5,
            "run_name": "literature-review", "metadata": {"thread_id": thread_id}}


def _drive(graph, inp, config):
    for update in graph.stream(inp, config, stream_mode="updates"):
        for node, delta in update.items():
            if node == "__interrupt__":
                yield "outline", (delta[0].value if delta else {})
                return
            for st in (delta or {}).get("steps") or []:
                yield "step", st
    yield "done", graph.get_state(config).values


def start(llm: LLM, topic: str, thread_id: str, year_from: Optional[int] = None, year_to: Optional[int] = None,
          confirm: bool = True, checkpointer=None):
    """Starts a review. Yields ("step", {node, text}) while working, then either
    ("outline", {...}) when it pauses for the user, or ("done", state)."""
    graph = build_graph(llm, checkpointer if checkpointer is not None else memory())
    inp = {"topic": topic, "year_from": int(year_from or 0), "year_to": int(year_to or 0), "confirm": bool(confirm)}
    yield from _drive(graph, inp, _config(thread_id))


def resume(llm: LLM, thread_id: str, outline: Optional[dict], checkpointer=None):
    """Continues a review paused at the outline with the user's edited outline."""
    graph = build_graph(llm, checkpointer if checkpointer is not None else memory())
    config = _config(thread_id)
    if not graph.get_state(config).next:
        raise RuntimeError("找不到这个待确认的综述（可能已经完成或已过期），请重新开始")
    yield from _drive(graph, Command(resume=outline or {}), config)


def forget(thread_id: str) -> None:
    try:
        memory().delete_thread(thread_id)
    except Exception:  # noqa: BLE001
        pass
