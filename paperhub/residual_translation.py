"""Small, deterministic helpers for terminal translation-quality repair.

The full-paper translator should not repeat hundreds of successful requests
because a quality gate found two or three English seams.  This module keeps
the policy pure and testable; the container driver owns the actual LLM calls.
"""

import re
from typing import Dict, Iterable, List


DEFAULT_MAX_LINES = 12


def residual_score(report: Dict[str, object]) -> tuple:
    """Return a lexicographic score where smaller means less residual prose."""
    return (
        int(report.get("very_long_english_lines", 0)),
        int(report.get("long_english_lines", 0)),
        int(report.get("mixed_english_clause_count", 0)),
        int(report.get("mixed_english_clause_words", 0)),
    )


def candidate_line_numbers(
    report: Dict[str, object], max_lines: int = DEFAULT_MAX_LINES
) -> List[int]:
    """Return unique, bounded TeX line numbers reported by the quality gate."""
    numbers = []
    samples: Iterable[object] = list(report.get("samples", []) or []) + list(
        report.get("mixed_english_clause_samples", []) or []
    )
    for sample in samples:
        line = sample.get("line") if isinstance(sample, dict) else sample[0]
        try:
            line_no = int(line)
        except (TypeError, ValueError, IndexError):
            continue
        if line_no > 0 and line_no not in numbers:
            numbers.append(line_no)
        if len(numbers) >= max_lines:
            break
    return numbers


def terminal_repair_eligible(
    report: Dict[str, object], max_lines: int = DEFAULT_MAX_LINES
) -> bool:
    """Limit targeted repair to high-coverage, small-residual translations."""
    if report.get("ok", True):
        return False
    cjk_pct = float(report.get("cjk_pct_exact", report.get("cjk_pct", 0.0)))
    long_lines = int(report.get("long_english_lines", 0))
    mixed_clauses = int(report.get("mixed_english_clause_count", 0))
    lines = candidate_line_numbers(report, max_lines=max_lines + 1)
    return (
        cjk_pct >= 60.0
        and long_lines <= 8
        and 0 < mixed_clauses + long_lines <= max_lines
        and 0 < len(lines) <= max_lines
    )


_FENCED_RESPONSE_RE = re.compile(
    r"\A\s*```(?:latex|tex)?\s*\n(?P<body>.*?)\n```\s*\Z",
    re.IGNORECASE | re.DOTALL,
)


def normalize_residual_response(response: object) -> str:
    """Remove only an exact outer Markdown fence; keep TeX content unchanged."""
    value = response if isinstance(response, str) else str(response or "")
    match = _FENCED_RESPONSE_RE.match(value)
    if match:
        value = match.group("body")
    return value.strip()
