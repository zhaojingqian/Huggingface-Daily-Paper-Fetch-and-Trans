import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import run_papers


class QuotaRetryTest(unittest.TestCase):
    def test_manual_review_failure_skips_automatic_full_translation(self):
        aid = "2608.90003"
        translate_full = Mock()
        fake_translate_mod = types.SimpleNamespace(
            CONTAINER_NAME="latex",
            TEX_BACKUP_DIR="/tmp",
            TEX_FAILED_BACKUP_DIR="/tmp",
            _restore_tex_to_container=Mock(return_value=False),
            translate_full=translate_full,
        )
        diagnosis = {
            "category": "translate.api_quota",
            "retry_strategy": "manual_review",
            "retryable": False,
        }

        with tempfile.TemporaryDirectory():
            papers = [{"arxiv_id": aid, "pdf_status": "failed"}]
            with patch.dict(sys.modules, {"translate_full": fake_translate_mod}), \
                 patch("run_papers._pdf_store_hit", return_value=None), \
                 patch("run_papers._pdf_quality_tainted", return_value=False), \
                 patch("run_papers.read_json", return_value=diagnosis), \
                 patch("run_papers._paper_store_update_pdf_status"):
                result = run_papers.retry_failed_pdf_entries(
                    papers,
                    label="[test]",
                )

        self.assertEqual(result["pdf_attempted"], 0)
        self.assertEqual(result["pdf_failed"], 0)
        self.assertEqual(result["residual_ids"], [aid])
        translate_full.assert_not_called()


if __name__ == "__main__":
    unittest.main()
