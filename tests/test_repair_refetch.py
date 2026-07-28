import sys
import json
import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

import run_repair


class RepairRefetchScheduleTest(unittest.TestCase):
    def test_weekly_current_key_is_refetchable_after_sunday_trigger(self):
        self.assertEqual(
            run_repair._pending_refetch_key("weekly", datetime(2026, 6, 28, 1, 59)),
            "2026-W26",
        )
        self.assertIsNone(
            run_repair._pending_refetch_key("weekly", datetime(2026, 6, 28, 2, 0))
        )
        self.assertIsNone(
            run_repair._pending_refetch_key("weekly", datetime(2026, 6, 28, 12, 4))
        )

    def test_weekly_ongoing_week_still_skips_before_its_sunday_trigger(self):
        self.assertEqual(
            run_repair._pending_refetch_key("weekly", datetime(2026, 6, 29, 9, 0)),
            "2026-W27",
        )

    def test_daily_current_key_is_refetchable_after_trigger(self):
        self.assertEqual(
            run_repair._pending_refetch_key("daily", datetime(2026, 6, 28, 22, 59)),
            "2026-06-28",
        )
        self.assertIsNone(
            run_repair._pending_refetch_key("daily", datetime(2026, 6, 28, 23, 0))
        )

    def test_monthly_current_key_is_refetchable_after_trigger(self):
        self.assertEqual(
            run_repair._pending_refetch_key("monthly", datetime(2026, 6, 28, 1, 59)),
            "2026-06",
        )
        self.assertIsNone(
            run_repair._pending_refetch_key("monthly", datetime(2026, 6, 28, 2, 0))
        )

    def test_explicit_refetch_key_never_expands_to_recent_days(self):
        key = "2026-07-27"
        with patch("run_repair._recent_keys",
                   side_effect=AssertionError("must not scan recent keys")), \
             patch("run_repair._pending_refetch_key",
                   side_effect=AssertionError("must not apply schedule to exact key")), \
             patch("run_repair._log"), \
             patch("run_repair.mode_index_path",
                   return_value=f"/tmp/daily/{key}/index.json"), \
             patch("run_repair._index_has_papers", return_value=False), \
             patch("run_papers.run", return_value=True) as run:
            result = run_repair.refetch_missing(
                mode="daily", days=30, key=key, return_stats=True
            )

        run.assert_called_once_with(
            mode="daily", key=key, limit=run_repair.mode_spec("daily").limit,
            do_full_translate=True,
        )
        self.assertEqual(result["refetch_attempted"], 1)
        self.assertEqual(result["refetch_succeeded"], 1)
        self.assertEqual(result["residual_failures"], 0)

    def test_nonempty_malformed_index_is_not_treated_as_healthy(self):
        malformed_payloads = (
            {"papers": {"2607.00001": {}}},
            {"papers": ["2607.00001"]},
            {"papers": [{"rank": 1}]},
        )
        with tempfile.TemporaryDirectory() as tmp:
            index_path = os.path.join(tmp, "index.json")
            for payload in malformed_payloads:
                with self.subTest(payload=payload):
                    with open(index_path, "w", encoding="utf-8") as handle:
                        json.dump(payload, handle)
                    self.assertFalse(
                        run_repair._index_has_papers(index_path)
                    )

    def test_malformed_topic_index_is_a_structural_residual(self):
        with tempfile.TemporaryDirectory() as tmp:
            index_path = os.path.join(tmp, "index.json")
            with open(index_path, "w", encoding="utf-8") as handle:
                handle.write("{broken")
            targets = [({"slug": "opd"}, "2026-07-27")]
            with patch(
                "topic_engine.topic_repair_targets",
                return_value=targets,
            ), patch(
                "paperhub.topic_store.index_path",
                return_value=index_path,
            ):
                summary = run_repair._topic_summary_stats(
                    "opd", 7, False, None
                )
                attempted, pdf_residuals = run_repair._topic_pdf_failures(
                    "opd", 7, False, None
                )

        expected = "topic/opd/2026-07-27:index"
        self.assertEqual(summary["residual_ids"], [expected])
        self.assertEqual(summary["residual_failures"], 1)
        self.assertEqual(attempted, 0)
        self.assertEqual(pdf_residuals, {expected})

    def test_topic_pdf_queue_includes_persisted_store_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            index_path = os.path.join(tmp, "index.json")
            with open(index_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {"papers": [{"arxiv_id": "2607.00001"}]},
                    handle,
                )
            targets = [({"slug": "opd"}, "2026-07-27")]
            with patch(
                "topic_engine.topic_repair_targets",
                return_value=targets,
            ), patch(
                "paperhub.topic_store.index_path",
                return_value=index_path,
            ), patch(
                "paperhub.paper_store.read_raw",
                return_value={"pdf_status": "failed"},
            ), patch(
                "paperhub.paper_store.pdf_quality_tainted",
                return_value=False,
            ):
                attempted, residuals = run_repair._topic_pdf_failures(
                    "opd", 7, False, None
                )

        self.assertEqual(attempted, 1)
        self.assertEqual(residuals, {"2607.00001"})

    def test_explicit_key_requires_mode_before_any_repair_work(self):
        argv = ["run_repair.py", "--post", "--key", "2026-07-27"]
        with patch.object(sys, "argv", argv), \
             patch("run_repair._log") as log:
            with self.assertRaises(SystemExit) as raised:
                run_repair.main()

        self.assertEqual(raised.exception.code, 2)
        log.assert_not_called()

    def test_action_modes_cannot_be_combined_and_silently_ignored(self):
        combinations = (
            ("--post", "--refetch"),
            ("--post", "--retry-pdf"),
            ("--refetch", "--retry-pdf"),
        )
        for actions in combinations:
            with self.subTest(actions=actions):
                argv = ["run_repair.py", *actions]
                with patch.object(sys, "argv", argv), \
                     patch("run_repair._log") as log:
                    with self.assertRaises(SystemExit) as raised:
                        run_repair.main()
                self.assertEqual(raised.exception.code, 2)
                log.assert_not_called()

    def test_refetch_rejects_modes_without_fetch_queues(self):
        for mode in ("manual", "topic"):
            with self.subTest(mode=mode):
                argv = ["run_repair.py", "--refetch", "--mode", mode]
                with patch.object(sys, "argv", argv), \
                     patch("run_repair._log") as log:
                    with self.assertRaises(SystemExit) as raised:
                        run_repair.main()
                self.assertEqual(raised.exception.code, 2)
                log.assert_not_called()

    def test_topic_filter_requires_topic_mode(self):
        for argv in (
            ["run_repair.py", "--topic", "opd"],
            ["run_repair.py", "--mode", "daily", "--topic", "opd"],
        ):
            with self.subTest(argv=argv), \
                 patch.object(sys, "argv", argv), \
                 patch("run_repair._log") as log:
                with self.assertRaises(SystemExit) as raised:
                    run_repair.main()
                self.assertEqual(raised.exception.code, 2)
                log.assert_not_called()

    def test_exact_key_cannot_be_combined_with_all_history(self):
        argv = [
            "run_repair.py", "--retry-pdf", "--mode", "daily",
            "--key", "2026-07-27", "--all",
        ]
        with patch.object(sys, "argv", argv), \
             patch("run_repair._log") as log:
            with self.assertRaises(SystemExit) as raised:
                run_repair.main()
        self.assertEqual(raised.exception.code, 2)
        log.assert_not_called()

    def test_mode_specific_explicit_keys_are_validated(self):
        invalid = (
            ("daily", "2026-W31"),
            ("manual", "../2026-07-27"),
            ("weekly", "2026-W54"),
            ("monthly", "2026-13"),
            ("topic", "opd/2026-02-30"),
            ("topic", "../../outside"),
        )
        for mode, key in invalid:
            with self.subTest(mode=mode, key=key):
                argv = [
                    "run_repair.py", "--retry-pdf", "--mode", mode,
                    "--key", key,
                ]
                with patch.object(sys, "argv", argv), \
                     patch("run_repair._log") as log:
                    with self.assertRaises(SystemExit) as raised:
                        run_repair.main()
                self.assertEqual(raised.exception.code, 2)
                log.assert_not_called()

    def test_valid_explicit_key_formats(self):
        valid = (
            ("daily", "2026-07-27"),
            ("manual", "2026-07-27"),
            ("weekly", "2026-W31"),
            ("monthly", "2026-07"),
            ("topic", "2026-07-27"),
            ("topic", "opd/2026-07-27"),
        )
        for mode, key in valid:
            with self.subTest(mode=mode, key=key):
                self.assertEqual(
                    run_repair.validate_explicit_key(mode, key),
                    key,
                )

    def test_post_all_refuses_unbounded_fetch_modes(self):
        for argv in (
            ["run_repair.py", "--post", "--all"],
            ["run_repair.py", "--post", "--all", "--mode", "weekly"],
        ):
            with self.subTest(argv=argv), \
                 patch.object(sys, "argv", argv), \
                 patch("run_repair._log") as log:
                with self.assertRaises(SystemExit) as raised:
                    run_repair.main()
                self.assertEqual(raised.exception.code, 2)
                log.assert_not_called()

    def test_refetch_function_rejects_wrong_key_shape(self):
        with patch("run_papers.run") as run:
            with self.assertRaises(ValueError):
                run_repair.refetch_missing(
                    mode="weekly",
                    key="2026-07-27",
                    return_stats=True,
                )
        run.assert_not_called()

    def test_later_persisted_audit_clears_earlier_residual(self):
        stats = run_repair._new_stats()
        run_repair._merge_stats(stats, {
            "audited_ids": ["2607.00001"],
            "residual_ids": ["2607.00001"],
            "residual_failures": 1,
        })
        run_repair._merge_stats(stats, {
            "audited_ids": ["2607.00001"],
            "residual_ids": [],
            "residual_failures": 0,
        })

        self.assertEqual(stats["residual_ids"], [])
        self.assertEqual(stats["residual_failures"], 0)

    def test_retry_pdf_cli_exits_nonzero_when_residuals_remain(self):
        result = {
            "pdf_attempted": 1,
            "pdf_succeeded": 0,
            "pdf_failed": 1,
            "residual_failures": 1,
            "residual_ids": ["2607.00001"],
        }
        argv = [
            "run_repair.py", "--retry-pdf", "--mode", "daily",
            "--key", "2026-07-27",
        ]
        with patch.object(sys, "argv", argv), \
             patch("run_repair._log"), \
             patch("run_papers.retry_pdf", return_value=result):
            with self.assertRaises(SystemExit) as raised:
                run_repair.main()
        self.assertEqual(raised.exception.code, 1)

    def test_post_exact_missing_key_refetches_without_stale_index_residual(self):
        key = "2026-07-27"
        argv = [
            "run_repair.py", "--post", "--mode", "daily", "--key", key,
        ]
        with patch.object(sys, "argv", argv), \
             patch("run_repair._log"), \
             patch("run_repair._index_has_papers", return_value=False), \
             patch("run_repair.mode_index_path",
                   return_value=f"/tmp/daily/{key}/index.json"), \
             patch("run_papers.repair") as repair, \
             patch("run_papers.run", return_value=True) as run:
            with self.assertRaises(SystemExit) as raised:
                run_repair.main()

        self.assertEqual(raised.exception.code, 0)
        repair.assert_not_called()
        run.assert_called_once_with(
            mode="daily", key=key, limit=run_repair.mode_spec("daily").limit,
            do_full_translate=True,
        )


if __name__ == "__main__":
    unittest.main()
