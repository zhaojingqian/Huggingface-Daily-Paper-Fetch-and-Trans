#!/usr/bin/env python3
"""Shared paper store helpers.

The paper store has two read modes:
- raw reads for Web rendering and status repair;
- translated-cache reads for the summary translator, which should only reuse
  entries that already contain a Chinese title.
"""

import json
import os
import re
import shutil

from paperhub import paths


MIN_VALID_PDF_BYTES = 10240


def has_chinese(text):
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def json_path(arxiv_id):
    os.makedirs(paths.PAPER_STORE_DIR, exist_ok=True)
    return paths.paper_store_json_path(arxiv_id)


def pdf_path(arxiv_id):
    os.makedirs(paths.PAPER_STORE_DIR, exist_ok=True)
    return paths.paper_store_pdf_path(arxiv_id)


def read_raw(arxiv_id):
    try:
        with open(json_path(arxiv_id), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def read_translated(arxiv_id):
    data = read_raw(arxiv_id)
    return data if has_chinese(data.get("title_zh", "")) else None


def write_raw(payload):
    with open(json_path(payload["arxiv_id"]), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def pdf_exists(arxiv_id, min_bytes=MIN_VALID_PDF_BYTES):
    p = paths.paper_store_pdf_path(arxiv_id)
    return os.path.exists(p) and os.path.getsize(p) > min_bytes


def pdf_hit(arxiv_id, min_bytes=MIN_VALID_PDF_BYTES):
    p = paths.paper_store_pdf_path(arxiv_id)
    return p if os.path.exists(p) and os.path.getsize(p) > min_bytes else None


def save_pdf(arxiv_id, src_path):
    shutil.copy2(src_path, pdf_path(arxiv_id))


def update_pdf_status(arxiv_id, status):
    data = read_raw(arxiv_id)
    if not data:
        return False
    data["pdf_status"] = status
    try:
        write_raw(data)
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
        if not pdf_exists(arxiv_id):
            continue
        data["pdf_status"] = "ok"
        try:
            write_raw(data)
            fixed.append(arxiv_id)
        except Exception:
            continue
    return fixed
