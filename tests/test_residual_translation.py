import unittest
import os
from pathlib import Path
from unittest.mock import Mock, patch

from paperhub.residual_translation import (
    candidate_line_numbers,
    normalize_residual_response,
    residual_score,
    terminal_repair_eligible,
)


class ResidualTranslationTests(unittest.TestCase):
    def test_restored_translation_failure_never_reenters_plugin(self):
        driver = (Path(__file__).resolve().parents[1] / "full_translate_driver.py").read_text()
        body = driver.split("# ── 主逻辑：", 1)[1].split("\n", 1)[1]
        body = body.split("# ── 输出结果", 1)[0]
        for original_exists in (False, True):
            source_cache = Mock()
            source_cache.restore_workfolder.return_value = True
            namespace = dict(os=os, arxiv_id="test", ARXIV_CACHE_DIR="cache",
                             keep_translation=True, no_cache=False, source_cache=source_cache,
                             repair_terminal_translation_residuals=Mock(),
                             patch_and_recompile=Mock(return_value=None), run_translation=Mock())
            with patch("os.path.exists", side_effect=lambda path: original_exists or path.endswith("merge_translate_zh.tex")):
                exec(compile(body, "driver-main", "exec"), namespace)
            namespace["run_translation"].assert_not_called()
            namespace["repair_terminal_translation_residuals"].assert_called_once()
            namespace["patch_and_recompile"].assert_called_once()

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

    def test_many_clauses_on_few_lines_remain_targeted(self):
        report = {
            "ok": False,
            "cjk_pct": 81.0,
            "long_english_lines": 3,
            "mixed_english_clause_count": 17,
            "mixed_english_clause_lines": 5,
            "samples": [{"line": line} for line in (401, 403, 405)],
        }

        self.assertTrue(terminal_repair_eligible(report))

    def test_normalize_removes_only_exact_outer_fence(self):
        self.assertEqual(
            normalize_residual_response("```latex\n\\item 中文\n```"),
            r"\item 中文",
        )
        self.assertEqual(
            normalize_residual_response("说明\n```latex\n正文\n```"),
            "说明\n```latex\n正文\n```",
        )

    def test_large_residual_queue_processes_one_bounded_batch(self):
        report = {"ok": False, "cjk_pct": 80,
                  "mixed_english_clause_lines": 13,
                  "mixed_english_clause_samples": [{"line": n} for n in range(1, 14)]}
        self.assertTrue(terminal_repair_eligible(report))
        self.assertEqual(candidate_line_numbers(report), list(range(1, 13)))


if __name__ == "__main__":
    unittest.main()
