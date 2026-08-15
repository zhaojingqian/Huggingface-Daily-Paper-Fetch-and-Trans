import json
import os
import tempfile
import unittest
from unittest import mock

from paperhub import translation_runtime


class TranslationRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(
            os.environ,
            {
                "PAPER_TRANS_EFFECTIVE_MODEL": "deepseek-v4-flash-0731",
            },
            clear=False,
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_recovery_keeps_only_valid_responses(self):
        sources = [
            "This is a translated paragraph with enough words to validate.",
            "Another English paragraph that still needs translation.",
            "A third English paragraph that still needs translation.",
        ]
        payload = [
            "prompt-0",
            "这是一段已经翻译完成的中文正文。",
            "prompt-1",
            "",
            "prompt-2",
            "insufficient_user_quota: API balance is insufficient",
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "paper.json")
            with mock.patch.dict(
                os.environ,
                {"PAPER_TRANS_RECOVERY_FILE": path},
                clear=False,
            ):
                self.assertTrue(
                    translation_runtime._save_translation_recovery(
                        sources,
                        payload,
                    )
                )
                self.assertEqual(
                    translation_runtime._load_translation_recovery(sources),
                    {0: "这是一段已经翻译完成的中文正文。"},
                )

    def test_recovery_is_invalidated_by_model_or_splitter_version(self):
        sources = ["A translated paragraph with enough words to validate."]
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "paper.json")
            with mock.patch.dict(
                os.environ,
                {"PAPER_TRANS_RECOVERY_FILE": path},
                clear=False,
            ):
                translation_runtime._save_translation_recovery(
                    sources,
                    ["prompt", "这是一段中文翻译结果。"],
                )
                with mock.patch.dict(
                    os.environ,
                    {"PAPER_TRANS_EFFECTIVE_MODEL": "other-model"},
                    clear=False,
                ):
                    self.assertEqual(
                        translation_runtime._load_translation_recovery(sources),
                        {},
                    )
                with open(path, encoding="utf-8") as handle:
                    payload = json.load(handle)
                payload["splitter"] = "stale"
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle)
                self.assertEqual(
                    translation_runtime._load_translation_recovery(sources),
                    {},
                )

    def test_recovery_matches_transport_whitespace_and_reordered_chunks(self):
        sources = [
            "First source paragraph with enough words to validate.",
            "Second source paragraph with enough words to validate.",
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "paper.json")
            with mock.patch.dict(
                os.environ,
                {"PAPER_TRANS_RECOVERY_FILE": path},
                clear=False,
            ):
                self.assertTrue(
                    translation_runtime._save_translation_recovery(
                        sources,
                        [
                            "prompt-0",
                            "第一段已经完成翻译。",
                            "prompt-1",
                            "第二段已经完成翻译。",
                        ],
                    )
                )
                with open(path, encoding="utf-8") as handle:
                    payload = json.load(handle)
                payload["items"] = list(reversed(payload["items"]))
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False)
                self.assertEqual(
                    translation_runtime._load_translation_recovery(
                        ["\n" + sources[1] + "\n", sources[0]],
                    ),
                    {0: "第二段已经完成翻译。", 1: "第一段已经完成翻译。"},
                )


if __name__ == "__main__":
    unittest.main()
