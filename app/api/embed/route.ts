import { env } from "cloudflare:workers";
import { rateLimited } from "../../../lib/rate-limit";

// Query embedding for semantic search on the public site. Uses the same model as the
// paper vectors (BAAI/bge-base-en-v1.5), hosted by Cloudflare Workers AI:
//   - an `AI` binding on the Worker, or
//   - CF_ACCOUNT_ID + CF_AI_TOKEN (an API token with "Workers AI" permission).
// Returns the raw 768-d vector; the browser projects it with data/v/pca.bin.gz.
// Without either setting it answers 503 and the site falls back to keyword feedback.
const MODEL = "@cf/baai/bge-base-en-v1.5";
const PREFIX = "Represent this sentence for searching relevant passages: ";
const cache = new Map<string, number[]>();

type AiBinding = { run: (model: string, input: unknown) => Promise<{ data?: number[][] }> };

export async function POST(request: Request) {
  const e = env as unknown as Record<string, unknown>;
  const ai = e.AI as AiBinding | undefined;
  const account = typeof e.CF_ACCOUNT_ID === "string" ? e.CF_ACCOUNT_ID.trim() : "";
  const token = typeof e.CF_AI_TOKEN === "string" ? e.CF_AI_TOKEN.trim() : "";
  if (!ai && !(account && token)) return Response.json({ error: "embedder not configured" }, { status: 503 });
  if (rateLimited(request, "embed", 60, 60_000)) return Response.json({ error: "请求太频繁，请稍后再试" }, { status: 429 });

  let q = "";
  try {
    const body = (await request.json()) as { q?: unknown };
    q = typeof body.q === "string" ? body.q.trim().slice(0, 300) : "";
  } catch {
    return Response.json({ error: "请求格式无效" }, { status: 400 });
  }
  if (!q) return Response.json({ error: "empty query" }, { status: 400 });
  const hit = cache.get(q);
  if (hit) return Response.json({ raw: hit });

  try {
    let data: number[][] | undefined;
    if (ai) {
      data = (await ai.run(MODEL, { text: [PREFIX + q], pooling: "cls" })).data;
    } else {
      const res = await fetch(`https://api.cloudflare.com/client/v4/accounts/${account}/ai/run/${MODEL}`, {
        method: "POST",
        headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
        body: JSON.stringify({ text: [PREFIX + q], pooling: "cls" }),
      });
      if (!res.ok) throw new Error(`workers ai ${res.status}`);
      data = ((await res.json()) as { result?: { data?: number[][] } }).result?.data;
    }
    const v = data?.[0];
    if (!v || v.length !== 768) throw new Error("bad vector");
    const raw = v.map((x) => Math.round(x * 1e5) / 1e5);
    if (cache.size > 2000) cache.clear();
    cache.set(q, raw);
    return Response.json({ raw });
  } catch {
    return Response.json({ error: "embedding failed" }, { status: 502 });
  }
}
