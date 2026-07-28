import os
import tempfile
import threading
import unittest
from unittest import mock

from paperhub import paper_store, paths


class PaperStoreTest(unittest.TestCase):
    def test_temporary_store_uses_repository_local_lock_directory(self):
        with tempfile.TemporaryDirectory() as root:
            with mock.patch.object(
                paper_store.paths,
                "PAPER_STORE_DIR",
                os.path.join(root, "papers"),
            ):
                self.assertEqual(
                    paper_store._paper_lock_dir(),
                    os.path.join(root, "locks"),
                )

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_store = paths.PAPER_STORE_DIR
        paths.PAPER_STORE_DIR = self.tmp.name

    def tearDown(self):
        paths.PAPER_STORE_DIR = self.old_store
        self.tmp.cleanup()

    @staticmethod
    def _valid_pdf_bytes(extra=1):
        filler = b"x" * (paper_store.MIN_VALID_PDF_BYTES + extra)
        return b"%PDF-1.7\n" + filler + b"\n%%EOF\n"

    def test_raw_and_translated_reads_have_separate_semantics(self):
        payload = {
            "arxiv_id": "2606.00001",
            "title": "Example",
            "title_zh": "",
            "summary_zh": "",
        }
        paper_store.write_raw(payload)

        self.assertEqual(paper_store.read_raw("2606.00001")["title"], "Example")
        self.assertIsNone(paper_store.read_translated("2606.00001"))

        payload["title_zh"] = "示例论文"
        paper_store.write_raw(payload)
        self.assertEqual(paper_store.read_translated("2606.00001")["title_zh"], "示例论文")
        self.assertFalse(paper_store.translation_complete(payload))

        payload["summary_zh"] = "这是完整的中文总结。"
        self.assertTrue(paper_store.translation_complete(payload))
        self.assertFalse(paper_store.translation_complete([payload]))

    def test_pdf_status_update_is_best_effort(self):
        payload = {"arxiv_id": "2606.00002", "title_zh": "已有中文标题"}
        paper_store.write_raw(payload)

        self.assertTrue(paper_store.update_pdf_status("2606.00002", "ok"))
        self.assertEqual(paper_store.read_raw("2606.00002")["pdf_status"], "ok")
        self.assertFalse(paper_store.update_pdf_status("2606.99999", "failed"))

    def test_summary_merge_preserves_concurrent_pdf_quality_state(self):
        paper_store.write_raw({
            "arxiv_id": "2606.00008",
            "pdf_status": "failed",
            "pdf_quality_tainted": True,
            "pdf_quality_taint_reason": "quality.translation_refusal",
        })

        paper_store.merge_raw(
            "2606.00008",
            {
                "title_zh": "中文标题",
                "summary_zh": "中文总结",
            },
        )

        stored = paper_store.read_raw("2606.00008")
        self.assertEqual(stored["title_zh"], "中文标题")
        self.assertEqual(stored["pdf_status"], "failed")
        self.assertTrue(stored["pdf_quality_tainted"])
        self.assertEqual(
            stored["pdf_quality_taint_reason"],
            "quality.translation_refusal",
        )

    def test_concurrent_field_merges_do_not_lose_updates(self):
        arxiv_id = "2606.00009"
        paper_store.write_raw({"arxiv_id": arxiv_id, "base": True})
        start = threading.Barrier(8)
        failures = []

        def worker(position):
            try:
                start.wait(timeout=2)
                paper_store.merge_raw(
                    arxiv_id,
                    {f"field_{position}": position},
                )
            except Exception as exc:
                failures.append(exc)

        threads = [
            threading.Thread(target=worker, args=(position,))
            for position in range(8)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        self.assertEqual(failures, [])
        self.assertFalse(any(thread.is_alive() for thread in threads))
        stored = paper_store.read_raw(arxiv_id)
        for position in range(8):
            self.assertEqual(stored[f"field_{position}"], position)

    def test_pdf_hit_requires_size_header_and_eof(self):
        pdf_path = paper_store.pdf_path("2606.00003")
        with open(pdf_path, "wb") as f:
            f.write(b"x" * paper_store.MIN_VALID_PDF_BYTES)
        self.assertFalse(paper_store.pdf_exists("2606.00003"))
        self.assertIsNone(paper_store.pdf_hit("2606.00003"))

        with open(pdf_path, "wb") as f:
            f.write(b"%PDF-1.7\n" + b"x" * paper_store.MIN_VALID_PDF_BYTES)
        self.assertFalse(paper_store.pdf_exists("2606.00003"))
        self.assertIsNone(paper_store.pdf_hit("2606.00003"))

        with open(pdf_path, "wb") as f:
            f.write(self._valid_pdf_bytes())
        self.assertTrue(paper_store.pdf_exists("2606.00003"))
        self.assertEqual(paper_store.pdf_hit("2606.00003"), pdf_path)

        with open(pdf_path, "wb") as f:
            f.write(b"NOT-PDF\n" + self._valid_pdf_bytes())
        self.assertFalse(paper_store.pdf_exists("2606.00003"))
        self.assertIsNone(paper_store.pdf_hit("2606.00003"))

    def test_save_pdf_copies_into_store(self):
        src = os.path.join(self.tmp.name, "src.pdf")
        with open(src, "wb") as f:
            f.write(self._valid_pdf_bytes())

        paper_store.save_pdf("2606.00004", src)

        dst = paper_store.pdf_path("2606.00004")
        self.assertTrue(os.path.exists(dst))
        self.assertTrue(paper_store.pdf_exists("2606.00004"))

    def test_reconcile_existing_pdf_statuses_only_marks_existing_pdfs_ok(self):
        paper_store.write_raw({"arxiv_id": "2606.00005", "pdf_status": "failed"})
        paper_store.write_raw({"arxiv_id": "2606.00006", "pdf_status": "failed"})
        with open(paper_store.pdf_path("2606.00005"), "wb") as f:
            f.write(self._valid_pdf_bytes())
        with open(paper_store.pdf_path("2606.00006"), "wb") as f:
            f.write(b"%PDF-1.7\n" + b"x" * paper_store.MIN_VALID_PDF_BYTES)

        fixed = paper_store.reconcile_existing_pdf_statuses()

        self.assertEqual(fixed, ["2606.00005"])
        self.assertEqual(paper_store.read_raw("2606.00005")["pdf_status"], "ok")
        self.assertEqual(paper_store.read_raw("2606.00006")["pdf_status"], "failed")

    def test_quality_taint_blocks_legacy_ok_until_new_pdf_is_verified(self):
        paper_store.write_raw({
            "arxiv_id": "2606.00007",
            "pdf_status": "failed",
            "pdf_quality_tainted": True,
            "pdf_quality_taint_reason": "quality.untranslated_prose",
        })

        self.assertTrue(paper_store.pdf_quality_tainted("2606.00007"))
        self.assertFalse(paper_store.update_pdf_status("2606.00007", "ok"))
        self.assertEqual(
            paper_store.read_raw("2606.00007")["pdf_status"],
            "failed",
        )

        self.assertTrue(paper_store.mark_pdf_verified("2606.00007"))
        verified = paper_store.read_raw("2606.00007")
        self.assertEqual(verified["pdf_status"], "ok")
        self.assertNotIn("pdf_quality_tainted", verified)
        self.assertNotIn("pdf_quality_taint_reason", verified)


if __name__ == "__main__":
    unittest.main()
