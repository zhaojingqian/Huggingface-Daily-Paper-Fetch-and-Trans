import os
import tempfile
import unittest

from paperhub import paper_store, paths


class PaperStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_store = paths.PAPER_STORE_DIR
        paths.PAPER_STORE_DIR = self.tmp.name

    def tearDown(self):
        paths.PAPER_STORE_DIR = self.old_store
        self.tmp.cleanup()

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

    def test_pdf_status_update_is_best_effort(self):
        payload = {"arxiv_id": "2606.00002", "title_zh": "已有中文标题"}
        paper_store.write_raw(payload)

        self.assertTrue(paper_store.update_pdf_status("2606.00002", "ok"))
        self.assertEqual(paper_store.read_raw("2606.00002")["pdf_status"], "ok")
        self.assertFalse(paper_store.update_pdf_status("2606.99999", "failed"))

    def test_pdf_hit_uses_existing_size_threshold(self):
        pdf_path = paper_store.pdf_path("2606.00003")
        with open(pdf_path, "wb") as f:
            f.write(b"x" * paper_store.MIN_VALID_PDF_BYTES)
        self.assertFalse(paper_store.pdf_exists("2606.00003"))
        self.assertIsNone(paper_store.pdf_hit("2606.00003"))

        with open(pdf_path, "ab") as f:
            f.write(b"x")
        self.assertTrue(paper_store.pdf_exists("2606.00003"))
        self.assertEqual(paper_store.pdf_hit("2606.00003"), pdf_path)

    def test_save_pdf_copies_into_store(self):
        src = os.path.join(self.tmp.name, "src.pdf")
        with open(src, "wb") as f:
            f.write(b"%PDF" + b"x" * paper_store.MIN_VALID_PDF_BYTES)

        paper_store.save_pdf("2606.00004", src)

        dst = paper_store.pdf_path("2606.00004")
        self.assertTrue(os.path.exists(dst))
        self.assertTrue(paper_store.pdf_exists("2606.00004"))


if __name__ == "__main__":
    unittest.main()
