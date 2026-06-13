#!/usr/bin/env python3
"""
在 gpt-academic Docker 容器内运行的全文翻译驱动脚本
用法: python3 full_translate_driver.py <arxiv_id> [--no-cache] [--retries N]
输出: RESULT:SUCCESS:<pdf_path>  或  RESULT:ERROR:<msg>
"""
import sys, os, glob, time, shutil, tarfile

sys.path.insert(0, '/gpt')
os.chdir('/gpt')

arxiv_id        = sys.argv[1] if len(sys.argv) > 1 else None
no_cache        = "--no-cache" in sys.argv
keep_translation = "--keep-translation" in sys.argv   # 保留已有翻译，只重跑编译
max_retries = 0   # 只翻译一次，不重试

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
class _PatchedSession(_OrigSession):
    def __init__(self):
        super().__init__()
        self.proxies.update(PROXIES_DICT)
_req.Session = _PatchedSession

# ── Patch compile_latex_with_timeout：用进程组 kill，防止 pdflatex 变孤儿进程 ─────
import subprocess as _subprocess
import os as _os
import signal as _signal

def _patched_compile_with_timeout(command, cwd, timeout=90):
    """修复版：shell=True + 进程组 kill，确保 pdflatex 子进程也被杀掉。"""
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
            f = s.group(1)
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
                    probe = f if f.endswith('.tex') else f + '.tex'
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
    protected_envs = {
        "figure", "figure*", "table", "table*", "tabular", "tabular*",
        "tabularx", "longtable", "algorithm", "algorithmic", "algorithm2e",
        "lstlisting", "verbatim", "Verbatim", "minted", "equation",
        "equation*", "align", "align*", "multline", "multline*", "gather",
        "gather*", "tikzpicture", "minipage", "minipage*", "thebibliography",
    }
    soft_text_envs = {
        "tabular", "tabular*", "tabularx", "longtable", "array",
        "algorithmic", "algorithmic*", "algorithm2e",
    }
    hard_protected_envs = protected_envs - soft_text_envs
    tracked_envs = protected_envs | soft_text_envs | {"center"}
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

    def _append(nodes, text: str, preserve: bool):
        if not text:
            return
        if nodes and nodes[-1].preserve == preserve:
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
            if env in tracked_envs:
                env_stack.append(env)
        for env in ends:
            if env in tracked_envs:
                if env in env_stack:
                    pos = len(env_stack) - 1 - env_stack[::-1].index(env)
                    env_stack = env_stack[:pos]
                elif env_stack:
                    env_stack.pop()
        return env_stack

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
            in_soft_env = active_env in soft_text_envs
            hard_active = any(env in hard_protected_envs for env in state["env_stack"])
            begins = _re.findall(r"\\begin\{([^}]+)\}", line)
            ends = _re.findall(r"\\end\{([^}]+)\}", line)
            structural_line = any(env in tracked_envs for env in begins + ends)

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

    def _patched_split(self, txt, project_folder, opts):
        res = _orig_split(self, txt, project_folder, opts)
        original_transform = sum(1 for node in self.nodes if not node.preserve)
        original_chars = sum(len(node.string) for node in self.nodes if not node.preserve)

        expanded = []
        state = {"in_document": False, "env_stack": []}
        for node in self.nodes:
            if not node.preserve:
                _append(expanded, node.string, False)
                if r"\begin{document}" in node.string:
                    state["in_document"] = True
                if r"\end{document}" in node.string:
                    state["in_document"] = False
                state["env_stack"] = _update_env_stack(node.string, state["env_stack"])
                continue
            parts = _split_preserved_text(node.string, state)
            for part in parts:
                _append(expanded, part.string, part.preserve)

        _recompute_ranges(expanded)
        self.nodes = expanded
        self.sp = [node.string for node in expanded if not node.preserve]

        added = len(self.sp) - original_transform
        added_chars = sum(len(node.string) for node in expanded if not node.preserve) - original_chars
        print(
            f"[driver] ✅ latex splitter expanded prose chunks: "
            f"{original_transform} -> {len(self.sp)} (chars +{max(0, added_chars)})",
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
    print("[driver] ✅ LatexPaperSplit 已 patch（普通正文保守扩展翻译）", flush=True)


_patch_latex_translation_splitter()

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

    protected_envs = {
        "figure", "figure*", "table", "table*", "tabular", "tabular*",
        "tabularx", "longtable", "algorithm", "algorithmic", "algorithm2e",
        "lstlisting", "verbatim", "Verbatim", "minted", "equation",
        "equation*", "align", "align*", "multline", "multline*", "gather",
        "gather*", "tikzpicture", "minipage", "minipage*", "thebibliography",
    }
    soft_text_envs = {
        "tabular", "tabular*", "tabularx", "longtable", "array",
        "algorithmic", "algorithmic*", "algorithm2e",
    }
    hard_protected_envs = protected_envs - soft_text_envs
    tracked_envs = protected_envs | soft_text_envs
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
        in_soft_env = active_env in soft_text_envs
        hard_active = any(env in hard_protected_envs for env in env_stack)
        structural_line = any(env in tracked_envs for env in begins + ends)
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
            if env in tracked_envs:
                env_stack.append(env)
        for env in ends:
            if env in tracked_envs:
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
        (long_count >= 10)
        or (very_long_english >= 3)
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


def latex_compile_health_ok(workfolder: str, arxiv_id_: str) -> bool:
    """Reject PDFs that compiled but still have unresolved TeX/cite/ref issues."""
    import re as _re

    log_path = os.path.join(workfolder, "merge_translate_zh.log")
    if not os.path.exists(log_path):
        print(f"[driver] ⚠️  找不到编译日志，跳过健康检查: {log_path}", flush=True)
        return True
    with open(log_path, encoding="utf-8", errors="replace") as f:
        log = f.read()

    checks = [
        ("undefined control sequence", r"Undefined control sequence"),
        ("missing number", r"Missing number, treated as zero"),
        ("undefined citation", r"Citation .* undefined"),
        ("undefined reference", r"Reference .* undefined"),
        ("undefined references", r"There were undefined references"),
        ("rerun labels", r"Label\(s\) may have changed"),
        ("rerun cross-references", r"Rerun to get cross-references right"),
        ("natbib undefined", r"Package natbib Warning: .* undefined"),
    ]
    failures = [name for name, pattern in checks if _re.search(pattern, log)]
    if failures:
        print(
            f"[driver] ❌ 编译健康检查失败: {arxiv_id_} "
            f"issues={', '.join(failures)}",
            flush=True,
        )
        for m in _re.finditer(
            r".{0,120}(Undefined control sequence|Missing number, treated as zero|"
            r"Citation .* undefined|"
            r"Reference .* undefined|There were undefined references|"
            r"Label\(s\) may have changed|Rerun to get cross-references right|"
            r"Package natbib Warning: .* undefined).{0,160}",
            log,
            flags=_re.DOTALL,
        ):
            sample = " ".join(m.group(0).split())
            print(f"[driver]    log: {sample[:260]}", flush=True)
            break
        return False

    print(f"[driver] ✅ 编译健康检查通过: {arxiv_id_}", flush=True)
    return True


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
    if os.path.exists(candidate) and os.path.getsize(candidate) > 50 * 1024:
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
        if os.path.exists(fp) and os.path.getsize(fp) > 50 * 1024:
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
        if os.path.getsize(best) > 50 * 1024:
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
    import re as _re

    with open(trans_tex_path, encoding='utf-8') as f:
        text = f.read()

    macro_names = set()
    for m in _re.finditer(
        r'\\(?:newcommand|renewcommand|providecommand)\s*\{\\([A-Za-z@]+)\}'
        r'(?:\[(\d+)\])?',
        text,
    ):
        arg_count = m.group(2)
        if arg_count in (None, '0'):
            macro_names.add(m.group(1))

    if not macro_names:
        return 0

    definition_spans = []
    for name in macro_names:
        for m in _re.finditer(
            r'\\(?:newcommand|renewcommand|providecommand)\s*\{\\'
            + _re.escape(name) + r'\}',
            text,
        ):
            definition_spans.append((m.start(), m.end()))

    def _in_definition(pos):
        return any(start <= pos < end for start, end in definition_spans)

    names_pat = '|'.join(_re.escape(n) for n in sorted(macro_names, key=len, reverse=True))
    pattern_slash_cjk = _re.compile(r'\\(' + names_pat + r')\\(?=[\u4e00-\u9fff])')
    pattern_glued = _re.compile(r'\\(' + names_pat + r')(?![A-Za-z@{])(?=[\u4e00-\u9fffA-Za-z])')

    total = 0

    def _replace(m):
        nonlocal total
        if _in_definition(m.start()):
            return m.group(0)
        total += 1
        return '\\' + m.group(1) + '{}'

    new_text = pattern_slash_cjk.sub(_replace, text)
    new_text = pattern_glued.sub(_replace, new_text)
    if total:
        with open(trans_tex_path, 'w', encoding='utf-8') as f:
            f.write(new_text)
        print(f"[driver] 🔧 patch_custom_macro_cjk_glue: 为 {total} 处自定义宏补充分隔符", flush=True)
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


def _insert_before_begin_document(text: str, insertion: str) -> tuple[str, bool]:
    marker = r'\begin{document}'
    pos = text.find(marker)
    if pos < 0:
        return text, False
    return text[:pos] + insertion + '\n' + text[pos:], True


def patch_fontawesome_legacy_aliases(trans_tex_path):
    """Provide common fontawesome5 aliases used by older templates."""
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

    if r'\faGlobe' not in (text + sibling_text) or r'\newcommand{\faGlobe}' in text:
        return 0

    insertion = (
        r'% paper-trans fallback for fontawesome5 legacy aliases' '\n'
        r'\providecommand{\faGlobe}{\ifcsname faIcon\endcsname\faIcon{globe}\else\textcircled{G}\fi}'
    )
    new_text, ok = _insert_before_begin_document(text, insertion)
    if not ok:
        return 0
    with open(trans_tex_path, 'w', encoding='utf-8') as f:
        f.write(new_text)
    print("[driver] 🔧 patch_fontawesome_legacy_aliases: 补充 \\faGlobe fallback", flush=True)
    return 1


def clean_latex_intermediates(workfolder):
    """Remove stale LaTeX/BibTeX intermediates before deterministic recompiles."""
    removed = 0
    for ext in ('aux', 'bbl', 'blg', 'log', 'out', 'toc', 'ptc', 'fls', 'fdb_latexmk'):
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


def synthesize_bbl_from_tex(workfolder, trans_tex_path):
    """Create a minimal aux from citation commands and run BibTeX before XeLaTeX."""
    import re as _re
    import subprocess as _sp

    with open(trans_tex_path, encoding='utf-8', errors='replace') as f:
        text = f.read()

    bibdata = _re.findall(r'\\bibliography\{([^{}]+)\}', text)
    if not bibdata:
        return False
    bibstyle = _re.findall(r'\\bibliographystyle\{([^{}]+)\}', text)
    style = bibstyle[-1] if bibstyle else 'plainnat'
    data = bibdata[-1]

    cite_re = _re.compile(
        r'\\(?:citep|citet|citealt|citeauthor|citeyearpar|cite)'
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
    bbl_path = os.path.join(workfolder, 'merge_translate_zh.bbl')
    ok = r.returncode == 0 and os.path.exists(bbl_path) and os.path.getsize(bbl_path) > 0
    if ok:
        print(f"[driver] 🔧 synthesize_bbl_from_tex: 预生成 bbl ({len(keys)} citations)", flush=True)
    return ok


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

    if total:
        with open(trans_tex_path, 'w', encoding='utf-8') as f:
            f.write(text)
        print(f"[driver] 🔧 patch_microtype_for_xelatex: 禁用 {total} 处 microtype 高风险特性", flush=True)
    return total


def patch_local_microtype_loads(workfolder):
    """Disable local class/style microtype loads that force pdfTeX-only options."""
    import re as _re

    total = 0
    pattern = _re.compile(r'\\(?:AtEndOfClass\{)?\\RequirePackage(?:\[[^\]]*\])?\{microtype\}\}?')
    for path in glob.glob(os.path.join(workfolder, '*.cls')) + glob.glob(os.path.join(workfolder, '*.sty')):
        try:
            with open(path, encoding='utf-8', errors='replace') as f:
                text = f.read()
        except Exception:
            continue
        if 'microtype' not in text:
            continue
        new_text, count = pattern.subn('% paper-trans: local microtype load disabled for XeLaTeX', text)
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

    VERBATIM_ENVS = [
        'tcblisting', 'lstlisting', 'verbatim', 'Verbatim', 'minted',
        *_discover_tcb_listing_envs(orig),
        *_discover_tcb_listing_envs(trans),
    ]
    VERBATIM_ENVS = sorted(set(VERBATIM_ENVS))

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
    fix_label_ref_emdash(trans_tex)
    patch_tcolorbox_small_groups(trans_tex)
    patch_fontawesome_legacy_aliases(trans_tex)
    patch_enumitem_for_optional_lists(trans_tex)
    patch_microtype_for_xelatex(trans_tex)
    patch_local_microtype_loads(workfolder)
    patch_local_nvidia_font_maps(workfolder)
    patch_long_citation_lists(trans_tex)
    n = patch_verbatim_envs(trans_tex, orig_tex)
    print(f"[driver] 🔧 修补了 {n} 个 verbatim 类环境块", flush=True)
    patch_unbalanced_groups_in_tcolorboxes(trans_tex)
    patch_custom_macro_cjk_glue(trans_tex)
    patch_stray_text_word_commands(trans_tex)
    patch_algorithmic_command_glue(trans_tex)
    patch_undefined_unique_ref_labels(trans_tex)
    clean_latex_intermediates(workfolder)
    synthesized_bbl = synthesize_bbl_from_tex(workfolder, trans_tex)

    try:
        cmds = [
            ['xelatex', '-interaction=nonstopmode', '-file-line-error', 'merge_translate_zh.tex'],
            ['xelatex', '-interaction=nonstopmode', '-file-line-error', 'merge_translate_zh.tex'],
            ['xelatex', '-interaction=nonstopmode', '-file-line-error', 'merge_translate_zh.tex'],
        ] if synthesized_bbl else [
            ['xelatex', '-interaction=nonstopmode', '-file-line-error', 'merge_translate_zh.tex'],
            ['bibtex', 'merge_translate_zh'],
            ['xelatex', '-interaction=nonstopmode', '-file-line-error', 'merge_translate_zh.tex'],
            ['xelatex', '-interaction=nonstopmode', '-file-line-error', 'merge_translate_zh.tex'],
        ]
        for cmd in cmds:
            _sp.run(
                cmd, cwd=workfolder, timeout=300,
                stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
            )
    except Exception as e:
        print(f"[driver] ⚠️  LaTeX/BibTeX 执行异常: {e}", flush=True)
        return None

    if os.path.exists(output_pdf) and os.path.getsize(output_pdf) > 50 * 1024:
        kb = os.path.getsize(output_pdf) // 1024
        if not translation_quality_ok(workfolder, arxiv_id_):
            return None
        if not latex_compile_health_ok(workfolder, arxiv_id_):
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

    # ── 3. 错误类型判断 ───────────────────────────────────────────────────
    all_text = '\n'.join(errors_raw)
    category   = 'unknown'
    suggestion = '查看下方详细错误信息'

    if phase == 'translate':
        # translate 阶段：从插件报错里提取具体原因
        if '找不到' in plugin_error_full and 'Tex源文件缺失' in plugin_error_full:
            m = _re.search(r'找不到([^\n，]+)[，,]', plugin_error_full)
            missing = m.group(1).strip() if m else '未知文件'
            category   = f'missing_input_file:{_os.path.basename(missing)}'
            suggestion = (f'\\input 引用的文件不在项目目录中: {missing}\n'
                          f'如果是系统级 TeX 文件（如 glyphtounicode），已由驱动 patch 自动跳过。\n'
                          f'如果是项目自定义文件，需手动将其加入 arxiv 源码包后重试。')
        elif 'RuntimeError' in plugin_error_full:
            m = _re.search(r'RuntimeError:\s*(.+)', plugin_error_full)
            msg = m.group(1).strip()[:120] if m else plugin_error_full[:120]
            category   = f'runtime_error'
            suggestion = f'插件内部 RuntimeError: {msg}'
        elif 'Traceback' in plugin_error_full or 'Error:' in plugin_error_full:
            category   = 'plugin_exception'
            suggestion = '插件抛出异常，见下方完整 Traceback'
        elif not plugin_error_full:
            suggestion = '无插件报错消息，可能是网络/API 超时，或驱动脚本被意外终止'
    else:
        # compile 阶段：从 LaTeX log 判断
        if 'input stack size' in all_text or ('normalsize' in all_text and '->' in all_text):
            category   = 'normalsize_recursion'
            suggestion = ('preamble 中 \\normalsize 自引用递归。\n'
                          '修复: 在 merge_translate_zh.tex 中把\n'
                          '  \\expandafter\\def\\expandafter\\normalsize\\expandafter{\\normalsize ...}\n'
                          '替换为:\n'
                          '  \\let\\normalsizesaved\\normalsize\n'
                          '  \\def\\normalsize{\\normalsizesaved ...}')
        elif 'pgfkeys Error' in all_text or ('tcblisting' in all_text and 'Missing $' in all_text):
            category   = 'tcblisting_translated'
            suggestion = ('tcblisting/lstlisting 内代码被 GPT 翻译，导致特殊字符破坏编译。\n'
                          '修复: 运行 patch_and_recompile 或手动从 merge.tex 还原 verbatim 块')
        elif 'File' in all_text and 'not found' in all_text:
            m = _re.search(r"File '([^']+)' not found", all_text)
            pkg = m.group(1) if m else '未知'
            category   = f'missing_package:{pkg}'
            suggestion = (f'缺少 LaTeX 包: {pkg}。\n'
                          f'修复: 在容器内 tlmgr install 或在 texmf 目录创建 stub .sty')
        elif ('twocolumn' in all_text and 'ended by' in all_text) or \
             'begin{document} ended by' in all_text:
            category   = 'missing_bracket'
            suggestion = ('\\twocolumn[ 缺少闭合 ]，GPT 翻译时被删除。\n'
                          '修复: 在 merge_translate_zh.tex 中 \\printAffiliationsAndNotice 前补回 ]\n'
                          '参考 merge.tex 对应位置')
        elif 'Emergency stop' in all_text or 'Missing }' in all_text:
            category   = 'group_mismatch'
            suggestion = '大括号/环境不匹配导致 Emergency stop，通常是 GPT 翻译破坏了嵌套结构'
        elif 'Undefined control sequence' in all_text:
            m = _re.search(r'\\([A-Za-z@]+)',
                           all_text.split('Undefined control sequence')[1][:80])
            cmd = ('\\' + m.group(1)) if m else '未知'
            category   = f'undefined_command:{cmd}'
            suggestion = f'未定义命令 {cmd}，可能是自定义宏未翻译/丢失'
        elif errors_raw:
            # 有错误行但未匹配具体类型
            first_line = errors_raw[0].splitlines()[0][:80]
            category   = f'latex_error:{first_line}'
            suggestion = '见下方 LaTeX 错误详情'

    diag = {
        'arxiv_id':          arxiv_id_,
        'phase':             phase,
        'category':          category,
        'suggestion':        suggestion,
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
else:
    workfolder_ = os.path.join(ARXIV_CACHE_DIR, arxiv_id, 'workfolder')
    diagnose_failure(workfolder_, arxiv_id)
    print(f"RESULT:ERROR:所有 {max_retries+1} 次尝试均未生成 PDF", flush=True)
    sys.exit(1)
