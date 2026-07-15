import unittest
from datetime import date, datetime
from unittest import mock

from paperhub.modes import CONTENT_MODES, FETCH_MODES, mode_spec
from paperhub.runner import run_fetch_mode_cli


class ModeConfigTest(unittest.TestCase):
    def test_mode_specs_keep_limits_and_calendar_keys_together(self):
        sample = date(2026, 6, 28)

        self.assertEqual(FETCH_MODES, ("daily", "weekly", "monthly"))
        self.assertEqual(CONTENT_MODES, FETCH_MODES + ("topic",))
        self.assertEqual(mode_spec("daily").limit, 3)
        self.assertEqual(mode_spec("weekly").key_for(sample), "2026-W26")
        self.assertEqual(mode_spec("monthly").key_for(sample), "2026-06")

    def test_recent_keys_deduplicate_week_and_month_buckets(self):
        self.assertEqual(
            mode_spec("weekly").recent_keys(3, date(2026, 6, 28)),
            ("2026-W26",),
        )
        self.assertEqual(
            mode_spec("monthly").recent_keys(2, date(2026, 7, 1)),
            ("2026-06", "2026-07"),
        )

    def test_pending_refetch_uses_each_modes_schedule(self):
        self.assertEqual(
            mode_spec("daily").pending_refetch_key(datetime(2026, 6, 28, 22, 59)),
            "2026-06-28",
        )
        self.assertIsNone(
            mode_spec("weekly").pending_refetch_key(datetime(2026, 6, 28, 2, 0))
        )
        self.assertIsNone(
            mode_spec("monthly").pending_refetch_key(datetime(2026, 6, 28, 2, 0))
        )

    def test_shared_runner_preserves_wrapper_contract(self):
        with mock.patch("run_papers.run", return_value=True) as run:
            code = run_fetch_mode_cli("weekly", ["2026-W26", "--no-full"])

        self.assertEqual(code, 0)
        run.assert_called_once_with(
            mode="weekly", key="2026-W26", limit=10, do_full_translate=False
        )


if __name__ == "__main__":
    unittest.main()
