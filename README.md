# AI Research Graph

AI Research Graph 是一个以真实论文引用边为核心的 AI / CS 文献关系图网站。每个论文节点来自 OpenAlex，`source -> target` 表示前者在 `referenced_works` 中引用后者。网站先提供可交互的图谱工作台，后续可将同一数据层接入 REST API 和 MCP。

## 运行网站

在项目根目录执行：

```powershell
cd frontend
python -m http.server 4173
```

打开 `http://localhost:4173/`。前端是 buildless HTML/CSS/JS，使用 D3 CDN 绘制有向图。

## 更新论文数据

默认样本为真实 OpenAlex 数据，当前用于端到端演示：11 个论文节点、14 条有向引用边。采集脚本支持按搜索词、年份和分页扩展：

```powershell
python data/collect_openalex_graph.py `
  --search "artificial intelligence machine learning" `
  --from-year 2016 --to-year 2026 --pages 10 --per-page 100 `
  --include-external --out data/ai_graph.json
```

将生成的 JSON 复制到 `frontend/data.sample.json` 后刷新网站即可。当前样本是可复现切片，不代表近十年 AI/CS 论文的完整覆盖；大规模版本需要持续分页、去重、覆盖率审计和增量同步。

## 目录

- `frontend/`：网站界面和交互式 D3 图谱
- `data/`：OpenAlex 采集脚本、数据模式和真实样本
- `UX_NOTES.md`：图谱方向语义、筛选、溯源和性能边界
- `AI_Research_Graph_Roadmap_v1.0.docx`：项目路线图
