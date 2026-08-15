import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.render_gpt_academic_config import render_config, write_config
from paperhub import env_config


RUNTIME_ENV = {
    "GPT_ACADEMIC_WEB_PORT": "12345",
    "GPT_ACADEMIC_LLM_MODEL": "test-model",
    "GPT_ACADEMIC_AVAIL_LLM_MODELS_JSON": '["test-model","fallback"]',
    "GPT_ACADEMIC_API_KEY": "test-secret-not-real",
    "GPT_ACADEMIC_API_URL_REDIRECT_JSON": '{"test-model":"https://example.invalid"}',
    "DOC2X_API_KEY": "test-doc2x-not-real",
}


class RuntimeConfigTests(unittest.TestCase):
    def test_http_proxies_share_configured_endpoint_and_disable_cleanly(self):
        with mock.patch.dict(
            os.environ,
            {"PAPER_TRANS_PROXY": "http://proxy.example:8080"},
            clear=True,
        ), mock.patch.object(env_config, "_LOADED", True):
            self.assertEqual(
                env_config.http_proxies(True),
                {
                    "http": "http://proxy.example:8080",
                    "https": "http://proxy.example:8080",
                },
            )
            self.assertEqual(
                env_config.http_proxies(False),
                {"http": "", "https": ""},
            )

    @mock.patch.dict(os.environ, RUNTIME_ENV, clear=True)
    def test_renderer_preserves_types_without_exposing_shell_syntax(self):
        namespace = {}
        rendered = render_config()
        exec(rendered, namespace)

        self.assertIn("API_KEY=", rendered)
        self.assertIn("API_URL_REDIRECT=", rendered)
        self.assertEqual(namespace["WEB_PORT"], 12345)
        self.assertEqual(namespace["LLM_MODEL"], "test-model")
        self.assertEqual(namespace["AVAIL_LLM_MODELS"], ["test-model", "fallback"])
        self.assertEqual(
            namespace["API_URL_REDIRECT"],
            {"test-model": "https://example.invalid"},
        )

    @mock.patch.dict(os.environ, RUNTIME_ENV, clear=True)
    def test_written_runtime_config_is_owner_only_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "config_private.py"
            write_config(output)
            first = output.read_bytes()
            write_config(output)

            self.assertEqual(output.read_bytes(), first)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)

    @mock.patch.dict(
        os.environ,
        dict(RUNTIME_ENV, GPT_ACADEMIC_CONFIG_OWNER="invalid"),
        clear=True,
    )
    def test_invalid_runtime_owner_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "numeric UID:GID"):
                write_config(Path(directory) / "config_private.py")


if __name__ == "__main__":
    unittest.main()
