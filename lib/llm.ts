// Thin client for the two supported model providers (Anthropic Claude and OpenAI).
// Keys come from the Worker environment only and never reach the browser.
//   AI_PROVIDER         "anthropic" | "openai" (default: whichever key is set, Claude first)
//   ANTHROPIC_API_KEY   ANTHROPIC_MODEL (default claude-sonnet-5-5)
//   OPENAI_API_KEY      OPENAI_MODEL    (default gpt-4.1-mini; e.g. a Codex/GPT-5 model)
export type Provider = "anthropic" | "openai";
export type ChatMessage = { role: "user" | "assistant"; content: string };
type Env = Record<string, string | undefined>;

export function availableProviders(env: Env): Provider[] {
  const out: Provider[] = [];
  if (env.ANTHROPIC_API_KEY?.trim()) out.push("anthropic");
  if (env.OPENAI_API_KEY?.trim()) out.push("openai");
  return out;
}

export function pickProvider(env: Env, requested?: unknown): Provider | null {
  const list = availableProviders(env);
  if (requested === "anthropic" || requested === "openai") return list.includes(requested) ? requested : null;
  const preferred = env.AI_PROVIDER === "openai" || env.AI_PROVIDER === "anthropic" ? env.AI_PROVIDER : null;
  if (preferred && list.includes(preferred)) return preferred;
  return list[0] ?? null;
}

export function modelName(env: Env, provider: Provider) {
  return provider === "anthropic" ? env.ANTHROPIC_MODEL || "claude-sonnet-5-5" : env.OPENAI_MODEL || "gpt-4.1-mini";
}

function request(env: Env, provider: Provider, system: string, messages: ChatMessage[], maxTokens: number, stream: boolean) {
  if (provider === "anthropic") {
    return fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: { "x-api-key": String(env.ANTHROPIC_API_KEY), "anthropic-version": "2023-06-01", "content-type": "application/json" },
      body: JSON.stringify({ model: modelName(env, provider), max_tokens: maxTokens, system, messages, stream }),
    });
  }
  return fetch("https://api.openai.com/v1/responses", {
    method: "POST",
    headers: { authorization: `Bearer ${env.OPENAI_API_KEY}`, "content-type": "application/json" },
    body: JSON.stringify({ model: modelName(env, provider), instructions: system, input: messages, max_output_tokens: maxTokens, stream }),
  });
}

async function errorText(res: Response) {
  const body = await res.json().catch(() => ({})) as { error?: { message?: string } | string };
  const msg = typeof body.error === "string" ? body.error : body.error?.message;
  return `模型服务返回 ${res.status}${msg ? `：${String(msg).slice(0, 200)}` : ""}`;
}

/** One complete (non-streamed) reply as plain text. */
export async function complete(env: Env, provider: Provider, system: string, messages: ChatMessage[], maxTokens = 600) {
  const res = await request(env, provider, system, messages, maxTokens, false);
  if (!res.ok) throw new Error(await errorText(res));
  const data = await res.json() as Record<string, unknown>;
  if (provider === "anthropic") {
    const content = Array.isArray(data.content) ? data.content as { type?: string; text?: string }[] : [];
    return content.filter((c) => c.type === "text").map((c) => c.text || "").join("");
  }
  if (typeof data.output_text === "string") return data.output_text;
  const parts: string[] = [];
  for (const item of Array.isArray(data.output) ? data.output as { content?: { text?: string }[] }[] : []) {
    for (const c of item.content || []) if (typeof c.text === "string") parts.push(c.text);
  }
  return parts.join("");
}

/** Streamed reply re-encoded as plain UTF-8 text chunks for the browser. */
export async function stream(env: Env, provider: Provider, system: string, messages: ChatMessage[], maxTokens = 1800) {
  const res = await request(env, provider, system, messages, maxTokens, true);
  if (!res.ok || !res.body) throw new Error(await errorText(res));
  const decoder = new TextDecoder();
  const encoder = new TextEncoder();
  let buffer = "";
  return res.body.pipeThrough(new TransformStream<Uint8Array, Uint8Array>({
    transform(chunk, controller) {
      buffer += decoder.decode(chunk, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const line of lines) {
        if (!line.startsWith("data:")) continue;
        const payload = line.slice(5).trim();
        if (!payload || payload === "[DONE]") continue;
        try {
          const event = JSON.parse(payload) as Record<string, unknown>;
          let text = "";
          if (provider === "anthropic" && event.type === "content_block_delta") {
            const delta = event.delta as { type?: string; text?: string };
            if (delta?.type === "text_delta") text = delta.text || "";
          } else if (provider === "openai" && event.type === "response.output_text.delta") {
            text = String(event.delta || "");
          } else if (event.type === "error") {
            text = "\n\n（模型服务出错，回答可能不完整）";
          }
          if (text) controller.enqueue(encoder.encode(text));
        } catch { /* ignore keep-alive or partial lines */ }
      }
    },
  }));
}
