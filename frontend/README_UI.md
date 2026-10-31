# AI Research Graph 前端原型

这是一个不依赖构建工具的静态前端原型，打开 `index.html` 即可查看界面。默认的 `data.sample.json.gz` 是真实 OpenAlex 联合快照（当前 9,498 个节点、45,287 条有向引用边），由两层递归引用图和按年份均衡的 Computer Science 目录样本组成；它不是近十年论文的全量索引。页面会在浏览器端解压 gzip 快照。为了让 `fetch("data.sample.json.gz")` 在浏览器中工作，建议在 `frontend/` 目录启动任意静态文件服务器，例如：

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

`source -> target` 表示前者引用后者。`external: true` 用于时间窗口外但仍被引用的基础论文；左侧开关可以隐藏这些节点。界面会根据节点主题、年份和搜索词重建可视子图，并支持拖动、缩放和点击节点查看相邻论文。

完整快照的节点数和引用边数由 `stats` 提供并始终显示在左侧；目录覆盖分母和样本比例由 `coverage` 提供并显示在数据说明中。为了保持力导图流畅，画布一次最多渲染 200 个节点。选中任意论文后，可以切换到它的一跳邻域，查看该论文的直接引用和被引关系。

## 与后端集成

后续可以把 `loadData()` 替换为 `/api/graph?from=2016&to=2026` 请求，或在服务端把 OpenAlex/arXiv 数据转换成上述格式。页面不依赖任何 React/构建工具，适合先部署为静态 Site，再逐步接入 REST API 或 MCP。
