import unittest
from datetime import datetime
import json
import tempfile
from unittest.mock import patch

from paperhub.patch_catalog import PATCH_CATALOG, patches_for_records
from paperhub.weekly_repair import current_week_key


class WeeklyRepairTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
