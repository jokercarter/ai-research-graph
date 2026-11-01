import { env } from "cloudflare:workers";

type Mode = "related" | "review" | "compare" | "gaps" | "frontier";

type Filters = {
  year_from?: number;
  year_to?: number;
  topics?: string[];
  publication_types?: string[];
  venues?: string[];
  min_citations?: number;
};

type Candidate = {
  id: string;
  title: string;
  year?: number;
  venue?: string;
  authors?: string[];
  topics?: string[];
  citations_count?: number;
  abstract?: string;
  metadata_completeness?: number;
  publication_type?: string;
  is_frontier?: boolean;
};

type SearchResult = {
  id: string;
  title?: string;
  year?: number;
  venue?: string;
  reason: string;
  relevance_score: number;
  evidence_ids: string[];
  graph_signals: { citation_count: number; is_frontier: boolean };
};

type EvidenceClaim = {
  text: string;
  evidence_ids: string[];
  confidence?: number;
  support?: "abstract" | "metadata" | "graph" | "unknown";
};

type NormalizedAnswer = {
  summary: string;
  key_points: EvidenceClaim[];
  caveats: string[];
  evidence_ids: string[];
};

const MAX_CANDIDATES = 80;
const MAX_RESULTS = 12;
const MAX_QUERY_LENGTH = 240;
const MODE_LABELS: Record<Mode, string> = {
  related: "找相关论文",
  review: "梳理文献综述",
  compare: "比较研究方法",
  gaps: "寻找研究空白",
  frontier: "寻找研究前沿",
};

function jsonError(message: string, status = 400) {
  return Response.json({ error: message }, { status });
}

function outputText(payload: unknown) {
  const record = payload && typeof payload === "object" ? payload as Record<string, unknown> : {};
  if (typeof record.output_text === "string") return record.output_text;
  const chunks: string[] = [];
  for (const item of Array.isArray(record.output) ? record.output : []) {
    const itemRecord = item && typeof item === "object" ? item as Record<string, unknown> : {};
    for (const content of Array.isArray(itemRecord.content) ? itemRecord.content : []) {
      const contentRecord = content && typeof content === "object" ? content as Record<string, unknown> : {};
      if (typeof contentRecord.text === "string") chunks.push(contentRecord.text);
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

function safeString(value: unknown, max = 240) {
  return String(value || "").slice(0, max);
}

function normalizeMode(value: unknown): Mode {
  return value === "related" || value === "compare" || value === "gaps" || value === "frontier" ? value : "review";
}

function normalizeCandidates(value: unknown): Candidate[] {
  if (!Array.isArray(value)) return [];
  return value.slice(0, MAX_CANDIDATES).map((candidate: unknown) => {
    const item = candidate && typeof candidate === "object" ? candidate as Record<string, unknown> : {};
    return {
      id: safeString(item.id, 180),
      title: safeString(item.title, 240),
      year: Number(item.year) || undefined,
      venue: safeString(item.venue, 120),
      authors: Array.isArray(item.authors) ? item.authors.slice(0, 5).map((author) => safeString(author, 100)) : [],
      topics: Array.isArray(item.topics) ? item.topics.slice(0, 8).map((topic) => safeString(topic, 100)) : [],
      citations_count: Number(item.citations_count) || 0,
      abstract: safeString(item.abstract, 800),
      metadata_completeness: Math.max(0, Math.min(1, Number(item.metadata_completeness) || 0)),
      publication_type: safeString(item.publication_type, 80),
      is_frontier: Boolean(item.is_frontier),
    };
  }).filter((candidate) => candidate.id && candidate.title);
}

function normalizeFilters(value: unknown): Filters {
  const filters = value && typeof value === "object" ? value as Record<string, unknown> : {};
  return {
    year_from: Number(filters.year_from) || undefined,
    year_to: Number(filters.year_to) || undefined,
    topics: Array.isArray(filters.topics) ? filters.topics.slice(0, 8).map(String) : [],
    publication_types: Array.isArray(filters.publication_types) ? filters.publication_types.slice(0, 8).map(String) : [],
    venues: Array.isArray(filters.venues) ? filters.venues.slice(0, 8).map(String) : [],
    min_citations: Math.max(0, Number(filters.min_citations) || 0),
  };
}

function buildFacets(candidates: Candidate[]) {
  const collect = (values: string[]) => [...new Map(values.filter(Boolean).map((value) => [value, values.filter((item) => item === value).length])).entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, 12)
    .map(([value, count]) => ({ value, count }));
  return {
    years: collect(candidates.map((candidate) => String(candidate.year || ""))),
    topics: collect(candidates.flatMap((candidate) => candidate.topics || [])),
    venues: collect(candidates.map((candidate) => candidate.venue || "")),
  };
}

function normalizeAnswer(value: unknown, allowed: Set<string>): NormalizedAnswer {
  if (typeof value === "string") return { summary: value.slice(0, 600), key_points: [], caveats: [], evidence_ids: [] };
  const answer = value && typeof value === "object" ? value as Record<string, unknown> : {};
  const keyPoints: EvidenceClaim[] = Array.isArray(answer.key_points) ? answer.key_points.slice(0, 8).map((item): EvidenceClaim => {
    if (typeof item === "string") return { text: item.slice(0, 240), evidence_ids: [] };
    const claim = item && typeof item === "object" ? item as Record<string, unknown> : {};
    const confidence = Number(claim.confidence);
    const support = claim.support === "abstract" || claim.support === "metadata" || claim.support === "graph" ? claim.support : "unknown";
    return {
      text: safeString(claim.text || claim.claim, 240),
      evidence_ids: Array.isArray(claim.evidence_ids) ? [...new Set(claim.evidence_ids.map((id) => safeString(id, 180)).filter((id) => allowed.has(id)))].slice(0, MAX_RESULTS) : [],
      confidence: Number.isFinite(confidence) ? Math.max(0, Math.min(1, confidence)) : undefined,
      support,
    };
  }).filter((item) => item.text) : [];
  return {
    summary: safeString(answer.summary, 600),
    key_points: keyPoints,
    caveats: Array.isArray(answer.caveats) ? answer.caveats.slice(0, 8).map((item) => safeString(item, 240)) : [],
    evidence_ids: Array.isArray(answer.evidence_ids) ? [...new Set(answer.evidence_ids.map((item) => safeString(item, 180)).filter((item) => allowed.has(item)))].slice(0, MAX_RESULTS) : [],
  };
}

function normalizeResults(value: unknown, allowed: Set<string>, candidates: Candidate[]): SearchResult[] {
  if (!Array.isArray(value)) return [];
  return value.map((rawItem: unknown) => {
    const item = rawItem && typeof rawItem === "object" ? rawItem as Record<string, unknown> : {};
    const id = safeString(item.id, 180);
    const candidate = candidates.find((entry) => entry.id === id);
    const evidence = Array.isArray(item.evidence_ids) ? item.evidence_ids.map((entry) => safeString(entry, 180)).filter((entry) => allowed.has(entry)) : [];
    const evidenceIds = [...new Set([id, ...evidence].filter((entry) => allowed.has(entry)))];
    const score = Number(item.relevance_score);
    const signals = item.graph_signals && typeof item.graph_signals === "object" ? item.graph_signals as Record<string, unknown> : {};
    return {
      id,
      title: candidate?.title,
      year: candidate?.year,
      venue: candidate?.venue,
      reason: safeString(item.reason, 240),
      relevance_score: Number.isFinite(score) ? Math.max(0, Math.min(1, score)) : 0.5,
      evidence_ids: evidenceIds,
      graph_signals: {
        citation_count: candidate?.citations_count || 0,
        is_frontier: Boolean(candidate?.is_frontier || signals.is_frontier),
      },
    };
  }).filter((item) => allowed.has(item.id) && item.evidence_ids.length > 0)
    .filter((item, index, all) => all.findIndex((candidate) => candidate.id === item.id) === index)
    .slice(0, MAX_RESULTS);
}

function normalizeView(value: unknown, allowed: Set<string>) {
  const view = value && typeof value === "object" ? value as Record<string, unknown> : {};
  const timeline = Array.isArray(view.timeline) ? view.timeline.slice(0, 12).map((item) => {
    const row = item && typeof item === "object" ? item as Record<string, unknown> : {};
    return {
      year: Number(row.year) || undefined,
      ids: Array.isArray(row.ids) ? [...new Set(row.ids.map((id) => safeString(id, 180)).filter((id) => allowed.has(id)))].slice(0, MAX_RESULTS) : [],
    };
  }).filter((item) => item.year && item.ids.length) : [];
  const gaps = Array.isArray(view.gaps) ? view.gaps.slice(0, 8).map((item) => safeString(item, 240)).filter(Boolean) : [];
  return {
    timeline,
    comparison_columns: ["研究方法", "数据集", "指标", "优点", "局限"],
    gaps,
  };
}

function modeInstruction(mode: Mode) {
  if (mode === "related") return "优先返回语义、主题和引用关系最相关的论文，并说明匹配依据。";
  if (mode === "compare") return "优先选择 2 到 5 篇方法差异明显且元数据足够的论文，比较方法、数据集、指标、优点和局限；未知字段写‘元数据不足’。";
  if (mode === "gaps") return "根据候选论文的主题、年份和引用线索提出需要核实的研究空白，不能把推测写成事实。";
  if (mode === "frontier") return "优先近年论文、引用量增长线索、候选的前沿信号和图谱位置，说明为什么可能属于前沿。";
  return "按研究主题、方法、数据集和时间阶段组织候选论文，形成可追溯的文献综述提纲。";
}

export async function POST(request: Request) {
  const runtimeEnv = env as typeof env & { OPENAI_API_KEY?: string; OPENAI_RESEARCH_MODEL?: string };
  const apiKey = String(runtimeEnv.OPENAI_API_KEY || "").trim();
  if (!apiKey) return jsonError("站点尚未配置 OPENAI_API_KEY。", 503);

  let body: { query?: unknown; mode?: unknown; filters?: unknown; candidates?: unknown; limit?: unknown };
  try {
    body = await request.json();
  } catch {
    return jsonError("请求格式无效。", 400);
  }

  const query = typeof body.query === "string" ? body.query.trim() : "";
  if (query.length < 2) return jsonError("请输入至少 2 个字符的研究问题。", 400);
  if (query.length > MAX_QUERY_LENGTH) return jsonError(`搜索内容不能超过 ${MAX_QUERY_LENGTH} 个字符。`, 400);
  const mode = normalizeMode(body.mode);
  const filters = normalizeFilters(body.filters);
  const candidates = normalizeCandidates(body.candidates);
  if (!candidates.length) return jsonError("没有可供 AI 匹配的论文候选。", 400);

  const allowed = new Set(candidates.map((candidate) => candidate.id));
  const limit = Math.max(1, Math.min(MAX_RESULTS, Number(body.limit) || MAX_RESULTS));
  const prompt = [
    "你是 AI/CS 文献综述助手。用户只允许你使用候选论文元数据，不能编造候选列表之外的论文、作者、摘要、引用关系或结论。",
    `研究模式：${MODE_LABELS[mode]}`,
    `用户问题：${query}`,
    `筛选条件：${JSON.stringify(filters)}`,
    modeInstruction(mode),
    "所有 answer.summary、answer.key_points、answer.caveats 和结果 reason 都应能由候选元数据支持；缺少摘要或字段时明确写‘元数据不足，需要进一步核实’。",
    "answer.key_points 必须是对象数组，每条包含 text 和 evidence_ids；evidence_ids 只能使用候选论文 ID，并可附 confidence（0 到 1）和 support（abstract、metadata 或 graph）。",
    "answer.evidence_ids 必须列出支撑总结和关键发现的候选论文 ID；如果没有足够证据就留空并在 caveats 中写‘需要进一步核实’。",
    `最多返回 ${limit} 篇论文。每篇结果必须包含 evidence_ids，且 evidence_ids 只能使用候选论文 ID；relevance_score 为 0 到 1。`,
    "只返回 JSON，不要 Markdown，格式为：",
    JSON.stringify({
      interpreted_query: { main_question: "", expanded_terms: [], sub_questions: [] },
      answer: { summary: "", key_points: [{ text: "", evidence_ids: [], confidence: 0, support: "metadata" }], caveats: [], evidence_ids: [] },
      results: [{ id: "候选ID", reason: "", relevance_score: 0.8, evidence_ids: ["候选ID"], graph_signals: { citation_count: 0, is_frontier: false } }],
      facets: { years: [], topics: [], venues: [] },
      view: { timeline: [], comparison_columns: ["研究方法", "数据集", "指标", "优点", "局限"], gaps: [] },
    }),
    "候选论文：",
    JSON.stringify(candidates),
  ].join("\n");

  let upstream: Response;
  try {
    upstream = await fetch("https://api.openai.com/v1/responses", {
      method: "POST",
      headers: { authorization: `Bearer ${apiKey}`, "content-type": "application/json" },
      body: JSON.stringify({
        model: runtimeEnv.OPENAI_RESEARCH_MODEL || "gpt-4.1-mini",
        instructions: "严格输出合法 JSON。只能引用候选 ID。",
        input: prompt,
        max_output_tokens: 1800,
      }),
    });
  } catch {
    return jsonError("无法连接 OpenAI API，请稍后重试。", 502);
  }

  const responsePayload = await upstream.json().catch(() => ({}));
  if (!upstream.ok) {
    const detail = safeString(responsePayload?.error?.message || "OpenAI API 请求失败", 220);
    return jsonError(detail, upstream.status === 401 || upstream.status === 403 ? 502 : upstream.status);
  }

  try {
    const parsed = parseModelJson(outputText(responsePayload));
    const results = normalizeResults(parsed?.results, allowed, candidates).slice(0, limit);
    const answer = normalizeAnswer(parsed?.answer, allowed);
    if (!answer.evidence_ids.length) answer.evidence_ids = [...new Set(results.flatMap((result) => result.evidence_ids))].slice(0, Math.min(limit, 8));
    return Response.json({
      interpreted_query: {
        main_question: safeString(parsed?.interpreted_query?.main_question || query, 240),
        expanded_terms: Array.isArray(parsed?.interpreted_query?.expanded_terms) ? parsed.interpreted_query.expanded_terms.slice(0, 16).map((item: unknown) => safeString(item, 80)) : [],
        sub_questions: Array.isArray(parsed?.interpreted_query?.sub_questions) ? parsed.interpreted_query.sub_questions.slice(0, 8).map((item: unknown) => safeString(item, 180)) : [],
      },
      answer,
      results,
      // Facets are derived from the same bounded candidate set so the model
      // cannot introduce a venue, year, or topic that was not retrieved.
      facets: buildFacets(candidates),
      view: normalizeView(parsed?.view, allowed),
      source: "openai",
    });
  } catch {
    return jsonError("AI 返回了无法识别的结果，请换一种描述再试。", 502);
  }
}
