"""Pure manifest for the Python bundle copied into the LaTeX container."""

from __future__ import annotations

import os
from typing import Iterable, List


SUPPORT_RELATIVE_PATHS = (
    "full_translate_driver.py",
    "latex_translation_filters.py",
    "paperhub/translation_policy.py",
    "paperhub/latex_pipeline.py",
    "paperhub/latex_compile.py",
    "paperhub/translation_runtime.py",
    "failure_taxonomy.py",
    "paperhub/translation_quality.py",
    "paperhub/residual_translation.py",
)


def support_files(root: str) -> List[str]:
    """Resolve the one authoritative driver bundle manifest."""
    base = os.path.realpath(str(root))
    return [os.path.join(base, relative) for relative in SUPPORT_RELATIVE_PATHS]


def missing_support_files(paths: Iterable[str]) -> List[str]:
    """Return missing manifest entries without touching the filesystem."""
    return [path for path in paths if not os.path.isfile(path)]
