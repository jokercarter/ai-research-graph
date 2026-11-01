// Data access, search and local workspace for the AI Research Graph site.
// Data is static and sharded (see data/pipeline/build_site.py); the browser only
// fetches the shards a page needs and keeps them in memory.
"use strict";

const DATA = "data/";
const cache = new Map();

async function gunzipJSON(res) {
  const buf = await res.arrayBuffer();
  const bytes = new Uint8Array(buf);
  // Some hosts already decode the gzip; detect the magic bytes.
  if (bytes[0] === 0x1f && bytes[1] === 0x8b) {
    const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
    return new Response(stream).arrayBuffer();
  }
  return buf;
}

function load(path, kind = "json") {
  if (cache.has(path)) return cache.get(path);
  const p = (async () => {
    const res = await fetch(DATA + path + (path.endsWith(".gz") ? "" : "") + `?v=${encodeURIComponent(Core.version || "")}`);
    if (!res.ok) throw new Error(`加载失败：${path} (${res.status})`);
    if (!path.endsWith(".gz")) return kind === "json" ? res.json() : res.arrayBuffer();
    const buf = await gunzipJSON(res);
    return kind === "json" ? JSON.parse(new TextDecoder().decode(buf)) : buf;
  })();
  p.catch(() => cache.delete(path));
  cache.set(path, p);
  return p;
}

// ---- tokenizer: must match data/pipeline/textutil.py --------------------------------
const STOP = new Set(`a an the and or of for in on to with by from at as is are be been being this that these those it its into
over under via we our us they their them can may might will would should could than then thus also such which who whom
whose what when where while how not no nor but if so do does did done have has had having more most less least very
using use based towards toward without within across between among about each per all any both other some only own same
further here there up down out off again once i you he she his her paper propose proposed approach method results show`.split(/\s+/));

function stem(w) {
  if (w.length > 4 && w.endsWith("ies")) return w.slice(0, -3) + "y";
  if (w.length > 4 && w.endsWith("es") && ("sxz".includes(w[w.length - 3]) || ["ch", "sh"].includes(w.slice(-4, -2)))) return w.slice(0, -2);
  if (w.length > 3 && w.endsWith("s") && !w.endsWith("ss") && !w.endsWith("us") && !w.endsWith("is")) return w.slice(0, -1);
  return w;
}
function tokens(text) {
  const out = [];
  for (const w of String(text || "").toLowerCase().match(/[a-z0-9]+/g) || []) {
    if (STOP.has(w) || w.length < 2 || (/^\d+$/.test(w) && w.length < 4)) continue;
    out.push(stem(w));
  }
  return out;
}
function fnv1a(s) {
  let h = 0x811c9dc5;
  for (const b of new TextEncoder().encode(s)) { h ^= b; h = Math.imul(h, 0x01000193) >>> 0; }
  return h >>> 0;
}
const hasCJK = (s) => /[㐀-鿿]/.test(s || "");

// Chinese research terms -> English search terms (used when the AI rewrite is unavailable).
const ZH_TERMS = [
  ["多模态大模型|多模态大语言模型|MLLM", "multimodal large language model"], ["大语言模型|(?<!多模态)大模型|LLM", "large language model"], ["语言模型", "language model"], ["多模态", "multimodal"],
  ["视觉语言模型|视觉-语言|图文", "vision language model"], ["对齐", "alignment"], ["人类反馈|RLHF", "human feedback reinforcement learning"],
  ["强化学习", "reinforcement learning"], ["离线强化学习", "offline reinforcement learning"], ["多智能体", "multi-agent"],
  ["智能体|代理", "agent"], ["工具调用|工具使用", "tool use"], ["检索增强|RAG", "retrieval augmented generation"],
  ["扩散模型", "diffusion model"], ["生成模型", "generative model"], ["图像生成", "image generation"], ["视频生成", "video generation"],
  ["文本生成图像|文生图", "text-to-image"], ["目标检测", "object detection"], ["语义分割", "semantic segmentation"], ["分割", "segmentation"],
  ["图像分类", "image classification"], ["三维|3D", "3d"], ["重建", "reconstruction"], ["神经辐射场|NeRF", "neural radiance field"],
  ["高斯泼溅|高斯溅射", "gaussian splatting"], ["自动驾驶", "autonomous driving"], ["机器人", "robot"], ["具身", "embodied"],
  ["抓取", "grasping"], ["操作|操控", "manipulation"], ["导航", "navigation"], ["联邦学习", "federated learning"], ["隐私", "privacy"],
  ["差分隐私", "differential privacy"], ["通信效率", "communication efficient"], ["安全", "safety"], ["对抗攻击|对抗样本", "adversarial attack"],
  ["鲁棒", "robustness"], ["可解释", "interpretability"], ["幻觉", "hallucination"], ["推理", "reasoning"], ["思维链", "chain of thought"],
  ["数学推理", "mathematical reasoning"], ["代码生成", "code generation"], ["程序合成", "program synthesis"], ["知识图谱", "knowledge graph"],
  ["图神经网络|GNN", "graph neural network"], ["推荐系统|推荐", "recommendation"], ["信息检索|检索", "retrieval"], ["问答", "question answering"],
  ["机器翻译|翻译", "machine translation"], ["语音识别", "speech recognition"], ["语音合成", "speech synthesis"], ["情感分析", "sentiment analysis"],
  ["摘要", "summarization"], ["对话", "dialogue"], ["预训练", "pretraining"], ["微调", "fine-tuning"], ["参数高效|LoRA", "parameter efficient fine-tuning"],
  ["提示|提示词", "prompt"], ["上下文学习", "in-context learning"], ["指令微调", "instruction tuning"], ["蒸馏|知识蒸馏", "distillation"],
  ["量化", "quantization"], ["剪枝", "pruning"], ["压缩", "compression"], ["推理加速|加速", "inference acceleration"], ["分布式训练", "distributed training"],
  ["注意力", "attention"], ["变换器|Transformer", "transformer"], ["状态空间|Mamba", "state space model"], ["混合专家|MoE", "mixture of experts"],
  ["对比学习", "contrastive learning"], ["自监督", "self-supervised"], ["半监督", "semi-supervised"], ["无监督", "unsupervised"],
  ["小样本|少样本", "few-shot"], ["零样本", "zero-shot"], ["迁移学习", "transfer learning"], ["领域自适应|域适应", "domain adaptation"],
  ["持续学习|终身学习", "continual learning"], ["元学习", "meta-learning"], ["主动学习", "active learning"], ["优化", "optimization"],
  ["泛化", "generalization"], ["不确定性", "uncertainty"], ["因果", "causal"], ["时间序列", "time series"], ["异常检测", "anomaly detection"],
  ["医学影像|医疗影像", "medical image"], ["医疗|医学", "medical"], ["药物", "drug discovery"], ["蛋白质", "protein"], ["科学发现", "scientific discovery"],
  ["评测|基准", "benchmark"], ["数据集", "dataset"], ["水印", "watermark"], ["版权", "copyright"], ["公平", "fairness"], ["偏见", "bias"],
  ["视觉问答", "visual question answering"], ["视频理解", "video understanding"], ["动作识别", "action recognition"], ["姿态估计", "pose estimation"],
  ["人脸", "face"], ["超分辨率", "super resolution"], ["去噪", "denoising"], ["点云", "point cloud"], ["SLAM|定位与建图", "slam"],
  ["神经网络", "neural network"], ["深度学习", "deep learning"], ["机器学习", "machine learning"], ["计算机视觉", "computer vision"],
  ["自然语言处理", "natural language processing"], ["世界模型", "world model"], ["规划", "planning"], ["模仿学习", "imitation learning"],
];
function localRewrite(q) {
  const terms = [];
  for (const [pat, en] of ZH_TERMS) if (new RegExp(pat, "i").test(q)) terms.push(en);
  const latin = (q.match(/[A-Za-z][A-Za-z0-9\-]+/g) || []).join(" ");
  return [...new Set([...terms, latin].filter(Boolean))].join(" ");
}

// ---- workspace (this browser only) -------------------------------------------------
const WS_KEY = "airg.workspace.v2";
const Workspace = {
  state: { saved: {}, topics: {}, papers: {}, history: [], lastVersion: "" },
  load() {
    try { Object.assign(this.state, JSON.parse(localStorage.getItem(WS_KEY) || "{}")); } catch { /* storage unavailable */ }
  },
  save() { try { localStorage.setItem(WS_KEY, JSON.stringify(this.state)); } catch { /* storage unavailable */ } },
  toggleSaved(key, info) { if (this.state.saved[key]) delete this.state.saved[key]; else this.state.saved[key] = { ...info, at: Date.now() }; this.save(); return !!this.state.saved[key]; },
  toggleTopic(id, info) { if (this.state.topics[id]) delete this.state.topics[id]; else this.state.topics[id] = { ...info, since: Core.version }; this.save(); return !!this.state.topics[id]; },
  togglePaper(key, info) { if (this.state.papers[key]) delete this.state.papers[key]; else this.state.papers[key] = { ...info, since: Core.version }; this.save(); return !!this.state.papers[key]; },
  addHistory(q) { const h = this.state.history.filter((x) => x !== q); h.unshift(q); this.state.history = h.slice(0, 12); this.save(); },
};

// ---- core API -----------------------------------------------------------------------
const Core = {
  version: "",
  manifest: null,
  meta: null,
  trends: null,
  async init() {
    const res = await fetch(DATA + "manifest.json", { cache: "no-cache" });
    if (!res.ok) throw new Error("数据清单加载失败");
    this.manifest = await res.json();
    this.version = this.manifest.version;
    Workspace.load();
    return this.manifest;
  },
  catName(c) { return this.manifest.category_names[c] || c; },
  async getTrends() {
    if (!this.trends) { const t = await load("trends.json.gz"); t.byId = new Map(t.trends.map((x) => [x.id, x])); this.trends = t; }
    return this.trends;
  },
  async getMeta() {
    if (!this.meta) this.meta = new Uint8Array(await load("meta.bin.gz", "bin"));
    return this.meta;
  },
  N() { return this.meta ? this.meta.length / 4 : this.manifest.papers; },
  year(m, sid) { return m[sid * 4] + this.manifest.year0; },
  ym(m, sid) { return (m[sid * 4] + this.manifest.year0) * 100 + m[sid * 4 + 1]; },
  cat(m, sid) { return m[sid * 4 + 2]; },
  isNew(m, sid) { return m[sid * 4 + 3] === 1; },
  async lite(sid) {
    const per = this.manifest.lite_per;
    const block = await load(`l/${String(Math.floor(sid / per)).padStart(4, "0")}.json.gz`);
    const r = block[sid % per];
    return { sid, title: r[0], date: r[1], year: Number(r[1].slice(0, 4)), cat: r[2], authors: r[3], snippet: r[4], key: r[5],
      isNew: r[1].slice(0, 7) === this.manifest.new_month };
  },
  async detail(sid) {
    const per = this.manifest.detail_per;
    const block = await load(`p/${String(Math.floor(sid / per)).padStart(4, "0")}.json.gz`);
    return { sid, ...block[sid % per] };
  },
  async sidOfKey(key) {
    const shard = (fnv1a(key) % this.manifest.key_shards).toString(16).padStart(2, "0");
    const map = await load(`k/${shard}.json.gz`);
    return map[key] ?? null;
  },
  async rewrite(query) {
    if (!hasCJK(query)) return { english: query, via: "none" };
    if (window.Connect?.isDirect()) {
      try {
        const text = await Connect.complete("You convert research questions into English academic search keywords for an AI/CS paper index. Output strict JSON only.",
          [{ role: "user", content: `Question: ${query}\nReturn {"english": "<6-14 English keywords/phrases as they appear in paper titles and abstracts, space separated>"}` }], 200);
        const j = JSON.parse(text.slice(text.indexOf("{"), text.lastIndexOf("}") + 1));
        const english = String(j.english || "").replace(/[^\w\s\-]/g, " ").replace(/\s+/g, " ").trim();
        if (english) return { english, via: "ai" };
      } catch { /* fall back below */ }
    }
    try {
      const res = await fetch("/api/query-rewrite", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ query }) });
      if (res.ok) { const j = await res.json(); if (j.english) return { english: j.english, via: "ai" }; }
    } catch { /* fall back to the local dictionary */ }
    return { english: localRewrite(query), via: "local" };
  },
  // Papers of one arXiv category are stored contiguously, newest first.
  async categoryRange(catIdx) {
    const m = await this.getMeta(); const n = this.N();
    let lo = -1, hi = -1;
    for (let i = 0; i < n; i++) { if (m[i * 4 + 2] === catIdx) { if (lo < 0) lo = i; hi = i; } else if (lo >= 0) break; }
    return lo < 0 ? [0, 0] : [lo, hi + 1];
  },
  async search(english, { yearFrom = 0, yearTo = 9999, cat = null, sort = "relevance", exclude = -1, limit = 400 } = {}) {
    const uni = tokens(english);
    const uniq = [...new Set(uni)];
    const bis = [...new Set(uni.slice(1).map((w, i) => `${uni[i]} ${w}`))];
    const terms = [...uniq.map((t) => [t, 1]), ...bis.map((t) => [t, 1.6])].slice(0, 24);
    if (!terms.length) return { hits: [], total: 0 };
    const meta = await this.getMeta();
    const shards = await Promise.all(terms.map(([t]) => load(`t/${String(fnv1a(t) % this.manifest.term_shards).padStart(4, "0")}.json.gz`).catch(() => ({}))));
    const score = new Map(); const matched = new Map();
    terms.forEach(([t, w], idx) => {
      const p = shards[idx][t];
      if (!p) return;
      let sid = 0;
      for (let i = 1; i < p.length; i += 2) {
        sid += p[i];
        score.set(sid, (score.get(sid) || 0) + p[i + 1] * w);
        if (w === 1) matched.set(sid, (matched.get(sid) || 0) + 1);
      }
    });
    const nUni = uniq.length; const newest = this.ym(meta, 0) ? 0 : 0;
    const hits = [];
    for (const [sid, s] of score) {
      if (sid === exclude) continue;
      const y = this.year(meta, sid);
      if (y < yearFrom || y > yearTo) continue;
      if (cat !== null && this.cat(meta, sid) !== cat) continue;
      const cover = nUni ? (matched.get(sid) || 0) / nUni : 1;
      hits.push({ sid, score: s * (0.3 + 0.7 * cover * cover), ym: this.ym(meta, sid), cat: this.cat(meta, sid) });
    }
    hits.sort((a, b) => b.score - a.score);
    let top = hits.slice(0, limit);
    if (sort === "recent") top = top.slice(0, 200).sort((a, b) => b.ym - a.ym || b.score - a.score);
    return { hits: top, total: hits.length };
  },
};

window.Core = Core;
window.Workspace = Workspace;
window.CoreUtil = { tokens, fnv1a, hasCJK, localRewrite };
