# Paper Hub

Paper Hub 自动抓取 Hugging Face 热门 AI 论文，翻译标题、摘要和核心信息，并按需生成全文中文 PDF。项目同时提供公网 Web 页面、手动提交、收藏夹、全局搜索和系统状态监控。

- 线上入口：https://zzzgry.top/paper/
- 本地服务：http://127.0.0.1:18080
- systemd 服务：`paper-trans-web.service`
- 主数据源：`data/papers/`

---

## 当前能力

| 功能 | 状态 | 说明 |
|---|---:|---|
| 每日 Top 3 | 完成 | `run_daily.py` 抓取、摘要翻译、可选全文 PDF |
| 每周 Top 10 | 完成 | `run_weekly.py` 抓取 weekly 榜单 |
| 每月 Top 10 | 完成 | `run_monthly.py` 抓取 monthly 榜单 |
| 摘要翻译 | 完成 | `translate_arxiv.py` 写入 paper store JSON |
| 全文中文 PDF | 完成 | `translate_full.py` 调用 Docker 内 gpt-academic LaTeX 流程 |
| 手动提交 | 完成 | `/submit` 输入 arXiv ID，后台排队处理 |
| 收藏夹 | 完成 | `/bookmarks` 支持多列表、移动、移出、重命名 |
| 全局搜索 | 完成 | `/search` 搜索标题、摘要、作者、关键词、arXiv ID |
| 系统状态 | 完成 | `/status` 查看磁盘、容器翻译进程和任务队列 |
| PDF 查看页 | 完成 | `/view/<id>` 固定 HTML wrapper，保证浏览器标签页标题正确 |
| 行为合约测试 | 完成 | `tests/test_web_server_contract.py` 锁定核心路由和点击链接 |

---

## 快速命令

### 抓取与翻译

```bash
cd /root/workspace/paper-trans

# daily
python3 run_daily.py
python3 run_daily.py 2026-06-05
python3 run_daily.py 2026-06-05 --no-full

# weekly
python3 run_weekly.py
python3 run_weekly.py 2026-W22
python3 run_weekly.py 2026-W22 --no-full

# monthly
python3 run_monthly.py
python3 run_monthly.py 2026-05
python3 run_monthly.py 2026-05 --no-full

# 单篇全文 PDF，输出到统一 paper store
python3 translate_full.py 2605.21573 -o data/papers
```

### 修复与重试

```bash
# 修复 title_zh / summary_zh 缺失
python3 run_repair.py
python3 run_repair.py --mode daily --days 2
python3 run_repair.py --mode weekly --key 2026-W22

# 补抓缺失 index.json 的日期/周期
python3 run_repair.py --refetch --mode daily --days 3

# 补翻译 + 补索引
python3 run_repair.py --post --mode daily --days 2

# 对 pdf_status=failed 的条目重试全文 PDF
python3 run_repair.py --retry-pdf --mode weekly --key 2026-W22
python3 run_repair.py --retry-pdf --mode daily --days 7
```

`retry-pdf` 会优先复用已有的翻译 tex 缓存；如果没有翻译 tex 但容器内已有有效 arXiv 源码包，也会复用源码包重新翻译/编译，避免网络断流时反复失败在源码下载阶段。

### Web 服务

```bash
systemctl status paper-trans-web.service
systemctl restart paper-trans-web.service
tail -f /root/workspace/paper-trans/logs/web.log
```

---

## Web 路由契约

这些入口是当前对外行为，重构时必须保持不变。

| 路由 | 类型 | 行为 |
|---|---|---|
| `/` | HTML | 首页，展示最新 daily / weekly / monthly |
| `/daily`, `/weekly`, `/monthly` | HTML | 各 mode 的期列表 |
| `/<mode>/<key>` | HTML | 某一期论文卡片列表 |
| `/<mode>/<key>/papers/<arxiv_id>` | HTML | 动态详情页，失败时回退旧 HTML 文件 |
| `/view/<arxiv_id>` | HTML | PDF wrapper 页面，`iframe` 加载中文 PDF |
| `/papers/<file>` | PDF/HTML/JSON | paper store 静态文件回退 |
| `/pdf/<arxiv_id>/<title>.pdf` | PDF | 直接 PDF 路由，保留中文文件名响应头 |
| `/bookmarks` | HTML/JSON | 收藏夹页面和 API |
| `/submit` | HTML/API | 手动提交页面和 API |
| `/search` | HTML/API | 搜索页面和 API |
| `/status` | HTML/API | 系统状态页面和 API |

### PDF 查看页说明

`/view/<arxiv_id>` 固定返回 HTML wrapper，不再跳转到裸 PDF。这样 Atlas / Chrome / Safari 的标签页标题都由外层 `<title>` 控制，不受 PDF 内部 metadata 中 `[Your Paper Title]` 之类占位符影响。

wrapper 内部 iframe 指向：

```text
{BASE_PATH}/papers/<arxiv_id>_zh.pdf#view=FitH
```

`/papers/<id>_zh.pdf` 和 `/pdf/<id>/<title>.pdf` 都保留 PDF Range 支持，用于浏览器 PDF viewer 和大文件加载。

---

## 数据架构

Paper Hub 使用统一 paper store，避免同一篇论文在 daily / weekly / monthly / manual 中重复翻译和重复存 PDF。

```text
data/
├── papers/
│   ├── <arxiv_id>.json      # 完整元数据、中文标题、摘要、关键词、pdf_status
│   └── <arxiv_id>_zh.pdf    # 全文中文 PDF
├── daily/<YYYY-MM-DD>/index.json
├── weekly/<YYYY-WNN>/index.json
├── monthly/<YYYY-MM>/index.json
└── manual/
    ├── jobs.json
    └── <YYYY-MM-DD>/index.json
```

`index.json` 是 slim index，只保存榜单和状态字段，例如 `arxiv_id`、`rank`、`upvotes`、`pdf_status`。Web 渲染时通过 `web_server.py` 合并 slim index 和 `data/papers/<id>.json`。

---

## 项目结构

```text
paper-trans/
├── fetch_hf.py                 # Hugging Face papers 抓取
├── run_daily.py                # daily 入口
├── run_weekly.py               # weekly 入口
├── run_monthly.py              # monthly 入口
├── run_papers.py               # 通用处理、PDF retry、slim index 写入
├── run_repair.py               # repair/refetch/post/retry-pdf 调度
├── translate_arxiv.py          # arXiv 元数据 + 摘要翻译 + paper store JSON
├── translate_full.py           # 宿主机侧全文 PDF 翻译封装
├── full_translate_driver.py    # 容器内 gpt-academic 驱动和 LaTeX fallback
├── web_server.py               # 单文件 HTTP Web 服务
├── tests/
│   └── test_web_server_contract.py
├── scripts/
│   ├── setup_docker_env.sh
│   ├── cleanup_docker_cache.sh
│   ├── weekly_cleanup.sh
│   ├── patch_axessibility.py
│   └── patch_find_main_tex.py
├── README.md
├── change.md
└── plan.md
```

---

## Web 实现要点

- `web_server.py` 保持单文件部署，避免引入 Flask/FastAPI 或额外前端构建流程。
- `paper_pdf_state()` 统一 PDF 状态判断。
- `enrich_paper_entry()` 统一 slim index + paper store 合并。
- `render_paper_actions()` 统一详情、全文 PDF、arXiv、原文 PDF 链接生成。
- `h_text()`、`h_attr()`、`js_str()` 分别处理 HTML 文本、HTML 属性、inline JS 字符串。
- `BASE_PATH=/paper` 由 systemd 注入，页面生成时为内部链接添加前缀；JS API 调用使用 `window.BP`。

---

## 测试与验证

### 语法检查

```bash
python3 -m py_compile \
  web_server.py translate_arxiv.py translate_full.py \
  full_translate_driver.py run_papers.py run_repair.py \
  tests/test_web_server_contract.py
```

### 合约测试

```bash
python3 -m unittest discover -s tests -v
/root/.pyenv/versions/3.10.13/bin/python3 -m unittest discover -s tests -v
```

测试覆盖：

- 核心页面返回 HTML。
- JSON API 返回 JSON。
- `/view/<id>` 是 HTML wrapper 且没有 302。
- `/papers/<id>_zh.pdf` 和 `/pdf/<id>/<title>.pdf` 保留 Range。
- 详情页保留 `/view/<id>`、arXiv abs、arXiv PDF 链接。
- `BASE_PATH=/paper` 下内部链接和 PDF iframe 前缀正确。
- 搜索、提交、状态页关键 fetch/click 合约仍存在。

### 线上抽查

```bash
curl -k -I https://zzzgry.top/paper/view/2605.21573
curl -k -I -r 0-0 https://zzzgry.top/paper/papers/2605.21573_zh.pdf
curl -k -I https://zzzgry.top/paper/weekly/2026-W22/papers/2605.23904
```

---

## 部署与运行环境

| 项 | 值 |
|---|---|
| Python | `/root/.pyenv/versions/3.10.13/bin/python3` |
| Web 端口 | `18080` |
| 绑定地址 | 默认 `127.0.0.1`，可用 `BIND_HOST` 覆盖 |
| 路径前缀 | `BASE_PATH=/paper` |
| Docker 容器 | `gpt-academic-latex` |
| 网络代理 | `http://127.0.0.1:7890`，失败时部分请求会切直连 |
| Web 日志 | `logs/web.log` |

systemd unit：

```ini
[Service]
WorkingDirectory=/root/workspace/paper-trans
Environment=BASE_PATH=/paper
ExecStart=/root/.pyenv/versions/3.10.13/bin/python3 /root/workspace/paper-trans/web_server.py
Restart=always
StandardOutput=append:/root/workspace/paper-trans/logs/web.log
StandardError=append:/root/workspace/paper-trans/logs/web.log
```

---

## Cron 运维建议

```cron
PYTHON=/root/.pyenv/versions/3.10.13/bin/python3
PTDIR=/root/workspace/paper-trans
RLOG=$PTDIR/logs/repair.log

0 23 * * *   $PYTHON $PTDIR/run_daily.py   >> $PTDIR/logs/cron-daily.log   2>&1
0  2 * * 0   $PYTHON $PTDIR/run_weekly.py  >> $PTDIR/logs/cron-weekly.log  2>&1
0  2 28 * *  $PYTHON $PTDIR/run_monthly.py >> $PTDIR/logs/cron-monthly.log 2>&1

0  5 * * *   docker restart gpt-academic-latex >> $PTDIR/logs/docker-restart.log 2>&1
30 3 * * 0   $PTDIR/scripts/cleanup_docker_cache.sh

0  1 * * *   $PYTHON $PTDIR/run_repair.py --post       --mode daily   --days 2  >> $RLOG 2>&1
0  6 * * *   $PYTHON $PTDIR/run_repair.py --retry-pdf  --mode daily   --days 7  >> $RLOG 2>&1
0  4 * * 0   $PYTHON $PTDIR/run_repair.py --post       --mode weekly  --days 14 >> $RLOG 2>&1
0  7 * * 0   $PYTHON $PTDIR/run_repair.py --retry-pdf  --mode weekly  --days 14 >> $RLOG 2>&1
0  4 28 * *  $PYTHON $PTDIR/run_repair.py --post       --mode monthly --days 60 >> $RLOG 2>&1
0  7 28 * *  $PYTHON $PTDIR/run_repair.py --retry-pdf  --mode monthly --days 60 >> $RLOG 2>&1
```

---

## 维护约定

1. 修改 Web 路由、按钮、PDF 查看页或 `BASE_PATH` 行为前，先补或更新合约测试。
2. 每次代码、流程或部署方式改变后，同步更新 `README.md`、`change.md` 或 `plan.md`。
3. 发布前至少运行语法检查和 `python3 -m unittest discover -s tests -v`。
4. 如果改动影响线上 Web，重启 `paper-trans-web.service` 并抽查 `/paper/view/<id>`、详情页、PDF Range。
5. 不把 API key、代理密钥、个人邮箱等敏感信息写入仓库。
