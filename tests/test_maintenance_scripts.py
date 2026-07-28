import fcntl
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLEANUP_SCRIPT = ROOT / "scripts" / "cleanup_docker_cache.sh"
RESTART_SCRIPT = ROOT / "scripts" / "restart_translation_container.sh"
WEEKLY_CLEANUP_SCRIPT = ROOT / "scripts" / "weekly_cleanup.sh"
ORPHAN_CLEANUP_SCRIPT = ROOT / "scripts" / "cleanup_orphan_artifacts.py"


class MaintenanceScriptsTest(unittest.TestCase):
    def _sandbox(self):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        fake_bin = root / "bin"
        fake_bin.mkdir()
        docker_log = root / "docker-calls.log"
        fake_docker = fake_bin / "docker"
        fake_docker.write_text(
            textwrap.dedent(
                """\
                #!/bin/sh
                printf '%s\\n' "$*" >> "$FAKE_DOCKER_LOG"
                if [ -n "${FAKE_DOCKER_SLEEP:-}" ]; then
                    sleep "$FAKE_DOCKER_SLEEP"
                fi
                if [ "$1" = "container" ] && [ "$2" = "inspect" ]; then
                    printf 'true\\n'
                    exit 0
                fi
                if [ "$1" = "exec" ]; then
                    case "$*" in
                        *"du -sh"*)
                            printf '2.0G /gpt/gpt_log\\n'
                            ;;
                        *)
                            printf 'DELETE /gpt/gpt_log/arxiv_cache/old-paper\\n'
                            printf 'KEEP_RECENT /gpt/gpt_log/arxiv_cache/recent-paper\\n'
                            ;;
                    esac
                    exit 0
                fi
                if [ "$1" = "restart" ]; then
                    printf '%s\\n' "$2"
                    exit 0
                fi
                exit 64
                """
            ),
            encoding="utf-8",
        )
        fake_docker.chmod(0o755)
        env = os.environ.copy()
        env.update(
            {
                "PAPER_TRANS_ROOT": str(root),
                "PAPER_TRANS_CACHE_RETENTION_DAYS": "17",
                "GPT_ACADEMIC_CONTAINER": "test-translation-container",
                "FAKE_DOCKER_LOG": str(docker_log),
                "PATH": f"{fake_bin}:/usr/bin:/bin",
            }
        )
        return temp, root, docker_log, env

    def _run(self, script, env):
        return subprocess.run(
            ["bash", str(script)],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )

    def test_scripts_have_valid_bash_syntax(self):
        for script in (CLEANUP_SCRIPT, RESTART_SCRIPT, WEEKLY_CLEANUP_SCRIPT):
            with self.subTest(script=script.name):
                result = subprocess.run(
                    ["bash", "-n", str(script)],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_both_maintenance_tasks_skip_without_calling_docker_when_busy(self):
        for script, log_name in (
            (CLEANUP_SCRIPT, "cleanup.log"),
            (RESTART_SCRIPT, "container-restart.log"),
        ):
            with self.subTest(script=script.name):
                temp, root, docker_log, env = self._sandbox()
                with temp:
                    lock_path = root / "locks" / "full-translation.lock"
                    lock_path.parent.mkdir(parents=True)
                    with lock_path.open("a+") as handle:
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        result = self._run(script, env)

                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertFalse(docker_log.exists())
                    log_text = (root / "logs" / log_name).read_text(encoding="utf-8")
                    self.assertIn("[SKIP]", log_text)
                    self.assertIn("全文翻译繁忙", log_text)

    def test_cleanup_deletes_only_aged_entries_while_idle(self):
        temp, root, docker_log, env = self._sandbox()
        with temp:
            result = self._run(CLEANUP_SCRIPT, env)

            self.assertEqual(result.returncode, 0, result.stderr)
            calls = docker_log.read_text(encoding="utf-8")
            self.assertIn("container inspect", calls)
            self.assertIn("-mtime \"+$retention_days\"", calls)
            self.assertIn("find \"$entry\" -depth -delete", calls)
            self.assertNotIn("rm -rf /gpt/gpt_log/arxiv_cache", calls)

            log_text = (root / "logs" / "cleanup.log").read_text(encoding="utf-8")
            self.assertIn("保留 17 天", log_text)
            self.assertIn("删除=1", log_text)
            self.assertIn("保留近期=1", log_text)
            self.assertIn("清理完成", log_text)

    def test_maintenance_docker_calls_time_out_with_failure(self):
        for script, log_name, expected in (
            (CLEANUP_SCRIPT, "cleanup.log", "状态超时"),
            (RESTART_SCRIPT, "container-restart.log", "重启超时"),
        ):
            with self.subTest(script=script.name):
                temp, root, _, env = self._sandbox()
                with temp:
                    env["PAPER_TRANS_DOCKER_CONTROL_TIMEOUT"] = "1"
                    env["FAKE_DOCKER_SLEEP"] = "5"
                    result = self._run(script, env)

                    self.assertEqual(result.returncode, 1, result.stderr)
                    log_text = (root / "logs" / log_name).read_text(
                        encoding="utf-8"
                    )
                    self.assertIn(expected, log_text)

    def test_cleanup_rejects_zero_retention_before_docker(self):
        temp, root, docker_log, env = self._sandbox()
        with temp:
            env["PAPER_TRANS_CACHE_RETENTION_DAYS"] = "0"
            result = self._run(CLEANUP_SCRIPT, env)

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(docker_log.exists())
            log_text = (root / "logs" / "cleanup.log").read_text(
                encoding="utf-8"
            )
            self.assertIn("必须是正整数", log_text)

    def test_restart_calls_docker_only_after_acquiring_idle_lock(self):
        temp, root, docker_log, env = self._sandbox()
        with temp:
            result = self._run(RESTART_SCRIPT, env)

            self.assertEqual(result.returncode, 0, result.stderr)
            calls = docker_log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(calls, ["restart test-translation-container"])
            log_text = (root / "logs" / "container-restart.log").read_text(
                encoding="utf-8"
            )
            self.assertIn("全文翻译空闲", log_text)
            self.assertIn("重启完成", log_text)

    def test_contract_uses_shared_lock_and_never_removes_cache_roots(self):
        cleanup = CLEANUP_SCRIPT.read_text(encoding="utf-8")
        restart = RESTART_SCRIPT.read_text(encoding="utf-8")

        for content in (cleanup, restart):
            self.assertIn("locks/full-translation.lock", content)
            self.assertIn("flock -n 9", content)

        self.assertIn("-mtime", cleanup)
        self.assertIn("-delete", cleanup)
        self.assertNotIn("rm -rf /gpt/gpt_log/arxiv_cache/", cleanup)
        self.assertNotIn("rm -rf /gpt/gpt_log/default_user/", cleanup)
        self.assertNotIn("rm -rf /gpt/gpt_log/admin/", cleanup)
        self.assertIn('docker_bounded restart "$CONTAINER"', restart)
        self.assertIn("PAPER_TRANS_DOCKER_CONTROL_TIMEOUT", cleanup)
        self.assertIn("PAPER_TRANS_DOCKER_CONTROL_TIMEOUT", restart)
        self.assertIn('timeout --signal=TERM --kill-after=5s', cleanup)
        self.assertIn('timeout --signal=TERM --kill-after=5s', restart)

    def test_weekly_orphan_cleanup_has_publication_grace_period(self):
        content = WEEKLY_CLEANUP_SCRIPT.read_text(encoding="utf-8")
        helper = ORPHAN_CLEANUP_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("cleanup_orphan_artifacts.py", content)
        self.assertIn("PAPER_TRANS_ORPHAN_GRACE_DAYS:-3", content)
        self.assertIn("--apply", content)
        self.assertNotIn("os.remove(path)", content)
        self.assertIn("DEFAULT_GRACE_DAYS = 3", helper)
        self.assertIn("catalog_publication_lock", helper)
        self.assertIn("paper_lock_path", helper)
        self.assertIn("_scan_references(paths[\"data\"])", helper)
        self.assertIn("crontab: 0 8 * * 0", content)

    def test_weekly_cleanup_uses_fixed_python_single_log_and_nonzero_failures(self):
        content = WEEKLY_CLEANUP_SCRIPT.read_text(encoding="utf-8")

        self.assertIn(
            "/root/.pyenv/versions/3.10.13/bin/python3",
            content,
        )
        self.assertIn('"$PYTHON" -m pip cache purge', content)
        self.assertNotIn("tee -a", content)
        self.assertIn('ERRORS=$((ERRORS + 1))', content)
        self.assertIn('if [ "$ERRORS" -gt 0 ]', content)
        self.assertIn("exit 1", content)
        self.assertIn("set -o pipefail", content)


if __name__ == "__main__":
    unittest.main()
