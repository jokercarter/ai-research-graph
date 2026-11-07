"""Research Q&A agent (LangGraph) over the site's own paper index.

    plan ──► gather ──► review ──► select ──► answer ──► check
                          ▲  │
                          └─ act  (at most MAX_ROUNDS extra tool rounds)

- plan    (fast model)  splits the question into 1-3 sub-questions with English search
                        queries, and resolves references to earlier answers ("第 2 篇")
- gather  (tools)       find_evidence for every sub-question; get_paper / related_papers
                        for papers the question is about
- review  (fast model)  looks at what was found and either stops or asks for up to 3
                        more tool calls (filtered search, related papers, a direction...)
- act     (tools)       runs those calls
- select                picks ~20 diverse sources across sub-questions (8 with excerpts)
- answer  (main model)  writes the cited answer (same prompt as the normal Q&A)
- check                 drops citation numbers that point to no source, saves the turn

Memory: the graph state is checkpointed per conversation (thread_id), so follow-up
questions see earlier questions, answers and their sources (SQLite under data/work/).

The model is injected: `llm(system, messages, tier, max_tokens) -> str`, where tier is
"fast" (planning/review) or "main" (the answer). serve_local.py passes the logged-in
Claude Code (subscription, no API key) or an Anthropic API key. LangSmith tracing turns
on by itself when LANGSMITH_TRACING=true and LANGSMITH_API_KEY are set.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from pathlib import Path
from typing import Callable, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from . import tools
from .search import default_index, excerpt, has_cjk, local_rewrite, tokens

ROOT = Path(__file__).resolve().parents[1]
MEMORY_DB = ROOT / "data" / "work" / "agent_memory.sqlite"
MAX_ROUNDS = 2
MAX_ACTIONS = 3
N_KEY, N_SOURCES = 8, 20
ACT_TOOLS = ("search_papers", "related_papers", "get_paper", "get_direction", "latest_in_category")

LLM = Callable[[str, list, str, int], str]


class AgentState(TypedDict, total=False):
    # input of one turn
    question: str
    pinned: Optional[str]
    client_history: list       # [{role, content}] from the browser, used when memory is empty
    # kept across turns (checkpointed per conversation)
    turns: list                # [{q, answer, sources:[{n,key,title}]}]
    # working state of the current turn
    language: str
    goal: str
    subqs: list                # [{q, queries}]
    focus: list                # paper ids the question is about
    evidence: list             # per sub-question: retrieve() result
    extra: list                # [{tool, args, papers:[card]}] from review rounds
    focus_info: list           # get_paper results
    steps: list                # [{node, text}] shown in the UI
    rounds: int
    review_note: str
    pending: list              # tool calls review asks act to run
    sources: list
    overview: str
    dirs: list
    matched: int
    answer: str


def _json(text: str) -> dict:
    try:
        return json.loads(text[text.index("{"): text.rindex("}") + 1])
    except (ValueError, json.JSONDecodeError):
        return {}


def _clean_query(q) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s\-]", " ", str(q or ""))).strip()[:120]


def _brief(answer: str, n: int = 500) -> str:
    a = re.sub(r"\[\d+(?:[,，、]\s*\d+)*\]", "", answer or "")
    return re.split(r"\n\s*延伸问题", a)[0].strip()[:n]


def _history_msgs(state: AgentState) -> list:
    turns = state.get("turns") or []
    if turns:
        msgs = []
        for t in turns[-2:]:
            msgs += [{"role": "user", "content": t["q"]}, {"role": "assistant", "content": _brief(t.get("answer", ""))}]
        return [m for m in msgs if m["content"]]
    return [m for m in (state.get("client_history") or [])[-4:] if m.get("content")]


def _step(state: AgentState, node: str, text: str) -> list:
    return [*(state.get("steps") or []), {"node": node, "text": text}]


PLAN_SYSTEM = """You plan literature searches for researchers over an index of ~550k AI/CS papers (2021-12 to now; arXiv plus top venues). The index matches English words in titles and abstracts, plus abstract meaning.
Given the conversation and the latest question, output strict JSON only:
{"language": "zh" | "en",
 "goal": "<one line: what the user wants to know>",
 "sub_questions": [{"q": "<sub-question>", "queries": ["<1-3 English keyword queries, 3-8 words, technical terms papers would use>"]}],
 "papers": ["<ids of earlier sources the question refers to, e.g. '第2篇' -> its id>"]}
Use 1 sub-question for a focused question, 2-3 when it has distinct parts (e.g. compare A vs B, methods + benchmarks). Queries in different sub-questions must not repeat each other."""

REVIEW_SYSTEM = """You check whether the papers found so far are enough to answer a research question well, and if not, request more searches. Output strict JSON only:
{"enough": true | false, "missing": "<what is missing, one line>", "actions": [{"tool": "<name>", "args": {...}}]}
Request at most 3 actions, only if they would clearly add relevant papers (a missing sub-topic, recent work, a specific method or benchmark, papers related to a key paper). Tools:
- search_papers {query (English keywords), year_from?, year_to?, category? (e.g. cs.CV), published_only?, sort? ("relevance"|"recent"), limit?}
- related_papers {paper_id, limit?}
- get_paper {paper_id}
- get_direction {direction_id}
- latest_in_category {category, limit?}
If the found papers already cover the question, return {"enough": true, "missing": "", "actions": []}."""


def build_graph(llm: LLM, checkpointer=None):
    ix = default_index()
    try:  # each model call becomes a child run in LangSmith (no-op unless tracing is on)
        from langsmith import traceable
        llm = traceable(run_type="llm", name="claude")(llm)
    except ImportError:
        pass

    def plan(state: AgentState):
        q = state["question"]
        turns = state.get("turns") or []
        ctx = ""
        if turns:
            last = turns[-1]
            ctx = "Earlier questions: " + " | ".join(t["q"] for t in turns[-3:]) + "\nSources of the last answer:\n" + "\n".join(
                f"[{s['n']}] {s['key']} — {s['title'][:90]}" for s in last.get("sources", [])[:20])
        msgs = _history_msgs(state) + [{"role": "user", "content": (ctx + "\n\nLatest question: " if ctx else "") + q}]
        j = {}
        try:
            j = _json(llm(PLAN_SYSTEM, msgs, "fast", 500))
        except Exception as e:  # noqa: BLE001  (planning is optional: fall back to the question itself)
            j = {"_error": str(e)[:200]}
        subqs = []
        for s in (j.get("sub_questions") or [])[:3]:
            if isinstance(s, dict):
                qs = [x for x in (_clean_query(x) for x in (s.get("queries") or [])[:3]) if x]
                if qs:
                    subqs.append({"q": str(s.get("q") or q)[:200], "queries": qs})
        if not subqs:
            subqs = [{"q": q, "queries": [_clean_query(local_rewrite(q) if has_cjk(q) else q) or q]}]
        known = {s["key"] for t in turns for s in t.get("sources", [])}
        focus = [str(p) for p in (j.get("papers") or []) if str(p) in known][:3]
        if state.get("pinned"):
            focus = [state["pinned"], *[p for p in focus if p != state["pinned"]]]
        lang = j.get("language") if j.get("language") in ("zh", "en") else ("zh" if has_cjk(q) else "en")
        text = "拆成 {} 个子问题：{}".format(len(subqs), "；".join(f"{s['q']}（检索：{' / '.join(s['queries'])}）" for s in subqs))
        if focus:
            text += f"；涉及论文 {', '.join(focus)}"
        return {"language": lang, "goal": str(j.get("goal") or q)[:200], "subqs": subqs, "focus": focus,
                "evidence": [], "extra": [], "focus_info": [], "rounds": 0, "review_note": "", "pending": [], "sources": [], "matched": 0, "answer": "",
                "steps": [{"node": "plan", "text": text}]}

    def gather(state: AgentState):
        evidence = []
        for s in state["subqs"]:
            r = tools.find_evidence(question=s["q"], queries=s["queries"])
            evidence.append({"q": s["q"], "queries": s["queries"], "sources": r["sources"], "overview": r["overview"], "dirs": r["dirs"], "matched": r["matched"]})
        focus_info, extra = [], []
        for pid in state.get("focus") or []:
            p = tools.get_paper(pid)
            if "error" not in p:
                focus_info.append(p)
                rel = tools.related_papers(pid, limit=6)
                extra.append({"tool": "related_papers", "args": {"paper_id": pid}, "papers": rel.get("related", [])})
        dirs = {d["name"] for e in evidence for d in e["dirs"]}
        text = f"在全部 {ix.manifest['papers']:,} 篇论文中检索，找到 {sum(len(e['sources']) for e in evidence)} 篇候选" + (f"，定位到方向：{'、'.join(dirs)}" if dirs else "")
        if focus_info:
            text += f"；读取了 {len(focus_info)} 篇指定论文及其相关工作"
        return {"evidence": evidence, "focus_info": focus_info, "extra": extra, "steps": _step(state, "gather", text)}

    def review(state: AgentState):
        if state.get("rounds", 0) >= MAX_ROUNDS:
            return {"review_note": "", "steps": _step(state, "review", "已达到补充检索上限，开始整理资料")}
        lines = [f"Question: {state['question']}", f"Goal: {state.get('goal', '')}"]
        for i, e in enumerate(state["evidence"]):
            lines.append(f"\nSub-question {i + 1}: {e['q']} (searched: {' / '.join(e['queries'])}; directions: {', '.join(d['name'] for d in e['dirs'])})")
            lines += [f"- [{s['key']}] {s['title'][:100]} ({s['date']})" for s in e["sources"][:12]]
        for x in state.get("extra") or []:
            lines.append(f"\nFrom {x['tool']} {json.dumps(x['args'], ensure_ascii=False)}:")
            lines += [f"- [{p['id']}] {p['title'][:100]} ({p['date']})" for p in x["papers"][:6]]
        dir_ids = sorted({d["id"] for e in state["evidence"] for d in e["dirs"]})
        lines.append(f"\nResearch direction ids found: {dir_ids}. Extra search rounds left: {MAX_ROUNDS - state.get('rounds', 0)}.")
        try:
            j = _json(llm(REVIEW_SYSTEM, [{"role": "user", "content": "\n".join(lines)}], "fast", 400))
        except Exception:  # noqa: BLE001
            j = {"enough": True}
        actions = []
        done = {json.dumps([x["tool"], {k: v for k, v in x["args"].items() if k != "limit"}], sort_keys=True) for x in state.get("extra") or []}
        if not j.get("enough", True):
            for a in (j.get("actions") or [])[:MAX_ACTIONS]:
                if isinstance(a, dict) and a.get("tool") in ACT_TOOLS and isinstance(a.get("args"), dict):
                    sig = json.dumps([a["tool"], {k: v for k, v in a["args"].items() if k != "limit"}], sort_keys=True)
                    if sig not in done:  # never repeat a call that already ran
                        done.add(sig)
                        actions.append({"tool": a["tool"], "args": a["args"]})
        if not actions:
            return {"review_note": "", "steps": _step(state, "review", "检查资料：已覆盖问题，开始整理")}
        note = str(j.get("missing") or "")[:200]
        desc = "；".join(f"{a['tool']}({', '.join(f'{k}={v}' for k, v in a['args'].items())})" for a in actions)
        return {"review_note": note, "pending": actions, "steps": _step(state, "review", f"检查资料：{note or '还不够全面'} → 补充检索：{desc}")}

    def act(state: AgentState):
        extra = list(state.get("extra") or [])
        found = 0
        for a in state.get("pending") or []:
            args = dict(a["args"])
            if a["tool"] in ("search_papers", "latest_in_category", "related_papers"):
                args["limit"] = min(int(args.get("limit") or 8), 10)
            r = tools.call_tool(a["tool"], args)
            if "error" in r:
                papers = []
            elif a["tool"] == "get_paper":
                papers = [{"id": r["id"], "title": r["title"], "date": r["date"], "venue": r["venue"]}]
            elif a["tool"] == "get_direction":
                papers = [{"id": p["id"], "title": p["title"], "date": p["date"]} for p in (r.get("latest") or [])[:4] + (r.get("representative") or [])[:4]]
            else:
                papers = r.get("papers") or r.get("related") or []
            found += len(papers)
            extra.append({"tool": a["tool"], "args": args, "papers": papers[:10]})
        return {"extra": extra, "pending": [], "rounds": state.get("rounds", 0) + 1,
                "steps": _step(state, "act", f"补充检索得到 {found} 篇论文")}

    def route_review(state: AgentState):
        return "act" if state.get("pending") else "select"

    def select(state: AgentState):
        chosen: list[str] = []
        seen = set()

        def add(key):
            if key and key not in seen and len(chosen) < N_SOURCES:
                seen.add(key)
                chosen.append(key)
        for p in state.get("focus_info") or []:
            add(p["id"])
        # Round-robin over sub-questions keeps every part of the question represented.
        lists = [[s["key"] for s in e["sources"]] for e in state["evidence"]]
        extra_lists = [[p["id"] for p in x["papers"][:4]] for x in state.get("extra") or []]
        for i in range(max([len(x) for x in lists] + [0])):
            for lst in lists:
                if i < len(lst):
                    add(lst[i])
            if i == 2:  # review results go in early, after the top papers of each sub-question
                for lst in extra_lists:
                    for k in lst:
                        add(k)
        for lst in extra_lists:
            for k in lst:
                add(k)
        terms = list(dict.fromkeys(t for s in state["subqs"] for q in s["queries"] for t in tokens(q)))
        pinned = set(state.get("focus") or [])
        sources = []
        for i, key in enumerate(chosen):
            sid = ix.sid_of_key(key)
            if sid is None:
                continue
            n = len(sources) + 1
            if len(sources) < N_KEY:
                d = ix.detail(sid)
                sources.append({"n": n, "key": d["ax"], "title": d["t"], "venue": d["vn"], "date": f"{d['vn']}, {d['d']}" if d["vn"] else d["d"],
                                "authors": ", ".join(d["au"][:3]) + (" et al." if d["na"] > 3 else ""),
                                "abstract": (" ".join(d["ab"].split())[:1300] if key in pinned else excerpt(d["ab"], terms, 460)), "cat": d["cat"][0]})
            else:
                p = ix.lite(sid)
                sources.append({"n": n, "key": p["key"], "title": p["title"], "venue": p["venue"], "date": f"{p['venue']}, {p['date']}" if p["venue"] else p["date"],
                                "authors": p["authors"], "abstract": "", "cat": p["cat"]})
        dirs, seen_d = [], set()
        for e in state["evidence"]:
            for d in e["dirs"]:
                if d["id"] not in seen_d:
                    seen_d.add(d["id"])
                    dirs.append(d)
        parts = []
        if len(state["subqs"]) > 1:
            parts.append("Sub-questions investigated: " + " | ".join(s["q"] for s in state["subqs"]) + ".")
        parts.append(state["evidence"][0]["overview"] if state["evidence"] else "")
        if state.get("review_note"):
            parts.append(f"Extra searches were run for: {state['review_note']}.")
        overview = " ".join(p for p in parts if p)
        matched = max([e["matched"] for e in state["evidence"]] + [0])
        return {"sources": sources, "dirs": dirs, "overview": overview, "matched": matched,
                "steps": _step(state, "select", f"从候选中选出 {len(sources)} 篇（前 {min(N_KEY, len(sources))} 篇附摘要），兼顾每个子问题")}

    def answer(state: AgentState):
        if not state.get("sources"):
            raise RuntimeError("没有检索到相关论文，换个说法试试")
        system = ANSWER_SYSTEM["fn"](state["sources"], state.get("language", "zh"), state.get("overview", ""))
        text = llm(system, _history_msgs(state) + [{"role": "user", "content": state["question"]}], "main", 1800)
        return {"answer": text, "steps": _step(state, "answer", "已撰写回答")}

    def check(state: AgentState):
        n = len(state["sources"])
        bad = set()

        def fix(m):
            nums = [x for x in re.split(r"\s*[,，、]\s*", m.group(1))]
            ok = [x for x in nums if 1 <= int(x) <= n]
            bad.update(x for x in nums if x not in ok)
            return f"[{', '.join(ok)}]" if ok else ""
        text = re.sub(r"\[(\d+(?:\s*[,，、]\s*\d+)*)\]", fix, state.get("answer", ""))
        cited = {int(x) for m in re.finditer(r"\[(\d+(?:\s*,\s*\d+)*)\]", text) for x in re.split(r"\s*,\s*", m.group(1))}
        note = f"核对引用：引用了 {len(cited)} 篇" + (f"，删除了 {len(bad)} 个无效编号" if bad else "，全部有效")
        turn = {"q": state["question"], "answer": text, "sources": [{"n": s["n"], "key": s["key"], "title": s["title"]} for s in state["sources"]]}
        return {"answer": text, "turns": [*(state.get("turns") or []), turn][-12:], "steps": _step(state, "check", note)}

    g = StateGraph(AgentState)
    for name, fn in (("plan", plan), ("gather", gather), ("review", review), ("act", act), ("select", select), ("answer", answer), ("check", check)):
        g.add_node(name, fn)
    g.add_edge(START, "plan")
    g.add_edge("plan", "gather")
    g.add_edge("gather", "review")
    g.add_conditional_edges("review", route_review, {"act": "act", "select": "select"})
    g.add_edge("act", "review")
    g.add_edge("select", "answer")
    g.add_edge("answer", "check")
    g.add_edge("check", END)
    return g.compile(checkpointer=checkpointer)


# The answer prompt is the one the normal Q&A uses; serve_local.py sets it.
ANSWER_SYSTEM: dict[str, Callable] = {"fn": lambda sources, lang, overview: ""}

_SAVER = {"saver": None}
_SAVER_LOCK = threading.Lock()


def memory():
    """Conversation memory shared by all requests (SQLite file under data/work/)."""
    with _SAVER_LOCK:
        if _SAVER["saver"] is None:
            from langgraph.checkpoint.sqlite import SqliteSaver
            MEMORY_DB.parent.mkdir(parents=True, exist_ok=True)
            _SAVER["saver"] = SqliteSaver(sqlite3.connect(str(MEMORY_DB), check_same_thread=False))
        return _SAVER["saver"]


def run(llm: LLM, question: str, thread_id: str, pinned: Optional[str] = None, client_history: Optional[list] = None, checkpointer=None):
    """Runs one turn; yields ("step", {node, text}) while working and finally ("done", state)."""
    graph = build_graph(llm, checkpointer if checkpointer is not None else memory())
    config = {"configurable": {"thread_id": thread_id or "default"}, "recursion_limit": 30,
              "run_name": "research-agent", "metadata": {"thread_id": thread_id}}
    inp = {"question": question, "pinned": pinned, "client_history": client_history or []}
    shown = 0
    for update in graph.stream(inp, config, stream_mode="updates"):
        for node, delta in update.items():
            steps = (delta or {}).get("steps")
            if steps is None:
                continue
            if node == "plan":
                shown = 0  # a new turn starts its own step list
            for st in steps[shown:]:
                yield "step", st
            shown = len(steps)
    yield "done", graph.get_state(config).values


def forget(thread_id: str) -> None:
    """Deletes the stored memory of one conversation."""
    try:
        memory().delete_thread(thread_id)
    except Exception:  # noqa: BLE001
        pass
