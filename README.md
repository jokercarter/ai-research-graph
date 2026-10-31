# AI Research Graph

AI Research Graph 是一个以真实论文引用边为核心的 AI / CS 文献关系图网站。`source -> target` 表示前者引用后者；边保留 OpenAlex `referenced_works` 或 CitationCS `ref_lst` 的来源字段。网站提供可交互的图谱工作台，后续可将同一数据层接入 REST API 和 MCP。

## 运行网站

在项目根目录执行：

```powershell
cd frontend
python -m http.server 4173
```

打开 `http://localhost:4173/`。前端是 buildless HTML/CSS/JS，使用 D3 CDN 绘制有向图。

## 更新论文数据

默认快照为 OpenAlex 与 CitationCS 的可追溯联合数据，当前为 **33,425 个论文节点、172,138 条有向引用边**：包含 OpenAlex 递归图、2,200 个年度 CS 目录样本、多主题 AI/CS 切片，以及 CitationCS 的 24,881 节点/127,279 边两层递推子图。网站保留快照中的全部节点，画布默认最多渲染 200 个节点以保持交互流畅；搜索、年份筛选和一跳邻域用于探索其余关系。

```powershell
python data/collect_openalex_graph.py `
  --search "artificial intelligence machine learning" `
  --from-year 2016 --to-year 2026 --pages 10 --per-page 100 `
  --include-external --out data/ai_graph.json
```

当前递归快照先搜索近年的 AI/CS 论文，再沿每篇论文的 `referenced_works` 批量抓取两层引用论文：

```powershell
python data/collect_recursive_graph.py `
  --target-nodes 7500 --seed-per-page 200 --seed-limit 1000 `
  --max-depth 2 --max-fetch-per-layer 6000 `
  --out data/recursive_graph_10000.json
```

如需生成当前网站使用的“递归图 + 年度 CS 目录样本”联合快照：

```powershell
python data/merge_recursive_catalog.py `
  --recursive data/recursive_graph_10000.json `
  --catalog data/cs_field_catalog_2200.json `
  --citationcs data/citationcs_ai_cs_slice.json.gz `
  --out data/ai_cs_graph_citationcs_35000.json.gz
```

CitationCS 离线底座（Zenodo 记录 10.5281/zenodo.16537950）可以在 OpenAlex API 配额不可用时扩展 CS 引用网络。下载和递推命令如下；原始 ZIP 保存在仓库外，避免进入 Git：

```powershell
python data/download_citationcs.py `
  --out D:\research\citation_cache\citation_cs.zip
python data/import_citationcs.py `
  --archive D:\research\citation_cache\citation_cs.zip `
  --existing data/ai_cs_graph_citationcs_35000.json.gz `
  --out data/citationcs_ai_cs_slice.json.gz `
  --depth 2 --max-seeds 7000 --max-nodes 25000
```

如需合并多个主题切片，也可以使用：

```powershell
python data/merge_openalex_graphs.py `
  --inputs data/*_graph_*.json `
  --out data/ai_cs_graph_expanded.json
```

合并器会按 OpenAlex ID 去重，并从 AI/CS 种子沿 `source -> target` 引用边计算遍历层级；每条边保留 `provenance_sources` 和 `source_system`，不会把 CitationCS 边伪装成 OpenAlex 边。

将生成的 JSON 压缩为 `frontend/data.sample.json.gz` 后刷新网站即可；页面会在浏览器端解压。当前联合快照 `data/ai_cs_graph_citationcs_35000.json.gz` 仍明确显示 OpenAlex CS 目录的 5,435,295 篇期望分母和 2,200 篇目录样本（0.0405%），并显示 CitationCS 的数据集总量与当前子图规模。它是可复现的有限样本，不代表近十年 AI/CS 论文的完整覆盖；完整 OpenAlex 目录导入仍需要 API key、断点续传、去重和持续覆盖率审计。

## 目录

- `frontend/`：网站界面和交互式 D3 图谱
- `data/`：OpenAlex/CitationCS 采集脚本、数据模式和真实样本
- `UX_NOTES.md`：图谱方向语义、筛选、溯源和性能边界
- `AI_Research_Graph_Roadmap_v1.0.docx`：项目路线图
