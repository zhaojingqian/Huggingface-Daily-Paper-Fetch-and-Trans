#!/usr/bin/env python3
"""Read current failure sidecars and summarize them by stable category."""

import os
import re
from collections import Counter
from pathlib import Path
from typing import Dict, List

from failure_taxonomy import classify_failure
from paperhub.json_io import read_json


def load_failure_records(error_dir: str) -> List[Dict[str, object]]:
    base = Path(error_dir)
    records: Dict[str, Dict[str, object]] = {}
    if not base.is_dir():
        return []

    for path in sorted(base.glob("*.json")):
        if path.name == "summary.json":
            continue
        data = read_json(str(path), {})
        if isinstance(data, dict):
            aid = str(data.get("arxiv_id") or path.stem)
            # Stable precedence rules evolve. Re-evaluate embedded raw evidence
            # for deterministic infrastructure/API causes even when an older
            # sidecar already chose a generic plugin class.
            phase = str(data.get("phase") or "translate")
            embedded_plugin = "\n".join(
                str(data.get(key) or "")
                for key in ("plugin_error_full", "evidence")
            )
            embedded_latex = str(data.get("tex_log_tail") or "")
            embedded = classify_failure(
                phase,
                embedded_latex if phase != "translate" else "",
                embedded_plugin if phase == "translate" else embedded_plugin,
            )
            if (
                embedded.get("category")
                in {
                    "infrastructure.disk_full",
                    "translate.api_quota",
                    "translate.api_auth",
                    "quality.translation_chunk_invalid",
                }
                and embedded.get("category") != data.get("category")
            ):
                old_category = data.get("category")
                preserved = {
                    key: value
                    for key, value in data.items()
                    if key not in embedded and key not in {"category", "family"}
                }
                data = {
                    **preserved,
                    **embedded,
                    "reclassified_from": old_category,
                }
            # Older sidecars could only see the final compile result and therefore
            # labelled a translation-coverage rejection as compile.unknown. Prefer
            # the richer driver log when it can turn an unknown into a stable class.
            if data.get("category") in {"compile.unknown", "unknown.unstructured"}:
                log_path = base / f"{path.stem}.log"
                if log_path.is_file():
                    log_text = log_path.read_text(encoding="utf-8", errors="replace")
                    coverage_pos = log_text.find("翻译覆盖率检查失败")
                    if coverage_pos < 0:
                        coverage_pos = log_text.lower().find("translation coverage")
                    # Coverage failures are explicit and may live in multi-megabyte
                    # driver logs. Classify the bounded evidence instead of running
                    # every fallback regex across the entire transcript.
                    evidence = (
                        log_text[max(0, coverage_pos - 120):coverage_pos + 360]
                        if coverage_pos >= 0 else log_text
                    )
                    refined = classify_failure("compile", evidence)
                    if refined.get("category") != "compile.unknown":
                        preserved = {
                            key: value for key, value in data.items()
                            if key not in refined and key not in {"category", "family"}
                        }
                        data = {**preserved, **refined, "reclassified_from": data.get("category")}
            data["arxiv_id"] = aid
            records[aid] = data

    for path in sorted(base.glob("*.log")):
        if path.stem in records:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        phase_match = re.search(r"【失败阶段】\s+(translate|compile)", text)
        phase = phase_match.group(1) if phase_match else (
            "translate" if "GPT 翻译阶段" in text else "compile"
        )
        record = classify_failure(phase, text if phase == "compile" else "", text if phase == "translate" else "")
        record.update({"arxiv_id": path.stem, "phase": phase, "legacy_log": True})
        records[path.stem] = record

    return [records[key] for key in sorted(records)]


def summarize_failures(records: List[Dict[str, object]]) -> Dict[str, object]:
    categories = Counter(str(item.get("category", "unknown")) for item in records)
    strategies = Counter(str(item.get("retry_strategy", "unknown")) for item in records)
    return {
        "total": len(records),
        "by_category": dict(sorted(categories.items())),
        "by_retry_strategy": dict(sorted(strategies.items())),
        "papers": records,
    }
