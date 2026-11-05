#!/usr/bin/env python3
"""Local preview of the website with working AI endpoints (no build step).

  set ANTHROPIC_API_KEY=...   (and/or OPENAI_API_KEY=...)
  python serve_local.py        -> http://localhost:4173/

Keys can also be put in a `.env.local` file next to this script (KEY=value lines;
it is git-ignored via `.env*`). AI_PROVIDER=mock answers without calling any API,
for trying the interface. Mirrors app/api/ask, app/api/query-rewrite and
app/api/ai-search closely enough for local testing; production uses the Worker routes.

Subscription mode (no API key, for your own local use only): if the Claude Code CLI
(`claude`, logged in with a Claude Pro/Max plan) or the Codex CLI (`codex`, logged in
with a ChatGPT plan) is installed, they are offered as models too. Set
AI_PROVIDER=claude-cli or AI_PROVIDER=codex-cli to make one the default.
A public deployment cannot use these: it needs ANTHROPIC_API_KEY / OPENAI_API_KEY.
"""
from __future__ import annotations

import http.server
import json
import os
import re
import shutil
import socketserver
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SITE = ROOT / "public" / "research"
PORT = int(os.environ.get("PORT", "4173"))

env_file = ROOT / ".env.local"
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"'))


def find_claude():
    path = shutil.which("claude")
    if path:
        return path
    for cand in (Path.home() / ".local" / "bin" / "claude.exe", Path.home() / ".local" / "bin" / "claude",
                 Path(os.environ.get("APPDATA", "")) / "npm" / "claude.cmd"):
        if cand.is_file():
            return str(cand)
    return None


CLAUDE_CLI = find_claude()
CODEX_CLI = shutil.which("codex")
LOGIN_STATE = {"logged_in": False}
CLI_IDS = ("claude-cli", "codex-cli")


def providers():
    out = []
    if os.environ.get("AI_PROVIDER") == "mock":
        return [{"id": "mock", "label": "Mock", "model": "offline-demo"}]
    global CLAUDE_CLI
    CLAUDE_CLI = CLAUDE_CLI or find_claude()
    if CLAUDE_CLI:
        out.append({"id": "claude-cli", "label": "Claude（邮箱登录）", "model": "Claude Code"})

    if os.environ.get("ANTHROPIC_API_KEY"):
        out.append({"id": "anthropic", "label": "Claude", "model": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")})
    # Claude only: OpenAI / Codex are intentionally not offered.
    pref = os.environ.get("AI_PROVIDER")
    out.sort(key=lambda p: 0 if p["id"] == pref else 1)
    return out


REQ_MODEL = {"api": None}


def call(provider: str, system: str, messages: list, max_tokens: int, stream: bool):
    if provider == "anthropic":
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", method="POST",
            headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01", "content-type": "application/json"},
            data=json.dumps({"model": REQ_MODEL["api"] or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5"), "max_tokens": max_tokens, "system": system, "messages": messages, "stream": stream}).encode())
    else:
        req = urllib.request.Request("https://api.openai.com/v1/responses", method="POST",
            headers={"authorization": "Bearer " + os.environ["OPENAI_API_KEY"], "content-type": "application/json"},
            data=json.dumps({"model": os.environ.get("OPENAI_MODEL", "gpt-4.1-mini"), "instructions": system, "input": messages, "max_output_tokens": max_tokens, "stream": stream}).encode())
    return urllib.request.urlopen(req, timeout=120)


def cli_prompt(system, messages):
    convo = "\n\n".join(("用户：" if m["role"] == "user" else "助手：") + m["content"] for m in messages)
    return (f"{system}\n\n=== 对话 ===\n{convo}\n\n"
            "只输出给用户的最终回答正文。不要调用任何工具，不要读写文件，不要联网搜索。")


CLAUDE_MODELS = {"sonnet", "opus", "haiku", "default"}
LAST_MODEL = {"name": ""}
LAST_USAGE = {"input": 0, "output": 0}
# Claude Code normally sends its own long system prompt and tool definitions with every
# request (tens of thousands of tokens). For Q&A we replace the system prompt and turn
# all tools off, so a question costs only the papers and the answer. Older CLI versions
# without these options fall back to the plain call.
LEAN = {"ok": True}


def run_cli(provider, system, messages, timeout=240, model=None) -> str:
    """Ask the locally logged-in Claude Code (subscription) once; returns the reply text."""
    with tempfile.TemporaryDirectory() as tmp:  # empty working dir: the CLI sees no project files
        base = [CLAUDE_CLI, "-p", "--output-format", "json"]
        if model in CLAUDE_MODELS and model != "default":
            base += ["--model", model]
        r = None
        if LEAN["ok"]:
            convo = "\n\n".join(("User: " if m["role"] == "user" else "Assistant: ") + m["content"] for m in messages)
            sys_file = Path(tmp) / "system.txt"; sys_file.write_text(system, encoding="utf-8")
            cmd = base + ["--system-prompt-file", str(sys_file), "--tools", "", "--strict-mcp-config"]
            r = subprocess.run(cmd, input=convo, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=tmp, timeout=timeout)
            if r.returncode != 0 or '"result"' not in (r.stdout or ""):
                r = None  # this CLI version does not take the lean options: use the plain call
        if r is None:
            r = subprocess.run(base, input=cli_prompt(system, messages), capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=tmp, timeout=timeout)
            if r.returncode == 0 and LEAN["ok"]:
                LEAN["ok"] = False
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        data = {}
    text = (data.get("result") or "").strip() if isinstance(data, dict) else ""
    if r.returncode != 0 or not text or data.get("is_error"):
        raise RuntimeError("Claude Code 调用失败（确认已用 Claude 账号登录；选 Opus 时也可能是订阅不支持或额度用完）：" + (text or r.stderr or r.stdout)[-300:])
    u = data.get("usage") or {}
    LAST_USAGE["input"] = int(u.get("input_tokens", 0)) + int(u.get("cache_read_input_tokens", 0)) + int(u.get("cache_creation_input_tokens", 0))
    LAST_USAGE["output"] = int(u.get("output_tokens", 0))
    usage = data.get("modelUsage") or {}
    LAST_MODEL["name"] = max(usage, key=lambda m: (usage[m] or {}).get("outputTokens", 0)) if usage else (model or "Claude Code")
    return text


def complete(provider, system, messages, max_tokens=600, model=None) -> str:
    if provider in CLI_IDS:
        return run_cli(provider, system, messages, timeout=120, model=model)
    with call(provider, system, messages, max_tokens, False) as r:
        data = json.loads(r.read())
    if provider == "anthropic":
        return "".join(c.get("text", "") for c in data.get("content", []) if c.get("type") == "text")
    if isinstance(data.get("output_text"), str):
        return data["output_text"]
    return "".join(c.get("text", "") for item in data.get("output", []) for c in item.get("content", []) or [] if isinstance(c.get("text"), str))


def stream_text(provider, system, messages, max_tokens=1800, model=None):
    if provider in CLI_IDS:
        yield run_cli(provider, system, messages, model=model)
        return
    with call(provider, system, messages, max_tokens, True) as r:
        for raw in r:
            line = raw.decode("utf-8", "ignore").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                ev = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if provider == "anthropic" and ev.get("type") == "content_block_delta" and ev.get("delta", {}).get("type") == "text_delta":
                yield ev["delta"]["text"]
            elif provider == "openai" and ev.get("type") == "response.output_text.delta":
                yield ev.get("delta", "")


# Prompts are kept identical to app/api/ask/route.ts.
PLAN_SYSTEM = re.search(r"const PLAN_SYSTEM = `(.*?)`;", (ROOT / "app/api/ask/route.ts").read_text(encoding="utf-8"), re.S).group(1)
ANSWER_TEMPLATE = re.search(r"return `(You are a research assistant.*?)`;", (ROOT / "app/api/ask/route.ts").read_text(encoding="utf-8"), re.S).group(1)


REWRITE_FILE = ROOT / "data" / "work" / "rewrite_cache.json"
try:
    REWRITE_CACHE = json.loads(REWRITE_FILE.read_text(encoding="utf-8"))
except Exception:  # noqa: BLE001
    REWRITE_CACHE = {}


def save_rewrite_cache():
    try:
        REWRITE_FILE.parent.mkdir(parents=True, exist_ok=True)
        REWRITE_FILE.write_text(json.dumps(REWRITE_CACHE, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


# Search-page AI assistant (same text as assistPrompt in public/research/app.js).
ASSIST_SYSTEM = "You are a literature assistant on an AI/CS paper search site. Use only the candidate papers given. Output strict JSON only."
ASSIST_MODES = {
    "review": "梳理综述：按研究主题、方法和时间阶段组织这些论文，形成文献综述提纲。",
    "related": "找相关论文：挑出与问题最相关的论文，并说明各自相关在哪里。",
    "compare": "比较方法：选 2–5 篇方法差异明显的论文，比较思路、数据、优点和局限；摘要里没有的写“摘要未提及”。",
    "gaps": "找研究空白：根据这些论文覆盖和没有覆盖的内容，提出值得核实的研究空白，不要把推测写成事实。",
    "frontier": "找前沿：关注最近的论文，说明哪些做法是新出现或正在升温的。",
}


def assist_prompt(query, mode, cands):
    lst = "\n".join(f"[{c['id']}] {c.get('title', '')} ({c.get('year', '')}{', ' + c['venue'] if c.get('venue') and c['venue'] != 'arXiv' else ''})\n{str(c.get('abstract', ''))[:260]}" for c in cands)
    return (f"任务：{ASSIST_MODES.get(mode, ASSIST_MODES['review'])}\n用户的问题：{query}\n"
            "要求：只依据下面的候选论文（摘要片段）；用简体中文，论文标题和术语保留英文；每条要点附支撑它的论文 ID。\n"
            '只输出 JSON：{"answer": {"summary": "<2-3 句总结>", "key_points": [{"text": "<要点>", "evidence_ids": ["<ID>"]}], "evidence_ids": ["<ID>"]}, "view": {"gaps": ["<研究空白，仅 gaps 模式需要>"]}}\n'
            f"候选论文：\n{lst}")


def answer_system(sources, lang, overview="(none)"):
    key = [s for s in sources if s.get("abstract")]
    more = [s for s in sources if not s.get("abstract")]
    lst = "\n\n".join(f"[{s['n']}] {s.get('title', '')} ({s.get('date', '?')})\n{str(s['abstract'])[:600]}" for s in key)
    mr = "\n".join(f"[{s['n']}] {s.get('title', '')} ({s.get('date', '?')})" for s in more) or "(none)"
    return (ANSWER_TEMPLATE.replace('${lang === "en" ? "English" : "Simplified Chinese"}', "English" if lang == "en" else "Simplified Chinese")
            .replace("${list}", lst).replace("${more}", mr).replace("${overview}", str(overview or "(none)")[:900]))


def mock_answer(question, sources):
    titles = "\n".join(f"- {s['title']} [{i + 1}]" for i, s in enumerate(sources[:4]))
    text = (f"**（演示模式，没有调用真实模型）** 关于「{question}」，检索到的论文主要集中在以下工作 [1][2]：\n\n{titles}\n\n"
            "## 主要方向\n- 第一类方法根据摘要侧重于训练策略的改进 [1]。\n- 第二类工作关注评测与基准 [3]。\n\n延伸问题：这些方法的计算成本如何？｜有哪些公开数据集？｜最新的改进方向是什么？")
    for i in range(0, len(text), 12):
        time.sleep(0.02)
        yield text[i:i + 12]


class QueryEmbedder:
    """Keeps data/pipeline/query_embed.py running so search queries can be turned into
    meaning vectors (semantic search). Optional: without it the site falls back to
    finding similar papers from the keyword results."""

    def __init__(self):
        import threading
        self.lock = threading.Lock(); self.proc = None; self.failed = None; self.cache = {}

    def python(self):
        for c in (ROOT / "data/work/venv-gpu/Scripts/python.exe", ROOT / "data/work/venv-gpu/bin/python"):
            if c.exists():
                return str(c)
        return sys.executable

    def start(self):
        if self.proc and self.proc.poll() is None:
            return True
        if self.failed:
            return False
        if not (SITE / "data/v/pca.bin.gz").exists():
            self.failed = "没有向量索引"; return False
        try:
            self.proc = subprocess.Popen([self.python(), str(ROOT / "data/pipeline/query_embed.py")], stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1)
            first = json.loads(self.proc.stdout.readline() or "{}")
            if not first.get("ready"):
                raise RuntimeError("embedder did not start")
            return True
        except Exception as e:  # noqa: BLE001
            self.failed = str(e)[:200]; self.proc = None
            return False

    def warm(self):
        with self.lock:
            self.start()

    def embed(self, q):
        q = q.strip()[:500]
        if q in self.cache:
            return self.cache[q]
        with self.lock:
            if not self.start():
                return None
            try:
                self.proc.stdin.write(json.dumps({"q": q}, ensure_ascii=False) + "\n"); self.proc.stdin.flush()
                out = json.loads(self.proc.stdout.readline() or "{}")
            except Exception:  # noqa: BLE001
                self.proc = None
                return None
        v = out.get("v")
        if v:
            if len(self.cache) > 500:
                self.cache.clear()
            self.cache[q] = v
        return v


EMBEDDER = QueryEmbedder()


class Handler(http.server.SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(SITE), **k)

    def log_message(self, fmt, *args):
        if args and "/api/" in str(args[0]):
            sys.stderr.write("%s\n" % (fmt % args))

    def send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status); self.send_header("content-type", "application/json; charset=utf-8"); self.send_header("content-length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def local_only(self):
        # A custom header forces a CORS preflight, which this server never answers,
        # so other websites cannot trigger these local actions.
        if self.headers.get("x-airg") != "1":
            self.send_json({"error": "forbidden"}, 403)
            return False
        return True

    def claude_status(self, check):
        global CLAUDE_CLI
        CLAUDE_CLI = find_claude()
        info = {"local": True, "installed": bool(CLAUDE_CLI), "logged_in": LOGIN_STATE["logged_in"], "platform": sys.platform}
        if CLAUDE_CLI and check and not LOGIN_STATE["logged_in"]:
            with tempfile.TemporaryDirectory() as tmp:
                try:
                    r = subprocess.run([CLAUDE_CLI, "-p", "--output-format", "text"], input="Reply with exactly: OK", capture_output=True,
                                       text=True, encoding="utf-8", errors="replace", cwd=tmp, timeout=90)
                    LOGIN_STATE["logged_in"] = r.returncode == 0 and bool(r.stdout.strip())
                    if not LOGIN_STATE["logged_in"]:
                        info["detail"] = (r.stderr or r.stdout)[-200:]
                except subprocess.TimeoutExpired:
                    info["detail"] = "检查超时"
            info["logged_in"] = LOGIN_STATE["logged_in"]
        return info

    def claude_login(self):
        LOGIN_STATE["logged_in"] = False
        path = find_claude()
        if sys.platform.startswith("win"):
            if path:
                subprocess.Popen(["cmd", "/c", "start", "Claude 登录", "cmd", "/k", path])
            else:
                script = ("Write-Host '正在安装 Claude Code…'; irm https://claude.ai/install.ps1 | iex; "
                          "Write-Host '安装完成，正在启动登录…'; & \"$env:USERPROFILE\\.local\\bin\\claude.exe\"")
                import base64  # -EncodedCommand avoids cmd.exe quoting problems with | and &
                encoded = base64.b64encode(script.encode("utf-16-le")).decode()
                subprocess.Popen(["cmd", "/c", "start", "安装并登录 Claude Code", "powershell", "-NoExit", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded])
            return {"started": True, "installing": not path}
        if sys.platform == "darwin":
            cmd = path or "curl -fsSL https://claude.ai/install.sh | bash && ~/.local/bin/claude"
            subprocess.Popen(["osascript", "-e", f'tell application "Terminal" to do script "{cmd}"'])
            return {"started": True, "installing": not path}
        return {"started": False, "manual": "请在终端运行: curl -fsSL https://claude.ai/install.sh | bash，然后运行 claude 登录"}

    def do_GET(self):
        if self.path.startswith("/api/claude/status"):
            if not self.local_only():
                return
            return self.send_json(self.claude_status("check=1" in self.path))
        if self.path.startswith("/api/ask"):
            p = providers()
            return self.send_json({"providers": p, "default": p[0]["id"] if p else None})
        return super().do_GET()

    def do_POST(self):
        length = int(self.headers.get("content-length") or 0)
        if self.path.startswith("/api/embed"):
            try:
                q = str(json.loads(self.rfile.read(length) or b"{}").get("q") or "")
            except json.JSONDecodeError:
                q = ""
            v = EMBEDDER.embed(q) if q.strip() else None
            return self.send_json({"v": v} if v else {"error": EMBEDDER.failed or "embedder unavailable"}, 200 if v else 503)
        if self.path.startswith("/api/claude/login"):
            self.rfile.read(length)
            if not self.local_only():
                return
            return self.send_json(self.claude_login())
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self.send_json({"error": "请求格式无效"}, 400)
        plist = providers()
        if not plist:
            return self.send_json({"error": "没有配置 ANTHROPIC_API_KEY 或 OPENAI_API_KEY"}, 503)
        ids = [p["id"] for p in plist]
        provider = body.get("provider") if body.get("provider") in ids else ids[0]
        try:
            if self.path.startswith("/api/query-rewrite"):
                q = str(body.get("query", "")).strip()[:240]
                if provider == "mock":
                    return self.send_json({"error": "mock"}, 503)
                if q in REWRITE_CACHE:
                    return self.send_json(REWRITE_CACHE[q])
                # Only writes keywords, so the fastest model is enough (also with the subscription).
                text = complete(provider, "You convert research questions into English academic search keywords for an AI/CS paper index. Output strict JSON only.",
                                [{"role": "user", "content": f'Question: {q}\nReturn {{"english": "<6-14 English keywords/phrases as they appear in paper titles and abstracts, space separated, most important first>"}}. Use standard technical terms (e.g. 大模型 -> large language model, 去模糊 -> deblurring). No explanations.'}],
                                200, model="haiku" if provider in CLI_IDS else None)
                j = json.loads(text[text.index("{"): text.rindex("}") + 1])
                out = {"english": re.sub(r"[^\w\s\-]", " ", str(j.get("english", ""))).strip()[:200]}
                if out["english"]:
                    REWRITE_CACHE[q] = out; save_rewrite_cache()
                return self.send_json(out)
            if self.path.startswith("/api/ask"):
                q = str(body.get("question", ""))[:600]
                req_model = str(body.get("model") or "")
                cli_model = req_model if req_model in CLAUDE_MODELS else None
                REQ_MODEL["api"] = req_model if re.fullmatch(r"claude-[a-z0-9.\-]{3,40}", req_model) else None
                history = [m for m in body.get("history", [])[-4:] if isinstance(m, dict) and m.get("content")]
                history = [{"role": "assistant" if m.get("role") == "assistant" else "user", "content": str(m["content"])[:800]} for m in history]
                if body.get("phase") == "plan":
                    if provider in CLI_IDS and not re.search(r"[\u3400-\u9fff]", q):
                        return self.send_json({"queries": [q], "language": "en", "provider": provider})
                    if provider == "mock":
                        return self.send_json({"error": "mock"}, 502)
                    # Planning only writes search keywords, so the fastest model is enough.
                    text = complete(provider, PLAN_SYSTEM, history + [{"role": "user", "content": q}], 300, model="haiku" if provider in CLI_IDS else None)
                    j = json.loads(text[text.index("{"): text.rindex("}") + 1])
                    qs = [re.sub(r"[^\w\s\-]", " ", str(x)).strip() for x in j.get("queries", [])][:4]
                    return self.send_json({"queries": [x for x in qs if x], "language": "en" if j.get("language") == "en" else "zh", "provider": provider})
                sources = [s for s in body.get("sources", []) if isinstance(s, dict) and s.get("title")][:24]
                for n, src in enumerate(sources):
                    src["n"] = n + 1
                if not sources:
                    return self.send_json({"error": "没有检索到可供回答的论文"}, 400)
                gen = mock_answer(q, sources) if provider == "mock" else stream_text(provider, answer_system(sources, body.get("language", "zh"), body.get("overview")), history + [{"role": "user", "content": q}], model=cli_model)
                first = next(gen, "")  # surface upstream errors before headers are sent
                p = next(x for x in plist if x["id"] == provider)
                self.send_response(200)
                self.send_header("content-type", "text/plain; charset=utf-8"); used_model = LAST_MODEL["name"] if provider in CLI_IDS else (REQ_MODEL["api"] or p["model"])
                self.send_header("x-provider", provider)
                if provider in CLI_IDS and LAST_USAGE.get("input"):
                    self.send_header("x-usage", f"{LAST_USAGE['input']}/{LAST_USAGE['output']}"); self.send_header("x-model", re.sub(r"[^\w.\-]", "", used_model) or p["model"])
                self.send_header("transfer-encoding", "chunked"); self.end_headers()
                for chunk in [first, *gen]:
                    data = chunk.encode()
                    if data:
                        self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n"); self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")
                return
            if self.path.startswith("/api/ai-search"):
                if provider == "mock":
                    return self.send_json({"error": "mock"}, 503)
                q = str(body.get("query", "")).strip()[:240]
                cands = [c for c in body.get("candidates", []) if isinstance(c, dict) and c.get("id") and c.get("title")][:30]
                if not cands:
                    return self.send_json({"error": "没有可供 AI 分析的论文"}, 400)
                req_model = str(body.get("model") or "")
                cli_model = req_model if req_model in CLAUDE_MODELS else None
                REQ_MODEL["api"] = req_model if re.fullmatch(r"claude-[a-z0-9.\-]{3,40}", req_model) else None
                text = complete(provider, ASSIST_SYSTEM, [{"role": "user", "content": assist_prompt(q, str(body.get("mode") or "review"), cands)}], 1600, model=cli_model)
                j = json.loads(text[text.index("{"): text.rindex("}") + 1])
                ids = {str(c["id"]) for c in cands}
                keep = lambda xs: [str(x) for x in (xs or []) if str(x) in ids][:6]
                ans = j.get("answer") or {}
                return self.send_json({
                    "answer": {"summary": str(ans.get("summary", ""))[:800],
                               "key_points": [{"text": str(k.get("text", ""))[:300], "evidence_ids": keep(k.get("evidence_ids"))} for k in (ans.get("key_points") or [])[:8] if isinstance(k, dict) and k.get("text")],
                               "evidence_ids": keep(ans.get("evidence_ids"))},
                    "view": {"gaps": [str(g)[:240] for g in ((j.get("view") or {}).get("gaps") or [])[:6]]}})
            return self.send_json({"error": "not found"}, 404)
        except urllib.error.HTTPError as e:
            return self.send_json({"error": f"模型服务返回 {e.code}: {e.read()[:300].decode('utf-8', 'ignore')}"}, 502)
        except subprocess.TimeoutExpired:
            return self.send_json({"error": "模型响应超时，请稍后重试"}, 504)
        except Exception as e:  # noqa: BLE001
            return self.send_json({"error": f"本地服务出错：{e}"}, 502)


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


if __name__ == "__main__":
    import webbrowser
    open_path = sys.argv[sys.argv.index("--open") + 1] if "--open" in sys.argv else None
    server = None
    for port in range(PORT, PORT + 20):  # use the next free port if 4173 is taken
        try:
            server = Server(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    if server is None:
        sys.exit(f"端口 {PORT}-{PORT + 19} 都被占用了")
    url = f"http://localhost:{port}/"
    names = ", ".join(f"{p['label']} ({p['model']})" for p in providers()) or "未检测到 Claude Code（请先安装并登录，或在网页里填写 Claude API key）"
    print(f"\nAI Research Graph 已启动: {url}\n可用模型: {names}\n关闭这个窗口即可停止网站。\n", flush=True)
    if open_path is not None:
        webbrowser.open(url + "index.html" + open_path)
    import threading
    threading.Thread(target=EMBEDDER.warm, daemon=True).start()  # load the query embedder in the background
    server.serve_forever()
