#!/usr/bin/env python3
"""
Paper Trans — 每周 Top 10 论文抓取与翻译
用法:
  python3 run_weekly.py                # 本周（含全文翻译）
  python3 run_weekly.py 2026-W08       # 指定周（含全文翻译）
  python3 run_weekly.py 2026-W08 --no-full  # 仅摘要翻译
定时: 每周日凌晨 2:00
"""
import sys

from paperhub.runner import run_fetch_mode_cli

if __name__ == "__main__":
    sys.exit(run_fetch_mode_cli("weekly"))
