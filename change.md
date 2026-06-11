# Paper Trans 项目变更日志

---

## v4.8 — 2026-06-11

### 全文翻译修复

#### 2026-06-10 两篇论文 PDF 失败修复

- **影响论文**：`2606.09967`（ABot-Earth 0.5: A Generative 3D Earth Model）和 `2606.10917`（Role-Agent: Guiding Large Language Model Agents by Dual Role Evolution）。
- **根因**：两篇均未进入 LaTeX 编译错误阶段，而是在 gpt-academic 容器写入缓存时失败；日志分别显示 `shutil.copytree(...): [Errno 28] No space left on device` 和 `os.makedirs(...): [Errno 28] No space left on device`。宿主机根分区与容器 overlay 当时均为 100%。
- **处理**：
  - 清理旧版 VS Code Server / Cursor Server 缓存、旧日志和可再生下载缓存，将根分区可用空间恢复到约 4GB；
  - 重新执行 `python3 run_repair.py --retry-pdf --mode daily --key 2026-06-10`；
  - `2606.09967` 复用已下载源码包重新翻译并生成 `data/papers/2606.09967_zh.pdf`；
  - `2606.10917` 重新下载/翻译/编译成功，生成 `data/papers/2606.10917_zh.pdf`。
- **结果**：`data/daily/2026-06-10/index.json` 中 3 篇论文全文 PDF 均恢复为 `pdf_status=ok`，两份中文 PDF 线上均返回 `HTTP/2 200`。

---

## v4.7 — 2026-06-10

### 全文翻译修复

#### 2026-06-09 论文 `2606.09828` retry-pdf 下载断流修复

- **影响论文**：`2606.09828`（Latent Spatial Memory for Video World Models）。
- **根因**：失败日志显示插件还未进入 LaTeX 编译阶段，`requests.get(arxiv /src)` 在读取源码包时触发 `ChunkedEncodingError / IncompleteRead`；由于 `retry-pdf` 在无翻译 tex 缓存时传入 `--no-cache`，gpt-academic 会忽略已有 `e-print/<arxiv_id>.tar`，每次重试都重新下载源码，容易反复失败在下载阶段。
- **修复**：`full_translate_driver.py` 在 `--no-cache` 重译前仍清理 `workfolder`、`translation` 和 `extract`，但若容器内已有有效 arXiv 源码 tar，则复用源码包，只重新执行翻译和编译。
- **文档**：`README.md` 的 retry-pdf 说明补充源码缓存复用策略。

---

## v4.6 — 2026-06-09

### 统计接入

#### zzzgry.top Umami Cloud 访问统计

- `/paper/` 主应用页面新增 Umami Cloud 统计脚本，用于匿名记录页面浏览数据；
- `/paper/view/<arxiv_id>` PDF HTML wrapper 同步注入同一脚本，避免只统计列表页而漏掉直接打开论文阅读页的访问；
- 新增合约测试，固定核心 HTML 页面和 `/view` wrapper 都必须包含 Umami 脚本。

---

## v4.5 — 2026-06-08

### Bug 修复

#### 搜索结果去重与详情页 404 修复

- **背景**：同一篇论文可能同时出现在多个索引中，例如连续 daily、weekly、monthly 或 manual；旧版搜索直接按索引条目返回结果，导致同一 arXiv ID 被显示成多张同名卡片。
- **修复**：
  - 搜索结果改为按 `arxiv_id` 去重，同一论文只展示一张卡片；
  - 搜索卡片显示索引来源提示，例如 `daily/2026-05-25、weekly/2026-W22`，说明它曾被哪些列表收录；
  - 搜索结果的详情按钮改为全局稳定路由 `/detail/<arxiv_id>`，不再依赖某个 daily/weekly/monthly 目录，也避开 nginx 对 `/paper/papers/*` 的静态文件规则；
  - 搜索 API 返回的 HTML 片段补齐 `BASE_PATH` 前缀，修复线上 `/paper/search` 注入结果后点击详情跳到站点根路径并出现 404 的问题。
- **说明**：中文 PDF 仍以 `data/papers/<arxiv_id>_zh.pdf` 为全局唯一来源；同一 arXiv ID 在多个索引中出现时，对应中文 PDF 是同源文件。

---

## v4.4 — 2026-06-06

### Web 行为回退

#### PDF 查看页恢复 HTML wrapper

- **背景**：v4.3 为提升 ChatGPT Atlas / Chromium 打开大 PDF 的速度，将 `/view/<arxiv_id>` 默认 302 到裸 PDF；但 Chromium PDF viewer 会优先采用 PDF 内部 metadata，部分论文 metadata 中的 `[Your Paper Title]` 会覆盖浏览器标签页标题。
- **修复**：
  - `/view/<arxiv_id>` 固定返回 HTML wrapper，不再默认跳转到 `/pdf/<arxiv_id>/<title>.pdf`；
  - wrapper 的 `<title>` 使用 `title_zh || title || arxiv_id`，内部 iframe 继续加载 `{BASE_PATH}/papers/<arxiv_id>_zh.pdf#view=FitH`；
  - 保留 `/pdf/<arxiv_id>/<title>.pdf` 直接 PDF 路由、中文 `filename*` 响应头、PDF Range 支持和分块发送。
- **结果**：ChatGPT Atlas / Chrome / Safari 打开 `/paper/view/2605.21573` 时标签页由 HTML 标题控制，不再显示 PDF metadata 中的占位标题。

### Web 代码整合

#### 无行为变更重构与合约测试

- `web_server.py` 新增纯 helper：
  - `paper_pdf_state()`：统一 `has_pdf` / `pdf_failed` / `pdf_status` 判断；
  - `enrich_paper_entry()`：统一 slim index 与 paper store 合并；
  - `render_paper_actions()`：统一详情、全文 PDF、arXiv、原文 PDF 链接生成；
  - `h_text()` / `h_attr()` / `js_str()`：按 HTML 文本、HTML 属性、inline JS 字符串上下文转义。
- 列表页、首页、详情页和收藏页复用上述 helper，保持按钮 href、点击语义、路由响应类型和页面结构不变。
- `load_index()` 改为 `with open(...)`，消除未关闭文件句柄的 `ResourceWarning`。
- 新增 `tests/test_web_server_contract.py`，使用 stdlib `unittest` 启动临时 HTTP server，固定核心页面、JSON API、PDF Range、`/view` wrapper、`BASE_PATH=/paper`、详情页关键链接和搜索/提交/status fetch 合约。

### 文档

- 重建 `README.md`，同步当前 paper store 架构、Web 路由契约、PDF wrapper 策略、测试命令、systemd 部署和 cron 运维建议。
- 更新 `plan.md` 为当前项目路线图，移除早期 `main.py` / `fetch_weekly.py` 时代的过时说明。

---

## v4.3 — 2026-06-06

### 性能优化

#### PDF 查看页在 ChatGPT Atlas / Chromium 浏览器中的加载优化

- **状态**：该默认跳转策略已在 v4.4 回退；仍保留 `/pdf/<arxiv_id>/<中文标题>.pdf`、PDF Range 和线程型 HTTP server 等底层优化。
- **背景**：`/paper/view/2605.21573` 在 ChatGPT Atlas 中打开明显慢于 Safari；该论文中文 PDF 约 26.5 MB，Safari 对嵌入式 PDF 处理较快，但 Atlas/Chromium 在 `<embed>` 嵌入插件里加载大 PDF 时容易出现首屏等待。
- **修复**：
  - `/view/<arxiv_id>` 默认对 Atlas、Chrome、Edge 等 Chromium 系浏览器返回 `302`，直接跳转到带中文文件名的顶层 PDF URL，让浏览器原生 PDF 查看器接管，减少嵌入式插件开销并保留中文标签页；
  - Safari 保留轻量 HTML 查看页，继续内嵌 PDF，维持原本较快体验；
  - 新增 `?embed=1` 参数，可强制使用内嵌查看页，便于排查和回退。
- **静态文件改进**：
  - 新增 `/pdf/<arxiv_id>/<中文标题>.pdf` 动态路由，PDF 响应头携带 `filename*` 中文文件名；
  - `web_server.py` 新增 `/papers/<file>` 静态文件回退路由；
  - Python 回退路径支持 PDF `Range` 请求，返回 `206 Partial Content`，并改为分块流式发送，避免一次性把大 PDF 读入内存；
  - Web 服务切换为线程型 HTTP server，避免大 PDF 传输阻塞其他页面请求；
  - PDF 响应增加 `Accept-Ranges: bytes` 与缓存头，提升非 nginx 直出场景下的大文件加载效率。

---

## v4.2 — 2026-06-05

### Bug 修复

#### 2026-06-04 每日论文 PDF 编译失败修复

- **影响论文**：`2606.02800`、`2606.02060`。
- **根因 1**：`2606.02800` 的合并 tex 正文中带入了子文件级 `\endinput`，导致 TeX 提前停止读取，文件末尾的 `\end{document}` 未被执行，报 `Emergency stop` / `no legal \end found`。
- **处理 1**：注释正文中的 `\endinput` 后重编译成功，生成 `data/papers/2606.02800_zh.pdf`。
- **根因 2**：`2606.02060` 的 `trajcase` / `tcolorbox` 块内存在跨盒子的 `{\small ...}` 分组和漏闭合 `\endgroup`；同时自定义 `newtcblisting` 环境 `promptbox` 内容被翻译成中文，触发 `listings` UTF-8 编译错误。
- **处理 2**：移除易损字号分组、补齐盒子内 `\endgroup`，并将自定义 listing 环境从原文还原后重编译成功，生成 `data/papers/2606.02060_zh.pdf`。
- **结果**：`data/daily/2026-06-04/index.json` 中 3 篇论文全文 PDF 均已恢复为 `pdf_status=ok`。

### 稳定性改进

#### 编译失败 fallback 自动修补增强

- `full_translate_driver.py` 新增正文 `\endinput` 自动注释，避免合并子文件时提前终止 TeX 输入。
- 自动发现并还原 `\newtcblisting` / `\DeclareTCBListing` 定义的自定义代码环境，覆盖 `promptbox` 等非标准 verbatim 场景。
- 对 `trajcase` 的 `{\small ...}` 跨盒子分组进行保守修复，并自动补齐自定义 `tcolorbox` 块内明显缺失的 `\endgroup`。

### 维护约定

- 以后涉及代码、产物或流程的更新，需要同步更新相关 `.md` 文档。
- 完成可提交更新后，需要 commit 并 push 到 Git 远端。

---

## v4.1 — 2026-05-26

### Bug 修复

#### W21 PDF 翻译失败修复：磁盘不足与 `bxcoloremoji` 缺包

- **影响论文**：`2026-W21` 中原本标记失败的 `2605.18747`、`2605.20025`、`2605.21467`、`2605.22109`。
- **根因 1**：首次 W21 任务运行时根分区接近满盘，容器内创建 `/gpt/gpt_log/arxiv_cache/<arxiv_id>` 失败，日志报 `OSError: [Errno 28] No space left on device`，任务尚未进入源码解压和 LaTeX 编译阶段。
- **处理 1**：清理 npm/pnpm/VS Code 扩展安装包缓存，根分区可用空间从约 1GB 恢复到约 4GB；重新执行 `run_repair.py --retry-pdf --mode weekly --key 2026-W21` 后，`2605.22109`、`2605.21467` 状态同步为已有 PDF，`2605.20025` 重新翻译并生成 PDF。
- **根因 2**：`2605.18747` 进入真实编译后失败，`merge_translate_zh.log` 显示 `LaTeX Error: File 'bxcoloremoji.sty' not found`，属于容器 TeX 环境缺少 emoji LaTeX 包。
- **处理 2**：按 `scripts/setup_docker_env.sh` 既有安装逻辑在容器内安装 `bxcoloremoji` 到 `TEXMFLOCAL`，执行 `mktexlsr` 后 `kpsewhich bxcoloremoji.sty` 可解析；随后复用 `merge_translate_zh.tex` 仅重跑编译，成功生成 `2605.18747_zh.pdf`。
- **结果**：`data/weekly/2026-W21/index.json` 中 10 篇论文均为 `pdf_status=ok`，`/paper/papers/2605.18747_zh.pdf` 与 `/paper/papers/2605.20025_zh.pdf` 线上返回 `HTTP/2 200`。

### 稳定性改进

#### 手动提交元数据链路补强

- **背景**：手动提交页面曾出现空卡片，原因是 `web_server.py` 已抓到 arXiv API 元数据，但 `translate_arxiv.py` 又重复抓取 arXiv 页面；后续空结果可能覆盖已有标题/摘要。
- **修复**：
  - 手动提交时将已抓取的 `prefetched_meta` 传入 `translate_and_save()`，避免重复请求 arXiv；
  - arXiv 元数据请求复用 `fetch_hf.py` 风格的代理优先、失败切直连、指数退避重试；
  - 合并 paper store 与 slim index 时，空字段不再覆盖已有完整元数据。

---

## v4.0 — 2026-05-05

### Bug 修复

#### PDF 翻译失败修复：系统级 TeX 文件 `\input{glyphtounicode}` 导致崩溃

- **根因**：部分论文的 `main.tex` 包含 `\input{glyphtounicode}`（TeX Live 系统文件，不在论文源码包中）。gpt-academic 的 `merge_tex_files_` 函数扫描所有 `\input{}` 引用并要求文件存在于项目目录，找不到时直接抛 `RuntimeError`，导致翻译在 7 秒内崩溃、阶段被标为 `translate/unknown`。
- **修复**：在 `full_translate_driver.py` 给 `merge_tex_files_` 打 patch：遇到不在项目目录中的 `\input{}` 文件，先用 `kpsewhich` 查询是否为系统级 TeX 文件，是则输出警告注释并跳过（`% [system file skipped by driver patch: ...]`），不是才真正报错。
- **影响论文**：`2604.27221`（以及今后所有引用 `glyphtounicode`、`hyperref` 等系统文件的论文）。

#### retry-pdf 新增宿主机 tex 备份恢复路径

- **背景**：容器重启后容器内的翻译缓存（`merge_translate_zh.tex`）会丢失，retry-pdf 只检查容器内是否有缓存，丢失后会触发完整的 GPT 重翻（20-40 分钟），浪费 API 调用。
- **修复**：`run_papers.py` 的 `retry_pdf` 新增宿主机备份路径：
  1. 先检查容器内缓存（同原逻辑）；
  2. 若容器内无缓存，再检查宿主机 `data/tex_backup/<arxiv_id>_merge_translate_zh.tex`；
  3. 有备份则通过 `_restore_tex_to_container` 恢复后只重跑编译；
  4. 两处均无缓存才触发全量重新翻译。

### 功能增强

#### PDF 翻译失败诊断日志大幅升级（`logs/pdf_errors/<arxiv_id>.log`）

旧版日志仅有错误类型和简短建议，新版全面展示失败细节：

**translate 阶段（GPT 翻译崩溃）**
- 新增 `【插件完整报错 / Traceback】` 节：完整还原 gpt-academic 插件抛出的异常，包含调用栈的每一个 `File`/行号/函数名/报错信息，不再截断至 180 字符。
- 错误类型识别细化：`missing_input_file:<filename>`（如 `glyphtounicode`）、`runtime_error`、`plugin_exception`，取代旧版统一的 `unknown`。
- 错误无匹配时给出"无插件报错消息，可能是网络/API 超时"的具体提示。

**compile 阶段（LaTeX 编译失败）**
- 每处 LaTeX 错误上下文从 **5 行**扩展到 **前 2 行 + 后 12 行**，最多采集 **10 处**错误（原为 8 处 / 5 行）。
- 新增 `【编译日志尾部（最后 60 行）】` 节：包含 Emergency stop / Fatal 位置，附容器内日志的完整路径。

**通用**
- 新增 `【驱动运行记录（[driver] 输出）】` 节：列出完整的驱动执行流水（从代理初始化到最终 RESULT），可直接看到翻译在哪一步挂掉。
- 关键驱动消息（含 `插件调用出错`）打印时去掉 180 字符截断，宿主机日志也完整捕获。
- 模块级 `_plugin_msgs_full` 列表收集所有 chatbot 消息原文（不截断），供 `diagnose_failure` 分析。

---

## v3.9 — 2026-04-30

### 新增功能

#### PDF 翻译失败自动诊断报告（`logs/pdf_errors/<arxiv_id>.log`）

- **背景**：PDF 翻译失败时需要手动进容器查日志才能定位原因，效率低。
- **实现**：
  - `full_translate_driver.py` 新增 `diagnose_failure()`：翻译失败时自动分析 LaTeX 编译日志，识别错误类型，输出 `PDF_DIAGNOSIS:<json>` 到 stdout。
  - `translate_full.py` 新增 `_write_error_log()`：捕获诊断 JSON，写入宿主机 `logs/pdf_errors/<arxiv_id>.log`，包含错误类型、修复建议、LaTeX 错误摘要、手动修复命令。
- **可识别的错误类型**：

| 类型 | 触发条件 | 修复建议 |
|---|---|---|
| `normalsize_recursion` | `\normalsize` 自引用递归 | 用 `\let\normalsizesaved` 替换 |
| `tcblisting_translated` | pgfkeys Error + Missing $ | 还原 verbatim 类环境块 |
| `missing_package:X` | File 'X.sty' not found | tlmgr install 或创建 stub |
| `missing_bracket` | twocolumn / begin{document} ended by | 在对应位置补回 `]` |
| `group_mismatch` | Missing } / Emergency stop | GPT 破坏嵌套结构 |
| `undefined_command:\X` | Undefined control sequence | 自定义宏丢失 |

#### retry-pdf 优先复用已有 GPT 翻译缓存

- **背景**：PDF 翻译失败多为编译阶段问题，GPT 翻译（20-40 分钟）已经完成，retry 时不应重新翻译。
- **实现**：
  - `full_translate_driver.py` 新增 `--keep-translation` 标志：若 `merge_translate_zh.tex` 已存在，跳过 GPT 翻译（不清缓存），直接以 `no_cache=False` 调用插件重跑编译。
  - `translate_full.py` 透传 `keep_translation` 参数到容器命令。
  - `run_papers.retry_pdf`：重试前通过 `docker exec test` 检测容器内是否已有翻译文件，有则用 `keep_translation=True`，否则用 `no_cache=True` 全量重译。

#### 编译失败时自动修补 verbatim 类环境后重编译

- **背景**：GPT 会错误翻译 `tcblisting`/`lstlisting`/`verbatim`/`minted` 等代码块内容，导致特殊字符（`{}`、`**`、`<>`、`_`）破坏 LaTeX 编译。
- **实现**：`full_translate_driver.py` 新增 `patch_verbatim_envs()` 和 `patch_and_recompile()`：
  - `run_translation` 失败但 `merge_translate_zh.tex` 存在时，自动将 verbatim 类环境块从原始 `merge.tex` 还原。
  - 还原后直接调用 `pdflatex` 重新编译，成功则将 PDF 复制到 translation 目录输出。
  - 无需任何人工介入，全自动完成。

### Bug 修复

#### 2604.25914 PDF 翻译失败（tcblisting 内容被翻译）

- **根因**：论文包含 11 个 `tcblisting` 环境（代码块/prompt 模板），GPT 将其中 9 个的内容翻译成中文，导致 `{work_dir}`、`**bold**`、`<value>` 等特殊字符触发 `pgfkeys Error` 和大量 `Missing $ inserted`，编译失败。
- **修复**：将 9 个被翻译的 `tcblisting` 块还原为原始内容，pdflatex 编译成功，生成 82 页 16 MB PDF。
- **自动化**：上述 `patch_and_recompile` fallback 机制已内置，同类问题今后自动解决。

---

## v3.8 — 2026-04-29

### 系统稳定性

#### OOM 导致服务器意外重启修复

- **根因**：服务器仅 1.8GB RAM + 1GB swap，`xdvipdfmx`（LaTeX 编译，500–800 MB RSS）与 `node`（Cursor Server，300–400 MB）并发时触发 OOM killer，导致 SSH 断连和服务中断。
- **扩充 swap**：新增 `/www/swap2`（2 GB swapfile），总 swap 从 1 GB 扩展至 3 GB，写入 `/etc/fstab` 开机自动挂载。
- **Docker 内存限制**：以 `--memory=1400m --memory-swap=3000m` 重建 `gpt-academic-latex` 容器，将 OOM 风险限制在容器内部，不再扩散至宿主机。
- **vm.swappiness 调整**：`/etc/sysctl.conf` 中将 `vm.swappiness` 从 10 调整为 40，使内核更积极地使用 swap。
- **OOM score 保护**：新增 `scripts/set_oom_protection.sh` 及 `systemd` 服务 `oom-protection.service`，开机后将 `nginx`、`web_server.py`、`BT-Panel/BT-Task` 的 `oom_score_adj` 设为 `-200`，防止被 OOM killer 误杀。

#### 磁盘清理与定期维护

- 手动释放约 3 GB 磁盘空间（pip cache、旧 Cursor server 版本、journal、rotated 日志、dnf cache、orphan PDF、nginx 大日志）。
- 新增 `scripts/weekly_cleanup.sh`：每周日 03:30 通过 cron 自动执行以下清理：
  - `pip cache purge`
  - `journalctl --vacuum-size=50M`
  - 删除 14 天前 rotated 系统日志
  - 截断超 50 MB 的 nginx 访问/错误日志
  - `dnf clean packages`
  - 清理 `/tmp` 超 7 天临时文件
  - 删除孤立的论文 PDF（不被任何 `index.json` 引用）
  - 清理旧版 Cursor server（保留当前进程使用版本）
  - 释放系统 PageCache

### Bug 修复

#### full_translate_driver.py：禁用自动重试

- 将 `max_retries` 从 `2` 改为 `0`，每次翻译任务只执行一次，不在容器内自动重试（失败由外层 `retry-pdf` cron 处理）。

#### 代理回退逻辑修复（fetch_hf.py / translate_arxiv.py）

- **根因**：代理失败时将 `proxies` 设为 `None`，但 `requests` 在 `None` 时会继续读取环境变量中的代理，导致无法真正切换为直连。
- **修复**：改为 `{"http": "", "https": ""}` 显式清空代理，确保直连生效。

#### PDF 翻译失败修复：2604.22748（fontawesome6 缺失）

- **根因**：`fontawesome6.sty` 在 TeX Live 2023 容器中不可用，`tlmgr` 无法从 2026 年源安装。
- **修复**：在容器 `texmf` 目录（`/home/gptuser/texmf/tex/latex/fontawesome6/`）创建 stub 包 `fontawesome6.sty` 和 `fontawesome6-generic.sty`，将其命令转发给 `fontawesome5`，编译成功。

#### PDF 翻译失败修复：2604.24300（两层 LaTeX 结构错误）

- **根因一**：原论文 preamble 使用 `\expandafter\def\expandafter\normalsize\expandafter{\normalsize ...}` 将 `\normalsize` 重定义为调用自身，触发 TeX input stack overflow（10000 层），导致 gpt-academic 的所有 10 次 fix 尝试全部止步于第 120 行（`\begin{document}`）。
  - **修复**：替换为 `\let\normalsizesaved\normalsize` + `\def\normalsize{\normalsizesaved ...}`，打断循环引用。
- **根因二**：gpt-academic GPT 翻译阶段将 `merge.tex` 中关闭 `\twocolumn[...]` 参数的 `]` 丢失，导致整个文档 body（包括所有 `\begin{itemize}`）都在 box 上下文内，引发大量 "Not in outer par mode" 错误，最终在 `\end{document}` 处因 group 不匹配触发 Emergency stop。
  - **修复**：在翻译后的 tex 文件中正确位置补回 `]`，编译通过，生成 25 页 40 MB PDF。

---

## v3.7 — 2026-03-06

### Bug 修复

#### 手动提交论文的全文 PDF 404 问题

- **根因**：`_do_submit_job` 将翻译好的 PDF 只存入 `data/manual/DATE/papers/`，未同步到 `data/papers/`（PAPER_STORE_DIR）。`_paper_pdf_exists()` 检查 paper store 返回 False，导致卡片生成 URL `/manual/KEY/papers/ARXIV_ID_zh.pdf`，该路由不存在故返回 404。
- **修复**：全文翻译成功后，用 `shutil.copy2` 将 PDF 同步复制到 `PAPER_STORE_DIR`（`data/papers/`），与 daily/weekly/monthly 流程保持一致。此后 `_paper_pdf_exists()` 正确返回 True，PDF 通过统一的 `/papers/ARXIV_ID_zh.pdf` 路由提供服务，不再 404。

---

## v3.6 — 2026-02-25

### UI 优化

#### 页脚增加个人网站链接

- 底部 footer 新增「关于作者」链接，指向 `https://zhaojingqian.top/about`，与 ICP 备案号并排显示

#### 标签页 favicon

- 使用 SVG emoji 内嵌方式添加 📰 favicon，无需额外图片文件，所有页面标签页均显示报纸图标

### Bug 修复

#### web_server.py 缩进错误修复

- 修复 `_upsert_manual_index()` 中 `for…else` 语句的缩进错误（`else` 误缩进为 16 空格）
- 修复 `_do_submit_job()` 中 `if…else` 及 `try…except` 的缩进错误（导致服务启动失败 / 502）

---

## 2026-02-20 (v3.1) - 收藏功能

### 新增功能

**论文收藏系统**

- 每张论文卡片右上角增加 ☆/★ 收藏按钮（JS 初始化后自动显示当前状态）
- 点击收藏按钮弹出模态框，支持：
  - 将论文加入/移出任意收藏列表（复选）
  - 一键新建收藏列表并同时收藏当前论文
- 新增独立收藏页 `/bookmarks`：
  - 卡片式展示所有收藏列表（名称、论文数、创建日期）
  - 支持新建列表、重命名、删除列表
- 列表详情页 `/bookmarks/{list_id}`：
  - 展示该列表所有论文卡片（含全文 PDF 按钮）
  - 支持"移出列表"、"移动到其他列表"下拉操作

**后端 API `POST /api/bookmarks`**（JSON body）


| action        | 说明               |
| ------------- | ---------------- |
| `toggle`      | 切换某论文在某列表中的收藏状态  |
| `create_list` | 新建列表（可同时收藏一篇论文）  |
| `delete_list` | 删除列表             |
| `rename_list` | 重命名列表            |
| `remove`      | 从列表移除论文          |
| `move`        | 将论文从一个列表移动到另一个列表 |


- `GET /api/bookmarks`：返回完整收藏 JSON
- 收藏数据持久化到 `data/bookmarks.json`，线程安全写入（`threading.Lock`）

### 导航栏更新

- 顶部 Tab 新增「⭐ 收藏」入口，与每日/每周/每月并列

### 代码重构

- 统一 `run_daily.py`/`run_weekly.py`/`run_monthly.py` 入口格式（--no-full opt-out）
- 删除旧版 `main.py`（已被 `run_weekly.py` 取代）
- 修复 `translate_full.py` Python 3.6 兼容性（`capture_output` → `stdout/stderr=PIPE`）
- 修复 web_server.py 中 PDF 文件 404（arXiv ID 含 "." 的路由判断错误）
- 修复论文详情页 404（相同路由 bug）
- 修复 crontab：去掉 `--full` 参数（现默认全文翻译）

---

## 2026-02-19 (v3.0) - 多维度内容 + 框架重构 + Web UI 全面升级

### 新增功能

**每日 Top 3（Daily）**

- 新增 `fetch_hf.py`：统一抓取器，支持 daily/weekly/monthly 三种模式
- 新增 `run_daily.py`：每日抓取 Top 3 论文并翻译摘要
- 数据存储路径：`data/daily/YYYY-MM-DD/`
- 定时：每天 23:00（cron）

**每月 Top 10（Monthly）**

- 新增 `run_monthly.py`：每月抓取 Top 10 论文并翻译摘要
- 数据存储路径：`data/monthly/YYYY-MM/`
- 定时：每月 28 日凌晨 2:00（cron）

### 框架重构

- 新增 `run_papers.py`：通用 runner，daily/weekly/monthly 共用同一套逻辑
- 重构 `main.py`（weekly）：改用 `run_papers.run()` 调用，消除重复代码
- 旧 `weekly/` 目录数据自动迁移到 `data/weekly/`，保持向后兼容
- 统一数据目录：`data/<mode>/<key>/index.json` + `data/<mode>/<key>/papers/`

### Web UI 重构

- 全新首页：汇总展示最新每日/每周/每月内容，顶部 Tab 导航
- 新增 `/daily`、`/monthly` 路径；旧 `/weekly` 路径保持兼容
- 统计栏：已抓天数 / 周数 / 月数
- 卡片支持 upvotes（热度）显示
- 路由统一：`/<mode>/<key>/papers/<id>` 详情页

### 全文翻译修复（v2.3）

- `full_translate_driver.py`：`compile_latex_with_timeout` monkey-patch，改用进程组 kill（`os.killpg`），彻底解决 pdflatex 变孤儿进程问题
- pdflatex 超时从 90s 调整为 300s，适配大型论文
- PDF 检测逻辑收紧：只认 `translation/translate_zh.pdf` 和 workfolder 根目录 PDF（> 50KB），不误判图片 PDF
- 清除 `translate_full.py` 中的 ZIP/TEX 兜底逻辑，失败即重试（最多 3 次）
- `web_server.py` 移除 ZIP 按钮
- `main.py` 移除 zip_zh 记录

### 定时任务

```cron
# 每天 23:00 — daily top 3
0 23 * * * python3 run_daily.py

# 每周日 02:00 — weekly top 10（含全文翻译）
0 2 * * 0  python3 main.py --full

# 每月 28 日 02:00 — monthly top 10
0 2 28 * * python3 run_monthly.py
```

### 测试结果

- daily 2026-02-19：3篇，成功=3 ✅
- monthly 2026-02：10篇，成功=10 ✅
- weekly 2026-W08：10篇，已存在跳过 ✅
- Web UI 三条路由全部正常 ✅

---

## 2026-02-19 (v2.0) - 全面重构与功能完善

### 背景

在 v1.0 中，尝试使用 void-terminal 和 Docker exec 方式翻译存在多个问题：

- Python 3.6 不兼容 void-terminal
- Docker exec 执行 gpt-academic 插件复杂且不稳定
- 路径配置混乱（旧路径 `/root/.openclaw/` 不存在）
- Web 服务器无法正常响应

### v2.0 架构决策

**翻译方案**: 直接读取 gpt-academic 的 `config_private.py` 获取 API Key，
通过 OpenAI 兼容接口调用 LLM 翻译，生成 HTML 双语页面。  
**优势**: 稳定、无容器依赖、可生成精美的 HTML 展示页面

---

### ✅ 修复：路径问题

- **旧**: 所有脚本引用 `/root/.openclaw/workspace/paper-trans/`（不存在）
- **新**: 统一使用 `/root/workspace/paper-trans/`（实际位置）
- 涉及文件: `main.py`, `web_server.py`, `translate_arxiv.py`, systemd 服务

### ✅ 重写：`fetch_weekly.py`

- 支持代理自动降级（Clash 不可用时直连重试）
- 改进 arxiv ID 提取正则，支持带标题的解析
- 支持指定周 (`python3 fetch_weekly.py 2026-W08`)
- 新增 `fetch_arxiv_metadata()` 辅助函数

### ✅ 重写：`translate_arxiv.py`

原 Docker exec 方式 → 直接 API 调用：

- 从 `/root/workspace/gpt-academic/config_private.py` 自动读取 API Key/模型/URL
- 调用 LLM 翻译标题、摘要，提取关键词和核心贡献总结
- 生成美观的双语 HTML 页面（深色标题、关键词标签、Tab 切换摘要）
- 支持代理降级

### ✅ 重写：`main.py`

- 使用 ISO 周格式 (`YYYY-WNN`)
- 增量处理：已翻译的论文直接复用，同时重新获取元数据补全 index
- 每篇翻译后立即保存 `index.json`（断点续传）
- 详细进度日志

### ✅ 重写：`web_server.py`

- 现代化深色主题首页，卡片式布局展示各周
- 周内论文列表：排名勋章（🥇🥈🥉）、中文标题、AI 速读、关键词标签
- 论文详情页：双语摘要 Tab 切换，直链 arXiv 和 PDF
- 使用 Python 3.10 兼容的 `SO_REUSEPORT` socket 选项
- 端口等待重试机制（最多 30 次，每次 2s）

### ✅ 更新：systemd 服务

- **旧**: `/usr/bin/python3` (Python 3.6.8)
- **新**: `/root/.pyenv/versions/3.10.13/bin/python3` (Python 3.10.13)
- 日志路径更新至 `/root/workspace/paper-trans/logs/web.log`

### ✅ 新增：cron 定时任务

```
0 2 * * 0 /root/.pyenv/versions/3.10.13/bin/python3 /root/workspace/paper-trans/main.py >> /root/workspace/paper-trans/logs/cron.log 2>&1
```

- 每周日凌晨 2:00 自动执行
- 日志保存至 `logs/cron.log`

### ✅ 功能验证：2026-W08 全量翻译


| 序号  | arXiv ID   | 中文标题                                       | 状态  |
| --- | ---------- | ------------------------------------------ | --- |
| 1   | 2602.10388 | 少即是多：在大语言模型特征空间中合成多样化数据                    | ✅   |
| 2   | 2602.12783 | SQuTR：在声学噪声下语音查询到文本检索的鲁棒性基准                | ✅   |
| 3   | 2602.12705 | MedXIAOHE：构建医疗多模态大模型的全面方案                  | ✅   |
| 4   | 2602.11858 | 无需缩放的缩放：面向细粒度多模态感知的区域到图像蒸馏方法               | ✅   |
| 5   | 2602.14111 | 稀疏自编码器的合理性检验：稀疏自编码器真的优于随机基线吗？              | ✅   |
| 6   | 2602.13949 | 体验式强化学习                                    | ✅   |
| 7   | 2602.08683 | OneVision-Encoder：以编解码器对齐的稀疏性作为多模态智能的基础原则  | ✅   |
| 8   | 2602.12670 | SkillsBench：评估智能体技能在多样化任务中的表现              | ✅   |
| 9   | 2602.10809 | DeepImageSearch：面向视觉历史的上下文感知图像检索多模态智能体基准测试 | ✅   |
| 10  | 2602.15763 | GLM-5：从氛围编码到自主工程                           | ✅   |


**结果**: 10/10 成功，耗时约 2 分钟

### 当前服务状态

- Web 服务: ✅ 运行中 (systemd: paper-trans-web)
- 访问地址: [http://51.79.130.17:18080](http://51.79.130.17:18080)
- 定时任务: ✅ 已配置 (cron: 每周日凌晨 2 点)

---

## 2026-02-19 (v1.0) - 初始构建

### Phase 1: 环境探索

- 尝试 void-terminal pip 安装 → Python 3.6 不兼容
- 尝试 pyenv 安装 Python 3.10 → 成功
- void-terminal pip 安装超时 → 放弃

### Phase 2: 初始脚本

- 创建 `fetch_weekly.py` ✅
- 创建 `translate_arxiv.py`（Docker exec 方式）⚠️ 未验证
- 创建 `main.py` ✅
- 创建 `web_server.py` ✅

### Phase 3: 服务配置

- systemd 服务 (使用错误路径) ❌
- 端口 18080 配置 ✅

### 已知问题

- 路径引用 `/root/.openclaw/` 不存在
- Web 服务器无法响应
- API Key 未集成

---

## 2026-02-19 (v2.1) - 全文翻译功能

### 新增功能：全文 LaTeX → 中文 PDF 翻译

**背景**：用户要求参考 [void-terminal issue #5](https://github.com/binary-husky/void-terminal/issues/5) 调用 gpt-academic 的
`Latex翻译中文并重新编译PDF` 插件进行全文翻译。

### 实现方案

```
void-terminal API 风格
    ↓
gpt-academic Docker 容器 (已有 Python 3.12 + pdflatex + API Key)
    ↓ (docker exec full_translate_driver.py)
arxiv LaTeX 源码下载 → LLM 分段翻译 → pdflatex 重编译
    ↓ (docker cp)
translate_zh.pdf → 本地 weekly/YYYY-WNN/papers/arxiv_id_zh.pdf
```

### 新增文件


| 文件                         | 功能                                |
| -------------------------- | --------------------------------- |
| `full_translate_driver.py` | 在容器内运行的驱动脚本                       |
| `translate_full.py`        | 容器外的调用封装（docker exec + docker cp） |


### 关键技术细节

1. **容器网络**: gpt-academic 容器使用 `--network=host` 模式，
  故在容器内访问宿主机 Clash 代理地址为 `http://127.0.0.1:7890`
2. **代理注入**:
  - monkey-patch `shared_utils.config_loader.read_single_conf_with_lru_cache`
  - 覆写 `requests.Session` 默认代理
  - 设置 `HTTP_PROXY` / `HTTPS_PROXY` 环境变量
3. **Generator 格式**: `update_ui` yield 格式为 4-tuple
  `(cookies_dict, chatbot_or_gradio_update, json_history, msg)`
4. **输出位置**: `/gpt/gpt_log/arxiv_cache/<arxiv_id>/translation/translate_zh.pdf`

### 测试结果

- 论文 `2602.12783` (MedXIAOHE) 全文翻译成功：
  - 258 步 generator 迭代
  - LaTeX 下载 → LLM 翻译 → pdflatex 编译均成功
  - 输出 PDF: 3.8MB
  - 耗时: ~15 分钟

### Web 界面更新

- 周内论文列表新增"📄 全文中文PDF"绿色按钮
- 论文详情页显示全文PDF下载链接
- web_server.py 支持 `.pdf` / `.zip` / `.tex` 静态文件下载

### main.py 更新

```bash
# 普通摘要翻译
python3 main.py 2026-W08

# 摘要 + 全文翻译
python3 main.py 2026-W08 --full
```

### Cron 任务更新

```
0 2 * * 0 /root/.pyenv/versions/3.10.13/bin/python3 /root/workspace/paper-trans/main.py --full >> /root/workspace/paper-trans/logs/cron.log 2>&1
```

---

## 2026-02-19 (v2.2) - 文档完善与 Web UI 优化

### 新增：README.md

- 完整项目说明，含快速开始、架构图、配置说明、使用方法
- 监控命令速查

### 更新：plan.md

- 补充 Phase 4（全文翻译）和 Phase 5（文档/UI 优化）进度
- 补充全量翻译使用方法
- 新增"可选优化"列表

### 优化：Web UI (web_server.py)

- 首页：新增统计栏（总周数、总论文数、已有 PDF 数）
- 首页：改进卡片布局，显示翻译进度徽章
- 周列表页：论文卡片增加渐变标题、更清晰的按钮组
- 论文详情页：增加 PDF 预览区域（直接嵌入 iframe），改进元数据展示
- 全局：新增 loading 动画、平滑滚动

### 批量全文翻译：2026-W08（共 10 篇）


| 序号  | arXiv ID   | 状态   | 耗时     |
| --- | ---------- | ---- | ------ |
| 1   | 2602.10388 | 翻译中… | -      |
| 2   | 2602.12783 | ✅ 已有 | ~15min |
| 3   | 2602.12705 | 翻译中… | -      |
| 4   | 2602.11858 | 翻译中… | -      |
| 5   | 2602.14111 | 翻译中… | -      |
| 6   | 2602.13949 | 翻译中… | -      |
| 7   | 2602.08683 | 翻译中… | -      |
| 8   | 2602.12670 | 翻译中… | -      |
| 9   | 2602.10809 | 翻译中… | -      |
| 10  | 2602.15763 | 翻译中… | -      |


> 后续更新最终统计结果。

---

## 2026-02-19 (v2.2) - 文档完善与 Web UI 优化

### 新增：README.md

- 完整项目说明，含快速开始、架构图、配置说明、使用方法
- 监控命令速查

### 更新：plan.md

- 补充 Phase 4（全文翻译）和 Phase 5（文档/UI 优化）进度
- 补充全量翻译使用方法、已知限制及可选优化列表

### 优化：Web UI (web_server.py)

- 首页：新增统计栏（总周数、总论文数、已有全文 PDF 数）
- 首页：卡片显示翻译完成度进度条
- 周列表页：论文卡片美化，更清晰按钮组
- 论文详情页：PDF 下载区域更突出，改进元数据展示
- 全局：响应式优化，渐变配色，平滑动画

### 批量全文翻译：2026-W08（共 10 篇）

- 已有：2602.12783 (已完成)
- 批量启动：2602.10388 等其余 9 篇（后台执行，每篇约 15-30 min）
- 日志：`logs/full_translate_2026-W08.log`

---

## 2026-02-19 (v2.2.1) - 全文翻译 Bug 修复

### 问题诊断

批量全量翻译失败（每篇 7 秒即退出），逐步排查：

1. **路径问题**：`translate_full.py` 解析 RESULT 时将错误消息也当成文件路径加 `/gpt/` 前缀
  - 修复：只对 kind in ("pdf","zip","tex") 才做路径补全
2. **代理配置问题**：gpt-academic 使用 `GPT_ACADEMIC_proxies` / `GPT_ACADEMIC_USE_PROXY` 环境变量
  而不是标准 `HTTP_PROXY`；驱动脚本未设置这些变量
  - 修复：在 `full_translate_driver.py` 中添加 `os.environ["GPT_ACADEMIC_USE_PROXY"] = "True"` 
  等语句
3. **IP 地址错误**：容器使用 `--network=host` 模式，代理应为 `127.0.0.1:7890`
  而非之前的 `172.17.0.1:7890`（bridge 模式的宿主机 IP）
  - 修复：将默认 HOST_PROXY 改为 `http://127.0.0.1:7890`
4. **LRU 缓存问题**：`read_single_conf_with_lru_cache` 带 LRU 缓存，monkey-patch 前需要清除
  - 修复：在替换函数之前调用 `cache_clear()`

### 修复后验证

- 论文 `2602.10388` 下载成功，LaTeX 解压成功，多线程翻译正在执行

---

## v3.2 — 2026-02-20

### 新功能

#### 手动添加论文（`/submit`）

- 新增 `➕ 手动` 导航 Tab，访问 `/submit` 页面
- 输入任意 arXiv ID，后台自动完成：从 arXiv API 抓取元数据 → 摘要翻译 → 全文 PDF 翻译
- 论文以卡片形式展示，与每日/每周/每月页面风格一致
- 支持任务队列（串行处理），实时状态追踪（排队中 / 获取元数据 / 翻译摘要 / 翻译全文 / 完成 / 失败）
- 有进行中任务时页面每 8 秒自动刷新；失败任务支持一键重试
- 论文数据存入 `data/manual/YYYY-MM-DD/`，与其他模式结构一致

#### 品牌更名

- 项目名称从 **Paper Trans** 改为 **Paper Hub**，标题、页面 title、服务 log 同步更新

#### Web 导航优化

- 左上角品牌标题改为可点击链接，点击返回主页 `/`
- 「每日」Tab 改为跳转 `/daily` 列表页（与每周/每月一致）

#### 磁盘维护

- 新增 `scripts/cleanup_docker_cache.sh`：每月 1 日 03:00 自动清理容器内 `gpt_log` 翻译缓存
- 单次手动清理释放约 5.5 GB 磁盘空间（94% → 79%）

#### 部署架构

- `web_server.py` 支持 `BASE_PATH` 环境变量，nginx strip-prefix 模式下路径前缀透明处理
- nginx `zzzgry.top` 配置：`/paper/` 路径反代到 `127.0.0.1:18080`，不暴露端口
- `BIND_HOST` 环境变量控制监听地址（`127.0.0.1` 或 `0.0.0.0`）

---

## v3.2 — 2026-02-20

### 新功能

#### 手动添加论文（/submit）

- 新增「➕ 手动」导航 Tab，访问 /submit 页面
- 输入任意 arXiv ID，后台自动完成：arXiv API 抓元数据 → 摘要翻译 → 全文 PDF 翻译
- 论文以卡片形式展示，与每日/每周/每月页面风格一致
- 支持任务队列（串行处理），实时状态追踪（排队中/获取元数据/翻译摘要/翻译全文/完成/失败）
- 有进行中任务时页面每 8 秒自动刷新；失败任务支持一键重试
- 论文数据存入 data/manual/YYYY-MM-DD/，与其他模式结构一致

#### 品牌更名

- 项目名称从 Paper Trans 改为 Paper Hub，标题、页面 title、服务 log 同步更新

#### Web 导航优化

- 左上角品牌标题改为可点击链接，点击返回主页
- 「每日」Tab 跳转 /daily 列表页（与每周/每月一致）

#### 磁盘维护

- 新增 scripts/cleanup_docker_cache.sh：每月 1 日 03:00 自动清理容器内 gpt_log 翻译缓存
- 单次手动清理释放约 5.5 GB 磁盘空间（磁盘使用率 94% -> 79%）

#### 部署架构

- web_server.py 支持 BASE_PATH 环境变量，nginx strip-prefix 路径前缀透明处理
- nginx zzzgry.top 配置：/paper/ 路径反代到 127.0.0.1:18080，不暴露端口号
- BIND_HOST 环境变量控制监听地址（127.0.0.1 仅本机 / 0.0.0.0 公网）

## v3.3 — 2026-02-20

### 新功能

#### 论文删除功能

- 所有卡片（每日/每周/每月/手动）右下角新增「🗑️」删除按钮
- 删除操作同时清除：本地 HTML 及 PDF 文件、index.json 条目、收藏夹中对应记录、manual 模式的 jobs.json 记录
- 点击前弹窗确认，删除后卡片淡出消失（无需刷新页面）
- 后端 API：POST /api/paper/delete，接收 mode / key / arxiv_id

## v3.4 — 2026-02-20

### 新功能

#### 全局搜索（/search）

- 新增「🔍 搜索」导航 Tab，访问 /search 页面
- 支持中英文模糊搜索：标题、中文标题、摘要、作者、关键词、arXiv ID
- 覆盖所有模式（每日/每周/每月/手动），实时 debounce 搜索（400ms 延迟）
- 搜索结果以卡片形式展示，与其他页面完全一致
- 支持 URL 参数 /search?q=xxx 直接触发搜索
- 后端 GET /api/search?q= 接口，返回服务端渲染的卡片 HTML

#### 修复 submit 页面卡片布局

- 修复手动添加页面已完成论文卡片单列问题，改为与其他页面一致的响应式多列网格（minmax 340px）

## v3.5 — 2026-02-20

### 新功能

#### 系统状态监控页面（/status）

- 新增「📊 状态」导航 Tab
- 实时展示：磁盘使用进度条、Docker 容器翻译进程（CPU/内存/运行时长）、任务队列全览
- 支持一键「⏹ 终止」当前翻译任务
- 页面每 8 秒轮询 /api/status，有活跃任务时自动刷新
- 新增 GET /api/status、POST /api/status/kill 接口

### 修复与改进

#### server 重启后任务自动恢复

- web server 启动时调用 _recover_stuck_jobs()
- 将中断状态（queued/fetching/abstract/full_pdf）的任务自动重新入队继续执行
- 防止 server 重启导致翻译任务永久卡死

#### 每日凌晨 5 点自动重启 Docker 容器

- 新增 cron：0 5 * * * docker restart gpt-academic-latex
- 定期清除累积的僵尸 pdflatex 进程（无害但会占用进程表条目）
- 重启日志写入 logs/docker-restart.log
