import os
import unittest

from paperhub import paths

import run_papers
import run_repair
import translate_arxiv
import translate_full
import web_server


class SharedPathsTest(unittest.TestCase):
    def test_script_constants_use_shared_paths(self):
        checks = [
            (run_papers.DATA_DIR, paths.DATA_DIR),
            (run_papers.PAPER_STORE_DIR, paths.PAPER_STORE_DIR),
            (run_papers.LOGS_DIR, paths.LOGS_DIR),
            (translate_arxiv.PAPER_STORE_DIR, paths.PAPER_STORE_DIR),
            (translate_full.TEX_BACKUP_DIR, paths.TEX_BACKUP_DIR),
            (translate_full.TEX_FAILED_BACKUP_DIR, paths.TEX_FAILED_BACKUP_DIR),
            (web_server.DATA_DIR, paths.DATA_DIR),
            (web_server.PAPER_STORE_DIR, paths.PAPER_STORE_DIR),
            (web_server.BOOKMARKS_FILE, paths.BOOKMARKS_FILE),
            (run_repair.LOGS_DIR, paths.LOGS_DIR),
        ]
        for actual, expected in checks:
            with self.subTest(actual=actual):
                self.assertEqual(actual, expected)

    def test_mode_path_helpers(self):
        self.assertEqual(paths.mode_dir("daily"), os.path.join(paths.DATA_DIR, "daily"))
        self.assertEqual(
            paths.mode_key_dir("weekly", "2026-W22"),
            os.path.join(paths.DATA_DIR, "weekly", "2026-W22"),
        )
        self.assertEqual(
            paths.mode_papers_dir("manual", "2026-06-27"),
            os.path.join(paths.DATA_DIR, "manual", "2026-06-27", "papers"),
        )
        self.assertEqual(
            paths.mode_index_path("monthly", "2026-06"),
            os.path.join(paths.DATA_DIR, "monthly", "2026-06", "index.json"),
        )

    def test_container_driver_support_modules_are_deployed_together(self):
        names = {os.path.basename(path) for path in translate_full.DRIVER_SUPPORT_FILES}
        self.assertEqual(
            names,
            {"full_translate_driver.py", "latex_translation_filters.py", "failure_taxonomy.py"},
        )


if __name__ == "__main__":
    unittest.main()
