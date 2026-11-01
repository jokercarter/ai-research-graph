import { env } from "cloudflare:workers";
import { rateLimited } from "../../../lib/rate-limit";
import { complete, pickProvider } from "../../../lib/llm";

// Turns a Chinese (or mixed) research question into English search keywords,
// because the paper index is built from English titles and abstracts.
const MAX_QUERY_LENGTH = 240;
const cacheMap = new Map<string, { english: string; terms: string[] }>();

export async function POST(request: Request) {
  const runtimeEnv = env as unknown as Record<string, string | undefined>;
  const provider = pickProvider(runtimeEnv);
  if (!provider) return Response.json({ error: "未配置 AI 服务" }, { status: 503 });
  if (rateLimited(request, "rewrite", 30, 60_000)) return Response.json({ error: "请求太频繁，请稍后再试" }, { status: 429 });

  let query = "";
  try {
    const body = (await request.json()) as { query?: unknown };
    query = typeof body.query === "string" ? body.query.trim().slice(0, MAX_QUERY_LENGTH) : "";
  } catch {
    return Response.json({ error: "请求格式无效" }, { status: 400 });
  }
  if (query.length < 2) return Response.json({ error: "查询太短" }, { status: 400 });
  const cached = cacheMap.get(query);
  if (cached) return Response.json(cached);

  try {
    const text = (await complete(runtimeEnv, provider,
      "You convert research questions into English academic search keywords for an AI/CS paper index. Output strict JSON only.",
      [{ role: "user", content: `Question: ${query}\nReturn {"english": "<6-14 English keywords/phrases as they appear in paper titles and abstracts, space separated, most important first>", "terms": ["<key phrase>", ...]}. Use standard technical terms (e.g. 大模型 -> large language model). No explanations.` }],
      200)).trim().replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/, "");
    const parsed = JSON.parse(text.slice(text.indexOf("{"), text.lastIndexOf("}") + 1));
    const english = String(parsed.english || "").replace(/[^\w\s\-]/g, " ").replace(/\s+/g, " ").trim().slice(0, 200);
    const terms = Array.isArray(parsed.terms) ? parsed.terms.slice(0, 10).map((t: unknown) => String(t).slice(0, 60)) : [];
    if (!english) throw new Error("empty");
    const result = { english, terms };
    if (cacheMap.size > 2000) cacheMap.clear();
    cacheMap.set(query, result);
    return Response.json(result);
  } catch {
    return Response.json({ error: "AI 返回无法解析" }, { status: 502 });
  }
}
