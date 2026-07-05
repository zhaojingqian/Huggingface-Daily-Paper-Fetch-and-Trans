import unittest
from unittest.mock import patch

from paperhub import topic_store
from topic_engine import build_terms_prompt, freshness_score, generate_terms, rank_candidates, relevance_score


class TopicEngineTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
