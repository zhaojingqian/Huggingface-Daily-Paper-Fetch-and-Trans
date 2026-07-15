#!/usr/bin/env python3
"""Audit every index, paper-store record, PDF status, and failure artifact."""

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from paperhub.audit import audit_repository
from paperhub.paths import DATA_DIR, LOGS_DIR


def main():
    parser = argparse.ArgumentParser(description="全项目论文数据一致性扫描")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON")
    parser.add_argument("--strict", action="store_true", help="存在任何问题时返回非零状态")
    args = parser.parse_args()

    report = audit_repository(DATA_DIR, LOGS_DIR)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(
            f"索引 {report['index_files']} 个，引用论文 {report['unique_referenced_papers']} 篇，"
            f"paper store {report['paper_store_json_files']} 条"
        )
        for name, count in report["issue_counts"].items():
            print(f"  {name}: {count}")
        print(
            f"失败日志 {report['failure_logs']}，结构化诊断 {report['failure_sidecars']}，"
            f"失败 TeX {report['failed_tex_backups']}"
        )
    artifact_failures = (
        report["failure_logs"]
        + report["failure_sidecars"]
        + report["failed_tex_backups"]
    )
    if args.strict and (any(report["issue_counts"].values()) or artifact_failures):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
