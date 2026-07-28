import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest import mock

from paperhub.audit import audit_repository
from paperhub.pdf_text_quality import PdfTextQualityError
from scripts import audit_project


class ProjectAuditTest(unittest.TestCase):
    @staticmethod
    def _write_translated_store(paper_dir, arxiv_id):
        with open(
            os.path.join(paper_dir, arxiv_id + ".json"),
            "w",
            encoding="utf-8",
        ) as handle:
            json.dump({
                "title_zh": "标题",
                "summary_zh": "摘要",
                "pdf_status": "ok",
            }, handle)

    @staticmethod
    def _write_valid_pdf(paper_dir, arxiv_id):
        with open(
            os.path.join(paper_dir, arxiv_id + "_zh.pdf"),
            "wb",
        ) as handle:
            handle.write(b"%PDF-1.7\n" + b"x" * 20_000 + b"\n%%EOF\n")

    def test_audit_reports_cross_store_inconsistencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = os.path.join(tmp, "data")
            logs = os.path.join(tmp, "logs")
            index_dir = os.path.join(data, "daily", "2026-07-15")
            paper_dir = os.path.join(data, "papers")
            os.makedirs(index_dir)
            os.makedirs(paper_dir)
            with open(os.path.join(index_dir, "index.json"), "w", encoding="utf-8") as handle:
                json.dump(
                    {"mode": "daily", "total": 2, "papers": [
                        {"arxiv_id": "2607.00001", "pdf_status": "ok"},
                        {"arxiv_id": "2607.00002", "pdf_status": "failed"},
                    ]},
                    handle,
                )
            with open(os.path.join(paper_dir, "2607.00001.json"), "w", encoding="utf-8") as handle:
                json.dump({"title_zh": "标题", "summary_zh": "摘要"}, handle)

            report = audit_repository(data, logs)

            self.assertEqual(report["unique_referenced_papers"], 2)
            self.assertEqual(report["issue_counts"]["missing_store"], 1)
            self.assertEqual(report["issue_counts"]["ok_missing_pdf"], 1)
            self.assertEqual(report["issue_counts"]["failed_status"], 1)

    def test_audit_rejects_large_truncated_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = os.path.join(tmp, "data")
            logs = os.path.join(tmp, "logs")
            index_dir = os.path.join(data, "daily", "2026-07-16")
            paper_dir = os.path.join(data, "papers")
            os.makedirs(index_dir)
            os.makedirs(paper_dir)
            arxiv_id = "2607.00003"
            with open(
                os.path.join(index_dir, "index.json"),
                "w",
                encoding="utf-8",
            ) as handle:
                json.dump(
                    {
                        "mode": "daily",
                        "total": 1,
                        "papers": [
                            {"arxiv_id": arxiv_id, "pdf_status": "ok"}
                        ],
                    },
                    handle,
                )
            self._write_translated_store(paper_dir, arxiv_id)
            pdf_path = os.path.join(paper_dir, arxiv_id + "_zh.pdf")
            with open(pdf_path, "wb") as handle:
                handle.write(b"%PDF-1.7\n" + b"x" * 20_000)

            report = audit_repository(data, logs)
            self.assertEqual(report["issue_counts"]["ok_missing_pdf"], 1)

            with open(pdf_path, "ab") as handle:
                handle.write(b"\n%%EOF\n")
            report = audit_repository(data, logs)
            self.assertEqual(report["issue_counts"]["ok_missing_pdf"], 0)

    def test_audit_reports_persistent_quality_taint_even_if_status_is_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = os.path.join(tmp, "data")
            logs = os.path.join(tmp, "logs")
            index_dir = os.path.join(data, "daily", "2026-07-16")
            paper_dir = os.path.join(data, "papers")
            os.makedirs(index_dir)
            os.makedirs(paper_dir)
            arxiv_id = "2607.00003"
            with open(
                os.path.join(index_dir, "index.json"), "w", encoding="utf-8"
            ) as handle:
                json.dump({
                    "mode": "daily",
                    "total": 1,
                    "papers": [{"arxiv_id": arxiv_id, "pdf_status": "ok"}],
                }, handle)
            self._write_translated_store(paper_dir, arxiv_id)
            store_path = os.path.join(paper_dir, arxiv_id + ".json")
            with open(store_path, encoding="utf-8") as handle:
                store = json.load(handle)
            store.update({
                "pdf_quality_tainted": True,
                "pdf_quality_taint_reason": "quality.untranslated_prose",
            })
            with open(store_path, "w", encoding="utf-8") as handle:
                json.dump(store, handle)
            self._write_valid_pdf(paper_dir, arxiv_id)

            report = audit_repository(data, logs)

        self.assertEqual(report["issue_counts"]["quality_tainted"], 1)
        self.assertEqual(
            report["issues"]["quality_tainted"][0]["reason"],
            "quality.untranslated_prose",
        )

    def test_audit_and_strict_cli_reject_untranslated_tex(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = os.path.join(tmp, "data")
            logs = os.path.join(tmp, "logs")
            index_dir = os.path.join(data, "daily", "2026-07-17")
            paper_dir = os.path.join(data, "papers")
            tex_dir = os.path.join(data, "tex_backup")
            os.makedirs(index_dir)
            os.makedirs(paper_dir)
            os.makedirs(tex_dir)
            os.makedirs(logs)
            arxiv_id = "2607.00004"
            with open(
                os.path.join(index_dir, "index.json"),
                "w",
                encoding="utf-8",
            ) as handle:
                json.dump(
                    {
                        "mode": "daily",
                        "total": 1,
                        "papers": [
                            {"arxiv_id": arxiv_id, "pdf_status": "ok"}
                        ],
                    },
                    handle,
                )
            self._write_translated_store(paper_dir, arxiv_id)
            with open(
                os.path.join(paper_dir, arxiv_id + "_zh.pdf"),
                "wb",
            ) as handle:
                handle.write(b"%PDF-1.7\n" + b"x" * 20_000 + b"\n%%EOF\n")
            prose = (
                "This ordinary prose line remains untranslated and contains "
                "enough English words for reliable repository quality detection."
            )
            with open(
                os.path.join(
                    tex_dir,
                    arxiv_id + "_merge_translate_zh.tex",
                ),
                "w",
                encoding="utf-8",
            ) as handle:
                handle.write(
                    "\\begin{document}\n"
                    + "\n".join([prose] * 21)
                    + "\n\\end{document}\n"
                )

            report = audit_repository(data, logs)

            self.assertEqual(report["quality_tex_scanned"], 1)
            self.assertEqual(
                report["issue_counts"]["quality_untranslated_prose"],
                1,
            )
            issue = report["issues"]["quality_untranslated_prose"][0]
            self.assertEqual(issue["arxiv_id"], arxiv_id)
            self.assertEqual(issue["cjk_pct"], 0.0)
            self.assertEqual(issue["long_english_lines"], 21)

            with mock.patch.object(audit_project, "DATA_DIR", data), \
                 mock.patch.object(audit_project, "LOGS_DIR", logs), \
                 mock.patch("sys.argv", ["audit_project.py", "--strict"]), \
                 redirect_stdout(StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    audit_project.main()
            self.assertEqual(raised.exception.code, 1)

    def test_audit_survives_semantically_malformed_indexes(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = os.path.join(tmp, "data")
            logs = os.path.join(tmp, "logs")
            malformed = [
                ("daily", "2026-01-01", []),
                ("weekly", "2026-W01", {"papers": {}}),
                ("monthly", "2026-01", {"papers": ["not-an-object"]}),
            ]
            for mode, key, payload in malformed:
                directory = os.path.join(data, mode, key)
                os.makedirs(directory)
                with open(
                    os.path.join(directory, "index.json"),
                    "w",
                    encoding="utf-8",
                ) as handle:
                    json.dump(payload, handle)

            report = audit_repository(data, logs)

        self.assertEqual(report["index_files"], 3)
        self.assertEqual(report["issue_counts"]["bad_json"], 3)
        self.assertEqual(report["unique_referenced_papers"], 0)

    def test_audit_reports_mode_fallback_store_mismatch_and_quality_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = os.path.join(tmp, "data")
            logs = os.path.join(tmp, "logs")
            index_dir = os.path.join(data, "manual", "2026-07-18")
            paper_dir = os.path.join(data, "papers")
            tex_dir = os.path.join(data, "tex_backup")
            os.makedirs(index_dir)
            os.makedirs(paper_dir)
            os.makedirs(tex_dir)
            ids = ("2607.00005", "2607.00006")
            with open(
                os.path.join(index_dir, "index.json"),
                "w",
                encoding="utf-8",
            ) as handle:
                json.dump({
                    "total": 2,
                    "papers": [
                        {"arxiv_id": ids[0], "pdf_status": "ok"},
                        {"arxiv_id": ids[1], "pdf_status": "ok"},
                    ],
                }, handle)
            for arxiv_id in ids:
                self._write_translated_store(paper_dir, arxiv_id)
                self._write_valid_pdf(paper_dir, arxiv_id)
            first_store = os.path.join(paper_dir, ids[0] + ".json")
            with open(first_store, encoding="utf-8") as handle:
                payload = json.load(handle)
            payload["pdf_status"] = "failed"
            with open(first_store, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            with open(
                os.path.join(
                    tex_dir,
                    ids[0] + "_merge_translate_zh.tex",
                ),
                "w",
                encoding="utf-8",
            ) as handle:
                handle.write(
                    "\\begin{document}\n"
                    "这是已经完整翻译的中文正文内容，不应触发英文质量门禁。\n"
                    "\\end{document}\n"
                )

            report = audit_repository(data, logs)

        self.assertEqual(report["entries_by_mode"], {"manual": 2})
        self.assertEqual(
            report["issue_counts"]["store_pdf_status_mismatch"],
            1,
        )
        self.assertEqual(report["quality_coverage"], {
            "valid_referenced_pdfs": 2,
            "valid_pdfs_with_tex": 1,
            "valid_pdfs_without_tex": 1,
            "valid_pdf_tex_coverage_pct": 50.0,
            "valid_pdfs_with_pdf_text_scan": 0,
            "valid_pdfs_quality_scanned": 1,
            "valid_pdfs_unscanned": 1,
            "valid_pdf_quality_coverage_pct": 50.0,
        })
        self.assertEqual(report["quality_unscanned_pdf_ids"], [ids[1]])

    def test_pdf_text_scan_is_explicit_and_closes_no_tex_coverage_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = os.path.join(tmp, "data")
            logs = os.path.join(tmp, "logs")
            index_dir = os.path.join(data, "daily", "2026-07-19")
            paper_dir = os.path.join(data, "papers")
            os.makedirs(index_dir)
            os.makedirs(paper_dir)
            arxiv_id = "2607.00009"
            with open(
                os.path.join(index_dir, "index.json"),
                "w",
                encoding="utf-8",
            ) as handle:
                json.dump({
                    "mode": "daily",
                    "total": 1,
                    "papers": [{
                        "arxiv_id": arxiv_id,
                        "pdf_status": "ok",
                    }],
                }, handle)
            self._write_translated_store(paper_dir, arxiv_id)
            self._write_valid_pdf(paper_dir, arxiv_id)
            quality = {
                "pages_scanned": 10,
                "page_limit_reached": False,
                "untranslated_prose": True,
                "cjk_pct": 8.0,
                "analyzable_pages": 8,
                "english_dominant_pages": 6,
                "longest_english_page_run": 6,
                "reference_pages": 2,
                "source_data_pages": 0,
                "structural_pages": 0,
                "translation_refusal_pages": 1,
                "translation_refusal_page_numbers": [7],
                "translation_refusal_samples": [{
                    "page": 7,
                    "text": "抱歉，我无法查看外部文件内容。",
                }],
                "samples": [{
                    "page": 2,
                    "cjk_pct": 0.0,
                    "english_words": 150,
                    "text": "Untranslated paper prose sample.",
                }],
            }

            with mock.patch(
                "paperhub.audit.analyze_pdf",
                side_effect=[
                    PdfTextQualityError("pdftotext timed out after 120s"),
                    quality,
                ],
            ) as analyze:
                default_report = audit_repository(data, logs)
                analyze.assert_not_called()
                scanned_report = audit_repository(
                    data,
                    logs,
                    scan_pdf_text=True,
                    pdf_text_workers=1,
                    pdf_text_cache=False,
                )

        self.assertEqual(analyze.call_count, 2)
        self.assertEqual(
            default_report["quality_unscanned_pdf_ids"],
            [arxiv_id],
        )
        self.assertEqual(scanned_report["quality_unscanned_pdf_ids"], [])
        self.assertEqual(
            scanned_report["issue_counts"]["quality_pdf_untranslated_prose"],
            1,
        )
        self.assertEqual(
            scanned_report["issue_counts"][
                "quality_pdf_translation_refusal"
            ],
            1,
        )
        self.assertEqual(
            scanned_report["quality_coverage"][
                "valid_pdf_quality_coverage_pct"
            ],
            100.0,
        )
        self.assertEqual(
            scanned_report["quality_pdf_text_scan"]["scanned"],
            1,
        )
        self.assertEqual(
            scanned_report["quality_pdf_text_scan"]["retried_timeouts"],
            1,
        )
        self.assertEqual(
            scanned_report["quality_pdf_text_scan"][
                "recovered_after_retry"
            ],
            1,
        )

    def test_inconclusive_pdf_text_does_not_close_quality_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = os.path.join(tmp, "data")
            logs = os.path.join(tmp, "logs")
            index_dir = os.path.join(data, "daily", "2026-07-20")
            paper_dir = os.path.join(data, "papers")
            os.makedirs(index_dir)
            os.makedirs(paper_dir)
            arxiv_id = "2607.00010"
            with open(
                os.path.join(index_dir, "index.json"),
                "w",
                encoding="utf-8",
            ) as handle:
                json.dump({
                    "mode": "daily",
                    "total": 1,
                    "papers": [{
                        "arxiv_id": arxiv_id,
                        "pdf_status": "ok",
                    }],
                }, handle)
            self._write_translated_store(paper_dir, arxiv_id)
            self._write_valid_pdf(paper_dir, arxiv_id)
            quality = {
                "pages_scanned": 5,
                "analyzable_pages": 0,
                "page_limit_reached": False,
                "untranslated_prose": False,
            }

            with mock.patch(
                "paperhub.audit.analyze_pdf",
                return_value=quality,
            ):
                report = audit_repository(
                    data,
                    logs,
                    scan_pdf_text=True,
                    pdf_text_workers=1,
                    pdf_text_cache=False,
                )

        self.assertEqual(report["quality_unscanned_pdf_ids"], [arxiv_id])
        self.assertEqual(
            report["issue_counts"]["quality_pdf_inconclusive"],
            1,
        )
        self.assertEqual(
            report["quality_pdf_text_scan"]["successful_extractions"],
            1,
        )
        self.assertEqual(report["quality_pdf_text_scan"]["scanned"], 0)
        self.assertEqual(report["quality_pdf_text_scan"]["inconclusive"], 1)


if __name__ == "__main__":
    unittest.main()
