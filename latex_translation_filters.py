#!/usr/bin/env python3
"""
Shared LaTeX filtering policy for the Chinese PDF translation pipeline.

The driver uses these predicates in three places: splitter expansion, quality
checks, and fallback restoration. Keeping the policy here prevents future fixes
from being hard-coded in only one of those paths.
"""

import difflib
import os
import re
import tarfile
from collections import Counter
from pathlib import PurePosixPath
from typing import Iterable, List, Optional, Set, Tuple

try:
    # The container bundles this module beside the driver as a flat support
    # file; the host imports it through the paperhub package.
    from translation_policy import (
        DEFAULT_TRANSLATION_CHUNK_LIMIT,
        translation_chunk_limit,
    )
except ImportError:
    from paperhub.translation_policy import (
        DEFAULT_TRANSLATION_CHUNK_LIMIT,
        translation_chunk_limit,
    )


def rank_main_tex_candidate(path: str, content: str, candidates: Iterable[str]) -> int:
    """Rank a TeX file as an entrypoint without guessing from prose volume.

    A source tree may contain both an entrypoint (usually ``main.tex``) and a
    body file with its own commented/documentclass preamble.  Word-count
    scoring cannot distinguish them and can select the body, dropping the
    entrypoint's layout/packages.  Prefer a conventional entrypoint and use
    the explicit ``input/include`` relationship as the general tie-breaker.
    """
    value = _without_unescaped_comments(content or "")
    basename = PurePosixPath(str(path).replace("\\", "/")).name.lower()
    names = {
        PurePosixPath(str(candidate).replace("\\", "/")).name.lower(): candidate
        for candidate in candidates
    }
    stems = {PurePosixPath(name).stem: candidate for name, candidate in names.items()}
    references = {
        PurePosixPath(ref.strip()).name.lower()
        for ref in re.findall(r"\\(?:input|include)\s*\{([^{}]+)\}", value)
    }
    referenced_stems = {PurePosixPath(ref).stem for ref in references}

    score = 0
    if basename in {"main.tex", "paper.tex", "root.tex", "manuscript.tex", "article.tex"}:
        score += 100
    if basename in {"paper_body.tex", "body.tex", "content.tex"}:
        score -= 100
    if referenced_stems & set(stems):
        score += 20
    return score


SOFT_TEXT_ENVS = frozenset({
    "tabular", "tabular*", "tabularx", "longtable", "array",
    "algorithmic", "algorithmic*", "algorithm2e",
})

# These environments are not protected as a class.  A concrete instance is
# protected only when its opening arguments or first content lines identify it
# as a prompt, benchmark example, trace, or other verbatim source-data block.
# Tracking the environments is still necessary so the splitter can preserve
# their structural begin/end lines while translating ordinary box prose.
SEMANTIC_SOURCE_DATA_ENVS = frozenset({
    "tcolorbox", "custombox", "casebox", "examplebox", "mdframed",
})

BASE_HARD_PROTECTED_ENVS = frozenset({
    "figure", "figure*", "table", "table*", "algorithm",
    "lstlisting", "verbatim", "Verbatim", "minted", "comment", "equation",
    "equation*", "align", "align*", "multline", "multline*", "gather",
    "gather*", "tikzpicture", "minipage", "minipage*", "thebibliography",
    # Structured JSON samples are source data, not untranslated prose.
    "captionexample",
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
    r"|(?:case|strategy|source|example)code$"
    r"|^(?:toolcall|caseresponse|errorspan|normalspan|templatebubble|ccsxml|paperresources)$"
    r"|(?:listing|verbatim|transcript|trajectory|trace|prompt|codeblock)$"
    r")"
)

SEMANTIC_SOURCE_DATA_TITLE_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:system|developer|user|judge|agent|translation)[\s_-]*prompt\b"
    r"|\bprompt[\s_-]*(?:template|example|block)?\b"
    r"|\b(?:execution|tool|agent|failure|success)[\s_-]*trace\b"
    r"|\b(?:trajectory|transcript|source[\s_-]*data|benchmark[\s_-]*example)\b"
    r"|\b(?:user[\s_-]*query|demonstration[\s_-]*example)\b"
    r")"
)
SEMANTIC_SOURCE_DATA_FIELD_RE = re.compile(
    r"(?i)^\s*\\(?:textbf|textit|emph)\s*\{"
    r"(?:question|answer|response|correct\s+answer|user\s+query|"
    r"task|role|instruction|input|output(?:\s+format)?|classification|language)"
    r"\s*:?\s*\}"
)
SEMANTIC_CASE_STYLE_RE = re.compile(
    r"(?i)(?:"
    r"\bcasebox\s*="
    r"|success(?:bg|frame)?"
    r"|fail(?:ure)?(?:bg|frame)?"
    r"|成功案例|失败案例"
    r")"
)

# A tcolorbox may be styled through a key declared in the document preamble.
# ``promptbox`` is a style name rather than natural-language title text, so the
# title-only policy below used to miss real prompt templates such as
# ``[promptbox,title={Coarse CoT Template}]``.  Keep the rule deliberately
# narrow: generic theorem/insight boxes are still translated.
SEMANTIC_PROMPT_BOX_STYLE_RE = re.compile(
    r"(?i)(?:^|[\[,{\s])(?:system|developer|user|judge|agent|prompt|"
    r"instruction|template)[_-]?box\b"
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


def is_semantic_source_data_env(env: Optional[str]) -> bool:
    """Return whether *env* supports instance-level source-data protection."""
    return bool(env) and env in SEMANTIC_SOURCE_DATA_ENVS


def is_semantic_source_data_opening(
    env: Optional[str],
    opening_line: str,
) -> bool:
    """Classify one environment instance from its ``\\begin`` line.

    This deliberately does not make ``tcolorbox``/``custombox``/``mdframed``
    globally hard-protected.  Ordinary theorem, insight, and explanatory boxes
    still need translation.  Only explicit prompt/trace/example/case semantics
    are treated as verbatim paper source data.
    """
    if not is_semantic_source_data_env(env):
        return False
    line = str(opening_line or "")
    if not re.search(rf"\\begin\{{{re.escape(str(env))}\}}", line):
        return False
    if env == "examplebox":
        # The environment name plus an explicit argument/title is an
        # instance-level declaration that the enclosed material is an example.
        suffix = line.split(rf"\begin{{{env}}}", 1)[-1]
        return bool(suffix.strip())
    if env == "casebox":
        return bool(SEMANTIC_CASE_STYLE_RE.search(line))
    if env == "tcolorbox" and SEMANTIC_CASE_STYLE_RE.search(line):
        return True
    if env == "tcolorbox" and SEMANTIC_PROMPT_BOX_STYLE_RE.search(line):
        return True
    return bool(SEMANTIC_SOURCE_DATA_TITLE_RE.search(line))


def is_semantic_source_data_content(
    env: Optional[str],
    line: str,
) -> bool:
    """Promote a candidate box when its first fields expose source data.

    Benchmark names are open-ended, so a title alone cannot enumerate them
    safely.  A leading ``Question:``, ``Answer:``, or response field inside a
    custom box is a precise, reusable signal without hiding ordinary boxes.
    """
    if env not in {"tcolorbox", "custombox", "casebox", "examplebox"}:
        return False
    return bool(SEMANTIC_SOURCE_DATA_FIELD_RE.search(str(line or "")))


def is_tracked_env(env: Optional[str]) -> bool:
    return (
        is_soft_text_env(env)
        or is_hard_protected_env(env)
        or is_semantic_source_data_env(env)
    )


def tracked_envs() -> Set[str]:
    return (
        soft_text_envs()
        | hard_protected_envs()
        | set(SEMANTIC_SOURCE_DATA_ENVS)
    )


def _archive_target_is_contained(name: str, base: str = "") -> bool:
    """Return whether a POSIX archive path resolves below its extraction root."""
    value = str(name or "")
    if not value or "\x00" in value or "\\" in value:
        return False
    path = PurePosixPath(value)
    if path.is_absolute():
        return False
    stack = []
    for part in PurePosixPath(base).parts + path.parts:
        if part in ("", "."):
            continue
        if part == "..":
            if not stack:
                return False
            stack.pop()
            continue
        stack.append(part)
    return bool(stack)


def source_tar_safety_error(
    path: str,
    max_members: int = 50_000,
    max_unpacked_bytes: int = 2 * 1024 * 1024 * 1024,
) -> str:
    """Return a reason when an untrusted arXiv tar is unsafe to extract."""
    try:
        with tarfile.open(path, mode="r:*") as archive:
            unpacked = 0
            member_count = 0
            for member in archive:
                member_count += 1
                if member_count > max_members:
                    return (
                        "archive has too many members: "
                        f"{member_count} > {max_members}"
                    )

                archive_root_entry = (
                    member.isdir()
                    and str(member.name or "").strip("/") in ("", ".")
                )
                if (
                    not archive_root_entry
                    and not _archive_target_is_contained(member.name)
                ):
                    return f"unsafe archive member path: {member.name!r}"
                if member.isdev() or member.isfifo():
                    return f"unsupported archive special file: {member.name!r}"
                if member.isfile():
                    unpacked += max(0, int(member.size or 0))
                    if unpacked > max_unpacked_bytes:
                        return (
                            "archive unpacked size exceeds limit: "
                            f"{unpacked} > {max_unpacked_bytes}"
                        )
                if member.issym():
                    parent = str(PurePosixPath(member.name).parent)
                    if not _archive_target_is_contained(
                        member.linkname,
                        base=parent,
                    ):
                        return (
                            f"unsafe archive symlink: {member.name!r} -> "
                            f"{member.linkname!r}"
                        )
                elif member.islnk():
                    if not _archive_target_is_contained(member.linkname):
                        return (
                            f"unsafe archive hardlink: {member.name!r} -> "
                            f"{member.linkname!r}"
                        )
    except (OSError, tarfile.TarError) as exc:
        return f"invalid tar archive: {exc}"
    return ""


TEX_SHELL_ESCAPE_FLAG_RE = re.compile(
    r"(?<!\S)-{1,2}(?:(?:no-)?shell-escape|"
    r"(?:enable|disable)-write18)(?=\s|$)",
    re.IGNORECASE,
)
TEX_ENGINE_COMMAND_RE = re.compile(
    r"(?<![A-Za-z0-9_./-])(?P<engine>"
    r"\"(?:/[^\"\s]+/)?(?:pdf|xe|lua)latex\"|"
    r"'(?:/[^'\s]+/)?(?:pdf|xe|lua)latex'|"
    r"(?:/[^\s'\";|&]+/)?(?:pdf|xe|lua)latex"
    r")(?=\s|$)",
    re.IGNORECASE,
)


def force_no_tex_shell_escape(command: str) -> str:
    """Remove conflicting write18 flags and force each TeX engine safe."""
    value = TEX_SHELL_ESCAPE_FLAG_RE.sub("", str(command or ""))
    value = re.sub(r"[ \t]{2,}", " ", value)
    return TEX_ENGINE_COMMAND_RE.sub(
        lambda match: match.group("engine") + " -no-shell-escape",
        value,
    )


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
    # Occasionally the model leaks a serialized response as a standalone TeX
    # line. These synthetic payloads are not paper content, and literal ``\n``
    # escapes inside them break compilation.
    re.compile(
        r'(?m)^[ \t]*"translation"\s*:\s*"(?:\\.|[^"\\])*"\s*,?[ \t]*$'
    ),
    re.compile(
        r'(?m)^[ \t]*\[\s*"(?:\\.|[^"\\])*"'
        r'(?:\s*,\s*"(?:\\.|[^"\\])*")*\s*\][ \t]*$'
    ),
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

LLM_REQUEST_FAILURE_MARKERS = (
    "[Local Message] 警告，线程",
    "Traceback",
    "Too Many Requests",
    "Rate limit reached",
    "429 Client Error",
    "Error code: 429",
    "insufficient_user_quota",
)


def llm_translation_response_failed(text: str) -> bool:
    """Return whether a chunk response is an upstream request failure payload."""
    value = text or ""
    return not value.strip() or any(marker in value for marker in LLM_REQUEST_FAILURE_MARKERS)


def llm_translation_response_quota_failed(text: str) -> bool:
    value = (text or "").lower()
    return (
        "insufficient_user_quota" in value
        or ("balance" in value and "insufficient" in value)
        or "余额不足" in value
        or "额度不足" in value
    )


# These labels are model-task metadata, not paper text.  In particular, a
# three-line block such as ``Classification: Academic Translation`` / ``Task:
# English to Chinese`` / ``Language: Chinese`` is a known prompt echo that can
# replace an entire LaTeX list item.  Reject it before merge instead of trying
# to regex-delete the already-corrupted output afterwards.
LLM_TRANSLATION_TASK_ECHO_RE = re.compile(
    r"(?is)(?=.*\bclassification\s*:\s*academic\s+translation\b)"
    r"(?=.*\btask\s*:\s*english\s+to\s+chinese\b)"
    r"(?=.*\blanguage\s*:\s*chinese\b)"
)

_CRITICAL_LATEX_COMMAND_RE = re.compile(
    r"\\(?P<name>begin|end|item|caption|captionof|section|subsection|subsubsection|"
    r"paragraph|subparagraph|label|ref|eqref|autoref|cref|Cref)\*?\b"
)

_SINGLE_HEADING_WRAPPER_RE = re.compile(
    r"\A\s*\\(?:section|subsection|subsubsection|paragraph|subparagraph)"
    r"\*?\s*\{"
)

_BARE_HALLUCINATED_HEADING_RE = re.compile(
    r"\\(?P<name>section|subsection|subsubsection|paragraph|subparagraph)"
    r"(?:\*)?(?![A-Za-z@])(?!(?:\s*)\{)"
)

_BARE_HEADING_LABELS = {
    "section": "章节",
    "subsection": "小节",
    "subsubsection": "小节",
    "paragraph": "段落",
    "subparagraph": "段落",
}


def _without_unescaped_comments(text: str) -> str:
    """Remove TeX comments before comparing structural command signatures."""
    lines = []
    for line in (text or "").splitlines(keepends=True):
        cut = len(line)
        for index, char in enumerate(line):
            if char != "%":
                continue
            slashes = 0
            cursor = index - 1
            while cursor >= 0 and line[cursor] == "\\":
                slashes += 1
                cursor -= 1
            if slashes % 2 == 0:
                cut = index
                break
        lines.append(line[:cut])
    return "".join(lines)


def _unescaped_brace_balance(text: str) -> int:
    """Return the net balance of uncommented, unescaped TeX braces."""
    balance = 0
    for line in _without_unescaped_comments(text).splitlines(keepends=True):
        for index, char in enumerate(line):
            if char not in "{}":
                continue
            slashes = 0
            cursor = index - 1
            while cursor >= 0 and line[cursor] == "\\":
                slashes += 1
                cursor -= 1
            if slashes % 2:
                continue
            balance += 1 if char == "{" else -1
    return balance


def _critical_latex_signature(
    text: str,
) -> Tuple[Tuple[str, ...], Tuple[Tuple[str, int], ...]]:
    """Return structural commands and an exact citation-call multiset.

    This is intentionally a small structural subset, not a textual similarity
    check: normal Chinese translation may change every word, but it must not
    create a section, drop list items, or change a citation from the chunk.
    Non-reference structural command order remains significant. Reference
    placement may move naturally in Chinese. The command, star, keys, numeric locators, and
    duplicate counts must remain exact; natural-language optional notes such
    as ``Figure`` may be translated.
    """
    value = _without_unescaped_comments(
        strip_inline_code_commands(extract_translation_fragment(text))
    )
    commands = tuple(
        match.group("name").lower()
        for match in _CRITICAL_LATEX_COMMAND_RE.finditer(value)
    )
    citations = tuple(sorted(Counter(
        (
            match.group("command").lower()
            + (match.group("star") or "")
            + "["
            + ",".join(re.findall(r"\d+(?:\.\d+)?", match.group("options")))
            + "]{"
            + ",".join(
                key.strip()
                for key in match.group("keys").split(",")
            )
            + "}"
        )
        for match in CITATION_IDENTITY_RE.finditer(value)
    ).items()))
    return commands, citations


def _critical_commands_equivalent(
    source_commands: Tuple[str, ...],
    response_commands: Tuple[str, ...],
) -> bool:
    """Allow citation/reference commands to move with Chinese word order."""
    if source_commands == response_commands:
        return True
    movable = {"cite", "citep", "citet", "citealt", "citealp", "ref", "eqref", "pageref", "autoref", "cref", "Cref"}
    source_fixed = tuple(command for command in source_commands if command not in movable)
    response_fixed = tuple(command for command in response_commands if command not in movable)
    if source_fixed != response_fixed:
        return False
    return Counter(
        command for command in source_commands if command in movable
    ) == Counter(
        command for command in response_commands if command in movable
    )


def llm_translation_structure_evidence(source: str, response: str):
    """Return a deterministic diff for a rejected translation response.

    The response gate deliberately compares only a small safety-critical LaTeX
    subset.  A short text preview can make a dropped later ``\\paragraph`` or
    citation look identical to its source, so callers need the complete
    signatures and *directional* citation multiset differences in their error
    log.  This helper is diagnostic only; it does not relax the gate.
    """
    source_commands, source_citations = _critical_latex_signature(source)
    response_commands, response_citations = _critical_latex_signature(response)
    source_counts = Counter(dict(source_citations))
    response_counts = Counter(dict(response_citations))
    return {
        "source_commands": source_commands,
        "response_commands": response_commands,
        "source_citations_only": tuple(sorted(
            (source_counts - response_counts).items()
        )),
        "response_citations_only": tuple(sorted(
            (response_counts - source_counts).items()
        )),
    }


def _matching_unescaped_brace(text: str, opening: int) -> int:
    """Return the matching brace index, or ``-1`` for an unbalanced group."""
    depth = 0
    for index in range(opening, len(text)):
        char = text[index]
        if char not in "{}":
            continue
        slashes = 0
        cursor = index - 1
        while cursor >= 0 and text[cursor] == "\\":
            slashes += 1
            cursor -= 1
        if slashes % 2:
            continue
        if char == "{":
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                return index
            if depth < 0:
                return -1
    return -1


def normalize_llm_translation_response(source: str, response: str) -> str:
    r"""Normalize one provably spurious heading command in a bare fragment.

    GPT occasionally sees a caption argument that the splitter has correctly
    detached from ``\caption{...}``, then wraps its translation in
    ``\section{...}``.  Accept only that recoverable shape: the source itself
    has no critical structural command, the complete response is exactly one
    balanced heading wrapper, and its inner critical commands/citations match
    the source.  Any extra paragraph or second command leaves the response
    unchanged so the normal structure gate rejects it.
    """
    source_value = extract_translation_fragment(source)
    response_value = response if isinstance(response, str) else str(response or "")
    if not source_value.strip() or not response_value.strip():
        return response_value
    source_commands, source_citations = _critical_latex_signature(source_value)

    # Some models deterministically wrap a long fragment with one unmatched
    # closing brace. Removing that final token is provably safe only when it is
    # the last non-whitespace character and the corrected brace balance plus
    # critical command/citation signatures exactly match the source.
    stripped_response = response_value.rstrip()
    if (
        stripped_response.endswith("}")
        and _unescaped_brace_balance(response_value)
        == _unescaped_brace_balance(source_value) - 1
    ):
        corrected = (
            stripped_response[:-1]
            + response_value[len(stripped_response):]
        )
        corrected_commands, corrected_citations = _critical_latex_signature(
            corrected
        )
        if (
            _unescaped_brace_balance(corrected)
            == _unescaped_brace_balance(source_value)
            and corrected_commands == source_commands
            and corrected_citations == source_citations
        ):
            response_value = corrected

    if source_commands:
        # The safe trailing-brace repair above is valid for fragments that
        # also contain critical commands. All broader wrapper normalization
        # below remains restricted to command-free source fragments.
        return response_value

    # A second recurring model error translates the ordinary word "section"
    # into a *bare* TeX command, for example ``（第 \section）``.  A heading
    # command without its braced argument is never a valid translation of bare
    # prose.  Recover exactly one such addition and retain the strict citation
    # and structural-signature checks below.
    bare_headings = list(_BARE_HALLUCINATED_HEADING_RE.finditer(response_value))
    if len(bare_headings) == 1:
        normalized = _BARE_HALLUCINATED_HEADING_RE.sub(
            lambda match: _BARE_HEADING_LABELS[match.group("name").lower()],
            response_value,
            count=1,
        )
        normalized_commands, normalized_citations = _critical_latex_signature(
            normalized
        )
        if (
            normalized_commands == source_commands
            and normalized_citations == source_citations
        ):
            return normalized

    opening_match = _SINGLE_HEADING_WRAPPER_RE.match(response_value)
    if not opening_match:
        return response_value
    opening = opening_match.end() - 1
    closing = _matching_unescaped_brace(response_value, opening)
    if closing < 0 or response_value[closing + 1:].strip():
        return response_value

    inner = response_value[opening + 1:closing]
    inner_commands, inner_citations = _critical_latex_signature(inner)
    if inner_commands != source_commands or inner_citations != source_citations:
        return response_value
    return inner


def normalize_llm_translation_payload(payload, sources: Iterable[str]) -> List[int]:
    """Normalize alternating ``[input, response, ...]`` results in place."""
    changed = []
    source_items = list(sources)
    for index, source in enumerate(source_items):
        response_index = index * 2 + 1
        if response_index >= len(payload):
            break
        response = payload[response_index]
        normalized = normalize_llm_translation_response(source, response)
        if normalized == response:
            continue
        payload[response_index] = normalized
        changed.append(index)
    return changed


def llm_translation_response_invalid(source: str, response: str) -> str:
    """Return a merge-blocking reason for a fabricated/corrupted response.

    The caller retries this exact slot serially.  Returning a reason rather
    than rewriting the response ensures a hallucinated paragraph can never
    enter the final TeX merely because it is syntactically compilable.
    """
    source_value = extract_translation_fragment(source)
    value = response or ""
    # A protected inline prompt/schema should have been removed by the
    # splitter.  If an upstream boundary nevertheless leaks a complete block,
    # accept only an exact pass-through and force a retry for any mutation.
    # This prevents prompt-injection text from becoming a translated response.
    if is_inline_prompt_source_data_block(source_value):
        if value == source_value:
            return ""
        return "protected_source_data_modified"
    if LLM_TRANSLATION_TASK_ECHO_RE.search(value):
        return "translation_task_echo"

    # Upstream's fallback for a brace-level mismatch splices the untranslated
    # source tail back into an otherwise valid Chinese response. Reject the
    # malformed slot before merge so the serialized retry can repair it.
    if _unescaped_brace_balance(source_value) != _unescaped_brace_balance(value):
        return "latex_brace_balance_mismatch"

    source_commands, source_citations = _critical_latex_signature(source)
    response_commands, response_citations = _critical_latex_signature(value)
    if not _critical_commands_equivalent(source_commands, response_commands):
        return "critical_latex_structure_mismatch"
    if source_citations != response_citations:
        return "citation_structure_mismatch"
    return ""


STRUCTURAL_RETRY_INSTRUCTION = (
    "\n\nOn this retry, preserve every LaTeX command and its arguments, "
    "including all citation, reference, label, and section commands. "
    "Translate prose only; do not omit, rename, reorder, or invent LaTeX "
    "commands or citation keys."
)


def translation_retry_system_prompt(prompt: str, reason: str) -> str:
    """Add a precise structure reminder only after a structural rejection."""
    value = str(prompt or "")
    if reason not in {
        "critical_latex_structure_mismatch",
        "citation_structure_mismatch",
        "latex_brace_balance_mismatch",
    }:
        return value
    if STRUCTURAL_RETRY_INSTRUCTION.strip() in value:
        return value
    return value + STRUCTURAL_RETRY_INSTRUCTION


TRANSLATION_PROMPT_MARKERS = (
    "Answer me only with the translated text:\n\n",
    "Answer me only with the translated text:\r\n\r\n",
)
INLINE_CODE_COMMAND_RE = re.compile(
    r"\\(?:[A-Za-z@]*tt|cmd|path|url|nolinkurl)\*?"
    r"(?:\[[^\]]*\])?\{[^{}]*\}"
)
INLINE_CODE_OPEN_RE = re.compile(
    r"\\(?:[A-Za-z@]*tt|cmd|path|url|nolinkurl)\*?"
    r"(?:\[[^\]]*\])?\{"
)
CITATION_COMMAND_RE = re.compile(
    r"\\(?:cite|citep|citet|citealp|citeauthor|citeyear|parencite|textcite)"
    r"\*?(?:\[[^\]]*\]){0,2}\{[^{}]*\}",
    re.IGNORECASE,
)
CITATION_IDENTITY_RE = re.compile(
    r"(?P<command>\\(?:cite|citep|citet|citealp|citeauthor|citeyear|"
    r"parencite|textcite))(?P<star>\*)?"
    r"(?P<options>(?:\[[^\]]*\]){0,2})\{(?P<keys>[^{}]*)\}",
    re.IGNORECASE,
)
REFERENCE_PAYLOAD_COMMAND_RE = re.compile(
    r"\\(?:label|ref|eqref|pageref|autoref|cref|Cref)\*?"
    r"(?:\[[^\]]*\]){0,2}\{[^{}]*\}",
    re.IGNORECASE,
)
MATH_SPAN_RE = re.compile(
    r"\$\$.*?\$\$|\$[^$]*\$|\\\(.*?\\\)|\\\[.*?\\\]",
    re.DOTALL,
)
_CATALOG_NAME_WORDS = {
    "audio",
    "base",
    "chat",
    "flash",
    "instruct",
    "large",
    "medium",
    "mini",
    "model",
    "omni",
    "pro",
    "small",
    "turbo",
}
_CATALOG_PROSE_WORDS = {
    "achieve",
    "achieves",
    "are",
    "compare",
    "compares",
    "evaluate",
    "evaluates",
    "include",
    "includes",
    "is",
    "outperform",
    "outperforms",
    "propose",
    "proposes",
    "report",
    "reports",
    "show",
    "shows",
    "use",
    "uses",
    "were",
}
_CATALOG_CONNECTOR_WORDS = {"and", "or", "plus", "with"}

# These commands render an external source/code file.  Their optional style
# list and path are TeX syntax, never translatable prose; a stateful line
# policy below keeps a multiline invocation intact.
STRUCTURAL_INPUT_COMMAND_RE = re.compile(
    r"\\(?:VerbatimInput|verbatiminput|lstinputlisting|inputminted|"
    r"includegraphics\*?|prompttext)\b",
    re.IGNORECASE,
)
AFFILIATION_MARKER_RE = re.compile(
    r"^\s*(?:\\(?:textsuperscript|affmark)\s*\{[^{}]+\}\s*)+",
    re.IGNORECASE,
)

# High-precision signal for a *partial* Chinese translation.  These are
# grammatical glue words, not a generic English-word blacklist: model names,
# paper titles, table labels, code and citations must remain legal output.
MIXED_ENGLISH_CLAUSE_GLUE_WORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "but",
    "by", "each", "for", "from", "if", "in", "into", "is", "it", "its",
    "of", "on", "or", "our", "that", "the", "their", "then", "these",
    "this", "those", "to", "was", "we", "were", "when", "where", "which",
    "while", "with",
})
MIXED_ENGLISH_WORD_RE = re.compile(r"\b[A-Za-z][A-Za-z'-]{1,}\b")
SOURCE_DATA_INLINE_MACRO_OPEN_RE = re.compile(
    r"\\(?:fbtask|fbtraj|fbresult)\*?(?:\[[^\]]*\])?\{",
    re.IGNORECASE,
)
PROMPT_TEMPLATE_LINE_RE = re.compile(
    r"(?i)(?:"
    r"\bdo\s+not\s+renumber\b"
    r"|\b(?:rules|rubrics|output\s+format|doc\s+set)\s*:"
    r"|\b(?:query|answer)\s*:"
    r")"
)

# Some appendices print an LLM's *own* XML/JSON prompt verbatim without a
# dedicated LaTeX code environment.  Passing those instructions back to the
# translation model is both incorrect (they are source data) and unsafe: an
# embedded ``<final_prompt>`` may make the model follow the paper's instruction
# instead of translating it.  Do not treat an arbitrary XML noun as source
# data.  The rules below require an instruction/template/schema combination.
INLINE_SOURCE_DATA_XML_PAIR_RE = re.compile(
    r"(?is)<(?P<tag>[a-z][a-z0-9_-]{2,})\b[^>]*>.*?</(?P=tag)>"
)
INLINE_SOURCE_DATA_TAG_RE = re.compile(r"(?i)</?([a-z][a-z0-9_-]{2,})\b[^>]*>")
INLINE_SOURCE_DATA_JSON_KEY_RE = re.compile(r'"[A-Za-z][A-Za-z0-9_-]{1,}"\s*:')
INLINE_SOURCE_DATA_START_RE = re.compile(
    r"(?is)(?:"
    r"\b[A-Z][A-Z0-9_]*_PROMPT_TEMPLATE\s*="
    r"|\bprompt\s+(?:upsampler|engineer)\b(?=.*\b(?:json|xml|template)\b)"
    r"|\b(?:write|return|output)\b(?=.*<final_prompt>)(?=.*\b(?:json|template)\b)"
    r"|\b(?:all\s+top-level\s+keys|output_json_template|user\s+visual\s+request)\b"
    r"|\bstructured\s+json\b(?=.*\b(?:template|schema|every\s+top-level)\b)"
    r")"
)
INLINE_SOURCE_DATA_END_RE = re.compile(
    r"(?i)(?:\\end\{(?:Verbatim|verbatim|lstlisting|minted|tcblisting|"
    r"tcolorbox|mybox)\}"
    r"|\\(?:section|subsection|subsubsection)\*?\{)"
)
SINGLE_LINE_OUTPUT_INSTRUCTION_RE = re.compile(
    r"(?i)^\s*(?:\\item(?:\[[^\]]*\])?\s*)?"
    r"(?:return|output|respond\s+with)\b"
    r"(?=.*\b(?:only|corrected)\b)"
    r"(?=.*\b(?:code|json|xml)\b)"
    r"(?=.*(?:\\[A-Za-z@]*fence\{|fenced\s+(?:code|json)|code\s+block))"
)
SINGLE_LINE_FORMAT_DIRECTIVE_RE = re.compile(
    r"(?i)^\s*(?:\\item(?:\[[^\]]*\])?\s*)?(?:"
    r"then\s+provide\b(?=.*\b(?:reasoning|assessment)\b)"
    r"(?=.*\b(?:exactly|block|lines?)\b)"
    r"|do\s+not\s+output\b(?=.*\boutside\b)"
    r"(?=.*\b(?:analysis|block|lines?|tags?)\b)"
    r"|you\s+must\s+output\b(?=.*\bexact\s+format\b)"
    r"|please\s+reason\s+step\s+by\s+step\b"
    r"(?=.*\bfinal\s+answer\b)(?=.*(?:boxed|\\texttt))"
    r"|also\s+write\s+a\s+one-line\s+memory\s+summary\b"
    r"|output\s+everything\s+after\s+the\s+marker\s+below\b"
    r"|convert\s+the\s+paragraph\s+into\s+a\s+json\s+dict\b"
    r")"
)


def _is_single_line_source_instruction(value: str) -> bool:
    return bool(
        SINGLE_LINE_OUTPUT_INSTRUCTION_RE.search(value or "")
        or SINGLE_LINE_FORMAT_DIRECTIVE_RE.search(value or "")
    )


def is_inline_prompt_source_data_block(text: str) -> bool:
    """Identify a complete inline XML/JSON prompt or schema with high precision.

    This is intentionally a block predicate, rather than a broad "contains
    JSON/XML" check: normal prose may mention one XML tag, show a small JSON
    example, or discuss a prompt, and must remain eligible for translation.
    """
    value = extract_translation_fragment(text)
    if _is_single_line_source_instruction(value):
        return True
    if not value.strip() or not INLINE_SOURCE_DATA_START_RE.search(value):
        return False
    tags = {match.group("tag").lower() for match in INLINE_SOURCE_DATA_XML_PAIR_RE.finditer(value)}
    tag_count = len(tags)
    json_keys = len(INLINE_SOURCE_DATA_JSON_KEY_RE.findall(value))
    has_template_placeholder = bool(re.search(
        r"<\s*[a-z][a-z0-9_-]*\s*>\s*\{(?:\{?\s*[A-Za-z_][A-Za-z0-9_]*\s*\}?)?\s*\}\s*</",
        value,
        flags=re.IGNORECASE,
    ))
    has_schema_marker = bool(re.search(
        r"(?i)\b(?:exact\s+template|output_json_template|top-level\s+keys|json\s+(?:object|schema|template)|fenced\s+json)\b",
        value,
    ))
    # A multi-tag instruction block or a tagged exact JSON template is strong
    # evidence.  A lone ``<method>...</method>`` in ordinary prose is not.
    return (
        (tag_count >= 2 and (has_template_placeholder or has_schema_marker))
        or ("<final_prompt>" in value.lower() and has_schema_marker)
        or (json_keys >= 4 and has_schema_marker and tag_count >= 1)
    )


def inline_prompt_source_data_line_protected(line: str, state=None):
    """Return ``(protected, state)`` for one line of inline prompt source data.

    The stateful form lets the splitter and final TeX quality scan agree when
    an upstream splitter has already separated a prompt template into tiny
    lines (for example ``<video_description>{description}</...>``).  Activation
    is deliberately strict and bounded; it ends at a code environment/section
    boundary or after 1,200 source lines, so it cannot silently hide an entire
    appendix after a coincidental English phrase.
    """
    current = dict(state or {})
    value = str(line or "")
    active = bool(current.get("active"))
    lines = int(current.get("lines", 0))
    single_line = _is_single_line_source_instruction(value)
    start = is_inline_prompt_source_data_block(value) or bool(
        INLINE_SOURCE_DATA_START_RE.search(value)
        and (
            INLINE_SOURCE_DATA_TAG_RE.search(value)
            or "json" in value.lower()
            or "template" in value.lower()
        )
    )
    protected = single_line or active or start
    if protected:
        lines += max(1, value.count("\n") + 1)
    close = bool(INLINE_SOURCE_DATA_END_RE.search(value))
    current["active"] = bool(
        (active or (start and not single_line))
        and not close
        and lines < 1200
    )
    current["lines"] = lines if current["active"] else 0
    return protected, current


def inline_prompt_source_data_fragment_protected(text: str, state=None):
    """Apply the line policy to a fragment and return ``(protected, state)``.

    A mixed fragment is preserved as a whole.  This small conservative choice
    prevents coalescing an instruction tail into an adjacent prose request;
    the strict start signal keeps ordinary appendix prose translatable.
    """
    current = dict(state or {})
    protected = False
    for line in str(text or "").splitlines(keepends=True) or [str(text or "")]:
        line_protected, current = inline_prompt_source_data_line_protected(
            line,
            current,
        )
        protected = protected or line_protected
    return protected, current


def extract_translation_fragment(prompt_or_fragment: str) -> str:
    """Return only the paper fragment from the upstream translation prompt.

    GPT Academic passes ``prompt + fragment`` as ``inputs_array``.  Language
    validation must not count the English prompt itself, otherwise a short
    LaTeX-only fragment is falsely classified as untranslated.
    """
    value = prompt_or_fragment or ""
    for marker in TRANSLATION_PROMPT_MARKERS:
        if marker in value:
            return value.split(marker, 1)[1]
    return value


def _split_top_level_option_items(value: str) -> Optional[List[str]]:
    """Split commas outside balanced TeX option groups, or return ``None``."""
    pairs = {"{": "}", "[": "]", "(": ")"}
    closers = set(pairs.values())
    stack = []
    parts = []
    start = 0
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char in pairs:
            stack.append(pairs[char])
            continue
        if char in closers:
            if not stack or stack[-1] != char:
                return None
            stack.pop()
            continue
        if char == "," and not stack:
            parts.append(value[start:index].strip())
            start = index + 1
    if stack:
        return None
    parts.append(value[start:].strip())
    return parts


def _top_level_assignment(item: str) -> Optional[Tuple[str, str]]:
    """Return the first top-level key/value split for one option item."""
    pairs = {"{": "}", "[": "]", "(": ")"}
    closers = set(pairs.values())
    stack = []
    escaped = False
    for index, char in enumerate(item):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char in pairs:
            stack.append(pairs[char])
            continue
        if char in closers:
            if not stack or stack[-1] != char:
                return None
            stack.pop()
            continue
        if char == "=" and not stack:
            return item[:index].strip(), item[index + 1:].strip()
    return None


def _is_key_value_option_list(text: str, allow_unbracketed: bool) -> bool:
    """Recognize balanced LaTeX option data while rejecting prose values."""
    value = (text or "").strip()
    bracketed = value.startswith("[") and value.endswith("]")
    if len(value) < 7 or (not bracketed and not allow_unbracketed):
        return False
    inner = value[1:-1] if bracketed else value
    if not bracketed and inner.rstrip().endswith(","):
        inner = inner.rstrip()[:-1].rstrip()
    items = _split_top_level_option_items(inner)
    # Upstream can split the contents of a macro argument away from its
    # opening command while leaving one or two outer macro-closing braces on
    # the option fragment (for example ``title={#1}}``). Peel only trailing
    # unmatched closers, then retain all strict key/value and prose checks.
    if not bracketed:
        trimmed_closers = 0
        while items is None and trimmed_closers < 2 and inner.rstrip().endswith("}"):
            inner = inner.rstrip()[:-1].rstrip()
            trimmed_closers += 1
            items = _split_top_level_option_items(inner)
    minimum_items = 2 if bracketed else 3
    if not items or len(items) < minimum_items or any(not item for item in items):
        return False

    assignment_count = 0
    key_re = re.compile(
        r"/?[A-Za-z][A-Za-z0-9_.:/-]*"
        r"(?:[ \t]+[A-Za-z][A-Za-z0-9_.:/-]*){0,5}"
    )
    prose_value_re = re.compile(
        r"\b[A-Za-z]{2,}\b(?:\s+\b[A-Za-z]{2,}\b){2,}"
    )
    for item in items:
        assignment = _top_level_assignment(item)
        if assignment is None:
            # tcolorbox/pgf lists commonly include bare boolean style keys
            # such as ``breakable`` alongside assignments.
            if not key_re.fullmatch(item):
                return False
            continue
        key, option_value = assignment
        if (
            not key
            or not option_value
            or not key_re.fullmatch(key)
            or len(option_value) > 500
        ):
            return False
        prose_probe = re.sub(r"\\[A-Za-z@]+", " ", option_value)
        prose_probe = re.sub(r"[^A-Za-z\s]", " ", prose_probe)
        if prose_value_re.search(prose_probe):
            return False
        assignment_count += 1
    return assignment_count >= 2


def is_bracketed_key_value_option_list(text: str) -> bool:
    r"""Recognize a pure ``[key=value, ...]`` LaTeX configuration fragment."""
    value = (text or "").strip()
    if not value.startswith("[") or not value.endswith("]"):
        return False
    return _is_key_value_option_list(value, allow_unbracketed=False)


def is_latex_key_value_option_list(text: str) -> bool:
    r"""Recognize bracketed or splitter-detached LaTeX configuration lists.

    Upstream can detach the contents of ``[...]`` into a standalone chunk.
    Requiring three items and two assignments for the unbracketed form avoids
    treating ordinary comma-separated paper prose as configuration.
    """
    return _is_key_value_option_list(text, allow_unbracketed=True)


def is_pure_latex_math_fragment(text: str) -> bool:
    r"""Recognize splitter-detached equations with no natural-language prose.

    Some upstream equation chunks lose their surrounding math environment and
    contain only commands such as ``\mathbf``, ``\text{diag}``, indices and
    operators. A fragment containing ordinary grammatical glue remains
    translatable.
    """
    value = extract_translation_fragment(text or "").strip()
    if not value:
        return False
    commands = re.findall(r"\\[A-Za-z@]+", value)
    math_symbols = re.findall(r"[{}_^=&+*/<>]", value)
    if len(commands) < 4 or len(math_symbols) < 8:
        return False

    probe = re.sub(r"\\[A-Za-z@]+", " ", value)
    words = [word.lower() for word in re.findall(r"\b[A-Za-z]{2,}\b", probe)]
    prose_glue = {
        "a", "an", "the", "and", "or", "of", "to", "in", "on", "for",
        "with", "from", "that", "this", "these", "those", "where", "which",
        "is", "are", "was", "were", "be", "denote", "denotes", "let", "then",
        "such", "given", "we", "our", "by", "as", "if", "when",
    }
    if any(word in prose_glue for word in words):
        return False
    return len(words) <= len(commands) + 8


def strip_inline_code_commands(text: str) -> str:
    """Remove inline code/URL payloads before natural-language heuristics."""
    value = text or ""
    for _ in range(3):
        updated = INLINE_CODE_COMMAND_RE.sub(" ", value)
        if updated == value:
            break
        value = updated
    # Custom code macros often contain escaped JSON braces, which the fast
    # regular expression above intentionally does not try to balance.
    replacements = []
    for match in INLINE_CODE_OPEN_RE.finditer(value):
        depth = 1
        index = match.end()
        while index < len(value):
            char = value[index]
            escaped = index > 0 and value[index - 1] == "\\"
            if char == "{" and not escaped:
                depth += 1
            elif char == "}" and not escaped:
                depth -= 1
                if depth == 0:
                    replacements.append((match.start(), index + 1))
                    break
            index += 1
    for start, end in reversed(replacements):
        value = value[:start] + " " + value[end:]
    # A URL may contain TeX escapes, base64-like query payloads and spaces.
    # Everything from the scheme to the line end is address data, not prose.
    value = re.sub(r"https?://[^\n]*", " ", value, flags=re.IGNORECASE)
    return value


def is_citation_heavy_proper_name_catalog(text: str) -> bool:
    """Recognize a citation-backed catalog of versioned model/dataset names.

    Such a line has no translatable predicate: the correct Chinese output often
    keeps every proper name unchanged and only localizes punctuation.  Treating
    that response as untranslated makes the whole paper fail after identical
    retries.  The test is intentionally strict so an explanatory sentence that
    happens to contain several citations still requires translation.
    """
    value = strip_inline_code_commands(extract_translation_fragment(text))
    citations = CITATION_COMMAND_RE.findall(value)
    if len(citations) < 2:
        return False
    remainder = CITATION_COMMAND_RE.sub(" ", value)
    remainder = re.sub(r"\$[^$]*\$", " ", remainder)
    remainder = re.sub(
        r"\\[A-Za-z@]+\*?(?:\[[^\]]*\])?",
        " ",
        remainder,
    )
    # Decimal version separators are name data; sentence punctuation is prose.
    # A catalog item commonly ends with a single period. Remove only that
    # terminal delimiter; internal sentence punctuation still proves prose.
    punctuation_probe = re.sub(r"[.!?]\s*$", "", remainder)
    punctuation_probe = re.sub(r"(?<=\d)\.(?=\d)", "", punctuation_probe)
    if re.search(r"[.!?]", punctuation_probe):
        return False
    separators = len(re.findall(r"[,;\n]", remainder))
    if separators < len(citations) - 2:
        return False
    tokens = re.findall(
        r"\b[A-Za-z][A-Za-z0-9]*(?:[-_.+][A-Za-z0-9]+)*\b",
        remainder,
    )
    tokens = [
        token for token in tokens
        if token.lower() not in _CATALOG_CONNECTOR_WORDS
    ]
    # A splitter boundary may leave a version-only item (for example
    # ``2.0~\citep{...}``) without an alphabetic token.  Allow one such item,
    # while the prose-word and name-shape checks below remain strict.
    if (
        len(tokens) < max(1, len(citations) - 1)
        or len(tokens) > len(citations) * 4 + 3
    ):
        return False
    lowered = [token.lower() for token in tokens]
    if any(
        word in _CATALOG_PROSE_WORDS and token.islower()
        for token, word in zip(tokens, lowered)
    ):
        return False
    name_like = sum(
        bool(re.search(r"[A-Z0-9_.+-]", token))
        or word in _CATALOG_NAME_WORDS
        for token, word in zip(tokens, lowered)
    )
    return name_like / max(1, len(tokens)) >= 0.8


def is_translated_heading_proper_name_catalog(
    source: str,
    response: str,
) -> bool:
    """Allow a translated short heading followed only by a proper-name list."""
    source_match = re.match(
        r"^\s*\\(?:paragraph|subparagraph)\*?\{[^{}]*\}\s*(?P<tail>.*)$",
        source or "",
        flags=re.DOTALL,
    )
    if not source_match:
        return False
    if len(re.findall(r"[\u4e00-\u9fff]", response or "")) < 4:
        return False
    tail = MATH_SPAN_RE.sub(" ", source_match.group("tail"))
    if len(re.findall(r"[,;\n]", tail)) < 4:
        return False
    tokens = re.findall(
        r"\b[A-Za-z][A-Za-z0-9]*(?:[-_.+][A-Za-z0-9]+)*\b",
        tail,
    )
    if len(tokens) < 5:
        return False
    lowered = [token.lower() for token in tokens]
    if any(
        word in _CATALOG_PROSE_WORDS and token.islower()
        for token, word in zip(tokens, lowered)
    ):
        return False
    name_like = sum(
        bool(re.search(r"[A-Z0-9_.+-]", token))
        or word in _CATALOG_NAME_WORDS
        for token, word in zip(tokens, lowered)
    )
    return name_like / len(tokens) >= 0.8


def _natural_language_probe(text: str) -> str:
    """Remove non-prose LaTeX payloads before language-ratio decisions."""
    value = strip_inline_code_commands(extract_translation_fragment(text))
    # Formatting commands with a configuration argument and a human-text
    # argument must expose only the latter. Otherwise color/style identifiers
    # such as ``iclrdeepblue`` make a correctly localized compact table label
    # look like copied English prose.
    value = re.sub(
        r"\\(?:textcolor|colorbox|href)\*?(?:\[[^\]]*\])?"
        r"\{[^{}]*\}\{([^{}]*)\}",
        r" \1 ",
        value,
    )
    value = re.sub(
        r"\\footnote\{([^{}]*)\}",
        lambda match: (
            " "
            if (
                len(re.findall(r"\d", match.group(1))) >= 3
                and re.search(
                    r"(?i)(?:vllm|cuda|dev\d+|[v-]?\d+\.\d+|"
                    r"\+[a-f0-9]{6,})",
                    match.group(1),
                )
                and len(re.findall(r"\b[a-z]{2,}\b", match.group(1))) <= 2
            )
            else match.group(0)
        ),
        value,
    )
    # Optional item labels may themselves be prose, so expose them before
    # removing generic command names.
    value = re.sub(r"\\item\s*\[([^\]]+)\]", r" \1 ", value)
    value = CITATION_COMMAND_RE.sub(" ", value)
    value = REFERENCE_PAYLOAD_COMMAND_RE.sub(" ", value)
    value = MATH_SPAN_RE.sub(" ", value)
    value = re.sub(
        r"\\(?:begin|end)\{[^{}]+\}(?:\[[^\]]*\])?",
        " ",
        value,
    )
    # Keep braced human text (for example a section title), but remove the
    # command name and options. Reference/citation keys were removed above.
    value = re.sub(
        r"\\[A-Za-z@]+\*?(?:\[[^\]]*\])?",
        " ",
        value,
    )
    return re.sub(r"[{}\\_^$&#~=+*/<>|]", " ", value)


def latex_prose_probe(text: str) -> str:
    """Expose human text while preserving arguments of custom text macros.

    A generic ``\\command{...}`` remover cannot know whether the argument is
    configuration or prose. Removing the whole group hid ordinary text in
    document-defined wrappers such as ``\\compactbullet{Hence ...}``, so those
    lines never became translation chunks. This shared probe removes
    citation/reference/math payloads and command tokens while retaining braced
    human text for the splitter and final quality scan.
    """
    return _natural_language_probe(text)


SHORT_TEXT_WRAPPER_RE = re.compile(
    r"^\s*\\(?:"
    r"[A-Za-z@]*(?:heading|bullet|number)|ii|item|textbf|textit|emph"
    r")\b"
)


def is_translated_prefix_proper_name_catalog(
    source: str,
    response: str,
) -> bool:
    """Allow translated short prose followed only by cited proper names."""
    if len(CITATION_COMMAND_RE.findall(source or "")) < 3:
        return False
    if len(re.findall(r"[\u4e00-\u9fff]", response or "")) < 4:
        return False

    response_probe = _natural_language_probe(response or "")
    tokens = re.findall(
        r"\b[A-Za-z][A-Za-z0-9]*(?:[-_.+][A-Za-z0-9]+)*\b",
        response_probe,
    )
    tokens = [
        token for token in tokens
        if token.lower() not in _CATALOG_CONNECTOR_WORDS
    ]
    if len(tokens) < 3:
        return False
    lowered = [token.lower() for token in tokens]
    if any(
        token.islower() and word not in _CATALOG_NAME_WORDS
        for token, word in zip(tokens, lowered)
    ):
        return False
    name_like = sum(
        bool(re.search(r"[A-Z0-9_.+-]", token))
        or word in _CATALOG_NAME_WORDS
        for token, word in zip(tokens, lowered)
    )
    return name_like / len(tokens) >= 0.8


def is_short_structural_bridge_prose(text: str) -> bool:
    r"""Recognize short prose stranded beside display math or custom wrappers.

    Upstream may preserve ``For the first term,\par`` together with the
    following display, or treat ``\compactbullet{On turn ...}`` as structural
    LaTeX. These fragments are too short for the ordinary paragraph threshold
    but are still visible paper prose. Requiring a text-wrapper/``\par``
    boundary plus three words avoids promoting bare commands or equations.
    """
    value = str(text or "").strip()
    if not value or not (
        SHORT_TEXT_WRAPPER_RE.match(value)
        or re.search(r"\\par\s*$", value)
    ):
        return False
    probe = latex_prose_probe(value)
    letters = len(re.findall(r"[A-Za-z]", probe))
    words = re.findall(r"\b[A-Za-z][A-Za-z'-]{2,}\b", probe)
    return letters >= 12 and len(words) >= 3


def _mixed_language_probe(text: str) -> str:
    """Return ordinary prose while hiding literal English examples/code."""
    value = extract_translation_fragment(text)
    if is_tikz_drawing_fragment(value):
        return ""
    # Failure-box task/trajectory/result payloads are verbatim source data.
    # Keep their surrounding explanation prose eligible for translation.
    value = _strip_source_data_inline_macros(value)
    # Some papers render an entire prompt template directly with ``\\`` line
    # breaks instead of a tracked box environment.  Narrow directive markers
    # distinguish that source data from normal paper prose.
    if "\\\\" in value and PROMPT_TEMPLATE_LINE_RE.search(value):
        return ""
    # Acronym expansions are often lettered with nested ``\underline`` and
    # ``\texttt`` commands (for example ``\underline{\texttt{M}}ulti-...``).
    # Hide the complete expansion from the English-clause detector while
    # leaving ordinary explanatory prose visible.
    value = re.sub(
        r"\\underline\{\\texttt\{[A-Z]\}\}[A-Za-z][A-Za-z-]*",
        " ",
        value,
    )
    for _ in range(3):
        updated = re.sub(
            r"\\(?:emph|textit|texttt|underline|verb)\*?(?:\[[^\]]*\])?\{[^{}]*\}",
            " ",
            value,
        )
        if updated == value:
            break
        value = updated
    # Dataset/task/citation identifiers may be rendered as escaped snake_case
    # or reach this probe as a raw splitter-detached key tail. Their component
    # words (for example ``look\_at\_obj\_in\_light`` or
    # ``when_cl_requires_learning_2026}``) are not an English clause.
    value = re.sub(
        r"\b[A-Za-z0-9]+(?:(?:\\_+|_)[A-Za-z0-9]+){1,}\b",
        " ",
        value,
    )
    value = re.sub(r'["“][^"”\n]*["”]', " ", value)
    value = re.sub(r"``.*?''", " ", value, flags=re.DOTALL)
    return _natural_language_probe(value)


def is_tikz_drawing_fragment(text: str) -> bool:
    """Return true for command-heavy TikZ path fragments, not paper prose."""
    value = text or ""
    commands = re.findall(
        r"\\(?:fill|draw|path|node|coordinate|clip)\b",
        value,
    )
    lowered = value.lower()
    return bool(
        commands
        and (
            "path picture bounding box" in lowered
            or (
                len(commands) >= 2
                and "rectangle" in lowered
                and "shift=" in lowered
            )
        )
    )


def is_tikz_style_definition_fragment(text: str) -> bool:
    """Return true for pgf/TikZ style declarations split across lines."""
    value = text or ""
    return bool(re.search(
        r"(?i)(?:^|[,{\s])[A-Za-z0-9_.:-]+\s*/\."
        r"(?:style|append\s+style|initial)\s*=",
        value,
    ))


def is_http_endpoint_catalog(text: str) -> bool:
    """Recognize a standalone list of HTTP method/path signatures."""
    value = extract_translation_fragment(text or "").strip()
    endpoint_re = re.compile(
        r"(?<![A-Za-z])(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+/\S+"
    )
    matches = endpoint_re.findall(value)
    if len(matches) < 2:
        return False
    remainder = endpoint_re.sub(" ", value)
    remainder = re.sub(r"[\\{}\[\](),;|&+_=?.:/~-]+", " ", remainder)
    return not re.search(r"\b[A-Za-z]{2,}\b", remainder)


def is_detached_citation_key_list(text: str) -> bool:
    """Recognize a splitter-detached ``{bib_key,...}`` argument."""
    value = extract_translation_fragment(text or "").strip()
    if not (value.startswith("{") and value.endswith("}")):
        return False
    keys = [item.strip() for item in value[1:-1].split(",")]
    if len(keys) < 3 or any(not key for key in keys):
        return False
    key_re = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/+-]*")
    if any(not key_re.fullmatch(key) for key in keys):
        return False
    key_like = sum(
        "_" in key or bool(re.search(r"\d", key)) or len(key) >= 12
        for key in keys
    )
    return key_like >= max(2, len(keys) // 2)


def is_structured_identifier_path(text: str) -> bool:
    r"""Recognize a standalone repository/package path, not slash prose.

    Splitter output can expose identifiers such as
    ``icloud-photos-downloader/icloud\_photos\_downloader`` as an isolated
    slot. Translating that value would corrupt it, while retrying an exact
    model echo can fail the whole paper. Require the entire fragment to be a
    path and at least one explicit identifier separator so ordinary prose such
    as ``input/output`` remains eligible for translation.
    """
    value = extract_translation_fragment(text or "").strip()
    value = re.sub(r"[.,;:]$", "", value).strip()
    if not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.+\\-]*"
        r"(?:/[A-Za-z0-9][A-Za-z0-9_.+\\-]*)+",
        value,
    ):
        return False
    return bool(re.search(r"(?:\\_|[_+.-])", value))


def is_person_name_catalog(text: str) -> bool:
    """Recognize a standalone comma-separated author/contributor list."""
    value = extract_translation_fragment(text or "").strip()
    if re.search(r"[.!?。！？]", value):
        return False
    parts = [
        part.strip()
        for part in re.split(r"\s*(?:,|;|\\and\b)\s*", value)
        if part.strip()
    ]
    if len(parts) < 6:
        return False

    def name_like(part: str) -> bool:
        words = re.findall(r"[A-Za-z][A-Za-z'.-]*", part)
        return (
            1 <= len(words) <= 5
            and all(
                word[0].isupper() or re.fullmatch(r"[A-Z]\.?", word)
                for word in words
            )
        )

    return sum(name_like(part) for part in parts) / len(parts) >= 0.85


def is_affiliation_metadata_fragment(text: str) -> bool:
    """Recognize superscripted author-affiliation metadata, not paper prose."""
    value = extract_translation_fragment(text or "").strip()
    if not value or not AFFILIATION_MARKER_RE.match(value):
        return False
    probe = re.sub(
        r"\\[A-Za-z@]+\*?(?:\[[^\]]*\])?(?:\{[^{}]*\})?",
        " ",
        value,
    )
    tokens = re.findall(
        r"\b[A-Za-z][A-Za-z0-9]*(?:[-_.+][A-Za-z0-9]+)*\b",
        probe,
    )
    if not tokens or len(tokens) > 18:
        return False
    # Institution names are normally title-cased/acronym-like.  Requiring a
    # strong name shape prevents a superscripted sentence from being hidden.
    name_like = sum(
        token[0].isupper()
        or token.isupper()
        or bool(re.search(r"\d", token))
        for token in tokens
    )
    return name_like / len(tokens) >= 0.7


def is_graphics_path_fragment(text: str) -> bool:
    """Recognize an isolated image path/options fragment."""
    value = extract_translation_fragment(text or "").strip()
    if not value or not re.search(
        r"(?i)\.(?:pdf|png|jpe?g|eps|svg)(?:\}|\s|$)",
        value,
    ):
        return False
    # A path or includegraphics tail has no grammatical sentence words.  Keep
    # this conservative so prose that merely mentions a file remains eligible.
    if re.search(
        r"(?i)\b(?:the|this|that|is|are|shows|showing|we|our|figure)\b",
        value,
    ):
        return False
    return bool(
        re.search(r"[/{]", value)
        and len(re.findall(r"\b[A-Za-z]{2,}\b", value)) <= 12
    )


def is_formatting_label_fragment(text: str) -> bool:
    """Recognize command-wrapped model/series labels without natural prose."""
    value = extract_translation_fragment(text or "").strip()
    if not re.search(r"\\(?:textcolor|textbf|textit)\b", value):
        return False
    probe = latex_prose_probe(value)
    words = re.findall(r"\b[A-Za-z][A-Za-z0-9+.-]*\b", probe)
    if not words:
        return False
    name_like = sum(
        word[0].isupper() or word.isupper() or any(ch.isdigit() for ch in word)
        for word in words
    )
    return name_like >= 2 and name_like / len(words) >= 0.6


def is_unbalanced_latex_fragment(text: str) -> bool:
    """Protect short command fragments split across a surrounding TeX group."""
    value = extract_translation_fragment(text or "").strip()
    return bool(
        value
        and len(value) <= 320
        and re.search(r"\\[A-Za-z@]+", value)
        and _unescaped_brace_balance(value) != 0
    )


def _scan_structural_input_line(value: str, state: dict) -> tuple[dict, bool]:
    """Advance delimiter state for one external-source input command line."""
    current = dict(state or {})
    bracket_depth = int(current.get("bracket_depth", 0))
    brace_depth = int(current.get("brace_depth", 0))
    optional_seen = bool(current.get("optional_seen"))
    optional_closed = bool(current.get("optional_closed"))
    required_seen = bool(current.get("required_seen"))
    escaped = False
    for char in str(value or ""):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "[" and brace_depth == 0:
            bracket_depth += 1
            optional_seen = True
            continue
        if char == "]" and bracket_depth:
            bracket_depth -= 1
            optional_closed = optional_seen and bracket_depth == 0
            continue
        if char == "{" and bracket_depth == 0:
            if brace_depth == 0:
                required_seen = True
            brace_depth += 1
            continue
        if char == "}" and brace_depth:
            brace_depth -= 1

    lines = int(current.get("lines", 0)) + 1
    complete = required_seen and bracket_depth == 0 and brace_depth == 0
    current.update({
        "active": not complete and lines < 64,
        "bracket_depth": bracket_depth,
        "brace_depth": brace_depth,
        "optional_seen": optional_seen,
        "optional_closed": optional_closed,
        "required_seen": required_seen,
        "lines": lines if not complete and lines < 64 else 0,
    })
    return current, complete


def structural_input_command_line_protected(text: str, state=None):
    """Return ``(protected, state)`` for one source-input command line."""
    value = str(text or "")
    current = dict(state or {})
    active = bool(current.get("active"))
    match = STRUCTURAL_INPUT_COMMAND_RE.search(value)
    if not active and not match:
        return False, {}
    fragment = value if active else value[match.start():]
    next_state, complete = _scan_structural_input_line(fragment, current)
    if complete:
        return True, {}
    return True, next_state


def structural_input_command_fragment_protected(text: str, state=None):
    """Apply the source-input command policy to a possibly multiline fragment."""
    current = dict(state or {})
    protected = False
    for line in str(text or "").splitlines(keepends=True) or [str(text or "")]:
        line_protected, current = structural_input_command_line_protected(
            line,
            current,
        )
        protected = protected or line_protected
    return protected, current


def is_structural_input_command_fragment(text: str) -> bool:
    """Return true when a complete fragment contains a source-input command."""
    return bool(STRUCTURAL_INPUT_COMMAND_RE.search(str(text or "")))


def is_tool_call_result_fragment(text: str) -> bool:
    """Recognize a standalone rendered function call followed by its result."""
    value = extract_translation_fragment(text or "").strip()
    return bool(
        re.match(
            r"^\\texttt\{[A-Za-z][A-Za-z0-9]*(?:\\_+[A-Za-z0-9]+)*"
            r"\([^{}]*\)\}\s*(?:\\;|\$|\s)*\\(?:to|rightarrow)\b",
            value,
        )
    )


def is_plain_prose_line_for_rescue(text: str) -> bool:
    """Identify short top-level prose lines stranded beside display math."""
    value = (text or "").strip()
    if not value or value.startswith("%"):
        return False
    if is_latex_metadata_line(value) or is_affiliation_metadata_fragment(value):
        return False
    if is_formatting_label_fragment(value) or is_unbalanced_latex_fragment(value):
        return False
    if is_inline_prompt_source_data_block(value):
        return False
    if is_tikz_drawing_fragment(value) or is_tikz_style_definition_fragment(value):
        return False
    if is_http_endpoint_catalog(value):
        return False
    if is_detached_citation_key_list(value):
        return False
    if is_bracketed_key_value_option_list(value):
        return False
    if re.search(
        r"\\(?:begin|end)\{|\\(?:\[|\])|\\(?:section|subsection|subsubsection)"
        r"\*?\s*\{",
        value,
    ):
        return False
    probe = _natural_language_probe(value)
    letters = len(re.findall(r"[A-Za-z]", probe))
    words = re.findall(r"\b[A-Za-z][A-Za-z'-]{2,}\b", probe)
    return letters >= 18 and len(words) >= 3


def is_latex_metadata_line(text: str) -> bool:
    """Identify author/institution metadata that may remain in English."""
    return bool(re.match(
        r"^\s*\\(?:"
        r"author|affiliation|icmlaffiliation|institute|institution|address|"
        r"email"
        r")\b",
        text or "",
        flags=re.IGNORECASE,
    ))


def _strip_source_data_inline_macros(value: str) -> str:
    """Remove balanced verbatim failure-box payloads without touching prose."""
    replacements = []
    for match in SOURCE_DATA_INLINE_MACRO_OPEN_RE.finditer(value or ""):
        depth = 1
        index = match.end()
        while index < len(value):
            char = value[index]
            escaped = index > 0 and value[index - 1] == "\\"
            if char == "{" and not escaped:
                depth += 1
            elif char == "}" and not escaped:
                depth -= 1
                if depth == 0:
                    replacements.append((match.start(), index + 1))
                    break
            index += 1
    for start, end in reversed(replacements):
        value = value[:start] + " " + value[end:]
    return value


def mixed_untranslated_english_clauses(text: str):
    """Return high-confidence English clauses embedded in Chinese prose.

    A clause must have four words, grammatical glue, one lower-case content
    words, and Chinese on the same fragment.  This deliberately ignores
    ordinary English titles/proper names and intentionally preserved inline
    prompts, code, citations, URLs, and equations.
    """
    probe = _mixed_language_probe(text)
    if len(re.findall(r"[\u4e00-\u9fff]", probe)) < 6:
        return []
    matches = list(MIXED_ENGLISH_WORD_RE.finditer(probe))
    clauses = []
    current = []
    previous_end = 0
    for match in matches:
        gap = probe[previous_end:match.start()] if current else ""
        if current and (
            re.search(r"[\u4e00-\u9fff.!?]", gap)
            or re.search(r"[^\s,;:()\[\]{}~\\'\"-]", gap)
        ):
            clauses.extend(_qualifying_mixed_english_clause(current))
            current = []
        current.append(match)
        previous_end = match.end()
    if current:
        clauses.extend(_qualifying_mixed_english_clause(current))
    return clauses


def absorb_short_prose_bridges(
    fragments: Iterable[Tuple[str, bool]],
    max_translate_chars: int = DEFAULT_TRANSLATION_CHUNK_LIMIT,
) -> List[Tuple[str, bool]]:
    r"""Attach short preserved prose around citations to a neighboring chunk.

    Upstream may split a paragraph at ``\cite{...}`` and preserve pieces such
    as ``\cite{key}, including``.  Those pieces are too short to translate on
    their own, but leaving them untouched creates English seams between Chinese
    chunks.  Absorb only fragments containing ordinary prose and citation/ref
    commands; structural or arbitrary LaTeX commands remain protected.
    """
    items = [[str(text), bool(preserve)] for text, preserve in fragments]

    def has_unclosed_citation(value: str) -> bool:
        """Return true when an upstream split cut through a citation payload."""
        match = re.search(
            r"\\(?:cite|citep|citet|citealp|citeauthor|citeyear|parencite|textcite)"
            r"\*?(?:\[[^\]]*\])?\s*\{",
            value or "",
        )
        if not match:
            return False
        tail = (value or "")[match.start():]
        return tail.count("{") > tail.count("}")

    def bridgeable(value: str) -> bool:
        stripped = value.strip()
        if not stripped or is_inline_prompt_source_data_block(stripped):
            return False
        probe = CITATION_COMMAND_RE.sub(" ", stripped)
        # Upstream can split a multiline citation in the middle of its key
        # list, leaving ``\cite{alpha,`` in a PRESERVE fragment and
        # ``beta}. Prose`` in the neighboring TRANSFORM fragment. Remove the
        # command prefix for bridge classification; after absorption the
        # completed citation is still checked by the exact multiset gate.
        probe = re.sub(
            r"\\(?:cite|citep|citet|citealp|citeauthor|citeyear|parencite|textcite)"
            r"\*?(?:\[[^\]]*\])?\s*\{?",
            " ",
            probe,
        )
        probe = REFERENCE_PAYLOAD_COMMAND_RE.sub(" ", probe)
        probe = MATH_SPAN_RE.sub(" ", probe)
        if re.search(r"\\[A-Za-z@]+", probe):
            return False
        words = re.findall(r"\b[A-Za-z][A-Za-z'-]{2,}\b", probe)
        letters = len(re.findall(r"[A-Za-z]", probe))
        return letters >= 5 and bool(words)

    changed = True
    while changed:
        changed = False
        for index, (value, preserve) in enumerate(items):
            if not preserve or not bridgeable(value):
                continue
            if has_unclosed_citation(value) and index + 1 < len(items):
                # Structural integrity wins over the soft request-size cap:
                # attach an incomplete ``\cite{alpha,`` prefix to the fragment
                # containing its closing brace. The completed fragment can
                # still remain a separate bounded translation request.
                items[index + 1][0] = value + items[index + 1][0]
                del items[index]
                changed = True
                break
            if (
                index > 0
                and not items[index - 1][1]
                and (
                    len(items[index - 1][0]) + len(value)
                    <= recommended_translation_chunk_limit(
                        items[index - 1][0] + value,
                        max_translate_chars,
                    )
                )
            ):
                items[index - 1][0] += value
                del items[index]
                changed = True
                break
            if (
                index + 1 < len(items)
                and not items[index + 1][1]
                and (
                    len(value) + len(items[index + 1][0])
                    <= recommended_translation_chunk_limit(
                        value + items[index + 1][0],
                        max_translate_chars,
                    )
                )
            ):
                items[index + 1][0] = value + items[index + 1][0]
                del items[index]
                changed = True
                break
    return [(text, preserve) for text, preserve in items]


def _qualifying_mixed_english_clause(matches):
    tokens = [match.group(0) for match in matches]
    lowered = [token.lower() for token in tokens]
    content = [
        token for token, lowered_token in zip(tokens, lowered)
        if lowered_token not in MIXED_ENGLISH_CLAUSE_GLUE_WORDS
    ]
    if (
        len(tokens) < 4
        or sum(len(token) for token in tokens) < 12
        or not any(token in MIXED_ENGLISH_CLAUSE_GLUE_WORDS for token in lowered)
        or not content
        or not any(token[0].islower() for token in content)
    ):
        return []
    return [{
        "text": " ".join(tokens)[:220],
        "words": len(tokens),
        "letters": sum(len(token) for token in tokens),
    }]


def llm_translation_response_untranslated(source: str, response: str) -> bool:
    """Detect prose-like source chunks whose response still lacks Chinese."""
    if llm_translation_response_failed(response):
        return True
    raw_source = extract_translation_fragment(source)
    if (
        is_structured_identifier_path(raw_source)
        or is_person_name_catalog(raw_source)
        or is_affiliation_metadata_fragment(raw_source)
        or is_graphics_path_fragment(raw_source)
        or is_formatting_label_fragment(raw_source)
        or is_unbalanced_latex_fragment(raw_source)
        or is_tool_call_result_fragment(raw_source)
        or is_structural_input_command_fragment(raw_source)
    ):
        return False
    source_value = strip_inline_code_commands(raw_source)
    if is_inline_prompt_source_data_block(source_value):
        return False
    if (
        is_graphics_path_fragment(source_value)
        or is_formatting_label_fragment(source_value)
        or is_unbalanced_latex_fragment(source_value)
    ):
        return False
    if (
        is_tikz_drawing_fragment(source_value)
        or is_tikz_style_definition_fragment(source_value)
        or is_http_endpoint_catalog(source_value)
        or is_detached_citation_key_list(source_value)
        or is_pure_latex_math_fragment(source_value)
    ):
        return False
    if is_latex_key_value_option_list(source_value):
        return False
    if is_citation_heavy_proper_name_catalog(source_value):
        return False
    if is_translated_heading_proper_name_catalog(
        source_value,
        response or "",
    ):
        return False
    if is_translated_prefix_proper_name_catalog(
        source_value,
        response or "",
    ):
        return False
    source_prose = _natural_language_probe(source_value)
    response_prose = _natural_language_probe(response or "")
    # A mostly Chinese response can still leave a grammatical English clause
    # behind.  The historic ratio-only test missed exactly this shape; reject
    # the slot before merge so the existing serialized retry path repairs it.
    if mixed_untranslated_english_clauses(response or ""):
        return True
    source_letters = len(re.findall(r"[A-Za-z]", source_prose))
    source_words = re.findall(r"\b[A-Za-z][A-Za-z'-]{1,}\b", source_prose)
    source_cjk = len(re.findall(r"[\u4e00-\u9fff]", source_prose))
    response_letters = len(re.findall(r"[A-Za-z]", response_prose))
    response_words = re.findall(
        r"\b[A-Za-z][A-Za-z'-]{1,}\b",
        response_prose,
    )
    response_cjk = len(re.findall(r"[\u4e00-\u9fff]", response_prose))

    # Short prose used to rely on long reference keys to cross the generic
    # 40-letter threshold. Once those keys are correctly removed, retain a
    # narrow exact-copy detector so a genuine English echo is still rejected.
    source_word_text = " ".join(word.lower() for word in source_words)
    response_word_text = " ".join(word.lower() for word in response_words)
    copied_short_prose = (
        source_letters >= 12
        and len(source_words) >= 3
        and source_cjk < 8
        and response_letters >= 10
        and response_cjk < 6
        and difflib.SequenceMatcher(
            None,
            source_word_text,
            response_word_text,
        ).ratio() >= 0.92
    )
    return copied_short_prose or (
        source_letters >= 40
        and len(source_words) >= 6
        and source_cjk < 8
        and response_letters >= 24
        and response_cjk < 6
    )


TRANSLATION_STRUCTURAL_UNIT_RE = re.compile(
    r"\\(?:section|subsection|subsubsection|paragraph|subparagraph)"
    r"\*?\s*\{"
)


def starts_translation_structural_unit(text: str) -> bool:
    return bool(TRANSLATION_STRUCTURAL_UNIT_RE.match((text or "").lstrip()))


def split_translation_structural_units(text: str) -> List[str]:
    """Split prose before headings so distinct semantic units stay separate."""
    value = str(text or "")
    starts = [
        match.start()
        for match in TRANSLATION_STRUCTURAL_UNIT_RE.finditer(value)
    ]
    if not starts:
        return [value] if value else []
    boundaries = [0] + [start for start in starts if start > 0] + [len(value)]
    return [
        value[boundaries[index]:boundaries[index + 1]]
        for index in range(len(boundaries) - 1)
        if boundaries[index] < boundaries[index + 1]
    ]


def recommended_translation_chunk_limit(
    text: str,
    default: int = DEFAULT_TRANSLATION_CHUNK_LIMIT,
) -> int:
    """Return the shared context/structure-aware request cap."""
    return translation_chunk_limit(text, default)


def adaptive_translation_retry_fragments(
    text: str,
    max_translate_chars: int = 480,
) -> List[str]:
    """Subdivide one proven-bad response without changing healthy requests."""
    value = str(text or "")
    if len(value) <= max_translate_chars:
        return [value] if value else []
    return split_translation_line_bounded(
        value,
        max_translate_chars=max_translate_chars,
        min_sentence_chars=min(160, max_translate_chars),
    )


def coalesce_translation_fragments(
    fragments: Iterable[Tuple[str, bool]],
    max_translate_chars: int = DEFAULT_TRANSLATION_CHUNK_LIMIT,
) -> List[Tuple[str, bool]]:
    """Merge prose fragments without recreating tiny or unbounded requests.

    Whitespace-only PRESERVE fragments between two prose chunks are formatting,
    not a semantic boundary. Absorb them into the translation request, while
    retaining structural LaTeX boundaries and a conservative request-size cap.
    """
    items = []
    inline_source_state = {}
    structural_input_state = {}
    for text, preserve in fragments:
        if not text:
            continue
        inline_source, inline_source_state = (
            inline_prompt_source_data_fragment_protected(
                text,
                inline_source_state,
            )
        )
        structural_input, structural_input_state = (
            structural_input_command_fragment_protected(
                text,
                structural_input_state,
            )
        )
        items.append((
            text,
            bool(
                preserve
                or inline_source
                or structural_input
                or is_bracketed_key_value_option_list(text)
            ),
        ))
    result: List[Tuple[str, bool]] = []
    pending_whitespace = ""
    for index, (text, preserve) in enumerate(items):
        next_is_translate = (
            index + 1 < len(items)
            and not items[index + 1][1]
        )
        if (
            preserve
            and text.isspace()
            and result
            and not result[-1][1]
            and next_is_translate
        ):
            pending_whitespace += text
            continue

        if pending_whitespace:
            combined = (
                result[-1][0] + pending_whitespace + text
                if result else text
            )
            combined_limit = recommended_translation_chunk_limit(
                combined,
                max_translate_chars,
            )
            if (
                not preserve
                and not starts_translation_structural_unit(text)
                and len(combined) <= combined_limit
            ):
                previous, _ = result[-1]
                result[-1] = (
                    previous + pending_whitespace + text,
                    False,
                )
                pending_whitespace = ""
                continue
            result.append((pending_whitespace, True))
            pending_whitespace = ""

        combined = (result[-1][0] + text) if result else text
        combined_limit = recommended_translation_chunk_limit(
            combined,
            max_translate_chars,
        )
        can_merge = (
            result
            and result[-1][1] == preserve
            and (preserve or not starts_translation_structural_unit(text))
            and (
                preserve
                or len(combined) <= combined_limit
            )
        )
        if can_merge:
            previous, _ = result[-1]
            result[-1] = (previous + text, preserve)
        else:
            result.append((text, preserve))
    if pending_whitespace:
        result.append((pending_whitespace, True))
    return result


def split_translation_line_bounded(
    line: str,
    max_translate_chars: int = DEFAULT_TRANSLATION_CHUNK_LIMIT,
    min_sentence_chars: int = 180,
) -> List[str]:
    r"""Split prose without cutting open TeX groups or inline math delimiters.

    Sentence punctuation is preferred, then top-level whitespace, then a plain
    text character boundary.  ``$...$``, ``$$...$$``, ``\(...\)``, ``\[...\]``
    and brace groups are kept balanced.  A single protected TeX group may
    necessarily exceed the cap, but ordinary unpunctuated prose remains bounded.
    """
    value = str(line or "")
    if not value:
        return []
    max_chars = max(1, int(max_translate_chars))
    min_chars = max(1, min(int(min_sentence_chars), max_chars))

    safe_positions = []
    whitespace_positions = []
    sentence_positions = []
    brace_depth = 0
    math_mode = ""
    index = 0

    def escaped(position):
        backslashes = 0
        cursor = position - 1
        while cursor >= 0 and value[cursor] == "\\":
            backslashes += 1
            cursor -= 1
        return backslashes % 2 == 1

    while index < len(value):
        token_end = index + 1
        if value.startswith(r"\(", index) and not escaped(index):
            if not math_mode:
                math_mode = "paren"
            token_end = index + 2
        elif value.startswith(r"\)", index) and not escaped(index):
            if math_mode == "paren":
                math_mode = ""
            token_end = index + 2
        elif value.startswith(r"\[", index) and not escaped(index):
            if not math_mode:
                math_mode = "bracket"
            token_end = index + 2
        elif value.startswith(r"\]", index) and not escaped(index):
            if math_mode == "bracket":
                math_mode = ""
            token_end = index + 2
        elif value.startswith("$$", index) and not escaped(index):
            math_mode = "" if math_mode == "display-dollar" else (
                "display-dollar" if not math_mode else math_mode
            )
            token_end = index + 2
        elif value[index] == "$" and not escaped(index):
            math_mode = "" if math_mode == "dollar" else (
                "dollar" if not math_mode else math_mode
            )
        elif not math_mode and value[index] == "{" and not escaped(index):
            brace_depth += 1
        elif (
            not math_mode
            and value[index] == "}"
            and not escaped(index)
            and brace_depth > 0
        ):
            brace_depth -= 1
        elif (
            not math_mode
            and brace_depth == 0
            and value[index] == "\\"
            and index + 1 < len(value)
            and value[index + 1].isalpha()
        ):
            token_end = index + 2
            while token_end < len(value) and (
                value[token_end].isalpha() or value[token_end] == "@"
            ):
                token_end += 1
            if token_end < len(value) and value[token_end] == "*":
                token_end += 1

        index = token_end
        if math_mode or brace_depth:
            continue

        safe_positions.append(index)
        previous = value[index - 1]
        if previous.isspace():
            whitespace_positions.append(index)
        if previous in ".;!?。；！？":
            following = value[index] if index < len(value) else ""
            if not following or following.isspace():
                sentence_positions.append(index)

    if not safe_positions or safe_positions[-1] != len(value):
        safe_positions.append(len(value))

    parts = []
    start = 0
    while start < len(value):
        remaining = len(value) - start
        preferred = next(
            (
                position
                for position in sentence_positions
                if start + min_chars <= position <= start + max_chars
            ),
            None,
        )
        if preferred is not None:
            end = preferred
            while (
                end < len(value)
                and value[end].isspace()
                and value[end] != "\n"
                and end - start < max_chars
            ):
                end += 1
        elif remaining <= max_chars:
            end = len(value)
        else:
            whitespace = [
                position
                for position in whitespace_positions
                if start < position <= start + max_chars
            ]
            safe = [
                position
                for position in safe_positions
                if start < position <= start + max_chars
            ]
            if whitespace:
                end = whitespace[-1]
            elif safe:
                end = safe[-1]
            else:
                # The cap falls inside one protected TeX group/math span.  Move
                # to its first balanced boundary instead of emitting malformed
                # chunks; ordinary prose always has safe character boundaries.
                end = next(
                    (position for position in safe_positions if position > start),
                    len(value),
                )
        if end <= start:
            end = min(len(value), start + max_chars)
        parts.append(value[start:end])
        start = end
    return parts


def enforce_translation_fragment_limits(
    fragments: Iterable[Tuple[str, bool]],
    max_translate_chars: int = DEFAULT_TRANSLATION_CHUNK_LIMIT,
) -> List[Tuple[str, bool]]:
    """Apply the dynamic request-size invariant after all merge/bridge passes."""
    bounded: List[Tuple[str, bool]] = []
    for text, preserve in fragments:
        value = str(text)
        if preserve or not value.strip():
            bounded.append((value, True))
            continue
        limit = recommended_translation_chunk_limit(
            value,
            max_translate_chars,
        )
        if len(value) <= limit:
            bounded.append((value, False))
            continue
        bounded.extend(
            (part, not part.strip())
            for part in split_translation_line_bounded(
                value,
                max_translate_chars=limit,
                min_sentence_chars=180,
            )
        )
    return bounded


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


def find_uncommented_latex_token(text: str, token: str) -> int:
    """Return the first TeX token position outside an unescaped ``%`` comment."""
    offset = 0
    for line in (text or "").splitlines(keepends=True):
        comment_at = len(line)
        for index, char in enumerate(line):
            if char != "%":
                continue
            backslashes = 0
            cursor = index - 1
            while cursor >= 0 and line[cursor] == "\\":
                backslashes += 1
                cursor -= 1
            if backslashes % 2 == 0:
                comment_at = index
                break
        position = line[:comment_at].find(token)
        if position >= 0:
            return offset + position
        offset += len(line)
    return -1


def _safe_top_level_line_start(text: str, position: int) -> int:
    """Return a line boundary before ``position`` that is outside TeX groups.

    A command use can occur inside a multi-line ``\newcommand`` body.  Inserting
    a fallback directly before that use makes the fallback local to the macro
    body, so it disappears before the command is expanded.  Track brace depth
    and comments and move the insertion point back to the line that opened the
    surrounding top-level construct.
    """
    source = text or ""
    limit = max(0, min(position, len(source)))
    brace_depth = 0
    bracket_depth = 0
    line_start = 0
    safe_line_start = 0
    index = 0
    in_comment = False

    while index < limit:
        char = source[index]
        if char == "\n":
            index += 1
            line_start = index
            in_comment = False
            if brace_depth == 0 and bracket_depth == 0:
                safe_line_start = line_start
            continue
        if in_comment:
            index += 1
            continue
        if char == "%":
            in_comment = True
            index += 1
            continue
        if char == "\\":
            # A control symbol such as ``\{`` does not open a TeX group.
            index += 2
            continue
        if char == "{":
            if (
                brace_depth == 0
                and bracket_depth == 0
                and line_start == safe_line_start
            ):
                safe_line_start = line_start
            brace_depth += 1
        elif char == "}" and brace_depth:
            brace_depth -= 1
        elif char == "[":
            if (
                brace_depth == 0
                and bracket_depth == 0
                and line_start == safe_line_start
            ):
                safe_line_start = line_start
            bracket_depth += 1
        elif char == "]" and bracket_depth:
            bracket_depth -= 1
        index += 1

    return safe_line_start if (brace_depth or bracket_depth) else line_start


def insert_latex_preamble_snippet(
    text: str,
    insertion: str,
    command_markers: Iterable[str] = (),
) -> Tuple[str, bool]:
    """Insert a snippet at a top-level preamble boundary.

    The earliest command marker still determines ordering, but a marker nested
    in a macro argument is mapped back to the top-level line that owns that
    argument.  This prevents fallback definitions from becoming accidentally
    local while retaining the previous "before first use" behavior.
    """
    snippet = insertion.strip()
    if not snippet or snippet in text:
        return text, False

    positions = []
    for marker in command_markers:
        token = marker if marker.startswith("\\") else "\\" + marker
        pos = find_uncommented_latex_token(text, token)
        if pos >= 0:
            positions.append(pos)

    begin_doc = find_uncommented_latex_token(text, r"\begin{document}")
    if positions:
        pos = min(positions)
        if begin_doc < 0 or pos < begin_doc:
            insert_at = _safe_top_level_line_start(text, pos)
            return text[:insert_at] + insertion + "\n" + text[insert_at:], True

    if begin_doc >= 0:
        insert_at = _safe_top_level_line_start(text, begin_doc)
        return text[:insert_at] + insertion + "\n" + text[insert_at:], True
    return text + ("" if text.endswith("\n") else "\n") + insertion + "\n", True


def _insert_latex_preamble_snippet(
    text: str,
    insertion: str,
    command_markers: Iterable[str] = (),
) -> Tuple[str, bool]:
    return insert_latex_preamble_snippet(text, insertion, command_markers)


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


def normalize_tex_include_target(target: str) -> str:
    r"""Normalize harmless whitespace inside ``\input{...}`` references."""
    return (target or "").strip()


def is_dynamic_tex_include_target(target: str) -> bool:
    r"""Return true when an include target is resolved through TeX macros."""
    value = normalize_tex_include_target(target)
    return bool(re.search(r"\\[A-Za-z@]+|#[1-9]", value))


def requires_runtime_tex_scope(text: str) -> bool:
    r"""Return true when flattening a TeX file can change its execution scope.

    Files that change catcodes or use ``@``-named internals must be read by
    TeX at their original call site.  Inlining them into a macro argument
    tokenizes the body under the caller's catcodes and can make otherwise
    valid generated figures fail before their local setup runs.
    """
    source = text or ""
    return bool(
        re.search(r"\\make(?:atletter|atother)\b", source)
        or re.search(r"\\catcode\s*`", source)
    )


def fontawesome_command_names(text: str) -> Tuple[str, ...]:
    """Return FontAwesome-style zero-argument commands used by a document."""
    names = set(re.findall(r"\\(fa[A-Z][A-Za-z]+)\b", text or ""))
    names.discard("faIcon")  # faIcon itself takes a mandatory icon-name argument.
    return tuple(sorted(names))


def add_fontawesome_legacy_aliases(
    text: str,
    sibling_text: str = "",
) -> Tuple[str, int]:
    r"""Add deterministic FontAwesome fallbacks at top-level preamble scope.

    Older versions inserted the tagged block immediately before the first icon
    token.  When that token lived inside a ``\newcommand`` body, every
    ``\providecommand`` became local and the icon was still undefined at use
    time.  Remove all historical tagged blocks first, then rebuild one block at
    a safe top-level boundary.
    """
    marker = "% paper-trans fallback for fontawesome5 legacy aliases"
    tagged_block = re.compile(
        r"(?m)^" + re.escape(marker) + r"\r?\n"
        r"(?:\\providecommand\{\\fa[A-Za-z]+\}\{[^\n]*\}\r?\n?)+"
    )
    source, removed_blocks = tagged_block.subn("", text or "")

    aliases = {
        "faFile": ("file", "F"),
        "faGlobe": ("globe", "G"),
        "faGithub": ("github", "GH"),
        "faSearch": ("search", "S"),
        "faTrophy": ("trophy", "T"),
        "faDatabase": ("database", "DB"),
        "faEnvelope": ("envelope", "@"),
        "faEnvelopeO": ("envelope", "@"),
        "faGem": ("gem", "*"),
    }
    combined = source + "\n" + (sibling_text or "")
    names = [
        name
        for name in fontawesome_command_names(combined)
        if not _latex_command_defined(combined, name)
    ]
    if not names:
        return source, removed_blocks

    lines = [marker]
    for name in names:
        alias = aliases.get(name)
        if alias is None:
            lines.append(r"\providecommand{\%s}{\textbullet}" % name)
            continue
        icon, fallback = alias
        lines.append(
            r"\providecommand{\%s}{\ifcsname faIcon\endcsname\faIcon{%s}"
            r"\else\textcircled{%s}\fi}" % (name, icon, fallback)
        )

    insertion = "\n".join(lines)
    fixed, inserted = insert_latex_preamble_snippet(source, insertion, names)
    if not inserted:
        return source, removed_blocks
    if fixed == text:
        return text, 0
    return fixed, max(len(names), removed_blocks)


def restore_environment_opening_options(
    translated: str, original: str, environment: str
) -> Tuple[str, int]:
    """Restore bracketed environment options by occurrence from the source TeX."""

    def spans(source: str):
        results = []
        pattern = re.compile(r"\\begin\{" + re.escape(environment) + r"\}")
        for match in pattern.finditer(source):
            pos = match.end()
            while pos < len(source) and source[pos].isspace():
                pos += 1
            if pos >= len(source) or source[pos] != "[":
                continue
            depth = 0
            escaped = False
            for end in range(pos, len(source)):
                char = source[end]
                if escaped:
                    escaped = False
                    continue
                if char == "\\":
                    escaped = True
                    continue
                if char == "[":
                    depth += 1
                elif char == "]":
                    depth -= 1
                    if depth == 0:
                        results.append((pos, end + 1, source[pos:end + 1]))
                        break
        return results

    translated_spans = spans(translated or "")
    original_spans = spans(original or "")
    replacements = []
    for translated_span, original_span in zip(translated_spans, original_spans):
        start, end, current = translated_span
        source_options = original_span[2]
        if current != source_options:
            replacements.append((start, end, source_options))

    fixed = translated or ""
    for start, end, source_options in reversed(replacements):
        fixed = fixed[:start] + source_options + fixed[end:]
    return fixed, len(replacements)


def remove_unmatched_environment_endings(
    text: str,
    environments: Iterable[str] = ("tcolorbox",),
) -> Tuple[str, int]:
    """Remove closing tags that have no matching earlier opening tag."""
    source = text or ""
    allowed = set(environments)
    depths = {name: 0 for name in allowed}
    removals = []
    pattern = re.compile(r"\\(?P<kind>begin|end)\{(?P<name>[^{}]+)\}")
    for match in pattern.finditer(source):
        name = match.group("name")
        if name not in allowed:
            continue
        if match.group("kind") == "begin":
            depths[name] += 1
        elif depths[name]:
            depths[name] -= 1
        else:
            removals.append((match.start(), match.end()))

    fixed = source
    for start, end in reversed(removals):
        fixed = fixed[:start] + fixed[end:]
    return fixed, len(removals)


def normalize_tikz_matrix_node_linebreaks(text: str) -> Tuple[str, int]:
    """Replace inline ``\\`` inside TikZ matrix node text with a safe space."""
    source = text or ""
    pattern = re.compile(
        r"\\matrix\b.*?^[ \t]*\};[ \t]*$",
        re.DOTALL | re.MULTILINE,
    )
    total = 0

    def replace_block(match):
        nonlocal total
        block, count = re.subn(
            r"(?<=[A-Za-z0-9)])\\\\(?=[A-Za-z0-9(])",
            " ",
            match.group(0),
        )
        total += count
        return block

    return pattern.sub(replace_block, source), total


def disable_fragile_tikz_matrix_legends(text: str) -> Tuple[str, int]:
    """Omit matrix-of-nodes legends that embed explicit node/draw commands."""
    pattern = re.compile(
        r"\\matrix\b.*?^[ \t]*\};[ \t]*$",
        re.DOTALL | re.MULTILINE,
    )

    total = 0

    def replace(match):
        nonlocal total
        block = match.group(0)
        if "matrix of nodes" not in block:
            return block
        # A matrix-of-nodes is also a valid way to build the main diagram.
        # Restrict this lossy fallback to blocks positioned as legends around
        # the current picture bounding box (or explicitly named as a legend).
        lowered = block.lower()
        if "current bounding box" not in lowered and "legend" not in lowered:
            return block
        if r"\node" not in block and r"\draw" not in block:
            return block
        total += 1
        return "% paper-trans: omitted incompatible TikZ matrix legend"

    return pattern.sub(replace, text or ""), total


_TAGGED_PROVIDECOMMAND_FALLBACK_RE = re.compile(
    r"(?m)^(?P<block>% paper-trans fallback[^\r\n]*\r?\n"
    r"(?:\\providecommand[^\r\n]*(?:\r?\n|$))+)"
)


def _relocate_unsafe_tagged_preamble_fallbacks(text: str) -> Tuple[str, int]:
    """Move generated providecommand blocks out of multiline TeX arguments."""
    source = text or ""
    moved = 0
    matches = list(_TAGGED_PROVIDECOMMAND_FALLBACK_RE.finditer(source))
    for match in reversed(matches):
        line_start = source.rfind("\n", 0, match.start()) + 1
        safe_start = _safe_top_level_line_start(source, match.start())
        if safe_start == line_start:
            continue
        block = match.group("block").rstrip("\r\n")
        source = source[:match.start()] + source[match.end():]
        source = source[:safe_start] + block + "\n" + source[safe_start:]
        moved += 1
    return source, moved


def add_xelatex_compatibility_fallbacks(text: str) -> Tuple[str, int]:
    """Add safe fallbacks for templates assuming pdfLaTeX/inputenc/fontspec state."""
    source = text or ""
    source, historical_repairs = _relocate_unsafe_tagged_preamble_fallbacks(
        source
    )

    # CJKutf8's tilde activation belongs to its legacy 8-bit input path.  The
    # translated document is compiled by a Unicode engine, where the command
    # may not exist and has no useful work left to do.  Normalize the setup
    # directive here instead of teaching the compiler to ignore its error.
    source, legacy_cjk_setup_repairs = re.subn(
        r"(?m)^(?P<indent>[ \t]*)\\CJKtilde[ \t]*(?:%[^\r\n]*)?$",
        r"\g<indent>% paper-trans: omitted legacy CJK tilde activation",
        source,
    )

    # v4.36 located ``\begin{document}`` with a raw string search.  A template
    # comment containing that example token could therefore be split by the
    # natbib fallback and turn the remaining comment tail into executable TeX.
    broken_natbib_comment = "\n".join([
        r"% Outer hook fires during % paper-trans fallback for missing natbib citation commands",
        r"\providecommand{\citep}[2][]{\cite{#2}}",
        r"\providecommand{\citet}[2][]{\cite{#2}}",
        r"\begin{document} processing;",
    ])
    if broken_natbib_comment in source:
        historical_repairs = source.count(broken_natbib_comment)
        source = source.replace(
            broken_natbib_comment,
            r"% Outer hook fires during \begin{document} processing;",
        )

    # Replace the first-generation CJK fallback, which used ``providecommand``
    # with ``\csname`` and can emit "already defined" errors on CJKutf8.
    legacy_cjk = "\n".join([
        r"% paper-trans fallback for legacy CJK environments under XeLaTeX",
        r"\expandafter\providecommand\csname CJK\endcsname[2]{}",
        r"\expandafter\providecommand\csname endCJK\endcsname{}",
        r"\expandafter\providecommand\csname CJK*\endcsname[2]{}",
        r"\expandafter\providecommand\csname endCJK*\endcsname{}",
        r"\providecommand{\CJKfamily}[1]{}",
    ])
    source = source.replace(legacy_cjk + "\n", "").replace(legacy_cjk, "")

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
    needs_textls_fallback = (
        r"\textls" in source
        and not _latex_command_defined(source, "textls")
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
    needs_natbib_cite_fallback = (
        (r"\citep" in source or r"\citet" in source)
        and not _latex_package_loaded(source, "natbib")
    )
    needs_mathbb_fallback = (
        r"\mathbb" in source
        and not _latex_command_defined(source, "mathbb")
        and not _latex_package_loaded(source, "amsfonts")
        and not _latex_package_loaded(source, "amssymb")
    )
    needs_appendices_fallback = (
        r"\begin{appendices}" in source
        and not re.search(r"\\(?:newenvironment|renewenvironment)\s*\{appendices\}", source)
        and not _latex_package_loaded(source, "appendix")
    )
    needs_booktabs_fallback = (
        any(command in source for command in (r"\toprule", r"\midrule", r"\bottomrule"))
        and not _latex_package_loaded(source, "booktabs")
    )
    needs_multirow_fallback = (
        r"\multirow" in source
        and not _latex_command_defined(source, "multirow")
        and not _latex_package_loaded(source, "multirow")
    )
    needs_bbding_symbol_fallback = any(
        command in source
        for command in (r"\CheckmarkBold", r"\XSolidBrush")
    )
    needs_cjk_environment_fallback = bool(
        re.search(r"\\(?:begin|end)\{CJK\*?\}", source)
        # Some legacy papers load CJKutf8 but the package does not expose the
        # CJK environments under XeLaTeX (or the slim image's compatibility
        # layer).  The emitted ``ifcsname`` guards are no-ops when the package
        # really did define them, so key this fallback on actual source
        # environments rather than the package declaration alone.
        and not _latex_command_defined(source, "CJK")
    )

    total = historical_repairs + legacy_cjk_setup_repairs
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

    if needs_textls_fallback:
        insertion = "\n".join([
            r"% paper-trans fallback for unavailable microtype tracking",
            r"\providecommand{\textls}[2][]{#2}",
        ])
        source, changed = _insert_latex_preamble_snippet(source, insertion, ["textls"])
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

    if needs_natbib_cite_fallback:
        insertion = "\n".join([
            r"% paper-trans fallback for missing natbib citation commands",
            r"\providecommand{\citep}[2][]{\cite{#2}}",
            r"\providecommand{\citet}[2][]{\cite{#2}}",
        ])
        source, changed = _insert_latex_preamble_snippet(source, insertion, ["citep", "citet"])
        total += int(changed)

    if needs_mathbb_fallback:
        insertion = "\n".join([
            r"% paper-trans fallback for missing AMS blackboard bold",
            r"\providecommand{\mathbb}[1]{\mathbf{#1}}",
        ])
        source, changed = _insert_latex_preamble_snippet(source, insertion, ["mathbb"])
        total += int(changed)

    if needs_appendices_fallback:
        insertion = "\n".join([
            r"% paper-trans fallback for missing appendices environment",
            r"\newenvironment{appendices}{\appendix}{}",
        ])
        source, changed = _insert_latex_preamble_snippet(source, insertion, ["appendices"])
        total += int(changed)

    if needs_booktabs_fallback:
        source, cmidrule_count = re.subn(
            r"\\cmidrule(?:\([^)]*\))?\{[^{}]+\}", r"\\hline", source
        )
        total += cmidrule_count
        insertion = "\n".join([
            r"% paper-trans fallback for missing booktabs package",
            r"\providecommand{\toprule}{\hline}",
            r"\providecommand{\midrule}{\hline}",
            r"\providecommand{\bottomrule}{\hline}",
        ])
        source, changed = _insert_latex_preamble_snippet(source, insertion, ["toprule", "midrule", "bottomrule"])
        total += int(changed)

    if needs_multirow_fallback:
        insertion = "\n".join([
            r"% paper-trans fallback for missing multirow package",
            r"\providecommand{\multirow}[4][]{#4}",
        ])
        source, changed = _insert_latex_preamble_snippet(source, insertion, ["multirow"])
        total += int(changed)

    if needs_bbding_symbol_fallback:
        insertion = "\n".join([
            r"% paper-trans fallback for unavailable bbding symbols",
            r"\providecommand{\CheckmarkBold}{\ensuremath{\ifcsname checkmark\endcsname\checkmark\else\surd\fi}}",
            r"\providecommand{\XSolidBrush}{\ensuremath{\times}}",
        ])
        source, changed = _insert_latex_preamble_snippet(
            source,
            insertion,
            ["CheckmarkBold", "XSolidBrush"],
        )
        total += int(changed)

    if needs_cjk_environment_fallback:
        insertion = "\n".join([
            r"% paper-trans fallback for legacy CJK environments under XeLaTeX",
            r"\ifcsname CJK\endcsname\else\expandafter\def\csname CJK\endcsname#1#2{}\fi",
            r"\ifcsname endCJK\endcsname\else\expandafter\def\csname endCJK\endcsname{}\fi",
            r"\ifcsname CJK*\endcsname\else\expandafter\def\csname CJK*\endcsname#1#2{}\fi",
            r"\ifcsname endCJK*\endcsname\else\expandafter\def\csname endCJK*\endcsname{}\fi",
            r"\ifcsname CJKfamily\endcsname\else\def\CJKfamily#1{}\fi",
        ])
        source, changed = _insert_latex_preamble_snippet(source, insertion, ["CJK"])
        total += int(changed)

    return source, total


def repair_missing_math_aliases(text: str) -> Tuple[str, int]:
    """Repair a common translation typo where ``\\Imat`` becomes ``\\I``.

    A few papers define a named identity-matrix macro (usually ``\\Imat``)
    but the translated prose drops the ``mat`` suffix.  Only apply this
    conservative replacement when the target macro is actually defined and
    ``\\I`` is not, so unrelated one-letter commands are left untouched.
    """
    source = text or ""
    if _latex_command_defined(source, "I") or not _latex_command_defined(source, "Imat"):
        return source, 0
    fixed, count = re.subn(r"\\I(?![A-Za-z@])", r"\\Imat", source)
    return fixed, count


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

# Built-in commands in this allow-list consume no mandatory text argument.
# Keep the list deliberately narrow: this repair handles translation glue, not
# arbitrary undefined control sequences.
ZERO_ARG_LAYOUT_COMMANDS = (
    "cleardoublepage",
    "clearpage",
    "noindent",
    "smallskip",
    "medskip",
    "bigskip",
    "newline",
    "newpage",
    "hfill",
    "vfill",
    "par",
)


def separate_builtin_layout_ascii_glue(
    text: str,
    definition_text: str = "",
) -> Tuple[str, int]:
    r"""Split built-in layout commands glued to Titlecase prose.

    For example, an LLM can turn ``\par From (A)-(C)`` into
    ``\parFrom (A)-(C)``, which TeX parses as the undefined command
    ``\parFrom``.  Only a small built-in zero-argument allow-list and a
    Titlecase ASCII word followed by prose punctuation/whitespace are accepted.
    An explicitly defined combined command always wins.
    """
    source = text or ""
    definition_source = source + "\n" + (definition_text or "")
    explicitly_defined = {
        (match.group(1) or match.group(2))
        for match in ZERO_ARG_COMMAND_DEF_RE.finditer(definition_source)
        if match.group(1) or match.group(2)
    }
    for match in re.finditer(
        r"\\(?:def|gdef|edef|xdef)\s*\\([A-Za-z@]+)\b"
        r"|\\let\s*\\([A-Za-z@]+)\b",
        definition_source,
    ):
        name = match.group(1) or match.group(2)
        if name:
            explicitly_defined.add(name)

    command_pattern = "|".join(
        re.escape(name)
        for name in sorted(ZERO_ARG_LAYOUT_COMMANDS, key=len, reverse=True)
    )
    glued_re = re.compile(
        r"\\(?P<layout>" + command_pattern + r")"
        r"(?P<word>[A-Z][a-z]{1,40})"
        r"(?=$|[\\\s()\[\],.;:!?，。；：！？\u3400-\u4dbf\u4e00-\u9fff])"
    )
    glued_cjk_re = re.compile(
        r"\\(?P<layout>" + command_pattern + r")"
        r"(?P<cjk>[\u3400-\u4dbf\u4e00-\u9fff])"
    )
    verbatim_envs = set(BASE_VERBATIM_RESTORE_ENVS)
    env_stack = []
    total = 0
    output = []

    def replace(match) -> str:
        nonlocal total
        combined = match.group("layout") + match.group("word")
        if combined in explicitly_defined:
            return match.group(0)
        total += 1
        return "\\" + match.group("layout") + " " + match.group("word")

    def replace_cjk(match) -> str:
        nonlocal total
        combined = match.group("layout") + match.group("cjk")
        if combined in explicitly_defined:
            return match.group(0)
        total += 1
        return "\\" + match.group("layout") + " " + match.group("cjk")

    for line in source.splitlines(keepends=True):
        begins = re.findall(r"\\begin\{([^{}]+)\}", line)
        protected = bool(env_stack) or any(env in verbatim_envs for env in begins)
        if protected:
            output.append(line)
        else:
            comment_at = len(line)
            for index, char in enumerate(line):
                if char != "%":
                    continue
                backslashes = 0
                cursor = index - 1
                while cursor >= 0 and line[cursor] == "\\":
                    backslashes += 1
                    cursor -= 1
                if backslashes % 2 == 0:
                    comment_at = index
                    break
            body = glued_re.sub(replace, line[:comment_at])
            body = glued_cjk_re.sub(replace_cjk, body)
            output.append(body + line[comment_at:])

        for env in begins:
            if env in verbatim_envs:
                env_stack.append(env)
        for env in re.findall(r"\\end\{([^{}]+)\}", line):
            if env not in verbatim_envs:
                continue
            if env in env_stack:
                pos = len(env_stack) - 1 - env_stack[::-1].index(env)
                del env_stack[pos:]

    return "".join(output), total


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


def repair_duplicated_macro_initials(text: str) -> Tuple[str, int]:
    r"""Repair undefined ``\nname`` when the defined zero-arg macro is ``\name``."""
    source = text or ""
    macro_names = {
        (match.group(1) or match.group(2))
        for match in ZERO_ARG_COMMAND_DEF_RE.finditer(source)
        if match.group(3) in (None, "0") and (match.group(1) or match.group(2))
    }
    total = 0
    for name in sorted(macro_names, key=len, reverse=True):
        duplicated = name[0] + name
        if duplicated in macro_names:
            continue
        source, count = re.subn(
            r"\\" + re.escape(duplicated) + r"(?![A-Za-z@])",
            r"\\" + name,
            source,
        )
        total += count
    return source, total


def strip_redundant_macro_empty_groups(text: str, macro_names: Set[str]) -> Tuple[str, int]:
    """Remove an empty group while retaining a TeX command terminator.

    The empty group may be the only delimiter between a zero-argument macro
    and CJK text.  Removing it without adding a replacement delimiter turns
    ``\\name{}中文`` back into the invalid control word ``\\name中文``.
    """
    if not macro_names:
        return text, 0
    names_pat = "|".join(re.escape(n) for n in sorted(macro_names, key=len, reverse=True))
    pattern = re.compile(
        r"\\(" + names_pat + r")\{\}(?=\s*"
        + CJK_CHAR_CLASS
        + r"|[，。！？；：、])"
    )

    def replace(match: re.Match[str]) -> str:
        # Keep an existing whitespace delimiter; otherwise insert one.  The
        # latter is required because CJK letters have TeX's command-letter
        # catcode and would otherwise be absorbed into the control sequence.
        delimiter = "" if match.end() < len(match.string) and match.string[match.end()].isspace() else " "
        return "\\" + match.group(1) + delimiter

    return pattern.subn(replace, text or "")


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
    r"""Terminate no-argument declarations before translated CJK prose.

    ``\\xspace`` is frequently appended to a project macro; when translation
    glues Chinese text to it, XeTeX reads one undefined control sequence such
    as ``\\xspace与``.  It has no mandatory argument, just like the legacy
    font declarations handled here.
    """
    commands = ("xspace", "em", "bf", "it", "rm", "sf", "tt")
    pattern = re.compile(r"\\(" + "|".join(commands) + r")(?=" + CJK_COMMAND_FOLLOW_RE + r")")
    return pattern.subn(lambda m: "\\" + m.group(1) + " ", text or "")


def remove_spurious_cjk_command_escapes(text: str) -> Tuple[str, int]:
    r"""Remove stray command escapes before CJK text or punctuation."""
    pattern = re.compile(r"\\(?=" + CJK_COMMAND_FOLLOW_RE + r")")
    return pattern.subn("", text or "")


def demote_cleveref_commands(text: str) -> Tuple[str, int]:
    r"""Use core LaTeX references when a template's cleveref setup is fragile."""
    pattern = re.compile(r"\\(?:c|C)ref(?:\s*\[[^\]]*\])?(?=\s*\{)")
    return pattern.subn(r"\\ref", text or "")


def split_multilabel_references(text: str) -> Tuple[str, int]:
    """Split core refs only when every individual label exists in this source."""
    source = text or ""
    defined_labels = {
        match.group(1).strip()
        for match in re.finditer(r"\\label\{([^{}\n]+)\}", source)
        if match.group(1).strip()
    }
    pattern = re.compile(
        r"\\(?P<command>ref|eqref|autoref)\{(?P<labels>[^{}\n]*,[^{}\n]*)\}"
    )
    total = 0

    def replace(match) -> str:
        nonlocal total
        combined = match.group("labels").strip()
        labels = [item.strip() for item in match.group("labels").split(",")]
        if (
            len(labels) < 2
            or any(not item for item in labels)
            or combined in defined_labels
            or any(item not in defined_labels for item in labels)
        ):
            return match.group(0)
        total += 1
        command = match.group("command")
        return ", ".join(f"\\{command}{{{label}}}" for label in labels)

    return pattern.sub(replace, source), total


def disable_microtype_package_loads(text: str) -> Tuple[str, int]:
    r"""Disable local microtype loads and dependent commands without breaking hooks."""
    source = text or ""
    marker = "% paper-trans: local microtype load disabled for XeLaTeX"
    total = 0
    source, repaired = re.subn(
        r"\\AtEndOfClass\{% paper-trans: local microtype load disabled for XeLaTeX\}",
        marker,
        source,
    )
    total += repaired
    for pattern in (
        re.compile(r"\\AtEndOfClass\{\s*\\RequirePackage(?:\[[^\]]*\])?\{microtype\}\s*\}"),
        re.compile(r"\\RequirePackage(?:\[[^\]]*\])?\{microtype\}"),
    ):
        source, count = pattern.subn(marker, source)
        total += count
    # A class may invoke microtype commands outside the package-load hook. Replacing
    # only the invocation (rather than commenting its full line) preserves any
    # surrounding AtBeginDocument/AtEndOfClass braces.
    command_patterns = (
        re.compile(r"\\DisableLigatures\s*(?:\[[^\]]*\])?\s*\{[^{}]*\}"),
        re.compile(r"\\(?:UseMicrotypeSet|microtypesetup)\s*(?:\[[^\]]*\])?\s*\{[^{}]*\}"),
    )
    for pattern in command_patterns:
        source, count = pattern.subn(r"\\relax", source)
        total += count
    if r"\textls" in source and not _latex_command_defined(source, "textls"):
        source, changed = _insert_latex_preamble_snippet(
            source,
            r"% paper-trans fallback after disabling microtype"
            "\n"
            r"\providecommand{\textls}[2][]{#2}",
            ("textls",),
        )
        total += int(changed)
    return source, total


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


def remove_pdftex_graphics_driver(text: str) -> Tuple[str, int]:
    """Remove an explicit pdfTeX graphicx driver from an XeLaTeX document."""
    pattern = re.compile(
        r"\\(?P<command>usepackage|RequirePackage)"
        r"\[(?P<options>[^\]]*)\]\{graphicx\}"
    )

    total = 0

    def replace(match) -> str:
        nonlocal total
        raw_options = [option.strip() for option in match.group("options").split(",")]
        if not any(option.lower() == "pdftex" for option in raw_options):
            return match.group(0)
        options = [
            option
            for option in raw_options
            if option.strip() and option.strip().lower() != "pdftex"
        ]
        option_text = "[" + ",".join(options) + "]" if options else ""
        total += 1
        return "\\" + match.group("command") + option_text + "{graphicx}"

    return pattern.sub(replace, text or ""), total


def fallback_sourcesans3_family(text: str) -> Tuple[str, int]:
    """Use the SourceSansPro family shipped by the slim TeX image."""
    return re.subn(r"\{SourceSans3\}", "{SourceSansPro}", text or "")


PDFTEX_PRIMITIVE_NAMES = (
    "pdfoutput",
    "pdfgentounicode",
    "pdfinfoomitdate",
    "pdftrailerid",
    "pdfsuppressptexinfo",
    "pdfminorversion",
    "pdfcompresslevel",
    "pdfobjcompresslevel",
    "pdfpagewidth",
    "pdfpageheight",
    "pdfhorigin",
    "pdfvorigin",
    "pdfmapfile",
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
    "pdfinclusioncopyfonts",
)

PDFTEX_PRIMITIVE_LINE_RE = re.compile(
    r"(?m)^(?P<indent>[ \t]*)(?P<body>\\(?P<name>"
    + "|".join(re.escape(name) for name in PDFTEX_PRIMITIVE_NAMES)
    + r")\b[^\n]*)$"
)


def guard_pdftex_primitive_lines(text: str) -> Tuple[str, int]:
    """Wrap pdfTeX-only primitive lines so XeLaTeX/LuaLaTeX can skip them."""
    total = 0
    value = text or ""
    names_pattern = "|".join(
        re.escape(name) for name in PDFTEX_PRIMITIVE_NAMES
    )

    # Repair the first-generation multiline guard, which placed ``\fi``
    # immediately after the opening brace and left the payload unguarded.
    malformed_re = re.compile(
        r"(?ms)^(?P<indent>[ \t]*)\\ifdefined\\(?P<name>"
        + names_pattern
        + r")\\(?P=name)\{\s*\\fi(?P<body>.*?)^(?P=indent)\}[ \t]*$"
    )

    def repair_malformed(match) -> str:
        nonlocal total
        total += 1
        return (
            match.group("indent")
            + "\\ifdefined\\"
            + match.group("name")
            + "\\"
            + match.group("name")
            + "{"
            + match.group("body")
            + match.group("indent")
            + "}\\fi"
        )

    value = malformed_re.sub(repair_malformed, value)

    # A primitive such as ``\pdfinfo{`` commonly spans several lines.  Guard
    # the whole balanced command; wrapping only its first line leaves the
    # payload outside the conditional and causes "Missing \begin{document}".
    braced_re = re.compile(
        r"(?m)^(?P<indent>[ \t]*)\\(?P<name>"
        + names_pattern
        + r")\b[^\n{]*\{"
    )
    replacements = []
    for match in braced_re.finditer(value):
        line_start = value.rfind("\n", 0, match.start()) + 1
        if "\\ifdefined\\" + match.group("name") in value[line_start:match.start()]:
            continue
        open_index = value.find("{", match.start(), match.end())
        depth = 0
        close_index = -1
        for index in range(open_index, len(value)):
            char = value[index]
            if char == "{" and (index == 0 or value[index - 1] != "\\"):
                depth += 1
            elif char == "}" and (index == 0 or value[index - 1] != "\\"):
                depth -= 1
                if depth == 0:
                    close_index = index + 1
                    break
        if close_index < 0:
            continue
        command_start = match.start() + len(match.group("indent"))
        guarded = (
            match.group("indent")
            + "\\ifdefined\\"
            + match.group("name")
            + value[command_start:close_index]
            + "\\fi"
        )
        replacements.append((match.start(), close_index, guarded))

    for start, end, guarded in reversed(replacements):
        value = value[:start] + guarded + value[end:]
        total += 1

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

    new_text = PDFTEX_PRIMITIVE_LINE_RE.sub(replace, value)
    return new_text, total


def unique_label_replacement(
    label: str,
    labels: Iterable[str],
    original_refs: Iterable[str] = (),
) -> Optional[str]:
    """Infer one conservative replacement for an undefined translated ref."""
    if "," in label:
        return None
    targets = sorted(set(labels))
    if "/" in label:
        colon_variant = label.replace("/", ":")
        if colon_variant in targets:
            return colon_variant

    candidates = [
        target for target in targets
        if target.startswith(label + "_") or target.startswith(label + "-")
    ]
    if len(candidates) == 1:
        return candidates[0]

    # Pure edit-distance guesses can silently redirect a genuinely missing ref
    # to the wrong figure/table.  Only consider fuzzy candidates that are
    # independently present as refs in the untranslated merge.tex.
    original_targets = set(original_refs)
    if not original_targets:
        return None

    namespace = label.split(":", 1)[0] if ":" in label else ""
    if not namespace:
        return None
    comparable = [
        target for target in targets
        if target.startswith(namespace + ":") and target in original_targets
    ]
    scored = sorted(
        (
            difflib.SequenceMatcher(None, label, target).ratio(),
            target,
        )
        for target in comparable
    )
    if not scored:
        return None
    best_score, best = scored[-1]
    runner_up = scored[-2][0] if len(scored) > 1 else 0.0
    if best_score >= 0.78 and best_score - runner_up >= 0.08:
        return best
    return None


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
