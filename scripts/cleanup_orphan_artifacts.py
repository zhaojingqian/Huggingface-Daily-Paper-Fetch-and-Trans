#!/usr/bin/env python3
"""Safely remove aged artifacts that are no longer referenced by any index.

The initial filesystem walk is only a candidate discovery pass.  Before any
candidate is reported or deleted, the helper acquires the repository catalog
lock exclusively and the matching per-paper lock, then scans every published
index again.  This prevents a cleanup run from racing a compliant publisher or
paper-store writer.
"""

import argparse
import json
import os
import re
import stat
import sys
import time
from collections import Counter, defaultdict


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from paperhub.publication_lock import (  # noqa: E402
    LOCK_EXCLUSIVE,
    PublicationBusyError,
    PublicationLock,
    catalog_publication_lock,
    paper_lock_path,
)


CONTENT_MODES = ("daily", "weekly", "monthly", "manual", "topic")
ARXIV_ID_PATTERN = r"(?P<arxiv_id>\d{4}\.\d{4,5})"
ARTIFACT_SPECS = (
    (
        "pdf",
        ("data", "papers"),
        re.compile(rf"^{ARXIV_ID_PATTERN}_zh\.pdf$"),
    ),
    (
        "sidecar",
        ("logs", "pdf_errors"),
        re.compile(rf"^{ARXIV_ID_PATTERN}\.(?:json|log)$"),
    ),
    (
        "failed_tex",
        ("data", "tex_backup_failed"),
        re.compile(rf"^{ARXIV_ID_PATTERN}_merge_translate_zh\.tex$"),
    ),
)
DEFAULT_GRACE_DAYS = 3
PAPER_LOCK_BATCH_SIZE = 128


class CleanupSafetyError(RuntimeError):
    """Raised when orphan status cannot be proven safely."""


def _repository_paths(project_root):
    project_root = os.path.realpath(project_root)
    return {
        "root": project_root,
        "data": os.path.join(project_root, "data"),
        "locks": os.path.join(project_root, "locks"),
    }


def _regular_file_stat(path):
    """Return lstat for a regular non-symlink file, otherwise ``None``."""
    try:
        current = os.lstat(path)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(current.st_mode):
        return None
    return current


def _discover_artifacts(project_root, *, grace_seconds, now):
    """Group preliminarily old candidates and count recent recognized files."""
    grouped = defaultdict(list)
    recent = Counter()
    for kind, relative_dir, filename_re in ARTIFACT_SPECS:
        directory = os.path.join(project_root, *relative_dir)
        if not os.path.isdir(directory):
            continue
        for filename in sorted(os.listdir(directory)):
            match = filename_re.fullmatch(filename)
            if not match:
                continue
            path = os.path.join(directory, filename)
            current = _regular_file_stat(path)
            if current is None:
                continue
            if now - current.st_mtime < grace_seconds:
                recent[kind] += 1
                continue
            grouped[match.group("arxiv_id")].append(
                {"kind": kind, "path": path}
            )
    return dict(grouped), recent


def _scan_references(data_dir):
    """Strictly scan all indexes; any ambiguity blocks the entire deletion."""
    referenced_ids = set()
    errors = []
    if not os.path.isdir(data_dir) or os.path.islink(data_dir):
        return referenced_ids, [
            f"{data_dir}: data directory is missing or is a symbolic link"
        ]
    for mode in CONTENT_MODES:
        mode_dir = os.path.join(data_dir, mode)
        if not os.path.exists(mode_dir):
            continue
        if not os.path.isdir(mode_dir) or os.path.islink(mode_dir):
            errors.append(
                f"{mode_dir}: content mode path is not a trusted directory"
            )
            continue
        walk_errors = []
        for current, dirs, files in os.walk(
            mode_dir,
            followlinks=False,
            onerror=walk_errors.append,
        ):
            symlink_dirs = sorted(
                name
                for name in dirs
                if os.path.islink(os.path.join(current, name))
            )
            errors.extend(
                f"{os.path.join(current, name)}: symbolic-link index "
                "directory is not trusted"
                for name in symlink_dirs
            )
            dirs[:] = sorted(name for name in dirs if name not in symlink_dirs)
            if "index.json" not in files:
                continue
            index_path = os.path.join(current, "index.json")
            if os.path.islink(index_path):
                errors.append(
                    f"{index_path}: symbolic-link index is not trusted"
                )
                continue
            try:
                with open(index_path, encoding="utf-8") as handle:
                    payload = json.load(handle)
                if not isinstance(payload, dict):
                    raise ValueError("index root must be an object")
                papers = payload.get("papers")
                if not isinstance(papers, list):
                    raise ValueError("papers must be a list")
                for position, paper in enumerate(papers, 1):
                    if not isinstance(paper, dict):
                        raise ValueError(
                            f"paper #{position} must be an object"
                        )
                    arxiv_id = str(paper.get("arxiv_id") or "").strip()
                    if not arxiv_id:
                        raise ValueError(
                            f"paper #{position} has no arxiv_id"
                        )
                    referenced_ids.add(arxiv_id)
            except (OSError, TypeError, ValueError) as exc:
                errors.append(f"{index_path}: {exc}")
        errors.extend(
            f"{getattr(exc, 'filename', mode_dir)}: index walk failed: {exc}"
            for exc in walk_errors
        )
    return referenced_ids, errors


def _chunks(values, size):
    values = list(values)
    for start in range(0, len(values), size):
        yield values[start:start + size]


def cleanup_orphan_artifacts(
    project_root,
    *,
    grace_seconds=DEFAULT_GRACE_DAYS * 24 * 60 * 60,
    dry_run=True,
    now=None,
    lock_timeout=60.0,
):
    """Return cleanup statistics after a locked reference recheck.

    ``dry_run`` defaults to true deliberately.  Actual deletion requires the
    caller to opt in, while exercising the same locks and strict index scan.
    """
    paths = _repository_paths(project_root)
    grace_seconds = float(grace_seconds)
    if grace_seconds < 0:
        raise ValueError("grace_seconds must not be negative")
    now = time.time() if now is None else float(now)
    candidates, recent = _discover_artifacts(
        paths["root"],
        grace_seconds=grace_seconds,
        now=now,
    )
    result = {
        "status": "ok",
        "dry_run": bool(dry_run),
        "grace_seconds": grace_seconds,
        "candidate_ids": len(candidates),
        "removed": {"pdf": 0, "sidecar": 0, "failed_tex": 0},
        "would_remove": {"pdf": 0, "sidecar": 0, "failed_tex": 0},
        "kept_recent": {"pdf": 0, "sidecar": 0, "failed_tex": 0},
        "kept_referenced": {"pdf": 0, "sidecar": 0, "failed_tex": 0},
        "freed_bytes": 0,
        "would_free_bytes": 0,
        "paths": [],
    }
    result["kept_recent"].update(recent)
    if not candidates:
        return result

    # The exclusive catalog lock freezes both the index set and index content.
    # Paper locks are acquired in bounded batches to avoid exhausting file
    # descriptors when a repository has accumulated many old sidecars.
    with catalog_publication_lock(
        lock_dir=paths["locks"],
        exclusive=True,
        timeout=lock_timeout,
    ):
        _, preflight_errors = _scan_references(paths["data"])
        if preflight_errors:
            raise CleanupSafetyError(
                f"{len(preflight_errors)} 个 index.json 读取失败，"
                f"本轮不清理孤立对象；首个错误={preflight_errors[0]}"
            )

        for arxiv_ids in _chunks(
            sorted(candidates),
            PAPER_LOCK_BATCH_SIZE,
        ):
            lock_specs = [
                (
                    paper_lock_path(arxiv_id, lock_dir=paths["locks"]),
                    LOCK_EXCLUSIVE,
                )
                for arxiv_id in arxiv_ids
            ]
            with PublicationLock(lock_specs, timeout=lock_timeout):
                # This is the authoritative reference scan: catalog exclusive
                # and every corresponding paper lock are held simultaneously.
                referenced_ids, scan_errors = _scan_references(paths["data"])
                if scan_errors:
                    raise CleanupSafetyError(
                        f"{len(scan_errors)} 个 index.json 读取失败，"
                        f"本轮停止清理孤立对象；首个错误={scan_errors[0]}"
                    )

                for arxiv_id in arxiv_ids:
                    is_referenced = arxiv_id in referenced_ids
                    for artifact in candidates[arxiv_id]:
                        kind = artifact["kind"]
                        path = artifact["path"]
                        current = _regular_file_stat(path)
                        if current is None:
                            continue
                        if is_referenced:
                            result["kept_referenced"][kind] += 1
                            continue
                        if now - current.st_mtime < grace_seconds:
                            result["kept_recent"][kind] += 1
                            continue
                        result["would_remove"][kind] += 1
                        result["would_free_bytes"] += current.st_size
                        result["paths"].append(path)
                        if dry_run:
                            continue
                        os.unlink(path)
                        result["removed"][kind] += 1
                        result["freed_bytes"] += current.st_size
    return result


def _human_summary(result):
    action = "将删除" if result["dry_run"] else "删除"
    counts = (
        result["would_remove"]
        if result["dry_run"]
        else result["removed"]
    )
    byte_count = (
        result["would_free_bytes"]
        if result["dry_run"]
        else result["freed_bytes"]
    )
    recent = sum(result["kept_recent"].values())
    referenced = sum(result["kept_referenced"].values())
    return (
        f"{action} PDF={counts['pdf']}、sidecar={counts['sidecar']}、"
        f"failed-tex={counts['failed_tex']}，"
        f"保留近期={recent}、仍被引用={referenced}，"
        f"{'预计释放' if result['dry_run'] else '释放'} "
        f"{byte_count // 1048576} MB"
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        default=ROOT,
        help="paper-trans 项目根目录",
    )
    parser.add_argument(
        "--grace-days",
        type=int,
        default=DEFAULT_GRACE_DAYS,
        help="对象最短保留天数（默认 3）",
    )
    parser.add_argument(
        "--lock-timeout",
        type=float,
        default=60.0,
        help="等待 catalog/paper 锁的秒数（默认 60）",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="实际删除；未指定时仅 dry-run",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.grace_days < 0:
        parser.error("--grace-days 不能为负数")
    if args.lock_timeout < 0:
        parser.error("--lock-timeout 不能为负数")

    try:
        result = cleanup_orphan_artifacts(
            args.root,
            grace_seconds=args.grace_days * 24 * 60 * 60,
            dry_run=not args.apply,
            lock_timeout=args.lock_timeout,
        )
    except (CleanupSafetyError, PublicationBusyError) as exc:
        print(f"SKIP: {exc}")
        return 2
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print(_human_summary(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
