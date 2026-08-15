"""LaTeX repair and compile pipeline.

This module owns deterministic TeX repair, bibliography synthesis, and the
fallback compile path.  It has no translation scheduling or paper-store I/O;
the driver supplies the quality callbacks and cache root once at startup.
"""

from __future__ import annotations

import os
import re
import glob
import shutil

import latex_translation_filters as _ltf

try:
    from translation_quality import (
        analyze_tex as _analyze_translated_tex,
        is_untranslated_prose as _is_untranslated_prose,
    )
except ImportError:
    from paperhub.translation_quality import (
        analyze_tex as _analyze_translated_tex,
        is_untranslated_prose as _is_untranslated_prose,
    )

_arxiv_cache_dir = ""


def pdf_integrity_ok(pdf_path: str) -> bool:
    """Accept only a non-trivial, parseable PDF produced by TeX."""
    if not os.path.exists(pdf_path) or os.path.getsize(pdf_path) < 50 * 1024:
        return False
    try:
        import pypdf

        reader = pypdf.PdfReader(pdf_path)
        return len(reader.pages) > 0
    except Exception as exc:
        print(
            f"[driver] ⚠️ PDF 完整性检查失败 ({os.path.basename(pdf_path)}): {exc}",
            flush=True,
        )
        return False


def translation_quality_report(workfolder: str) -> dict:
    """Apply the shared repository/publication translated-TeX quality gate."""
    trans_tex = os.path.join(workfolder, "merge_translate_zh.tex")
    if not os.path.exists(trans_tex):
        return {"ok": False, "reason": "missing merge_translate_zh.tex"}
    report = _analyze_translated_tex(trans_tex)
    report["ok"] = not _is_untranslated_prose(report)
    report["samples"] = [
        (sample["line"], sample["text"])
        for sample in report.get("samples", [])
    ]
    return report


def translation_quality_ok(
    workfolder: str,
    arxiv_id_: str,
    report: dict | None = None,
) -> bool:
    """Log and apply the one translated-TeX quality predicate."""
    report = report if report is not None else translation_quality_report(workfolder)
    if not report.get("ok"):
        print(
            f"[driver] ❌ 翻译覆盖率检查失败: {arxiv_id_} "
            f"cjk_pct={report.get('cjk_pct', 0):.1f}% "
            f"long_english_lines={report.get('long_english_lines', 0)} "
            f"prose_lines={report.get('prose_lines', 0)}",
            flush=True,
        )
        for line_no, sample in report.get("samples", []):
            print(f"[driver]    untranslated line {line_no}: {sample}", flush=True)
        return False

    print(
        f"[driver] ✅ 翻译覆盖率检查通过: {arxiv_id_} "
        f"cjk_pct={report.get('cjk_pct', 0):.1f}% "
        f"long_english_lines={report.get('long_english_lines', 0)}",
        flush=True,
    )
    return True


def _compile_health_report(workfolder: str) -> dict:
    """Read one TeX log and classify all compile-health facts once."""
    log_path = os.path.join(workfolder, "merge_translate_zh.log")
    if not os.path.exists(log_path):
        return {"exists": False, "complete": False, "failures": [], "warnings": []}
    with open(log_path, encoding="utf-8", errors="replace") as handle:
        log = handle.read()

    primitive_re = re.compile(
        r"\\(?:" + "|".join(re.escape(name) for name in _ltf.PDFTEX_PRIMITIVE_NAMES) + r")\b"
    )
    fatal_undefined = False
    pdftex_undefined = False
    for match in re.finditer(r"Undefined control sequence", log):
        context = log[match.start(): match.start() + 300]
        if primitive_re.search(context):
            pdftex_undefined = True
        else:
            fatal_undefined = True

    failures = []
    if fatal_undefined:
        failures.append("undefined control sequence")
    if re.search(r"Missing number, treated as zero", log):
        failures.append("missing number")

    promoted = []
    if re.search(r"(?<!Package natbib Warning: )Citation .* undefined", log):
        promoted.append("undefined citation")
    if re.search(r"Reference .* undefined", log):
        promoted.append("undefined reference")
    if re.search(r"There were undefined references", log):
        promoted.append("undefined references")

    warnings = []
    if re.search(r"Package natbib Warning: .* undefined", log):
        warnings.append("natbib undefined")
    if pdftex_undefined:
        warnings.append("pdftex primitive undef")

    sample = ""
    sample_match = re.search(
        r".{0,120}(Missing number, treated as zero|"
        r"(?<!Package natbib Warning: )Citation .* undefined|"
        r"Reference .* undefined|There were undefined references|"
        r"Label\(s\) may have changed|Rerun to get cross-references right|"
        r"Package natbib Warning: .* undefined).{0,160}",
        log,
        flags=re.DOTALL,
    )
    if sample_match:
        sample = " ".join(sample_match.group(0).split())[:260]
    fatal_sample = ""
    if fatal_undefined:
        for match in re.finditer(r"Undefined control sequence", log):
            context = log[match.start(): match.start() + 300]
            if not primitive_re.search(context):
                fatal_sample = " ".join(context[:200].split())[:260]
                break

    return {
        "exists": True,
        "complete": (
            "Output written on merge_translate_zh.xdv" in log
            or "Output written on merge_translate_zh.pdf" in log
        ),
        "failures": failures,
        "promoted": promoted,
        "warnings": warnings,
        "sample": sample,
        "fatal_sample": fatal_sample,
    }


def latex_compile_health_ok(workfolder: str, arxiv_id_: str, strict: bool = False) -> bool:
    """Reject PDFs that compiled but still have unresolved TeX/cite/ref issues."""
    report = _compile_health_report(workfolder)
    if not report["exists"]:
        print(
            f"[driver] ⚠️  找不到编译日志，跳过健康检查: "
            f"{os.path.join(workfolder, 'merge_translate_zh.log')}",
            flush=True,
        )
        return True
    if not report["complete"]:
        print(
            f"[driver] ❌ 编译健康检查失败: {arxiv_id_} 找不到输出写入标记，"
            "编译日志不完整（可能已被 OOM 强杀）",
            flush=True,
        )
        return False

    failures = list(report["failures"])
    warnings = list(report["warnings"])
    if strict:
        failures.extend(report["promoted"])
    else:
        warnings.extend(report["promoted"])

    if warnings and not failures:
        print(
            f"[driver] ⚠️  编译健康警告(非致命): {arxiv_id_} "
            f"warnings={', '.join(warnings)}",
            flush=True,
        )
    if failures:
        print(
            f"[driver] ❌ 编译健康检查失败: {arxiv_id_} "
            f"issues={', '.join(failures + warnings)}",
            flush=True,
        )
        if report["sample"]:
            print(f"[driver]    log: {report['sample']}", flush=True)
        if report["fatal_sample"]:
            print(f"[driver]    log: {report['fatal_sample']}", flush=True)
        return False

    print(f"[driver] ✅ 编译健康检查通过: {arxiv_id_}", flush=True)
    return True


def latex_compile_health_only_stale_refs(workfolder: str) -> bool:
    """True when the shared health report contains only rerunnable ref warnings."""
    report = _compile_health_report(workfolder)
    if not report["exists"] or not report["complete"] or report["failures"]:
        return False
    if "undefined citation" in report["promoted"]:
        return False
    return bool(
        "undefined reference" in report["promoted"]
        or "undefined references" in report["promoted"]
    )


def install_gpt_academic_patches() -> bool:
    """Install the deterministic TeX-toolbox adapters inside the container.

    The driver owns orchestration; this boundary owns all imports and monkey
    patches that are specific to gpt-academic's LaTeX implementation.  Keeping
    the adapter here means the same driver can remain a small lifecycle shell
    while the host-side tests can inspect the patch contract without importing
    gpt-academic.
    """
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
            if r"\documentclass" in clean:
                candidates.append(texf)

        if not candidates:
            raise RuntimeError("无法找到一个主Tex文件（包含documentclass关键字）")
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
        # latex_actions imports this symbol eagerly, so patching only the
        # toolbox module leaves the plugin's main path on the unbounded
        # compiler and can strand xdvipdfmx indefinitely.
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
                if _ltf.requires_runtime_tex_scope(content):
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


def configure(*, arxiv_cache_dir: str) -> None:
    """Bind only the cache root; the TeX layer owns its own publication gates."""
    global _arxiv_cache_dir
    _arxiv_cache_dir = str(arxiv_cache_dir)


def _restricted_tex_env():
    """Constrain TeX/BibTeX file I/O to the active project tree."""
    env = os.environ.copy()
    env.update({
        "openin_any": "p",
        "openout_any": "p",
        "shell_escape": "0",
    })
    return env


def _require_configured_gates() -> None:
    if not _arxiv_cache_dir:
        raise RuntimeError("latex pipeline cache root has not been configured")



def _extract_env_blocks(content, env):
    """提取所有 \\begin{env}...\\end{env} 块，返回 (start, end, text) 列表。"""
    begin_tag = r'\begin{' + env + '}'
    end_tag   = r'\end{' + env + '}'
    blocks, pos = [], 0
    while True:
        start = content.find(begin_tag, pos)
        if start < 0:
            break
        end_idx = content.find(end_tag, start + len(begin_tag))
        if end_idx < 0:
            break
        end_idx += len(end_tag)
        blocks.append((start, end_idx, content[start:end_idx]))
        pos = end_idx
    return blocks


def _discover_tcb_listing_envs(content):
    """从 \\newtcblisting{...} 定义中发现自定义 verbatim/listing 环境。"""
    import re as _re
    envs = set()
    for pat in (
        r'\\newtcblisting\s*\{([A-Za-z][A-Za-z0-9*_-]*)\}',
        r'\\DeclareTCBListing\s*\{([A-Za-z][A-Za-z0-9*_-]*)\}',
    ):
        envs.update(_re.findall(pat, content))
    return sorted(envs)


def _discover_tcolorbox_envs(content):
    """从 \\newtcolorbox{...} 定义中发现普通 tcolorbox 环境。"""
    import re as _re
    envs = set()
    for pat in (
        r'\\newtcolorbox\s*\{([A-Za-z][A-Za-z0-9*_-]*)\}',
        r'\\DeclareTColorBox\s*\{([A-Za-z][A-Za-z0-9*_-]*)\}',
    ):
        envs.update(_re.findall(pat, content))
    return sorted(envs)


def fix_label_ref_emdash(trans_tex_path):
    """
    修复 GPT 翻译时将 \\label{}/\\ref{}/\\cite{}/\\eqref{} 等命令参数中的
    ASCII 连字符 '-' 替换为 Unicode 破折号（em-dash '—' U+2014、en-dash '–' U+2013）
    导致的 LaTeX 编译报错。

    仅替换这些命令的花括号参数内部，不触碰正文。
    返回修复的数量。
    """
    import re as _re
    with open(trans_tex_path, encoding='utf-8') as f:
        text = f.read()

    CMD_RE = _re.compile(
        r'(\\(?:label|ref|eqref|cite|citealt|citep|citet|pageref|nameref|hyperref|autoref)'
        r'(?:\[[^\]]*\])?'   # 可选 [别名]
        r'\{)([^}]*?)(\})',  # 捕获花括号内容
        _re.DOTALL,
    )

    total = 0

    def _replace(m):
        nonlocal total
        inner = m.group(2)
        fixed = inner.replace('\u2014', '-').replace('\u2013', '-')
        if fixed != inner:
            total += len([c for c in inner if c in '\u2014\u2013'])
        return m.group(1) + fixed + m.group(3)

    new_text = CMD_RE.sub(_replace, text)
    if total:
        with open(trans_tex_path, 'w', encoding='utf-8') as f:
            f.write(new_text)
        print(f"[driver] 🔧 fix_label_ref_emdash: 修复了 {total} 处破折号", flush=True)
    return total


def patch_body_endinput(trans_tex_path):
    """
    合并后的论文正文里偶尔会带入子文件的 \\endinput，导致 TeX 提前停止读取，
    后面的 \\end{document} 被忽略。只注释 \\begin{document} 之后整行的 \\endinput。
    """
    with open(trans_tex_path, encoding='utf-8') as f:
        text = f.read()

    doc_pos = text.find(r'\begin{document}')
    if doc_pos < 0:
        return 0

    head, body = text[:doc_pos], text[doc_pos:]
    lines = body.splitlines(keepends=True)
    total = 0
    for i, line in enumerate(lines):
        if line.strip() == r'\endinput':
            newline = '\n' if line.endswith('\n') else ''
            lines[i] = '% \\endinput removed by paper-trans repair' + newline
            total += 1

    if total:
        with open(trans_tex_path, 'w', encoding='utf-8') as f:
            f.write(head + ''.join(lines))
        print(f"[driver] 🔧 patch_body_endinput: 注释了 {total} 处正文 \\endinput", flush=True)
    return total


def patch_tcolorbox_small_groups(trans_tex_path):
    """
    GPT 有时把 \\begin{trajcase} 后的 {\\small ... } 保留下来。
    这种跨 breakable tcolorbox 的显式分组容易触发 tcb@savebox 分组错误。
    """
    import re as _re
    with open(trans_tex_path, encoding='utf-8') as f:
        text = f.read()

    total = 0

    def _replace_begin(m):
        nonlocal total
        total += 1
        return m.group(1) + r'\small' + '\n'

    new_text = _re.sub(
        r'(\\begin\{trajcase\}(?:\[[^\n]*\])?(?:\{[^\n]*\})?\s*)\{\\small\s*',
        _replace_begin,
        text,
    )
    if total:
        new_text = _re.sub(
            r'(?m)^\s*\}\s*\n(\s*\\end\{trajcase\})',
            r'\1',
            new_text,
        )
        with open(trans_tex_path, 'w', encoding='utf-8') as f:
            f.write(new_text)
        print(f"[driver] 🔧 patch_tcolorbox_small_groups: 修复了 {total} 个 trajcase 字号分组", flush=True)
    return total


def patch_tcolorbox_opening_options(trans_tex_path, orig_tex_path):
    """Restore tcolorbox option keys/units that must never be translated."""
    with open(trans_tex_path, encoding='utf-8') as f:
        translated = f.read()
    with open(orig_tex_path, encoding='utf-8') as f:
        original = f.read()
    fixed, total = _ltf.restore_environment_opening_options(
        translated, original, 'tcolorbox'
    )
    if total:
        with open(trans_tex_path, 'w', encoding='utf-8') as f:
            f.write(fixed)
        print(f"[driver] 🔧 patch_tcolorbox_opening_options: 恢复 {total} 组原始选项", flush=True)
    return total


def patch_unbalanced_groups_in_tcolorboxes(trans_tex_path):
    """在自定义 tcolorbox 块内补齐明显漏掉的 \\endgroup。"""
    with open(trans_tex_path, encoding='utf-8') as f:
        text = f.read()

    envs = _discover_tcolorbox_envs(text)
    result = text
    total = 0
    for env in envs:
        blocks = _extract_env_blocks(result, env)
        for start, end, block in reversed(blocks):
            missing = block.count(r'\begingroup') - block.count(r'\endgroup')
            if missing <= 0:
                continue
            end_tag = r'\end{' + env + '}'
            pos = block.rfind(end_tag)
            if pos < 0:
                continue
            fixed = block[:pos] + ('\\endgroup\n' * missing) + block[pos:]
            result = result[:start] + fixed + result[end:]
            total += missing

    if total:
        with open(trans_tex_path, 'w', encoding='utf-8') as f:
            f.write(result)
        print(f"[driver] 🔧 patch_unbalanced_groups_in_tcolorboxes: 补齐了 {total} 个 endgroup", flush=True)
    return total


def patch_custom_macro_cjk_glue(trans_tex_path):
    """
    GPT may translate text around no-argument custom macros into forms like
    ``\\name的``. Under XeLaTeX/CJK this can be parsed as one longer undefined
    control sequence instead of macro ``\\name`` followed by Chinese text. Add
    an empty group delimiter after simple custom macros when they are glued to a
    CJK character or ASCII letter.
    """
    with open(trans_tex_path, encoding='utf-8') as f:
        text = f.read()

    sibling_definitions = ''
    workfolder = os.path.dirname(trans_tex_path)
    for pattern in ('**/*.sty', '**/*.cls'):
        for path in glob.glob(os.path.join(workfolder, pattern), recursive=True):
            try:
                with open(path, encoding='utf-8', errors='replace') as f:
                    sibling_definitions += f.read() + '\n'
            except Exception:
                pass

    new_text, total = _ltf.repair_duplicated_macro_initials(text)
    new_text, builtin_ascii = _ltf.separate_builtin_layout_ascii_glue(
        new_text,
        sibling_definitions,
    )
    total += builtin_ascii
    new_text, separated = _ltf.separate_custom_macro_cjk_glue(new_text)
    total += separated
    new_text, spaced = _ltf.collapse_spaced_cjk_characters(new_text)
    total += spaced
    if total:
        with open(trans_tex_path, 'w', encoding='utf-8') as f:
            f.write(new_text)
        print(
            f"[driver] 🔧 patch_custom_macro_cjk_glue: "
            f"修复 {total} 处排版命令/正文、宏/CJK 粘连或中文空格",
            flush=True,
        )
    return total


def patch_missing_custom_macro_definitions(trans_tex_path, orig_tex_path):
    """Restore user-source macros dropped from translated local preambles.

    Package/class implementation files are intentionally excluded.  Their
    commands can be defined lazily inside an environment (``algorithmic`` is
    one example); copying those definitions into the document preamble makes
    the package's later ``\\newcommand`` declarations collide.
    """
    with open(trans_tex_path, encoding="utf-8") as handle:
        text = handle.read()

    sources = []
    for path in (orig_tex_path,):
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                sources.append(handle.read())
        except OSError:
            pass

    workfolder = os.path.dirname(trans_tex_path)
    for pattern in ("**/*.tex",):
        for path in glob.glob(os.path.join(workfolder, pattern), recursive=True):
            if os.path.abspath(path) == os.path.abspath(trans_tex_path):
                continue
            try:
                with open(path, encoding="utf-8", errors="replace") as handle:
                    sources.append(handle.read())
            except OSError:
                pass

    fixed, total = _ltf.restore_missing_custom_macro_definitions(
        text,
        "\n".join(sources),
    )
    if total:
        with open(trans_tex_path, "w", encoding="utf-8") as handle:
            handle.write(fixed)
        print(
            f"[driver] 🔧 patch_missing_custom_macro_definitions: 恢复 {total} 个源文件宏",
            flush=True,
        )
    return total


def patch_stray_text_word_commands(trans_tex_path):
    """
    Repair translation artifacts like ``\\textTest:``. These usually come from
    plain prompt text where GPT glued ``\\text`` to an English word, producing an
    undefined LaTeX command.
    """
    import re as _re

    with open(trans_tex_path, encoding='utf-8') as f:
        text = f.read()

    defined = {
        m.group(1)
        for m in _re.finditer(
            r'\\(?:newcommand|renewcommand|providecommand)\s*\{\\([A-Za-z@]+)\}',
            text,
        )
    }
    pattern = _re.compile(r'\\text([A-Z][A-Za-z]{1,40})(?=[:：,，.;；!?！？\s])')
    total = 0

    def _replace(m):
        nonlocal total
        full_name = 'text' + m.group(1)
        if full_name in defined:
            return m.group(0)
        total += 1
        return r'\textbf{' + m.group(1) + '}'

    new_text = pattern.sub(_replace, text)
    if total:
        with open(trans_tex_path, 'w', encoding='utf-8') as f:
            f.write(new_text)
        print(f"[driver] 🔧 patch_stray_text_word_commands: 修复了 {total} 个误生成的 text 命令", flush=True)
    return total


def patch_algorithmic_command_glue(trans_tex_path):
    """Repair algorithmic commands glued to translated CJK text."""
    import re as _re

    with open(trans_tex_path, encoding='utf-8') as f:
        text = f.read()

    total = 0

    def _comment_replace(m):
        nonlocal total
        total += 1
        return m.group('indent') + r'\Comment{' + m.group('body').strip() + '}'

    new_text = _re.sub(
        r'(?m)^(?P<indent>\s*)\\Comment(?P<body>[\u4e00-\u9fff][^{}\n]*)$',
        _comment_replace,
        text,
    )

    def _space_replace(m):
        nonlocal total
        total += 1
        return '\\' + m.group(1) + ' '

    new_text = _re.sub(
        r'\\(State|Require|Ensure|Return)(?=[\u4e00-\u9fff])',
        _space_replace,
        new_text,
    )

    if total:
        with open(trans_tex_path, 'w', encoding='utf-8') as f:
            f.write(new_text)
        print(f"[driver] 🔧 patch_algorithmic_command_glue: 修复了 {total} 处 algorithmic 命令粘连", flush=True)
    return total


def patch_algorithm2e_keyword_aliases(trans_tex_path):
    """Restore algorithm2e keyword aliases if translation renamed definitions."""
    with open(trans_tex_path, encoding='utf-8') as f:
        text = f.read()

    needed = []
    for name in ('Input', 'Output', 'KwIn', 'KwOut'):
        if '\\' + name + '{' in text and ('\\SetKwInOut{' + name + '}') not in text:
            needed.append(name)
    if not needed or r'\SetKwInOut' not in text:
        return 0

    alias_lines = ['% paper-trans: restore algorithm2e keyword aliases']
    for name in needed:
        label = 'Input' if name in ('Input', 'KwIn') else 'Output'
        alias_lines.append(r'\SetKwInOut{' + name + '}{' + label + '}')
    insertion = '\n'.join(alias_lines)

    pos = text.find(r'\begin{algorithm')
    if pos < 0:
        pos = text.find(r'\begin{document}')
    if pos < 0:
        return 0

    new_text = text[:pos] + insertion + '\n' + text[pos:]
    with open(trans_tex_path, 'w', encoding='utf-8') as f:
        f.write(new_text)
    print(f"[driver] 🔧 patch_algorithm2e_keyword_aliases: 恢复 {len(needed)} 个 algorithm2e 关键字别名", flush=True)
    return len(needed)


def patch_llm_translation_artifacts(trans_tex_path):
    """Remove common LLM refusal/request artifacts inserted into translated TeX."""
    with open(trans_tex_path, encoding='utf-8') as f:
        text = f.read()

    new_text, total = _ltf.strip_llm_translation_artifacts(text)

    if total:
        with open(trans_tex_path, 'w', encoding='utf-8') as f:
            f.write(new_text)
        print(f"[driver] 🔧 patch_llm_translation_artifacts: 清理了 {total} 处非原文翻译残留", flush=True)
    return total


def patch_structural_commands_in_captions(trans_tex_path):
    """Demote ``\\section``-class commands mistakenly inserted into figure/table captions."""
    with open(trans_tex_path, encoding='utf-8') as f:
        text = f.read()

    new_text, total = _ltf.demote_structural_commands_in_captions(text)

    if total:
        with open(trans_tex_path, 'w', encoding='utf-8') as f:
            f.write(new_text)
        print(
            f"[driver] 🔧 patch_structural_commands_in_captions: "
            f"修正 {total} 处 caption 内结构命令",
            flush=True,
        )
    return total


def patch_stray_closing_brace_after_cjk_sentence(trans_tex_path):
    """Remove obvious extra ``}`` after translated CJK prose sentences."""
    import re as _re

    with open(trans_tex_path, encoding='utf-8') as f:
        lines = f.readlines()

    total = 0
    anchor = r'(?:图像|图|表格|表|公式|式|第|附录)'
    pattern = _re.compile(r'([。！？；，、])\}(?=' + anchor + r'(?:\s*~?\\(?:ref|eqref|autoref|cref|Cref)\{|\b|[\u4e00-\u9fff]))')

    def _brace_balance(prefix):
        balance = 0
        escaped = False
        for ch in prefix:
            if escaped:
                escaped = False
                continue
            if ch == '\\':
                escaped = True
                continue
            if ch == '{':
                balance += 1
            elif ch == '}':
                balance -= 1
        return balance

    new_lines = []
    for line in lines:
        pieces = []
        last = 0
        for m in pattern.finditer(line):
            pieces.append(line[last:m.start()])
            if _brace_balance(line[:m.start()]) <= 0:
                pieces.append(m.group(1))
                total += 1
            else:
                pieces.append(m.group(0))
            last = m.end()
        pieces.append(line[last:])
        new_lines.append(''.join(pieces))

    if total:
        with open(trans_tex_path, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)
        print(f"[driver] 🔧 patch_stray_closing_brace_after_cjk_sentence: 移除了 {total} 个多余右花括号", flush=True)
    return total


def patch_unclosed_textbf_reference_heads(trans_tex_path):
    """Close section lead-in bold text if a previous repair removed the brace."""
    import re as _re

    with open(trans_tex_path, encoding='utf-8') as f:
        text = f.read()

    total = 0
    pattern = _re.compile(
        r'(\\textbf\{[^{}\n]{1,100}?[。！？；：:])'
        r'(?=((?:如)?(?:图像|图|表格|表|公式|式|第|附录)\s*~?\\(?:ref|eqref|autoref|cref|Cref)\{))'
    )

    def _replace(m):
        nonlocal total
        total += 1
        return m.group(1) + '}'

    new_text = pattern.sub(_replace, text)
    if total:
        with open(trans_tex_path, 'w', encoding='utf-8') as f:
            f.write(new_text)
        print(f"[driver] 🔧 patch_unclosed_textbf_reference_heads: 补齐了 {total} 个 textbf 右花括号", flush=True)
    return total


def patch_inline_math_delimiter_artifacts(trans_tex_path):
    """Repair common LLM-produced orphan ``\\)`` inline-math delimiters."""
    import re as _re

    with open(trans_tex_path, encoding='utf-8') as f:
        lines = f.readlines()

    total = 0
    new_lines = []
    math_cmd_re = _re.compile(r'\\(?:mathcal|mathbf|mathbb|mathrm|mathsf|mathscr|operatorname|Pi|pi|Delta|Theta|Omega)\b')
    for line in lines:
        new_line = line
        if r'\)' in line and r'\(' not in line:
            candidate = line.replace(r'\)', '$')
            if candidate.count('$') >= 2 and math_cmd_re.search(candidate):
                new_line = candidate
        if new_line != line:
            total += 1
        new_lines.append(new_line)

    if total:
        with open(trans_tex_path, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)
        print(f"[driver] 🔧 patch_inline_math_delimiter_artifacts: 修复了 {total} 行 orphan inline math delimiter", flush=True)
    return total


def patch_common_command_cjk_glue(trans_tex_path):
    """Add a separating space when safe LaTeX commands are glued to CJK text."""
    import re as _re

    with open(trans_tex_path, encoding='utf-8') as f:
        text = f.read()

    safe_commands = (
        'newline', 'newpage', 'clearpage', 'noindent', 'indent',
        'smallskip', 'medskip', 'bigskip',
    )
    pattern = _re.compile(r'\\(' + '|'.join(safe_commands) + r')(?=[\u4e00-\u9fff])')
    new_text, total = pattern.subn(lambda m: '\\' + m.group(1) + ' ', text)
    if total:
        with open(trans_tex_path, 'w', encoding='utf-8') as f:
            f.write(new_text)
        print(f"[driver] 🔧 patch_common_command_cjk_glue: 修复了 {total} 处命令/CJK 粘连", flush=True)
    return total


def patch_bare_citation_commands(trans_tex_path):
    """Turn argument-less citations glued to CJK prose into readable text."""
    with open(trans_tex_path, encoding='utf-8') as f:
        text = f.read()
    new_text, total = _ltf.replace_bare_citation_commands(text)
    if total:
        with open(trans_tex_path, 'w', encoding='utf-8') as f:
            f.write(new_text)
        print(f"[driver] 🔧 patch_bare_citation_commands: 修复 {total} 处缺失参数的 cite", flush=True)
    return total


def patch_declaration_command_cjk_glue(trans_tex_path):
    """Separate legacy font declaration commands from CJK text."""
    with open(trans_tex_path, encoding='utf-8') as f:
        text = f.read()
    new_text, total = _ltf.separate_declaration_command_cjk_glue(text)
    if total:
        with open(trans_tex_path, 'w', encoding='utf-8') as f:
            f.write(new_text)
        print(f"[driver] 🔧 patch_declaration_command_cjk_glue: 修复 {total} 处字体命令/CJK 粘连", flush=True)
    return total


def patch_spurious_cjk_command_escapes(trans_tex_path):
    with open(trans_tex_path, encoding='utf-8') as f:
        text = f.read()
    new_text, total = _ltf.remove_spurious_cjk_command_escapes(text)
    if total:
        with open(trans_tex_path, 'w', encoding='utf-8') as f:
            f.write(new_text)
        print(f"[driver] 🔧 patch_spurious_cjk_command_escapes: 移除 {total} 处中文前误加反斜杠", flush=True)
    return total


def patch_missing_graphics(trans_tex_path):
    """Replace genuinely missing image inclusions with a compilable marker."""
    import base64 as _base64
    import re as _re
    workfolder = os.path.realpath(os.path.dirname(trans_tex_path))
    with open(trans_tex_path, encoding='utf-8') as f:
        text = f.read()
    pattern = _re.compile(r"\\includegraphics\*?(?P<opts>\s*\[[^\]]*\])?\s*\{(?P<path>[^{}]+)\}")
    total = 0

    def replace(match):
        nonlocal total
        rel = match.group('path').strip()
        candidates = [os.path.join(workfolder, rel)]
        if not os.path.splitext(rel)[1]:
            candidates.extend(os.path.join(workfolder, rel + ext) for ext in ('.pdf', '.png', '.jpg', '.jpeg', '.eps'))
        if any(os.path.exists(path) for path in candidates):
            return match.group(0)
        total += 1
        return r"\fbox{\texttt{missing image}}"

    new_text = pattern.sub(replace, text)
    # Class/style files sometimes hide logo paths behind macros, so there is no
    # includegraphics command in the merged TeX to replace.
    asset_re = _re.compile(r"(?<![A-Za-z0-9_./-])([A-Za-z0-9_./-]+\.png)")
    transparent_png = _base64.b64decode(
        b'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='
    )
    for support in glob.glob(os.path.join(workfolder, '*.cls')) + glob.glob(os.path.join(workfolder, '*.sty')):
        with open(support, encoding='utf-8', errors='replace') as f:
            support_text = f.read()
        for rel in asset_re.findall(support_text):
            if rel.startswith('/') or '//' in rel:
                continue
            target = os.path.realpath(os.path.join(workfolder, rel))
            if not target.startswith(workfolder + os.sep) or os.path.exists(target):
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, 'wb') as f:
                f.write(transparent_png)
            total += 1
    if total:
        with open(trans_tex_path, 'w', encoding='utf-8') as f:
            f.write(new_text)
        print(f"[driver] 🔧 patch_missing_graphics: 替换 {total} 个缺失图片引用", flush=True)
    return total


def patch_fragile_cleveref_references(trans_tex_path):
    """Demote fragile cleveref calls to core references after a failed compile."""
    with open(trans_tex_path, encoding='utf-8') as f:
        text = f.read()
    new_text, total = _ltf.demote_cleveref_commands(text)
    new_text, split_count = _ltf.split_multilabel_references(new_text)
    total += split_count
    if total:
        with open(trans_tex_path, 'w', encoding='utf-8') as f:
            f.write(new_text)
        print(f"[driver] 🔧 patch_fragile_cleveref_references: 降级 {total} 处 cleveref 引用", flush=True)
    return total


def patch_packages_in_documentclass_options(trans_tex_path):
    """Move package imports out of a multiline documentclass option list."""
    with open(trans_tex_path, encoding='utf-8') as f:
        text = f.read()
    new_text, total = _ltf.relocate_packages_from_documentclass_options(text)
    if total:
        with open(trans_tex_path, 'w', encoding='utf-8') as f:
            f.write(new_text)
        print(f"[driver] 🔧 patch_packages_in_documentclass_options: 移出 {total} 个 package", flush=True)
    return total


def patch_pdftex_graphics_driver(trans_tex_path):
    """Let XeLaTeX select the correct graphicx backend."""
    with open(trans_tex_path, encoding='utf-8') as f:
        text = f.read()
    new_text, total = _ltf.remove_pdftex_graphics_driver(text)
    if total:
        with open(trans_tex_path, 'w', encoding='utf-8') as f:
            f.write(new_text)
        print(f"[driver] 🔧 patch_pdftex_graphics_driver: 移除 {total} 处 pdftex graphicx driver", flush=True)
    return total


def patch_duplicate_end_environments(trans_tex_path):
    """Remove accidental duplicated environment endings produced by translation."""
    import re as _re

    with open(trans_tex_path, encoding='utf-8') as f:
        text = f.read()

    total = 0

    def _replace(m):
        nonlocal total
        total += 1
        return r'\end{' + m.group(1) + '}'

    new_text = _re.sub(r'\\end\{(proof|lemma|theorem|proposition|corollary)\}\s*\\end\{\1\}', _replace, text)
    new_text, unmatched = _ltf.remove_unmatched_environment_endings(
        new_text,
        ("tcolorbox",),
    )
    total += unmatched
    if total:
        with open(trans_tex_path, 'w', encoding='utf-8') as f:
            f.write(new_text)
        print(f"[driver] 🔧 patch_duplicate_end_environments: 移除了 {total} 个重复 end 环境", flush=True)
    return total


def patch_tikz_matrix_node_linebreaks(trans_tex_path):
    """Avoid TikZ matrix row-parser confusion from inline node line breaks."""
    with open(trans_tex_path, encoding='utf-8') as f:
        text = f.read()
    new_text, total = _ltf.normalize_tikz_matrix_node_linebreaks(text)
    if not total:
        return 0
    with open(trans_tex_path, 'w', encoding='utf-8') as f:
        f.write(new_text)
    print(f"[driver] 🔧 patch_tikz_matrix_node_linebreaks: 修复 {total} 处节点换行", flush=True)
    return total


def patch_fragile_tikz_matrix_legends(trans_tex_path):
    """Omit explicit-command legends that break TikZ matrix parsing."""
    with open(trans_tex_path, encoding='utf-8') as f:
        text = f.read()
    new_text, total = _ltf.disable_fragile_tikz_matrix_legends(text)
    if not total:
        return 0
    with open(trans_tex_path, 'w', encoding='utf-8') as f:
        f.write(new_text)
    print(f"[driver] 🔧 patch_fragile_tikz_matrix_legends: 省略 {total} 个不兼容图例", flush=True)
    return total


def patch_undefined_unique_ref_labels(trans_tex_path):
    """
    If a source has a ref to ``foo`` but only defines one longer label such as
    ``foo_bar``, rewrite that ref. This fixes upstream label/ref drift without
    guessing when multiple candidates exist.
    """
    import re as _re

    with open(trans_tex_path, encoding='utf-8') as f:
        text = f.read()

    labels = set(_re.findall(r'\\label\{([^{}]+)\}', text))
    if not labels:
        return 0

    ref_pattern = _re.compile(r'\\(ref|eqref|autoref|cref|Cref)\{([^{}]+)\}')
    original_refs = set()
    original_path = os.path.join(os.path.dirname(trans_tex_path), "merge.tex")
    try:
        with open(original_path, encoding="utf-8", errors="replace") as f:
            original_refs = {
                match.group(2)
                for match in ref_pattern.finditer(f.read())
            }
    except OSError:
        pass
    replacements: dict[str, str] = {}
    for label in sorted({m.group(2) for m in ref_pattern.finditer(text)} - labels):
        replacement = _ltf.unique_label_replacement(
            label,
            labels,
            original_refs=original_refs,
        )
        if replacement:
            replacements[label] = replacement

    if not replacements:
        return 0

    total = 0

    def _replace(m):
        nonlocal total
        label = m.group(2)
        if label not in replacements:
            return m.group(0)
        total += 1
        return '\\' + m.group(1) + '{' + replacements[label] + '}'

    new_text = ref_pattern.sub(_replace, text)
    if total:
        with open(trans_tex_path, 'w', encoding='utf-8') as f:
            f.write(new_text)
        detail = ', '.join(f'{k}->{v}' for k, v in replacements.items())
        print(f"[driver] 🔧 patch_undefined_unique_ref_labels: 修复了 {total} 个 ref ({detail})", flush=True)
    return total


def patch_dangling_href_commands(trans_tex_path, orig_tex_path=None):
    """Restore ``\\href`` blocks broken by GPT line wrapping or truncation."""
    import re as _re

    with open(trans_tex_path, encoding='utf-8') as f:
        text = f.read()

    orig_hrefs: list[tuple[str, str]] = []
    if orig_tex_path and os.path.exists(orig_tex_path):
        with open(orig_tex_path, encoding='utf-8', errors='replace') as f:
            orig_hrefs = _re.findall(r'\\href\{([^{}]+)\}\{([^{}]*)\}', f.read())

    total = 0
    dangling_re = _re.compile(r'\\href\{([^}\n]+)\n')

    def _restore(m):
        nonlocal total
        partial = m.group(1).strip()
        for url, display in orig_hrefs:
            if url.startswith(partial) or partial.startswith(url[: max(8, len(partial))]):
                total += 1
                return '\\href{' + url + '}{' + display + '}\n'
        total += 1
        return ''

    new_text = dangling_re.sub(_restore, text)
    if total:
        with open(trans_tex_path, 'w', encoding='utf-8') as f:
            f.write(new_text)
        print(f"[driver] 🔧 patch_dangling_href_commands: 修复了 {total} 处截断 href", flush=True)
    return total


def _insert_before_begin_document(text: str, insertion: str) -> tuple[str, bool]:
    marker = r'\begin{document}'
    pos = _ltf.find_uncommented_latex_token(text, marker)
    if pos < 0:
        return text, False
    return text[:pos] + insertion + '\n' + text[pos:], True


def patch_fontawesome_legacy_aliases(trans_tex_path):
    """Provide common fontawesome5 aliases used by older templates."""
    with open(trans_tex_path, encoding='utf-8') as f:
        text = f.read()

    workfolder = os.path.dirname(trans_tex_path)
    sibling_text = ''
    # FontAwesome commands are often defined in a local ``preamble.tex``
    # reached through ``\\input`` rather than in the class/style itself. The
    # merged TeX only contains the input call, so inspect sibling TeX sources
    # as well when deciding which legacy aliases need a fallback.
    sibling_paths = set()
    for ext in ('*.sty', '*.cls', '*.tex'):
        sibling_paths.update(glob.glob(os.path.join(workfolder, ext)))
    # Some arXiv sources use extensionless ``\input{preamble}`` files.  Add
    # only explicit local input/include targets; do not scan arbitrary paths.
    for match in re.finditer(r"\\(?:input|include)\s*\{([^{}]+)\}", text):
        target = match.group(1).strip()
        if not target or target.startswith(("/", "\\")) or "\\" in target:
            continue
        for candidate in (target, target + ".tex"):
            sibling_paths.add(os.path.join(workfolder, candidate))
    for path in sorted(sibling_paths):
        if os.path.realpath(path) == os.path.realpath(trans_tex_path):
            continue
        try:
            with open(path, encoding='utf-8', errors='replace') as f:
                sibling_text += f.read() + '\n'
        except Exception:
            pass

    new_text, total = _ltf.add_fontawesome_legacy_aliases(text, sibling_text)
    if new_text == text:
        return 0
    with open(trans_tex_path, 'w', encoding='utf-8') as f:
        f.write(new_text)
    names = ','.join(
        '\\' + name for name in _ltf.fontawesome_command_names(new_text)
    )
    print(f"[driver] 🔧 patch_fontawesome_legacy_aliases: 补充/迁移 {names} fallback", flush=True)
    return total


def patch_declare_unicode_character_fallback(trans_tex_path):
    """Provide a no-op fallback for templates using inputenc-only Unicode declarations."""
    with open(trans_tex_path, encoding='utf-8') as f:
        text = f.read()

    marker = r'\DeclareUnicodeCharacter'
    if marker not in text:
        return 0
    fallback = (
        r'% paper-trans fallback for XeLaTeX without inputenc DeclareUnicodeCharacter'
        '\n'
        r'\providecommand{\DeclareUnicodeCharacter}[2]{}'
    )
    if fallback in text or r'\providecommand{\DeclareUnicodeCharacter}' in text:
        return 0

    new_text, ok = _ltf.insert_latex_preamble_snippet(
        text,
        fallback,
        command_markers=('DeclareUnicodeCharacter',),
    )
    if not ok:
        return 0
    with open(trans_tex_path, 'w', encoding='utf-8') as f:
        f.write(new_text)
    print("[driver] 🔧 patch_declare_unicode_character_fallback: 补充 \\DeclareUnicodeCharacter fallback", flush=True)
    return 1


def patch_xelatex_compatibility_fallbacks(trans_tex_path):
    with open(trans_tex_path, encoding='utf-8') as f:
        text = f.read()
    fixed, count = _ltf.add_xelatex_compatibility_fallbacks(text)
    fixed, acm_count = _ltf.reset_acm_baselinestretch_before_end_document(fixed)
    total = count + acm_count
    if not total:
        return 0
    with open(trans_tex_path, 'w', encoding='utf-8') as f:
        f.write(fixed)
    if count:
        print("[driver] 🔧 patch_xelatex_compatibility_fallbacks: 补充 XeLaTeX 兼容命令 fallback", flush=True)
    if acm_count:
        print("[driver] 🔧 patch_xelatex_compatibility_fallbacks: 重置 ACM/CIDR baselinestretch guard", flush=True)
    return total


def patch_missing_math_aliases(trans_tex_path):
    """Repair conservative identity-matrix aliases introduced by translation."""
    with open(trans_tex_path, encoding='utf-8') as f:
        text = f.read()
    fixed, total = _ltf.repair_missing_math_aliases(text)
    if not total:
        return 0
    with open(trans_tex_path, 'w', encoding='utf-8') as f:
        f.write(fixed)
    print(f"[driver] 🔧 patch_missing_math_aliases: 修复 {total} 处 \\I -> \\Imat", flush=True)
    return total


def patch_pdftex_primitives_for_xelatex(trans_tex_path):
    """Guard pdfTeX primitive lines in the translated main tex."""
    with open(trans_tex_path, encoding='utf-8') as f:
        text = f.read()

    new_text, total = _ltf.guard_pdftex_primitive_lines(text)
    if not total:
        return 0

    with open(trans_tex_path, 'w', encoding='utf-8') as f:
        f.write(new_text)
    print(f"[driver] 🔧 patch_pdftex_primitives_for_xelatex: guard {total} 处 pdfTeX primitive", flush=True)
    return total


def patch_local_pdftex_primitives(workfolder):
    """Guard pdfTeX primitive lines in local class/style/source files."""
    total = 0
    targets = []
    for pattern in ('**/*.cls', '**/*.sty', '**/*.tex'):
        targets.extend(glob.glob(os.path.join(workfolder, pattern), recursive=True))
    for path in sorted(set(targets)):
        if os.path.basename(path) == 'merge_translate_zh.tex':
            continue
        try:
            with open(path, encoding='utf-8', errors='replace') as f:
                text = f.read()
        except Exception:
            continue
        if not any('\\' + name in text for name in _ltf.PDFTEX_PRIMITIVE_NAMES):
            continue
        new_text, count = _ltf.guard_pdftex_primitive_lines(text)
        if not count:
            continue
        with open(path, 'w', encoding='utf-8') as f:
            f.write(new_text)
        total += count
    if total:
        print(f"[driver] 🔧 patch_local_pdftex_primitives: guard {total} 处本地 pdfTeX primitive", flush=True)
    return total


def patch_local_xelatex_compatibility_fallbacks(workfolder):
    """Apply early font-command compatibility to local class/style files."""
    total = 0
    targets = (
        glob.glob(os.path.join(workfolder, '**/*.cls'), recursive=True)
        + glob.glob(os.path.join(workfolder, '**/*.sty'), recursive=True)
    )
    for path in sorted(set(targets)):
        try:
            with open(path, encoding='utf-8', errors='replace') as f:
                text = f.read()
        except Exception:
            continue
        fixed, count = _ltf.add_xelatex_compatibility_fallbacks(text)
        if not count:
            continue
        with open(path, 'w', encoding='utf-8') as f:
            f.write(fixed)
        total += count
    if total:
        print(f"[driver] 🔧 patch_local_xelatex_compatibility_fallbacks: 补充 {total} 处本地兼容命令", flush=True)
    return total


def patch_local_textls_fallback(workfolder, trans_tex_path):
    """Expose a main-document textls fallback for local report styles."""
    uses_textls = False
    for path in glob.glob(os.path.join(workfolder, '**/*.sty'), recursive=True):
        try:
            with open(path, encoding='utf-8', errors='replace') as f:
                if r'\textls' in f.read():
                    uses_textls = True
                    break
        except Exception:
            continue
    if not uses_textls:
        return 0

    with open(trans_tex_path, encoding='utf-8') as f:
        text = f.read()
    if r'\providecommand{\textls}' in text:
        return 0
    insertion = (
        r'% paper-trans fallback for unavailable microtype tracking'
        '\n'
        r'\providecommand{\textls}[2][]{#2}'
    )
    new_text, ok = _insert_before_begin_document(text, insertion)
    if not ok:
        return 0
    with open(trans_tex_path, 'w', encoding='utf-8') as f:
        f.write(new_text)
    print("[driver] 🔧 patch_local_textls_fallback: 补充本地样式 textls fallback", flush=True)
    return 1


def patch_local_sourcesans3_family(workfolder):
    """Map SourceSans3 to the compatible SourceSansPro fonts in TeX Live."""
    total = 0
    for path in glob.glob(os.path.join(workfolder, '**/*.sty'), recursive=True):
        try:
            with open(path, encoding='utf-8', errors='replace') as f:
                text = f.read()
        except Exception:
            continue
        fixed, count = _ltf.fallback_sourcesans3_family(text)
        if not count:
            continue
        with open(path, 'w', encoding='utf-8') as f:
            f.write(fixed)
        total += count
    if total:
        print(f"[driver] 🔧 patch_local_sourcesans3_family: 回退 {total} 处 SourceSans3 字体族", flush=True)
    return total


def clean_latex_intermediates(workfolder):
    """Remove stale LaTeX/BibTeX intermediates before deterministic recompiles."""
    removed = 0
    for ext in (
        'aux', 'bbl', 'blg', 'log', 'out', 'toc', 'ptc', 'fls', 'fdb_latexmk',
        'lof', 'lot', 'lol', 'nav', 'snm', 'vrb', 'xdv', 'synctex.gz', 'pdf',
    ):
        path = os.path.join(workfolder, f'merge_translate_zh.{ext}')
        if os.path.exists(path):
            try:
                os.remove(path)
                removed += 1
            except Exception:
                pass
    if removed:
        print(f"[driver] 🧹 clean_latex_intermediates: 清理了 {removed} 个旧中间文件", flush=True)
    return removed


def sanitize_latex_aux_file(workfolder):
    """Drop fragile aux rows while keeping citation and compact label data."""
    import re as _re

    aux_path = os.path.join(workfolder, 'merge_translate_zh.aux')
    if not os.path.exists(aux_path):
        return 0
    try:
        with open(aux_path, encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
    except Exception:
        return 0
    kept = []
    removed = 0
    compacted = 0
    label_re = _re.compile(r'^\\newlabel\{([^{}]+)\}\{\{([^{}]*)\}\{([^{}]*)\}')
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith(r'\@writefile'):
            removed += 1
            continue
        if stripped.startswith(r'\newlabel'):
            m = label_re.match(stripped)
            if m:
                kept.append(r'\newlabel{' + m.group(1) + '}{{' + m.group(2) + '}{' + m.group(3) + '}}' + '\n')
                if kept[-1] != line:
                    compacted += 1
                continue
            removed += 1
            continue
        kept.append(line)

    if removed or compacted:
        with open(aux_path, 'w', encoding='utf-8') as f:
            f.writelines(kept)
        detail = []
        if removed:
            detail.append(f"移除 {removed} 行")
        if compacted:
            detail.append(f"压缩 {compacted} 个 newlabel")
        print(f"[driver] 🧹 sanitize_latex_aux_file: {', '.join(detail)}", flush=True)
    return removed + compacted


def synthesize_bbl_from_tex(workfolder, trans_tex_path):
    """Create a minimal aux from citation commands and run BibTeX before XeLaTeX."""
    import re as _re
    import subprocess as _sp

    with open(trans_tex_path, encoding='utf-8', errors='replace') as f:
        text = f.read()

    bbl_path = os.path.join(workfolder, 'merge_translate_zh.bbl')

    def _copy_existing_bbl(reason: str) -> bool:
        candidates: list[str] = []
        for m in _re.finditer(r'\\(?:input|include)\{([^{}]+\.bbl)\}', text):
            p = os.path.join(workfolder, m.group(1))
            if os.path.exists(p):
                candidates.append(p)
        for p in glob.glob(os.path.join(workfolder, '*.bbl')):
            if os.path.basename(p) != 'merge_translate_zh.bbl':
                candidates.append(p)
        candidates = sorted(set(candidates), key=lambda p: os.path.getsize(p), reverse=True)
        for src in candidates:
            try:
                content = open(src, encoding='utf-8', errors='replace').read()
            except Exception:
                continue
            if r'\bibitem' not in content:
                continue
            shutil.copy2(src, bbl_path)
            print(
                f"[driver] 🔧 synthesize_bbl_from_tex: 复用现有 bbl "
                f"({os.path.basename(src)}, {reason})",
                flush=True,
            )
            return True
        return False

    bibdata = _re.findall(r'\\bibliography\{([^{}]+)\}', text)
    bibstyle = _re.findall(r'\\bibliographystyle\{([^{}]+)\}', text)
    if not bibdata:
        orig_tex_path = os.path.join(workfolder, 'merge.tex')
        if os.path.exists(orig_tex_path):
            try:
                with open(orig_tex_path, encoding='utf-8', errors='replace') as f:
                    orig_text = f.read()
                bibdata = _re.findall(r'\\bibliography\{([^{}]+)\}', orig_text)
                if not bibstyle:
                    bibstyle = _re.findall(r'\\bibliographystyle\{([^{}]+)\}', orig_text)
            except Exception:
                pass
    if not bibdata:
        return _copy_existing_bbl('no bibliography command')
    style = bibstyle[-1] if bibstyle else 'plainnat'
    data = bibdata[-1]

    cite_re = _re.compile(
        r'\\(?:citep|citet|citealt|citeauthor|citeyearpar|citealp|citeyear|nocite|cite)'
        r'(?:\[[^\]]*\]){0,2}\{([^{}]+)\}'
    )
    keys: list[str] = []
    seen = set()
    for m in cite_re.finditer(text):
        for key in m.group(1).split(','):
            key = key.strip()
            if key and key not in seen:
                keys.append(key)
                seen.add(key)
    if not keys:
        return False

    aux_path = os.path.join(workfolder, 'merge_translate_zh.aux')
    with open(aux_path, 'w', encoding='utf-8') as f:
        f.write('\\relax\n')
        for key in keys:
            f.write('\\citation{' + key + '}\n')
        f.write('\\bibstyle{' + style + '}\n')
        f.write('\\bibdata{' + data + '}\n')

    r = _sp.run(
        ['bibtex', 'merge_translate_zh'],
        cwd=workfolder, timeout=120,
        stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
        env=_restricted_tex_env(),
    )
    ok = r.returncode == 0 and os.path.exists(bbl_path) and os.path.getsize(bbl_path) > 0
    if ok:
        print(f"[driver] 🔧 synthesize_bbl_from_tex: 预生成 bbl ({len(keys)} citations)", flush=True)
    else:
        ok = _copy_existing_bbl('bibtex unavailable')
    return ok


def patch_unsafe_bibtex_keys(workfolder, trans_tex_path):
    """Normalize citation keys with characters that can destabilize BibTeX/natbib."""
    import re as _re

    def _safe_key(key):
        pieces = []
        for ch in key:
            if ch.isalnum() or ch in '_:./-':
                pieces.append(ch)
            elif ch == '+':
                pieces.append('p')
            elif ch == '#':
                pieces.append('num')
            else:
                pieces.append('_')
        safe = ''.join(pieces).strip('._-:/')
        safe = _re.sub(r'_+', '_', safe)
        return safe or 'citation_key'

    bib_paths = glob.glob(os.path.join(workfolder, '*.bib'))
    keys = set()
    entry_re = _re.compile(r'(@[A-Za-z]+\s*\{\s*)([^,\s{}]+)(\s*,)')
    for path in bib_paths:
        try:
            with open(path, encoding='utf-8', errors='replace') as f:
                content = f.read()
        except Exception:
            continue
        keys.update(entry_re.findall(content))

    mapping: dict[str, str] = {}
    used = {key for _prefix, key, _suffix in keys if _re.fullmatch(r'[A-Za-z0-9:_./-]+', key)}
    for _prefix, key, _suffix in keys:
        if _re.fullmatch(r'[A-Za-z0-9:_./-]+', key):
            continue
        safe = _safe_key(key)
        base = safe
        idx = 2
        while safe in used:
            safe = f"{base}_{idx}"
            idx += 1
        used.add(safe)
        mapping[key] = safe

    if not mapping:
        return 0

    for path in bib_paths:
        try:
            with open(path, encoding='utf-8', errors='replace') as f:
                content = f.read()
        except Exception:
            continue

        def _entry_replace(m):
            key = m.group(2)
            return m.group(1) + mapping.get(key, key) + m.group(3)

        new_content = entry_re.sub(_entry_replace, content)
        if new_content != content:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(new_content)

    with open(trans_tex_path, encoding='utf-8') as f:
        text = f.read()

    cite_re = _re.compile(
        r'\\(?P<cmd>citep|citet|citealt|citeauthor|citeyearpar|citealp|citeyear|nocite|cite)'
        r'(?P<opts>(?:\[[^\]]*\]){0,2})\{(?P<keys>[^{}]+)\}'
    )

    def _cite_replace(m):
        keys = [k.strip() for k in m.group('keys').split(',') if k.strip()]
        new_keys = [mapping.get(k, k) for k in keys]
        return '\\' + m.group('cmd') + m.group('opts') + '{' + ','.join(new_keys) + '}'

    new_text = cite_re.sub(_cite_replace, text)
    if new_text != text:
        with open(trans_tex_path, 'w', encoding='utf-8') as f:
            f.write(new_text)

    sample = ','.join(f"{old}->{new}" for old, new in list(mapping.items())[:8])
    print(f"[driver] 🔧 patch_unsafe_bibtex_keys: 规范化 {len(mapping)} 个 citation key ({sample})", flush=True)
    return len(mapping)


def patch_missing_bibitem_citations(trans_tex_path, bbl_path):
    """Degrade citations whose keys are not present in the generated .bbl."""
    import re as _re

    if not os.path.exists(bbl_path):
        return 0
    try:
        with open(bbl_path, encoding='utf-8', errors='replace') as f:
            bbl = f.read()
    except Exception:
        return 0

    bibitems = set(_re.findall(r'\\bibitem(?:\[[^\]]*\])?\{([^{}]+)\}', bbl))
    if not bibitems:
        return 0

    with open(trans_tex_path, encoding='utf-8') as f:
        text = f.read()

    cite_re = _re.compile(
        r'\\(?P<cmd>citep|citet|citealt|citeauthor|citeyearpar|citealp|citeyear|nocite|cite)'
        r'(?P<opts>(?:\[[^\]]*\]){0,2})\{(?P<keys>[^{}]+)\}'
    )
    missing_seen: list[str] = []

    def _replace(m):
        keys = [k.strip() for k in m.group('keys').split(',') if k.strip()]
        present = [k for k in keys if k in bibitems]
        missing = [k for k in keys if k not in bibitems]
        if not missing:
            return m.group(0)
        for key in missing:
            if key not in missing_seen:
                missing_seen.append(key)
        if m.group('cmd') == 'nocite':
            if present:
                return r'\nocite{' + ','.join(present) + '}'
            return ''
        marker = r'\textsuperscript{[缺失引用:' + ','.join(missing) + ']}'
        if present:
            return '\\' + m.group('cmd') + m.group('opts') + '{' + ','.join(present) + '}' + marker
        return marker

    new_text = cite_re.sub(_replace, text)
    total = len(missing_seen)
    if total:
        with open(trans_tex_path, 'w', encoding='utf-8') as f:
            f.write(new_text)
        sample = ','.join(missing_seen[:12])
        print(f"[driver] 🔧 patch_missing_bibitem_citations: 降级 {total} 个缺失 bibitem ({sample})", flush=True)
    return total


def patch_bibliography_to_generated_bbl(workfolder, trans_tex_path):
    """Input the generated bbl directly so XeLaTeX does not depend on BibTeX state."""
    import re as _re

    bbl_path = os.path.join(workfolder, 'merge_translate_zh.bbl')
    if not os.path.exists(bbl_path) or os.path.getsize(bbl_path) <= 0:
        return 0

    with open(trans_tex_path, encoding='utf-8') as f:
        text = f.read()

    if r'\input{merge_translate_zh.bbl}' in text:
        return 0
    if r'\bibliography' not in text:
        return 0

    replacement = r'\input{merge_translate_zh.bbl}'
    pattern = _re.compile(
        r'(?:^[ \t]*\\bibliographystyle\{[^{}]+\}[ \t]*(?:\r?\n|\s))*'
        r'^[ \t]*\\bibliography\{[^{}]+\}[ \t]*',
        _re.MULTILINE,
    )
    new_text, total = pattern.subn(lambda _m: replacement, text, count=1)
    if not total:
        new_text, total = _re.subn(
            r'\\bibliography\{[^{}]+\}',
            lambda _m: replacement,
            text,
            count=1,
        )

    if total:
        with open(trans_tex_path, 'w', encoding='utf-8') as f:
            f.write(new_text)
        print("[driver] 🔧 patch_bibliography_to_generated_bbl: 直接接入生成的 bbl", flush=True)
    return total


def patch_enumitem_for_optional_lists(trans_tex_path):
    """Load enumitem when translated/source text uses itemize/enumerate options."""
    import re as _re

    with open(trans_tex_path, encoding='utf-8') as f:
        text = f.read()

    if not _re.search(r'\\begin\{(?:itemize|enumerate|description)\}\[[^\]]+\]', text):
        return 0

    # Local preambles are commonly loaded with ``\input{preamble}`` and may
    # call ``\setlist`` before the merged document reaches ``\begin{document}``.
    # Loading enumitem at the latter boundary is therefore too late: TeX has
    # already parsed the local list setup and later reports a misleading
    # ``Missing number`` at the first ``\item``.  Put the package before the
    # earliest local input/include so its declarations are available at the
    # original call site.
    marker = r'% paper-trans fallback for optional list arguments'
    # A previous run may have persisted the old, too-late insertion in the
    # failed TeX cache.  Remove that tagged block before rebuilding the
    # canonical placement; otherwise the early-return guard would preserve
    # the broken ordering forever.
    text = _re.sub(
        r'(?m)^' + _re.escape(marker) + r'\r?\n'
        r'[ \t]*\\usepackage(?:\[[^\n]*\])?\{enumitem\}[ \t]*\r?\n?',
        '',
        text,
    )

    # If an untagged enumitem declaration is already present after a local
    # input, relocate that exact declaration instead of creating a duplicate.
    boundary_positions = [
        pos for token in (r'\input', r'\include', r'\begin{document}')
        if (pos := _ltf.find_uncommented_latex_token(text, token)) >= 0
    ]
    boundary = min(boundary_positions) if boundary_positions else len(text)
    package_match = _re.search(
        r'(?m)^[ \t]*\\usepackage(?:\[[^\n]*\])?\{enumitem\}[ \t]*\r?\n?',
        text,
    )
    package_line = r'\usepackage{enumitem}'
    if package_match:
        package_line = package_match.group(0).strip()
        if package_match.start() >= boundary:
            text = text[:package_match.start()] + text[package_match.end():]
        else:
            return 0

    new_text, ok = _ltf.insert_latex_preamble_snippet(
        text,
        marker + '\n' + package_line,
        command_markers=(r'\input', r'\include', r'\begin{document}'),
    )
    if not ok:
        return 0
    with open(trans_tex_path, 'w', encoding='utf-8') as f:
        f.write(new_text)
    print("[driver] 🔧 patch_enumitem_for_optional_lists: 补充 enumitem", flush=True)
    return 1


def patch_microtype_for_xelatex(trans_tex_path):
    """Disable microtype features that can break XeLaTeX with non-native fonts."""
    import re as _re

    with open(trans_tex_path, encoding='utf-8') as f:
        text = f.read()

    if 'microtype' not in text:
        return 0

    total = 0
    option_line = r'\PassOptionsToPackage{protrusion=false,expansion=false,tracking=false}{microtype}'
    if option_line not in text:
        docclass = _re.search(r'\\documentclass(?:\[[^\]]*\])?\{[^{}]+\}', text)
        if docclass:
            text = text[:docclass.start()] + option_line + '\n' + text[docclass.start():]
            total += 1

    package_re = _re.compile(r'(?m)^(\s*)\\usepackage(?:\[[^\]]*\])?\{microtype\}\s*$')
    text, removed = package_re.subn(r'\1% paper-trans: microtype disabled for XeLaTeX', text)
    total += removed

    command_re = _re.compile(r'(?m)^(\s*)\\(?:UseMicrotypeSet|microtypesetup)\b.*$')
    text, removed = command_re.subn(r'\1% paper-trans: microtype command disabled for XeLaTeX', text)
    total += removed

    if total:
        with open(trans_tex_path, 'w', encoding='utf-8') as f:
            f.write(text)
        print(f"[driver] 🔧 patch_microtype_for_xelatex: 禁用 {total} 处 microtype 高风险特性", flush=True)
    return total


def patch_local_microtype_loads(workfolder):
    """Disable local microtype loads that force pdfTeX-only font metrics.

    Templates frequently keep the package load in an input-only ``preamble.tex``
    rather than a class/style file. Inspect explicit local TeX sources while
    skipping generated merge documents.
    """
    total = 0
    targets = []
    for pattern in ('**/*.cls', '**/*.sty', '**/*.tex'):
        targets.extend(glob.glob(os.path.join(workfolder, pattern), recursive=True))
    generated = {'merge.tex', 'merge_translate_zh.tex'}
    for path in sorted(set(targets)):
        if os.path.basename(path) in generated:
            continue
        try:
            with open(path, encoding='utf-8', errors='replace') as f:
                text = f.read()
        except Exception:
            continue
        if 'microtype' not in text:
            continue
        new_text, count = _ltf.disable_microtype_package_loads(text)
        if count:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(new_text)
            total += count
    if total:
        print(f"[driver] 🔧 patch_local_microtype_loads: 禁用 {total} 处本地源 microtype 加载", flush=True)
    return total


def patch_local_nvidia_font_maps(workfolder):
    """Disable bundled NVIDIA Sans pdfmap hooks when their TFM files are absent."""
    import re as _re
    import subprocess as _sp

    try:
        tfm = _sp.run(
            ['kpsewhich', 'NVIDIASans_It.tfm'],
            capture_output=True, text=True, timeout=5,
        )
        if tfm.returncode == 0 and tfm.stdout.strip():
            return 0
    except Exception:
        pass

    total = 0
    patterns = [
        _re.compile(r'(?m)^(?P<indent>\s*)\\input\{NVIDIA-Sans-Font-TTF/t1NVIDIASans\.fd\}\s*$'),
        _re.compile(r'(?m)^(?P<indent>\s*)\\pdfmapline\{\+NVIDIASans_[^{}]+\}\s*$'),
        _re.compile(r'(?m)^(?P<indent>\s*)\\renewcommand\{\\rmdefault\}\{NVIDIASans\}\s*$'),
    ]
    for path in glob.glob(os.path.join(workfolder, '*.cls')) + glob.glob(os.path.join(workfolder, '*.sty')):
        try:
            with open(path, encoding='utf-8', errors='replace') as f:
                text = f.read()
        except Exception:
            continue
        if 'NVIDIASans' not in text:
            continue

        def _comment(m):
            return m.group('indent') + '% paper-trans: disabled unavailable NVIDIASans font map'

        new_text = text
        count = 0
        for pattern in patterns:
            new_text, n = pattern.subn(_comment, new_text)
            count += n
        if count:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(new_text)
            total += count
    if total:
        print(f"[driver] 🔧 patch_local_nvidia_font_maps: 禁用 {total} 处不可用 NVIDIASans 映射", flush=True)
    return total


def patch_textsc_for_xelatex(trans_tex_path):
    """Replace \\textsc with XeLaTeX-safe styling when T1 small caps are unavailable."""
    import re as _re

    with open(trans_tex_path, encoding='utf-8') as f:
        text = f.read()

    new_text, total = _re.subn(
        r'\\textsc\{([^{}]+)\}',
        r'\\textbf{\\small \1}',
        text,
    )
    if not total:
        return 0

    with open(trans_tex_path, 'w', encoding='utf-8') as f:
        f.write(new_text)
    print(f"[driver] 🔧 patch_textsc_for_xelatex: 替换 {total} 处 \\textsc", flush=True)
    return total


def patch_local_unavailable_t1_font_defaults(workfolder):
    """Fallback local T1 font families to Latin Modern when TFM files are absent."""
    import re as _re

    total = 0
    fallback_for_default = {
        'sfdefault': 'lmss',
        'rmdefault': 'lmr',
        'ttdefault': 'lmtt',
    }
    known_t1_replacements = {
        'rmdefault': {'ptm': 'lmr', 'ppl': 'lmr', 'pbk': 'lmr', 'pag': 'lmr'},
        'sfdefault': {'phv': 'lmss'},
        'ttdefault': {'pcr': 'lmtt'},
    }
    for path in glob.glob(os.path.join(workfolder, '**', '*.cls'), recursive=True) + \
            glob.glob(os.path.join(workfolder, '**', '*.sty'), recursive=True):
        try:
            with open(path, encoding='utf-8', errors='replace') as f:
                text = f.read()
        except Exception:
            continue

        new_text = text
        count = 0
        for default_name, family_map in known_t1_replacements.items():
            for family, fallback in family_map.items():
                pattern = _re.compile(
                    r'(?m)^(?P<indent>\s*)\\renewcommand\{\\'
                    + default_name
                    + r'\}\{'
                    + _re.escape(family)
                    + r'\}\s*%?\s*$'
                )
                replacement = (
                    r'\g<indent>\\renewcommand{\\'
                    + default_name
                    + r'}{'
                    + fallback
                    + r'}% paper-trans: fallback unavailable T1 default '
                    + family
                )
                new_text, n = pattern.subn(replacement, new_text)
                count += n

        if count:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(new_text)
            total += count

    shape_re = _re.compile(
        r'\\DeclareFontShape\{T1\}\{([^{}]+)\}\{[^{}]+\}\{[^{}]+\}\{([^{}]+)\}\{[^{}]*\}'
    )
    for path in glob.glob(os.path.join(workfolder, '**', '*.cls'), recursive=True) + \
            glob.glob(os.path.join(workfolder, '**', '*.sty'), recursive=True):
        try:
            with open(path, encoding='utf-8', errors='replace') as f:
                text = f.read()
        except Exception:
            continue

        local_families = set()
        for m in shape_re.finditer(text):
            spec = m.group(2)
            if '/' in spec or '.ttf' in spec.lower() or '.otf' in spec.lower():
                local_families.add(m.group(1))
        if not local_families:
            continue

        new_text = text
        count = 0
        for family in sorted(local_families, key=len, reverse=True):
            for default_name, fallback in fallback_for_default.items():
                pattern = _re.compile(
                    r'(?m)^(?P<indent>\s*)\\renewcommand\{\\'
                    + default_name
                    + r'\}\{'
                    + _re.escape(family)
                    + r'\}\s*%?\s*$'
                )
                replacement = (
                    r'\g<indent>\\renewcommand{\\'
                    + default_name
                    + r'}{'
                    + fallback
                    + r'}% paper-trans: fallback unavailable local T1 font family '
                    + family
                )
                new_text, n = pattern.subn(replacement, new_text)
                count += n

            fontfamily_pattern = _re.compile(r'\\fontfamily\{' + _re.escape(family) + r'\}')
            new_text, n = fontfamily_pattern.subn(r'\\fontfamily{lmss}', new_text)
            count += n

        if count:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(new_text)
            total += count

    if total:
        print(f"[driver] 🔧 patch_local_unavailable_t1_font_defaults: 回退 {total} 处本地 T1 字体默认值", flush=True)
    return total


def patch_local_pdftex_engine_guards(workfolder):
    """Disable local class/style guards that forbid XeLaTeX."""
    import re as _re

    total = 0
    patterns = [
        _re.compile(r'(?m)^(?P<indent>\s*)\\RequirePDFTeX\s*$'),
        _re.compile(r'(?m)^(?P<indent>\s*)\\RequirePackage\{pdftexcmds\}\s*$'),
    ]
    for path in glob.glob(os.path.join(workfolder, '*.cls')) + glob.glob(os.path.join(workfolder, '*.sty')):
        try:
            with open(path, encoding='utf-8', errors='replace') as f:
                text = f.read()
        except Exception:
            continue
        if 'RequirePDFTeX' not in text and 'pdftexcmds' not in text:
            continue

        def _comment(m):
            return m.group('indent') + '% paper-trans: disabled pdfTeX-only guard for XeLaTeX'

        new_text = text
        count = 0
        for pattern in patterns:
            new_text, n = pattern.subn(_comment, new_text)
            count += n
        if count:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(new_text)
            total += count
    if total:
        print(f"[driver] 🔧 patch_local_pdftex_engine_guards: 禁用 {total} 处 PDFTeX-only guard", flush=True)
    return total


def patch_long_citation_lists(trans_tex_path, max_keys=3):
    """
    Split very long citation lists. Some templates/engines can write truncated
    \\citation lines to .aux, which makes BibTeX skip \\bibdata and leaves an
    empty .bbl. Shorter adjacent citation commands avoid that aux corruption.
    """
    import re as _re

    with open(trans_tex_path, encoding='utf-8') as f:
        text = f.read()

    cite_re = _re.compile(r'\\(citep|citet|citealt|citeauthor|citeyearpar|cite)\{([^{}]+)\}')
    total = 0

    def _replace(m):
        nonlocal total
        keys = [k.strip() for k in m.group(2).split(',') if k.strip()]
        if len(keys) <= max_keys:
            return m.group(0)
        total += 1
        cmd = m.group(1)
        chunks = [keys[i:i + max_keys] for i in range(0, len(keys), max_keys)]
        return ''.join('\\' + cmd + '{' + ','.join(chunk) + '}' for chunk in chunks)

    new_text = cite_re.sub(_replace, text)
    if total:
        with open(trans_tex_path, 'w', encoding='utf-8') as f:
            f.write(new_text)
        print(f"[driver] 🔧 patch_long_citation_lists: 拆分了 {total} 个超长 citation", flush=True)
    return total


def patch_verbatim_envs(trans_tex_path, orig_tex_path):
    """
    将翻译后的 tex 文件中所有 verbatim 类环境（tcblisting / lstlisting / verbatim）
    还原为原始文件中的对应块，避免 GPT 翻译破坏代码/prompt 内容导致编译失败。
    返回替换的块数量。
    """
    with open(orig_tex_path, encoding='utf-8') as f:
        orig = f.read()
    with open(trans_tex_path, encoding='utf-8') as f:
        trans = f.read()

    VERBATIM_ENVS = sorted(_ltf.verbatim_restore_envs(orig, trans))

    result = trans
    total = 0
    for env in VERBATIM_ENVS:
        orig_blocks  = _extract_env_blocks(orig, env)
        trans_blocks = _extract_env_blocks(trans, env)
        if not orig_blocks or not trans_blocks:
            continue
        if len(orig_blocks) != len(trans_blocks):
            print(f"[driver] ⚠️  {env} 块数不一致 (orig={len(orig_blocks)} trans={len(trans_blocks)})，跳过", flush=True)
            continue
        # 从后往前替换，避免索引偏移
        for (_, _, ob), (ts, te, tb) in reversed(list(zip(orig_blocks, trans_blocks))):
            if ob != tb:
                result = result[:ts] + ob + result[te:]
                total += 1

    if total:
        with open(trans_tex_path, 'w', encoding='utf-8') as f:
            f.write(result)
    return total


def patch_inline_verb_delimiter_collisions(trans_tex_path):
    with open(trans_tex_path, encoding='utf-8') as f:
        text = f.read()
    fixed, total = _ltf.repair_inline_verb_delimiter_collisions(text)
    if total:
        with open(trans_tex_path, 'w', encoding='utf-8') as f:
            f.write(fixed)
        print(f"[driver] 🔧 patch_inline_verb_delimiter_collisions: 重定界 {total} 个 inline verb", flush=True)
    return total


def patch_and_recompile(workfolder, arxiv_id_):
    """
    当 gpt-academic 翻译完成但编译失败时：
    1. 还原 verbatim 类环境为原始内容
    2. 直接用 xelatex 重新编译 merge_translate_zh.tex
    3. 成功则把 PDF 复制到 translation 目录并返回路径
    """
    _require_configured_gates()
    import subprocess as _sp
    trans_tex  = os.path.join(workfolder, 'merge_translate_zh.tex')
    orig_tex   = os.path.join(workfolder, 'merge.tex')
    output_pdf = os.path.join(workfolder, 'merge_translate_zh.pdf')
    dest_dir   = os.path.join(_arxiv_cache_dir, arxiv_id_, 'translation')
    dest_pdf   = os.path.join(dest_dir, 'translate_zh.pdf')

    if not os.path.exists(trans_tex) or not os.path.exists(orig_tex):
        return None

    print(f"[driver] 🔧 检测到编译失败但翻译已完成，尝试 verbatim 修补+重编译...", flush=True)
    patch_body_endinput(trans_tex)
    patch_packages_in_documentclass_options(trans_tex)
    patch_pdftex_graphics_driver(trans_tex)
    fix_label_ref_emdash(trans_tex)
    patch_tcolorbox_opening_options(trans_tex, orig_tex)
    patch_tcolorbox_small_groups(trans_tex)
    patch_fontawesome_legacy_aliases(trans_tex)
    patch_declare_unicode_character_fallback(trans_tex)
    patch_xelatex_compatibility_fallbacks(trans_tex)
    patch_local_xelatex_compatibility_fallbacks(workfolder)
    patch_local_textls_fallback(workfolder, trans_tex)
    patch_local_sourcesans3_family(workfolder)
    patch_missing_math_aliases(trans_tex)
    patch_local_pdftex_primitives(workfolder)
    patch_pdftex_primitives_for_xelatex(trans_tex)
    patch_textsc_for_xelatex(trans_tex)
    patch_enumitem_for_optional_lists(trans_tex)
    patch_microtype_for_xelatex(trans_tex)
    patch_local_microtype_loads(workfolder)
    patch_local_nvidia_font_maps(workfolder)
    patch_local_unavailable_t1_font_defaults(workfolder)
    patch_local_pdftex_engine_guards(workfolder)
    patch_long_citation_lists(trans_tex)
    n = patch_verbatim_envs(trans_tex, orig_tex)
    print(f"[driver] 🔧 修补了 {n} 个 verbatim 类环境块", flush=True)
    patch_inline_verb_delimiter_collisions(trans_tex)
    patch_unbalanced_groups_in_tcolorboxes(trans_tex)
    patch_missing_custom_macro_definitions(trans_tex, orig_tex)
    patch_custom_macro_cjk_glue(trans_tex)
    patch_stray_text_word_commands(trans_tex)
    patch_algorithmic_command_glue(trans_tex)
    patch_algorithm2e_keyword_aliases(trans_tex)
    patch_llm_translation_artifacts(trans_tex)
    patch_structural_commands_in_captions(trans_tex)
    patch_stray_closing_brace_after_cjk_sentence(trans_tex)
    patch_unclosed_textbf_reference_heads(trans_tex)
    patch_inline_math_delimiter_artifacts(trans_tex)
    patch_common_command_cjk_glue(trans_tex)
    patch_bare_citation_commands(trans_tex)
    patch_declaration_command_cjk_glue(trans_tex)
    patch_spurious_cjk_command_escapes(trans_tex)
    patch_missing_graphics(trans_tex)
    patch_fragile_cleveref_references(trans_tex)
    patch_duplicate_end_environments(trans_tex)
    patch_fragile_tikz_matrix_legends(trans_tex)
    patch_tikz_matrix_node_linebreaks(trans_tex)
    patch_undefined_unique_ref_labels(trans_tex)
    patch_dangling_href_commands(trans_tex, orig_tex)
    clean_latex_intermediates(workfolder)
    patch_unsafe_bibtex_keys(workfolder, trans_tex)
    synthesized_bbl = synthesize_bbl_from_tex(workfolder, trans_tex)
    if synthesized_bbl:
        bbl_path = os.path.join(workfolder, 'merge_translate_zh.bbl')
        if patch_missing_bibitem_citations(trans_tex, bbl_path):
            clean_latex_intermediates(workfolder)
            synthesized_bbl = synthesize_bbl_from_tex(workfolder, trans_tex)
        if synthesized_bbl:
            patch_bibliography_to_generated_bbl(workfolder, trans_tex)

    # Some late bibliography/source-reconciliation patches rewrite TeX fragments.
    # Run the idempotent escape cleanup once more immediately before compilation
    # so a restored ``\中文`` artifact cannot survive into the final pass.
    patch_spurious_cjk_command_escapes(trans_tex)

    def _latex_cmds(engine, has_bbl):
        if engine == 'xelatex':
            engine_cmd = [
                engine, '-no-shell-escape', '-no-pdf',
                '-interaction=nonstopmode', '-file-line-error',
                'merge_translate_zh.tex',
            ]
        else:
            engine_cmd = [
                engine, '-no-shell-escape',
                '-interaction=nonstopmode', '-file-line-error',
                'merge_translate_zh.tex',
            ]
        if has_bbl:
            return [engine_cmd, engine_cmd, engine_cmd, engine_cmd]
        return [
            engine_cmd,
            ['bibtex', 'merge_translate_zh'],
            engine_cmd,
            engine_cmd,
            engine_cmd,
        ]

    def _run_latex_cmds(cmds):
        segfault = False
        is_xelatex = False
        for idx, cmd in enumerate(cmds):
            r = _sp.run(
                cmd, cwd=workfolder, timeout=900,
                stdout=_sp.DEVNULL, stderr=_sp.PIPE,
                env=_restricted_tex_env(),
            )
            if cmd[0] == 'xelatex':
                is_xelatex = True
            if cmd[0] in ('xelatex', 'lualatex') and idx < len(cmds) - 1:
                sanitize_latex_aux_file(workfolder)
            stderr = (r.stderr or b'').decode('utf-8', errors='replace')
            if r.returncode >= 128 or 'Segmentation fault' in stderr:
                segfault = True
                break

        if not segfault and is_xelatex:
            print("[driver] 🛠️  运行 xdvipdfmx 转换 DVI 为 PDF (zlib compression level = 3)", flush=True)
            r_pdf = _sp.run(
                ['xdvipdfmx', '-z', '3', 'merge_translate_zh.xdv'],
                cwd=workfolder, timeout=900,
                stdout=_sp.DEVNULL, stderr=_sp.PIPE,
            )
            stderr_pdf = (r_pdf.stderr or b'').decode('utf-8', errors='replace')
            if r_pdf.returncode != 0 or 'Segmentation fault' in stderr_pdf:
                print(f"[driver] ❌ xdvipdfmx 运行失败: returncode={r_pdf.returncode}, stderr={stderr_pdf[:200]}", flush=True)
                segfault = True
        return segfault

    try:
        segfault = _run_latex_cmds(_latex_cmds('xelatex', synthesized_bbl))
        if segfault:
            print("[driver] ⚠️  xelatex 触发 segfault，切换 lualatex 重编译", flush=True)
            clean_latex_intermediates(workfolder)
            synthesized_bbl = synthesize_bbl_from_tex(workfolder, trans_tex)
            if synthesized_bbl:
                patch_bibliography_to_generated_bbl(workfolder, trans_tex)
            _run_latex_cmds(_latex_cmds('lualatex', synthesized_bbl))
    except Exception as e:
        print(f"[driver] ⚠️  LaTeX/BibTeX 执行异常: {e}", flush=True)
        return None

    if pdf_integrity_ok(output_pdf):
        kb = os.path.getsize(output_pdf) // 1024
        if not translation_quality_ok(workfolder, arxiv_id_):
            return None
        if not latex_compile_health_ok(workfolder, arxiv_id_, strict=True):
            if latex_compile_health_only_stale_refs(workfolder):
                print(
                    "[driver] 🔁 仅残留交叉引用警告，追加 1 次 xelatex 重跑 (sequential)",
                    flush=True,
                )
                try:
                    r1 = _sp.run(
                        [
                            'xelatex', '-no-shell-escape', '-no-pdf',
                            '-interaction=nonstopmode', '-file-line-error',
                            'merge_translate_zh.tex',
                        ],
                        cwd=workfolder,
                        timeout=900,
                        stdout=_sp.DEVNULL,
                        stderr=_sp.PIPE,
                        env=_restricted_tex_env(),
                    )
                    if r1.returncode == 0:
                        _sp.run(
                            ['xdvipdfmx', '-z', '3', 'merge_translate_zh.xdv'],
                            cwd=workfolder,
                            timeout=900,
                            stdout=_sp.DEVNULL,
                            stderr=_sp.PIPE,
                        )
                except Exception as e:
                    print(f"[driver] ⚠️  追加 xelatex 失败: {e}", flush=True)
            if not latex_compile_health_ok(workfolder, arxiv_id_, strict=False):
                return None
        os.makedirs(dest_dir, exist_ok=True)
        shutil.copy2(output_pdf, dest_pdf)
        print(f"[driver] ✅ 修补重编译成功: {dest_pdf} ({kb}KB)", flush=True)
        return dest_pdf

    print(f"[driver] ❌ 修补重编译仍未生成有效 PDF", flush=True)
    return None
