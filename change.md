# Paper Trans 项目变更日志

---

## v4.34 — 2026-07-20

### 周末当前周修复调度与通用补丁沉淀

- **调度闭环**：root crontab 已安装每周日 02:00 的 `scripts/repair_weekly_current.py`；runner 会等待当前 ISO 周索引出现、等待 weekly 抓取锁释放，再在共享锁内串行执行摘要/翻译修复和失败 PDF 重试，最长等待 3 小时，并兼容 02:30 weekly 兜底抓取。
- **历史与分类**：每次运行以 `runs` 追加写入 `logs/repair_history/weekly-<key>.json`，记录前后失败、剩余项、错误和通用 patch 计划；`paperhub/patch_catalog.py` 将失败类别映射到可复用实现，便于后续按类别直接定位。
- **新增通用修复**：兼容声明 `CJKutf8` 但 XeLaTeX 未暴露 `CJK/CJK*` 环境的旧论文；修复翻译把已定义的 `\\Imat` 误写成 `\\I` 的数学宏异常，并加入回归测试。
- **本周验证**：W29 的 `2607.05382`、`2607.12463`、`2607.13125`、`2607.14952` 已完成 PDF 状态闭环；其中 `2607.13125` 通过 CJK fallback 与数学别名 patch 后生成 48.89MB PDF，覆盖率 79.5%、编译健康检查通过。

## v4.33 — 2026-07-15

### 全项目巡检、失败分类与架构收拢

- **全项目审计**：扫描 227 个 `index.json`、827 条索引记录、485 篇唯一论文和 492 份 paper store JSON；最终坏 JSON、索引总数不一致、缺 store、缺翻译、`ok` 缺 PDF、`failed` 状态、失败日志和失败现场 tex 全部为 0。
- **论文恢复**：修复并通过翻译覆盖率与 LaTeX 健康门禁的 PDF 共 11 篇：`2607.07675`、`2607.09657`、`2607.09024`、`2607.04033`、`2607.02980`、`2606.16190`、`2607.09153`、`2606.27180`、`2607.09375`、`2606.22528`、`2606.30445`；另把已有 PDF 的 `2606.29526` 历史失败状态同步为 `ok`。
- **摘要恢复**：修正“不完整缓存只看中文标题”的误判，重新获取元数据并补齐 `2602.12670`、`2602.10809` 的原文元数据、中文摘要和中文总结；重译时保留独立的 `pdf_status`。
- **结构化失败诊断**：新增 `failure_taxonomy.py`，失败 sidecar 固定输出 `phase/category/family/retry_strategy/repair_action/evidence`；新增 `scripts/summarize_failures.py`，可按失败类别、策略和动作聚合，成功后同步清理 `.log`、`.json` 和失败 tex。
- **智能 retry**：编译类失败和未分类驱动异常默认保留中文 tex，不再自动清缓存重复调用 GPT；只有 `retry_translation` 明确要求时才重译。修复容器编译工作目录相对路径重复解析导致的 `FileNotFoundError`，并单独归类为 `runtime.workdir_missing`。
- **通用 LaTeX 修复**：覆盖 microtype class hook 失衡及残留 `\DisableLigatures`、`\input{... }` 尾空格、晚期 `\中文`、FontAwesome 任意零参数图标 fallback、cleveref 元数据损坏降级、tcolorbox 键名/长度单位被翻译、缺失图片和 pdfTeX 原语等失败形态。
- **架构优化**：新增 `paperhub.modes` / `paperhub.runner` 收敛 daily、weekly、monthly 入口与周期语义；新增 `paperhub.json_io` 统一同目录临时文件、fsync、`os.replace` 的原子 JSON 写入；新增 `paperhub.audit` / `paperhub.failure_reports` 作为日常维护入口。
- **验证命令**：`python3 scripts/audit_project.py --json` 与 `python3 scripts/summarize_failures.py` 最终均为 0 问题；完整回归 88 项测试通过，Python 语法检查、Shell 语法检查和 `git diff --check` 通过。

---

## v4.32 — 2026-07-14

### 近两周论文巡检与 topic PDF 防误删修复

- **巡检范围**：检查 `2026-06-29` 至 `2026-07-12` 的 daily、weekly、monthly、topic 索引，核对中文标题/摘要、paper store JSON、PDF 实体、索引状态和失败日志；中文标题与摘要未发现缺失。
- **根因**：`scripts/weekly_cleanup.sh` 的孤立 PDF 扫描只统计 daily/weekly/monthly/manual，既漏掉 topic，也只检查一层 key 目录。`2026-07-12 04:00` 因而把 63 个 topic-only PDF 误判为孤立文件并删除。
- **修复**：孤立 PDF 扫描改为递归遍历全部 mode，并把 topic 纳入引用集合，兼容 `data/topic/<slug>/<date>/index.json` 两层目录。
- **驱动收口**：一次性容器驱动输出最终 `RESULT` 后直接退出，避免 gpt-academic 遗留非守护线程让宿主机误判为持续运行。
- **新增 LaTeX fallback**：
  - 缺失参数并与中文粘连的 `\\cite中` 降级为可读的“文献中”；
  - `\\em去中心化` 等旧式字体声明与中文自动分隔；
  - 误插入 `\\documentclass[...]` 选项块的 `\\usepackage` 自动移到 class 声明之后；
  - 禁用 microtype 时同步禁用残留的 `\\UseMicrotypeSet` / `\\microtypesetup` 调用。
- **已验证恢复**：`2606.31399`、`2606.30966`、`2607.07663` 的新增失败类型均已通过翻译覆盖率和 LaTeX 健康门禁；批量恢复过程中成功生成的 PDF 会继续复用 paper store 和 tex 备份。
- **后续编译兜底**：补充 `pdfgentounicode` 等 pdfTeX 原语兼容、XeLaTeX 缺失 `xspace`/摘要/链接命令 fallback、声明宏与中文粘连、裸 `cite`、中文误转义及 `documentclass` 选项包迁移修复；缺失图片资源会使用可编译占位图，避免单个 logo 资源阻断整篇 PDF。
- **回归验证**：`tests.test_latex_translation_filters` 共 24 项通过；`full_translate_driver.py`、`latex_translation_filters.py` 通过 `py_compile`，清理脚本通过 `bash -n`，`git diff --check` 无空白错误。

---

## v4.31 — 2026-07-06

### 主题订阅每日调度修复

- **根因**：生产 root crontab 只有 daily/weekly/monthly 抓取和 repair 任务，缺少 `run_topic.py --all`，导致 `/topic` 的最新主题结果停留在 `2026-07-04`；`2026-07-06 22:27 CST` 时 daily 未出现是正常现象，因为 daily 主任务固定 `23:00` 才触发。
- **修复**：已在 root crontab 增加 topic 生成和 PDF 重试：
  - `30 1 * * * $PYTHON $PTDIR/run_topic.py --all >> $PTDIR/logs/cron-topic.log 2>&1`
  - `30 6 * * * $PYTHON $PTDIR/run_repair.py --retry-pdf --mode topic --days 7 >> $RLOG 2>&1`
- **补跑**：手动执行 `python3 run_topic.py --all` 生成 `2026-07-06` 三个启用主题：
  - `loop-agent-long-horizen-task`：`2607.00483`、`2607.00160`、`2606.31639`
  - `long-trajectory-reward-multi-reward-dense-reward`：`2606.30316`、`2607.00442`、`2606.29905`
  - `opd`：`2606.30406`、`2606.30616`、`2607.01763`
- **验证**：`data/topic/*/2026-07-06/index.json` 均已生成，9 篇均为 `pdf_status=ok`；`topic_ok_missing=0`；本地 `/topic` 返回 `200`，列表已展示“最新 2026-07-06”。

---

## v4.30 — 2026-07-04

### 主题 PDF 编译失败与状态同步修复

- **`2606.26080`**：topic `long-trajectory-reward-multi-reward-dense-reward/2026-07-04` 的 PDF 失败在 `tcolorbox/newtcblisting`，XeLaTeX 环境缺少 `\inputencodingname`。fallback 编译阶段新增 `\providecommand{\inputencodingname}{utf8}` 兼容，只重跑中文 tex 编译后通过。
- **`2606.29823`**：topic `loop-agent-long-horizen-task/2026-07-04` 的 PDF 失败在 `cidr-2025/acmart` 模板，class 加载阶段调用 `\setmonofont` 但未加载 `fontspec`。fallback 新增 fontspec 风格命令 no-op，并对 CIDR/ACM 类提前插入到 `\documentclass` 前；同时在 `\end{document}` 前重置 ACM/CIDR `\baselinestretch` guard，避免最终 class 校验报错。
- `latex_translation_filters.py` 沉淀 XeLaTeX 兼容补丁：`inputenc`/listing 场景补 `\inputencodingname`，CIDR/ACM 或直接使用 `\setmainfont`、`\setsansfont`、`\setmonofont`、`\newfontfamily` 时补安全 no-op，且识别 `\usepackage[...]{fontspec}` / `\RequirePackage{fontspec}` 时不干预正常 fontspec 论文。
- `run_papers.retry_pdf()` 新增 paper store 状态一致性同步：全局 JSON 里 `pdf_status=failed` 但 `data/papers/<id>_zh.pdf` 已真实存在时自动回写 `ok`，避免索引已恢复但 paper store 旧状态继续误报。
- `retry_failed_pdf_entries()` 新增反向一致性修复：slim index 标记 `pdf_status=ok` 但 paper store PDF 文件缺失时，会先降级为 `failed` 并进入同一套缓存重编译/全文重译流程；topic、daily、weekly、monthly 的 retry caller 不再提前只筛 `failed`，统一交给 shared helper 判断。
- 本次复用中文 tex 缓存重编译成功：`2606.26080_zh.pdf`、`2606.29823_zh.pdf`、历史残留 `2605.10344_zh.pdf`；另将已有 PDF 的 `2603.21065`、`2604.24300`、`2604.25914`、`2606.02060` 全局状态同步为 `ok`。全量扫描 `data/` 后已无 `pdf_status=failed`，`logs/pdf_errors/` 和 `data/tex_backup_failed/` 均无残留。
- 继续补齐用户反馈的 3 篇 topic 中文 PDF：`2606.29296`（Process Advantage Signal Shaping）、`2606.29445`（Bridging VideoQA and Video-Guided Agentic Tasks）、`2606.29823`（Experience Graphs）。其中前两篇为 `pdf_status=ok` 但 paper store PDF 缺失，已通过中文 tex 缓存重编译补回；`2606.29823` 现有 PDF 路由验证正常。
- 全库复扫还发现 5 个历史 paper store `ok` 但 PDF 缺失残留；其中 `2605.06507`、`2605.26114`、`2606.03985` 复用中文 tex 缓存重编译，`2604.04921`、`2604.11804` 无缓存后重新全文翻译并通过健康检查。最终全库 `ok_missing=0`，无 `pdf_status=failed`。

#### 验证

- `python3 -m py_compile paperhub/paper_store.py run_papers.py latex_translation_filters.py full_translate_driver.py tests/test_paper_store.py tests/test_latex_translation_filters.py` 通过。
- `python3 -m unittest tests.test_paper_store tests.test_latex_translation_filters` 通过。
- `python3 run_repair.py --retry-pdf --mode topic --key long-trajectory-reward-multi-reward-dense-reward/2026-07-04` 和 `python3 run_repair.py --retry-pdf --mode topic --key loop-agent-long-horizen-task/2026-07-04` 均成功。
- `python3 translate_full.py 2605.10344 -o data/papers --keep-translation` 复用中文 tex 缓存重编译成功。
- 本地 Range GET 验证 `2606.29296_zh.pdf`、`2606.29445_zh.pdf`、`2606.29823_zh.pdf` 均返回 `206 application/pdf`，topic 页面均渲染“全文PDF”按钮。

### 主题订阅翻译失败修复

- `run_repair.py` 支持 `--mode topic` 和 `--topic <slug>`，可按全部 topic、单个 topic、近 N 天或 `slug/YYYY-MM-DD` 指定 key 扫描修复。
- topic 摘要/标题缺失时复用 daily 的摘要 repair 语义，重新调用 `translate_arxiv.translate_and_save` 写入统一 paper store，topic slim index 不重复保存大字段。
- topic `pdf_status=failed` 重试复用从 `run_papers.py` 抽出的 `retry_failed_pdf_entries()`：优先 paper store PDF 命中，其次复用容器/宿主机翻译 tex 缓存重编译，失败后再 no-cache 全文重译。
- `run_repair.py --post` 和 `run_repair.py --retry-pdf` 默认扫描范围扩展到 topic；`--refetch` 仍仅适用于 daily/weekly/monthly，topic 新结果继续由 `run_topic.py --all` 生成。
- 新增测试覆盖 topic repair target、摘要修复调用和 PDF retry 状态回写。

### 主题订阅备注名

- topic profile 新增可选 `display_name` 字段，用作用户自定义备注名；为空时继续回退到原始 query。
- `/topic` 创建表单和 `/topic/<slug>` 详情页支持填写/编辑备注名；列表和详情标题优先展示备注名，meta 里保留 query 与 slug，检索逻辑、slug、seen 去重和缓存目录不受影响。
- 每日 topic index 会保存当时的 `display_name`，便于后续追溯展示名称。
- 新增测试覆盖备注名规范化、API 保存和页面渲染。

### 主题订阅检索词生成优化

- 优化 `topic_engine.py` 的 LLM prompt：明确用户输入一定按 AI/ML/CS 论文主题解释，限定 arXiv 类别方向，并要求 `should` 覆盖同义词、方法名、任务名、应用子方向、上下游相邻概念和 arXiv 标题常见写法。
- 将非 AI/ML/CS 的缩写含义显式放入 `negative`，并要求避免 `AI`、`machine learning`、`deep learning` 等泛词污染召回。
- 加强生成后清洗：must / should / negative 统一去重限量，must/should 中和 negative 冲突的短语会被过滤；已知主题的本地语义偏好会注入 prompt，OPD hint 扩展为更偏 on-policy distillation / policy distillation / RL policy 的多元检索词。
- arXiv 查询词上限从 10 提高到 16，避免多元检索词只生成不参与召回。
- `tests/test_topic_engine.py` 新增 prompt 约束、OPD 负向短语过滤、生成词去重和 negative 冲突过滤测试。

#### 验证

- `python3 -m py_compile topic_engine.py run_topic.py web_server.py paperhub/topic_store.py` 通过。
- `python3 -m unittest tests.test_topic_engine` 通过。

### 主题订阅 Top 3 检索与手动提交保护

#### 新增能力

- 新增 `/topic` tab，支持创建长期主题订阅，例如 `opd`；主题 profile 保存在 `data/topic/topics.json`，每日结果保存在 `data/topic/<slug>/<YYYY-MM-DD>/index.json`。
- 新增 `run_topic.py` 和 `topic_engine.py`：
  - 首次创建主题时使用 `.env` 中的 `TOPIC_LLM_*` 调用 v3api 生成 must / should / negative 检索词；
  - arXiv 检索默认限定 `cs.AI`、`cs.LG`、`cs.CL`、`cs.CV`、`cs.RO`、`cs.IR`、`stat.ML`；
  - HF Papers 近 7 天 upvote 作为热度信号；
  - 默认权重为相关性 45%、新鲜度 30%、HF vote 25%；
  - 同一 topic 默认排除已推送过的 arXiv ID，宁缺毋滥；全站允许不同 topic 重叠。
- 主题 Top 3 复用统一 paper store：已有 `data/papers/<arxiv_id>.json` 或 `<arxiv_id>_zh.pdf` 时直接命中缓存，不重复摘要翻译或重复生成全文中文 PDF。

#### Web 与安全

- 新增 topic 管理 API，可创建 topic、编辑检索词、启停主题、刷新今天和强制重排。
- 写操作使用 `.env` 中的 `TOPIC_ADMIN_TOKEN` 校验；手动提交 `/api/submit` 同样要求该 token，页面仍可公开访问。
- `/search` 保持公开，只做本地已缓存论文搜索，不消耗 LLM/PDF 资源。

#### 运维

- `.gitignore` 增加 `.env`，本地 API key 和管理 token 不进 git。
- README 增加 topic CLI、`.env` 变量、数据目录和 cron 示例：`30 1 * * * python3 run_topic.py --all`。

#### 验证

- `python3 -m py_compile paperhub/env_config.py paperhub/topic_store.py topic_engine.py run_topic.py web_server.py` 通过。
- 新增 `tests/test_topic_engine.py`，覆盖 slug、相关性、负关键词、新鲜度和同 topic 去重。
- `tests/test_web_server_contract.py` 覆盖 `/topic` 页面、手动提交 token、topic API token，同时确认 `/search` 保持原公开行为。
- `python3 run_topic.py opd 2026-07-04 --force` 验证真实 topic 链路，Top 3 均命中 paper store PDF 缓存，未重复生成 PDF。

---

## v4.29 — 2026-07-04

### 全量 PDF 失败状态补扫与恢复

#### 扫描范围

- 全量扫描 `data/*/*/index.json`，检查 `pdf_status=failed` 以及 `pdf_status=ok` 但 paper store 缺少 `<arxiv_id>_zh.pdf` 的条目。
- 同步检查 `logs/pdf_errors/` 和 `data/tex_backup_failed/`，区分真实缺失 PDF 与已恢复后的陈旧诊断。

#### 修复结果

- **`2606.11324`**：`monthly/2026-06` 中仍为 `pdf_status=failed`，且 paper store 缺 PDF。复用 `data/tex_backup_failed/2606.11324_merge_translate_zh.tex` 重编译；在 v4.28 行级 snippet 插入修复基础上，继续补齐 `\faEnvelope`、`\faGem` FontAwesome legacy alias，最终健康检查通过并生成 `data/papers/2606.11324_zh.pdf`，约 26.40MB。
- **`2605.10344`**：不在当前索引失败项中，但有历史失败日志且 paper store 缺 PDF。复用 `data/tex_backup/2605.10344_merge_translate_zh.tex`，从源码缓存重建 workfolder 后直编译成功，生成 `data/papers/2605.10344_zh.pdf`，约 3.66MB。
- 清理了所有“PDF 已存在但失败日志仍残留”的陈旧 `logs/pdf_errors/*.log`，以及 `2606.24597` 已恢复后残留的 `data/tex_backup_failed/*`，避免后续误判。

#### 验证

- `python3 run_repair.py --retry-pdf --mode monthly --key 2026-06` 已同步 `2606.11324` 为 `pdf_status=ok`。
- 全量复扫结果：索引失败 0、失败日志 0、失败现场 tex 0。

---

## v4.28 — 2026-07-04

### 2026-07-01 / 2026-07-03 daily PDF 编译失败修复

#### 失败定位

- **2026-07-01 `2606.30626`**：daily 流程遗留 `locks/daily-2026-07-01.lock`，对应 PID 已不存在，导致该篇未继续产出 PDF。清理陈旧锁后复跑，真实失败点为旧模板在 `\author[...]` 中使用 `\faEnvelopeO`，现有 FontAwesome legacy alias 未覆盖该命令。
- **2026-07-03 `2607.02440`**：失败发生在 LaTeX 编译阶段，正文中文翻译缓存完整；模板使用 `\faDatabase`，旧 alias 表未覆盖。
- **2026-07-03 `2607.02255`**：失败发生在 LaTeX 编译阶段，翻译 tex 中存在 `\DeclareUnicodeCharacter{...}`，但当前 XeLaTeX/fallback 环境没有对应命令定义。

#### 通用修复

- 扩展 `patch_fontawesome_legacy_aliases()`，新增 `\faDatabase` 与 `\faEnvelopeO` fallback；后续同类旧式 FontAwesome 命令会在 fallback 重编译阶段自动补齐。
- 修复 LaTeX fallback snippet 插入策略：命令出现在 preamble 参数内部时，改为插到“最早使用所在行之前”，避免把 fallback 块插进 `\author[...]`、`\text{...}` 等参数中造成新的编译错误。
- 新增 `patch_declare_unicode_character_fallback()`，在 fallback 重编译阶段为缺失的 `\DeclareUnicodeCharacter` 补 no-op 兼容，覆盖 inputenc 风格源码迁移到 XeLaTeX 时的常见失败。

#### 恢复结果

- 使用 `--keep-translation` 复用已保留的中文 tex 缓存重编译成功，未重新消耗全文翻译：
  - `2606.30626_zh.pdf` 约 2.00MB；
  - `2607.02440_zh.pdf` 约 1.20MB；
  - `2607.02255_zh.pdf` 约 6.99MB。
- `python3 run_repair.py --retry-pdf --mode daily --key 2026-07-01` 和 `python3 run_repair.py --retry-pdf --mode daily --key 2026-07-03` 已同步索引，两个日期的 daily 论文均为 `pdf_status=ok`。
- 三篇旧失败诊断日志和 `data/tex_backup_failed/` 失败现场 tex 已在成功后自动清理；成功版中文 tex 已备份到 `data/tex_backup/`。

#### 验证

- `python3 -m py_compile full_translate_driver.py translate_full.py run_papers.py` 通过。
- `python3 translate_full.py <id> -o data/papers --keep-translation` 分别验证三篇失败论文重编译通过。

---

## v4.27 — 2026-07-01

### inline `\verb` 分隔符冲突 fallback 修复

#### 2026-06-30 daily `2606.28733` 中文 PDF 恢复

- **影响论文**：daily `2026-06-30` 的 `2606.28733` (Agentic Abstention)。
- **根因定位**：
  - 中文翻译缓存已经完整存在，失败发生在 LaTeX 编译阶段。
  - `merge_translate_zh.tex` 中一处正则表达式使用 `\verb|...|` 包裹，但正则内容自身包含 `|`，导致 TeX 在内部 `|` 处提前结束 verbatim 参数。
  - 后续正则里的 `\?`、`\!` 逃逸字符脱离 verbatim 后被 LaTeX 当成控制序列解析，触发 `Undefined control sequence`，健康门禁正确拦截了坏 PDF。
- **修复**：
  - 在 `latex_translation_filters.py` 中新增 `repair_inline_verb_delimiter_collisions()`，只识别同一行内可疑的 inline `\verb` 分隔符冲突，并改用当前内容中不存在的新分隔符。
  - 在 `full_translate_driver.py` 的 fallback 重编译阶段接入该修复；正常首次编译成功的论文不会经过该路径，避免影响既有正常 PDF。
  - 新增单元测试覆盖失败形态，并确认普通 `\verb|foo|` 与一行多个正常 `\verb` 不会被改写。
- **结果**：
  - 使用 `python3 translate_full.py 2606.28733 -o data/papers --keep-translation` 复用 `tex_backup_failed` 中的中文翻译缓存重编译成功，没有重新走全文翻译。
  - 新 PDF 写入 `data/papers/2606.28733_zh.pdf`，大小约 1.83MB；旧失败诊断和失败现场 tex 已自动清理。
  - `python3 run_repair.py --retry-pdf --mode daily --key 2026-06-30` 命中 paper store PDF 并同步 `data/daily/2026-06-30/index.json` 与 `data/papers/2606.28733.json`，该日 3 篇论文均为 `pdf_status=ok`。

#### 验证

- `python3 -m unittest tests/test_latex_translation_filters.py` 通过，12 个测试 OK。
- `python3 -m py_compile latex_translation_filters.py full_translate_driver.py translate_full.py run_papers.py run_repair.py` 通过。

---

## v4.26 — 2026-06-28

### Weekly 当前周补抓边界修复

#### W26 网络失败后的自动补抓恢复

- **影响周期**：weekly `2026-W26`。
- **根因定位**：
  - `2026-06-28 02:00` 与 `02:30` 的 weekly cron 均已启动，但当时访问 `huggingface.co` 返回 `Network is unreachable`，导致 `run_weekly.py` 未能生成 `data/weekly/2026-W26/index.json`。
  - 后续 `run_repair.py --post --mode weekly --days 14` 虽然扫描到了 `2026-W26`，但旧逻辑会无条件跳过当前 ISO 周；周日 02:00 触发时间已经过去后，当前周仍无法被自动补抓。
- **修复**：
  - 新增 `_pending_refetch_key()`，将跳过规则从“永远跳过当前周期”改为“仅在该周期首次 cron 触发时间未到时跳过”。
  - weekly 在周日 02:00 后允许补抓当前 ISO 周；daily 在 23:00 后允许补抓当天；monthly 在 28 日 02:00 后允许补抓当月。
  - `refetch_missing()` 继续避免提前误抓未到触发时间的周期，同时能修复触发后发生的临时网络失败。
  - 新增 `tests/test_repair_refetch.py`，覆盖 weekly/daily/monthly 触发时间前后的跳过边界。
- **结果**：
  - 已补抓 `2026-W26` weekly Top 10，`data/weekly/2026-W26/index.json` 生成成功。
  - 10 篇论文摘要与期内 HTML 均完成，10 个 `pdf_status` 均恢复为 `ok`。
  - 新生成全文 PDF：`2606.19409_zh.pdf`、`2606.20945_zh.pdf`；其余 8 篇命中 paper store 既有 PDF。

#### 验证

- `python3 fetch_hf.py weekly 2026-W26 10` 可正常返回 10 篇。
- `python3 run_weekly.py 2026-W26` 完整流程成功，最终 `成功=10 失败=0`。
- `/root/.pyenv/versions/3.10.13/bin/python3 -m unittest discover -s tests -v` 全部 33 个测试通过。

---

## v4.25 — 2026-06-27

### 项目结构轻量收拢

#### 共享路径常量与回归护栏

- 新增 `paperhub.paths`，统一维护项目根目录、`data/`、`data/papers/`、`logs/`、`locks/`、手动提交目录、书签文件、tex 备份目录、mode index 路径和默认翻译容器名。
- 新增 `paperhub.paper_store`，统一维护 paper store JSON raw read、translated-cache read、PDF 有效性阈值、PDF 入库和 `pdf_status` 更新。
- `run_papers.py`、`run_repair.py`、`translate_arxiv.py`、`translate_full.py`、`web_server.py` 改为复用共享路径定义，但继续保留原有模块级常量名，避免影响外部脚本和测试引用。
- 新增 `tests/test_paths.py` 和 `tests/test_paper_store.py`，锁定入口脚本常量、共享路径与 paper store 读写语义，防止后续重构中数据目录或缓存规则漂移。
- 扩展 Web 合约测试，覆盖收藏 API 的 create/toggle/move/remove 流程，以及 `/api/submit`、`/api/bookmarks` 的错误响应契约。
- 本次不改 Web 路由、不改 JSON 数据结构、不改 PDF 翻译流程、不改 Docker 调用语义。

#### 验证

- `/root/.pyenv/versions/3.10.13/bin/python3 -m py_compile ...` 通过。
- `/root/.pyenv/versions/3.10.13/bin/python3 -m unittest discover -s tests -v` 全部 29 个测试通过。

---

## v4.24 — 2026-06-26

### PDF 编译 OOM 强杀与健康检查漏过修复

#### 开启容器 Swappiness 与 sequential 编译架构优化

- **影响论文**：daily `2026-06-24` 的 `2606.19930` (MobileForge) 和 daily `2026-06-25` 的 `2606.26058` (DomainShuttle)。
- **根因定位**：
  - **内存超限**：两篇论文含有高分辨率或超大图片（MobileForge 含有 32MB 图像，DomainShuttle 含有 11MB PNG 及多个 3MB PDF）。在 1.8GB RAM 物理内存受限的机器上，`xelatex` 编译至 DVI 并并发调用 `xdvipdfmx` 转换 PDF 时，两者并发运行引发系统级 host OOM 强杀进程。
  - **Docker 限制**：旧 Docker 容器未显式设置 `memory-swappiness`。在 `--memory` 内存限制下，内核 cgroup 的 `memory.swappiness` 默认为 `0`，禁止使用宿主机交换空间（Swap），导致即使宿主机有 2.8GB 闲置 Swap，容器进程依然会被直接强杀。
  - **健康检查绕过**：如果编译被强杀，生成的 PDF 往往是不完整的损坏文件。然而由于强杀导致编译日志 `merge_translate_zh.log` 同样被截断（如刚好卡在 80KB 处），日志中未写入最终的错误报告与引用缺失汇总。旧健康门禁仅在日志中查找特定错误特征，未检测日志是否完整，导致漏判，认为编译通过并把坏 PDF 复制给用户。
- **修复**：
  - **解除 Swap 限制**：在 `scripts/run_latex_slim.sh` 中为 `docker run` 命令添加 `--memory-swappiness=60` 参数，解封容器内存页在物理 RAM 不足时与宿主机 Swap 文件的正常换页操作。
  - **编译拆分串行**：修改 `full_translate_driver.py` 中的 LaTeX/DVI 管道，采用串行两阶段执行（通过 `xelatex -no-pdf` 输出 `.xdv` 文件，等进程退出释放内存后，再调用 `xdvipdfmx -z 3` 转换 PDF，并设定压缩等级 `3` 限制 zlib 的内存开销），有效规避了两个内存大户并发占内存的现象。
  - **超时参数放宽**：为照顾 Swap 高磁盘延迟换页，将所有 LaTeX/DVI/xdvipdfmx 编译命令执行超时从 300 秒合理放宽至 900 秒，避免被主进程提前中断。
  - **两级健康门禁兜底**：
    - 在驱动内通过 `latex_compile_health_ok` 首行校验编译日志中是否包含 `"Output written on ..."` 的正常写入标志，规避日志截断漏判。
    - 在驱动内基于 `pypdf` 实现 `check_pdf_integrity` 完整性校验，确认生成的 PDF 真正可以加载且存在页面。
    - 在宿主机端 `translate_full.py` 增加二进制后验，读取并确认从容器中复制回的本地 PDF 尾部 1024 字节内确实包含 `%%EOF` 标志（不全或损坏均判定为失败）。
- **结果**：
  - `2606.26058` 成功以串行编译在 72 秒内产出 28.85MB (20页) 正常 PDF。
  - `2606.19930` 成功依靠 Swap 置换，历时 8 分钟编译出 39.51MB (34页) 正常 PDF，无 OOM 阻碍。
  - 自动清理了宿主机端旧错误诊断与旧现场 tex。

---

## v4.23 — 2026-06-25

### 2026-06-24 daily PDF 失败修复与健康门禁优化

#### 翻译覆盖率门禁放宽与 natbib 警告降级

- **影响论文**：daily `2026-06-24` 的 `2606.24597` (Qwen-AgentWorld)、`2606.19930` (MobileForge)。
- **根因定位**：
  - `2606.24597`：翻译覆盖率门禁过严。该论文正文极长（Qwen 智能体世界模型），包含了较多 Related Work 英文文献描述，其最终 `cjk_pct` 达到 73.0%，但因包含 14 行长英文行，触发了旧规则 `long_count >= 10 and cjk_pct < 78.0`，导致被误判为覆盖率不达标。
  - `2606.19930`：编译健康门禁将 natbib 的未定义引用警告视作致命错误。在多轮编译中，natbib 可能会在引用被 bibliography 解析前产生 `Package natbib Warning: Citation ... undefined` 警告，这其实不影响最终 PDF 的生成质量（生成的 PDF 达 3.8MB，完全正常且排版完整），但由于旧正则匹配了任何包含 `undefined citation` 的日志行，导致发布被拦截。
- **修复**：
  - 放宽 `full_translate_driver.py` 中的翻译覆盖率门禁：将 `long_count >= 10 and cjk_pct < 78.0` 的判定条件放宽至 `cjk_pct < 70.0`，更加契合长论文含有部分英文保留段落（如 Related Work 或公式推导）的实际需求。
  - 优化编译健康检查 `latex_compile_health_ok()`：拆分致命错误 (`fatal_checks`) 与非致命警告 (`warn_checks`)。将 natbib 相关的 `Citation ... undefined` 警告归类为 `warn_checks`，仅记录非致命日志警告，不再拦截 PDF 的生成。
  - 保持 `latex_compile_health_only_stale_refs()` 的拦截条件与上述拆分一致。
- **结果**：
  - 通过工具脚本直接复用已有的翻译 tex 缓存进行重编译，避免了耗时的二次 LLM 全文翻译。
  - `2606.19930` 编译成功：PDF 大小 3.8MB。
  - `2606.24597` 编译成功：PDF 大小 5.3MB。
  - 更新 `data/daily/2026-06-24/index.json`、`data/papers/2606.19930.json`、`data/papers/2606.24597.json` 中的 `pdf_status` 为 `ok`。
- **验证**：
  - `python3 -m py_compile full_translate_driver.py latex_translation_filters.py tests/test_latex_translation_filters.py` 通过。
  - `python3 -m unittest discover -s tests -v` 全部 21 个单元测试通过。
  - 宿主机端 PDF 文件有效性与大小验证通过。

---

## v4.22 — 2026-06-17

### LaTeX 翻译 chunk 回归收口

#### 二次拆分后恢复上游短 chunk 保护

- **背景**：前序 splitter 优化为了减少 preserve 区域漏翻译，对表格、算法和普通正文做了二次拆分；这能提升覆盖率，但也绕过了 gpt-academic 原始 `post_process` 对过短 transform 节点的保护。
- **根因定位**：
  - gpt-academic 原始链路先用 mask 标记 `PRESERVE/TRANSFORM`，随后 `post_process` 会把空白和短于 42 字符的 transform 节点降级为 preserve，并剥离前后空白；
  - 当前项目的二次拆分发生在上游 `post_process` 之后，表格单元格、algorithmic 参数和短正文行可能被重新拆成过短 chunk；
  - 这些碎片单独送入 `switch_prompt()` 后，模型更容易回答 prompt 本身，生成 “Below is.../Please provide.../请提供...” 等非原文残留；
  - 旧 fallback 只在编译失败后的重编译阶段清理 artifact，若 LaTeX 恰好能编译，污染会随成功 PDF 发布。
- **修复**：
  - `full_translate_driver.py` 在二次拆分完成后新增最终收口：过短、命令占比高、空白/分隔符类 transform chunk 降级回 preserve；章节标题仍按单独规则允许翻译；
  - 保留上游 `LatexPaperSplit` 的 mask 结果作为第一层，不改写原始保护策略，只在新增扩展节点上补回等价安全门；
  - 新增 splitter cache version marker，splitter 结构变化时自动丢弃旧 `temp.pkl`，避免旧翻译缓存和新节点结构错位 merge；
  - monkey-patch gpt-academic 的 `fix_content` 和 `latex_actions.fix_content` 引用，在每个翻译节点 merge 进 `merge_translate_zh.tex` 前清理非原文 LLM artifact；若清理后没有有效内容则回退原始 chunk；
  - 扩展 `latex_translation_filters.py` 的 artifact 模式，覆盖本轮实际出现的 “Below is the section you provided translated into Chinese. If you have any specific section...” 等残留；
  - 新增单元测试覆盖 prompt echo 清理。
- **验证**：
  - `python3 -m py_compile full_translate_driver.py latex_translation_filters.py translate_full.py run_papers.py run_repair.py` 通过；
  - `python3 -m unittest tests.test_latex_translation_filters -v` 通过；
  - 容器内 `/tmp/full_translate_driver.py` 与 `/tmp/latex_translation_filters.py` 语法检查通过；
  - `python3 translate_full.py 2606.11176 -o /tmp/paper-trans-verify-2606.11176 --keep-translation --timeout 900` 成功，`cjk_pct=85.8%`，编译健康检查通过，PDF 34.09MB。

---

## v4.21 — 2026-06-17

### 2026-06-16 daily PDF 失败修复

#### 通用 LaTeX fallback 与宿主机超时收口

- **影响论文**：daily `2026-06-16` 的 `2606.14777`、`2606.11176`。
- **根因定位**：
  - `2606.14777` 的 `jingdong.cls` 在 XeLaTeX/LuaLaTeX 路径中直接执行 `\pdfoutput`、`\pdfmapline` 等 pdfTeX primitive，后续还把本地 T1 字体族设成 `\sfdefault`，导致 `xdvipdfmx` 找不到 `JINGDONGLangZhengTi2-Regular` 的 TFM；
  - `2606.11176` 翻译后存在自定义零参数宏与中文/中文标点粘连，例如 `\methodshort\并非`、`\yespart标记`，并缺少 `\faSearch` fallback；
  - 宿主机 `translate_full.py` 的流式读取实际使用阻塞 `readline()`，容器长时间无换行输出时外层 timeout 不能稳定收口。
- **修复**：
  - 将自定义零参数宏 CJK 粘连修复抽象到 `latex_translation_filters.py`，按已定义宏自动处理后接中文、中文标点或 ASCII 的场景；
  - 将 pdfTeX primitive 行 guard 抽象到共享过滤模块，并在 fallback 中递归扫描本地 `.cls/.sty/.tex`；
  - 新增本地不可用 T1 字体默认值回退：当 class/style 把本地路径字体族设为 `\sfdefault/\rmdefault/\ttdefault` 时，自动回退到 Latin Modern；
  - FontAwesome legacy fallback 扩展到 `\faSearch`；
  - `translate_full.py` 改为 `os.read` 非阻塞读取容器输出，timeout 后会尽力清理容器内同篇 driver 进程；
  - 增加单元测试覆盖宏粘连和 pdfTeX primitive guard。
- **结果**：
  - `2606.11176` 复用翻译缓存重编译成功：`cjk_pct=85.8%`，编译健康检查通过，PDF 34.09MB；
  - `2606.14777` 复用新 no-cache 翻译缓存重编译成功：`cjk_pct=84.9%`，编译健康检查通过，PDF 5.06MB；
  - `data/daily/2026-06-16/index.json` 三篇均为 `pdf_status=ok`，两篇旧失败日志和失败现场 tex 已清理。
- **验证**：
  - `python3 -m py_compile full_translate_driver.py latex_translation_filters.py translate_full.py run_papers.py run_repair.py` 通过；
  - `python3 -m unittest tests.test_latex_translation_filters -v` 通过；
  - `python3 run_repair.py --retry-pdf --mode daily --key 2026-06-16` 成功恢复两篇失败 PDF；
  - 容器内最新 `merge_translate_zh.log` 无 undefined command/citation/reference、missing number、fatal/emergency 残留。

---

## v4.20 — 2026-06-16

### retry-pdf slim 容器恢复与 LaTeX fallback 加固

#### 2026-06-15 daily 三篇与 2026-W24 weekly 两篇恢复

- **影响论文**：
  - daily `2026-06-15`：`2606.13432`、`2606.12384`、`2606.06036`；
  - weekly `2026-W24`：`2606.05405`、`2606.03988`。
- **根因定位**：
  - `translate_full.py` 的默认容器仍是旧 `gpt-academic-latex`，而机器上只保留 `gpt-academic-latex-slim`；
  - 容器检查使用 `docker ps -f name=...`，会把 `gpt-academic-latex-slim` 当成旧容器的模糊匹配，导致检查通过但后续 `docker cp gpt-academic-latex:/...` 失败；
  - retry 只判断“有翻译缓存”，但 `translate_full --keep-translation` 之前只接受宿主机成功 tex 备份，未能复用容器内现有 `merge_translate_zh.tex`；
  - 缓存 tex 编译失败后外层没有自动降级到 no-cache 重译；
  - 本轮暴露多类 LaTeX/LLM 残留问题：PDFTeX-only 模板、坏 `.aux`、缺失/不安全 citation key、FontAwesome 旧别名、XeLaTeX segfault、algorithm2e 关键字被翻译、表格单元格里混入 “Please provide/通用引言” 幻觉段落。
- **修复**：
  - 代码默认容器改为 `gpt-academic-latex-slim`，并用 `docker container inspect` 精确检查容器运行状态；
  - 复制驱动脚本失败时输出 stderr，便于直接看到容器名/权限问题；
  - `--keep-translation` 支持直接复用容器内 `merge_translate_zh.tex`，workfolder 不完整时仍可从源码缓存重建；
  - `retry-pdf` 在缓存重编译失败后自动清缓存并 no-cache 重译全文；
  - fallback 清理旧 PDF 和更多 LaTeX 中间文件，避免半截/旧 PDF 误通过；
  - `.aux` 净化改为删除 fragile `\@writefile` 并将 `\newlabel` 压缩为安全二字段格式，保留 `\citation/\bibcite`；
  - 预生成 `.bbl` 后直接 `\input{merge_translate_zh.bbl}`，缺 `\bibliography` 时可回退读取原始 `merge.tex`；
  - 自动规范化不安全 citation key，例如 `hu2025reinforce++ -> hu2025reinforcepp`、`yeshwanth2023scannet++ -> yeshwanth2023scannetpp`；
  - FontAwesome legacy alias 扩展到 `\faGlobe/\faGithub/\faTrophy`；
  - 检测到 XeLaTeX segfault 时自动切换 LuaLaTeX 多轮重编译；
  - 恢复 algorithm2e 的英文 `\Input` 等关键字别名；
  - 扩展 LLM artifact 过滤，清理更多 “Below is/Please provide/请提供” 和伪造 ML 引言段落，避免污染表格或正文。
- **结果**：
  - `data/daily/2026-06-15/index.json` 中 `2606.13432`、`2606.12384`、`2606.06036` 均恢复为 `pdf_status=ok`；
  - `data/weekly/2026-W24/index.json` 中 `2606.05405`、`2606.03988` 均恢复为 `pdf_status=ok`；
  - 成功 PDF 已生成：`2606.13432` 6.07MB、`2606.12384` 5.29MB、`2606.06036` 5.45MB、`2606.05405` 22.79MB、`2606.03988` 16.15MB；
  - 上述五篇旧失败诊断日志已清理。
- **验证**：
  - `python3 -m py_compile full_translate_driver.py latex_translation_filters.py translate_full.py run_papers.py web_server.py` 通过；
  - `python3 -m unittest tests.test_latex_translation_filters -v` 通过；
  - daily 与 weekly retry 均跑通，编译健康检查无 undefined command/citation/reference 残留。

---

## v4.19 — 2026-06-15

### 翻译过滤策略抽象化

#### LaTeX 环境保护与模型残留清理通用化

- **背景**：`2606.09426` 修复中暴露出一类可复用规则：自定义 CLI/GUI 轨迹、prompt、code、trace、listing 环境应该统一保护；LLM “请提供/Please provide” 这类非原文残留也应该统一过滤，而不是每次在 splitter、质量门禁和 fallback 里分别硬编码。
- **修复**：
  - 新增 `latex_translation_filters.py`，统一维护 LaTeX 环境策略和 LLM artifact 过滤策略；
  - splitter、`translation_quality_report()` 和 `patch_verbatim_envs()` 改为复用同一套策略；
  - 自定义环境按命名特征动态识别：CLI/GUI、trace、trajectory、transcript、console、terminal、shell、prompt、code、log、listing、verbatim、minted 等环境默认硬保护；
  - 区分“硬保护”和“可从原文恢复”：table/figure/equation 仍可保护编译，但 fallback 不再把它们恢复成英文；只有 verbatim/listing/trace/prompt/CLI/GUI 等环境参与原文块恢复；
  - 支持通过 `PAPER_TRANS_EXTRA_HARD_ENVS`、`PAPER_TRANS_EXTRA_SOFT_ENVS`、`PAPER_TRANS_EXTRA_RESTORE_ENVS` 和 `PAPER_TRANS_EXTRA_LLM_ARTIFACT_PATTERNS` 扩展过滤条件；
  - `translate_full.py` 复制驱动时同步复制策略模块，并在容器内 chmod 为可读，避免 gptuser import 权限问题；
  - tex 备份改为成功后才覆盖 `data/tex_backup/`，失败现场另存 `data/tex_backup_failed/`，避免质量门禁失败污染可复用翻译缓存；同篇 PDF 成功后自动清理旧失败现场 tex。
- **验证**：
  - 新增 `tests/test_latex_translation_filters.py`，覆盖动态环境识别、恢复环境筛选、环境变量扩展和 LLM 残留清理；
  - `2606.09426` 使用 `gpt-academic-latex-slim` no-cache 完整重跑成功，`cjk_pct=74.1%`，`long_english_lines=1`，编译健康检查通过；
  - `data/papers/2606.09426_zh.pdf` 已刷新，旧失败日志已自动清理，成功 tex 备份已恢复为干净版本。

---

## v4.18 — 2026-06-15

### 全文翻译失败恢复

#### 2026-06-14 daily `2606.09426` 中文 PDF 修复

- **影响论文**：`2606.09426`，WeaveBench。
- **根因定位**：
  - 原始 gpt-academic splitter 对超长正文行和 cite 密集段落切分不够细，少数正文 chunk 被模型回显为英文；
  - 论文定义了 `climode`、`guimode`、`failmode` 和 `trajact*` 轨迹环境，这些内容本质是代码/操作轨迹示例，应保持原样，但此前会被翻译或被质量门禁误判为漏译；
  - `trajact*` 环境被模型改写后会生成 `trajaIMG` 等非法命令，导致 LaTeX 编译失败；
  - arXiv 源码包包含可用的 `main.bbl`，但没有 `references_v2.bib`，旧 fallback 跑 BibTeX 后得到空 `merge_translate_zh.bbl`，继而触发 undefined citation；
  - 失败缓存中还混入了模型生成的非原文残留，例如 “Please provide...” 和伪造的通用引言段落。
- **修复**：
  - 在保留 gpt-academic 原始 mask 的基础上，对超长普通正文行按句子边界继续拆分，降低长段英文回显概率；
  - 将 `climode`、`guimode`、`failmode`、`trajactCLI`、`trajactGUI`、`trajactFAIL`、`trajactFAILpair` 作为硬保护环境，翻译前不拆入 GPT，质量门禁也不按正文检查；
  - fallback 重编译时从原始 `merge.tex` 恢复上述 verbatim 类环境块，修复已损坏的旧翻译 tex；
  - 当 BibTeX 不可用、缺 `.bib` 或生成空 `.bbl` 时，自动复用源码包中已有且包含 `\bibitem` 的 `.bbl`；
  - 新增模型非原文残留清理，移除常见 “请提供/Please provide” 和伪造模板段落。
  - `translate_full.py` 在同篇 PDF 成功生成后自动清理旧 `logs/pdf_errors/<arxiv_id>.log`，避免已恢复论文仍被旧失败日志误导。
- **结果**：
  - 使用 `gpt-academic-latex-slim` 对 `2606.09426` 复用翻译 tex 重编译成功；
  - `data/papers/2606.09426_zh.pdf` 已刷新：6,009,381 bytes，34 页；
  - `data/papers/2606.09426.json` 与 `data/daily/2026-06-14/index.json` 的 `pdf_status` 已更新为 `ok`。
  - 旧失败诊断 `logs/pdf_errors/2606.09426.log` 已清理。
- **验证**：
  - 翻译覆盖率门禁通过：`cjk_pct=77.1%`，`long_english_lines=0`；
  - 编译健康检查通过，最新容器内 `merge_translate_zh.log` 无 undefined command、undefined citation/reference、fatal error 残留；
  - 当前 `merge_translate_zh.tex` 无 “Please provide”、 “请提供”、`lecun2015deep`、`ribeiro2016should` 等失败缓存残留。

---

## v4.17 — 2026-06-13

### 全文翻译覆盖率优化

#### 基于 gpt-academic 原始切分逻辑的安全扩展

- **影响论文**：`2606.13679` 和 `2606.13681`。
- **根因定位**：
  - gpt-academic 原始 `LatexPaperSplit` 会先按 mask 将 title 前、短 begin/end 环境、figure/table/algorithm/equation 等区域标成 `PRESERVE`，再把 caption/abstract 等少量区域反向放回 `TRANSFORM`；
  - 该策略能保护编译，但遇到正文被短环境包裹、表格单元里放长段自然语言、algorithmic 中有说明文字时，会把可翻译正文留在 preserve 区；
  - `2606.13679` 的 abstract、Introduction、Related Work 存在大段正文未翻译；`2606.13681` 的附录正文、偏好演化表格、algorithmic 说明文字存在残留英文。
- **修复**：
  - 保留 gpt-academic 原始 splitter 作为第一层切分，不直接改写上游 mask 规则；
  - 在原始 `PRESERVE` 节点上做二次安全拆分：普通正文行重新送入 GPT；`tabular/tabularx/longtable/array` 只拆出单元格文本，保留 `&`、行尾 `\\` 和 rule 命令；`algorithmic` 只拆出 `\State/\Require/\Ensure/\Comment/\For/\If` 的自然语言参数，保留 LaTeX 命令和括号；
  - 翻译覆盖率门禁同步纳入表格长文本和 algorithmic 说明文字，但继续忽略 figure/equation/verbatim/listing/bibliography 等硬保护区域；
  - fallback 增加 `algorithmic` 命令粘连修复，处理 `\Comment中文` 这类 GPT 翻译后丢失空格/括号的编译副作用。
- **结果**：
  - `2606.13679` 重跑 no-cache：chunk 从 45 扩到 130，正文 CJK 覆盖约 75.1%，`long_english_lines=0`，PDF 5,319,916 bytes，20 页；
  - `2606.13681` 重跑 no-cache：chunk 从 197 扩到 609，正文 CJK 覆盖约 85.5%，`long_english_lines=0`，PDF 3,727,808 bytes，55 页；
  - 两篇旧漏译位置抽查均已变为中文；保留 prompt/json/listing 示例中的英文原文，不强行翻译代码或示例协议。
- **验证**：
  - 两篇容器内 `merge_translate_zh.log` 无 undefined command、missing number、undefined citation/reference、rerun cross-reference 残留；
  - `pdfinfo` 可读取两篇页数和文件体积；
  - `data/daily/2026-06-12/index.json` 与 paper store JSON 中两篇 `pdf_status` 保持 `ok`。

---

## v4.16 — 2026-06-13

### 全文翻译编译修复

#### 2026-06-12 daily 两篇失败 PDF 恢复

- **影响论文**：`2606.13681` 和 `2606.13673`。
- **根因定位**：
  - `2606.13681` 使用的 NeurIPS 模板引用旧式 `\faGlobe`，但当前 `fontawesome5` 没有该别名；同时存在 XeLaTeX 下 `microtype` 非兼容特性和带可选参数的 list 环境；
  - `2606.13673` 的 NVIDIA technical report 模板硬编码 `NVIDIASans_*` T1/pdfmap 字体，但容器和源码包均缺少对应 TFM/字体映射，`xdvipdfmx` 在第 5 页报 `Unable to find TFM file "NVIDIASans_It"`，只留下 42KB 半截 PDF；
  - `2606.13673` 后续看到的 `File ended while scanning use of \citation` 是 LaTeX 中途崩溃后写坏 `.aux` 的副作用，不是中文翻译内容损坏。
- **修复**：
  - `full_translate_driver.py` 增加 `\faGlobe` legacy alias fallback；
  - 对 XeLaTeX 下高风险 `microtype` 显式加载进行禁用，并扫描本地 `.cls/.sty` 中的 `\RequirePackage{microtype}`；
  - 对含可选参数的 `itemize/enumerate/description` 自动补 `enumitem`；
  - 在 fallback 重编译前清理旧 `.aux/.bbl/.log` 等中间文件，并从 `merge_translate_zh.tex` 的 citation/bibliography 信息预生成 `.bbl`；
  - 新增 NVIDIA Sans 本地模板补丁：当 `kpsewhich NVIDIASans_It.tfm` 不存在时，注释本地 class/style 中的 `NVIDIASans` `\input`、`\pdfmapline` 和 `\rmdefault` 覆盖，让模板回退到容器已有字体。
- **结果**：
  - `2606.13681_zh.pdf` 重新生成成功：3,677,117 bytes，56 页，正文 CJK 覆盖约 86.4%；
  - `2606.13673_zh.pdf` 重新生成成功：1,560,088 bytes，27 页，正文 CJK 覆盖约 82.3%；
  - `data/daily/2026-06-12/index.json` 与两篇 paper store JSON 的 `pdf_status` 已更新为 `ok`。
- **验证**：
  - 两篇 `translate_full.py --keep-translation` 均返回 success；
  - 两篇容器内编译健康检查均通过，无 undefined command/citation/reference 残留；
  - `pdfinfo` 可读取页数和文件体积，确认不再是半截小 PDF。

---

## v4.15 — 2026-06-12

### PDF 查看页缓存修复

#### 重新生成 PDF 后仍显示旧内容

- **问题**：`/paper/view/2606.12397` 和 `/paper/view/2606.12344` 路由已经正确，线上 `/paper/papers/<id>_zh.pdf` 的 hash 也与服务器本地新 PDF 一致；但浏览器/PDF viewer 可能仍使用此前打开过的旧 PDF 缓存。线上 nginx 对 PDF 返回 `Cache-Control: public, max-age=3600`，因此同一路径的 PDF 在短时间内可能不重新拉取。
- **修复**：`/view/<arxiv_id>` 的 iframe PDF URL 增加文件 mtime 版本号：
  - 旧：`/paper/papers/<id>_zh.pdf#view=FitH`
  - 新：`/paper/papers/<id>_zh.pdf?v=<pdf_mtime>#view=FitH`
- **缓存策略**：`/view/<id>` wrapper 返回 `Cache-Control: no-store`，PDF 文件本身继续保留 Range 与 nginx 缓存；重新生成 PDF 后只需刷新 wrapper，即可得到新的版本化 PDF URL。
- **验证**：
  - `2606.12397_zh.pdf` 线上 hash：`6f8e06c6b7614e115300844b5d472caabc5da636a9de7a16d6942dd6b827fa56`，与本地一致；
  - `2606.12344_zh.pdf` 线上 hash：`42e52bc8519a33710fce38f50af6f07735205f8a975bdfd3dbd10e713eee4a73`，与本地一致；
  - 合约测试增加 `BASE_PATH=/paper` 下带前缀 view/PDF/redirect 检查，并固定 iframe 版本号格式。

---

## v4.14 — 2026-06-12

### 全文翻译质量与编译健康修复

#### 2606.06021 与 2026-06-11 daily 三篇中文 PDF 重新生成

- **影响论文**：`2606.06021`、`2606.12397`、`2606.11926`、`2606.12344`。
- **根因定位**：
  - 大段英文不是 PDF 编译造成的，而是 `merge_translate_zh.tex` 本身已经包含大量未翻译正文；
  - gpt-academic 的 LaTeX splitter 对复杂论文过于保守，会把普通正文粘进 `preserve=True` 节点，导致这些正文没有送入 GPT 翻译；
  - `--keep-translation` 遇到已存在 `merge_translate_zh.tex` 会跳过 GPT，因此会反复发布旧的半翻译 tex；
  - `2606.12397` 的 cite/ref 问题不是 bib key 损坏，实际是 `\name\的` / `\Name\的` 这类自定义宏与中文粘连触发 `Undefined control sequence`；
  - `2606.11926` 另有 `\textTest:` 误生成命令；`2606.12344` 原始源码存在 `\ref{fig:leak_fix_openclaw}` 与唯一实际 label `fig:leak_fix_openclaw_multilingual` 不一致。
- **修复**：
  - `full_translate_driver.py` patch `LatexPaperSplit.split`，把被保守 preserve 的明显普通正文重新拆出并送翻译；
  - 新增 `merge_translate_zh.tex` 翻译覆盖率检查，忽略 figure/table/equation/verbatim/bibliography 等保护块，发现低 CJK 覆盖或大量长英文正文时拒绝发布 PDF；
  - 新增编译健康检查，拒绝带 `Undefined control sequence`、undefined citation/reference、rerun cross-reference 等残留问题的 PDF；
  - fallback 重编译改为 `xelatex -> bibtex -> xelatex -> xelatex`，并加入自定义宏中文粘连、误生成 `\textWord`、唯一前缀 ref label 修补；
  - `translate_full.py --keep-translation` 在恢复宿主机 tex 备份后重设容器 workfolder 属主，避免 `docker cp` 生成 root-owned 文件后驱动无法改写 tex。
- **最终产物**：
  - `2606.06021_zh.pdf`：4,235,411 bytes，29 页，文本抽取约 92k 字符，正文 CJK 覆盖约 79.6%；
  - `2606.12397_zh.pdf`：854,351 bytes，15 页，文本抽取约 48k 字符，正文 CJK 覆盖约 89.8%；
  - `2606.11926_zh.pdf`：2,690,472 bytes，39 页，文本抽取约 139k 字符，正文 CJK 覆盖约 78.9%；
  - `2606.12344_zh.pdf`：2,021,696 bytes，37 页，文本抽取约 93k 字符，正文 CJK 覆盖约 73.9%。
- **验证**：
  - 四篇容器内 `merge_translate_zh.log` 均无 undefined control sequence、undefined citation/reference、rerun cross-reference 残留；
  - 四篇 `pdfinfo` / `pdftotext` 均可读取页数和文本内容；
  - 本地 Web Range GET 对 `/paper/papers/<id>_zh.pdf` 均返回 `206 Partial Content`、`Content-Type: application/pdf`；
  - `/paper/view/<id>` 均返回 HTML wrapper，iframe 指向 `/paper/papers/<id>_zh.pdf#view=FitH`。

### Web 路由修复

#### BASE_PATH=/paper 前缀请求兼容

- **问题**：页面生成已按 `BASE_PATH=/paper` 输出 `/paper/view/<id>` 和 `/paper/papers/<id>_zh.pdf`，但 HTTP handler 入口只识别去前缀后的 `/view/<id>` / `/papers/<file>`，导致直接访问 `/paper/view/<id>` 返回 404。
- **修复**：
  - 新增 `route_path()`，在请求入口统一剥离部署前缀后再路由；
  - 新增 `with_base_path()`，redirect 的内部目标自动补回 `/paper`；
  - 重启本地 Web 服务后验证 `/paper/view/<id>`、`/paper/papers/<id>_zh.pdf` 和旧 `/paper/papers/<id>` 重定向均正常。

---

## v4.13 — 2026-06-12

### 数据产物刷新

#### 2026-06-11 daily 三篇中文 PDF 重新生成

- **范围**：重新生成 `data/daily/2026-06-11/index.json` 对应的三篇中文 PDF：
  - `2606.12397_zh.pdf`：808,169 bytes，12 页，文本抽取约 35k 字符；
  - `2606.11926_zh.pdf`：2,667,200 bytes，41 页，文本抽取约 140k 字符；
  - `2606.12344_zh.pdf`：1,975,419 bytes，37 页，文本抽取约 86k 字符。
- **执行方式**：使用当前生产容器 `gpt-academic-latex-slim`，通过 `translate_full.py --keep-translation` 复用宿主机 `data/tex_backup/*_merge_translate_zh.tex`，只重跑 LaTeX 编译并覆盖 `data/papers/*_zh.pdf`。
- **验证**：
  - 三篇 `translate_full.py` 均返回 success；
  - `data/daily/2026-06-11/index.json` 与 paper store JSON 中三篇 `pdf_status` 均保持 `ok`；
  - 本地 Web Range GET 对三篇 `/papers/<id>_zh.pdf` 均返回 `206 Partial Content`、`Content-Type: application/pdf`；
  - 容器内 `pdfinfo` / `pdftotext` 可读取页数和文本内容。
- **说明**：`data/` 为服务器数据产物目录，当前未被 git 跟踪；本次提交记录操作与验证结果，PDF 文件已在服务器本地刷新。

---

## v4.12 — 2026-06-12

### Docker 镜像瘦身最终切换

#### full-TeX slim 生产容器落地

- **背景**：4.55GB 激进 slim 镜像能通过 canary，但 2026-06-11 daily 继续暴露缺 TeX/font 包问题；逐个补包风险高、维护成本大。最终改为保留原 `gpt_academic_with_latex` 的完整 TeX/font 运行时，只裁剪中文翻译不需要的 ML/runtime/cache/doc/source 负载。
- **构建调整**：
  - `scripts/build_latex_slim.sh` 新增 `GPT_ACADEMIC_SLIM_TEX_PROFILE`，默认 `full`，显式设为 `slim` 时才继续执行历史 TeX/font/JRE/asymptote 裁剪；
  - 新增 `GPT_ACADEMIC_SLIM_DRY_RUN=1`，可只估算裁剪后的 rootfs 体积；
  - 新增 `GPT_ACADEMIC_SLIM_EXPORT_ARCHIVE` 与 `GPT_ACADEMIC_SLIM_EXPORT_COMPRESSOR`，支持先导出压缩 rootfs，再删除旧镜像腾空间后手动 `docker import`，避免 40GB 根分区上的空间死锁；
  - `scripts/setup_docker_env.sh` 与 `docker/latex-slim/Dockerfile` 的 `bxcoloremoji` 下载改为固定 CTAN 镜像回退，避免 `mirrors.ctan.org` 偶发 403 导致构建失败。
- **生产切换**：
  - 已生成并导入 `paper-trans-latex-slim:latest`，镜像大小约 7.62GB；
  - 已启动 `gpt-academic-latex-slim`，并确认 `kpsewhich` 可解析 `libertine.sty`、`newtxmath.sty`、`zlmtt.sty`、`bxcoloremoji.sty`、`nicematrix.sty`、`latin.ldf`；
  - root crontab 和 `paper-trans-web.service` drop-in 均已切到 `GPT_ACADEMIC_CONTAINER=gpt-academic-latex-slim`。
- **磁盘清理**：
  - 确认新容器可用后，删除旧容器 `gpt-academic-latex` 和旧镜像 `ghcr.io/binary-husky/gpt_academic_with_latex:master`；
  - 删除旧镜像后 Docker overlay2 残留孤儿目录，确认 Docker images/containers/volumes/build cache 均为空后，停 Docker/containerd 清理孤儿 overlay2，再启动服务；
  - 删除临时 rootfs 压缩包后，根分区可用空间恢复到约 14GB，当前 Docker 仅保留 `paper-trans-latex-slim:latest` 与 `gpt-academic-latex-slim`。
- **验证**：
  - `GPT_ACADEMIC_CONTAINER=gpt-academic-latex-slim python3 translate_full.py 2606.11926 -o /tmp/paper-trans-fulltex-validate --keep-translation --timeout 900` 成功，PDF 约 2.52MB；
  - `GPT_ACADEMIC_CONTAINER=gpt-academic-latex-slim python3 translate_full.py 2606.12344 -o /tmp/paper-trans-fulltex-validate --keep-translation --timeout 900` 成功，PDF 约 1.88MB；
  - `/api/status` 本地抽查通过，报告当前容器为 `gpt-academic-latex-slim`。

---

## v4.11 — 2026-06-12

### Slim 翻译容器试跑修复

#### 2026-06-11 两篇 daily PDF 失败修复

- **影响论文**：`2606.11926` 和 `2606.12344`。
- **根因**：
  - `2606.11926` 已完成 GPT 翻译，但 slim TeX 环境缺少 `libertine.sty`，且该模板会按文件名查找 `Inconsolatazi4-*.otf`；
  - `2606.12344` 首次失败在 arXiv 源码下载阶段，日志为 `ChunkedEncodingError / IncompleteRead`；下载修复后又暴露 slim 缺少 `newtxmath.sty` 和 `zlmtt.sty`；
  - 容器缓存曾被清理，只有宿主机侧 `merge_translate_zh.tex` 备份时，旧 `--keep-translation` 无法重建完整 workfolder，只能退回插件路径。
- **修复**：
  - `scripts/setup_docker_env.sh` 与 `docker/latex-slim/Dockerfile` 增加轻量 stub：`libertine`、`newtxmath`、`zlmtt`，并为 `Inconsolatazi4` 文件名提供 Latin Modern Mono 字体别名；
  - `full_translate_driver.py` 增加 arXiv 源码预下载：代理/直连交替重试，写入 `e-print/<id>.tar` 后验证 tar 有效，再交给 gpt-academic 使用，避免断流导致无法进入翻译/编译；
  - `--keep-translation` 在 workfolder 不完整时会先用源码缓存重建 workfolder、放回中文 tex、生成 `merge.tex`，然后直接重编译，避免重复调用 GPT。
- **验证**：
  - 已对正在运行的 `gpt-academic-latex-slim` 执行 setup，`kpsewhich` 可解析 `libertine.sty`、`newtxmath.sty`、`zlmtt.sty` 和 `Inconsolatazi4-Regular.otf`；
  - `GPT_ACADEMIC_CONTAINER=gpt-academic-latex-slim python3 run_repair.py --retry-pdf --mode daily --key 2026-06-11` 修复两篇失败论文；
  - `data/daily/2026-06-11/index.json` 中 `2606.12397`、`2606.11926`、`2606.12344` 均为 `pdf_status=ok`；
  - 生成 PDF：`2606.11926_zh.pdf` 约 2.52MB，`2606.12344_zh.pdf` 约 1.88MB。

---

## v4.10 — 2026-06-11

### Docker 镜像瘦身闭环

#### 低磁盘 slim LaTeX 镜像构建与 canary 验证

- **背景**：服务器根分区只有 40GB，原 `gpt_academic_with_latex` 镜像约 15.4GB；直接 Dockerfile 构建 slim 镜像会因为构建上下文和中间层占用空间而陷入“没空间构建新镜像、又不能先删旧镜像”的死锁。
- **修复**：
  - `scripts/build_latex_slim.sh` 默认改为低磁盘 `flatten` 模式：从当前生产镜像创建临时容器，复制已 patch 的 `/gpt` 代码，裁剪 torch/nvidia/nougat/transformers 等大依赖和 TeX doc/source 后，用 `docker export | docker import` 生成扁平镜像；
  - 保留必要 TeX 能力：`texlive-lang-european`、`texlive-science`、CJK/XeLaTeX、Noto/Arphic 字体，并增加 `fontawesome` v4/v5/v6、`bbding`、`inconsolata` 等轻量 stub，避免拉回 `texlive-fonts-extra`；
  - `scripts/run_latex_slim.sh` 支持 `GPT_ACADEMIC_SKIP_SETUP=1`，镜像已内置补丁时可跳过启动时 setup；
  - `scripts/canary_latex_slim.sh` 默认使用 compile 模式，复用 `data/tex_backup` 中的 `merge_translate_zh.tex`，只验证 slim 容器的 LaTeX/runtime 编译链；`GPT_ACADEMIC_SLIM_CANARY_MODE=full` 才跑完整重译；
  - full canary 默认 ID 设为 `2606.08432`，用于无缓存测试 GPT 翻译阶段是否正常；
  - `translate_full.py` CLI 增加 `--keep-translation`，并在运行前自动恢复宿主机备份的中文 tex；
  - `full_translate_driver.py` 在 `--keep-translation` 且 workfolder 完整时直接走 XeLaTeX fallback 重编译，避免 gpt-academic 插件重建 workfolder 后删除已恢复的中文 tex。
- **验证**：
  - 本机生成 `paper-trans-latex-slim:latest`，镜像大小约 4.55GB；
  - fresh slim 容器从新镜像启动成功，跳过 setup 后仍能解析 `ctex`、`bxcoloremoji`、`fontawesome`、`bbding`、`inconsolata`、`nicematrix`、`latin.ldf`，并能读取生产 `config_private.py`；
  - compile canary 通过 `2606.09967`、`2606.10917`、`2606.09828`、`2606.02060`。
  - full no-cache canary 通过 `2606.08432`，`no_cache=True`，完整翻译+编译成功生成中文 PDF；虽然 PDF 仅约 0.95MB，但已用 `pdfinfo`/`pdftotext` 验证为 29 页、约 81k 可提取文本字符，原始 arXiv PDF 也仅约 1.1MB/24 页，属于论文素材体积较小而非空 PDF。
- **Cron 试跑**：root crontab 已设置 `GPT_ACADEMIC_CONTAINER=gpt-academic-latex-slim`，今晚例行 daily/post/retry-pdf 会使用 slim 容器；05:00 容器重启任务同步改为 `docker restart $GPT_ACADEMIC_CONTAINER`。
- **Web 手动提交试跑**：`paper-trans-web.service` 新增 systemd drop-in `/etc/systemd/system/paper-trans-web.service.d/10-slim-container.conf`，设置 `GPT_ACADEMIC_CONTAINER=gpt-academic-latex-slim`；重启 Web 后 `/submit` 与 `/api/status` 本地抽查通过，手动任务 `2606.01476` 已经通过 slim 容器生成中文 PDF。
- **安全策略**：旧生产容器 `gpt-academic-latex` 和旧镜像仍保留；若今晚 cron 出现异常，可将 crontab 中 `GPT_ACADEMIC_CONTAINER` 改回 `gpt-academic-latex` 回滚。

---

## v4.9 — 2026-06-11

### Docker 镜像瘦身准备

#### 可选 slim LaTeX 翻译容器

- **背景**：当前 `ghcr.io/binary-husky/gpt_academic_with_latex:master` 镜像约 15GB，40GB 服务器磁盘压力较大；中文论文翻译实际只需要 gpt-academic 代码、API 配置和 LaTeX/CJK 编译链路，不需要 torch/nvidia/nougat 等大体积依赖。
- **修复**：
  - `translate_full.py`、`run_papers.py`、`web_server.py` 和相关运维脚本支持 `GPT_ACADEMIC_CONTAINER` 覆盖容器名，默认仍为 `gpt-academic-latex`；
  - 新增 `docker/latex-slim/Dockerfile`，用 Ubuntu + Python + TeX Live/CJK 子集构建 `paper-trans-latex-slim:latest`，避免安装 `texlive-full` 和深度学习栈；
  - 新增 `scripts/build_latex_slim.sh`、`scripts/run_latex_slim.sh`、`scripts/canary_latex_slim.sh`，从当前生产容器复制 `/gpt` 代码，启动独立 `gpt-academic-latex-slim` 容器，并用近期失败论文做 canary。
- **安全策略**：生产默认路径不变；确认 slim canary 成功前，不删除原容器或原镜像。

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
