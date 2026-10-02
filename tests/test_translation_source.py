import os
import tempfile
import unittest
from unittest import mock

from paperhub.translation_source import SourceCache, SourceDownloadPolicy, SourceUnavailableError


class _Response:
    headers = {"Content-Length": "2048"}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        self.chunk_size = chunk_size
        return [b"x" * 2048]


class _Session:
    def __init__(self):
        self.proxies = {}
        self.trust_env = True
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _Response()


class TranslationSourceTest(unittest.TestCase):
    def test_pdf_only_source_stops_without_network_retries(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = _Session()
            cache = SourceCache(cache_dir=tmp, paper_id="2608.19880", proxies={},
                                session_factory=lambda: session, safety_error=lambda _: None,
                                policy=SourceDownloadPolicy())
            with mock.patch.object(_Response, "iter_content", return_value=[b"%PDF-" + b"x" * 2043]):
                with self.assertRaises(SourceUnavailableError):
                    cache.prefetch()
            self.assertEqual(len(session.calls), 1)
            self.assertFalse(os.path.exists(cache.source_tar + ".part"))

    def test_policy_clamps_operator_values(self):
        policy = SourceDownloadPolicy.from_env({
            "PAPER_TRANS_SOURCE_CONNECT_TIMEOUT": "1",
            "PAPER_TRANS_SOURCE_MAX_BYTES": str(10 * 1024 * 1024 * 1024),
        })
        self.assertEqual(policy.connect_timeout, 5)
        self.assertEqual(policy.max_bytes, 2 * 1024 * 1024 * 1024)

    def test_prefetch_validates_and_publishes_archive_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = _Session()
            cache = SourceCache(
                cache_dir=tmp,
                paper_id="2608.19880",
                proxies={"https": "http://proxy"},
                session_factory=lambda: session,
                safety_error=lambda _: None,
                policy=SourceDownloadPolicy(
                    attempt_seconds=60,
                    total_seconds=120,
                    rate_grace_seconds=10,
                ),
            )

            self.assertTrue(cache.prefetch(max_rounds=1))
            self.assertTrue(cache.is_valid())
            self.assertEqual(os.path.getsize(cache.source_tar), 2048)
            self.assertFalse(os.path.exists(cache.source_tar + ".part"))
            self.assertEqual(session.calls[0][1]["timeout"], (15, 90))


if __name__ == "__main__":
    unittest.main()
