# Paper Trans 项目变更日志

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

| action | 说明 |
|--------|------|
| `toggle` | 切换某论文在某列表中的收藏状态 |
| `create_list` | 新建列表（可同时收藏一篇论文）|
| `delete_list` | 删除列表 |
| `rename_list` | 重命名列表 |
| `remove` | 从列表移除论文 |
| `move` | 将论文从一个列表移动到另一个列表 |

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
| 序号 | arXiv ID | 中文标题 | 状态 |
|------|----------|----------|------|
| 1 | 2602.10388 | 少即是多：在大语言模型特征空间中合成多样化数据 | ✅ |
| 2 | 2602.12783 | SQuTR：在声学噪声下语音查询到文本检索的鲁棒性基准 | ✅ |
| 3 | 2602.12705 | MedXIAOHE：构建医疗多模态大模型的全面方案 | ✅ |
| 4 | 2602.11858 | 无需缩放的缩放：面向细粒度多模态感知的区域到图像蒸馏方法 | ✅ |
| 5 | 2602.14111 | 稀疏自编码器的合理性检验：稀疏自编码器真的优于随机基线吗？ | ✅ |
| 6 | 2602.13949 | 体验式强化学习 | ✅ |
| 7 | 2602.08683 | OneVision-Encoder：以编解码器对齐的稀疏性作为多模态智能的基础原则 | ✅ |
| 8 | 2602.12670 | SkillsBench：评估智能体技能在多样化任务中的表现 | ✅ |
| 9 | 2602.10809 | DeepImageSearch：面向视觉历史的上下文感知图像检索多模态智能体基准测试 | ✅ |
| 10 | 2602.15763 | GLM-5：从氛围编码到自主工程 | ✅ |

**结果**: 10/10 成功，耗时约 2 分钟

### 当前服务状态
- Web 服务: ✅ 运行中 (systemd: paper-trans-web)
- 访问地址: http://51.79.130.17:18080
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

| 文件 | 功能 |
|------|------|
| `full_translate_driver.py` | 在容器内运行的驱动脚本 |
| `translate_full.py` | 容器外的调用封装（docker exec + docker cp）|

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

| 序号 | arXiv ID | 状态 | 耗时 |
|------|----------|------|------|
| 1 | 2602.10388 | 翻译中… | - |
| 2 | 2602.12783 | ✅ 已有 | ~15min |
| 3 | 2602.12705 | 翻译中… | - |
| 4 | 2602.11858 | 翻译中… | - |
| 5 | 2602.14111 | 翻译中… | - |
| 6 | 2602.13949 | 翻译中… | - |
| 7 | 2602.08683 | 翻译中… | - |
| 8 | 2602.12670 | 翻译中… | - |
| 9 | 2602.10809 | 翻译中… | - |
| 10 | 2602.15763 | 翻译中… | - |

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
