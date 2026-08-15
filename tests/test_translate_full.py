import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import translate_full


class TranslateFullCommandTest(unittest.TestCase):
    def test_driver_bundle_includes_shared_quality_gate(self):
        bundled = {
            os.path.basename(path)
            for path in translate_full.DRIVER_SUPPORT_FILES
        }
        self.assertIn("translation_quality.py", bundled)
        self.assertIn("residual_translation.py", bundled)
        self.assertIn("translation_policy.py", bundled)
        self.assertIn("latex_pipeline.py", bundled)
        with open(translate_full.DRIVER_SCRIPT, encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("translation_quality_ok as _translation_quality_ok", source)

    def test_passes_only_supported_llm_overrides_to_container(self):
        with mock.patch.dict(
            os.environ,
            {
                "HOST_PROXY": "http://127.0.0.1:7890",
                "PAPER_TRANS_LLM_HTTP_TIMEOUT": "90",
                "PAPER_TRANS_LLM_MODEL": "gpt-4o-mini",
                "PAPER_TRANS_LLM_WORKERS": "2",
                "PAPER_TRANS_EXTRA_HARD_ENVS": "customPrompt",
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
        self.assertIn("PAPER_TRANS_LLM_HTTP_TIMEOUT=90", command)
        self.assertIn("PAPER_TRANS_EXTRA_HARD_ENVS=customPrompt", command)
        self.assertNotIn("UNRELATED_SECRET", joined)
        self.assertEqual(command[-1], "2607.13399")
        self.assertIn("os.setsid()", translate_full._CONTAINER_DRIVER_LAUNCHER)
        self.assertEqual(command[-3], "-c")

    def test_container_tree_termination_is_scoped_and_structured(self):
        payload = {
            "ok": True,
            "found": True,
            "verified": True,
            "arxiv_id": "2607.13399",
            "driver_pids": [101],
            "target_pids": [101, 102],
            "survivors": [],
        }
        completed = mock.Mock(
            returncode=0,
            stdout=__import__("json").dumps(payload) + "\n",
            stderr="",
        )
        with mock.patch.object(
            translate_full.subprocess,
            "run",
            return_value=completed,
        ) as run:
            result = translate_full.terminate_container_driver_tree(
                "2607.13399",
                container_name="gpt-test",
            )

        self.assertTrue(result["verified"])
        command = run.call_args.args[0]
        self.assertEqual(command[:6], [
            "docker", "exec", "-u", "root", "gpt-test", "python3",
        ])
        self.assertEqual(command[-2:], ["terminate", "2607.13399"])
        self.assertNotIn("pkill", command)

    def test_short_docker_control_timeout_is_a_clean_failure(self):
        timeout = subprocess.TimeoutExpired(["docker", "container", "inspect"], 0.25)
        with mock.patch.object(
            translate_full,
            "_docker_control_timeout",
            return_value=0.25,
        ), mock.patch.object(
            translate_full.subprocess,
            "run",
            side_effect=timeout,
        ) as run:
            self.assertFalse(translate_full.check_container())

        self.assertEqual(run.call_args.kwargs["timeout"], 0.25)

    def test_retry_cache_cleanup_is_scoped_to_reproducible_artifacts(self):
        completed = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(
            translate_full, "_run_docker_control", return_value=completed
        ) as run:
            self.assertTrue(
                translate_full._cleanup_completed_retry_runtime_cache(
                    "2608.03571"
                )
            )

        command = run.call_args.args[0]
        self.assertEqual(command[:5], [
            "docker", "exec", "-u", "root", translate_full.CONTAINER_NAME,
        ])
        self.assertEqual(command[-1], "2608.03571")
        cleanup_source = command[-2]
        self.assertIn("shutil.rmtree(paper_cache", cleanup_source)
        self.assertIn("default_user/shared", cleanup_source)
        self.assertNotIn("find", cleanup_source)
        self.assertNotIn("arxiv_cache/*", cleanup_source)

    def test_retry_cleanup_deletes_only_files_created_during_attempt(self):
        snapshots = [
            {"/gpt/gpt_log/default_user/shared/old.zip"},
            {
                "/gpt/gpt_log/default_user/shared/old.zip",
                "/gpt/gpt_log/default_user/shared/new.zip",
            },
        ]
        completed = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(
            translate_full,
            "_snapshot_retry_runtime_files",
            side_effect=snapshots,
        ), mock.patch.object(
            translate_full,
            "_run_docker_control",
            return_value=completed,
        ) as run:
            before = translate_full._snapshot_retry_runtime_files()
            self.assertTrue(
                translate_full._cleanup_completed_retry_runtime_cache(
                    "2608.03571", baseline_files=before
                )
            )

        command = run.call_args.args[0]
        self.assertIn("/gpt/gpt_log/default_user/shared/new.zip", command)
        self.assertNotIn("/gpt/gpt_log/default_user/shared/old.zip", command)

    def test_disk_preflight_blocks_translation_at_critical_watermark(self):
        usage = (100, 96, 4)
        with mock.patch.object(
            translate_full.shutil, "disk_usage", return_value=usage
        ), mock.patch.dict(
            os.environ,
            {
                "PAPER_TRANS_MIN_FREE_MB": "0",
                "PAPER_TRANS_DISK_CRITICAL_WATERMARK": "95",
            },
        ):
            error = translate_full._disk_preflight_error()

        self.assertIn("No space left on device", error)
        self.assertIn("used=96%", error)

    def test_timed_out_tex_backup_does_not_replace_last_good_copy(self):
        arxiv_id = "2607.13399"
        with tempfile.TemporaryDirectory() as tmp:
            backup = os.path.join(
                tmp,
                f"{arxiv_id}_merge_translate_zh.tex",
            )
            with open(backup, "w", encoding="utf-8") as handle:
                handle.write("last-good")

            def docker_control(command, operation, **_):
                if command[1] == "exec":
                    return mock.Mock(returncode=0)
                with open(command[-1], "w", encoding="utf-8") as handle:
                    handle.write("partial")
                return None

            with mock.patch.object(translate_full, "TEX_BACKUP_DIR", tmp), \
                 mock.patch.object(
                     translate_full,
                     "_run_docker_control",
                     side_effect=docker_control,
                 ):
                self.assertFalse(
                    translate_full._backup_tex_from_container(arxiv_id)
                )

            with open(backup, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "last-good")
            self.assertFalse(os.path.exists(f"{backup}.tmp.{os.getpid()}"))

    def test_compile_only_restore_prefers_newer_failed_tex(self):
        arxiv_id = "2605.25874"
        with tempfile.TemporaryDirectory() as tmp:
            good_dir = os.path.join(tmp, "tex_backup")
            failed_dir = os.path.join(tmp, "tex_backup_failed")
            os.makedirs(good_dir)
            os.makedirs(failed_dir)
            filename = f"{arxiv_id}_merge_translate_zh.tex"
            good = os.path.join(good_dir, filename)
            failed = os.path.join(failed_dir, filename)
            with open(good, "w", encoding="utf-8") as handle:
                handle.write("old untranslated tex")
            with open(failed, "w", encoding="utf-8") as handle:
                handle.write("new translated tex")
            os.utime(good, ns=(1_000_000_000, 1_000_000_000))
            os.utime(failed, ns=(2_000_000_000, 2_000_000_000))

            copied = []

            def docker_control(command, operation, **_):
                if command[1] == "cp":
                    copied.append(command[2])
                return mock.Mock(returncode=0)

            with mock.patch.object(translate_full, "TEX_BACKUP_DIR", good_dir), \
                 mock.patch.object(
                     translate_full,
                     "TEX_FAILED_BACKUP_DIR",
                     failed_dir,
                 ), \
                 mock.patch.object(
                     translate_full,
                     "_run_docker_control",
                     side_effect=docker_control,
                 ), \
                 mock.patch.object(
                     translate_full,
                     "_ensure_workfolder_writable",
                     return_value=True,
                 ):
                self.assertTrue(
                    translate_full._restore_tex_to_container(arxiv_id)
                )

            self.assertEqual(copied, [failed])

    @unittest.skipUnless(os.path.isdir("/proc"), "requires Linux /proc")
    def test_process_helper_terminates_real_scoped_descendant_tree(self):
        aid = "2999.99999"
        root_code = (
            "import subprocess,sys,time;"
            "subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);"
            "time.sleep(30)"
        )
        proc = subprocess.Popen(
            [
                sys.executable,
                "-c",
                root_code,
                "/tmp/full_translate_driver.py",
                aid,
            ],
            start_new_session=True,
        )
        reaper = threading.Thread(target=proc.wait, daemon=True)
        reaper.start()
        try:
            time.sleep(0.2)
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    translate_full._CONTAINER_PROCESS_TREE_HELPER,
                    "terminate",
                    aid,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=8,
            )
            payload = json.loads(completed.stdout.strip().splitlines()[-1])
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(payload["found"])
            self.assertTrue(payload["verified"], payload)
            self.assertGreaterEqual(len(payload["target_pids"]), 2)
            reaper.join(timeout=2)
            self.assertIsNotNone(proc.poll())
        finally:
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            reaper.join(timeout=2)

    def test_timeout_stops_container_tree_before_reaping_docker_exec(self):
        class FakeProcess:
            def __init__(self, stdout):
                self.stdout = stdout

            def poll(self):
                return None

        with tempfile.TemporaryFile() as output:
            proc = FakeProcess(output)
            calls = mock.Mock()
            calls.tree = mock.Mock(return_value={
                "ok": True,
                "found": True,
                "verified": True,
                "survivors": [],
            })
            calls.client = mock.Mock()
            with mock.patch.object(
                translate_full.subprocess, "Popen", return_value=proc,
            ), mock.patch.object(
                translate_full,
                "_terminate_container_driver",
                calls.tree,
            ), mock.patch.object(
                translate_full,
                "_stop_docker_exec_client",
                calls.client,
            ), mock.patch.object(
                translate_full.time,
                "time",
                side_effect=[0.0, 0.0, 2.0],
            ), mock.patch.object(translate_full.time, "sleep"):
                rc, _, error = translate_full.run_in_container(
                    "2607.13399", no_cache=False, timeout=1,
                )

        self.assertEqual(rc, -1)
        self.assertIn("超时", error)
        self.assertEqual(calls.mock_calls[:2], [
            mock.call.tree("2607.13399"),
            mock.call.client(proc),
        ])

    def test_keyboard_interrupt_still_cleans_scoped_container_tree(self):
        class FakeProcess:
            def __init__(self, stdout):
                self.stdout = stdout

            def poll(self):
                return None

        with tempfile.TemporaryFile() as output:
            proc = FakeProcess(output)
            cleanup = mock.Mock(return_value={"verified": True})
            with mock.patch.object(
                translate_full.subprocess, "Popen", return_value=proc,
            ), mock.patch.object(
                translate_full, "_cleanup_container_driver", cleanup,
            ), mock.patch.object(
                translate_full.time, "time", side_effect=[0.0, 0.0, 0.0],
            ), mock.patch.object(
                translate_full.time, "sleep", side_effect=KeyboardInterrupt,
            ):
                with self.assertRaises(KeyboardInterrupt):
                    translate_full.run_in_container(
                        "2607.13399", no_cache=False, timeout=60,
                    )

        cleanup.assert_called_once_with(proc, "2607.13399")

    def test_terminate_rejects_unscoped_or_invalid_target(self):
        with self.assertRaises(ValueError):
            translate_full._container_process_tree_action("terminate")
        with self.assertRaises(ValueError):
            translate_full.terminate_container_driver_tree("../other")

    def test_driver_disables_shell_escape_and_restricts_tex_io(self):
        pipeline = os.path.join(
            translate_full.BASE_DIR,
            "paperhub",
            "latex_pipeline.py",
        )
        with open(pipeline, encoding="utf-8") as handle:
            source = handle.read()

        self.assertIn('"openin_any": "p"', source)
        self.assertIn('"openout_any": "p"', source)
        self.assertIn('"shell_escape": "0"', source)
        self.assertIn("'-no-shell-escape'", source)

    def test_driver_compile_wrapper_rejects_nonzero_tex_exit(self):
        """A TeX failure must not advance the upstream compile-success path."""
        pipeline = os.path.join(
            translate_full.BASE_DIR,
            "paperhub",
            "latex_pipeline.py",
        )
        with open(pipeline, encoding="utf-8") as handle:
            source = handle.read()

        self.assertIn("return process.returncode == 0", source)

    def test_translation_driver_is_lifecycle_sized(self):
        """TeX adapters and schedulers must not grow back into the driver."""
        with open(translate_full.DRIVER_SCRIPT, encoding="utf-8") as handle:
            source = handle.read()

        self.assertLessEqual(len(source.splitlines()), 1100)
        self.assertIn("_install_gpt_academic_latex_patches()", source)
        self.assertNotIn("def _patched_compile_with_timeout", source)

    def test_driver_bounds_slow_source_prefetch_and_validates_length(self):
        """A trickling arXiv response cannot hold the global lock indefinitely."""
        with open(translate_full.DRIVER_SCRIPT, encoding="utf-8") as handle:
            source = handle.read()

        self.assertIn("PAPER_TRANS_SOURCE_TOTAL_SECONDS", source)
        self.assertIn("PAPER_TRANS_SOURCE_ATTEMPT_SECONDS", source)
        self.assertIn("PAPER_TRANS_SOURCE_MIN_BYTES_PER_SECOND", source)
        self.assertIn("time.monotonic()", source)
        self.assertIn("Content-Length", source)
        self.assertIn("incomplete source archive", source)

    def test_global_translation_lock_serializes_entrypoints(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "full-translation.lock")
            first = translate_full.GlobalTranslationLock(
                "2607.00001",
                wait_seconds=0,
                lock_path=path,
            )
            second = translate_full.GlobalTranslationLock(
                "2607.00002",
                wait_seconds=0,
                lock_path=path,
            )

            with first:
                with self.assertRaises(TimeoutError):
                    second.__enter__()

            with translate_full.GlobalTranslationLock(
                "2607.00002",
                wait_seconds=0,
                lock_path=path,
            ):
                pass

    def test_global_lock_honors_maintenance_lock_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "shared", "translation.lock")
            with mock.patch.dict(
                os.environ,
                {"PAPER_TRANS_FULL_TRANSLATION_LOCK": path},
            ):
                lock = translate_full.GlobalTranslationLock(
                    "2607.00003",
                    wait_seconds=0,
                )
            self.assertEqual(lock.path, path)
            self.assertTrue(os.path.isdir(os.path.dirname(path)))

    def test_local_pdf_integrity_uses_shared_header_and_eof_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper.pdf")
            with open(path, "wb") as handle:
                handle.write(b"not-a-pdf" + b"x" * 12000 + b"\n%%EOF\n")
            self.assertFalse(translate_full.check_local_pdf_integrity(path))
            with open(path, "wb") as handle:
                handle.write(b"%PDF-1.7\n" + b"x" * 12000 + b"\n%%EOF\n")
            self.assertTrue(translate_full.check_local_pdf_integrity(path))


if __name__ == "__main__":
    unittest.main()
