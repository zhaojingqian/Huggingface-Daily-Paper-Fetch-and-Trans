import io
import os
import tarfile
import tempfile
import unittest
from unittest import mock

import latex_translation_filters as filters


class LatexTranslationFiltersTest(unittest.TestCase):
    def test_latex_prose_probe_keeps_custom_macro_human_argument(self):
        value = (
            r"\compactbullet{If \(m\) is irrational, then the line can "
            r"contain at most one integer point. Hence the bound holds.}"
        )
        probe = filters.latex_prose_probe(value)
        self.assertIn("If", probe)
        self.assertIn("Hence the bound holds", probe)

    def test_latex_prose_probe_removes_citation_keys(self):
        probe = filters.latex_prose_probe(
            r"Ordinary explanation \citep{smith_long_benchmark_key} continues."
        )
        self.assertIn("Ordinary explanation", probe)
        self.assertNotIn("smith_long_benchmark_key", probe)

    def test_short_structural_bridge_prose_promotes_formula_neighbors(self):
        self.assertTrue(filters.is_short_structural_bridge_prose(
            r"For the first term,\par"
        ))
        self.assertTrue(filters.is_short_structural_bridge_prose(
            r"\compactbullet{On turn \(2M+1\) she plays the final move.}"
        ))
        self.assertFalse(filters.is_short_structural_bridge_prose(
            r"\captionof{figure}"
        ))
        self.assertFalse(filters.is_short_structural_bridge_prose(
            r"\[\alpha + \beta = \gamma\]"
        ))

    def test_response_gate_rejects_changed_brace_balance(self):
        source = (
            r"\textbf{First, bound }\(y\). "
            r"Then the structure of the list is restricted.\par"
        )
        translated = (
            r"\textbf{首先，界定}\(y\)。"
            r"此时列表的结构非常受限。\par}"
        )
        self.assertEqual(
            filters.llm_translation_response_invalid(source, translated),
            "latex_brace_balance_mismatch",
        )

    def test_normalize_response_removes_only_safe_trailing_brace(self):
        source = (
            r"\emph{If there is no adjacent equal pair}, then merge it.\par"
        )
        translated = r"\emph{若没有相邻相等对}，则将其合并。\par}"
        normalized = filters.normalize_llm_translation_response(
            source,
            translated,
        )
        self.assertEqual(normalized, translated[:-1])
        self.assertEqual(
            filters.llm_translation_response_invalid(source, normalized),
            "",
        )

    def test_compact_colored_label_with_localized_connector_is_accepted(self):
        source = r"\textbf{\textcolor{iclrdeepblue}{SU-01} w/ TTS}"
        response = r"\textbf{\textcolor{iclrdeepblue}{SU-01} 带 TTS}"
        self.assertFalse(
            filters.llm_translation_response_untranslated(source, response)
        )

    def test_force_no_tex_shell_escape_handles_paths_quotes_and_conflicts(self):
        self.assertEqual(
            filters.force_no_tex_shell_escape(
                "/usr/bin/pdflatex -shell-escape -interaction=batchmode x.tex"
            ),
            "/usr/bin/pdflatex -no-shell-escape -interaction=batchmode x.tex",
        )
        self.assertEqual(
            filters.force_no_tex_shell_escape(
                '"xelatex" --enable-write18 -no-shell-escape x.tex'
            ),
            '"xelatex" -no-shell-escape x.tex',
        )
        self.assertEqual(
            filters.force_no_tex_shell_escape(
                "latexdiff a.tex b.tex > diff.tex"
            ),
            "latexdiff a.tex b.tex > diff.tex",
        )

    def test_source_tar_safety_rejects_traversal_and_escaping_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            safe_path = os.path.join(tmp, "safe.tar")
            with tarfile.open(safe_path, "w") as archive:
                root = tarfile.TarInfo(".")
                root.type = tarfile.DIRTYPE
                archive.addfile(root)
                payload = b"\\documentclass{article}"
                member = tarfile.TarInfo("paper/main.tex")
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))
                link = tarfile.TarInfo("paper/current.tex")
                link.type = tarfile.SYMTYPE
                link.linkname = "main.tex"
                archive.addfile(link)
            self.assertEqual(filters.source_tar_safety_error(safe_path), "")

            traversal_path = os.path.join(tmp, "traversal.tar")
            with tarfile.open(traversal_path, "w") as archive:
                member = tarfile.TarInfo("../../config_private.py")
                member.size = 1
                archive.addfile(member, io.BytesIO(b"x"))
            self.assertIn(
                "unsafe archive member path",
                filters.source_tar_safety_error(traversal_path),
            )

            symlink_path = os.path.join(tmp, "symlink.tar")
            with tarfile.open(symlink_path, "w") as archive:
                link = tarfile.TarInfo("paper/secret")
                link.type = tarfile.SYMTYPE
                link.linkname = "../../config_private.py"
                archive.addfile(link)
            self.assertIn(
                "unsafe archive symlink",
                filters.source_tar_safety_error(symlink_path),
            )

    def test_hard_env_policy_recognizes_code_trace_names(self):
        self.assertTrue(filters.is_hard_protected_env("climode"))
        self.assertTrue(filters.is_hard_protected_env("trajactGUI"))
        self.assertTrue(filters.is_hard_protected_env("custom_prompt"))
        self.assertTrue(filters.is_hard_protected_env("promptbox"))
        self.assertTrue(filters.is_hard_protected_env("BuildTranscript"))
        self.assertTrue(filters.is_soft_text_env("algorithmic"))
        self.assertFalse(filters.is_hard_protected_env("algorithmic"))

    def test_verbatim_restore_envs_discovers_declarations_and_dynamic_begins(self):
        tex = r"""
        \newtcblisting{terminalBox}{}
        \begin{customCLITrace}
        click the Launch button
        \end{customCLITrace}
        \begin{algorithmic}
        \State Natural language should still be handled as soft text.
        \end{algorithmic}
        \begin{table*}
        \begin{tabular}{ll}
        English prose table cell & should not be restored as verbatim.
        \end{tabular}
        \end{table*}
        """

        envs = filters.verbatim_restore_envs(tex)

        self.assertIn("terminalBox", envs)
        self.assertIn("customCLITrace", envs)
        self.assertNotIn("algorithmic", envs)
        self.assertNotIn("table*", envs)
        self.assertNotIn("tabular", envs)

    def test_env_vars_extend_policy(self):
        with mock.patch.dict(os.environ, {"PAPER_TRANS_EXTRA_HARD_ENVS": "specialProof"}):
            self.assertTrue(filters.is_hard_protected_env("specialProof"))
            self.assertTrue(filters.is_tracked_env("specialProof"))

    def test_discovered_code_and_benchmark_data_envs_are_protected(self):
        for env in (
            "casecode",
            "strategycode",
            "toolcall",
            "caseresponse",
            "errorspan",
            "normalspan",
            "templatebubble",
            "CCSXML",
            "paperresources",
        ):
            with self.subTest(env=env):
                self.assertTrue(filters.is_dynamic_hard_env(env))
        self.assertTrue(filters.is_hard_protected_env("comment"))

    def test_semantic_source_data_protection_is_instance_scoped(self):
        protected = (
            (
                "tcolorbox",
                r"\begin{tcolorbox}[casebox=yellow!5, title=1. User Query]",
            ),
            (
                "tcolorbox",
                r"\begin{tcolorbox}[promptbox,title={Coarse CoT Template}]",
            ),
            (
                "custombox",
                r"\begin{custombox}[title=Judge Prompt]",
            ),
            (
                "examplebox",
                r"\begin{examplebox}[title={(1) Personality Attribution}]",
            ),
            (
                "casebox",
                r"\begin{casebox}{successbg}{successframe}{成功案例}",
            ),
            (
                "mdframed",
                r"\begin{mdframed}[frametitle={Problem-Solving Prompt}]",
            ),
        )
        for env, opening in protected:
            with self.subTest(env=env):
                self.assertTrue(
                    filters.is_semantic_source_data_opening(env, opening)
                )

        # Environment classes remain translatable unless a concrete instance
        # declares prompt/trace/example/source-data semantics.
        self.assertFalse(filters.is_hard_protected_env("tcolorbox"))
        self.assertFalse(filters.is_semantic_source_data_opening(
            "tcolorbox",
            r"\begin{tcolorbox}[title=Key Insight]",
        ))
        self.assertFalse(filters.is_semantic_source_data_opening(
            "custombox",
            r"\begin{custombox}[title=Main Theorem]",
        ))
        self.assertFalse(filters.is_semantic_source_data_opening(
            "mdframed",
            r"\begin{mdframed}[frametitle={Implementation Details}]",
        ))
        self.assertFalse(filters.is_semantic_source_data_opening(
            "mdframed",
            r"\begin{mdframed}[frametitle={IMO 2025 Problem 1}]",
        ))

    def test_custom_box_question_field_promotes_only_that_instance(self):
        self.assertTrue(filters.is_semantic_source_data_content(
            "custombox",
            r"\textbf{Question:} What is the benchmark answer?",
        ))
        self.assertTrue(filters.is_semantic_source_data_content(
            "custombox",
            r"\textbf{Correct Answer:} The published reference answer.",
        ))
        self.assertTrue(filters.is_semantic_source_data_content(
            "tcolorbox",
            r"\textbf{Task:} Classify the agent trajectory.",
        ))
        self.assertFalse(filters.is_semantic_source_data_content(
            "custombox",
            "This box explains the main theorem in ordinary paper prose.",
        ))
        self.assertFalse(filters.is_semantic_source_data_content(
            "mdframed",
            r"\textbf{Question:} This heading is not a source-data marker here.",
        ))

    def test_strip_llm_translation_artifacts(self):
        text = (
            "正常中文段落。\n"
            "Please provide the section from the English academic paper that you would like me to translate into Chinese.\n"
            "Below is a section from an English academic paper, translated into Chinese:\n"
            "请提供您需要翻译的英文学术论文部分内容。\n"
            "请提供需要翻译的具体英文内容。\n"
            "后续中文段落。"
        )

        stripped, count = filters.strip_llm_translation_artifacts(text)

        self.assertGreaterEqual(count, 4)
        self.assertIn("正常中文段落", stripped)
        self.assertIn("后续中文段落", stripped)
        self.assertNotIn("Please provide", stripped)
        self.assertNotIn("Below is", stripped)
        self.assertNotIn("请提供", stripped)

    def test_strip_llm_translation_artifacts_from_prompt_echoes(self):
        text = (
            "已翻译正文。"
            "Below is the section you provided translated into Chinese. "
            "If you have any specific section you want translated, please provide the text."
            "继续正文。"
            "Certainly! 如果预测的切换相对于参考切换的时间误差小于3帧，则认为该切换成功。"
            "Please provide the section you would like me to translate."
            "尾段。"
        )

        stripped, count = filters.strip_llm_translation_artifacts(text)

        self.assertGreaterEqual(count, 4)
        self.assertIn("已翻译正文", stripped)
        self.assertIn("继续正文", stripped)
        self.assertIn("如果预测的切换", stripped)
        self.assertIn("尾段", stripped)
        self.assertNotIn("Below is the section", stripped)
        self.assertNotIn("specific section", stripped)
        self.assertNotIn("Certainly!", stripped)
        self.assertNotIn("Please provide", stripped)

    def test_strip_serialized_translation_artifacts(self):
        text = (
            "正文。\n"
            '  "translation": "\\\\section{引言}\\\\n伪造内容。"\n'
            '["\\\\section{数据处理}\\\\n伪造内容。"]\n'
            "后文。"
        )

        stripped, count = filters.strip_llm_translation_artifacts(text)

        self.assertEqual(count, 2)
        self.assertIn("正文。", stripped)
        self.assertIn("后文。", stripped)
        self.assertNotIn('"translation"', stripped)
        self.assertNotIn("数据处理", stripped)

    def test_detects_failed_llm_chunk_responses(self):
        self.assertTrue(filters.llm_translation_response_failed(""))
        self.assertTrue(filters.llm_translation_response_failed(
            "[Local Message] 警告，线程7在执行过程中遭遇问题, Traceback：429"
        ))
        self.assertTrue(filters.llm_translation_response_failed(
            "429 Client Error: Too Many Requests"
        ))
        self.assertFalse(filters.llm_translation_response_failed(
            r"\section{方法}我们提出一种新的训练方法。"
        ))
        self.assertTrue(filters.llm_translation_response_quota_failed(
            "insufficient_user_quota: balance $0.005 is insufficient"
        ))

    def test_detects_untranslated_llm_chunk_responses(self):
        source = (
            "This paragraph explains the training method and evaluation "
            "protocol used throughout the paper."
        )
        self.assertTrue(filters.llm_translation_response_untranslated(
            source,
            source,
        ))
        self.assertFalse(filters.llm_translation_response_untranslated(
            source,
            "本段解释了全文采用的训练方法和评估协议。",
        ))
        self.assertFalse(filters.llm_translation_response_untranslated(
            r"\section{GPT-4o}",
            r"\section{GPT-4o}",
        ))
        for short_echo in (
            "Similarly, we have",
            "Since we can show",
            "Finally, for this case, we have",
        ):
            with self.subTest(short_echo=short_echo):
                self.assertTrue(
                    filters.llm_translation_response_untranslated(
                        short_echo,
                        short_echo,
                    )
                )

    def test_rejects_mixed_chinese_response_with_english_prose_clause(self):
        source = (
            "The system significantly outperforms independent generators and "
            "the agentic baseline on every benchmark."
        )
        partial = (
            "该系统显著优于独立生成器和 the agentic baseline on "
            "所有基准测试。"
        )

        self.assertTrue(filters.mixed_untranslated_english_clauses(partial))
        self.assertTrue(filters.llm_translation_response_untranslated(
            source,
            partial,
        ))

    def test_mixed_clause_gate_keeps_names_quotes_and_code(self):
        source = "This sentence must be translated into Chinese."
        for response in (
            "我们遵循 Values in the Wild 和 LongBench v2 的评估设置。",
            "系统使用 \\textit{first care, then order, then the business of the day} 作为示例。",
            "输入句子为：``Here we see that constraints imposed by GS-EC make it superior than GS-GR in terms of retrieval.''",
            "调用 \\texttt{book_table(restaurant_id, date, time, guests)} 完成操作。",
            r"执行 \cmd{search 'The Joggers band lead singer son of American chemist'} 继续检索。",
            r"Look 任务（即 look\_at\_obj\_in\_light）提升了11.0%。",
            "\\fbresult{score from JSON-schema validation failure on a single field.}",
            "下面规则必须严格执行：Do NOT renumber the documents as 1,2,3,...; use their original ids.\\\\",
        ):
            with self.subTest(response=response):
                self.assertFalse(
                    filters.mixed_untranslated_english_clauses(response)
                )
                self.assertFalse(filters.llm_translation_response_untranslated(
                    source,
                    response,
                ))

    def test_tikz_drawing_fragment_is_not_translation_prose(self):
        drawing = (
            r"\fill[#1!\a] ([shift={(0,24-8*\r)}]path picture bounding box."
            r"south west) rectangle ([shift={(8,32-8*\r)}]"
            r"path picture bounding box.south west);"
        )

        self.assertTrue(filters.is_tikz_drawing_fragment(drawing))
        self.assertFalse(filters.mixed_untranslated_english_clauses(drawing))
        self.assertFalse(
            filters.llm_translation_response_untranslated(drawing, drawing)
        )

    def test_tikz_style_fragment_is_not_rescued_as_prose(self):
        style = (
            "grid42/.style={kcpcell, minimum width=16pt, "
            "minimum height=32pt,"
        )

        self.assertTrue(filters.is_tikz_style_definition_fragment(style))
        self.assertFalse(filters.is_plain_prose_line_for_rescue(style))
        self.assertFalse(
            filters.llm_translation_response_untranslated(style, style)
        )

    def test_terminal_period_keeps_citation_catalog_non_prose(self):
        source = (
            r"\item \textbf{Coding}: DeepSWE~\citep{deepswe}, "
            r"ProgramBench~\citep{programbench}, "
            r"Terminal-Bench~2.1~\citep{terminal}, "
            r"FrontierSWE~\citep{frontier}, "
            r"SWE-Marathon~\citep{marathon}, and "
            r"SciCode~\citep{scicode}."
        )
        response = (
            r"\item \textbf{编码}：DeepSWE~\citep{deepswe}，"
            r"ProgramBench~\citep{programbench}，"
            r"Terminal-Bench~2.1~\citep{terminal}，"
            r"FrontierSWE~\citep{frontier}，"
            r"SWE-Marathon~\citep{marathon}，以及 "
            r"SciCode~\citep{scicode}。"
        )

        self.assertTrue(filters.is_citation_heavy_proper_name_catalog(source))
        self.assertFalse(
            filters.llm_translation_response_untranslated(source, response)
        )

    def test_plain_prose_rescue_keeps_math_structure_protected(self):
        self.assertTrue(filters.is_plain_prose_line_for_rescue(
            r"We treat scalar weights $w_i,\hat{w}_i$ as unchanged if"
        ))
        self.assertTrue(filters.is_plain_prose_line_for_rescue(
            "distillation and training efficiency"
        ))
        for protected in (
            r"\begin{equation}",
            r"\section{Related Work}",
            r"\[",
            r"\fill[red] (0,0) rectangle (1,1);",
        ):
            with self.subTest(protected=protected):
                self.assertFalse(
                    filters.is_plain_prose_line_for_rescue(protected)
                )

    def test_rejects_task_echo_before_merging_prompt_box_chunk(self):
        source = r"""
\begin{tcolorbox}[promptbox,title={Coarse CoT Template}]
\begin{itemize}
\item Follow rule one~\citep{rule_one}.
\item Follow rule two.
\end{itemize}
\end{tcolorbox}
"""
        corrupted = r"""
\section{引言}
深度学习在图像分类任务中取得了显著进展~\citep{invented}。
Classification: Academic Translation
Task: English to Chinese
Language: Chinese
"""

        self.assertEqual(
            filters.llm_translation_response_invalid(source, corrupted),
            "translation_task_echo",
        )

    def test_single_line_code_output_instruction_is_protected_and_bounded(self):
        instructions = (
            (
                "Return only the corrected Python code inside a single "
                r"\promptfence{python} code block."
            ),
            "Then provide your assessment in exactly three lines:",
            r"Then provide your reasoning in an \texttt{<Analysis>} block:",
            (
                "Do not output any additional text outside the "
                r"\texttt{<Analysis>} block and the three classification lines."
            ),
            (
                r"\item Do not output any additional text outside the "
                r"\texttt{<Analysis>}"
            ),
            (
                "You MUST output in this exact format -- no other text "
                "outside the tags:"
            ),
            (
                "Please reason step by step, and put your final answer within "
                r"\texttt{\textbackslash boxed\{\}}."
            ),
        )

        for instruction in instructions:
            with self.subTest(instruction=instruction):
                self.assertTrue(
                    filters.is_inline_prompt_source_data_block(instruction)
                )
                protected, state = (
                    filters.inline_prompt_source_data_line_protected(
                        instruction
                    )
                )
                self.assertTrue(protected)
                self.assertFalse(state["active"])
                next_protected, _ = (
                    filters.inline_prompt_source_data_line_protected(
                        "This ordinary appendix explanation must still be translated.",
                        state,
                    )
                )
                self.assertFalse(next_protected)

    def test_rejects_latex_structure_or_citation_loss_before_merge(self):
        source = r"""
\begin{itemize}
\item Keep the cited rule~\citep{rule_one}.
\item Keep the second rule.
\end{itemize}
"""
        self.assertEqual(
            filters.llm_translation_response_invalid(
                source,
                r"\section{引言}这是一个虚构段落。",
            ),
            "critical_latex_structure_mismatch",
        )
        self.assertEqual(
            filters.llm_translation_response_invalid(
                source,
                r"""\begin{itemize}
\item 保留第一条规则。
\item 保留第二条规则。
\end{itemize}""",
            ),
            "citation_structure_mismatch",
        )

    def test_citation_structure_allows_reordering_but_preserves_multiset(self):
        source = (
            r"VERL~\citep[see][p.~1]{verl} is compared with "
            r"SGLang~\citep{sglang}."
        )
        reordered = (
            r"我们比较 SGLang~\citep{sglang} 与 "
            r"VERL~\citep[see][p.~1]{verl}。"
        )

        self.assertEqual(
            filters.llm_translation_response_invalid(source, reordered),
            "",
        )
        for corrupted in (
            r"仅保留 SGLang~\citep{sglang}。",
            reordered + r" 重复~\citep{sglang}。",
            (
                r"SGLang~\citep{sglang} 与 "
                r"VERL~\citet[see][p.~1]{verl}。"
            ),
            (
                r"SGLang~\citep{sglang} 与 "
                r"VERL~\citep[see][p.~2]{verl}。"
            ),
        ):
            with self.subTest(corrupted=corrupted):
                self.assertEqual(
                    filters.llm_translation_response_invalid(
                        source,
                        corrupted,
                    ),
                    "citation_structure_mismatch",
                )

    def test_citation_structure_allows_translated_optional_note(self):
        source = (
            r"See \citep[Figure 3]{alpha} and "
            r"\citep[Figure~18]{beta}."
        )
        translated = (
            r"见 \citep[图3]{alpha} 与 "
            r"\citep[图18]{beta}。"
        )

        self.assertEqual(
            filters.llm_translation_response_invalid(source, translated),
            "",
        )
        self.assertEqual(
            filters.llm_translation_response_invalid(
                source,
                translated.replace("{beta}", "{gamma}"),
            ),
            "citation_structure_mismatch",
        )

    def test_structural_retry_prompt_adds_preservation_instruction(self):
        base = "Translate the paper fragment into Chinese."
        strengthened = filters.translation_retry_system_prompt(
            base,
            "critical_latex_structure_mismatch",
        )

        self.assertIn("preserve every LaTeX command", strengthened)
        self.assertIn("citation keys", strengthened)
        self.assertEqual(
            filters.translation_retry_system_prompt(
                base,
                "request_or_untranslated",
            ),
            base,
        )
        self.assertEqual(
            filters.translation_retry_system_prompt(
                strengthened,
                "citation_structure_mismatch",
            ),
            strengthened,
        )

    def test_structure_evidence_exposes_later_paragraph_loss(self):
        # This is the shape that made 2606.05553 look harmless in a 180-char
        # preview: the subsection/label and first paragraph matched, while a
        # later paragraph was absent.  The gate must still reject it, and the
        # diagnostic must make the difference immediately visible.
        source = (
            r"\subsection{Evaluation validation\label{sec:judge-agreement}} "
            r"\paragraph{Human-anchored judge plausibility.} First result. "
            r"\paragraph{Cross-judge replication.} Second result."
        )
        response = (
            r"\subsection{评估验证\label{sec:judge-agreement}} "
            r"\paragraph{基于人工的评审合理性。} 第一个结果。"
        )

        self.assertEqual(
            filters.llm_translation_response_invalid(source, response),
            "critical_latex_structure_mismatch",
        )
        evidence = filters.llm_translation_structure_evidence(source, response)
        self.assertEqual(
            evidence["source_commands"],
            ("subsection", "label", "paragraph", "paragraph"),
        )
        self.assertEqual(
            evidence["response_commands"],
            ("subsection", "label", "paragraph"),
        )
        self.assertEqual(evidence["source_citations_only"], ())
        self.assertEqual(evidence["response_citations_only"], ())

    def test_structure_evidence_reports_directional_citation_multiset_diff(self):
        source = r"Prior work~\citep{alpha} and~\citep{beta}."
        response = r"已有工作~\citep{alpha} 和~\citep{gamma}."

        evidence = filters.llm_translation_structure_evidence(source, response)

        self.assertEqual(
            evidence["source_commands"], evidence["response_commands"]
        )
        self.assertEqual(
            evidence["source_citations_only"], ((r"\citep[]{beta}", 1),)
        )
        self.assertEqual(
            evidence["response_citations_only"], ((r"\citep[]{gamma}", 1),)
        )

    def test_normalizes_single_heading_wrapper_around_bare_caption_argument(self):
        source = r"Cold-start Trajectory Generation for \ourmethod{}"
        response = r"\section{针对\ourmethod{}的冷启动轨迹生成}"
        payload = ["translate_zh segment-17", response]

        changed = filters.normalize_llm_translation_payload(
            payload,
            [source],
        )

        self.assertEqual(changed, [0])
        self.assertEqual(payload[1], r"针对\ourmethod{}的冷启动轨迹生成")
        self.assertEqual(
            filters.llm_translation_response_invalid(source, payload[1]),
            "",
        )

    def test_normalizes_one_bare_hallucinated_section_command(self):
        source = (
            r"With TinyLoRA, we can compress the learned policy update to as "
            r"few as one trainable scalar \cite{morris2026learning}. This "
            "extreme parameterization forms the architectural basis."
        )
        response = (
            r"通过 TinyLoRA，我们可以将策略更新压缩到一个可训练标量 "
            r"\cite{morris2026learning}。这种参数化构成了架构基础（第 "
            r"\section）。"
        )

        normalized = filters.normalize_llm_translation_response(source, response)

        self.assertIn("第 章节", normalized)
        self.assertNotIn(r"\section", normalized)
        self.assertEqual(
            filters.llm_translation_response_invalid(source, normalized),
            "",
        )

    def test_bare_heading_normalizer_rejects_multiple_or_real_commands(self):
        source = r"See the section for details."
        multiple = r"见第 \section 和 \subsection。"
        real_source = r"\section{Method}"
        real_response = r"\section"

        self.assertEqual(
            filters.normalize_llm_translation_response(source, multiple),
            multiple,
        )
        self.assertEqual(
            filters.normalize_llm_translation_response(real_source, real_response),
            real_response,
        )

    def test_heading_wrapper_normalizer_rejects_extra_text_or_commands(self):
        source = r"Cold-start Trajectory Generation for \ourmethod{}"
        malicious = (
            r"\section{针对\ourmethod{}的冷启动轨迹生成}"
            "\n额外伪造正文。"
        )
        multiple = (
            r"\section{针对\ourmethod{}的冷启动轨迹生成}"
            r"\subsection{额外标题}"
        )
        lost_citation = r"\section{冷启动轨迹生成}"
        cited_source = source + r"~\citep{cold_start}"

        for value in (malicious, multiple):
            with self.subTest(value=value):
                self.assertEqual(
                    filters.normalize_llm_translation_response(source, value),
                    value,
                )
                self.assertEqual(
                    filters.llm_translation_response_invalid(source, value),
                    "critical_latex_structure_mismatch",
                )
        self.assertEqual(
            filters.normalize_llm_translation_response(
                cited_source,
                lost_citation,
            ),
            lost_citation,
        )

    def test_heading_wrapper_normalizer_never_strips_real_caption_command(self):
        source = r"\caption{Cold-start Trajectory Generation for \ourmethod{}}"
        response = r"\section{针对\ourmethod{}的冷启动轨迹生成}"

        self.assertEqual(
            filters.normalize_llm_translation_response(source, response),
            response,
        )
        self.assertEqual(
            filters.llm_translation_response_invalid(source, response),
            "critical_latex_structure_mismatch",
        )

    def test_short_ref_translation_ignores_reference_key_for_language_ratio(self):
        source = (
            r"Substituting into Eq.~\ref{eq:small_beta_teacher_gain_app} gives"
        )
        translated = r"代入式~\ref{eq:small_beta_teacher_gain_app} 得到"

        self.assertEqual(
            filters.llm_translation_response_invalid(source, translated),
            "",
        )
        self.assertFalse(
            filters.llm_translation_response_untranslated(source, translated)
        )
        self.assertTrue(
            filters.llm_translation_response_untranslated(source, source)
        )

    def test_citation_structure_ignores_equivalent_internal_whitespace(self):
        source = "\\cite{alpha,\nbeta,\ngamma}"
        translated = r"\cite{alpha, beta, gamma}"

        self.assertEqual(
            filters.llm_translation_response_invalid(source, translated),
            "",
        )

        self.assertEqual(
            filters.llm_translation_response_invalid(
                r"\citep{alpha,beta, gamma}",
                r"\citep{alpha, beta,gamma}",
            ),
            "",
        )

    def test_bracketed_key_value_options_are_structural_not_untranslated(self):
        options = (
            r"[fontsize=\small, breaklines=true, breakanywhere=true, "
            r"breakindent=2em]"
        )

        self.assertTrue(filters.is_bracketed_key_value_option_list(options))
        self.assertFalse(
            filters.llm_translation_response_untranslated(options, options)
        )
        self.assertTrue(
            filters.llm_translation_response_untranslated(options, "")
        )

    def test_bracketed_english_prose_is_not_misclassified_as_options(self):
        prose_label = (
            "[This English prose label explains the complete evaluation "
            "procedure used for every benchmark.]"
        )
        prose_value = (
            "[label=This English prose should remain translated in the paper, "
            "mode=true]"
        )

        for value in (prose_label, prose_value):
            with self.subTest(value=value):
                self.assertFalse(
                    filters.is_bracketed_key_value_option_list(value)
                )
                self.assertTrue(
                    filters.llm_translation_response_untranslated(value, value)
                )

    def test_untranslated_check_allows_citation_backed_name_catalog(self):
        catalog = (
            r"Qwen2.5-Omni-7B~\citep{qwen2.5omni}, "
            r"Step-Audio-2-mini~\citep{stepaudio2}, "
            r"Voxtral-Mini-3B~\citep{voxtral}, "
            r"Kimi-Audio-7B~\citep{Kimi-Audio}, "
            r"Gemini-3-Flash~\citep{}, "
            r"Seed-ASR~\citep{seedasr}, "
        )

        self.assertTrue(
            filters.is_citation_heavy_proper_name_catalog(catalog)
        )
        self.assertFalse(
            filters.llm_translation_response_untranslated(catalog, catalog)
        )
        for fragment in (
            (
                r"Qwen2.5-Omni-7B~\citep{qwen2.5omni}, "
                r"Step-Audio-2-mini~\citep{stepaudio2}, "
                r"Voxtral-Mini-3B~\citep{voxtral}, "
            ),
            (
                r"Kimi-Audio-7B~\citep{Kimi-Audio}, "
                r"Gemini-3-Flash~\citep{}, Seed-ASR~\citep{seedasr},"
            ),
        ):
            with self.subTest(fragment=fragment):
                self.assertTrue(
                    filters.is_citation_heavy_proper_name_catalog(fragment)
                )
                self.assertFalse(
                    filters.llm_translation_response_untranslated(
                        fragment,
                        fragment,
                    )
                )
        connector_catalog = (
            r"PerceptionBench~\citep{perceptionbench}, "
            r"Video-MME~\citep{videomme}, MMVU~\citep{mmvu}, and"
        )
        self.assertTrue(
            filters.is_citation_heavy_proper_name_catalog(
                connector_catalog
            )
        )

    def test_citation_catalog_check_does_not_hide_explanatory_prose(self):
        prose = (
            r"This study compares Qwen-Omni~\citep{qwen}, "
            r"Step-Audio~\citep{step}, Voxtral~\citep{voxtral}, and "
            r"Kimi-Audio~\citep{kimi} across difficult speech benchmarks."
        )

        self.assertFalse(
            filters.is_citation_heavy_proper_name_catalog(prose)
        )
        self.assertTrue(
            filters.llm_translation_response_untranslated(prose, prose)
        )

    def test_translated_heading_allows_proper_name_catalog_tail(self):
        source = (
            r"\paragraph{Third-party results} GDPval-AA v2, AA-Briefcase, "
            r"$\tau^3$-Banking, Harvey Lab-AA, APEX-Agents, SciCode,"
        )
        response = (
            r"\paragraph{第三方结果} GDPval-AA v2，AA-Briefcase，"
            r"$\tau^3$-Banking，Harvey Lab-AA，APEX-Agents，SciCode，"
        )

        self.assertTrue(
            filters.is_translated_heading_proper_name_catalog(
                source,
                response,
            )
        )
        self.assertFalse(
            filters.llm_translation_response_untranslated(source, response)
        )

    def test_translated_heading_catalog_does_not_hide_explanatory_tail(self):
        source = (
            r"\paragraph{Results} Our method compares Alpha, Beta, Gamma, "
            r"Delta, and Epsilon across difficult benchmarks."
        )
        response = (
            r"\paragraph{结果} Our method compares Alpha, Beta, Gamma, "
            r"Delta, and Epsilon across difficult benchmarks."
        )

        self.assertFalse(
            filters.is_translated_heading_proper_name_catalog(
                source,
                response,
            )
        )
        self.assertTrue(
            filters.llm_translation_response_untranslated(source, response)
        )

    def test_untranslated_check_ignores_technical_version_footnote(self):
        source = (
            r"\footnote{Nemotron-Cascade-2:vLLM-0.17.2rc1.dev148+"
            r"g47b7af0d8.cu128 ; DeepSeek-V3.2-Speciale: "
            r"vLLM-v0.20-CUDA12.9.}. We"
        )
        response = (
            r"\footnote{Nemotron-Cascade-2:vLLM-0.17.2rc1.dev148+"
            r"g47b7af0d8.cu128 ; DeepSeek-V3.2-Speciale: "
            r"vLLM-v0.20-CUDA12.9.}。我们"
        )

        self.assertFalse(
            filters.llm_translation_response_untranslated(source, response)
        )

    def test_untranslated_check_keeps_natural_language_footnote(self):
        source = (
            r"\footnote{This ordinary explanatory footnote remains natural "
            r"language prose and must be translated by the model.}"
        )

        self.assertTrue(
            filters.llm_translation_response_untranslated(source, source)
        )

    def test_untranslated_check_ignores_upstream_english_prompt(self):
        prompt = (
            "Below is a section from an English academic paper, translate it "
            "into Chinese. Do not modify any latex command. "
            "Answer me only with the translated text:\n\n"
            r"\section{GPT-4o}"
        )
        self.assertEqual(
            filters.extract_translation_fragment(prompt),
            r"\section{GPT-4o}",
        )
        self.assertFalse(filters.llm_translation_response_untranslated(
            prompt,
            r"\section{GPT-4o}",
        ))
        structural = (
            r"\begin{tcolorbox}[colback=gray!5, colframe=gray!60, "
            r"boxrule=0.4pt, left=4pt, right=4pt, top=2pt, bottom=2pt]"
        )
        self.assertFalse(filters.llm_translation_response_untranslated(
            structural,
            structural,
        ))
        item_label = (
            r"\item[This English prose explains the complete evaluation "
            r"procedure used for every benchmark.]"
        )
        self.assertTrue(filters.llm_translation_response_untranslated(
            item_label,
            item_label,
        ))
        code_list = (
            r"\texttt{get\_weather(location, units)}, "
            r"\texttt{book\_table(restaurant\_id, date, time, guests)}, "
            r"\texttt{check\_schedule(user\_email, date)}"
        )
        self.assertFalse(filters.llm_translation_response_untranslated(
            code_list,
            code_list,
        ))
        url_line = (
            r"L1: URL: https://media.example.com/connector?cmd=file\&target="
            r"v1\_XFNjaGVkdWxlc1xUUl9sb25nX3BheWxvYWQ="
        )
        self.assertFalse(filters.llm_translation_response_untranslated(
            url_line,
            url_line,
        ))
        json_code = (
            r'\smalltt{\{"query": "three albums", "topn": 10, '
            r'"source": "news"\}}'
        )
        self.assertFalse(filters.llm_translation_response_untranslated(
            json_code,
            json_code,
        ))

    def test_coalesces_adjacent_translation_fragments(self):
        fragments = [
            ("First sentence. ", False),
            ("Second sentence.\n", False),
            (r"\begin{equation}", True),
            ("\n", True),
            ("Following paragraph.", False),
        ]

        merged = filters.coalesce_translation_fragments(fragments)

        self.assertEqual(merged, [
            ("First sentence. Second sentence.\n", False),
            (r"\begin{equation}" "\n", True),
            ("Following paragraph.", False),
        ])

    def test_default_coalescer_does_not_create_oversized_request(self):
        merged = filters.coalesce_translation_fragments([
            ("A" * 700, False),
            ("B" * 700, False),
        ])

        self.assertEqual(merged, [
            ("A" * 700, False),
            ("B" * 700, False),
        ])

    def test_headings_force_distinct_translation_units(self):
        source = (
            r"\subsection{Validation} Intro text. "
            r"\paragraph{Human study.} First result~\ref{tab:first}. "
            r"\paragraph{Agreement.} Second result~\ref{tab:second}."
        )
        units = filters.split_translation_structural_units(source)
        merged = filters.coalesce_translation_fragments([
            (unit, False) for unit in units
        ])

        self.assertEqual(len(units), 3)
        self.assertEqual(len(merged), 3)
        self.assertTrue(units[1].startswith(r"\paragraph"))
        self.assertIn(r"\ref{tab:first}", units[1])
        self.assertIn(r"\ref{tab:second}", units[2])

    def test_structure_dense_prose_uses_smaller_chunk_limit(self):
        citations = (
            r"Prior work \cite{a}, \citep{b}, and \citet{c} motivates this."
        )
        references = (
            r"Compare \ref{tab:first} with \ref{tab:second} in our study."
        )

        self.assertEqual(
            filters.recommended_translation_chunk_limit(citations),
            120,
        )
        self.assertEqual(
            filters.recommended_translation_chunk_limit(references),
            350,
        )
        self.assertEqual(
            filters.recommended_translation_chunk_limit(
                "Ordinary prose without structural references."
            ),
            1200,
        )
        dense_fragments = [
            ("A" * 120 + r"\cite{a}\cite{b}", False),
            ("B" * 120 + r"\cite{c}", False),
        ]
        self.assertEqual(
            len(filters.coalesce_translation_fragments(dense_fragments)),
            2,
        )

    def test_final_fragment_limit_rechecks_after_intermediate_merge(self):
        source = (
            "Automatic speech recognition has evolved rapidly. "
            r"Models~\citep{qwen3-asr} perform well on benchmarks "
            r"such as LibriSpeech~\citep{librispeech}. "
            r"Audio-language models~\citep{qwen3-omni} also support "
            r"reasoning-based correction~\citep{reasoningforasr}."
        )
        bounded = filters.enforce_translation_fragment_limits([
            (source, False),
        ])

        self.assertEqual("".join(text for text, _ in bounded), source)
        self.assertGreater(len(bounded), 1)
        self.assertTrue(all(not preserve for _, preserve in bounded))
        self.assertTrue(all(
            len(text) <= filters.recommended_translation_chunk_limit(text)
            for text, _ in bounded
        ))

    def test_final_fragment_limit_preserves_whitespace_tail(self):
        bounded = filters.enforce_translation_fragment_limits([
            ("\n", False),
        ])

        self.assertEqual("".join(text for text, _ in bounded), "\n")
        self.assertTrue(bounded[-1][1])
        self.assertFalse(bounded[-1][0].strip())

    def test_coalescer_forces_pure_option_list_to_preserve(self):
        options = (
            r"[fontsize=\small, breaklines=true, breakanywhere=true, "
            r"breakindent=2em]"
        )
        prose = "This ordinary English sentence still needs translation."

        merged = filters.coalesce_translation_fragments([
            (options, False),
            (prose, False),
        ])

        self.assertEqual(merged, [
            (options, True),
            (prose, False),
        ])

    def test_coalesces_whitespace_between_prose_with_size_cap(self):
        fragments = [
            ("First prose sentence.", False),
            ("\n", True),
            ("Second prose sentence.", False),
            (r"\begin{equation}", True),
            ("A" * 40, False),
            ("B" * 40, False),
        ]

        merged = filters.coalesce_translation_fragments(
            fragments,
            max_translate_chars=60,
        )

        self.assertEqual(merged[:2], [
            ("First prose sentence.\nSecond prose sentence.", False),
            (r"\begin{equation}", True),
        ])
        self.assertEqual(merged[-3:], [
            (r"\begin{equation}", True),
            ("A" * 40, False),
            ("B" * 40, False),
        ])

    def test_absorbs_short_citation_prose_into_neighboring_translation(self):
        fragments = [
            ("Prior work explains the main result", False),
            (r" \cite{alpha}, including ", True),
            ("the strongest baseline.", False),
            (r"\section{Method}", True),
        ]

        absorbed = filters.coalesce_translation_fragments(
            filters.absorb_short_prose_bridges(fragments)
        )

        self.assertEqual(absorbed, [
            (
                r"Prior work explains the main result \cite{alpha}, including "
                "the strongest baseline.",
                False,
            ),
            (r"\section{Method}", True),
        ])

    def test_absorbs_citation_fragments_split_inside_key_list(self):
        fragments = [
            ("Previous translated prose ", False),
            ("\\cite{alpha,\n", True),
            ("beta}. Recent work studies scaling recipes and ", False),
            ("distillation efficiency \\cite{gamma,\n", True),
            ("delta}. These accounts characterize the method.", False),
        ]

        absorbed = filters.coalesce_translation_fragments(
            filters.absorb_short_prose_bridges(fragments)
        )

        self.assertTrue(all(not preserve for _, preserve in absorbed))
        combined = "".join(text for text, _ in absorbed)
        self.assertIn("\\cite{alpha,\nbeta}", combined)
        self.assertIn("\\cite{gamma,\ndelta}", combined)
        self.assertFalse(any(
            text.rstrip().endswith(("alpha,", "gamma,"))
            for text, _ in absorbed
        ))

    def test_citation_bridge_respects_structure_dense_limit(self):
        fragments = [
            ("A" * 330, False),
            (r" \cite{alpha}, including ", True),
            ("B" * 330 + r" \cite{beta}\cite{gamma}", False),
        ]

        absorbed = filters.absorb_short_prose_bridges(fragments)

        self.assertGreater(len(absorbed), 1)
        self.assertFalse(any(len(text) > 700 for text, _ in absorbed))

    def test_short_prose_bridge_keeps_structural_or_overflow_fragments(self):
        structural = r"\end{abstract} including"
        fragments = [
            ("A" * 20, False),
            (structural, True),
            ("following prose", False),
        ]

        self.assertEqual(
            filters.absorb_short_prose_bridges(fragments, max_translate_chars=25),
            fragments,
        )

    def test_bounded_splitter_keeps_paren_and_bracket_math_balanced(self):
        line = (
            "A" * 220
            + r" \(\text{inside. math; stays together}\) "
            + "B" * 220
            + ". "
            + "C" * 220
            + r" \[\text{display. math; stays together}\] "
            + "D" * 220
        )

        parts = filters.split_translation_line_bounded(
            line,
            max_translate_chars=360,
            min_sentence_chars=120,
        )

        self.assertEqual("".join(parts), line)
        self.assertGreater(len(parts), 2)
        for part in parts:
            self.assertEqual(part.count(r"\("), part.count(r"\)"))
            self.assertEqual(part.count(r"\["), part.count(r"\]"))

    def test_bounded_splitter_caps_unpunctuated_prose(self):
        for line in (
            ("ordinaryword " * 500).strip(),
            "A" * 5000,
        ):
            with self.subTest(has_spaces=" " in line):
                parts = filters.split_translation_line_bounded(
                    line,
                    max_translate_chars=600,
                )
                self.assertEqual("".join(parts), line)
                self.assertGreater(len(parts), 1)
                self.assertTrue(all(len(part) <= 600 for part in parts))

    def test_separate_custom_macro_cjk_glue(self):
        text = (
            r"\newcommand{\methodshort}{Data2Story}" "\n"
            r"\newcommand{\method}{Data Journalist Agent}" "\n"
            r"\newcommand{\yespart}{\ding{51}}" "\n"
            r"\newcommand{\witharg}[1]{#1}" "\n"
            r"\methodshort\并非默认使用纯文本。" "\n"
            r"\methodshort\，这些示例被选取。" "\n"
            r"\method\进行了评估。" "\n"
            r"\yespart标记部分代码。" "\n"
            r"\witharg中文不应改。"
        )

        fixed, count = filters.separate_custom_macro_cjk_glue(text)

        self.assertEqual(count, 12)
        self.assertIn(r"\methodshort 并非", fixed)
        self.assertIn(r"\methodshort ，", fixed)
        self.assertIn(r"\yespart 标记", fixed)
        self.assertIn(r"\witharg中文", fixed)

    def test_separate_builtin_layout_command_from_ascii_prose(self):
        text = (
            r"\parFrom (A)-(C), the result follows." "\n"
            r"\noindentThis paragraph is retained." "\n"
            r"\smallskipNext paragraph." "\n"
            r"\par在我们的情形中，结论成立。" "\n"
            r"\parTherefore\par"
        )

        fixed, count = filters.separate_builtin_layout_ascii_glue(text)

        self.assertEqual(count, 5)
        self.assertIn(r"\par From (A)-(C)", fixed)
        self.assertIn(r"\noindent This paragraph", fixed)
        self.assertIn(r"\smallskip Next paragraph", fixed)
        self.assertIn(r"\par 在我们的情形中", fixed)
        self.assertIn(r"\par Therefore\par", fixed)

    def test_builtin_layout_ascii_glue_preserves_real_or_ambiguous_commands(self):
        text = (
            r"\newcommand{\parFrom}{custom}" "\n"
            r"\parFrom (A)-(C) stays custom." "\n"
            r"\paragraph{Heading}" "\n"
            r"\parbox{2cm}{Box}" "\n"
            r"\parUSA remains ambiguous." "\n"
            r"% example only: \noindentThis must stay literal." "\n"
            r"\begin{verbatim}" "\n"
            r"\parFrom (A)-(C)" "\n"
            r"\end{verbatim}"
        )

        fixed, count = filters.separate_builtin_layout_ascii_glue(text)

        self.assertEqual(count, 0)
        self.assertEqual(fixed, text)

        external_use = r"\parFrom (A)-(C) is supplied by the class."
        external_definition = r"\DeclareRobustCommand{\parFrom}{custom}"
        fixed, count = filters.separate_builtin_layout_ascii_glue(
            external_use,
            external_definition,
        )
        self.assertEqual(count, 0)
        self.assertEqual(fixed, external_use)

    def test_repairs_duplicated_macro_initial(self):
        text = (
            r"\newcommand{\ndiffbase}{9--51\%}" "\n"
            r"结果为\nndiffbase的提升。"
        )

        fixed, count = filters.repair_duplicated_macro_initials(text)

        self.assertEqual(count, 1)
        self.assertIn(r"\ndiffbase的提升", fixed)
        self.assertNotIn(r"\nndiffbase", fixed)

    def test_separate_custom_macro_empty_group_cjk_glue(self):
        text = (
            r"\newcommand{\Ours}{\OURS}" "\n"
            r"\Ours{}通过空间对齐。"
        )

        fixed, count = filters.separate_custom_macro_cjk_glue(text)

        self.assertEqual(count, 2)
        self.assertIn(r"\Ours 通过", fixed)

    def test_separate_custom_macro_robust_command_cjk_glue(self):
        text = (
            r"\DeclareRobustCommand{\ourmethod}{LaMem-VLA\xspace}" "\n"
            r"\ourmethod通过四个模块。"
        )

        fixed, count = filters.separate_custom_macro_cjk_glue(text)

        self.assertEqual(count, 3)
        self.assertIn(r"\ourmethod 通过", fixed)

    def test_collapse_spaced_cjk_characters(self):
        text = r"\item 我 们提出了\Ours{}，一种统一框架。"

        fixed, count = filters.collapse_spaced_cjk_characters(text)

        self.assertEqual(count, 1)
        self.assertIn("我们提出了", fixed)
        self.assertNotIn("我 们", fixed)

    def test_guard_pdftex_primitive_lines(self):
        text = (
            r"\pdfoutput=1" "\n"
            r"\pdfgentounicode =1" "\n"
            r"\pdfinfoomitdate=1" "\n"
            r"  \pdfmapline{+font < font.ttf < enc.enc}" "\n"
            r"\pdfinclusioncopyfonts=1" "\n"
            r"\ifdefined\pdfinfo\pdfinfo{/Title(Test)}\fi" "\n"
            r"\pdfinfo{" "\n"
            r"/TemplateVersion (2027.1)" "\n"
            r"}" "\n"
            r"\section{正文}"
        )

        fixed, count = filters.guard_pdftex_primitive_lines(text)

        self.assertEqual(count, 6)
        self.assertIn(r"\ifdefined\pdfoutput\pdfoutput=1\fi", fixed)
        self.assertIn(r"\ifdefined\pdfgentounicode\pdfgentounicode =1\fi", fixed)
        self.assertIn(r"\ifdefined\pdfinfoomitdate\pdfinfoomitdate=1\fi", fixed)
        self.assertIn(r"  \ifdefined\pdfmapline\pdfmapline{+font < font.ttf < enc.enc}\fi", fixed)
        self.assertIn(r"\ifdefined\pdfinclusioncopyfonts\pdfinclusioncopyfonts=1\fi", fixed)
        self.assertEqual(fixed.count(r"\ifdefined\pdfinfo\pdfinfo"), 2)
        self.assertIn(
            "\\ifdefined\\pdfinfo\\pdfinfo{\n"
            "/TemplateVersion (2027.1)\n"
            "}\\fi",
            fixed,
        )

    def test_unique_label_replacement_handles_translated_long_form(self):
        labels = {
            "tab:sens-bcp-tau",
            "tab:sens-dapo-tau",
            "tab:sens-dapo-gbs",
            "tab:trainconfig",
        }
        self.assertEqual(
            filters.unique_label_replacement(
                "tab:sens-browsecomp-tau",
                labels,
                original_refs={
                    "tab:trainconfig",
                    "tab:sens-bcp-tau",
                    "tab:sens-dapo-tau",
                    "tab:sens-dapo-gbs",
                },
            ),
            "tab:sens-bcp-tau",
        )
        self.assertIsNone(filters.unique_label_replacement(
            "tab:sens-browsecomp-tau",
            labels,
        ))
        self.assertIsNone(filters.unique_label_replacement(
            "tab:sens-unknown",
            labels,
            original_refs=labels,
        ))
        self.assertIsNone(filters.unique_label_replacement(
            "tab:sens-bcp-tau,tab:sens-dapo-tau",
            labels,
            original_refs=labels,
        ))

    def test_splits_multilabel_references_after_cleveref_demotion(self):
        source = (
            "\\label{sec:experiments}\n"
            "\\label{sec:moe}\n"
            "\\label{sec:agent}\n"
            "\\label{sec:datapath}\n"
            r"See \Cref{sec:experiments,sec:moe} and "
            r"\ref{sec:agent,sec:datapath}."
        )
        demoted, demoted_count = filters.demote_cleveref_commands(source)
        fixed, split_count = filters.split_multilabel_references(demoted)
        self.assertEqual(demoted_count, 1)
        self.assertEqual(split_count, 2)
        self.assertIn(r"\ref{sec:experiments}, \ref{sec:moe}", fixed)
        self.assertIn(r"\ref{sec:agent}, \ref{sec:datapath}", fixed)

    def test_multilabel_split_preserves_real_comma_label_and_unknown_parts(self):
        source = (
            "\\label{sec:a,b}\n"
            "\\label{sec:a}\n"
            r"Keep \ref{sec:a,b}; unknown \ref{sec:a,sec:missing}."
        )

        fixed, split_count = filters.split_multilabel_references(source)

        self.assertEqual(split_count, 0)
        self.assertIn(r"\ref{sec:a,b}", fixed)
        self.assertIn(r"\ref{sec:a,sec:missing}", fixed)

    def test_repairs_first_generation_multiline_pdfinfo_guard(self):
        text = (
            "\\ifdefined\\pdfinfo\\pdfinfo{\\fi\n"
            "/TemplateVersion (2027.1)\n"
            "}\n"
            "\\begin{document}\n"
        )
        fixed, count = filters.guard_pdftex_primitive_lines(text)
        self.assertEqual(count, 1)
        self.assertIn(
            "\\ifdefined\\pdfinfo\\pdfinfo{\n"
            "/TemplateVersion (2027.1)\n"
            "}\\fi",
            fixed,
        )
        self.assertNotIn("\\pdfinfo{\\fi", fixed)

    def test_replace_bare_citation_commands_glued_to_cjk(self):
        text = r"测试定义如\cite中所述；正常引用见\cite{smith2026}。"

        fixed, count = filters.replace_bare_citation_commands(text)

        self.assertEqual(count, 1)
        self.assertIn("如文献中所述", fixed)
        self.assertIn(r"\cite{smith2026}", fixed)

    def test_separate_declaration_command_cjk_glue(self):
        text = r"这是{\em去中心化}策略，\tbv\xspace与另一项策略，且 \textit{正常命令} 不变。"

        fixed, count = filters.separate_declaration_command_cjk_glue(text)

        self.assertEqual(count, 2)
        self.assertIn(r"{\em 去中心化}", fixed)
        self.assertIn(r"\tbv\xspace 与另一项策略", fixed)
        self.assertIn(r"\textit{正常命令}", fixed)

    def test_remove_spurious_cjk_command_escapes(self):
        text = r"我们使用\(\widehat{T}\)\作为接受信号，保留\alpha。"

        fixed, count = filters.remove_spurious_cjk_command_escapes(text)

        self.assertEqual(count, 1)
        self.assertIn(r"\(\widehat{T}\)作为接受信号", fixed)
        self.assertIn(r"\alpha", fixed)

    def test_captionexample_is_hard_protected(self):
        self.assertTrue(filters.is_hard_protected_env("captionexample"))

    def test_demote_cleveref_commands(self):
        text = r"见 \cref{fig:a} 与 \Cref[名字]{sec:b}，保留 \ref{tab:c}。"

        fixed, count = filters.demote_cleveref_commands(text)

        self.assertEqual(count, 2)
        self.assertIn(r"\ref{fig:a}", fixed)
        self.assertIn(r"\ref{sec:b}", fixed)
        self.assertIn(r"\ref{tab:c}", fixed)

    def test_disable_microtype_loads_keeps_class_hooks_balanced(self):
        text = "\\AtEndOfClass{\\RequirePackage{microtype}}\n\\RequirePackage[tracking]{microtype}\n正文"

        fixed, count = filters.disable_microtype_package_loads(text)

        self.assertEqual(count, 2)
        self.assertNotIn(r"\AtEndOfClass{", fixed)
        self.assertIn("\n正文", fixed)

    def test_disable_microtype_loads_repairs_historical_broken_marker(self):
        broken = r"\AtEndOfClass{% paper-trans: local microtype load disabled for XeLaTeX}" + "\n正文"

        fixed, count = filters.disable_microtype_package_loads(broken)

        self.assertEqual(count, 1)
        self.assertEqual(fixed, "% paper-trans: local microtype load disabled for XeLaTeX\n正文")

    def test_disable_microtype_loads_neutralizes_dependent_commands_inside_hooks(self):
        text = (
            r"\AtBeginDocument{\DisableLigatures[f]{family=sf*}}" "\n"
            r"\microtypesetup{protrusion=true}"
        )

        fixed, count = filters.disable_microtype_package_loads(text)

        self.assertEqual(count, 2)
        self.assertEqual(fixed, "\\AtBeginDocument{\\relax}\n\\relax")

    def test_disable_microtype_loads_provides_textls_fallback(self):
        text = (
            r"\RequirePackage[tracking]{microtype}" "\n"
            r"\newcommand{\heading}{\textls[18]{Title}}"
        )

        fixed, count = filters.disable_microtype_package_loads(text)

        self.assertGreaterEqual(count, 2)
        self.assertIn(r"\providecommand{\textls}[2][]{#2}", fixed)
        self.assertLess(fixed.index(r"\providecommand{\textls}"), fixed.index(r"\textls[18]"))

    def test_normalize_tex_include_target_strips_harmless_whitespace(self):
        self.assertEqual(filters.normalize_tex_include_target(" 6_conclusion \n"), "6_conclusion")

    def test_fontawesome_command_names_excludes_argument_based_fa_icon(self):
        text = r"\faRobot \faCheckCircle \faIcon{github} \faRobot"

        self.assertEqual(
            filters.fontawesome_command_names(text),
            ("faCheckCircle", "faRobot"),
        )

    def test_fontawesome_fallback_is_inserted_before_enclosing_macro(self):
        text = (
            r"\documentclass{article}" "\n"
            r"\usepackage{fontawesome5}" "\n"
            r"\newcommand{\homepage}[1]{" "\n"
            r"  \faVideo\ #1 -- \faGamepad" "\n"
            r"}" "\n"
            r"\begin{document}\homepage{x}\end{document}" "\n"
        )

        fixed, count = filters.add_fontawesome_legacy_aliases(text)

        self.assertEqual(count, 2)
        marker = "% paper-trans fallback for fontawesome5 legacy aliases"
        self.assertEqual(fixed.count(marker), 1)
        self.assertLess(fixed.index(marker), fixed.index(r"\newcommand{\homepage}"))
        self.assertGreater(fixed.index(marker), fixed.index(r"\usepackage{fontawesome5}"))
        self.assertIn(r"\providecommand{\faVideo}{\textbullet}", fixed)
        self.assertIn(r"\providecommand{\faGamepad}{\textbullet}", fixed)

    def test_fontawesome_fallback_migrates_historical_nested_block(self):
        marker = "% paper-trans fallback for fontawesome5 legacy aliases"
        text = (
            r"\documentclass{article}" "\n"
            r"\newcommand{\homepage}[1]{" "\n"
            + marker + "\n"
            r"\providecommand{\faVideo}{\textbullet}" "\n"
            r"\providecommand{\faGamepad}{\textbullet}" "\n"
            r"\faVideo\ #1 -- \faGamepad" "\n"
            r"}" "\n"
            r"\begin{document}\homepage{x}\end{document}" "\n"
        )

        fixed, count = filters.add_fontawesome_legacy_aliases(text)
        stable, second_count = filters.add_fontawesome_legacy_aliases(fixed)

        self.assertGreaterEqual(count, 2)
        self.assertEqual(fixed.count(marker), 1)
        self.assertLess(fixed.index(marker), fixed.index(r"\newcommand{\homepage}"))
        self.assertEqual(stable, fixed)
        self.assertEqual(second_count, 0)

    def test_preamble_snippet_never_enters_multiline_macro_body(self):
        text = (
            r"\documentclass{article}" "\n"
            r"\newcommand{\homepage}[1]{" "\n"
            r"  prefix \faVideo\ #1" "\n"
            r"}" "\n"
            r"\begin{document}\homepage{x}\end{document}" "\n"
        )

        fixed, changed = filters.insert_latex_preamble_snippet(
            text,
            r"\providecommand{\faVideo}{\textbullet}",
            ("faVideo",),
        )

        self.assertTrue(changed)
        self.assertLess(
            fixed.index(r"\providecommand{\faVideo}"),
            fixed.index(r"\newcommand{\homepage}"),
        )

    def test_restore_tcolorbox_opening_options_from_original(self):
        original = (
            "\\begin{tcolorbox}[boxsep=1.5mm, attach boxed title to top left={xshift=4mm}]\n"
            "Body\n\\end{tcolorbox}"
        )
        translated = (
            "\\begin{tcolorbox}[boxsep=1.5毫米, 将带框标题附加到左上角={xshift=4mm}]\n"
            "正文\n\\end{tcolorbox}"
        )

        fixed, count = filters.restore_environment_opening_options(
            translated, original, "tcolorbox"
        )

        self.assertEqual(count, 1)
        self.assertIn("boxsep=1.5mm, attach boxed title to top left", fixed)
        self.assertIn("正文", fixed)

    def test_remove_unmatched_tcolorbox_ending(self):
        text = (
            r"\begin{tcolorbox}外层"
            r"\begin{tcolorbox}内层\end{tcolorbox}"
            r"\end{tcolorbox}"
            r"\end{tcolorbox}"
        )

        fixed, count = filters.remove_unmatched_environment_endings(text)

        self.assertEqual(count, 1)
        self.assertEqual(fixed.count(r"\begin{tcolorbox}"), 2)
        self.assertEqual(fixed.count(r"\end{tcolorbox}"), 2)

    def test_normalizes_tikz_matrix_node_linebreak(self):
        text = (
            r"\matrix [matrix of nodes] at (current bounding box.north east) {"
            "\n"
            r"\node {Pareto-Performance\\Frontier}; & \node {Value}; \\"
            "\n"
            r"};"
        )

        fixed, count = filters.normalize_tikz_matrix_node_linebreaks(text)

        self.assertEqual(count, 1)
        self.assertIn("Pareto-Performance Frontier", fixed)
        self.assertIn(r"\node {Value}; \\", fixed)

    def test_disables_fragile_tikz_matrix_legend(self):
        text = (
            r"\begin{tikzpicture}" "\n"
            r"\matrix [matrix of nodes] at (current bounding box.north east) {" "\n"
            r"\node {Value}; & \draw (0,0) -- (1,0); \\" "\n"
            r"};" "\n"
            r"\draw (0,0) -- (2,2);" "\n"
            r"\end{tikzpicture}"
        )

        fixed, count = filters.disable_fragile_tikz_matrix_legends(text)

        self.assertEqual(count, 1)
        self.assertIn("omitted incompatible TikZ matrix legend", fixed)
        self.assertIn(r"\draw (0,0) -- (2,2);", fixed)

    def test_keeps_main_tikz_matrix_diagram(self):
        text = (
            r"\matrix (graph) [matrix of nodes] {" "\n"
            r"\node {A}; & \node {B}; \\" "\n"
            r"};"
        )

        fixed, count = filters.disable_fragile_tikz_matrix_legends(text)

        self.assertEqual(count, 0)
        self.assertEqual(fixed, text)

    def test_relocate_packages_from_documentclass_options(self):
        text = "\\documentclass[\n\\usepackage{ctex}\n  11pt,\n]{article}\n正文"

        fixed, count = filters.relocate_packages_from_documentclass_options(text)

        self.assertEqual(count, 1)
        self.assertIn("11pt,", fixed)
        self.assertGreater(fixed.index(r"\usepackage{ctex}"), fixed.index(r"]{article}"))
        self.assertNotIn("\\documentclass[\n\\usepackage", fixed)

    def test_remove_pdftex_graphics_driver(self):
        text = (
            r"\usepackage[pdftex]{graphicx}" "\n"
            r"\RequirePackage[pdftex,demo]{graphicx}" "\n"
            r"\usepackage[demo]{graphicx}"
        )

        fixed, count = filters.remove_pdftex_graphics_driver(text)

        self.assertEqual(count, 2)
        self.assertIn(r"\usepackage{graphicx}", fixed)
        self.assertIn(r"\RequirePackage[demo]{graphicx}", fixed)
        self.assertEqual(fixed.count(r"\usepackage[demo]{graphicx}"), 1)

    def test_fallback_sourcesans3_family(self):
        fixed, count = filters.fallback_sourcesans3_family(
            r"\newfontfamily\sourcesans{SourceSans3}"
        )

        self.assertEqual(count, 1)
        self.assertIn("{SourceSansPro}", fixed)

    def test_adds_textls_fallback_for_local_style(self):
        fixed, count = filters.add_xelatex_compatibility_fallbacks(
            r"\newcommand{\heading}{\textls[18]{Title}}"
        )

        self.assertEqual(count, 1)
        self.assertIn(r"\providecommand{\textls}[2][]{#2}", fixed)
        self.assertLess(fixed.index(r"\providecommand{\textls}"), fixed.index(r"\textls[18]"))

    def test_demote_structural_commands_in_captions(self):
        text = (
            r"\caption{\section{\bench{} 概述} \textit{\textbf{数据构建}} 正文。}" "\n"
            r"\caption{\section*{无星号标题} 说明。}"
        )

        fixed, count = filters.demote_structural_commands_in_captions(text)

        self.assertEqual(count, 2)
        self.assertIn(r"\caption{\textbf{\bench{} 概述}", fixed)
        self.assertNotIn(r"\caption{\section{", fixed)
        self.assertIn(r"\caption{\textbf{无星号标题}", fixed)

    def test_repair_inline_verb_delimiter_collisions_for_regex(self):
        text = (
            r"使用正则表达式 \verb|r\"(?<=\.| 选择上下文 )[^\.\?\!]*\?$\"|。"
        )

        fixed, count = filters.repair_inline_verb_delimiter_collisions(text)

        self.assertEqual(count, 1)
        self.assertIn(r"\verb@r\"(?<=\.| 选择上下文 )[^\.\?\!]*\?$\"@", fixed)
        self.assertNotIn(r"\verb|r\"(?<=\.|", fixed)

    def test_repair_inline_verb_delimiter_collisions_leaves_normal_verbs(self):
        text = (
            r"正常代码 \verb|foo| 中文说明 \verb|bar\?| 仍应保持。"
        )

        fixed, count = filters.repair_inline_verb_delimiter_collisions(text)

        self.assertEqual(count, 0)
        self.assertEqual(fixed, text)

    def test_add_xelatex_compatibility_fallbacks_for_tcolorbox_listing(self):
        text = (
            r"\documentclass{article}" "\n"
            r"\usepackage[most]{tcolorbox}" "\n"
            r"\newtcblisting{promptbox}{listing only}" "\n"
            r"\begin{document}" "\n"
            r"\begin{promptbox}x\end{promptbox}" "\n"
            r"\end{document}"
        )

        fixed, count = filters.add_xelatex_compatibility_fallbacks(text)

        self.assertEqual(count, 1)
        self.assertIn(r"\providecommand{\inputencodingname}{utf8}", fixed)
        self.assertLess(fixed.index(r"\providecommand{\inputencodingname}"), fixed.index(r"\begin{document}"))

    def test_add_xelatex_compatibility_fallbacks_for_legacy_cjk_environments(self):
        text = (
            r"\documentclass{article}" "\n"
            r"\begin{document}" "\n"
            r"\begin{CJK*}{UTF8}{gbsn}中文\end{CJK*}" "\n"
            r"\end{document}"
        )

        fixed, count = filters.add_xelatex_compatibility_fallbacks(text)

        self.assertEqual(count, 1)
        self.assertIn(r"\csname CJK*\endcsname", fixed)
        self.assertIn(r"\csname endCJK*\endcsname", fixed)
        self.assertLess(fixed.index(r"% paper-trans fallback for legacy CJK"), fixed.index(r"\begin{document}"))

    def test_legacy_cjk_fallback_is_added_even_when_cjkutf8_is_declared(self):
        text = (
            r"\documentclass{article}" "\n"
            r"\usepackage{CJKutf8}" "\n"
            r"\begin{document}" "\n"
            r"\begin{CJK}{UTF8}{gbsn}中文\end{CJK}" "\n"
            r"\end{document}" "\n"
        )

        fixed, count = filters.add_xelatex_compatibility_fallbacks(text)

        self.assertGreater(count, 0)
        self.assertIn(r"\csname endCJK\endcsname", fixed)

    def test_repair_missing_identity_matrix_alias(self):
        text = (
            r"\newcommand{\Imat}{\bm{I}}" "\n"
            r"$\Omat^\top\Omat=\I_n$"
        )

        fixed, count = filters.repair_missing_math_aliases(text)

        self.assertEqual(count, 1)
        self.assertIn(r"=\Imat_n", fixed)

    def test_cjk_fallback_replaces_legacy_providecommand_variant(self):
        text = (
            r"\documentclass{article}" "\n"
            r"% paper-trans fallback for legacy CJK environments under XeLaTeX" "\n"
            r"\expandafter\providecommand\csname CJK\endcsname[2]{}" "\n"
            r"\expandafter\providecommand\csname endCJK\endcsname{}" "\n"
            r"\expandafter\providecommand\csname CJK*\endcsname[2]{}" "\n"
            r"\expandafter\providecommand\csname endCJK*\endcsname{}" "\n"
            r"\providecommand{\CJKfamily}[1]{}" "\n"
            r"\begin{document}\begin{CJK}{UTF8}{gbsn}中文\end{CJK}\end{document}"
        )

        fixed, _ = filters.add_xelatex_compatibility_fallbacks(text)

        self.assertNotIn(r"\expandafter\providecommand\csname endCJK\endcsname", fixed)
        self.assertIn(r"\ifcsname endCJK\endcsname", fixed)

    def test_add_xelatex_compatibility_fallbacks_for_cidr_fontspec_commands(self):
        text = (
            r"\documentclass[sigplan]{cidr-2025}" "\n"
            r"\begin{document}" "\n"
            r"\setmonofont[StylisticSet=3]{inconsolata}" "\n"
            r"正文" "\n"
            r"\end{document}"
        )

        fixed, count = filters.add_xelatex_compatibility_fallbacks(text)

        self.assertEqual(count, 1)
        self.assertIn(r"\providecommand{\setmonofont}[2][]{}", fixed)
        self.assertIn(r"\providecommand{\newfontfamily}[3][]{\providecommand#2{}}", fixed)
        self.assertLess(fixed.index(r"\providecommand{\setmonofont}"), fixed.index(r"\documentclass"))
        self.assertLess(fixed.index(r"\providecommand{\setmonofont}"), fixed.index(r"\setmonofont"))

    def test_add_xelatex_compatibility_fallbacks_respects_fontspec_package(self):
        text = (
            r"\documentclass{article}" "\n"
            r"\usepackage{fontspec}" "\n"
            r"\begin{document}" "\n"
            r"\setmonofont{Inconsolata}" "\n"
            r"\end{document}"
        )

        fixed, count = filters.add_xelatex_compatibility_fallbacks(text)

        self.assertEqual(count, 0)
        self.assertEqual(fixed, text)

    def test_add_xelatex_compatibility_fallbacks_for_missing_xspace(self):
        text = (
            r"\documentclass{article}" "\n"
            r"\newcommand{\model}{Audex\xspace}" "\n"
            r"\begin{document}" "\n"
            r"\model 文本" "\n"
            r"\end{document}"
        )

        fixed, count = filters.add_xelatex_compatibility_fallbacks(text)

        self.assertEqual(count, 1)
        self.assertIn(r"\providecommand{\xspace}{}", fixed)
        self.assertLess(fixed.index(r"\providecommand{\xspace}"), fixed.index(r"\newcommand{\model}"))

    def test_add_xelatex_compatibility_fallbacks_for_abscontent(self):
        text = (
            r"\documentclass{nvidiatechreport}" "\n"
            r"\begin{document}" "\n"
            r"\maketitle" "\n"
            r"\abscontent" "\n"
            r"\end{document}"
        )

        fixed, count = filters.add_xelatex_compatibility_fallbacks(text)

        self.assertEqual(count, 1)
        self.assertIn(r"\providecommand{\abscontent}", fixed)
        self.assertLess(fixed.index(r"\providecommand{\abscontent}"), fixed.index(r"\begin{document}"))

    def test_add_xelatex_compatibility_fallbacks_for_missing_href(self):
        text = (
            r"\documentclass{article}" "\n"
            r"\begin{document}" "\n"
            r"\href{https://example.com}{Example}" "\n"
            r"\end{document}"
        )

        fixed, count = filters.add_xelatex_compatibility_fallbacks(text)

        self.assertEqual(count, 1)
        self.assertIn(r"\providecommand{\href}[2]{#2}", fixed)
        self.assertLess(fixed.index(r"\providecommand{\href}"), fixed.index(r"\begin{document}"))

    def test_preamble_fallback_stays_outside_multiline_optional_argument(self):
        text = (
            r"\documentclass{article}" "\n"
            r"\newcommand{\checkdata}[2][]{#2}" "\n"
            r"\checkdata[" "\n"
            r"\raisebox{-0.2em}{icon}~~Project Page]"
            r"{\href{https://example.com}{Example}}" "\n"
            r"\begin{document}" "\n"
            r"\end{document}"
        )

        fixed, count = filters.add_xelatex_compatibility_fallbacks(text)

        self.assertEqual(count, 1)
        fallback = fixed.index(r"\providecommand{\href}[2]{#2}")
        optional_argument = fixed.index(r"\checkdata[")
        self.assertLess(fallback, optional_argument)

    def test_relocates_existing_fallback_from_multiline_optional_argument(self):
        text = (
            r"\documentclass{article}" "\n"
            r"\newcommand{\checkdata}[2][]{#2}" "\n"
            r"\checkdata[" "\n"
            r"% paper-trans fallback for missing hyperref package" "\n"
            r"\providecommand{\href}[2]{#2}" "\n"
            r"\raisebox{-0.2em}{icon}~~Project Page]"
            r"{\href{https://example.com}{Example}}" "\n"
            r"\begin{document}" "\n"
            r"\end{document}"
        )

        fixed, count = filters.add_xelatex_compatibility_fallbacks(text)

        self.assertEqual(count, 1)
        self.assertEqual(
            fixed.count("% paper-trans fallback for missing hyperref package"),
            1,
        )
        self.assertLess(
            fixed.index(r"\providecommand{\href}[2]{#2}"),
            fixed.index(r"\checkdata["),
        )

    def test_add_xelatex_compatibility_fallbacks_for_common_missing_commands(self):
        text = "\\documentclass{article}\n\\begin{document}\n\\citep{x} $\\mathbb{R}$\n\\begin{appendices}A\\end{appendices}\n\\begin{tabular}{cc}\\toprule\\multirow{2}{*}{A}&B\\\\\\cmidrule(lr){1-2}\\bottomrule\\end{tabular}\n\\end{document}"

        fixed, count = filters.add_xelatex_compatibility_fallbacks(text)

        self.assertEqual(count, 6)
        self.assertIn(r"\providecommand{\citep}[2][]{\cite{#2}}", fixed)
        self.assertIn(r"\providecommand{\mathbb}[1]{\mathbf{#1}}", fixed)
        self.assertIn(r"\newenvironment{appendices}{\appendix}{}", fixed)
        self.assertIn(r"\providecommand{\toprule}{\hline}", fixed)
        self.assertIn(r"\providecommand{\multirow}[4][]{#4}", fixed)
        self.assertNotIn(r"\cmidrule", fixed)

    def test_add_xelatex_compatibility_fallbacks_for_bbding_symbols(self):
        text = (
            "\\documentclass{article}\n"
            "\\usepackage{bbding}\n"
            "\\newcommand{\\cmark}{\\CheckmarkBold}\n"
            "\\newcommand{\\xmark}{\\XSolidBrush}\n"
            "\\begin{document}\\cmark\\xmark\\end{document}"
        )

        fixed, count = filters.add_xelatex_compatibility_fallbacks(text)
        fixed_again, second_count = (
            filters.add_xelatex_compatibility_fallbacks(fixed)
        )

        self.assertEqual(count, 1)
        self.assertIn(r"\providecommand{\CheckmarkBold}", fixed)
        self.assertIn(r"\providecommand{\XSolidBrush}", fixed)
        self.assertEqual(fixed_again, fixed)
        self.assertEqual(second_count, 0)

    def test_preamble_fallback_ignores_tokens_inside_comments(self):
        source = (
            "\\NeedsTeXFormat{LaTeX2e}\n"
            "% Outer hook fires during \\\\begin{document} processing.\n"
            "% Example only: \\\\citep{ignored}\n"
            "\\AtBeginDocument{\\\\citep{real}}\n"
        )

        fixed, count = filters.add_xelatex_compatibility_fallbacks(source)

        marker = r"% paper-trans fallback for missing natbib citation commands"
        self.assertGreaterEqual(count, 1)
        self.assertIn(marker, fixed)
        self.assertLess(fixed.index(marker), fixed.index(r"\AtBeginDocument"))
        self.assertIn(
            "% Outer hook fires during \\\\begin{document} processing.\n",
            fixed,
        )

    def test_repairs_historical_fallback_inserted_inside_comment(self):
        broken = (
            "% Outer hook fires during "
            "% paper-trans fallback for missing natbib citation commands\n"
            "\\providecommand{\\citep}[2][]{\\cite{#2}}\n"
            "\\providecommand{\\citet}[2][]{\\cite{#2}}\n"
            "\\begin{document} processing;\n"
            "\\AtBeginDocument{\\citep{real}}\n"
        )

        fixed, count = filters.add_xelatex_compatibility_fallbacks(broken)

        self.assertGreaterEqual(count, 1)
        self.assertIn(
            r"% Outer hook fires during \begin{document} processing;",
            fixed,
        )
        self.assertNotIn(
            r"% Outer hook fires during % paper-trans fallback",
            fixed,
        )

    def test_reset_acm_baselinestretch_before_end_document(self):
        text = (
            r"\documentclass{acmart}" "\n"
            r"\begin{document}" "\n"
            r"正文" "\n"
            r"\end{document}"
        )

        fixed, count = filters.reset_acm_baselinestretch_before_end_document(text)

        self.assertEqual(count, 1)
        self.assertIn("paper-trans reset ACM baselinestretch guard", fixed)
        self.assertLess(fixed.index("paper-trans reset ACM"), fixed.index(r"\end{document}"))


if __name__ == "__main__":
    unittest.main()
