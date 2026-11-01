# AI Research Graph

AI Research Graph 是一个以真实论文引用边为核心的 AI / CS 文献关系图网站。`source -> target` 表示前者引用后者；边保留 OpenAlex `referenced_works` 或 CitationCS `ref_lst` 的来源字段。网站提供可交互的图谱工作台，后续可将同一数据层接入 REST API 和 MCP。

## 运行网站

图谱界面源文件仍位于 `frontend/`，可以用静态服务器快速查看 UI：

```powershell
cd frontend
python -m http.server 4173
```

打开 `http://localhost:4173/`。前端是 buildless HTML/CSS/JS，使用 D3 CDN 绘制有向图。

Hosted Site 使用 Vinext Worker 提供同源的 `/api/ai-search` 服务端接口。AI 搜索框会把当前快照中的候选论文发送到 OpenAI API 进行语义排序；`OPENAI_API_KEY` 只通过 Sites 的环境变量注入 Worker，不会进入浏览器代码。部署形态的本地构建命令为：

```powershell
npm ci
node "$env:USERPROFILE\.codex\plugins\cache\openai-curated-remote\sites\1.1.2\scripts\build-site.mjs"
```

## 更新论文数据

默认快照为 **200,000 个近十年 AI 优先论文节点、435,731 条有向引用边**。节点来自 CitationCS 的 Computer Science 记录，优先保留 Artificial Intelligence、machine learning、deep learning、computer vision、natural language、robotics 和 neural 等主题，并按年份比例抽样；边只复制快照中的真实 `ref_lst` 引用关系。网站保留快照中的全部节点，画布初始按引用量显示前 100 个节点，也可切换到 200、500 或 1,000 个节点；搜索、主题筛选或邻域展开后的当前簇若超过 20 个节点，会按引用边方向取拓扑排序最前层，并叠加数据源提供的获奖论文信号，用黄色大节点标记前沿论文；搜索还会展开相关引用节点并显示少量标题，年份筛选和 1–3 跳邻域用于继续探索关系。

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
  --citationcs data/citationcs_ai_cs_slice_50000.json.gz `
  --out data/ai_cs_graph_citationcs_60000.json.gz
```

CitationCS 离线底座（Zenodo 记录 10.5281/zenodo.16537950）可以在 OpenAlex API 配额不可用时扩展 CS 引用网络。下载和递推命令如下；原始 ZIP 保存在仓库外，避免进入 Git：

```powershell
python data/download_citationcs.py `
  --out D:\research\citation_cache\citation_cs.zip
python data/import_citationcs.py `
  --archive D:\research\citation_cache\citation_cs.zip `
  --existing data/ai_cs_graph_citationcs_60000.json.gz `
  --out data/citationcs_ai_cs_slice_50000.json.gz `
  --depth 2 --max-seeds 10000 --max-nodes 50000
```

当前网站使用的 200,000 篇快照可由本地 CitationCS 压缩档案重建。它只保留目标窗口内的 AI 优先 Computer Science 记录，并把旧快照中重叠论文的 OpenAlex 摘要、 DOI、奖项和引用计数作为补充元数据：

```powershell
python data/build_recent_ai_cs_catalogue.py `
  --archive D:\research\citation_cache\citation_cs.parallel.zip `
  --existing data/ai_cs_graph_citationcs_60000.json.gz `
  --target-nodes 200000 `
  --out data/ai_cs_graph_200000.json.gz
```

如需合并多个主题切片，也可以使用：

```powershell
python data/merge_openalex_graphs.py `
  --inputs data/*_graph_*.json `
  --out data/ai_cs_graph_expanded.json
```

合并器会按 OpenAlex ID 去重，并从 AI/CS 种子沿 `source -> target` 引用边计算遍历层级；每条边保留 `provenance_sources` 和 `source_system`，不会把 CitationCS 边伪装成 OpenAlex 边。

将生成的 JSON 压缩为 `frontend/data.sample.json.gz` 后刷新网站即可；页面会在浏览器端解压。当前 200,000 篇快照覆盖 CitationCS 近十年 AI 优先候选中的约 96.19%，但仍是可复现的有限样本，不代表近十年 AI/CS 论文的完整覆盖；完整 OpenAlex 目录导入仍需要 API key、断点续传、去重和持续覆盖率审计。

## 目录

- `frontend/`：网站界面和交互式 D3 图谱
- `data/`：OpenAlex/CitationCS 采集脚本、数据模式和真实样本
- `UX_NOTES.md`：图谱方向语义、筛选、溯源和性能边界
- `AI_Research_Graph_Roadmap_v1.0.docx`：项目路线图
