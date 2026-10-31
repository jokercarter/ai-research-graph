/* AI Research Graph static prototype. Replace data.sample.json with the
 * output of the ingestion pipeline; the graph schema is intentionally small. */

const FALLBACK_DATA = {
  nodes: [
    { id: "fallback-1", title: "Attention Is All You Need", year: 2017, venue: "NeurIPS", authors: ["Ashish Vaswani", "Noam Shazeer"], topics: ["NLP", "Architecture"], abstract: "A compact fallback graph is shown when the data snapshot cannot be loaded.", url: "https://arxiv.org/abs/1706.03762" },
    { id: "fallback-2", title: "BERT: Pre-training of Deep Bidirectional Transformers", year: 2018, venue: "NAACL", authors: ["Jacob Devlin", "Ming-Wei Chang"], topics: ["NLP", "Pretraining"], abstract: "The sample data file can be replaced with real OpenAlex records.", url: "https://arxiv.org/abs/1810.04805" }
  ],
  edges: [{ source: "fallback-2", target: "fallback-1", kind: "citation" }]
};

const DOMAIN_DEFINITIONS = [
  { id: "language", label: "语言与文本", color: "#73d8f5", terms: ["language", "natural language", "nlp", "text", "speech", "translation", "sentiment", "question answering", "large language", "llm", "bert"] },
  { id: "vision", label: "计算机视觉", color: "#9a8cff", terms: ["vision", "image", "visual", "video", "object detection", "segmentation", "recognition", "cnn", "scene", "3d reconstruction"] },
  { id: "generative", label: "生成模型", color: "#f3a86b", terms: ["generative", "diffusion", "gan", "image synthesis", "generation", "generative ai", "denoising"] },
  { id: "reinforcement", label: "强化学习与控制", color: "#f5d267", terms: ["reinforcement", "policy", "reward", "q-learning", "actor critic", "control", "markov decision"] },
  { id: "robotics", label: "机器人与智能体", color: "#78e6ba", terms: ["robot", "robotics", "autonomous", "navigation", "manipulation", "drone", "embodied", "agent"] },
  { id: "learning", label: "机器学习方法", color: "#8aa8ff", terms: ["machine learning", "deep learning", "neural network", "representation", "classification", "few-shot", "optimization", "attention", "learning"] },
  { id: "data", label: "数据、检索与知识", color: "#d39bff", terms: ["data mining", "information retrieval", "recommender", "database", "knowledge graph", "search", "ranking", "query"] },
  { id: "systems", label: "系统与网络", color: "#6fc1aa", terms: ["computer systems", "distributed", "network", "software", "hardware", "operating system", "cloud", "compiler"] },
  { id: "security", label: "安全与隐私", color: "#ff8f9c", terms: ["security", "privacy", "adversarial", "cyber", "cryptography", "attack", "robustness"] },
  { id: "theory", label: "理论与算法", color: "#c2d08a", terms: ["theory", "algorithm", "computational", "complexity", "mathematical", "formal", "optimization"] },
  { id: "science", label: "科学与生命", color: "#e0a6c8", terms: ["medical", "biomedical", "bioinformatics", "protein", "genomics", "health", "drug", "chemistry"] },
  { id: "other", label: "其他 CS", color: "#8693a9", terms: [] }
];

function classifyDomain(node) {
  const text = [node.title, node.venue, ...(node.topics || [])].join(" ").toLowerCase();
  let best = DOMAIN_DEFINITIONS[DOMAIN_DEFINITIONS.length - 1];
  let bestScore = 0;
  DOMAIN_DEFINITIONS.slice(0, -1).forEach((domain) => {
    const score = domain.terms.reduce((sum, term) => sum + (text.includes(term) ? (term.length > 8 ? 2 : 1) : 0), 0);
    if (score > bestScore) { best = domain; bestScore = score; }
  });
  return best.id;
}

const state = {
  data: { nodes: [], edges: [] },
  snapshot: null,
  stats: { nodes: 0, edges: 0, topics: 0 },
  filteredNodes: [],
  filteredEdges: [],
  selectedId: null,
  neighborhoodId: null,
  neighborhoodDepth: 1,
  activeTopics: new Set(),
  yearMin: 2016,
  yearMax: 2026,
  showExternal: true,
  showLabels: true,
  renderLimit: 100,
  searchMatchIds: new Set(),
  search: "",
  zoom: null,
  simulation: null,
  renderNodes: [],
  frontierIds: new Set(),
  frontierAwardIds: new Set(),
  viewMode: "overview",
  activeDomainId: null,
  selectedDomainId: null,
  domainSummaries: [],
  domainEdges: [],
  renderedViewMode: null,
  width: 0,
  height: 0
};

const $ = (id) => document.getElementById(id);
const svg = d3.select("#graph");
const graphPanel = $("graphPanel");
const linkLayer = svg.append("g").attr("class", "links");
const nodeLayer = svg.append("g").attr("class", "nodes");
const labelLayer = svg.append("g").attr("class", "labels");

function normalizeData(raw) {
  const normalizeAuthors = (authors) => (authors || []).map((author) => {
    if (typeof author === "string") return author;
    return author?.name || author?.author?.display_name || author?.display_name || "";
  }).filter(Boolean);
  const normalizeTopics = (topics) => (topics || []).map((topic) => {
    if (typeof topic === "string") return topic;
    return topic?.display_name || topic?.name || "";
  }).filter(Boolean);
  const nodes = (raw.nodes || []).map((node) => {
    const normalized = {
      ...node,
      id: String(node.id),
      year: Number(node.year ?? node.publication_year) || 2016,
      title: node.title || "Untitled paper",
      venue: node.venue || node.primary_topic?.display_name || node.source || "OpenAlex",
      topics: normalizeTopics(node.topics),
      authors: normalizeAuthors(node.authors),
      abstract: node.abstract || "",
      citations_count: Number(node.citations_count ?? node.cited_by_count ?? 0),
      url: node.url || node.open_access_url || node.landing_page_url || node.doi || (node.openalex_id ? `https://openalex.org/${String(node.openalex_id).split("/").pop()}` : ""),
      external: Boolean(node.external ?? node.is_external_reference)
    };
    normalized.domainId = node.domainId || node.domain_id || classifyDomain(normalized);
    return normalized;
  });
  const known = new Set(nodes.map((node) => node.id));
  const edges = (raw.edges || []).map((edge) => ({ ...edge, source: String(edge.source), target: String(edge.target) })).filter((edge) => known.has(edge.source) && known.has(edge.target));
  return { nodes, edges };
}

async function fetchSnapshot() {
  const response = await fetch("data.sample.json.gz", { cache: "no-store" });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const bytes = new Uint8Array(await response.arrayBuffer());
  let text = new TextDecoder().decode(bytes);
  // Sites serves the gzip asset as a normal static file. If a proxy has
  // already decompressed it, the JSON bytes are parsed directly instead.
  if (bytes[0] === 0x1f && bytes[1] === 0x8b) {
    if (!("DecompressionStream" in window)) throw new Error("浏览器不支持 gzip 解压");
    const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
    text = await new Response(stream).text();
  }
  return JSON.parse(text);
}

async function loadData() {
  try {
    const raw = await fetchSnapshot();
    state.snapshot = raw;
    state.data = normalizeData(raw);
    state.stats = datasetStats(raw, state.data);
    $("syncStatus").textContent = `${raw.provenance?.provider || "OpenAlex"} · ${formatCount(state.stats.nodes)} 节点 / ${formatCount(state.stats.edges)} 条边`;
  } catch (error) {
    state.snapshot = null;
    state.data = normalizeData(FALLBACK_DATA);
    state.stats = datasetStats(null, state.data);
    $("syncStatus").textContent = "使用内置演示数据";
  }
  updateDatasetStats();
  const years = state.data.nodes.map((node) => node.year);
  // Keep the default research window at the requested ten-year horizon.
  // The slider may still expose older external foundation nodes when present.
  state.yearMin = 2016;
  state.yearMax = Math.max(2026, ...years);
  setupFilters();
  applyFilters();
  $("loadingState").hidden = true;
}

function datasetStats(raw, data) {
  const stats = raw?.stats || {};
  const topicCount = new Set(data.nodes.flatMap((node) => node.topics || [])).size;
  return {
    nodes: Number(stats.nodes ?? stats.papers ?? stats.total_nodes ?? data.nodes.length) || data.nodes.length,
    edges: Number(stats.edges ?? stats.citations ?? stats.total_edges ?? data.edges.length) || data.edges.length,
    topics: Number(stats.topics ?? stats.topic_count ?? stats.total_topics ?? topicCount) || topicCount,
    provider: raw?.provenance?.provider || "OpenAlex",
    generatedAt: raw?.provenance?.generated_at || raw?.generated_at || "",
    coverage: raw?.coverage
      ? {
          catalogExpectedCount: Number(raw.coverage.catalog_expected_count) || 0,
          catalogSampleCount: Number(raw.coverage.catalog_sample_count) || 0,
          catalogSampleFraction: Number(raw.coverage.catalog_sample_fraction) || 0,
          citationcsDatasetNodes: Number(raw.coverage.citationcs_dataset_nodes) || 0,
          citationcsDatasetEdges: Number(raw.coverage.citationcs_dataset_edges) || 0,
          citationcsWindowCsNodes: Number(raw.coverage.citationcs_window_cs_nodes) || 0,
          citationcsSampleCount: Number(raw.coverage.citationcs_sample_count) || 0,
          citationcsNewNodes: Number(raw.coverage.citationcs_new_nodes_in_union) || 0
        }
      : null
  };
}

function updateDatasetStats() {
  $("datasetNodes").textContent = formatCount(state.stats.nodes);
  $("datasetEdges").textContent = formatCount(state.stats.edges);
  $("datasetTopics").textContent = formatCount(state.stats.topics);
  $("datasetSource").textContent = state.stats.provider || "快照";
  const timestamp = state.stats.generatedAt ? ` · ${state.stats.generatedAt}` : "";
  const coverage = state.stats.coverage;
  const coverageNotes = [];
  if (coverage?.catalogExpectedCount) {
    coverageNotes.push(`CS 年度目录样本 ${formatCount(coverage.catalogSampleCount)} / ${formatCount(coverage.catalogExpectedCount)}（${(coverage.catalogSampleFraction * 100).toFixed(4)}%）`);
  }
  if (coverage?.citationcsDatasetNodes) {
    const denominator = coverage.citationcsWindowCsNodes || coverage.citationcsDatasetNodes;
    const label = coverage.citationcsWindowCsNodes ? "近十年 CS 数据集" : "数据集";
    coverageNotes.push(`CitationCS 子图 ${formatCount(coverage.citationcsSampleCount)} / ${label} ${formatCount(denominator)} 篇`);
  }
  const coverageNote = coverageNotes.length ? `；${coverageNotes.join("；")}` : "";
  $("datasetNote").textContent = `完整快照 ${formatCount(state.stats.nodes)} 节点 / ${formatCount(state.stats.edges)} 边${timestamp}${coverageNote}；这是可追溯的有限样本，画布是受限子图，最多渲染 ${formatCount(state.renderLimit)} 个节点，可用搜索或 ${state.neighborhoodDepth} 跳邻域继续展开关系。`;
}

function formatCount(value) {
  return new Intl.NumberFormat("zh-CN").format(Number(value) || 0);
}

const VIEW_COPY = {
  overview: { kicker: "研究领域 · 聚合图", title: "AI/CS 领域总览", description: "先看领域簇的规模、引用流向和前沿数量，再进入单个领域的论文图。" },
  papers: { kicker: "论文网络 · 局部图", title: "论文关系图", description: "当前画布只展示代表论文；完整快照保留在搜索、列表和数据层中。" },
  lineage: { kicker: "引用谱系 · 时间图", title: "研究演化路径", description: "沿时间轴查看基础工作、当前论文和后续研究。" },
  frontiers: { kicker: "前沿榜单 · 分领域", title: "各领域前沿", description: "按领域查看拓扑前沿和可追溯的奖项信号。" }
};

function updateViewModeUI() {
  const copy = VIEW_COPY[state.viewMode] || VIEW_COPY.overview;
  $("viewKicker").textContent = copy.kicker;
  $("viewTitle").textContent = copy.title;
  $("viewDescription").textContent = copy.description;
  document.querySelectorAll(".view-tab").forEach((tab) => {
    const active = tab.dataset.view === state.viewMode;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  });
}

function setViewMode(mode) {
  state.viewMode = mode;
  if (mode === "overview" || mode === "frontiers") {
    state.selectedId = null;
    state.neighborhoodId = null;
    state.activeDomainId = null;
    state.selectedDomainId = null;
    if (mode === "overview") {
      state.search = "";
      $("searchInput").value = "";
    }
  }
  if (mode === "papers") {
    // Leaving a time-based lineage should return to the normal searchable
    // paper canvas instead of keeping the previous one-node neighborhood lock.
    state.neighborhoodId = null;
  }
  if (mode === "lineage") {
    const candidate = state.selectedId || [...state.searchMatchIds][0] || state.filteredNodes[0]?.id || state.data.nodes[0]?.id;
    state.selectedId = candidate || null;
    state.neighborhoodId = state.selectedId;
  }
  updateViewModeUI();
  applyFilters();
}

function selectDomain(domainId) {
  state.activeDomainId = domainId;
  state.selectedDomainId = domainId;
  state.selectedId = null;
  state.neighborhoodId = null;
  state.search = "";
  $("searchInput").value = "";
  state.viewMode = "papers";
  updateViewModeUI();
  applyFilters();
}

function setupFilters() {
  const topicCounts = new Map();
  state.data.nodes.flatMap((node) => node.topics).forEach((topic) => topicCounts.set(topic, (topicCounts.get(topic) || 0) + 1));
  // OpenAlex can return many long-tail topic labels. Keep the control rail
  // usable while retaining every topic in search and the paper inspector.
  const topics = [...topicCounts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, 18).map(([topic]) => topic);
  $("topicList").innerHTML = topics.map((topic) => `<button type="button" class="topic-chip" aria-pressed="false" data-topic="${escapeHtml(topic)}">${escapeHtml(topic)}</button>`).join("");
  $("topicStat").textContent = formatCount(state.stats.topics || topics.length);
  $("yearMin").min = String(state.yearMin); $("yearMin").max = String(state.yearMax); $("yearMin").value = String(state.yearMin);
  $("yearMax").min = String(state.yearMin); $("yearMax").max = String(state.yearMax); $("yearMax").value = String(state.yearMax);
  $("yearMin").addEventListener("input", handleYearInput);
  $("yearMax").addEventListener("input", handleYearInput);
  $("searchInput").addEventListener("input", (event) => {
    state.search = event.target.value.trim().toLowerCase();
    if (state.search && state.viewMode === "overview") {
      state.viewMode = "papers";
      state.activeDomainId = null;
      updateViewModeUI();
    }
    if (state.search && state.neighborhoodId) state.neighborhoodId = null;
    applyFilters();
  });
  $("showExternal").addEventListener("change", (event) => { state.showExternal = event.target.checked; applyFilters(); });
  $("showLabels").addEventListener("change", (event) => { state.showLabels = event.target.checked; applyLabelVisibility(); });
  $("renderLimit").addEventListener("change", (event) => {
    state.renderLimit = Math.max(100, Number(event.target.value) || 100);
    updateDatasetStats();
    applyFilters();
  });
  $("neighborhoodDepth").addEventListener("change", (event) => {
    state.neighborhoodDepth = Math.min(3, Math.max(1, Number(event.target.value) || 1));
    updateDatasetStats();
    if (state.neighborhoodId) applyFilters();
    else if (state.selectedId) renderDetails(state.data.nodes.find((node) => node.id === state.selectedId));
  });
  $("clearTopics").addEventListener("click", () => { state.activeTopics.clear(); document.querySelectorAll(".topic-chip").forEach((chip) => { chip.classList.remove("active"); chip.setAttribute("aria-pressed", "false"); }); applyFilters(); });
  document.querySelectorAll(".topic-chip").forEach((chip) => chip.addEventListener("click", () => {
    const topic = chip.dataset.topic;
    if (state.activeTopics.has(topic)) { state.activeTopics.delete(topic); chip.classList.remove("active"); chip.setAttribute("aria-pressed", "false"); }
    else { state.activeTopics.add(topic); chip.classList.add("active"); chip.setAttribute("aria-pressed", "true"); }
    applyFilters();
  }));
  $("resetView").addEventListener("click", resetView);
  $("neighborhoodButton").addEventListener("click", toggleNeighborhood);
  $("neighborhoodToggle").addEventListener("click", exitNeighborhood);
  $("zoomIn").addEventListener("click", () => svg.transition().call(state.zoom.scaleBy, 1.25));
  $("zoomOut").addEventListener("click", () => svg.transition().call(state.zoom.scaleBy, .8));
  $("fitGraph").addEventListener("click", fitGraph);
  document.querySelectorAll(".view-tab").forEach((tab) => tab.addEventListener("click", () => setViewMode(tab.dataset.view)));
  $("domainOpenButton").addEventListener("click", () => { if (state.selectedDomainId) selectDomain(state.selectedDomainId); });
  window.addEventListener("keydown", (event) => { if (event.key === "/" && document.activeElement !== $("searchInput")) { event.preventDefault(); $("searchInput").focus(); } });
}

function handleYearInput() {
  let min = Number($("yearMin").value), max = Number($("yearMax").value);
  if (min > max) [min, max] = [max, min];
  state.yearMin = min; state.yearMax = max;
  $("yearValue").textContent = `${min} — ${max}`;
  applyFilters();
}

function applyFilters() {
  const nodeById = new Map(state.data.nodes.map((node) => [node.id, node]));
  const matchesNode = (node) => {
    const haystack = [node.title, node.venue, ...(node.authors || []), ...(node.topics || [])].join(" ").toLowerCase();
    const matchesSearch = !state.search || haystack.includes(state.search);
    // External foundation nodes can sit outside the time window but remain
    // visible when their cited edge is part of the current graph.
    const matchesYear = node.external || (node.year >= state.yearMin && node.year <= state.yearMax);
    const matchesTopic = !state.activeTopics.size || [...state.activeTopics].every((topic) => node.topics.includes(topic));
    const matchesDomain = !state.activeDomainId || node.domainId === state.activeDomainId;
    return matchesSearch && matchesYear && matchesTopic && matchesDomain && (state.showExternal || !node.external);
  };
  const matchedNodes = state.data.nodes.filter(matchesNode);
  state.searchMatchIds = new Set(state.search ? matchedNodes.map((node) => node.id) : []);
  let visibleIds = new Set(matchedNodes.map((node) => node.id));
  if (state.search && state.searchMatchIds.size) {
    // Searching a paper opens its local citation context instead of leaving
    // only the matching title on the canvas.
    const relatedIds = new Set(state.searchMatchIds);
    state.data.edges.forEach((edge) => {
      if (state.searchMatchIds.has(edge.source)) relatedIds.add(edge.target);
      if (state.searchMatchIds.has(edge.target)) relatedIds.add(edge.source);
    });
    visibleIds = new Set([...relatedIds].filter((id) => {
      const node = nodeById.get(id);
      return node && (state.showExternal || !node.external);
    }));
  }
  if (state.neighborhoodId) {
    const focus = nodeById.get(state.neighborhoodId);
    if (!focus || (!state.showExternal && focus.external)) {
      state.neighborhoodId = null;
    } else {
      // A neighborhood view intentionally relaxes the text/year filters for
      // adjacent nodes so a search result can be followed through several
      // citation layers without leaving the current paper context.
      const neighborhoodIds = new Set([state.neighborhoodId]);
      let frontier = new Set([state.neighborhoodId]);
      for (let depth = 0; depth < state.neighborhoodDepth && frontier.size; depth += 1) {
        const candidates = new Set();
        state.data.edges.forEach((edge) => {
          if (frontier.has(edge.source)) candidates.add(edge.target);
          if (frontier.has(edge.target)) candidates.add(edge.source);
        });
        const nextFrontier = new Set();
        candidates.forEach((id) => {
          if (!neighborhoodIds.has(id)) {
            neighborhoodIds.add(id);
            nextFrontier.add(id);
          }
        });
        frontier = nextFrontier;
      }
      visibleIds = new Set([...neighborhoodIds].filter((id) => {
        const node = nodeById.get(id);
        return node && (state.showExternal || !node.external);
      }));
    }
  }
  // Keep the visual layer bounded for large snapshots while preserving the
  // full dataset for the eventual API and ingestion pipeline.
  let filteredNodes = state.data.nodes.filter((node) => visibleIds.has(node.id));
  const ranked = [...filteredNodes].sort((a, b) => (b.citations_count || 0) - (a.citations_count || 0));
  const focus = state.neighborhoodId ? nodeById.get(state.neighborhoodId) : null;
  const pinnedIds = new Set(state.searchMatchIds);
  if (focus) pinnedIds.add(focus.id);
  if (filteredNodes.length > state.renderLimit || pinnedIds.size) {
    const pinned = ranked.filter((node) => pinnedIds.has(node.id));
    const remainder = ranked.filter((node) => !pinnedIds.has(node.id));
    filteredNodes = [...pinned, ...remainder].slice(0, state.renderLimit);
  }
  const finalIds = new Set(filteredNodes.map((node) => node.id));
  const filteredEdges = state.data.edges.filter((edge) => finalIds.has(edge.source) && finalIds.has(edge.target));
  state.filteredNodes = filteredNodes; state.filteredEdges = filteredEdges;
  state.frontierIds = computeFrontierIds(filteredNodes, visibleIds);
  if (state.selectedId && !finalIds.has(state.selectedId)) state.selectedId = null;
  if (state.neighborhoodId && !finalIds.has(state.neighborhoodId)) state.neighborhoodId = null;
  const matchCount = state.search
    ? [...state.searchMatchIds].filter((id) => !nodeById.get(id)?.external).length
    : [...visibleIds].filter((id) => !nodeById.get(id)?.external).length;
  $("resultCount").textContent = formatCount(matchCount);
  const overviewMode = state.viewMode === "overview" || state.viewMode === "frontiers";
  const summaries = overviewMode ? buildDomainSummaries() : [];
  if (overviewMode) {
    const frontierTotal = summaries.reduce((sum, domain) => sum + domain.frontierNodes.length, 0);
    $("paperStat").textContent = formatCount(summaries.length);
    $("edgeStat").textContent = formatCount(state.domainEdges.length);
    $("topicStat").textContent = formatCount(frontierTotal);
    $("paperStatLabel").textContent = "领域簇";
    $("edgeStatLabel").textContent = "领域引用";
    $("topicStatLabel").textContent = "前沿论文";
    $("canvasCoverage").textContent = `${formatCount(summaries.length)} 个领域簇 · ${formatCount(state.stats.nodes)} 篇论文 · 前沿 ${formatCount(frontierTotal)}`;
  } else {
    $("paperStat").textContent = filteredNodes.length;
    $("edgeStat").textContent = filteredEdges.length;
    $("topicStat").textContent = formatCount(state.stats.topics);
    $("paperStatLabel").textContent = "画布节点";
    $("edgeStatLabel").textContent = "画布边";
    $("topicStatLabel").textContent = "主题";
    const frontierNote = state.frontierIds.size ? ` · 前沿 ${formatCount(state.frontierIds.size)}` : "";
    $("canvasCoverage").textContent = state.neighborhoodId
      ? `${state.neighborhoodDepth} 跳邻域渲染 ${filteredNodes.length} / 全量 ${formatCount(state.stats.nodes)} · 上限 ${formatCount(state.renderLimit)}${frontierNote}`
      : `渲染 ${filteredNodes.length} / 全量 ${formatCount(state.stats.nodes)} · 上限 ${formatCount(state.renderLimit)}${frontierNote}`;
  }
  $("neighborhoodToggle").hidden = !state.neighborhoodId;
  $("neighborhoodButton").hidden = !state.selectedId;
  $("neighborhoodButton").textContent = state.neighborhoodId ? "退出邻域" : `在图中查看 ${state.neighborhoodDepth} 跳邻域`;
  $("emptyState").hidden = filteredNodes.length > 0;
  $("viewSummary").textContent = overviewMode
    ? `${formatCount(summaries.length)} 个领域簇 · ${formatCount(summaries.reduce((sum, domain) => sum + domain.frontierNodes.length, 0))} 个前沿`
    : (state.neighborhoodId
      ? `${state.neighborhoodDepth} 跳邻域 · ${filteredNodes.length} 个节点`
      : (state.search
        ? `${formatCount(state.searchMatchIds.size)} 个匹配 · ${filteredNodes.length} 个相关节点`
        : (state.activeTopics.size ? `${filteredNodes.length} 个匹配节点` : `全量 ${formatCount(state.stats.nodes)} · 画布 ${filteredNodes.length}`)));
  renderGraph();
  renderDetails(state.selectedId ? state.data.nodes.find((node) => node.id === state.selectedId) : null);
}

function hasAwardSignal(node) {
  if (node.is_award === true || node.award === true) return true;
  const values = [node.awards, node.award, node.award_info, node.recognition].flatMap((value) => Array.isArray(value) ? value : [value]).filter(Boolean);
  const text = values.map((value) => typeof value === "string" ? value : JSON.stringify(value)).join(" ");
  return /(best paper|best student paper|outstanding paper|test of time|paper award|最佳论文|最佳学生论文|杰出论文|时间考验奖)/i.test(text);
}

function topologicalFrontierIds(candidates, clusterIds, edges = state.data.edges) {
  const ids = new Set(candidates.map((node) => node.id));
  const indegree = new Map(candidates.map((node) => [node.id, 0]));
  edges.forEach((edge) => {
    if (clusterIds.has(edge.source) && ids.has(edge.source) && ids.has(edge.target)) {
      indegree.set(edge.target, (indegree.get(edge.target) || 0) + 1);
    }
  });
  const firstLayer = candidates.filter((node) => indegree.get(node.id) === 0);
  const fallback = [...candidates].sort((a, b) => (b.year || 0) - (a.year || 0) || (b.citations_count || 0) - (a.citations_count || 0));
  return (firstLayer.length ? firstLayer : fallback).sort((a, b) => (b.year || 0) - (a.year || 0) || (b.citations_count || 0) - (a.citations_count || 0));
}

function frontierSelectionForCluster(candidates, clusterIds, includeTopological = true) {
  const awardCandidates = candidates.filter(hasAwardSignal);
  const topological = includeTopological && candidates.length > 20
    ? topologicalFrontierIds(candidates, clusterIds)
    : [];
  const limit = Math.min(12, Math.max(4, Math.ceil(candidates.length * 0.1)));
  const awardIds = new Set(awardCandidates.map((node) => node.id));
  const ordered = [...awardIds, ...topological.map((node) => node.id)];
  return {
    awardIds,
    ids: new Set(ordered.filter((id, index, list) => list.indexOf(id) === index).slice(0, limit))
  };
}

function computeFrontierIds(nodes, visibleIds) {
  const visibleNodeIds = new Set(nodes.map((node) => node.id));
  const clusterIds = visibleIds || visibleNodeIds;
  const clusterCandidates = state.data.nodes.filter((node) => clusterIds.has(node.id) && !node.external);
  const hasClusterContext = Boolean(state.search || state.neighborhoodId || state.activeTopics.size || state.activeDomainId);
  const selection = frontierSelectionForCluster(clusterCandidates, clusterIds, hasClusterContext);
  state.frontierAwardIds = new Set([...selection.awardIds].filter((id) => visibleNodeIds.has(id)));
  return new Set([...selection.ids].filter((id) => visibleNodeIds.has(id)));
}

function overviewNodes() {
  return state.data.nodes.filter((node) => {
    if (node.external) return false;
    if (node.year < state.yearMin || node.year > state.yearMax) return false;
    return !state.activeTopics.size || [...state.activeTopics].every((topic) => node.topics.includes(topic));
  });
}

function buildDomainSummaries() {
  const nodes = overviewNodes();
  const grouped = new Map(DOMAIN_DEFINITIONS.map((domain) => [domain.id, []]));
  nodes.forEach((node) => grouped.get(node.domainId || "other")?.push(node));
  const domainByNode = new Map(nodes.map((node) => [node.id, node.domainId || "other"]));
  const edgeCounts = new Map();
  state.data.edges.forEach((edge) => {
    const source = domainByNode.get(edge.source), target = domainByNode.get(edge.target);
    if (!source || !target || source === target) return;
    const key = `${source}::${target}`;
    edgeCounts.set(key, (edgeCounts.get(key) || 0) + 1);
  });
  const summaries = DOMAIN_DEFINITIONS.map((domain) => {
    const members = grouped.get(domain.id) || [];
    if (!members.length) return null;
    const memberIds = new Set(members.map((node) => node.id));
    const frontier = frontierSelectionForCluster(members, memberIds, true);
    const frontierNodes = members.filter((node) => frontier.ids.has(node.id));
    return {
      ...domain,
      nodes: members,
      papers: members.length,
      citations: members.reduce((sum, node) => sum + (node.citations_count || 0), 0),
      latestYear: Math.max(...members.map((node) => node.year || 0)),
      frontierIds: frontier.ids,
      frontierAwardIds: frontier.awardIds,
      frontierNodes,
      topPaper: [...members].sort((a, b) => (b.citations_count || 0) - (a.citations_count || 0))[0]
    };
  }).filter(Boolean);
  const domainEdges = [...edgeCounts.entries()].map(([key, count]) => {
    const [source, target] = key.split("::");
    return { source, target, count };
  });
  state.domainSummaries = summaries;
  state.domainEdges = domainEdges;
  return summaries;
}

function labelIdsForView() {
  const ids = new Set();
  if (!state.search && !state.neighborhoodId) return ids;
  const ranked = [...state.filteredNodes].sort((a, b) => (b.citations_count || 0) - (a.citations_count || 0));
  if (state.searchMatchIds.size) {
    ranked.filter((node) => state.searchMatchIds.has(node.id)).slice(0, 3).forEach((node) => ids.add(node.id));
  }
  if (state.selectedId) ids.add(state.selectedId);
  ranked.filter((node) => !ids.has(node.id)).slice(0, 5).forEach((node) => ids.add(node.id));
  return ids;
}

function applyLabelVisibility() {
  const labelIds = labelIdsForView();
  labelLayer.selectAll("text").style("display", (node) => state.showLabels && labelIds.has(node.id) ? null : "none");
}

function nodeRadius(node) {
  if (node.external) return 4;
  const base = Math.min(10, 5 + ((node.citations_count || 0) / 30));
  return state.frontierIds.has(node.id) ? Math.min(15, base + 3) : base;
}

function nodeDescriptor(node) {
  if (node.external) return "外部引用节点";
  if (state.frontierAwardIds.has(node.id)) return "获奖前沿论文";
  return state.frontierIds.has(node.id) ? "前沿论文" : "收录论文";
}

function renderGraph() {
  const graph = $("graph");
  const frontierBoard = $("frontierBoard");
  const lineageBoard = $("lineageBoard");
  const modeChanged = state.renderedViewMode !== state.viewMode;
  if (modeChanged && (state.viewMode === "overview" || state.viewMode === "papers")) {
    // Each canvas view has its own coordinate system. Reset the transform
    // when switching between the aggregate and paper layouts so a previous
    // zoom never strands the new graph in a corner of the panel.
    svg.call(state.zoom.transform, d3.zoomIdentity);
  }
  state.renderedViewMode = state.viewMode;
  graph.hidden = state.viewMode === "frontiers" || state.viewMode === "lineage";
  frontierBoard.hidden = state.viewMode !== "frontiers";
  lineageBoard.hidden = state.viewMode !== "lineage";
  document.querySelector(".graph-legend").hidden = state.viewMode === "frontiers" || state.viewMode === "lineage";
  $("graphPanel").classList.toggle("board-mode", state.viewMode === "frontiers" || state.viewMode === "lineage");
  if (state.viewMode === "overview") return renderDomainGraph();
  if (state.viewMode === "frontiers") return renderFrontierBoard();
  if (state.viewMode === "lineage") return renderLineageBoard();
  renderPaperGraph();
}

function renderDomainGraph() {
  const summaries = state.domainSummaries.length ? state.domainSummaries : buildDomainSummaries();
  const rect = graphPanel.getBoundingClientRect();
  state.width = Math.max(400, rect.width); state.height = Math.max(560, rect.height);
  svg.attr("viewBox", `0 0 ${state.width} ${state.height}`).attr("aria-label", "AI/CS 领域簇总览图");
  linkLayer.selectAll("*").remove(); nodeLayer.selectAll("*").remove(); labelLayer.selectAll("*").remove();
  const nodes = summaries.map((domain) => ({ ...domain }));
  const nodeMap = new Map(nodes.map((node) => [node.id, node]));
  const edges = state.domainEdges.map((edge) => ({ ...edge, source: nodeMap.get(edge.source), target: nodeMap.get(edge.target) })).filter((edge) => edge.source && edge.target);
  state.renderNodes = nodes;
  state.filteredEdges = [];
  const links = linkLayer.selectAll("line").data(edges, (edge) => `${edge.source.id}-${edge.target.id}`).enter().append("line").attr("class", "domain-link").attr("stroke-width", (edge) => Math.min(4, 1 + Math.log1p(edge.count) / 2)).attr("stroke-opacity", .28);
  const nodeGroups = nodeLayer.selectAll("g.domain-node").data(nodes, (node) => node.id).enter().append("g").attr("class", "domain-node").attr("role", "button").attr("tabindex", 0)
    .attr("aria-label", (node) => `${node.label}，${formatCount(node.papers)} 篇论文，${formatCount(node.frontierNodes.length)} 篇前沿论文，点击进入论文图`)
    .on("click", (event, node) => { event.stopPropagation(); selectDomain(node.id); })
    .on("keydown", (event, node) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); selectDomain(node.id); } });
  nodeGroups.append("circle").attr("class", "domain-circle").attr("r", (node) => Math.min(43, 16 + Math.sqrt(node.papers) / 2.5)).attr("fill", (node) => node.color).attr("stroke", (node) => node.frontierNodes.length ? "#ffd166" : "rgba(255,255,255,.2)").attr("stroke-width", (node) => node.frontierNodes.length ? 3 : 1.2);
  nodeGroups.append("circle").attr("class", "domain-core").attr("r", 4).attr("fill", "#fff").attr("opacity", .8);
  const labels = labelLayer.selectAll("text.domain-label").data(nodes, (node) => node.id).enter().append("text").attr("class", "domain-label").text((node) => `${node.label} · ${formatCount(node.papers)}`);
  svg.on("click", () => { state.selectedDomainId = null; renderDetails(null); });
  if (state.simulation) state.simulation.stop();
  state.simulation = d3.forceSimulation(nodes).force("link", d3.forceLink(edges).id((node) => node.id).distance(210).strength(.08)).force("charge", d3.forceManyBody().strength(-720)).force("center", d3.forceCenter(state.width / 2, state.height / 2)).force("x", d3.forceX(state.width / 2).strength(.035)).force("y", d3.forceY(state.height / 2).strength(.035)).force("collide", d3.forceCollide().radius((node) => Math.min(58, 28 + Math.sqrt(node.papers) / 2))).on("tick", () => {
    links.attr("x1", (edge) => edge.source.x).attr("y1", (edge) => edge.source.y).attr("x2", (edge) => edge.target.x).attr("y2", (edge) => edge.target.y);
    nodeGroups.attr("transform", (node) => `translate(${node.x},${node.y})`);
    labels.attr("x", (node) => node.x).attr("y", (node) => node.y + Math.min(58, 30 + Math.sqrt(node.papers) / 2.5)).attr("text-anchor", "middle");
  });
  setTimeout(fitGraph, 260);
}

function renderFrontierBoard() {
  const summaries = [...state.domainSummaries].sort((a, b) => b.frontierNodes.length - a.frontierNodes.length || b.papers - a.papers);
  $("frontierBoard").innerHTML = summaries.map((domain) => {
    const papers = domain.frontierNodes.slice(0, 6);
    return `<section class="frontier-domain" data-domain="${escapeHtml(domain.id)}"><div class="frontier-domain-heading"><div><span class="domain-swatch" style="--domain-color:${domain.color}"></span><strong>${escapeHtml(domain.label)}</strong></div><span>${formatCount(domain.papers)} 篇 · ${formatCount(domain.frontierNodes.length)} 前沿</span></div><div class="frontier-paper-grid">${papers.map((paper) => `<button class="frontier-paper" type="button" data-paper-id="${escapeHtml(paper.id)}"><span class="frontier-paper-title">${escapeHtml(shortTitle(paper.title))}</span><span class="frontier-paper-meta">${paper.year} · ${formatCount(paper.citations_count || 0)} 引用${domain.frontierAwardIds.has(paper.id) ? " · 获奖信号" : ""}</span></button>`).join("") || `<span class="frontier-empty">暂无可标记前沿</span>`}</div></section>`;
  }).join("");
  $("frontierBoard").querySelectorAll("[data-paper-id]").forEach((button) => button.addEventListener("click", () => { state.selectedId = button.dataset.paperId; state.viewMode = "lineage"; state.neighborhoodId = state.selectedId; updateViewModeUI(); applyFilters(); }));
}

function renderLineageBoard() {
  const nodes = [...state.filteredNodes].sort((a, b) => (a.year || 0) - (b.year || 0) || (b.citations_count || 0) - (a.citations_count || 0));
  if (!state.selectedId || !nodes.length) {
    $("lineageBoard").innerHTML = `<div class="lineage-empty"><strong>先搜索或选择一篇论文</strong><span>引用谱系会按年份展示基础工作、当前论文和后续研究。</span></div>`;
    return;
  }
  const years = [...new Set(nodes.map((node) => node.year))].sort((a, b) => a - b);
  $("lineageBoard").innerHTML = `<div class="lineage-intro"><strong>${escapeHtml(state.data.nodes.find((node) => node.id === state.selectedId)?.title || "当前论文")}</strong><span>引用者 → 被引论文 · 当前展示 ${formatCount(nodes.length)} 个相关节点</span></div>${years.map((year) => `<section class="lineage-year"><div class="lineage-year-label">${year}</div><div class="lineage-year-papers">${nodes.filter((node) => node.year === year).slice(0, 14).map((node) => `<button class="lineage-paper ${node.id === state.selectedId ? "selected" : ""} ${state.frontierIds.has(node.id) ? "frontier" : ""}" type="button" data-paper-id="${escapeHtml(node.id)}"><span class="lineage-dot"></span><span><strong>${escapeHtml(shortTitle(node.title))}</strong><small>${formatCount(node.citations_count || 0)} 引用${state.frontierIds.has(node.id) ? " · 前沿" : ""}</small></span></button>`).join("")}</div></section>`).join("")}`;
  $("lineageBoard").querySelectorAll("[data-paper-id]").forEach((button) => button.addEventListener("click", () => { state.selectedId = button.dataset.paperId; state.neighborhoodId = state.selectedId; applyFilters(); }));
}

function renderPaperGraph() {
  const rect = graphPanel.getBoundingClientRect();
  state.width = Math.max(400, rect.width); state.height = Math.max(560, rect.height);
  svg.attr("viewBox", `0 0 ${state.width} ${state.height}`);
  const nodes = state.filteredNodes.map((node) => ({ ...node }));
  state.renderNodes = nodes;
  const nodeMap = new Map(nodes.map((node) => [node.id, node]));
  const edges = state.filteredEdges.map((edge) => ({ ...edge, source: nodeMap.get(edge.source), target: nodeMap.get(edge.target) }));
  const linkSelection = linkLayer.selectAll("line").data(edges, (edge) => `${edge.source.id}-${edge.target.id}`);
  linkSelection.exit().transition().duration(120).style("opacity", 0).remove();
  const linkEnter = linkSelection.enter().append("line").attr("class", "link");
  const links = linkEnter.merge(linkSelection);
  const nodeSelection = nodeLayer.selectAll("g.node").data(nodes, (node) => node.id);
  nodeSelection.exit().transition().duration(120).style("opacity", 0).remove();
  const nodeEnter = nodeSelection.enter().append("g").attr("class", "node")
    .attr("role", "button")
    .attr("tabindex", 0)
    .attr("aria-label", (node) => `${node.title}，${node.year} 年，${nodeDescriptor(node)}，点击查看论文详情`)
    .on("click", (event, node) => {
      event.stopPropagation();
      selectNode(node.id);
    })
    .on("keydown", (event, node) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectNode(node.id);
      }
    })
    .call(d3.drag().on("start", dragStart).on("drag", dragged).on("end", dragEnd));
  nodeEnter.append("circle").attr("class", "node-circle").attr("r", nodeRadius).on("click", (event, node) => { event.stopPropagation(); selectNode(node.id); });
  const mergedNodes = nodeEnter.merge(nodeSelection);
  mergedNodes.attr("aria-label", (node) => `${node.title}，${node.year} 年，${nodeDescriptor(node)}，点击查看论文详情`)
    .select("circle").attr("r", nodeRadius).attr("class", (node) => `node-circle ${node.external ? "external" : `core topic-${topicIndex(node)}${state.frontierIds.has(node.id) ? " frontier" : ""}`}${state.selectedId === node.id ? " selected" : ""}`);
  const labelSelection = labelLayer.selectAll("text").data(nodes, (node) => node.id);
  labelSelection.exit().remove();
  labelSelection.enter().append("text").attr("class", "node-label").merge(labelSelection).text((node) => shortTitle(node.title)).classed("active", (node) => state.selectedId === node.id);
  applyLabelVisibility();
  svg.on("click", () => { state.selectedId = null; renderGraph(); renderDetails(null); });
  if (state.simulation) state.simulation.stop();
  state.simulation = d3.forceSimulation(nodes).force("link", d3.forceLink(edges).id((node) => node.id).distance((edge) => edge.source.external || edge.target.external ? 110 : 88).strength(.55)).force("charge", d3.forceManyBody().strength((node) => node.external ? -70 : -150)).force("center", d3.forceCenter(state.width / 2, state.height / 2)).force("collide", d3.forceCollide().radius((node) => node.external ? 10 : 15)).on("tick", () => {
    links.attr("x1", (edge) => edge.source.x).attr("y1", (edge) => edge.source.y).attr("x2", (edge) => edge.target.x).attr("y2", (edge) => edge.target.y);
    mergedNodes.attr("transform", (node) => `translate(${node.x},${node.y})`);
    labelLayer.selectAll("text").attr("x", (node) => node.x + (node.external ? 7 : 10)).attr("y", (node) => node.y + 3);
  });
  applyHighlight();
  if (state.filteredNodes.length) {
    // Let the force settle in the panel's own coordinate system. The paper
    // view is already bounded to the render limit, so an extra auto-zoom can
    // push a newly searched subgraph toward an edge of the viewport.
    setTimeout(() => state.simulation?.stop(), 950);
  }
}

function ensureArrow() {
  if (!svg.select("defs").node()) svg.append("defs");
  if (!svg.select("#arrow").node()) svg.select("defs").append("marker").attr("id", "arrow").attr("viewBox", "0 -4 8 8").attr("refX", 8).attr("refY", 0).attr("markerWidth", 5).attr("markerHeight", 5).attr("orient", "auto").append("path").attr("d", "M0,-4L8,0L0,4").attr("fill", "rgba(123,226,255,.72)");
}

function selectNode(id) {
  if (state.neighborhoodId && state.neighborhoodId !== id) state.neighborhoodId = null;
  state.selectedId = id;
  const node = state.data.nodes.find((item) => item.id === id);
  $("neighborhoodToggle").hidden = !state.neighborhoodId;
  renderGraph();
  renderDetails(node);
}

function toggleNeighborhood() {
  if (!state.selectedId) return;
  state.neighborhoodId = state.neighborhoodId === state.selectedId ? null : state.selectedId;
  applyFilters();
}

function exitNeighborhood() {
  if (!state.neighborhoodId) return;
  state.neighborhoodId = null;
  applyFilters();
}

function applyHighlight() {
  const selected = state.selectedId;
  if (!selected) { linkLayer.selectAll("line").classed("highlighted", false).classed("link-dim", false); nodeLayer.selectAll("circle").classed("node-dim", false); labelLayer.selectAll("text").classed("active", false); return; }
  const connected = new Set([selected]);
  state.filteredEdges.forEach((edge) => { if (edge.source === selected) connected.add(edge.target); if (edge.target === selected) connected.add(edge.source); });
  linkLayer.selectAll("line").data(state.filteredEdges, (edge) => `${edge.source}-${edge.target}`).classed("highlighted", (edge) => edge.source === selected || edge.target === selected).classed("link-dim", (edge) => edge.source !== selected && edge.target !== selected);
  nodeLayer.selectAll("g.node").classed("node-dim", (node) => !connected.has(node.id));
  labelLayer.selectAll("text").classed("active", (node) => node.id === selected);
}

function showEmptyDetails(kicker, title, copy) {
  $("detailsEmpty").hidden = false;
  $("detailsContent").hidden = true;
  $("domainDetails").hidden = true;
  $("frontierDetails").hidden = true;
  $("detailsEmptyKicker").textContent = kicker;
  $("detailsEmptyTitle").textContent = title;
  $("detailsEmptyCopy").textContent = copy;
}

function renderDomainDetails(domain) {
  if (!domain) {
    showEmptyDetails("领域总览", "选择一个领域簇", "点击领域节点，查看规模、引用流向和领域前沿论文。");
    return;
  }
  $("detailsEmpty").hidden = true;
  $("detailsContent").hidden = true;
  $("frontierDetails").hidden = true;
  $("domainDetails").hidden = false;
  $("domainYear").textContent = `最新 ${domain.latestYear}`;
  $("domainTitle").textContent = domain.label;
  $("domainMeta").textContent = `${formatCount(domain.papers)} 篇论文 · ${formatCount(domain.frontierNodes.length)} 篇前沿 · ${formatCount(domain.citations)} 次累计引用`;
  $("domainPapers").textContent = formatCount(domain.papers);
  $("domainCitations").textContent = formatCount(domain.citations);
  $("domainFrontiers").textContent = formatCount(domain.frontierNodes.length);
  $("domainFrontierCount").textContent = formatCount(domain.frontierNodes.length);
  $("domainFrontierList").innerHTML = domain.frontierNodes.slice(0, 6).map((paper) => `<div class="related-item domain-paper-item" data-related="${escapeHtml(paper.id)}"><span class="related-dot frontier-dot"></span><span class="related-copy"><span class="related-title">${escapeHtml(paper.title)}</span><span class="related-meta">${paper.year} · ${formatCount(paper.citations_count || 0)} 引用${domain.frontierAwardIds.has(paper.id) ? " · 获奖信号" : ""}</span></span></div>`).join("") || `<p class="details-intro">当前簇暂未形成超过 20 个节点的拓扑前沿。</p>`;
  $("domainFrontierList").querySelectorAll("[data-related]").forEach((item) => item.addEventListener("click", () => { state.selectedId = item.dataset.related; state.viewMode = "lineage"; state.neighborhoodId = state.selectedId; updateViewModeUI(); applyFilters(); }));
}

function renderFrontierDetails() {
  $("detailsEmpty").hidden = true;
  $("detailsContent").hidden = true;
  $("domainDetails").hidden = true;
  $("frontierDetails").hidden = false;
  const summaries = [...state.domainSummaries].sort((a, b) => b.frontierNodes.length - a.frontierNodes.length);
  $("frontierSummaryList").innerHTML = summaries.map((domain) => `<button class="frontier-summary-item" type="button" data-domain-id="${escapeHtml(domain.id)}"><span class="domain-swatch" style="--domain-color:${domain.color}"></span><span class="frontier-summary-copy"><strong>${escapeHtml(domain.label)}</strong><small>${formatCount(domain.frontierNodes.length)} 前沿 · ${formatCount(domain.papers)} 篇</small></span><span class="frontier-summary-arrow">›</span></button>`).join("");
  $("frontierSummaryList").querySelectorAll("[data-domain-id]").forEach((item) => item.addEventListener("click", () => selectDomain(item.dataset.domainId)));
}

function renderDetails(node) {
  if (state.viewMode === "overview") return renderDomainDetails(state.selectedDomainId ? state.domainSummaries.find((domain) => domain.id === state.selectedDomainId) : null);
  if (state.viewMode === "frontiers") return renderFrontierDetails();
  if (!node) {
    if (state.selectedDomainId) return renderDomainDetails(state.domainSummaries.find((domain) => domain.id === state.selectedDomainId) || null);
    showEmptyDetails("论文详情", "选择一个节点", "点击图中的论文，查看元数据、引用关系和研究主题。");
    return;
  }
  $("domainDetails").hidden = true;
  $("frontierDetails").hidden = true;
  $("detailsEmpty").hidden = true; $("detailsContent").hidden = false;
  $("detailsPanel").scrollTop = 0;
  $("neighborhoodButton").hidden = false;
  $("neighborhoodButton").textContent = state.neighborhoodId ? "退出邻域" : `在图中查看 ${state.neighborhoodDepth} 跳邻域`;
  $("detailYear").textContent = node.year;
  $("detailTitle").textContent = node.title;
  $("detailVenue").textContent = [node.venue, node.external ? "外部引用节点" : "收录论文"].filter(Boolean).join(" · ");
  $("detailAuthors").textContent = (node.authors || []).join(" · ") || "作者信息待补充";
  $("detailProvenance").textContent = `来源 · ${node.source || "OpenAlex / arXiv 公共元数据快照"}`;
  $("detailTags").innerHTML = (node.topics || []).map((topic) => `<span class="detail-tag">${escapeHtml(topic)}</span>`).join("");
  $("detailAbstract").textContent = node.abstract || "暂无摘要。后续数据同步会补充论文摘要和来源信息。";
  const incoming = state.data.edges.filter((edge) => edge.target === node.id);
  const outgoing = state.data.edges.filter((edge) => edge.source === node.id);
  $("detailReferences").textContent = outgoing.length;
  $("detailCitations").textContent = incoming.length;
  $("detailConnections").textContent = incoming.length + outgoing.length;
  const relatedIds = [...new Set([...incoming.map((edge) => ({ id: edge.source, type: "incoming" })), ...outgoing.map((edge) => ({ id: edge.target, type: "outgoing" }))].map((item) => JSON.stringify(item)))].map((item) => JSON.parse(item));
  $("relatedCount").textContent = relatedIds.length;
  $("relatedList").innerHTML = relatedIds.slice(0, 7).map((item) => {
    const related = state.data.nodes.find((candidate) => candidate.id === item.id); if (!related) return "";
    return `<div class="related-item" data-related="${escapeHtml(related.id)}"><span class="related-dot ${item.type === "incoming" ? "incoming" : ""}"></span><span class="related-copy"><span class="related-title">${escapeHtml(related.title)}</span><span class="related-meta">${item.type === "incoming" ? "引用此论文" : "此论文引用"} · ${related.year}</span></span></div>`;
  }).join("");
  $("relatedList").querySelectorAll(".related-item").forEach((item) => item.addEventListener("click", () => selectNode(item.dataset.related)));
  const link = $("detailLink"); link.href = node.url || `https://openalex.org/${encodeURIComponent(node.id)}`;
}

function fitGraph() {
  if (!state.zoom || !state.filteredNodes.length) return;
  const xs = state.renderNodes.map((node) => node.x).filter(Number.isFinite), ys = state.renderNodes.map((node) => node.y).filter(Number.isFinite);
  if (!xs.length || !ys.length) return;
  const minX = Math.min(...xs) - 55, maxX = Math.max(...xs) + 55, minY = Math.min(...ys) - 55, maxY = Math.max(...ys) + 55;
  const scale = Math.min(.95, Math.max(.48, Math.min(state.width / (maxX - minX), state.height / (maxY - minY))));
  const tx = state.width / 2 - scale * (minX + maxX) / 2, ty = state.height / 2 - scale * (minY + maxY) / 2;
  svg.transition().duration(450).call(state.zoom.transform, d3.zoomIdentity.translate(tx, ty).scale(scale));
}

function resetView() { state.viewMode = "overview"; state.activeDomainId = null; state.selectedDomainId = null; state.selectedId = null; state.neighborhoodId = null; state.neighborhoodDepth = 1; state.search = ""; $("searchInput").value = ""; state.activeTopics.clear(); document.querySelectorAll(".topic-chip").forEach((chip) => { chip.classList.remove("active"); chip.setAttribute("aria-pressed", "false"); }); $("showExternal").checked = true; state.showExternal = true; state.renderLimit = 100; $("renderLimit").value = "100"; $("neighborhoodDepth").value = "1"; $("yearMin").value = state.yearMin; $("yearMax").value = state.yearMax; $("yearValue").textContent = `${state.yearMin} — ${state.yearMax}`; updateViewModeUI(); updateDatasetStats(); applyFilters(); }
function topicIndex(node) { const topic = (node.topics || [])[0] || ""; return Math.abs(hash(topic)) % 4; }
function hash(text) { return [...String(text)].reduce((sum, char) => ((sum << 5) - sum + char.charCodeAt(0)) | 0, 0); }
function shortTitle(title) { return title.length > 26 ? `${title.slice(0, 25)}…` : title; }
function escapeHtml(value) { return String(value).replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char])); }
function dragStart(event, node) { if (!event.active) state.simulation.alphaTarget(.3).restart(); node.fx = node.x; node.fy = node.y; }
function dragged(event, node) { node.fx = event.x; node.fy = event.y; }
function dragEnd(event, node) { if (!event.active) state.simulation.alphaTarget(0); node.fx = null; node.fy = null; }

state.zoom = d3.zoom().scaleExtent([.35, 3]).on("zoom", (event) => { linkLayer.attr("transform", event.transform); nodeLayer.attr("transform", event.transform); labelLayer.attr("transform", event.transform); });
svg.call(state.zoom);
ensureArrow();
loadData();
