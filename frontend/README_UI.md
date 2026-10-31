# AI Research Graph 前端原型

这是一个不依赖构建工具的静态前端原型，打开 `index.html` 即可查看界面。默认的 `data.sample.json.gz` 是 OpenAlex 与 CitationCS 的联合快照（当前 56,832 个节点、330,245 条有向引用边），包含两层递归引用图、按年份均衡的 CS 目录样本、多主题 AI/CS 切片和 CitationCS 两层引用子图；它不是近十年论文的全量索引。页面会在浏览器端解压 gzip 快照。为了让 `fetch("data.sample.json.gz")` 在浏览器中工作，建议在 `frontend/` 目录启动任意静态文件服务器，例如：

```powershell
python -m http.server 4173
```

然后访问 `http://localhost:4173/`。

## 数据接口

将 `data.sample.json.gz` 替换为采集管线 JSON 的 gzip 快照即可。前端同时兼容采集管线的 OpenAlex 字段（`publication_year`、对象形式的 `authors` / `topics`、`is_external_reference`、`cited_by_count`）和下面的简化格式：

```json
{
  "stats": {
    "nodes": 1000,
    "edges": 9000,
    "topics": 48
  },
  "provenance": {
    "provider": "OpenAlex",
    "generated_at": "2026-10-31"
  },
  "nodes": [
    {
      "id": "openalex-or-internal-id",
      "title": "Paper title",
      "year": 2024,
      "venue": "Conference or journal",
      "authors": ["Author"],
      "topics": ["NLP"],
      "abstract": "Optional abstract",
      "url": "https://...",
      "citations_count": 42,
      "external": false
    }
  ],
  "edges": [
    {"source": "newer-paper-id", "target": "cited-paper-id"}
  ]
}
```

`source -> target` 表示前者引用后者。`external: true` 用于时间窗口外但仍被引用的基础论文；左侧开关可以隐藏这些节点。节点的 `source` 和边的 `source_system` 用于区分 OpenAlex 与 CitationCS。界面会根据节点主题、年份和搜索词重建可视子图，并支持拖动、缩放和点击节点查看相邻论文。

完整快照的节点数和引用边数由 `stats` 提供并始终显示在左侧；目录覆盖分母、CitationCS 数据集规模和样本比例由 `coverage` 提供并显示在数据说明中。初始画布按引用量显示前 100 个节点；也可以在“图谱范围”中切换到 200、500 或 1,000 个节点。搜索论文时，画布会加入它的直接引用关系，并只显示少量相关论文标题；选中任意论文后，还可以选择 1、2 或 3 跳邻域继续展开关系。

## 与后端集成

后续可以把 `loadData()` 替换为 `/api/graph?from=2016&to=2026` 请求，或在服务端把 OpenAlex/arXiv 数据转换成上述格式。页面不依赖任何 React/构建工具，适合先部署为静态 Site，再逐步接入 REST API 或 MCP。
