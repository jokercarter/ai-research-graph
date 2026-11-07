// Runs search cases through the browser engine (public/research/core.js and the
// retrieve() of ask.js) inside Node, for scripts/parity_check.py.
//
//   node scripts/parity_check.mjs cases.json > js_results.json
//
// core.js runs unchanged in a vm context: fetch() reads public/research/data from disk,
// /api/* answers 503 (so semantic search uses keyword feedback, like airg with embed=None).
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SITE = path.join(ROOT, "public", "research");

const store = new Map();
const ctx = {
  console, Response, Blob, DecompressionStream, TextDecoder, TextEncoder, URL, AbortController, setTimeout, clearTimeout,
  Uint8Array, Uint32Array, Int32Array, Int8Array, Float32Array, Map, Set, Math, JSON, Promise, Number, String, Array, Object, RegExp, Error,
  localStorage: { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, String(v)), removeItem: (k) => store.delete(k) },
  async fetch(url) {
    const u = String(url);
    if (u.startsWith("/api/")) return new Response("{}", { status: 503 });
    const rel = u.split("?")[0];
    try {
      return new Response(await readFile(path.join(SITE, rel)), { status: 200 });
    } catch {
      return new Response("not found", { status: 404 });
    }
  },
};
ctx.window = ctx;
vm.createContext(ctx);
vm.runInContext(await readFile(path.join(SITE, "core.js"), "utf8"), ctx, { filename: "core.js" });

// Only the retrieval part of ask.js (the rest needs the DOM).
const ask = (await readFile(path.join(SITE, "ask.js"), "utf8")).replace(/\r\n/g, "\n");
const grab = (re, name) => { const m = ask.match(re); if (!m) throw new Error(`ask.js: ${name} not found`); return m[0]; };
const pieces = [
  'function dirName(t) { return t ? t.name : ""; }',
  grab(/const N_KEY = .*?;/, "N_KEY"),
  grab(/function excerpt\([\s\S]*?\n}\n/, "excerpt"),
  grab(/function cos\(.*\n/, "cos"),
  grab(/async function retrieve\([\s\S]*?\n}\n/, "retrieve"),
  "window.retrieve = retrieve;",
];
vm.runInContext(pieces.join("\n"), ctx, { filename: "ask.js (retrieve)" });

const Core = ctx.Core;
await Core.init();

const hitList = (hits) => hits.slice(0, 50).map((h) => ({ sid: h.sid, score: h.score ?? null, sim: h.sim ?? null, semOnly: !!h.semOnly }));

async function run(c) {
  switch (c.kind) {
    case "search": { const r = await Core.search(c.q, c.opts || {}); return { hits: hitList(r.hits), total: r.total }; }
    case "fuzzy": {
      const r = await Core.fuzzySearch(c.q, c.opts || {});
      return { hits: hitList(r.hits), total: r.total, fixes: r.fixes, query: r.query, semantic: r.semantic, keywordTotal: r.keywordTotal };
    }
    case "correct": return await Core.correct(c.q);
    case "sidOfKey": return { sid: await Core.sidOfKey(c.key) };
    case "similar": {
      const sid = await Core.sidOfKey(c.key);
      const lite = await Core.lite(sid);
      return { sid, hits: hitList(await Core.similar(sid, lite.title, c.limit || 10)) };
    }
    case "retrieve": {
      const r = await ctx.retrieve(c.question, c.queries, c.pinned || null, null);
      return { keys: r.sources.map((s) => s.key), sources: r.sources, overview: r.overview, dirs: r.dirs, matched: r.matched };
    }
    default: throw new Error(`unknown case kind ${c.kind}`);
  }
}

const cases = JSON.parse(await readFile(process.argv[2], "utf8"));
const out = [];
for (const c of cases) {
  try { out.push({ ok: true, result: await run(c) }); } catch (e) { out.push({ ok: false, error: String(e && e.stack || e) }); }
}
process.stdout.write(JSON.stringify(out));
