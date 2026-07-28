#!/usr/bin/env python3
"""Shared cross-process locks and field-level index publication helpers.

All publication locks use the historical ``locks/<mode>-<key>.lock`` filename
for flat production keys, so fetch runners, repair jobs, and Web mutations
compete for the same inode.  Topic keys and untrusted components use a stable
hash instead of becoming filesystem paths.  Lock files are persistent; only
``flock`` ownership is authoritative.
"""

import fcntl
import hashlib
import json
import os
import re
import time
from contextlib import contextmanager

from paperhub.json_io import write_json_atomic
from paperhub.paths import DATA_DIR, LOCK_DIR


_SAFE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
CATALOG_LOCK_NAME = "publication-catalog.lock"
LOCK_SHARED = "shared"
LOCK_EXCLUSIVE = "exclusive"


class PublicationBusyError(RuntimeError):
    """Raised when another process owns a requested publication resource."""


class InvalidIndexError(RuntimeError):
    """Raised when a locked index cannot be safely read or merged."""


def _stable_lock_component(prefix, value):
    value = str(value or "")
    if value and _SAFE_COMPONENT_RE.fullmatch(value):
        return f"{prefix}-{value}"
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


def index_lock_path(mode, key, lock_dir=None):
    """Return the shared lock path for one logical mode/key index."""
    lock_dir = lock_dir or LOCK_DIR
    mode = str(mode or "")
    key = str(key or "")
    if _SAFE_COMPONENT_RE.fullmatch(mode) and _SAFE_COMPONENT_RE.fullmatch(key):
        filename = f"{mode}-{key}.lock"
    else:
        identity = f"{mode}\0{key}"
        filename = _stable_lock_component("index", identity) + ".lock"
    return os.path.join(lock_dir, filename)


def paper_lock_path(arxiv_id, lock_dir=None):
    """Return the per-paper lock used for JSON/PDF store mutations."""
    lock_dir = lock_dir or LOCK_DIR
    return os.path.join(
        lock_dir,
        _stable_lock_component("paper", arxiv_id) + ".lock",
    )


def catalog_lock_path(lock_dir=None):
    return os.path.join(lock_dir or LOCK_DIR, CATALOG_LOCK_NAME)


def lock_dir_for_index(
    index_path,
    mode,
    data_dir=None,
    lock_dir=None,
):
    """Keep production compatibility while isolating temporary/test data roots."""
    data_dir = os.path.realpath(data_dir or DATA_DIR)
    index_path = os.path.realpath(index_path)
    try:
        in_default_data = os.path.commonpath(
            (data_dir, index_path)
        ) == data_dir
    except ValueError:
        in_default_data = False
    if in_default_data:
        return lock_dir or LOCK_DIR

    marker = os.sep + str(mode) + os.sep
    if marker in index_path:
        root = index_path.split(marker, 1)[0]
    else:
        root = os.path.dirname(os.path.dirname(index_path))
    if os.path.basename(root) == "data":
        root = os.path.dirname(root)
    return os.path.join(root, "locks")


class PublicationLock:
    """Acquire one or more lock files in canonical order using retryable NB flock."""

    def __init__(self, lock_paths, timeout=0.0, poll_seconds=0.05):
        modes_by_path = {}
        for item in lock_paths:
            if isinstance(item, tuple):
                path, mode = item
            else:
                path, mode = item, LOCK_EXCLUSIVE
            if not path:
                continue
            path = os.path.abspath(path)
            if mode not in {LOCK_SHARED, LOCK_EXCLUSIVE}:
                raise ValueError(f"invalid publication lock mode: {mode}")
            if (
                modes_by_path.get(path) == LOCK_EXCLUSIVE
                or mode == LOCK_EXCLUSIVE
            ):
                modes_by_path[path] = LOCK_EXCLUSIVE
            else:
                modes_by_path[path] = LOCK_SHARED
        self.lock_specs = sorted(
            modes_by_path.items(),
            key=lambda item: self._sort_key(item[0]),
        )
        if not self.lock_specs:
            raise ValueError("at least one publication lock path is required")
        self.timeout = max(0.0, float(timeout or 0.0))
        self.poll_seconds = max(0.01, min(float(poll_seconds or 0.05), 1.0))
        self._handles = []

    @staticmethod
    def _sort_key(path):
        name = os.path.basename(path)
        if name == CATALOG_LOCK_NAME:
            resource_rank = 0
        elif name.startswith("paper-"):
            resource_rank = 2
        else:
            resource_rank = 1
        return resource_rank, os.path.normcase(path)

    def _release(self):
        handles, self._handles = self._handles, []
        for _, handle in reversed(handles):
            try:
                fcntl.flock(handle, fcntl.LOCK_UN)
            finally:
                handle.close()

    def _try_acquire(self):
        acquired = []
        busy_path = ""
        try:
            for path, mode in self.lock_specs:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                handle = open(path, "a+", encoding="utf-8")
                try:
                    fcntl.flock(
                        handle,
                        (
                            fcntl.LOCK_SH
                            if mode == LOCK_SHARED
                            else fcntl.LOCK_EX
                        )
                        | fcntl.LOCK_NB,
                    )
                except BlockingIOError:
                    busy_path = path
                    handle.close()
                    return False, busy_path, acquired
                if mode == LOCK_EXCLUSIVE:
                    handle.seek(0)
                    handle.truncate()
                    handle.write(str(os.getpid()))
                    handle.flush()
                acquired.append((path, handle))
            return True, "", acquired
        except Exception:
            self._handles = acquired
            self._release()
            raise

    def __enter__(self):
        deadline = time.monotonic() + self.timeout
        while True:
            acquired, busy_path, handles = self._try_acquire()
            if acquired:
                self._handles = handles
                return self
            self._handles = handles
            self._release()
            if not self.timeout or time.monotonic() >= deadline:
                raise PublicationBusyError(
                    f"publication resource is busy: {busy_path}"
                )
            time.sleep(
                min(self.poll_seconds, max(0.0, deadline - time.monotonic()))
            )

    def __exit__(self, *_):
        self._release()


@contextmanager
def index_publication_lock(
    mode,
    key,
    *,
    lock_dir=None,
    timeout=0.0,
):
    """Lock one logical index using the same filename as its fetch runner."""
    lock_dir = lock_dir or LOCK_DIR
    with PublicationLock(
        [
            (catalog_lock_path(lock_dir), LOCK_SHARED),
            (
                index_lock_path(mode, key, lock_dir=lock_dir),
                LOCK_EXCLUSIVE,
            ),
        ],
        timeout=timeout,
    ) as acquired:
        yield acquired


@contextmanager
def catalog_publication_lock(
    *,
    lock_dir=None,
    exclusive=True,
    timeout=0.0,
):
    """Freeze the set/content of indexes for a repository-wide scan."""
    lock_dir = lock_dir or LOCK_DIR
    mode = LOCK_EXCLUSIVE if exclusive else LOCK_SHARED
    with PublicationLock(
        [(catalog_lock_path(lock_dir), mode)],
        timeout=timeout,
    ) as acquired:
        yield acquired


@contextmanager
def paper_publication_lock(
    arxiv_id,
    *,
    lock_dir=None,
    timeout=30.0,
):
    """Serialize one paper-store JSON/PDF mutation across every entrypoint."""
    with PublicationLock(
        [paper_lock_path(arxiv_id, lock_dir=lock_dir)],
        timeout=timeout,
    ) as acquired:
        yield acquired


def _load_index_strict(index_path):
    try:
        with open(index_path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError, TypeError) as exc:
        raise InvalidIndexError(f"{index_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise InvalidIndexError(f"{index_path}: index root is not an object")
    papers = payload.get("papers")
    if not isinstance(papers, list):
        raise InvalidIndexError(f"{index_path}: papers is not an array")
    for position, paper in enumerate(papers, 1):
        if not isinstance(paper, dict):
            raise InvalidIndexError(
                f"{index_path}: paper #{position} is not an object"
            )
    return payload


def read_index_snapshot(
    index_path,
    *,
    mode,
    key,
    lock_dir=None,
    timeout=0.0,
):
    """Read one structurally valid index while its publication lock is held."""
    with index_publication_lock(
        mode,
        key,
        lock_dir=lock_dir,
        timeout=timeout,
    ):
        return _load_index_strict(index_path)


def merge_index_paper_fields(
    index_path,
    updates,
    *,
    mode,
    key,
    allowed_fields=("pdf_status",),
    lock_dir=None,
    timeout=0.0,
):
    """Re-read under lock and merge only approved fields by ``arxiv_id``.

    This deliberately never writes a caller's stale index payload, and never
    changes paper order, rank, unrelated fields, ``total``, or ``generated_at``.
    """
    normalized = {}
    allowed = set(allowed_fields)
    for arxiv_id, fields in (updates or {}).items():
        aid = str(arxiv_id or "").strip()
        if not aid or not isinstance(fields, dict):
            continue
        unexpected = set(fields) - allowed
        if unexpected:
            raise ValueError(
                f"index merge contains disallowed fields: {sorted(unexpected)}"
            )
        normalized[aid] = dict(fields)

    with index_publication_lock(
        mode,
        key,
        lock_dir=lock_dir,
        timeout=timeout,
    ):
        payload = _load_index_strict(index_path)
        changed = 0
        matched = set()
        statuses = {}
        for paper in payload["papers"]:
            arxiv_id = str(paper.get("arxiv_id") or "").strip()
            if not arxiv_id:
                continue
            if arxiv_id in normalized:
                matched.add(arxiv_id)
                for field, value in normalized[arxiv_id].items():
                    if paper.get(field) != value:
                        paper[field] = value
                        changed += 1
            if "pdf_status" in paper:
                statuses.setdefault(arxiv_id, []).append(
                    paper.get("pdf_status")
                )
        if changed:
            write_json_atomic(index_path, payload)
        return {
            "changed_fields": changed,
            "matched_ids": sorted(matched),
            "missing_ids": sorted(set(normalized) - matched),
            "pdf_statuses": statuses,
            "payload": payload,
        }
