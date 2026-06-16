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
    re.compile(r"If you provide the section you want translated, I can proceed\.", re.IGNORECASE),
    re.compile(r"If you provide the English academic paper section, I will translate it for you accordingly\.", re.IGNORECASE),
    re.compile(r"Below is the translated text(?: in Chinese)?:?", re.IGNORECASE),
    re.compile(r"Below is a section from an English academic paper, translated into Chinese(?:\.|:)?", re.IGNORECASE),
    re.compile(r"Below is the translation of your provided English academic paper section into Chinese\.?", re.IGNORECASE),
    re.compile(r"LaTeX commands and equations are kept unchanged\.?", re.IGNORECASE),
    re.compile(r"请提供您需要翻译的英文学术论文部分内容。"),
    re.compile(r"请提供需要翻译的英文学术论文部分内容。"),
    re.compile(r"请提供您需要翻译的英文学术论文部分。"),
    re.compile(r"请提供您需要翻译的具体英文内容。"),
    re.compile(r"请提供需要翻译的具体英文内容。"),
    re.compile(r"请提供需要翻译的具体英文段落内容。"),
    re.compile(r"好的，请提供需要翻译的英文部分。"),
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
