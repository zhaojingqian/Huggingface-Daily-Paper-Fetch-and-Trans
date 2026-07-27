import os
import unittest
from unittest import mock

import translate_full


class TranslateFullCommandTest(unittest.TestCase):
    def test_passes_only_supported_llm_overrides_to_container(self):
        with mock.patch.dict(
            os.environ,
            {
                "PAPER_TRANS_LLM_MODEL": "gpt-4o-mini",
                "PAPER_TRANS_LLM_WORKERS": "2",
                "UNRELATED_SECRET": "do-not-forward",
            },
            clear=False,
        ):
            command = translate_full._container_driver_command("2607.13399")

        joined = " ".join(command)
        self.assertIn(
            "PAPER_TRANS_LLM_MODEL=gpt-4o-mini",
            command,
        )
        self.assertIn("PAPER_TRANS_LLM_WORKERS=2", command)
        self.assertNotIn("UNRELATED_SECRET", joined)
        self.assertEqual(command[-1], "2607.13399")


if __name__ == "__main__":
    unittest.main()
