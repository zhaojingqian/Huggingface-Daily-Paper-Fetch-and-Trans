import fcntl
import json
import os
import tempfile
import unittest

from paperhub.publication_lock import (
    LOCK_EXCLUSIVE,
    PublicationBusyError,
    PublicationLock,
    catalog_lock_path,
    catalog_publication_lock,
    index_lock_path,
    index_publication_lock,
    merge_index_paper_fields,
    paper_lock_path,
)


class PublicationLockTest(unittest.TestCase):
    def test_flat_index_lock_keeps_cron_compatible_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                index_lock_path("daily", "2026-07-27", lock_dir=tmp),
                os.path.join(tmp, "daily-2026-07-27.lock"),
            )
            topic_path = index_lock_path(
                "topic",
                "opd/2026-07-27",
                lock_dir=tmp,
            )
            self.assertEqual(os.path.dirname(topic_path), tmp)
            self.assertNotIn("opd/", os.path.basename(topic_path))

    def test_catalog_exclusive_blocks_index_publishers(self):
        with tempfile.TemporaryDirectory() as tmp:
            with index_publication_lock(
                "daily",
                "2026-07-27",
                lock_dir=tmp,
            ):
                with self.assertRaises(PublicationBusyError):
                    with catalog_publication_lock(
                        lock_dir=tmp,
                        exclusive=True,
                    ):
                        pass

    def test_multi_resource_order_is_catalog_then_index_then_paper(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = PublicationLock([
                paper_lock_path("2607.00001", lock_dir=tmp),
                index_lock_path(
                    "daily", "2026-07-27", lock_dir=tmp
                ),
                catalog_lock_path(tmp),
            ])

        self.assertEqual(
            [os.path.basename(path) for path, _ in lock.lock_specs],
            [
                "publication-catalog.lock",
                "daily-2026-07-27.lock",
                "paper-2607.00001.lock",
            ],
        )

    def test_partial_multi_lock_failure_releases_earlier_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = os.path.join(tmp, "a.lock")
            busy = os.path.join(tmp, "b.lock")
            with open(busy, "a+", encoding="utf-8") as busy_handle:
                fcntl.flock(
                    busy_handle,
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )
                with self.assertRaises(PublicationBusyError):
                    with PublicationLock(
                        [
                            (first, LOCK_EXCLUSIVE),
                            (busy, LOCK_EXCLUSIVE),
                        ],
                    ):
                        pass
                with open(first, "a+", encoding="utf-8") as first_handle:
                    fcntl.flock(
                        first_handle,
                        fcntl.LOCK_EX | fcntl.LOCK_NB,
                    )
                    fcntl.flock(first_handle, fcntl.LOCK_UN)
                fcntl.flock(busy_handle, fcntl.LOCK_UN)

    def test_field_merge_preserves_new_papers_rank_and_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            index_path = os.path.join(
                tmp, "data", "daily", "2026-07-27", "index.json"
            )
            os.makedirs(os.path.dirname(index_path), exist_ok=True)
            with open(index_path, "w", encoding="utf-8") as handle:
                json.dump({
                    "generated_at": "publisher",
                    "papers": [
                        {
                            "arxiv_id": "2607.00001",
                            "rank": 7,
                            "new_field": "keep",
                            "pdf_status": "failed",
                        },
                        {
                            "arxiv_id": "2607.00002",
                            "rank": 8,
                        },
                    ],
                }, handle)

            result = merge_index_paper_fields(
                index_path,
                {"2607.00001": {"pdf_status": "ok"}},
                mode="daily",
                key="2026-07-27",
                lock_dir=os.path.join(tmp, "locks"),
            )
            with open(index_path, encoding="utf-8") as handle:
                persisted = json.load(handle)

        self.assertEqual(result["changed_fields"], 1)
        self.assertEqual(persisted["generated_at"], "publisher")
        self.assertEqual(persisted["papers"][0]["rank"], 7)
        self.assertEqual(persisted["papers"][0]["new_field"], "keep")
        self.assertEqual(persisted["papers"][0]["pdf_status"], "ok")
        self.assertEqual(persisted["papers"][1]["arxiv_id"], "2607.00002")


if __name__ == "__main__":
    unittest.main()
