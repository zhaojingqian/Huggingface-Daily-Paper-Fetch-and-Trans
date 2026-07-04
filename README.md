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
| 主题订阅 Top 3 | 完成 | `/topic` 订阅关键词，按相关性/新鲜度/HF vote 每日推荐 |
| 摘要翻译 | 完成 | `translate_arxiv.py` 写入 paper store JSON |
| 全文中文 PDF | 完成 | `translate_full.py` 调用 Docker 内 gpt-academic LaTeX 流程 |
| 手动提交 | 完成 | `/submit` 输入 arXiv ID，后台排队处理；提交动作需要管理口令 |
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

# topic subscription
python3 run_topic.py opd
python3 run_topic.py opd --no-full
python3 run_topic.py --all

# 单篇全文 PDF，输出到统一 paper store
python3 translate_full.py 2605.21573 -o data/papers
```

主题订阅使用 `.env` 中的 `TOPIC_LLM_API_KEY`、`TOPIC_LLM_BASE_URL`、`TOPIC_LLM_MODEL` 生成检索词，`TOPIC_ADMIN_TOKEN` 用于保护主题管理和手动提交动作；`.env` 已被 git ignore，不能提交密钥。主题检索默认限定 `cs.AI`、`cs.LG`、`cs.CL`、`cs.CV`、`cs.RO`、`cs.IR`、`stat.ML`，排序权重为相关性 45%、新鲜度 30%、HF vote 25%。检索词生成默认把用户输入解释为 AI/ML/CS 论文主题，要求 must 高精度、should 多元覆盖同义词/方法/任务/相邻概念，非 AI/ML/CS 常见含义进入 negative；代码侧还会去重、限制数量并过滤和 negative 冲突的召回词。同一 topic 已推送过的 arXiv ID 默认不重复推；paper store 会全站复用中文摘要和全文 PDF 缓存，避免重复翻译或重复生成 PDF。

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

`retry-pdf` 会优先复用已有的翻译 tex 缓存；宿主机成功备份和容器内 `merge_translate_zh.tex` 都可作为缓存来源。如果只有 tex 备份、容器 workfolder 已被清理，会先从有效 arXiv 源码缓存重建 workfolder，再只重跑编译。如果缓存重编译失败，外层 retry 会清缓存后自动退回 no-cache 全文重译。如果没有翻译 tex 但源码下载断流，驱动会先预下载并校验 `e-print/<id>.tar`，再交给 gpt-academic 重新翻译/编译，避免反复失败在源码下载阶段。

全文翻译驱动会在发布 PDF 前做两类门禁：一是检查 `merge_translate_zh.tex` 的普通正文翻译覆盖率，避免 splitter 漏译导致“大半 PDF 仍是英文”；二是检查 LaTeX log，拒绝 undefined command、undefined citation/reference 的 PDF。fallback 编译会自动修补常见翻译副作用，例如自定义零参数宏与中文/中文标点粘连、误生成的 `\textWord` 命令、唯一可推断的 label/ref 不一致、inline `\verb` 分隔符与正则内容冲突、坏 `.aux`、旧式 FontAwesome 图标、XeLaTeX 下缺失的 `\DeclareUnicodeCharacter`、algorithm2e 关键字被翻译、不安全 citation key，以及 XeLaTeX segfault 时的 LuaLaTeX fallback。

splitter 优化基于 gpt-academic 原始 `LatexPaperSplit`：先保留上游 mask 的 `PRESERVE/TRANSFORM` 结果，再对 preserve 节点做二次安全拆分。普通正文行会重新送翻译；`tabular/tabularx/longtable/array` 只翻译单元格文本，保留 `&` 和行尾 `\\`；`algorithmic` 只翻译命令后的自然语言参数。二次拆分后会再次套用类似上游 `post_process` 的语义收口，过短、命令占比过高或空白/分隔符类 chunk 会降级回 preserve，避免模型收到坏 chunk 后生成 “Below is/Please provide/请提供” 这类非原文回答。splitter 版本变化时会自动丢弃旧 `temp.pkl`，避免旧翻译缓存与新节点结构错位。质量门禁会检查这些软保护区域里的长英文，但仍跳过 equation、verbatim、listing、bibliography 等硬保护区域。

`latex_translation_filters.py` 统一维护 LaTeX 过滤策略，供 splitter、翻译覆盖率门禁、merge 前 `fix_content` 清理和 fallback 重编译共同使用。对超长普通正文行，splitter 会按句子边界继续拆分，避免长段 cite 密集内容被模型整体回显成英文。CLI/GUI、trace、trajectory、prompt、code、listing、verbatim 等命名特征的自定义环境会被动态识别为硬保护环境；但 fallback 只会从原文恢复真正的 verbatim/listing/trace 类环境，不会把 table/figure/equation 这类普通保护块恢复成英文。

过滤策略可通过环境变量扩展：`PAPER_TRANS_EXTRA_HARD_ENVS` 增加需要硬保护的环境名，`PAPER_TRANS_EXTRA_SOFT_ENVS` 增加可拆出自然语言继续翻译的环境名，`PAPER_TRANS_EXTRA_RESTORE_ENVS` 增加 fallback 中可从原文恢复的环境名，`PAPER_TRANS_EXTRA_LLM_ARTIFACT_PATTERNS` 按行增加需要清理的模型残留正则。

fallback 编译还会处理部分模板兼容问题：为旧模板补 `fontawesome5` legacy alias（含 `\faDatabase`、`\faEnvelopeO`、`\faEnvelope`、`\faGem` 等旧命令），禁用 XeLaTeX 下容易报错的 `microtype` 特性，为可选参数列表补 `enumitem`，补充 inputenc 场景常见的 `\DeclareUnicodeCharacter` no-op 兼容，从 tex 预生成 BibTeX 中间文件，guard 本地 class/style/source 中的 pdfTeX-only primitive，并在本地 class/style 硬编码不可用 `NVIDIASans_*` 或其他 T1 字体默认值时回退到容器已有字体。如果 arXiv 源码包只提供 `.bbl` 而没有对应 `.bib`，fallback 会复用已有且包含 `\bibitem` 的 `.bbl`，避免 BibTeX 生成空参考文献导致 undefined citation。若日志里先看到半截小 PDF，再看到 `.aux` 的 `File ended while scanning use of \citation`，需要优先查前一轮真正的 LaTeX/xdvipdfmx 崩溃原因。`Label(s) may have changed` 这类 rerun 提示不是发布拦截条件；真正会导致 `?` 的 undefined citation/reference 仍是硬失败。

宿主机侧 `translate_full.py` 使用非阻塞方式读取容器输出；当容器内长时间没有换行输出时，外层 timeout 仍会按时收口，并会尽力清理同篇 `full_translate_driver.py` 进程，避免 retry 阶段被悬挂的旧编译卡住。

`logs/pdf_errors/<arxiv_id>.log` 只保留最近一次失败诊断；同篇 PDF 后续成功生成后，`translate_full.py` 会自动清理旧失败日志。成功生成 PDF 后才会覆盖 `data/tex_backup/<id>_merge_translate_zh.tex`；失败现场会另存到 `data/tex_backup_failed/`，避免坏 tex 覆盖可用缓存。同篇 PDF 成功后，对应的失败现场 tex 也会自动清理。如果日志中出现 `No space left on device`，先用 `df -h /` 和 `docker exec ${GPT_ACADEMIC_CONTAINER:-gpt-academic-latex-slim} df -h /gpt /` 确认宿主机根分区与容器 overlay 空间；清理旧编辑器 server 缓存或 gpt-academic 可再生缓存后，再重跑 `retry-pdf`。如果编译超大图片/重资源论文时发生 `xdvipdfmx` 进程异常退出或超时（可能由 OOM 强杀导致），需确认独立容器已启用 `--memory-swappiness=60` 以允许向 Swap 换页。

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
| `/topic`, `/topic/<slug>` | HTML/API | 主题订阅管理、主题 Top 3 和历史结果 |
| `/<mode>/<key>` | HTML | 某一期论文卡片列表 |
| `/<mode>/<key>/papers/<arxiv_id>` | HTML | 动态详情页，失败时回退旧 HTML 文件 |
| `/view/<arxiv_id>` | HTML | PDF wrapper 页面，`iframe` 加载中文 PDF |
| `/papers/<file>` | PDF/HTML/JSON | paper store 静态文件回退 |
| `/pdf/<arxiv_id>/<title>.pdf` | PDF | 直接 PDF 路由，保留中文文件名响应头 |
| `/bookmarks` | HTML/JSON | 收藏夹页面和 API |
| `/submit` | HTML/API | 手动提交页面和 API |
| `/search` | HTML/API | 搜索页面和 API |
| `/status` | HTML/API | 系统状态页面和 API |

当服务以 `BASE_PATH=/paper` 部署时，请求入口同时接受带前缀的线上路径，例如 `/paper/view/<arxiv_id>` 和 `/paper/papers/<file>`；内部 redirect 会自动保留 `/paper` 前缀。

### PDF 查看页说明

`/view/<arxiv_id>` 固定返回 HTML wrapper，不再跳转到裸 PDF。这样 Atlas / Chrome / Safari 的标签页标题都由外层 `<title>` 控制，不受 PDF 内部 metadata 中 `[Your Paper Title]` 之类占位符影响。

wrapper 内部 iframe 指向：

```text
{BASE_PATH}/papers/<arxiv_id>_zh.pdf?v=<pdf_mtime>#view=FitH
```

`/view/<id>` wrapper 返回 `Cache-Control: no-store`，`v=<pdf_mtime>` 用于在重新生成中文 PDF 后绕开浏览器/PDF viewer 缓存，避免路由正确但 iframe 仍显示旧 PDF。`/papers/<id>_zh.pdf` 和 `/pdf/<id>/<title>.pdf` 都保留 PDF Range 支持，用于浏览器 PDF viewer 和大文件加载。

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
├── topic/
│   ├── topics.json            # 主题 profile、检索词、权重和启停状态
│   └── <slug>/<YYYY-MM-DD>/index.json
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
├── run_topic.py                # topic subscription 入口
├── topic_engine.py             # topic 检索词生成、召回、排序和翻译流程
├── run_papers.py               # 通用处理、PDF retry、slim index 写入
├── run_repair.py               # repair/refetch/post/retry-pdf 调度
├── translate_arxiv.py          # arXiv 元数据 + 摘要翻译 + paper store JSON
├── translate_full.py           # 宿主机侧全文 PDF 翻译封装
├── full_translate_driver.py    # 容器内 gpt-academic 驱动和 LaTeX fallback
├── latex_translation_filters.py # LaTeX 环境保护、质量过滤和 LLM 残留清理策略
├── web_server.py               # 单文件 HTTP Web 服务
├── paperhub/
│   ├── paths.py                 # 共享路径、paper store、容器默认名常量
│   ├── env_config.py            # 本地 .env 读取 helper
│   ├── paper_store.py           # 统一 paper store JSON/PDF 读写 helper
│   └── topic_store.py           # topic profile、seen 和 index 读写 helper
├── tests/
│   ├── test_web_server_contract.py
│   ├── test_latex_translation_filters.py
│   ├── test_paper_store.py
│   ├── test_paths.py
│   └── test_repair_refetch.py
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
- `BASE_PATH=/paper` 由 systemd 注入，页面生成时为内部链接添加前缀，请求入口会剥离此前缀后路由；JS API 调用使用 `window.BP`。

---

## 测试与验证

### 语法检查

```bash
python3 -m py_compile \
  paperhub/__init__.py paperhub/paths.py paperhub/paper_store.py \
  web_server.py translate_arxiv.py translate_full.py \
  full_translate_driver.py latex_translation_filters.py \
  run_papers.py run_repair.py \
  tests/test_web_server_contract.py tests/test_latex_translation_filters.py \
  tests/test_paper_store.py tests/test_paths.py tests/test_repair_refetch.py
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
- 收藏 API 的 create/toggle/move/remove 和常见 API 错误响应保持稳定。
- 入口脚本继续使用同一套共享路径常量。
- paper store 的 raw read、translated-cache read、PDF 阈值和 pdf_status 更新语义保持稳定。
- `run_repair.py --refetch/--post` 对 daily/weekly/monthly 当前周期的跳过边界保持稳定：只在首次 cron 触发时间未到时跳过，触发后允许补抓临时网络失败的周期。
- LaTeX fallback 对 inline `\verb` 分隔符冲突只修补可疑 regex/code 形态，不改普通 inline verb。

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
| Docker 容器 | 代码默认 `gpt-academic-latex-slim`，可用 `GPT_ACADEMIC_CONTAINER` 覆盖；当前生产使用 full-TeX slim |
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

当前 Web 手动提交使用 full-TeX slim 容器，systemd drop-in：

```ini
# /etc/systemd/system/paper-trans-web.service.d/10-slim-container.conf
[Service]
Environment=GPT_ACADEMIC_CONTAINER=gpt-academic-latex-slim
```

如需切换容器，修改该 drop-in 后执行 `systemctl daemon-reload && systemctl restart paper-trans-web.service`。原 `gpt-academic-latex` 容器和 `ghcr.io/binary-husky/gpt_academic_with_latex:master` 镜像已在 2026-06-12 删除，当前不再保留本机 Docker 回滚副本。

---

## Cron 运维建议

```cron
PYTHON=/root/.pyenv/versions/3.10.13/bin/python3
PTDIR=/root/workspace/paper-trans
RLOG=$PTDIR/logs/repair.log
# 当前 cron 使用 full-TeX slim 翻译容器
GPT_ACADEMIC_CONTAINER=gpt-academic-latex-slim

0 23 * * *   $PYTHON $PTDIR/run_daily.py   >> $PTDIR/logs/cron-daily.log   2>&1
30 1 * * *   $PYTHON $PTDIR/run_topic.py --all >> $PTDIR/logs/cron-topic.log 2>&1
0  2 * * 0   $PYTHON $PTDIR/run_weekly.py  >> $PTDIR/logs/cron-weekly.log  2>&1
0  2 28 * *  $PYTHON $PTDIR/run_monthly.py >> $PTDIR/logs/cron-monthly.log 2>&1

0  5 * * *   docker restart $GPT_ACADEMIC_CONTAINER >> $PTDIR/logs/docker-restart.log 2>&1
30 3 * * 0   $PTDIR/scripts/cleanup_docker_cache.sh

0  1 * * *   $PYTHON $PTDIR/run_repair.py --post       --mode daily   --days 2  >> $RLOG 2>&1
0  6 * * *   $PYTHON $PTDIR/run_repair.py --retry-pdf  --mode daily   --days 7  >> $RLOG 2>&1
0  4 * * 0   $PYTHON $PTDIR/run_repair.py --post       --mode weekly  --days 14 >> $RLOG 2>&1
0  7 * * 0   $PYTHON $PTDIR/run_repair.py --retry-pdf  --mode weekly  --days 14 >> $RLOG 2>&1
0  4 28 * *  $PYTHON $PTDIR/run_repair.py --post       --mode monthly --days 60 >> $RLOG 2>&1
0  7 28 * *  $PYTHON $PTDIR/run_repair.py --retry-pdf  --mode monthly --days 60 >> $RLOG 2>&1
```

`run_repair.py --post` 会先修复已有索引中的摘要，再补抓缺失或空 `index.json` 的周期。为避免提前抓取未到榜单生成时间的数据，当前周期只会在首次 cron 触发时间前被跳过：daily 为当天 23:00 前，weekly 为周日 02:00 前，monthly 为 28 日 02:00 前。触发时间之后如果遇到 Hugging Face 临时网络失败，后续 `--post` 会重新补抓该周期。

### full-TeX slim LaTeX 容器

当前生产翻译容器为 `gpt-academic-latex-slim`，镜像为 `paper-trans-latex-slim:latest`。它继续继承原 `gpt_academic_with_latex` 的完整 TeX/font 运行时，避免逐个补 TeX 包；同时删除 torch、nvidia、transformers、nougat、缓存、文档和源码等中文翻译不需要的大体积内容。

当前本机状态（2026-06-12）：

- 原镜像 `ghcr.io/binary-husky/gpt_academic_with_latex:master`：约 15.4GB。
- full-TeX slim 镜像 `paper-trans-latex-slim:latest`：约 7.62GB。
- 当前仅保留并运行 `gpt-academic-latex-slim`；原生产容器 `gpt-academic-latex` 与原 15.4GB 镜像已删除。
- 删除旧镜像后 Docker overlay2 曾残留孤儿目录；确认 Docker images/containers/volumes/build cache 均为空后，停 Docker/containerd 清理孤儿 overlay2，再启动服务，根分区可用空间恢复到约 14GB。
- compile canary 已通过：`2606.09967`、`2606.10917`、`2606.09828`、`2606.02060`。
- full no-cache canary 已通过：`2606.08432`。
- 2026-06-12 复盘 2026-06-11 daily 失败项：`2606.11926`、`2606.12344` 已在 slim 容器下修复并恢复为 `pdf_status=ok`。
- full-TeX slim 切换后再次用 `2606.11926`、`2606.12344` 复用中文 tex 备份重编译验证通过，PDF 分别约 2.52MB 和 1.88MB。

默认 `GPT_ACADEMIC_SLIM_TEX_PROFILE=full`，保留完整 TeX/font 运行时。历史的激进裁剪仍可用 `GPT_ACADEMIC_SLIM_TEX_PROFILE=slim` 显式开启；该模式会继续依赖轻量 stub 覆盖常见装饰字体包：`fontawesome` v4/v5/v6、`bbding`、`inconsolata`、`libertine`、`newtxmath`、`zlmtt`，并为 `Inconsolatazi4-*.otf` 提供字体文件别名。新增 stub 时必须同时更新 `scripts/setup_docker_env.sh` 和 `docker/latex-slim/Dockerfile`。

```bash
# 默认使用低磁盘 flatten 模式：从当前生产镜像创建临时容器，保留 full TeX，裁剪大依赖后 docker export/import
./scripts/build_latex_slim.sh

# 只估算 rootfs 体积，不导入镜像
GPT_ACADEMIC_SLIM_DRY_RUN=1 ./scripts/build_latex_slim.sh

# 低磁盘切换时可先导出压缩 rootfs，删除旧镜像腾空间后再手动 docker import
GPT_ACADEMIC_SLIM_EXPORT_ARCHIVE=/tmp/paper-trans-fulltex-slim.tar.gz \
  GPT_ACADEMIC_SLIM_EXPORT_COMPRESSOR=pigz \
  ./scripts/build_latex_slim.sh

# 显式使用历史 slim TeX 裁剪策略
GPT_ACADEMIC_SLIM_TEX_PROFILE=slim ./scripts/build_latex_slim.sh

# 启动独立容器 gpt-academic-latex-slim，并复用 config_private.py (默认携带 --memory=1400m --memory-swap=3000m --memory-swappiness=60 参数)
./scripts/run_latex_slim.sh

# 镜像已内置 setup 补丁时可跳过启动时 setup，减少磁盘和 apt cache 抖动
GPT_ACADEMIC_SKIP_SETUP=1 ./scripts/run_latex_slim.sh

# 默认 compile 模式：复用 data/tex_backup 中的中文 tex，只验证 LaTeX/runtime 编译链
./scripts/canary_latex_slim.sh

# full 模式：默认用 2606.08432 跑 --no-cache，验证 GPT 翻译 + LaTeX 编译完整链路
GPT_ACADEMIC_SLIM_CANARY_MODE=full ./scripts/canary_latex_slim.sh
```

如需在磁盘更宽裕的外部 builder 上走 Dockerfile 构建，可设置：

```bash
GPT_ACADEMIC_SLIM_BUILD_MODE=dockerfile ./scripts/build_latex_slim.sh
```

单次手动验证时可只给当前命令加环境变量，不影响生产容器：

```bash
GPT_ACADEMIC_CONTAINER=gpt-academic-latex-slim \
  python3 translate_full.py 2606.09967 -o /tmp/paper-trans-canary --no-cache
```

---

## 维护约定

1. 修改 Web 路由、按钮、PDF 查看页或 `BASE_PATH` 行为前，先补或更新合约测试。
2. 每次代码、流程或部署方式改变后，同步更新 `README.md`、`change.md` 或 `plan.md`。
3. 发布前至少运行语法检查和 `python3 -m unittest discover -s tests -v`。
4. 如果改动影响线上 Web，重启 `paper-trans-web.service` 并抽查 `/paper/view/<id>`、详情页、PDF Range。
5. 不把 API key、代理密钥、个人邮箱等敏感信息写入仓库。
