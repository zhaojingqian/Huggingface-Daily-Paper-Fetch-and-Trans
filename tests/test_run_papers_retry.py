import json
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import run_papers


class RunPapersRetryTest(unittest.TestCase):
    def test_run_lock_is_shared_persistent_and_reacquirable(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            run_papers, "LOCK_DIR", tmp
        ):
            lock_path = os.path.join(tmp, "daily-2026-07-27.lock")
            with run_papers.RunLock("daily", "2026-07-27"):
                self.assertTrue(os.path.exists(lock_path))
                with self.assertRaises(RuntimeError):
                    with run_papers.RunLock("daily", "2026-07-27"):
                        pass
            self.assertTrue(os.path.exists(lock_path))
            self.assertTrue(
                os.path.exists(
                    os.path.join(tmp, "publication-catalog.lock")
                )
            )
            with run_papers.RunLock("daily", "2026-07-27"):
                pass

    def test_persisted_store_failure_promotes_missing_slim_status_to_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            aid = "2607.00005"
            translate_full = Mock(
                return_value={"pdf_path": None, "error": "compile failed"}
            )
            fake_translate_mod = types.SimpleNamespace(
                CONTAINER_NAME="latex",
                TEX_BACKUP_DIR=tmp,
                TEX_FAILED_BACKUP_DIR=tmp,
                _restore_tex_to_container=Mock(return_value=False),
                translate_full=translate_full,
            )
            docker_test = Mock()
            docker_test.return_value.returncode = 1
            papers = [{"arxiv_id": aid}]

            with patch.dict(
                sys.modules, {"translate_full": fake_translate_mod}
            ), patch(
                "run_papers.paper_store.read_raw",
                return_value={"pdf_status": "failed"},
            ), patch(
                "run_papers._pdf_store_hit", return_value=None
            ), patch(
                "run_papers._pdf_quality_tainted", return_value=False
            ), patch(
                "run_papers.read_json", return_value={}
            ), patch(
                "run_papers._paper_store_update_pdf_status"
            ), patch(
                "run_papers.subprocess.run", docker_test
            ):
                result = run_papers.retry_failed_pdf_entries(
                    papers, label="[test]"
                )

        self.assertEqual(result["pdf_attempted"], 1)
        self.assertEqual(result["pdf_failed"], 1)
        self.assertEqual(result["residual_ids"], [aid])
        self.assertEqual(papers[0]["pdf_status"], "failed")
        translate_full.assert_called_once()

    def test_existing_ok_pdf_still_clears_stale_failure_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            aid = "2606.00005"
            translate_full = Mock()
            fake_translate_mod = types.SimpleNamespace(
                CONTAINER_NAME="latex",
                TEX_BACKUP_DIR=tmp,
                TEX_FAILED_BACKUP_DIR=tmp,
                _restore_tex_to_container=Mock(return_value=False),
                translate_full=translate_full,
            )
            papers = [{"arxiv_id": aid, "pdf_status": "ok"}]
            with patch.dict(sys.modules, {"translate_full": fake_translate_mod}), \
                 patch("run_papers._pdf_store_hit",
                       return_value=f"/tmp/{aid}_zh.pdf"), \
                 patch("run_papers._paper_store_update_pdf_status") as update_status, \
                 patch("run_papers._clear_stale_failure_artifacts") as clear_stale:
                result = run_papers.retry_failed_pdf_entries(
                    papers, label="[test]"
                )

        self.assertEqual(result["pdf_attempted"], 0)
        self.assertEqual(result["pdf_succeeded"], 0)
        self.assertEqual(result["residual_failures"], 0)
        update_status.assert_called_once_with(aid, "ok")
        clear_stale.assert_called_once_with(aid)
        translate_full.assert_not_called()

    def test_existing_pdf_sync_clears_stale_failure_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            aid = "2606.00006"
            translate_full = Mock()
            fake_translate_mod = types.SimpleNamespace(
                CONTAINER_NAME="latex",
                TEX_BACKUP_DIR=tmp,
                TEX_FAILED_BACKUP_DIR=tmp,
                _restore_tex_to_container=Mock(return_value=False),
                translate_full=translate_full,
            )
            papers = [{"arxiv_id": aid, "pdf_status": "failed"}]
            with patch.dict(sys.modules, {"translate_full": fake_translate_mod}), \
                 patch("run_papers._pdf_store_hit", return_value=f"/tmp/{aid}_zh.pdf"), \
                 patch("run_papers._paper_store_update_pdf_status") as update_status, \
                 patch("run_papers._clear_stale_failure_artifacts") as clear_stale:
                result = run_papers.retry_failed_pdf_entries(papers, label="[test]")

        self.assertEqual(result["residual_failures"], 0)
        self.assertEqual(papers[0]["pdf_status"], "ok")
        update_status.assert_called_once_with(aid, "ok")
        clear_stale.assert_called_once_with(aid)
        translate_full.assert_not_called()

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
                 patch("run_papers._pdf_store_hit",
                       side_effect=[
                           None,
                           None,
                           "/tmp/2606.00007_zh.pdf",
                           "/tmp/2606.00007_zh.pdf",
                       ]), \
                 patch("run_papers._paper_store_update_pdf_status") as update_status, \
                 patch(
                     "run_papers._paper_store_mark_pdf_verified",
                     return_value=True,
                 ) as mark_verified, \
                 patch("run_papers._clear_stale_failure_artifacts") as clear_stale, \
                 patch("run_papers.subprocess.run", docker_test):
                result = run_papers.retry_failed_pdf_entries(papers, label="[test]")

        self.assertEqual(result, {
            "ok": 1,
            "failed": 0,
            "changed": True,
            "pdf_attempted": 1,
            "pdf_succeeded": 1,
            "pdf_failed": 0,
            "residual_failures": 0,
            "residual_ids": [],
        })
        self.assertEqual(papers[0]["pdf_status"], "ok")
        self.assertEqual(
            [call[0] for call in update_status.call_args_list],
            [("2606.00007", "failed")],
        )
        mark_verified.assert_called_once_with("2606.00007")
        clear_stale.assert_called_once_with("2606.00007")
        translate_full.assert_called_once()

    def test_quality_failure_retranslates_even_when_old_pdf_is_structurally_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            aid = "2606.00010"
            translated_path = f"/tmp/{aid}_zh.pdf"
            translate_full = Mock(return_value={"pdf_path": translated_path})
            fake_translate_mod = types.SimpleNamespace(
                CONTAINER_NAME="latex",
                TEX_BACKUP_DIR=tmp,
                TEX_FAILED_BACKUP_DIR=tmp,
                _restore_tex_to_container=Mock(return_value=False),
                translate_full=translate_full,
            )
            docker_test = Mock()
            docker_test.return_value.returncode = 1
            papers = [{"arxiv_id": aid, "pdf_status": "failed"}]
            diagnosis = {
                "category": "quality.untranslated_prose",
                "retry_strategy": "retry_translation",
            }

            with patch.dict(sys.modules, {"translate_full": fake_translate_mod}), \
                 patch("run_papers._pdf_store_hit",
                       side_effect=[f"/tmp/{aid}_old.pdf",
                                    f"/tmp/{aid}_old.pdf",
                                    translated_path,
                                    translated_path]), \
                 patch("run_papers.read_json", return_value=diagnosis), \
                 patch("run_papers._paper_store_update_pdf_status") as update_status, \
                 patch(
                     "run_papers._paper_store_mark_pdf_verified",
                     return_value=True,
                 ) as mark_verified, \
                 patch("run_papers._clear_stale_failure_artifacts") as clear_stale, \
                 patch("run_papers.subprocess.run", docker_test):
                result = run_papers.retry_failed_pdf_entries(
                    papers,
                    label="[test]",
                )

        self.assertEqual(result["residual_failures"], 0)
        self.assertEqual(papers[0]["pdf_status"], "ok")
        translate_full.assert_called_once_with(
            arxiv_id=aid,
            output_dir=run_papers.PAPER_STORE_DIR,
            no_cache=True,
            keep_translation=False,
            timeout=3600,
        )
        mark_verified.assert_called_once_with(aid)
        clear_stale.assert_called_once_with(aid)

    def test_persistent_quality_taint_survives_later_compile_diagnosis(self):
        with tempfile.TemporaryDirectory() as tmp:
            aid = "2606.00011"
            with open(
                f"{tmp}/{aid}_merge_translate_zh.tex",
                "w",
                encoding="utf-8",
            ) as handle:
                handle.write("新的中文翻译，但编译仍失败")
            translate_full = Mock(
                return_value={"pdf_path": None, "error": "compile failed"}
            )
            fake_translate_mod = types.SimpleNamespace(
                CONTAINER_NAME="latex",
                TEX_BACKUP_DIR=tmp,
                TEX_FAILED_BACKUP_DIR=tmp,
                _restore_tex_to_container=Mock(return_value=True),
                translate_full=translate_full,
            )
            docker_test = Mock()
            docker_test.return_value.returncode = 1
            diagnosis = {
                "category": "compile.undefined_command",
                "retry_strategy": "reuse_translation",
            }
            papers = [{"arxiv_id": aid, "pdf_status": "failed"}]

            with patch.dict(sys.modules, {"translate_full": fake_translate_mod}), \
                 patch(
                     "run_papers.paper_store.pdf_quality_tainted",
                     return_value=True,
                 ), \
                 patch(
                     "run_papers.paper_store.pdf_hit",
                     return_value=f"/tmp/{aid}_old.pdf",
                 ), \
                 patch("run_papers.read_json", return_value=diagnosis), \
                 patch("run_papers._paper_store_update_pdf_status") as update_status, \
                 patch("run_papers._clear_stale_failure_artifacts") as clear_stale, \
                 patch("run_papers.subprocess.run", docker_test):
                result = run_papers.retry_failed_pdf_entries(
                    papers,
                    label="[test]",
                )

        self.assertEqual(result["residual_ids"], [aid])
        self.assertEqual(papers[0]["pdf_status"], "failed")
        self.assertNotIn(
            (aid, "ok"),
            [call.args for call in update_status.call_args_list],
        )
        clear_stale.assert_not_called()
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
                 patch("run_papers._paper_store_update_pdf_status") as update_status, \
                 patch("run_papers.subprocess.run", docker_test):
                result = run_papers.retry_failed_pdf_entries(papers, label="[test]")

        self.assertEqual(result["ok"], 0)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["residual_ids"], [aid])
        update_status.assert_called_with(aid, "failed")
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
                 patch("run_papers._paper_store_update_pdf_status") as update_status, \
                 patch("run_papers.subprocess.run", docker_test):
                result = run_papers.retry_failed_pdf_entries(papers, label="[test]")

        self.assertEqual(result["ok"], 0)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["residual_failures"], 1)
        update_status.assert_called_with(aid, "failed")
        translate_full.assert_called_once()

    def test_repair_does_not_report_returned_but_unpersisted_translation_as_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            key = "2026-07-27"
            mode_path = os.path.join(tmp, "daily")
            key_path = os.path.join(mode_path, key)
            os.makedirs(key_path)
            index_path = os.path.join(key_path, "index.json")
            with open(index_path, "w", encoding="utf-8") as handle:
                json.dump({"papers": [{"arxiv_id": "2607.00001", "rank": 1}]}, handle)

            translated = {
                "title": "English title",
                "abstract": "English abstract",
                "title_zh": "中文标题",
                "summary_zh": "中文摘要",
            }
            with patch("run_papers.mode_dir", return_value=mode_path), \
                 patch("run_papers.mode_index_path", return_value=index_path), \
                 patch("run_papers.paper_store.read_raw", return_value={}), \
                 patch("translate_arxiv.load_api_config", return_value={}), \
                 patch("translate_arxiv.translate_and_save", return_value=translated):
                result = run_papers.repair(
                    mode="daily", key=key, return_stats=True
                )

        self.assertEqual(result["summary_repaired"], 0)
        self.assertEqual(result["summary_failed"], 1)
        self.assertEqual(result["residual_ids"], ["2607.00001"])

    def test_repair_deduplicates_failed_paper_across_indexes(self):
        with tempfile.TemporaryDirectory() as tmp:
            mode_path = os.path.join(tmp, "daily")
            keys = ["2026-07-26", "2026-07-27"]
            index_paths = {}
            for key in keys:
                key_path = os.path.join(mode_path, key)
                os.makedirs(key_path)
                index_paths[key] = os.path.join(key_path, "index.json")
                with open(index_paths[key], "w", encoding="utf-8") as handle:
                    json.dump({
                        "papers": [{"arxiv_id": "2607.00002", "rank": 1}]
                    }, handle)

            with patch("run_papers.mode_dir", return_value=mode_path), \
                 patch(
                     "run_papers.mode_index_path",
                     side_effect=lambda _mode, key: index_paths[key],
                 ), \
                 patch("run_papers.paper_store.read_raw", return_value={}), \
                 patch("translate_arxiv.load_api_config", return_value={}), \
                 patch(
                     "translate_arxiv.translate_and_save",
                     return_value={},
                 ) as translate:
                result = run_papers.repair(
                    mode="daily",
                    keys=keys,
                    return_stats=True,
                )

        translate.assert_called_once()
        self.assertEqual(result["summary_attempted"], 1)
        self.assertEqual(result["summary_failed"], 1)
        self.assertEqual(result["residual_ids"], ["2607.00002"])
        self.assertEqual(result["audited_ids"], ["2607.00002"])

    def test_repair_display_payload_cannot_flip_persisted_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            key = "2026-07-27"
            mode_path = os.path.join(tmp, "daily")
            key_path = os.path.join(mode_path, key)
            os.makedirs(key_path)
            index_path = os.path.join(key_path, "index.json")
            with open(index_path, "w", encoding="utf-8") as handle:
                json.dump({
                    "papers": [{"arxiv_id": "2607.00003", "rank": 1}]
                }, handle)
            persisted = {
                "title": "English",
                "abstract": "English abstract",
                "title_zh": "持久化中文标题",
                "summary_zh": "持久化中文摘要",
            }

            with patch("run_papers.mode_dir", return_value=mode_path), \
                 patch("run_papers.mode_index_path", return_value=index_path), \
                 patch(
                     "run_papers.paper_store.read_raw",
                     side_effect=[{}, persisted, persisted],
                 ), \
                 patch("translate_arxiv.load_api_config", return_value={}), \
                 patch(
                     "translate_arxiv.translate_and_save",
                     return_value={},
                 ):
                result = run_papers.repair(
                    mode="daily",
                    key=key,
                    return_stats=True,
                )

        self.assertEqual(result["summary_repaired"], 1)
        self.assertEqual(result["summary_succeeded"], 1)
        self.assertEqual(result["summary_failed"], 0)
        self.assertEqual(result["residual_ids"], [])

    def test_retry_pdf_deduplicates_same_paper_across_indexes(self):
        with tempfile.TemporaryDirectory() as tmp:
            aid = "2607.00004"
            mode_path = os.path.join(tmp, "daily")
            keys = ["2026-07-26", "2026-07-27"]
            index_paths = {}
            for key in keys:
                key_path = os.path.join(mode_path, key)
                os.makedirs(key_path)
                index_paths[key] = os.path.join(key_path, "index.json")
                with open(index_paths[key], "w", encoding="utf-8") as handle:
                    json.dump({
                        "papers": [{"arxiv_id": aid, "pdf_status": "failed"}]
                    }, handle)

            translate_full = Mock(
                return_value={"pdf_path": None, "error": "compile failed"}
            )
            fake_translate_mod = types.SimpleNamespace(
                CONTAINER_NAME="latex",
                TEX_BACKUP_DIR=tmp,
                TEX_FAILED_BACKUP_DIR=tmp,
                _restore_tex_to_container=Mock(return_value=False),
                translate_full=translate_full,
            )
            docker_test = Mock()
            docker_test.return_value.returncode = 1

            with patch.dict(sys.modules, {"translate_full": fake_translate_mod}), \
                 patch("run_papers.mode_dir", return_value=mode_path), \
                 patch(
                     "run_papers.mode_index_path",
                     side_effect=lambda _mode, key: index_paths[key],
                 ), \
                 patch("run_papers._pdf_store_hit", return_value=None), \
                 patch("run_papers._pdf_quality_tainted", return_value=False), \
                 patch("run_papers.read_json", return_value={}), \
                 patch("run_papers._paper_store_update_pdf_status"), \
                 patch("run_papers.subprocess.run", docker_test):
                result = run_papers.retry_pdf(
                    mode="daily",
                    keys=keys,
                    return_stats=True,
                )

        translate_full.assert_called_once()
        self.assertEqual(result["pdf_attempted"], 1)
        self.assertEqual(result["pdf_failed"], 1)
        self.assertEqual(result["residual_ids"], [aid])
        self.assertEqual(result["audited_ids"], [aid])

    def test_retry_pdf_merges_status_into_fresh_concurrent_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            aid = "2607.00006"
            mode_path = os.path.join(tmp, "daily")
            key = "2026-07-27"
            key_path = os.path.join(mode_path, key)
            os.makedirs(key_path)
            index_path = os.path.join(key_path, "index.json")
            with open(index_path, "w", encoding="utf-8") as handle:
                json.dump({
                    "generated_at": "old",
                    "papers": [{
                        "arxiv_id": aid,
                        "rank": 1,
                        "pdf_status": "failed",
                    }],
                }, handle)

            def concurrent_publish(papers, **_kwargs):
                papers[0]["pdf_status"] = "ok"
                with open(index_path, "w", encoding="utf-8") as handle:
                    json.dump({
                        "generated_at": "new-publisher",
                        "papers": [
                            {
                                "arxiv_id": aid,
                                "rank": 99,
                                "publisher_field": "keep",
                                "pdf_status": "failed",
                            },
                            {
                                "arxiv_id": "2607.99999",
                                "rank": 2,
                                "pdf_status": "none",
                            },
                        ],
                    }, handle)
                return {
                    "ok": 1,
                    "failed": 0,
                    "changed": True,
                    "pdf_attempted": 1,
                    "pdf_succeeded": 1,
                    "pdf_failed": 0,
                    "residual_failures": 0,
                    "residual_ids": [],
                }

            with patch("run_papers.mode_dir", return_value=mode_path), \
                 patch("run_papers.mode_index_path", return_value=index_path), \
                 patch(
                     "run_papers.paper_store.read_raw",
                     return_value={"pdf_status": "failed"},
                 ), \
                 patch(
                     "run_papers.retry_failed_pdf_entries",
                     side_effect=concurrent_publish,
                 ), \
                 patch(
                     "run_papers._pdf_store_hit",
                     return_value="/tmp/verified.pdf",
                 ), \
                 patch(
                     "run_papers._pdf_quality_tainted",
                     return_value=False,
                 ):
                result = run_papers.retry_pdf(
                    mode="daily",
                    key=key,
                    return_stats=True,
                )

            with open(index_path, encoding="utf-8") as handle:
                persisted = json.load(handle)

        self.assertEqual(result["residual_ids"], [])
        self.assertEqual(persisted["generated_at"], "new-publisher")
        self.assertEqual(len(persisted["papers"]), 2)
        self.assertEqual(persisted["papers"][0]["rank"], 99)
        self.assertEqual(
            persisted["papers"][0]["publisher_field"], "keep"
        )
        self.assertEqual(persisted["papers"][0]["pdf_status"], "ok")
        self.assertEqual(
            persisted["papers"][1]["arxiv_id"], "2607.99999"
        )

    def test_multi_key_missing_mode_reports_every_requested_index(self):
        keys = ["2026-07-26", "2026-07-27"]
        with patch("run_papers.mode_dir", return_value="/missing/daily"), \
             patch("translate_arxiv.load_api_config", return_value={}):
            repair = run_papers.repair(
                mode="daily",
                keys=keys,
                return_stats=True,
            )
            retry = run_papers.retry_pdf(
                mode="daily",
                keys=keys,
                return_stats=True,
            )

        expected = [
            "daily/2026-07-26:index",
            "daily/2026-07-27:index",
        ]
        self.assertEqual(repair["residual_ids"], expected)
        self.assertEqual(retry["residual_ids"], expected)


if __name__ == "__main__":
    unittest.main()
