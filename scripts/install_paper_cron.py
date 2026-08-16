#!/usr/bin/env python3
"""Install one managed Paper Trans cron block without touching other jobs."""

import argparse
import os
import subprocess
import sys


BEGIN = "# BEGIN PAPER-TRANS MANAGED"
END = "# END PAPER-TRANS MANAGED"
LEGACY_PATHS = (
    "/root/workspace/paper-trans",
    "/root/workspace/apps/paper-trans",
)
LEGACY_COMMENT_MARKERS = (
    "主抓取任务",
    "daily top",
    "daily 兜底",
    "weekly top",
    "weekly 兜底",
    "monthly top",
    "monthly 兜底",
    "容器维护",
    "容器空闲",
    "Docker 翻译缓存",
    "修复任务",
    "daily 补翻译",
    "daily PDF 重试",
    "monthly 补翻译",
    "monthly PDF 重试",
    "本周五模式",
    "topic subscriptions",
    "topic PDF 重试",
    "修复本周已发布五模式论文",
)


def managed_block(root):
    ctl = os.environ.get("WORKSPACE_CTL", "/usr/local/bin/workspace-ctl")
    return [
        BEGIN,
        "# Acquisition only; runtime and paths are owned by workspace-ctl.",
        "0 23 * * * {ctl} paper daily >> {root}/logs/cron-daily.log 2>&1".format(
            ctl=ctl, root=root
        ),
        "30 1 * * * {ctl} paper topic --all >> {root}/logs/cron-topic.log 2>&1".format(
            ctl=ctl, root=root
        ),
        "0 2 * * 0 {ctl} paper weekly >> {root}/logs/cron-weekly.log 2>&1".format(
            ctl=ctl, root=root
        ),
        "0 2 28 * * {ctl} paper monthly >> {root}/logs/cron-monthly.log 2>&1".format(
            ctl=ctl, root=root
        ),
        "# Required current-week all-mode repair; its coordinator waits for acquisition.",
        "0 2 * * 0 {ctl} paper repair-weekly >> {root}/logs/repair.log 2>&1".format(
            ctl=ctl, root=root
        ),
        "# Cache, restart, post-processing, all-mode PDF retry, and Sunday host cleanup.",
        "0 6 * * * {ctl} paper maintenance >> {root}/logs/maintenance.log 2>&1".format(
            ctl=ctl, root=root
        ),
        END,
    ]


def is_legacy_paper_line(line):
    stripped = line.strip()
    if any(path in line for path in LEGACY_PATHS):
        return True
    if "$PTDIR" in line or "$RLOG" in line:
        return True
    if "workspace-ctl paper " in line:
        return True
    return stripped.startswith(
        ("PTDIR=", "RLOG=", "PYTHON=", "GPT_ACADEMIC_CONTAINER=")
    )


def is_legacy_paper_comment(line):
    stripped = line.strip()
    return stripped.startswith("#") and any(
        marker in stripped for marker in LEGACY_COMMENT_MARKERS
    )


def render_crontab(existing, root):
    kept = []
    in_managed = False
    for line in existing.splitlines():
        if line.strip() == BEGIN:
            in_managed = True
            continue
        if line.strip() == END:
            in_managed = False
            continue
        if in_managed or is_legacy_paper_line(line) or is_legacy_paper_comment(line):
            continue
        value = line.rstrip()
        if not value and (not kept or not kept[-1]):
            continue
        kept.append(value)
    while kept and not kept[-1]:
        kept.pop()
    return "\n".join(kept + ["", *managed_block(root), ""])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    current = subprocess.run(
        ["crontab", "-l"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        universal_newlines=True,
    )
    rendered = render_crontab(current.stdout if current.returncode == 0 else "", args.root)
    if args.dry_run:
        sys.stdout.write(rendered)
        return 0
    installed = subprocess.run(["crontab", "-"], input=rendered, universal_newlines=True)
    return installed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
