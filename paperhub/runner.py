#!/usr/bin/env python3
"""Shared CLI entrypoint for daily, weekly, and monthly fetch wrappers."""

import sys
from typing import Optional, Sequence

from paperhub.modes import mode_spec


def run_fetch_mode_cli(mode: str, argv: Optional[Sequence[str]] = None) -> int:
    from run_papers import run

    spec = mode_spec(mode)
    args = list(sys.argv[1:] if argv is None else argv)
    positional = [arg for arg in args if not arg.startswith("--")]
    key = positional[0] if positional else spec.current_key()
    ok = run(
        mode=mode,
        key=key,
        limit=spec.limit,
        do_full_translate="--no-full" not in args,
    )
    return 0 if ok else 1

