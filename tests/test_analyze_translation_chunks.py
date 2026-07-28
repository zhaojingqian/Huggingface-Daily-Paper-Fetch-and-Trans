import tempfile
import unittest
from pathlib import Path

from paperhub.translation_quality import is_untranslated_prose
from scripts.analyze_translation_chunks import analyze, analyze_tex


class AnalyzeTranslationChunksTest(unittest.TestCase):
    def test_repository_quality_threshold_is_strict(self):
        self.assertTrue(is_untranslated_prose({
            "cjk_pct": 90.0,
            "long_english_lines": 20,
            "very_long_english_lines": 0,
            "prose_lines": 100,
        }))
        self.assertTrue(is_untranslated_prose({
            "cjk_pct": 69.9,
            "long_english_lines": 10,
            "very_long_english_lines": 0,
            "prose_lines": 100,
        }))
        self.assertFalse(is_untranslated_prose({
            "cjk_pct": 70.0,
            "long_english_lines": 10,
            "very_long_english_lines": 0,
            "prose_lines": 100,
        }))

    def test_reports_unchanged_transform_chunks(self):
        debug = (
            '<p style="color:red;">\\begin{document}<br/></p>'
            '<p style="color:black;">#[1, 3]'
            'This sentence should have been translated into Chinese.#</p>'
        )
        translated = (
            "\\begin{document}\n"
            "This sentence should have been translated into Chinese.\n"
            "\\end{document}\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            debug_path = Path(tmp) / "debug.html"
            tex_path = Path(tmp) / "translated.tex"
            debug_path.write_text(debug, encoding="utf-8")
            tex_path.write_text(translated, encoding="utf-8")

            report = analyze(debug_path, tex_path)

        self.assertEqual(report["nodes"]["transform"], 1)
        self.assertEqual(report["unchanged_transform_chunks"], 1)

    def test_tex_scan_ignores_protected_code_environment(self):
        tex = (
            "\\begin{document}\n"
            "这是已经翻译的正文段落，包含足够多的中文内容用于统计。\n"
            "\\begin{casecode}\n"
            "This is intentionally preserved benchmark source code with many words.\n"
            "\\end{casecode}\n"
            "This ordinary prose line remains untranslated and contains enough "
            "natural language words to be reported by the shared publication "
            "quality gate here.\n"
            "\\end{document}\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            tex_path = Path(tmp) / "paper.tex"
            tex_path.write_text(tex, encoding="utf-8")

            report = analyze_tex(tex_path)

        self.assertEqual(report["long_english_lines"], 1)
        self.assertEqual(report["samples"][0]["env"], "body")

    def test_tex_scan_ignores_inline_code_and_urls(self):
        tex = (
            "\\begin{document}\n"
            "\\texttt{This is intentionally preserved source code with many "
            "English tokens and should not count as untranslated prose.}\n"
            "\\url{https://example.com/this/path/contains/many/english/words/"
            "that/are/not/prose}\n"
            "This ordinary prose line remains untranslated and contains enough "
            "English words for reliable quality detection.\n"
            "\\end{document}\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            tex_path = Path(tmp) / "paper.tex"
            tex_path.write_text(tex, encoding="utf-8")

            report = analyze_tex(tex_path)

        self.assertEqual(report["long_english_lines"], 1)
        self.assertIn("ordinary prose", report["samples"][0]["text"])

    def test_tex_scan_detects_partial_mixed_prose_at_high_cjk_ratio(self):
        tex = (
            "\\begin{document}\n"
            "本文提出一个统一框架，用于科学图表生成和可编辑矢量图转换，并在多个任务设置中进行严格评估。"
            "实验结果说明该方法具有稳定性、可扩展性和良好的跨领域泛化能力，同时在质量、效率、可靠性和可解释性方面保持一致表现，并为实际研究人员提供可靠支持。\n"
            "系统显著优于独立生成器和 the agentic baseline on 所有基准测试。\n"
            "这些机制集中在 prompts of $\\mathcal{D}$, $\\mathcal{V}$, and $\\mathcal{R}$; and。\n"
            "我们总结了方法，\n"
            "此外我们确保所有角色都正确分配，\\Crafter and \\Editor instantiate each role。\n"
            "\\end{document}\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            tex_path = Path(tmp) / "paper.tex"
            tex_path.write_text(tex, encoding="utf-8")

            report = analyze_tex(tex_path)

        self.assertGreaterEqual(report["cjk_pct"], 70.0)
        self.assertEqual(report["long_english_lines"], 0)
        self.assertGreaterEqual(report["mixed_english_clause_count"], 3)
        self.assertTrue(is_untranslated_prose(report))

    def test_tex_scan_mixed_clause_excludes_table_name_and_literal_example(self):
        tex = (
            "\\begin{document}\n"
            "我们遵循 Values in the Wild 和 LongBench v2 的评估设置。\n"
            "系统使用 \\textit{first care, then order, then the business of the day} 作为示例。\n"
            "\\begin{tabular}{ll}\n"
            "中文指标 & the agentic baseline on every benchmark \\\\\n"
            "\\end{tabular}\n"
            "\\end{document}\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            tex_path = Path(tmp) / "paper.tex"
            tex_path.write_text(tex, encoding="utf-8")

            report = analyze_tex(tex_path)

        self.assertEqual(report["mixed_english_clause_count"], 0)
        self.assertFalse(is_untranslated_prose(report))

    def test_tex_scan_ignores_unescaped_comments_but_keeps_literal_percent(self):
        english = (
            "This long English source annotation contains enough ordinary "
            "natural language words to look exactly like untranslated paper "
            "prose when comments are not removed correctly."
        )
        tex = (
            "\\begin{document}\n"
            + "\n".join("% " + english for _ in range(24))
            + "\n这是已经翻译的正文，实验成功率为 90\\%，应保留百分号。\n"
            + english
            + "\n\\end{document}\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            tex_path = Path(tmp) / "paper.tex"
            tex_path.write_text(tex, encoding="utf-8")

            report = analyze_tex(tex_path)

        self.assertEqual(report["long_english_lines"], 1)
        self.assertFalse(is_untranslated_prose(report))
        self.assertEqual(report["samples"][0]["line"], 27)

    def test_tex_scan_protects_only_semantic_box_instances(self):
        english = (
            "This ordinary English paragraph contains enough natural language "
            "words to qualify as a long untranslated prose line in the shared "
            "publication quality analysis."
        )
        tex = (
            "\\begin{document}\n"
            "这是已经翻译的正文段落，包含足够多的中文内容用于统计。\n"
            "\\begin{custombox}[title=Natural Questions]\n"
            "\\textbf{Question:} " + english + "\n"
            "\\textbf{Answer:} A benchmark source answer.\n"
            "\\end{custombox}\n"
            "\\begin{examplebox}[title={(1) Published benchmark example}]\n"
            + english + "\n"
            "\\end{examplebox}\n"
            "\\begin{mdframed}[frametitle={Problem-Solving Prompt}]\n"
            + english + "\n"
            "\\end{mdframed}\n"
            "\\begin{tcolorbox}[title=Key Insight]\n"
            + english + "\n"
            "\\end{tcolorbox}\n"
            + english + "\n"
            "\\end{document}\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            tex_path = Path(tmp) / "paper.tex"
            tex_path.write_text(tex, encoding="utf-8")

            report = analyze_tex(tex_path)

        self.assertEqual(report["long_english_lines"], 2)
        self.assertEqual(
            [sample["env"] for sample in report["samples"]],
            ["tcolorbox", "body"],
        )


if __name__ == "__main__":
    unittest.main()
