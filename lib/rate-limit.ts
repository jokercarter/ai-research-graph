// Best-effort per-visitor rate limit for the public AI endpoints.
// Counters live in the Worker isolate, so this caps bursts from one visitor
// rather than giving a global guarantee; it keeps casual abuse from running up
// OpenAI costs without needing a database.
const buckets = new Map<string, { count: number; reset: number }>();

export function visitorKey(request: Request) {
  return request.headers.get("cf-connecting-ip") || request.headers.get("x-forwarded-for")?.split(",")[0]?.trim() || "anonymous";
}

export function rateLimited(request: Request, scope: string, limit: number, windowMs: number) {
  const now = Date.now();
  const key = `${scope}:${visitorKey(request)}`;
  const entry = buckets.get(key);
  if (!entry || entry.reset <= now) {
    buckets.set(key, { count: 1, reset: now + windowMs });
    if (buckets.size > 5000) {
      for (const [k, v] of buckets) if (v.reset <= now) buckets.delete(k);
    }
    return false;
  }
  entry.count += 1;
  return entry.count > limit;
}
