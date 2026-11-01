/* AI Research Graph static prototype. Replace data.sample.json with the
 * output of the ingestion pipeline; the graph schema is intentionally small. */

const FALLBACK_DATA = {
  nodes: [
    { id: "fallback-1", title: "Attention Is All You Need", year: 2017, venue: "NeurIPS", authors: ["Ashish Vaswani", "Noam Shazeer"], topics: ["NLP", "Architecture"], abstract: "A compact fallback graph is shown when the data snapshot cannot be loaded.", url: "https://arxiv.org/abs/1706.03762" },
    { id: "fallback-2", title: "BERT: Pre-training of Deep Bidirectional Transformers", year: 2018, venue: "NAACL", authors: ["Jacob Devlin", "Ming-Wei Chang"], topics: ["NLP", "Pretraining"], abstract: "The sample data file can be replaced with real OpenAlex records.", url: "https://arxiv.org/abs/1810.04805" }
  ],
  edges: [{ source: "fallback-2", target: "fallback-1", kind: "citation" }]
};

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
  aiSearchIds: new Set(),
  aiResults: [],
  aiAnswer: "",
  aiPayload: null,
  aiMode: "review",
  aiFilters: { year_from: 2016, year_to: 2026, topics: [], publication_types: [], venues: [], min_citations: 0 },
  aiBusy: false,
  compareIds: new Set(),
  workspace: null,
  width: 0,
  height: 0
};

const WORKSPACE_KEY = "ai-research-graph.workspace.v1";
const DEFAULT_WORKSPACE = { favorites: [], compare: [], notes: {}, read: {}, tags: {}, history: [], savedQueries: [] };
const AI_MODES = {
  related: "找相关论文",
  review: "梳理文献综述",
  compare: "比较研究方法",
  gaps: "寻找研究空白",
  frontier: "寻找研究前沿"
};

function loadWorkspace() {
  try {
    const stored = JSON.parse(localStorage.getItem(WORKSPACE_KEY) || "null");
    return {
      ...DEFAULT_WORKSPACE,
      ...(stored || {}),
      favorites: Array.isArray(stored?.favorites) ? stored.favorites : [],
      compare: Array.isArray(stored?.compare) ? stored.compare : [],
      notes: stored?.notes && typeof stored.notes === "object" ? stored.notes : {},
      read: stored?.read && typeof stored.read === "object" ? stored.read : {},
      tags: stored?.tags && typeof stored.tags === "object" ? stored.tags : {},
      history: Array.isArray(stored?.history) ? stored.history : [],
      savedQueries: Array.isArray(stored?.savedQueries) ? stored.savedQueries : []
    };
  } catch {
    return { ...DEFAULT_WORKSPACE };
  }
}

function persistWorkspace() {
  try { localStorage.setItem(WORKSPACE_KEY, JSON.stringify(state.workspace)); } catch { /* local storage may be unavailable */ }
}

function workspaceHas(collection, id) {
  return Array.isArray(state.workspace?.[collection]) && state.workspace[collection].includes(String(id));
}

function toggleWorkspaceId(collection, id) {
  const value = String(id);
  const list = state.workspace[collection] || [];
  state.workspace[collection] = list.includes(value) ? list.filter((item) => item !== value) : [...list, value];
  persistWorkspace();
  state.compareIds = new Set(state.workspace.compare || []);
  updateWorkspaceControls();
}

state.workspace = loadWorkspace();
state.compareIds = new Set(state.workspace.compare || []);

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
  const nodes = (raw.nodes || []).map((node) => ({
    ...node,
    id: String(node.id),
    year: Number(node.year ?? node.publication_year) || 2016,
    title: node.title || "Untitled paper",
    venue: node.venue || node.primary_topic?.display_name || node.source || "OpenAlex",
    topics: normalizeTopics(node.topics || (node.primary_topic ? [node.primary_topic] : [])),
    authors: normalizeAuthors(node.authors),
    abstract: node.abstract || "",
    citations_count: Number(node.citations_count ?? node.cited_by_count ?? 0),
    url: node.url || node.open_access_url || node.landing_page_url || node.doi || (node.openalex_id ? `https://openalex.org/${String(node.openalex_id).split("/").pop()}` : ""),
    metadata_completeness: Number(node.metadata_completeness ?? node.field_completeness ?? 0) || 0,
    publication_type: node.publication_type || node.type || "",
    openalex_id: node.openalex_id || node.ids?.openalex || "",
    doi: node.doi || node.ids?.doi || "",
    arxiv_id: node.arxiv_id || node.ids?.arxiv || "",
    external: Boolean(node.external ?? node.is_external_reference)
  }));
  const known = new Set(nodes.map((node) => node.id));
  const edges = (raw.edges || []).map((edge) => ({ ...edge, source: String(edge.source), target: String(edge.target) })).filter((edge) => known.has(edge.source) && known.has(edge.target));
  return { nodes, edges };
}

async function decodeSnapshotResponse(response) {
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

async function fetchSnapshot() {
  // The hosted source repository has a 32 MiB per-file limit.  Prefer the
  // two-part release and keep the single-file path as a backwards-compatible
  // fallback for local fixtures and older deployments.
  const partResponses = await Promise.all([
    fetch("data.sample.part1.json.gz", { cache: "no-store" }),
    fetch("data.sample.part2.json.gz", { cache: "no-store" })
  ]);
  if (partResponses.every((response) => response.ok)) {
    const parts = await Promise.all(partResponses.map(decodeSnapshotResponse));
    return {
      ...parts[0],
      nodes: parts.flatMap((part) => part.nodes || []),
      edges: parts.flatMap((part) => part.edges || [])
    };
  }
  return decodeSnapshotResponse(await fetch("data.sample.json.gz", { cache: "no-store" }));
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
  // Avoid spreading the full node list into Math.max; the release can contain
  // hundreds of thousands of papers and would exceed the call-stack limit.
  state.yearMax = years.reduce((maxYear, year) => Math.max(maxYear, year), 2026);
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
          sourceWindowCsRecords: Number(raw.coverage.source_window_cs_records) || 0,
          sourceWindowAiPriorityRecords: Number(raw.coverage.source_window_ai_priority_records) || 0,
          selectedSampleCount: Number(raw.coverage.selected_sample_count) || 0,
          selectedSampleFraction: Number(raw.coverage.selected_sample_fraction_of_window_ai_priority) || 0,
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
  if (coverage?.sourceWindowAiPriorityRecords) {
    coverageNotes.push(`近十年 AI 优先样本 ${formatCount(coverage.selectedSampleCount)} / ${formatCount(coverage.sourceWindowAiPriorityRecords)} 篇（${(coverage.selectedSampleFraction * 100).toFixed(2)}%）`);
  }
  const coverageNote = coverageNotes.length ? `；${coverageNotes.join("；")}` : "";
  $("datasetNote").textContent = `当前快照 ${formatCount(state.stats.nodes)} 节点 / ${formatCount(state.stats.edges)} 边${timestamp}${coverageNote}；这是可追溯的有限样本，画布是受限子图，最多渲染 ${formatCount(state.renderLimit)} 个节点，可用搜索或 ${state.neighborhoodDepth} 跳邻域继续展开关系。`;
}

function formatCount(value) {
  return new Intl.NumberFormat("zh-CN").format(Number(value) || 0);
}

function setupFilters() {
  state.workspace = state.workspace || loadWorkspace();
  state.compareIds = new Set(state.workspace.compare || []);
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
    if (state.search) {
      state.aiSearchIds.clear();
      state.aiResults = [];
      state.aiAnswer = "";
      renderAiResults();
    }
    applyFilters();
  });
  $("aiSearchForm").addEventListener("submit", (event) => { event.preventDefault(); runAiSearch(); });
  $("aiSearchMode").addEventListener("change", (event) => { state.aiMode = event.target.value; });
  [$("aiYearFrom"), $("aiYearTo"), $("aiMinCitations"), $("aiTopicFilter"), $("aiPublicationType"), $("aiVenueFilter")].forEach((control) => control.addEventListener("change", syncAiFilters));
  [$("aiTopicFilter"), $("aiVenueFilter")].forEach((control) => control.addEventListener("input", syncAiFilters));
  $("aiCompareButton").addEventListener("click", () => {
    state.aiMode = "compare";
    $("aiSearchMode").value = "compare";
    if (state.compareIds.size) {
      const titles = [...state.compareIds].map((id) => state.data.nodes.find((node) => node.id === id)?.title).filter(Boolean);
      $("aiSearchInput").value = titles.slice(0, 5).join("；");
      runAiSearch();
    }
  });
  $("aiExportButton").addEventListener("click", exportAiResults);
  $("favoriteButton").addEventListener("click", () => { if (state.selectedId) { toggleWorkspaceId("favorites", state.selectedId); renderDetails(state.data.nodes.find((node) => node.id === state.selectedId)); } });
  $("compareButton").addEventListener("click", () => { if (state.selectedId) { toggleWorkspaceId("compare", state.selectedId); renderDetails(state.data.nodes.find((node) => node.id === state.selectedId)); } });
  $("readButton").addEventListener("click", () => { if (state.selectedId) { state.workspace.read[state.selectedId] = true; persistWorkspace(); renderDetails(state.data.nodes.find((node) => node.id === state.selectedId)); } });
  $("saveNoteButton").addEventListener("click", () => {
    if (!state.selectedId) return;
    state.workspace.notes[state.selectedId] = $("detailNote").value.trim();
    persistWorkspace();
    $("saveNoteButton").textContent = "已保存";
    setTimeout(() => { if ($("saveNoteButton")) $("saveNoteButton").textContent = "保存笔记"; }, 1200);
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
  window.addEventListener("keydown", (event) => { if (event.key === "/" && document.activeElement !== $("searchInput")) { event.preventDefault(); $("searchInput").focus(); } });
  updateWorkspaceControls();
}

function syncAiFilters() {
  const from = Number($("aiYearFrom").value) || state.yearMin;
  const to = Number($("aiYearTo").value) || state.yearMax;
  state.aiFilters = {
    year_from: Math.min(from, to),
    year_to: Math.max(from, to),
    topics: $("aiTopicFilter").value.trim() ? [$("aiTopicFilter").value.trim()] : [],
    publication_types: $("aiPublicationType").value ? [$("aiPublicationType").value] : [],
    venues: $("aiVenueFilter").value.trim() ? [$("aiVenueFilter").value.trim()] : [],
    min_citations: Math.max(0, Number($("aiMinCitations").value) || 0)
  };
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
    return matchesSearch && matchesYear && matchesTopic && (state.showExternal || !node.external);
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
  if (state.aiSearchIds.size) {
    // Keep the AI answer connected to the graph: show the returned papers and
    // one citation layer around them so the user can continue exploring.
    const relatedIds = new Set(state.aiSearchIds);
    state.data.edges.forEach((edge) => {
      if (state.aiSearchIds.has(edge.source)) relatedIds.add(edge.target);
      if (state.aiSearchIds.has(edge.target)) relatedIds.add(edge.source);
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
  const pinnedIds = new Set([...state.searchMatchIds, ...state.aiSearchIds]);
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
    : state.aiSearchIds.size
      ? [...state.aiSearchIds].filter((id) => !nodeById.get(id)?.external).length
      : [...visibleIds].filter((id) => !nodeById.get(id)?.external).length;
  $("resultCount").textContent = formatCount(matchCount);
  $("paperStat").textContent = filteredNodes.length;
  $("edgeStat").textContent = filteredEdges.length;
  const frontierNote = state.frontierIds.size ? ` · 前沿 ${formatCount(state.frontierIds.size)}` : "";
  $("canvasCoverage").textContent = state.neighborhoodId
    ? `${state.neighborhoodDepth} 跳邻域渲染 ${filteredNodes.length} / 全量 ${formatCount(state.stats.nodes)} · 上限 ${formatCount(state.renderLimit)}${frontierNote}`
    : `渲染 ${filteredNodes.length} / 全量 ${formatCount(state.stats.nodes)} · 上限 ${formatCount(state.renderLimit)}${frontierNote}`;
  $("neighborhoodToggle").hidden = !state.neighborhoodId;
  $("neighborhoodButton").hidden = !state.selectedId;
  $("neighborhoodButton").textContent = state.neighborhoodId ? "退出邻域" : `在图中查看 ${state.neighborhoodDepth} 跳邻域`;
  $("emptyState").hidden = filteredNodes.length > 0;
  $("viewSummary").textContent = state.neighborhoodId
    ? `${state.neighborhoodDepth} 跳邻域 · ${filteredNodes.length} 个节点`
    : (state.search
      ? `${formatCount(state.searchMatchIds.size)} 个匹配 · ${filteredNodes.length} 个相关节点`
      : (state.aiSearchIds.size
        ? `AI 搜索 ${formatCount(state.aiSearchIds.size)} 篇匹配 · ${filteredNodes.length} 个相关节点`
        : (state.activeTopics.size ? `${filteredNodes.length} 个匹配节点` : `全量 ${formatCount(state.stats.nodes)} · 画布 ${filteredNodes.length}`)));
  renderGraph();
  renderDetails(state.selectedId ? state.data.nodes.find((node) => node.id === state.selectedId) : null);
}

function aiQueryTerms(query) {
  const latin = query.toLowerCase().match(/[a-z0-9][a-z0-9+.#-]*/g) || [];
  const chinese = Array.from(query).filter((char) => /[\u4e00-\u9fff]/.test(char));
  const grams = [];
  for (let index = 0; index < chinese.length - 1; index += 1) grams.push(chinese.slice(index, index + 2).join(""));
  return [...new Set([...latin, ...grams])].filter((term) => term.length >= 2);
}

function buildAiCandidates(query, filters = state.aiFilters) {
  const terms = aiQueryTerms(query);
  const topicTerms = (filters.topics || []).flatMap(aiQueryTerms);
  const minYear = Number(filters.year_from) || 0;
  const maxYear = Number(filters.year_to) || 9999;
  const minCitations = Number(filters.min_citations) || 0;
  const typeTerms = (filters.publication_types || []).map((value) => String(value).toLowerCase());
  const venueTerms = (filters.venues || []).map((value) => String(value).toLowerCase());
  const ranked = state.data.nodes.filter((node) => {
    const topicText = (node.topics || []).join(" ").toLowerCase();
    const venueText = String(node.venue || "").toLowerCase();
    const publicationType = String(node.publication_type || node.type || "").toLowerCase();
    return !node.external && node.year >= minYear && node.year <= maxYear && (node.citations_count || 0) >= minCitations && (!topicTerms.length || topicTerms.every((term) => topicText.includes(term))) && (!typeTerms.length || typeTerms.includes(publicationType)) && (!venueTerms.length || venueTerms.some((term) => venueText.includes(term)));
  }).map((node) => {
    const title = String(node.title || "").toLowerCase();
    const text = [node.title, node.venue, ...(node.authors || []), ...(node.topics || []), node.abstract].join(" ").toLowerCase();
    const lexical = terms.reduce((score, term) => score + (title.includes(term) ? 8 : 0) + (text.includes(term) ? 2 : 0), 0);
    const topicMatch = topicTerms.reduce((score, term) => score + (text.includes(term) ? 3 : 0), 0);
    const recency = Math.max(0, (node.year || 0) - 2015) * 0.05;
    const citationBoost = Math.log1p(node.citations_count || 0) * 0.22;
    return { node, score: lexical + topicMatch + citationBoost + recency };
  }).sort((a, b) => b.score - a.score || (b.node.citations_count || 0) - (a.node.citations_count || 0));
  const selected = ranked.filter((item) => item.score > 0).slice(0, 64);
  const selectedIds = new Set(selected.map((item) => item.node.id));
  ranked.filter((item) => !selectedIds.has(item.node.id)).slice(0, Math.max(0, 80 - selected.length)).forEach((item) => selected.push(item));
  return selected.slice(0, 80).map(({ node }) => ({
    id: node.id,
    title: node.title,
    year: node.year,
    venue: node.venue,
    authors: (node.authors || []).slice(0, 5),
    topics: (node.topics || []).slice(0, 6),
    citations_count: node.citations_count || 0,
    abstract: String(node.abstract || "").slice(0, 800),
    metadata_completeness: node.metadata_completeness || 0,
    publication_type: node.publication_type || "",
    is_frontier: state.frontierIds.has(node.id) || hasAwardSignal(node)
  }));
}

function localAiPayload(query, mode, filters, candidates) {
  const terms = aiQueryTerms(query);
  const selected = candidates.slice(0, 12);
  const topicCounts = new Map();
  const venueCounts = new Map();
  selected.forEach((candidate) => {
    (candidate.topics || []).forEach((topic) => topicCounts.set(topic, (topicCounts.get(topic) || 0) + 1));
    if (candidate.venue) venueCounts.set(candidate.venue, (venueCounts.get(candidate.venue) || 0) + 1);
  });
  const years = [...new Set(selected.map((candidate) => candidate.year).filter(Boolean))].sort((a, b) => a - b);
  const resultItems = selected.map((candidate, index) => ({
    ...candidate,
    relevance_score: Math.max(0.15, Math.min(0.99, 0.98 - index * 0.045)),
    reason: terms.length ? `标题、主题或作者字段与“${query.slice(0, 36)}”匹配；引用量 ${formatCount(candidate.citations_count)}。` : `按引用量、年份和主题相关性排序；引用量 ${formatCount(candidate.citations_count)}。`,
    evidence_ids: [candidate.id],
    graph_signals: { citation_count: candidate.citations_count || 0, is_frontier: hasAwardSignal(candidate) }
  }));
  const modeLabel = AI_MODES[mode] || AI_MODES.review;
  const summary = mode === "gaps"
    ? `本地检索找到 ${formatCount(resultItems.length)} 篇候选论文；研究空白需要结合摘要和全文进一步核实。`
    : mode === "frontier"
      ? `本地检索按近年发表、引用量和图谱位置整理了 ${formatCount(resultItems.length)} 篇候选论文。`
      : `本地检索围绕“${query}”整理了 ${formatCount(resultItems.length)} 篇候选论文，可继续用 AI 生成结构化综述。`;
  return {
    interpreted_query: {
      main_question: query,
      expanded_terms: [...new Set(terms)].slice(0, 12),
      sub_questions: mode === "review" ? ["研究主题如何演化？", "常用方法和数据集有哪些？", "哪些方向值得继续关注？"] : [modeLabel, "哪些论文构成主要证据？"]
    },
    answer: {
      summary,
      key_points: [
        `当前模式：${modeLabel}`,
        years.length ? `候选年份覆盖 ${years[0]}–${years[years.length - 1]}。` : "当前筛选没有足够的年份信息。",
        "本地回退只使用论文元数据，不会生成库外论文或虚构摘要。"
      ],
      caveats: ["摘要和语义向量尚未完全补齐，结论需要打开证据论文核实。"]
    },
    results: resultItems,
    facets: {
      years: years.map((year) => ({ value: year, count: selected.filter((candidate) => candidate.year === year).length })),
      topics: [...topicCounts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8).map(([value, count]) => ({ value, count })),
      venues: [...venueCounts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8).map(([value, count]) => ({ value, count }))
    },
    view: {
      timeline: years.map((year) => ({ year, ids: selected.filter((candidate) => candidate.year === year).map((candidate) => candidate.id) })),
      comparison_columns: ["研究方法", "数据集", "指标", "优点", "局限"],
      gaps: mode === "gaps" ? ["摘要字段仍在补全，方法与数据集缺口需要进一步核实。", "建议沿引用薄弱区域查看 1–3 跳邻域。"] : []
    }
  };
}

function renderAiResults() {
  const status = $("aiSearchStatus");
  const results = $("aiSearchResults");
  const interpretation = $("aiQueryInterpretation");
  if (!state.aiResults.length) {
    results.hidden = true;
    results.innerHTML = "";
    interpretation.hidden = true;
    interpretation.innerHTML = "";
    updateWorkspaceControls();
    return;
  }
  const payload = state.aiPayload || {};
  const interpreted = payload.interpreted_query || {};
  interpretation.hidden = false;
  interpretation.innerHTML = `<strong>AI 对问题的理解</strong><span>${escapeHtml(interpreted.main_question || $("aiSearchInput").value)}</span><small>扩展词：${escapeHtml((interpreted.expanded_terms || []).join("、") || "暂无")}</small>`;
  results.hidden = false;
  const answer = payload.answer || {};
  const keyPoints = Array.isArray(answer.key_points) ? answer.key_points : [];
  const caveats = Array.isArray(answer.caveats) ? answer.caveats : [];
  const answerBlock = `<div class="ai-answer-block"><p class="ai-search-answer">${escapeHtml(answer.summary || state.aiAnswer || "")}</p>${keyPoints.length ? `<ul>${keyPoints.slice(0, 4).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>` : ""}${caveats.length ? `<p class="ai-caveat">${escapeHtml(caveats[0])}</p>` : ""}</div>`;
  const items = state.aiResults.slice(0, 12).map((result) => {
    const id = String(result.id || "");
    const checked = workspaceHas("compare", id) ? " checked" : "";
    const graph = result.graph_signals || {};
    return `<article class="ai-result" data-ai-result="${escapeHtml(id)}">
      <div class="ai-result-main"><label class="ai-compare-check"><input type="checkbox" data-compare-id="${escapeHtml(id)}"${checked} /><span></span></label><button type="button" class="ai-result-open" data-ai-open="${escapeHtml(id)}"><span class="ai-result-title">${escapeHtml(result.title || "未命名论文")}</span><span class="ai-result-meta">${escapeHtml([result.year, result.venue, `引用 ${formatCount(graph.citation_count ?? result.citations_count ?? 0)}`, result.reason].filter(Boolean).join(" · "))}</span></button></div>
      <div class="ai-result-actions"><button type="button" data-ai-focus="${escapeHtml(id)}">定位图中</button><button type="button" data-ai-neighborhood="${escapeHtml(id)}">1 跳邻域</button><button type="button" data-ai-favorite="${escapeHtml(id)}">${workspaceHas("favorites", id) ? "已收藏" : "收藏"}</button></div>
    </article>`;
  }).join("");
  const view = payload.view || {};
  const timeline = Array.isArray(view.timeline) && view.timeline.length ? `<div class="ai-research-view"><strong>时间线</strong><div class="ai-timeline">${view.timeline.slice(0, 8).map((item) => `<span><b>${escapeHtml(item.year)}</b>${formatCount((item.ids || []).length)} 篇</span>`).join("")}</div></div>` : "";
  const gaps = Array.isArray(view.gaps) && view.gaps.length ? `<div class="ai-research-view"><strong>研究空白提示</strong><ul>${view.gaps.slice(0, 3).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></div>` : "";
  const comparison = state.aiMode === "compare" ? renderComparisonTable() : "";
  results.innerHTML = `${answerBlock}<div class="ai-result-list">${items}</div>${comparison}${timeline}${gaps}`;
  results.querySelectorAll("[data-ai-open]").forEach((item) => item.addEventListener("click", () => focusAiResult(item.dataset.aiOpen)));
  results.querySelectorAll("[data-ai-focus]").forEach((item) => item.addEventListener("click", () => focusAiResult(item.dataset.aiFocus)));
  results.querySelectorAll("[data-ai-neighborhood]").forEach((item) => item.addEventListener("click", () => focusAiResult(item.dataset.aiNeighborhood, true)));
  results.querySelectorAll("[data-ai-favorite]").forEach((item) => item.addEventListener("click", () => {
    toggleWorkspaceId("favorites", item.dataset.aiFavorite);
    renderAiResults();
  }));
  results.querySelectorAll("[data-compare-id]").forEach((item) => item.addEventListener("change", () => {
    const id = item.dataset.compareId;
    if (item.checked && state.workspace.compare.length >= 5) { item.checked = false; $("aiSearchStatus").textContent = "对比列表最多保留 5 篇论文。"; return; }
    if (item.checked !== workspaceHas("compare", id)) toggleWorkspaceId("compare", id);
    renderAiResults();
  }));
  if (status && (state.aiAnswer || answer.summary)) status.textContent = `${AI_MODES[state.aiMode] || "AI 搜索"} · 找到 ${formatCount(state.aiResults.length)} 篇候选论文`;
  updateWorkspaceControls();
}

function renderComparisonTable() {
  const selectedIds = state.workspace.compare?.length ? new Set(state.workspace.compare) : new Set(state.aiResults.slice(0, 5).map((result) => String(result.id)));
  const rows = state.aiResults.filter((result) => selectedIds.has(String(result.id))).slice(0, 5).map((result) => {
    const node = state.data.nodes.find((item) => item.id === String(result.id));
    const topics = (node?.topics || []).slice(0, 2).join("、") || "元数据不足";
    const abstractState = node?.abstract ? "摘要已补充" : "元数据不足";
    return `<tr><th>${escapeHtml(shortTitle(node?.title || result.title || "未命名论文"))}</th><td>${escapeHtml(node?.year || result.year || "—")}</td><td>${escapeHtml(topics)}</td><td>${escapeHtml(formatCount(node?.citations_count || result.graph_signals?.citation_count || 0))}</td><td>${escapeHtml(abstractState)}</td></tr>`;
  }).join("");
  if (!rows) return "";
  return `<div class="ai-research-view ai-comparison-view"><strong>方法矩阵（当前元数据）</strong><div class="ai-table-wrap"><table><thead><tr><th>论文</th><th>年份</th><th>主题</th><th>引用</th><th>摘要</th></tr></thead><tbody>${rows}</tbody></table></div><small>方法、数据集和指标字段会在摘要补全后继续丰富；未提供的字段显示为“元数据不足”。</small></div>`;
}

function focusAiResult(id, neighborhood = false) {
  if (!id) return;
  selectNode(String(id));
  if (neighborhood) { state.neighborhoodId = String(id); applyFilters(); }
  const node = state.data.nodes.find((candidate) => candidate.id === String(id));
  if (node) $("aiSearchStatus").textContent = `${neighborhood ? "已展开邻域" : "已定位"}：${node.title}`;
}

function saveSearchHistory(query, mode, filters) {
  state.workspace.history = [{ query, mode, filters, at: new Date().toISOString() }, ...(state.workspace.history || [])].slice(0, 20);
  persistWorkspace();
}

function updateWorkspaceControls() {
  const count = state.workspace?.compare?.length || 0;
  if ($("aiCompareCount")) $("aiCompareCount").textContent = String(count);
  if ($("aiCompareButton")) $("aiCompareButton").disabled = count < 2;
  if ($("compareButton") && state.selectedId) $("compareButton").textContent = workspaceHas("compare", state.selectedId) ? "✓ 已加入对比" : "＋ 加入对比";
}

function downloadText(filename, content, type = "text/plain;charset=utf-8") {
  const blob = new Blob([content], { type });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

function exportAiResults() {
  if (!state.aiResults.length) { $("aiSearchStatus").textContent = "请先完成一次 AI 搜索。"; return; }
  const rows = state.aiResults.map((result) => {
    const node = state.data.nodes.find((item) => item.id === String(result.id));
    return { ...result, node };
  });
  const csv = ["id,title,year,venue,citations,reason", ...rows.map(({ node, ...result }) => [result.id, node?.title || result.title, node?.year || result.year, node?.venue || result.venue, node?.citations_count || result.citations_count || 0, result.reason || ""].map((value) => `"${String(value ?? "").replace(/"/g, '""')}"`).join(","))].join("\n");
  downloadText("ai-research-results.csv", `\ufeff${csv}`, "text/csv;charset=utf-8");
}

async function runAiSearch() {
  const input = $("aiSearchInput");
  const button = $("aiSearchButton");
  const query = input.value.trim();
  if (query.length < 2 || state.aiBusy) {
    if (query.length < 2) $("aiSearchStatus").textContent = "请输入至少 2 个字符。";
    return;
  }
  syncAiFilters();
  state.aiMode = $("aiSearchMode").value || "review";
  const candidates = buildAiCandidates(query, state.aiFilters);
  state.aiBusy = true;
  button.disabled = true;
  button.textContent = "搜索中…";
  $("aiSearchStatus").textContent = `正在从 ${formatCount(state.data.nodes.length)} 篇论文中筛选候选…`;
  state.aiSearchIds.clear();
  state.aiResults = [];
  state.aiAnswer = "";
  state.aiPayload = null;
  state.aiMode = "review";
  state.aiFilters = { year_from: state.yearMin, year_to: state.yearMax, topics: [], publication_types: [], venues: [], min_citations: 0 };
  state.aiPayload = null;
  renderAiResults();
  try {
    const response = await fetch("/api/ai-search", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ query, mode: state.aiMode, filters: state.aiFilters, limit: 12, candidates })
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || `AI 搜索失败（${response.status}）`);
    const known = new Set(state.data.nodes.map((node) => node.id));
    state.aiPayload = payload;
    state.aiResults = (payload.results || []).filter((result) => known.has(String(result.id))).slice(0, 12);
    state.aiSearchIds = new Set(state.aiResults.map((result) => String(result.id)));
    state.aiAnswer = payload.answer?.summary || payload.answer || "";
    saveSearchHistory(query, state.aiMode, state.aiFilters);
    if (!state.aiResults.length) throw new Error("没有找到足够匹配的论文，可以换一种描述。");
    $("searchInput").value = "";
    state.search = "";
    renderAiResults();
    applyFilters();
    selectNode(state.aiResults[0].id);
  } catch (error) {
    // OpenAI is optional: the same candidate set remains searchable locally.
    if (candidates.length) {
      state.aiPayload = localAiPayload(query, state.aiMode, state.aiFilters, candidates);
      state.aiResults = state.aiPayload.results.slice(0, 12);
      state.aiSearchIds = new Set(state.aiResults.map((result) => String(result.id)));
      state.aiAnswer = state.aiPayload.answer.summary;
      saveSearchHistory(query, state.aiMode, state.aiFilters);
      $("aiSearchStatus").textContent = `OpenAI 暂不可用，已使用本地元数据检索（${error.message || "API 未连接"}）。`;
      renderAiResults();
      applyFilters();
      selectNode(state.aiResults[0].id);
    } else {
      $("aiSearchStatus").textContent = error.message || "AI 搜索暂时不可用。";
      state.aiSearchIds.clear();
      applyFilters();
    }
  } finally {
    state.aiBusy = false;
    button.disabled = false;
    button.textContent = "搜索";
  }
}

function hasAwardSignal(node) {
  if (node.is_award === true || node.award === true) return true;
  const values = [node.awards, node.award, node.award_info, node.recognition].flatMap((value) => Array.isArray(value) ? value : [value]).filter(Boolean);
  const text = values.map((value) => typeof value === "string" ? value : JSON.stringify(value)).join(" ");
  return /(best paper|best student paper|outstanding paper|test of time|paper award|最佳论文|最佳学生论文|杰出论文|时间考验奖)/i.test(text);
}

function topologicalFrontierIds(candidates, clusterIds) {
  const ids = new Set(candidates.map((node) => node.id));
  const indegree = new Map(candidates.map((node) => [node.id, 0]));
  state.data.edges.forEach((edge) => {
    if (clusterIds.has(edge.source) && ids.has(edge.source) && ids.has(edge.target)) {
      indegree.set(edge.target, (indegree.get(edge.target) || 0) + 1);
    }
  });
  const firstLayer = candidates.filter((node) => indegree.get(node.id) === 0);
  const fallback = [...candidates].sort((a, b) => (b.year || 0) - (a.year || 0) || (b.citations_count || 0) - (a.citations_count || 0));
  return (firstLayer.length ? firstLayer : fallback).sort((a, b) => (b.year || 0) - (a.year || 0) || (b.citations_count || 0) - (a.citations_count || 0));
}

function computeFrontierIds(nodes, visibleIds) {
  const visibleNodeIds = new Set(nodes.map((node) => node.id));
  const clusterIds = visibleIds || visibleNodeIds;
  const clusterCandidates = state.data.nodes.filter((node) => clusterIds.has(node.id) && !node.external);
  const awardCandidates = clusterCandidates.filter(hasAwardSignal);
  state.frontierAwardIds = new Set(awardCandidates.filter((node) => visibleNodeIds.has(node.id)).map((node) => node.id));
  const hasClusterContext = Boolean(state.search || state.aiSearchIds.size || state.neighborhoodId || state.activeTopics.size);
  const topological = hasClusterContext && clusterCandidates.length > 20
    ? topologicalFrontierIds(clusterCandidates, clusterIds)
    : [];
  const limit = Math.min(12, Math.max(4, Math.ceil(clusterCandidates.length * 0.1)));
  const ordered = [...state.frontierAwardIds, ...topological.map((node) => node.id)];
  return new Set(ordered.filter((id, index, list) => list.indexOf(id) === index && visibleNodeIds.has(id)).slice(0, limit));
}

function labelIdsForView() {
  const ids = new Set();
  if (!state.search && !state.aiSearchIds.size && !state.neighborhoodId) return ids;
  const ranked = [...state.filteredNodes].sort((a, b) => (b.citations_count || 0) - (a.citations_count || 0));
  if (state.searchMatchIds.size) {
    ranked.filter((node) => state.searchMatchIds.has(node.id)).slice(0, 4).forEach((node) => ids.add(node.id));
  }
  if (state.aiSearchIds.size) {
    ranked.filter((node) => state.aiSearchIds.has(node.id)).slice(0, 6).forEach((node) => ids.add(node.id));
  }
  if (state.selectedId) ids.add(state.selectedId);
  ranked.filter((node) => !ids.has(node.id)).slice(0, 10).forEach((node) => ids.add(node.id));
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
  if (state.filteredNodes.length) setTimeout(fitGraph, 280);
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

function renderDetails(node) {
  if (!node) { $("detailsEmpty").hidden = false; $("detailsContent").hidden = true; $("neighborhoodButton").hidden = true; return; }
  $("detailsEmpty").hidden = true; $("detailsContent").hidden = false;
  $("neighborhoodButton").hidden = false;
  $("neighborhoodButton").textContent = state.neighborhoodId ? "退出邻域" : `在图中查看 ${state.neighborhoodDepth} 跳邻域`;
  $("detailYear").textContent = node.year;
  $("detailTitle").textContent = node.title;
  $("detailVenue").textContent = [node.venue, node.external ? "外部引用节点" : "收录论文"].filter(Boolean).join(" · ");
  $("detailAuthors").textContent = (node.authors || []).join(" · ") || "作者信息待补充";
  const completeness = node.metadata_completeness ? ` · 元数据完整度 ${Math.round(node.metadata_completeness * 100)}%` : "";
  $("detailProvenance").textContent = `来源 · ${node.source || "OpenAlex / arXiv 公共元数据快照"}${completeness}`;
  $("detailTags").innerHTML = (node.topics || []).map((topic) => `<span class="detail-tag">${escapeHtml(topic)}</span>`).join("");
  $("detailAbstract").textContent = node.abstract || "暂无摘要。当前页面不会根据标题猜测摘要，后续公开元数据同步后再补充。";
  $("favoriteButton").textContent = workspaceHas("favorites", node.id) ? "★ 已收藏" : "☆ 收藏";
  $("compareButton").textContent = workspaceHas("compare", node.id) ? "✓ 已加入对比" : "＋ 加入对比";
  $("readButton").textContent = state.workspace.read?.[node.id] ? "✓ 已读" : "标记已读";
  $("detailNote").value = state.workspace.notes?.[node.id] || "";
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
  updateWorkspaceControls();
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

function resetView() {
  state.selectedId = null;
  state.neighborhoodId = null;
  state.neighborhoodDepth = 1;
  state.search = "";
  state.aiSearchIds.clear();
  state.aiResults = [];
  state.aiAnswer = "";
  $("searchInput").value = "";
  $("aiSearchInput").value = "";
  $("aiSearchMode").value = "review";
  $("aiYearFrom").value = state.yearMin;
  $("aiYearTo").value = state.yearMax;
  $("aiMinCitations").value = "0";
  $("aiTopicFilter").value = "";
  $("aiPublicationType").value = "";
  $("aiVenueFilter").value = "";
  $("aiSearchStatus").textContent = "";
  renderAiResults();
  state.activeTopics.clear();
  document.querySelectorAll(".topic-chip").forEach((chip) => { chip.classList.remove("active"); chip.setAttribute("aria-pressed", "false"); });
  $("showExternal").checked = true;
  state.showExternal = true;
  state.renderLimit = 100;
  $("renderLimit").value = "100";
  $("neighborhoodDepth").value = "1";
  $("yearMin").value = state.yearMin;
  $("yearMax").value = state.yearMax;
  $("yearValue").textContent = `${state.yearMin} — ${state.yearMax}`;
  updateDatasetStats();
  applyFilters();
}
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
