#!/usr/bin/env python3
"""
Paper Trans — 每月 Top 10 论文抓取与翻译
用法:
  python3 run_monthly.py            # 本月（含全文翻译）
  python3 run_monthly.py 2026-02    # 指定月份（含全文翻译）
  python3 run_monthly.py 2026-02 --no-full  # 仅摘要翻译
定时: 每月 28 日凌晨 2:00
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_hf import current_month_key
from run_papers import run

if __name__ == "__main__":
    args  = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    key  = args[0] if args else current_month_key()
    full = "--no-full" not in flags   # 默认执行全文翻译

    ok = run(mode="monthly", key=key, limit=10, do_full_translate=full)
    sys.exit(0 if ok else 1)
