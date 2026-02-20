# Paper Trans 项目计划

## 项目目标
每周自动抓取 Hugging Face Weekly Top 10 Papers，进行 AI 摘要翻译和全文中文 PDF 翻译，通过 Web 界面对外提供访问。

## 核心功能

| # | 功能 | 状态 |
|---|------|------|
| 1 | 定时抓取：每周日凌晨 2 点自动执行 | ✅ 已完成 |
| 2 | 数据源：`https://huggingface.co/papers/week/YYYY-WNN` | ✅ 已完成 |
| 3 | 摘要翻译：LLM 翻译标题/摘要/关键词/速读 | ✅ 已完成 |
| 4 | HTML 展示页：双语摘要 Tab 切换，关键词标签 | ✅ 已完成 |
| 5 | 全文翻译：gpt-academic LaTeX 插件翻译为中文 PDF | ✅ 已完成 |
| 6 | Web 访问：端口 18080，公网可访问 | ✅ 已完成 |
| 7 | 增量处理：已翻译内容自动跳过 | ✅ 已完成 |

## 技术架构

### 翻译流程
```
HF Weekly Page  (fetch_weekly.py)
    ↓
arxiv ID 列表 (Top 10)
    ↓  (translate_arxiv.py)
arxiv 元数据 (标题/摘要/作者/类别)
    ↓  (LLM API 直接调用)
双语 HTML 页面  ←  存 weekly/YYYY-WNN/papers/arxiv_id.html
    ↓  (translate_full.py + full_translate_driver.py, --full 模式)
LaTeX 源码 → LLM 翻译 → pdflatex 编译
    ↓  (docker cp)
中文 PDF  →  存 weekly/YYYY-WNN/papers/arxiv_id_zh.pdf
    ↓  (web_server.py)
Web 界面  http://51.79.130.17:18080
```

### 核心模块

| 文件 | 功能 |
|------|------|
| `fetch_weekly.py` | 抓取 HF Weekly Papers 列表（代理自动降级） |
| `translate_arxiv.py` | 获取 arxiv 元数据 + LLM 翻译 + 生成 HTML |
| `main.py` | 主调度脚本，整合全流程；支持 `--full` 标志 |
| `translate_full.py` | 全文翻译封装（容器外，调用 docker exec + docker cp） |
| `full_translate_driver.py` | 全文翻译驱动（容器内，调用 gpt-academic 插件） |
| `web_server.py` | HTTP 服务器，端口 18080；响应式 Web 界面 |

## 项目结构
```
/root/workspace/paper-trans/
├── main.py                   # 主入口
├── fetch_weekly.py           # 抓取脚本
├── translate_arxiv.py        # 摘要翻译
├── translate_full.py         # 全文翻译（容器外）
├── full_translate_driver.py  # 全文翻译驱动（容器内）
├── web_server.py             # Web 服务
├── README.md                 # 项目说明
├── plan.md                   # 本文件
├── change.md                 # 变更日志
├── weekly/
│   └── 2026-W08/
│       ├── index.json        # 周索引
│       └── papers/
│           ├── 2602.xxxxx.html      # 双语摘要页面
│           └── 2602.xxxxx_zh.pdf    # 全文中文 PDF
└── logs/
    ├── 2026-W08.log          # 周处理日志
    ├── full_translate_2026-W08.log  # 全文翻译日志
    ├── cron.log              # cron 执行日志
    └── web.log               # Web 服务日志
```

## 配置

### API 配置（自动读取 gpt-academic）
- 配置文件: `/root/workspace/gpt-academic/config_private.py`
- 默认模型: `gpt-4.1-mini`
- API 代理: `https://api.aaai.vip/v1/chat/completions`
- 网络代理: `http://127.0.0.1:7890` (Clash)

### systemd 服务
- 文件: `/etc/systemd/system/paper-trans-web.service`
- 运行时: Python 3.10.13 (`/root/.pyenv/versions/3.10.13/bin/python3`)
- 端口: 18080
- 开机自启: 已启用

### cron 定时任务
```
0 2 * * 0 /root/.pyenv/versions/3.10.13/bin/python3 /root/workspace/paper-trans/main.py --full >> /root/workspace/paper-trans/logs/cron.log 2>&1
```
说明: 每周日凌晨 2:00 执行（摘要 + 全文翻译）

## 使用方法

### 手动运行
```bash
cd /root/workspace/paper-trans

# 只做摘要翻译（快速，约 2 分钟/10 篇）
python3 main.py
python3 main.py 2026-W08

# 摘要 + 全文 PDF 翻译（每篇约 15-30 分钟）
python3 main.py 2026-W08 --full

# 单篇全文翻译
python3 translate_full.py 2602.10388 -o weekly/2026-W08/papers
```

### 服务管理
```bash
systemctl status paper-trans-web
systemctl restart paper-trans-web
journalctl -u paper-trans-web -f
```

### 查看日志
```bash
tail -f /root/workspace/paper-trans/logs/cron.log
tail -f /root/workspace/paper-trans/logs/full_translate_2026-W08.log
tail -f /root/workspace/paper-trans/logs/web.log
```

## 实施进度

### ✅ Phase 1: 环境准备
- [x] 确认 gpt-academic Docker 容器运行中 (`gpt-academic-latex`)
- [x] 确认 API Key 已在 gpt-academic config_private.py 中配置
- [x] 安装 Python 3.10.13 (pyenv) 及依赖 (requests, beautifulsoup4)

### ✅ Phase 2: 摘要翻译（v2.0）
- [x] `fetch_weekly.py` - 抓取 HF Weekly Top 10，支持代理自动降级
- [x] `translate_arxiv.py` - 获取 arxiv 元数据 + LLM 翻译 + HTML 生成
- [x] `main.py` - 主调度，支持增量处理（跳过已翻译）
- [x] `web_server.py` - 现代化 Web 界面，响应式设计

### ✅ Phase 3: 部署配置
- [x] systemd 服务 (paper-trans-web) 使用 Python 3.10
- [x] cron 定时任务 (每周日凌晨 2 点，含 --full)
- [x] 端口 18080 对外开放

### ✅ Phase 4: 全文翻译（v2.1）
- [x] `full_translate_driver.py` - 容器内 gpt-academic 插件驱动
- [x] `translate_full.py` - 容器外封装（docker exec + docker cp）
- [x] 代理 monkey-patch（容器内 host 网络模式）
- [x] `main.py` 集成 `--full` 参数
- [x] `web_server.py` 展示全文 PDF 下载链接
- [x] 实测 2602.12783 全文翻译成功（~15min，3.8MB PDF）

### ✅ Phase 5: 文档与 UI 优化（v2.2）
- [x] 编写 README.md（完整项目说明）
- [x] 更新 plan.md / change.md
- [x] 优化 Web UI（更现代的卡片设计，进度指示器）
- [x] 批量全文翻译 2026-W08 全部 10 篇

## 已知限制
1. **代理依赖**: 访问 arxiv.org 和 HF 需要 Clash 代理 (127.0.0.1:7890)，断开时自动降级
2. **API 成本**: 摘要翻译每篇约 1000-2000 tokens (gpt-4.1-mini)
3. **全文翻译耗时**: 每篇约 15-30 分钟，依赖论文 LaTeX 源码可用性
4. **排名精确性**: HF 页面抓取基于 HTML 结构，仅保证 top 10 不保证确切排名顺序

## 可选优化（未来）
- [ ] 支持多周数据对比 / 趋势页面
- [ ] 添加论文分类/标签过滤
- [ ] RSS 订阅支持
- [ ] 全文翻译并行执行（多篇同时）
- [ ] 邮件/钉钉通知翻译完成
