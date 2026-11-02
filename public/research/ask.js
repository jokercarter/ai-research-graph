"use strict";
// Research Q&A: the model plans search queries, the browser retrieves papers from the
// site's own index, and the model answers from those papers with [n] citations.
const Ask = {
  providers: null,
  turns: [],          // {q, answer, sources:[{n,key,title,date,authors,abstract,cat}], queries, provider}
  provider: "",
  busy: false,
};
const ASK_KEY = "airg.ask.v1";
// Same prompts as app/api/ask/route.ts (used when the visitor connects with their own key).
const PLAN_SYSTEM = `You help researchers search an index of AI/CS arXiv papers (about 500k, 2021-12 to now, updated monthly). The index matches English words in titles and abstracts.
Given the conversation and the latest question, output strict JSON only:
{"queries": ["<2-4 English keyword queries, 3-8 words each, using the technical terms papers would use; cover different angles of the question>"], "language": "zh" | "en"}`;
// Kept short on purpose: every word here is paid for on every question.
function answerSystem(sources, lang, overview) {
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
// ---- conversation history (this browser only) ------------------------------------
// airg.ask.v2 = {cur, convs:[{id, title, updated, turns}]}; the older single-thread
// airg.ask.v1 is imported once as the first conversation.
const CONV_KEY = "airg.ask.v2", MAX_CONVS = 80, MAX_TURNS = 30;
Ask.convs = []; Ask.cur = null;
const newId = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
function loadConvs() {
  try {
    const v2 = JSON.parse(localStorage.getItem(CONV_KEY) || "null");
    if (v2?.convs) { Ask.convs = v2.convs; Ask.cur = v2.cur; }
    else {
      const old = JSON.parse(localStorage.getItem(ASK_KEY) || "[]");
      if (old.length) { Ask.convs = [{ id: newId(), title: old[0].q.slice(0, 40), updated: Date.now(), turns: old }]; Ask.cur = Ask.convs[0].id; }
    }
  } catch { /* storage unavailable */ }
  let c = Ask.convs.find((x) => x.id === Ask.cur);
  if (!c) { c = { id: newId(), title: "", updated: Date.now(), turns: [] }; Ask.convs.unshift(c); Ask.cur = c.id; }
  Ask.turns = c.turns;
}
function curConv() { return Ask.convs.find((x) => x.id === Ask.cur); }
function saveAsk() {
  const c = curConv();
  if (c) { c.turns = Ask.turns; if (c.turns.length && !c.title) c.title = c.turns[0].q.slice(0, 40); }
  // Abstract excerpts were only needed for answering; links and titles are kept.
  const slim = (conv) => ({ ...conv, turns: conv.turns.slice(-MAX_TURNS).map((t) => { const { status, ...r } = t; return { ...r, sources: (t.sources || []).map(({ abstract, authors, ...x }) => x) }; }) });
  let list = Ask.convs.filter((x) => x.turns.length || x.id === Ask.cur).slice(0, MAX_CONVS);
  for (let tries = 0; tries < 5; tries++) {
    try { localStorage.setItem(CONV_KEY, JSON.stringify({ cur: Ask.cur, convs: list.map(slim) })); return; }
    catch { list = list.slice(0, Math.max(1, Math.floor(list.length * 0.7))); } // storage full: drop the oldest
  }
}
function switchConv(id) {
  if (Ask.busy) { toast("请等当前回答完成"); return; }
  const c = Ask.convs.find((x) => x.id === id); if (!c) return;
  Ask.cur = id; Ask.turns = c.turns; saveAsk(); renderThread(); renderSide();
}
function newConv() {
  if (Ask.busy) { toast("请等当前回答完成"); return; }
  const empty = Ask.convs.find((x) => !x.turns.length);
  if (empty) { Ask.cur = empty.id; Ask.turns = empty.turns; }
  else { const c = { id: newId(), title: "", updated: Date.now(), turns: [] }; Ask.convs.unshift(c); Ask.cur = c.id; Ask.turns = c.turns; }
  saveAsk(); renderThread(); renderSide();
  document.getElementById("askInput")?.focus();
}
function deleteConv(id) {
  if (Ask.busy && id === Ask.cur) return;
  Ask.convs = Ask.convs.filter((x) => x.id !== id);
  if (id === Ask.cur) { Ask.cur = null; const c = Ask.convs.find((x) => x.turns.length) || null; if (c) { Ask.cur = c.id; Ask.turns = c.turns; } else { Ask.turns = []; newConv(); return; } }
  saveAsk(); renderThread(); renderSide();
}
function dayLabel(ts) {
  const d = new Date(ts), now = new Date();
  const start = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((start(now) - start(d)) / 864e5);
  return days <= 0 ? "今天" : days === 1 ? "昨天" : days < 7 ? "最近 7 天" : days < 30 ? "最近 30 天" : `${d.getFullYear()} 年 ${d.getMonth() + 1} 月`;
}
function renderSide() {
  const box = document.getElementById("convList");
  if (!box) return;
  const f = (document.getElementById("convFilter")?.value || "").trim().toLowerCase();
  const list = Ask.convs.filter((c) => c.turns.length).sort((a, b) => b.updated - a.updated)
    .filter((c) => !f || c.title.toLowerCase().includes(f) || c.turns.some((t) => t.q.toLowerCase().includes(f)));
  let last = "", html = "";
  for (const c of list) {
    const g = dayLabel(c.updated);
    if (g !== last) { html += `<div class="conv-group">${g}</div>`; last = g; }
    html += `<div class="conv ${c.id === Ask.cur ? "on" : ""}" data-conv="${c.id}" title="${esc(c.turns[0]?.q || "")}">
      <span class="conv-t">${esc(c.title || "新对话")}</span><span class="conv-n">${c.turns.length}</span>
      <button class="conv-x" data-del="${c.id}" title="删除这个对话">×</button></div>`;
  }
  box.innerHTML = html || `<div class="conv-empty">${f ? "没有匹配的对话" : "还没有历史对话"}</div>`;
  box.querySelectorAll("[data-conv]").forEach((el) => (el.onclick = (e) => { if (!e.target.closest("[data-del]")) { switchConv(el.dataset.conv); document.getElementById("askSide")?.classList.remove("open"); } }));
  box.querySelectorAll("[data-del]").forEach((b) => (b.onclick = () => { if (confirmDelete(b.dataset.del)) deleteConv(b.dataset.del); }));
}
function confirmDelete(id) {
  const el = document.querySelector(`[data-conv="${id}"]`);
  if (el?.dataset.armed) return true;
  if (el) { el.dataset.armed = "1"; el.classList.add("armed"); el.querySelector(".conv-x").textContent = "删除？"; setTimeout(() => { if (el.isConnected) { delete el.dataset.armed; el.classList.remove("armed"); el.querySelector(".conv-x").textContent = "×"; } }, 2500); }
  return false;
}
loadConvs();

async function askProviders() {
  await Connect.serverInfo();
  return Connect.active();
}

function mdLite(text, sources) {
  const lines = esc(text).split("\n");
  const out = []; let list = null;
  const inline = (s) => s.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\[(\d+(?:\s*[,，、]\s*\d+)*)\]/g, (m, nums) => nums.split(/\s*[,，、]\s*/).map((n) => {
      const s = sources[Number(n) - 1];
      return s ? `<a class="cite" href="${paperHref(s.key)}" title="${esc(s.title)}">[${n}]</a>` : `[${n}]`;
    }).join(""));
  const close = () => { if (list) { out.push(`</${list}>`); list = null; } };
  for (const raw of lines) {
    const line = raw.trimEnd();
    if (/^延伸问题[:：]/.test(line.trim())) continue;
    let m;
    if ((m = line.match(/^#{1,4}\s+(.*)/))) { close(); out.push(`<h4>${inline(m[1])}</h4>`); }
    else if ((m = line.match(/^\s*[-*•]\s+(.*)/))) { if (list !== "ul") { close(); out.push("<ul>"); list = "ul"; } out.push(`<li>${inline(m[1])}</li>`); }
    else if ((m = line.match(/^\s*\d+[.)]\s+(.*)/))) { if (list !== "ol") { close(); out.push("<ol>"); list = "ol"; } out.push(`<li>${inline(m[1])}</li>`); }
    else if (!line.trim()) { close(); }
    else { close(); out.push(`<p>${inline(line)}</p>`); }
  }
  close();
  return out.join("");
}
function followUps(text) {
  const line = text.split("\n").find((l) => /^\s*延伸问题[:：]/.test(l));
  if (!line) return [];
  return line.replace(/^\s*延伸问题[:：]\s*/, "").split(/[｜|]/).map((s) => s.trim()).filter(Boolean).slice(0, 3);
}

function turnHtml(t, i) {
  const fu = followUps(t.answer || "");
  return `<div class="turn" id="turn${i}">
    <div class="q">${esc(t.q)}</div>
    ${t.dirs?.length ? `<div class="chips" style="margin:4px 0 6px"><span class="conn-desc">从全部论文中定位到的方向：</span>${t.dirs.map((d) => `<a class="chip acc" href="${trendHref(d.id)}">${esc(d.name)}</a>`).join("")}${t.matched ? `<span class="conn-desc">（检索了全部 ${fmt(Core.manifest.papers)} 篇）</span>` : ""}</div>` : ""}
    <div class="a">${t.answer ? mdLite(t.answer, t.sources) : t.error ? "" : `<div class="loading" style="padding:6px 0">${esc(t.status || "正在思考…")}</div>`}
      ${t.error ? `<div class="error" style="margin-top:8px">${esc(t.error)}${/40[13]|key|未配置|调用失败/.test(t.error) ? ` <button class="btn small" data-reconnect>更换模型</button>` : ""}</div>` : ""}
      ${fu.length ? `<div class="chips" style="margin-top:10px">${fu.map((f) => `<button class="chip acc" data-follow="${esc(f)}">${esc(f)}</button>`).join("")}</div>` : ""}</div>
    ${t.sources?.length ? `<details class="srcs" ${i === Ask.turns.length - 1 ? "open" : ""}><summary>参考的 ${t.sources.length} 篇论文（前 ${Math.min(8, t.sources.length)} 篇读了摘要）${t.queries?.length ? ` · 检索词：${esc(t.queries.join(" / "))}` : ""}</summary>
      ${t.sources.map((s) => `<div class="mini"><div><span class="reason">[${s.n}]</span><a href="${paperHref(s.key)}">${esc(s.title)}</a></div>
        <span class="m">${esc(s.date)} <button class="linkbtn ${Workspace.state.saved[s.key] ? "on" : ""}" data-save="${esc(s.key)}" data-title="${esc(s.title)}" data-year="${esc((String(s.date).match(/\d{4}/) || [""])[0])}">${Workspace.state.saved[s.key] ? "★" : "☆"}</button></span></div>`).join("")}
      <div class="row-actions" style="margin-top:8px"><button class="btn small" data-saveall="${i}">全部收藏</button></div></details>` : ""}
    ${t.provider && t.answer ? `<div class="note" style="color:var(--faint);font-size:12px">由 ${esc(t.provider)} 根据检索到的论文摘要生成，请以原文为准${t.usage ? ` · 本次约 ${fmt(t.usage.input)} 输入 + ${fmt(t.usage.output)} 输出 tokens` : ""}</div>` : ""}
  </div>`;
}

function renderThread() {
  const box = document.getElementById("thread");
  if (!box) return;
  box.innerHTML = Ask.turns.length ? Ask.turns.map(turnHtml).join("") : `<div class="empty" style="text-align:left">
    <p>输入一个科研问题，系统会先在站内 ${fmt(Core.manifest.papers)} 篇论文里检索，再让模型阅读最相关的论文摘要，给出带引用编号的回答。</p>
    <div class="chips">${["大语言模型强化学习推理（如 GRPO）最近有哪些改进方向？", "视觉-语言-动作模型（VLA）在机器人操作上的主要技术路线和瓶颈", "扩散模型做视频生成时，长视频一致性有哪些解决方法？", "有哪些评测 LLM agent 编程能力的基准？各自的局限是什么？"]
      .map((q) => `<button class="chip acc" data-follow="${esc(q)}">${esc(q)}</button>`).join("")}</div></div>`;
  box.querySelectorAll("[data-follow]").forEach((b) => (b.onclick = () => { document.getElementById("askInput").value = b.dataset.follow; askSubmit(); }));
  bindSave(box);
  box.querySelectorAll("[data-reconnect]").forEach((b) => (b.onclick = openConnect));
  box.querySelectorAll("[data-saveall]").forEach((b) => (b.onclick = () => {
    Ask.turns[Number(b.dataset.saveall)].sources.forEach((s) => { if (!Workspace.state.saved[s.key]) Workspace.toggleSaved(s.key, { title: s.title, year: Number((String(s.date).match(/\d{4}/) || [0])[0]) }); });
    updateBadge(); renderThread(); toast("已收藏全部参考论文");
  }));
}

// ---- retrieval over the whole index -------------------------------------------------
// 1. every query is searched over all papers (keywords + meaning), giving a pool of ~500
// 2. the pool tells which research directions the question belongs to
// 3. those directions are scanned completely by meaning, so relevant papers that use
//    other words are not missed
// 4. a diverse set of the closest papers is chosen (near-duplicates skipped); only short
//    abstract excerpts are sent to the model, the rest as titles (keeps tokens low)
const N_KEY = 8, N_MORE = 12;
function excerpt(text, terms, max = 460) {
  const sents = String(text || "").replace(/\s+/g, " ").match(/[^.!?]+[.!?]+(\s|$)|[^.!?]+$/g) || [];
  if (!sents.length) return "";
  const scored = sents.map((t, i) => { const w = new Set(CoreUtil.tokens(t)); let k = 0; terms.forEach((x) => { if (w.has(x)) k++; }); return { t: t.trim(), i, k }; });
  const keep = new Set([0]);
  for (const x of scored.slice(1).sort((a, b) => b.k - a.k || a.i - b.i)) { if (x.k === 0 || keep.size >= 3) break; keep.add(x.i); }
  let out = ""; for (const x of scored) if (keep.has(x.i)) { if ((out + " " + x.t).length > max) break; out += (out ? " … " : "") + x.t; }
  return out || sents[0].slice(0, max);
}
function cos(a, b) { let s = 0, na = 0, nb = 0; for (let i = 0; i < a.length; i++) { s += a[i] * b[i]; na += a[i] * a[i]; nb += b[i] * b[i]; } return s / (Math.sqrt(na * nb) || 1); }

async function retrieve(question, queries, pinnedKey, onStatus) {
  const [T, dirs] = await Promise.all([Core.getTrends(), Core.getDirs()]);
  const results = await Promise.all(queries.map((q) => Core.fuzzySearch(q, { limit: 160 })));
  const pool = new Map();
  results.forEach((r) => r.hits.forEach((h, rank) => pool.set(h.sid, (pool.get(h.sid) || 0) + 1 / (20 + rank))));
  const matched = Math.max(...results.map((r) => Math.max(r.total, r.keywordTotal || 0)), pool.size);
  // Directions of the question, weighted by how strongly each pooled paper matched.
  const dScore = new Map(); let dSum = 0;
  for (const [sid, sc] of pool) Core.dirsOf(dirs, sid).forEach((d, j) => { const w = sc * (j ? 0.5 : 1); dScore.set(d, (dScore.get(d) || 0) + w); dSum += w; });
  const ranked = [...dScore].sort((a, b) => b[1] - a[1]);
  const chosen = ranked.filter(([, w], i) => i === 0 || w / dSum >= 0.1).slice(0, 4).map(([d]) => d);
  onStatus?.(`问题属于：${chosen.map((d) => dirName(T.byId.get(d)) || d).join("、")}，正在扫描这些方向的全部论文…`);
  // Meaning vector of the question (local embedder / Workers AI), else of the best matches.
  const poolRanked = [...pool].sort((a, b) => b[1] - a[1]).map(([sid]) => ({ sid }));
  let qv = await Core.embedQuery(queries.join(". ").slice(0, 400));
  if (!qv) qv = await Core.feedbackVec(poolRanked.slice(0, 8));
  if (qv) {
    const sem = await Core.semantic(qv, { dirs: chosen, limit: 250 });
    sem.forEach((h, rank) => pool.set(h.sid, (pool.get(h.sid) || 0) + 1.2 / (20 + rank)));
  }
  let cands = [...pool].sort((a, b) => b[1] - a[1]).slice(0, 70).map(([sid, sc]) => ({ sid, sc }));
  // Diverse selection (maximal marginal relevance) using the stored paper vectors.
  const vecs = await Promise.all(cands.map((c) => Core.paperVec(c.sid)));
  const top = cands[0]?.sc || 1;
  cands.forEach((c, i) => { c.v = vecs[i]; c.rel = qv && c.v ? 0.55 * cos(qv, c.v) + 0.45 * (c.sc / top) : c.sc / top; });
  const picked = [];
  if (pinnedKey) { const pin = await Core.sidOfKey(pinnedKey); if (pin !== null) { const v = await Core.paperVec(pin); picked.push({ sid: pin, v, rel: 1 }); cands = cands.filter((c) => c.sid !== pin); } }
  const rest = [...cands];
  while (picked.length < N_KEY + N_MORE && rest.length) {
    let bi = -1, bs = -1e9;
    rest.forEach((c, i) => {
      const dup = c.v ? Math.max(0, ...picked.filter((p) => p.v).map((p) => cos(c.v, p.v))) : 0;
      if (dup > 0.9) return; // near-duplicate of a chosen paper
      const s = 0.65 * c.rel - 0.35 * dup;
      if (s > bs) { bs = s; bi = i; }
    });
    if (bi < 0) break;
    picked.push(rest.splice(bi, 1)[0]);
  }
  const terms = [...new Set(queries.flatMap((q) => CoreUtil.tokens(q)))];
  const details = await Promise.all(picked.map((c, i) => (i < N_KEY ? Core.detail(c.sid) : Core.lite(c.sid))));
  const sources = details.map((d, i) => i < N_KEY
    ? { n: i + 1, key: d.ax, title: d.t, venue: d.vn, date: d.vn ? `${d.vn}, ${d.d}` : d.d, authors: d.au.slice(0, 3).join(", ") + (d.na > 3 ? " et al." : ""), abstract: excerpt(d.ab, terms), cat: d.cat[0] }
    : { n: i + 1, key: d.key, title: d.title, venue: d.venue, date: d.venue ? `${d.venue}, ${d.date}` : d.date, authors: d.authors, abstract: "", cat: d.cat });
  const dirInfo = chosen.map((d) => T.byId.get(d)).filter(Boolean);
  const years = {}; for (const [sid] of pool) { const y = Core.year(Core.meta, sid); years[y] = (years[y] || 0) + 1; }
  const overview = `searched all ${Core.manifest.papers} papers; the question falls in these research directions: ` + dirInfo.map((t) =>
    `${t.name_en || t.name} (${t.size} papers in index, ${t.recent} in last 3 months, share ${t.growth >= 1 ? "+" : ""}${Math.round((t.growth - 1) * 100)}% vs prior year)`).join("; ")
    + `. Top matches by year: ${Object.entries(years).sort().map(([y, n]) => `${y}: ${n}`).join(", ")}.`;
  return { sources, overview, dirs: dirInfo.map((t) => ({ id: t.id, name: dirName(t) })), matched };
}

async function askSubmit() {
  const input = document.getElementById("askInput");
  const q = input.value.trim();
  if (!q || Ask.busy) return;
  const conn = await askProviders();
  if (!conn) { openConnect(); return; }
  Ask.busy = true; input.value = "";
  const pinned = new URLSearchParams(location.hash.split("?")[1] || "").get("paper");
  // Only the last two turns, answers shortened and without citation numbers: enough context for follow-ups.
  const history = Ask.turns.slice(-2).flatMap((t) => [{ role: "user", content: t.q }, { role: "assistant", content: (t.answer || "").replace(/\[\d+(?:[,，、]\s*\d+)*\]/g, "").split(/\n\s*延伸问题/)[0].slice(0, 500) }]).filter((m) => m.content);
  const turn = { q, answer: "", sources: [], queries: [], status: "正在理解问题、生成检索词…" };
  Ask.turns.push(turn); const cc = curConv(); if (cc) cc.updated = Date.now(); renderThread(); scrollToTurn();
  try {
    let language = CoreUtil.hasCJK(q) ? "zh" : "en";
    // English question without earlier context: search with it directly (no model call).
    const needPlan = CoreUtil.hasCJK(q) || history.length > 0;
    if (needPlan) try {
      if (Connect.isDirect()) {
        const text = await Connect.complete(PLAN_SYSTEM, [...history, { role: "user", content: q }], 200, "claude-haiku-5-5");
        const j = JSON.parse(text.slice(text.indexOf("{"), text.lastIndexOf("}") + 1));
        turn.queries = (j.queries || []).slice(0, 4).map((x) => String(x).replace(/[^\w\s\-]/g, " ").trim()).filter(Boolean);
        language = j.language === "en" ? "en" : "zh";
      } else {
        const res = await fetch("/api/ask", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ phase: "plan", question: q, history, provider: conn.provider, model: Connect.requestModel() }) });
        const j = await res.json();
        if (!res.ok) throw new Error(j.error);
        turn.queries = j.queries; language = j.language || language;
      }
    } catch { /* fall back to local keywords */ }
    if (!turn.queries.length) turn.queries = [CoreUtil.hasCJK(q) ? CoreUtil.localRewrite(q) : q].filter(Boolean);
    turn.status = `正在检索：${turn.queries.join(" / ")}`; renderThread();
    const got = await retrieve(q, turn.queries, pinned, (m) => { turn.status = m; renderThread(); });
    turn.sources = got.sources; turn.dirs = got.dirs; turn.matched = got.matched;
    const c = curConv(); if (c && !c.title) { c.title = q.slice(0, 40); c.updated = Date.now(); renderSide(); }
    if (!turn.sources.length) throw new Error("没有检索到相关论文，换个说法试试");
    turn.status = `已从全部论文中选出最相关的 ${turn.sources.length} 篇，正在撰写回答…`; renderThread();
    turn.provider = `${conn.label} · ${Connect.choice().label}`;
    let last = 0;
    const onText = (t) => { turn.answer += t; if (Date.now() - last > 120) { renderThread(); last = Date.now(); } };
    if (Connect.isDirect()) {
      await Connect.stream(answerSystem(turn.sources, language, got.overview), [...history, { role: "user", content: q }], onText, 1400);
      if (Connect.lastUsage) turn.usage = Connect.lastUsage;
    } else {
      const res = await fetch("/api/ask", { method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ phase: "answer", question: q, history, language, provider: conn.provider, model: Connect.requestModel(), overview: got.overview,
          sources: turn.sources.map((s) => ({ id: s.key, title: s.title, date: s.date, authors: s.authors, abstract: s.abstract })) }) });
      if (!res.ok || !res.body) { const j = await res.json().catch(() => ({})); throw new Error(j.error || `服务返回 ${res.status}`); }
      const used = res.headers.get("x-model");
      const u = res.headers.get("x-usage"); if (u) { const [i, o] = u.split("/").map(Number); turn.usage = { input: i, output: o }; }
      if (used && used !== "Claude Code") turn.provider = `${conn.label} · ${used}`;
      const reader = res.body.getReader(); const dec = new TextDecoder();
      for (;;) { const { done, value } = await reader.read(); if (done) break; onText(dec.decode(value, { stream: true })); }
    }
  } catch (e) {
    turn.error = e.message || String(e);
  } finally {
    delete turn.status; Ask.busy = false; saveAsk(); renderThread(); renderSide();
  }
}
function scrollToTurn() { const el = document.getElementById(`turn${Ask.turns.length - 1}`); el?.scrollIntoView({ behavior: "smooth", block: "start" }); }

async function viewAsk(hash) {
  setNav("ask");
  const conn = await askProviders();
  const pinned = new URLSearchParams(hash.split("?")[1] || "").get("paper");
  let pinTitle = "";
  if (pinned) { const sid = await Core.sidOfKey(pinned); if (sid !== null) pinTitle = (await Core.lite(sid)).title; }
  $view.innerHTML = `<div class="asklayout">
  <aside class="askside" id="askSide">
    <button class="btn primary" id="convNew" style="width:100%">＋ 新对话</button>
    <input id="convFilter" class="conv-filter" placeholder="搜索历史问答">
    <div id="convList" class="conv-list"></div>
    <div class="conv-note">历史只保存在这个浏览器里</div>
  </aside>
  <div class="askwrap">
    <div class="section-head"><h1>AI 问答</h1>
      <div class="row-actions">${conn ? `<span class="chip new">● ${esc(conn.label)}</span>
        <label class="modelpick" title="切换 Claude 模型">模型 <select id="askModel">${CLAUDE_CHOICES.map((m) => `<option value="${m.id}" ${m.id === Connect.choice().id ? "selected" : ""}>${m.label} · ${m.note}</option>`).join("")}</select></label>` : ""}
        <button class="btn small" id="askConn">${conn ? "更换模型" : "连接模型"}</button><button class="btn small" id="askClear">新对话</button><button class="btn small side-toggle" id="sideToggle">历史</button></div></div>
    ${conn ? "" : `<div class="ai" style="margin:10px 0"><b>先连接一个模型</b><p class="conn-desc" style="margin:6px 0 10px">可以用已登录的 Claude Code（Pro/Max 包月订阅），或者填写自己的 Claude API key（只保存在你的浏览器里）。</p><button class="btn primary" id="askConn2">连接 Claude</button></div>`}
    ${pinTitle ? `<div class="chip acc" style="margin:8px 0">围绕论文提问：${esc(pinTitle)}</div>` : ""}
    <div id="thread"></div>
    <form class="askbar" id="askForm"><textarea id="askInput" rows="2" placeholder="${pinTitle ? "例如：这篇论文的方法和之前的工作有什么不同？" : "输入科研问题，例如：多模态大模型的幻觉问题有哪些缓解方法？"}"></textarea>
      <button class="btn primary">提问</button></form></div></div>`;
  renderThread(); renderSide();
  document.getElementById("convNew").onclick = newConv;
  document.getElementById("convFilter").oninput = renderSide;
  document.getElementById("sideToggle").onclick = () => document.getElementById("askSide").classList.toggle("open");
  document.getElementById("askForm").onsubmit = (e) => { e.preventDefault(); askSubmit(); };
  document.getElementById("askInput").onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); askSubmit(); } };
  document.getElementById("askClear").onclick = newConv;
  document.getElementById("askConn").onclick = openConnect;
  const pick = document.getElementById("askModel");
  if (pick) pick.onchange = () => { Connect.setModel(pick.value); toast(`已切换到 ${Connect.choice().label}，下一个问题生效`); };
  document.getElementById("askConn2")?.addEventListener("click", openConnect);
  if (new URLSearchParams(hash.split("?")[1] || "").get("connect") === "1") { history.replaceState(null, "", "#/ask"); openConnect(); }
  const preset = new URLSearchParams(hash.split("?")[1] || "").get("q");
  if (preset && conn && Ask.turns[Ask.turns.length - 1]?.q !== preset) { if (Ask.turns.length) newConv(); document.getElementById("askInput").value = preset; askSubmit(); }
}
