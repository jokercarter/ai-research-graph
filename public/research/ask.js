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
const PLAN_SYSTEM = `You help researchers search an index of AI/CS arXiv papers (2024-11 to 2026-10). The index matches English words in titles and abstracts.
Given the conversation and the latest question, output strict JSON only:
{"queries": ["<2-4 English keyword queries, 3-8 words each, using the technical terms papers would use; cover different angles of the question>"], "language": "zh" | "en"}`;
function answerSystem(sources, lang) {
  const list = sources.map((s) => `[${s.n}] ${s.title} (${s.date || "?"}; ${s.authors || ""})\n${s.abstract || "(no abstract)"}`).join("\n\n");
  return `You are a research assistant on an AI/CS paper search website. Answer the researcher's question using ONLY the papers listed below (retrieved from the site's index for this question).
Rules:
- Cite papers inline as [n] right after the claim they support; cite only numbers from the list. Never invent papers, numbers, results or authors.
- If the listed papers do not cover part of the question, say so plainly and suggest what to search for next.
- Abstracts are summaries; say "根据摘要" when a detail may need checking in the full paper.
- Structure: a direct answer first, then grouped findings (methods / representative works / open problems as fits), then 2-3 suggested follow-up questions on a final line starting with "延伸问题：" separated by "｜".
- Write in ${lang === "en" ? "English" : "Simplified Chinese"}; keep paper titles and technical terms in English. Use Markdown headings sparingly and bullet lists where helpful. Keep it under about 600 words.

Papers:
${list}`;
}
try { Object.assign(Ask, { turns: JSON.parse(localStorage.getItem(ASK_KEY) || "[]") }); } catch { /* storage unavailable */ }
function saveAsk() { try { localStorage.setItem(ASK_KEY, JSON.stringify(Ask.turns.slice(-8))); } catch { /* ignore */ } }

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
    <div class="a">${t.answer ? mdLite(t.answer, t.sources) : t.error ? "" : `<div class="loading" style="padding:6px 0">${esc(t.status || "正在思考…")}</div>`}
      ${t.error ? `<div class="error" style="margin-top:8px">${esc(t.error)}${/40[13]|key|未配置|调用失败/.test(t.error) ? ` <button class="btn small" data-reconnect>更换模型</button>` : ""}</div>` : ""}
      ${fu.length ? `<div class="chips" style="margin-top:10px">${fu.map((f) => `<button class="chip acc" data-follow="${esc(f)}">${esc(f)}</button>`).join("")}</div>` : ""}</div>
    ${t.sources?.length ? `<details class="srcs" ${i === Ask.turns.length - 1 ? "open" : ""}><summary>参考的 ${t.sources.length} 篇论文${t.queries?.length ? ` · 检索词：${esc(t.queries.join(" / "))}` : ""}</summary>
      ${t.sources.map((s) => `<div class="mini"><div><span class="reason">[${s.n}]</span><a href="${paperHref(s.key)}">${esc(s.title)}</a></div>
        <span class="m">${esc(s.date)} <button class="linkbtn ${Workspace.state.saved[s.key] ? "on" : ""}" data-save="${esc(s.key)}" data-title="${esc(s.title)}" data-year="${esc(String(s.date).slice(0, 4))}">${Workspace.state.saved[s.key] ? "★" : "☆"}</button></span></div>`).join("")}
      <div class="row-actions" style="margin-top:8px"><button class="btn small" data-saveall="${i}">全部收藏</button></div></details>` : ""}
    ${t.provider && t.answer ? `<div class="note" style="color:var(--faint);font-size:12px">由 ${esc(t.provider)} 根据检索到的论文摘要生成，请以原文为准</div>` : ""}
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
    Ask.turns[Number(b.dataset.saveall)].sources.forEach((s) => { if (!Workspace.state.saved[s.key]) Workspace.toggleSaved(s.key, { title: s.title, year: Number(String(s.date).slice(0, 4)) }); });
    updateBadge(); renderThread(); toast("已收藏全部参考论文");
  }));
}

async function retrieve(queries, pinnedKey) {
  // Reciprocal-rank fusion over several queries, then full abstracts for the best papers.
  const score = new Map();
  const results = await Promise.all(queries.map((q) => Core.search(q, { limit: 60 })));
  results.forEach((r) => r.hits.forEach((h, rank) => score.set(h.sid, (score.get(h.sid) || 0) + 1 / (12 + rank))));
  let sids = [...score].sort((a, b) => b[1] - a[1]).map(([sid]) => sid);
  if (pinnedKey) {
    const pin = await Core.sidOfKey(pinnedKey);
    if (pin !== null) sids = [pin, ...sids.filter((s) => s !== pin)];
  }
  sids = sids.slice(0, 14);
  const details = await Promise.all(sids.map((sid) => Core.detail(sid)));
  return details.map((d, i) => ({ n: i + 1, key: d.ax, title: d.t, date: d.d, authors: d.au.slice(0, 4).join(", ") + (d.na > 4 ? " et al." : ""), abstract: d.ab, cat: d.cat[0] }));
}

async function askSubmit() {
  const input = document.getElementById("askInput");
  const q = input.value.trim();
  if (!q || Ask.busy) return;
  const conn = await askProviders();
  if (!conn) { openConnect(); return; }
  Ask.busy = true; input.value = "";
  const pinned = new URLSearchParams(location.hash.split("?")[1] || "").get("paper");
  const history = Ask.turns.slice(-3).flatMap((t) => [{ role: "user", content: t.q }, { role: "assistant", content: (t.answer || "").slice(0, 1500) }]).filter((m) => m.content);
  const turn = { q, answer: "", sources: [], queries: [], status: "正在理解问题、生成检索词…" };
  Ask.turns.push(turn); renderThread(); scrollToTurn();
  try {
    let language = CoreUtil.hasCJK(q) ? "zh" : "en";
    try {
      if (Connect.isDirect()) {
        const text = await Connect.complete(PLAN_SYSTEM, [...history, { role: "user", content: q }], 300);
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
    turn.sources = await retrieve(turn.queries, pinned);
    if (!turn.sources.length) throw new Error("没有检索到相关论文，换个说法试试");
    turn.status = `正在阅读 ${turn.sources.length} 篇论文并撰写回答…`; renderThread();
    turn.provider = `${conn.label} · ${Connect.choice().label}`;
    let last = 0;
    const onText = (t) => { turn.answer += t; if (Date.now() - last > 120) { renderThread(); last = Date.now(); } };
    if (Connect.isDirect()) {
      await Connect.stream(answerSystem(turn.sources, language), [...history, { role: "user", content: q }], onText);
    } else {
      const res = await fetch("/api/ask", { method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ phase: "answer", question: q, history, language, provider: conn.provider, model: Connect.requestModel(),
          sources: turn.sources.map((s) => ({ id: s.key, title: s.title, date: s.date, authors: s.authors, abstract: s.abstract })) }) });
      if (!res.ok || !res.body) { const j = await res.json().catch(() => ({})); throw new Error(j.error || `服务返回 ${res.status}`); }
      const used = res.headers.get("x-model");
      if (used && used !== "Claude Code") turn.provider = `${conn.label} · ${used}`;
      const reader = res.body.getReader(); const dec = new TextDecoder();
      for (;;) { const { done, value } = await reader.read(); if (done) break; onText(dec.decode(value, { stream: true })); }
    }
  } catch (e) {
    turn.error = e.message || String(e);
  } finally {
    delete turn.status; Ask.busy = false; saveAsk(); renderThread();
  }
}
function scrollToTurn() { const el = document.getElementById(`turn${Ask.turns.length - 1}`); el?.scrollIntoView({ behavior: "smooth", block: "start" }); }

async function viewAsk(hash) {
  setNav("ask");
  const conn = await askProviders();
  const pinned = new URLSearchParams(hash.split("?")[1] || "").get("paper");
  let pinTitle = "";
  if (pinned) { const sid = await Core.sidOfKey(pinned); if (sid !== null) pinTitle = (await Core.lite(sid)).title; }
  $view.innerHTML = `<div class="askwrap">
    <div class="section-head"><h1>AI 问答</h1>
      <div class="row-actions">${conn ? `<span class="chip new">● ${esc(conn.label)}</span>
        <label class="modelpick" title="切换 Claude 模型">模型 <select id="askModel">${CLAUDE_CHOICES.map((m) => `<option value="${m.id}" ${m.id === Connect.choice().id ? "selected" : ""}>${m.label} · ${m.note}</option>`).join("")}</select></label>` : ""}
        <button class="btn small" id="askConn">${conn ? "更换模型" : "连接模型"}</button><button class="btn small" id="askClear">新对话</button></div></div>
    ${conn ? "" : `<div class="ai" style="margin:10px 0"><b>先连接一个模型</b><p class="conn-desc" style="margin:6px 0 10px">可以用已登录的 Claude Code（Pro/Max 包月订阅），或者填写自己的 Claude API key（只保存在你的浏览器里）。</p><button class="btn primary" id="askConn2">连接 Claude</button></div>`}
    ${pinTitle ? `<div class="chip acc" style="margin:8px 0">围绕论文提问：${esc(pinTitle)}</div>` : ""}
    <div id="thread"></div>
    <form class="askbar" id="askForm"><textarea id="askInput" rows="2" placeholder="${pinTitle ? "例如：这篇论文的方法和之前的工作有什么不同？" : "输入科研问题，例如：多模态大模型的幻觉问题有哪些缓解方法？"}"></textarea>
      <button class="btn primary">提问</button></form></div>`;
  renderThread();
  document.getElementById("askForm").onsubmit = (e) => { e.preventDefault(); askSubmit(); };
  document.getElementById("askInput").onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); askSubmit(); } };
  document.getElementById("askClear").onclick = () => { if (Ask.busy) return; Ask.turns = []; saveAsk(); renderThread(); };
  document.getElementById("askConn").onclick = openConnect;
  const pick = document.getElementById("askModel");
  if (pick) pick.onchange = () => { Connect.setModel(pick.value); toast(`已切换到 ${Connect.choice().label}，下一个问题生效`); };
  document.getElementById("askConn2")?.addEventListener("click", openConnect);
  if (new URLSearchParams(hash.split("?")[1] || "").get("connect") === "1") { history.replaceState(null, "", "#/ask"); openConnect(); }
  const preset = new URLSearchParams(hash.split("?")[1] || "").get("q");
  if (preset && conn && Ask.turns[Ask.turns.length - 1]?.q !== preset) { document.getElementById("askInput").value = preset; askSubmit(); }
}
