# AI Research Graph

面向科研人员的 **AI / 计算机科学论文搜索网站**：快速找到某个方向的关键论文和最新进展，从一篇论文出发找到相关工作，并用 Claude 基于检索到的论文回答研究问题。

- 收录 **552,140 篇** 2021-12 至 2026-10 的 AI 相关论文（全部带摘要，约 5 年），**每月更新一次**：
  - 501,905 篇 arXiv 论文
  - 50,235 篇只发表在顶会 / 顶刊、不在 arXiv 上的论文（38 个会议与期刊，见下文）
  - 去重：会议 / 期刊版本按 arXiv 编号、DOI 或标题与 arXiv 论文匹配，同一篇只收录一次；95,554 篇 arXiv 论文标注了发表的会议（如「CVPR 2023」），搜索页可以「只看已发表于顶会 / 顶刊」
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
| 搜索 | `#/search?q=…` | 模糊检索：关键词匹配 + 摘要语义相似度按相关度综合排序，自动纠正拼写，换一种说法也能找到；支持中文提问；按年份、研究方向、arXiv 主分类筛选，按相关度或时间排序 |
| 论文详情 | `#/paper/<arXiv 编号>` | 摘要、所属研究方向、作者、分类、arXiv / PDF / Semantic Scholar / DOI 链接、复制 BibTeX、10 篇语义最相近的相关论文、"问 AI 关于这篇论文" |
| 研究方向 | `#/topics`、`#/trend/<id>` | 根据摘要语义把论文归入 150 个研究方向（每篇 1–3 个），按近 3 个月的占比增长排序；每个方向有中文名、说明、关键词、每月趋势、最新与最典型的论文 |
| 领域最新 | `#/category/<分类>` | 某个 arXiv 主分类（如 cs.CV）按时间倒序的全部论文 |
| AI 问答 | `#/ask` | Claude 先从全部论文中定位研究方向、筛选资料，再回答，每条结论带 `[n]` 出处；多轮追问、延伸问题、一键收藏参考论文；可切换 Sonnet / Opus / Haiku；左侧边栏保存历史对话（可搜索、切换、删除） |
| 文献综述 | `#/review` | **仅本地**：输入主题，Agent 检索全库、拟定大纲（可修改后再写）、分节撰写，生成带 `[n]` 引用的综述、参考文献和 BibTeX；可下载 Markdown / .bib，一键收藏全部参考文献；研究方向页和搜索页有入口 |
| 我的文献 | `#/library` | 收藏的论文、关注的方向；导出 BibTeX / CSV，备份与导入工作区 |
| 数据说明 | `#/about` | 收录范围、数据来源、算法说明、隐私说明 |

搜索结果页还有"AI 研究助手"（梳理综述、找相关论文、比较方法、找研究空白、找前沿），基于前 30 条结果生成；本地用邮箱登录的 Claude、自己的 API key，或部署时网站配置的 key 都可以使用。中文搜索同样会先由 Claude（Haiku）翻译成英文检索词，翻译结果缓存在本地，重复搜索不再消耗额度；连不上 Claude 时退回内置词典。

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
| `AI` 绑定，或 `CF_ACCOUNT_ID` + `CF_AI_TOKEN` | 否 | Cloudflare Workers AI（`@cf/baai/bge-base-en-v1.5`，与论文向量同一模型），把搜索词转成语义向量；不配置时语义检索改用「关键词结果反馈」近似 |

不配置 key 时网站照常可用：搜索、浏览、升温方向都不依赖模型；AI 问答由访客填写自己的 key；中文检索回退到内置词典。

服务端的 AI 接口有按访客的限流（问答每分钟 10 次、检索词改写 30 次、研究助手 8 次），避免公开后被刷费用。

---

## 每月更新数据

在自己的电脑上运行（不需要任何 API key）。第一次先双击 **`安装GPU版PyTorch.bat`**，它会在 `data/work/venv-gpu` 建一个独立环境（不改动 anaconda），装好 GPU 版 PyTorch 和其他依赖。之后每月运行：

```powershell
data\work\venv-gpu\Scripts\python.exe data\pipeline\monthly_update.py
```

它会：

1. 通过 arXiv OAI-PMH **增量**拉取上次运行之后新增或更新的论文（状态保存在 `data/work/`）；通过 Semantic Scholar 重新拉取今年和去年的顶会 / 顶刊论文（`fetch_venues.py`，保存在 `data/work/venues/`）
2. 重建最近 **60 个完整月**（约 50 万篇）的网站数据到 `public/research/data`（文档、检索索引、清单）；环境变量 `AIRG_MONTHS` 可调整
3. 只给新论文计算语义向量（GPU），归入已有的研究方向，重新计算升温趋势

通常 10–20 分钟。之后照常提交并部署。首次在一台新电脑上运行时会从 2016-11 开始全量拉取，耗时较长（约 2–3 小时）。

也可以分阶段手动运行：

```bash
python data/pipeline/harvest_arxiv.py --until 2026-11-30
python data/pipeline/build_simple.py all --start 2021-12-01 --end 2026-11-30
# 或单独运行某个阶段：docs | cards | index | shards | trends1 | trends2
```

---

## 工作原理

### 数据

- **来源**：arXiv OAI-PMH（`set=cs`），保留主分类或交叉分类属于 cs.AI、cs.LG、cs.CL、cs.CV、cs.RO、cs.IR、cs.MA、cs.NE、stat.ML 等 AI 相关方向的论文
- **发布时间**：按 arXiv 编号中的年月判断首次发布月份（OAI 的 `created` 字段对应最新版本，不能用来判断首发）；同一论文多个版本只保留最新元数据
- **收录窗口**：最近 24 个完整月

### 顶会 / 顶刊论文

`data/pipeline/fetch_venues.py` 用 Semantic Scholar 的批量检索接口（无需 key）按会议 / 期刊和年份拉取论文列表与摘要，收录范围：

- 机器学习：NeurIPS、ICML、ICLR、AISTATS、UAI、COLT、JMLR、TNNLS
- 人工智能：AAAI、IJCAI、Artificial Intelligence
- 视觉与多媒体：CVPR、ICCV、ECCV、WACV、ACM MM、TPAMI、IJCV、TIP
- 自然语言处理：ACL、EMNLP、NAACL、Findings、TACL
- 数据挖掘与检索：KDD、SIGIR、WWW、TKDE
- 机器人：ICRA、IROS、RSS、CoRL、T-RO
- 人机交互与安全：CHI、CCS、USENIX Security、IEEE S&P、NDSS

合并规则（`build_simple.py` 的 `merge_venues`）：与 arXiv 论文按 arXiv 编号 → DOI → 规范化标题依次匹配，命中则只给 arXiv 论文标注会议名，不重复收录；不在 arXiv 上且有摘要的论文以 `s2-<Semantic Scholar ID>` 为编号加入，分类按会议领域（如 CVPR → 计算机视觉）。没有摘要的论文（约 1.4 万篇）暂不收录。

### 网站数据文件（`public/research/data`）

| 文件 | 内容 |
|---|---|
| `manifest.json` | 快照版本、论文数、分片大小、分类名称 |
| `meta.bin.gz` | 每篇论文 4 字节：年份、月份、arXiv 主分类、是否本月新增（筛选和排序用） |
| `t/NNNN.json.gz` | 2,048 个检索分片：按词项哈希分配，存每个词的倒排表（论文编号差值 + 量化 BM25 分数） |
| `l/NNNN.json.gz` | 结果卡片（每片 400 篇）：标题、日期、分类、作者、摘要片段 |
| `p/NNNN.json.gz` | 论文详情（每片 200 篇）：完整摘要、作者、分类、备注、DOI |
| `k/XX.json.gz` | arXiv 编号 → 本快照中的论文编号（保证链接在每月更新后仍有效） |
| `trends.json.gz` | 150 个研究方向：名称、说明、关键词、每月论文数、升温指标、代表论文 |
| `dir.bin.gz` | 每篇论文 3 字节：所属的研究方向编号（255 表示无） |
| `v/NNN.bin.gz` | 语义向量：按主研究方向分片，每篇论文的摘要向量降到 256 维（PCA）并存为 int8，约 104 MB |
| `v/centroids.bin.gz`、`v/pca.bin.gz` | 150 个方向的中心向量；768→256 维的投影矩阵（查询向量用同一投影） |
| `vocab.txt.gz` | 约 4 万个常用词及文档数，用于拼写纠正 |

论文按 arXiv 主分类分组、组内按时间倒序编号，因此"领域最新"页面直接读取连续的编号区间。

### 检索与相关论文

搜索是「模糊」的：不要求论文包含查询里的每个词，结果按相关度综合排序。

1. **拼写纠正**：语料中罕见或不存在的词，替换为编辑距离 1–2 内最常见的词（`transfomer` → `transformer`），全大写缩写保持不变；结果页会提示纠正了什么
2. **关键词检索**：字段加权 BM25，标题词权重 3 倍，标题相邻词对（如 *diffusion model*）单独建索引以匹配短语；覆盖更多查询词的论文得分更高，但只匹配部分词的论文也会返回
3. **语义检索**：查询转成与摘要相同空间的向量（本地由 `serve_local.py` 调用 `data/pipeline/query_embed.py`，部署时由 Workers AI），先找最接近的 8 个研究方向，再在这些方向的论文向量中按相似度排序。没有查询向量时，用关键词前几名论文的向量平均作为查询（伪相关反馈）
4. **融合**：两路结果用倒数排名融合（RRF）合并；只靠语义找到的论文标记「语义相关」
5. 中文查询先由 Claude 改写成英文检索词，失败时用内置的约 120 条中英术语词典
6. 论文页的「相关论文」：直接用该论文的摘要向量找最相近的论文（256 维 int8 压缩后，前 10 名与原始向量精确结果的重合率约 70%）

### 研究方向与升温趋势

1. **理解摘要**：用 [bge-base-en-v1.5](https://huggingface.co/BAAI/bge-base-en-v1.5) 把每篇论文的"标题 + 摘要"编码成 768 维语义向量（`data/pipeline/embed_gpu.py`，有 NVIDIA 显卡时用 GPU，RTX 4060 笔记本约 10–20 分钟算完 27 万篇）
2. **发现方向**：对全部向量做球面 k-means，得到 150 个研究方向；每篇论文归入最接近的方向，另外 1–2 个几乎同样接近的方向也算在内（平均 1.6 个）
3. **命名**：阅读每个方向中最靠近中心的 15 篇论文，写出中文名、英文名和一句话说明，保存在 `data/pipeline/direction_names.json`；关键词由各方向摘要的 TF-IDF 提取
4. **升温**：比较**近 3 个月**与**之前 12 个月**中该方向论文占全部论文的比例（已扣除总量增长）

方向的划分固定下来，每月更新时只给新论文计算向量并归类，方向编号和名称保持不变。需要重新划分时运行 `python data/pipeline/directions.py build --refit`，然后重新命名。

### AI 问答流程

回答依据的是从**全部论文**中筛出来的资料，而不是一次关键词检索的前几名：

1. **理解问题**：中文问题或追问由 Claude Haiku 改写成 2–4 组英文检索词；没有上下文的英文问题直接用原文检索，不调用模型
2. **全库召回**：每组检索词在全部论文上做模糊检索（关键词 + 语义），合并成约 500 篇的候选池
3. **定位研究方向**：统计候选池落在哪些研究方向，取占比最高的 1–4 个（界面上会显示「从全部论文中定位到的方向」）
4. **扫描整个方向**：用问题的语义向量把这些方向里的论文全部打分，补上用词不同但内容相关的论文
5. **挑选资料**：按「与问题的相似度 + 检索得分」排序，同时跳过彼此几乎重复的论文（最大边际相关），选出 20 篇：前 8 篇附摘要摘录（首句 + 与问题最相关的句子，约 460 字符），后 12 篇只给标题
6. **回答**：Claude 依据这些论文和方向概况（论文数、近 3 个月数量、升温幅度）回答，用 `[n]` 标注出处

**节省 token 的设计**：
- 发给模型的资料约 1,500 tokens（以前 14 篇完整摘要约 5,000 tokens）
- 只带最近两轮对话，且历史回答截短到 500 字、去掉引用编号
- 生成检索词固定用最便宜的 Haiku，英文问题直接跳过这一步
- 邮箱登录（Claude Code）模式下，用 `--system-prompt-file` 替换 Claude Code 自带的长系统提示，并用 `--tools ""` 关闭工具，避免每次额外消耗上万 tokens（旧版 CLI 不支持时自动退回普通调用）
- 每条回答下方显示本次的输入 / 输出 token 数

### Agent 模式（本地，LangGraph）

AI 问答页的 **"Agent 模式"** 开关（只在本地服务、且用 Claude Code 邮箱登录或服务器 key 时出现）把上面的固定流程换成一个 LangGraph 工作流（`airg/agent.py`），由模型决定查什么、查几次：

```
规划 ──► 检索 ──► 自查 ──► 选资料 ──► 回答 ──► 核对引用
                   ▲  │
                   └─ 补充检索（最多 2 轮，每轮最多 3 次工具调用）
```

1. **规划**（Haiku）：把问题拆成 1–3 个子问题，每个配英文检索词；追问里的"第 2 篇"等会对应到上一轮的参考论文
2. **检索**：每个子问题走一遍上面的全库资料筛选（`find_evidence`）；问题指向具体论文时读取它的完整摘要和相关论文
3. **自查**（Haiku）：看已找到的论文标题，判断还缺什么，按需调用 `search_papers`（可按年份 / 分类 / 顶会筛选、按时间排序）、`related_papers`、`get_paper`、`get_direction`、`latest_in_category`；不会重复已执行过的调用
4. **选资料**：在子问题之间轮流挑选，选出 20 篇（前 8 篇附摘要），保证每个子问题都有资料
5. **回答**（页面上选的模型）：与普通问答相同的提示词，用 `[n]` 标注出处
6. **核对引用**：删除指向不存在资料的编号

**记忆**：每个对话的状态（历史问题、回答和参考论文）由 LangGraph 按对话保存到 `data/work/agent_memory.sqlite`，追问时可以引用前面的论文；在网页上删除对话时一并删除。

**代价**：一次提问约 4–6 次模型调用，用 Claude Code 时约 50–70 秒、约 2 万输入 tokens（普通模式约 2 次调用）。

**环境**：需要 `langgraph`、`langgraph-checkpoint-sqlite`（见 `requirements-agent.txt`）。`安装GPU版PyTorch.bat` 会把它们装进 `data/work/venv-gpu`，`启动网站.bat` 会优先用这个环境启动；用别的 Python 启动时开关不会出现，普通问答不受影响。

**LangSmith（可选）**：在 `.env.local` 中设置 `LANGSMITH_TRACING=true` 和 `LANGSMITH_API_KEY=...` 后，每次运行的节点、工具调用和模型输入输出会上传到 LangSmith 便于调试。注意这会把问题和论文摘要发送给 LangSmith。

### 文献综述（本地，LangGraph）

`#/review` 页面输入一个主题（如"检索增强生成"），由 `airg/review.py` 中的 LangGraph 工作流写出一篇结构化综述：

```
规划 ─► 普查 ─► 大纲 ─► 确认大纲 ─┬─► 分节检索 ×N（并行）─► 编号 ─┬─► 分节撰写 ×N（并行）─► 引言与展望 ─► 整理
                     （暂停，等用户修改）                          
```

1. **规划**（Haiku）：综述题目、语言，以及 4–6 个不同角度的英文检索词（核心方法、变体、应用、评测、新趋势）
2. **普查**：每个检索词在全库检索，合并成约 100 篇候选，并统计它们所属的研究方向和年份
3. **大纲**（页面上选的模型）：把候选论文归纳成 3–5 节（技术路线或主题），每节配检索词和归入的论文
4. **确认大纲**：工作流用 LangGraph 的 `interrupt` 暂停，页面显示大纲；可以改标题和描述、调整顺序、删除或新增一节，确认后从暂停处继续（状态保存在 checkpoint 中，刷新页面后仍可继续）。不勾选"先确认大纲"则直接往下走
5. **分节检索**（`Send` 并行）：每节用与 AI 问答相同的资料筛选再检索一次，加上大纲阶段归入的论文，取 10 篇
6. **编号**：全文统一编号，去掉重复论文，取每篇摘要中与该节最相关的句子
7. **分节撰写**（`Send` 并行）：每节只看本节的论文，写清问题与核心思路、代表方法及其差异、优势与局限，用 `[n]` 标注出处
8. **引言与展望**：根据各节草稿和方向增长统计写引言、跨节趋势和开放问题
9. **整理**：删除无效引用编号，按首次出现重新编号，生成参考文献列表、Markdown 和 BibTeX（格式与论文页的"复制 BibTeX"相同）

**实测**（"检索增强生成（RAG）"，Sonnet，6 节）：约 40 秒出大纲，确认后约 1.5 分钟写完；检索 100 篇候选、阅读 59 篇摘要，正文引用 56 篇，引用编号全部有效；约 3.6 万输入 + 1.3 万输出 tokens。

**局限**：只读摘要，不读全文，具体实验数字以原文为准；模型偶尔会抄错方法名（如把摘要里的 HippoRAG 写成 HripoRAG），引用指向的论文是真实的，但结论请对照原文。综述保存在当前浏览器（localStorage，最多 30 篇），工作流状态保存在 `data/work/agent_memory.sqlite`，删除综述时一并删除。

---

## 项目结构

```
AI Research Graph/
├── public/research/          新网站（静态，无需构建）
│   ├── index.html  styles.css
│   ├── core.js               数据加载、分词、检索、本地工作区
│   ├── app.js                页面与路由（首页、搜索、论文、升温方向、文献库）
│   ├── ask.js                AI 问答
│   ├── review.js             文献综述（大纲编辑、进度、导出 Markdown / BibTeX）
│   ├── connect.js            "连接 Claude"窗口与浏览器直连 Anthropic
│   └── data/                 每月生成的分片数据（约 230 MB）
├── data/pipeline/            数据管线
│   ├── harvest_arxiv.py      arXiv OAI-PMH 增量拉取（可断点续传）
│   ├── build_simple.py       生成网站数据（当前版本使用）
│   ├── embed_gpu.py          用 GPU 计算论文语义向量
│   ├── directions.py         研究方向聚类、归类与升温趋势
│   ├── direction_names.json  150 个研究方向的中文名与说明
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
├── airg/                     服务端检索（Python，读取与网页相同的分片数据，需要 numpy）
│   ├── search.py             core.js 检索与 ask.js 资料筛选的 Python 移植（结果与浏览器一致）
│   ├── tools.py              面向 AI Agent 的检索工具（JSON 输入输出 + JSON Schema）
│   ├── agent.py              Agent 模式：LangGraph 工作流（规划、检索、自查补搜、回答、核对）与对话记忆
│   └── review.py             文献综述：LangGraph 工作流（普查、大纲、人工确认、并行分节检索与撰写、引用整理）
├── requirements-agent.txt    airg/ 的依赖（numpy、LangGraph、LangSmith）
├── scripts/parity_check.py   对比浏览器（core.js）与 Python（airg）的检索结果
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
| `/api/embed` | POST `{q}` | 查询语义向量：本地返回 256 维 `{v}`，部署时返回 768 维 `{raw}`（浏览器投影）；未配置时 503 |
| `/api/ai-search` | POST `{query, mode, candidates}` | 搜索页 AI 研究助手（综述 / 相关 / 比较 / 空白 / 前沿） |
| `/api/claude/status` | GET | **仅本地**：Claude Code 是否安装、是否已登录 |
| `/api/claude/login` | POST | **仅本地**：打开安装 / 登录窗口 |

本地专用接口要求请求头 `x-airg: 1`，其他网站无法跨域触发。

### 服务端检索工具（仅本地，为 AI Agent 准备）

`airg/` 把浏览器里的检索（`core.js`）移植成了 Python，直接读取 `public/research/data` 中同一套分片，结果与网页一致（用 `python scripts/parity_check.py` 核对）。`airg/tools.py` 把它包装成只读工具，供 Agent 调用：

| 工具 | 说明 |
|---|---|
| `search_papers` | 模糊检索（关键词 + 语义 + 拼写纠正），可按年份、arXiv 分类、研究方向、是否发表于顶会 / 顶刊筛选 |
| `get_paper` | 一篇论文的完整信息（摘要、作者、会议、研究方向、链接） |
| `related_papers` | 语义最相近的论文 |
| `find_evidence` | 与 AI 问答相同的资料筛选：全库召回 → 定位研究方向 → 扫描方向 → 去重选出约 20 篇 |
| `list_directions` / `get_direction` | 150 个研究方向（按升温或规模排序）及每个方向的详情 |
| `latest_in_category` | 某个 arXiv 主分类的最新论文 |
| `index_info` | 收录范围与快照信息 |

| 接口 | 方法 | 说明 |
|---|---|---|
| `/api/tools` | GET | 所有工具的名称、说明和 JSON Schema 参数 |
| `/api/tools/<工具名>` | POST JSON 参数 | 调用一个工具，返回 JSON；参数错误返回 400 |
| `/api/agent` | GET | Agent 模式是否可用 `{available, providers, reason}` |
| `/api/agent` | POST `{question, thread_id, provider, model, pinned?, history?}` | 运行 Agent；逐行返回 JSON（NDJSON）：`{"type":"step"}` 若干条，最后 `{"type":"result", answer, sources, dirs, …}` 或 `{"type":"error"}` |
| `/api/agent/forget` | POST `{thread_id}` | 删除一个对话的 Agent 记忆 |
| `/api/review` | GET | 文献综述是否可用 `{available, providers, reason}` |
| `/api/review` | POST `{topic, thread_id, provider, model, year_from?, year_to?, confirm?}` | 开始一篇综述；NDJSON：`{"type":"step"}` 若干条，然后 `{"type":"outline", outline}`（暂停等待确认）或 `{"type":"result", title, body, markdown, references, bibtex, …}` |
| `/api/review/resume` | POST `{thread_id, provider, model, outline:{title, sections:[{id?, title, focus}]}}` | 用修改后的大纲继续，返回同上 |
| `/api/review/forget` | POST `{thread_id}` | 删除一篇综述的工作流状态 |

在 Python 中也可直接使用：

```python
from airg.tools import call_tool
call_tool("search_papers", {"query": "vision language action model", "year_from": 2025, "limit": 5})
```

需要 numpy（`pip install numpy`）；没有 numpy 时这些接口返回 503，网站其他功能不受影响。本地向量模型加载完成后检索会使用真实的查询向量，加载中或不可用时与网页一样改用"关键词结果反馈"。

---

## 隐私与安全

- 搜索在浏览器中完成；使用语义检索时，查询词会发给本地服务或网站的 `/api/embed` 转成向量（不记录）
- 使用 AI 时，问题和检索到的论文标题、摘要会发送给 Claude（经网站服务器，或在"我的 API key"模式下由浏览器直接发送）
- 访客的 API key 只保存在其浏览器中（可选择关闭标签页即清除），只发送给 `api.anthropic.com`
- 服务器端的 key 只通过环境变量注入；`.env.local`、`data/work/` 不会提交到仓库
- 邮箱登录模式下，Claude Code 在空的临时目录中运行，提示词要求不调用工具、不读写文件
- Agent 模式的对话记忆和文献综述的工作流状态保存在本机 `data/work/agent_memory.sqlite`（不提交）；只有设置了 LangSmith 环境变量时才会把运行记录发给 LangSmith

---

## 已知局限与后续计划

当前是简化版，以下功能还没有上线：

- **被引数和引用关系**：论文页暂不显示，可通过 Semantic Scholar 链接查看。`s2_enrich.py` 已能批量获取，但全量拉取较慢
- **引用关系**：相关论文已基于摘要语义，尚未加入共被引和文献耦合
- **收录范围**：只有近 5 年的 arXiv 论文；未上传 arXiv 的会议 / 期刊论文不在库中
- 账号与跨设备同步、邮件订阅提醒

---

## 旧版组件

以下是项目早期的引用图谱版本，仍保留在仓库中，但不再是网站入口：

- `frontend/`、`public/graph/`：基于 CitationCS 的 20 万篇论文引用图谱界面（D3）
- `data/*.py`：OpenAlex / CitationCS / Semantic Scholar 采集与合并脚本；`data/*.md` 为相应的数据模式与审计文档
- `UX_NOTES.md`、`AI_Research_Graph_Roadmap_v1.0.docx`：早期产品设计与路线图

数据来源许可：arXiv 元数据（[arXiv](https://arxiv.org)）；CitationCS（Zenodo 16537950，CC BY 4.0）。
