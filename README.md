# 宏观命题监控

围绕 `Indictor.md` 里的 13 个宏观命题（美股分化与估值、衰退、通胀、加息终点、美元流动性、主权债务、信用利差、AI 资本开支/融资/回报/GDP 支撑、能源、黄金），自动拉取免费数据存入本地 SQLite，再生成一个离线可用的单文件看板 `dashboard.html`。

在线看板：https://qianshuofu.github.io/Indictor_monitor/ （GitHub Pages，每天定时任务更新后自动推送）。

各指标的来源、代码、自动/手工与覆盖区间见 [`指标数据源清单.md`](指标数据源清单.md)。

## 目录结构

```
Indictor.md              13 个命题与想看的指标（需求原文）
指标数据源清单.md          每个指标的实际来源、自动/手工、覆盖区间 + 最初调研记录
指标解释.md              看板上悬停指标名时弹出的解释（每节标题对应一张卡片，可继续添加）
每日更新手册.md            每日定时任务的操作手册：每张卡片归入代码抓取 / 浏览固定页面 / 搜新闻，附存储位置与序列 ID
update.sh                一键更新：拉数据 → 写库 → 重建看板
manual_data.csv          手工录入的数据（新闻、财报、付费数据里读到的数字）
requirements.txt
monitor/
  indicators.py          配置：SERIES（每条原始序列的来源与代码）、DERIVED（派生序列）、THESES（看板结构）
  update_data.py         抓取与入库（并行下载，SQLite 单线程写入）
  build_dashboard.py     从数据库生成 dashboard.html
  runbook.py             每日更新手册的辅助命令：今天该处理哪些浏览/搜索条目、写入手工记录
  dashboard_template.html  看板页面模板（ECharts）
  echarts.min.js         内联进看板，离线也能打开
data/                    整个文件夹只在本机，不进 git
  macro.db               SQLite 数据库（可随时重建）
  snapshots.csv          只能取“当天值”的序列的历史快照（重建数据库时回灌）
  runbook_state.json     每日更新里各浏览/搜索条目上次处理的日期
dashboard.html           生成的看板（进 git，GitHub Pages 发布的就是它）
index.html               GitHub Pages 首页，跳转到 dashboard.html
.env                     本机设置，如 SEC 联系邮箱（不进 git）
```

## 安装

需要 Python 3（本机用 3.14 运行）。

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

`update.sh` 会优先用项目里的 `.venv`，没有就用系统 `python3`。

可选：本机运行 **moomoo OpenD**（默认 `127.0.0.1:11111`）并安装 `moomoo-api` 时，每次更新会顺带用选股器记录标普 500 和全美股站上 200 日均线的占比。没开 OpenD 会自动跳过，不影响其他数据。

## 日常使用

```bash
./update.sh              # 增量更新所有序列，然后重建看板
./update.sh --full       # 从 2010-01-01 起全量重拉（数据源修订过历史、或删库重建时用）
./update.sh --breadth    # 额外用当前标普 500 成分股自算站上 200 日均线占比（16 线程，约 1 分钟）
./update.sh --only DGS10,SPY   # 只更新指定序列（序列名见 monitor/indicators.py）
./update.sh --workers 4  # 并行下载线程数，默认 8
./update.sh -v           # 失败时打印完整报错
```

只改了看板模板时，可以只重建看板：`.venv/bin/python monitor/build_dashboard.py`。改了 `manual_data.csv` 则要跑 `./update.sh`，手工数据经 `update_data.py` 才会写进数据库。

跑完后用浏览器打开 `dashboard.html`：

- 左侧切换命题，右上角切换时间范围（1/3/5/10 年/全部），选择会记在浏览器里。
- 地址栏加 `#命题id` 可直接打开某个命题，如 `dashboard.html#fed`、`#ai_capex`、`#gold`（id 见 `indicators.py` 里 `THESES` 的 `id`）。
- 每张卡片：大字为最新值和较一年前的变化；底部写来源，取数失败的序列会标黄色 ⚠。
- 指标名带虚线下划线的，鼠标悬停（手机上点按）会弹出解释小窗。解释写在 `指标解释.md`：加一节 `## 卡片标题`（与看板上的标题完全一致），下面写解释，然后重建看板即可；标题对不上时重建会打印提示。
- 原指标没有免费下载渠道时（如 CDS、VLCC 运价），卡片显示「原指标暂无数据」，不用替代指标凑数；网页上能看到数据的（如 GPU 租赁价、VLCC 运价周报），卡片里列出可点击跳转的网站链接。
- 卡片顺序：关键/领先指标在前，标“手工/付费”的卡片在最后；序列多的图占满一行。
- 跟随系统深色模式。

单个来源失败不会中断整体更新：该序列在数据库里标为错误、保留旧数据，看板上显示 ⚠。

## 每日自动更新

Claude 桌面版里有一个本地定时任务「每日更新宏观看板」（ID `daily-macro-dashboard-update`），每天 10:00（北京时间；App 为错峰固定延后几分钟，实际约 10:14 开始）在这台 Mac 上运行：在后台跑 `./update.sh --breadth`，同时按 [`每日更新手册.md`](每日更新手册.md) 处理当天到期的浏览/搜索条目（VLCC 周报、央行购金、云业务增速、资本开支指引、模型公司 ARR、违约率等），写入 `manual_data.csv`，重建看板，提交并 `git push` 到 GitHub（在线看板约 1 分钟后更新），最后给出简短报告。

- 只在 Claude 桌面版开着时运行；到点时 App 没开，会在下次打开时补跑。数据源和 moomoo OpenD 只有这台 Mac 能访问，所以不用云端任务。
- 任务的提示词存在 `~/.claude/scheduled-tasks/daily-macro-dashboard-update/SKILL.md`；在桌面版的定时任务列表里可以暂停、立即运行、查看每次运行的记录。
- update.sh（约 6 分钟）在后台跑，浏览/搜索同时进行；每个浏览/搜索条目限时 3 分钟，整次运行限 20 分钟；哪天该做哪几条由 `monitor/runbook.py due` 判断。手工操作也用同一套命令：

```bash
.venv/bin/python monitor/runbook.py due      # 今天该处理哪些条目
.venv/bin/python monitor/runbook.py add cb_gold 2026-06-30 "央行净购金（季度）" 288.9 --text "2026Q2；2026-07-30 发布" \
    --source https://www.gold.org/goldhub/research/gold-demand-trends/gold-demand-trends-q2-2026/central-banks
```

## 在线看板（GitHub Pages）

仓库 https://github.com/qianshuofu/Indictor_monitor 是公开的，GitHub Pages 从 `main` 分支根目录发布，`index.html` 跳转到 `dashboard.html`（ECharts 已内联在看板里）。看板是写死数据的静态页面，本地重建后要提交 `dashboard.html` 并 `git push` 才会更新，定时任务每天最后会做这一步。`data/` 文件夹（数据库、快照、运行状态）只在本机，不进 git。提交用 GitHub 匿名邮箱（本仓库的 `git config user.email`），SEC 联系邮箱放在本机 `.env`，都不会公开。

## 手工数据 `manual_data.csv`

没有免费接口的数字（新闻稿、财报电话会、付费数据的公开摘录）手工录入这个 CSV。以 `#` 开头的行是注释。列：

| 列 | 说明 |
|---|---|
| `ref` | 对应看板指标的 `ref`（见 `indicators.py` 中 `ind(..., ref="...")`，文件头注释里也列了全部可用值） |
| `date` | 数据所属日期 `YYYY-MM-DD`（月度数据写当月 1 日，季度写季末或发布日） |
| `item` | 项目名。同一 `ref` 下每个 `item` 画成一条线，所以同一口径要用完全相同的写法 |
| `value` | 数值，可空（只记文字事件时留空） |
| `text` | 文字说明：原始区间、同比、口径等 |
| `source` | 出处网址，必填 |

规则：只录网页上实际读到的数字，不估算；区间型指引的 `value` 取中值，原区间写进 `text`。`(ref, date, item)` 相同的行以最后一次为准。

示例：

```csv
ref,date,item,value,text,source
korea_semi_exports,2026-09-01,产业部半导体出口（初值）,603,同比 +263%；次月 1 日公布,https://...
capex_guidance,2026-10-21,Meta 2026,140,区间 $135–145B（此前 $130–145B）；数值为区间中值,https://...
```

看板上：纯手工指标的卡片显示最近 12 条记录，有 2 个以上数值时画折线；自动指标若带 `ref`，手工记录作为补充表格（或 `M:ref` 序列）显示在同一张卡片里。

## 数据库结构（`data/macro.db`）

| 表 | 主键 | 列 | 内容 |
|---|---|---|---|
| `series` | `series_id` | `name, unit, freq, source, code, last_updated, last_obs, n_obs, status` | 每条序列的元数据与最近一次抓取状态（`ok` 或错误信息） |
| `observations` | `(series_id, date)` | `value` | 所有时间序列的观测值，含派生序列（`X_` 开头） |
| `auctions` | `(cusip, auction_date)` | `security_type, security_term, offering_amt, high_yield, bid_to_cover, indirect_accepted, direct_accepted, primary_dealer_accepted, competitive_accepted` | 美债拍卖结果（财政部 Fiscal Data） |
| `manual` | `(ref, date, item)` | `value, text, source` | `manual_data.csv` 的内容，每次运行整表重载 |
| `events` | `(ref, date, item)` | `value, text, source` | 自动抓取的事件型记录，如 FOMC 异议票（异议者及主张） |

日期一律为 `YYYY-MM-DD` 文本。数据库可随时删除，用 `./update.sh --full` 重建；只能取当天值或近期值的序列（DRAM 现货价、moomoo 宽度，以及免费接口只给近 3 个月的 Ornn GPU 租赁价格指数）会从 `data/snapshots.csv` 回灌。

## 新增或修改指标

1. 在 `monitor/indicators.py` 的 `SERIES` 里加原始序列（如 `fred("CODE", "名称", "单位", "D")`、`yahoo("TICKER", "名称")`）；需要计算的放进 `DERIVED`，并在 `update_data.py` 的 `derive()` 里实现。
2. 在 `THESES` 对应命题下加 `ind(名称, 图表类型, [序列...], 说明, 来源, ref=...)`。图表类型：`line` 折线、`rebased` 定基 100 折线、`bar` 柱状、`stack` 堆叠柱、`sector` 行业横条、`dots` 点阵图、`curve` 利率期货曲线、`auctions` 拍卖、`manual` 纯手工。原指标没有免费下载渠道时加 `nodata="原因"`，能查看数据的网页用 `links=[("名称", "网址")]` 列出，卡片上会显示「原指标暂无数据」和跳转链接。同一张图里量级差很大、放在一起会被压平的序列，用 `right=["序列名"]` 放到右侧纵轴，图例里会标「（右轴）」。
3. 运行 `./update.sh --only 新序列` 拉数据并重建看板。

## 说明

- SEC EDGAR 要求请求头带联系邮箱：写在项目根目录的 `.env`（一行 `SEC_CONTACT_EMAIL=你的邮箱`，不进 git），也可以用同名环境变量。没有设置时用占位邮箱，SEC 可能拒绝请求。各来源按主机限速（SEC 每秒 8 次等）。
- FRED 用公开的 `fredgraph.csv` 接口，不需要 API key。
- Yahoo Finance 为非官方接口，偶尔限流；失败的序列下次运行会补上。
