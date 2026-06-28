import unittest
from datetime import datetime

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


if __name__ == "__main__":
    unittest.main()
