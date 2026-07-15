#!/usr/bin/env python3
"""
Paper Trans — 每日 Top 3 论文抓取与翻译
用法:
  python3 run_daily.py                 # 今天（含全文翻译）
  python3 run_daily.py 2026-02-19      # 指定日期（含全文翻译）
  python3 run_daily.py 2026-02-19 --no-full  # 仅摘要翻译
定时: 每天 23:00
"""
import sys

from paperhub.runner import run_fetch_mode_cli

if __name__ == "__main__":
    sys.exit(run_fetch_mode_cli("daily"))
