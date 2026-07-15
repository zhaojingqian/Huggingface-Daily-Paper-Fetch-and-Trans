import json
import os
import tempfile
import unittest

from paperhub.audit import audit_repository


class ProjectAuditTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
