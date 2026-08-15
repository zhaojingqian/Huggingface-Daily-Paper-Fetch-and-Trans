import fcntl
import http.client
import json
import os
import socketserver
import sys
import tempfile
import threading
import unittest
from unittest import mock


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import web_server  # noqa: E402


SAMPLE_VIEW_ID = "2605.21573"
SAMPLE_DETAIL_ID = "2605.23904"
SAMPLE_MODE = "weekly"
SAMPLE_KEY = "2026-W22"


def _sample_pdf_is_publishable():
    entry = web_server._read_paper_store(SAMPLE_VIEW_ID)
    return bool(
        web_server._paper_pdf_exists(SAMPLE_VIEW_ID)
        and not web_server._entry_blocks_pdf(entry)
        and not web_server._index_blocks_pdf(SAMPLE_VIEW_ID)
        and not web_server._quality_failure_active(SAMPLE_VIEW_ID)
    )


class ThreadingHTTPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class WebServerContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._base_path = web_server.BASE_PATH
        cls._admin_token = os.environ.get("TOPIC_ADMIN_TOKEN")
        os.environ["TOPIC_ADMIN_TOKEN"] = "test-token"
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
        if cls._admin_token is None:
            os.environ.pop("TOPIC_ADMIN_TOKEN", None)
        else:
            os.environ["TOPIC_ADMIN_TOKEN"] = cls._admin_token

    def request(self, path, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", path, headers=headers or {})
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        return resp, body

    def post_json(self, path, payload, headers=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req_headers = {"Content-Type": "application/json", "Content-Length": str(len(body))}
        if headers:
            req_headers.update(headers)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request(
            "POST",
            path,
            body=body,
            headers=req_headers,
        )
        resp = conn.getresponse()
        resp_body = resp.read()
        conn.close()
        return resp, resp_body

    def tearDown(self):
        web_server._invalidate_search_snapshot()
        web_server._submit_cancelled_ids.clear()

    def assert_content_type(self, resp, expected):
        self.assertIn(expected, resp.getheader("Content-Type") or "")

    def assert_umami_script(self, html):
        self.assertIn('src="https://cloud.umami.is/script.js"', html)
        self.assertIn('data-website-id="848a0bed-4004-423d-8f2b-52c9cbd39d93"', html)

    def sample_pdf_exists(self, arxiv_id=SAMPLE_VIEW_ID):
        return os.path.exists(os.path.join(web_server.PAPER_STORE_DIR, f"{arxiv_id}_zh.pdf"))

    def test_core_pages_return_html(self):
        for path in ["/", "/daily", "/weekly", "/monthly", "/topic", "/bookmarks", "/submit", "/search", "/status"]:
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

    def test_bookmarks_api_mutations_keep_contract(self):
        old_file = web_server.BOOKMARKS_FILE
        token_header = {"X-Topic-Admin-Token": "test-token"}
        with tempfile.TemporaryDirectory() as tmp:
            web_server.BOOKMARKS_FILE = os.path.join(tmp, "bookmarks.json")
            try:
                resp, body = self.post_json("/api/bookmarks", {
                    "action": "create_list",
                    "name": "Read Later",
                    "arxiv_id": SAMPLE_VIEW_ID,
                    "mode": SAMPLE_MODE,
                    "key": SAMPLE_KEY,
                }, headers=token_header)
                payload = json.loads(body.decode("utf-8"))
                self.assertEqual(resp.status, 200)
                self.assertIn("read_later", payload["lists"])
                self.assertEqual(payload["lists"]["read_later"]["papers"][0]["arxiv_id"], SAMPLE_VIEW_ID)

                resp, body = self.post_json("/api/bookmarks", {
                    "action": "toggle",
                    "list_id": "read_later",
                    "arxiv_id": SAMPLE_VIEW_ID,
                    "mode": SAMPLE_MODE,
                    "key": SAMPLE_KEY,
                }, headers=token_header)
                payload = json.loads(body.decode("utf-8"))
                self.assertEqual(resp.status, 200)
                self.assertEqual(payload["lists"]["read_later"]["papers"], [])

                resp, body = self.post_json("/api/bookmarks", {
                    "action": "toggle",
                    "list_id": "read_later",
                    "arxiv_id": SAMPLE_VIEW_ID,
                    "mode": SAMPLE_MODE,
                    "key": SAMPLE_KEY,
                }, headers=token_header)
                payload = json.loads(body.decode("utf-8"))
                self.assertEqual(resp.status, 200)
                self.assertEqual(len(payload["lists"]["read_later"]["papers"]), 1)

                resp, _ = self.post_json("/api/bookmarks", {
                    "action": "create_list",
                    "name": "Second",
                }, headers=token_header)
                self.assertEqual(resp.status, 200)

                resp, body = self.post_json("/api/bookmarks", {
                    "action": "move",
                    "from_list": "read_later",
                    "to_list": "second",
                    "arxiv_id": SAMPLE_VIEW_ID,
                }, headers=token_header)
                payload = json.loads(body.decode("utf-8"))
                self.assertEqual(resp.status, 200)
                self.assertEqual(payload["lists"]["read_later"]["papers"], [])
                self.assertEqual(payload["lists"]["second"]["papers"][0]["arxiv_id"], SAMPLE_VIEW_ID)

                resp, body = self.post_json("/api/bookmarks", {
                    "action": "remove",
                    "list_id": "second",
                    "arxiv_id": SAMPLE_VIEW_ID,
                }, headers=token_header)
                payload = json.loads(body.decode("utf-8"))
                self.assertEqual(resp.status, 200)
                self.assertEqual(payload["lists"]["second"]["papers"], [])
            finally:
                web_server.BOOKMARKS_FILE = old_file

    def test_bookmark_mutations_require_admin_token_and_do_not_write(self):
        old_file = web_server.BOOKMARKS_FILE
        initial = {
            "lists": {
                "first": {
                    "name": "First",
                    "papers": [{
                        "arxiv_id": SAMPLE_VIEW_ID,
                        "mode": SAMPLE_MODE,
                        "key": SAMPLE_KEY,
                    }],
                },
                "second": {"name": "Second", "papers": []},
            },
        }
        mutations = [
            {"action": "create_list", "name": "Third"},
            {
                "action": "toggle", "list_id": "first",
                "arxiv_id": SAMPLE_VIEW_ID, "mode": SAMPLE_MODE,
                "key": SAMPLE_KEY,
            },
            {"action": "delete_list", "list_id": "first"},
            {"action": "rename_list", "list_id": "first", "name": "Renamed"},
            {
                "action": "remove", "list_id": "first",
                "arxiv_id": SAMPLE_VIEW_ID,
            },
            {
                "action": "move", "from_list": "first", "to_list": "second",
                "arxiv_id": SAMPLE_VIEW_ID,
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            web_server.BOOKMARKS_FILE = os.path.join(tmp, "bookmarks.json")
            try:
                web_server.save_bookmarks(initial)
                for payload in mutations:
                    with self.subTest(action=payload["action"]):
                        resp, body = self.post_json("/api/bookmarks", payload)
                        self.assertEqual(resp.status, 403)
                        self.assertEqual(
                            json.loads(body.decode("utf-8"))["error"],
                            "forbidden",
                        )
                        self.assertEqual(web_server.load_bookmarks(), initial)

                resp, _ = self.post_json(
                    "/api/bookmarks",
                    {"action": "delete_list", "list_id": "first"},
                    headers={"X-Topic-Admin-Token": "wrong-token"},
                )
                self.assertEqual(resp.status, 403)
                self.assertEqual(web_server.load_bookmarks(), initial)
            finally:
                web_server.BOOKMARKS_FILE = old_file

    def test_bookmark_api_bounds_and_validates_mutation_fields(self):
        old_file = web_server.BOOKMARKS_FILE
        token_header = {"X-Topic-Admin-Token": "test-token"}
        invalid_payloads = [
            {"action": "create_list", "name": "x" * 121},
            {"action": "create_list", "name": "bad\x00name"},
            {
                "action": "toggle", "list_id": "../first",
                "arxiv_id": SAMPLE_VIEW_ID, "mode": SAMPLE_MODE,
                "key": SAMPLE_KEY,
            },
            {
                "action": "toggle", "list_id": "first",
                "arxiv_id": "../invalid", "mode": SAMPLE_MODE,
                "key": SAMPLE_KEY,
            },
            {
                "action": "toggle", "list_id": "first",
                "arxiv_id": SAMPLE_VIEW_ID, "mode": "topic",
                "key": "../../etc/passwd",
            },
            {
                "action": "move", "from_list": "first", "to_list": "first",
                "arxiv_id": SAMPLE_VIEW_ID,
            },
            {"action": ["create_list"], "name": "Bad action"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            web_server.BOOKMARKS_FILE = os.path.join(tmp, "bookmarks.json")
            try:
                web_server.save_bookmarks({
                    "lists": {
                        "first": {"name": "First", "papers": []},
                    },
                })
                for payload in invalid_payloads:
                    with self.subTest(payload=payload):
                        resp, _ = self.post_json(
                            "/api/bookmarks", payload, headers=token_header
                        )
                        self.assertEqual(resp.status, 400)

                resp, _ = self.post_json(
                    "/api/bookmarks",
                    ["not", "an", "object"],
                    headers=token_header,
                )
                self.assertEqual(resp.status, 400)

                resp, body = self.post_json(
                    "/api/bookmarks",
                    {"action": "create_list", "name": "x" * 17000},
                    headers=token_header,
                )
                self.assertEqual(resp.status, 413)
                self.assertEqual(
                    json.loads(body.decode("utf-8"))["error"],
                    "request too large",
                )
                self.assertEqual(
                    set(web_server.load_bookmarks()["lists"]),
                    {"first"},
                )
            finally:
                web_server.BOOKMARKS_FILE = old_file

    def test_bookmark_payload_token_remains_supported(self):
        old_file = web_server.BOOKMARKS_FILE
        with tempfile.TemporaryDirectory() as tmp:
            web_server.BOOKMARKS_FILE = os.path.join(tmp, "bookmarks.json")
            try:
                resp, body = self.post_json("/api/bookmarks", {
                    "action": "create_list",
                    "name": "Payload Token",
                    "admin_token": "test-token",
                })
                payload = json.loads(body.decode("utf-8"))
                self.assertEqual(resp.status, 200)
                self.assertIn("payload_token", payload["lists"])
            finally:
                web_server.BOOKMARKS_FILE = old_file

    def test_api_error_contracts(self):
        resp, body = self.request("/api/submit")
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(resp.status, 405)
        self.assertEqual(payload["error"], "POST only")

        resp, body = self.post_json("/api/submit", {"arxiv_id": "not-an-id"})
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(resp.status, 403)
        self.assertEqual(payload["error"], "forbidden")

        resp, body = self.post_json(
            "/api/submit",
            {"arxiv_id": "not-an-id"},
            headers={"X-Topic-Admin-Token": "test-token"},
        )
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(resp.status, 400)
        self.assertIn("无效的 arXiv ID", payload["error"])

        resp, body = self.post_json(
            "/api/bookmarks",
            {"action": "unknown"},
            headers={"X-Topic-Admin-Token": "test-token"},
        )
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(resp.status, 400)
        self.assertEqual(payload["error"], "unknown action")

    def test_kill_is_post_only_and_requires_admin_token(self):
        with mock.patch.object(
            web_server,
            "kill_current_translation",
            return_value={"ok": True, "msg": "terminated"},
        ) as kill:
            resp, body = self.request("/api/status/kill")
            payload = json.loads(body.decode("utf-8"))
            self.assertEqual(resp.status, 405)
            self.assertEqual(payload["error"], "POST only")
            kill.assert_not_called()

            resp, body = self.post_json("/api/status/kill", {})
            payload = json.loads(body.decode("utf-8"))
            self.assertEqual(resp.status, 403)
            self.assertEqual(payload["error"], "forbidden")
            kill.assert_not_called()

            resp, _ = self.post_json(
                "/api/status/kill",
                {},
                headers={"X-Topic-Admin-Token": "wrong-token"},
            )
            self.assertEqual(resp.status, 403)
            kill.assert_not_called()

            resp, body = self.post_json(
                "/api/status/kill",
                {},
                headers={"X-Topic-Admin-Token": "test-token"},
            )
            payload = json.loads(body.decode("utf-8"))
            self.assertEqual(resp.status, 200)
            self.assertTrue(payload["ok"])
            kill.assert_called_once_with()

    def test_kill_can_target_one_arxiv_id_from_post_body(self):
        aid = "2607.20001"
        with mock.patch.object(
            web_server,
            "kill_current_translation",
            return_value={"ok": True, "arxiv_id": aid},
        ) as kill:
            resp, body = self.post_json(
                "/api/status/kill",
                {"arxiv_id": aid},
                headers={"X-Topic-Admin-Token": "test-token"},
            )
        self.assertEqual(resp.status, 200)
        self.assertTrue(json.loads(body.decode("utf-8"))["ok"])
        kill.assert_called_once_with(aid)

    def test_kill_cleans_unique_tree_and_only_marks_matching_job(self):
        target = "2607.20001"
        other = "2607.20002"
        jobs = {
            target: {"arxiv_id": target, "status": "full_pdf"},
            other: {"arxiv_id": other, "status": "full_pdf"},
        }
        cleanup = {
            "ok": True,
            "found": True,
            "verified": True,
            "driver_pids": [101],
            "target_pids": [101, 102, 103],
            "survivors": [],
        }
        with \
            mock.patch(
                "translate_full.list_container_drivers",
                return_value=[{"arxiv_id": target, "pid": 101}],
            ), \
            mock.patch(
                "translate_full.terminate_container_driver_tree",
                return_value=cleanup,
            ) as terminate, \
            mock.patch.object(web_server, "_load_jobs", return_value=jobs), \
            mock.patch.object(web_server, "_save_jobs") as save:
            result = web_server.kill_current_translation()

        self.assertTrue(result["ok"])
        terminate.assert_called_once_with(
            target,
            container_name=web_server.GPT_ACADEMIC_CONTAINER,
        )
        self.assertEqual(jobs[target]["status"], "error")
        self.assertEqual(jobs[other]["status"], "full_pdf")
        self.assertIn(target, web_server._submit_cancelled_ids)
        save.assert_called_once_with(jobs)

    def test_kill_refuses_ambiguous_drivers_instead_of_cross_killing(self):
        drivers = [
            {"arxiv_id": "2607.20001", "pid": 101},
            {"arxiv_id": "2607.20002", "pid": 201},
        ]
        with \
            mock.patch(
                "translate_full.list_container_drivers",
                return_value=drivers,
            ), \
            mock.patch(
                "translate_full.terminate_container_driver_tree",
            ) as terminate:
            result = web_server.kill_current_translation()

        self.assertFalse(result["ok"])
        self.assertIn("多个", result["msg"])
        terminate.assert_not_called()

    def test_kill_does_not_claim_success_when_descendant_survives(self):
        target = "2607.20001"
        cleanup = {
            "ok": False,
            "found": True,
            "verified": False,
            "driver_pids": [101],
            "target_pids": [101, 102],
            "survivors": [102],
        }
        with \
            mock.patch(
                "translate_full.list_container_drivers",
                return_value=[{"arxiv_id": target, "pid": 101}],
            ), \
            mock.patch(
                "translate_full.terminate_container_driver_tree",
                return_value=cleanup,
            ), \
            mock.patch.object(web_server, "_save_jobs") as save:
            result = web_server.kill_current_translation(target)

        self.assertFalse(result["ok"])
        self.assertEqual(result["survivors"], [102])
        self.assertNotIn(target, web_server._submit_cancelled_ids)
        save.assert_not_called()

    def test_delete_validates_location_and_preserves_shared_pdf(self):
        aid = "2607.20001"
        token_header = {"X-Topic-Admin-Token": "test-token"}
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = os.path.join(tmp, "data")
            paper_dir = os.path.join(data_dir, "papers")
            bookmarks_file = os.path.join(data_dir, "bookmarks.json")
            jobs_file = os.path.join(data_dir, "manual", "jobs.json")
            lock_dir = os.path.join(tmp, "locks")

            def local_mode_dir(mode):
                return os.path.join(data_dir, mode)

            def local_mode_index_path(mode, key):
                return os.path.join(data_dir, mode, key, "index.json")

            def local_mode_papers_dir(mode, key):
                return os.path.join(data_dir, mode, key, "papers")

            def write_index(mode, key):
                path = local_mode_index_path(mode, key)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    json.dump({
                        "mode": mode,
                        "key": key,
                        "total": 1,
                        "papers": [{"arxiv_id": aid}],
                    }, f)
                return path

            daily_index = write_index("daily", "2026-07-28")
            weekly_index = write_index("weekly", "2026-W31")
            daily_html = os.path.join(
                local_mode_papers_dir("daily", "2026-07-28"), aid + ".html"
            )
            os.makedirs(os.path.dirname(daily_html), exist_ok=True)
            with open(daily_html, "w", encoding="utf-8") as f:
                f.write("<html></html>")
            os.makedirs(paper_dir, exist_ok=True)
            store_pdf = os.path.join(paper_dir, aid + "_zh.pdf")
            with open(store_pdf, "wb") as f:
                f.write(b"%PDF-shared")

            patches = {
                "DATA_DIR": data_dir,
                "PAPER_STORE_DIR": paper_dir,
                "BOOKMARKS_FILE": bookmarks_file,
                "SUBMIT_JOBS_FILE": jobs_file,
                "LOCK_DIR": lock_dir,
                "mode_dir": local_mode_dir,
                "mode_index_path": local_mode_index_path,
                "mode_papers_dir": local_mode_papers_dir,
            }
            with mock.patch.multiple(web_server, **patches), mock.patch.object(
                web_server.paper_store,
                "pdf_path",
                side_effect=lambda arxiv_id: os.path.join(
                    paper_dir, arxiv_id + "_zh.pdf"
                ),
            ):
                payload = {
                    "mode": "daily",
                    "key": "2026-07-28",
                    "arxiv_id": aid,
                }
                resp, _ = self.post_json("/api/paper/delete", payload)
                self.assertEqual(resp.status, 403)
                self.assertTrue(os.path.exists(store_pdf))

                for bad_payload in [
                    {"mode": "topic", "key": "2026-07-28", "arxiv_id": aid},
                    {"mode": "daily", "key": "../../etc", "arxiv_id": aid},
                    {"mode": "daily", "key": "2026-07-28", "arxiv_id": "../bad"},
                ]:
                    resp, _ = self.post_json(
                        "/api/paper/delete", bad_payload, headers=token_header
                    )
                    self.assertEqual(resp.status, 400)

                os.makedirs(lock_dir, exist_ok=True)
                catalog_lock_path = os.path.join(
                    lock_dir, "publication-catalog.lock"
                )
                with open(catalog_lock_path, "a+") as busy_lock:
                    fcntl.flock(
                        busy_lock, fcntl.LOCK_SH | fcntl.LOCK_NB
                    )
                    resp, body = self.post_json(
                        "/api/paper/delete", payload, headers=token_header
                    )
                    self.assertEqual(resp.status, 409)
                    self.assertIn(
                        "定时任务",
                        json.loads(body.decode("utf-8"))["error"],
                    )
                    with open(daily_index, encoding="utf-8") as f:
                        self.assertEqual(len(json.load(f)["papers"]), 1)
                    fcntl.flock(busy_lock, fcntl.LOCK_UN)

                resp, body = self.post_json(
                    "/api/paper/delete", payload, headers=token_header
                )
                result = json.loads(body.decode("utf-8"))
                self.assertEqual(resp.status, 200)
                self.assertFalse(result["pdf_deleted"])
                self.assertIn("weekly/2026-W31", result["remaining_references"])
                self.assertFalse(os.path.exists(daily_html))
                self.assertTrue(os.path.exists(store_pdf))
                with open(daily_index, encoding="utf-8") as f:
                    self.assertEqual(json.load(f)["papers"], [])

                resp, body = self.post_json(
                    "/api/paper/delete",
                    {"mode": "weekly", "key": "2026-W31", "arxiv_id": aid},
                    headers=token_header,
                )
                result = json.loads(body.decode("utf-8"))
                self.assertEqual(resp.status, 200)
                self.assertTrue(result["pdf_deleted"])
                self.assertEqual(result["remaining_references"], [])
                self.assertFalse(os.path.exists(store_pdf))
                with open(weekly_index, encoding="utf-8") as f:
                    self.assertEqual(json.load(f)["papers"], [])

    def test_manual_upsert_refuses_to_overwrite_corrupt_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = os.path.join(tmp, "data")
            lock_dir = os.path.join(tmp, "locks")
            key_dir = os.path.join(data_dir, "manual", "2026-07-28")
            index_path = os.path.join(key_dir, "index.json")
            os.makedirs(key_dir, exist_ok=True)
            with open(index_path, "w", encoding="utf-8") as handle:
                handle.write("{broken")

            with mock.patch.object(
                web_server,
                "mode_key_dir",
                return_value=key_dir,
            ), mock.patch.object(
                web_server,
                "LOCK_DIR",
                lock_dir,
            ):
                with self.assertRaises(RuntimeError):
                    web_server._upsert_manual_index(
                        "manual",
                        "2026-07-28",
                        {"arxiv_id": "2607.20001"},
                    )

            with open(index_path, encoding="utf-8") as handle:
                persisted = handle.read()

        self.assertEqual(persisted, "{broken")

    def test_delete_invalidates_search_snapshot_after_locked_failure(self):
        validated = (
            "daily",
            "2026-07-28",
            "2607.20001",
            "/tmp/index.json",
            "/tmp/paper.html",
        )
        publication_lock = mock.MagicMock()
        with mock.patch.object(
            web_server,
            "_validated_delete_location",
            return_value=validated,
        ), mock.patch.object(
            web_server,
            "_publication_lock",
            publication_lock,
        ), mock.patch.object(
            web_server,
            "_delete_paper_locked",
            side_effect=RuntimeError("mid-transaction failure"),
        ), mock.patch.object(
            web_server,
            "_invalidate_search_snapshot",
        ) as invalidate:
            with self.assertRaises(RuntimeError):
                web_server._delete_paper(*validated[:3])

        publication_lock.assert_called_once_with(
            "daily", "2026-07-28", "2607.20001"
        )
        invalidate.assert_called_once_with()

    def test_failed_or_quality_gated_pdf_is_not_publicly_served(self):
        aid = "2607.20009"
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = os.path.join(tmp, "data")
            paper_dir = os.path.join(data_dir, "papers")
            error_dir = os.path.join(tmp, "logs", "pdf_errors")
            index_path = os.path.join(
                data_dir, "daily", "2026-07-28", "index.json"
            )
            pdf_path = os.path.join(paper_dir, aid + "_zh.pdf")
            os.makedirs(os.path.dirname(index_path), exist_ok=True)
            os.makedirs(paper_dir, exist_ok=True)
            os.makedirs(error_dir, exist_ok=True)
            with open(pdf_path, "wb") as handle:
                handle.write(b"%PDF-old-content")

            store = {
                "arxiv_id": aid,
                "title": "Publication gate",
                "title_zh": "发布门禁",
                "pdf_status": "ok",
                "pdf_zh": "papers/" + aid + "_zh.pdf",
            }

            def write_index(status):
                with open(index_path, "w", encoding="utf-8") as handle:
                    json.dump({
                        "mode": "daily",
                        "key": "2026-07-28",
                        "papers": [{
                            "arxiv_id": aid,
                            "pdf_status": status,
                        }],
                    }, handle)

            patches = {
                "DATA_DIR": data_dir,
                "PAPER_STORE_DIR": paper_dir,
                "_PDF_ERROR_DIR": error_dir,
            }
            with mock.patch.multiple(web_server, **patches), \
                    mock.patch.object(
                        web_server, "_read_paper_store",
                        side_effect=lambda _: dict(store),
                    ), \
                    mock.patch.object(
                        web_server, "_paper_pdf_exists", return_value=True
                    ), \
                    mock.patch.object(
                        web_server.paper_store, "pdf_path",
                        return_value=pdf_path,
                    ):
                write_index("failed")
                for route in (
                    f"/view/{aid}",
                    f"/pdf/{aid}/paper.pdf",
                    f"/papers/{aid}_zh.pdf",
                ):
                    with self.subTest(blocker="index", route=route):
                        resp, _ = self.request(route)
                        self.assertEqual(resp.status, 404)

                write_index("ok")
                store["pdf_status"] = "failed"
                resp, _ = self.request(f"/view/{aid}")
                self.assertEqual(resp.status, 404)

                store["pdf_status"] = "ok"
                with open(
                    os.path.join(error_dir, aid + ".json"),
                    "w",
                    encoding="utf-8",
                ) as handle:
                    json.dump({
                        "category": "quality.untranslated_prose",
                        "retry_strategy": "retry_translation",
                    }, handle)
                resp, _ = self.request(f"/view/{aid}")
                self.assertEqual(resp.status, 404)

                os.remove(os.path.join(error_dir, aid + ".json"))
                web_server._invalidate_search_snapshot()
                resp, _ = self.request(f"/view/{aid}")
                self.assertEqual(resp.status, 200)

                # Taint is persistent failure truth even if a legacy writer
                # has incorrectly changed the mutable status back to ok.
                store["pdf_quality_tainted"] = True
                store["pdf_quality_taint_reason"] = "quality.untranslated_prose"
                resp, _ = self.request(f"/view/{aid}")
                self.assertEqual(resp.status, 404)
                store.pop("pdf_quality_tainted")
                store.pop("pdf_quality_taint_reason")

    def test_status_downgrades_done_job_blocked_by_publication_gate(self):
        aid = "2607.20010"
        jobs = {
            aid: {
                "arxiv_id": aid,
                "status": "done",
                "pdf_zh": "papers/" + aid + "_zh.pdf",
                "submitted_at": "2026-07-28 01:00:00",
            },
        }
        store = {
            "arxiv_id": aid,
            "title": "Blocked PDF",
            "pdf_status": "ok",
        }
        with mock.patch.object(
            web_server, "_load_jobs", return_value=jobs
        ), mock.patch.object(
            web_server, "_index_failed_pdf_ids", return_value={aid}
        ), mock.patch.object(
            web_server, "_read_paper_store", return_value=store
        ), mock.patch.object(
            web_server, "_paper_pdf_exists", return_value=True
        ), mock.patch(
            "shutil.disk_usage", return_value=(100, 50, 50)
        ), mock.patch.object(
            web_server.subprocess,
            "check_output",
            return_value=b"USER PID %CPU %MEM VSZ RSS TTY STAT START TIME COMMAND\n",
        ):
            status = web_server.get_system_status()

        job = status["jobs"][0]
        self.assertEqual(job["status"], "error")
        self.assertEqual(job["pdf_status"], "failed")
        self.assertNotIn("pdf_zh", job)
        self.assertIn("发布门禁", job["msg"])

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
        _sample_pdf_is_publishable(),
        "sample PDF is absent or blocked by the publication gate",
    )
    def test_view_is_html_wrapper_not_redirect(self):
        resp, body = self.request(f"/view/{SAMPLE_VIEW_ID}")
        html = body.decode("utf-8", errors="replace")
        self.assertEqual(resp.status, 200)
        self.assertIsNone(resp.getheader("Location"))
        self.assertEqual(resp.getheader("Cache-Control"), "no-store")
        self.assert_content_type(resp, "text/html")
        self.assertIn("<title>Lens：重新思考基础文本到图像模型的训练效率</title>", html)
        self.assertRegex(html, rf'<iframe src="/papers/{SAMPLE_VIEW_ID}_zh\.pdf\?v=\d+#view=FitH"')
        self.assert_umami_script(html)

    @unittest.skipUnless(
        _sample_pdf_is_publishable(),
        "sample PDF is absent or blocked by the publication gate",
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

    def test_papers_route_only_serves_canonical_chinese_pdf_names(self):
        aid = "2607.20011"
        canonical_name = aid + "_zh.pdf"
        rejected_names = [
            aid + ".json",
            aid + ".tex",
            aid + ".pdf",
            canonical_name + ".bak",
            "bookmarks.json",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            for name in rejected_names:
                with open(os.path.join(tmp, name), "wb") as handle:
                    handle.write(b"private repository data")
            with open(os.path.join(tmp, canonical_name), "wb") as handle:
                handle.write(b"%PDF-" + b"x" * 20000 + b"%%EOF")

            with mock.patch.object(
                web_server, "PAPER_STORE_DIR", tmp
            ), mock.patch.object(
                web_server, "_read_paper_store",
                return_value={"arxiv_id": aid, "pdf_status": "ok"},
            ), mock.patch.object(
                web_server, "_paper_pdf_exists", return_value=True
            ), mock.patch.object(
                web_server, "_index_blocks_pdf", return_value=False
            ), mock.patch.object(
                web_server, "_quality_failure_active", return_value=False
            ):
                for name in rejected_names:
                    with self.subTest(name=name):
                        resp, _ = self.request("/papers/" + name)
                        self.assertEqual(resp.status, 404)

                resp, body = self.request(
                    "/papers/" + canonical_name,
                    headers={"Range": "bytes=0-0"},
                )
                self.assertEqual(resp.status, 206)
                self.assertEqual(body, b"%")
                self.assert_content_type(resp, "application/pdf")
                self.assertEqual(resp.getheader("Accept-Ranges"), "bytes")

    @unittest.skipUnless(
        _sample_pdf_is_publishable(),
        "sample PDF is absent or blocked by the publication gate",
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
        self.assertRegex(html, rf'<iframe src="/paper/papers/{SAMPLE_VIEW_ID}_zh\.pdf\?v=\d+#view=FitH"')
        self.assertEqual(prefixed_resp.status, 200)
        self.assertEqual(prefixed_resp.getheader("Cache-Control"), "no-store")
        self.assertRegex(prefixed_html, rf'<iframe src="/paper/papers/{SAMPLE_VIEW_ID}_zh\.pdf\?v=\d+#view=FitH"')
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
        self.assertIn("X-Topic-Admin-Token", html)
        self.assertIn("onclick=\"submitForm()\"", html)

        resp, body = self.request("/status")
        html = body.decode("utf-8", errors="replace")
        self.assertEqual(resp.status, 200)
        self.assertIn('"/api/status"', html)
        self.assertIn('"/api/status/kill"', html)
        self.assertIn('"X-Topic-Admin-Token":token', html)

        resp, body = self.request("/bookmarks")
        html = body.decode("utf-8", errors="replace")
        self.assertEqual(resp.status, 200)
        self.assertIn("adminHeaders()", html)
        self.assertIn("'X-Topic-Admin-Token': token", html)
        self.assertIn("if (!response.ok)", html)
        self.assertIn("服务器返回了无效 JSON", html)
        self.assertIn("if (!data) return;", html)
        self.assertIn("收藏操作失败：", html)

    def test_search_dedupes_duplicate_arxiv_ids(self):
        results = web_server.search_papers(SAMPLE_VIEW_ID)
        ids = [p.get("arxiv_id") for p in results]
        self.assertEqual(ids.count(SAMPLE_VIEW_ID), 1)
        hit = next(p for p in results if p.get("arxiv_id") == SAMPLE_VIEW_ID)
        self.assertEqual(hit.get("_detail_href"), f"/detail/{SAMPLE_VIEW_ID}")
        self.assertIn("weekly/2026-W22", hit.get("_source_note", ""))

    def test_search_snapshot_reads_each_paper_once_and_includes_topics(self):
        shared_id = "2607.20001"
        topic_id = "2607.20002"
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = os.path.join(tmp, "data")

            def local_mode_dir(mode):
                return os.path.join(data_dir, mode)

            def local_mode_index_path(mode, key):
                return os.path.join(data_dir, mode, key, "index.json")

            def write_index(path, mode, key, papers, topic=None):
                os.makedirs(os.path.dirname(path), exist_ok=True)
                payload = {
                    "mode": mode,
                    "key": key,
                    "total": len(papers),
                    "papers": papers,
                }
                if topic:
                    payload["topic"] = topic
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(payload, f)

            write_index(
                local_mode_index_path("daily", "2026-07-28"),
                "daily",
                "2026-07-28",
                [{"arxiv_id": shared_id}],
            )
            write_index(
                local_mode_index_path("weekly", "2026-W31"),
                "weekly",
                "2026-W31",
                [{"arxiv_id": shared_id}],
            )
            write_index(
                os.path.join(
                    data_dir, "topic", "cache-agents", "2026-07-28", "index.json"
                ),
                "topic",
                "2026-07-28",
                [{"arxiv_id": topic_id}],
                topic="cache-agents",
            )
            store = {
                shared_id: {
                    "arxiv_id": shared_id,
                    "title": "Cache architecture",
                },
                topic_id: {
                    "arxiv_id": topic_id,
                    "title": "Topic cache agents",
                },
            }
            patches = {
                "DATA_DIR": data_dir,
                "mode_dir": local_mode_dir,
                "mode_index_path": local_mode_index_path,
                "_SEARCH_SNAPSHOT_TTL_SECONDS": 60.0,
            }
            with mock.patch.multiple(web_server, **patches), mock.patch.object(
                web_server,
                "_read_paper_store",
                side_effect=lambda arxiv_id: store[arxiv_id],
            ) as read_store:
                web_server._invalidate_search_snapshot()
                first = web_server.search_papers("cache")
                second = web_server.search_papers("cache")

            self.assertEqual(read_store.call_count, 2)
            self.assertEqual(
                {p["arxiv_id"] for p in first},
                {shared_id, topic_id},
            )
            self.assertEqual(
                [p["arxiv_id"] for p in first],
                [p["arxiv_id"] for p in second],
            )
            shared = next(p for p in first if p["arxiv_id"] == shared_id)
            topic = next(p for p in first if p["arxiv_id"] == topic_id)
            self.assertIn("daily/2026-07-28", shared["_source_note"])
            self.assertIn("weekly/2026-W31", shared["_source_note"])
            self.assertEqual(topic["_mode"], "paper")
            self.assertIn(
                "topic/cache-agents/2026-07-28", topic["_source_note"]
            )

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

    def test_topic_api_requires_admin_token(self):
        resp, body = self.post_json("/api/topic", {"action": "refresh", "slug": "opd"})
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(resp.status, 403)
        self.assertEqual(payload["error"], "forbidden")

    def test_topic_display_name_is_saved_and_rendered(self):
        old_topic_dir = web_server.topic_store.TOPIC_DIR
        old_topics_file = web_server.topic_store.TOPICS_FILE
        with tempfile.TemporaryDirectory() as tmp:
            web_server.topic_store.TOPIC_DIR = tmp
            web_server.topic_store.TOPICS_FILE = os.path.join(tmp, "topics.json")
            try:
                web_server.topic_store.upsert_topic({
                    "slug": "opd",
                    "query": "opd",
                    "display_name": "OPD 策略蒸馏",
                    "generated_terms": {"must": ["opd"], "should": [], "negative": []},
                })

                resp, body = self.request("/topic")
                html = body.decode("utf-8", errors="replace")
                self.assertEqual(resp.status, 200)
                self.assertIn("OPD 策略蒸馏", html)
                self.assertIn("query: <code>opd</code>", html)

                resp, body = self.post_json(
                    "/api/topic",
                    {"action": "update", "slug": "opd", "display_name": "策略蒸馏每日"},
                    headers={"X-Topic-Admin-Token": "test-token"},
                )
                payload = json.loads(body.decode("utf-8"))
                self.assertEqual(resp.status, 200)
                self.assertEqual(payload["topic"]["display_name"], "策略蒸馏每日")

                resp, body = self.request("/topic/opd")
                html = body.decode("utf-8", errors="replace")
                self.assertEqual(resp.status, 200)
                self.assertIn("🧭 策略蒸馏每日", html)
                self.assertIn('value="策略蒸馏每日"', html)
            finally:
                web_server.topic_store.TOPIC_DIR = old_topic_dir
                web_server.topic_store.TOPICS_FILE = old_topics_file

    def test_topic_history_keeps_older_dates_reachable(self):
        old_topic_dir = web_server.topic_store.TOPIC_DIR
        old_topics_file = web_server.topic_store.TOPICS_FILE
        with tempfile.TemporaryDirectory() as tmp:
            web_server.topic_store.TOPIC_DIR = tmp
            web_server.topic_store.TOPICS_FILE = os.path.join(
                tmp, "topics.json"
            )
            try:
                web_server.topic_store.upsert_topic({
                    "slug": "opd",
                    "query": "opd",
                    "generated_terms": {
                        "must": ["opd"],
                        "should": [],
                        "negative": [],
                    },
                })
                for day in range(1, 15):
                    key = f"2026-07-{day:02d}"
                    directory = os.path.join(tmp, "opd", key)
                    os.makedirs(directory, exist_ok=True)
                    with open(
                        os.path.join(directory, "index.json"),
                        "w",
                        encoding="utf-8",
                    ) as handle:
                        json.dump(
                            {
                                "mode": "topic",
                                "key": key,
                                "papers": [],
                            },
                            handle,
                        )

                resp, body = self.request("/topic/opd")
                html = body.decode("utf-8", errors="replace")

                self.assertEqual(resp.status, 200)
                self.assertIn("更早 2 天（最早 2026-07-01）", html)
                self.assertIn('href="/topic/opd/2026-07-01"', html)
                self.assertIn('href="/topic/opd/2026-07-14"', html)
            finally:
                web_server.topic_store.TOPIC_DIR = old_topic_dir
                web_server.topic_store.TOPICS_FILE = old_topics_file

    def test_global_paper_detail_route(self):
        resp, body = self.request(f"/detail/{SAMPLE_VIEW_ID}")
        html = body.decode("utf-8", errors="replace")
        self.assertEqual(resp.status, 200)
        self.assert_content_type(resp, "text/html")
        self.assertIn("Lens：重新思考基础文本到图像模型的训练效率", html)
        if _sample_pdf_is_publishable():
            self.assertIn(f'href="/view/{SAMPLE_VIEW_ID}"', html)
        else:
            self.assertIn("全文PDF转换失败", html)

    def test_old_global_papers_route_redirects_locally(self):
        resp, _ = self.request(f"/papers/{SAMPLE_VIEW_ID}")
        self.assertEqual(resp.status, 302)
        self.assertEqual(resp.getheader("Location"), f"/detail/{SAMPLE_VIEW_ID}")


if __name__ == "__main__":
    unittest.main()
