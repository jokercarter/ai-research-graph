import { env } from "cloudflare:workers";
import { availableProviders, complete, modelName, pickProvider, stream, type ChatMessage } from "../../../lib/llm";
import { rateLimited } from "../../../lib/rate-limit";

// Research Q&A over papers the browser retrieved from the site's own index.
//   GET                      -> which providers are configured
//   POST {phase:"plan"}      -> English search queries for the question (JSON)
//   POST {phase:"answer"}    -> streamed answer that cites the supplied papers as [n]
const MAX_QUESTION = 600;
const MAX_SOURCES = 16;
const MAX_HISTORY = 6;

type Source = { n: number; id: string; title: string; date?: string; authors?: string; abstract?: string };
const runtimeEnv = () => env as unknown as Record<string, string | undefined>;
const clip = (v: unknown, max: number) => String(v ?? "").slice(0, max);

export async function GET() {
  const e = runtimeEnv();
  const providers = availableProviders(e);
  return Response.json({ providers: providers.map((p) => ({ id: p, model: modelName(e, p), label: p === "anthropic" ? "Claude" : "OpenAI" })), default: pickProvider(e) });
}

const PLAN_SYSTEM = `You help researchers search an index of AI/CS arXiv papers (2024-11 to 2026-10). The index matches English words in titles and abstracts.
Given the conversation and the latest question, output strict JSON only:
{"queries": ["<2-4 English keyword queries, 3-8 words each, using the technical terms papers would use; cover different angles of the question>"], "language": "zh" | "en"}`;

function answerSystem(sources: Source[], lang: string) {
  const list = sources.map((s) => `[${s.n}] ${s.title} (${s.date || "?"}; ${s.authors || ""})\n${s.abstract || "(no abstract)"}`).join("\n\n");
  return `You are a research assistant on an AI/CS paper search website. Answer the researcher's question using ONLY the papers listed below (retrieved from the site's index for this question).
Rules:
- Cite papers inline as [n] right after the claim they support; cite only numbers from the list. Never invent papers, numbers, results or authors.
- If the listed papers do not cover part of the question, say so plainly and suggest what to search for next.
- Abstracts are summaries; say "根据摘要" when a detail may need checking in the full paper.
- Structure: a direct answer first, then grouped findings (methods / representative works / open problems as fits), then 2-3 suggested follow-up questions on a final line starting with "延伸问题：" separated by "｜".
- Write in ${lang === "en" ? "English" : "Simplified Chinese"}; keep paper titles and technical terms in English. Use Markdown headings sparingly and bullet lists where helpful. Keep it under about 600 words.

Papers:
${list}`;
}

function cleanHistory(value: unknown): ChatMessage[] {
  if (!Array.isArray(value)) return [];
  return value.slice(-MAX_HISTORY).map((m) => {
    const item = m && typeof m === "object" ? m as Record<string, unknown> : {};
    return { role: item.role === "assistant" ? "assistant" : "user", content: clip(item.content, 2500) } as ChatMessage;
  }).filter((m) => m.content);
}

export async function POST(request: Request) {
  const e = runtimeEnv();
  let body: Record<string, unknown>;
  try { body = await request.json(); } catch { return Response.json({ error: "请求格式无效" }, { status: 400 }); }
  const provider = pickProvider(e, body.provider);
  if (!provider) return Response.json({ error: "网站还没有配置模型 API（ANTHROPIC_API_KEY 或 OPENAI_API_KEY）" }, { status: 503 });
  const question = clip(body.question, MAX_QUESTION).trim();
  if (question.length < 2) return Response.json({ error: "问题太短" }, { status: 400 });
  const history = cleanHistory(body.history);

  if (body.phase === "plan") {
    if (rateLimited(request, "ask-plan", 20, 60_000)) return Response.json({ error: "请求太频繁，请稍后再试" }, { status: 429 });
    try {
      const text = await complete(e, provider, PLAN_SYSTEM, [...history, { role: "user", content: question }], 300);
      const json = JSON.parse(text.slice(text.indexOf("{"), text.lastIndexOf("}") + 1)) as { queries?: unknown; language?: unknown };
      const queries = Array.isArray(json.queries) ? json.queries.slice(0, 4).map((q) => clip(q, 120).replace(/[^\w\s\-]/g, " ").trim()).filter(Boolean) : [];
      return Response.json({ queries, language: json.language === "en" ? "en" : "zh", provider });
    } catch (err) {
      return Response.json({ error: err instanceof Error ? err.message : "模型没有返回可用的检索词" }, { status: 502 });
    }
  }

  if (rateLimited(request, "ask-answer", 10, 60_000)) return Response.json({ error: "提问太频繁，请一分钟后再试" }, { status: 429 });
  const sources: Source[] = (Array.isArray(body.sources) ? body.sources : []).slice(0, MAX_SOURCES).map((s: unknown, i: number) => {
    const r = s && typeof s === "object" ? s as Record<string, unknown> : {};
    return { n: i + 1, id: clip(r.id, 40), title: clip(r.title, 300), date: clip(r.date, 12), authors: clip(r.authors, 160), abstract: clip(r.abstract, 1800) };
  }).filter((s) => s.title);
  if (!sources.length) return Response.json({ error: "没有检索到可供回答的论文" }, { status: 400 });
  try {
    const requested = typeof body.model === "string" && /^claude-[a-z0-9.-]{3,40}$/.test(body.model) && provider === "anthropic" ? body.model : "";
    const env2 = requested ? { ...e, ANTHROPIC_MODEL: requested } : e;
    const out = await stream(env2, provider, answerSystem(sources, body.language === "en" ? "en" : "zh"), [...history, { role: "user", content: question }]);
    return new Response(out, { headers: { "content-type": "text/plain; charset=utf-8", "cache-control": "no-store", "x-provider": provider, "x-model": modelName(env2, provider) } });
  } catch (err) {
    return Response.json({ error: err instanceof Error ? err.message : "模型服务暂不可用" }, { status: 502 });
  }
}
