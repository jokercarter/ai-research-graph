import { env } from "cloudflare:workers";
import { availableProviders, complete, modelName, pickProvider, stream, type ChatMessage } from "../../../lib/llm";
import { rateLimited } from "../../../lib/rate-limit";

// Research Q&A over papers the browser retrieved from the site's own index.
//   GET                      -> which providers are configured
//   POST {phase:"plan"}      -> English search queries for the question (JSON)
//   POST {phase:"answer"}    -> streamed answer that cites the supplied papers as [n]
const MAX_QUESTION = 600;
const MAX_SOURCES = 24;
const MAX_HISTORY = 4;
// Writing search keywords needs no big model.
const PLAN_MODEL = "claude-haiku-5-5";

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

// Same text as answerSystem in public/research/ask.js. Kept short: it is sent with every question.
function answerSystem(sources: Source[], lang: string, overview: string) {
  const list = sources.filter((s) => s.abstract).map((s) => `[${s.n}] ${s.title} (${s.date || "?"})\n${s.abstract}`).join("\n\n");
  const more = sources.filter((s) => !s.abstract).map((s) => `[${s.n}] ${s.title} (${s.date || "?"})`).join("\n") || "(none)";
  return `You are a research assistant on an AI/CS paper search site. Answer from the papers below only; they were selected from the whole index for this question.
Rules: cite as [n] after each claim, only numbers listed; never invent papers or results. Title-only papers may be cited for what their title shows. Say plainly what the papers don't cover. Start with a direct answer, then grouped findings (methods / representative work / open problems as fits), max ~450 words, in ${lang === "en" ? "English" : "Simplified Chinese"} (keep titles and terms in English). End with one line "延伸问题：" + 2-3 follow-up questions separated by "｜".

Index overview: ${overview}

Key papers (abstract excerpts):
${list}

More relevant papers (titles):
${more}`;
}

function cleanHistory(value: unknown): ChatMessage[] {
  if (!Array.isArray(value)) return [];
  return value.slice(-MAX_HISTORY).map((m) => {
    const item = m && typeof m === "object" ? m as Record<string, unknown> : {};
    return { role: item.role === "assistant" ? "assistant" : "user", content: clip(item.content, 800) } as ChatMessage;
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
      const planEnv = provider === "anthropic" ? { ...e, ANTHROPIC_MODEL: e.ANTHROPIC_PLAN_MODEL || PLAN_MODEL } : e;
      const text = await complete(planEnv, provider, PLAN_SYSTEM, [...history, { role: "user", content: question }], 200);
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
    return { n: i + 1, id: clip(r.id, 40), title: clip(r.title, 300), date: clip(r.date, 12), authors: clip(r.authors, 160), abstract: clip(r.abstract, 600) };
  }).filter((s) => s.title);
  if (!sources.length) return Response.json({ error: "没有检索到可供回答的论文" }, { status: 400 });
  try {
    const requested = typeof body.model === "string" && /^claude-[a-z0-9.-]{3,40}$/.test(body.model) && provider === "anthropic" ? body.model : "";
    const env2 = requested ? { ...e, ANTHROPIC_MODEL: requested } : e;
    const out = await stream(env2, provider, answerSystem(sources, body.language === "en" ? "en" : "zh", clip(body.overview, 900) || "(none)"), [...history, { role: "user", content: question }], 1400);
    return new Response(out, { headers: { "content-type": "text/plain; charset=utf-8", "cache-control": "no-store", "x-provider": provider, "x-model": modelName(env2, provider) } });
  } catch (err) {
    return Response.json({ error: err instanceof Error ? err.message : "模型服务暂不可用" }, { status: 502 });
  }
}
