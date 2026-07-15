import os
import unittest
from unittest import mock

import latex_translation_filters as filters


class LatexTranslationFiltersTest(unittest.TestCase):
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
            r"\ifdefined\pdfinfo\pdfinfo{/Title(Test)}\fi" "\n"
            r"\section{正文}"
        )

        fixed, count = filters.guard_pdftex_primitive_lines(text)

        self.assertEqual(count, 4)
        self.assertIn(r"\ifdefined\pdfoutput\pdfoutput=1\fi", fixed)
        self.assertIn(r"\ifdefined\pdfgentounicode\pdfgentounicode =1\fi", fixed)
        self.assertIn(r"\ifdefined\pdfinfoomitdate\pdfinfoomitdate=1\fi", fixed)
        self.assertIn(r"  \ifdefined\pdfmapline\pdfmapline{+font < font.ttf < enc.enc}\fi", fixed)
        self.assertEqual(fixed.count(r"\ifdefined\pdfinfo\pdfinfo"), 1)

    def test_replace_bare_citation_commands_glued_to_cjk(self):
        text = r"测试定义如\cite中所述；正常引用见\cite{smith2026}。"

        fixed, count = filters.replace_bare_citation_commands(text)

        self.assertEqual(count, 1)
        self.assertIn("如文献中所述", fixed)
        self.assertIn(r"\cite{smith2026}", fixed)

    def test_separate_declaration_command_cjk_glue(self):
        text = r"这是{\em去中心化}策略，且 \textit{正常命令} 不变。"

        fixed, count = filters.separate_declaration_command_cjk_glue(text)

        self.assertEqual(count, 1)
        self.assertIn(r"{\em 去中心化}", fixed)
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

    def test_normalize_tex_include_target_strips_harmless_whitespace(self):
        self.assertEqual(filters.normalize_tex_include_target(" 6_conclusion \n"), "6_conclusion")

    def test_fontawesome_command_names_excludes_argument_based_fa_icon(self):
        text = r"\faRobot \faCheckCircle \faIcon{github} \faRobot"

        self.assertEqual(
            filters.fontawesome_command_names(text),
            ("faCheckCircle", "faRobot"),
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

    def test_relocate_packages_from_documentclass_options(self):
        text = "\\documentclass[\n\\usepackage{ctex}\n  11pt,\n]{article}\n正文"

        fixed, count = filters.relocate_packages_from_documentclass_options(text)

        self.assertEqual(count, 1)
        self.assertIn("11pt,", fixed)
        self.assertGreater(fixed.index(r"\usepackage{ctex}"), fixed.index(r"]{article}"))
        self.assertNotIn("\\documentclass[\n\\usepackage", fixed)

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
