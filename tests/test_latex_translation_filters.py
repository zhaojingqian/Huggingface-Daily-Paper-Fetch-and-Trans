import os
import unittest
from unittest import mock

import latex_translation_filters as filters


class LatexTranslationFiltersTest(unittest.TestCase):
    def test_hard_env_policy_recognizes_code_trace_names(self):
        self.assertTrue(filters.is_hard_protected_env("climode"))
        self.assertTrue(filters.is_hard_protected_env("trajactGUI"))
        self.assertTrue(filters.is_hard_protected_env("custom_prompt"))
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
            "请提供您需要翻译的英文学术论文部分内容。\n"
            "后续中文段落。"
        )

        stripped, count = filters.strip_llm_translation_artifacts(text)

        self.assertEqual(count, 2)
        self.assertIn("正常中文段落", stripped)
        self.assertIn("后续中文段落", stripped)
        self.assertNotIn("Please provide", stripped)
        self.assertNotIn("请提供", stripped)


if __name__ == "__main__":
    unittest.main()
