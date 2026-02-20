# 📰 Paper Hub

> 自动抓取 Hugging Face 每日 / 每周 / 每月热门 AI 论文，LLM 翻译摘要 + 全文中文 PDF，现代化 Web 界面对外发布。支持手动输入 arXiv ID 按需翻译，内置收藏夹功能。

**Web 访问**：http://xxxxxxxxx:18080

---

## 功能特性

| 功能 | 说明 |
|------|------|
| 📅 **每日 Top 3** | 每天 23:00 抓取当日热榜前 3 篇，翻译摘要 + 全文 PDF |
| 📚 **每周 Top 10** | 每周日 02:00 抓取本周热榜前 10 篇，翻译摘要 + 全文 PDF |
| 📆 **每月 Top 10** | 每月 28 日 02:00 抓取本月热榜前 10 篇，翻译摘要 + 全文 PDF |
| 🤖 **摘要翻译** | LLM 翻译标题、摘要，提取关键词和核心贡献 |
| 📄 **全文翻译** | gpt-academic LaTeX 插件翻译全文，重新编译中文 PDF |
| 🌍 **Web 发布** | 现代响应式界面，首页汇总三类内容，Tab 导航 |
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
    -o data/weekly/2026-W08/papers
```

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
├── run_papers.py            # 通用处理 runner（三种模式共用）
├── fetch_hf.py              # 统一 HF 抓取器（daily/weekly/monthly）
├── translate_arxiv.py       # 摘要翻译 → 双语 HTML
├── translate_full.py        # 全文翻译（容器外封装）
├── full_translate_driver.py # 全文翻译（容器内驱动，含重试逻辑）
├── web_server.py            # Web 服务器（端口 18080）
│
├── data/                    # 统一数据目录
│   ├── daily/
│   │   └── YYYY-MM-DD/
│   │       ├── index.json
│   │       └── papers/
│   │           ├── ARXIV_ID.html       # 双语摘要页
│   │           └── ARXIV_ID_zh.pdf    # 全文中文 PDF
│   ├── weekly/
│   │   └── YYYY-WNN/
│   │       ├── index.json
│   │       └── papers/
│   └── monthly/
│       └── YYYY-MM/
│           ├── index.json
│           └── papers/
│
├── logs/
│   ├── cron-daily.log
│   ├── cron-weekly.log
│   ├── cron-monthly.log
│   └── {mode}-{key}.log     # 每次运行的详细日志
│
├── README.md
├── plan.md
└── change.md
```

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
│                    fetch_hf.py（统一解析 arxiv ID）           │
│                          ↓                                    │
│                    translate_arxiv.py                         │
│                    arXiv API → 元数据 → LLM 翻译 → HTML      │
│                          ↓                                    │
│                    translate_full.py                          │
│                    docker exec full_translate_driver.py       │
│                    → gpt-academic Latex翻译中文并重新编译PDF  │
│                    → compile_latex（进程组 kill，300s 超时）  │
│                    → 最多 3 次重试                            │
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
# 每天 23:00 — daily top 3（摘要 + 全文 PDF）
0 23 * * * /root/.pyenv/versions/3.10.13/bin/python3 /root/workspace/paper-trans/run_daily.py >> /root/workspace/paper-trans/logs/cron-daily.log 2>&1

# 每周日 02:00 — weekly top 10（摘要 + 全文 PDF）
0 2 * * 0 /root/.pyenv/versions/3.10.13/bin/python3 /root/workspace/paper-trans/run_weekly.py >> /root/workspace/paper-trans/logs/cron-weekly.log 2>&1

# 每月 28 日 02:00 — monthly top 10（摘要 + 全文 PDF）
0 2 28 * * /root/.pyenv/versions/3.10.13/bin/python3 /root/workspace/paper-trans/run_monthly.py >> /root/workspace/paper-trans/logs/cron-monthly.log 2>&1
```

> **日期说明**：各脚本均根据运行时间自动计算目标日期，无需手动指定。
> ISO 8601 周规则：周日为第 7 天，仍属于本周（`current_week_key()`）。

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

### 论文卡片

每张卡片展示：中文标题 / AI 摘要 / 关键词 / 提交日期 / 热度（upvotes）

按钮：🔍 详情 | 📄 全文中文PDF（有则显示）| arXiv | 原文PDF

---

## 全文翻译说明

全文翻译调用 gpt-academic 容器内的 `Latex翻译中文并重新编译PDF` 插件：

1. **下载** arXiv LaTeX 源码包（`.tar.gz`）
2. **翻译** LLM 逐段翻译 `.tex` 文件（保留 LaTeX 命令）
3. **编译** `pdflatex` 重新编译为中文 PDF
4. **重试** 最多 3 次（每次清空缓存重新翻译）
5. **输出** 保存到 `data/<mode>/<key>/papers/<arxiv_id>_zh.pdf`

> ⚠️ 全文翻译每篇约 5～15 分钟，依赖论文 LaTeX 源码可用性。
> 无 LaTeX 源码或编译失败的论文不生成 PDF（摘要翻译仍可用）。

**关键技术细节**

- `pdflatex` 超时：300s / 次（进程组 kill，防止孤儿进程）
- 驱动脚本位于容器内 `/tmp/full_translate_driver.py`
- 代理：host 网络模式，`127.0.0.1:7890`
- 跳过全文翻译：运行时加 `--no-full` 参数

---

## 监控与维护

```bash
# 查看当日翻译进度
tail -f /root/workspace/paper-trans/logs/cron-daily.log

# 查看当周翻译进度
tail -f /root/workspace/paper-trans/logs/cron-weekly.log

# 检查 PDF 生成情况
python3 - << 'EOF'
import json, os
BASE = "/root/workspace/paper-trans/data"
for mode in ["daily", "weekly", "monthly"]:
    d = os.path.join(BASE, mode)
    if not os.path.exists(d): continue
    for key in sorted(os.listdir(d))[-1:]:
        idx = json.load(open(f"{d}/{key}/index.json"))
        pdfs = sum(1 for p in idx["papers"] if p.get("pdf_zh"))
        print(f"[{mode}] {key}: {pdfs}/{len(idx['papers'])} PDFs")
EOF

# Web 服务状态
systemctl status paper-trans-web
```
