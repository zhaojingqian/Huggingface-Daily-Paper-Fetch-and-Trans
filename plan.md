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
    ↓ docker exec
full_translate_driver.py
    ↓
data/papers/<arxiv_id>_zh.pdf
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

### 翻译容器

当前生产容器为 `gpt-academic-latex-slim`，镜像为 `paper-trans-latex-slim:latest`。该镜像默认使用 `GPT_ACADEMIC_SLIM_TEX_PROFILE=full`，继承原 `gpt_academic_with_latex` 的完整 TeX/font 运行时，只裁剪中文翻译不需要的 ML/runtime/cache/doc/source 负载。

2026-06-12 已确认新容器可用，并删除旧容器 `gpt-academic-latex` 和旧镜像 `ghcr.io/binary-husky/gpt_academic_with_latex:master`；本机不再保留旧 Docker 回滚副本。根分区可用空间从切换前的紧张状态恢复到约 14GB。

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
/root/.pyenv/versions/3.10.13/bin/python3 -m unittest discover -s tests -v
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

### Phase B — 安全的单文件整理

- [x] 收敛共享路径、paper store 和容器默认名常量到 `paperhub.paths`，保留各入口脚本原常量名。
- [x] 抽出 `paperhub.paper_store`，统一 paper store JSON/PDF 读写，同时保留旧函数入口。
- [ ] 将更多重复页面片段收敛为小 helper，但继续保持单文件部署。
- [ ] 对 bookmarks 的 HTML/JS 输出补充更多合约测试。
- [ ] 给 `/api/bookmarks`、`/api/search`、`/api/submit` 增加 POST/GET API 级测试。
- [ ] 对 `BASE_PATH` 字符串替换策略做快照测试，防止误改外链。
- [x] 新增主题订阅 tab、topic profile、检索词编辑、token 保护和 `run_topic.py --all` 调度入口，同时保持搜索公开、手动提交写操作受保护。
- [x] 优化主题订阅检索词 prompt：默认按 AI/ML/CS 解释用户输入，要求多元 should 词，并过滤和 negative 冲突的无关召回词。

### Phase C — 翻译链路稳定性

- [ ] 为 `run_papers.py` 的 slim index 写入和 pdf_status 同步增加纯函数测试。
- [ ] 为 `translate_arxiv.py` 的 JSON 修复逻辑增加样例测试。
- [x] 为 inline `\verb` 分隔符冲突沉淀 fallback fixture，修复 regex 内容包含原分隔符导致的 undefined control sequence。
- [x] 扩展 FontAwesome legacy alias 与 `\DeclareUnicodeCharacter` fallback，并修复 preamble 参数内命令的 snippet 插入位置，恢复 2026-07-01 / 2026-07-03 daily 三篇失败 PDF。
- [x] 全量补扫索引失败、缺失 PDF、残留失败日志和失败现场，恢复 `2606.11324`、补齐 `2605.10344`，并清理已恢复 PDF 的陈旧诊断。
- [ ] 继续梳理 `full_translate_driver.py` 其他 fallback patch 的触发条件，沉淀为小型 fixtures。
- [ ] 继续补充 PDF 失败诊断日志中的常见 LaTeX 错误分类。
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

### Phase D — 运维体验

- [ ] 将常用 health check 命令整理成脚本。
- [ ] 为 cron 运行结果增加轻量摘要日志。
- [x] 修复 `run_repair.py --post` 对当前周期的跳过边界，确保首次 cron 触发后可补抓临时网络失败的 daily/weekly/monthly。
- [ ] 对磁盘低水位、Docker 容器异常、PDF retry 长期失败增加告警入口。

### Phase E — Docker 镜像瘦身验证

- [x] 将翻译容器名改为 `GPT_ACADEMIC_CONTAINER` 可覆盖，默认使用 `gpt-academic-latex-slim`。
- [x] 增加 `paper-trans-latex-slim` 构建、启动和 canary 脚本。
- [x] 在 40GB 服务器上用低磁盘 flatten 模式构建 slim 镜像，并记录最终镜像体积约 4.55GB。
- [x] 将最终生产方案调整为 full-TeX slim：默认保留完整 TeX/font 运行时，只裁剪 ML/runtime/cache/doc/source 负载，镜像体积约 7.62GB。
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
4. 搜索是线性扫描全部 index 和 paper store，数据量继续增长后可能需要索引缓存。
5. 手动提交和自动抓取共享 paper store，删除 PDF 时需要谨慎处理跨 mode 引用。
6. 原 full Docker 镜像已从本机删除；如需回滚到上游 full 镜像，需要重新拉取或重新构建。

---

## 文档维护规则

- 用户可见行为变更：更新 `README.md` 和 `change.md`。
- 架构、路线或维护约定变更：更新 `plan.md`。
- 代码提交前：跑语法检查和合约测试。
- 上线 Web 改动后：重启 `paper-trans-web.service` 并做线上抽查。
