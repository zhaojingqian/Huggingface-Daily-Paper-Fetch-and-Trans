import unittest
from unittest.mock import patch

from paperhub import topic_store
from topic_engine import freshness_score, generate_terms, rank_candidates, relevance_score


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
        with patch("topic_engine._call_topic_llm", return_value='{"must":["openable part detection"],"should":[],"negative":[]}'):
            terms = generate_terms("opd")
        self.assertIn("on-policy distillation", terms["must"])
        self.assertNotIn("openable part detection", terms["must"])
        self.assertIn("openable part detection", terms["negative"])


if __name__ == "__main__":
    unittest.main()
