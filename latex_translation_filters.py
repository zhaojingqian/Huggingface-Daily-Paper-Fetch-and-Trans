#!/usr/bin/env python3
"""
Shared LaTeX filtering policy for the Chinese PDF translation pipeline.

The driver uses these predicates in three places: splitter expansion, quality
checks, and fallback restoration. Keeping the policy here prevents future fixes
from being hard-coded in only one of those paths.
"""

import os
import re
from typing import Iterable, List, Optional, Set, Tuple


SOFT_TEXT_ENVS = frozenset({
    "tabular", "tabular*", "tabularx", "longtable", "array",
    "algorithmic", "algorithmic*", "algorithm2e",
})

BASE_HARD_PROTECTED_ENVS = frozenset({
    "figure", "figure*", "table", "table*", "algorithm",
    "lstlisting", "verbatim", "Verbatim", "minted", "equation",
    "equation*", "align", "align*", "multline", "multline*", "gather",
    "gather*", "tikzpicture", "minipage", "minipage*", "thebibliography",
})

BASE_VERBATIM_RESTORE_ENVS = frozenset({
    "tcblisting", "lstlisting", "verbatim", "Verbatim", "minted",
})

# Patterns for custom code, prompt, transcript, CLI/GUI, and trajectory blocks.
# These are treated as hard-protected because translating them tends to damage
# commands or benchmark traces, and their English text is usually intentional.
DYNAMIC_HARD_ENV_RE = re.compile(
    r"(?i)("
    r"(?:^|[_-])(?:cli|gui|trace|traj|trajectory|transcript|console|terminal|"
    r"shell|prompt|code|log|verbatim|listing|minted)(?:$|[_-])"
    r"|(?:prompt|code|trace|traj|trajectory|transcript|console|terminal|log)(?:box|block)$"
    r"|(?:cli|gui|fail)mode$"
    r"|^trajact"
    r"|(?:listing|verbatim|transcript|trajectory|trace|prompt|codeblock)$"
    r")"
)


def _split_env_var(name: str) -> Set[str]:
    raw = os.environ.get(name, "")
    if not raw.strip():
        return set()
    return {item.strip() for item in re.split(r"[,\s]+", raw) if item.strip()}


def soft_text_envs() -> Set[str]:
    return set(SOFT_TEXT_ENVS) | _split_env_var("PAPER_TRANS_EXTRA_SOFT_ENVS")


def hard_protected_envs() -> Set[str]:
    hard = set(BASE_HARD_PROTECTED_ENVS)
    hard.update(_split_env_var("PAPER_TRANS_EXTRA_HARD_ENVS"))
    # Existing one-off fixes now live as policy defaults, but can also be
    # extended through PAPER_TRANS_EXTRA_HARD_ENVS for future papers.
    hard.update({"climode", "guimode", "failmode"})
    return hard


def is_soft_text_env(env: Optional[str]) -> bool:
    return bool(env) and env in soft_text_envs()


def is_dynamic_hard_env(env: Optional[str]) -> bool:
    if not env or is_soft_text_env(env):
        return False
    return bool(DYNAMIC_HARD_ENV_RE.search(env))


def is_hard_protected_env(env: Optional[str]) -> bool:
    if not env or is_soft_text_env(env):
        return False
    return env in hard_protected_envs() or is_dynamic_hard_env(env)


def is_tracked_env(env: Optional[str]) -> bool:
    return is_soft_text_env(env) or is_hard_protected_env(env)


def tracked_envs() -> Set[str]:
    return soft_text_envs() | hard_protected_envs()


def discover_tcb_listing_envs(content: str) -> Set[str]:
    envs = set()
    for pat in (
        r"\\newtcblisting\s*\{([A-Za-z][A-Za-z0-9*_-]*)\}",
        r"\\DeclareTCBListing\s*\{([A-Za-z][A-Za-z0-9*_-]*)\}",
    ):
        envs.update(re.findall(pat, content))
    return envs


def discover_envs(content: str) -> Set[str]:
    return set(re.findall(r"\\begin\{([^}]+)\}", content or ""))


def verbatim_restore_envs(*contents: str, extra_envs: Iterable[str] = ()) -> Set[str]:
    envs = set(BASE_VERBATIM_RESTORE_ENVS)
    envs.update(_split_env_var("PAPER_TRANS_EXTRA_RESTORE_ENVS"))
    envs.update(extra_envs)
    for content in contents:
        envs.update(discover_tcb_listing_envs(content))
        envs.update(env for env in discover_envs(content) if is_dynamic_hard_env(env))
    return envs


LLM_ARTIFACT_PATTERNS = (
    re.compile(
        r"\n\\section\{引言\}\s*\n\s*在过去的几十年中.*?"
        r"我们希望本工作能够为相关领域提供新的思路和工具。\s*",
        re.DOTALL,
    ),
    re.compile(
        r"Please provide the section from the English academic paper that you "
        r"would like me to translate into Chinese\.",
        re.IGNORECASE,
    ),
    re.compile(r"Please provide[^。\n]*?(?:Chinese|中文)[^。\n]*(?:\.|。)?", re.IGNORECASE),
    re.compile(r"Please provide the text you would like me to translate\.", re.IGNORECASE),
    re.compile(r"Please provide the text you want me to translate\.", re.IGNORECASE),
    re.compile(r"Please provide the English text you want me to translate\.", re.IGNORECASE),
    re.compile(r"Please provide the English section you want me to translate\.", re.IGNORECASE),
    re.compile(r"\(?Please provide the section you want translated\.\)?", re.IGNORECASE),
    re.compile(r"\.?\s*Please provide the section you would like me to translate\.", re.IGNORECASE),
    re.compile(r"\.?\s*Please provide the English text you want to translate\.", re.IGNORECASE),
    re.compile(r"\.?\s*Please provide the English academic paper section for translation\.", re.IGNORECASE),
    re.compile(r"\.?\s*Please provide the English academic paper section you want translated\.", re.IGNORECASE),
    re.compile(r"\.?\s*Please share the text you want translated\.", re.IGNORECASE),
    re.compile(r"\.?\s*Please share the section you want to be translated\.", re.IGNORECASE),
    re.compile(r"If you provide the section you want translated, I can proceed\.", re.IGNORECASE),
    re.compile(r"If you provide the English academic paper section, I will translate it for you accordingly\.", re.IGNORECASE),
    re.compile(r"If you have any specific section you want translated, please provide the text\.", re.IGNORECASE),
    re.compile(r"If you provide the specific text, I can translate it accordingly\.", re.IGNORECASE),
    re.compile(r"If you provide the English academic paper section, I will translate it accordingly\.", re.IGNORECASE),
    re.compile(r"as per your instructions\.\s*(?=Please provide|$)", re.IGNORECASE),
    re.compile(r"Certainly!\s*(?=[\u4e00-\u9fff])", re.IGNORECASE),
    re.compile(r"Below is the translated text(?: in Chinese)?:?", re.IGNORECASE),
    re.compile(r"Below is a section from an English academic paper, translated into Chinese(?:\.|:)?", re.IGNORECASE),
    re.compile(r"Below is the translation of your provided English academic paper section into Chinese\.?", re.IGNORECASE),
    re.compile(r"Below is the section you provided translated into Chinese\.?", re.IGNORECASE),
    re.compile(r"Below is the translated Chinese text of the provided English academic paper section(?:\.|:)?", re.IGNORECASE),
    re.compile(r"Below is the Chinese translation of the provided English academic paper section[^:\n]*(?:\.|:)?", re.IGNORECASE),
    re.compile(r"Below is the English academic paper section for translation\.\s*", re.IGNORECASE),
    re.compile(r"Below is an English academic paper section, translated into Chinese[^:\n]*(?:\.|:)?", re.IGNORECASE),
    re.compile(r"LaTeX commands and equations are kept unchanged\.?", re.IGNORECASE),
    re.compile(r"All LaTeX commands and equations have been kept intact\.?", re.IGNORECASE),
    re.compile(r"LaTeX commands have been kept intact as requested\.?", re.IGNORECASE),
    re.compile(r"请提供您需要翻译的英文学术论文部分内容。"),
    re.compile(r"请提供需要翻译的英文学术论文部分内容。"),
    re.compile(r"请提供您需要翻译的英文学术论文部分。"),
    re.compile(r"请提供您需要翻译的英文论文部分内容。"),
    re.compile(r"请提供需要翻译的英文论文部分内容。"),
    re.compile(r"请提供您需要翻译的具体英文内容。"),
    re.compile(r"请提供需要翻译的具体英文内容。"),
    re.compile(r"请提供需要翻译的具体英文段落内容。"),
    re.compile(r"好的，请提供需要翻译的英文部分。"),
    re.compile(r"(?:好的|当然|可以)，?请提供您?希望(?:我)?翻译的英文(?:学术论文)?(?:部分|内容|段落)?。?"),
    re.compile(r"(?:好的|当然|可以)，?请提供您?需要翻译的英文(?:学术论文)?(?:部分|内容|段落)?。?"),
    re.compile(r"请提供您?希望(?:我)?翻译的英文(?:学术论文)?(?:部分|内容|段落)?。?"),
    re.compile(r"请提供您?要翻译的英文(?:学术论文)?(?:部分|内容|段落)?。?"),
    re.compile(r"请提供[^。\n]*?(?:论文|文本)[^。\n]*?(?:。|$)"),
    re.compile(r"下面是一篇英文学术论文的部分内容，翻译成中文如下。请注意保留所有的latex命令不变："),
    re.compile(r"抱歉，您提供的文本仅包含[^。\n]*?(?:。|$)"),
    re.compile(r"机器学习在过去几十年中取得了显著的进展\\cite\{smith2020advances\}。.*?\\cite\{lecun2015deep\}。", re.DOTALL),
    re.compile(r"本文提出了一种基于深度学习的新方法，用于图像分类任务。.*?探索其在其他视觉任务中的应用潜力。", re.DOTALL),
    re.compile(r"近年来，深度学习在图像识别、自然语言处理等领域取得了显著进展.*?实验部分将在多个公开数据集上验证所提方法的有效性，并与现有主流方法进行对比分析。", re.DOTALL),
    re.compile(r"\\section\{引言\}\s*在过去的几十年里，机器学习已经成为人工智能领域的核心方法之一.*?提出若干有待解决的挑战。", re.DOTALL),
    re.compile(r"\\section\{相关工作\}\s*近年来，深度学习在图像识别、自然语言处理等领域取得了显著进展.*?半监督分类的准确率。", re.DOTALL),
    re.compile(r"在现代计算机科学的发展过程中，机器学习技术得到了广泛的应用。.*?\\cite\{mnih2015human\}。", re.DOTALL),
    re.compile(r"近年来，迁移学习作为一种有效的方法被提出.*?\\cite\{mnih2015human\}。", re.DOTALL),
    re.compile(r"为了解决数据不足的问题，许多研究关注于半监督学习和无监督学习方法\\cite\{lecun2015deep\}。此外，迁移学习.*?仍然是一个挑战。", re.DOTALL),
)


def _extra_artifact_patterns() -> List[object]:
    raw = os.environ.get("PAPER_TRANS_EXTRA_LLM_ARTIFACT_PATTERNS", "")
    if not raw.strip():
        return []
    patterns = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        patterns.append(re.compile(line, re.DOTALL))
    return patterns


def strip_llm_translation_artifacts(text: str) -> Tuple[str, int]:
    new_text = text
    total = 0
    for pattern in (*LLM_ARTIFACT_PATTERNS, *_extra_artifact_patterns()):
        new_text, count = pattern.subn("", new_text)
        total += count
    return new_text, total


def _insert_latex_preamble_snippet(
    text: str,
    insertion: str,
    command_markers: Iterable[str] = (),
) -> Tuple[str, bool]:
    snippet = insertion.strip()
    if not snippet or snippet in text:
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

    if begin_doc >= 0:
        return text[:begin_doc] + insertion + "\n" + text[begin_doc:], True
    return text + "\n" + insertion + "\n", True


def _latex_command_defined(text: str, name: str) -> bool:
    pattern = re.compile(
        r"\\(?:providecommand|newcommand|renewcommand)\*?\s*"
        r"(?:\{\\" + re.escape(name) + r"\}|\\" + re.escape(name) + r"\b)"
        r"|\\def\\" + re.escape(name) + r"\b"
    )
    return bool(pattern.search(text or ""))


def _latex_package_loaded(text: str, name: str) -> bool:
    pattern = re.compile(
        r"\\(?:usepackage|RequirePackage)(?:\[[^\]]*\])?\s*\{[^}]*\b"
        + re.escape(name)
        + r"\b[^}]*\}"
    )
    return bool(pattern.search(text or ""))


def add_xelatex_compatibility_fallbacks(text: str) -> Tuple[str, int]:
    """Add safe fallbacks for templates assuming pdfLaTeX/inputenc/fontspec state."""
    source = text or ""

    needs_inputencoding = (
        (r"\inputencodingname" in source
         or r"\newtcblisting" in source
         or r"\DeclareTCBListing" in source
         or "listing only" in source)
        and not _latex_command_defined(source, "inputencodingname")
    )
    class_needs_early_fontspec = bool(
        re.search(r"\\documentclass(?:\[[^\]]*\])?\{(?:cidr-2025|acmart)\}", source)
    )
    needs_fontspec_noops = (
        (re.search(r"\\(?:setmainfont|setsansfont|setmonofont|newfontfamily)\b", source)
         or class_needs_early_fontspec)
        and not _latex_package_loaded(source, "fontspec")
    )
    needs_xspace_noop = (
        r"\xspace" in source
        and not _latex_command_defined(source, "xspace")
        and not _latex_package_loaded(source, "xspace")
    )
    needs_abscontent_fallback = (
        r"\abscontent" in source
        and not _latex_command_defined(source, "abscontent")
    )
    needs_href_fallback = (
        r"\href" in source
        and not _latex_command_defined(source, "href")
        and not _latex_package_loaded(source, "hyperref")
    )

    total = 0
    if needs_inputencoding:
        insertion = "\n".join([
            r"% paper-trans fallback for XeLaTeX compatibility commands",
            r"\providecommand{\inputencodingname}{utf8}",
        ])
        markers = ["inputencodingname"] if r"\inputencodingname" in source else []
        source, changed = _insert_latex_preamble_snippet(source, insertion, markers)
        total += int(changed)

    if needs_fontspec_noops:
        lines = [r"% paper-trans fallback for XeLaTeX compatibility commands"]
        if not _latex_command_defined(source, "setmainfont"):
            lines.append(r"\providecommand{\setmainfont}[2][]{}")
        if not _latex_command_defined(source, "setsansfont"):
            lines.append(r"\providecommand{\setsansfont}[2][]{}")
        if not _latex_command_defined(source, "setmonofont"):
            lines.append(r"\providecommand{\setmonofont}[2][]{}")
        if not _latex_command_defined(source, "newfontfamily"):
            lines.append(r"\providecommand{\newfontfamily}[3][]{\providecommand#2{}}")
        if len(lines) > 1:
            insertion = "\n".join(lines)
            markers = ["documentclass"] if class_needs_early_fontspec else [
                "setmainfont", "setsansfont", "setmonofont", "newfontfamily"
            ]
            source, changed = _insert_latex_preamble_snippet(source, insertion, markers)
            total += int(changed)

    if needs_xspace_noop:
        insertion = "\n".join([
            r"% paper-trans fallback for missing xspace package",
            r"\providecommand{\xspace}{}",
        ])
        source, changed = _insert_latex_preamble_snippet(source, insertion, ["xspace"])
        total += int(changed)

    if needs_abscontent_fallback:
        insertion = "\n".join([
            r"% paper-trans fallback for templates with external abstract renderer",
            r"\providecommand{\theabstract}{}",
            r"\providecommand{\abscontent}{\par\noindent{\bfseries Abstract}\par\theabstract\par}",
        ])
        source, changed = _insert_latex_preamble_snippet(source, insertion, ["abscontent"])
        total += int(changed)

    if needs_href_fallback:
        insertion = "\n".join([
            r"% paper-trans fallback for missing hyperref package",
            r"\providecommand{\href}[2]{#2}",
        ])
        source, changed = _insert_latex_preamble_snippet(source, insertion, ["href"])
        total += int(changed)

    return source, total


def reset_acm_baselinestretch_before_end_document(text: str) -> Tuple[str, int]:
    """Reset acmart/CIDR baselinestretch guard before final class validation."""
    source = text or ""
    if "paper-trans reset ACM baselinestretch guard" in source:
        return source, 0
    if not re.search(r"\\documentclass(?:\[[^\]]*\])?\{(?:cidr-2025|acmart)\}", source):
        return source, 0
    end_marker = r"\end{document}"
    pos = source.rfind(end_marker)
    if pos < 0:
        return source, 0
    snippet = (
        r"% paper-trans reset ACM baselinestretch guard" "\n"
        r"\makeatletter" "\n"
        r"\@ifundefined{ACM@origbaselinestretch}{}{\let\baselinestretch\ACM@origbaselinestretch}" "\n"
        r"\makeatother"
    )
    return source[:pos] + snippet + "\n" + source[pos:], 1


ZERO_ARG_COMMAND_DEF_RE = re.compile(
    r"\\(?:newcommand|renewcommand|providecommand|DeclareRobustCommand)\*?\s*"
    r"(?:\{\\([A-Za-z@]+)\}|\\([A-Za-z@]+))"
    r"(?:\[(\d+)\])?"
)

CJK_COMMAND_FOLLOW_RE = (
    r"[\u3400-\u4dbf\u4e00-\u9fff"
    r"\uff0c\u3002\uff01\uff1f\uff1b\uff1a\u3001"
    r"\uff08\uff09\u300a\u300b\u201c\u201d\u2018\u2019]"
)

CJK_CHAR_CLASS = CJK_COMMAND_FOLLOW_RE

CJK_INTER_CHAR_SPACE_RE = re.compile(
    r"(" + CJK_CHAR_CLASS + r") +(?=" + CJK_CHAR_CLASS + r")"
)


def separate_custom_macro_cjk_glue(text: str) -> Tuple[str, int]:
    r"""Separate no-argument custom macros from glued CJK text/punctuation.

    Translated TeX commonly turns ``\name\ 中文`` into ``\name\中文`` or
    ``\name中文``. XeLaTeX can parse the glued part as an undefined command.
    The fix is to terminate known zero-argument custom macros with ``{}``.
    """
    macro_names = set()
    definition_spans = []
    for m in ZERO_ARG_COMMAND_DEF_RE.finditer(text or ""):
        arg_count = m.group(3)
        if arg_count not in (None, "0"):
            continue
        name = m.group(1) or m.group(2)
        if not name:
            continue
        macro_names.add(name)
        definition_spans.append((m.start(), m.end()))

    if not macro_names:
        return text, 0

    def in_definition(pos: int) -> bool:
        return any(start <= pos < end for start, end in definition_spans)

    names_pat = "|".join(re.escape(n) for n in sorted(macro_names, key=len, reverse=True))
    slash_cjk_re = re.compile(r"\\(" + names_pat + r")\\(?=" + CJK_COMMAND_FOLLOW_RE + r")")
    glued_re = re.compile(r"\\(" + names_pat + r")(?![A-Za-z@{])(?=" + CJK_COMMAND_FOLLOW_RE + r"|[A-Za-z])")

    total = 0

    def replace(m) -> str:
        nonlocal total
        if in_definition(m.start()):
            return m.group(0)
        total += 1
        return "\\" + m.group(1) + "{}"

    new_text = slash_cjk_re.sub(replace, text)
    new_text = glued_re.sub(replace, new_text)

    brace_cjk_re = re.compile(
        r"\\(" + names_pat + r")\{\}(?=" + CJK_COMMAND_FOLLOW_RE + r")"
    )

    def replace_brace(m) -> str:
        nonlocal total
        if in_definition(m.start()):
            return m.group(0)
        total += 1
        return "\\" + m.group(1) + "{} "

    new_text = brace_cjk_re.sub(replace_brace, new_text)
    new_text, stripped = strip_redundant_macro_empty_groups(new_text, macro_names)
    total += stripped
    return new_text, total


def strip_redundant_macro_empty_groups(text: str, macro_names: Set[str]) -> Tuple[str, int]:
    """Drop ``\\name{}`` before CJK text when ``\\name`` is zero-argument."""
    if not macro_names:
        return text, 0
    names_pat = "|".join(re.escape(n) for n in sorted(macro_names, key=len, reverse=True))
    pattern = re.compile(
        r"\\(" + names_pat + r")\{\}(?=\s*"
        + CJK_CHAR_CLASS
        + r"|[，。！？；：、])"
    )
    return pattern.subn(lambda m: "\\" + m.group(1), text or "")


def collapse_spaced_cjk_characters(text: str) -> Tuple[str, int]:
    """Remove GPT-injected spaces between consecutive CJK characters/punctuation."""
    return CJK_INTER_CHAR_SPACE_RE.subn(r"\1", text or "")


def replace_bare_citation_commands(text: str) -> Tuple[str, int]:
    r"""Replace citations whose argument was deleted by translation.

    A fragment such as ``如\cite中所述`` is parsed as the undefined command
    ``\cite中`` by XeLaTeX.  The citation key is already gone, so the safest
    deterministic fallback is readable prose instead of inventing a key.
    Proper ``\cite{key}`` and optional-argument forms are left untouched.
    """
    pattern = re.compile(r"\\cite(?=" + CJK_COMMAND_FOLLOW_RE + r")")
    return pattern.subn("文献", text or "")


def separate_declaration_command_cjk_glue(text: str) -> Tuple[str, int]:
    r"""Terminate legacy font declarations before translated CJK prose."""
    commands = ("em", "bf", "it", "rm", "sf", "tt")
    pattern = re.compile(r"\\(" + "|".join(commands) + r")(?=" + CJK_COMMAND_FOLLOW_RE + r")")
    return pattern.subn(lambda m: "\\" + m.group(1) + " ", text or "")


def remove_spurious_cjk_command_escapes(text: str) -> Tuple[str, int]:
    r"""Remove stray command escapes before CJK text or punctuation."""
    pattern = re.compile(r"\\(?=" + CJK_COMMAND_FOLLOW_RE + r")")
    return pattern.subn("", text or "")


def relocate_packages_from_documentclass_options(text: str) -> Tuple[str, int]:
    r"""Move ``\usepackage`` lines accidentally inserted inside class options."""
    pattern = re.compile(
        r"(?P<head>\\documentclass\[)(?P<options>.*?)(?P<tail>\]\{[^{}]+\})",
        re.DOTALL,
    )
    total = 0

    def replace(match) -> str:
        nonlocal total
        options = match.group("options")
        packages = re.findall(r"(?m)^\s*(\\usepackage(?:\[[^\]]*\])?\{[^{}]+\})\s*$", options)
        if not packages:
            return match.group(0)
        cleaned = re.sub(r"(?m)^\s*\\usepackage(?:\[[^\]]*\])?\{[^{}]+\}\s*\n?", "", options)
        total += len(packages)
        return match.group("head") + cleaned + match.group("tail") + "\n" + "\n".join(packages)

    return pattern.sub(replace, text or "", count=1), total


PDFTEX_PRIMITIVE_NAMES = (
    "pdfoutput",
    "pdfgentounicode",
    "pdfminorversion",
    "pdfcompresslevel",
    "pdfobjcompresslevel",
    "pdfpagewidth",
    "pdfpageheight",
    "pdfhorigin",
    "pdfvorigin",
    "pdfmapline",
    "pdfinfo",
    "pdfcatalog",
    "pdfobj",
    "pdfximage",
    "pdfrefximage",
    "pdfannot",
    "pdfsavepos",
    "pdfliteral",
    "pdfpageattr",
)

PDFTEX_PRIMITIVE_LINE_RE = re.compile(
    r"(?m)^(?P<indent>[ \t]*)(?P<body>\\(?P<name>"
    + "|".join(re.escape(name) for name in PDFTEX_PRIMITIVE_NAMES)
    + r")\b[^\n]*)$"
)


def guard_pdftex_primitive_lines(text: str) -> Tuple[str, int]:
    """Wrap pdfTeX-only primitive lines so XeLaTeX/LuaLaTeX can skip them."""
    total = 0

    def replace(m) -> str:
        nonlocal total
        line = m.group(0)
        name = m.group("name")
        if "\\ifdefined\\" + name in line:
            return line
        total += 1
        return (
            m.group("indent")
            + "\\ifdefined\\"
            + name
            + m.group("body")
            + "\\fi"
        )

    new_text = PDFTEX_PRIMITIVE_LINE_RE.sub(replace, text or "")
    return new_text, total


CAPTION_MARKER = r"\caption{"
STRUCTURAL_CMD_IN_CAPTION_RE = re.compile(
    r"\\(section|subsection|subsubsection|paragraph|subparagraph)\*?(\s*)\{"
)


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


def demote_structural_commands_in_captions(text: str) -> Tuple[str, int]:
    """Replace ``\\section``-class commands inside ``\\caption{...}`` with ``\\textbf``."""
    if not text:
        return text, 0

    count = 0
    result: List[str] = []
    i = 0
    while i < len(text):
        start = text.find(CAPTION_MARKER, i)
        if start < 0:
            result.append(text[i:])
            break
        result.append(text[i:start])
        open_idx = start + len(r"\caption")
        close_idx = _find_matching_brace(text, open_idx)
        if close_idx < 0:
            result.append(text[start:])
            break
        body = text[open_idx + 1:close_idx]
        new_body = body
        while True:
            m = STRUCTURAL_CMD_IN_CAPTION_RE.search(new_body)
            if not m:
                break
            arg_open = m.end() - 1
            arg_close = _find_matching_brace(new_body, arg_open)
            if arg_close < 0:
                break
            arg_content = new_body[arg_open + 1:arg_close]
            replacement = "\\textbf{" + arg_content + "}"
            new_body = new_body[:m.start()] + replacement + new_body[arg_close + 1:]
            count += 1
        result.append(CAPTION_MARKER + new_body + "}")
        i = close_idx + 1
    return "".join(result), count


INLINE_VERB_DELIMITER_CANDIDATES = ("@", "~", "/", ";", ":", "+", "=")
INLINE_VERB_COMMAND_RE = re.compile(r"\\verb\*?")


def _is_valid_inline_verb_delimiter(delim: str) -> bool:
    return bool(delim) and not delim.isspace() and not delim.isalnum() and delim != "\\"


def _choose_inline_verb_delimiter(content: str, current: str) -> Optional[str]:
    for delim in INLINE_VERB_DELIMITER_CANDIDATES:
        if delim != current and delim not in content:
            return delim
    return None


def _looks_like_broken_inline_verb(content: str, original_delim: str) -> bool:
    if original_delim not in content:
        return False
    if "\\verb" in content:
        return False
    code_like = bool(
        re.search(r"\\[?.!$]", content)
        or re.search(r"\br[\"']", content)
        or any(token in content for token in ("(?", "[", "]", "^", "*", "+"))
    )
    # The common failure shape is a regex/code literal whose original delimiter
    # appears inside the literal, making later escaped punctuation look like
    # normal LaTeX control sequences after the premature close.
    return code_like


def repair_inline_verb_delimiter_collisions(text: str) -> Tuple[str, int]:
    r"""Re-delimit inline ``\verb`` commands whose content contains the delimiter.

    GPT can preserve a regex as ``\verb|...|`` while the regex itself contains
    ``|``. TeX closes the verb at the first inner delimiter, and escaped
    punctuation such as ``\?`` or ``\!`` then becomes an undefined command. This
    repair only rewrites suspicious single-line inline verb commands and leaves
    ordinary ``\verb|foo|`` or multiple independent verb commands untouched.
    """
    if not text:
        return text, 0

    total = 0
    fixed_lines: List[str] = []

    for line in text.splitlines(keepends=True):
        newline = ""
        body = line
        if body.endswith("\n"):
            newline = "\n"
            body = body[:-1]

        result = []
        pos = 0
        changed = False
        while pos < len(body):
            m = INLINE_VERB_COMMAND_RE.search(body, pos)
            if not m:
                result.append(body[pos:])
                break

            delim_idx = m.end()
            if delim_idx >= len(body):
                result.append(body[pos:])
                break

            delim = body[delim_idx]
            if not _is_valid_inline_verb_delimiter(delim):
                result.append(body[pos:delim_idx + 1])
                pos = delim_idx + 1
                continue

            rest = body[delim_idx + 1:]
            delim_positions = [dm.start() for dm in re.finditer(re.escape(delim), rest)]
            if len(delim_positions) < 2:
                result.append(body[pos:delim_idx + 1 + (delim_positions[0] + 1 if delim_positions else 0)])
                pos = delim_idx + 1 + (delim_positions[0] + 1 if delim_positions else 0)
                continue

            last_delim = delim_positions[-1]
            content = rest[:last_delim]
            new_delim = _choose_inline_verb_delimiter(content, delim)
            if new_delim and _looks_like_broken_inline_verb(content, delim):
                result.append(body[pos:m.start()])
                result.append(body[m.start():delim_idx])
                result.append(new_delim + content + new_delim)
                pos = delim_idx + 1 + last_delim + 1
                total += 1
                changed = True
            else:
                first_delim = delim_positions[0]
                result.append(body[pos:delim_idx + 1 + first_delim + 1])
                pos = delim_idx + 1 + first_delim + 1

        fixed_lines.append(("".join(result) if changed else body) + newline)

    return "".join(fixed_lines), total
