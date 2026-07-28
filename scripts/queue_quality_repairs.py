#!/usr/bin/env python3
"""Queue structurally valid but substantially untranslated PDFs for repair."""

import argparse
import glob
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from failure_taxonomy import quality_failure
from paperhub.audit import audit_repository
from paperhub.json_io import read_json, write_json_atomic
from paperhub.paths import DATA_DIR, LOGS_DIR
from paperhub.publication_lock import (
    LOCK_SHARED,
    PublicationLock,
    catalog_lock_path,
    lock_dir_for_index,
    merge_index_paper_fields,
    paper_lock_path,
)
from paperhub.translation_quality import analyze_tex, is_untranslated_prose


PDF_ISSUE_CATEGORIES = (
    (
        "quality_pdf_untranslated_prose",
        "quality.pdf_sustained_untranslated",
    ),
    (
        "quality_pdf_partial_untranslated_prose",
        "quality.pdf_partial_untranslated",
    ),
    (
        "quality_pdf_translation_refusal",
        "quality.translation_refusal",
    ),
)

_CATEGORY_PRIORITY = {
    "quality.untranslated_prose": 10,
    "quality.pdf_partial_untranslated": 20,
    "quality.pdf_sustained_untranslated": 30,
    "quality.translation_refusal": 40,
}


class QueuePreflightError(RuntimeError):
    """Abort an apply before any write when repository state is unsafe."""


def _repository_lock_dir(data_dir):
    data_dir = os.path.realpath(data_dir)
    default_data = os.path.realpath(DATA_DIR)
    if data_dir == default_data:
        return os.path.join(os.path.dirname(default_data), "locks")
    root = os.path.dirname(data_dir) if os.path.basename(data_dir) == "data" else data_dir
    return os.path.join(root, "locks")


def _index_files(data_dir):
    return sorted(glob.glob(os.path.join(data_dir, "**", "index.json"), recursive=True))


def referenced_papers(data_dir):
    references = {}
    for path in _index_files(data_dir):
        payload = read_json(path, {})
        if not isinstance(payload, dict):
            continue
        for paper in payload.get("papers", []):
            if not isinstance(paper, dict):
                continue
            arxiv_id = str(paper.get("arxiv_id") or "").strip()
            if arxiv_id:
                references.setdefault(arxiv_id, []).append(path)
    return references


def _find_tex_quality_failures(data_dir, references):
    backup_dir = os.path.join(data_dir, "tex_backup")
    failures = []
    for path in sorted(glob.glob(os.path.join(backup_dir, "*_merge_translate_zh.tex"))):
        arxiv_id = os.path.basename(path).split("_merge_translate_zh.tex", 1)[0]
        if arxiv_id not in references:
            continue
        report = analyze_tex(path)
        if not is_untranslated_prose(report):
            continue
        failures.append({
            "arxiv_id": arxiv_id,
            "cjk_pct": report["cjk_pct"],
            "long_english_lines": report["long_english_lines"],
            "very_long_english_lines": report["very_long_english_lines"],
            "mixed_english_clause_count": report[
                "mixed_english_clause_count"
            ],
            "mixed_english_clause_samples": report[
                "mixed_english_clause_samples"
            ],
            "prose_lines": report["prose_lines"],
            "tex_path": path,
            "indexes": references[arxiv_id],
        })
    return failures


def _find_pdf_quality_failures(data_dir, logs_dir, references):
    """Map cached audit findings for valid PDFs without TeX to queue records."""
    report = audit_repository(
        data_dir,
        logs_dir,
        scan_pdf_text=True,
        pdf_text_cache=True,
    )
    issues = report.get("issues", {})
    failures = []
    for issue_name, category in PDF_ISSUE_CATEGORIES:
        for issue in issues.get(issue_name, []):
            if not isinstance(issue, dict):
                continue
            arxiv_id = str(issue.get("arxiv_id") or "").strip()
            if not arxiv_id or arxiv_id not in references:
                continue
            failure = {
                "arxiv_id": arxiv_id,
                "category": category,
                "source": "pdf_text",
                "pdf_path": issue.get("pdf", ""),
                "indexes": references[arxiv_id],
            }
            for key, value in issue.items():
                if key not in {"arxiv_id", "pdf"}:
                    failure[key] = value
            failures.append(failure)
    return failures


def find_quality_failures(data_dir, logs_dir=None, scan_pdf_text=False):
    """Return referenced TeX failures and optionally cached PDF-text findings."""
    references = referenced_papers(data_dir)
    failures = _find_tex_quality_failures(data_dir, references)
    if scan_pdf_text:
        if logs_dir is None:
            logs_dir = os.path.join(
                os.path.dirname(os.path.abspath(data_dir)),
                "logs",
            )
        failures.extend(
            _find_pdf_quality_failures(data_dir, logs_dir, references)
        )
    return failures


def _failure_category(item):
    return str(item.get("category") or "quality.untranslated_prose")


def _failure_evidence(item):
    category = _failure_category(item)
    if item.get("source") == "existing_quality_taint":
        return f"preserved_existing_category={category}"
    if category == "quality.untranslated_prose":
        return (
            f"cjk_pct={item['cjk_pct']} "
            f"long_english_lines={item['long_english_lines']} "
            f"very_long_english_lines={item['very_long_english_lines']} "
            f"mixed_english_clause_count="
            f"{item.get('mixed_english_clause_count', 0)} "
            f"prose_lines={item['prose_lines']}"
        )
    if category == "quality.pdf_sustained_untranslated":
        return (
            f"cjk_pct={item.get('cjk_pct')} "
            f"analyzable_pages={item.get('analyzable_pages')} "
            f"english_dominant_pages={item.get('english_dominant_pages')} "
            f"longest_english_page_run={item.get('longest_english_page_run')}"
        )
    pages = item.get("pages") or []
    return f"pages={pages} samples={len(item.get('samples') or [])}"


def _select_failures(failures):
    """Choose one deterministic primary diagnosis per paper."""
    selected = {}
    for item in failures:
        arxiv_id = str(item.get("arxiv_id") or "").strip()
        if not arxiv_id:
            continue
        previous = selected.get(arxiv_id)
        if previous is None:
            selected[arxiv_id] = item
            continue
        previous_priority = _CATEGORY_PRIORITY.get(
            _failure_category(previous),
            0,
        )
        current_priority = _CATEGORY_PRIORITY.get(
            _failure_category(item),
            0,
        )
        if current_priority > previous_priority:
            selected[arxiv_id] = item
    return selected


def _validated_index_payloads(data_dir):
    loaded = []
    errors = []
    for path in _index_files(data_dir):
        payload = read_json(path, None)
        if not isinstance(payload, dict):
            errors.append(f"{path}: index root is not a JSON object")
            continue
        papers = payload.get("papers", [])
        if not isinstance(papers, list):
            errors.append(f"{path}: papers is not a JSON array")
            continue
        malformed_positions = []
        for position, paper in enumerate(papers):
            if (
                not isinstance(paper, dict)
                or not isinstance(paper.get("arxiv_id"), str)
                or not paper["arxiv_id"].strip()
            ):
                malformed_positions.append(position)
        if malformed_positions:
            errors.append(
                f"{path}: invalid papers entries at {malformed_positions}"
            )
            continue
        loaded.append((path, payload))
    if errors:
        raise QueuePreflightError(
            "refusing partial quality queue update; invalid indexes: "
            + "; ".join(errors)
        )
    return loaded


def _references_from_payloads(index_payloads):
    references = {}
    for path, payload in index_payloads:
        for paper in payload.get("papers", []):
            arxiv_id = str(paper.get("arxiv_id") or "").strip()
            references.setdefault(arxiv_id, []).append(path)
    return references


def _validated_stores(data_dir, by_id):
    stores = {}
    errors = []
    for arxiv_id in sorted(by_id):
        path = os.path.join(data_dir, "papers", f"{arxiv_id}.json")
        store = read_json(path, None)
        if not isinstance(store, dict) or not store:
            errors.append(f"{path}: missing or invalid paper store")
            continue
        stored_id = store.get("arxiv_id")
        if stored_id is not None and str(stored_id).strip() != arxiv_id:
            errors.append(
                f"{path}: arxiv_id={stored_id!r} does not match {arxiv_id}"
            )
            continue
        stores[arxiv_id] = store
    if errors:
        raise QueuePreflightError(
            "refusing partial quality queue update; invalid stores: "
            + "; ".join(errors)
        )
    return stores


def _validate_failure_categories(by_id):
    unknown = sorted({
        _failure_category(item)
        for item in by_id.values()
        if _failure_category(item) not in _CATEGORY_PRIORITY
    })
    if unknown:
        raise QueuePreflightError(
            "refusing quality queue update; unknown categories: "
            + ", ".join(unknown)
        )


def _merge_existing_taint_priorities(logs_dir, by_id, stores):
    """Prevent a repeated apply from downgrading a stronger diagnosis."""
    selected = dict(by_id)
    for arxiv_id, item in by_id.items():
        store = stores[arxiv_id]
        if not (
            isinstance(store, dict)
            and store.get("pdf_quality_tainted")
        ):
            continue
        sidecar_path = os.path.join(
            logs_dir,
            "pdf_errors",
            f"{arxiv_id}.json",
        )
        sidecar = read_json(sidecar_path, {})
        store_category = str(
            store.get("pdf_quality_taint_reason") or ""
        )
        sidecar_category = (
            str(sidecar.get("category") or "")
            if isinstance(sidecar, dict)
            else ""
        )
        categories = (
            _failure_category(item),
            store_category,
            sidecar_category,
        )
        category = max(
            categories,
            key=lambda value: _CATEGORY_PRIORITY.get(value, -1),
        )
        if category == _failure_category(item):
            continue
        selected[arxiv_id] = {
            "arxiv_id": arxiv_id,
            "category": category,
            "source": "existing_quality_taint",
            "indexes": item.get("indexes", []),
            "_preserve_sidecar": sidecar_category == category,
        }
    return selected


def _queue_quality_failures_locked(data_dir, logs_dir, failures):
    """Persist failed status plus a structured retry-translation diagnosis."""
    by_id = _select_failures(failures)
    if not by_id:
        return {
            "queued": [],
            "queued_by_category": {},
            "changed_indexes": [],
            "missing_stores": [],
            "preserved_existing": [],
        }
    _validate_failure_categories(by_id)
    index_payloads = _validated_index_payloads(data_dir)
    references = _references_from_payloads(index_payloads)
    stores = _validated_stores(data_dir, by_id)
    by_id = _merge_existing_taint_priorities(
        logs_dir,
        by_id,
        stores,
    )
    changed_indexes = []
    for path, payload in index_payloads:
        updates = {}
        for paper in payload.get("papers", []):
            arxiv_id = str(paper.get("arxiv_id") or "").strip()
            if arxiv_id not in by_id:
                continue
            updates[arxiv_id] = {"pdf_status": "failed"}
        if updates:
            relative = os.path.relpath(path, data_dir).split(os.sep)
            mode = relative[0]
            key = "/".join(relative[1:-1])
            lock_dir = lock_dir_for_index(
                path,
                mode,
                data_dir=data_dir,
                lock_dir=_repository_lock_dir(data_dir),
            )
            merged = merge_index_paper_fields(
                path,
                updates,
                mode=mode,
                key=key,
                lock_dir=lock_dir,
            )
        else:
            merged = {"changed_fields": 0}
        if merged["changed_fields"]:
            changed_indexes.append(path)

    error_dir = os.path.join(logs_dir, "pdf_errors")
    os.makedirs(error_dir, exist_ok=True)
    queued = []
    queued_by_category = Counter()
    preserved_existing = []
    for arxiv_id, item in sorted(by_id.items()):
        category = _failure_category(item)
        queued_at = datetime.now(timezone.utc).isoformat()
        store_path = os.path.join(data_dir, "papers", f"{arxiv_id}.json")
        with PublicationLock(
            [
                paper_lock_path(
                    arxiv_id,
                    lock_dir=_repository_lock_dir(data_dir),
                )
            ],
            timeout=30,
        ):
            store = read_json(store_path, None)
            if not isinstance(store, dict) or not store:
                raise QueuePreflightError(
                    f"{store_path}: store disappeared during apply"
                )
            store["pdf_status"] = "failed"
            store["pdf_quality_tainted"] = True
            store["pdf_quality_taint_reason"] = category
            store["pdf_quality_tainted_at"] = queued_at
            write_json_atomic(store_path, store)

        metadata = quality_failure(category, _failure_evidence(item))
        if category == "quality.untranslated_prose":
            # Preserve the established TeX queue wording and payload.
            metadata["suggestion"] = (
                "普通正文中文覆盖不足；按最新 chunk 策略重新翻译，"
                "成功通过覆盖率和编译门禁后再恢复 ok。"
            )
        diagnosis = {
            "arxiv_id": arxiv_id,
            "phase": "quality",
            **metadata,
            "indexes": references.get(
                arxiv_id,
                item.get("indexes", []),
            ),
            "queued_at": queued_at,
        }
        for key in (
            "cjk_pct",
            "long_english_lines",
            "very_long_english_lines",
            "mixed_english_clause_count",
            "mixed_english_clause_samples",
            "prose_lines",
            "tex_path",
            "pdf_path",
            "analyzable_pages",
            "english_dominant_pages",
            "longest_english_page_run",
            "reference_pages",
            "source_data_pages",
            "structural_pages",
            "pages",
            "samples",
            "source",
        ):
            if key in item:
                diagnosis[key] = item[key]
        if item.get("_preserve_sidecar"):
            preserved_existing.append(arxiv_id)
        else:
            write_json_atomic(
                os.path.join(error_dir, f"{arxiv_id}.json"),
                diagnosis,
            )
        queued.append(arxiv_id)
        queued_by_category[category] += 1
    return {
        "queued": queued,
        "queued_by_category": dict(sorted(queued_by_category.items())),
        "changed_indexes": changed_indexes,
        "missing_stores": [],
        "preserved_existing": preserved_existing,
    }


def queue_quality_failures(data_dir, logs_dir, failures):
    """Apply one queue batch while repository-wide delete scans are excluded."""
    lock_dir = _repository_lock_dir(data_dir)
    with PublicationLock(
        [(catalog_lock_path(lock_dir), LOCK_SHARED)],
        timeout=30,
    ):
        return _queue_quality_failures_locked(
            data_dir,
            logs_dir,
            failures,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="写入 failed 状态和结构化诊断",
    )
    parser.add_argument(
        "--id",
        action="append",
        default=[],
        help="仅处理指定 arXiv ID，可重复",
    )
    parser.add_argument(
        "--scan-pdf-text",
        action="store_true",
        help="显式扫描无 TeX 的有效 PDF；复用审计的有界提取缓存",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    failures = find_quality_failures(
        DATA_DIR,
        LOGS_DIR,
        scan_pdf_text=args.scan_pdf_text,
    )
    if args.id:
        selected = set(args.id)
        failures = [item for item in failures if item["arxiv_id"] in selected]
    failures = list(_select_failures(failures).values())
    result = {
        "policy": "paperhub.translation_quality.is_untranslated_prose",
        "count": len(failures),
        "failures": failures,
        "applied": False,
        "scan_pdf_text": args.scan_pdf_text,
    }
    exit_code = 0
    if args.apply:
        try:
            result.update(
                queue_quality_failures(DATA_DIR, LOGS_DIR, failures)
            )
            result["applied"] = True
        except QueuePreflightError as exc:
            result["apply_error"] = str(exc)
            exit_code = 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        counts = Counter(_failure_category(item) for item in failures)
        for category, count in sorted(counts.items()):
            print(f"{category}: {count}")
        for item in failures:
            category = _failure_category(item)
            print(
                f"  {item['arxiv_id']} [{category}]: "
                f"{_failure_evidence(item)}"
            )
        if result.get("apply_error"):
            print(f"未写入: {result['apply_error']}")
        elif args.apply:
            print(f"已加入重译队列: {len(result['queued'])}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
