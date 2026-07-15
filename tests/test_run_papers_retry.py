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

    def test_compile_diagnosis_does_not_waste_a_second_gpt_translation(self):
        with tempfile.TemporaryDirectory() as tmp:
            aid = "2606.00008"
            with open(f"{tmp}/{aid}_merge_translate_zh.tex", "w", encoding="utf-8") as handle:
                handle.write("中文翻译缓存")
            translate_full = Mock(return_value={"pdf_path": None, "error": "compile failed"})
            fake_translate_mod = types.SimpleNamespace(
                CONTAINER_NAME="latex",
                TEX_BACKUP_DIR=tmp,
                TEX_FAILED_BACKUP_DIR=tmp,
                _restore_tex_to_container=Mock(return_value=True),
                translate_full=translate_full,
            )
            docker_test = Mock()
            docker_test.return_value.returncode = 1
            papers = [{"arxiv_id": aid, "pdf_status": "failed"}]

            with patch.dict(sys.modules, {"translate_full": fake_translate_mod}), \
                 patch("run_papers._pdf_store_hit", return_value=None), \
                 patch("run_papers.read_json", return_value={
                     "category": "compile.undefined_command",
                     "retry_strategy": "reuse_translation",
                 }), \
                 patch("run_papers.subprocess.run", docker_test):
                result = run_papers.retry_failed_pdf_entries(papers, label="[test]")

        self.assertEqual(result, {"ok": 0, "failed": 1, "changed": False})
        translate_full.assert_called_once_with(
            arxiv_id=aid,
            output_dir=run_papers.PAPER_STORE_DIR,
            no_cache=False,
            keep_translation=True,
            timeout=3600,
        )

    def test_unknown_cache_failure_is_preserved_without_retranslation(self):
        with tempfile.TemporaryDirectory() as tmp:
            aid = "2606.00009"
            with open(f"{tmp}/{aid}_merge_translate_zh.tex", "w", encoding="utf-8") as handle:
                handle.write("中文翻译缓存")
            translate_full = Mock(return_value={"pdf_path": None, "error": "driver exited"})
            fake_translate_mod = types.SimpleNamespace(
                CONTAINER_NAME="latex",
                TEX_BACKUP_DIR=tmp,
                TEX_FAILED_BACKUP_DIR=tmp,
                _restore_tex_to_container=Mock(return_value=True),
                translate_full=translate_full,
            )
            docker_test = Mock()
            docker_test.return_value.returncode = 1
            papers = [{"arxiv_id": aid, "pdf_status": "failed"}]

            with patch.dict(sys.modules, {"translate_full": fake_translate_mod}), \
                 patch("run_papers._pdf_store_hit", return_value=None), \
                 patch("run_papers.read_json", return_value={}), \
                 patch("run_papers.subprocess.run", docker_test):
                result = run_papers.retry_failed_pdf_entries(papers, label="[test]")

        self.assertEqual(result, {"ok": 0, "failed": 1, "changed": False})
        translate_full.assert_called_once()


if __name__ == "__main__":
    unittest.main()
