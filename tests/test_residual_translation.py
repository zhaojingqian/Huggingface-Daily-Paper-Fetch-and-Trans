import unittest

from paperhub.residual_translation import (
    candidate_line_numbers,
    normalize_residual_response,
    residual_score,
    terminal_repair_eligible,
)


class ResidualTranslationTests(unittest.TestCase):
    def test_selects_unique_mixed_and_long_lines(self):
        report = {
            "samples": [(7, "long"), {"line": 8}],
            "mixed_english_clause_samples": [
                {"line": 8, "text": "same"},
                {"line": 12, "text": "mixed"},
            ],
        }
        self.assertEqual(candidate_line_numbers(report), [7, 8, 12])

    def test_terminal_repair_requires_small_high_coverage_residual(self):
        report = {
            "ok": False,
            "cjk_pct_exact": 93.5,
            "long_english_lines": 0,
            "mixed_english_clause_count": 3,
            "mixed_english_clause_words": 22,
            "mixed_english_clause_samples": [
                {"line": 166},
                {"line": 241},
                {"line": 628},
            ],
        }
        self.assertTrue(terminal_repair_eligible(report))
        self.assertEqual(residual_score(report), (0, 0, 3, 22))
        self.assertFalse(terminal_repair_eligible({**report, "cjk_pct_exact": 40}))
        self.assertFalse(terminal_repair_eligible({**report, "ok": True}))

    def test_normalize_removes_only_exact_outer_fence(self):
        self.assertEqual(
            normalize_residual_response("```latex\n\\item 中文\n```"),
            r"\item 中文",
        )
        self.assertEqual(
            normalize_residual_response("说明\n```latex\n正文\n```"),
            "说明\n```latex\n正文\n```",
        )


if __name__ == "__main__":
    unittest.main()
