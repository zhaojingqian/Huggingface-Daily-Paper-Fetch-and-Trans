"""Small side-effect boundary for running a bounded LaTeX command sequence."""

from __future__ import annotations

import subprocess
from typing import Callable, Iterable, Mapping, Optional, Sequence


def command_sequence(engine: str, has_bbl: bool) -> list[list[str]]:
    """Build the deterministic XeLaTeX/LuaLaTeX + BibTeX command sequence."""
    if engine == "xelatex":
        engine_command = [
            engine,
            "-no-shell-escape",
            "-no-pdf",
            "-interaction=nonstopmode",
            "-file-line-error",
            "merge_translate_zh.tex",
        ]
    else:
        engine_command = [
            engine,
            "-no-shell-escape",
            "-interaction=nonstopmode",
            "-file-line-error",
            "merge_translate_zh.tex",
        ]
    if has_bbl:
        return [list(engine_command) for _ in range(4)]
    return [
        list(engine_command),
        ["bibtex", "merge_translate_zh"],
        list(engine_command),
        list(engine_command),
        list(engine_command),
    ]


def run_sequence(
    workfolder: str,
    commands: Iterable[Sequence[str]],
    environment: Optional[Mapping[str, str]],
    sanitize_aux: Callable[[str], object],
    *,
    timeout: int = 900,
    runner: Callable[..., object] = subprocess.run,
) -> bool:
    """Run one compile sequence and return whether an engine segfaulted."""
    segfault = False
    is_xelatex = False
    commands = list(commands)
    for index, command in enumerate(commands):
        result = runner(
            list(command),
            cwd=workfolder,
            timeout=timeout,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env=environment,
        )
        executable = str(command[0]) if command else ""
        is_xelatex = is_xelatex or executable == "xelatex"
        if executable in ("xelatex", "lualatex") and index < len(commands) - 1:
            sanitize_aux(workfolder)
        stderr = (getattr(result, "stderr", b"") or b"")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        returncode = int(getattr(result, "returncode", 0) or 0)
        if returncode >= 128 or "Segmentation fault" in stderr:
            segfault = True
            break

    if not segfault and is_xelatex:
        print(
            "[driver] 🛠️  运行 xdvipdfmx 转换 DVI 为 PDF "
            "(zlib compression level = 3)",
            flush=True,
        )
        result = runner(
            ["xdvipdfmx", "-z", "3", "merge_translate_zh.xdv"],
            cwd=workfolder,
            timeout=timeout,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        stderr = (getattr(result, "stderr", b"") or b"")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        returncode = int(getattr(result, "returncode", 0) or 0)
        if returncode != 0 or "Segmentation fault" in stderr:
            print(
                "[driver] ❌ xdvipdfmx 运行失败: "
                f"returncode={returncode}, stderr={stderr[:200]}",
                flush=True,
            )
            segfault = True
    return segfault
