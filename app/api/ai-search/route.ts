import { env } from "cloudflare:workers";

type Candidate = {
  id: string;
  title: string;
  year?: number;
  venue?: string;
  authors?: string[];
  topics?: string[];
  citations_count?: number;
  abstract?: string;
};

type SearchResult = {
  id: string;
  reason?: string;
};

const MAX_CANDIDATES = 80;
const MAX_QUERY_LENGTH = 240;

function jsonError(message: string, status = 400) {
  return Response.json({ error: message }, { status });
}

function outputText(payload: any) {
  if (typeof payload?.output_text === "string") return payload.output_text;
  const chunks: string[] = [];
  for (const item of payload?.output || []) {
    for (const content of item?.content || []) {
      if (typeof content?.text === "string") chunks.push(content.text);
    }
  }
  return chunks.join("\n");
}

function parseModelJson(text: string) {
  const cleaned = text.trim().replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/i, "");
  try {
    return JSON.parse(cleaned);
  } catch {
    const start = cleaned.indexOf("{");
    const end = cleaned.lastIndexOf("}");
    if (start < 0 || end <= start) throw new Error("AI 返回的结果无法解析");
    return JSON.parse(cleaned.slice(start, end + 1));
  }
}

function normalizeResults(value: unknown, allowed: Set<string>): SearchResult[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => ({
      id: String((item as any)?.id || ""),
      reason: String((item as any)?.reason || "").slice(0, 180),
    }))
    .filter((item) => allowed.has(item.id))
    .filter((item, index, all) => all.findIndex((candidate) => candidate.id === item.id) === index)
    .slice(0, 8);
}

export async function POST(request: Request) {
  const runtimeEnv = env as typeof env & { OPENAI_API_KEY?: string; OPENAI_RESEARCH_MODEL?: string };
  const apiKey = String(runtimeEnv.OPENAI_API_KEY || "").trim();
  if (!apiKey) return jsonError("站点尚未配置 OPENAI_API_KEY。", 503);

  let body: { query?: unknown; candidates?: unknown };
  try {
    body = await request.json();
  } catch {
    return jsonError("请求格式无效。", 400);
  }

  const query = typeof body.query === "string" ? body.query.trim() : "";
  if (query.length < 2) return jsonError("请输入至少 2 个字符的研究问题。", 400);
  if (query.length > MAX_QUERY_LENGTH) return jsonError(`搜索内容不能超过 ${MAX_QUERY_LENGTH} 个字符。`, 400);

  const candidates = Array.isArray(body.candidates)
    ? body.candidates.slice(0, MAX_CANDIDATES).map((candidate: any) => ({
        id: String(candidate?.id || ""),
        title: String(candidate?.title || "").slice(0, 240),
        year: Number(candidate?.year) || undefined,
        venue: String(candidate?.venue || "").slice(0, 120),
        authors: Array.isArray(candidate?.authors) ? candidate.authors.slice(0, 5).map(String) : [],
        topics: Array.isArray(candidate?.topics) ? candidate.topics.slice(0, 6).map(String) : [],
        citations_count: Number(candidate?.citations_count) || 0,
        abstract: String(candidate?.abstract || "").slice(0, 420),
      }))
    : [];
  if (!candidates.length) return jsonError("没有可供 AI 匹配的论文候选。", 400);

  const allowed = new Set(candidates.map((candidate) => candidate.id));
  const prompt = [
    "用户要在一张 AI/CS 论文引用图谱中查找相关文章。",
    `用户问题：${query}`,
    "请仅从候选论文中选择最相关的 1 到 8 篇，按照相关性排序。",
    "不要编造候选列表以外的论文 ID。reason 用中文简短说明为什么相关。",
    "只返回 JSON，不要 Markdown：{\"answer\":\"一句话总结\",\"results\":[{\"id\":\"候选ID\",\"reason\":\"相关原因\"}]}。",
    "候选论文：",
    JSON.stringify(candidates),
  ].join("\n");

  let upstream: Response;
  try {
    upstream = await fetch("https://api.openai.com/v1/responses", {
      method: "POST",
      headers: {
        authorization: `Bearer ${apiKey}`,
        "content-type": "application/json",
      },
      body: JSON.stringify({
        model: runtimeEnv.OPENAI_RESEARCH_MODEL || "gpt-4.1-mini",
        instructions: "你是论文检索助手，必须严格遵守用户指定的 JSON 输出格式。",
        input: prompt,
        max_output_tokens: 1200,
      }),
    });
  } catch {
    return jsonError("无法连接 OpenAI API，请稍后重试。", 502);
  }

  const responsePayload = await upstream.json().catch(() => ({}));
  if (!upstream.ok) {
    const detail = String(responsePayload?.error?.message || "OpenAI API 请求失败").slice(0, 220);
    return jsonError(detail, upstream.status === 401 || upstream.status === 403 ? 502 : upstream.status);
  }

  try {
    const parsed = parseModelJson(outputText(responsePayload));
    const results = normalizeResults(parsed?.results, allowed);
    return Response.json({
      answer: String(parsed?.answer || "").slice(0, 240),
      results: results.map((result) => {
        const candidate = candidates.find((item) => item.id === result.id);
        return { ...result, title: candidate?.title, year: candidate?.year, venue: candidate?.venue };
      }),
    });
  } catch {
    return jsonError("AI 返回了无法识别的结果，请换一种描述再试。", 502);
  }
}
