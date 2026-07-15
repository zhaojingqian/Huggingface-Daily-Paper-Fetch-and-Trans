#!/usr/bin/env python3
"""
Paper Trans — 每月 Top 10 论文抓取与翻译
用法:
  python3 run_monthly.py            # 本月（含全文翻译）
  python3 run_monthly.py 2026-02    # 指定月份（含全文翻译）
  python3 run_monthly.py 2026-02 --no-full  # 仅摘要翻译
定时: 每月 28 日凌晨 2:00
"""
import sys

from paperhub.runner import run_fetch_mode_cli

if __name__ == "__main__":
    sys.exit(run_fetch_mode_cli("monthly"))
