"use strict";
// Literature review (local server only): topic -> outline the user can edit -> cited review
// with references and BibTeX. The work happens in airg/review.py (LangGraph); this page
// shows its steps, pauses at the outline, and keeps finished reviews in this browser.
const Review = {
  info: null,          // GET /api/review: {available, providers, reason}
  list: [],            // [{id, topic, title, status, steps, outline, result, error, created, years}]
  cur: null,
  busy: false,
};
const REVIEW_KEY = "airg.reviews.v1", MAX_REVIEWS = 30;
const RV_STEP = { scope: "规划", survey: "普查", outline: "大纲", approve: "确认", research: "检索", number: "编号", write: "撰写", synthesize: "综合", assemble: "整理" };
const RV_EXAMPLES = ["检索增强生成（RAG）", "大语言模型的强化学习推理（RLHF、GRPO 等）", "视觉-语言-动作模型（VLA）在机器人操作中的应用", "扩散模型视频生成", "LLM Agent 的评测基准"];

function rvLoad() {
  try { Review.list = JSON.parse(localStorage.getItem(REVIEW_KEY) || "[]"); } catch { Review.list = []; }
  // A page reload cuts an unfinished run off; one paused at the outline can still continue.
  Review.list.forEach((r) => { if (r.status === "running") { r.status = "error"; r.error = "页面刷新或关闭，生成被中断，请重新开始"; } });
}
function rvSave() {
  let list = Review.list.slice(0, MAX_REVIEWS);
  for (let tries = 0; tries < 5; tries++) {
    try { localStorage.setItem(REVIEW_KEY, JSON.stringify(list)); return; }
    catch { list = list.slice(0, Math.max(1, Math.floor(list.length * 0.7))); } // storage full: drop the oldest
  }
}
const rvCur = () => Review.list.find((r) => r.id === Review.cur) || null;
rvLoad();

// ---- server calls (NDJSON streams) ----------------------------------------------------
async function rvStream(url, body, r) {
  const res = await fetch(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  if (!res.ok || !res.body) { const j = await res.json().catch(() => ({})); throw new Error(j.error || `服务返回 ${res.status}`); }
  const reader = res.body.getReader(), dec = new TextDecoder();
  let buf = "", ended = false;
  const handle = (ev) => {
    if (ev.type === "step") { r.steps.push({ node: ev.node, text: ev.text }); r.statusText = ev.text; }
    else if (ev.type === "outline") { r.status = "outline"; r.outline = ev.outline; ended = true; }
    else if (ev.type === "result") { r.status = "done"; r.result = ev; r.title = ev.title || r.title; delete r.outline; ended = true; }
    else if (ev.type === "error") throw new Error(ev.error);
    rvRender();
  };
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n")) >= 0) { const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1); if (line) handle(JSON.parse(line)); }
  }
  if (buf.trim()) handle(JSON.parse(buf));
  if (!ended) throw new Error("连接中断，没有收到结果");
}
async function rvRun(r, url, body) {
  const conn = await askProviders();
  if (!conn) { openConnect(); return; }
  Review.busy = true; r.status = "running"; r.error = ""; r.statusText = "正在开始…"; rvSave(); rvRender();
  try {
    await rvStream(url, { ...body, thread_id: r.id, provider: conn.provider, model: Connect.requestModel() }, r);
    if (r.result) r.result.providerLabel = `${conn.label} · ${r.result.model && r.result.model !== "Claude Code" ? r.result.model : Connect.choice().label}`;
  } catch (e) {
    r.error = e.message || String(e);
    // A failed resume goes back to the outline (still paused on the server), so it can be retried.
    if (r.status === "running") r.status = r.outline ? "outline" : "error";
  } finally {
    Review.busy = false; delete r.statusText; rvSave(); rvRender(); rvSide();
  }
}
function rvStart() {
  if (Review.busy) { toast("请等当前综述完成"); return; }
  const topic = document.getElementById("rvTopic").value.trim();
  if (topic.length < 2) { toast("请输入综述主题"); return; }
  const years = document.getElementById("rvYears").value;
  const confirm = document.getElementById("rvConfirm").checked;
  const r = { id: "rv-" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6), topic, title: topic, status: "running", steps: [], created: Date.now(), years };
  Review.list.unshift(r); Review.cur = r.id; rvSide();
  rvRun(r, "/api/review", { topic, year_from: years ? Number(years) : 0, confirm });
}
function rvContinue() {
  const r = rvCur(); if (!r || Review.busy) return;
  const outline = rvReadOutline();
  if (!outline.sections.length) { toast("至少保留一节"); return; }
  r.outline = { ...r.outline, ...outline, sections: outline.sections.map((s) => ({ ...(r.outline.sections.find((o) => o.id === s.id) || {}), ...s })) };
  rvRun(r, "/api/review/resume", { outline });
}
function rvDelete(id) {
  if (Review.busy && id === Review.cur) return;
  fetch("/api/review/forget", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ thread_id: id }) }).catch(() => {});
  Review.list = Review.list.filter((r) => r.id !== id);
  if (Review.cur === id) Review.cur = null;
  rvSave(); rvRender(); rvSide();
}

// ---- outline editor -------------------------------------------------------------------
function rvReadOutline() {
  const secs = [...document.querySelectorAll("#rvOutline .rv-sec")].map((el) => ({
    ...(el.dataset.id !== "" ? { id: Number(el.dataset.id) } : {}),
    title: el.querySelector("[data-f=title]").value.trim(), focus: el.querySelector("[data-f=focus]").value.trim(),
  })).filter((s) => s.title);
  return { title: document.getElementById("rvTitle")?.value.trim() || "", sections: secs };
}
function rvSecHtml(s, i) {
  return `<div class="rv-sec" data-id="${s.id ?? ""}">
    <div class="rv-sec-head"><span class="rv-num">${i + 1}</span>
      <input class="inp" data-f="title" value="${esc(s.title)}" placeholder="这一节的标题">
      <button type="button" class="btn small" data-mv="-1" title="上移">↑</button><button type="button" class="btn small" data-mv="1" title="下移">↓</button>
      <button type="button" class="btn small" data-rm title="删除这一节">删除</button></div>
    <input class="inp rv-focus" data-f="focus" value="${esc(s.focus || "")}" placeholder="这一节写什么（一句话）">
    ${s.papers?.length ? `<details class="rv-papers"><summary>大纲阶段归入这一节的 ${s.papers.length} 篇论文（撰写前还会针对这一节再检索）</summary>
      ${s.papers.map((p) => `<div class="mini"><div><a href="${paperHref(p.key)}" target="_blank">${esc(p.title)}</a></div><span class="m">${esc(p.year)}</span></div>`).join("")}</details>`
    : `<div class="conn-desc rv-new">新增的一节：撰写前会按标题和描述检索论文</div>`}</div>`;
}
function rvOutlineHtml(o) {
  return `<div class="card rv-outline">
    <h3>确认大纲</h3>
    <p class="conn-desc">Agent 根据检索到的论文拟定了下面的结构。可以改标题、调整顺序、删除或新增一节，确认后开始针对每一节检索并撰写。</p>
    <label class="rv-label">综述标题 <input class="inp" id="rvTitle" value="${esc(o.title || "")}"></label>
    <div id="rvOutline">${o.sections.map(rvSecHtml).join("")}</div>
    <div class="row-actions" style="margin-top:10px"><button class="btn small" id="rvAdd">＋ 添加一节</button></div>
    <div class="row-actions" style="margin-top:14px"><button class="btn primary" id="rvGo">确认大纲，开始撰写</button>
      <span class="conn-desc">各节会同时撰写，通常需要 1～2 分钟</span></div></div>`;
}
function rvBindOutline() {
  const box = document.getElementById("rvOutline"); if (!box) return;
  const renumber = () => box.querySelectorAll(".rv-num").forEach((n, i) => (n.textContent = i + 1));
  box.onclick = (e) => {
    const sec = e.target.closest(".rv-sec"); if (!sec) return;
    if (e.target.matches("[data-rm]")) { if (box.children.length > 1) sec.remove(); else toast("至少保留一节"); }
    else if (e.target.matches("[data-mv]")) {
      const up = e.target.dataset.mv === "-1";
      if (up && sec.previousElementSibling) box.insertBefore(sec, sec.previousElementSibling);
      if (!up && sec.nextElementSibling) box.insertBefore(sec.nextElementSibling, sec);
    }
    renumber();
  };
  document.getElementById("rvAdd").onclick = () => {
    if (box.children.length >= 6) { toast("最多 6 节"); return; }
    box.insertAdjacentHTML("beforeend", rvSecHtml({ title: "", focus: "" }, box.children.length));
    renumber(); box.lastElementChild?.querySelector("[data-f=title]")?.focus();
  };
  document.getElementById("rvGo").onclick = rvContinue;
}

// ---- finished review ------------------------------------------------------------------
function rvBodyHtml(res) {
  const refs = res.references || [];
  return (res.body || "").split(/\n(?=## )/).map((part) => {
    const m = part.match(/^## (.*)\n?([\s\S]*)$/);
    return m ? `<h2>${esc(m[1])}</h2>${mdLite(m[2], refs)}` : mdLite(part, refs);
  }).join("");
}
function rvDoneHtml(r) {
  const res = r.result;
  const refs = res.references || [];
  return `<article class="card rv-doc">
    <h1>${esc(res.title || r.title)}</h1>
    <div class="conn-desc">主题：${esc(r.topic)}${r.years ? ` · ${esc(r.years)} 年以后` : ""} · 在全部 ${fmt(Core.manifest.papers)} 篇论文中检索，筛选 ${fmt(res.candidates)} 篇候选，阅读 ${fmt(res.papers)} 篇摘要，正文引用 ${refs.length} 篇</div>
    ${res.dirs?.length ? `<div class="chips" style="margin:8px 0 0"><span class="conn-desc">涉及方向：</span>${res.dirs.map((d) => `<a class="chip acc" href="${trendHref(d.id)}">${esc(d.name)}</a>`).join("")}</div>` : ""}
    <div class="row-actions rv-actions">
      <button class="btn small primary" data-x="md-dl">下载 Markdown</button><button class="btn small" data-x="md-copy">复制 Markdown</button>
      <button class="btn small" data-x="bib-dl">下载 BibTeX</button><button class="btn small" data-x="bib-copy">复制 BibTeX</button>
      <button class="btn small" data-x="save">收藏全部参考文献</button></div>
    <div class="rv-body">${rvBodyHtml(res)}</div>
    <h2>${res.language === "en" ? "References" : "参考文献"}</h2>
    <ol class="rv-refs">${refs.map((x) => `<li value="${x.n}"><span>${esc(String(x.authors || "").replace(/\.$/, ""))}.</span> <a href="${paperHref(x.key)}">${esc(x.title)}</a>. <span class="conn-desc">${esc(x.venue || "arXiv")}${/\d{4}$/.test(x.venue || "") ? "" : `, ${x.year}`}</span>
      ${x.url ? `<a class="conn-desc" href="${esc(x.url)}" target="_blank" rel="noopener">原文</a>` : ""}
      <button class="linkbtn ${Workspace.state.saved[x.key] ? "on" : ""}" data-save="${esc(x.key)}" data-title="${esc(x.title)}" data-year="${esc(x.year)}">${Workspace.state.saved[x.key] ? "★" : "☆"}</button></li>`).join("")}</ol>
    <div class="note" style="color:var(--faint);font-size:12px;margin-top:10px">由 ${esc(res.providerLabel || res.provider || "Claude")} 根据论文摘要撰写，适合作为调研起点；具体结论请以原文为准${res.usage ? ` · 本次约 ${fmt(res.usage.input)} 输入 + ${fmt(res.usage.output)} 输出 tokens` : ""}</div>
  </article>`;
}
function rvDownload(name, text, type) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type }));
  a.download = name; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}
function rvFileName(r) { return (r.result?.title || r.topic).replace(/[\\/:*?"<>|\s]+/g, "_").slice(0, 60) || "review"; }
function rvBindDone(r) {
  const res = r.result;
  document.querySelectorAll("[data-x]").forEach((b) => (b.onclick = () => {
    const x = b.dataset.x;
    if (x === "md-dl") rvDownload(`${rvFileName(r)}.md`, res.markdown, "text/markdown;charset=utf-8");
    else if (x === "bib-dl") rvDownload(`${rvFileName(r)}.bib`, res.bibtex, "application/x-bibtex;charset=utf-8");
    else if (x === "md-copy") navigator.clipboard?.writeText(res.markdown).then(() => toast("Markdown 已复制"));
    else if (x === "bib-copy") navigator.clipboard?.writeText(res.bibtex).then(() => toast(`已复制 ${res.references.length} 条 BibTeX`));
    else if (x === "save") {
      res.references.forEach((p) => { if (!Workspace.state.saved[p.key]) Workspace.toggleSaved(p.key, { title: p.title, year: p.year }); });
      updateBadge(); rvRender(); toast(`已收藏 ${res.references.length} 篇参考文献`);
    }
  }));
  bindSave(document.getElementById("rvMain"));
}

// ---- page -----------------------------------------------------------------------------
function rvStepsHtml(r, open) {
  if (!r.steps?.length) return "";
  return `<details class="srcs rv-steps" ${open ? "open" : ""}><summary>Agent 执行了 ${r.steps.length} 步</summary>
    ${r.steps.map((s) => `<div class="mini"><div><span class="reason">${esc(RV_STEP[s.node] || s.node)}</span>${esc(s.text)}</div></div>`).join("")}</details>`;
}
function rvFormHtml() {
  const y = new Date().getFullYear();
  return `<div class="card rv-form">
    <h3>输入综述主题</h3>
    <p class="conn-desc">Agent 会在站内 ${fmt(Core.manifest.papers)} 篇论文中检索，归纳出几条技术路线作为大纲，确认后分节阅读论文摘要并撰写，最后给出带编号引用的综述、参考文献和 BibTeX。</p>
    <textarea id="rvTopic" class="inp rv-topic" rows="2" placeholder="例如：多模态大模型的幻觉问题；或 retrieval-augmented generation"></textarea>
    <div class="row-actions" style="margin-top:10px;flex-wrap:wrap">
      <label class="modelpick">时间范围 <select id="rvYears"><option value="">全部年份</option>${[y - 3, y - 2, y - 1].map((v) => `<option value="${v}">${v} 年以后</option>`).join("")}</select></label>
      <label class="modelpick" title="先看 Agent 拟定的大纲，可以修改后再撰写"><input type="checkbox" id="rvConfirm" checked> 先确认大纲</label>
      <button class="btn primary" id="rvStart">生成综述</button></div>
    <div class="chips" style="margin-top:12px"><span class="conn-desc">试试：</span>${RV_EXAMPLES.map((e) => `<button class="chip acc" data-topic="${esc(e)}">${esc(e)}</button>`).join("")}</div></div>`;
}
function rvRender() {
  const main = document.getElementById("rvMain"); if (!main) return;
  const r = rvCur();
  if (!r) {
    main.innerHTML = rvFormHtml();
    document.getElementById("rvStart").onclick = rvStart;
    document.getElementById("rvTopic").onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); rvStart(); } };
    main.querySelectorAll("[data-topic]").forEach((b) => (b.onclick = () => { document.getElementById("rvTopic").value = b.dataset.topic; }));
    const preset = new URLSearchParams(location.hash.split("?")[1] || "").get("topic");
    if (preset) document.getElementById("rvTopic").value = preset;
    return;
  }
  let html = `<div class="rv-head"><div class="rv-topic-line">${esc(r.topic)}</div>
    <button class="btn small" id="rvDel" ${Review.busy && r.status === "running" ? "disabled" : ""}>删除</button></div>`;
  if (r.status === "running") html += `<div class="card"><div class="loading" style="padding:8px 0;text-align:left">${esc(r.statusText || "正在处理…")}</div>${rvStepsHtml(r, true)}</div>`;
  else if (r.status === "outline") html += (r.error ? `<div class="error" style="margin:8px 0">${esc(r.error)}</div>` : "") + rvOutlineHtml(r.outline) + rvStepsHtml(r, false);
  else if (r.status === "done") html += rvDoneHtml(r) + rvStepsHtml(r, false);
  else html += `<div class="error" style="margin:8px 0">${esc(r.error || "生成失败")}${/40[13]|key|未配置|调用失败/.test(r.error || "") ? ` <button class="btn small" data-reconnect>更换模型</button>` : ""}</div>
    <div class="row-actions"><button class="btn small primary" id="rvRetry">用同样的主题重新开始</button></div>${rvStepsHtml(r, true)}`;
  main.innerHTML = html;
  document.getElementById("rvDel").onclick = () => rvDelete(r.id);
  main.querySelectorAll("[data-reconnect]").forEach((b) => (b.onclick = openConnect));
  const retry = document.getElementById("rvRetry");
  if (retry) retry.onclick = () => { Review.cur = null; rvRender(); document.getElementById("rvTopic").value = r.topic; if (r.years) document.getElementById("rvYears").value = r.years; };
  if (r.status === "outline") rvBindOutline();
  if (r.status === "done") rvBindDone(r);
  rvSide(); // the sidebar shows each review's status
}
function rvSide() {
  const box = document.getElementById("rvList"); if (!box) return;
  box.innerHTML = Review.list.length ? Review.list.map((r) => `<div class="conv ${r.id === Review.cur ? "on" : ""}" data-rv="${r.id}" title="${esc(r.topic)}">
      <span class="conv-t">${esc(r.result?.title || r.title || r.topic)}</span><span class="conv-n">${{ running: "生成中", outline: "待确认", done: "", error: "失败" }[r.status] ?? ""}</span></div>`).join("")
    : `<div class="conv-empty">还没有综述</div>`;
  box.querySelectorAll("[data-rv]").forEach((el) => (el.onclick = () => {
    if (Review.busy && Review.cur !== el.dataset.rv && rvCur()?.status === "running") { toast("请等当前综述完成"); return; }
    Review.cur = el.dataset.rv; rvRender(); rvSide(); document.getElementById("rvSide")?.classList.remove("open");
  }));
}
async function viewReview(hash) {
  setNav("review");
  const conn = await askProviders();
  if (!Review.info) Review.info = await fetch("/api/review").then((x) => (x.ok ? x.json() : null)).catch(() => null);
  const ok = Review.info?.available && !Connect.isDirect();
  $view.innerHTML = `<div class="asklayout">
  <aside class="askside" id="rvSide">
    <button class="btn primary" id="rvNew" style="width:100%">＋ 新综述</button>
    <div id="rvList" class="conv-list" style="margin-top:10px"></div>
    <div class="conv-note">综述只保存在这个浏览器里</div>
  </aside>
  <div class="askwrap">
    <div class="section-head"><h1>文献综述</h1>
      <div class="row-actions">${conn ? `<span class="chip new">● ${esc(conn.label)}</span>
        <label class="modelpick" title="撰写各节和引言用的模型；规划用 Haiku">模型 <select id="rvModel">${CLAUDE_CHOICES.map((m) => `<option value="${m.id}" ${m.id === Connect.choice().id ? "selected" : ""}>${m.label} · ${m.note}</option>`).join("")}</select></label>` : ""}
        <button class="btn small" id="rvConn">${conn ? "更换模型" : "连接模型"}</button><button class="btn small side-toggle" id="rvSideToggle">历史</button></div></div>
    ${!ok ? `<div class="ai" style="margin:10px 0"><b>文献综述需要本地服务器</b><p class="conn-desc" style="margin:6px 0 0">${Connect.isDirect()
      ? "综述由服务器上的 Agent 多步完成，浏览器里填的 API key 用不了。请用「启动网站.bat」启动本地服务器，并连接已登录的 Claude Code。"
      : esc(Review.info?.reason || "请用「启动网站.bat」启动本地服务器（需要 Python 依赖：pip install -r requirements-agent.txt），并登录 Claude Code。")}</p></div>`
      : !conn ? `<div class="ai" style="margin:10px 0"><b>先连接一个模型</b><p class="conn-desc" style="margin:6px 0 10px">文献综述用已登录的 Claude Code（Pro/Max 包月订阅）或服务器上的 ANTHROPIC_API_KEY。</p><button class="btn primary" id="rvConn2">连接 Claude</button></div>` : ""}
    <div id="rvMain"></div></div></div>`;
  if (new URLSearchParams(hash.split("?")[1] || "").get("topic")) Review.cur = Review.busy ? Review.cur : null;
  if (ok) rvRender(); rvSide();
  document.getElementById("rvNew").onclick = () => { if (Review.busy && rvCur()?.status === "running") { toast("请等当前综述完成"); return; } Review.cur = null; rvRender(); rvSide(); document.getElementById("rvTopic")?.focus(); };
  document.getElementById("rvConn").onclick = openConnect;
  document.getElementById("rvConn2")?.addEventListener("click", openConnect);
  document.getElementById("rvSideToggle").onclick = () => document.getElementById("rvSide").classList.toggle("open");
  const pick = document.getElementById("rvModel");
  if (pick) pick.onchange = () => { Connect.setModel(pick.value); toast(`已切换到 ${Connect.choice().label}`); };
}
