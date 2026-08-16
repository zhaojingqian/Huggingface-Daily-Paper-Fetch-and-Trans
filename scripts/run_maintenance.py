#!/usr/bin/env python3
"""One bounded maintenance entrypoint for Paper Trans.

Acquisition keeps its own schedule.  This coordinator owns all recurring
post-processing, PDF retry, cache cleanup, restart, and weekly host cleanup so
cron does not encode workflow logic in a dozen independent lines.
"""

import argparse
import datetime as dt
import os
import subprocess
import sys


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON = os.environ.get("PAPER_TRANS_PYTHON") or os.environ.get(
    "SERVER_PYTHON", sys.executable
)


def maintenance_commands(now=None, include_host_cleanup=True):
    now = now or dt.datetime.now()
    commands = [
        [os.path.join(ROOT, "scripts", "cleanup_docker_cache.sh")],
        [os.path.join(ROOT, "scripts", "restart_translation_container.sh")],
        [PYTHON, os.path.join(ROOT, "run_repair.py"), "--post", "--days", "2"],
        [
            PYTHON,
            os.path.join(ROOT, "run_repair.py"),
            "--retry-pdf",
            "--days",
            "7",
        ],
    ]
    if now.day == 28:
        commands.extend(
            [
                [
                    PYTHON,
                    os.path.join(ROOT, "run_repair.py"),
                    "--post",
                    "--mode",
                    "monthly",
                    "--days",
                    "60",
                ],
                [
                    PYTHON,
                    os.path.join(ROOT, "run_repair.py"),
                    "--retry-pdf",
                    "--mode",
                    "monthly",
                    "--days",
                    "60",
                ],
            ]
        )
    if include_host_cleanup and now.weekday() == 6:
        commands.append([os.path.join(ROOT, "scripts", "weekly_cleanup.sh")])
    return commands


def main():
    parser = argparse.ArgumentParser(description="Run the bounded maintenance cycle")
    parser.add_argument(
        "--no-host-cleanup",
        action="store_true",
        help="skip the Sunday host cleanup step",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    failures = []
    for command in maintenance_commands(
        include_host_cleanup=not args.no_host_cleanup
    ):
        print("[maintenance] " + " ".join(command), flush=True)
        if args.dry_run:
            continue
        result = subprocess.run(command, cwd=ROOT)
        if result.returncode:
            failures.append((result.returncode, command))
            print(
                "[maintenance] WARN: step failed; continuing independent steps",
                flush=True,
            )

    if failures:
        print(
            "[maintenance] failed steps: "
            + "; ".join(
                "exit={} {}".format(code, " ".join(command))
                for code, command in failures
            ),
            flush=True,
        )
        return 1
    print("[maintenance] complete", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
