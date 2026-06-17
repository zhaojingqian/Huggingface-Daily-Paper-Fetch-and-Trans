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

        self.assertEqual(count, 4)
        self.assertIn(r"\methodshort{}并非", fixed)
        self.assertIn(r"\methodshort{}，", fixed)
        self.assertIn(r"\yespart{}标记", fixed)
        self.assertIn(r"\witharg中文", fixed)

    def test_guard_pdftex_primitive_lines(self):
        text = (
            r"\pdfoutput=1" "\n"
            r"  \pdfmapline{+font < font.ttf < enc.enc}" "\n"
            r"\ifdefined\pdfinfo\pdfinfo{/Title(Test)}\fi" "\n"
            r"\section{正文}"
        )

        fixed, count = filters.guard_pdftex_primitive_lines(text)

        self.assertEqual(count, 2)
        self.assertIn(r"\ifdefined\pdfoutput\pdfoutput=1\fi", fixed)
        self.assertIn(r"  \ifdefined\pdfmapline\pdfmapline{+font < font.ttf < enc.enc}\fi", fixed)
        self.assertEqual(fixed.count(r"\ifdefined\pdfinfo"), 1)


if __name__ == "__main__":
    unittest.main()
