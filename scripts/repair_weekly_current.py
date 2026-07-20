#!/usr/bin/env python3
"""Sunday 02:00 current-week translation/PDF repair entrypoint."""

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from paperhub.weekly_repair import run_current_week_repair


def main() -> int:
    parser = argparse.ArgumentParser(description="修复当前 weekly 的摘要/翻译/PDF 编译问题")
    parser.add_argument("--key", help="指定 weekly key；默认当前 ISO 周")
    parser.add_argument("--wait-seconds", type=int, default=10_800)
    parser.add_argument("--poll-seconds", type=int, default=15)
    args = parser.parse_args()
    result = run_current_week_repair(
        key=args.key,
        wait_seconds=args.wait_seconds,
        poll_seconds=args.poll_seconds,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0 if result.get("status") in {"ok", "already_running"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
