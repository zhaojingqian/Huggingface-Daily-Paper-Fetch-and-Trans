import json
import os
import tempfile
import unittest
from unittest.mock import patch

from paperhub.json_io import read_json
from scripts.queue_quality_repairs import (
    QueuePreflightError,
    find_quality_failures,
    queue_quality_failures,
)


class QueueQualityRepairsTest(unittest.TestCase):
    def test_detects_and_queues_referenced_untranslated_tex(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = os.path.join(tmp, "data")
            logs_dir = os.path.join(tmp, "logs")
            index_path = os.path.join(data_dir, "daily", "2026-01-01", "index.json")
            store_path = os.path.join(data_dir, "papers", "2601.00001.json")
            tex_path = os.path.join(
                data_dir,
                "tex_backup",
                "2601.00001_merge_translate_zh.tex",
            )
            os.makedirs(os.path.dirname(index_path), exist_ok=True)
            os.makedirs(os.path.dirname(store_path), exist_ok=True)
            os.makedirs(os.path.dirname(tex_path), exist_ok=True)
            with open(index_path, "w", encoding="utf-8") as handle:
                json.dump({
                    "papers": [{
                        "arxiv_id": "2601.00001",
                        "pdf_status": "ok",
                    }],
                }, handle)
            with open(store_path, "w", encoding="utf-8") as handle:
                json.dump({
                    "arxiv_id": "2601.00001",
                    "pdf_status": "ok",
                }, handle)
            english = (
                "This substantial English paragraph contains enough ordinary "
                "academic prose to prove that the translated document remains "
                "mostly untranslated and must be queued for another pass."
            )
            with open(tex_path, "w", encoding="utf-8") as handle:
                handle.write("\\begin{document}\n")
                # The shared production predicate also rejects a short paper
                # with ten untranslated prose lines and very low CJK coverage;
                # the queue must not retain its former independent 21-line rule.
                for _ in range(10):
                    handle.write(english + "\n")
                handle.write("\\end{document}\n")

            failures = find_quality_failures(data_dir)
            result = queue_quality_failures(data_dir, logs_dir, failures)

            self.assertEqual([item["arxiv_id"] for item in failures], ["2601.00001"])
            self.assertEqual(result["queued"], ["2601.00001"])
            self.assertEqual(
                read_json(index_path, {})["papers"][0]["pdf_status"],
                "failed",
            )
            queued_store = read_json(store_path, {})
            self.assertEqual(queued_store["pdf_status"], "failed")
            self.assertTrue(queued_store["pdf_quality_tainted"])
            self.assertEqual(
                queued_store["pdf_quality_taint_reason"],
                "quality.untranslated_prose",
            )
            sidecar = read_json(
                os.path.join(logs_dir, "pdf_errors", "2601.00001.json"),
                {},
            )
            self.assertEqual(sidecar["category"], "quality.untranslated_prose")
            self.assertEqual(sidecar["retry_strategy"], "retry_translation")

    def test_pdf_text_scan_is_explicit_cached_and_queues_stable_categories(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = os.path.join(tmp, "data")
            logs_dir = os.path.join(tmp, "logs")
            daily_index = os.path.join(
                data_dir,
                "daily",
                "2026-01-01",
                "index.json",
            )
            topic_index = os.path.join(
                data_dir,
                "topic",
                "agents",
                "2026-01-01",
                "index.json",
            )
            paper_ids = (
                "2601.00011",
                "2601.00012",
                "2601.00013",
            )
            os.makedirs(os.path.dirname(daily_index), exist_ok=True)
            os.makedirs(os.path.dirname(topic_index), exist_ok=True)
            os.makedirs(os.path.join(data_dir, "papers"), exist_ok=True)
            with open(daily_index, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "papers": [
                            {"arxiv_id": arxiv_id, "pdf_status": "ok"}
                            for arxiv_id in paper_ids
                        ],
                    },
                    handle,
                )
            with open(topic_index, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "papers": [{
                            "arxiv_id": paper_ids[0],
                            "pdf_status": "ok",
                        }],
                    },
                    handle,
                )
            for arxiv_id in paper_ids:
                with open(
                    os.path.join(data_dir, "papers", f"{arxiv_id}.json"),
                    "w",
                    encoding="utf-8",
                ) as handle:
                    json.dump(
                        {"arxiv_id": arxiv_id, "pdf_status": "ok"},
                        handle,
                    )

            audit_result = {
                "issues": {
                    "quality_pdf_untranslated_prose": [{
                        "arxiv_id": paper_ids[0],
                        "pdf": f"/pdf/{paper_ids[0]}_zh.pdf",
                        "cjk_pct": 4.2,
                        "analyzable_pages": 18,
                        "english_dominant_pages": 14,
                        "longest_english_page_run": 9,
                        "reference_pages": [19],
                        "source_data_pages": [],
                        "structural_pages": [1],
                        "samples": ["sustained sample"],
                    }],
                    "quality_pdf_partial_untranslated_prose": [{
                        "arxiv_id": paper_ids[1],
                        "pdf": f"/pdf/{paper_ids[1]}_zh.pdf",
                        "pages": [15],
                        "samples": ["partial sample"],
                    }],
                    "quality_pdf_translation_refusal": [{
                        "arxiv_id": paper_ids[2],
                        "pdf": f"/pdf/{paper_ids[2]}_zh.pdf",
                        "pages": [43],
                        "samples": ["refusal sample"],
                    }],
                },
            }
            with patch(
                "scripts.queue_quality_repairs.audit_repository",
                return_value=audit_result,
            ) as audit:
                self.assertEqual(find_quality_failures(data_dir), [])
                audit.assert_not_called()
                failures = find_quality_failures(
                    data_dir,
                    logs_dir,
                    scan_pdf_text=True,
                )

            self.assertEqual(
                {item["category"] for item in failures},
                {
                    "quality.pdf_sustained_untranslated",
                    "quality.pdf_partial_untranslated",
                    "quality.translation_refusal",
                },
            )
            self.assertTrue(audit.call_args.kwargs["scan_pdf_text"])
            self.assertTrue(audit.call_args.kwargs["pdf_text_cache"])

            result = queue_quality_failures(data_dir, logs_dir, failures)

            self.assertEqual(result["missing_stores"], [])
            self.assertEqual(
                result["queued_by_category"],
                {
                    "quality.pdf_partial_untranslated": 1,
                    "quality.pdf_sustained_untranslated": 1,
                    "quality.translation_refusal": 1,
                },
            )
            self.assertEqual(
                set(result["changed_indexes"]),
                {daily_index, topic_index},
            )
            for index_path in (daily_index, topic_index):
                index = read_json(index_path, {})
                self.assertTrue(
                    all(
                        paper["pdf_status"] == "failed"
                        for paper in index["papers"]
                    )
                )
            for arxiv_id, category in zip(
                paper_ids,
                (
                    "quality.pdf_sustained_untranslated",
                    "quality.pdf_partial_untranslated",
                    "quality.translation_refusal",
                ),
            ):
                store = read_json(
                    os.path.join(data_dir, "papers", f"{arxiv_id}.json"),
                    {},
                )
                self.assertTrue(store["pdf_quality_tainted"])
                self.assertEqual(
                    store["pdf_quality_taint_reason"],
                    category,
                )
                sidecar = read_json(
                    os.path.join(
                        logs_dir,
                        "pdf_errors",
                        f"{arxiv_id}.json",
                    ),
                    {},
                )
                self.assertEqual(sidecar["category"], category)
                self.assertEqual(
                    sidecar["retry_strategy"],
                    "retry_translation",
                )
                self.assertEqual(sidecar["family"], "translation_quality")
                self.assertEqual(
                    sidecar["indexes"],
                    [daily_index, topic_index]
                    if arxiv_id == paper_ids[0]
                    else [daily_index],
                )

    def test_repeated_apply_preserves_stronger_existing_quality_category(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = os.path.join(tmp, "data")
            logs_dir = os.path.join(tmp, "logs")
            arxiv_id = "2601.00021"
            index_path = os.path.join(
                data_dir,
                "daily",
                "2026-01-01",
                "index.json",
            )
            store_path = os.path.join(
                data_dir,
                "papers",
                f"{arxiv_id}.json",
            )
            sidecar_path = os.path.join(
                logs_dir,
                "pdf_errors",
                f"{arxiv_id}.json",
            )
            os.makedirs(os.path.dirname(index_path), exist_ok=True)
            os.makedirs(os.path.dirname(store_path), exist_ok=True)
            os.makedirs(os.path.dirname(sidecar_path), exist_ok=True)
            with open(index_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "papers": [{
                            "arxiv_id": arxiv_id,
                            "pdf_status": "ok",
                        }],
                    },
                    handle,
                )
            with open(store_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "arxiv_id": arxiv_id,
                        "pdf_status": "failed",
                        "pdf_quality_tainted": True,
                        "pdf_quality_taint_reason": (
                            "quality.translation_refusal"
                        ),
                    },
                    handle,
                )
            with open(sidecar_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "arxiv_id": arxiv_id,
                        "category": "quality.translation_refusal",
                        "evidence": "original refusal evidence",
                    },
                    handle,
                )
            lower_priority_tex_failure = {
                "arxiv_id": arxiv_id,
                "cjk_pct": 2.0,
                "long_english_lines": 20,
                "very_long_english_lines": 10,
                "prose_lines": 20,
                "tex_path": "/tmp/example.tex",
                "indexes": [index_path],
            }

            result = queue_quality_failures(
                data_dir,
                logs_dir,
                [lower_priority_tex_failure],
            )

            self.assertEqual(
                result["queued_by_category"],
                {"quality.translation_refusal": 1},
            )
            self.assertEqual(result["preserved_existing"], [arxiv_id])
            self.assertEqual(
                read_json(store_path, {})["pdf_quality_taint_reason"],
                "quality.translation_refusal",
            )
            self.assertEqual(
                read_json(sidecar_path, {})["evidence"],
                "original refusal evidence",
            )

    def test_apply_preflight_rejects_bad_index_before_any_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = os.path.join(tmp, "data")
            logs_dir = os.path.join(tmp, "logs")
            arxiv_id = "2601.00022"
            index_path = os.path.join(
                data_dir,
                "daily",
                "2026-01-01",
                "index.json",
            )
            bad_index_path = os.path.join(
                data_dir,
                "topic",
                "broken",
                "index.json",
            )
            store_path = os.path.join(
                data_dir,
                "papers",
                f"{arxiv_id}.json",
            )
            os.makedirs(os.path.dirname(index_path), exist_ok=True)
            os.makedirs(os.path.dirname(bad_index_path), exist_ok=True)
            os.makedirs(os.path.dirname(store_path), exist_ok=True)
            with open(index_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "papers": [{
                            "arxiv_id": arxiv_id,
                            "pdf_status": "ok",
                        }],
                    },
                    handle,
                )
            with open(bad_index_path, "w", encoding="utf-8") as handle:
                handle.write("{not-json")
            with open(store_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {"arxiv_id": arxiv_id, "pdf_status": "ok"},
                    handle,
                )
            failure = {
                "arxiv_id": arxiv_id,
                "category": "quality.pdf_partial_untranslated",
                "pages": [3],
                "samples": ["sample"],
                "indexes": [index_path],
            }

            with self.assertRaises(QueuePreflightError):
                queue_quality_failures(
                    data_dir,
                    logs_dir,
                    [failure],
                )

            self.assertEqual(
                read_json(index_path, {})["papers"][0]["pdf_status"],
                "ok",
            )
            self.assertEqual(
                read_json(store_path, {})["pdf_status"],
                "ok",
            )
            self.assertFalse(
                os.path.exists(
                    os.path.join(
                        logs_dir,
                        "pdf_errors",
                        f"{arxiv_id}.json",
                    )
                )
            )

    def test_apply_preflight_rejects_unknown_category_before_any_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = os.path.join(tmp, "data")
            logs_dir = os.path.join(tmp, "logs")
            arxiv_id = "2601.00023"
            index_path = os.path.join(
                data_dir,
                "daily",
                "2026-01-01",
                "index.json",
            )
            store_path = os.path.join(
                data_dir,
                "papers",
                f"{arxiv_id}.json",
            )
            os.makedirs(os.path.dirname(index_path), exist_ok=True)
            os.makedirs(os.path.dirname(store_path), exist_ok=True)
            with open(index_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "papers": [{
                            "arxiv_id": arxiv_id,
                            "pdf_status": "ok",
                        }],
                    },
                    handle,
                )
            with open(store_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {"arxiv_id": arxiv_id, "pdf_status": "ok"},
                    handle,
                )

            with self.assertRaises(QueuePreflightError):
                queue_quality_failures(
                    data_dir,
                    logs_dir,
                    [{
                        "arxiv_id": arxiv_id,
                        "category": "quality.not_registered",
                    }],
                )

            self.assertEqual(
                read_json(index_path, {})["papers"][0]["pdf_status"],
                "ok",
            )
            self.assertEqual(
                read_json(store_path, {})["pdf_status"],
                "ok",
            )

    def test_apply_preflight_requires_store_before_index_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = os.path.join(tmp, "data")
            logs_dir = os.path.join(tmp, "logs")
            arxiv_id = "2601.00024"
            index_path = os.path.join(
                data_dir,
                "daily",
                "2026-01-01",
                "index.json",
            )
            os.makedirs(os.path.dirname(index_path), exist_ok=True)
            with open(index_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "papers": [{
                            "arxiv_id": arxiv_id,
                            "pdf_status": "ok",
                        }],
                    },
                    handle,
                )

            with self.assertRaises(QueuePreflightError):
                queue_quality_failures(
                    data_dir,
                    logs_dir,
                    [{
                        "arxiv_id": arxiv_id,
                        "category": (
                            "quality.pdf_sustained_untranslated"
                        ),
                        "cjk_pct": 3.0,
                    }],
                )

            self.assertEqual(
                read_json(index_path, {})["papers"][0]["pdf_status"],
                "ok",
            )


if __name__ == "__main__":
    unittest.main()
