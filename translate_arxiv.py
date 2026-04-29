#!/usr/bin/env python3
"""
arxiv 论文翻译脚本
使用 gpt-academic 配置的 OpenAI 兼容 API 翻译摘要
生成 HTML 格式的双语论文页面
"""

import os
import sys
import json
import re
import time
import requests
import argparse
from datetime import datetime
from pathlib import Path

# 读取 gpt-academic 配置
GPT_ACADEMIC_CONFIG = "/root/workspace/gpt-academic/config_private.py"
PROXY = "http://127.0.0.1:7890"

# Paper Store — 所有论文元数据/翻译的唯一存储（daily/weekly/monthly 共用）
_BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
PAPER_STORE_DIR = os.path.join(_BASE_DIR, "data", "papers")


def _has_chinese(text):
    """检查字符串中是否包含中文字符"""
    return bool(re.search(r'[\u4e00-\u9fff]', text or ""))


# ── Paper Store 读写 ──────────────────────────────────────────────────────────
def paper_store_path(arxiv_id):
    os.makedirs(PAPER_STORE_DIR, exist_ok=True)
    return os.path.join(PAPER_STORE_DIR, f"{arxiv_id}.json")


def paper_store_read(arxiv_id):
    """读取 paper store；若不存在或 title_zh 为空则返回 None"""
    try:
        with open(paper_store_path(arxiv_id), encoding="utf-8") as f:
            data = json.load(f)
        if _has_chinese(data.get("title_zh", "")):
            return data
    except Exception:
        pass
    return None


def paper_store_write_raw(payload):
    """直接写入已构建好的 payload dict"""
    try:
        with open(paper_store_path(payload["arxiv_id"]), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"  ⚠️ paper store 写入失败: {e}", flush=True)


def paper_store_write(arxiv_id, meta, translation):
    """将元数据 + 翻译结果合并写入 paper store"""
    payload = {
        "arxiv_id":   arxiv_id,
        "stored_at":  datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "title":      meta.get("title", ""),
        "abstract":   meta.get("abstract", ""),
        "authors":    meta.get("authors", ""),
        "submitted":  meta.get("submitted", ""),
        "url":        meta.get("url", ""),
        "pdf_url":    meta.get("pdf_url", ""),
        "title_zh":   translation.get("title_zh", ""),
        "abstract_zh":translation.get("abstract_zh", ""),
        "keywords_zh":translation.get("keywords_zh", []),
        "summary_zh": translation.get("summary_zh", ""),
    }
    paper_store_write_raw(payload)



def load_api_config():
    """从 gpt-academic 的 config_private.py 读取 API 配置"""
    config = {}
    try:
        with open(GPT_ACADEMIC_CONFIG, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("API_KEY="):
                    config["api_key"] = line.split("=", 1)[1].strip().strip("'\"")
                elif line.startswith("API_URL_REDIRECT="):
                    raw = line.split("=", 1)[1].strip()
                    try:
                        redirect = json.loads(raw)
                        config["api_base"] = list(redirect.values())[0]
                    except Exception:
                        pass
                elif line.startswith("LLM_MODEL="):
                    config["model"] = line.split("=", 1)[1].strip().strip("'\"")
    except Exception as e:
        print(f"⚠️ 读取配置失败: {e}")

    # 默认值
    config.setdefault("api_key", "")
    config.setdefault("api_base", "https://api.openai.com/v1/chat/completions")
    config.setdefault("model", "gpt-4.1-mini")

    # 推导 base_url (去掉末尾的 chat/completions)
    base = config["api_base"]
    if base.endswith("/chat/completions"):
        config["base_url"] = base[: -len("/chat/completions")]
    else:
        config["base_url"] = base.rstrip("/")

    return config


def call_llm(messages, config, max_tokens=4000, max_retries=3):
    """调用 LLM API，带指数退避重试（代理失败自动切直连）"""
    url = config["api_base"]
    if not url.endswith("/chat/completions"):
        url = url.rstrip("/") + "/chat/completions"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config['api_key']}",
    }

    payload = {
        "model": config["model"],
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }

    proxies = {"http": PROXY, "https": PROXY}
    last_exc = None

    for attempt in range(max_retries):
        try:
            resp = requests.post(url, headers=headers, json=payload,
                                 proxies=proxies, timeout=120)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except requests.exceptions.ProxyError:
            if proxies.get("https"):
                print("  ⚠️ LLM 代理失败，切换直连重试...", flush=True)
                proxies = {"http": "", "https": ""}
                last_exc = None
                continue
            last_exc = RuntimeError("代理不可用且直连也失败")
        except (requests.exceptions.SSLError,
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as e:
            last_exc = e
            wait = 2 ** attempt
            print(f"  ⚠️ LLM 连接错误 (尝试 {attempt+1}/{max_retries}): {type(e).__name__}", flush=True)
            if proxies.get("https"):
                print("  ⚠️ 切换直连重试...", flush=True)
                proxies = {"http": "", "https": ""}
            elif attempt < max_retries - 1:
                print(f"  ⚠️ 等待 {wait}s 后重试...", flush=True)
                time.sleep(wait)
        except Exception as e:
            last_exc = e
            wait = 2 ** attempt
            print(f"  ⚠️ LLM 调用失败 (尝试 {attempt+1}/{max_retries}): {e}", flush=True)
            if attempt < max_retries - 1:
                time.sleep(wait)

    raise RuntimeError(f"LLM API 调用失败（已重试 {max_retries} 次）: {last_exc}")


def fetch_arxiv_metadata(arxiv_id, use_proxy=True):
    """从 arxiv 获取论文元数据"""
    url = f"https://export.arxiv.org/abs/{arxiv_id}"
    proxies = {"http": PROXY, "https": PROXY} if use_proxy else {"http": "", "https": ""}

    try:
        resp = requests.get(url, proxies=proxies, timeout=30,
                            headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        html = resp.text

        # 标题
        title = ""
        m = re.search(r'<h1 class="title mathjax"[^>]*>(?:<span[^>]*>Title:</span>\s*)?(.*?)</h1>',
                      html, re.DOTALL)
        if m:
            title = re.sub(r'<[^>]+>', '', m.group(1)).strip()

        # 摘要
        abstract = ""
        m = re.search(r'<blockquote class="abstract mathjax"[^>]*>'
                      r'(?:<span[^>]*>Abstract:</span>\s*)?(.*?)</blockquote>',
                      html, re.DOTALL)
        if m:
            abstract = re.sub(r'<[^>]+>', '', m.group(1)).strip()
            abstract = re.sub(r'\s+', ' ', abstract)

        # 作者
        authors = ""
        m = re.search(r'<div class="authors"[^>]*>(.*?)</div>', html, re.DOTALL)
        if m:
            authors = re.sub(r'<[^>]+>', '', m.group(1)).strip()
            authors = re.sub(r'\s+', ' ', authors).strip(", ")

        # 提交日期
        submitted = ""
        m = re.search(r'Submitted on ([\w\s,]+?)(?:\s*\(|\s*\[)', html)
        if m:
            submitted = m.group(1).strip()

        return {
            "arxiv_id": arxiv_id,
            "title": title,
            "abstract": abstract,
            "authors": authors,
            "submitted": submitted,
            "url": f"https://arxiv.org/abs/{arxiv_id}",
            "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
        }

    except Exception as e:
        if use_proxy:
            return fetch_arxiv_metadata(arxiv_id, use_proxy=False)
        print(f"  ⚠️ 获取元数据失败: {e}")
        return {
            "arxiv_id": arxiv_id, "title": "", "abstract": "",
            "authors": "", "submitted": "",
            "url": f"https://arxiv.org/abs/{arxiv_id}",
            "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
        }


def translate_paper(meta, config, max_retries=3):
    """使用 LLM 翻译论文标题和摘要，带重试和中文校验"""
    title = meta.get("title", "")
    abstract = meta.get("abstract", "")

    if not title and not abstract:
        return {"title_zh": "", "abstract_zh": "", "keywords_zh": [], "summary_zh": ""}

    prompt = f"""请将以下学术论文的标题和摘要翻译成中文，并提供简短的中文总结和关键词。

【论文标题】
{title}

【摘要】
{abstract}

请按以下 JSON 格式返回（不要添加任何其他文字）：
{{
  "title_zh": "中文标题",
  "abstract_zh": "中文摘要",
  "keywords_zh": ["关键词1", "关键词2", "关键词3", "关键词4", "关键词5"],
  "summary_zh": "用2-3句话总结本文的核心贡献和意义"
}}"""

    messages = [
        {"role": "system", "content": "你是一位专业的AI/ML领域学术论文翻译专家，擅长准确翻译英文论文并提取关键信息。"},
        {"role": "user", "content": prompt}
    ]

    empty = {"title_zh": "", "abstract_zh": "", "keywords_zh": [], "summary_zh": ""}

    for attempt in range(max_retries):
        try:
            result = call_llm(messages, config, max_tokens=2000)
            json_match = re.search(r'\{.*\}', result, re.DOTALL)
            if not json_match:
                print(f"  ⚠️ 翻译响应不含 JSON (尝试 {attempt+1}/{max_retries})", flush=True)
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                continue
            json_str = json_match.group()
            # 修复 LLM 返回的 JSON 中由 LaTeX/数学符号引入的非法转义序列
            # 合法的 JSON escape: \", \\, \/, \b, \f, \n, \r, \t, \uXXXX
            json_str = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', json_str)
            parsed = json.loads(json_str)
            # 校验：title_zh 必须含中文字符
            if not _has_chinese(parsed.get("title_zh", "")):
                print(f"  ⚠️ title_zh 无中文内容，重试 ({attempt+1}/{max_retries})...", flush=True)
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                continue
            return parsed
        except json.JSONDecodeError as e:
            print(f"  ⚠️ JSON 解析失败 (尝试 {attempt+1}/{max_retries}): {e}", flush=True)
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
        except Exception as e:
            print(f"  ⚠️ 翻译失败 (尝试 {attempt+1}/{max_retries}): {e}", flush=True)
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)

    print(f"  ❌ 翻译最终失败（已重试 {max_retries} 次）", flush=True)
    return empty


def generate_html(meta, translation, rank, week_str, pdf_zh=None):
    """生成论文的 HTML 页面"""
    title = meta.get("title", meta["arxiv_id"])
    title_zh = translation.get("title_zh", "")
    abstract = meta.get("abstract", "")
    abstract_zh = translation.get("abstract_zh", "")
    summary_zh = translation.get("summary_zh", "")
    keywords_zh = translation.get("keywords_zh", [])
    authors = meta.get("authors", "")
    submitted = meta.get("submitted", "")
    arxiv_id = meta["arxiv_id"]
    arxiv_url = meta.get("url", f"https://arxiv.org/abs/{arxiv_id}")
    pdf_url = meta.get("pdf_url", f"https://arxiv.org/pdf/{arxiv_id}")

    keywords_html = ""
    if keywords_zh:
        keywords_html = "".join(f'<span class="keyword">{k}</span>' for k in keywords_zh)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>#{rank} {title_zh or title}</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
      background: #f5f7fa;
      color: #333;
      line-height: 1.7;
    }}
    .container {{
      max-width: 900px;
      margin: 0 auto;
      padding: 20px;
    }}
    .breadcrumb {{
      font-size: 14px;
      color: #888;
      margin-bottom: 20px;
    }}
    .breadcrumb a {{
      color: #4a90e2;
      text-decoration: none;
    }}
    .rank-badge {{
      display: inline-block;
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      color: white;
      font-size: 14px;
      font-weight: bold;
      padding: 4px 14px;
      border-radius: 20px;
      margin-bottom: 16px;
    }}
    .card {{
      background: white;
      border-radius: 12px;
      box-shadow: 0 2px 12px rgba(0,0,0,0.08);
      padding: 32px;
      margin-bottom: 20px;
    }}
    .title-en {{
      font-size: 22px;
      font-weight: 700;
      color: #1a1a2e;
      margin-bottom: 10px;
      line-height: 1.4;
    }}
    .title-zh {{
      font-size: 18px;
      color: #4a90e2;
      font-weight: 600;
      margin-bottom: 16px;
    }}
    .meta {{
      font-size: 13px;
      color: #888;
      border-top: 1px solid #f0f0f0;
      padding-top: 12px;
      margin-top: 12px;
    }}
    .meta span {{ margin-right: 16px; }}
    .links {{ margin-top: 12px; }}
    .links a {{
      display: inline-block;
      padding: 6px 16px;
      border-radius: 6px;
      text-decoration: none;
      font-size: 13px;
      font-weight: 500;
      margin-right: 10px;
      margin-top: 6px;
      transition: opacity 0.2s;
    }}
    .links a:hover {{ opacity: 0.85; }}
    .btn-arxiv {{ background: #b31b1b; color: white; }}
    .btn-pdf {{ background: #e74c3c; color: white; }}
    .section-title {{
      font-size: 16px;
      font-weight: 700;
      color: #555;
      margin-bottom: 12px;
      padding-left: 10px;
      border-left: 4px solid #4a90e2;
    }}
    .summary-box {{
      background: linear-gradient(135deg, #e8f4fd 0%, #f0f7ff 100%);
      border-radius: 8px;
      padding: 20px;
      font-size: 15px;
      color: #2c3e50;
      margin-bottom: 20px;
      border-left: 4px solid #4a90e2;
    }}
    .keywords {{ margin-bottom: 20px; }}
    .keyword {{
      display: inline-block;
      background: #eef2ff;
      color: #5a67d8;
      font-size: 12px;
      padding: 4px 12px;
      border-radius: 20px;
      margin: 4px 4px 4px 0;
      font-weight: 500;
    }}
    .abstract-en {{
      font-size: 14px;
      color: #666;
      line-height: 1.8;
      margin-bottom: 16px;
    }}
    .abstract-zh {{
      font-size: 14px;
      color: #444;
      line-height: 1.8;
    }}
    .tab-container {{ margin-bottom: 20px; }}
    .tab-btns {{
      display: flex;
      gap: 8px;
      margin-bottom: 12px;
    }}
    .tab-btn {{
      padding: 6px 16px;
      border: none;
      border-radius: 6px;
      cursor: pointer;
      font-size: 13px;
      font-weight: 500;
      transition: all 0.2s;
    }}
    .tab-btn.active {{
      background: #4a90e2;
      color: white;
    }}
    .tab-btn:not(.active) {{
      background: #f0f0f0;
      color: #666;
    }}
    .tab-content {{ display: none; }}
    .tab-content.active {{ display: block; }}
    .back-btn {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 8px 18px;
      background: white;
      border: 1px solid #e0e0e0;
      border-radius: 8px;
      color: #555;
      text-decoration: none;
      font-size: 14px;
      margin-bottom: 20px;
      transition: all 0.2s;
    }}
    .back-btn:hover {{
      background: #f5f5f5;
      border-color: #ccc;
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="breadcrumb">
      <a href="/">首页</a> &rsaquo; <a href="/{week_str}">{week_str}</a> &rsaquo; {arxiv_id}
    </div>

    <a href="/{week_str}" class="back-btn">← 返回本周列表</a>

    <div class="rank-badge">#{rank} 本周热门</div>

    <div class="card">
      <div class="title-en">{title}</div>
      {f'<div class="title-zh">{title_zh}</div>' if title_zh else ''}
      <div class="meta">
        {f'<span>👥 {authors[:120]}{"..." if len(authors) > 120 else ""}</span>' if authors else ''}
        {f'<span>📅 {submitted}</span>' if submitted else ''}
        <span>🆔 {arxiv_id}</span>
      </div>
      <div class="links">
        <a href="{arxiv_url}" target="_blank" class="btn-arxiv">arXiv 页面</a>
        <a href="{pdf_url}" target="_blank" class="btn-pdf">📄 PDF 原文</a>
        {f'<a href="/{week_str}/papers/{arxiv_id}_zh.pdf" class="btn-full" style="background:#059669;color:white;display:inline-block;padding:6px 16px;border-radius:6px;text-decoration:none;font-size:13px;font-weight:500;margin-right:10px;margin-top:6px;">📑 全文中文PDF</a>' if pdf_zh else ''}
      </div>
    </div>

    {f'''<div class="card">
      <div class="section-title">💡 核心贡献 (AI 速读)</div>
      <div class="summary-box">{summary_zh}</div>
    </div>''' if summary_zh else ''}

    {f'''<div class="card">
      <div class="section-title">🏷️ 关键词</div>
      <div class="keywords">{keywords_html}</div>
    </div>''' if keywords_zh else ''}

    <div class="card">
      <div class="section-title">📝 摘要</div>
      <div class="tab-container">
        <div class="tab-btns">
          <button class="tab-btn active" onclick="switchTab('zh')">中文翻译</button>
          <button class="tab-btn" onclick="switchTab('en')">English Original</button>
        </div>
        <div id="tab-zh" class="tab-content active">
          <div class="abstract-zh">{abstract_zh or '(翻译暂不可用)'}</div>
        </div>
        <div id="tab-en" class="tab-content">
          <div class="abstract-en">{abstract or '(Abstract not available)'}</div>
        </div>
      </div>
    </div>
  </div>

  <script>
    function switchTab(lang) {{
      document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.getElementById('tab-' + lang).classList.add('active');
      event.target.classList.add('active');
    }}
  </script>
</body>
</html>"""
    return html


def translate_and_save(arxiv_id, output_dir, rank=1, week_str="", config=None):
    """翻译一篇论文并保存 HTML（优先命中共享缓存，跨 daily/weekly/monthly 复用）"""
    if config is None:
        config = load_api_config()

    print(f"\n📝 [{rank}] 处理论文: {arxiv_id}", flush=True)

    # ── 1. 命中 paper store（跨 mode/key 复用，无需重复翻译）─────────────────
    cached = paper_store_read(arxiv_id)
    if cached:
        print(f"  ⚡ paper store 命中: {cached['title_zh'][:50]}...", flush=True)
        meta = {
            "arxiv_id": arxiv_id,
            "title":    cached.get("title", ""),
            "abstract": cached.get("abstract", ""),
            "authors":  cached.get("authors", ""),
            "submitted":cached.get("submitted", ""),
            "url":      cached.get("url", f"https://arxiv.org/abs/{arxiv_id}"),
            "pdf_url":  cached.get("pdf_url", f"https://arxiv.org/pdf/{arxiv_id}"),
        }
        translation = {
            "title_zh":   cached.get("title_zh", ""),
            "abstract_zh":cached.get("abstract_zh", ""),
            "keywords_zh":cached.get("keywords_zh", []),
            "summary_zh": cached.get("summary_zh", ""),
        }
    else:
        # ── 2. 无缓存：抓取元数据 + LLM 翻译 ──────────────────────────────────
        print(f"  🔍 获取元数据...", flush=True)
        meta = fetch_arxiv_metadata(arxiv_id)

        if meta.get("title"):
            print(f"  📌 标题: {meta['title'][:60]}...", flush=True)

        print(f"  🌐 翻译中...", flush=True)
        translation = translate_paper(meta, config)

        if translation.get("title_zh"):
            print(f"  ✅ 译文: {translation['title_zh'][:50]}...", flush=True)
            # 写入 paper store，供后续 mode/key 直接复用
            paper_store_write(arxiv_id, meta, translation)

    # ── 3. 生成 HTML（week_str 决定返回链接，每个 mode/key 单独生成）──────────
    html = generate_html(meta, translation, rank, week_str)

    os.makedirs(output_dir, exist_ok=True)
    html_path = os.path.join(output_dir, f"{arxiv_id}.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"  💾 已保存: {html_path}", flush=True)

    return {
        "arxiv_id":   arxiv_id,
        "title":      meta.get("title", ""),
        "title_zh":   translation.get("title_zh", ""),
        "summary_zh": translation.get("summary_zh", ""),
        "keywords_zh":translation.get("keywords_zh", []),
        "authors":    meta.get("authors", ""),
        "submitted":  meta.get("submitted", ""),
        "url":        meta.get("url", ""),
        "html_file":  f"{arxiv_id}.html",
    }


def main():
    parser = argparse.ArgumentParser(description="翻译 arXiv 论文")
    parser.add_argument("arxiv_id", help="arXiv 论文 ID (如: 2602.05400)")
    parser.add_argument("-o", "--output", default="/root/workspace/paper-trans/weekly",
                        help="输出目录")
    parser.add_argument("--rank", type=int, default=1, help="论文排名")
    parser.add_argument("--week", default="", help="周数 (如: 2026-W08)")
    args = parser.parse_args()

    config = load_api_config()
    print(f"📡 使用模型: {config['model']}", flush=True)

    result = translate_and_save(args.arxiv_id, args.output, args.rank, args.week, config)
    print(f"\n✅ 完成: {json.dumps(result, ensure_ascii=False, indent=2)}")


if __name__ == "__main__":
    main()
