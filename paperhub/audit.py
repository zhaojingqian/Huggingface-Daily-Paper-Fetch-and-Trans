#!/usr/bin/env python3
"""Repository data-integrity audit used by maintenance and repair workflows."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from pathlib import Path
from typing import Dict, List

from paperhub import paper_store
from paperhub.pdf_text_quality import (
    DEFAULT_MAX_PAGES,
    DEFAULT_MAX_TEXT_BYTES,
    DEFAULT_TIMEOUT_SECONDS,
    analyze_pdf,
    analyze_pdf_cached,
)
from paperhub.translation_quality import analyze_tex, is_untranslated_prose


def audit_repository(
    data_dir: str,
    logs_dir: str,
    min_pdf_bytes: int = 10_240,
    scan_pdf_text: bool = False,
    pdf_text_max_files: int = 250,
    pdf_text_max_pages: int = DEFAULT_MAX_PAGES,
    pdf_text_max_bytes: int = DEFAULT_MAX_TEXT_BYTES,
    pdf_text_timeout: int = DEFAULT_TIMEOUT_SECONDS,
    pdf_text_workers: int = 4,
    pdf_text_cache: bool = True,
) -> Dict[str, object]:
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
        "store_pdf_status_mismatch": [],
        "quality_tainted": [],
        "quality_untranslated_prose": [],
        "quality_scan_error": [],
        "quality_pdf_untranslated_prose": [],
        "quality_pdf_partial_untranslated_prose": [],
        "quality_pdf_translation_refusal": [],
        "quality_pdf_scan_error": [],
        "quality_pdf_inconclusive": [],
    }
    pdf_valid_cache: Dict[str, bool] = {}

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

    def valid_pdf(aid: str) -> bool:
        if aid not in pdf_valid_cache:
            pdf_valid_cache[aid] = paper_store.pdf_file_valid(
                paper_root / f"{aid}_zh.pdf",
                min_pdf_bytes,
            )
        return pdf_valid_cache[aid]

    index_paths = sorted(data_root.glob("**/index.json"))
    for path in index_paths:
        try:
            index = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            issues["bad_json"].append({"path": str(path), "error": str(exc)})
            continue
        if not isinstance(index, dict):
            issues["bad_json"].append({
                "path": str(path),
                "error": "index root must be a JSON object",
            })
            continue
        papers = index.get("papers", [])
        if not isinstance(papers, list):
            issues["bad_json"].append({
                "path": str(path),
                "error": "index papers must be a JSON array",
            })
            continue
        try:
            relative_parts = path.relative_to(data_root).parts
        except ValueError:
            relative_parts = ()
        mode = str(
            index.get("mode")
            or (relative_parts[0] if relative_parts else "unknown")
        )
        entries_by_mode[mode] += len(papers)
        if index.get("total") != len(papers):
            issues["index_total_mismatch"].append(
                {"path": str(path), "declared": index.get("total"), "actual": len(papers)}
            )
        for position, item in enumerate(papers):
            if not isinstance(item, dict):
                issues["bad_json"].append({
                    "path": str(path),
                    "error": f"papers[{position}] must be a JSON object",
                })
                continue
            raw_aid = item.get("arxiv_id")
            if not isinstance(raw_aid, str) or not raw_aid.strip():
                issues["bad_json"].append({
                    "path": str(path),
                    "error": (
                        f"papers[{position}].arxiv_id must be a "
                        "non-empty string"
                    ),
                })
                continue
            aid = raw_aid.strip()
            referenced.add(aid)
            store_path = paper_root / f"{aid}.json"
            store = load_store(aid)
            if not store_path.exists():
                issues["missing_store"].append({"arxiv_id": aid, "index": str(path)})
            elif not paper_store.translation_complete(store):
                issues["missing_translation"].append({"arxiv_id": aid, "index": str(path)})

            status = item.get("pdf_status")
            if status is not None and not isinstance(status, str):
                issues["bad_json"].append({
                    "path": str(path),
                    "error": (
                        f"papers[{position}].pdf_status must be a string or null"
                    ),
                })
                status = None
            pdf_is_valid = valid_pdf(aid)
            if status == "ok" and not pdf_is_valid:
                issues["ok_missing_pdf"].append({"arxiv_id": aid, "index": str(path)})
            elif status == "failed":
                issues["failed_status"].append({"arxiv_id": aid, "index": str(path)})

            store_status = store.get("pdf_status") if store else None
            if store and paper_store.pdf_quality_tainted(store):
                # A taint is intentionally independent from the mutable
                # status field.  Report it even if a legacy/manual writer has
                # incorrectly put the store or its index row back to ``ok``.
                issues["quality_tainted"].append({
                    "arxiv_id": aid,
                    "index": str(path),
                    "index_status": status,
                    "store_status": store_status,
                    "reason": store.get(
                        paper_store.PDF_QUALITY_TAINT_REASON_FIELD,
                        "quality.unknown",
                    ),
                })
            if (
                store
                and status in {"ok", "failed"}
                and store_status != status
            ):
                issues["store_pdf_status_mismatch"].append({
                    "arxiv_id": aid,
                    "index": str(path),
                    "index_status": status,
                    "store_status": store_status,
                    "reason": "index_store_disagree",
                })
            elif (
                store
                and status not in {"ok", "failed"}
                and store_status == "ok"
                and not pdf_is_valid
            ):
                issues["store_pdf_status_mismatch"].append({
                    "arxiv_id": aid,
                    "index": str(path),
                    "index_status": status,
                    "store_status": store_status,
                    "reason": "store_ok_missing_pdf",
                })

    quality_tex_ids = set()
    tex_backup_root = data_root / "tex_backup"
    for aid in sorted(referenced):
        tex_path = tex_backup_root / f"{aid}_merge_translate_zh.tex"
        if not tex_path.is_file():
            continue
        try:
            quality = analyze_tex(tex_path)
        except (OSError, UnicodeError, ValueError) as exc:
            issues["quality_scan_error"].append({
                "arxiv_id": aid,
                "tex": str(tex_path),
                "error": str(exc),
            })
            continue
        quality_tex_ids.add(aid)
        if is_untranslated_prose(quality):
            issues["quality_untranslated_prose"].append({
                "arxiv_id": aid,
                "tex": str(tex_path),
                "cjk_pct": quality["cjk_pct"],
                "long_english_lines": quality["long_english_lines"],
                "mixed_english_clause_count": quality[
                    "mixed_english_clause_count"
                ],
                "mixed_english_clause_samples": quality[
                    "mixed_english_clause_samples"
                ],
                "samples": quality["samples"],
            })

    error_dir = Path(logs_dir) / "pdf_errors"
    failed_tex_dir = data_root / "tex_backup_failed"
    valid_pdf_ids = {aid for aid in referenced if valid_pdf(aid)}
    quality_covered_pdf_ids = valid_pdf_ids & quality_tex_ids
    quality_without_tex_pdf_ids = sorted(valid_pdf_ids - quality_tex_ids)
    pdf_text_scanned_ids = set()
    pdf_text_extracted_ids = set()
    pdf_text_page_limited_ids = []
    pdf_text_inconclusive_ids = []
    pdf_text_targets = []
    pdf_text_retried = 0
    pdf_text_recovered_after_retry = 0
    pdf_text_cache_hits = 0
    if scan_pdf_text:
        pdf_text_targets = quality_without_tex_pdf_ids[
            :max(0, int(pdf_text_max_files))
        ]

        def scan_pdf(aid):
            pdf_path = paper_root / f"{aid}_zh.pdf"
            try:
                options = {
                    "max_pages": max(1, int(pdf_text_max_pages)),
                    "max_text_bytes": max(1, int(pdf_text_max_bytes)),
                    "timeout_seconds": max(1, int(pdf_text_timeout)),
                }
                if pdf_text_cache:
                    quality = analyze_pdf_cached(
                        pdf_path,
                        Path(logs_dir) / "pdf_text_quality_cache",
                        **options,
                    )
                else:
                    quality = analyze_pdf(pdf_path, **options)
            except Exception as exc:
                return aid, None, str(exc)
            return aid, quality, ""

        workers = max(1, min(8, int(pdf_text_workers)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            scan_results = list(executor.map(scan_pdf, pdf_text_targets))
        # Poppler can exceed its wall-clock budget transiently when several
        # font-heavy PDFs start together. Retry only timeout cases serially;
        # deterministic extraction/format errors remain single-attempt.
        for index, result in enumerate(scan_results):
            aid, quality, error = result
            if not error or "timed out" not in error:
                continue
            pdf_text_retried += 1
            retried = scan_pdf(aid)
            scan_results[index] = retried
            if not retried[2]:
                pdf_text_recovered_after_retry += 1
        for aid, quality, error in scan_results:
            if error:
                issues["quality_pdf_scan_error"].append({
                    "arxiv_id": aid,
                    "pdf": str(paper_root / f"{aid}_zh.pdf"),
                    "error": error,
                })
                continue
            pdf_text_extracted_ids.add(aid)
            if quality.get("_cache_hit"):
                pdf_text_cache_hits += 1
            if quality["page_limit_reached"]:
                pdf_text_page_limited_ids.append(aid)
            inconclusive_reasons = []
            if quality["page_limit_reached"]:
                inconclusive_reasons.append("page_limit_reached")
            if int(quality.get("pages_scanned", 0)) <= 0:
                inconclusive_reasons.append("no_pages_extracted")
            elif int(quality.get("analyzable_pages", 0)) <= 0:
                inconclusive_reasons.append("no_analyzable_prose")
            if inconclusive_reasons:
                pdf_text_inconclusive_ids.append(aid)
                issues["quality_pdf_inconclusive"].append({
                    "arxiv_id": aid,
                    "pdf": str(paper_root / f"{aid}_zh.pdf"),
                    "reasons": inconclusive_reasons,
                    "pages_scanned": quality.get("pages_scanned", 0),
                    "analyzable_pages": quality.get(
                        "analyzable_pages",
                        0,
                    ),
                })
            else:
                pdf_text_scanned_ids.add(aid)
            if quality["untranslated_prose"]:
                issues["quality_pdf_untranslated_prose"].append({
                    "arxiv_id": aid,
                    "pdf": str(paper_root / f"{aid}_zh.pdf"),
                    "cjk_pct": quality["cjk_pct"],
                    "analyzable_pages": quality["analyzable_pages"],
                    "english_dominant_pages": (
                        quality["english_dominant_pages"]
                    ),
                    "longest_english_page_run": (
                        quality["longest_english_page_run"]
                    ),
                    "reference_pages": quality["reference_pages"],
                    "source_data_pages": quality["source_data_pages"],
                    "structural_pages": quality["structural_pages"],
                    "samples": quality["samples"],
                })
            if int(quality.get("translation_refusal_pages", 0)) > 0:
                issues["quality_pdf_translation_refusal"].append({
                    "arxiv_id": aid,
                    "pdf": str(paper_root / f"{aid}_zh.pdf"),
                    "pages": quality[
                        "translation_refusal_page_numbers"
                    ],
                    "samples": quality["translation_refusal_samples"],
                })
            if int(
                quality.get("partial_untranslated_prose_pages", 0)
            ) > 0 and not quality["untranslated_prose"]:
                issues["quality_pdf_partial_untranslated_prose"].append({
                    "arxiv_id": aid,
                    "pdf": str(paper_root / f"{aid}_zh.pdf"),
                    "pages": quality[
                        "partial_untranslated_prose_page_numbers"
                    ],
                    "samples": quality[
                        "partial_untranslated_prose_samples"
                    ],
                })

    for key, values in issues.items():
        if key in {"bad_json", "index_total_mismatch"}:
            continue
        unique = {
            (item.get("arxiv_id"), item.get("index")): item
            for item in values
        }
        issues[key] = list(unique.values())

    quality_scanned_pdf_ids = quality_covered_pdf_ids | pdf_text_scanned_ids
    quality_unscanned_pdf_ids = sorted(valid_pdf_ids - quality_scanned_pdf_ids)
    return {
        "index_files": len(index_paths),
        "entries_by_mode": dict(sorted(entries_by_mode.items())),
        "unique_referenced_papers": len(referenced),
        "quality_tex_scanned": len(quality_tex_ids),
        "quality_coverage": {
            "valid_referenced_pdfs": len(valid_pdf_ids),
            "valid_pdfs_with_tex": len(quality_covered_pdf_ids),
            "valid_pdfs_without_tex": len(quality_without_tex_pdf_ids),
            "valid_pdf_tex_coverage_pct": round(
                100 * len(quality_covered_pdf_ids) / max(1, len(valid_pdf_ids)),
                1,
            ),
            "valid_pdfs_with_pdf_text_scan": len(pdf_text_scanned_ids),
            "valid_pdfs_quality_scanned": len(quality_scanned_pdf_ids),
            "valid_pdfs_unscanned": len(quality_unscanned_pdf_ids),
            "valid_pdf_quality_coverage_pct": round(
                100 * len(quality_scanned_pdf_ids) / max(1, len(valid_pdf_ids)),
                1,
            ),
        },
        "quality_pdf_text_scan": {
            "enabled": bool(scan_pdf_text),
            "attempted": len(pdf_text_targets),
            "successful_extractions": len(pdf_text_extracted_ids),
            "scanned": len(pdf_text_scanned_ids),
            "inconclusive": len(pdf_text_inconclusive_ids),
            "inconclusive_ids": sorted(pdf_text_inconclusive_ids),
            "errors": len(issues["quality_pdf_scan_error"]),
            "retried_timeouts": pdf_text_retried,
            "recovered_after_retry": pdf_text_recovered_after_retry,
            "cache_enabled": bool(pdf_text_cache),
            "cache_hits": pdf_text_cache_hits,
            "skipped_by_file_limit": max(
                0,
                len(quality_without_tex_pdf_ids) - len(pdf_text_targets),
            ),
            "page_limited": len(pdf_text_page_limited_ids),
            "page_limited_ids": sorted(pdf_text_page_limited_ids),
            "max_files": max(0, int(pdf_text_max_files)),
            "max_pages_per_pdf": max(1, int(pdf_text_max_pages)),
            "max_text_bytes_per_pdf": max(1, int(pdf_text_max_bytes)),
            "timeout_seconds_per_pdf": max(1, int(pdf_text_timeout)),
            "workers": max(1, min(8, int(pdf_text_workers))),
        },
        "quality_without_tex_pdf_ids": quality_without_tex_pdf_ids,
        "quality_unscanned_pdf_ids": quality_unscanned_pdf_ids,
        "paper_store_json_files": len(list(paper_root.glob("*.json"))),
        "failure_logs": len(list(error_dir.glob("*.log"))) if error_dir.is_dir() else 0,
        "failure_sidecars": len(list(error_dir.glob("*.json"))) if error_dir.is_dir() else 0,
        "failed_tex_backups": len(list(failed_tex_dir.glob("*.tex"))) if failed_tex_dir.is_dir() else 0,
        "issues": issues,
        "issue_counts": {name: len(values) for name, values in issues.items()},
    }
