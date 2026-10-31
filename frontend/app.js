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
  activeTopics: new Set(),
  yearMin: 2016,
  yearMax: 2026,
  showExternal: true,
  showLabels: true,
  search: "",
  zoom: null,
  simulation: null,
  renderNodes: [],
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
  const nodes = (raw.nodes || []).map((node) => ({
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
  }));
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
  $("datasetNote").textContent = `完整快照 ${formatCount(state.stats.nodes)} 节点 / ${formatCount(state.stats.edges)} 边${timestamp}${coverageNote}；这是可追溯的有限样本，画布是受限子图，最多渲染 200 个节点，可用搜索或一跳邻域查看局部关系。`;
}

function formatCount(value) {
  return new Intl.NumberFormat("zh-CN").format(Number(value) || 0);
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
  $("searchInput").addEventListener("input", (event) => { state.search = event.target.value.trim().toLowerCase(); applyFilters(); });
  $("showExternal").addEventListener("change", (event) => { state.showExternal = event.target.checked; applyFilters(); });
  $("showLabels").addEventListener("change", (event) => { state.showLabels = event.target.checked; labelLayer.style("display", state.showLabels ? null : "none"); });
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
  let visibleIds = new Set(state.data.nodes.filter((node) => {
    const haystack = [node.title, node.venue, ...(node.authors || []), ...(node.topics || [])].join(" ").toLowerCase();
    const matchesSearch = !state.search || haystack.includes(state.search);
    // External foundation nodes can sit outside the time window but remain
    // visible when their cited edge is part of the current graph.
    const matchesYear = node.external || (node.year >= state.yearMin && node.year <= state.yearMax);
    const matchesTopic = !state.activeTopics.size || [...state.activeTopics].every((topic) => node.topics.includes(topic));
    return matchesSearch && matchesYear && matchesTopic && (state.showExternal || !node.external);
  }).map((node) => node.id));
  if (state.neighborhoodId) {
    const focus = nodeById.get(state.neighborhoodId);
    if (!focus || (!state.showExternal && focus.external)) {
      state.neighborhoodId = null;
    } else {
      // A neighborhood view intentionally relaxes the text/year filters for
      // adjacent nodes so a search result can be followed into its citations.
      const neighborhoodIds = new Set([state.neighborhoodId]);
      state.data.edges.forEach((edge) => {
        if (edge.source === state.neighborhoodId) neighborhoodIds.add(edge.target);
        if (edge.target === state.neighborhoodId) neighborhoodIds.add(edge.source);
      });
      visibleIds = new Set([...neighborhoodIds].filter((id) => {
        const node = nodeById.get(id);
        return node && (state.showExternal || !node.external);
      }));
    }
  }
  // Keep the visual layer bounded for large snapshots while preserving the
  // full dataset for the eventual API and ingestion pipeline.
  let filteredNodes = state.data.nodes.filter((node) => visibleIds.has(node.id));
  if (filteredNodes.length > 200) {
    const ranked = [...filteredNodes].sort((a, b) => (b.citations_count || 0) - (a.citations_count || 0));
    const focus = state.neighborhoodId ? nodeById.get(state.neighborhoodId) : null;
    filteredNodes = focus ? [focus, ...ranked.filter((node) => node.id !== focus.id).slice(0, 199)] : ranked.slice(0, 200);
  }
  const finalIds = new Set(filteredNodes.map((node) => node.id));
  const filteredEdges = state.data.edges.filter((edge) => finalIds.has(edge.source) && finalIds.has(edge.target));
  state.filteredNodes = filteredNodes; state.filteredEdges = filteredEdges;
  if (state.selectedId && !finalIds.has(state.selectedId)) state.selectedId = null;
  if (state.neighborhoodId && !finalIds.has(state.neighborhoodId)) state.neighborhoodId = null;
  const matchCount = [...visibleIds].filter((id) => !nodeById.get(id)?.external).length;
  $("resultCount").textContent = formatCount(matchCount);
  $("paperStat").textContent = filteredNodes.length;
  $("edgeStat").textContent = filteredEdges.length;
  $("canvasCoverage").textContent = state.neighborhoodId
    ? `邻域渲染 ${filteredNodes.length} / 全量 ${formatCount(state.stats.nodes)} · 上限 200`
    : `渲染 ${filteredNodes.length} / 全量 ${formatCount(state.stats.nodes)} · 上限 200`;
  $("neighborhoodToggle").hidden = !state.neighborhoodId;
  $("neighborhoodButton").hidden = !state.selectedId;
  $("neighborhoodButton").textContent = state.neighborhoodId ? "退出 1 跳邻域" : "在图中查看 1 跳邻域";
  $("emptyState").hidden = filteredNodes.length > 0;
  $("viewSummary").textContent = state.neighborhoodId
    ? `1 跳邻域 · ${filteredNodes.length} 个节点`
    : (state.search || state.activeTopics.size ? `${filteredNodes.length} 个匹配节点` : `全量 ${formatCount(state.stats.nodes)} · 画布 ${filteredNodes.length}`);
  renderGraph();
  renderDetails(state.selectedId ? state.data.nodes.find((node) => node.id === state.selectedId) : null);
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
    .attr("aria-label", (node) => `${node.title}，${node.year} 年，点击查看论文详情`)
    .on("keydown", (event, node) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectNode(node.id);
      }
    })
    .call(d3.drag().on("start", dragStart).on("drag", dragged).on("end", dragEnd));
  nodeEnter.append("circle").attr("class", "node-circle").attr("r", (node) => node.external ? 4 : Math.min(10, 5 + ((node.citations_count || 0) / 30))).on("click", (event, node) => { event.stopPropagation(); selectNode(node.id); });
  const mergedNodes = nodeEnter.merge(nodeSelection);
  mergedNodes.attr("aria-label", (node) => `${node.title}，${node.year} 年，${node.external ? "外部引用节点" : "收录论文"}，点击查看论文详情`)
    .select("circle").attr("class", (node) => `node-circle ${node.external ? "external" : `core topic-${topicIndex(node)}`}${state.selectedId === node.id ? " selected" : ""}`);
  const labelSelection = labelLayer.selectAll("text").data(nodes, (node) => node.id);
  labelSelection.exit().remove();
  labelSelection.enter().append("text").attr("class", "node-label").merge(labelSelection).text((node) => shortTitle(node.title)).classed("active", (node) => state.selectedId === node.id);
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
  $("neighborhoodButton").textContent = state.neighborhoodId ? "退出 1 跳邻域" : "在图中查看 1 跳邻域";
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

function resetView() { state.selectedId = null; state.neighborhoodId = null; state.search = ""; $("searchInput").value = ""; state.activeTopics.clear(); document.querySelectorAll(".topic-chip").forEach((chip) => { chip.classList.remove("active"); chip.setAttribute("aria-pressed", "false"); }); $("showExternal").checked = true; state.showExternal = true; $("yearMin").value = state.yearMin; $("yearMax").value = state.yearMax; $("yearValue").textContent = `${state.yearMin} — ${state.yearMax}`; applyFilters(); }
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
