import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import run_papers


class RunPapersRetryTest(unittest.TestCase):
    def test_ok_status_missing_pdf_is_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            translate_full = Mock(return_value={"pdf_path": "/tmp/2606.00007_zh.pdf"})
            fake_translate_mod = types.SimpleNamespace(
                CONTAINER_NAME="latex",
                TEX_BACKUP_DIR=tmp,
                TEX_FAILED_BACKUP_DIR=tmp,
                _restore_tex_to_container=Mock(return_value=False),
                translate_full=translate_full,
            )
            docker_test = Mock()
            docker_test.return_value.returncode = 1

            papers = [{"arxiv_id": "2606.00007", "pdf_status": "ok"}]
            with patch.dict(sys.modules, {"translate_full": fake_translate_mod}), \
                 patch("run_papers._pdf_store_hit", return_value=None), \
                 patch("run_papers._paper_store_update_pdf_status") as update_status, \
                 patch("run_papers.subprocess.run", docker_test):
                result = run_papers.retry_failed_pdf_entries(papers, label="[test]")

        self.assertEqual(result, {"ok": 1, "failed": 0, "changed": True})
        self.assertEqual(papers[0]["pdf_status"], "ok")
        self.assertEqual(
            [call[0] for call in update_status.call_args_list],
            [("2606.00007", "failed"), ("2606.00007", "ok")],
        )
        translate_full.assert_called_once()


if __name__ == "__main__":
    unittest.main()
