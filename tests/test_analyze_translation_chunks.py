import tempfile
import unittest
from pathlib import Path

from scripts.analyze_translation_chunks import analyze, analyze_tex


class AnalyzeTranslationChunksTest(unittest.TestCase):
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
            "This ordinary prose line remains untranslated and should be reported here.\n"
            "\\end{document}\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            tex_path = Path(tmp) / "paper.tex"
            tex_path.write_text(tex, encoding="utf-8")

            report = analyze_tex(tex_path)

        self.assertEqual(report["long_english_lines"], 1)
        self.assertEqual(report["samples"][0]["env"], "body")


if __name__ == "__main__":
    unittest.main()
