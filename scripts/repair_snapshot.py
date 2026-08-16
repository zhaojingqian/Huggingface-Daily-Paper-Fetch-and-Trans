#!/usr/bin/env python3
"""Fast operator snapshot; no PDF extraction and no publication-lock waits."""

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from paperhub.failure_reports import load_failure_records, summarize_failures
from paperhub.paths import DATA_DIR, LOGS_DIR


def _command(*args):
    try:
        result = subprocess.run(
            list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            universal_newlines=True,
            timeout=3,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _active_jobs():
    """Return bounded process identities without dumping supervisor source."""
    output = _command("ps", "-eo", "pid=,args=")
    jobs = []
    seen = set()
    patterns = (
        ("daily", "run_daily.py"),
        ("weekly", "run_weekly.py"),
        ("monthly", "run_monthly.py"),
        ("topic", "run_topic.py"),
        ("translate", "full_translate_driver.py"),
    )
    for line in output.splitlines():
        match = re.match(r"\s*(\d+)\s+(.*)$", line)
        if not match:
            continue
        pid, command = match.groups()
        kind = next((name for name, token in patterns if token in command), "")
        if not kind:
            continue
        paper = re.search(r"(?<!\d)(\d{4}\.\d{4,5})(?!\d)", command)
        identity = (kind, paper.group(1) if paper else "")
        # A host wrapper, docker exec supervisor and container child describe
        # the same translation. One compact identity is enough for routing.
        if identity in seen:
            continue
        seen.add(identity)
        jobs.append({
            "pid": int(pid),
            "type": kind,
            "paper_id": identity[1] or None,
        })
        if len(jobs) >= 5:
            break
    return jobs


def snapshot():
    failures = load_failure_records(os.path.join(LOGS_DIR, "pdf_errors"))
    full_failure_summary = summarize_failures(failures)
    failure_summary = {
        "total": full_failure_summary["total"],
        "by_category": full_failure_summary["by_category"],
        "by_retry_strategy": full_failure_summary["by_retry_strategy"],
    }
    statuses = Counter()
    index_files = 0
    for base, _, files in os.walk(DATA_DIR):
        if "index.json" not in files:
            continue
        index_files += 1
        path = os.path.join(base, "index.json")
        try:
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
            for paper in payload.get("papers", []):
                statuses[str(paper.get("pdf_status") or "none")] += 1
        except (OSError, ValueError, AttributeError):
            statuses["bad_index"] += 1

    disk = os.statvfs(ROOT)
    total = disk.f_blocks * disk.f_frsize
    free = disk.f_bavail * disk.f_frsize
    active = _active_jobs()
    image_bytes = _command(
        "docker",
        "image",
        "inspect",
        "paper-trans-latex-slim:latest",
        "--format",
        "{{.Size}}",
    )
    cache_output = _command(
        "docker",
        "exec",
        "gpt-academic-latex-slim",
        "du",
        "-sh",
        "/gpt/gpt_log",
    )
    cache_size = cache_output.split()[0] if cache_output else ""
    return {
        "disk": {
            "used_pct": round(100.0 * (total - free) / max(1, total), 1),
            "free_gib": round(free / 1024 ** 3, 2),
        },
        "docker": {
            "image_gib": round(int(image_bytes or 0) / 1024 ** 3, 2),
            "runtime_cache": cache_size or "unknown",
        },
        "indexes": index_files,
        "pdf_status_references": dict(statuses),
        "failures": failure_summary,
        "active": active,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = snapshot()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    print(
        "disk={used_pct}% free={free_gib}GiB image={image_gib}GiB cache={cache} "
        "indexes={indexes} failures={failures} active={active}".format(
            used_pct=report["disk"]["used_pct"],
            free_gib=report["disk"]["free_gib"],
            image_gib=report["docker"]["image_gib"],
            cache=report["docker"]["runtime_cache"],
            indexes=report["indexes"],
            failures=report["failures"]["total"],
            active=len(report["active"]),
        )
    )
    for category, count in report["failures"]["by_category"].items():
        print("{}={}".format(category, count))


if __name__ == "__main__":
    main()
