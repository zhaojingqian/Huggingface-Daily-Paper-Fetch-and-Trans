#!/usr/bin/env python3
"""Repository data-integrity audit used by maintenance and repair workflows."""

import json
import os
from collections import Counter
from pathlib import Path
from typing import Dict, List

from paperhub import paper_store


def audit_repository(data_dir: str, logs_dir: str, min_pdf_bytes: int = 10_240) -> Dict[str, object]:
    data_root = Path(data_dir)
    paper_root = data_root / "papers"
    store_cache: Dict[str, Dict[str, object]] = {}
    referenced = set()
    entries_by_mode = Counter()
    issues: Dict[str, List[object]] = {
        "bad_json": [],
        "index_total_mismatch": [],
        "missing_store": [],
        "missing_translation": [],
        "ok_missing_pdf": [],
        "failed_status": [],
    }

    def load_store(aid: str):
        if aid in store_cache:
            return store_cache[aid]
        path = paper_root / f"{aid}.json"
        if not path.exists():
            store_cache[aid] = {}
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("paper store root must be a JSON object")
            store_cache[aid] = payload
        except (OSError, ValueError) as exc:
            issues["bad_json"].append({"path": str(path), "error": str(exc)})
            store_cache[aid] = {}
        return store_cache[aid]

    index_paths = sorted(data_root.glob("**/index.json"))
    for path in index_paths:
        try:
            index = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            issues["bad_json"].append({"path": str(path), "error": str(exc)})
            continue
        papers = index.get("papers", [])
        mode = str(index.get("mode") or (path.parts[1] if len(path.parts) > 1 else "unknown"))
        entries_by_mode[mode] += len(papers)
        if index.get("total") != len(papers):
            issues["index_total_mismatch"].append(
                {"path": str(path), "declared": index.get("total"), "actual": len(papers)}
            )
        for item in papers:
            aid = str(item.get("arxiv_id") or "")
            if not aid:
                continue
            referenced.add(aid)
            store_path = paper_root / f"{aid}.json"
            store = load_store(aid)
            if not store_path.exists():
                issues["missing_store"].append({"arxiv_id": aid, "index": str(path)})
            elif not paper_store.translation_complete(store):
                issues["missing_translation"].append({"arxiv_id": aid, "index": str(path)})

            status = item.get("pdf_status")
            pdf_path = paper_root / f"{aid}_zh.pdf"
            if status == "ok" and (not pdf_path.exists() or pdf_path.stat().st_size <= min_pdf_bytes):
                issues["ok_missing_pdf"].append({"arxiv_id": aid, "index": str(path)})
            elif status == "failed":
                issues["failed_status"].append({"arxiv_id": aid, "index": str(path)})

    for key, values in issues.items():
        if key in {"bad_json", "index_total_mismatch"}:
            continue
        unique = {(item.get("arxiv_id"), item.get("index")): item for item in values}
        issues[key] = list(unique.values())

    error_dir = Path(logs_dir) / "pdf_errors"
    failed_tex_dir = data_root / "tex_backup_failed"
    return {
        "index_files": len(index_paths),
        "entries_by_mode": dict(sorted(entries_by_mode.items())),
        "unique_referenced_papers": len(referenced),
        "paper_store_json_files": len(list(paper_root.glob("*.json"))),
        "failure_logs": len(list(error_dir.glob("*.log"))) if error_dir.is_dir() else 0,
        "failure_sidecars": len(list(error_dir.glob("*.json"))) if error_dir.is_dir() else 0,
        "failed_tex_backups": len(list(failed_tex_dir.glob("*.tex"))) if failed_tex_dir.is_dir() else 0,
        "issues": issues,
        "issue_counts": {name: len(values) for name, values in issues.items()},
    }
