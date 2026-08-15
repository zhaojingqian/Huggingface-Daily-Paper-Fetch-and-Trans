import unittest
from unittest import mock

import requests

from paperhub import http_client


class HttpClientTest(unittest.TestCase):
    def test_proxy_failure_falls_back_to_direct_request(self):
        response = mock.Mock(text="ok")
        response.raise_for_status.return_value = None
        with mock.patch.object(
            http_client.requests,
            "get",
            side_effect=[requests.exceptions.ProxyError("proxy"), response],
        ) as get, mock.patch.object(http_client, "http_proxies", return_value={"http": ""}), mock.patch(
            "builtins.print"
        ):
            self.assertEqual(http_client.fetch_text("https://example.test"), "ok")

        self.assertEqual(get.call_count, 2)
        self.assertEqual(get.call_args_list[0].kwargs["timeout"], 30)
        self.assertEqual(get.call_args_list[1].kwargs["proxies"], {"http": ""})

    def test_use_proxy_false_starts_direct_and_retries_transient_error(self):
        response = mock.Mock(text="ok")
        response.raise_for_status.return_value = None
        with mock.patch.object(
            http_client.requests,
            "get",
            side_effect=[requests.exceptions.ConnectionError("offline"), response],
        ) as get, mock.patch.object(http_client, "http_proxies", return_value={}), mock.patch.object(
            http_client.time, "sleep"
        ) as sleep, mock.patch("builtins.print"):
            self.assertEqual(
                http_client.fetch_text(
                    "https://example.test",
                    use_proxy=False,
                    max_retries=2,
                ),
                "ok",
            )

        self.assertEqual(get.call_count, 2)
        sleep.assert_called_once_with(1)


if __name__ == "__main__":
    unittest.main()
