#!/usr/bin/env python3
"""Shared paper store helpers.

The paper store has two read modes:
- raw reads for Web rendering and status repair;
- translated-cache reads for the summary translator, which should only reuse
  entries that already contain a Chinese title.
"""

import os
import re
import shutil
import tempfile

from paperhub import paths
from paperhub.json_io import read_json, write_json_atomic
from paperhub.publication_lock import paper_publication_lock


MIN_VALID_PDF_BYTES = 10240
PDF_HEADER = b"%PDF-"
PDF_EOF_MARKER = b"%%EOF"
PDF_TAIL_SCAN_BYTES = 4096
PDF_QUALITY_TAINT_FIELD = "pdf_quality_tainted"
PDF_QUALITY_TAINT_REASON_FIELD = "pdf_quality_taint_reason"
PDF_QUALITY_TAINT_AT_FIELD = "pdf_quality_tainted_at"


def has_chinese(text):
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def json_path(arxiv_id):
    os.makedirs(paths.PAPER_STORE_DIR, exist_ok=True)
    return paths.paper_store_json_path(arxiv_id)


def pdf_path(arxiv_id):
    os.makedirs(paths.PAPER_STORE_DIR, exist_ok=True)
    return paths.paper_store_pdf_path(arxiv_id)


def read_raw(arxiv_id):
    return read_json(json_path(arxiv_id), {})


def read_translated(arxiv_id):
    data = read_raw(arxiv_id)
    return data if has_chinese(data.get("title_zh", "")) else None


def translation_complete(data):
    """Return whether a cached entry has both a Chinese title and summary."""
    return bool(
        isinstance(data, dict)
        and has_chinese(data.get("title_zh", ""))
        and has_chinese(data.get("summary_zh", ""))
    )


def metadata_complete(data):
    """Return whether a paper has the source title and abstract/summary."""
    return bool(
        isinstance(data, dict)
        and str(data.get("title", "")).strip()
        and str(data.get("abstract") or data.get("summary") or "").strip()
    )


def pdf_quality_tainted(data_or_arxiv_id):
    """Return whether a structurally valid PDF is blocked by a quality failure."""
    data = (
        read_raw(data_or_arxiv_id)
        if isinstance(data_or_arxiv_id, str)
        else data_or_arxiv_id
    )
    return bool(
        isinstance(data, dict)
        and data.get(PDF_QUALITY_TAINT_FIELD)
    )


def _paper_lock_dir():
    """Use the project lock dir in production and isolate temporary stores."""
    store_dir = os.path.realpath(paths.PAPER_STORE_DIR)
    default_store = os.path.realpath(
        os.path.join(paths.DATA_DIR, "papers")
    )
    if store_dir == default_store:
        return paths.LOCK_DIR
    if os.path.basename(store_dir) == "papers":
        parent = os.path.dirname(store_dir)
        root = (
            os.path.dirname(parent)
            if os.path.basename(parent) == "data"
            else parent
        )
    else:
        root = store_dir
    return os.path.join(root, "locks")


def _write_raw_unlocked(payload):
    write_json_atomic(json_path(payload["arxiv_id"]), payload)


def write_raw(payload):
    """Replace one complete store payload under its per-paper lock."""
    arxiv_id = str(payload.get("arxiv_id") or "").strip()
    if not arxiv_id:
        raise ValueError("paper store payload requires arxiv_id")
    with paper_publication_lock(
        arxiv_id,
        lock_dir=_paper_lock_dir(),
    ):
        _write_raw_unlocked(dict(payload))


def merge_raw(arxiv_id, fields, create=True):
    """Atomically merge fields without losing concurrent PDF/quality state."""
    arxiv_id = str(arxiv_id or "").strip()
    if not arxiv_id:
        raise ValueError("paper store merge requires arxiv_id")
    with paper_publication_lock(
        arxiv_id,
        lock_dir=_paper_lock_dir(),
    ):
        data = read_raw(arxiv_id)
        if not isinstance(data, dict):
            data = {}
        if not data and not create:
            return None
        data.update(dict(fields or {}))
        data["arxiv_id"] = arxiv_id
        _write_raw_unlocked(data)
        return data


def pdf_file_valid(filepath, min_bytes=MIN_VALID_PDF_BYTES):
    """Validate a PDF with bounded header/tail reads.

    A size-only check accepts interrupted copies that happen to exceed the
    threshold.  The store and repository audit share this lightweight gate so
    an ``ok`` status always means the file has a PDF header and a closing EOF
    marker, without loading a potentially large document into memory.
    """
    try:
        size = os.path.getsize(filepath)
        if size <= min_bytes:
            return False
        with open(filepath, "rb") as handle:
            if handle.read(len(PDF_HEADER)) != PDF_HEADER:
                return False
            handle.seek(max(0, size - PDF_TAIL_SCAN_BYTES), os.SEEK_SET)
            return PDF_EOF_MARKER in handle.read(PDF_TAIL_SCAN_BYTES)
    except (OSError, ValueError):
        return False


def pdf_exists(arxiv_id, min_bytes=MIN_VALID_PDF_BYTES):
    return pdf_file_valid(paths.paper_store_pdf_path(arxiv_id), min_bytes)


def pdf_hit(arxiv_id, min_bytes=MIN_VALID_PDF_BYTES):
    p = paths.paper_store_pdf_path(arxiv_id)
    return p if pdf_file_valid(p, min_bytes) else None


def save_pdf(arxiv_id, src_path):
    """Atomically replace one PDF while holding the matching paper lock."""
    destination = pdf_path(arxiv_id)
    directory = os.path.dirname(destination)
    temp_path = None
    with paper_publication_lock(
        arxiv_id,
        lock_dir=_paper_lock_dir(),
    ):
        try:
            with tempfile.NamedTemporaryFile(
                dir=directory,
                prefix=f".{os.path.basename(destination)}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_path = handle.name
            shutil.copy2(src_path, temp_path)
            with open(temp_path, "rb") as handle:
                os.fsync(handle.fileno())
            os.replace(temp_path, destination)
            temp_path = None
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)


def update_pdf_status(arxiv_id, status):
    try:
        with paper_publication_lock(
            arxiv_id,
            lock_dir=_paper_lock_dir(),
        ):
            data = read_raw(arxiv_id)
            if not data:
                return False
            if status == "ok" and pdf_quality_tainted(data):
                return False
            data["pdf_status"] = status
            _write_raw_unlocked(data)
            return True
    except Exception:
        return False


def mark_pdf_quality_tainted(arxiv_id, reason="quality.untranslated_prose", tainted_at=""):
    """Persist a quality taint independently from the mutable retry diagnosis."""
    try:
        with paper_publication_lock(
            arxiv_id,
            lock_dir=_paper_lock_dir(),
        ):
            data = read_raw(arxiv_id)
            if not data:
                return False
            data["pdf_status"] = "failed"
            data[PDF_QUALITY_TAINT_FIELD] = True
            data[PDF_QUALITY_TAINT_REASON_FIELD] = str(
                reason or "quality.unknown"
            )
            if tainted_at:
                data[PDF_QUALITY_TAINT_AT_FIELD] = str(tainted_at)
            _write_raw_unlocked(data)
            return True
    except Exception:
        return False


def mark_pdf_verified(arxiv_id):
    """Atomically clear a quality taint after a newly generated PDF is verified."""
    try:
        with paper_publication_lock(
            arxiv_id,
            lock_dir=_paper_lock_dir(),
        ):
            data = read_raw(arxiv_id)
            if not data:
                return True
            data["pdf_status"] = "ok"
            data.pop(PDF_QUALITY_TAINT_FIELD, None)
            data.pop(PDF_QUALITY_TAINT_REASON_FIELD, None)
            data.pop(PDF_QUALITY_TAINT_AT_FIELD, None)
            _write_raw_unlocked(data)
            return True
    except Exception:
        return False


def reconcile_existing_pdf_statuses():
    """Mark stale failed paper-store entries ok when their PDF already exists."""
    os.makedirs(paths.PAPER_STORE_DIR, exist_ok=True)
    fixed = []
    for name in sorted(os.listdir(paths.PAPER_STORE_DIR)):
        if not name.endswith(".json"):
            continue
        arxiv_id = name[:-5]
        data = read_raw(arxiv_id)
        if data.get("pdf_status") != "failed":
            continue
        if pdf_quality_tainted(data):
            continue
        if not pdf_exists(arxiv_id):
            continue
        try:
            if update_pdf_status(arxiv_id, "ok"):
                fixed.append(arxiv_id)
        except Exception:
            continue
    return fixed
