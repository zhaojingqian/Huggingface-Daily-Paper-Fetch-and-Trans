"""Pure request policy shared by the splitter and LLM scheduler.

The translation pipeline has two independent controls:

* ordinary prose should be large enough to preserve context and avoid one API
  call per sentence;
* retries should be concurrent, but bounded so a bad batch cannot create a
  second unbounded burst.

This module deliberately has no filesystem, network, or model imports.  The
container copies it beside the driver as ``translation_policy.py``; the host
tests import the same source through ``paperhub``.
"""

from __future__ import annotations

import re
from typing import Mapping, Optional


DEFAULT_TRANSLATION_CHUNK_LIMIT = 2400
MAX_TRANSLATION_CHUNK_LIMIT = 3200
DENSE_TRANSLATION_CHUNK_LIMIT = 1500
STRUCTURED_TRANSLATION_CHUNK_LIMIT = 1900
RETRY_WORKER_CEILING = 16

_CITATION_COMMAND_RE = re.compile(
    r"\\(?:cite|citep|citet|citealp|citeauthor|citeyear|parencite|textcite)"
    r"\*?(?:\[[^\]]*\])?\s*\{",
    re.IGNORECASE,
)
_LATEX_COMMAND_RE = re.compile(r"\\[A-Za-z@]+")


def bounded_int(
    value,
    default: int,
    *,
    minimum: int = 0,
    maximum: Optional[int] = None,
) -> int:
    """Parse one operator value without allowing an invalid policy."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(default)
    parsed = max(int(minimum), parsed)
    if maximum is not None:
        parsed = min(int(maximum), parsed)
    return parsed


def configured_worker_count(
    env: Mapping[str, str],
    *,
    name: str = "PAPER_TRANS_LLM_WORKERS",
    default: int = 50,
    ceiling: int = 50,
) -> int:
    """Return the configured first-pass worker count."""
    return bounded_int(
        env.get(name),
        default,
        minimum=1,
        maximum=max(1, int(ceiling)),
    )


def retry_worker_count(
    remaining: int,
    configured: int,
    *,
    ceiling: int = RETRY_WORKER_CEILING,
) -> int:
    """Bound retry fan-out while keeping independent failed slots concurrent."""
    count = max(0, int(remaining))
    if not count:
        return 0
    return min(
        count,
        bounded_int(configured, 1, minimum=1, maximum=max(1, int(ceiling))),
    )


def translation_chunk_limit(text: str, default: int = DEFAULT_TRANSLATION_CHUNK_LIMIT) -> int:
    """Choose a request cap from LaTeX density, not from paper-specific IDs.

    Ordinary prose gets the larger context-preserving cap.  Citation- and
    command-dense fragments get a smaller cap because they are more likely to
    cross a structural boundary or spend most of the request on protected
    LaTeX.  The final splitter still guarantees balanced boundaries.
    """
    base = bounded_int(
        default,
        DEFAULT_TRANSLATION_CHUNK_LIMIT,
        minimum=1,
        maximum=MAX_TRANSLATION_CHUNK_LIMIT,
    )
    value = str(text or "")
    if not value:
        return base

    size = max(1, len(value))
    citations = len(_CITATION_COMMAND_RE.findall(value))
    commands = len(_LATEX_COMMAND_RE.findall(value))
    citation_density = citations * 1000.0 / size

    if citations >= 4 or (citations >= 3 and citation_density >= 2.5) or commands >= 8:
        return min(base, DENSE_TRANSLATION_CHUNK_LIMIT)
    if citations >= 2 or commands >= 4:
        return min(base, STRUCTURED_TRANSLATION_CHUNK_LIMIT)
    return base
