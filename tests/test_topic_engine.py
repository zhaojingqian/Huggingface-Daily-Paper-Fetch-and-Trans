import os
import tempfile
import unittest
from unittest.mock import patch

from paperhub import topic_store
from topic_engine import (
    build_terms_prompt,
    freshness_score,
    generate_terms,
    rank_candidates,
    relevance_score,
    repair_topic,
    retry_topic_pdf,
    topic_repair_targets,
)


class TopicEngineTest(unittest.TestCase):
    def with_temp_topics(self):
        return tempfile.TemporaryDirectory()

    def set_temp_topic_dir(self, tmp):
        self._old_topic_dir = topic_store.TOPIC_DIR
        self._old_topics_file = topic_store.TOPICS_FILE
        topic_store.TOPIC_DIR = tmp
        topic_store.TOPICS_FILE = os.path.join(tmp, "topics.json")

    def restore_topic_dir(self):
        topic_store.TOPIC_DIR = self._old_topic_dir
        topic_store.TOPICS_FILE = self._old_topics_file

    def profile(self):
        return {
            "slug": "opd",
            "query": "opd",
            "generated_terms": {
                "must": ["on-policy distillation"],
                "should": ["policy distillation", "student policy"],
                "negative": ["outpatient department"],
            },
            "categories": ["cs.AI", "cs.LG", "stat.ML"],
            "weights": {"relevance": 0.45, "freshness": 0.30, "votes": 0.25},
        }

    def test_slugify_keeps_safe_topic_ids(self):
        self.assertEqual(topic_store.slugify("On Policy Distillation!"), "on-policy-distillation")
        self.assertEqual(topic_store.slugify("OPD_v2"), "opd_v2")

    def test_topic_profile_keeps_optional_display_name(self):
        profile = topic_store.normalize_profile({
            "query": "opd",
            "display_name": "  OPD   策略蒸馏  ",
        })
        self.assertEqual(profile["display_name"], "OPD 策略蒸馏")
        self.assertEqual(profile["query"], "opd")

    def test_relevance_filters_negative_terms(self):
        good = {
            "title": "On-Policy Distillation for Large Language Models",
            "abstract": "We study teacher and student policy optimization.",
            "categories": ["cs.LG"],
        }
        bad = {
            "title": "OPD workflow in an outpatient department",
            "abstract": "Hospital scheduling.",
            "categories": ["cs.LG"],
        }
        self.assertGreater(relevance_score(self.profile(), good), 0.2)
        self.assertEqual(relevance_score(self.profile(), bad), 0.0)

    def test_freshness_decays_over_window(self):
        self.assertGreater(
            freshness_score({"submitted": "2026-07-04"}, "2026-07-04"),
            freshness_score({"submitted": "2026-06-20"}, "2026-07-04"),
        )

    def test_rank_candidates_respects_seen_unless_forced(self):
        candidates = [
            {
                "arxiv_id": "2607.00001",
                "title": "On-Policy Distillation for Reasoning",
                "abstract": "A student policy learns from a teacher policy.",
                "submitted": "2026-07-04",
                "categories": ["cs.LG"],
            },
            {
                "arxiv_id": "2607.00002",
                "title": "Policy Distillation for Agents",
                "abstract": "On-policy training with stronger teachers.",
                "submitted": "2026-07-03",
                "categories": ["cs.AI"],
            },
        ]
        ranked = rank_candidates(
            self.profile(),
            candidates,
            votes_by_id={"2607.00001": 5, "2607.00002": 20},
            seen_ids={"2607.00001"},
            key="2026-07-04",
        )
        self.assertEqual([p["arxiv_id"] for p in ranked], ["2607.00002"])

        forced = rank_candidates(
            self.profile(),
            candidates,
            votes_by_id={"2607.00001": 5, "2607.00002": 20},
            seen_ids={"2607.00001"},
            key="2026-07-04",
            force=True,
        )
        self.assertIn("2607.00001", [p["arxiv_id"] for p in forced])

    def test_known_opd_hint_is_merged_with_llm_terms(self):
        raw = (
            '{"must":["openable part detection"],'
            '"should":["openable part detection for robotics","articulated object part detection",'
            '"openable part motion prediction","policy optimization distillation"],'
            '"negative":[]}'
        )
        with patch("topic_engine._call_topic_llm", return_value=raw):
            terms = generate_terms("opd")
        self.assertIn("on-policy distillation", terms["must"])
        self.assertNotIn("openable part detection", terms["must"])
        self.assertFalse(any("openable part detection" in x.lower() for x in terms["should"]))
        self.assertFalse(any("articulated object" in x.lower() for x in terms["should"]))
        self.assertFalse(any("openable part motion" in x.lower() for x in terms["should"]))
        self.assertIn("openable part detection", terms["negative"])
        self.assertIn("language agent policy distillation", terms["should"])

    def test_terms_prompt_constrains_llm_to_ai_ml_cs_and_diverse_terms(self):
        prompt = build_terms_prompt("opd")
        self.assertIn("用户输入一定属于 AI", prompt)
        self.assertIn("cs.AI", prompt)
        self.assertIn("stat.ML", prompt)
        self.assertIn("医学、光学、电力、行政、商业、心理学", prompt)
        self.assertIn("8-16 个多元检索短语", prompt)
        self.assertIn("避免只输出同一短语", prompt)

    def test_terms_prompt_can_include_local_topic_hint(self):
        prompt = build_terms_prompt(
            "opd",
            {"must": ["on-policy distillation"], "should": ["policy distillation"], "negative": ["openable part"]},
        )
        self.assertIn("本地语义偏好", prompt)
        self.assertIn("preferred_terms", prompt)
        self.assertIn("on-policy distillation", prompt)
        self.assertIn("openable part", prompt)

    def test_generated_terms_remove_self_declared_negative_conflicts(self):
        raw = (
            '{"must":["abc","medical abc"],'
            '"should":["medical abc detection","abc planning","abc planning"],'
            '"negative":["medical abc"]}'
        )
        with patch("topic_engine._call_topic_llm", return_value=raw):
            terms = generate_terms("abc")
        self.assertIn("abc", terms["must"])
        self.assertNotIn("medical abc", terms["must"])
        self.assertNotIn("medical abc detection", terms["should"])
        self.assertEqual(terms["should"], ["abc planning"])

    def test_topic_repair_targets_accept_slug_date_key(self):
        with self.with_temp_topics() as tmp:
            self.set_temp_topic_dir(tmp)
            try:
                topic_store.upsert_topic({"slug": "opd", "query": "opd"})
                topic_store.save_index("opd", "2026-07-05", [{"arxiv_id": "2607.00001", "rank": 1}])
                targets = topic_repair_targets(key="opd/2026-07-05", scan_all=True)
                self.assertEqual([(p["slug"], k) for p, k in targets], [("opd", "2026-07-05")])
            finally:
                self.restore_topic_dir()

    def test_topic_repair_targets_days_zero_scans_nothing(self):
        with self.with_temp_topics() as tmp:
            self.set_temp_topic_dir(tmp)
            try:
                topic_store.upsert_topic({"slug": "opd", "query": "opd"})
                topic_store.save_index("opd", "2026-07-05", [{"arxiv_id": "2607.00001", "rank": 1}])
                self.assertEqual(topic_repair_targets(days=0, scan_all=False), [])
            finally:
                self.restore_topic_dir()

    def test_retry_topic_pdf_reuses_shared_retry_helper_and_writes_index(self):
        with self.with_temp_topics() as tmp:
            self.set_temp_topic_dir(tmp)
            try:
                topic_store.upsert_topic({"slug": "opd", "query": "opd"})
                topic_store.save_index(
                    "opd",
                    "2026-07-05",
                    [{"arxiv_id": "2607.00001", "rank": 1, "pdf_zh_failed": True}],
                )

                def fake_retry(papers, label):
                    self.assertIn("opd/2026-07-05", label)
                    papers[0]["pdf_status"] = "ok"
                    return {"ok": 1, "failed": 0, "changed": True}

                with patch("run_papers.retry_failed_pdf_entries", side_effect=fake_retry):
                    self.assertEqual(retry_topic_pdf(topic="opd", key="2026-07-05"), 1)
                idx = topic_store.load_index("opd", "2026-07-05")
                self.assertEqual(idx["papers"][0]["pdf_status"], "ok")
            finally:
                self.restore_topic_dir()

    def test_repair_topic_retries_missing_summary_translation(self):
        with self.with_temp_topics() as tmp:
            self.set_temp_topic_dir(tmp)
            try:
                topic_store.upsert_topic({"slug": "opd", "query": "opd"})
                topic_store.save_index("opd", "2026-07-05", [{"arxiv_id": "2607.00001", "rank": 1}])
                translated = {"title_zh": "中文标题", "summary_zh": "中文总结"}
                with patch("topic_engine.paper_store.read_raw", return_value={}), \
                     patch("translate_arxiv.load_api_config", return_value={}), \
                     patch("translate_arxiv.translate_and_save", return_value=translated) as translate:
                    self.assertEqual(repair_topic(topic="opd", key="2026-07-05"), 1)
                translate.assert_called_once()
                self.assertEqual(translate.call_args[1]["week_str"], "topic/opd")
            finally:
                self.restore_topic_dir()


if __name__ == "__main__":
    unittest.main()
