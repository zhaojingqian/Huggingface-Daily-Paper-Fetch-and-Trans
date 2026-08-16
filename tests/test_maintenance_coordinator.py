import datetime as dt
import unittest

from scripts.install_paper_cron import BEGIN, END, render_crontab
from scripts.run_maintenance import maintenance_commands


class MaintenanceCoordinatorTests(unittest.TestCase):
    def test_cron_replaces_legacy_paper_jobs_and_preserves_other_projects(self):
        existing = "\n".join(
            [
                "LANG=en_US.UTF-8",
                "PYTHON=/root/.pyenv/versions/3.10.13/bin/python3",
                "PTDIR=/root/workspace/paper-trans",
                "# 每天 23:00 — daily top 3（摘要 + 全文 PDF）",
                "# COROS health refresh",
                "0 23 * * * $PYTHON $PTDIR/run_daily.py",
                "0 9 * * * /root/workspace/integrations/coros-mcp/pull.sh",
                "17 */12 * * * /root/scholar-citation-monitor/run_monitor.sh",
            ]
        )
        rendered = render_crontab(existing, "/root/workspace/apps/paper-trans")
        self.assertEqual(rendered.count(BEGIN), 1)
        self.assertEqual(rendered.count(END), 1)
        self.assertNotIn("/root/workspace/paper-trans", rendered)
        self.assertNotIn("daily top 3", rendered)
        self.assertIn("COROS health refresh", rendered)
        self.assertIn("integrations/coros-mcp/pull.sh", rendered)
        self.assertIn("scholar-citation-monitor", rendered)
        self.assertNotIn("PYTHON=", rendered)
        self.assertNotIn("/root/.pyenv", rendered)
        self.assertEqual(rendered.count("workspace-ctl paper repair-weekly"), 1)
        self.assertEqual(rendered.count("workspace-ctl paper maintenance"), 1)

    def test_daily_cycle_consolidates_all_mode_repair(self):
        commands = maintenance_commands(dt.datetime(2026, 8, 11))
        joined = [" ".join(command) for command in commands]
        self.assertEqual(sum("run_repair.py --post --days 2" in item for item in joined), 1)
        self.assertEqual(sum("run_repair.py --retry-pdf --days 7" in item for item in joined), 1)
        self.assertFalse(any("weekly_cleanup.sh" in item for item in joined))

    def test_sunday_and_month_end_add_only_conditional_steps(self):
        commands = maintenance_commands(dt.datetime(2026, 6, 28))
        joined = [" ".join(command) for command in commands]
        self.assertEqual(sum("--mode monthly" in item for item in joined), 2)
        self.assertEqual(sum("weekly_cleanup.sh" in item for item in joined), 1)


if __name__ == "__main__":
    unittest.main()
