# AI Research Graph（简化版网站）

AI/CS 论文搜索与升温方向网站。纯静态页面 + 分片数据，访问者只下载用到的小文件。

## 本地打开（用包月订阅，不需要 API key）

电脑上装好并登录 Claude Code（Claude Pro/Max）或 Codex CLI（ChatGPT Plus/Pro）后，直接运行 `python serve_local.py`，问答页会出现"Claude（订阅）"/"Codex（订阅）"。只限自己本地使用；公开部署必须用下面的 API key 方式。

## 本地打开（用 API key）

在项目根目录新建 `.env.local`（已被 .gitignore 忽略），写入任意一个或两个 key：

```
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-sonnet-5-5
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4.1-mini
AI_PROVIDER=anthropic
```

然后运行 `python serve_local.py`，打开 http://localhost:4173/ 。两个 key 都配置时，问答页右上角可以切换模型。不配 key 时可用 `AI_PROVIDER=mock` 看演示效果。

部署到 Sites 时，在站点环境变量里设置同样的变量即可（服务端读取，不会进入浏览器）。

## 只看静态页面（不带 AI）

```powershell
cd public\research
python -m http.server 4173
```

浏览器打开 http://localhost:4173/ 。本地预览没有服务端 AI 接口：中文检索会自动用内置词典翻译，"AI 研究助手"会提示不可用；部署到 Sites 并配置 `OPENAI_API_KEY` 后两者都会走 AI。

## 页面

- 首页：搜索框、正在升温的研究方向、按领域看最新论文
- 搜索：BM25 关键词检索（标题权重更高，支持短语），可按年份、arXiv 主分类筛选，可按相关度或时间排序；"AI 研究助手"基于前 30 篇结果做综述/比较/找空白
- 论文页：摘要、arXiv/PDF/Semantic Scholar/DOI 链接、BibTeX、相关论文
- 升温方向：近 3 个月出现占比增长最快、且常出现在标题中的技术短语，含每月趋势和代表论文
- AI 问答：模型先把问题改写成检索词，浏览器在站内索引里检索并取最相关 14 篇论文的完整摘要，模型只根据这些论文回答，并用 [n] 标出处；支持多轮追问、延伸问题、"围绕某篇论文提问"、一键收藏参考论文
- 我的文献：收藏、关注方向，导出 BibTeX/CSV，备份/导入（只存在本地浏览器）

## 每月更新

```powershell
pip install numpy
python data\pipeline\monthly_update.py
```

脚本会增量拉取 arXiv 新论文（不需要 API key），重建最近 24 个月的数据到 `public/research/data`，然后照常部署。

## 数据文件

见 `data/pipeline/build_simple.py` 顶部说明：`manifest.json`、`meta.bin.gz`、`t/`（检索分片）、`l/`（结果卡片）、`p/`（论文详情）、`k/`（arXiv 编号映射）、`trends.json.gz`。
