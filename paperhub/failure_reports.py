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

