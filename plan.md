# Paper Hub 项目计划

## 目标

Paper Hub 是一个面向 AI 论文阅读和归档的自动化系统：抓取 Hugging Face 热门论文，翻译摘要，按需生成全文中文 PDF，并通过 Web 站点提供浏览、搜索、收藏、手动提交和系统状态监控。

当前生产入口：

- 线上：https://zzzgry.top/paper/
- 服务：`paper-trans-web.service`
- 主数据：`data/papers/`

---

## 已完成能力

| 模块 | 状态 | 当前入口 |
|---|---:|---|
| Daily Top 3 | 完成 | `run_daily.py` |
| Weekly Top 10 | 完成 | `run_weekly.py` |
| Monthly Top 10 | 完成 | `run_monthly.py` |
| 摘要翻译 | 完成 | `translate_arxiv.py` |
| 全文 PDF 翻译 | 完成 | `translate_full.py` + `full_translate_driver.py` + `gpt-academic-latex-slim` |
| 统一 paper store | 完成 | `data/papers/<id>.json` / `<id>_zh.pdf` |
| slim index | 完成 | `data/{daily,weekly,monthly,manual}/<key>/index.json` |
| Web 页面 | 完成 | `web_server.py` |
| 手动提交 | 完成 | `/submit` |
| 收藏夹 | 完成 | `/bookmarks` |
| 全局搜索 | 完成 | `/search` |
| 系统状态 | 完成 | `/status` |
| PDF wrapper | 完成 | `/view/<arxiv_id>` |
| 行为合约测试 | 完成 | `tests/test_web_server_contract.py` |
| 论文修复 skill | 完成 | `$paper-trans-repair` |
| 统一翻译质量门禁 | 完成 | `paperhub.translation_quality` |
| 当前周五模式 repair | 完成 | `scripts/repair_weekly_current.py` |

---

## 当前架构

```text
HF papers page
    ↓
fetch_hf.py
    ↓
run_daily.py / run_weekly.py / run_monthly.py
    ↓
translate_arxiv.py
    ↓
data/papers/<arxiv_id>.json
    ↓
translate_full.py
    ↓ locks/full-translation.lock
    ↓ serialized docker exec
full_translate_driver.py (813 lines: lifecycle + diagnostics)
    ├─ translation_runtime.py (gpt-academic adapters + bounded scheduler)
    └─ latex_pipeline.py (TeX repair + publication gates + compile fallback)
    ↓ chunk v45 policy + structural guards + shared quality gate
    ↓
data/papers/<arxiv_id>_zh.pdf
    ↓ PDF header + EOF gate / quality taint
    ↓
web_server.py
    ↓
https://zzzgry.top/paper/
```

### Web 数据流

```text
slim index
    + paper store JSON
    + PDF existence check
    ↓
enrich_paper_entry()
    ↓
paper_card() / detail page / bookmark page
```

`web_server.py` 目前保持单文件部署，但内部已经收敛出以下 helper：

- `paper_pdf_state()`：统一 PDF 状态判断。
- `enrich_paper_entry()`：统一元数据合并。
- `render_paper_actions()`：统一按钮链接生成。
- `h_text()` / `h_attr()` / `js_str()`：统一输出转义。
- `_get_search_snapshot()`：20 秒 TTL 的递归全索引搜索快照，按 arXiv ID 去重。
- `_validated_delete_location()` / `_index_reference_labels()`：限制删除路径并保护跨 mode 共享 PDF。

### 翻译容器

当前生产容器为 `gpt-academic-latex-slim`，镜像为 `paper-trans-latex-slim:latest`。该镜像使用平衡裁剪：保留完整 TeX/font 运行时，只删除中文翻译不需要的 ML/runtime/cache/doc/source 负载；实测 6,745,966,319 bytes（约 6.28GiB）。

2026-08-11 已完成新一轮候选 canary、xz 导出校验、孤立 overlay2 清理、导入和生产复验；镜像相较上一版减少约 875MB。本机仅保留 1 个生产镜像和 1 个容器，运行缓存外置到 XDG data，根分区可用约 6.0GB。

所有全文翻译入口在 `translate_full.py` 最底层通过
`locks/full-translation.lock` 串行化。容器缓存清理和容器重启也竞争同一
把锁，但采用非阻塞策略：有翻译运行时跳过维护，不排队后突然打断下一篇。
每篇驱动使用独立 session 和 subreaper supervisor；timeout/Web kill 只按
精确 arXiv 进程树终止并回收子孙进程。Docker 控制操作也有默认 30 秒的有界
超时。TeX 子进程统一禁用 shell escape，并使用受限的 openin/openout
策略。生产、cron、测试固定使用 `/root/workspace/.env` 中的
`SERVER_PYTHON`（当前为 Python 3.13.12），不支持回退到系统旧 Python。

---

## Web 行为护栏

这些行为必须作为未来重构的验收标准。

| 行为 | 要求 |
|---|---|
| `/view/<id>` | 返回 HTML wrapper，不能默认 302 到裸 PDF |
| PDF iframe | wrapper no-store；iframe 指向 `{BASE_PATH}/papers/<id>_zh.pdf?v=<pdf_mtime>#view=FitH`，重新生成后必须自动换 URL |
| 详情按钮 | 继续指向 `/<mode>/<key>/papers/<id>` |
| 中文 PDF 按钮 | 继续指向 `/view/<id>` |
| 原文 PDF | 继续指向 `https://arxiv.org/pdf/<id>` |
| arXiv 原文 | 继续指向 `https://arxiv.org/abs/<id>` |
| PDF 文件 | `/papers/<id>_zh.pdf` 保留 `Range` / `206` |
| 直接 PDF | `/pdf/<id>/<title>.pdf` 保留中文 filename 响应头 |
| 路径前缀 | `BASE_PATH=/paper` 下内部链接必须正确加前缀，请求入口也必须接受 `/paper/...` |
| 破坏性 API | `/api/paper/delete`、`/api/status/kill` 仅接受 POST，且必须有显式配置的管理 token |
| 删除边界 | 校验 mode/key/arXiv ID/真实路径；其他索引仍引用或引用扫描失败时不得删除共享 PDF |
| 质量封锁 | store/index/quality sidecar 任一失败时，即使旧 PDF 存在也不得展示或直出 |
| 搜索快照 | 搜索公开；递归覆盖 topic，按 arXiv ID 去重，20 秒 TTL，内部写操作主动失效 |

---

## 验收命令

每次修改 Web 路由、按钮、PDF 查看页或部署行为前后都应运行：

```bash
python3 -m py_compile \
  web_server.py translate_arxiv.py translate_full.py \
  full_translate_driver.py latex_translation_filters.py \
  run_papers.py run_repair.py \
  tests/test_web_server_contract.py tests/test_latex_translation_filters.py

python3 -m unittest discover -s tests -v
python3 scripts/audit_project.py --strict
python3 scripts/queue_quality_repairs.py --json
```

线上抽查：

```bash
curl -k -I https://zzzgry.top/paper/view/2605.21573
curl -k -I -r 0-0 https://zzzgry.top/paper/papers/2605.21573_zh.pdf
curl -k -I https://zzzgry.top/paper/weekly/2026-W22/papers/2605.23904
```

---

## 近期维护路线

### Phase A — Web 稳定性护栏

- [x] 固定 `/view/<id>` HTML wrapper 行为。
- [x] 新增 Web 合约测试。
- [x] 收敛 PDF 状态判断和按钮链接生成。
- [x] 重建 README / plan / change 文档。
- [x] 请求入口兼容 `BASE_PATH=/paper` 前缀，避免 `/paper/view/<id>` 与 `/paper/papers/<file>` 404。
- [x] PDF wrapper iframe 增加 `v=<pdf_mtime>`，避免浏览器缓存旧的重新生成 PDF。
- [x] 删除论文和终止翻译改为 token 保护的 POST，补齐路径约束与共享 PDF 引用保护。
- [x] 全局搜索改为覆盖递归 topic 的 20 秒去重快照，避免每次输入重读全库。

### Phase B — 安全的单文件整理

- [x] 收敛共享路径、paper store 和容器默认名常量到 `paperhub.paths`，保留各入口脚本原常量名。
- [x] 抽出 `paperhub.paper_store`，统一 paper store JSON/PDF 读写，同时保留旧函数入口。
- [ ] 将更多重复页面片段收敛为小 helper，但继续保持单文件部署。
- [ ] 对 bookmarks 的 HTML/JS 输出补充更多合约测试。
- [ ] 给 `/api/bookmarks`、`/api/search`、`/api/submit` 增加 POST/GET API 级测试。
- [ ] 对 `BASE_PATH` 字符串替换策略做快照测试，防止误改外链。
- [x] 新增主题订阅 tab、topic profile、检索词编辑、token 保护和 `run_topic.py --all` 调度入口，同时保持搜索公开、手动提交写操作受保护。
- [x] 优化主题订阅检索词 prompt：默认按 AI/ML/CS 解释用户输入，要求多元 should 词，并过滤和 negative 冲突的无关召回词。
- [x] 给主题订阅增加可编辑备注名 `display_name`，列表/详情优先展示备注名且不影响检索和缓存。
- [x] 将 topic 接入 `run_repair.py`：摘要缺失补翻译，`pdf_status=failed` 复用 daily PDF retry 逻辑重试和修复。

### Phase C — 翻译链路稳定性

- [ ] 为 `run_papers.py` 的 slim index 写入和 pdf_status 同步增加纯函数测试。
- [x] 为 paper store 不完整翻译缓存、原子 JSON 写入和全项目数据审计增加样例测试。
- [x] 为 inline `\verb` 分隔符冲突沉淀 fallback fixture，修复 regex 内容包含原分隔符导致的 undefined control sequence。
- [x] 扩展 FontAwesome legacy alias 与 `\DeclareUnicodeCharacter` fallback，并修复 preamble 参数内命令的 snippet 插入位置，恢复 2026-07-01 / 2026-07-03 daily 三篇失败 PDF。
- [x] 全量补扫索引失败、缺失 PDF、残留失败日志和失败现场，恢复 `2606.11324`、补齐 `2605.10344`，并清理已恢复 PDF 的陈旧诊断。
- [x] 修复 topic PDF 编译失败：为 listing/inputenc 补 `\inputencodingname`，为 CIDR/ACM/fontspec 风格模板补安全 no-op 和 `baselinestretch` guard reset，恢复 `2606.26080`、`2606.29823`。
- [x] `retry-pdf` 增加 paper store 状态一致性同步，自动把已有中文 PDF 但 JSON 仍 failed 的历史残留回写为 `ok`。
- [x] `retry-pdf` 增加 ok-but-missing 降级重试：索引标 `pdf_status=ok` 但 paper store PDF 缺失时自动进入缓存重编译/全文重译，补回 `2606.29296`、`2606.29445` 等缺失 PDF。
- [x] 将 `full_translate_driver.py` 的 TeX patch/编译/门禁迁移到 `paperhub/latex_pipeline.py`，将 gpt-academic 适配迁移到 `paperhub/translation_runtime.py`，driver 收敛到 813 行；编译 patch 同时绑定 `latex_toolbox` 与 `latex_actions`，并恢复源文件丢失的自定义宏。
- [x] 建立稳定的翻译/编译失败 taxonomy、JSON sidecar、聚合报告和 retry strategy，后续按 category 直接定位。
- [x] 为 arXiv 源码下载断流增加预下载/校验缓存，并支持只有 tex 备份时重建 workfolder 后直编译。
- [x] 为 gpt-academic LaTeX splitter 增加普通正文扩展翻译补丁，避免 preserve 节点吞掉正文。
- [x] 增加 `merge_translate_zh.tex` 翻译覆盖率门禁，拒绝大段英文漏译 PDF。
- [x] 增加编译健康门禁和多轮 BibTeX/XeLaTeX fallback，拒绝 undefined command/cite/ref 残留。
- [x] 增加自定义宏中文粘连、误生成 `\textWord`、唯一前缀 label/ref 的自动修补。
- [x] 增加旧式 fontawesome alias、XeLaTeX microtype、本地 `NVIDIASans` 字体映射和 BibTeX 中间文件恢复补丁，修复 2026-06-12 daily 两篇失败 PDF。
- [x] 基于 gpt-academic 原始 splitter 增加 preserve 节点二次安全拆分，覆盖表格单元格和 algorithmic 说明文字，同时保留硬保护环境不翻译。
- [x] 将自定义零参数宏/CJK 粘连、pdfTeX primitive guard、本地不可用 T1 字体默认值回退沉淀为通用 fallback，修复 2026-06-16 daily 两篇失败 PDF。
- [x] 将 `translate_full.py` 宿主机输出读取改为非阻塞轮询，避免容器长时间无换行输出时外层 timeout 失效。
- [x] 增加超长正文句子级拆分、CLI/GUI 轨迹环境硬保护、verbatim 环境恢复和缺 `.bib` 时复用既有 `.bbl`，修复 2026-06-14 daily `2606.09426` 失败 PDF。
- [x] 抽象 `latex_translation_filters.py`，统一 splitter、质量门禁和 fallback 的环境筛选/过滤条件，并支持环境变量扩展。
- [x] 修复 retry-pdf slim 默认容器、精确容器检查、容器内翻译缓存复用和缓存失败后的 no-cache fallback。
- [x] 加固 fallback 编译：安全 aux、直接接入生成 bbl、不安全 citation key 规范化、LuaLaTeX segfault fallback、algorithm2e/FontAwesome 兼容和更多 LLM artifact 过滤。
- [x] 将模型序列化残留、字体族、engine driver、环境失配、TikZ matrix 图例和重复宏首字母归入稳定 taxonomy，并为每类登记通用 patch。
- [x] 摘要翻译兼容 OpenAI-compatible 多形态 content、截断/非法反斜杠 JSON，并可从已成功中文 TeX 回填缺失字段。
- [x] 针对 `quality.untranslated_prose` 增加 chunk 级失败响应验证与局部重试；首轮 50 路，失败槽使用最多 16 路有界并发，仍失败则拒绝半成品缓存。
- [x] chunk v45 取消 citation/ref 密集正文的预切碎；普通正文上限 2400 字符，结构/引用密集片段自动降为 1900/1500，仅对结构门禁真正失败的 slot 自适应细分。
- [x] 为高覆盖 TeX 增加最多 12 行的末端残留定向重译；逐行结构校验和质量
  分数提交，`2607.23782` 已从 3 个混合英文 clause 降至 0 并真实发布。
- [x] 增加全量中文 TeX/chunk 英文分布分析，区分普通正文、混合语言与代码/轨迹保护环境，并按 CJK 覆盖和长英文行生成历史严重项队列。
- [x] chunk v9 跳过环境结构、纯 option list、citation-heavy 名称目录、inline
  code、URL、注释和上游 prompt，并对 prompt/trace/example/source-data box 做
  实例级保护；普通 box 正文仍翻译。每个响应在合并前再校验关键结构/citation
  签名和高置信中英混合 clause，失败进入单路重试而非写回英文。
- [x] chunk v11 将 citation/ref 两侧被上游保留的短英文正文接缝有界吸收到
  相邻翻译 chunk；裸 `\section` 模型幻觉仅在原文无结构命令且 citation
  多重集不变时归一化，其他结构变化继续拒绝。
- [x] chunk v28 修复无尾随空格时 TRANSFORM 边界被立即回并的问题，并在
  citation/ref bridge 与 coalescer 之后强制执行最终动态上限；引用 key
  断裂优先闭合，空白片段、TikZ path、严格专名目录和已翻译标题后的产品名
  清单不再制造伪漏译，解释性正文仍由结构和混合语言门禁拦截。
- [x] chunk v30 保留自定义文本宏的人类语言参数，并把展示公式旁的短
  `\par`/heading/bullet 正文重新入队；响应合并前检查未转义花括号净平衡，
  仅对结构签名不变的单个末尾孤立 `}` 做安全归一化，防止合并器恢复英文尾段。
- [x] fallback 前导区插入器跟踪跨行花括号和方括号，并迁移历史错误插入块；
  混合语言门禁排除 TeX 双反引号引用文本与命令密集 TikZ 绘图片段。
- [x] 将生产发布、repository audit、英文分布和历史质量 queue 收敛到
  `paperhub.translation_quality` 的同一正文提取与阈值。
- [x] 为质量失败持久化 store taint，并让 cache hit、卡片、wrapper 和直接
  PDF 路由一直封锁旧文件，直到新 PDF 完成全门禁验证。
- [x] PDF 轻量校验增加 `%PDF-` header 与尾部 `%%EOF`，store、Web、repair
  和审计不再接受只满足大小阈值的残缺文件。
- [x] 为无 TeX 备份的历史 PDF 增加有界 `pdftotext` fallback 审计与缓存，按
  持续英文、局部英文、翻译拒绝分别进入
  `quality.pdf_sustained_untranslated`、`quality.pdf_partial_untranslated`、
  `quality.translation_refusal`；确认后才通过质量队列预检并原子 taint 全部引用。
- [x] 全文入口下沉统一全局锁，跨 daily/weekly/monthly/manual/topic/Web 串行使用共享容器。
- [x] 每篇容器驱动增加独立进程组与 subreaper 回收，Docker 控制操作增加
  有界 timeout，Web kill 精确终止单篇完整进程树。
- [x] TeX 编译全路径禁用 shell escape，并限制 openin/openout 文件访问。
- [x] 索引/store/PDF 发布统一通过 publication lock，PDF 校验+fsync 后原子
  替换；孤儿清理在锁内复扫引用并给新对象 3 天缓冲，索引/锁错误 fail closed。
- [ ] 分批处理全量扫描发现的 69 篇 mixed-language 候选：扫描仅统计可回溯 TeX
  的普通正文同段中英混合 clause，排除表格、代码、引用和 source-data；候选需
  重译后通过覆盖率、编译健康、索引/PDF 一致性门禁才能从队列移除。目前仍在修复，
  不以候选数或中间批次成功数宣称全量清零。
- [ ] 本轮生产修复结束后补录最终篇数和残留 ID；只以 strict audit、共享
  质量 queue、失败 sidecar/tex 全部归零作为完成条件，不记录中途快照。

### Phase D — 运维体验

- [x] 将索引/store/PDF 全量审计和失败分类摘要整理成独立脚本。
- [x] 为失败队列增加 category / retry strategy / repair action 轻量摘要日志。
- [x] 增加周日 02:00 当前 ISO 周五模式 repair runner：等待 weekly index
  和抓取锁，收集 daily/weekly/monthly/manual/topic 已发布论文，按 arXiv
  ID 去重修复并同步全部引用索引。
- [x] 建立通用 patch catalog 与 `logs/repair_history/weekly-<key>.json`，沉淀每周失败类别、匹配补丁和剩余失败。
- [x] 将全模式近窗审计、串行修复、patch 沉淀、验证、文档和 push 约定整理为 `$paper-trans-repair` Codex skill。
- [x] 对旧 `compile.unknown` sidecar 增加日志重分类，并对大日志使用有界证据，缩短后续定位时间。
- [x] 增加低 token 的 `repair_snapshot.py`，skill 默认按快照和 category 路由，
  仅在全量/final gate 使用重型审计。
- [x] 将重复 cron 收敛为 managed block；日常 post、全模式 PDF retry、缓存、
  空闲重启及周日清理由单一 maintenance coordinator 负责。
- [x] daily 队列结束后原子迁移到 `apps/paper-trans` 与 `vendor/gpt-academic`，
  更新 systemd、cron、Docker bind、Codex trusted path 并验证 `/paper/`。
- [x] 构建平衡裁剪 TeX candidate，导出前运行 4 篇历史 compile canary、归档
  校验后替换生产镜像并再次 canary；镜像从 7.62GB 降至 6.746GB，
  `/gpt/gpt_log` 已迁到 XDG data bind mount。没有用删除 TeX 能力换取 4.5GB。
- [x] 修复 `run_repair.py --post` 对当前周期的跳过边界，确保首次 cron 触发后可补抓临时网络失败的 daily/weekly/monthly。
- [x] 补齐生产 root crontab 的 topic 调度：每天 01:30 `run_topic.py --all`，06:30 `run_repair.py --retry-pdf --mode topic --days 7`，并补跑 `2026-07-06` 主题结果。
- [x] 修复 weekly cleanup 的 topic 引用漏扫：孤立 PDF 统计递归覆盖 topic 两层索引，避免 topic-only PDF 被误删。
- [x] 修复一次性翻译驱动输出 RESULT 后被遗留 worker 线程拖住的问题，结果落盘后立即退出容器子进程。
- [x] repair/refetch/retry 输出真实 attempted/succeeded/failed 和残留 ID；残留非零时 CLI 非零退出。
- [x] `run_repair.py --key` 改为严格精确范围，post/refetch 不再意外扫描多日。
- [x] 缓存清理和容器重启复用全文翻译锁；缓存每天运行，常规保留 3 天、磁盘高水位时保留 1 天，并按文件回收 shared/downloadzone 历史产物。
- [x] 孤立 PDF 清理增加 3 天发布缓冲，避免 PDF 与 index 分步落盘时发生竞态。
- [x] weekly cleanup 显式累计每个步骤的失败，索引扫描错误拒绝孤立 PDF
  删除，任一失败最终非零退出。
- [x] 将 manual 纳入正式 `CONTENT_MODES`、默认 repair/retry、日期范围和
  周日当前周修复，不再遗漏 Web 手动提交历史。
- [x] Nginx `/paper/papers/` 改为反代 Web 应用，不再用静态 alias 绕过 taint
  与 PDF 完整性检查；健康 PDF 保留 Range/`206`。
- [x] 对磁盘低水位和缓存清理异常增加 Gmail 告警入口，复用服务器现有 SMTP 配置。
- [x] 全文 retry 遇到实时 API quota 错误后立即熔断本批；动态 TeX include、
  公式、专名目录、endpoint 与 citation-key 参数已沉淀为通用 patch。
- [ ] 对 Docker 容器异常、PDF retry 长期失败增加告警入口。

### Phase E — Docker 镜像瘦身验证

- [x] 将翻译容器名改为 `GPT_ACADEMIC_CONTAINER` 可覆盖，默认使用 `gpt-academic-latex-slim`。
- [x] 增加 `paper-trans-latex-slim` 构建、启动和 canary 脚本。
- [x] 在 40GB 服务器上用低磁盘 flatten 模式构建 slim 镜像，并记录最终镜像体积约 4.55GB。
- [x] 将最终生产方案调整为 full-TeX slim：默认保留完整 TeX/font 运行时，只裁剪 ML/runtime/cache/doc/source 负载，镜像体积约 7.62GB。
- [x] 在导出前对实际裁剪 rootfs 运行 canary，公开源码 tar 使用校验后的 XDG
  cache；xz 归档通过完整性校验后导入 6.746GB 平衡裁剪镜像。
- [x] 将 `/gpt/gpt_log` 外置为 XDG data bind mount，增加 runtime-ready marker；
  依赖齐全时跳过 setup，缺包 apt 具有总超时和连接超时。
- [x] Docker 对象为空但 overlay2 仍占约 22GB 时，停服务并清除确认无引用的
  孤儿层；最终仅保留 1 镜像、1 容器、0 build cache。
- [x] 为低磁盘切换增加 dry-run 和压缩 rootfs 导出路径，避免无法同时容纳旧镜像、新镜像和构建中间层。
- [x] 使用 `2606.09967`、`2606.10917`、`2606.09828`、`2606.02060` 跑完 compile canary。
- [x] 使用 `2606.08432` 跑完 full no-cache canary，验证 GPT 翻译阶段和 LaTeX 编译完整链路。
- [x] 将 root cron 的例行翻译容器切换到 `gpt-academic-latex-slim` 做今晚试跑。
- [x] 将 `paper-trans-web.service` 的手动提交路径切换到 `gpt-academic-latex-slim` 做试跑。
- [x] 复盘 2026-06-11 daily slim 失败项，补齐 `libertine`、`newtxmath`、`zlmtt`、`Inconsolatazi4` 兼容层，并恢复 `2606.11926`、`2606.12344` PDF 状态。
- [x] 使用 full-TeX slim 重新验证 `2606.11926`、`2606.12344`，确认 keep-translation 重编译链路正常。
- [x] 生产 cron 和 Web 手动提交均切换到 `gpt-academic-latex-slim`。
- [x] 删除原容器和原镜像，并清理 Docker overlay2 孤儿目录，根分区可用空间恢复到约 14GB。

---

## 已知限制

1. 全文 PDF 翻译强依赖 arXiv LaTeX 源码质量和 gpt-academic 插件行为。
2. 大 PDF 在不同浏览器中的 viewer 行为不一致，因此 `/view/<id>` 必须保留 HTML wrapper。
3. 当前 Web 是单文件 HTTP 服务，易部署但代码会继续增长；未来拆分前必须先扩大合约测试。
4. 搜索快照仍在单进程内存中，外部 cron 写入最多延迟 20 秒可见；数据量显著增长后可再升级为持久化倒排索引。
5. 手动提交和自动抓取共享 paper store；Web 删除已保护跨 mode 引用，但离线维护脚本仍必须先递归核对全部索引。
6. 原 full Docker 镜像已从本机删除；如需回滚到上游 full 镜像，需要重新拉取或重新构建。

---

## 文档维护规则

- 用户可见行为变更：更新 `README.md` 和 `change.md`。
- 架构、路线或维护约定变更：更新 `plan.md`。
- 代码提交前：跑语法检查和合约测试。
- 上线 Web 改动后：重启 `paper-trans-web.service` 并做线上抽查。
