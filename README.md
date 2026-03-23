# 📰 Paper Hub

> 自动抓取 Hugging Face 每日 / 每周 / 每月热门 AI 论文，LLM 翻译摘要 + 全文中文 PDF，现代化 Web 界面对外发布。支持手动输入 arXiv ID 按需翻译，内置收藏夹与全局搜索功能。

**Web 访问**：https://zzzgry.top/paper/

---

## 功能特性

| 功能 | 说明 |
|------|------|
| 📅 **每日 Top 3** | 每天 23:00 抓取当日热榜前 3 篇，翻译摘要 + 全文 PDF |
| 📚 **每周 Top 10** | 每周日 02:00 抓取本周热榜前 10 篇，翻译摘要 + 全文 PDF |
| 📆 **每月 Top 10** | 每月 28 日 02:00 抓取本月热榜前 10 篇，翻译摘要 + 全文 PDF |
| 🤖 **摘要翻译** | LLM 翻译标题、摘要，提取关键词和核心贡献 |
| 📄 **全文翻译** | gpt-academic LaTeX 插件翻译全文，重新编译中文 PDF |
| ➕ **手动按需翻译** | 输入任意 arXiv ID，后台自动完成全流程，实时进度追踪 |
| ⭐ **收藏夹** | 多列表收藏管理，支持新建 / 重命名 / 删除列表 |
| 🔍 **全局搜索** | 中英文模糊搜索标题、摘要、作者、关键词，覆盖全部内容 |
| 📊 **系统状态** | 磁盘使用、Docker 翻译进程监控、任务队列一览 |
| 🌍 **Web 发布** | 现代响应式深色界面，nginx 反代，域名 zzzgry.top/paper/ |
| 🔄 **增量处理** | 断点续传，已翻译内容自动跳过 |

---

## 快速开始

### 每日

```bash
cd /root/workspace/paper-trans
python3 run_daily.py                   # 今天（含全文翻译）
python3 run_daily.py 2026-02-19        # 指定日期
python3 run_daily.py 2026-02-19 --no-full  # 仅摘要翻译
```

### 每周

```bash
python3 run_weekly.py                  # 本周（含全文翻译）
python3 run_weekly.py 2026-W08         # 指定周
python3 run_weekly.py 2026-W08 --no-full  # 仅摘要翻译
```

### 每月

```bash
python3 run_monthly.py                 # 本月（含全文翻译）
python3 run_monthly.py 2026-02         # 指定月份
python3 run_monthly.py 2026-02 --no-full  # 仅摘要翻译
```

### 单篇全文翻译

```bash
python3 translate_full.py 2602.10388 \
    -o data/papers    # PDF 直接写入 paper store
```

### 修复与重试（run_repair.py）

```bash
# 补翻译：修复 title_zh / summary_zh 为空的条目
python3 run_repair.py                        # 扫描全部 mode 近 30 天
python3 run_repair.py --mode daily --days 2  # 仅修复最近 2 天 daily
python3 run_repair.py --mode weekly --key 2026-W09  # 指定 key

# 补索引：重新执行因网络故障未生成 index.json 的任务
python3 run_repair.py --refetch                       # 全部 mode 近 30 天
python3 run_repair.py --refetch --mode daily --days 3

# --post：补翻译 + 补索引一次完成（推荐 cron 使用）
python3 run_repair.py --post --mode daily --days 2

# --retry-pdf：对 pdf_status=failed 的条目重新翻译全文 PDF
python3 run_repair.py --retry-pdf --mode weekly --key 2026-W12
python3 run_repair.py --retry-pdf --mode daily  --days 7
```

> **模式说明**：`repair`（默认）修复已有 index.json 中翻译为空的条目；`--refetch` 补抓根本没有 index.json 的日期；`--post` 两者串行执行；`--retry-pdf` 专门对 PDF 编译失败的论文发起重试。

> **注意**：包含 LaTeX 数学公式的论文（如 `$O(\log N)$`、`$P(\text{h}|b)$`）在翻译时会产生非法 JSON 转义序列，`translate_arxiv.py` 已内置自动修复逻辑。

### Web 访问

```
本地：http://localhost:18080
公网：http://xxxxxxxxx:18080
```

---

## 项目结构

```
paper-trans/
├── run_daily.py             # 每日入口（Top 3）
├── run_weekly.py            # 每周入口（Top 10）
├── run_monthly.py           # 每月入口（Top 10）
├── run_papers.py            # 通用处理 runner（含 retry_pdf）
├── run_repair.py            # 翻译修复扫描器（repair/refetch/post/retry-pdf）
├── fetch_hf.py              # 统一 HF 抓取器（含指数退避重试）
├── translate_arxiv.py       # 摘要翻译 → paper store JSON
├── translate_full.py        # 全文翻译（容器外封装，实时流式日志）
├── full_translate_driver.py # 全文翻译（容器内驱动，含重试逻辑）
├── web_server.py            # Web 服务器（端口 18080）
│
├── scripts/
│   ├── setup_docker_env.sh      # ★ Docker 容器环境修补脚本（容器重建后运行）
│   ├── patch_axessibility.py    # latex_toolbox 补丁：移除 xelatex 不兼容的 axessibility 包
│   └── cleanup_docker_cache.sh  # 清理 Docker 翻译缓存
│
├── data/                    # 统一数据目录
│   ├── papers/              # ★ 唯一数据源（paper store）
│   │   ├── ARXIV_ID.json        # 完整元数据 + 翻译结果（所有 mode 共用）
│   │   └── ARXIV_ID_zh.pdf      # 全文中文 PDF（所有 mode 共用）
│   │
│   ├── daily/               # 轻量索引（slim index）
│   │   └── YYYY-MM-DD/
│   │       └── index.json       # 仅含 [{arxiv_id, rank, upvotes, pdf_status}]
│   ├── weekly/
│   │   └── YYYY-WNN/
│   │       └── index.json
│   ├── monthly/
│   │   └── YYYY-MM/
│   │       └── index.json
│   └── manual/              # 手动按需翻译
│       ├── jobs.json
│       └── YYYY-MM-DD/
│           └── index.json
│
├── logs/
│   ├── cron-daily.log
│   ├── cron-weekly.log
│   ├── cron-monthly.log
│   ├── repair.log
│   └── {mode}-{key}.log     # 每次运行的详细日志
│
└── README.md
```

### 数据架构说明

所有论文的元数据（标题、摘要、中文翻译、关键词）和 PDF 仅存一份，统一在 `data/papers/`。daily / weekly / monthly / manual 的 index.json 只保存论文 ID + 榜单顺序（rank/upvotes），web_server 在渲染时实时从 paper store 查询完整内容。

**优势**：同一篇论文出现在多个榜单时，翻译只做一次、PDF 只存一份，不存在跨 mode 数据冗余。

---

## 技术架构

```
┌─────────────────────────────────────────────────────────────┐
│  定时触发（cron）                                             │
│                                                               │
│  23:00 每天      → run_daily.py   → HF /date/YYYY-MM-DD     │
│  02:00 每周日    → run_weekly.py  → HF /week/YYYY-WNN        │
│  02:00 每月28日  → run_monthly.py → HF /month/YYYY-MM        │
│                          ↓                                    │
│                    fetch_hf.py（指数退避重试，代理→直连）     │
│                          ↓ 失败（网络瞬断）                   │
│                    run_repair.py --refetch（次日 02:00）      │
│                    → 检测无 index.json 的日期 → 重跑全流程    │
│                          ↓ 成功                               │
│                    translate_arxiv.py                         │
│                    arXiv API → 元数据 → LLM 翻译 → HTML      │
│                          ↓                                    │
│                    translate_full.py                          │
│                    docker exec full_translate_driver.py       │
│                    → gpt-academic Latex翻译中文并重新编译PDF  │
│                    → xelatex + ctex + fontset=fandol          │
│                    → compile_latex（进程组 kill，300s 超时）  │
│                    → 最多 3 次重试（含 extract 清理）         │
│                          ↓                                    │
│  01:00 每天      → run_repair.py --post（补翻译 + 补索引）    │
│  06:00 每天      → run_repair.py --retry-pdf（PDF 重试）      │
│                          ↓                                    │
│                    web_server.py :18080                       │
│                    首页 / 每日 / 每周 / 每月 / 详情           │
└─────────────────────────────────────────────────────────────┘
```

### 依赖环境

| 组件 | 版本 / 说明 |
|------|------------|
| Python | 3.10.13（`/root/.pyenv/versions/3.10.13`）|
| gpt-academic | Docker 容器 `gpt-academic-latex`（含 TeX Live / pdflatex）|
| Clash | 本地代理 `127.0.0.1:7890`，用于访问 arXiv / HF |
| API | OpenAI 兼容接口，读自 gpt-academic `config_private.py` |

---

## 配置说明

### API 配置（自动读取）

```
/root/workspace/gpt-academic/config_private.py
  API_KEY=sk-xxx
  LLM_MODEL=gpt-4.1-mini
  API_URL_REDIRECT={"...openai...": "https://your-proxy/..."}
```

### 定时任务（crontab）

```cron
PYTHON=/root/.pyenv/versions/3.10.13/bin/python3
PTDIR=/root/workspace/paper-trans
RLOG=$PTDIR/logs/repair.log

# ── 主抓取 ────────────────────────────────────────────────────────────────
# 每天 23:00 — daily top 3（摘要 + 全文 PDF）
0 23 * * *   $PYTHON $PTDIR/run_daily.py   >> $PTDIR/logs/cron-daily.log   2>&1
# 每周日 02:00 — weekly top 10
0  2 * * 0   $PYTHON $PTDIR/run_weekly.py  >> $PTDIR/logs/cron-weekly.log  2>&1
# 每月 28 日 02:00 — monthly top 10
0  2 28 * *  $PYTHON $PTDIR/run_monthly.py >> $PTDIR/logs/cron-monthly.log 2>&1

# ── 容器维护 ─────────────────────────────────────────────────────────────
# 每天 05:00 — 重启翻译容器，清除僵尸进程
0  5 * * *   docker restart gpt-academic-latex >> $PTDIR/logs/docker-restart.log 2>&1
# 每周日 03:00 — 清理 Docker 翻译缓存
30 3 * * 0   $PTDIR/scripts/cleanup_docker_cache.sh

# ── 修复任务 ─────────────────────────────────────────────────────────────
# 每天 01:00 — daily 补翻译 + 补索引
0  1 * * *   $PYTHON $PTDIR/run_repair.py --post       --mode daily   --days 2  >> $RLOG 2>&1
# 每天 06:00 — daily PDF 重试（须在 05:00 docker 重启后）
0  6 * * *   $PYTHON $PTDIR/run_repair.py --retry-pdf  --mode daily   --days 7  >> $RLOG 2>&1
# 每周日 04:00 — weekly 补翻译 + 补索引
0  4 * * 0   $PYTHON $PTDIR/run_repair.py --post       --mode weekly  --days 14 >> $RLOG 2>&1
# 每周日 07:00 — weekly PDF 重试
0  7 * * 0   $PYTHON $PTDIR/run_repair.py --retry-pdf  --mode weekly  --days 14 >> $RLOG 2>&1
# 每月 28 日 04:00 — monthly 补翻译 + 补索引
0  4 28 * *  $PYTHON $PTDIR/run_repair.py --post       --mode monthly --days 60 >> $RLOG 2>&1
# 每月 28 日 07:00 — monthly PDF 重试
0  7 28 * *  $PYTHON $PTDIR/run_repair.py --retry-pdf  --mode monthly --days 60 >> $RLOG 2>&1
```

> **日期说明**：各脚本均根据运行时间自动计算目标日期，无需手动指定。ISO 8601 周规则：周日为第 7 天，仍属于本周。
>
> **--post vs --retry-pdf**：`--post` 修复摘要翻译缺失（repair + refetch）；`--retry-pdf` 专门重试 PDF 编译失败的论文，安排在每天 05:00 docker 重启之后执行，避免容器状态问题。

### systemd 服务

```bash
systemctl status  paper-trans-web   # 查看状态
systemctl restart paper-trans-web   # 重启
journalctl -u paper-trans-web -f    # 实时日志
```

---

## Web 界面

### 首页 `/`

- 三个板块：**每日精选**（Top 3）/ **本周热榜**（最新5篇预览）/ **本月热榜**（最新3篇预览）
- 顶部 Tab 导航：📅 每日 / 📚 每周 / 📆 每月
- 全局统计：已抓天数 / 周数 / 月数

### 列表页

| 路径 | 内容 |
|------|------|
| `/daily` | 所有已抓日期列表 |
| `/weekly` | 所有已抓周列表 |
| `/monthly` | 所有已抓月列表 |
| `/daily/YYYY-MM-DD` | 当日 Top 3 论文卡片 |
| `/weekly/YYYY-WNN` | 当周 Top 10 论文卡片 |
| `/monthly/YYYY-MM` | 当月 Top 10 论文卡片 |
| `/view/ARXIV_ID` | PDF 查看器（标签页显示中文标题） |
| `/papers/ARXIV_ID_zh.pdf` | 原始中文 PDF 文件（流式传输） |

### 论文卡片

每张卡片展示：中文标题 / AI 摘要 / 关键词 / 提交日期 / 热度（upvotes）

按钮：🔍 详情 | 📄 全文中文PDF（有则显示）| arXiv | 原文PDF

> 📄 全文PDF 按钮指向 `/view/ARXIV_ID`，在独立标签页中全屏展示 PDF，浏览器标签页标题显示中文论文名。

---

## 全文翻译说明

全文翻译调用 gpt-academic 容器内的 `Latex翻译中文并重新编译PDF` 插件：

1. **下载** arXiv LaTeX 源码包（`.tar.gz`）
2. **翻译** LLM 逐段翻译 `.tex` 文件（保留 LaTeX 命令）
3. **编译** `xelatex`（含 ctex）重新编译为中文 PDF（使用 Fandol 字体）
4. **重试** 最多 3 次（每次清空缓存，含 extract 目录，防止权限问题）
5. **输出** 保存到 `data/papers/<arxiv_id>_zh.pdf`（paper store，所有 mode 共用）

> ⚠️ 全文翻译每篇约 3～15 分钟，依赖论文 LaTeX 源码可用性。
> 无 LaTeX 源码或编译失败的论文不生成 PDF（摘要翻译仍可用）。
> PDF 编译失败会记录为 `pdf_status=failed`，由 `--retry-pdf` cron 任务自动重试。

**关键技术细节**

- 编译器：`xelatex`（`ctex` 包注入后自动切换，`fontset=fandol` 替代 Windows CJK 字体）
- 编译超时：300s / 次（进程组 kill，防止孤儿进程）
- 驱动脚本 `full_translate_driver.py` 由 `translate_full.py` 复制进容器执行
- 代理：host 网络模式，`127.0.0.1:7890`
- 跳过全文翻译：运行时加 `--no-full` 参数

**Docker 容器环境初始化**

容器 `gpt-academic-latex` 创建或重建后，需运行一次修补脚本：

```bash
bash /root/workspace/paper-trans/scripts/setup_docker_env.sh
```

该脚本完成 8 项修补（字体安装、fontconfig 映射、bxcoloremoji、fontset=fandol 注入、ctex xelatex 检测、axessibility 移除、merge_tex 路径修复、arxiv_cache 权限修复），确保在 Linux 环境下正确编译各类 LaTeX 论文。

---

## 监控与维护

```bash
# 查看当日翻译进度
tail -f /root/workspace/paper-trans/logs/cron-daily.log

# 查看当周翻译进度
tail -f /root/workspace/paper-trans/logs/cron-weekly.log

# 检查 paper store 覆盖率
python3 - << 'EOF'
import json, os
BASE = "/root/workspace/paper-trans/data"
STORE = os.path.join(BASE, "papers")
for mode in ["daily", "weekly", "monthly"]:
    d = os.path.join(BASE, mode)
    if not os.path.exists(d): continue
    for key in sorted(os.listdir(d))[-1:]:
        idx = json.load(open(f"{d}/{key}/index.json"))
        pdfs = sum(1 for p in idx["papers"]
                   if p.get("pdf_status") == "ok" or
                   os.path.exists(os.path.join(STORE, p["arxiv_id"] + "_zh.pdf")))
        print(f"[{mode}] {key}: {pdfs}/{len(idx['papers'])} PDFs")
EOF

# Web 服务状态
systemctl status paper-trans-web
```
