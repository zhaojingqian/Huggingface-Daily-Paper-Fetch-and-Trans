import unittest
from contextlib import redirect_stdout
from datetime import datetime
import io
import json
import os
import tempfile
from unittest.mock import MagicMock, patch

from paperhub.patch_catalog import PATCH_CATALOG, patches_for_records
from paperhub.weekly_repair import current_week_key, current_week_targets


class WeeklyRepairTest(unittest.TestCase):
    def test_weekly_coordinator_lock_is_persistent_and_exclusive(self):
        from paperhub import weekly_repair

        with tempfile.TemporaryDirectory() as tmp, patch.object(
            weekly_repair, "LOCK_DIR", tmp
        ):
            lock_path = os.path.join(
                tmp, "weekly-repair-2026-W31.lock"
            )
            with weekly_repair._exclusive_repair_lock("2026-W31") as acquired:
                self.assertTrue(acquired)
                self.assertTrue(os.path.exists(lock_path))
                with weekly_repair._exclusive_repair_lock(
                    "2026-W31"
                ) as duplicate:
                    self.assertFalse(duplicate)
            self.assertTrue(os.path.exists(lock_path))
            with weekly_repair._exclusive_repair_lock(
                "2026-W31"
            ) as reacquired:
                self.assertTrue(reacquired)

    def test_current_week_key_uses_iso_calendar(self):
        self.assertEqual(current_week_key(datetime(2026, 7, 20, 2, 0)), "2026-W30")

    def test_patch_catalog_deduplicates_known_failure_classes(self):
        records = [
            {"category": "compile.asset_missing"},
            {"category": "compile.asset_missing"},
            {"category": "compile.numeric_syntax"},
            {"category": "compile.not_in_catalog"},
        ]

        patches = patches_for_records(records)

        self.assertEqual(
            [item["category"] for item in patches],
            ["compile.asset_missing", "compile.numeric_syntax"],
        )
        self.assertEqual(patches[0]["strategy"], "reuse_translation")

    def test_patch_catalog_covers_structured_failure_taxonomy(self):
        expected = {
            "translate.api_auth",
            "translate.api_rate_limit",
            "translate.network_timeout",
            "translate.plugin_runtime",
            "translate.plugin_exception",
            "translate.unknown",
            "compile.macro_recursion",
            "compile.asset_missing",
            "compile.dependency_missing",
            "compile.legacy_cjk_environment",
            "compile.pdftex_primitive",
            "compile.undefined_command",
            "compile.structure_mismatch",
            "compile.numeric_syntax",
            "compile.math_or_alignment",
            "compile.verbatim_corruption",
            "compile.resource_exhaustion",
            "quality.untranslated_prose",
            "quality.pdf_sustained_untranslated",
            "quality.pdf_partial_untranslated",
            "quality.translation_refusal",
            "compile.latex_error",
            "compile.unknown",
        }

        self.assertTrue(expected.issubset(PATCH_CATALOG))

    def test_duplicate_weekly_repair_returns_without_running_work(self):
        from paperhub import weekly_repair

        with patch.object(weekly_repair, "_exclusive_repair_lock") as lock_factory:
            lock_factory.return_value.__enter__.return_value = False
            lock_factory.return_value.__exit__.return_value = None
            result = weekly_repair.run_current_week_repair(key="2026-W30")

        self.assertEqual(result["status"], "already_running")

    def test_history_keeps_multiple_runs_for_the_same_week(self):
        from paperhub import weekly_repair

        with tempfile.TemporaryDirectory() as tmp, patch.object(weekly_repair, "LOGS_DIR", tmp):
            weekly_repair._write_history("2026-W30", {"status": "partial", "run": 1})
            weekly_repair._write_history("2026-W30", {"status": "ok", "run": 2})
            path = f"{tmp}/repair_history/weekly-2026-W30.json"
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)

        self.assertEqual([item["run"] for item in data["runs"]], [1, 2])
        self.assertEqual(data["latest"]["status"], "ok")

    def test_current_week_targets_cover_all_published_modes_and_cross_month(self):
        from paperhub import weekly_repair

        with tempfile.TemporaryDirectory() as tmp:
            paths = (
                ("daily", "2026-07-27"),
                ("weekly", "2026-W31"),
                ("manual", "2026-07-30"),
                ("topic", "opd/2026-08-01"),
                ("monthly", "2026-07"),
                ("monthly", "2026-08"),
                # Outside the selected ISO week and must not be included.
                ("daily", "2026-07-26"),
                ("topic", "opd/2026-08-03"),
            )
            for mode, key in paths:
                directory = os.path.join(tmp, mode, key)
                os.makedirs(directory, exist_ok=True)
                with open(os.path.join(directory, "index.json"), "w", encoding="utf-8") as handle:
                    json.dump({"papers": [{"arxiv_id": "2607.00001"}]}, handle)

            targets = current_week_targets("2026-W31", data_dir=tmp)

        self.assertEqual(targets["daily"], ["2026-07-27"])
        self.assertEqual(targets["weekly"], ["2026-W31"])
        self.assertEqual(targets["manual"], ["2026-07-30"])
        self.assertEqual(targets["topic"], ["opd/2026-08-01"])
        self.assertEqual(targets["monthly"], ["2026-07", "2026-08"])

    def test_weekly_wait_checks_the_explicit_data_root(self):
        from paperhub import weekly_repair

        with tempfile.TemporaryDirectory() as tmp:
            index_path = os.path.join(
                tmp, "weekly", "2026-W31", "index.json"
            )
            os.makedirs(os.path.dirname(index_path), exist_ok=True)
            with open(index_path, "w", encoding="utf-8") as handle:
                json.dump({"papers": []}, handle)
            lock = MagicMock()
            with patch("run_papers.RunLock", return_value=lock) as lock_type:
                acquired, error = weekly_repair._wait_for_weekly_lock(
                    "2026-W31",
                    wait_seconds=0,
                    poll_seconds=1,
                    data_dir=tmp,
                )

        self.assertIs(acquired, lock)
        self.assertIsNone(error)
        lock_type.assert_called_once_with(
            "weekly",
            "2026-W31",
            lock_dir=os.path.join(tmp, "locks"),
        )
        lock.__enter__.assert_called_once_with()

    def test_unique_pdf_result_syncs_every_reference(self):
        from paperhub import weekly_repair

        with tempfile.TemporaryDirectory() as tmp:
            for mode, key, status in (
                ("daily", "2026-07-27", "failed"),
                ("weekly", "2026-W31", "failed"),
                # Historical reference outside the selected week must still
                # receive the shared paper-store result.
                ("monthly", "2026-06", "failed"),
            ):
                directory = os.path.join(tmp, mode, key)
                os.makedirs(directory, exist_ok=True)
                with open(os.path.join(directory, "index.json"), "w", encoding="utf-8") as handle:
                    json.dump(
                        {
                            "papers": [
                                {"arxiv_id": "2607.00001", "pdf_status": status}
                            ]
                        },
                        handle,
                    )

            documents, references, errors = weekly_repair._load_all_indexes(tmp)
            self.assertEqual(errors, [])
            with patch.object(
                weekly_repair.paper_store,
                "read_raw",
                return_value={"pdf_status": "failed"},
            ):
                entries = weekly_repair._representative_pdf_entries(
                    {"2607.00001"}, references
                )
            self.assertEqual(list(entries), ["2607.00001"])
            entries["2607.00001"]["pdf_status"] = "ok"

            sync_stats, sync_errors = weekly_repair._sync_pdf_statuses(
                entries, references, documents
            )

            self.assertEqual(sync_errors, [])
            self.assertEqual(sync_stats["indexes_written"], 3)
            self.assertEqual(sync_stats["references_updated"], 3)
            for ref in references["2607.00001"]:
                with open(ref["path"], encoding="utf-8") as handle:
                    payload = json.load(handle)
                self.assertEqual(payload["papers"][0]["pdf_status"], "ok")

    def test_weekly_sync_preserves_concurrent_publisher_payload(self):
        from paperhub import weekly_repair

        with tempfile.TemporaryDirectory() as tmp:
            directory = os.path.join(tmp, "daily", "2026-07-27")
            os.makedirs(directory, exist_ok=True)
            index_path = os.path.join(directory, "index.json")
            with open(index_path, "w", encoding="utf-8") as handle:
                json.dump({
                    "generated_at": "old",
                    "papers": [{
                        "arxiv_id": "2607.00001",
                        "rank": 1,
                        "pdf_status": "failed",
                    }],
                }, handle)
            documents, references, errors = weekly_repair._load_all_indexes(
                tmp
            )
            self.assertEqual(errors, [])

            # Simulate a publisher replacing the index after weekly's initial
            # scan but before status synchronization.
            with open(index_path, "w", encoding="utf-8") as handle:
                json.dump({
                    "generated_at": "new-publisher",
                    "papers": [
                        {
                            "arxiv_id": "2607.00001",
                            "rank": 42,
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

            stats, sync_errors = weekly_repair._sync_pdf_statuses(
                {"2607.00001": {
                    "arxiv_id": "2607.00001",
                    "pdf_status": "ok",
                }},
                references,
                documents,
                data_dir=tmp,
            )
            with open(index_path, encoding="utf-8") as handle:
                persisted = json.load(handle)

        self.assertEqual(sync_errors, [])
        self.assertEqual(stats["indexes_written"], 1)
        self.assertEqual(stats["references_updated"], 1)
        self.assertEqual(persisted["generated_at"], "new-publisher")
        self.assertEqual(len(persisted["papers"]), 2)
        self.assertEqual(persisted["papers"][0]["rank"], 42)
        self.assertEqual(
            persisted["papers"][0]["publisher_field"], "keep"
        )
        self.assertEqual(persisted["papers"][0]["pdf_status"], "ok")
        self.assertEqual(
            persisted["papers"][1]["arxiv_id"], "2607.99999"
        )

    def test_persistent_quality_taint_is_always_a_weekly_pdf_candidate(self):
        from paperhub import weekly_repair

        entries = {
            "2607.00001": {
                "arxiv_id": "2607.00001",
                "pdf_status": "ok",
            },
        }
        with patch.object(
            weekly_repair.paper_store,
            "pdf_quality_tainted",
            return_value=True,
        ), patch.object(
            weekly_repair.paper_store,
            "pdf_hit",
            return_value="/tmp/old-english.pdf",
        ):
            candidates = weekly_repair._pdf_candidates(entries)

        self.assertEqual(candidates, {"2607.00001"})

    def test_empty_topic_is_valid_but_empty_weekly_is_residual(self):
        from paperhub import weekly_repair

        targets = {
            "daily": [],
            "weekly": ["2026-W30"],
            "monthly": [],
            "manual": [],
            "topic": ["opd/2026-07-20"],
        }
        documents = {
            "/weekly/index.json": {"papers": []},
            "/topic/index.json": {"papers": []},
        }
        mapping = {
            ("weekly", "2026-W30"): "/weekly/index.json",
            ("topic", "opd/2026-07-20"): "/topic/index.json",
        }
        with patch.object(
            weekly_repair,
            "_index_path",
            side_effect=lambda _, mode, key: mapping[(mode, key)],
        ):
            selected, _, errors = weekly_repair._selected_ids(
                targets, documents, "/data"
            )

        self.assertEqual(selected, set())
        self.assertEqual(errors, ["weekly/2026-W30:empty-index"])

    def test_failed_index_without_sidecar_keeps_all_mode_run_partial(self):
        from paperhub import weekly_repair

        shared_lock = MagicMock()
        targets = {
            "daily": ["2026-07-20"],
            "weekly": ["2026-W30"],
            "monthly": [],
            "manual": [],
            "topic": ["opd/2026-07-20"],
        }
        documents = {
            "/daily/index.json": {
                "papers": [{"arxiv_id": "2607.00001", "pdf_status": "failed"}]
            },
            "/weekly/index.json": {
                "papers": [{"arxiv_id": "2607.00001", "pdf_status": "failed"}]
            },
            "/topic/index.json": {
                "papers": [{"arxiv_id": "2607.00002", "pdf_status": "failed"}]
            },
        }
        references = {
            "2607.00001": [
                {
                    "mode": "daily",
                    "key": "2026-07-20",
                    "path": "/daily/index.json",
                    "item": documents["/daily/index.json"]["papers"][0],
                },
                {
                    "mode": "weekly",
                    "key": "2026-W30",
                    "path": "/weekly/index.json",
                    "item": documents["/weekly/index.json"]["papers"][0],
                },
            ],
            "2607.00002": [
                {
                    "mode": "topic",
                    "key": "opd/2026-07-20",
                    "path": "/topic/index.json",
                    "item": documents["/topic/index.json"]["papers"][0],
                }
            ],
        }
        summary_stats = weekly_repair._finish_stats(
            weekly_repair._new_stats(), ["2607.00001"]
        )
        summary_stats["summary_repaired"] = 0

        def retry(entries):
            self.assertEqual(
                sorted(entries), ["2607.00001", "2607.00002"]
            )
            entries["2607.00001"]["pdf_status"] = "failed"
            entries["2607.00002"]["pdf_status"] = "ok"
            return {
                "pdf_attempted": 2,
                "pdf_succeeded": 1,
                "pdf_failed": 1,
                "residual_failures": 1,
                "residual_ids": ["2607.00001"],
            }

        def target_path(data_dir, mode, key):
            mapping = {
                ("daily", "2026-07-20"): "/daily/index.json",
                ("weekly", "2026-W30"): "/weekly/index.json",
                ("topic", "opd/2026-07-20"): "/topic/index.json",
            }
            return mapping[(mode, key)]

        with patch.object(weekly_repair, "_exclusive_repair_lock") as repair_lock, \
             patch.object(
                 weekly_repair,
                 "_wait_for_weekly_lock",
                 return_value=(shared_lock, None),
             ), \
             patch.object(weekly_repair, "current_week_targets", return_value=targets), \
             patch.object(
                 weekly_repair,
                 "_load_all_indexes",
                 return_value=(documents, references, []),
             ), \
             patch.object(weekly_repair, "_index_path", side_effect=target_path), \
             patch.object(
                 weekly_repair,
                 "_repair_unique_summaries",
                 return_value=(
                     summary_stats,
                     {"2607.00001"},
                     set(),
                     [],
                 ),
             ), \
             patch.object(weekly_repair, "_retry_unique_pdfs", side_effect=retry), \
             patch.object(
                 weekly_repair,
                 "_sync_pdf_statuses",
                 return_value=(
                     {"indexes_written": 3, "references_updated": 3},
                     [],
                 ),
             ), \
             patch.object(
                 weekly_repair,
                 "_failure_records_for_ids",
                 return_value=[],
             ), \
             patch.object(
                 weekly_repair.paper_store,
                 "read_raw",
                 return_value={"pdf_status": "failed"},
             ), \
             patch.object(
                 weekly_repair.paper_store,
                 "pdf_hit",
                 return_value=None,
             ), \
             patch.object(weekly_repair, "_write_history") as write_history:
            repair_lock.return_value.__enter__.return_value = True
            repair_lock.return_value.__exit__.return_value = None
            result = weekly_repair.run_current_week_repair(
                key="2026-W30", data_dir="/data"
            )

        shared_lock.__exit__.assert_called_once_with(None, None, None)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["unique_papers"], 2)
        self.assertEqual(result["metadata_repaired"], 0)
        self.assertEqual(result["pdf_repaired"], 1)
        self.assertEqual(result["failures_after"], [])
        self.assertEqual(result["residual_failures"], 1)
        self.assertEqual(
            result["residual_ids"], ["2607.00001"]
        )
        self.assertEqual(result["targets_by_mode"], targets)
        self.assertEqual(result["stats_by_mode"]["daily"]["unique_papers"], 1)
        self.assertEqual(result["stats_by_mode"]["topic"]["pdf_repaired"], 1)
        write_history.assert_called_once_with("2026-W30", result)

    def test_cron_entrypoint_returns_nonzero_for_partial_result(self):
        from scripts import repair_weekly_current

        with patch.object(
            repair_weekly_current,
            "run_current_week_repair",
            return_value={"key": "2026-W30", "status": "partial"},
        ), patch("sys.argv", ["repair_weekly_current.py", "--key", "2026-W30"]), \
             redirect_stdout(io.StringIO()):
            self.assertEqual(repair_weekly_current.main(), 1)


if __name__ == "__main__":
    unittest.main()
