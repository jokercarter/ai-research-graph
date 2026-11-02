"use strict";
const $view = document.getElementById("view");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (n) => Number(n || 0).toLocaleString("zh-CN");
let routeToken = 0;

function toast(msg) { const t = document.createElement("div"); t.className = "toast"; t.textContent = msg; document.body.appendChild(t); setTimeout(() => t.remove(), 1800); }
const paperHref = (key) => `#/paper/${encodeURIComponent(key)}`;
const trendHref = (id) => `#/trend/${id}`;
function spark(values, w = 240, h = 34) {
  if (!values || values.length < 2) return "";
  const max = Math.max(...values, 1); const step = w / (values.length - 1);
  const d = values.map((v, i) => `${i ? "L" : "M"}${(i * step).toFixed(1)},${(h - 2 - (v / max) * (h - 6)).toFixed(1)}`).join("");
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true"><path class="area" d="${d}L${w},${h}L0,${h}Z"/><path class="line" d="${d}"/></svg>`;
}
function setNav(name) { document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === name)); }
function updateBadge() { const n = Object.keys(Workspace.state.saved).length; const b = document.getElementById("libBadge"); b.hidden = !n; b.textContent = n; }
function growthLabel(t) { return t.base ? `${t.growth >= 1 ? "+" : ""}${Math.round((t.growth - 1) * 100)}%` : "新出现"; }
function dirName(t) { return t ? t.name : ""; }
function monthChart(monthly) {
  if (!monthly?.length) return "";
  const W = 900, H = 170, pad = 26; const max = Math.max(...monthly.map((m) => m[1]), 1); const bw = (W - pad * 2) / monthly.length;
  const bars = monthly.map(([m, v], i) => `<rect x="${pad + i * bw + 1}" y="${H - pad - (v / max) * (H - pad * 2)}" width="${Math.max(1, bw - 2)}" height="${(v / max) * (H - pad * 2)}" fill="#9db4ec"><title>${m}: ${v} 篇</title></rect>`).join("");
  const labels = monthly.map(([m], i) => (i % (monthly.length > 36 ? 6 : 4) === 0 ? `<text x="${pad + i * bw}" y="${H - 8}">${m}</text>` : "")).join("");
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${bars}${labels}<text x="${pad}" y="16">${max}</text></svg>`;
}

// ---------------------------------------------------------------------------- shared lists
function resultItem(l, hit) {
  const saved = !!Workspace.state.saved[l.key];
  return `<article class="result"><h3><a href="${paperHref(l.key)}">${esc(l.title)}</a></h3>
    <div class="byline">${esc(l.authors)} · ${esc(l.date)} · ${esc(Core.catName(l.cat))}</div>
    <div class="snippet">${esc(l.snippet)}</div>
    <div class="row-actions">${l.isNew ? '<span class="chip new">本月新增</span>' : ""}${hit?.semOnly ? '<span class="chip sem" title="摘要的意思与搜索内容相近，但不一定包含相同的词">语义相关</span>' : ""}
      <button class="linkbtn ${saved ? "on" : ""}" data-save="${esc(l.key)}" data-title="${esc(l.title)}" data-year="${l.year}">${saved ? "★ 已收藏" : "☆ 收藏"}</button>
      <a class="linkbtn" href="${paperHref(l.key)}">详情与相关论文 →</a>
      <a class="linkbtn" href="https://arxiv.org/pdf/${esc(l.key)}" target="_blank" rel="noopener">PDF</a></div></article>`;
}
function bindSave(root) {
  root.querySelectorAll("[data-save]").forEach((b) => (b.onclick = () => {
    const on = Workspace.toggleSaved(b.dataset.save, { title: b.dataset.title, year: Number(b.dataset.year) || 0 });
    b.classList.toggle("on", on); b.textContent = on ? "★ 已收藏" : "☆ 收藏"; updateBadge();
  }));
}
function trendCard(t, followed) {
  const ex = (t.latest && t.latest[0]) || (t.representative && t.representative[0]);
  return `<a class="card topic-card" href="${trendHref(t.id)}" style="color:inherit;text-decoration:none">
    <span class="kw">${esc(dirName(t))}</span>
    ${t.desc ? `<span class="rep">${esc(t.desc)}</span>` : `<span class="area">${esc((t.keywords || []).slice(0, 4).join(" · "))}</span>`}
    ${spark(t.monthly.slice(-12).map((m) => m[1]))}
    <span class="chips"><span class="chip ${t.growth >= 1.15 ? "hot" : ""}">占比 ${growthLabel(t)}</span><span class="chip">近 3 月 ${fmt(t.recent)} 篇</span>${followed ? '<span class="chip acc">已关注</span>' : ""}</span>
    ${ex ? `<span class="rep">例：${esc(ex[1])}</span>` : ""}</a>`;
}

// ---------------------------------------------------------------------------- home
async function viewHome() {
  setNav("");
  const m = Core.manifest; const T = await Core.getTrends();
  const followed = Object.keys(Workspace.state.topics).map(Number).map((id) => T.byId.get(id)).filter(Boolean);
  const examples = ["大语言模型的推理能力", "diffusion model video generation", "视觉语言模型的对齐方法", "vision-language-action robot", "联邦学习 隐私"];
  $view.innerHTML = `
  <section class="hero">
    <h1>找到一个 AI 方向的关键论文与最新进展</h1>
    <p>收录 ${fmt(m.papers)} 篇近 5 年的 arXiv AI 论文（${esc(m.window[0])} 至 ${esc(m.window[1])}），每月更新；${esc(m.new_month)} 新增 ${fmt(m.new_papers)} 篇</p>
    <form class="bigsearch" id="heroSearch"><input id="heroQuery" type="search" placeholder="输入论文标题、方法名或研究问题（支持中文）" autofocus><button class="btn primary">搜索</button></form>
    <div class="examples">试试：${examples.map((e) => `<a href="#/search?q=${encodeURIComponent(e)}">${esc(e)}</a>`).join("")}</div>
  </section>
  ${followed.length ? `<section class="section"><div class="section-head"><h2>我关注的方向</h2></div><div class="grid">${followed.map((t) => trendCard(t, true)).join("")}</div></section>` : ""}
  <section class="section"><div class="section-head"><h2>正在升温的研究方向</h2><p>根据摘要内容把论文归入研究方向，按 ${esc(T.window.recent[0])} 至 ${esc(T.window.recent[1])} 的论文占比增长排序 · <a href="#/topics">查看全部 ${T.directions.length} 个方向</a></p></div>
    <div class="grid">${T.directions.filter((t) => t.recent >= 40).slice(0, 12).map((t) => trendCard(t, !!Workspace.state.topics[t.id])).join("")}</div></section>
  <section class="section"><div class="section-head"><h2>按领域看最新论文</h2></div>
    <div class="chips">${T.categories.filter((c) => c.total).sort((a, b) => b.total - a.total).map((c) => `<a class="chip acc" href="#/category/${encodeURIComponent(c.id)}">${esc(c.name)} · ${fmt(c.total)}</a>`).join("")}</div></section>`;
  document.getElementById("heroSearch").onsubmit = (e) => { e.preventDefault(); go(document.getElementById("heroQuery").value); };
}

// ---------------------------------------------------------------------------- search
function parseQuery(hash) {
  const qs = new URLSearchParams(hash.split("?")[1] || "");
  return { q: qs.get("q") || "", y0: Number(qs.get("y0")) || 0, y1: Number(qs.get("y1")) || 0, cat: qs.has("cat") ? Number(qs.get("cat")) : null, dir: qs.has("dir") ? Number(qs.get("dir")) : null,
    sort: qs.get("sort") || "relevance", page: Math.max(1, Number(qs.get("page")) || 1) };
}
function searchHref(p) {
  const qs = new URLSearchParams(); qs.set("q", p.q);
  if (p.y0) qs.set("y0", p.y0); if (p.y1) qs.set("y1", p.y1); if (p.cat !== null && p.cat !== undefined) qs.set("cat", p.cat);
  if (p.dir !== null && p.dir !== undefined) qs.set("dir", p.dir);
  if (p.sort !== "relevance") qs.set("sort", p.sort); if (p.page > 1) qs.set("page", p.page);
  return `#/search?${qs}`;
}
function go(q) { q = (q || "").trim(); if (q) location.hash = searchHref({ q, y0: 0, y1: 0, cat: null, dir: null, sort: "relevance", page: 1 }); }

async function viewSearch(hash, token) {
  setNav("");
  const p = parseQuery(hash);
  document.getElementById("topQuery").value = p.q;
  if (!p.q) { location.hash = "#/"; return; }
  Workspace.addHistory(p.q);
  $view.innerHTML = `<div class="loading">正在检索…</div>`;
  const rw = await Core.rewrite(p.q);
  const [res, T] = await Promise.all([Core.fuzzySearch(rw.english, { yearFrom: p.y0 || 0, yearTo: p.y1 || 9999, cat: p.cat, dir: p.dir, sort: p.sort }), Core.getTrends()]);
  if (token !== routeToken) return;
  const per = 20; const pageHits = res.hits.slice((p.page - 1) * per, p.page * per);
  const lites = await Promise.all(pageHits.map((h) => Core.lite(h.sid)));
  if (token !== routeToken) return;
  const catCount = new Map(); res.hits.slice(0, 400).forEach((h) => catCount.set(h.cat, (catCount.get(h.cat) || 0) + 1));
  const dirCount = new Map(); res.hits.slice(0, 300).forEach((h) => { if (h.dir !== 255) dirCount.set(h.dir, (dirCount.get(h.dir) || 0) + 1); });
  const dirList = [...dirCount].sort((x, y) => y[1] - x[1]).slice(0, 8);
  if (p.dir !== null && !dirCount.has(p.dir)) dirList.unshift([p.dir, 0]);
  const cats = Core.manifest.categories;
  const years = []; for (let y = Number(Core.manifest.window[0].slice(0, 4)); y <= Number(Core.manifest.window[1].slice(0, 4)); y++) years.push(y);
  const yopt = (sel) => `<option value="0">不限</option>` + years.map((y) => `<option ${y === sel ? "selected" : ""}>${y}</option>`).join("");
  $view.innerHTML = `
  <div class="layout">
    <aside class="side">
      <div><h3>排序</h3><select id="fSort"><option value="relevance">相关度</option><option value="recent">最新发表</option></select></div>
      <div><h3>年份</h3><div class="yearrow"><select id="fY0">${yopt(p.y0)}</select>–<select id="fY1">${yopt(p.y1)}</select></div></div>
      ${dirList.length ? `<div><h3>研究方向</h3>
        <label><input type="radio" name="dir" value="" ${p.dir === null ? "checked" : ""}> 全部</label>
        ${dirList.map(([d, n]) => `<label><input type="radio" name="dir" value="${d}" ${p.dir === d ? "checked" : ""}> ${esc(dirName(T.byId.get(d)) || d)}${n ? ` <span class="m">(${n})</span>` : ""}</label>`).join("")}</div>` : ""}
      <div><h3>领域（arXiv 主分类）</h3>
        <label><input type="radio" name="cat" value="" ${p.cat === null ? "checked" : ""}> 全部</label>
        ${[...catCount].sort((a, b) => b[1] - a[1]).map(([c, n]) => `<label><input type="radio" name="cat" value="${c}" ${p.cat === c ? "checked" : ""}> ${esc(Core.catName(cats[c]))} <span class="m">(${n})</span></label>`).join("")}
      </div>
    </aside>
    <section>
      <div class="result-meta">约 ${fmt(Math.max(res.total, res.keywordTotal))} 篇相关论文 · 按相关度综合关键词与语义排序${rw.via !== "none" ? ` · 检索词：<b>${esc(res.query)}</b>${rw.via === "local" ? "（本地词典翻译）" : "（AI 翻译）"}` : ""}</div>
      ${res.fixes.length ? `<div class="result-meta">已自动纠正拼写：${res.fixes.map(([a, b]) => `<s>${esc(a)}</s> → <b>${esc(b)}</b>`).join("，")}</div>` : ""}
      <div class="ai" id="aiBox"><div class="ai-head"><strong>AI 研究助手</strong><a class="btn small primary" href="#/ask?q=${encodeURIComponent(p.q)}">用 AI 问答深入</a>
        ${[["review", "梳理综述"], ["related", "找相关论文"], ["compare", "比较方法"], ["gaps", "找研究空白"], ["frontier", "找前沿"]].map(([k, t]) => `<button class="btn small" data-mode="${k}">${t}</button>`).join("")}</div>
        <div id="aiOut" class="note">基于前 30 篇检索结果的摘要生成，结论会标注出处论文（需要网站配置 AI 服务）。</div></div>
      <div id="results">${lites.length ? lites.map((l, i) => resultItem(l, pageHits[i])).join("") : `<div class="empty">没有找到匹配的论文。可以换个说法，或用英文关键词试试。</div>`}</div>
      <div class="pager">${p.page > 1 ? `<a class="btn" href="${searchHref({ ...p, page: p.page - 1 })}">上一页</a>` : ""}
        ${res.hits.length > p.page * per ? `<a class="btn" href="${searchHref({ ...p, page: p.page + 1 })}">下一页</a>` : ""}</div>
    </section>
  </div>`;
  document.getElementById("fSort").value = p.sort;
  const apply = (patch) => { location.hash = searchHref({ ...p, page: 1, ...patch }); };
  document.getElementById("fSort").onchange = (e) => apply({ sort: e.target.value });
  document.getElementById("fY0").onchange = (e) => apply({ y0: Number(e.target.value) });
  document.getElementById("fY1").onchange = (e) => apply({ y1: Number(e.target.value) });
  document.querySelectorAll("input[name=cat]").forEach((r) => (r.onchange = () => apply({ cat: r.value === "" ? null : Number(r.value) })));
  document.querySelectorAll("input[name=dir]").forEach((r) => (r.onchange = () => apply({ dir: r.value === "" ? null : Number(r.value) })));
  bindSave($view);
  document.querySelectorAll("[data-mode]").forEach((b) => (b.onclick = () => runAI(p.q, b.dataset.mode, res.hits.slice(0, 30))));
}
async function runAI(query, mode, hits) {
  const out = document.getElementById("aiOut");
  out.className = ""; out.innerHTML = `<div class="loading" style="padding:8px 0">AI 正在阅读检索结果…</div>`;
  const lites = await Promise.all(hits.map((h) => Core.lite(h.sid)));
  const byKey = new Map(lites.map((l, i) => [l.key, i]));
  const candidates = lites.map((l) => ({ id: l.key, title: l.title, year: l.year, venue: "arXiv", authors: l.authors.split(", ").slice(0, 3), topics: [Core.catName(l.cat)], citations_count: 0, abstract: l.snippet }));
  try {
    const res = await fetch("/api/ai-search", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ query, mode, candidates, limit: 8 }) });
    const j = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(j.error || `服务返回 ${res.status}`);
    const cite = (ids) => (ids || []).filter((id) => byKey.has(id)).slice(0, 4).map((id) => `<a class="cite" href="${paperHref(id)}" title="${esc(lites[byKey.get(id)].title)}">[${byKey.get(id) + 1}]</a>`).join("");
    out.innerHTML = `<p class="summary">${esc(j.answer?.summary || "")}${cite(j.answer?.evidence_ids)}</p>
      ${(j.answer?.key_points || []).length ? `<ul>${j.answer.key_points.map((k) => `<li>${esc(k.text)}${cite(k.evidence_ids)}</li>`).join("")}</ul>` : ""}
      ${(j.view?.gaps || []).length ? `<p><b>可能的研究空白（需核实）</b></p><ul>${j.view.gaps.map((g) => `<li>${esc(g)}</li>`).join("")}</ul>` : ""}
      <div class="note">方括号数字对应相关度排序的第几篇（只使用摘要片段，请以原文为准）。</div>`;
  } catch (e) { out.className = "note"; out.textContent = `AI 总结暂不可用（${e.message}）。`; }
}

// ---------------------------------------------------------------------------- category listing
async function viewCategory(catId, hash, token) {
  setNav("");
  const idx = Core.manifest.categories.indexOf(catId);
  const page = Math.max(1, Number(new URLSearchParams(hash.split("?")[1] || "").get("page")) || 1);
  const [lo, hi] = await Core.categoryRange(idx);
  const per = 20; const from = lo + (page - 1) * per; const to = Math.min(hi, from + per);
  const lites = await Promise.all(Array.from({ length: Math.max(0, to - from) }, (_, i) => Core.lite(from + i)));
  if (token !== routeToken) return;
  $view.innerHTML = `<h1>${esc(Core.catName(catId))} · 最新论文</h1><p class="result-meta">arXiv 主分类 ${esc(catId)}，共 ${fmt(hi - lo)} 篇，按发表时间从新到旧</p>
    ${lites.map(resultItem).join("")}
    <div class="pager">${page > 1 ? `<a class="btn" href="#/category/${catId}?page=${page - 1}">上一页</a>` : ""}${to < hi ? `<a class="btn" href="#/category/${catId}?page=${page + 1}">下一页</a>` : ""}</div>`;
  bindSave($view);
}

// ---------------------------------------------------------------------------- paper
async function viewPaper(key, token) {
  setNav("");
  $view.innerHTML = `<div class="loading">正在加载论文…</div>`;
  const sid = await Core.sidOfKey(key);
  if (sid === null) { $view.innerHTML = `<div class="error">当前快照里没有这篇论文（${esc(key)}）。<a href="https://arxiv.org/abs/${esc(key)}" target="_blank" rel="noopener">在 arXiv 上查看</a></div>`; return; }
  const [d, dirs, T] = await Promise.all([Core.detail(sid), Core.getDirs(), Core.getTrends()]);
  if (token !== routeToken) return;
  const myDirs = Core.dirsOf(dirs, sid).map((id) => T.byId.get(id)).filter(Boolean);
  const saved = !!Workspace.state.saved[d.ax];
  document.title = `${d.t} · AI Research Graph`;
  $view.innerHTML = `
  <div class="paper-grid">
    <div>
      <div class="paper-head"><h1>${esc(d.t)}</h1>
        <div class="byline">${esc(d.au.join(", "))}${d.na > d.au.length ? ` 等 ${d.na} 人` : ""}</div>
        ${myDirs.length ? `<div class="chips" style="margin-top:8px"><span class="conn-desc">研究方向：</span>${myDirs.map((t) => `<a class="chip acc" href="${trendHref(t.id)}">${esc(dirName(t))}</a>`).join("")}</div>` : ""}
        <div class="chips" style="margin-top:8px">${d.cat.map((c) => `<span class="chip">${esc(Core.catName(c))}</span>`).join("")}${d.d.slice(0, 7) === Core.manifest.new_month ? '<span class="chip new">本月新增</span>' : ""}</div></div>
      <div class="row-actions" style="margin:14px 0">
        <button class="btn ${saved ? "on" : ""}" id="bSave">${saved ? "★ 已收藏" : "☆ 收藏"}</button>
        <button class="btn" id="bBib">复制 BibTeX</button>
        <a class="btn" href="https://arxiv.org/pdf/${esc(d.ax)}" target="_blank" rel="noopener">打开 PDF</a>
        <a class="btn primary" href="#/ask?paper=${encodeURIComponent(d.ax)}">问 AI 关于这篇论文</a></div>
      <h2>摘要</h2><div class="abstract">${esc(d.ab)}</div>
      <h2 style="margin-top:26px">相关论文</h2><p class="note" style="color:var(--muted);font-size:13px">按标题和摘要关键词在库内检索出的最接近的论文。</p>
      <div id="rel"><div class="loading">正在查找相关论文…</div></div>
    </div>
    <aside class="card">
      <div class="kv"><span>首次发布</span><b>${esc(d.d)}</b>${d.u && d.u !== d.d ? `<span>最近更新</span><b>${esc(d.u)}</b>` : ""}
        <span>arXiv</span><b>${esc(d.ax)}</b>${d.jr ? `<span>发表于</span><b>${esc(d.jr)}</b>` : ""}${d.doi ? `<span>DOI</span><b style="word-break:break-all">${esc(d.doi)}</b>` : ""}
        ${d.cm ? `<span>备注</span><b>${esc(d.cm)}</b>` : ""}</div>
      <h3 style="margin-top:14px">外部链接</h3><div class="chips">
        <a class="chip acc" href="https://arxiv.org/abs/${esc(d.ax)}" target="_blank" rel="noopener">arXiv</a>
        <a class="chip acc" href="https://www.semanticscholar.org/arxiv/${esc(d.ax)}" target="_blank" rel="noopener">Semantic Scholar（引用）</a>
        <a class="chip acc" href="https://scholar.google.com/scholar?q=${encodeURIComponent(d.t)}" target="_blank" rel="noopener">Google Scholar</a>
        ${d.doi ? `<a class="chip acc" href="https://doi.org/${esc(d.doi)}" target="_blank" rel="noopener">DOI</a>` : ""}</div>
    </aside>
  </div>`;
  document.getElementById("bSave").onclick = (e) => { const on = Workspace.toggleSaved(d.ax, { title: d.t, year: Number(d.d.slice(0, 4)), au: d.au.slice(0, 8), ax: d.ax, doi: d.doi }); e.target.classList.toggle("on", on); e.target.textContent = on ? "★ 已收藏" : "☆ 收藏"; updateBadge(); };
  document.getElementById("bBib").onclick = () => { navigator.clipboard?.writeText(bibtex({ title: d.t, year: d.d.slice(0, 4), au: d.au, ax: d.ax, doi: d.doi })); toast("BibTeX 已复制"); };
  // Related papers: closest abstracts in meaning (falls back to shared title/abstract words).
  const relHits = await Core.similar(sid, `${d.t} ${d.t} ${d.ab.slice(0, 400)}`, 10);
  if (token !== routeToken) return;
  const rel = await Promise.all(relHits.slice(0, 10).map((h) => Core.lite(h.sid)));
  if (token !== routeToken) return;
  document.getElementById("rel").innerHTML = rel.length ? rel.map((l) => `<div class="mini"><a href="${paperHref(l.key)}">${esc(l.title)}</a><span class="m">${esc(l.date)} · ${esc(Core.catName(l.cat))}</span></div>`).join("") : '<div class="empty">没有找到相关论文</div>';
}
function bibtex(p) {
  const first = (p.au?.[0] || "anon").split(" ").pop().toLowerCase().replace(/[^a-z]/g, "");
  const word = (p.title || "").toLowerCase().match(/[a-z]{4,}/)?.[0] || "paper";
  const lines = [`  title = {${p.title}}`, p.au?.length ? `  author = {${p.au.join(" and ")}}` : null, p.year ? `  year = {${p.year}}` : null,
    p.ax ? `  eprint = {${p.ax}},\n  archivePrefix = {arXiv}` : null, p.doi ? `  doi = {${p.doi}}` : null].filter(Boolean);
  return `@misc{${first}${p.year || ""}${word},\n${lines.join(",\n")}\n}`;
}

// ---------------------------------------------------------------------------- trends
async function viewTopics(hash) {
  setNav("topics");
  const T = await Core.getTrends();
  const qs = new URLSearchParams((hash || "").split("?")[1] || "");
  const sort = qs.get("sort") || "score";
  const cat = qs.get("cat") || "";
  const keyOf = { score: (t) => t.score, growth: (t) => t.growth, recent: (t) => t.recent, size: (t) => t.size };
  const list = T.directions.filter((t) => !cat || t.main_category === cat).sort((x, y) => (keyOf[sort] || keyOf.score)(y) - (keyOf[sort] || keyOf.score)(x));
  // Only arXiv categories with a Chinese name get a filter chip; others are rare main categories of a direction.
  const cats = [...new Set(T.directions.map((t) => t.main_category).filter((c) => c && Core.manifest.category_names[c]))];
  const href = (patch) => { const p = new URLSearchParams({ sort, ...(cat ? { cat } : {}), ...patch }); if (!p.get("cat")) p.delete("cat"); return `#/topics?${p}`; };
  $view.innerHTML = `<h1>研究方向与升温趋势</h1>
  <p style="color:var(--muted)">根据每篇论文<b>标题和摘要的语义内容</b>，把 ${fmt(Core.manifest.papers)} 篇论文归入 ${T.directions.length} 个研究方向（一篇论文可属于 1–3 个方向）。"占比增长"比较 <b>${esc(T.window.recent[0])} 至 ${esc(T.window.recent[1])}</b> 与之前一年（${esc(T.window.base[0])} 至 ${esc(T.window.base[1])}）该方向论文在全部论文中的占比，已扣除论文总量的整体增长。</p>
  <div class="areabar"><a class="chip ${cat ? "" : "acc"}" href="${href({ cat: "" })}">全部领域</a>${cats.map((c) => `<a class="chip ${c === cat ? "acc" : ""}" href="${href({ cat: c })}">${esc(Core.catName(c))}</a>`).join("")}</div>
  <table class="tt"><thead><tr><th>研究方向</th><th data-s="recent">近 3 月</th><th class="hide-m">之前一年</th><th data-s="growth">占比增长</th><th data-s="size" class="hide-m">总数</th><th class="hide-m">近 12 个月</th></tr></thead>
  <tbody>${list.map((t) => `<tr><td><a href="${trendHref(t.id)}"><b>${esc(dirName(t))}</b></a>${Workspace.state.topics[t.id] ? ' <span class="chip acc">已关注</span>' : ""}
      <div class="conn-desc">${esc(t.desc || (t.keywords || []).slice(0, 5).join(" · "))}</div></td>
    <td class="num">${fmt(t.recent)}</td><td class="num hide-m">${fmt(t.base)}</td><td class="num">${growthLabel(t)}</td><td class="num hide-m">${fmt(t.size)}</td>
    <td class="hide-m" style="width:150px">${spark(t.monthly.slice(-12).map((m) => m[1]), 150, 26)}</td></tr>`).join("")}</tbody></table>`;
  document.querySelectorAll("th[data-s]").forEach((th) => (th.onclick = () => (location.hash = href({ sort: th.dataset.s }))));
}
async function viewTrend(id) {
  setNav("topics");
  const T = await Core.getTrends(); const t = T.byId.get(id);
  if (!t) { $view.innerHTML = `<div class="error">没有这个研究方向。</div>`; return; }
  const followed = !!Workspace.state.topics[id];
  document.title = `${dirName(t)} · AI Research Graph`;
  const list = (cards) => (cards || []).map((c) => `<div class="mini"><a href="${paperHref(c[3])}">${esc(c[1])}</a><span class="m">${esc(c[2])} · ${esc(Core.catName(c[4]))}</span></div>`).join("") || '<div class="empty">暂无</div>';
  $view.innerHTML = `<div class="byline"><a href="#/topics">研究方向</a>${t.main_category ? ` · ${esc(Core.catName(t.main_category))}` : ""}</div>
  <h1>${esc(dirName(t))}</h1>${t.name_en ? `<div class="byline" style="margin:-4px 0 6px">${esc(t.name_en)}</div>` : ""}
  ${t.desc ? `<p>${esc(t.desc)}</p>` : ""}
  <div class="chips" style="margin:6px 0 12px">${(t.keywords || []).map((k) => `<a class="chip acc" href="#/search?q=${encodeURIComponent(k)}">${esc(k)}</a>`).join("")}</div>
  <div class="row-actions"><button class="btn ${followed ? "on" : ""}" id="bFollow">${followed ? "已关注" : "关注这个方向"}</button>
    <a class="btn primary" href="#/search?q=${encodeURIComponent((t.keywords || []).slice(0, 3).join(" "))}&dir=${t.id}&sort=recent">查看这个方向的全部论文</a>
    <a class="btn" href="#/ask?q=${encodeURIComponent(`${dirName(t)}方向最近有哪些主要进展和代表性方法？`)}">问 AI 这个方向的进展</a></div>
  <div class="metric-row"><div class="metric"><b>${fmt(t.recent)}</b><span>近 3 个月论文</span></div>
    <div class="metric"><b>${fmt(t.base)}</b><span>之前一年</span></div><div class="metric"><b>${growthLabel(t)}</b><span>论文占比变化</span></div>
    <div class="metric"><b>${fmt(t.size)}</b><span>收录期内总计</span></div></div>
  <h3 style="margin-top:14px">每月论文数</h3>${monthChart(t.monthly)}
  <div class="cols" style="margin-top:20px">
    <section><h2>最新论文</h2><p class="conn-desc">近 3 个月内容上最贴近这个方向的论文</p>${list(t.latest)}</section>
    <section><h2>最典型的论文</h2><p class="conn-desc">内容最接近这个方向中心的论文，适合快速了解它在研究什么</p>${list(t.representative)}</section></div>`;
  document.getElementById("bFollow").onclick = (e) => { const on = Workspace.toggleTopic(id, { name: dirName(t) }); e.target.classList.toggle("on", on); e.target.textContent = on ? "已关注" : "关注这个方向"; toast(on ? "已关注，会显示在首页" : "已取消关注"); };
}

// ---------------------------------------------------------------------------- library / about
async function viewLibrary() {
  setNav("library");
  const W = Workspace.state; const T = await Core.getTrends();
  const saved = Object.entries(W.saved).sort((a, b) => b[1].at - a[1].at);
  const topics = Object.keys(W.topics).map(Number).map((id) => T.byId.get(id)).filter(Boolean);
  $view.innerHTML = `<h1>我的文献</h1><p style="color:var(--muted)">收藏和关注只保存在这个浏览器里，不需要登录。换电脑前可以先备份。</p>
  <div class="row-actions" style="margin-bottom:16px"><button class="btn" id="exBib">导出 BibTeX</button><button class="btn" id="exCsv">导出 CSV</button>
    <button class="btn" id="exJson">备份工作区</button><label class="btn">导入备份<input type="file" id="imJson" accept=".json" hidden></label></div>
  <div class="cols"><section><h2>收藏的论文 (${saved.length})</h2>${saved.length ? saved.map(([k, p]) => `<div class="mini"><a href="${paperHref(k)}">${esc(p.title)}</a><span class="m">${p.year || ""} <button class="linkbtn" data-un="${esc(k)}">移除</button></span></div>`).join("") : '<div class="empty">还没有收藏。</div>'}</section>
    <section><h2>关注的方向 (${topics.length})</h2>${topics.length ? topics.map((t) => `<div class="mini"><a href="${trendHref(t.id)}">${esc(dirName(t))}</a><span class="m">近 3 月 ${fmt(t.recent)} 篇</span></div>`).join("") : '<div class="empty">还没有关注的方向。</div>'}</section></div>`;
  $view.querySelectorAll("[data-un]").forEach((b) => (b.onclick = () => { delete W.saved[b.dataset.un]; Workspace.save(); updateBadge(); viewLibrary(); }));
  const dl = (name, text, type) => { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([text], { type })); a.download = name; a.click(); };
  document.getElementById("exBib").onclick = () => dl("ai-research-graph.bib", saved.map(([, p]) => bibtex(p)).join("\n\n"), "text/plain");
  document.getElementById("exCsv").onclick = () => dl("ai-research-graph.csv", "﻿arxiv_id,title,year\n" + saved.map(([k, p]) => [k, p.title, p.year].map((x) => `"${String(x ?? "").replace(/"/g, '""')}"`).join(",")).join("\n"), "text/csv");
  document.getElementById("exJson").onclick = () => dl("ai-research-graph-workspace.json", JSON.stringify(W, null, 1), "application/json");
  document.getElementById("imJson").onchange = async (e) => { try { Object.assign(W, JSON.parse(await e.target.files[0].text())); Workspace.save(); updateBadge(); viewLibrary(); toast("已导入"); } catch { toast("文件格式不正确"); } };
}
function viewAbout() {
  setNav("about");
  const m = Core.manifest;
  $view.innerHTML = `<div class="prose"><h1>数据说明</h1>
  <p>当前快照：<b>${esc(m.version)}</b>，每月更新一次。收录 ${esc(m.window[0])} 至 ${esc(m.window[1])} 在 arXiv 首次发布、主分类或交叉分类属于 AI 相关方向（cs.AI、cs.LG、cs.CL、cs.CV、cs.RO、cs.IR、cs.MA、cs.NE、stat.ML 等）的论文，共 ${fmt(m.papers)} 篇。</p>
  <h2>这是简化版</h2><ul><li>只收录近 5 年（2021-12 起）的 arXiv 论文；没有上传 arXiv 的会议/期刊论文不在库里。</li><li>暂不显示被引数和引用关系，可以通过论文页的 Semantic Scholar 链接查看。</li>
    <li>"相关论文"按标题和摘要关键词检索得出，还没有使用语义向量和引用关系。</li><li>"研究方向"由论文标题和摘要的语义向量（bge-base 模型）聚类得到，每篇论文按内容相似度归入 1–3 个方向；"升温"指该方向近 3 个月的论文占比相对之前一年上升，反映关注度变化，不代表质量判断。</li></ul>
  <h2>数据来源</h2><ul>${m.sources.map((s) => `<li><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.name)}</a>：${esc(s.use)}</li>`).join("")}</ul>
  <h2>隐私</h2><p>搜索在你的浏览器里完成；只有使用 AI 研究助手或中文检索时，问题和候选论文的标题、摘要片段会经服务器发送给 AI 模型。收藏和关注只保存在本地浏览器。</p></div>`;
}

// ---------------------------------------------------------------------------- router
async function route() {
  const token = ++routeToken;
  const hash = location.hash || "#/";
  const path = hash.slice(1).split("?")[0];
  window.scrollTo(0, 0);
  try {
    if (path === "/" || path === "") await viewHome();
    else if (path === "/search") await viewSearch(hash, token);
    else if (path.startsWith("/paper/")) await viewPaper(decodeURIComponent(path.slice(7)), token);
    else if (path.startsWith("/category/")) await viewCategory(decodeURIComponent(path.slice(10)), hash, token);
    else if (path === "/topics") await viewTopics(hash);
    else if (path.startsWith("/trend/")) await viewTrend(Number(path.slice(7)));
    else if (path === "/ask") await viewAsk(hash);
    else if (path === "/library") await viewLibrary();
    else if (path === "/about") viewAbout();
    else $view.innerHTML = `<div class="empty">页面不存在。<a href="#/">回到首页</a></div>`;
    if (!path.startsWith("/paper/") && !path.startsWith("/trend/")) document.title = "AI Research Graph · AI/CS 论文搜索与前沿方向";
  } catch (e) {
    console.error(e);
    if (token === routeToken) $view.innerHTML = `<div class="error">加载出错：${esc(e.message)}。请刷新重试。</div>`;
  }
}
// Run after every deferred script (connect.js, ask.js) has been executed.
// Deferred scripts run while readyState is "interactive", before DOMContentLoaded fires.
const whenReady = new Promise((resolve) => (document.readyState === "complete" ? resolve() : document.addEventListener("DOMContentLoaded", resolve, { once: true })));
(async function start() {
  await whenReady;
  try { await Core.init(); } catch (e) { $view.innerHTML = `<div class="error">${esc(e.message)}</div>`; return; }
  document.getElementById("foot").innerHTML = `数据截至 ${esc(Core.manifest.version)} · 每月更新 · 来源 arXiv · <a href="#/about">数据说明</a>`;
  document.getElementById("topSearch").onsubmit = (e) => { e.preventDefault(); go(document.getElementById("topQuery").value); };
  updateBadge();
  window.addEventListener("hashchange", route);
  route();
})();
