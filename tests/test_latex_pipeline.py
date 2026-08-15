import tempfile
import unittest
from pathlib import Path
from unittest import mock

from paperhub import latex_pipeline


class LatexPipelineMacroTest(unittest.TestCase):
    def test_quality_gate_reuses_precomputed_report(self):
        report = {
            "ok": True,
            "cjk_pct": 92.0,
            "long_english_lines": 0,
            "prose_lines": 4,
            "samples": [],
        }
        with mock.patch.object(
            latex_pipeline,
            "translation_quality_report",
            side_effect=AssertionError("report should be reused"),
        ):
            self.assertTrue(
                latex_pipeline.translation_quality_ok(
                    "/unused",
                    "2606.00001",
                    report=report,
                )
            )

    def test_macro_restore_does_not_copy_package_environment_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            translated = root / "merge_translate_zh.tex"
            original = root / "merge.tex"
            package = root / "algorithmic.sty"

            translated.write_text(
                "\\documentclass{article}\n"
                "\\usepackage{algorithmic}\n"
                "\\begin{document}\n"
                "\\STATE $x$\n"
                "\\ours{}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            original.write_text(
                "\\newcommand{\\ours}{text}\n",
                encoding="utf-8",
            )
            package.write_text(
                "\\newenvironment{algorithmic}{"
                "\\newcommand{\\STATE}{\\ALC@it}}{}\n",
                encoding="utf-8",
            )

            restored = latex_pipeline.patch_missing_custom_macro_definitions(
                str(translated), str(original)
            )
            text = translated.read_text(encoding="utf-8")

            self.assertEqual(restored, 1)
            self.assertIn(r"\providecommand{\ours}{text}", text)
            self.assertNotIn(r"\providecommand{\STATE}", text)


if __name__ == "__main__":
    unittest.main()
