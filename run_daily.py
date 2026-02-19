#!/usr/bin/env python3
"""
Paper Trans — 每日 Top 3 论文抓取与翻译
用法:
  python3 run_daily.py                 # 今天（含全文翻译）
  python3 run_daily.py 2026-02-19      # 指定日期（含全文翻译）
  python3 run_daily.py 2026-02-19 --no-full  # 仅摘要翻译
定时: 每天 23:00
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_hf import today_key
from run_papers import run

if __name__ == "__main__":
    args  = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    key  = args[0] if args else today_key()
    full = "--no-full" not in flags   # 默认执行全文翻译

    ok = run(mode="daily", key=key, limit=3, do_full_translate=full)
    sys.exit(0 if ok else 1)
