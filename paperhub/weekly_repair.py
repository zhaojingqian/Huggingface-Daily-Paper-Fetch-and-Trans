#!/usr/bin/env python3
"""Serialized current-week repair runner used by the Sunday 02:00 cron."""

import fcntl
import os
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Dict, Iterator, Optional

from paperhub.failure_reports import load_failure_records
from paperhub.json_io import read_json, write_json_atomic
from paperhub.modes import mode_spec
from paperhub.patch_catalog import patches_for_records
from paperhub.paths import LOCK_DIR, LOGS_DIR, mode_index_path


def current_week_key(now: Optional[datetime] = None) -> str:
    return mode_spec("weekly").current_key(now)


def _week_arxiv_ids(key: str):
    index = read_json(mode_index_path("weekly", key), {})
    if not isinstance(index, dict):
        return set()
    return {
        str(item.get("arxiv_id"))
        for item in index.get("papers", [])
        if isinstance(item, dict) and item.get("arxiv_id")
    }


def _week_failure_records(key: str):
    ids = _week_arxiv_ids(key)
    if not ids:
        return []
    return [
        record
        for record in load_failure_records(os.path.join(LOGS_DIR, "pdf_errors"))
        if str(record.get("arxiv_id")) in ids
    ]


@contextmanager
def _exclusive_repair_lock(key: str) -> Iterator[bool]:
    """Prevent duplicate 02:00 invocations while keeping lock files disposable."""
    os.makedirs(LOCK_DIR, exist_ok=True)
    path = os.path.join(LOCK_DIR, f"weekly-repair-{key}.lock")
    handle = open(path, "w", encoding="utf-8")
    acquired = False
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError:
            yield False
            return
        handle.write(str(os.getpid()))
        handle.flush()
        yield True
    finally:
        if acquired:
            fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()
        if acquired:
            try:
                os.remove(path)
            except OSError:
                pass


def _wait_for_weekly_lock(key: str, wait_seconds: int, poll_seconds: int):
    """Wait for the weekly index/fetch, then acquire the shared repair lock."""
    from run_papers import RunLock

    deadline = time.monotonic() + max(0, wait_seconds)
    while True:
        # Cron starts the fetch and repair jobs in parallel.  The repair job
        # must not acquire an unused lock before the fetch creates the current
        # ISO-week index; otherwise it would report success without touching
        # the papers that are about to arrive.  The 02:30 fetch fallback is
        # covered by this same wait loop.
        if not os.path.exists(mode_index_path("weekly", key)):
            if time.monotonic() >= deadline:
                return None, "weekly index did not appear before timeout"
            time.sleep(max(1, poll_seconds))
            continue
        lock = RunLock("weekly", key)
        try:
            lock.__enter__()
            return lock, None
        except RuntimeError:
            if time.monotonic() >= deadline:
                return None, "weekly fetch lock did not release before timeout"
            time.sleep(max(1, poll_seconds))


def _write_history(key: str, payload: Dict[str, object]) -> str:
    history_dir = os.path.join(LOGS_DIR, "repair_history")
    path = os.path.join(history_dir, f"weekly-{key}.json")
    existing = read_json(path, {})
    if isinstance(existing, dict) and isinstance(existing.get("runs"), list):
        runs = list(existing["runs"])
    elif isinstance(existing, dict) and existing:
        # Migrate the first-generation single-run file without losing the
        # diagnostic evidence already captured for this week.
        runs = [existing]
    else:
        runs = []
    runs.append(payload)
    write_json_atomic(
        path,
        {
            "key": key,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "latest": payload,
            "runs": runs,
        },
    )
    return path


def run_current_week_repair(
    key: Optional[str] = None,
    wait_seconds: int = 10_800,
    poll_seconds: int = 15,
) -> Dict[str, object]:
    """Repair metadata and PDF failures for exactly one weekly index."""
    key = key or current_week_key()
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _exclusive_repair_lock(key) as acquired:
        if not acquired:
            return {"key": key, "status": "already_running", "started_at": started_at}

        before = _week_failure_records(key)
        lock, wait_error = _wait_for_weekly_lock(key, wait_seconds, poll_seconds)
        if not lock:
            result = {
                "key": key,
                "status": "timeout",
                "error": wait_error,
                "started_at": started_at,
                "failures_before": before,
                "patches": patches_for_records(before),
            }
            _write_history(key, result)
            return result

        # The index may have been created while we were waiting for the
        # weekly fetch.  Re-read the failure queue after the shared lock is
        # acquired so history and patch plans describe the actual final fetch,
        # not a pre-fetch snapshot.
        before = _week_failure_records(key)

        repair_count = 0
        pdf_count = 0
        errors = []
        try:
            from run_papers import repair, retry_pdf

            try:
                repair_count = repair(mode="weekly", key=key)
            except Exception as exc:  # keep PDF retry available if metadata repair fails
                errors.append(f"metadata: {exc}")
            try:
                pdf_count = retry_pdf(mode="weekly", key=key)
            except Exception as exc:
                errors.append(f"pdf: {exc}")
        finally:
            lock.__exit__(None, None, None)

        after = _week_failure_records(key)
        result = {
            "key": key,
            # A run can finish without raising while one or more papers still
            # have a failed status.  Preserve that signal for cron monitoring
            # instead of reporting a misleading all-clear.
            "status": "partial" if errors or after else "ok",
            "started_at": started_at,
            "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "metadata_repaired": repair_count,
            "pdf_repaired": pdf_count,
            "failures_before": before,
            "failures_after": after,
            "patches": patches_for_records(before),
            "errors": errors,
        }
        _write_history(key, result)
        return result
