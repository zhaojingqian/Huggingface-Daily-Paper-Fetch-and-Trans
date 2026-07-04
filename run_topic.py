#!/usr/bin/env python3
"""
Paper Trans — 主题订阅 Top 3 论文抓取与翻译

用法:
  python3 run_topic.py opd
  python3 run_topic.py opd --no-full
  python3 run_topic.py --all
  python3 run_topic.py opd 2026-07-04 --force
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from topic_engine import run_all_topics, run_topic


def main():
    parser = argparse.ArgumentParser(description="运行主题订阅论文检索")
    parser.add_argument("topic", nargs="?", help="topic slug 或新主题关键词")
    parser.add_argument("key", nargs="?", help="日期 YYYY-MM-DD，默认今天")
    parser.add_argument("--all", action="store_true", help="运行所有启用主题")
    parser.add_argument("--no-full", action="store_true", help="只做摘要翻译，不生成全文中文 PDF")
    parser.add_argument("--force", action="store_true", help="忽略 topic seen 去重，强制重排当天")
    parser.add_argument("--refresh-terms", action="store_true", help="重新调用 topic LLM 生成检索词")
    args = parser.parse_args()

    if args.all:
        run_all_topics(key=args.key, do_full_translate=not args.no_full, force=args.force)
        return 0
    if not args.topic:
        parser.error("需要 topic，或使用 --all")
    run_topic(
        args.topic,
        key=args.key,
        do_full_translate=not args.no_full,
        force=args.force,
        refresh_terms=args.refresh_terms,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
