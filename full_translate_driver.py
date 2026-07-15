#!/usr/bin/env python3
"""
在 gpt-academic Docker 容器内运行的全文翻译驱动脚本
用法: python3 full_translate_driver.py <arxiv_id> [--no-cache] [--retries N]
输出: RESULT:SUCCESS:<pdf_path>  或  RESULT:ERROR:<msg>
"""
import sys, os, glob, time, shutil, tarfile
import latex_translation_filters as _ltf
from failure_taxonomy import classify_failure

sys.path.insert(0, '/gpt')
os.chdir('/gpt')

arxiv_id        = sys.argv[1] if len(sys.argv) > 1 else None
no_cache        = "--no-cache" in sys.argv
keep_translation = "--keep-translation" in sys.argv   # 保留已有翻译，只重跑编译
max_retries = 0   # 只翻译一次，不重试
SPLITTER_CACHE_VERSION = "paper-trans-splitter-2026-06-17-v2"

if not arxiv_id:
    print("RESULT:ERROR:请提供 arxiv_id", flush=True)
    sys.exit(1)

print(f"[driver] 开始处理: {arxiv_id}  no_cache={no_cache}  keep_translation={keep_translation}  max_retries={max_retries}", flush=True)

# ── 代理注入（必须在所有 gpt-academic 模块导入之前）──────────────────────────────
HOST_PROXY   = os.environ.get("HOST_PROXY", "http://127.0.0.1:7890")
PROXIES_DICT = {"http": HOST_PROXY, "https": HOST_PROXY}
PROXIES_STR  = '{{"http": "{p}", "https": "{p}"}}'.format(p=HOST_PROXY)

for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ[k] = HOST_PROXY
os.environ["NO_PROXY"]               = "localhost,127.0.0.1"
os.environ["GPT_ACADEMIC_USE_PROXY"] = "True"
os.environ["GPT_ACADEMIC_proxies"]   = PROXIES_STR
os.environ["USE_PROXY"]              = "True"
os.environ["proxies"]                = PROXIES_STR

print(f"[driver] 代理: {HOST_PROXY}", flush=True)

from loguru import logger
logger.disable("root")

import shared_utils.config_loader as _cfg
try:
    _cfg.read_single_conf_with_lru_cache.cache_clear()
except AttributeError:
    pass
_orig_read = _cfg.read_single_conf_with_lru_cache
def _patched_read(arg):
    if arg == 'proxies':   return PROXIES_DICT
    if arg == 'USE_PROXY': return True
    return _orig_read(arg)
_cfg.read_single_conf_with_lru_cache = _patched_read

import requests as _req
_OrigSession = _req.Session
_LLM_HTTP_TIMEOUT = int(os.environ.get("PAPER_TRANS_LLM_HTTP_TIMEOUT", "120"))


class _PatchedSession(_OrigSession):
    def __init__(self):
        super().__init__()
        self.proxies.update(PROXIES_DICT)

    def request(self, method, url, **kwargs):
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = _LLM_HTTP_TIMEOUT
        return super().request(method, url, **kwargs)


_orig_request = _req.request


def _patched_request(method, url, **kwargs):
    if kwargs.get("timeout") is None:
        kwargs["timeout"] = _LLM_HTTP_TIMEOUT
    return _orig_request(method, url, **kwargs)


_req.Session = _PatchedSession
_req.request = _patched_request

# ── Patch compile_latex_with_timeout：用进程组 kill，防止 pdflatex 变孤儿进程 ─────
import subprocess as _subprocess
import os as _os
import signal as _signal

def _patched_compile_with_timeout(command, cwd, timeout=90):
    """修复版：shell=True + 进程组 kill，确保 pdflatex 子进程也被杀掉。"""
    # gpt-academic 传入的缓存目录通常是相对 /gpt 的路径。插件内部会切换
    # 当前目录，多轮编译时若继续把相对路径交给 Popen，会把同一路径重复
    # 拼接并触发 FileNotFoundError。这里统一锚定到稳定的容器项目根目录。
    if not _os.path.isabs(cwd):
        cwd = _os.path.join("/gpt", cwd)
    cwd = _os.path.abspath(cwd)
    process = _subprocess.Popen(
        command, shell=True,
        stdout=_subprocess.PIPE, stderr=_subprocess.PIPE,
        cwd=cwd,
        preexec_fn=_os.setsid,   # 创建新进程组
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        return True
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

_patched_compile_with_timeout.__defaults__ = (300,)  # 将默认 timeout 从 60 改为 300s

# 导入后替换（必须在导入 crazy_functions 之前设置好）
import importlib
_latex_toolbox_spec = importlib.util.find_spec('crazy_functions.latex_fns.latex_toolbox')
if _latex_toolbox_spec:
    _lt = importlib.import_module('crazy_functions.latex_fns.latex_toolbox')
    _lt.compile_latex_with_timeout = _patched_compile_with_timeout
    print(f"[driver] ✅ compile_latex_with_timeout 已 patch（timeout=300s，进程组 kill）", flush=True)

    # ── Patch find_main_tex_file：先去注释再搜索 \documentclass，避免注释行误匹配 ──────
    # gpt-academic 原实现在原始文本（含注释）中搜索 \documentclass，
    # 导致注释掉的 \documentclass 也会使非主文件被列为候选，进而得分更高后被误选。
    _orig_find_main = _lt.find_main_tex_file

    def _patched_find_main_tex_file(file_manifest, mode):
        import os as _os
        import re as _re
        import numpy as _np

        def _rm_comments_simple(text):
            lines = []
            for line in text.splitlines():
                stripped = line.lstrip()
                if stripped.startswith('%'):
                    continue
                idx = line.find('%')
                if idx >= 0:
                    line = line[:idx]
                lines.append(line)
            return '\n'.join(lines)

        candidates = []
        for texf in file_manifest:
            if _os.path.basename(texf).startswith('merge'):
                continue
            try:
                with open(texf, 'r', encoding='utf8', errors='ignore') as _f:
                    raw = _f.read()
            except Exception:
                continue
            clean = _rm_comments_simple(raw)
            if r'\documentclass' in clean:
                candidates.append(texf)

        if len(candidates) == 0:
            raise RuntimeError('无法找到一个主Tex文件（包含documentclass关键字）')
        if len(candidates) == 1:
            print(f"[driver] ✅ 主 Tex 文件: {candidates[0]}", flush=True)
            return candidates[0]

        # 多个候选时按原始逻辑打分（但在去注释后的内容上）
        unexpected_words = [r'\LaTeX', 'manuscript', 'Guidelines', 'font',
                            'citations', 'rejected', 'blind review', 'reviewers']
        expected_words   = [r'\input', r'\ref', r'\cite']
        scores = []
        for texf in candidates:
            try:
                with open(texf, 'r', encoding='utf8', errors='ignore') as _f:
                    content = _lt.rm_comments(_f.read())
            except Exception:
                content = ''
            s = 0
            for w in unexpected_words:
                if w in content:
                    s -= 1
            for w in expected_words:
                if w in content:
                    s += 1
            scores.append(s)
        best = candidates[int(_np.argmax(scores))]
        print(f"[driver] ✅ 主 Tex 文件 (多候选, scores={dict(zip([_os.path.basename(c) for c in candidates], scores))}): {best}", flush=True)
        return best

    _lt.find_main_tex_file = _patched_find_main_tex_file
    # 同步更新 latex_actions 模块中已导入的引用
    _la_spec = importlib.util.find_spec('crazy_functions.latex_fns.latex_actions')
    if _la_spec:
        _la = importlib.import_module('crazy_functions.latex_fns.latex_actions')
        _la.find_main_tex_file = _patched_find_main_tex_file
    print(f"[driver] ✅ find_main_tex_file 已 patch（注释行不参与 documentclass 检测）", flush=True)

    # ── Patch merge_tex_files_：系统级 TeX 文件（如 glyphtounicode）不在项目目录里，
    #    原实现直接 raise RuntimeError，改为先用 kpsewhich 确认是否为系统文件，是则跳过。
    _orig_merge_tex_files_ = _lt.merge_tex_files_

    def _patched_merge_tex_files_(project_folder, main_file, mode):
        import re as _re, subprocess as _sp
        main_file = _lt.rm_comments(main_file)
        for s in reversed([q for q in _re.finditer(r"\\input\{(.*?)\}", main_file, _re.M)]):
            f = _ltf.normalize_tex_include_target(s.group(1))
            fp = _os.path.join(project_folder, f)
            fp_ = _lt.find_tex_file_ignore_case(fp)
            if fp_:
                try:
                    with open(fp_, "r", encoding="utf-8", errors="replace") as fx:
                        c = fx.read()
                except Exception:
                    c = "\n\nWarning from GPT-Academic: LaTex source file is missing!\n\n"
            else:
                # 检查是否为系统级 TeX 文件（通过 kpsewhich 查找）
                try:
                    probe = f if _os.path.splitext(f)[1] else f + '.tex'
                    r = _sp.run(['kpsewhich', probe],
                                capture_output=True, text=True, timeout=5)
                    if r.returncode == 0 and r.stdout.strip():
                        print(f"[driver] ⚠️  跳过系统 TeX 文件（非项目文件）: {f}", flush=True)
                        c = f"% [system file skipped by driver patch: {f}]\n"
                    else:
                        raise RuntimeError(f"找不到{fp}，Tex源文件缺失！")
                except _sp.TimeoutExpired:
                    raise RuntimeError(f"找不到{fp}，Tex源文件缺失！")
            c = _patched_merge_tex_files_(project_folder, c, mode)
            main_file = main_file[:s.span()[0]] + c + main_file[s.span()[1]:]
        return main_file

    _lt.merge_tex_files_ = _patched_merge_tex_files_
    print(f"[driver] ✅ merge_tex_files_ 已 patch（系统 TeX 文件引用自动跳过）", flush=True)


def _patch_latex_translation_splitter():
    """
    gpt-academic upstream is intentionally conservative: many LaTeX blocks are
    marked as PRESERVE to protect compilation. In complex papers, ordinary prose
    can become glued to those preserved blocks, so it never reaches the LLM and
    the final PDF is only partially translated. Split large preserved nodes
    again and send obvious prose lines through the translator.
    """
    if os.environ.get("PAPER_TRANS_EXPAND_TRANSLATION_SPLIT", "1") == "0":
        print("[driver] ⚠️  latex translation splitter expansion disabled", flush=True)
        return

    import html as _html
    import re as _re
    from crazy_functions.latex_fns import latex_actions as _la
    from crazy_functions.latex_fns.latex_toolbox import LinkedListNode as _Node

    if getattr(_la.LatexPaperSplit, "_paper_trans_split_patch", False):
        return

    _orig_split = _la.LatexPaperSplit.split
    tracked_static_envs = _ltf.tracked_envs() | {"center"}
    command_only_re = _re.compile(
        r"^\\(?:includegraphics|label|ref|eqref|cite|citep|citet|citealt|"
        r"bibliography|bibliographystyle|toprule|midrule|bottomrule|hline|"
        r"cline|cmidrule|addlinespace|centering|raggedright|small|footnotesize|"
        r"scriptsize|normalsize|vspace|hspace|vfill|newpage|clearpage|appendix|"
        r"tableofcontents|maketitle|printbibliography)\b"
    )
    latex_cmd_re = _re.compile(r"\\[A-Za-z@]+\*?(?:\[[^\]]*\])?(?:\{[^{}]*\})?")
    inline_math_re = _re.compile(r"\$[^$]*\$")

    def _rough_text(line: str) -> str:
        rough = inline_math_re.sub(" ", line)
        rough = _re.sub(
            r"\\(?:textcolor|colorbox|href)\*?(?:\[[^\]]*\])?\{[^{}]*\}\{([^{}]*)\}",
            r" \1 ",
            rough,
        )
        for _ in range(3):
            rough = _re.sub(
                r"\\(?:textbf|textit|texttt|emph|underline|small|footnotesize|"
                r"scriptsize|normalsize|large|Large|captionof)\*?"
                r"(?:\[[^\]]*\])?(?:\{[^{}]*\})?\{([^{}]*)\}",
                r" \1 ",
                rough,
        )
        rough = latex_cmd_re.sub(" ", rough)
        rough = _re.sub(r"\\.|[{}$^_&#~]", " ", rough)
        return rough

    def _env_is_tracked(env: str | None) -> bool:
        return env == "center" or _ltf.is_tracked_env(env)

    def _text_has_translatable_prose(text: str, min_letters=32, min_words=5) -> bool:
        stripped = text.strip()
        if not stripped or stripped.startswith("%"):
            return False
        if command_only_re.match(stripped):
            return False
        if _re.fullmatch(r"[\s{}\\\[\](),.;:~_^$&%#0-9+\-*/=<>|]+", stripped):
            return False

        rough = _rough_text(stripped)
        letters = len(_re.findall(r"[A-Za-z]", rough))
        cjk = len(_re.findall(r"[\u4e00-\u9fff]", rough))
        words = _re.findall(r"\b[A-Za-z][A-Za-z\-]{2,}\b", rough)

        if _re.match(r"^\\(?:section|subsection|subsubsection|paragraph|title)\*?\{", stripped):
            return letters >= 6
        if cjk >= 8 and cjk >= letters:
            return False
        return letters >= min_letters and len(words) >= min_words

    def _line_has_translatable_prose(line: str) -> bool:
        return _text_has_translatable_prose(line, min_letters=32, min_words=5)

    def _append(nodes, text: str, preserve: bool, merge: bool = True):
        if not text:
            return
        if merge and nodes and nodes[-1].preserve == preserve:
            nodes[-1].string += text
        else:
            nodes.append(_Node(text, preserve=preserve))

    def _split_comment(line: str):
        for idx, ch in enumerate(line):
            if ch == "%" and (idx == 0 or line[idx - 1] != "\\"):
                return line[:idx], line[idx:]
        return line, ""

    def _append_translatable_fragment(nodes, text: str, min_letters=32, min_words=5):
        if not text:
            return
        leading_len = len(text) - len(text.lstrip())
        trailing_len = len(text.rstrip()) if text.rstrip() else 0
        leading = text[:leading_len]
        core = text[leading_len:trailing_len]
        trailing = text[trailing_len:]
        if _text_has_translatable_prose(core, min_letters=min_letters, min_words=min_words):
            _append(nodes, leading, True)
            _append(nodes, core, False)
            _append(nodes, trailing, True)
        else:
            _append(nodes, text, True)

    def _split_unescaped_ampersands(text: str):
        tokens = []
        start = 0
        for m in _re.finditer(r"(?<!\\)&", text):
            tokens.append(("cell", text[start:m.start()]))
            tokens.append(("delimiter", text[m.start():m.end()]))
            start = m.end()
        tokens.append(("cell", text[start:]))
        return tokens

    def _split_tabular_line(line: str):
        nodes = []
        code, comment = _split_comment(line)
        newline = "\n" if code.endswith("\n") else ""
        if newline:
            code = code[:-1]
        if _re.match(r"^\s*\\(?:toprule|midrule|bottomrule|hline|cline|cmidrule|addlinespace)\b", code.strip()):
            _append(nodes, line, True)
            return nodes

        suffix = ""
        row_end = _re.search(r"(?<!\\)(\\\\(?:\[[^\]]*\])?\s*)$", code)
        if row_end:
            suffix = row_end.group(1)
            code = code[:row_end.start()]

        for kind, token in _split_unescaped_ampersands(code):
            if kind == "delimiter":
                _append(nodes, token, True)
            else:
                _append_translatable_fragment(nodes, token, min_letters=5, min_words=1)
        _append(nodes, suffix + comment + newline, True)
        return nodes

    def _find_matching_brace(text: str, open_idx: int) -> int:
        depth = 0
        for idx in range(open_idx, len(text)):
            ch = text[idx]
            if ch == "{" and (idx == 0 or text[idx - 1] != "\\"):
                depth += 1
            elif ch == "}" and (idx == 0 or text[idx - 1] != "\\"):
                depth -= 1
                if depth == 0:
                    return idx
        return -1

    def _split_algorithmic_line(line: str):
        nodes = []
        code, comment = _split_comment(line)
        newline = "\n" if code.endswith("\n") else ""
        if newline:
            code = code[:-1]
        m = _re.match(r"^(\s*\\Comment\s*)\{", code)
        if m:
            open_idx = m.end() - 1
            close_idx = _find_matching_brace(code, open_idx)
            if close_idx > open_idx:
                _append(nodes, code[:open_idx + 1], True)
                _append_translatable_fragment(nodes, code[open_idx + 1:close_idx], min_letters=5, min_words=1)
                _append(nodes, code[close_idx:] + comment + newline, True)
                return nodes
        m = _re.match(r"^(\s*\\(?:State|Require|Ensure|Return)\b\s*)(.*)$", code)
        if m:
            _append(nodes, m.group(1), True)
            _append_translatable_fragment(nodes, m.group(2), min_letters=5, min_words=1)
            _append(nodes, comment + newline, True)
            return nodes
        m = _re.match(r"^(\s*\\(?:If|ElsIf|For|ForAll|While)\s*)\{", code)
        if m:
            open_idx = m.end() - 1
            close_idx = _find_matching_brace(code, open_idx)
            if close_idx > open_idx:
                _append(nodes, code[:open_idx + 1], True)
                _append_translatable_fragment(nodes, code[open_idx + 1:close_idx], min_letters=5, min_words=1)
                _append(nodes, code[close_idx:] + comment + newline, True)
                return nodes
        _append_translatable_fragment(nodes, code, min_letters=12, min_words=2)
        _append(nodes, comment + newline, True)
        return nodes

    def _update_env_stack(line: str, env_stack: list[str]):
        begins = _re.findall(r"\\begin\{([^}]+)\}", line)
        ends = _re.findall(r"\\end\{([^}]+)\}", line)
        for env in begins:
            if _env_is_tracked(env):
                env_stack.append(env)
        for env in ends:
            if _env_is_tracked(env):
                if env in env_stack:
                    pos = len(env_stack) - 1 - env_stack[::-1].index(env)
                    env_stack = env_stack[:pos]
                elif env_stack:
                    env_stack.pop()
        return env_stack

    def _split_long_transform_line(line: str):
        if len(_rough_text(line)) < 420:
            return [line]

        parts = []
        start = 0
        brace_depth = 0
        in_math = False
        for idx, ch in enumerate(line):
            prev = line[idx - 1] if idx else ""
            if ch == "$" and prev != "\\":
                in_math = not in_math
            elif not in_math and ch == "{" and prev != "\\":
                brace_depth += 1
            elif not in_math and ch == "}" and prev != "\\" and brace_depth > 0:
                brace_depth -= 1
            if brace_depth == 0 and not in_math and ch in ".;。；":
                nxt = line[idx + 1] if idx + 1 < len(line) else ""
                if nxt.isspace() and idx + 1 - start >= 180:
                    end = idx + 1
                    while end < len(line) and line[end].isspace() and line[end] != "\n":
                        end += 1
                    parts.append(line[start:end])
                    start = end
        if start < len(line):
            parts.append(line[start:])
        return parts if len(parts) > 1 else [line]

    def _split_transform_text(text: str):
        parts = []
        for line in text.splitlines(keepends=True):
            if _line_has_translatable_prose(line):
                parts.extend(_split_long_transform_line(line))
            else:
                parts.append(line)
        return parts

    def _split_preserved_text(text: str, state: dict):
        nodes = []
        for line in text.splitlines(keepends=True):
            if r"\begin{document}" in line:
                _append(nodes, line, True)
                state["in_document"] = True
                state["env_stack"] = _update_env_stack(line, state["env_stack"])
                continue
            if r"\end{document}" in line:
                _append(nodes, line, True)
                state["in_document"] = False
                state["env_stack"] = _update_env_stack(line, state["env_stack"])
                continue

            active_env = state["env_stack"][-1] if state["env_stack"] else None
            in_soft_env = _ltf.is_soft_text_env(active_env)
            hard_active = any(_ltf.is_hard_protected_env(env) for env in state["env_stack"])
            begins = _re.findall(r"\\begin\{([^}]+)\}", line)
            ends = _re.findall(r"\\end\{([^}]+)\}", line)
            structural_line = any(_env_is_tracked(env) for env in begins + ends)

            if state["in_document"] and in_soft_env and not structural_line:
                if active_env.startswith("tabular") or active_env in {"longtable", "array"}:
                    for part in _split_tabular_line(line):
                        _append(nodes, part.string, part.preserve)
                elif active_env.startswith("algorithm"):
                    for part in _split_algorithmic_line(line):
                        _append(nodes, part.string, part.preserve)
                else:
                    _append_translatable_fragment(nodes, line, min_letters=12, min_words=2)
            else:
                line_protected = (
                    (not state["in_document"])
                    or hard_active
                    or structural_line
                )
                transform = (not line_protected) and _line_has_translatable_prose(line)
                _append(nodes, line, preserve=not transform)

            state["env_stack"] = _update_env_stack(line, state["env_stack"])
        return nodes

    def _recompute_ranges(nodes):
        n_line = 0
        for node in nodes:
            n_l = node.string.count("\n")
            node.range = [n_line - 2, n_line + n_l + 2]
            n_line += n_l

    def _invalidate_stale_split_cache(project_folder: str):
        marker = os.path.join(project_folder, ".paper_trans_splitter_version")
        temp_cache = os.path.join(project_folder, "temp.pkl")
        old_version = ""
        try:
            if os.path.exists(marker):
                with open(marker, encoding="utf-8", errors="replace") as f:
                    old_version = f.read().strip()
            if os.path.exists(temp_cache) and old_version != SPLITTER_CACHE_VERSION:
                os.remove(temp_cache)
                print(
                    "[driver] 🧹 temp.pkl splitter cache version changed; "
                    "removed stale translation cache",
                    flush=True,
                )
            with open(marker, "w", encoding="utf-8") as f:
                f.write(SPLITTER_CACHE_VERSION + "\n")
        except Exception as e:
            print(f"[driver] ⚠️  splitter cache version check failed: {e}", flush=True)

    def _is_section_heading(text: str) -> bool:
        return bool(_re.match(
            r"^\\(?:section|subsection|subsubsection|paragraph|subparagraph|title)\*?\{",
            text.strip(),
        ))

    def _semantic_enough_for_gpt(text: str) -> bool:
        """
        Re-apply the spirit of upstream post_process after our expansion.

        Upstream demotes all tiny transform nodes before GPT sees them. The
        expansion below can create new short table/algorithm/prose fragments,
        so we need a second gate here; otherwise the model may answer the
        prompt itself ("Below is...", "Please provide...") instead of
        translating source text.
        """
        stripped = text.strip()
        if not stripped:
            return False
        if _is_section_heading(stripped):
            rough = _rough_text(stripped)
            letters = len(_re.findall(r"[A-Za-z]", rough))
            return letters >= 6
        if not _text_has_translatable_prose(stripped, min_letters=18, min_words=3):
            return False

        rough = _rough_text(stripped)
        letters = len(_re.findall(r"[A-Za-z]", rough))
        words = _re.findall(r"\b[A-Za-z][A-Za-z\-]{2,}\b", rough)

        if len(stripped) < 42:
            return letters >= 24 and len(words) >= 4
        if stripped.count("\\") >= 3 and letters < 32 and len(words) < 5:
            return False
        return True

    def _finalize_expanded_nodes(nodes):
        finalized = []
        demoted = 0
        for node in nodes:
            if not node.preserve and not _semantic_enough_for_gpt(node.string):
                node.preserve = True
                demoted += 1

            if node.preserve:
                _append(finalized, node.string, True)
                continue

            leading_len = len(node.string) - len(node.string.lstrip())
            trailing_len = len(node.string.rstrip()) if node.string.rstrip() else 0
            leading = node.string[:leading_len]
            core = node.string[leading_len:trailing_len]
            trailing = node.string[trailing_len:]
            _append(finalized, leading, True)
            _append(finalized, core, False, merge=False)
            _append(finalized, trailing, True)
        return finalized, demoted

    def _patched_split(self, txt, project_folder, opts):
        res = _orig_split(self, txt, project_folder, opts)
        original_transform = sum(1 for node in self.nodes if not node.preserve)
        original_chars = sum(len(node.string) for node in self.nodes if not node.preserve)

        expanded = []
        state = {"in_document": False, "env_stack": []}
        for node in self.nodes:
            if not node.preserve:
                for part in _split_transform_text(node.string):
                    _append(expanded, part, False, merge=False)
                if r"\begin{document}" in node.string:
                    state["in_document"] = True
                if r"\end{document}" in node.string:
                    state["in_document"] = False
                state["env_stack"] = _update_env_stack(node.string, state["env_stack"])
                continue
            parts = _split_preserved_text(node.string, state)
            for part in parts:
                _append(expanded, part.string, part.preserve)

        expanded, demoted_short = _finalize_expanded_nodes(expanded)
        _invalidate_stale_split_cache(project_folder)
        _recompute_ranges(expanded)
        self.nodes = expanded
        self.sp = [node.string for node in expanded if not node.preserve]

        added = len(self.sp) - original_transform
        added_chars = sum(len(node.string) for node in expanded if not node.preserve) - original_chars
        print(
            f"[driver] ✅ latex splitter expanded prose chunks: "
            f"{original_transform} -> {len(self.sp)} "
            f"(chars +{max(0, added_chars)}, short demoted={demoted_short})",
            flush=True,
        )
        if added > 0:
            try:
                with open(os.path.join(project_folder, "debug_log.html"), "w", encoding="utf8") as f:
                    for node in expanded:
                        show_html = _html.escape(node.string).replace("\n", "<br/>")
                        if node.preserve:
                            f.write(f'<p style="color:red;">{show_html}</p>')
                        else:
                            f.write(f'<p style="color:black;">#{node.range}{show_html}#</p>')
            except Exception as e:
                print(f"[driver] ⚠️  rewrite debug_log.html failed: {e}", flush=True)
        return self.sp

    _la.LatexPaperSplit.split = _patched_split
    _la.LatexPaperSplit._paper_trans_split_patch = True
    dynamic_note = " + dynamic env policy" if tracked_static_envs else ""
    print(f"[driver] ✅ LatexPaperSplit 已 patch（普通正文保守扩展翻译{dynamic_note}）", flush=True)


def _patch_latex_fix_content_artifacts():
    """Clean LLM prompt/refusal artifacts before translated nodes enter final TeX."""
    from crazy_functions.latex_fns import latex_toolbox as _ltb
    from crazy_functions.latex_fns import latex_actions as _la

    if getattr(_ltb, "_paper_trans_fix_content_patch", False):
        return

    _orig_fix_content = _ltb.fix_content

    def _patched_fix_content(final_tex, node_string):
        fixed = _orig_fix_content(final_tex, node_string)
        cleaned, total = _ltf.strip_llm_translation_artifacts(fixed)
        if not total:
            return fixed
        if not cleaned.strip():
            print(
                "[driver] ⚠️  fix_content: 翻译结果仅剩非原文残留，回退原始 chunk",
                flush=True,
            )
            return node_string
        print(f"[driver] 🔧 fix_content: 清理 {total} 处非原文翻译残留", flush=True)
        return cleaned

    _ltb.fix_content = _patched_fix_content
    _la.fix_content = _patched_fix_content
    _ltb._paper_trans_fix_content_patch = True
    print("[driver] ✅ fix_content 已 patch（merge 前清理 LLM 非原文残留）", flush=True)


_patch_latex_translation_splitter()
_patch_latex_fix_content_artifacts()

from toolbox import get_conf, ChatBotWithCookies, default_user_name

api_key   = get_conf('API_KEY')
llm_model = get_conf('LLM_MODEL')
ARXIV_CACHE_DIR = get_conf('ARXIV_CACHE_DIR')
print(f"[driver] 模型: {llm_model}", flush=True)
print(f"[driver] 缓存目录: {ARXIV_CACHE_DIR}", flush=True)

from crazy_functions.Latex_Function import Latex翻译中文并重新编译PDF

arxiv_url = f"https://arxiv.org/abs/{arxiv_id}"

# 模块级：收集插件运行中所有完整消息（不截断），供 diagnose_failure 分析
_plugin_msgs_full: list[str] = []


def translation_quality_report(workfolder: str) -> dict:
    """
    Inspect merge_translate_zh.tex and estimate whether ordinary prose was
    actually translated. This intentionally ignores hard LaTeX blocks such as
    equations, listings, figures, and bibliographies, but still inspects prose
    inside table cells and algorithmic descriptions.
    """
    import re as _re

    trans_tex = os.path.join(workfolder, "merge_translate_zh.tex")
    if not os.path.exists(trans_tex):
        return {"ok": False, "reason": "missing merge_translate_zh.tex"}

    latex_cmd_re = _re.compile(r"\\[A-Za-z@]+\*?(?:\[[^\]]*\])?(?:\{[^{}]*\})?")
    inline_math_re = _re.compile(r"\$[^$]*\$")

    def _rough_text(line: str) -> str:
        rough = inline_math_re.sub(" ", line)
        rough = _re.sub(
            r"\\(?:textcolor|colorbox|href)\*?(?:\[[^\]]*\])?\{[^{}]*\}\{([^{}]*)\}",
            r" \1 ",
            rough,
        )
        for _ in range(3):
            rough = _re.sub(
                r"\\(?:textbf|textit|texttt|emph|underline|small|footnotesize|"
                r"scriptsize|normalsize|large|Large|captionof)\*?"
                r"(?:\[[^\]]*\])?(?:\{[^{}]*\})?\{([^{}]*)\}",
                r" \1 ",
                rough,
            )
        rough = latex_cmd_re.sub(" ", rough)
        rough = _re.sub(r"\\.|[{}$^_&#~]", " ", rough)
        return rough

    def _env_is_tracked(env: str | None) -> bool:
        return _ltf.is_tracked_env(env)

    with open(trans_tex, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    in_document = False
    env_stack: list[str] = []
    cjk = 0
    letters = 0
    prose_lines = 0
    long_english: list[tuple[int, str]] = []
    very_long_english = 0

    for line_no, line in enumerate(lines, 1):
        if r"\begin{document}" in line:
            in_document = True
            continue
        if not in_document:
            continue

        begins = _re.findall(r"\\begin\{([^}]+)\}", line)
        ends = _re.findall(r"\\end\{([^}]+)\}", line)
        active_env = env_stack[-1] if env_stack else None
        in_soft_env = _ltf.is_soft_text_env(active_env)
        hard_active = any(_ltf.is_hard_protected_env(env) for env in env_stack)
        structural_line = any(_env_is_tracked(env) for env in begins + ends)
        line_protected = (
            (hard_active and not in_soft_env)
            or structural_line
        )

        if not line_protected:
            rough = _rough_text(line)
            line_cjk = len(_re.findall(r"[\u4e00-\u9fff]", rough))
            line_letters = len(_re.findall(r"[A-Za-z]", rough))
            words = _re.findall(r"\b[A-Za-z][A-Za-z\-]{2,}\b", rough)
            cjk += line_cjk
            letters += line_letters
            if line_letters >= 40 or line_cjk >= 10:
                prose_lines += 1
            if line_letters >= 80 and line_cjk <= 5 and len(words) >= 12:
                long_english.append((line_no, line.strip()[:220]))
            if line_letters >= 180 and line_cjk <= 8 and len(words) >= 24:
                very_long_english += 1

        for env in begins:
            if _env_is_tracked(env):
                env_stack.append(env)
        for env in ends:
            if _env_is_tracked(env):
                if env in env_stack:
                    pos = len(env_stack) - 1 - env_stack[::-1].index(env)
                    env_stack = env_stack[:pos]
                elif env_stack:
                    env_stack.pop()
        if r"\end{document}" in line:
            in_document = False

    total = cjk + letters
    cjk_pct = (cjk / total * 100) if total else 0.0
    long_count = len(long_english)
    fail = (
        (long_count >= 20)
        or (very_long_english >= 3)
        or (long_count >= 10 and cjk_pct < 70.0)  # relaxed: long papers with EN related-work prose are acceptable
        or (long_count >= 6 and cjk_pct < 55.0)
        or (cjk_pct < 45.0 and long_count >= 4)
        or (cjk_pct < 15.0 and prose_lines >= 20)
    )
    return {
        "ok": not fail,
        "cjk": cjk,
        "letters": letters,
        "cjk_pct": cjk_pct,
        "prose_lines": prose_lines,
        "long_english_lines": long_count,
        "very_long_english_lines": very_long_english,
        "samples": long_english[:8],
    }


def check_pdf_integrity(pdf_path: str) -> bool:
    """Check that the PDF file exists, is larger than 50KB, and can be successfully parsed with pages."""
    if not os.path.exists(pdf_path) or os.path.getsize(pdf_path) < 50 * 1024:
        return False
    try:
        import pypdf
        reader = pypdf.PdfReader(pdf_path)
        return len(reader.pages) > 0
    except Exception as e:
        print(f"[driver] ⚠️ PDF 完整性检查失败 ({os.path.basename(pdf_path)}): {e}", flush=True)
        return False


def translation_quality_ok(workfolder: str, arxiv_id_: str) -> bool:
    report = translation_quality_report(workfolder)
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


def latex_compile_health_ok(workfolder: str, arxiv_id_: str, strict: bool = False) -> bool:
    """Reject PDFs that compiled but still have unresolved TeX/cite/ref issues."""
    import re as _re

    log_path = os.path.join(workfolder, "merge_translate_zh.log")
    if not os.path.exists(log_path):
        print(f"[driver] ⚠️  找不到编译日志，跳过健康检查: {log_path}", flush=True)
        return True
    with open(log_path, encoding="utf-8", errors="replace") as f:
        log = f.read()

    # Verify that the log indicates successful compilation completion (not truncated/killed)
    if "Output written on merge_translate_zh.xdv" not in log and "Output written on merge_translate_zh.pdf" not in log:
        print(f"[driver] ❌ 编译健康检查失败: {arxiv_id_} 找不到输出写入标记，编译日志不完整（可能已被 OOM 强杀）", flush=True)
        return False

    # pdfTeX-only primitive names: XeLaTeX/LuaLaTeX raise "Undefined control sequence"
    # for these, but the engine recovers and the PDF is still complete.  Treat these
    # occurrences as non-fatal warnings rather than hard failures.
    _PDFTEX_PRIM_RE = _re.compile(
        r"\\(?:" + "|".join(_re.escape(name) for name in _ltf.PDFTEX_PRIMITIVE_NAMES) + r")\b"
    )

    def _has_fatal_undefined_control(text: str) -> bool:
        """True when any Undefined control sequence is NOT a pdfTeX primitive."""
        for m in _re.finditer(r"Undefined control sequence", text):
            # Inspect the next 300 chars for a pdfTeX primitive name.
            context = text[m.start(): m.start() + 300]
            if not _PDFTEX_PRIM_RE.search(context):
                return True
        return False

    def _has_pdftex_undef(text: str) -> bool:
        """True when any Undefined control sequence IS a pdfTeX primitive."""
        for m in _re.finditer(r"Undefined control sequence", text):
            context = text[m.start(): m.start() + 300]
            if _PDFTEX_PRIM_RE.search(context):
                return True
        return False

    # Fatal checks: these indicate genuine TeX errors that will corrupt the PDF.
    failures = []
    if _has_fatal_undefined_control(log):
        failures.append("undefined control sequence")
    if _re.search(r"Missing number, treated as zero", log):
        failures.append("missing number")

    # Warnings that can be promoted to failures under strict mode
    promoted_failures = []
    if _re.search(r"(?<!Package natbib Warning: )Citation .* undefined", log):
        promoted_failures.append("undefined citation")
    if _re.search(r"Reference .* undefined", log):
        promoted_failures.append("undefined reference")
    if _re.search(r"There were undefined references", log):
        promoted_failures.append("undefined references")

    # Non-fatal warnings: natbib undefined warnings are produced during multi-pass
    # compilation when citations appear before the bibliography is fully resolved.
    # pdfTeX-primitive "Undefined control sequence" under XeLaTeX is similarly
    # cosmetic — the engine skips the primitive and the PDF is complete.
    warnings = []
    if _re.search(r"Package natbib Warning: .* undefined", log):
        warnings.append("natbib undefined")
    if _has_pdftex_undef(log):
        warnings.append("pdftex primitive undef")

    if strict:
        failures.extend(promoted_failures)
    else:
        warnings.extend(promoted_failures)

    if warnings and not failures:
        print(
            f"[driver] ⚠️  编译健康警告(非致命): {arxiv_id_} "
            f"warnings={', '.join(warnings)}",
            flush=True,
        )
    if failures:
        all_issues = failures + warnings
        print(
            f"[driver] ❌ 编译健康检查失败: {arxiv_id_} "
            f"issues={', '.join(all_issues)}",
            flush=True,
        )
        for m in _re.finditer(
            r".{0,120}(Missing number, treated as zero|"
            r"(?<!Package natbib Warning: )Citation .* undefined|"
            r"Reference .* undefined|There were undefined references|"
            r"Label\(s\) may have changed|Rerun to get cross-references right|"
            r"Package natbib Warning: .* undefined).{0,160}",
            log,
            flags=_re.DOTALL,
        ):
            sample = " ".join(m.group(0).split())
            print(f"[driver]    log: {sample[:260]}", flush=True)
            break
        # Also show a sample of any fatal Undefined control sequence
        if "undefined control sequence" in failures:
            for m in _re.finditer(r"Undefined control sequence", log):
                context = log[m.start(): m.start() + 300]
                if not _PDFTEX_PRIM_RE.search(context):
                    sample = " ".join(context[:200].split())
                    print(f"[driver]    log: {sample[:260]}", flush=True)
                    break
        return False

    print(f"[driver] ✅ 编译健康检查通过: {arxiv_id_}", flush=True)
    return True


def latex_compile_health_only_stale_refs(workfolder: str) -> bool:
    """True when the log only reports cross-ref warnings that another pass may fix."""
    import re as _re

    log_path = os.path.join(workfolder, "merge_translate_zh.log")
    if not os.path.exists(log_path):
        return False
    with open(log_path, encoding="utf-8", errors="replace") as f:
        log = f.read()

    # pdfTeX-only primitives cause recoverable "Undefined control sequence" under
    # XeLaTeX; these should not block the stale-refs re-run path.
    _PDFTEX_PRIM_RE = _re.compile(
        r"\\(?:" + "|".join(_re.escape(name) for name in _ltf.PDFTEX_PRIMITIVE_NAMES) + r")\b"
    )
    for m in _re.finditer(r"Undefined control sequence", log):
        context = log[m.start(): m.start() + 300]
        if not _PDFTEX_PRIM_RE.search(context):
            # A genuine (non-pdftex) undefined control sequence — cannot be fixed by rerun.
            return False
    if _re.search(
        r"Missing number, treated as zero|"
        r"(?<!Package natbib Warning: )Citation .* undefined",
        log,
    ):
        return False
    # natbib undefined warnings are non-fatal (multi-pass compilation artefact)
    return bool(
        _re.search(r"Reference .* undefined|There were undefined references", log)
    )


def run_translation(attempt_no_cache: bool, attempt_idx: int) -> str | None:
    """执行一次翻译+编译，成功返回 PDF 路径，否则返回 None。"""
    llm_kwargs = {
        'api_key': api_key, 'llm_model': llm_model,
        'top_p': 1.0, 'max_length': None, 'temperature': 1.0,
    }
    cookie     = {**llm_kwargs, 'user_name': default_user_name, 'files_to_promote': []}
    chatbot    = ChatBotWithCookies(cookie)
    plugin_kwargs = {'advanced_arg': '--no-cache' if attempt_no_cache else ''}

    print(f"[driver] 第 {attempt_idx} 次尝试  no_cache={attempt_no_cache}", flush=True)
    print(f"[driver] 调用插件: {arxiv_url}", flush=True)

    last_msg = ''
    step_cnt = 0
    t0       = time.time()

    def elapsed():
        return f"{int(time.time() - t0)}s"

    try:
        gen = Latex翻译中文并重新编译PDF(
            arxiv_url, llm_kwargs, plugin_kwargs,
            chatbot, [], '', ''
        )
        for step in gen:
            step_cnt += 1
            if not isinstance(step, tuple) or len(step) < 2:
                continue
            cb = step[1]
            if not cb:
                continue
            try:
                last_pair = list(cb)[-1] if cb else None
                if last_pair and len(last_pair) >= 2 and last_pair[1]:
                    msg   = str(last_pair[1])
                    # 完整消息存档（供诊断用，不截断）
                    _plugin_msgs_full.append(msg)
                    clean = msg.replace('`', '').replace('\n', ' ').strip()
                    if clean != last_msg or step_cnt % 20 == 0:
                        is_key = any(k in clean for k in [
                            '下载', '解压', '分析', '切分', '开始翻译', '编译',
                            '成功', '失败', '错误', 'Error', 'PDF', '完成',
                            '第', '次编译', 'GPT结果', '插件调用',
                        ])
                        prefix = f"[driver|{elapsed()}]"
                        if is_key:
                            # 关键消息：完整打印（不截断），宿主机可捕获完整 traceback
                            print(f"{prefix} ✦ {clean}", flush=True)
                        elif clean != last_msg:
                            print(f"{prefix} · {clean[:120]}", flush=True)
                        last_msg = clean
            except Exception:
                pass
        print(f"[driver|{elapsed()}] 生成器完成，共 {step_cnt} 步", flush=True)
    except Exception as e:
        import traceback
        print(f"[driver|{elapsed()}] 异常: {e}", flush=True)
        traceback.print_exc()

    # 查找生成的 PDF（只在固定位置找，绝不搜索 Figures 子目录）
    translation_dir = os.path.join(ARXIV_CACHE_DIR, arxiv_id, 'translation')
    workfolder      = os.path.join(ARXIV_CACHE_DIR, arxiv_id, 'workfolder')

    # 优先：translation/translate_zh.pdf（插件最终输出）
    candidate = os.path.join(translation_dir, 'translate_zh.pdf')
    if check_pdf_integrity(candidate):
        kb = os.path.getsize(candidate) // 1024
        if not translation_quality_ok(workfolder, arxiv_id):
            return None
        if not latex_compile_health_ok(workfolder, arxiv_id):
            return None
        print(f"[driver|{elapsed()}] ✅ translate_zh.pdf ({kb}KB)", flush=True)
        return candidate

    # 备选：workfolder 根目录里的翻译 PDF（名字含 translate_zh）
    for fname in ('merge_translate_zh.pdf', 'translate_zh.pdf'):
        fp = os.path.join(workfolder, fname)
        if check_pdf_integrity(fp):
            kb = os.path.getsize(fp) // 1024
            if not translation_quality_ok(workfolder, arxiv_id):
                return None
            if not latex_compile_health_ok(workfolder, arxiv_id):
                return None
            print(f"[driver|{elapsed()}] ✅ workfolder/{fname} ({kb}KB)", flush=True)
            return fp

    # 最后：workfolder 根目录（非子目录）内最大 PDF（排除原始 merge.pdf）
    root_pdfs = [
        os.path.join(workfolder, fn)
        for fn in os.listdir(workfolder)
        if fn.endswith('.pdf') and fn not in ('merge.pdf',)
    ] if os.path.isdir(workfolder) else []
    if root_pdfs:
        best = max(root_pdfs, key=os.path.getsize)
        if check_pdf_integrity(best):
            kb = os.path.getsize(best) // 1024
            if not translation_quality_ok(workfolder, arxiv_id):
                return None
            if not latex_compile_health_ok(workfolder, arxiv_id):
                return None
            print(f"[driver|{elapsed()}] ✅ 找到 workfolder 根目录 PDF: {os.path.basename(best)} ({kb}KB)", flush=True)
            return best

    print(f"[driver|{elapsed()}] ❌ 本次未生成有效翻译 PDF（>50KB）", flush=True)
    return None


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

    new_text, total = _ltf.separate_custom_macro_cjk_glue(text)
    new_text, spaced = _ltf.collapse_spaced_cjk_characters(new_text)
    total += spaced
    if total:
        with open(trans_tex_path, 'w', encoding='utf-8') as f:
            f.write(new_text)
        print(
            f"[driver] 🔧 patch_custom_macro_cjk_glue: "
            f"修复 {total} 处宏/CJK 粘连或中文空格",
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
    if total:
        with open(trans_tex_path, 'w', encoding='utf-8') as f:
            f.write(new_text)
        print(f"[driver] 🔧 patch_duplicate_end_environments: 移除了 {total} 个重复 end 环境", flush=True)
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
    replacements: dict[str, str] = {}
    for label in sorted({m.group(2) for m in ref_pattern.finditer(text)} - labels):
        if '/' in label:
            colon_variant = label.replace('/', ':')
            if colon_variant in labels:
                replacements[label] = colon_variant
                continue
        candidates = sorted(
            target for target in labels
            if target.startswith(label + '_') or target.startswith(label + '-')
        )
        if len(candidates) == 1:
            replacements[label] = candidates[0]

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
    pos = text.find(marker)
    if pos < 0:
        return text, False
    return text[:pos] + insertion + '\n' + text[pos:], True


def _insert_latex_preamble_snippet(
    text: str,
    insertion: str,
    command_markers: tuple[str, ...] = (),
) -> tuple[str, bool]:
    """Insert a preamble snippet before the earliest marker use, not only before document."""
    snippet = insertion.strip()
    if snippet and snippet in text:
        return text, False

    positions = []
    for marker in command_markers:
        token = marker if marker.startswith("\\") else "\\" + marker
        pos = text.find(token)
        if pos >= 0:
            positions.append(pos)

    begin_doc = text.find(r"\begin{document}")
    if positions:
        pos = min(positions)
        if begin_doc < 0 or pos < begin_doc:
            line_start = text.rfind("\n", 0, pos) + 1
            return text[:line_start] + insertion + "\n" + text[line_start:], True

    return _insert_before_begin_document(text, insertion)


def patch_fontawesome_legacy_aliases(trans_tex_path):
    """Provide common fontawesome5 aliases used by older templates."""
    import re as _re

    with open(trans_tex_path, encoding='utf-8') as f:
        text = f.read()

    workfolder = os.path.dirname(trans_tex_path)
    sibling_text = ''
    for ext in ('*.sty', '*.cls'):
        for path in glob.glob(os.path.join(workfolder, ext)):
            try:
                with open(path, encoding='utf-8', errors='replace') as f:
                    sibling_text += f.read() + '\n'
            except Exception:
                pass

    text = _re.sub(
        r"% paper-trans fallback for fontawesome5 legacy aliases\r?\n"
        r"(?:\\providecommand\{\\fa[A-Za-z]+\}\{[^\n]*\}\r?\n)+",
        "",
        text,
    )

    aliases = {
        'faFile': ('file', 'F'),
        'faGlobe': ('globe', 'G'),
        'faGithub': ('github', 'GH'),
        'faSearch': ('search', 'S'),
        'faTrophy': ('trophy', 'T'),
        'faDatabase': ('database', 'DB'),
        'faEnvelope': ('envelope', '@'),
        'faEnvelopeO': ('envelope', '@'),
        'faGem': ('gem', '*'),
    }
    combined = text + sibling_text
    needed = []
    for name, (icon, fallback) in aliases.items():
        token = '\\' + name
        if token not in combined:
            continue
        if ('\\newcommand{\\' + name + '}') in text:
            continue
        use_pos = text.find(token)
        provide_pos = text.find('\\providecommand{\\' + name + '}')
        if provide_pos >= 0 and (use_pos < 0 or provide_pos < use_pos):
            continue
        needed.append((name, icon, fallback))
    known_names = {name for name, _icon, _fallback in needed}
    generic_needed = [
        name for name in _ltf.fontawesome_command_names(combined)
        if name not in known_names
    ]
    if not needed and not generic_needed:
        return 0

    lines = [r'% paper-trans fallback for fontawesome5 legacy aliases']
    for name, icon, fallback in needed:
        lines.append(
            '\\providecommand{\\' + name + '}{\\ifcsname faIcon\\endcsname\\faIcon{'
            + icon + '}\\else\\textcircled{' + fallback + '}\\fi}'
        )
    for name in generic_needed:
        lines.append('\\providecommand{\\' + name + '}{\\textbullet}')
    insertion = '\n'.join(lines)
    markers = tuple(name for name, _icon, _fallback in needed) + tuple(generic_needed)
    new_text, ok = _insert_latex_preamble_snippet(text, insertion, markers)
    if not ok:
        return 0
    with open(trans_tex_path, 'w', encoding='utf-8') as f:
        f.write(new_text)
    names = ','.join(
        ['\\' + name for name, _icon, _fallback in needed]
        + ['\\' + name for name in generic_needed]
    )
    print(f"[driver] 🔧 patch_fontawesome_legacy_aliases: 补充 {names} fallback", flush=True)
    return len(needed) + len(generic_needed)


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

    new_text, ok = _insert_latex_preamble_snippet(
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

    if r'\usepackage{enumitem}' in text or r'\usepackage[shortlabels]{enumitem}' in text:
        return 0
    if not _re.search(r'\\begin\{(?:itemize|enumerate|description)\}\[[^\]]+\]', text):
        return 0

    new_text, ok = _insert_before_begin_document(
        text,
        r'% paper-trans fallback for optional list arguments' '\n' r'\usepackage{enumitem}',
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
    """Disable local class/style microtype loads that force pdfTeX-only options."""
    total = 0
    for path in glob.glob(os.path.join(workfolder, '*.cls')) + glob.glob(os.path.join(workfolder, '*.sty')):
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
        print(f"[driver] 🔧 patch_local_microtype_loads: 禁用 {total} 处本地 class/style microtype 加载", flush=True)
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
    import subprocess as _sp
    trans_tex  = os.path.join(workfolder, 'merge_translate_zh.tex')
    orig_tex   = os.path.join(workfolder, 'merge.tex')
    output_pdf = os.path.join(workfolder, 'merge_translate_zh.pdf')
    dest_dir   = os.path.join(ARXIV_CACHE_DIR, arxiv_id_, 'translation')
    dest_pdf   = os.path.join(dest_dir, 'translate_zh.pdf')

    if not os.path.exists(trans_tex) or not os.path.exists(orig_tex):
        return None

    print(f"[driver] 🔧 检测到编译失败但翻译已完成，尝试 verbatim 修补+重编译...", flush=True)
    patch_body_endinput(trans_tex)
    patch_packages_in_documentclass_options(trans_tex)
    fix_label_ref_emdash(trans_tex)
    patch_tcolorbox_opening_options(trans_tex, orig_tex)
    patch_tcolorbox_small_groups(trans_tex)
    patch_fontawesome_legacy_aliases(trans_tex)
    patch_declare_unicode_character_fallback(trans_tex)
    patch_xelatex_compatibility_fallbacks(trans_tex)
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
            engine_cmd = [engine, '-no-pdf', '-interaction=nonstopmode', '-file-line-error', 'merge_translate_zh.tex']
        else:
            engine_cmd = [engine, '-interaction=nonstopmode', '-file-line-error', 'merge_translate_zh.tex']
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

    if check_pdf_integrity(output_pdf):
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
                        ['xelatex', '-no-pdf', '-interaction=nonstopmode', '-file-line-error', 'merge_translate_zh.tex'],
                        cwd=workfolder,
                        timeout=900,
                        stdout=_sp.DEVNULL,
                        stderr=_sp.PIPE,
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


def diagnose_failure(workfolder, arxiv_id_):
    """
    分析编译失败原因，输出结构化诊断行供宿主机捕获。
    格式: PDF_DIAGNOSIS:<json>
    """
    import json as _json, re as _re

    trans_tex = os.path.join(workfolder, 'merge_translate_zh.tex')
    orig_tex  = os.path.join(workfolder, 'merge.tex')
    has_trans = os.path.exists(trans_tex)
    has_orig  = os.path.exists(orig_tex)
    phase     = 'compile' if has_trans else 'translate'

    # ── 1. 插件报错全文（translate 阶段：从收集的 chatbot 消息中提取）──────
    plugin_error_full = ''
    for msg in _plugin_msgs_full:
        if '插件调用出错' in msg or 'Traceback' in msg or 'RuntimeError' in msg \
                or 'Error:' in msg or '找不到' in msg:
            # 还原换行（gpt-academic 把换行替换成了空格，用 4 空格对齐重新断行）
            plugin_error_full = msg.strip()
            break

    # ── 2. 读取 LaTeX 编译日志 ────────────────────────────────────────────
    log_candidates = sorted(glob.glob(os.path.join(workfolder, '*.log')))
    tex_log = None
    for cand in reversed(log_candidates):
        if 'merge_translate_zh' in os.path.basename(cand):
            tex_log = cand
            break
    if not tex_log and log_candidates:
        tex_log = log_candidates[-1]

    all_log_lines: list[str] = []
    errors_raw:    list[str] = []
    tex_log_tail   = ''

    if tex_log and os.path.exists(tex_log):
        with open(tex_log, encoding='utf-8', errors='replace') as f:
            all_log_lines = f.readlines()

        # 找所有错误行，带更多上下文（前2行 + 后10行）
        for i, ln in enumerate(all_log_lines):
            is_err = (
                (ln.startswith('!') and 'TeX capacity exceeded' not in ln)
                or 'Undefined control sequence' in ln
                or 'LaTeX Error:' in ln
                or 'Package Error:' in ln
                or 'Missing number' in ln
                or 'Illegal unit of measure' in ln
                or 'TeX capacity exceeded' in ln
                or '! LaTeX Error' in ln
                or '! Package' in ln
                or '! Missing' in ln
                or '! Extra' in ln
                or '! Emergency' in ln
                or '! Undefined' in ln
                or '! Too many' in ln
            )
            if is_err:
                ctx_start = max(0, i - 2)
                ctx_end   = min(len(all_log_lines), i + 12)
                errors_raw.append(''.join(all_log_lines[ctx_start:ctx_end]).rstrip())
                if len(errors_raw) >= 10:
                    break

        # 日志尾部（最后 60 行，通常包含 Fatal/Emergency stop 位置）
        tail_lines = all_log_lines[-60:] if len(all_log_lines) > 60 else all_log_lines
        tex_log_tail = ''.join(tail_lines).strip()

    # ── 3. 稳定分类 + 可执行重试策略 ──────────────────────────────────────
    diagnostic_text = '\n'.join(errors_raw + [tex_log_tail])
    classified = classify_failure(phase, diagnostic_text, plugin_error_full)

    diag = {
        'arxiv_id':          arxiv_id_,
        'phase':             phase,
        **classified,
        'top_errors':        errors_raw[:8],
        'tex_log_tail':      tex_log_tail,
        'plugin_error_full': plugin_error_full,
        'log_file':          tex_log or '(none)',
        'has_orig_tex':      has_orig,
        'has_trans_tex':     has_trans,
    }
    print(f"PDF_DIAGNOSIS:{_json.dumps(diag, ensure_ascii=False)}", flush=True)
    return diag


def clear_compile_cache(full=False):
    """清除 workfolder 和 translation（full=True 时也清 extract）。"""
    cache_base = os.path.join(ARXIV_CACHE_DIR, arxiv_id)
    targets = ['workfolder', 'translation']
    if full:
        targets += ['extract']
    for subdir in targets:
        d = os.path.join(cache_base, subdir)
        if os.path.exists(d):
            shutil.rmtree(d)
            print(f"[driver] 已清除缓存: {d}", flush=True)


def source_cache_is_valid():
    """检查已下载的 arXiv 源码包是否可复用，避免 --no-cache 重试反复卡在下载断流。"""
    src_tar = os.path.join(ARXIV_CACHE_DIR, arxiv_id, 'e-print', arxiv_id + '.tar')
    if not os.path.exists(src_tar) or os.path.getsize(src_tar) < 1024:
        return False
    try:
        return tarfile.is_tarfile(src_tar)
    except Exception:
        return False


def prefetch_source_cache(max_rounds=3):
    """预下载 arXiv 源码包，代理/直连交替重试，避免插件下载断流后直接失败。"""
    src_dir = os.path.join(ARXIV_CACHE_DIR, arxiv_id, 'e-print')
    src_tar = os.path.join(src_dir, arxiv_id + '.tar')
    tmp_tar = src_tar + '.part'
    url = f'https://arxiv.org/e-print/{arxiv_id}'

    if source_cache_is_valid():
        print(f"[driver] ♻️  arXiv 源码缓存已存在: {src_tar}", flush=True)
        return True

    os.makedirs(src_dir, exist_ok=True)
    plans = [('proxy', True), ('direct', False)]

    for round_idx in range(1, max_rounds + 1):
        for label, use_proxy in plans:
            try:
                if os.path.exists(tmp_tar):
                    os.remove(tmp_tar)
                session = _OrigSession()
                if use_proxy:
                    session.proxies.update(PROXIES_DICT)
                else:
                    session.trust_env = False
                print(f"[driver] ⬇️  预下载 arXiv 源码 ({label}, round={round_idx}): {url}", flush=True)
                with session.get(url, stream=True, timeout=(15, 180)) as r:
                    r.raise_for_status()
                    with open(tmp_tar, 'wb') as f:
                        for chunk in r.iter_content(chunk_size=1024 * 256):
                            if chunk:
                                f.write(chunk)
                if os.path.getsize(tmp_tar) < 1024:
                    raise RuntimeError('downloaded source is too small')
                if not tarfile.is_tarfile(tmp_tar):
                    raise RuntimeError('downloaded source is not a valid tar archive')
                os.replace(tmp_tar, src_tar)
                kb = os.path.getsize(src_tar) // 1024
                print(f"[driver] ✅ arXiv 源码预下载成功: {src_tar} ({kb}KB)", flush=True)
                return True
            except Exception as e:
                print(f"[driver] ⚠️  arXiv 源码预下载失败 ({label}, round={round_idx}): {type(e).__name__}: {e}", flush=True)
                try:
                    if os.path.exists(tmp_tar):
                        os.remove(tmp_tar)
                except Exception:
                    pass
        time.sleep(min(2 * round_idx, 6))

    return False


def prepare_keep_translation_workfolder():
    """
    只有宿主机恢复了 merge_translate_zh.tex、但 workfolder 源码不完整时：
    1. 确保 arXiv 源码包已缓存；
    2. 解压源码并重建 gpt-academic workfolder；
    3. 放回已翻译 tex，并尽量生成 merge.tex 供修补/诊断使用。
    """
    src_tar = os.path.join(ARXIV_CACHE_DIR, arxiv_id, 'e-print', arxiv_id + '.tar')
    extract_dst = os.path.join(ARXIV_CACHE_DIR, arxiv_id, 'extract')

    if not os.path.exists(TRANSLATE_TEX):
        return False
    try:
        with open(TRANSLATE_TEX, 'rb') as f:
            translated_tex = f.read()
    except Exception as e:
        print(f"[driver] ⚠️  读取翻译 tex 失败，无法恢复 workfolder: {e}", flush=True)
        return False

    if not (source_cache_is_valid() or prefetch_source_cache()):
        print(f"[driver] ⚠️  源码缓存不可用，无法恢复 workfolder", flush=True)
        return False

    try:
        from toolbox import extract_archive
        from crazy_functions.Latex_Function import (
            descend_to_extracted_folder_if_exist,
            move_project,
        )
        from crazy_functions.latex_fns import latex_toolbox as _lt_local

        if os.path.exists(extract_dst):
            shutil.rmtree(extract_dst)
        os.makedirs(extract_dst, exist_ok=True)
        extract_archive(file_path=src_tar, dest_dir=extract_dst)

        project_folder = descend_to_extracted_folder_if_exist(extract_dst)
        os.makedirs(project_folder, exist_ok=True)
        # 也放一份到 extract 侧，若后续退回插件编译，move_project 后仍可跳过 GPT。
        with open(os.path.join(project_folder, 'merge_translate_zh.tex'), 'wb') as f:
            f.write(translated_tex)

        workfolder = move_project(project_folder, arxiv_id)
        with open(os.path.join(workfolder, 'merge_translate_zh.tex'), 'wb') as f:
            f.write(translated_tex)

        file_manifest = [
            f for f in glob.glob(f'{workfolder}/**/*.tex', recursive=True)
            if not os.path.basename(f).startswith('merge')
        ]
        if file_manifest:
            maintex = _lt_local.find_main_tex_file(file_manifest, 'translate_zh')
            with open(maintex, 'r', encoding='utf-8', errors='replace') as f:
                merged_content = _lt_local.merge_tex_files(workfolder, f.read(), 'translate_zh')
            with open(os.path.join(workfolder, 'merge.tex'), 'w', encoding='utf-8', errors='replace') as f:
                f.write(merged_content)
            print(f"[driver] ✅ 已恢复完整 workfolder 并生成 merge.tex: {workfolder}", flush=True)
        else:
            print(f"[driver] ⚠️  源码解压后未找到 tex 文件，仅恢复中文 tex: {workfolder}", flush=True)
        return True
    except Exception as e:
        print(f"[driver] ⚠️  恢复 keep-translation workfolder 失败: {type(e).__name__}: {e}", flush=True)
        return False


# ── 主逻辑：首次 + 重试 ────────────────────────────────────────────────────────
result_pdf = None

WORKFOLDER = os.path.join(ARXIV_CACHE_DIR, arxiv_id, 'workfolder')
TRANSLATE_TEX = os.path.join(WORKFOLDER, 'merge_translate_zh.tex')
ORIG_TEX = os.path.join(WORKFOLDER, 'merge.tex')

if keep_translation and os.path.exists(TRANSLATE_TEX) and os.path.exists(ORIG_TEX):
    # 保留已有 GPT 翻译，只重跑编译。绕开插件生成器，避免它重建 workfolder 后删掉已恢复的中文 tex。
    print(f"[driver] ♻️  复用已有翻译缓存: {TRANSLATE_TEX}（直接重编译，跳过 GPT 翻译）", flush=True)
    result_pdf = patch_and_recompile(WORKFOLDER, arxiv_id)
else:
    if keep_translation and os.path.exists(TRANSLATE_TEX):
        # 只有中文 tex、没有完整源码 workfolder 时，先重建 workfolder 并直编译。
        print(f"[driver] ♻️  发现翻译缓存但 workfolder 不完整，尝试恢复源码后直编译", flush=True)
        if prepare_keep_translation_workfolder():
            result_pdf = patch_and_recompile(WORKFOLDER, arxiv_id)
        if result_pdf:
            actual_no_cache = False
        else:
            print(f"[driver] ⚠️  直编译未成功，退回插件路径（仍尝试复用翻译 tex）", flush=True)
        actual_no_cache = False
    elif no_cache:
        # 强制重新翻译/编译；若源码包已经有效缓存，则复用源码，避免 arXiv 下载断流导致无法进入编译阶段。
        clear_compile_cache(full=True)
        if source_cache_is_valid() or prefetch_source_cache():
            print(f"[driver] ♻️  复用已下载源码缓存（仍会重新翻译/编译）", flush=True)
            actual_no_cache = False
        else:
            actual_no_cache = True
    else:
        if not source_cache_is_valid():
            prefetch_source_cache()
        actual_no_cache = False

    if not result_pdf:
        for attempt in range(1, max_retries + 2):   # 最多3次（1次首次 + 2次重试）
            if attempt == 1:
                result_pdf = run_translation(actual_no_cache, attempt)
            else:
                # 重试：强制清缓存，重新翻译
                print(f"\n[driver] ══ 第 {attempt} 次重试（清除缓存后重新翻译）══", flush=True)
                clear_compile_cache()
                result_pdf = run_translation(True, attempt)

            if result_pdf:
                break
            if attempt <= max_retries:
                print(f"[driver] 等待 5s 后重试...", flush=True)
                time.sleep(5)

    # ── Fallback：翻译完成但编译失败时，修补 verbatim 环境后重编译 ──────────────
    if not result_pdf:
        result_pdf = patch_and_recompile(WORKFOLDER, arxiv_id)

# ── 输出结果 ────────────────────────────────────────────────────────────────
if result_pdf:
    print(f"RESULT:SUCCESS:{result_pdf}", flush=True)
    # gpt-academic may leave non-daemon worker threads alive after all output
    # has been produced.  This file is a one-shot subprocess, so waiting for
    # those idle workers only makes the host wrapper appear hung.
    os._exit(0)
else:
    workfolder_ = os.path.join(ARXIV_CACHE_DIR, arxiv_id, 'workfolder')
    diagnose_failure(workfolder_, arxiv_id)
    print(f"RESULT:ERROR:所有 {max_retries+1} 次尝试均未生成 PDF", flush=True)
    os._exit(1)
