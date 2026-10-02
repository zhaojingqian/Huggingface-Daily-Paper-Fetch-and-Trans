"""Adapters for the gpt-academic LaTeX runtime.

The compile pipeline owns repair and publication gates.  This module owns the
third-party monkey patches needed only while the plugin runs inside Docker.
Keeping that integration boundary separate prevents the fallback compiler from
depending on plugin import details.
"""

from __future__ import annotations

try:
    import latex_translation_filters as _ltf
except ImportError:
    from paperhub import latex_translation_filters as _ltf

try:
    from latex_pipeline import _restricted_tex_env
except ImportError:
    from paperhub.latex_pipeline import _restricted_tex_env


def install_gpt_academic_patches() -> bool:
    """Install deterministic TeX-toolbox adapters inside the container."""
    import importlib
    import os as _os
    import re as _re
    import signal as _signal
    import subprocess as _subprocess

    toolbox_spec = importlib.util.find_spec(
        "crazy_functions.latex_fns.latex_toolbox"
    )
    if not toolbox_spec:
        return False

    toolbox = importlib.import_module("crazy_functions.latex_fns.latex_toolbox")

    def _patched_compile_with_timeout(command, cwd, timeout=300):
        """Bound compilation and disable TeX shell escape / broad file access."""
        if not _os.path.isabs(cwd):
            cwd = _os.path.join("/gpt", cwd)
        cwd = _os.path.abspath(cwd)
        command = _ltf.force_no_tex_shell_escape(command)
        process = _subprocess.Popen(
            command,
            shell=True,
            stdout=_subprocess.PIPE,
            stderr=_subprocess.PIPE,
            cwd=cwd,
            env=_restricted_tex_env(),
            preexec_fn=_os.setsid,
        )
        try:
            process.communicate(timeout=timeout)
            if process.returncode != 0:
                print(
                    f"[driver] ⚠️  LaTeX 命令失败（exit={process.returncode}）",
                    flush=True,
                )
            return process.returncode == 0
        except _subprocess.TimeoutExpired:
            try:
                _os.killpg(_os.getpgid(process.pid), _signal.SIGKILL)
            except Exception:
                process.kill()
            try:
                process.communicate(timeout=5)
            except Exception:
                pass
            print(f"[driver] ⚠️  pdflatex 超时（{timeout}s），已强制终止", flush=True)
            return False

    toolbox.compile_latex_with_timeout = _patched_compile_with_timeout
    print(
        "[driver] ✅ compile_latex_with_timeout 已 patch"
        "（timeout=300s，受限 TeX I/O，禁用 shell escape，进程组 kill）",
        flush=True,
    )

    def _rm_comments_simple(text):
        lines = []
        for line in text.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("%"):
                continue
            idx = line.find("%")
            if idx >= 0:
                line = line[:idx]
            lines.append(line)
        return "\n".join(lines)

    def _patched_find_main_tex_file(file_manifest, mode):
        import numpy as _np

        candidates = []
        for texf in file_manifest:
            if _os.path.basename(texf).startswith("merge"):
                continue
            try:
                with open(texf, "r", encoding="utf8", errors="ignore") as handle:
                    clean = _rm_comments_simple(handle.read())
            except Exception:
                continue
            if r"\documentclass" in clean or _re.search(r"\\(?:begin|end)\s*\{document\}", clean):
                candidates.append(texf)

        if not candidates:
            raise RuntimeError("无法找到主 TeX（documentclass 或 document 环境入口）")
        if len(candidates) == 1:
            print(f"[driver] ✅ 主 Tex 文件: {candidates[0]}", flush=True)
            return candidates[0]

        unexpected_words = [
            r"\LaTeX", "manuscript", "Guidelines", "font", "citations",
            "rejected", "blind review", "reviewers",
        ]
        expected_words = [r"\input", r"\ref", r"\cite"]
        scores = []
        for texf in candidates:
            try:
                with open(texf, "r", encoding="utf8", errors="ignore") as handle:
                    content = toolbox.rm_comments(handle.read())
            except Exception:
                content = ""
            score = sum(-1 for word in unexpected_words if word in content)
            score += sum(1 for word in expected_words if word in content)
            score += _ltf.rank_main_tex_candidate(texf, content, candidates)
            scores.append(score)
        best = candidates[int(_np.argmax(scores))]
        names = [_os.path.basename(candidate) for candidate in candidates]
        print(
            f"[driver] ✅ 主 Tex 文件 (多候选, scores={dict(zip(names, scores))}): {best}",
            flush=True,
        )
        return best

    toolbox.find_main_tex_file = _patched_find_main_tex_file
    actions_spec = importlib.util.find_spec(
        "crazy_functions.latex_fns.latex_actions"
    )
    if actions_spec:
        actions = importlib.import_module("crazy_functions.latex_fns.latex_actions")
        # latex_actions imports these symbols eagerly; patch both modules so
        # the plugin cannot fall back to an unbounded compiler.
        actions.compile_latex_with_timeout = _patched_compile_with_timeout
        actions.find_main_tex_file = _patched_find_main_tex_file
    print(
        "[driver] ✅ find_main_tex_file 已 patch（注释行不参与 documentclass 检测）",
        flush=True,
    )

    original_merge = toolbox.merge_tex_files_

    def _patched_merge_tex_files_(project_folder, main_file, mode):
        main_file = toolbox.rm_comments(main_file)
        matches = list(_re.finditer(r"\\input\{(.*?)\}", main_file, _re.M))
        for match in reversed(matches):
            raw_target = match.group(1)
            if _ltf.is_dynamic_tex_include_target(raw_target):
                print(
                    f"[driver] ⚠️  保留动态 TeX include，由 TeX 运行时解析: {raw_target}",
                    flush=True,
                )
                continue
            target = _ltf.normalize_tex_include_target(raw_target)
            path = _os.path.join(project_folder, target)
            resolved = toolbox.find_tex_file_ignore_case(path)
            if resolved:
                try:
                    with open(resolved, "r", encoding="utf-8", errors="replace") as handle:
                        content = handle.read()
                except Exception:
                    content = "\n\nWarning from GPT-Academic: LaTex source file is missing!\n\n"
                if _ltf.requires_runtime_tex_scope(content, main_file[:match.start()]):
                    print(
                        f"[driver] ⚠️  保留作用域敏感 TeX include，交由 TeX 运行时解析: {target}",
                        flush=True,
                    )
                    continue
            else:
                probe = target if _os.path.splitext(target)[1] else target + ".tex"
                try:
                    result = _subprocess.run(
                        ["kpsewhich", probe],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                except _subprocess.TimeoutExpired:
                    raise RuntimeError(f"找不到{path}，Tex源文件缺失！")
                if result.returncode == 0 and result.stdout.strip():
                    print(
                        f"[driver] ⚠️  跳过系统 TeX 文件（非项目文件）: {target}",
                        flush=True,
                    )
                    content = f"% [system file skipped by driver patch: {target}]\n"
                else:
                    raise RuntimeError(f"找不到{path}，Tex源文件缺失！")
            content = _patched_merge_tex_files_(project_folder, content, mode)
            main_file = main_file[:match.start()] + content + main_file[match.end():]
        return main_file

    toolbox.merge_tex_files_ = _patched_merge_tex_files_
    print(
        "[driver] ✅ merge_tex_files_ 已 patch（系统文件跳过 + 动态 include 保留）",
        flush=True,
    )
    return True
