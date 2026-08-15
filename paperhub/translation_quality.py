#!/usr/bin/env python3
"""Shared translated-TeX content quality analysis.

The repository audit and the chunk-analysis CLI must use the same definition
of prose.  In particular, code-like environments, inline code, URLs, math, and
LaTeX structure are not evidence of an untranslated paper.
"""

import re
from collections import Counter
from pathlib import Path
from typing import Dict

import latex_translation_filters as filters


LATEX_COMMAND_RE = re.compile(
    r"\\[A-Za-z@]+\*?(?:\[[^\]]*\])?(?:\{[^{}]*\})?"
)
COMMAND_RE = re.compile(r"^\s*\\([A-Za-z@]+)")
WORD_RE = re.compile(r"\b[A-Za-z][A-Za-z-]{2,}\b")


def mixed_untranslated_english_clauses(line: str):
    """Return high-confidence English prose clauses embedded in Chinese text.

    This catches the damaging partial-translation shape that a document-level
    CJK ratio misses (for example, ``中文 ... the agentic baseline on ...``).
    The predicate intentionally requires a four-word clause, grammar glue,
    lower-case content, and Chinese on the same line.  It therefore
    excludes titles/proper names, citations, code, URLs, math, and stand-alone
    English headings while retaining ordinary sentence fragments.
    """
    # Keep the repository audit exactly aligned with the response gate.  The
    # surrounding document scan additionally supplies environment protection.
    return filters.mixed_untranslated_english_clauses(line)


def strip_tex_comment(line: str) -> str:
    r"""Remove an unescaped TeX comment while preserving literal ``\%``.

    Counting source comments as paper prose makes both the repository audit and
    the production publication gate reject otherwise translated documents.
    TeX treats ``%`` as escaped only when it is preceded by an odd number of
    consecutive backslashes.
    """
    value = str(line or "")
    for index, char in enumerate(value):
        if char != "%":
            continue
        backslashes = 0
        cursor = index - 1
        while cursor >= 0 and value[cursor] == "\\":
            backslashes += 1
            cursor -= 1
        if backslashes % 2 == 0:
            return value[:index]
    return value


def rough_text(line: str) -> str:
    """Return natural-language text after removing protected LaTeX payloads."""
    value = strip_tex_comment(line)
    if (
        filters.is_bracketed_key_value_option_list(value)
        or filters.is_latex_configuration_command_fragment(value)
    ):
        return ""
    value = re.sub(r"\$[^$]*\$", " ", value)
    # The full balanced-brace scanner is deliberately robust but comparatively
    # expensive.  Most prose lines cannot contain any token it handles, so use
    # a cheap exact prefilter before invoking it during a repository-wide scan.
    if (
        "http://" in value
        or "https://" in value
        or r"\url" in value
        or r"\nolinkurl" in value
        or r"\path" in value
        or ("\\" in value and "tt" in value)
    ):
        value = filters.strip_inline_code_commands(value)
    value = re.sub(
        r"\\(?:begin|end)\{[^{}]+\}(?:\[[^\]]*\])?",
        " ",
        value,
    )
    value = re.sub(
        r"\\(?:textcolor|colorbox|href)\*?(?:\[[^\]]*\])?"
        r"\{[^{}]*\}\{([^{}]*)\}",
        r" \1 ",
        value,
    )
    for _ in range(3):
        value = re.sub(
            r"\\(?:textbf|textit|texttt|emph|underline|small|footnotesize|"
            r"scriptsize|normalsize|large|Large)\*?"
            r"(?:\[[^\]]*\])?\{([^{}]*)\}",
            r" \1 ",
            value,
        )
    value = re.sub(
        r"\\captionof\*?(?:\[[^\]]*\])?\{[^{}]*\}\{([^{}]*)\}",
        r" \1 ",
        value,
    )
    return filters.latex_prose_probe(value)


def analyze_tex(path) -> Dict[str, object]:
    """Measure Chinese coverage and long English prose in translated TeX."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    in_document = False
    # ``(environment_name, semantic_source_data)`` frames keep instance-level
    # prompt/example protection aligned with the production splitter.
    env_stack = []
    inline_source_data_state = {}
    structural_input_data_state = {}
    english_lines = []
    broad_english_lines = []
    very_long_english = 0
    mixed_lines = 0
    english_dominant_lines = 0
    mixed_clause_lines = 0
    mixed_clause_words = 0
    mixed_clauses = []
    envs = Counter()
    commands = Counter()
    english_words = Counter()
    english_word_line_bins = Counter()
    cjk_total = 0
    letter_total = 0
    prose_lines = 0

    for line_no, line in enumerate(text.splitlines(), 1):
        code = strip_tex_comment(line)
        if r"\begin{document}" in code:
            in_document = True
            continue
        if r"\end{document}" in code:
            in_document = False
            continue
        begins = re.findall(r"\\begin\{([^}]+)\}", code)
        ends = re.findall(r"\\end\{([^}]+)\}", code)
        inline_source_data, inline_source_data_state = (
            filters.inline_prompt_source_data_line_protected(
                code,
                inline_source_data_state,
            )
        )
        structural_input_data, structural_input_data_state = (
            filters.structural_input_command_line_protected(
                code,
                structural_input_data_state,
            )
        )
        for index in range(len(env_stack) - 1, -1, -1):
            env, semantic_source_data = env_stack[index]
            if semantic_source_data:
                break
            if filters.is_semantic_source_data_content(env, code):
                env_stack[index] = (env, True)
                break
        active = next(
            (env for env, _ in reversed(env_stack) if env != "document"),
            "body",
        )
        protected = any(
            semantic_source_data or filters.is_hard_protected_env(env)
            for env, semantic_source_data in env_stack
        ) or (
            inline_source_data
            or structural_input_data
            or filters.is_latex_metadata_line(code)
            or filters.is_affiliation_metadata_fragment(code)
            or filters.is_contact_metadata_fragment(code)
            or filters.is_bracketed_heading_fragment(code)
            or filters.is_algorithmic_pseudocode_fragment(code)
            or filters.is_graphics_path_fragment(code)
            or filters.is_formatting_label_fragment(code)
            or filters.is_unbalanced_latex_fragment(code)
            or filters.is_tikz_drawing_fragment(code)
            or filters.is_tikz_style_definition_fragment(code)
            or filters.is_citation_heavy_proper_name_catalog(code)
            or filters.is_structured_identifier_path(code)
            or filters.is_person_name_catalog(code)
            or filters.is_tool_call_result_fragment(code)
            or filters.is_structural_command_data_fragment(code)
            or filters.is_latex_configuration_command_fragment(code)
        )
        structural = any(filters.is_tracked_env(env) for env in begins + ends)
        if in_document and not protected and not structural:
            rough = rough_text(code)
            letters = len(re.findall(r"[A-Za-z]", rough))
            cjk = len(re.findall(r"[\u4e00-\u9fff]", rough))
            words = WORD_RE.findall(rough)
            cjk_total += cjk
            letter_total += letters
            if letters >= 40 or cjk >= 10:
                prose_lines += 1
            if len(words) >= 8 and letters >= 50:
                normalized_words = [word.lower() for word in words]
                english_words.update(normalized_words)
                english_word_line_bins[
                    "8-15" if len(words) <= 15 else
                    "16-30" if len(words) <= 30 else
                    "31+"
                ] += 1
                envs[active] += 1
                command = COMMAND_RE.match(line)
                commands[command.group(1) if command else "plain"] += 1
                broad_english_lines.append({
                    "line": line_no,
                    "env": active,
                    "command": command.group(1) if command else "plain",
                    "words": len(words),
                    "cjk": cjk,
                    "text": line.strip()[:220],
                })
                if cjk >= 8:
                    mixed_lines += 1
                if cjk < 8 or len(words) >= cjk:
                    english_dominant_lines += 1
            if letters >= 80 and cjk <= 5 and len(words) >= 12:
                command = COMMAND_RE.match(line)
                english_lines.append({
                    "line": line_no,
                    "env": active,
                    "command": command.group(1) if command else "plain",
                    "words": len(words),
                    "cjk": cjk,
                    "text": line.strip()[:220],
                })
            if letters >= 180 and cjk <= 8 and len(words) >= 24:
                very_long_english += 1
            # Tables/algorithms are structured data rather than ordinary
            # paragraph prose.  Their English labels and pseudo-code should
            # not create a mixed-language repair candidate.
            if not filters.is_soft_text_env(active):
                clauses = mixed_untranslated_english_clauses(code)
                if clauses:
                    mixed_clause_lines += 1
                    for clause in clauses:
                        clause.update({
                            "line": line_no,
                            "env": active,
                            "command": (
                                COMMAND_RE.match(line).group(1)
                                if COMMAND_RE.match(line) else "plain"
                            ),
                        })
                        mixed_clauses.append(clause)
                        mixed_clause_words += int(clause["words"])

        for env in begins:
            semantic_source_data = (
                filters.is_semantic_source_data_opening(env, code)
                or filters.is_semantic_source_data_content(env, code)
            )
            env_stack.append((env, semantic_source_data))
        for env in ends:
            names = [name for name, _ in env_stack]
            if env in names:
                pos = len(names) - 1 - names[::-1].index(env)
                env_stack = env_stack[:pos]
    total_letters = cjk_total + letter_total
    cjk_pct_exact = 100 * cjk_total / max(1, total_letters)
    return {
        "path": str(path),
        "cjk": cjk_total,
        "letters": letter_total,
        "cjk_pct": round(cjk_pct_exact, 1),
        "cjk_pct_exact": cjk_pct_exact,
        "prose_lines": prose_lines,
        "long_english_lines": len(english_lines),
        "broad_english_lines": len(broad_english_lines),
        "very_long_english_lines": very_long_english,
        "mixed_language_lines": mixed_lines,
        "mixed_english_clause_count": len(mixed_clauses),
        "mixed_english_clause_lines": mixed_clause_lines,
        "mixed_english_clause_words": mixed_clause_words,
        "mixed_english_clause_samples": mixed_clauses[:12],
        "english_dominant_lines": english_dominant_lines,
        "english_word_occurrences": sum(english_words.values()),
        "english_word_line_bins": dict(english_word_line_bins),
        "top_english_words": dict(english_words.most_common(30)),
        "_english_word_counts": dict(english_words),
        "by_environment": dict(envs),
        "by_command": dict(commands),
        "samples": english_lines[:8],
    }


def is_untranslated_prose(report: Dict[str, object]) -> bool:
    """Apply the one publication-quality predicate used by every workflow."""
    cjk_pct = float(report.get("cjk_pct_exact", report.get("cjk_pct", 0.0)))
    prose_lines = int(report.get("prose_lines", 0))
    long_count = int(report.get("long_english_lines", 0))
    very_long_count = int(report.get("very_long_english_lines", 0))
    # One isolated English phrase can be a formally retained term.  Two
    # independently qualified mixed clauses in ordinary prose are strong
    # evidence of a partial translation and cover the high-CJK failure mode.
    mixed_clause_count = int(report.get("mixed_english_clause_count", 0))
    return (
        long_count >= 20
        or very_long_count >= 3
        or (long_count >= 10 and cjk_pct < 70.0)
        or (long_count >= 6 and cjk_pct < 55.0)
        or (cjk_pct < 45.0 and long_count >= 4)
        or (cjk_pct < 15.0 and prose_lines >= 20)
        or mixed_clause_count >= 2
    )
