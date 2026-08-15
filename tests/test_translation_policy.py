import unittest

from paperhub.translation_policy import (
    configured_worker_count,
    retry_worker_count,
    translation_chunk_limit,
)


class TranslationPolicyTest(unittest.TestCase):
    def test_ordinary_prose_gets_context_preserving_cap(self):
        self.assertEqual(
            translation_chunk_limit("A plain paragraph without LaTeX commands."),
            2400,
        )

    def test_structure_dense_prose_gets_smaller_cap(self):
        text = r"Prior work \cite{a}, \citep{b}, and \citet{c} motivates this."
        self.assertEqual(translation_chunk_limit(text), 1500)

    def test_custom_small_cap_remains_authoritative(self):
        text = r"Prior work \cite{a}, \citep{b}, and \citet{c} motivates this."
        self.assertEqual(translation_chunk_limit(text, 60), 60)

    def test_first_pass_workers_are_bounded_by_operator_setting(self):
        self.assertEqual(
            configured_worker_count({"PAPER_TRANS_LLM_WORKERS": "75"}),
            50,
        )
        self.assertEqual(
            configured_worker_count({"PAPER_TRANS_LLM_WORKERS": "bad"}),
            50,
        )

    def test_retry_workers_are_concurrent_but_bounded(self):
        self.assertEqual(retry_worker_count(1, 50), 1)
        self.assertEqual(retry_worker_count(8, 50), 8)
        self.assertEqual(retry_worker_count(100, 50), 16)


if __name__ == "__main__":
    unittest.main()
