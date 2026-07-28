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
    parser.add_argument(
        "--scan-pdf-text",
        action="store_true",
        help="对缺少 TeX 的有效 PDF 启用保守文本质量扫描",
    )
    parser.add_argument(
        "--pdf-text-max-files",
        type=int,
        default=250,
        help="单次最多扫描的无 TeX PDF 数（默认 250）",
    )
    parser.add_argument(
        "--pdf-text-max-pages",
        type=int,
        default=200,
        help="每份 PDF 最多提取页数（默认 200）",
    )
    parser.add_argument(
        "--pdf-text-max-bytes",
        type=int,
        default=8 * 1024 * 1024,
        help="每份 PDF 最大提取文本字节数（默认 8 MiB）",
    )
    parser.add_argument(
        "--pdf-text-timeout",
        type=int,
        default=120,
        help="每份 PDF 的 pdftotext 超时秒数（默认 120）",
    )
    parser.add_argument(
        "--pdf-text-workers",
        type=int,
        default=4,
        help="PDF 文本提取并发数，范围 1-8（默认 4）",
    )
    parser.add_argument(
        "--pdf-text-no-cache",
        action="store_true",
        help="忽略并禁用 PDF 文本质量指标缓存",
    )
    args = parser.parse_args()

    report = audit_repository(
        DATA_DIR,
        LOGS_DIR,
        scan_pdf_text=args.scan_pdf_text,
        pdf_text_max_files=args.pdf_text_max_files,
        pdf_text_max_pages=args.pdf_text_max_pages,
        pdf_text_max_bytes=args.pdf_text_max_bytes,
        pdf_text_timeout=args.pdf_text_timeout,
        pdf_text_workers=args.pdf_text_workers,
        pdf_text_cache=not args.pdf_text_no_cache,
    )
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
        coverage = report["quality_coverage"]
        print(
            "翻译质量 TeX 覆盖 "
            f"{coverage['valid_pdfs_with_tex']}/"
            f"{coverage['valid_referenced_pdfs']} "
            f"({coverage['valid_pdf_tex_coverage_pct']}%)，"
            f"缺 TeX {coverage['valid_pdfs_without_tex']} 篇"
        )
        pdf_scan = report["quality_pdf_text_scan"]
        if pdf_scan["enabled"]:
            print(
                "PDF 文本质量补扫 "
                f"成功 {pdf_scan['scanned']}/"
                f"{pdf_scan['attempted']}，"
                f"错误 {pdf_scan['errors']}，"
                f"总质量覆盖 "
                f"{coverage['valid_pdfs_quality_scanned']}/"
                f"{coverage['valid_referenced_pdfs']} "
                f"({coverage['valid_pdf_quality_coverage_pct']}%)"
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
