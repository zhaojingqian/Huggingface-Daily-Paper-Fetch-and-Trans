"""Runtime patches applied at the gpt-academic integration boundary.

The driver owns lifecycle and paper identity.  This module owns the one-time
adapter layer for splitter, response validation, and archive safety.
"""

from __future__ import annotations

import os
import tarfile

import latex_translation_filters as _ltf
try:
    from translation_policy import (
        bounded_int as _bounded_policy_int,
        configured_worker_count as _configured_worker_count,
        retry_worker_count as _retry_worker_count,
    )
except ImportError:
    from paperhub.translation_policy import (
        bounded_int as _bounded_policy_int,
        configured_worker_count as _configured_worker_count,
        retry_worker_count as _retry_worker_count,
    )

SPLITTER_CACHE_VERSION = (
    "paper-trans-splitter-2026-08-16-v61-prompt-directives"
)


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
        r"tableofcontents|maketitle|printbibliography|author|affiliation|"
        r"icmlauthor|icmlaffiliation|icmlcorrespondingauthor|institute|email|"
        r"homepage|orcidlink|thanks|newcommand|renewcommand|providecommand|"
        r"DeclareMathOperator|newtheorem|def)\b"
    )
    inline_math_re = _re.compile(r"\$[^$]*\$")

    def _rough_text(line: str) -> str:
        rough = _ltf.strip_inline_code_commands(inline_math_re.sub(" ", line))
        rough = _re.sub(
            r"\\(?:begin|end)\{[^{}]+\}(?:\[[^\]]*\])?",
            " ",
            rough,
        )
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
        return _ltf.latex_prose_probe(rough)

    def _env_is_tracked(env: str | None) -> bool:
        return env == "center" or _ltf.is_tracked_env(env)

    def _text_has_translatable_prose(text: str, min_letters=32, min_words=5) -> bool:
        stripped = text.strip()
        if not stripped or stripped.startswith("%"):
            return False
        if (
            _ltf.is_latex_key_value_option_list(stripped)
            or _ltf.is_latex_configuration_command_fragment(stripped)
            or _ltf.is_affiliation_metadata_fragment(stripped)
            or _ltf.is_contact_metadata_fragment(stripped)
            or _ltf.is_bracketed_heading_fragment(stripped)
            or _ltf.is_algorithmic_pseudocode_fragment(stripped)
            or _ltf.is_structural_input_command_fragment(stripped)
            or _ltf.is_structural_command_data_fragment(stripped)
            or _ltf.is_graphics_path_fragment(stripped)
            or _ltf.is_formatting_label_fragment(stripped)
            or _ltf.is_unbalanced_latex_fragment(stripped)
        ):
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
        return (
            _text_has_translatable_prose(line, min_letters=32, min_words=5)
            or _ltf.is_short_structural_bridge_prose(line)
            or (
                _re.search(r"\\(?:texttt|verb)\b", line)
                and _text_has_translatable_prose(
                    line,
                    min_letters=10,
                    min_words=3,
                )
            )
        )

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
            # Keep caller-created split boundaries intact. A bounded split may
            # end exactly after a balanced citation/group without whitespace;
            # the old implicit merge joined the adjacent TRANSFORM cores back
            # into the original oversized request before the controlled
            # coalescer could enforce its dynamic citation/reference cap.
            _append(nodes, core, False, merge=False)
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

    def _promote_semantic_frame(line: str, env_stack):
        for index in range(len(env_stack) - 1, -1, -1):
            env, semantic_source_data = env_stack[index]
            if semantic_source_data:
                break
            if _ltf.is_semantic_source_data_content(env, line):
                env_stack[index] = (env, True)
                break
        return env_stack

    def _update_env_stack(text: str, env_stack):
        for line in text.splitlines(keepends=True) or [text]:
            env_stack = _promote_semantic_frame(line, env_stack)
            events = _re.finditer(r"\\(begin|end)\{([^}]+)\}", line)
            for event in events:
                action, env = event.groups()
                if not _env_is_tracked(env):
                    continue
                if action == "begin":
                    semantic_source_data = (
                        _ltf.is_semantic_source_data_opening(env, line)
                        or _ltf.is_semantic_source_data_content(env, line)
                    )
                    env_stack.append((env, semantic_source_data))
                    continue
                names = [name for name, _ in env_stack]
                if env in names:
                    pos = len(names) - 1 - names[::-1].index(env)
                    env_stack = env_stack[:pos]
                elif env_stack:
                    env_stack.pop()
        return env_stack

    dense_split_log_count = 0

    def _split_long_transform_line(line: str):
        nonlocal dense_split_log_count
        max_chars = _ltf.recommended_translation_chunk_limit(line)
        if len(_rough_text(line)) < 420 and len(line) <= max_chars:
            return [line]
        parts = _ltf.split_translation_line_bounded(
            line,
            max_translate_chars=max_chars,
            min_sentence_chars=180,
        )
        if (
            max_chars < 1800
            and len(parts) > 1
            and dense_split_log_count < 5
        ):
            dense_split_log_count += 1
            print(
                "[driver] 🔬 structure-dense split: "
                f"{len(line)} -> {[len(part) for part in parts]} "
                f"(cap={max_chars})",
                flush=True,
            )
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

            state["env_stack"] = _promote_semantic_frame(
                line,
                state["env_stack"],
            )
            inline_source_data, state["inline_source_data"] = (
                _ltf.inline_prompt_source_data_line_protected(
                    line,
                    state.get("inline_source_data"),
                )
            )
            structural_input_data, state["structural_input_data"] = (
                _ltf.structural_input_command_line_protected(
                    line,
                    state.get("structural_input_data"),
                )
            )
            active_env = (
                state["env_stack"][-1][0]
                if state["env_stack"]
                else None
            )
            in_soft_env = _ltf.is_soft_text_env(active_env)
            hard_active = any(
                semantic_source_data or _ltf.is_hard_protected_env(env)
                for env, semantic_source_data in state["env_stack"]
            )
            begins = _re.findall(r"\\begin\{([^}]+)\}", line)
            ends = _re.findall(r"\\end\{([^}]+)\}", line)
            structural_line = any(_env_is_tracked(env) for env in begins + ends)

            if (
                state["in_document"]
                and in_soft_env
                and not hard_active
                and not structural_line
                and not inline_source_data
                and not structural_input_data
            ):
                if active_env.startswith("tabular") or active_env in {"longtable", "array"}:
                    for part in _split_tabular_line(line):
                        _append(nodes, part.string, part.preserve)
                elif active_env.startswith("algorithm"):
                    for part in _split_algorithmic_line(line):
                        _append(nodes, part.string, part.preserve)
                else:
                    for part in _split_long_transform_line(line):
                        _append_translatable_fragment(
                            nodes,
                            part,
                            min_letters=12,
                            min_words=2,
                        )
            else:
                line_protected = (
                    (not state["in_document"])
                    or hard_active
                    or structural_line
                    or inline_source_data
                    or structural_input_data
                )
                transform = (not line_protected) and _line_has_translatable_prose(line)
                if transform:
                    for part in _split_long_transform_line(line):
                        _append(nodes, part, preserve=False)
                else:
                    _append(nodes, line, preserve=True)

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
        if _ltf.is_inline_prompt_source_data_block(stripped):
            return False
        if (
            _ltf.is_affiliation_metadata_fragment(stripped)
            or _ltf.is_contact_metadata_fragment(stripped)
            or _ltf.is_bracketed_heading_fragment(stripped)
            or _ltf.is_algorithmic_pseudocode_fragment(stripped)
            or _ltf.is_structural_input_command_fragment(stripped)
            or _ltf.is_graphics_path_fragment(stripped)
            or _ltf.is_formatting_label_fragment(stripped)
            or _ltf.is_unbalanced_latex_fragment(stripped)
        ):
            return False
        if _ltf.is_citation_heavy_proper_name_catalog(stripped):
            return False
        if _ltf.is_tikz_drawing_fragment(stripped):
            return False
        if _ltf.is_http_endpoint_catalog(stripped):
            return False
        if _ltf.is_detached_citation_key_list(stripped):
            return False
        if _ltf.is_structured_identifier_path(stripped):
            return False
        if _ltf.is_person_name_catalog(stripped):
            return False
        if _ltf.is_tool_call_result_fragment(stripped):
            return False
        if _ltf.is_structural_command_data_fragment(stripped):
            return False
        if _ltf.is_latex_key_value_option_list(stripped):
            return False
        if _ltf.is_latex_configuration_command_fragment(stripped):
            return False
        if _ltf.is_pure_latex_math_fragment(stripped):
            return False
        if _is_section_heading(stripped):
            rough = _rough_text(stripped)
            letters = len(_re.findall(r"[A-Za-z]", rough))
            return letters >= 6
        if _ltf.is_short_structural_bridge_prose(stripped):
            return True
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
        fragments = []
        demoted = 0
        transform_before_merge = 0
        for node in nodes:
            if not node.preserve and not _semantic_enough_for_gpt(node.string):
                node.preserve = True
                demoted += 1

            if node.preserve:
                fragments.append((node.string, True))
                continue

            transform_before_merge += 1
            # Whitespace between adjacent prose fragments is safe to keep
            # inside one translation request. Keeping it as a separate
            # PRESERVE node previously fragmented a paragraph into one API
            # call per sentence, multiplying 429 risk and creating mixed
            # Chinese/English paragraphs when only some calls succeeded.
            fragments.append((node.string, False))
        fragments = _ltf.absorb_short_prose_bridges(fragments)
        merged = _ltf.coalesce_translation_fragments(fragments)
        merged = _ltf.enforce_translation_fragment_limits(merged)
        finalized = [_Node(text, preserve=preserve) for text, preserve in merged]
        transform_after_merge = sum(1 for node in finalized if not node.preserve)
        return finalized, demoted, transform_before_merge - transform_after_merge

    def _rescue_short_top_level_prose(nodes):
        """Promote short prose lines stranded in preserved math-adjacent nodes."""
        rescued = []
        state = {
            "in_document": False,
            "env_stack": [],
            "inline_source_data": {},
            "structural_input_data": {},
        }
        promoted = 0
        for node in nodes:
            for line in node.string.splitlines(keepends=True):
                if r"\begin{document}" in line:
                    state["in_document"] = True
                state["env_stack"] = _promote_semantic_frame(
                    line,
                    state["env_stack"],
                )
                active_env = (
                    state["env_stack"][-1][0]
                    if state["env_stack"]
                    else None
                )
                hard_active = any(
                    semantic_source_data or _ltf.is_hard_protected_env(env)
                    for env, semantic_source_data in state["env_stack"]
                )
                inline_source_data, state["inline_source_data"] = (
                    _ltf.inline_prompt_source_data_line_protected(
                        line,
                        state.get("inline_source_data"),
                    )
                )
                structural_input_data, state["structural_input_data"] = (
                    _ltf.structural_input_command_line_protected(
                        line,
                        state.get("structural_input_data"),
                    )
                )
                safe_top_level = active_env in {None, "document"}
                promote = bool(
                    node.preserve
                    and state["in_document"]
                    and safe_top_level
                    and not hard_active
                    and not inline_source_data
                    and not structural_input_data
                    and _ltf.is_plain_prose_line_for_rescue(line)
                )
                _append(
                    rescued,
                    line,
                    preserve=not promote if node.preserve else False,
                    merge=False,
                )
                promoted += int(promote)
                state["env_stack"] = _update_env_stack(
                    line,
                    state["env_stack"],
                )
                if r"\end{document}" in line:
                    state["in_document"] = False
        return rescued, promoted

    def _patched_split(self, txt, project_folder, opts):
        res = _orig_split(self, txt, project_folder, opts)
        original_transform = sum(1 for node in self.nodes if not node.preserve)
        original_chars = sum(len(node.string) for node in self.nodes if not node.preserve)

        expanded = []
        state = {
            "in_document": False,
            "env_stack": [],
            "inline_source_data": {},
            "structural_input_data": {},
        }
        for node in self.nodes:
            parts = _split_preserved_text(node.string, state)
            for part in parts:
                # Preserve splitter-created boundaries until the controlled
                # coalescer applies semantic and size constraints.
                _append(
                    expanded,
                    part.string,
                    part.preserve,
                    merge=False,
                )

        expanded, rescued_short = _rescue_short_top_level_prose(expanded)
        structural_units = []
        for node in expanded:
            if node.preserve:
                _append(structural_units, node.string, True, merge=False)
                continue
            for part in _ltf.split_translation_structural_units(node.string):
                _append(structural_units, part, False, merge=False)
        expanded = structural_units
        expanded, demoted_short, coalesced = _finalize_expanded_nodes(expanded)
        _invalidate_stale_split_cache(project_folder)
        _recompute_ranges(expanded)
        self.nodes = expanded
        self.sp = [node.string for node in expanded if not node.preserve]

        added = len(self.sp) - original_transform
        added_chars = sum(len(node.string) for node in expanded if not node.preserve) - original_chars
        print(
            f"[driver] ✅ latex splitter expanded prose chunks: "
            f"{original_transform} -> {len(self.sp)} "
            f"(chars +{max(0, added_chars)}, short demoted={demoted_short}, "
            f"rescued short={rescued_short}, adjacent merged={coalesced})",
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


def _patch_latex_llm_rate_limit_handling():
    """Use one bounded scheduler for first-pass and failed-slot requests."""
    from crazy_functions import crazy_utils as _crazy_utils

    if getattr(_crazy_utils, "_paper_trans_rate_limit_patch", False):
        return

    original = (
        _crazy_utils
        .request_gpt_model_multi_threads_with_very_awesome_ui_and_high_efficiency
    )
    # The configured gateway is an OpenAI-compatible relay with a much higher
    # concurrency budget than the old eight-worker safeguard. Keep the first
    # pass operator-controlled at 50, while retries use a smaller shared cap.
    max_workers = _configured_worker_count(os.environ)
    # Let the outer failed-slot loop own retries. Upstream retries every failed
    # future independently and can spend tens of minutes sleeping on a
    # deterministic quota error before the batch result is inspectable.
    per_call_retries = _bounded_policy_int(
        os.environ.get("PAPER_TRANS_LLM_RETRIES"),
        0,
        minimum=0,
        maximum=10,
    )
    retry_rounds = _bounded_policy_int(
        os.environ.get("PAPER_TRANS_FAILED_CHUNK_RETRY_ROUNDS"),
        2,
        minimum=0,
        maximum=5,
    )

    def _patched(*args, **kwargs):
        options = dict(kwargs)
        options["max_workers"] = max_workers
        options["retry_times_at_unknown_error"] = per_call_retries
        result = yield from original(*args, **options)

        inputs = options.get("inputs_array", [])
        visible_inputs = options.get("inputs_show_user_array", [])
        histories = options.get("history_array", [])
        prompts = options.get("sys_prompt_array", [])
        # inputs_array contains the translation prompt plus the actual LaTeX
        # fragment. Strip that English prompt before language validation or a
        # short command-only fragment becomes a false untranslated positive.
        # inputs_show_user_array is only a label such as
        # "translate_zh segment-7" and cannot be used for validation.
        validation_sources = [
            _ltf.extract_translation_fragment(item)
            for item in inputs
        ]

        def log_abnormal_chunks(payload, failed_indices, invalid_reasons, stage):
            """Print enough structure evidence to diagnose a rejected slot.

            Text previews intentionally stay short, while the signatures remain
            complete.  Log after every validation pass so the final serial
            retry cannot hide a different persistent mismatch from the first
            attempt.
            """
            for index in failed_indices[:3]:
                label = (
                    visible_inputs[index]
                    if index < len(visible_inputs)
                    else f"chunk-{index}"
                )
                source = (
                    validation_sources[index]
                    if index < len(validation_sources)
                    else ""
                )
                response_index = index * 2 + 1
                response = (
                    payload[response_index]
                    if response_index < len(payload)
                    else ""
                )
                source_preview = " ".join(source.split())[:180]
                response_preview = " ".join(str(response).split())[:180]
                invalid_reason = invalid_reasons.get(
                    index,
                    "request_or_untranslated",
                )
                print(
                    f"[driver]    abnormal {stage} {label}: "
                    f"reason={invalid_reason} "
                    f"source={source_preview!r} response={response_preview!r}",
                    flush=True,
                )
                if invalid_reason in {
                    "critical_latex_structure_mismatch",
                    "citation_structure_mismatch",
                }:
                    evidence = _ltf.llm_translation_structure_evidence(
                        source,
                        str(response),
                    )
                    print(
                        "[driver]      latex-signatures: "
                        f"source_commands={evidence['source_commands']!r} "
                        f"response_commands={evidence['response_commands']!r} "
                        f"source_citations_only="
                        f"{evidence['source_citations_only']!r} "
                        f"response_citations_only="
                        f"{evidence['response_citations_only']!r}",
                        flush=True,
                    )

        def response_status(payload):
            failed = []
            request_failed = []
            untranslated = []
            structurally_invalid = []
            invalid_reasons = {}
            quota_failed = []
            normalized_indices = _ltf.normalize_llm_translation_payload(
                payload,
                validation_sources,
            )
            for index in normalized_indices:
                label = (
                    visible_inputs[index]
                    if index < len(visible_inputs)
                    else f"chunk-{index}"
                )
                print(
                    f"[driver] 🔧 {label}: 移除误加的单一标题 wrapper 后再校验",
                    flush=True,
                )
            for index, response in enumerate(payload[1::2]):
                source = (
                    validation_sources[index]
                    if index < len(validation_sources)
                    else ""
                )
                if _ltf.llm_translation_response_quota_failed(response):
                    quota_failed.append(index)
                if _ltf.llm_translation_response_failed(response):
                    request_failed.append(index)
                else:
                    invalid_reason = _ltf.llm_translation_response_invalid(
                        source,
                        response,
                    )
                    if invalid_reason:
                        structurally_invalid.append(index)
                        invalid_reasons[index] = invalid_reason
                    elif _ltf.llm_translation_response_untranslated(source, response):
                        untranslated.append(index)
                if (
                    index in request_failed
                    or index in untranslated
                    or index in structurally_invalid
                ):
                    failed.append(index)
            return (
                failed,
                request_failed,
                untranslated,
                structurally_invalid,
                invalid_reasons,
                quota_failed,
            )

        (
            remaining,
            request_failed,
            untranslated,
            structurally_invalid,
            invalid_reasons,
            quota_failed,
        ) = response_status(result)
        if quota_failed:
            raise RuntimeError(
                "insufficient_user_quota: API balance is insufficient for "
                f"{len(quota_failed)} translation chunks"
            )
        if remaining:
            print(
                f"[driver] ⚠️  chunk 首轮异常: request_failed={len(request_failed)}, "
                f"untranslated={len(untranslated)}, "
                f"structure_invalid={len(structurally_invalid)}",
                flush=True,
            )
            log_abnormal_chunks(
                result,
                remaining,
                invalid_reasons,
                "首轮",
            )
        for round_index in range(1, retry_rounds + 1):
            if not remaining:
                break
            if round_index == 1:
                adaptive = [
                    index
                    for index in remaining
                    if invalid_reasons.get(index) in {
                        "critical_latex_structure_mismatch",
                        "citation_structure_mismatch",
                        "latex_brace_balance_mismatch",
                    }
                    and len(validation_sources[index]) > 480
                ]
                adaptive_groups = []
                adaptive_inputs = []
                adaptive_part_sources = []
                adaptive_labels = []
                adaptive_histories = []
                adaptive_prompts = []
                for original_index in adaptive:
                    source = validation_sources[original_index]
                    parts = _ltf.adaptive_translation_retry_fragments(source)
                    if len(parts) <= 1:
                        continue
                    raw_input = inputs[original_index]
                    prefix = (
                        raw_input[:-len(source)]
                        if source and raw_input.endswith(source)
                        else ""
                    )
                    prompt = (
                        prompts[original_index]
                        if original_index < len(prompts)
                        else ""
                    )
                    retry_prompt = _ltf.translation_retry_system_prompt(
                        prompt,
                        invalid_reasons.get(original_index, ""),
                    )
                    print(
                        f"[driver] ✂️  仅细分结构失败 chunk-{original_index}: "
                        f"{len(source)} chars -> {[len(part) for part in parts]}",
                        flush=True,
                    )
                    start = len(adaptive_inputs)
                    adaptive_groups.append((original_index, start, len(parts), source))
                    adaptive_inputs.extend(prefix + part for part in parts)
                    adaptive_part_sources.extend(parts)
                    adaptive_labels.extend(
                        f"adaptive-{original_index}-{part_index}"
                        for part_index in range(len(parts))
                    )
                    adaptive_histories.extend(
                        [
                            histories[original_index]
                            if original_index < len(histories)
                            else []
                        ] * len(parts)
                    )
                    adaptive_prompts.extend([retry_prompt] * len(parts))

                if adaptive_inputs:
                    # All structurally bad slots share one retry batch. This is
                    # the scheduler boundary: no paper-specific serial loop,
                    # but no unbounded second burst either.
                    split_options = dict(options)
                    split_options.update({
                        "inputs_array": adaptive_inputs,
                        "inputs_show_user_array": adaptive_labels,
                        "history_array": adaptive_histories,
                        "sys_prompt_array": adaptive_prompts,
                        "max_workers": _retry_worker_count(
                            len(adaptive_inputs),
                            max_workers,
                        ),
                    })
                    split_result = yield from original(**split_options)
                    responses = list(split_result[1::2])
                    for original_index, start, count, source in adaptive_groups:
                        group = responses[start:start + count]
                        parts = adaptive_part_sources[start:start + count]
                        if len(group) != count:
                            continue
                        combined = "".join(group)
                        combined = _ltf.normalize_llm_translation_response(
                            source,
                            combined,
                        )
                        if any(
                            _ltf.llm_translation_response_failed(response)
                            for response in group
                        ):
                            continue
                        if (
                            _ltf.llm_translation_response_invalid(source, combined)
                            or _ltf.llm_translation_response_untranslated(
                                source,
                                combined,
                            )
                        ):
                            continue
                        result[original_index * 2 + 1] = combined

                if adaptive:
                    (
                        remaining,
                        request_failed,
                        untranslated,
                        structurally_invalid,
                        invalid_reasons,
                        quota_failed,
                    ) = response_status(result)
                    if quota_failed:
                        raise RuntimeError(
                            "insufficient_user_quota: API balance is insufficient "
                            "during adaptive structural retry"
                        )
                    if not remaining:
                        break
            print(
                f"[driver] 🐢 LLM 限流恢复: 第 {round_index}/{retry_rounds} 轮，"
                f"并发重试 {len(remaining)} 个失败/漏译/结构异常 chunk",
                flush=True,
            )
            retry_options = dict(options)
            retry_prompts = []
            for index in remaining:
                prompt = prompts[index] if index < len(prompts) else ""
                retry_prompts.append(
                    _ltf.translation_retry_system_prompt(
                        prompt,
                        invalid_reasons.get(index, ""),
                    )
                )
            retry_options.update({
                "inputs_array": [
                    _ltf.prepare_braced_prose_retry_input(inputs[index])
                    for index in remaining
                ],
                "inputs_show_user_array": [visible_inputs[index] for index in remaining],
                "history_array": [histories[index] for index in remaining],
                "sys_prompt_array": retry_prompts,
                "max_workers": _retry_worker_count(
                    len(remaining),
                    max_workers,
                ),
            })
            retried = yield from original(**retry_options)
            for local_index, original_index in enumerate(remaining):
                result[original_index * 2 + 1] = retried[local_index * 2 + 1]
            (
                remaining,
                request_failed,
                untranslated,
                structurally_invalid,
                invalid_reasons,
                quota_failed,
            ) = response_status(result)
            if quota_failed:
                raise RuntimeError(
                    "insufficient_user_quota: API balance is insufficient for "
                    f"{len(quota_failed)} translation chunks"
                )
            if remaining:
                log_abnormal_chunks(
                    result,
                    remaining,
                    invalid_reasons,
                    f"第 {round_index} 轮重试后",
                )

        if remaining:
            # Never cache a temp.pkl where request failures are silently
            # merged back into the output as English source text.
            raise RuntimeError(
                "LLM request/untranslated/structural failures remain in "
                f"{len(remaining)} translation chunks after bounded retry"
            )
        return result

    _crazy_utils.request_gpt_model_multi_threads_with_very_awesome_ui_and_high_efficiency = _patched
    _crazy_utils._paper_trans_rate_limit_patch = True
    print(
        f"[driver] ✅ LaTeX LLM 请求已 patch（workers={max_workers}, "
        f"retries={per_call_retries}, failed_chunk_rounds={retry_rounds}）",
        flush=True,
    )


def _patch_safe_archive_extraction():
    """Validate untrusted arXiv source archives before any extraction."""
    import toolbox as _toolbox

    if getattr(_toolbox, "_paper_trans_safe_archive_patch", False):
        return
    original = _toolbox.extract_archive

    def _safe_extract_archive(file_path, dest_dir, *args, **kwargs):
        if tarfile.is_tarfile(file_path):
            reason = _ltf.source_tar_safety_error(file_path)
            if reason:
                raise RuntimeError(f"unsafe arXiv source archive: {reason}")
        return original(file_path, dest_dir, *args, **kwargs)

    _toolbox.extract_archive = _safe_extract_archive
    _toolbox._paper_trans_safe_archive_patch = True
    print(
        "[driver] ✅ archive extraction 已 patch"
        "（路径穿越/逃逸链接/设备文件/解压体积门禁）",
        flush=True,
    )



def bootstrap(splitter_cache_version: str = SPLITTER_CACHE_VERSION) -> None:
    """Install all gpt-academic adapters exactly once for this process."""
    global SPLITTER_CACHE_VERSION
    SPLITTER_CACHE_VERSION = str(splitter_cache_version)
    _patch_latex_translation_splitter()
    _patch_latex_fix_content_artifacts()
    _patch_latex_llm_rate_limit_handling()
    _patch_safe_archive_extraction()
