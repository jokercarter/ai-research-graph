# AI Research Graph

面向科研人员的 **AI / 计算机科学论文搜索网站**：快速找到某个方向的关键论文和最新进展，从一篇论文出发找到相关工作，并用 Claude 基于检索到的论文回答研究问题。

- 收录 **276,668 篇** 2024-11 至 2026-10 在 arXiv 首次发布的 AI 相关论文（全部带摘要），**每月更新一次**
- 网站是纯静态页面 + 分片数据：访问者只下载用到的小文件（一次搜索约 1–3 MB），手机也能用
- AI 功能只使用 **Claude**：本地可用 Claude 账号（邮箱）登录，用包月订阅；公开部署可用 API key

---

## 目录

- [功能](#功能)
- [快速开始（本地）](#快速开始本地)
- [连接 Claude](#连接-claude)
- [部署到网站](#部署到网站)
- [每月更新数据](#每月更新数据)
- [工作原理](#工作原理)
- [项目结构](#项目结构)
- [服务端接口](#服务端接口)
- [隐私与安全](#隐私与安全)
- [已知局限与后续计划](#已知局限与后续计划)
- [旧版组件](#旧版组件)

---

## 功能

| 页面 | 地址 | 说明 |
|---|---|---|
| 首页 | `#/` | 搜索框、正在升温的研究方向、按领域浏览入口；关注的方向会显示在这里 |
| 搜索 | `#/search?q=…` | 关键词检索（标题权重更高，支持短语），支持中文提问；按年份、arXiv 主分类筛选，按相关度或时间排序 |
| 论文详情 | `#/paper/<arXiv 编号>` | 摘要、作者、分类、arXiv / PDF / Semantic Scholar / DOI 链接、复制 BibTeX、10 篇相关论文、"问 AI 关于这篇论文" |
| 升温方向 | `#/topics`、`#/trend/<id>` | 80 个近 3 个月出现占比增长最快的技术短语（如 *on-policy distillation*、*VLA policies*），含每月趋势图和代表论文 |
| 领域最新 | `#/category/<分类>` | 某个 arXiv 主分类（如 cs.CV）按时间倒序的全部论文 |
| AI 问答 | `#/ask` | Claude 基于站内检索到的论文回答问题，每条结论带 `[n]` 出处；多轮追问、延伸问题、一键收藏参考论文；可切换 Sonnet / Opus / Haiku |
| 我的文献 | `#/library` | 收藏的论文、关注的方向；导出 BibTeX / CSV，备份与导入工作区 |
| 数据说明 | `#/about` | 收录范围、数据来源、算法说明、隐私说明 |

搜索结果页还有"AI 研究助手"（梳理综述、找相关论文、比较方法、找研究空白、找前沿），基于前 30 条结果生成；它需要网站服务器配置了 Claude API key，本地预览请使用 AI 问答页。

收藏、关注和对话记录只保存在当前浏览器（localStorage），不需要注册账号。

---

## 快速开始（本地）

**需要**：Windows / macOS / Linux，Python 3.10+（只用标准库，不用装依赖）。

### Windows：双击启动

双击项目根目录的 **`启动网站.bat`**。它会：

1. 启动本地服务（默认 `http://localhost:4173/`，端口被占用时自动换下一个）
2. 用默认浏览器打开 AI 问答页，并弹出"连接 Claude"窗口

关闭黑色命令行窗口即停止网站。

### 命令行启动

```bash
python serve_local.py                      # 打开 http://localhost:4173/
python serve_local.py --open "#/ask"       # 启动后自动打开某个页面
PORT=8080 python serve_local.py            # 指定端口（Windows PowerShell：$env:PORT=8080）
```

只想看静态页面（不带 AI）也可以：`cd public/research && python -m http.server 4173`。这时中文检索会改用内置词典翻译。

---

## 连接 Claude

点页面右上角 **"连接 Claude"**，选择一种方式：

| 方式 | 适用 | 费用 | 说明 |
|---|---|---|---|
| **用 Claude 账号（邮箱）登录** | 本地使用 | Claude Pro / Max 包月订阅 | 点"安装并用邮箱登录"：未安装 Claude Code 时自动安装，然后在浏览器里输入邮箱和验证码登录。完成后点"我已登录，检查状态" |
| **我的 Claude API key** | 本地或公开网站的访客 | 按用量计费 | key 只保存在访客自己的浏览器，请求从浏览器**直接**发给 Anthropic，不经过本网站服务器 |
| **网站提供的模型** | 公开部署 | 站点运营者付费 | 服务器配置了 `ANTHROPIC_API_KEY` 时出现 |

**切换模型**：AI 问答页顶部的"模型"下拉框可在 **Sonnet 5.5**（默认，均衡）、**Opus 5.5**（最强，较慢、更耗额度）、**Haiku 5.5**（最快）之间切换，下一个问题生效；每条回答下方会显示实际使用的模型。邮箱登录模式下，第一步"生成检索词"固定用 Haiku 以节省时间和额度。

> 包月订阅只供本人使用。部署成公开网站后，服务器上没有你登录的 Claude Code，访客需要用 API key。

### 本地配置文件 `.env.local`（可选）

放在项目根目录（已被 `.gitignore` 忽略）：

```ini
ANTHROPIC_API_KEY=sk-ant-...        # 让本地服务器以"网站提供的模型"方式调用 Claude
ANTHROPIC_MODEL=claude-sonnet-5-5   # 默认模型
AI_PROVIDER=mock                    # 演示模式：不调用任何模型，用于检查界面
```

---

## 部署到网站

网站外层是 Vinext（Next.js 风格）应用，运行在 Cloudflare Workers（OpenAI Sites 托管）上；首页 `app/page.tsx` 以 iframe 加载 `public/research/index.html`，`public/research/data` 中的分片作为静态资源发布。

```powershell
npm ci
node "$env:USERPROFILE\.codex\plugins\cache\openai-curated-remote\sites\1.1.2\scripts\build-site.mjs"
```

**环境变量**（在站点设置中配置，只在服务端使用，不会进入浏览器）：

| 变量 | 必需 | 说明 |
|---|---|---|
| `ANTHROPIC_API_KEY` | 否 | 配置后访客可直接使用"网站提供的模型"，搜索页的 AI 研究助手、中文检索改写也会走 Claude |
| `ANTHROPIC_MODEL` | 否 | 默认 `claude-sonnet-5-5` |

不配置 key 时网站照常可用：搜索、浏览、升温方向都不依赖模型；AI 问答由访客填写自己的 key；中文检索回退到内置词典。

服务端的 AI 接口有按访客的限流（问答每分钟 10 次、检索词改写 30 次、研究助手 8 次），避免公开后被刷费用。

---

## 每月更新数据

在自己的电脑上运行（不需要任何 API key）：

```bash
pip install numpy
python data/pipeline/monthly_update.py
```

它会：

1. 通过 arXiv OAI-PMH **增量**拉取上次运行之后新增或更新的论文（状态保存在 `data/work/`）
2. 重建最近 **24 个完整月**的网站数据到 `public/research/data`（文档、检索索引、升温方向、清单）

通常 10–20 分钟。之后照常提交并部署。首次在一台新电脑上运行时会从 2016-11 开始全量拉取，耗时较长（约 2–3 小时）。

也可以分阶段手动运行：

```bash
python data/pipeline/harvest_arxiv.py --until 2026-11-30
python data/pipeline/build_simple.py all --start 2024-12-01 --end 2026-11-30
# 或单独运行某个阶段：docs | cards | index | shards | trends1 | trends2
```

---

## 工作原理

### 数据

- **来源**：arXiv OAI-PMH（`set=cs`），保留主分类或交叉分类属于 cs.AI、cs.LG、cs.CL、cs.CV、cs.RO、cs.IR、cs.MA、cs.NE、stat.ML 等 AI 相关方向的论文
- **发布时间**：按 arXiv 编号中的年月判断首次发布月份（OAI 的 `created` 字段对应最新版本，不能用来判断首发）；同一论文多个版本只保留最新元数据
- **收录窗口**：最近 24 个完整月

### 网站数据文件（`public/research/data`）

| 文件 | 内容 |
|---|---|
| `manifest.json` | 快照版本、论文数、分片大小、分类名称 |
| `meta.bin.gz` | 每篇论文 4 字节：年份、月份、arXiv 主分类、是否本月新增（筛选和排序用） |
| `t/NNNN.json.gz` | 2,048 个检索分片：按词项哈希分配，存每个词的倒排表（论文编号差值 + 量化 BM25 分数） |
| `l/NNNN.json.gz` | 结果卡片（每片 400 篇）：标题、日期、分类、作者、摘要片段 |
| `p/NNNN.json.gz` | 论文详情（每片 200 篇）：完整摘要、作者、分类、备注、DOI |
| `k/XX.json.gz` | arXiv 编号 → 本快照中的论文编号（保证链接在每月更新后仍有效） |
| `trends.json.gz` | 升温方向、每月论文数、各分类月度统计 |

论文按 arXiv 主分类分组、组内按时间倒序编号，因此"领域最新"页面直接读取连续的编号区间。

### 检索与相关论文

- 字段加权 BM25：标题词权重 3 倍，摘要权重 1 倍；标题中的相邻词对（如 *diffusion model*）单独建索引以匹配短语；每个词最多保留 1,500 篇得分最高的论文
- 排序时奖励覆盖了更多查询词的论文；中文查询先由 Claude 改写成英文检索词，失败时用内置的约 120 条中英术语词典
- 论文页的"相关论文"：用该论文的标题和摘要开头在站内检索，排除自身

### 升温方向

统计每篇论文标题和摘要中的技术短语（二元词组），比较**近 3 个月**与**之前 12 个月**中包含该短语的论文**占比**（已扣除论文总量的整体增长）；要求短语多次出现在论文标题中，以过滤"percentage points"这类写作套话，并合并单复数等重复短语。它反映的是关注度上升，不代表质量判断。

### AI 问答流程

1. **生成检索词**：Claude 把问题（中文或英文）改写成 2–4 组英文检索词
2. **站内检索**：浏览器用这些词在索引中检索并做排名融合，取最相关的 14 篇，读取完整摘要
3. **回答**：Claude 只依据这 14 篇论文回答，用 `[n]` 标注出处；资料覆盖不到的部分会明确说明，并给出延伸问题

---

## 项目结构

```
AI Research Graph/
├── public/research/          新网站（静态，无需构建）
│   ├── index.html  styles.css
│   ├── core.js               数据加载、分词、检索、本地工作区
│   ├── app.js                页面与路由（首页、搜索、论文、升温方向、文献库）
│   ├── ask.js                AI 问答
│   ├── connect.js            "连接 Claude"窗口与浏览器直连 Anthropic
│   └── data/                 每月生成的分片数据（约 230 MB）
├── data/pipeline/            数据管线
│   ├── harvest_arxiv.py      arXiv OAI-PMH 增量拉取（可断点续传）
│   ├── build_simple.py       生成网站数据（当前版本使用）
│   ├── monthly_update.py     每月更新入口
│   ├── textutil.py           分词规则（与 core.js 保持一致）
│   └── common.py  corpus_db.py  s2_enrich.py  embed.py  related.py  frontier.py  build_site.py
│                             完整版管线（引用关系、语义向量、主题聚类），尚未用于当前网站
├── app/                      Vinext 应用（部署用）
│   ├── page.tsx              以 iframe 加载 /research/
│   └── api/
│       ├── ask/              AI 问答（检索词规划 + 流式回答）
│       ├── query-rewrite/    中文问题 → 英文检索词
│       └── ai-search/        搜索页的 AI 研究助手
├── lib/llm.ts                Claude（及 OpenAI）调用封装
├── lib/rate-limit.ts         按访客限流
├── serve_local.py            本地服务：静态页面 + 与线上一致的 AI 接口 + Claude Code 登录
├── 启动网站.bat               Windows 一键启动
└── data/work/                管线中间数据（已忽略，不提交）
```

---

## 服务端接口

| 接口 | 方法 | 说明 |
|---|---|---|
| `/api/ask` | GET | 返回可用的服务器端模型 |
| `/api/ask` | POST `{phase:"plan", question, history, model}` | 返回英文检索词 `{queries, language}` |
| `/api/ask` | POST `{phase:"answer", question, history, sources, model}` | 流式返回回答文本；响应头 `x-model` 为实际使用的模型 |
| `/api/query-rewrite` | POST `{query}` | 中文问题 → `{english, terms}` |
| `/api/ai-search` | POST `{query, mode, candidates}` | 搜索页 AI 研究助手（综述 / 相关 / 比较 / 空白 / 前沿） |
| `/api/claude/status` | GET | **仅本地**：Claude Code 是否安装、是否已登录 |
| `/api/claude/login` | POST | **仅本地**：打开安装 / 登录窗口 |

本地专用接口要求请求头 `x-airg: 1`，其他网站无法跨域触发。

---

## 隐私与安全

- 搜索在浏览器中完成，查询词不会发到服务器
- 使用 AI 时，问题和检索到的论文标题、摘要会发送给 Claude（经网站服务器，或在"我的 API key"模式下由浏览器直接发送）
- 访客的 API key 只保存在其浏览器中（可选择关闭标签页即清除），只发送给 `api.anthropic.com`
- 服务器端的 key 只通过环境变量注入；`.env.local`、`data/work/` 不会提交到仓库
- 邮箱登录模式下，Claude Code 在空的临时目录中运行，提示词要求不调用工具、不读写文件

---

## 已知局限与后续计划

当前是简化版，以下功能还没有上线：

- **被引数和引用关系**：论文页暂不显示，可通过 Semantic Scholar 链接查看。`s2_enrich.py` 已能批量获取，但全量拉取较慢
- **语义相关论文**：现在的相关论文基于关键词；`embed.py` + `related.py` 可加入语义向量、共被引和文献耦合
- **主题聚类**：升温方向基于短语统计；`frontier.py` 可改为按语义聚类的研究方向
- **收录范围**：只有近两年的 arXiv 论文；未上传 arXiv 的会议 / 期刊论文不在库中
- 搜索页的"AI 研究助手"在本地预览中不可用（请用 AI 问答）
- 账号与跨设备同步、邮件订阅提醒

---

## 旧版组件

以下是项目早期的引用图谱版本，仍保留在仓库中，但不再是网站入口：

- `frontend/`、`public/graph/`：基于 CitationCS 的 20 万篇论文引用图谱界面（D3）
- `data/*.py`：OpenAlex / CitationCS / Semantic Scholar 采集与合并脚本；`data/*.md` 为相应的数据模式与审计文档
- `UX_NOTES.md`、`AI_Research_Graph_Roadmap_v1.0.docx`：早期产品设计与路线图

数据来源许可：arXiv 元数据（[arXiv](https://arxiv.org)）；CitationCS（Zenodo 16537950，CC BY 4.0）。
