#!/usr/bin/env python3
"""
统一的 Hugging Face Papers 抓取器
支持 daily / weekly / monthly 三种模式
"""
import re
import sys
import json
import requests
from collections import OrderedDict
from datetime import datetime, timedelta

PROXY = "http://127.0.0.1:7890"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}


def _get_proxies(use_proxy):
    return {"http": PROXY, "https": PROXY} if use_proxy else None


def _parse_papers(html, limit):
    """从 HF papers 页面 HTML 中解析论文列表"""
    # 找所有 arxiv ID（保序去重）
    id_pattern = r'href="/papers/(\d{4}\.\d{4,})"'
    matches = re.findall(id_pattern, html)

    # 尝试同时抓取 upvotes 和 title（HF 页面结构）
    # upvote 数字紧跟在 arxiv id 链接附近，用于排序
    seen = OrderedDict()
    for aid in matches:
        if aid not in seen:
            seen[aid] = {"arxiv_id": aid, "title": "", "upvotes": 0,
                         "url": f"https://arxiv.org/abs/{aid}"}

    # 填入标题（<h3> 紧跟论文链接）
    block_pat = r'href="/papers/(\d{4}\.\d{4,})"[^>]*>.*?<h3[^>]*>(.*?)</h3>'
    for aid, raw in re.findall(block_pat, html, re.DOTALL):
        if aid in seen:
            clean = re.sub(r"<[^>]+>", "", raw).strip()
            if clean:
                seen[aid]["title"] = clean

    # 尝试提取 upvote 数字（出现在相同区块附近的数字）
    # 格式：<div ...>317</div> 或类似
    upvote_pat = (
        r'href="/papers/(\d{4}\.\d{4,})".*?'
        r'<div[^>]*>\s*(\d+)\s*</div>'
    )
    for aid, votes_str in re.findall(upvote_pat, html, re.DOTALL):
        if aid in seen:
            try:
                seen[aid]["upvotes"] = int(votes_str)
            except ValueError:
                pass

    papers = list(seen.values())[:limit]

    # 备用：如果主解析失败，直接找 arxiv ID
    if not papers:
        all_ids = list(OrderedDict.fromkeys(re.findall(r"\b(\d{4}\.\d{4,5})\b", html)))
        papers = [{"arxiv_id": aid, "title": "", "upvotes": 0,
                   "url": f"https://arxiv.org/abs/{aid}"}
                  for aid in all_ids[:limit]]

    return papers


def fetch_hf_papers(mode, key, limit=10, use_proxy=True):
    """
    通用抓取接口
    mode: 'daily' | 'weekly' | 'monthly'
    key:  日期字符串，如 '2026-02-19' / '2026-W08' / '2026-02'
    limit: 最多返回几篇
    """
    if mode == "daily":
        url = f"https://huggingface.co/papers/date/{key}"
    elif mode == "weekly":
        url = f"https://huggingface.co/papers/week/{key}"
    elif mode == "monthly":
        url = f"https://huggingface.co/papers/month/{key}"
    else:
        raise ValueError(f"未知 mode: {mode}")

    print(f"[fetch] {mode.upper()} {key} -> {url}", flush=True)

    try:
        resp = requests.get(url, headers=HEADERS,
                            proxies=_get_proxies(use_proxy), timeout=30)
        resp.raise_for_status()
        papers = _parse_papers(resp.text, limit)
        print(f"[fetch] 找到 {len(papers)} 篇", flush=True)
        return papers
    except requests.exceptions.ProxyError:
        if use_proxy:
            print("[fetch] 代理失败，尝试直连...", flush=True)
            return fetch_hf_papers(mode, key, limit, use_proxy=False)
        return []
    except Exception as e:
        print(f"[fetch] 请求失败: {e}", flush=True)
        return []


# ── 便捷日期函数 ─────────────────────────────────────────────────────────────
def today_key():
    """当前日期 YYYY-MM-DD。daily cron 在 23:00 触发，取当天日期。"""
    return datetime.now().strftime("%Y-%m-%d")

def current_month_key():
    """当前年月 YYYY-MM。monthly cron 在 28 日 02:00 触发，取当月。"""
    return datetime.now().strftime("%Y-%m")

def current_week_key():
    """
    当前 ISO 周 YYYY-WNN。
    ISO 8601：周一为第 1 天，周日为第 7 天（仍属于本周）。
    weekly cron 在周日 02:00 触发，此时 isocalendar() 返回本周编号，正确。
    """
    now = datetime.now()
    y, w, _ = now.isocalendar()
    return f"{y}-W{w:02d}"

def last_week_key():
    """
    上一个完整 ISO 周 YYYY-WNN（仅在需要补抓历史数据时使用）。
    注意：若在周日调用，会得到上上周，请勿在 cron 中使用。
    """
    now = datetime.now()
    last_mon = now - timedelta(days=now.weekday() + 7)
    y, w, _ = last_mon.isocalendar()
    return f"{y}-W{w:02d}"


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "daily"
    key  = sys.argv[2] if len(sys.argv) > 2 else {
        "daily": today_key(),
        "weekly": current_week_key(),
        "monthly": current_month_key(),
    }.get(mode, today_key())
    limit = int(sys.argv[3]) if len(sys.argv) > 3 else 10
    papers = fetch_hf_papers(mode, key, limit)
    print(json.dumps(papers, indent=2, ensure_ascii=False))
