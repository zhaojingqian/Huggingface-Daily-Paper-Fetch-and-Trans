import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from paperhub.publication_lock import (
    PublicationBusyError,
    PublicationLock,
    catalog_lock_path,
    paper_lock_path,
)
from scripts import cleanup_orphan_artifacts as cleanup


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "cleanup_orphan_artifacts.py"


class CleanupOrphanArtifactsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = 10_000_000.0
        self.grace = 3 * 24 * 60 * 60
        for directory in (
            self.root / "data" / "papers",
            self.root / "data" / "tex_backup_failed",
            self.root / "logs" / "pdf_errors",
        ):
            directory.mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def _write_index(self, arxiv_ids, mode="daily", key="2026-07-27"):
        path = self.root / "data" / mode / key / "index.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "papers": [
                        {"arxiv_id": arxiv_id, "rank": position}
                        for position, arxiv_id in enumerate(arxiv_ids, 1)
                    ]
                }
            ),
            encoding="utf-8",
        )
        return path

    def _artifact(self, relative_path, *, old=True, content=b"artifact"):
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        age = self.grace + 10 if old else 10
        os.utime(path, (self.now - age, self.now - age))
        return path

    def _all_orphan_artifacts(self, arxiv_id="2607.00002", old=True):
        return [
            self._artifact(
                f"data/papers/{arxiv_id}_zh.pdf",
                old=old,
                content=b"%PDF-1.7\n%%EOF\n",
            ),
            self._artifact(
                f"logs/pdf_errors/{arxiv_id}.json",
                old=old,
            ),
            self._artifact(
                f"logs/pdf_errors/{arxiv_id}.log",
                old=old,
            ),
            self._artifact(
                f"data/tex_backup_failed/"
                f"{arxiv_id}_merge_translate_zh.tex",
                old=old,
            ),
        ]

    def test_dry_run_uses_real_paths_without_deleting(self):
        referenced = self._artifact("data/papers/2607.00001_zh.pdf")
        orphan_paths = self._all_orphan_artifacts()
        recent = self._artifact(
            "data/papers/2607.00003_zh.pdf",
            old=False,
        )
        unrelated = self._artifact("data/papers/README.txt")
        self._write_index(["2607.00001"])

        result = cleanup.cleanup_orphan_artifacts(
            self.root,
            grace_seconds=self.grace,
            dry_run=True,
            now=self.now,
            lock_timeout=0,
        )

        self.assertEqual(
            result["would_remove"],
            {"pdf": 1, "sidecar": 2, "failed_tex": 1},
        )
        self.assertEqual(result["removed"], {
            "pdf": 0,
            "sidecar": 0,
            "failed_tex": 0,
        })
        self.assertEqual(result["kept_referenced"]["pdf"], 1)
        self.assertEqual(result["kept_recent"]["pdf"], 1)
        for path in [referenced, recent, unrelated, *orphan_paths]:
            self.assertTrue(path.exists(), path)

    def test_apply_cli_deletes_only_aged_orphan_artifact_paths(self):
        orphan_paths = self._all_orphan_artifacts()
        referenced = self._artifact("data/papers/2607.00001_zh.pdf")
        recent = self._artifact(
            "logs/pdf_errors/2607.00003.json",
            old=False,
        )
        os.utime(recent, None)
        self._write_index(["2607.00001"])

        result = subprocess.run(
            [
                sys.executable,
                str(HELPER),
                "--root",
                str(self.root),
                "--grace-days",
                "3",
                "--apply",
                "--json",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(
            payload["removed"],
            {"pdf": 1, "sidecar": 2, "failed_tex": 1},
        )
        for path in orphan_paths:
            self.assertFalse(path.exists(), path)
        self.assertTrue(referenced.exists())
        self.assertTrue(recent.exists())

    def test_locked_rescan_preserves_candidate_published_after_discovery(self):
        orphan = self._artifact("data/papers/2607.00004_zh.pdf")
        original_discover = cleanup._discover_artifacts

        def discover_then_publish(root, **kwargs):
            candidates = original_discover(root, **kwargs)
            self._write_index(["2607.00004"])
            return candidates

        with mock.patch.object(
            cleanup,
            "_discover_artifacts",
            side_effect=discover_then_publish,
        ):
            result = cleanup.cleanup_orphan_artifacts(
                self.root,
                grace_seconds=self.grace,
                dry_run=False,
                now=self.now,
                lock_timeout=0,
            )

        self.assertTrue(orphan.exists())
        self.assertEqual(result["kept_referenced"]["pdf"], 1)
        self.assertEqual(result["removed"]["pdf"], 0)

    def test_corrupt_index_fails_closed_before_any_deletion(self):
        orphan_paths = self._all_orphan_artifacts()
        index_path = self.root / "data" / "topic" / "opd" / "2026-07-27" / "index.json"
        index_path.parent.mkdir(parents=True)
        index_path.write_text("{broken", encoding="utf-8")

        with self.assertRaises(cleanup.CleanupSafetyError):
            cleanup.cleanup_orphan_artifacts(
                self.root,
                grace_seconds=self.grace,
                dry_run=False,
                now=self.now,
                lock_timeout=0,
            )

        for path in orphan_paths:
            self.assertTrue(path.exists(), path)

    def test_busy_catalog_or_paper_lock_never_deletes(self):
        arxiv_id = "2607.00005"
        orphan = self._artifact(f"data/papers/{arxiv_id}_zh.pdf")
        lock_dir = self.root / "locks"
        scenarios = (
            catalog_lock_path(str(lock_dir)),
            paper_lock_path(arxiv_id, lock_dir=str(lock_dir)),
        )
        for busy_path in scenarios:
            with self.subTest(lock=Path(busy_path).name):
                with PublicationLock([busy_path], timeout=0):
                    with self.assertRaises(PublicationBusyError):
                        cleanup.cleanup_orphan_artifacts(
                            self.root,
                            grace_seconds=self.grace,
                            dry_run=False,
                            now=self.now,
                            lock_timeout=0,
                        )
                self.assertTrue(orphan.exists())


if __name__ == "__main__":
    unittest.main()
