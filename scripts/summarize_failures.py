#!/usr/bin/env python3
"""Print a compact overview of current PDF translation/compile failures."""

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from paperhub.failure_reports import load_failure_records, summarize_failures
from paperhub.paths import LOGS_DIR


def main():
    parser = argparse.ArgumentParser(description="汇总 PDF 翻译/编译失败分类")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON")
    args = parser.parse_args()

    records = load_failure_records(os.path.join(LOGS_DIR, "pdf_errors"))
    summary = summarize_failures(records)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    print(f"当前失败: {summary['total']} 篇")
    for category, count in summary["by_category"].items():
        print(f"  {category}: {count}")
    if records:
        print("\n论文明细:")
        for item in records:
            print(
                f"  {item['arxiv_id']}  {item.get('category', 'unknown')}  "
                f"{item.get('retry_strategy', 'unknown')}  {item.get('repair_action', '')}"
            )


if __name__ == "__main__":
    main()
