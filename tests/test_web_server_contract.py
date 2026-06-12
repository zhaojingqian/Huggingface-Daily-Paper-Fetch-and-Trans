import http.client
import json
import os
import socketserver
import sys
import threading
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import web_server  # noqa: E402


SAMPLE_VIEW_ID = "2605.21573"
SAMPLE_DETAIL_ID = "2605.23904"
SAMPLE_MODE = "weekly"
SAMPLE_KEY = "2026-W22"


class ThreadingHTTPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class WebServerContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._base_path = web_server.BASE_PATH
        web_server.BASE_PATH = ""
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        web_server.BASE_PATH = cls._base_path

    def request(self, path, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", path, headers=headers or {})
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        return resp, body

    def assert_content_type(self, resp, expected):
        self.assertIn(expected, resp.getheader("Content-Type") or "")

    def assert_umami_script(self, html):
        self.assertIn('src="https://cloud.umami.is/script.js"', html)
        self.assertIn('data-website-id="848a0bed-4004-423d-8f2b-52c9cbd39d93"', html)

    def sample_pdf_exists(self, arxiv_id=SAMPLE_VIEW_ID):
        return os.path.exists(os.path.join(web_server.PAPER_STORE_DIR, f"{arxiv_id}_zh.pdf"))

    def test_core_pages_return_html(self):
        for path in ["/", "/daily", "/weekly", "/monthly", "/bookmarks", "/submit", "/search", "/status"]:
            with self.subTest(path=path):
                resp, body = self.request(path)
                self.assertEqual(resp.status, 200)
                self.assert_content_type(resp, "text/html")
                self.assert_umami_script(body.decode("utf-8", errors="replace"))

    def test_json_endpoints_return_json(self):
        for path in ["/api/bookmarks", "/api/status"]:
            with self.subTest(path=path):
                resp, _ = self.request(path)
                self.assertEqual(resp.status, 200)
                self.assert_content_type(resp, "application/json")

    def test_weekly_list_and_detail_keep_links(self):
        resp, body = self.request(f"/{SAMPLE_MODE}/{SAMPLE_KEY}")
        self.assertEqual(resp.status, 200)
        self.assert_content_type(resp, "text/html")

        if not web_server.get_paper_entry(SAMPLE_MODE, SAMPLE_KEY, SAMPLE_DETAIL_ID).get("title"):
            self.skipTest("sample detail paper metadata is not available")

        resp, body = self.request(f"/{SAMPLE_MODE}/{SAMPLE_KEY}/papers/{SAMPLE_DETAIL_ID}")
        html = body.decode("utf-8", errors="replace")
        self.assertEqual(resp.status, 200)
        self.assert_content_type(resp, "text/html")
        self.assertIn("SkillOpt：自我进化代理技能的执行策略", html)
        self.assertIn(f'href="/view/{SAMPLE_DETAIL_ID}"', html)
        self.assertIn(f'href="https://arxiv.org/pdf/{SAMPLE_DETAIL_ID}"', html)
        self.assertIn(f'href="https://arxiv.org/abs/{SAMPLE_DETAIL_ID}"', html)

    @unittest.skipUnless(
        os.path.exists(os.path.join(web_server.PAPER_STORE_DIR, f"{SAMPLE_VIEW_ID}_zh.pdf")),
        "sample PDF is not available",
    )
    def test_view_is_html_wrapper_not_redirect(self):
        resp, body = self.request(f"/view/{SAMPLE_VIEW_ID}")
        html = body.decode("utf-8", errors="replace")
        self.assertEqual(resp.status, 200)
        self.assertIsNone(resp.getheader("Location"))
        self.assert_content_type(resp, "text/html")
        self.assertIn("<title>Lens：重新思考基础文本到图像模型的训练效率</title>", html)
        self.assertIn(f'<iframe src="/papers/{SAMPLE_VIEW_ID}_zh.pdf#view=FitH"', html)
        self.assert_umami_script(html)

    @unittest.skipUnless(
        os.path.exists(os.path.join(web_server.PAPER_STORE_DIR, f"{SAMPLE_VIEW_ID}_zh.pdf")),
        "sample PDF is not available",
    )
    def test_pdf_routes_keep_range_and_direct_pdf(self):
        resp, _ = self.request(f"/papers/{SAMPLE_VIEW_ID}_zh.pdf", headers={"Range": "bytes=0-0"})
        self.assertEqual(resp.status, 206)
        self.assert_content_type(resp, "application/pdf")
        self.assertEqual(resp.getheader("Accept-Ranges"), "bytes")
        self.assertTrue((resp.getheader("Content-Range") or "").startswith("bytes 0-0/"))

        resp, _ = self.request(f"/pdf/{SAMPLE_VIEW_ID}/Lens.pdf", headers={"Range": "bytes=0-0"})
        self.assertEqual(resp.status, 206)
        self.assert_content_type(resp, "application/pdf")
        self.assertEqual(resp.getheader("Accept-Ranges"), "bytes")

    @unittest.skipUnless(
        os.path.exists(os.path.join(web_server.PAPER_STORE_DIR, f"{SAMPLE_VIEW_ID}_zh.pdf")),
        "sample PDF is not available",
    )
    def test_base_path_rewrites_internal_links(self):
        old_base = web_server.BASE_PATH
        web_server.BASE_PATH = "/paper"
        try:
            resp, body = self.request(f"/view/{SAMPLE_VIEW_ID}")
            html = body.decode("utf-8", errors="replace")
            prefixed_resp, prefixed_body = self.request(f"/paper/view/{SAMPLE_VIEW_ID}")
            prefixed_html = prefixed_body.decode("utf-8", errors="replace")
            pdf_resp, _ = self.request(
                f"/paper/papers/{SAMPLE_VIEW_ID}_zh.pdf",
                headers={"Range": "bytes=0-0"},
            )
            redirect_resp, _ = self.request(f"/paper/papers/{SAMPLE_VIEW_ID}")
        finally:
            web_server.BASE_PATH = old_base
        self.assertEqual(resp.status, 200)
        self.assertIn(f'<iframe src="/paper/papers/{SAMPLE_VIEW_ID}_zh.pdf#view=FitH"', html)
        self.assertEqual(prefixed_resp.status, 200)
        self.assertIn(f'<iframe src="/paper/papers/{SAMPLE_VIEW_ID}_zh.pdf#view=FitH"', prefixed_html)
        self.assertEqual(pdf_resp.status, 206)
        self.assert_content_type(pdf_resp, "application/pdf")
        self.assertEqual(redirect_resp.status, 302)
        self.assertEqual(redirect_resp.getheader("Location"), f"/paper/detail/{SAMPLE_VIEW_ID}")

        old_base = web_server.BASE_PATH
        web_server.BASE_PATH = "/paper"
        try:
            resp, body = self.request("/")
            html = body.decode("utf-8", errors="replace")
        finally:
            web_server.BASE_PATH = old_base
        self.assertEqual(resp.status, 200)
        self.assertIn('href="/paper/daily"', html)
        self.assertIn('fetch((window.BP||\'\') + \'/api/bookmarks\'', html)

    def test_key_fetch_and_click_contracts_still_exist(self):
        resp, body = self.request("/search")
        html = body.decode("utf-8", errors="replace")
        self.assertEqual(resp.status, 200)
        self.assertIn("'/api/search?q='", html)
        self.assertIn("onclick=\"doSearch()\"", html)

        resp, body = self.request("/submit")
        html = body.decode("utf-8", errors="replace")
        self.assertEqual(resp.status, 200)
        self.assertIn("'/api/submit'", html)
        self.assertIn("onclick=\"submitForm()\"", html)

        resp, body = self.request("/status")
        html = body.decode("utf-8", errors="replace")
        self.assertEqual(resp.status, 200)
        self.assertIn('"/api/status"', html)
        self.assertIn('"/api/status/kill"', html)

    def test_search_dedupes_duplicate_arxiv_ids(self):
        results = web_server.search_papers(SAMPLE_VIEW_ID)
        ids = [p.get("arxiv_id") for p in results]
        self.assertEqual(ids.count(SAMPLE_VIEW_ID), 1)
        hit = next(p for p in results if p.get("arxiv_id") == SAMPLE_VIEW_ID)
        self.assertEqual(hit.get("_detail_href"), f"/detail/{SAMPLE_VIEW_ID}")
        self.assertIn("weekly/2026-W22", hit.get("_source_note", ""))

    def test_search_api_rewrites_injected_result_links(self):
        old_base = web_server.BASE_PATH
        web_server.BASE_PATH = "/paper"
        try:
            resp, body = self.request(f"/api/search?q={SAMPLE_VIEW_ID}")
        finally:
            web_server.BASE_PATH = old_base
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(resp.status, 200)
        self.assertEqual(payload["total"], 1)
        self.assertIn(f'href="/paper/detail/{SAMPLE_VIEW_ID}"', payload["html"])
        self.assertNotIn(f'href="/weekly/{SAMPLE_KEY}/papers/{SAMPLE_VIEW_ID}"', payload["html"])

    def test_global_paper_detail_route(self):
        resp, body = self.request(f"/detail/{SAMPLE_VIEW_ID}")
        html = body.decode("utf-8", errors="replace")
        self.assertEqual(resp.status, 200)
        self.assert_content_type(resp, "text/html")
        self.assertIn("Lens：重新思考基础文本到图像模型的训练效率", html)
        self.assertIn(f'href="/view/{SAMPLE_VIEW_ID}"', html)

    def test_old_global_papers_route_redirects_locally(self):
        resp, _ = self.request(f"/papers/{SAMPLE_VIEW_ID}")
        self.assertEqual(resp.status, 302)
        self.assertEqual(resp.getheader("Location"), f"/detail/{SAMPLE_VIEW_ID}")


if __name__ == "__main__":
    unittest.main()
