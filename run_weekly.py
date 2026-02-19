#!/usr/bin/env python3
"""
Paper Trans — 每周 Top 10 论文抓取与翻译
用法:
  python3 run_weekly.py                # 本周（含全文翻译）
  python3 run_weekly.py 2026-W08       # 指定周（含全文翻译）
  python3 run_weekly.py 2026-W08 --no-full  # 仅摘要翻译
定时: 每周日凌晨 2:00
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_hf import current_week_key
from run_papers import run

if __name__ == "__main__":
    args  = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    # ISO 8601：周日 = 当前周第 7 天，cron 在周日 02:00 触发时仍属于本周
    key  = args[0] if args else current_week_key()
    full = "--no-full" not in flags   # 默认执行全文翻译

    ok = run(mode="weekly", key=key, limit=10, do_full_translate=full)
    sys.exit(0 if ok else 1)
