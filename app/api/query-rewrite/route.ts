import { env } from "cloudflare:workers";
import { rateLimited } from "../../../lib/rate-limit";

// Turns a Chinese (or mixed) research question into English search keywords,
// because the paper index is built from English titles and abstracts.
const MAX_QUERY_LENGTH = 240;
const cacheMap = new Map<string, { english: string; terms: string[] }>();

function outputText(payload: unknown) {
  const record = payload && typeof payload === "object" ? (payload as Record<string, unknown>) : {};
  if (typeof record.output_text === "string") return record.output_text;
  const chunks: string[] = [];
  for (const item of Array.isArray(record.output) ? record.output : []) {
    const contents = item && typeof item === "object" ? (item as Record<string, unknown>).content : [];
    for (const content of Array.isArray(contents) ? contents : []) {
      const text = content && typeof content === "object" ? (content as Record<string, unknown>).text : "";
      if (typeof text === "string") chunks.push(text);
    }
  }
  return chunks.join("\n");
}

export async function POST(request: Request) {
  const runtimeEnv = env as typeof env & { OPENAI_API_KEY?: string; OPENAI_RESEARCH_MODEL?: string };
  const apiKey = String(runtimeEnv.OPENAI_API_KEY || "").trim();
  if (!apiKey) return Response.json({ error: "未配置 AI 服务" }, { status: 503 });
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

  const upstream = await fetch("https://api.openai.com/v1/responses", {
    method: "POST",
    headers: { authorization: `Bearer ${apiKey}`, "content-type": "application/json" },
    body: JSON.stringify({
      model: runtimeEnv.OPENAI_RESEARCH_MODEL || "gpt-4.1-mini",
      instructions: "You convert research questions into English academic search keywords for an AI/CS paper index. Output strict JSON only.",
      input: `Question: ${query}\nReturn {"english": "<6-14 English keywords/phrases as they appear in paper titles and abstracts, space separated, most important first>", "terms": ["<key phrase>", ...]}. Use standard technical terms (e.g. 大模型 -> large language model). No explanations.`,
      max_output_tokens: 200,
    }),
  }).catch(() => null);
  if (!upstream || !upstream.ok) return Response.json({ error: "AI 服务暂不可用" }, { status: 502 });
  try {
    const text = outputText(await upstream.json()).trim().replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/, "");
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
