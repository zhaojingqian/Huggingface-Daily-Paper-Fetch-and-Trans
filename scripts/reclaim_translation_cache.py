#!/usr/bin/env python3
"""List Docker translation caches that are safe to regenerate.

The Docker volume is only a runtime workspace.  The host paper store is the
source of truth for a published PDF, so a cache can be reclaimed when its
paper has a valid, non-tainted store PDF and no current failure record.  This
helper deliberately emits only arXiv identifiers; the shell entrypoint owns
Docker lifecycle and performs the actual deletion while holding the shared
translation lock.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter


ARXIV_ID_RE = re.compile(r"^\d{4}\.\d{4,5}$")
PDF_HEADER = b"%PDF-"
PDF_EOF = b"%%EOF"
MIN_PDF_BYTES = 10 * 1024
TAIL_BYTES = 4096


def _valid_pdf(path: str) -> bool:
    try:
        size = os.path.getsize(path)
        if size <= MIN_PDF_BYTES:
            return False
        with open(path, "rb") as handle:
            if handle.read(len(PDF_HEADER)) != PDF_HEADER:
                return False
            handle.seek(max(0, size - TAIL_BYTES), os.SEEK_SET)
            return PDF_EOF in handle.read(TAIL_BYTES)
    except (OSError, ValueError):
        return False


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def reclaimable_ids(root: str):
    """Return eligible IDs and counters without touching the filesystem."""
    paper_dir = os.path.join(root, "data", "papers")
    error_dir = os.path.join(root, "logs", "pdf_errors")
    if not os.path.isdir(paper_dir):
        return [], Counter(missing_store_dir=1)

    failure_ids = set()
    if os.path.isdir(error_dir):
        for name in os.listdir(error_dir):
            if name.endswith(".json"):
                aid = name[:-5]
                if ARXIV_ID_RE.fullmatch(aid):
                    failure_ids.add(aid)

    eligible = []
    counters = Counter(
        stores=0,
        eligible=0,
        protected_failure=0,
        protected_quality=0,
        protected_status=0,
        invalid_pdf=0,
    )
    for name in sorted(os.listdir(paper_dir)):
        if not name.endswith(".json"):
            continue
        aid = name[:-5]
        if not ARXIV_ID_RE.fullmatch(aid):
            continue
        counters["stores"] += 1
        data = _read_json(os.path.join(paper_dir, name))
        if not data:
            counters["protected_status"] += 1
            continue
        if aid in failure_ids:
            counters["protected_failure"] += 1
            continue
        if data.get("pdf_quality_tainted"):
            counters["protected_quality"] += 1
            continue
        if data.get("pdf_status") != "ok":
            counters["protected_status"] += 1
            continue
        if not _valid_pdf(os.path.join(paper_dir, f"{aid}_zh.pdf")):
            counters["invalid_pdf"] += 1
            continue
        eligible.append(aid)

    counters["eligible"] = len(eligible)
    return eligible, counters


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="paper-trans project root")
    parser.add_argument("--ids", action="store_true", help="print eligible IDs")
    parser.add_argument("--json", action="store_true", help="print a JSON summary")
    args = parser.parse_args(argv)
    eligible, counters = reclaimable_ids(os.path.realpath(args.root))
    if args.ids:
        sys.stdout.write("".join(f"{aid}\n" for aid in eligible))
        return 0
    payload = {
        "policy": "valid_store_pdf_and_no_failure_record",
        "eligible_ids": eligible,
        "eligible_count": len(eligible),
        "counters": dict(sorted(counters.items())),
    }
    if args.json or not args.ids:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
