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
| 全文 PDF 翻译 | 完成 | `translate_full.py` + `full_translate_driver.py` |
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

---

## Web 行为护栏

这些行为必须作为未来重构的验收标准。

| 行为 | 要求 |
|---|---|
| `/view/<id>` | 返回 HTML wrapper，不能默认 302 到裸 PDF |
| PDF iframe | 指向 `{BASE_PATH}/papers/<id>_zh.pdf#view=FitH` |
| 详情按钮 | 继续指向 `/<mode>/<key>/papers/<id>` |
| 中文 PDF 按钮 | 继续指向 `/view/<id>` |
| 原文 PDF | 继续指向 `https://arxiv.org/pdf/<id>` |
| arXiv 原文 | 继续指向 `https://arxiv.org/abs/<id>` |
| PDF 文件 | `/papers/<id>_zh.pdf` 保留 `Range` / `206` |
| 直接 PDF | `/pdf/<id>/<title>.pdf` 保留中文 filename 响应头 |
| 路径前缀 | `BASE_PATH=/paper` 下内部链接必须正确加前缀 |

---

## 验收命令

每次修改 Web 路由、按钮、PDF 查看页或部署行为前后都应运行：

```bash
python3 -m py_compile \
  web_server.py translate_arxiv.py translate_full.py \
  full_translate_driver.py run_papers.py run_repair.py \
  tests/test_web_server_contract.py

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

### Phase B — 安全的单文件整理

- [ ] 将更多重复页面片段收敛为小 helper，但继续保持单文件部署。
- [ ] 对 bookmarks 的 HTML/JS 输出补充更多合约测试。
- [ ] 给 `/api/bookmarks`、`/api/search`、`/api/submit` 增加 POST/GET API 级测试。
- [ ] 对 `BASE_PATH` 字符串替换策略做快照测试，防止误改外链。

### Phase C — 翻译链路稳定性

- [ ] 为 `run_papers.py` 的 slim index 写入和 pdf_status 同步增加纯函数测试。
- [ ] 为 `translate_arxiv.py` 的 JSON 修复逻辑增加样例测试。
- [ ] 梳理 `full_translate_driver.py` fallback patch 的触发条件，沉淀为小型 fixtures。
- [ ] 继续补充 PDF 失败诊断日志中的常见 LaTeX 错误分类。

### Phase D — 运维体验

- [ ] 将常用 health check 命令整理成脚本。
- [ ] 为 cron 运行结果增加轻量摘要日志。
- [ ] 对磁盘低水位、Docker 容器异常、PDF retry 长期失败增加告警入口。

---

## 已知限制

1. 全文 PDF 翻译强依赖 arXiv LaTeX 源码质量和 gpt-academic 插件行为。
2. 大 PDF 在不同浏览器中的 viewer 行为不一致，因此 `/view/<id>` 必须保留 HTML wrapper。
3. 当前 Web 是单文件 HTTP 服务，易部署但代码会继续增长；未来拆分前必须先扩大合约测试。
4. 搜索是线性扫描全部 index 和 paper store，数据量继续增长后可能需要索引缓存。
5. 手动提交和自动抓取共享 paper store，删除 PDF 时需要谨慎处理跨 mode 引用。

---

## 文档维护规则

- 用户可见行为变更：更新 `README.md` 和 `change.md`。
- 架构、路线或维护约定变更：更新 `plan.md`。
- 代码提交前：跑语法检查和合约测试。
- 上线 Web 改动后：重启 `paper-trans-web.service` 并做线上抽查。
