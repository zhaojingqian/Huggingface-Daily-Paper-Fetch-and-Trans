# Paper Hub

Paper Hub 自动抓取 Hugging Face 热门 AI 论文，翻译标题、摘要和核心信息，并按需生成全文中文 PDF。项目同时提供公网 Web 页面、手动提交、收藏夹、全局搜索和系统状态监控。

- 线上入口：https://zzzgry.top/paper/
- 本地服务：http://127.0.0.1:18080
- systemd 服务：`paper-trans-web.service`
- 主数据源：`data/papers/`

---

## 当前能力

<!-- translation chunk policy: v64-name-catalogs -->

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

### 翻译运行时边界（2026-08-15）

当前 chunk 策略版本为 `v64-name-catalogs`；后文旧版本号仅保留为变更历史。

Docker 仍是全文翻译的必要运行时边界：宿主机没有 TeX 编译器或
gpt-academic 源码，容器同时提供字体、TeX 和受限进程组。当前 slim 镜像约
6.75GB；删除它不会消除依赖，只会把约 3.2GB TeX、818MB 字体和 Python 运行时
搬回宿主机，因此只做镜像/缓存瘦身，不直接删容器。

翻译链路按职责收敛为三层：`full_translate_driver.py`（813 行）只负责单篇
生命周期、源码缓存、请求生命周期和结果诊断；`paperhub/translation_runtime.py`
负责 gpt-academic splitter、响应校验、失败槽重试和归档安全适配；
`paperhub/latex_pipeline.py` 同时拥有容器内 PDF、翻译质量、编译健康门禁，以及
TeX 修补、BibTeX/XeLaTeX/LuaLaTeX fallback；`paperhub/translation_policy.py`
统一 chunk 与并发策略。编译 patch 会同时绑定 `latex_toolbox` 和
`latex_actions` 的实际引用，并只从用户 `.tex` 源恢复被翻译丢失的自定义宏；
`.sty/.cls` 包实现不被复制到导言区，避免算法等环境的延迟宏重复定义。

首轮请求由 `PAPER_TRANS_LLM_WORKERS` 控制，默认 50；普通正文上限 2400 字符，
结构/引用密集片段自动降为 1900/1500；失败槽使用最多 16 路的有界并发重试。

---

## 快速命令

生产、cron、测试和修复统一从 `/usr/local/bin/workspace-ctl` 进入。宿主运行时
由 `/root/workspace/.env` 的 `SERVER_PYTHON` 唯一定义，PaperHub 不再在脚本、
cron 或 systemd 中硬编码 Python 路径。项目密钥位于权限为 `600` 的
`/root/workspace/.env.d/paper.env` 与 `gpt-academic.env`；生成的第三方 Python
配置位于 `/root/.local/share/paper-trans/runtime/config_private.py`，禁止手改。

### 抓取与翻译

```bash
# daily
workspace-ctl paper daily
workspace-ctl paper daily 2026-06-05
workspace-ctl paper daily 2026-06-05 --no-full

# weekly
workspace-ctl paper weekly
workspace-ctl paper weekly 2026-W22
workspace-ctl paper weekly 2026-W22 --no-full

# monthly
workspace-ctl paper monthly
workspace-ctl paper monthly 2026-05
workspace-ctl paper monthly 2026-05 --no-full

# topic subscription
workspace-ctl paper topic opd
workspace-ctl paper topic opd --no-full
workspace-ctl paper topic --all

# 单篇全文 PDF，输出到统一 paper store
workspace-ctl paper translate 2605.21573 -o /root/workspace/apps/paper-trans/data/papers
```

主题订阅使用 `.env.d/paper.env` 中的 `TOPIC_LLM_API_KEY`、`TOPIC_LLM_BASE_URL`、`TOPIC_LLM_MODEL` 生成检索词，`TOPIC_ADMIN_TOKEN` 用于保护主题管理和手动提交动作；密钥文件禁止提交。主题检索默认限定 `cs.AI`、`cs.LG`、`cs.CL`、`cs.CV`、`cs.RO`、`cs.IR`、`stat.ML`，排序权重为相关性 45%、新鲜度 30%、HF vote 25%。检索词生成默认把用户输入解释为 AI/ML/CS 论文主题，要求 must 高精度、should 多元覆盖同义词/方法/任务/相邻概念，非 AI/ML/CS 常见含义进入 negative；代码侧还会去重、限制数量并过滤和 negative 冲突的召回词。主题 profile 支持可选 `display_name` 备注名，列表和详情页优先展示备注名，但检索、slug 和缓存仍使用原始 query。同一 topic 已推送过的 arXiv ID 默认不重复推；paper store 会全站复用中文摘要和全文 PDF 缓存，避免重复翻译或重复生成 PDF。

### 修复与重试

生产、cron 和手工操作都走同一个 `workspace-ctl` 入口；下面的旧脚本只保留为
兼容调用，不再作为独立运行方式维护。

```bash
# 修复 title_zh / summary_zh 缺失
workspace-ctl paper repair
workspace-ctl paper repair --mode daily --days 2
workspace-ctl paper repair --mode weekly --key 2026-W22
workspace-ctl paper repair --mode topic --topic opd --days 7

# 补抓缺失 index.json 的日期/周期
workspace-ctl paper repair --refetch --mode daily --days 3

# 补翻译 + 补索引
workspace-ctl paper repair --post --mode daily --days 2

# 对 pdf_status=failed 的条目重试全文 PDF
workspace-ctl paper repair --retry-pdf --mode weekly --key 2026-W22
workspace-ctl paper repair --retry-pdf --mode daily --days 7
workspace-ctl paper repair --retry-pdf --mode manual --days 7
workspace-ctl paper repair --retry-pdf --mode topic --topic opd --days 7
workspace-ctl paper repair --retry-pdf --mode topic --key opd/2026-07-05

# 全项目数据一致性与失败分类报告
python3 scripts/audit_project.py
python3 scripts/audit_project.py --json
python3 scripts/summarize_failures.py
python3 scripts/summarize_failures.py --json

# 全量中文 TeX 的英文残留、词频、环境与 chunk 分布
python3 scripts/analyze_translation_chunks.py \
  --tex-dir data/tex_backup \
  --tex-dir data/tex_backup_failed

# 按共享质量门禁查找历史漏译；确认后写入重译队列
python3 scripts/queue_quality_repairs.py --json
python3 scripts/queue_quality_repairs.py --apply --json
```

`run_repair.py` 的 `--key` 是严格精确操作：`repair`、`--post` 和
daily/weekly/monthly 的 `--refetch` 都只处理指定 key，不会再隐式扩展为近
N 天扫描。repair/refetch/retry 结束后会重新核对持久化的元数据、中文摘要和
PDF 实体，统一输出 attempted/succeeded/failed 与 `residual_ids`；仍有真实
残留时进程返回非零，cron/skill 不会再把“命令跑完”误当成“修复完成”。
`manual` 已是正式内容模式：默认 repair/retry 与全模式审计都会覆盖
daily、weekly、monthly、manual 和递归 topic，不再依赖 Web 手动队列单独
兜底。

已安装的 Codex skill 为 `$paper-trans-repair`（本机路径
`/root/.codex/skills/paper-trans-repair`）。后续可直接要求“用
`$paper-trans-repair` 修复近两周论文并沉淀 patch”；skill 会覆盖
daily、weekly、monthly、manual 和递归 topic 索引，串行修复 PDF，
按 taxonomy 登记通用 patch，并在更新文档、测试和 push 前执行审计。
skill 自带的近窗审计命令为：

```bash
/root/.pyenv/versions/3.13.12/bin/python3 \
  /root/.codex/skills/paper-trans-repair/scripts/recent_audit.py \
  --repo /root/workspace/apps/paper-trans --days 14 --json
```

`retry-pdf` 会优先复用已有的翻译 tex 缓存；宿主机成功备份和容器内 `merge_translate_zh.tex` 都可作为缓存来源。如果只有 tex 备份、容器 workfolder 已被清理，会先从有效 arXiv 源码缓存重建 workfolder，再只重跑编译。失败诊断会同时写入便于阅读的 `logs/pdf_errors/<id>.log` 和便于程序处理的 `<id>.json`，稳定字段包括 `phase`、`category`、`family`、`retry_strategy`、`repair_action` 和 `evidence`。`reuse_translation` 表示保留中文 tex、定向修补后重编译；只有明确分类为 `retry_translation` 才会清缓存再次调用 GPT，未知驱动异常也不会自动浪费一次全文重译。若没有翻译 tex 且源码下载断流，驱动会先预下载并校验 `e-print/<id>.tar`，再交给 gpt-academic 翻译/编译。五个内容模式的 retry 入口都会同步 paper store 与所有引用索引：已有且通过门禁的 PDF 可回写 `ok`；slim index 标记 `ok` 但 PDF 实体缺失或无效时自动降级进入重试。

失败分类由 `failure_taxonomy.py` 统一维护，当前区分翻译、质量、编译和基础设施故障；`Errno 28` 即使被旧 sidecar 记成插件异常，读取时也会按原始证据重新归入 `infrastructure.disk_full`，而“响应仍漏译或结构校验失败”统一归入 `quality.translation_chunk_invalid`。`paperhub/patch_catalog.py` 为每个结构化类别登记 patch、来源和策略。定位先运行 `scripts/repair_snapshot.py` 获取磁盘、Docker、活跃任务、索引状态和失败分类；再按需运行 `scripts/summarize_failures.py`、近窗审计或全项目严格审计，避免每次都加载重型扫描。

topic 修复复用 daily 的 repair 语义：摘要/标题缺失时补写统一 paper store，`pdf_status=failed` 时复用同一套分类式 PDF retry 逻辑。paper store 的完整翻译缓存要求中文标题和中文总结同时有效；只残留标题的历史条目会重新抓取元数据并补译，同时保留原 `pdf_status`。topic 没有缺 index 补抓模式；新增订阅结果仍由 `run_topic.py --all` 负责生成。

全文翻译驱动会在发布 PDF 前做三类门禁：一是检查
`merge_translate_zh.tex` 的普通正文翻译覆盖率，避免 splitter 漏译导致
“大半 PDF 仍是英文”；二是检查 LaTeX log，拒绝 undefined command、
undefined citation/reference；三是校验 PDF 实体。paper store、Web 和全库
审计共享轻量 PDF 判定：文件必须大于 10 KiB，并同时具有 `%PDF-` 文件头和
尾部 `%%EOF`，不能再只靠文件大小把中断复制的残缺 PDF 当成 `ok`。fallback
编译会自动修补常见翻译副作用，例如自定义零参数宏与中文/中文标点粘连、
误生成的 `\textWord` 命令、唯一可推断的 label/ref 不一致、inline `\verb`
分隔符与正则内容冲突、坏 `.aux`、旧式 FontAwesome 图标、XeLaTeX 下缺失的
`\DeclareUnicodeCharacter`、algorithm2e 关键字被翻译、不安全 citation key，
以及 XeLaTeX segfault 时的 LuaLaTeX fallback。

splitter 优化基于 gpt-academic 原始 `LatexPaperSplit`：先保留上游 mask 的
`PRESERVE/TRANSFORM` 结果，再对 preserve 节点做二次安全拆分。普通正文行
会重新送翻译；`tabular/tabularx/longtable/array` 只翻译单元格文本，保留
`&` 和行尾 `\\`；`algorithmic` 只翻译命令后的自然语言参数。二次拆分后
会再次套用类似上游 `post_process` 的语义收口，过短、命令占比过高或
空白/分隔符类 chunk 会降级回 preserve。相邻正文及其间纯空白会合并为
上下文更完整、普通正文最长 2400 字符的请求；结构/引用密集片段按共享策略
降为 1900/1500，超长正文继续按句子边界拆分，作者、单位、邮箱、宏定义和纯环境配置不送模型。当前 **chunk v64-name-catalogs** 还会剥离
gpt-academic 拼在 fragment 前的英文翻译指令，只用真实论文片段检查漏译；
inline code、URL 和 TeX 注释也不参与正文覆盖率。纯 `[key=value, ...]` 配置
片段、`\setlist`/`\hypersetup` 等纯布局配置命令与 citation-heavy 的模型/数据集名称目录保持结构、不送模型，避免误判为
英文正文。`tcolorbox`、`custombox`、
`casebox`、`examplebox`、`mdframed` 不再按环境名整体保护，而是逐实例读取
opening option 和首段内容：只有明确标记为 prompt、trace、trajectory、
benchmark example、user query 或 source data 的实例保留原文，普通定理、
说明和结论框仍继续翻译。每个返回 chunk 在合并前同时经过结构签名与语言
门禁：不得新增/丢失关键 LaTeX 结构或 citation；中文响应中若仍有满足“至少
4 个英文词、含语法连接词、与中文同段”的自然语言 clause，即判为未翻译并
走单路重试。对于上游在 citation/ref 两侧切出的短英文接缝（例如
`\cite{...}, including`），只在相邻节点确为可翻译正文且合并后不超过
策略上限内吸收到同一 chunk；`\section`、环境边界和任意其他命令仍保持
保护。TeX ``...'' 引用的原始输入示例以及命令密集的 TikZ path 片段属于
论文数据/绘图代码，不触发混合语言重试。这能拦住“整体中文、局部一句英文”
的漏译，同时避免把应保留内容误报为正文。splitter 版本变化会
自动丢弃旧 `temp.pkl`，避免旧翻译缓存与新节点结构错位。

`paperhub.translation_quality` 是生产发布、`audit_project.py --strict`、
英文分布分析和 `queue_quality_repairs.py` 共用的唯一质量定义；这些入口
不会再各自维护不同阈值或环境排除表。队列默认只检查可回溯的 TeX；需要
覆盖没有 TeX 备份的有效 PDF 时，显式执行只读预览：

```bash
python3 scripts/queue_quality_repairs.py --scan-pdf-text --json
```

PDF 扫描复用 `audit_project.py` 的有界文本提取缓存，并把连续英文正文、
局部英文正文和模型拒绝回显稳定归入
`quality.pdf_sustained_untranslated`、`quality.pdf_partial_untranslated`
和 `quality.translation_refusal`，三类都使用 `retry_translation`。
确认预览后用完整命令
`python3 scripts/queue_quality_repairs.py --scan-pdf-text --apply --json`
写入，才会把所有引用索引和 paper store 标成 `failed`，持久化
`pdf_quality_tainted` 与对应结构化 sidecar；TeX 异常仍使用
`quality.untranslated_prose / retry_translation`。apply 会先验证全部索引
和 category，发现坏索引时不做部分写入。即使旧 PDF 在
磁盘上结构完整，卡片、wrapper 和直接 PDF 路由也会继续封锁；只有新生成的
PDF 同时通过翻译、编译和实体门禁后才清除 taint，后续编译诊断不能意外把
旧英文 PDF 恢复成 `ok`。

LaTeX 全文翻译默认使用 50 路首轮并发。首轮不再因 citation/ref 密度预先膨胀请求数；只有结构签名、引用多重集或花括号门禁真正失败的 slot 才自适应拆成最多约 480 字符，并使用最多 16 路的有界并发重试。遇到 429、空响应或漏译也只补对应 slot；补偿后仍失败会拒绝写入 `temp.pkl`。若完整 TeX 已有较高中文覆盖、仅质量门禁命中少量行，末端修复器最多定向重译 12 行，并仅提交质量分数严格改善且 LaTeX 结构不变的结果，再重编译 PDF，避免重跑整篇数百个 chunk。

全文翻译默认使用 `deepseek-v4-flash-0731`；单次覆盖可用
`PAPER_TRANS_LLM_MODEL=<model> workspace-ctl paper translate ...`。宿主机只会把
`PAPER_TRANS_LLM_MODEL`、worker/retry 等明确白名单变量传入容器，不会透传其他
环境或密钥。`insufficient_user_quota`、余额/额度不足会独立归类为
`translate.api_quota / manual_review`，停止盲目重试；需要充值或显式切换到同一
凭据已授权、并经过翻译质量验证的模型后再恢复队列。

`latex_translation_filters.py` 统一维护 LaTeX 过滤策略，供 splitter、翻译覆盖率门禁、merge 前 `fix_content` 清理和 fallback 重编译共同使用。对超长普通正文行，splitter 会按句子边界继续拆分，避免长段 cite 密集内容被模型整体回显成英文。CLI/GUI、trace、trajectory、prompt、code、listing、verbatim 等命名特征的自定义环境会被动态识别为硬保护环境；但 fallback 只会从原文恢复真正的 verbatim/listing/trace 类环境，不会把 table/figure/equation 这类普通保护块恢复成英文。

过滤策略可通过环境变量扩展：`PAPER_TRANS_EXTRA_HARD_ENVS` 增加需要硬保护的环境名，`PAPER_TRANS_EXTRA_SOFT_ENVS` 增加可拆出自然语言继续翻译的环境名，`PAPER_TRANS_EXTRA_RESTORE_ENVS` 增加 fallback 中可从原文恢复的环境名，`PAPER_TRANS_EXTRA_LLM_ARTIFACT_PATTERNS` 按行增加需要清理的模型残留正则。

fallback 编译还会处理部分模板兼容问题：为旧模板补 `fontawesome5` legacy alias（含 `\faDatabase`、`\faEnvelopeO`、`\faEnvelope`、`\faGem` 等旧命令），为声明了 `CJKutf8` 但 XeLaTeX 未暴露环境的旧论文补 `CJK/CJK*` no-op guard，将已定义的 `\Imat` 被误写成 `\I` 的数学别名恢复，禁用 XeLaTeX 下容易报错的 `microtype` 特性，为可选参数列表补 `enumitem`，补充 inputenc/listing 场景常见的 `\DeclareUnicodeCharacter` no-op 和 `\inputencodingname` 兼容，为缺少 `fontspec` 的 CIDR/ACM 或 fontspec 风格模板补 `\setmainfont`、`\setsansfont`、`\setmonofont`、`\newfontfamily` no-op，并在 CIDR/ACM 文档结束前重置 `\baselinestretch` guard。从 tex 预生成 BibTeX 中间文件，guard 本地 class/style/source 中的 pdfTeX-only primitive，并在本地 class/style 硬编码不可用 `NVIDIASans_*` 或其他 T1 字体默认值时回退到容器已有字体。如果 arXiv 源码包只提供 `.bbl` 而没有对应 `.bib`，fallback 会复用已有且包含 `\bibitem` 的 `.bbl`，避免 BibTeX 生成空参考文献导致 undefined citation。若日志里先看到半截小 PDF，再看到 `.aux` 的 `File ended while scanning use of \citation`，需要优先查前一轮真正的 LaTeX/xdvipdfmx 崩溃原因。`Label(s) may have changed` 这类 rerun 提示不是发布拦截条件；真正会导致 `?` 的 undefined citation/reference 仍是硬失败。

宿主机侧 `translate_full.py` 使用非阻塞方式读取容器输出；当容器内长时间
没有换行输出时，外层 timeout 仍会按时收口。每篇驱动在独立 session 中
运行，由 subreaper supervisor 回收孤儿孙进程；超时或 Web 人工终止会按
“精确驱动 argv + arXiv ID + PID starttime”递归终止同篇进程树，不误杀其他
任务，也不把 zombie 留给容器 PID 1。短 Docker 控制命令默认 30 秒超时，
可用 `PAPER_TRANS_DOCKER_CONTROL_TIMEOUT` 调整（上限 600 秒），避免在持有
全局锁时无限卡住。所有 daily、weekly、monthly、manual、topic 和 Web 入口
最终都在这里竞争 `locks/full-translation.lock`，共享容器同一时间只运行
一篇全文任务；等待上限默认是任务 timeout 加 300 秒，可用
`PAPER_TRANS_GLOBAL_LOCK_TIMEOUT` 覆盖。

上游编译和 fallback 重编译都显式增加 `-no-shell-escape`，并在子进程环境中固定 `shell_escape=0`、`openin_any=p`、`openout_any=p`。论文 TeX 因而只能执行受限文件 I/O，不能借 shell escape 执行容器命令；这一约束同时覆盖 XeLaTeX、LuaLaTeX 和 pdfLaTeX 路径。

`logs/pdf_errors/<arxiv_id>.log` 只保留最近一次失败诊断；同篇 PDF 后续成功生成后，`translate_full.py` 会自动清理旧失败日志。成功生成 PDF 后才会覆盖 `data/tex_backup/<id>_merge_translate_zh.tex`；失败现场会另存到 `data/tex_backup_failed/`，避免坏 tex 覆盖可用缓存。同篇 PDF 成功后，对应的失败现场 tex 也会自动清理。如果日志中出现 `No space left on device`，先用 `df -h /` 和 `docker exec ${GPT_ACADEMIC_CONTAINER:-gpt-academic-latex-slim} df -h /gpt /` 确认宿主机根分区与容器 overlay 空间；清理旧编辑器 server 缓存或 gpt-academic 可再生缓存后，再重跑 `retry-pdf`。如果编译超大图片/重资源论文时发生 `xdvipdfmx` 进程异常退出或超时（可能由 OOM 强杀导致），需确认独立容器已启用 `--memory-swappiness=60` 以允许向 Swap 换页。

`scripts/cleanup_docker_cache.sh` 与 `scripts/restart_translation_container.sh` 复用全文翻译全局锁；锁繁忙时维护任务直接记录 `SKIP`，不会删除活跃 workfolder 或重启正在工作的容器。Docker 缓存每天清理：常规保留 3 天，磁盘达到 90% 或可用空间低于 2GB 时切换为保留 1 天。`arxiv_cache` 按论文目录回收，`default_user/shared`、`downloadzone` 和 `admin` 按文件年龄回收，避免一个近期文件阻止整个目录内的旧 zip 被删除。清理后仍达到 95% 或可用空间仍低于 2GB 时任务返回非零，并通过服务器已有 Gmail SMTP 配置发送告警；高水位自动恢复也会发送通知。可用 `PAPER_TRANS_CACHE_RETENTION_DAYS`、`PAPER_TRANS_EMERGENCY_RETENTION_DAYS`、`PAPER_TRANS_DISK_HIGH_WATERMARK`、`PAPER_TRANS_DISK_CRITICAL_WATERMARK` 和 `PAPER_TRANS_MIN_FREE_MB` 调整阈值。

`scripts/weekly_cleanup.sh` 通过
`scripts/cleanup_orphan_artifacts.py --apply` 清理孤立 PDF、失败诊断
sidecar 和 failed TeX；helper 会递归扫描 daily、weekly、monthly、manual
和 topic 的全部 `index.json`。topic 使用
`data/topic/<slug>/<date>/index.json` 两层目录，维护清理脚本时不能退回只
扫描一层 key，也不能遗漏 topic；新生成但索引尚未发布的孤立候选还会保留
3 天缓冲。真正删除前会按 `catalog(exclusive) -> paper(exclusive)` 顺序
加锁，在锁内重新扫描引用并再次检查文件年龄；因此不会删除正在发布、刚被
重新生成，或仍由任一索引引用的对象。默认直接运行 helper 只做 dry-run，
必须显式传 `--apply` 才删除。任一索引损坏、读取失败或锁超时时，本轮孤立
对象清理会 fail closed 并记为失败；pip、journal、日志、临时文件、孤立
对象、Cursor 或 pagecache 任一步失败，脚本会尝试完其余安全步骤后以非零
退出，cron 不会再记录“伪成功”。该任务安排在周日 08:00，晚于周日 02:00 的
weekly 抓取与全模式 repair。

### Web 服务

```bash
systemctl status paper-trans-web.service
systemctl restart paper-trans-web.service
tail -f /root/workspace/apps/paper-trans/logs/web.log
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

生产 Nginx 的 `/paper/papers/` 必须反代到 `web_server.py`，不能使用绕过应用
质量判定的静态 `alias` 直出目录；这样 `pdf_quality_tainted` 的 PDF 在 wrapper、
下载路由和直接 `/papers/<file>` 请求上使用同一封锁逻辑，同时保留健康 PDF 的
Range/`206` 响应。

`/api/paper/delete` 与 `/api/status/kill` 属于破坏性操作：只接受 POST，
并且必须配置并提交 `TOPIC_ADMIN_TOKEN`；未配置 token 时也不会放行。删除
请求会严格验证 mode、周期 key、arXiv ID 和最终真实路径，拒绝路径穿越。
删除某个索引引用后，只有确认全项目没有其他索引引用、且引用扫描没有读错
时才删除共享 PDF。`/search` 保持公开，但使用覆盖递归 topic 的全索引去重
快照；快照 TTL 为 20 秒，手动写入和删除会主动失效，避免每次按键都重读
全部 index 与 paper store。

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
├── full_translate_driver.py    # 容器内单篇生命周期与结果诊断（813 行）
├── latex_translation_filters.py # LaTeX 环境保护、质量过滤和 LLM 残留清理策略
├── failure_taxonomy.py         # 翻译/编译失败稳定分类与重试策略
├── web_server.py               # 单文件 HTTP Web 服务
├── paperhub/
│   ├── paths.py                 # 共享路径、paper store、容器默认名常量
│   ├── env_config.py            # 本地 .env 读取 helper
│   ├── modes.py                 # mode 限额、周期和 cron 语义
│   ├── runner.py                # daily/weekly/monthly 共享 CLI runner
│   ├── json_io.py               # 原子 JSON 读写
│   ├── patch_catalog.py         # 失败类别到通用 patch 的映射
│   ├── weekly_repair.py         # 周日 02:00 当前周串行修复 runner
│   ├── paper_store.py           # 统一 paper store JSON/PDF 读写 helper
│   ├── topic_store.py           # topic profile、seen 和 index 读写 helper
│   ├── audit.py                 # 全项目索引/store/PDF 一致性审计
│   ├── failure_reports.py       # 结构化与历史失败日志聚合
│   ├── translation_policy.py    # chunk 上限与并发/重试策略
│   ├── translation_runtime.py   # gpt-academic 适配与响应调度
│   └── latex_pipeline.py        # TeX 修补、编译与健康门禁
├── tests/
│   ├── test_web_server_contract.py
│   ├── test_latex_translation_filters.py
│   ├── test_paper_store.py
│   ├── test_paths.py
│   ├── test_weekly_repair.py
│   └── test_repair_refetch.py
├── scripts/
│   ├── audit_project.py
│   ├── repair_weekly_current.py
│   ├── summarize_failures.py
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
/root/.pyenv/versions/3.13.12/bin/python3 -m unittest discover -s tests -v
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
- mode 规格、共享 runner、原子 JSON 写入、全项目审计与失败分类保持稳定。
- 缓存编译失败默认保留中文 tex，只有明确的翻译阶段诊断才允许再次调用 GPT。
- `run_repair.py --refetch/--post` 对 daily/weekly/monthly 当前周期的跳过边界保持稳定：只在首次 cron 触发时间未到时跳过，触发后允许补抓临时网络失败的周期。
- `run_repair.py --key` 保持精确范围，真实残留会进入统计并令 CLI 非零退出。
- 全文翻译、缓存清理和容器重启共享同一把跨入口锁，维护任务忙时只跳过。
- production/audit/queue 共用同一翻译质量谓词；实例级 prompt/example 保护
  不会把普通 box 正文排除在翻译之外。
- quality taint 会阻断旧 PDF 的 cache hit 与 Web 直出，只有新 PDF 通过全部
  门禁后清除；PDF 实体同时校验 header 与 EOF。
- `/paper/papers/` 的 Nginx 反代不得绕过应用的 taint、文件名与完整性校验；
  健康 PDF 仍支持 Range/`206`。
- 周日当前周 runner 覆盖五模式、按 arXiv ID 去重并同步所有引用；任一
  residual 或索引错误必须保留 `partial`。
- 破坏性 Web API 必须带管理 token，删除路径受限且共享 PDF 不会被单索引误删；搜索快照覆盖递归 topic 并按 TTL 复用。
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
| Python | `${SERVER_PYTHON}`（当前 `/root/.pyenv/versions/3.13.12/bin/python3`） |
| Web 端口 | `18080` |
| 绑定地址 | 默认 `127.0.0.1`，可用 `BIND_HOST` 覆盖 |
| 路径前缀 | `BASE_PATH=/paper` |
| Docker 容器 | 代码默认 `gpt-academic-latex-slim`，可用 `GPT_ACADEMIC_CONTAINER` 覆盖；当前生产使用 full-TeX slim |
| 网络代理 | `http://127.0.0.1:7890`，失败时部分请求会切直连 |
| Web 日志 | `logs/web.log` |

systemd unit：

```ini
[Service]
WorkingDirectory=/root/workspace/apps/paper-trans
Environment=BASE_PATH=/paper
ExecStart=/usr/local/bin/workspace-ctl paper serve
Restart=always
StandardOutput=append:/root/workspace/apps/paper-trans/logs/web.log
StandardError=append:/root/workspace/apps/paper-trans/logs/web.log
```

当前 Web 手动提交使用 full-TeX slim 容器，systemd drop-in：

```ini
# /etc/systemd/system/paper-trans-web.service.d/10-slim-container.conf
[Service]
Environment=GPT_ACADEMIC_CONTAINER=gpt-academic-latex-slim
```

如需切换容器，修改该 drop-in 后执行 `systemctl daemon-reload && systemctl restart paper-trans-web.service`。原 `gpt-academic-latex` 容器和 `ghcr.io/binary-husky/gpt_academic_with_latex:master` 镜像已在 2026-06-12 删除，当前不再保留本机 Docker 回滚副本。

---

## Cron 运维

不要手工复制多组 post/retry/cleanup cron。运行
`workspace-ctl paper cron-install` 安装单一 managed block：4 个抓取入口、恰好
1 个周日 02:00 当前周修复入口，以及 1 个每天 06:00 maintenance 入口。
`scripts/run_maintenance.py` 顺序协调缓存水位清理、空闲重启、全模式两日 post、
全模式七日 PDF retry；每月 28 日追加 60 日 monthly 修复，周日追加宿主清理。
各步骤独立报错并继续安全步骤，cron 不再承载业务分支。

`run_repair.py --post` 会先修复已有索引中的摘要，再补抓缺失或空 `index.json` 的周期。为避免提前抓取未到榜单生成时间的数据，当前周期只会在首次 cron 触发时间前被跳过：daily 为当天 23:00 前，weekly 为周日 02:00 前，monthly 为 28 日 02:00 前。触发时间之后如果遇到 Hugging Face 临时网络失败，后续 `--post` 会重新补抓该周期；显式给出 `--key` 时只检查该 key。repair/refetch/retry 任一阶段仍有持久化残留都会记录 ID 并返回非零。

每天 06:00 的统一 maintenance 会调用缓存清理、空闲重启和修复；周日同一入口再追加孤儿清理，不能退回裸
`rm -rf` 或 `docker restart`。生命周期脚本会非阻塞竞争
`locks/full-translation.lock`；翻译繁忙时跳过本轮维护。缓存常规保留 3 天，
高水位时保留 1 天；清理失败或磁盘仍危险时使用
`/root/scholar-citation-monitor/config.env` 中的 SMTP 配置发送 Gmail 告警。

周日 02:00 的 `scripts/repair_weekly_current.py` 与 weekly 抓取并行启动，但
会先等待 `weekly/<当前 ISO 周>/index.json` 出现，再等待抓取锁释放。随后
收集当前 ISO 周已经发布在 daily、weekly、monthly、manual、topic 五个模式
中的论文，按 arXiv ID 去重后串行修复摘要、翻译和 PDF，再把结果同步回全库
每一处引用索引；同一篇不会因为跨模式重复出现而重复调用模型。最长等待
3 小时，不会读取半成品索引或与 02:30 weekly 兜底抓取互相覆盖。任何索引
读写错误、无 sidecar 的 failed 状态或持久化 residual 都会令本轮
`status=partial` 并使入口非零退出。每次运行以 `runs` 追加失败类别、匹配
patch、五模式统计、同步数量和残留 ID 到
`logs/repair_history/weekly-<key>.json`。通用 patch 目录由
`paperhub/patch_catalog.py` 维护，具体实现集中在
`paperhub/translation_runtime.py`、`paperhub/latex_pipeline.py` 和
`latex_translation_filters.py`。
批处理若实时收到 `translate.api_quota`，会在当前论文写入诊断后把
`abort_reason` 传到最外层 coordinator，立即停止剩余索引、topic 和 mode，
不会继续让论文逐篇重复失败，并通过现有 SMTP 配置发送 Gmail 告警；
恢复额度后重新运行同一 retry 命令即可继续。

全文翻译当前使用 chunk v64-name-catalogs。正文按数学邻接、章节结构和自然句边界
整理，普通首轮请求上限 2400 字符，结构/引用密集片段自动降为 1900/1500。引用密度只作为失败诊断证据，不再把
所有段落预拆成 120/350 字符；结构门禁失败后只细分对应 slot。
上游若从 citation key 中间切断片段，会优先闭合引用再送模型。纯 TikZ path、
纯公式、HTTP endpoint 清单、脱离命令的 citation-key 参数、代码/提示源码、
作者元数据和严格的专名/引用目录不计入漏译正文；已翻译短标题
后的产品名清单也不会因专名保持英文而重复重试。响应仍必须通过命令和 citation
多重集校验，解释性英文正文不会被这些排除规则隐藏。自定义文本宏
（如 `\compactbullet{...}`）只移除命令 token、保留自然语言参数；与展示公式
相邻的 `\par` 短句也会进入翻译。响应花括号净平衡必须与原文一致；仅允许删除
一个位于响应末尾、且删除后命令/citation 签名完全一致的孤立 `}`，避免上游
合并器为补括号而恢复英文原文尾部。

索引发布和 paper store 更新统一使用
`paperhub.publication_lock`。锁顺序固定为“weekly coordinator → catalog →
按路径排序的 index → full-translation → 按 arXiv ID 排序的 paper”，批量
流程不得反向嵌套。普通 fetch/topic/Web/repair 写索引时持 catalog shared
与对应 per-index 锁；全库引用扫描持 catalog exclusive；摘要、PDF 状态和
quality taint 通过 per-paper 锁内字段合并，不能再用旧 JSON 整份覆盖。
`retry-pdf` 和 weekly 同步会在 index 锁内重新读取当前文件，只按 arXiv ID
合并本轮 `pdf_status`，并保留并发发布新增的论文、rank、生成时间和其他字段。
PDF 先复制到同目录临时文件，校验并 fsync 后才在 per-paper 锁内原子替换，
读取方不会看到半复制文件。`locks/*.lock` 是持久诊断文件；是否繁忙只以
`flock` 为准，不能凭文件存在与否判断或手动删除。

重复启动周修复时，后启动实例会返回 `status=already_running` 且退出码为
0，以免把正常的 cron 去重当成故障；这只表示主实例仍在处理，不表示本周已
修复完成。监控必须继续检查主实例最终写入的
`logs/repair_history/weekly-<key>.json`，只有最新状态为 `ok` 才能判定完成。

topic 订阅不走 `--refetch` 补索引，必须由 managed cron 中的 `run_topic.py --all` 生成每日结果；PDF retry 由全模式 maintenance 统一覆盖。如果 `/topic` 没有当天结果，检查 `crontab -l` 的 managed block、`logs/cron-topic.log` 和 `logs/maintenance.log`。

### full-TeX slim LaTeX 容器

当前生产翻译容器为 `gpt-academic-latex-slim`，镜像为 `paper-trans-latex-slim:latest`。它继续继承原 `gpt_academic_with_latex` 的完整 TeX/font 运行时，避免逐个补 TeX 包；同时删除 torch、nvidia、transformers、nougat、缓存、文档和源码等中文翻译不需要的大体积内容。

当前本机状态（2026-08-11）：

- 生产镜像 `paper-trans-latex-slim:latest`：6,745,966,319 bytes（Docker 显示 6.746GB，约 6.28GiB）；相较上一版 7,620,981,032 bytes 减少约 875MB。
- 当前仅保留并运行 `gpt-academic-latex-slim`：1 个镜像、1 个容器、0 build cache；原生产容器和上游 15.4GB 镜像均未保留。
- Docker 对象清空后仍发现约 22GB 孤立 overlay2。停 Docker/containerd 并清除确认无引用的孤儿层后再导入平衡裁剪镜像，当前根分区可用约 6.0GB。
- `/gpt/config_private.py` 从 `${XDG_DATA_HOME:-/root/.local/share}/paper-trans/runtime/config_private.py` 只读挂载；该文件由 scoped env 原子生成且保持 owner-only，`/gpt/gpt_log` 外置到 `${XDG_DATA_HOME:-/root/.local/share}/paper-trans/gpt-log`，重建镜像不再复制运行缓存或密钥。
- 镜像内 `/opt/paper-trans-runtime-ready` 表示依赖已就绪，正常启动直接跳过 setup；仅在缺包时运行带总超时和网络超时的 apt。
- compile canary 已通过：`2606.09967`、`2606.10917`、`2606.09828`、`2606.02060`。
- 新平衡裁剪 rootfs 在导出前通过上述 4 篇 canary；导入为生产镜像后又以 `2606.10917` 复验通过。
- full no-cache canary 已通过：`2606.08432`。
- 2026-06-12 复盘 2026-06-11 daily 失败项：`2606.11926`、`2606.12344` 已在 slim 容器下修复并恢复为 `pdf_status=ok`。
- full-TeX slim 切换后再次用 `2606.11926`、`2606.12344` 复用中文 tex 备份重编译验证通过，PDF 分别约 2.52MB 和 1.88MB。

默认 `GPT_ACADEMIC_SLIM_TEX_PROFILE=full`，保留完整 TeX/font 运行时。历史的激进裁剪仍可用 `GPT_ACADEMIC_SLIM_TEX_PROFILE=slim` 显式开启；该模式会继续依赖轻量 stub 覆盖常见装饰字体包：`fontawesome` v4/v5/v6、`bbding`、`inconsolata`、`libertine`、`newtxmath`、`zlmtt`，并为 `Inconsolatazi4-*.otf` 提供字体文件别名。新增 stub 时必须同时更新 `scripts/setup_docker_env.sh` 和 `docker/latex-slim/Dockerfile`。

```bash
# 默认使用低磁盘 flatten 模式：从当前生产镜像创建临时容器，保留 full TeX，裁剪大依赖后 docker export/import
./scripts/build_latex_slim.sh

# 只估算 rootfs 体积，不导入镜像
GPT_ACADEMIC_SLIM_DRY_RUN=1 ./scripts/build_latex_slim.sh

# 低磁盘切换时可先导出压缩 rootfs，校验归档后再替换旧镜像
GPT_ACADEMIC_SLIM_EXPORT_ARCHIVE=/tmp/paper-trans-fulltex-slim.tar.xz \
  GPT_ACADEMIC_SLIM_EXPORT_COMPRESSOR=xz \
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
