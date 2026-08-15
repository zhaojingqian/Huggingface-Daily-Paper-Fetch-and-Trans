#!/usr/bin/env python3
"""Serialized current-ISO-week repair runner used by the Sunday 02:00 cron.

The weekly coordinator repairs every *published* index that belongs to the
current ISO week.  Expensive summary/PDF work is deduplicated by arXiv ID, then
the final PDF state is synchronized to every index that references that paper.
"""

import json
import os
import re
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Dict, Iterator, Optional

from paperhub import paper_store
from paperhub.failure_reports import load_failure_records
from paperhub.json_io import read_json, write_json_atomic
from paperhub.modes import mode_spec
from paperhub.patch_catalog import patches_for_records
from paperhub.paths import DATA_DIR, LOCK_DIR, LOGS_DIR, PAPER_STORE_DIR
from paperhub.publication_lock import (
    InvalidIndexError,
    PublicationBusyError,
    PublicationLock,
    catalog_publication_lock,
    lock_dir_for_index,
    merge_index_paper_fields,
)


_CONTENT_MODES = ("daily", "weekly", "monthly", "manual", "topic")
_STAT_FIELDS = (
    "metadata_attempted",
    "metadata_succeeded",
    "metadata_failed",
    "summary_attempted",
    "summary_succeeded",
    "summary_failed",
    "pdf_attempted",
    "pdf_succeeded",
    "pdf_failed",
)
_WEEK_KEY_RE = re.compile(r"^(\d{4})-W(\d{2})$")


def _new_stats():
    stats = {field: 0 for field in _STAT_FIELDS}
    stats["residual_failures"] = 0
    stats["residual_ids"] = []
    return stats


def _finish_stats(stats, residual_ids=()):
    result = dict(stats)
    ids = sorted({str(item) for item in residual_ids if item})
    result["residual_ids"] = ids
    result["residual_failures"] = len(ids)
    return result


def current_week_key(now: Optional[datetime] = None) -> str:
    return mode_spec("weekly").current_key(now)


def _week_dates(key: str):
    match = _WEEK_KEY_RE.fullmatch(str(key or ""))
    if not match:
        raise ValueError(f"invalid ISO week key: {key}")
    monday = date.fromisocalendar(int(match.group(1)), int(match.group(2)), 1)
    return tuple(monday + timedelta(days=offset) for offset in range(7))


def _index_path(data_dir: str, mode: str, key: str) -> str:
    return os.path.join(data_dir, mode, key, "index.json")


def _repository_lock_dir(data_dir: str) -> str:
    data_dir = os.path.realpath(data_dir)
    if data_dir == os.path.realpath(DATA_DIR):
        return LOCK_DIR
    root = os.path.dirname(data_dir) if os.path.basename(data_dir) == "data" else data_dir
    return os.path.join(root, "locks")


def current_week_targets(key: str, data_dir: Optional[str] = None):
    """Return published index keys for all content modes in one ISO week."""
    data_dir = data_dir or DATA_DIR
    dates = _week_dates(key)
    date_keys = tuple(value.isoformat() for value in dates)
    targets = {mode: [] for mode in _CONTENT_MODES}

    for mode in ("daily", "manual"):
        targets[mode] = [
            value for value in date_keys
            if os.path.isfile(_index_path(data_dir, mode, value))
        ]

    if os.path.isfile(_index_path(data_dir, "weekly", key)):
        targets["weekly"] = [key]

    month_keys = sorted({value.strftime("%Y-%m") for value in dates})
    targets["monthly"] = [
        value for value in month_keys
        if os.path.isfile(_index_path(data_dir, "monthly", value))
    ]

    topic_root = os.path.join(data_dir, "topic")
    if os.path.isdir(topic_root):
        for slug in sorted(os.listdir(topic_root)):
            if not os.path.isdir(os.path.join(topic_root, slug)):
                continue
            for value in date_keys:
                topic_key = f"{slug}/{value}"
                if os.path.isfile(_index_path(data_dir, "topic", topic_key)):
                    targets["topic"].append(topic_key)

    return targets


def _iter_index_paths(data_dir: str):
    for mode in _CONTENT_MODES:
        root = os.path.join(data_dir, mode)
        if not os.path.isdir(root):
            continue
        for current, dirs, files in os.walk(root):
            dirs.sort()
            if "index.json" in files:
                yield mode, os.path.join(current, "index.json")


def _load_all_indexes(data_dir: str):
    """Load every index once so selected-paper status can be synced globally."""
    documents = {}
    references = {}
    errors = []
    try:
        with catalog_publication_lock(
            lock_dir=_repository_lock_dir(data_dir),
            exclusive=True,
            timeout=60,
        ):
            for mode, path in _iter_index_paths(data_dir):
                try:
                    with open(path, encoding="utf-8") as handle:
                        payload = json.load(handle)
                    if (
                        not isinstance(payload, dict)
                        or not isinstance(payload.get("papers"), list)
                    ):
                        raise ValueError(
                            "index payload must contain a papers list"
                        )
                except Exception as exc:
                    errors.append(f"index:{path}: {exc}")
                    continue
                documents[path] = payload
                for position, item in enumerate(payload["papers"], 1):
                    if not isinstance(item, dict):
                        errors.append(
                            f"index:{path}: paper #{position} is not an object"
                        )
                        continue
                    arxiv_id = str(item.get("arxiv_id") or "").strip()
                    if not arxiv_id:
                        errors.append(
                            f"index:{path}: paper #{position} has no arxiv_id"
                        )
                        continue
                    references.setdefault(arxiv_id, []).append(
                        {
                            "mode": mode,
                            "path": path,
                            "item": item,
                            "key": os.path.relpath(
                                os.path.dirname(path),
                                os.path.join(data_dir, mode),
                            ),
                        }
                    )
    except PublicationBusyError as exc:
        errors.append(f"index-catalog:{data_dir}: {exc}")
    return documents, references, errors


def _selected_ids(targets, documents, data_dir: str):
    selected = set()
    errors = []
    ids_by_mode = {mode: set() for mode in _CONTENT_MODES}
    for mode, keys in targets.items():
        for key in keys:
            path = _index_path(data_dir, mode, key)
            payload = documents.get(path)
            if payload is None:
                errors.append(f"{mode}/{key}:index")
                continue
            papers = payload.get("papers", [])
            # Topic subscriptions intentionally prefer an empty result over
            # irrelevant papers, and an emptied manual collection is valid.
            # Scheduled leaderboard indexes, however, should not publish an
            # empty Top-N snapshot.
            if not papers and mode in {"daily", "weekly", "monthly"}:
                errors.append(f"{mode}/{key}:empty-index")
                continue
            for item in papers:
                if not isinstance(item, dict):
                    continue
                arxiv_id = str(item.get("arxiv_id") or "").strip()
                if arxiv_id:
                    selected.add(arxiv_id)
                    ids_by_mode[mode].add(arxiv_id)
    return selected, ids_by_mode, errors


def _failure_records_for_ids(arxiv_ids):
    ids = {str(item) for item in arxiv_ids if item}
    if not ids:
        return []
    return [
        record
        for record in load_failure_records(os.path.join(LOGS_DIR, "pdf_errors"))
        if str(record.get("arxiv_id")) in ids
    ]


def _repair_unique_summaries(arxiv_ids, references):
    """Repair each shared paper-store summary at most once."""
    from translate_arxiv import load_api_config, translate_and_save

    stats = _new_stats()
    residual_ids = set()
    repaired_ids = set()
    candidate_ids = set()
    errors = []
    config = None

    for arxiv_id in sorted(arxiv_ids):
        stats["metadata_attempted"] += 1
        stats["summary_attempted"] += 1
        stored = paper_store.read_raw(arxiv_id)
        before_complete = paper_store.translation_complete(stored)
        if not before_complete:
            candidate_ids.add(arxiv_id)
            first_ref = references.get(arxiv_id, [{}])[0]
            item = first_ref.get("item", {})
            try:
                if config is None:
                    config = load_api_config()
                translate_and_save(
                    arxiv_id=arxiv_id,
                    output_dir=PAPER_STORE_DIR,
                    rank=int(item.get("rank") or 1),
                    week_str=f"{first_ref.get('mode', 'weekly')}/{first_ref.get('key', '')}",
                    config=config,
                )
            except Exception as exc:
                errors.append(f"summary:{arxiv_id}: {exc}")
            stored = paper_store.read_raw(arxiv_id)
            if paper_store.translation_complete(stored):
                repaired_ids.add(arxiv_id)

        if paper_store.metadata_complete(stored):
            stats["metadata_succeeded"] += 1
        else:
            stats["metadata_failed"] += 1
            residual_ids.add(arxiv_id)
        if paper_store.translation_complete(stored):
            stats["summary_succeeded"] += 1
        else:
            stats["summary_failed"] += 1
            residual_ids.add(arxiv_id)

    stats["summary_repaired"] = len(repaired_ids)
    stats["summary_candidates"] = len(candidate_ids)
    return (
        _finish_stats(stats, residual_ids),
        candidate_ids,
        repaired_ids,
        errors,
    )


def _representative_pdf_entries(arxiv_ids, references):
    """Build one retry entry per selected paper from all referencing indexes."""
    entries = {}
    for arxiv_id in sorted(arxiv_ids):
        refs = references.get(arxiv_id, [])
        statuses = {
            str(ref["item"].get("pdf_status") or "")
            for ref in refs
        }
        stored_status = str(paper_store.read_raw(arxiv_id).get("pdf_status") or "")
        first = dict(refs[0]["item"]) if refs else {"arxiv_id": arxiv_id}
        first["arxiv_id"] = arxiv_id
        if "failed" in statuses or stored_status == "failed":
            first["pdf_status"] = "failed"
        elif "ok" in statuses or stored_status == "ok":
            first["pdf_status"] = "ok"
        elif "none" in statuses:
            first["pdf_status"] = "none"
        else:
            first.pop("pdf_status", None)
        entries[arxiv_id] = first
    return entries


def _pdf_candidates(entries):
    candidates = set()
    for arxiv_id, item in entries.items():
        status = item.get("pdf_status")
        if (
            paper_store.pdf_quality_tainted(arxiv_id)
            or status == "failed"
            or (status == "ok" and not paper_store.pdf_hit(arxiv_id))
        ):
            candidates.add(arxiv_id)
    return candidates


def _retry_unique_pdfs(entries):
    from run_papers import retry_failed_pdf_entries

    previous = os.environ.get("PAPER_TRANS_RETRY_MANUAL_REVIEW")
    if previous != "0":
        os.environ["PAPER_TRANS_RETRY_MANUAL_REVIEW"] = "1"
    try:
        return retry_failed_pdf_entries(
            [entries[arxiv_id] for arxiv_id in sorted(entries)],
            label="[weekly-repair:all-modes]",
        )
    finally:
        if previous is None:
            os.environ.pop("PAPER_TRANS_RETRY_MANUAL_REVIEW", None)
        elif previous != "0":
            os.environ["PAPER_TRANS_RETRY_MANUAL_REVIEW"] = previous


def _sync_pdf_statuses(
    entries,
    references,
    documents,
    data_dir=DATA_DIR,
):
    """Merge final PDF state into current indexes without stale-payload writes."""
    del documents  # Kept in the signature for callers/tests from the old API.
    updates_by_path = {}
    errors = []
    updated_references = 0
    for arxiv_id, representative in entries.items():
        final_status = representative.get("pdf_status")
        if os.path.realpath(data_dir) == os.path.realpath(DATA_DIR):
            stored = paper_store.read_raw(arxiv_id)
            has_pdf = bool(paper_store.pdf_hit(arxiv_id))
        else:
            stored = read_json(
                os.path.join(data_dir, "papers", f"{arxiv_id}.json"),
                {},
            )
            has_pdf = paper_store.pdf_file_valid(
                os.path.join(
                    data_dir,
                    "papers",
                    f"{arxiv_id}_zh.pdf",
                )
            )
        stored_status = (
            stored.get("pdf_status") if isinstance(stored, dict) else None
        )
        if paper_store.pdf_quality_tainted(stored):
            final_status = "failed"
        elif stored_status == "failed":
            final_status = "failed"
        elif stored_status == "ok":
            final_status = "ok" if has_pdf else "failed"
        if final_status not in {"ok", "failed", "none"}:
            continue
        representative["pdf_status"] = final_status
        for ref in references.get(arxiv_id, []):
            path_state = updates_by_path.setdefault(
                ref["path"],
                {
                    "mode": ref["mode"],
                    "key": ref["key"],
                    "updates": {},
                },
            )
            path_state["updates"][arxiv_id] = {
                "pdf_status": final_status,
            }

    written = 0
    for path in sorted(updates_by_path):
        state = updates_by_path[path]
        lock_dir = lock_dir_for_index(
            path,
            state["mode"],
            data_dir=data_dir,
            lock_dir=_repository_lock_dir(data_dir),
        )
        try:
            result = merge_index_paper_fields(
                path,
                state["updates"],
                mode=state["mode"],
                key=state["key"],
                lock_dir=lock_dir,
            )
            if result["changed_fields"]:
                written += 1
                updated_references += result["changed_fields"]
        except (InvalidIndexError, PublicationBusyError) as exc:
            errors.append(f"index-write:{path}: {exc}")
    return {
        "indexes_written": written,
        "references_updated": updated_references,
    }, errors


def _stats_by_mode(
    targets,
    ids_by_mode,
    summary_candidate_ids,
    summary_repaired_ids,
    pdf_candidate_ids,
    entries,
    residual_ids,
):
    residual = set(residual_ids)
    result = {}
    for mode in _CONTENT_MODES:
        ids = set(ids_by_mode.get(mode, set()))
        pdf_repaired = {
            arxiv_id
            for arxiv_id in ids & set(pdf_candidate_ids)
            if entries.get(arxiv_id, {}).get("pdf_status") == "ok"
        }
        mode_residual = sorted(ids & residual)
        result[mode] = {
            "target_indexes": len(targets.get(mode, [])),
            "unique_papers": len(ids),
            "summary_candidates": len(ids & set(summary_candidate_ids)),
            "summary_repaired": len(ids & set(summary_repaired_ids)),
            "pdf_candidates": len(ids & set(pdf_candidate_ids)),
            "pdf_repaired": len(pdf_repaired),
            "residual_failures": len(mode_residual),
            "residual_ids": mode_residual,
        }
    return result


@contextmanager
def _exclusive_repair_lock(
    key: str,
    lock_dir: Optional[str] = None,
) -> Iterator[bool]:
    """Prevent duplicate 02:00 coordinators; flock, not file existence, is truth."""
    path = os.path.join(
        lock_dir or LOCK_DIR,
        f"weekly-repair-{key}.lock",
    )
    try:
        lock = PublicationLock([path], timeout=0)
        lock.__enter__()
    except PublicationBusyError:
        yield False
        return
    try:
        yield True
    finally:
        lock.__exit__(None, None, None)


def _wait_for_weekly_lock(
    key: str,
    wait_seconds: int,
    poll_seconds: int,
    data_dir: Optional[str] = None,
):
    """Wait for the weekly index/fetch, then acquire its mode/key lock."""
    from run_papers import RunLock

    data_dir = data_dir or DATA_DIR
    deadline = time.monotonic() + max(0, wait_seconds)
    while True:
        if not os.path.exists(_index_path(data_dir, "weekly", key)):
            if time.monotonic() >= deadline:
                return None, "weekly index did not appear before timeout"
            time.sleep(max(1, poll_seconds))
            continue
        lock = RunLock(
            "weekly",
            key,
            lock_dir=_repository_lock_dir(data_dir),
        )
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
    data_dir: Optional[str] = None,
) -> Dict[str, object]:
    """Repair all published current-week content with one deduplicated queue."""
    key = key or current_week_key()
    data_dir = data_dir or DATA_DIR
    # Validate before creating a lock/history path from user-controlled text.
    _week_dates(key)
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with _exclusive_repair_lock(
        key,
        lock_dir=_repository_lock_dir(data_dir),
    ) as acquired:
        if not acquired:
            return {"key": key, "status": "already_running", "started_at": started_at}

        lock, wait_error = _wait_for_weekly_lock(
            key,
            wait_seconds,
            poll_seconds,
            data_dir=data_dir,
        )
        if not lock:
            result = {
                "key": key,
                "status": "timeout",
                "error": wait_error,
                "started_at": started_at,
                "targets_by_mode": {mode: [] for mode in _CONTENT_MODES},
                "stats_by_mode": {mode: {} for mode in _CONTENT_MODES},
                "residual_failures": 1,
                "residual_ids": [f"weekly/{key}:index"],
            }
            _write_history(key, result)
            return result
        # The fetch hand-off is complete.  Do not retain one index lock while
        # acquiring the catalog or other mode locks later in the run.
        lock.__exit__(None, None, None)
        lock = None

        errors = []
        targets = {mode: [] for mode in _CONTENT_MODES}
        documents = {}
        references = {}
        selected_ids = set()
        ids_by_mode = {mode: set() for mode in _CONTENT_MODES}
        selection_errors = []
        before = []
        after = []
        summary_stats = _finish_stats(_new_stats())
        summary_stats["summary_repaired"] = 0
        summary_stats["summary_candidates"] = 0
        summary_candidate_ids = set()
        summary_repaired_ids = set()
        entries = {}
        pdf_candidate_ids = set()
        pdf_stats = _finish_stats(_new_stats())
        sync_stats = {"indexes_written": 0, "references_updated": 0}
        try:
            targets = current_week_targets(key, data_dir=data_dir)
            documents, references, scan_errors = _load_all_indexes(data_dir)
            errors.extend(scan_errors)
            selected_ids, ids_by_mode, selection_errors = _selected_ids(
                targets, documents, data_dir
            )
            errors.extend(selection_errors)

            before = _failure_records_for_ids(selected_ids)
            (
                summary_stats,
                summary_candidate_ids,
                summary_repaired_ids,
                summary_errors,
            ) = _repair_unique_summaries(selected_ids, references)
            errors.extend(summary_errors)

            entries = _representative_pdf_entries(selected_ids, references)
            pdf_candidate_ids = _pdf_candidates(entries)
            try:
                pdf_stats = _retry_unique_pdfs(entries)
            except Exception as exc:
                pdf_stats = _finish_stats(_new_stats(), pdf_candidate_ids)
                pdf_stats["pdf_attempted"] = len(pdf_candidate_ids)
                pdf_stats["pdf_failed"] = len(pdf_candidate_ids)
                errors.append(f"pdf: {exc}")

            sync_stats, sync_errors = _sync_pdf_statuses(
                entries,
                references,
                documents,
                data_dir=data_dir,
            )
            errors.extend(sync_errors)
            after = _failure_records_for_ids(selected_ids)
        except Exception as exc:
            errors.append(f"runner: {exc}")
        finally:
            if lock is not None:
                lock.__exit__(None, None, None)

        residual_ids = set(summary_stats.get("residual_ids", []))
        residual_ids.update(pdf_stats.get("residual_ids", []))
        residual_ids.update(
            str(record.get("arxiv_id"))
            for record in after
            if record.get("arxiv_id")
        )
        residual_ids.update(selection_errors)
        # An unreadable index or failed write means global reference
        # synchronization cannot be proven, so preserve it as a residual.
        residual_ids.update(
            error.split(": ", 1)[0]
            for error in errors
            if error.startswith(("index:", "index-catalog:", "index-write:"))
        )
        if errors and not residual_ids:
            residual_ids.add(f"weekly/{key}:runner")
        residual_ids = sorted(item for item in residual_ids if item)

        result = {
            "key": key,
            "status": "partial" if errors or residual_ids else "ok",
            "started_at": started_at,
            "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "targets_by_mode": targets,
            "stats_by_mode": _stats_by_mode(
                targets,
                ids_by_mode,
                summary_candidate_ids,
                summary_repaired_ids,
                pdf_candidate_ids,
                entries,
                residual_ids,
            ),
            "unique_papers": len(selected_ids),
            "metadata_repaired": int(summary_stats.get("summary_repaired", 0) or 0),
            "pdf_repaired": int(pdf_stats.get("pdf_succeeded", 0) or 0),
            "repair_stats": summary_stats,
            "pdf_stats": pdf_stats,
            "sync_stats": sync_stats,
            "residual_failures": len(residual_ids),
            "residual_ids": residual_ids,
            "failures_before": before,
            "failures_after": after,
            "patches": patches_for_records(before),
            "errors": errors,
        }
        _write_history(key, result)
        return result
