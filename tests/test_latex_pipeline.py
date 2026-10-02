import tempfile
import unittest
from pathlib import Path
from unittest import mock

from paperhub import latex_pipeline


class LatexPipelineMacroTest(unittest.TestCase):
    def test_incompatible_bundled_colortbl_uses_distribution_pair(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as system:
            local = Path(tmp) / 'colortbl.sty'
            local.write_text(r'\insert@pcolumn')
            array = Path(system) / 'array.sty'
            color = Path(system) / 'colortbl.sty'
            array.write_text(r'\insert@column')
            color.write_text(r'\insert@column')
            result = mock.Mock(returncode=0, stdout=f'{array}\n{color}\n')
            with mock.patch('subprocess.run', return_value=result):
                self.assertEqual(latex_pipeline.patch_local_xelatex_compatibility_fallbacks(tmp), 1)
                self.assertFalse(local.exists())
                self.assertEqual(Path(str(local) + '.incompatible').read_text(), r'\insert@pcolumn')
                self.assertEqual(latex_pipeline.patch_local_xelatex_compatibility_fallbacks(tmp), 0)
                local.write_text(r'\insert@pcolumn')
                (Path(tmp) / 'array.sty').write_text(r'\def\insert@pcolumn{}')
                self.assertEqual(latex_pipeline.patch_local_xelatex_compatibility_fallbacks(tmp), 0)
                self.assertTrue(local.exists())

    def test_aux_cleanup_preserves_two_and_five_field_label_schemas(self):
        labels = (r"\newlabel{plain}{{1}{2}}" "\n"
                  r"\newlabel{eq:test}{{3}{4}{\textbf{标题}}{equation.3}{}}" "\n")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "merge_translate_zh.aux"
            path.write_text(labels + r"\@writefile{toc}{fragile}" + "\n"
                            + r"\newlabel{broken}{{1}{2}" + "\n")
            self.assertEqual(latex_pipeline.sanitize_latex_aux_file(tmp), 2)
            self.assertEqual(path.read_text(), labels)
            self.assertEqual(latex_pipeline.sanitize_latex_aux_file(tmp), 0)

    def test_optional_list_fallback_keeps_one_standard_list_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "paper.tex"
            path.write_text(
                "\\documentclass{article}\n\\usepackage{paralist}\n"
                "\\begin{document}\n\\begin{itemize}[leftmargin=*]\n"
                "\\item 正文\n\\end{itemize}\n\\end{document}\n",
                encoding="utf-8",
            )
            latex_pipeline.patch_enumitem_for_optional_lists(str(path))
            fixed = path.read_text(encoding="utf-8")
            self.assertIn(r"\usepackage[olditem,oldenum]{paralist}", fixed)
            self.assertEqual(fixed.count(r"\usepackage{enumitem}"), 1)
            latex_pipeline.patch_enumitem_for_optional_lists(str(path))
            self.assertEqual(path.read_text(encoding="utf-8"), fixed)

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
