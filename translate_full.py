#!/usr/bin/env python3
"""
全文翻译入口脚本 (容器外调用)
使用 docker exec 在 GPT_ACADEMIC_CONTAINER 指定的容器内运行驱动脚本，
翻译 arxiv 论文全文（LaTeX → 中文 PDF），然后 docker cp 取回 PDF。

用法:
  python3 translate_full.py <arxiv_id> -o <output_dir> [--no-cache] [--keep-translation] [--timeout 3600]
"""

import subprocess
import sys
import os
import argparse
import time
import shutil
import json
import fcntl
import re
import math
from pathlib import Path

from failure_taxonomy import classify_failure
from paperhub.json_io import write_json_atomic
from paperhub.paper_store import pdf_file_valid
from paperhub.publication_lock import paper_publication_lock
from paperhub.paths import (
    ROOT_DIR as BASE_DIR,
    DEFAULT_GPT_ACADEMIC_CONTAINER,
    LOCK_DIR,
    TEX_BACKUP_DIR,
    TEX_FAILED_BACKUP_DIR,
)

DEFAULT_CONTAINER_NAME = DEFAULT_GPT_ACADEMIC_CONTAINER
CONTAINER_NAME  = os.environ.get("GPT_ACADEMIC_CONTAINER", DEFAULT_CONTAINER_NAME)
DRIVER_SCRIPT   = os.path.join(BASE_DIR, "full_translate_driver.py")
DRIVER_SUPPORT_FILES = [
    DRIVER_SCRIPT,
    os.path.join(BASE_DIR, "latex_translation_filters.py"),
    os.path.join(BASE_DIR, "paperhub", "translation_policy.py"),
    os.path.join(BASE_DIR, "paperhub", "latex_pipeline.py"),
    os.path.join(BASE_DIR, "paperhub", "translation_runtime.py"),
    os.path.join(BASE_DIR, "failure_taxonomy.py"),
    os.path.join(BASE_DIR, "paperhub", "translation_quality.py"),
    os.path.join(BASE_DIR, "paperhub", "residual_translation.py"),
]
# 容器内 gpt_log/arxiv_cache 对应的绝对路径
CONTAINER_CACHE = "/gpt/gpt_log/arxiv_cache"
# 宿主机侧 tex 备份目录（容器重启后可从这里恢复翻译缓存，避免重复调 GPT）

_ARXIV_ID_RE = re.compile(r"^\d{4}\.\d{4,5}$")
DEFAULT_DOCKER_CONTROL_TIMEOUT = 30.0
MAX_DOCKER_CONTROL_TIMEOUT = 600.0
DEFAULT_MIN_FREE_MB = 2048
DEFAULT_DISK_CRITICAL_WATERMARK = 95


def _docker_control_timeout() -> float:
    """Return a bounded timeout for short Docker control-plane operations."""
    raw = os.environ.get(
        "PAPER_TRANS_DOCKER_CONTROL_TIMEOUT",
        str(DEFAULT_DOCKER_CONTROL_TIMEOUT),
    )
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = DEFAULT_DOCKER_CONTROL_TIMEOUT
    if not math.isfinite(value) or value <= 0:
        value = DEFAULT_DOCKER_CONTROL_TIMEOUT
    return min(MAX_DOCKER_CONTROL_TIMEOUT, value)


def _run_docker_control(command, operation, **kwargs):
    """Run one short Docker command without allowing it to hold the global lock forever."""
    try:
        return subprocess.run(
            command,
            timeout=_docker_control_timeout(),
            **kwargs,
        )
    except subprocess.TimeoutExpired:
        print(
            f"❌ Docker 操作超时（{_docker_control_timeout():g}s）: {operation}",
            flush=True,
        )
    except OSError as exc:
        print(f"❌ Docker 操作失败: {operation} ({exc})", flush=True)
    return None


def _bounded_nonnegative_int(env_name, default):
    raw = os.environ.get(env_name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(0, value)


def _disk_preflight_error():
    """Return an ENOSPC-style message when translation must not start."""
    total, used, free = shutil.disk_usage(BASE_DIR)
    free_mb = free // (1024 * 1024)
    used_pct = int(round((used * 100.0) / total)) if total else 100
    min_free_mb = _bounded_nonnegative_int(
        "PAPER_TRANS_MIN_FREE_MB", DEFAULT_MIN_FREE_MB
    )
    critical = min(
        100,
        _bounded_nonnegative_int(
            "PAPER_TRANS_DISK_CRITICAL_WATERMARK",
            DEFAULT_DISK_CRITICAL_WATERMARK,
        ),
    )
    if free_mb < min_free_mb or used_pct >= critical:
        return (
            "OSError: [Errno 28] No space left on device (preflight): "
            f"used={used_pct}% free={free_mb}MB "
            f"required_free={min_free_mb}MB critical={critical}%"
        )
    return ""


def _snapshot_retry_runtime_files():
    """Return exact transient output files present before one locked retry."""
    roots = (
        "/gpt/gpt_log/default_user/shared",
        "/gpt/gpt_log/default_user/downloadzone",
    )
    command = [
        "docker", "exec", CONTAINER_NAME, "sh", "-c",
        "for root in \"$@\"; do "
        "[ -d \"$root\" ] || continue; "
        "find \"$root\" -type f -print; "
        "find \"$root\" -type l -print; done",
        "sh", *roots,
    ]
    completed = _run_docker_control(
        command,
        "记录 retry 前的容器临时输出",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed is None or completed.returncode != 0:
        return None
    return {
        line.strip()
        for line in (completed.stdout or "").splitlines()
        if line.strip()
    }


def _cleanup_completed_retry_runtime_cache(arxiv_id, baseline_files=None):
    """Drop one paper cache and only transient files created by this retry."""
    if not _ARXIV_ID_RE.fullmatch(str(arxiv_id or "")):
        raise ValueError(f"invalid arXiv ID: {arxiv_id!r}")

    after_files = _snapshot_retry_runtime_files()
    created_files = []
    if baseline_files is not None and after_files is not None:
        created_files = sorted(after_files - set(baseline_files))

    cleanup_script = r'''
import os
import shutil
import sys

aid = sys.argv[1]
cache_root = os.path.realpath("/gpt/gpt_log/arxiv_cache")
paper_cache = os.path.realpath(os.path.join(cache_root, aid))
if os.path.dirname(paper_cache) != cache_root:
    raise SystemExit("unsafe paper cache path")
shutil.rmtree(paper_cache, ignore_errors=True)

allowed_roots = tuple(map(os.path.realpath, (
    "/gpt/gpt_log/default_user/shared",
    "/gpt/gpt_log/default_user/downloadzone",
)))
for raw_path in sys.argv[2:]:
    path = os.path.realpath(raw_path)
    if not any(os.path.commonpath((path, root)) == root for root in allowed_roots):
        raise SystemExit("unsafe transient output path")
    if os.path.isfile(path) or os.path.islink(path):
        os.unlink(path)
'''
    command = [
        "docker", "exec", "-u", "root", CONTAINER_NAME,
        "python3", "-c", cleanup_script, arxiv_id, *created_files,
    ]
    completed = _run_docker_control(
        command,
        f"清理 {arxiv_id} 已完成 retry 的容器缓存",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed is None or completed.returncode != 0:
        print(f"⚠️  retry 容器缓存清理失败: {arxiv_id}", flush=True)
        return False
    print(
        f"🧹 已回收 retry 容器缓存: {arxiv_id} "
        f"(本次临时输出={len(created_files)})",
        flush=True,
    )
    return True


def _cleanup_retry_cache_enabled():
    return os.environ.get("PAPER_TRANS_CLEAN_RETRY_CACHE", "").strip().lower() in {
        "1", "true", "yes", "on",
    }

# docker exec 默认不保证容器内命令拥有独立进程组。驱动先建立新 session，
# 让超时/人工终止可以只向该论文的进程组发信号，而不会波及其他 docker exec。
_CONTAINER_DRIVER_LAUNCHER = r"""
import ctypes
import os
import signal
import sys
import time

argv = ["python3", "/tmp/full_translate_driver.py"] + sys.argv[1:]
try:
    # Linux PR_SET_CHILD_SUBREAPER：驱动被终止后，由这个轻量 supervisor
    # 接管并回收孤儿孙进程，避免容器 PID 1（tail）留下永久 zombie。
    ctypes.CDLL(None).prctl(36, 1, 0, 0, 0)
except Exception:
    pass

child = os.fork()
if child == 0:
    os.setsid()
    os.execvp(argv[0], argv)

_, driver_status = os.waitpid(child, 0)


def adopted_descendants():
    found = []
    me = os.getpid()
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open("/proc/%s/stat" % name, encoding="utf-8") as handle:
                stat = handle.read()
            fields = stat[stat.rfind(")") + 2:].split()
            if int(fields[1]) == me:
                found.append(int(name))
        except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError):
            continue
    return found


def reap_nonblocking():
    while True:
        try:
            waited, _ = os.waitpid(-1, os.WNOHANG)
        except (ChildProcessError, InterruptedError):
            return
        if waited == 0:
            return


# 正常/异常退出都收束残留 descendants。子进程成为 subreaper 的直接子进程
# 后逐个 TERM，再用 KILL 兜底；不会触碰其他 docker exec 的进程。
for sig, grace in ((signal.SIGTERM, 0.5), (signal.SIGKILL, 0.5)):
    deadline = time.monotonic() + grace
    signaled = set()
    while time.monotonic() < deadline:
        reap_nonblocking()
        remaining = adopted_descendants()
        if not remaining:
            break
        for pid in remaining:
            if pid in signaled:
                continue
            try:
                os.kill(pid, sig)
                signaled.add(pid)
            except (ProcessLookupError, PermissionError):
                pass
        time.sleep(0.05)
reap_nonblocking()

if os.WIFEXITED(driver_status):
    raise SystemExit(os.WEXITSTATUS(driver_status))
if os.WIFSIGNALED(driver_status):
    os.kill(os.getpid(), os.WTERMSIG(driver_status))
raise SystemExit(1)
""".strip()

# 在容器内读取 /proc，按“驱动精确 argv + arXiv ID”定位根进程，并递归处理
# descendants。starttime 用来防止等待期间 PID 被复用后误杀无关进程。
_CONTAINER_PROCESS_TREE_HELPER = r"""
import json
import os
import re
import signal
import sys
import time

DRIVER = "/tmp/full_translate_driver.py"
ID_RE = re.compile(r"^\d{4}\.\d{4,5}$")


def snapshot():
    result = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        pid = int(name)
        try:
            with open("/proc/%s/cmdline" % name, "rb") as handle:
                argv = [
                    part.decode("utf-8", "replace")
                    for part in handle.read().split(b"\0")
                    if part
                ]
            with open("/proc/%s/stat" % name, encoding="utf-8") as handle:
                stat = handle.read()
            fields = stat[stat.rfind(")") + 2:].split()
            result[pid] = {
                "pid": pid,
                "ppid": int(fields[1]),
                "pgid": int(fields[2]),
                "starttime": int(fields[19]),
                "argv": argv,
            }
        except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError):
            continue
    return result


def driver_id(proc):
    argv = proc["argv"]
    for pos, arg in enumerate(argv[:-1]):
        if arg == DRIVER and ID_RE.fullmatch(argv[pos + 1]):
            return argv[pos + 1]
    return ""


def matching_drivers(table, selector):
    found = []
    for proc in table.values():
        arxiv_id = driver_id(proc)
        if arxiv_id and (not selector or selector == arxiv_id):
            item = dict(proc)
            item.pop("argv", None)
            item["arxiv_id"] = arxiv_id
            found.append(item)
    return sorted(found, key=lambda item: item["pid"])


def descendants(table, roots):
    children = {}
    for proc in table.values():
        children.setdefault(proc["ppid"], []).append(proc["pid"])
    found = set(roots)
    pending = list(roots)
    while pending:
        parent = pending.pop()
        for child in children.get(parent, ()):
            if child not in found:
                found.add(child)
                pending.append(child)
    return found


def same_process(pid, starttime):
    try:
        with open("/proc/%d/stat" % pid, encoding="utf-8") as handle:
            stat = handle.read()
        fields = stat[stat.rfind(")") + 2:].split()
        return int(fields[19]) == starttime
    except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError):
        return False


def signal_pid(pid, sig):
    try:
        os.kill(pid, sig)
        return True
    except (ProcessLookupError, PermissionError):
        return False


action = sys.argv[1] if len(sys.argv) > 1 else "list"
selector = sys.argv[2] if len(sys.argv) > 2 else ""
if selector and not ID_RE.fullmatch(selector):
    print(json.dumps({"ok": False, "error": "invalid arxiv id"}))
    raise SystemExit(2)

table = snapshot()
drivers = matching_drivers(table, selector)
if action == "list":
    print(json.dumps({"ok": True, "drivers": drivers}, sort_keys=True))
    raise SystemExit(0)
if action != "terminate":
    print(json.dumps({"ok": False, "error": "invalid action"}))
    raise SystemExit(2)
if not selector:
    print(json.dumps({"ok": False, "error": "arxiv id required"}))
    raise SystemExit(2)
if not drivers:
    print(json.dumps({
        "ok": False, "found": False, "verified": True, "arxiv_id": selector,
        "driver_pids": [], "target_pids": [], "survivors": [],
    }, sort_keys=True))
    raise SystemExit(0)

root_pids = [item["pid"] for item in drivers]
target_pids = descendants(table, root_pids)
identities = {
    pid: table[pid]["starttime"]
    for pid in target_pids
    if pid in table
}
# 新启动的驱动由 launcher 保证 pgid == driver pid。旧进程不满足这个
# 条件时只递归按 PID 清理，避免向共享进程组发信号。
isolated_groups = {
    table[pid]["pgid"]
    for pid in root_pids
    if table[pid]["pgid"] == pid and pid > 1
}

term_pids = []
for pgid in sorted(isolated_groups):
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
for pid in sorted(target_pids):
    if table[pid]["pgid"] not in isolated_groups and signal_pid(pid, signal.SIGTERM):
        term_pids.append(pid)

deadline = time.monotonic() + 2.0
survivors = []
while time.monotonic() < deadline:
    survivors = [
        pid for pid, started in identities.items()
        if same_process(pid, started)
    ]
    if not survivors:
        break
    time.sleep(0.1)

kill_pids = []
if survivors:
    for pgid in sorted(isolated_groups):
        if not any(
            pid in survivors and table[pid]["pgid"] == pgid
            for pid in target_pids
        ):
            continue
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    for pid in survivors:
        if table[pid]["pgid"] not in isolated_groups and signal_pid(pid, signal.SIGKILL):
            kill_pids.append(pid)
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        survivors = [
            pid for pid, started in identities.items()
            if same_process(pid, started)
        ]
        if not survivors:
            break
        time.sleep(0.05)

remaining_drivers = matching_drivers(snapshot(), selector)
verified = not survivors and not remaining_drivers
print(json.dumps({
    "ok": verified,
    "found": True,
    "verified": verified,
    "arxiv_id": selector,
    "driver_pids": root_pids,
    "target_pids": sorted(target_pids),
    "term_pids": term_pids,
    "kill_pids": kill_pids,
    "survivors": survivors,
    "remaining_driver_pids": [item["pid"] for item in remaining_drivers],
}, sort_keys=True))
""".strip()


def _container_workfolder(arxiv_id: str) -> str:
    return f"{CONTAINER_CACHE}/{arxiv_id}/workfolder"


def _container_translated_tex(arxiv_id: str) -> str:
    return f"{_container_workfolder(arxiv_id)}/merge_translate_zh.tex"


def _container_tex_exists(arxiv_id: str) -> bool:
    result = _run_docker_control(
        ["docker", "exec", CONTAINER_NAME, "test", "-s", _container_translated_tex(arxiv_id)],
        f"检查翻译 TeX {arxiv_id}",
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return result is not None and result.returncode == 0


def _ensure_workfolder_writable(arxiv_id: str) -> bool:
    workfolder = _container_workfolder(arxiv_id)
    chown = _run_docker_control(
        ["docker", "exec", "-u", "root", CONTAINER_NAME,
         "chown", "-R", "gptuser:gptuser", workfolder],
        f"修复 workfolder owner {arxiv_id}",
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if chown is None or chown.returncode != 0:
        print(f"⚠️  重设容器 workfolder owner 失败: {workfolder}", flush=True)
        return False
    chmod = _run_docker_control(
        ["docker", "exec", "-u", "root", CONTAINER_NAME,
         "chmod", "-R", "u+rw", workfolder],
        f"修复 workfolder mode {arxiv_id}",
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    ok = chmod is not None and chmod.returncode == 0
    if not ok:
        print(f"⚠️  重设容器 workfolder 权限失败: {workfolder}", flush=True)
    return ok


def _backup_tex_from_container(arxiv_id: str, failed: bool = False) -> bool:
    """
    将容器内已翻译的 merge_translate_zh.tex 备份到宿主机 TEX_BACKUP_DIR。
    容器重启后可通过 _restore_tex_to_container 恢复，避免重新调用 GPT 翻译。
    返回是否备份成功。
    """
    container_tex = _container_translated_tex(arxiv_id)
    # 先确认文件在容器内存在且非空
    check = _run_docker_control(
        ["docker", "exec", CONTAINER_NAME, "test", "-s", container_tex],
        f"检查待备份 TeX {arxiv_id}",
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if check is None or check.returncode != 0:
        return False
    backup_dir = TEX_FAILED_BACKUP_DIR if failed else TEX_BACKUP_DIR
    os.makedirs(backup_dir, exist_ok=True)
    local_tex = os.path.join(backup_dir, f"{arxiv_id}_merge_translate_zh.tex")
    staged_tex = f"{local_tex}.tmp.{os.getpid()}"
    try:
        os.remove(staged_tex)
    except FileNotFoundError:
        pass
    except OSError as exc:
        print(f"⚠️  无法清理旧 TeX 备份临时文件: {staged_tex} ({exc})", flush=True)
        return False
    r = _run_docker_control(
        ["docker", "cp", f"{CONTAINER_NAME}:{container_tex}", staged_tex],
        f"备份翻译 TeX {arxiv_id}",
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    ok = (
        r is not None
        and r.returncode == 0
        and os.path.exists(staged_tex)
        and os.path.getsize(staged_tex) > 0
    )
    if ok:
        try:
            os.replace(staged_tex, local_tex)
        except OSError as exc:
            print(f"⚠️  翻译 TeX 备份落盘失败: {local_tex} ({exc})", flush=True)
            try:
                os.remove(staged_tex)
            except OSError:
                pass
            return False
        label = "失败现场 tex" if failed else "翻译 tex"
        print(f"💾 已备份{label} 到宿主机: {local_tex}", flush=True)
    else:
        try:
            os.remove(staged_tex)
        except OSError:
            pass
    return ok


def _restore_tex_to_container(arxiv_id: str) -> bool:
    """
    将宿主机备份的 merge_translate_zh.tex 恢复到容器内 workfolder。
    返回是否恢复成功。
    """
    filename = f"{arxiv_id}_merge_translate_zh.tex"
    candidates = [
        os.path.join(TEX_BACKUP_DIR, filename),
        os.path.join(TEX_FAILED_BACKUP_DIR, filename),
    ]
    candidates = [
        path
        for path in candidates
        if os.path.isfile(path) and os.path.getsize(path) > 0
    ]
    if not candidates:
        return False
    # A failed compile after a successful retranslation deliberately leaves the
    # newer, higher-quality TeX in tex_backup_failed while preserving the last
    # published backup. Compile-only repair must use that newest work product;
    # otherwise it silently recompiles the stale untranslated TeX.
    local_tex = max(
        candidates,
        key=lambda path: (
            os.stat(path).st_mtime_ns,
            os.path.abspath(path).startswith(
                os.path.abspath(TEX_FAILED_BACKUP_DIR) + os.sep
            ),
        ),
    )
    workfolder = _container_workfolder(arxiv_id)
    # 确保容器内目标目录存在
    mkdir = _run_docker_control(
        ["docker", "exec", CONTAINER_NAME, "mkdir", "-p", workfolder],
        f"创建 workfolder {arxiv_id}",
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if mkdir is None or mkdir.returncode != 0:
        print(f"⚠️  容器 workfolder 创建失败: {workfolder}", flush=True)
        return False
    container_tex = _container_translated_tex(arxiv_id)
    r = _run_docker_control(
        ["docker", "cp", local_tex, f"{CONTAINER_NAME}:{container_tex}"],
        f"恢复翻译 TeX {arxiv_id}",
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    ok = r is not None and r.returncode == 0
    if ok:
        # docker cp writes files as root. The driver runs as gptuser and needs to
        # rewrite merge_translate_zh.tex during keep-translation repair passes.
        ok = _ensure_workfolder_writable(arxiv_id)
        if ok:
            print(f"♻️  已从宿主机恢复翻译 tex 到容器: {container_tex} (来自 {os.path.basename(os.path.dirname(local_tex))})", flush=True)
    return ok


def _prepare_keep_translation(arxiv_id: str) -> bool:
    """Prepare an existing translated tex for a compile-only retry."""
    if _restore_tex_to_container(arxiv_id):
        return True
    if _container_tex_exists(arxiv_id):
        if _ensure_workfolder_writable(arxiv_id):
            print(f"♻️  容器内已有翻译 tex，直接复用: {_container_translated_tex(arxiv_id)}", flush=True)
            return True
    return False


def check_container():
    r = _run_docker_control(
        ["docker", "container", "inspect", "-f", "{{.State.Running}}", CONTAINER_NAME],
        "检查翻译容器状态",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    return r is not None and r.returncode == 0 and r.stdout.strip() == "true"


def copy_driver_to_container():
    """将驱动脚本及其纯 Python 支持模块复制进容器"""
    copied = []
    for src in DRIVER_SUPPORT_FILES:
        name = os.path.basename(src)
        dst = f"{CONTAINER_NAME}:/tmp/{name}"
        r = _run_docker_control(
            ["docker", "cp", src, dst],
            f"复制容器驱动支持文件 {name}",
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True,
        )
        if r is None or r.returncode != 0:
            if r is None:
                return False
            msg = (r.stderr or r.stdout or "").strip()
            if msg:
                print(f"❌ 复制 {name} 到容器失败: {msg}", flush=True)
            return False
        copied.append(f"/tmp/{name}")
    chmod = _run_docker_control(
        ["docker", "exec", "-u", "root", CONTAINER_NAME, "chmod", "0644", *copied],
        "设置容器驱动支持文件权限",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    if chmod is None:
        return False
    if chmod.returncode != 0:
        msg = (chmod.stderr or chmod.stdout or "").strip()
        if msg:
            print(f"❌ 设置容器驱动脚本权限失败: {msg}", flush=True)
    return chmod.returncode == 0


def _container_process_tree_action(action: str, arxiv_id: str = "",
                                   container_name: str = None):
    """Run the exact-driver /proc helper and return its structured result."""
    if action not in ("list", "terminate"):
        raise ValueError(f"unsupported process action: {action}")
    if arxiv_id and not _ARXIV_ID_RE.fullmatch(arxiv_id):
        raise ValueError(f"invalid arXiv ID: {arxiv_id}")
    if action == "terminate" and not arxiv_id:
        raise ValueError("terminate requires an arXiv ID")

    cmd = [
        "docker", "exec", "-u", "root", container_name or CONTAINER_NAME,
        "python3", "-c", _CONTAINER_PROCESS_TREE_HELPER, action,
    ]
    if arxiv_id:
        cmd.append(arxiv_id)
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": str(exc), "drivers": []}

    output = (result.stdout or "").strip().splitlines()
    try:
        payload = json.loads(output[-1]) if output else {}
    except (TypeError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    if result.returncode != 0 or not payload:
        detail = (result.stderr or result.stdout or "").strip()
        payload.setdefault("ok", False)
        payload.setdefault(
            "error",
            detail or f"container process helper exited {result.returncode}",
        )
    return payload


def list_container_drivers(container_name: str = None):
    """Return exact full-translation driver processes currently in the container."""
    result = _container_process_tree_action(
        "list", container_name=container_name,
    )
    if not result.get("ok"):
        raise RuntimeError(
            result.get("error") or "unable to inspect container drivers"
        )
    drivers = result.get("drivers", [])
    if not isinstance(drivers, list):
        raise RuntimeError("container driver response is malformed")
    return drivers


def terminate_container_driver_tree(arxiv_id: str, container_name: str = None):
    """TERM/KILL one paper's complete container process tree and verify exit."""
    return _container_process_tree_action(
        "terminate", arxiv_id=arxiv_id, container_name=container_name,
    )


def _terminate_container_driver(arxiv_id: str):
    """Compatibility wrapper used by timeout/error cleanup."""
    return terminate_container_driver_tree(arxiv_id)


def _stop_docker_exec_client(proc, grace_seconds: float = 2.0):
    """Reap the host docker-exec client after the container tree has stopped."""
    if proc.poll() is not None:
        return
    try:
        proc.wait(timeout=grace_seconds)
        return
    except subprocess.TimeoutExpired:
        pass
    proc.terminate()
    try:
        proc.wait(timeout=1)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass


def _cleanup_container_driver(proc, arxiv_id: str):
    """Stop the scoped container tree first, then reap docker exec."""
    cleanup = _terminate_container_driver(arxiv_id)
    _stop_docker_exec_client(proc)
    if not cleanup.get("verified"):
        detail = cleanup.get("error") or cleanup.get("survivors") or "unknown"
        print(
            f"⚠️  容器翻译进程树清理未通过验证 ({arxiv_id}): {detail}",
            flush=True,
        )
    return cleanup


def _container_driver_command(arxiv_id: str):
    """Build docker exec command with an explicit, bounded LLM env allowlist."""
    cmd = ["docker", "exec"]
    for name in (
        "HOST_PROXY",
        "PAPER_TRANS_LLM_HTTP_TIMEOUT",
        "PAPER_TRANS_LLM_MODEL",
        "PAPER_TRANS_LLM_WORKERS",
        "PAPER_TRANS_LLM_RETRIES",
        "PAPER_TRANS_FAILED_CHUNK_RETRY_ROUNDS",
        "PAPER_TRANS_FORCE_RESIDUAL_REPAIR",
        "PAPER_TRANS_EXPAND_TRANSLATION_SPLIT",
        "PAPER_TRANS_EXTRA_HARD_ENVS",
        "PAPER_TRANS_EXTRA_SOFT_ENVS",
        "PAPER_TRANS_EXTRA_RESTORE_ENVS",
        "PAPER_TRANS_EXTRA_LLM_ARTIFACT_PATTERNS",
    ):
        value = os.environ.get(name)
        if value:
            cmd.extend(["-e", f"{name}={value}"])
    cmd.extend([
        CONTAINER_NAME,
        "python3", "-c", _CONTAINER_DRIVER_LAUNCHER, arxiv_id,
    ])
    return cmd


def run_in_container(arxiv_id: str, no_cache: bool, timeout: int,
                     keep_translation: bool = False):
    """
    在容器内运行翻译驱动，实时流式打印进度，返回 (returncode, stdout_full, "")
    每 30s 打印一次心跳，避免长时间无输出让人误以为卡死。
    """
    cmd = _container_driver_command(arxiv_id)
    if no_cache:
        cmd.append("--no-cache")
    if keep_translation:
        cmd.append("--keep-translation")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,   # 合并 stderr → stdout
    )

    collected = []
    t_start   = time.time()
    t_beat    = t_start   # 上次心跳时间
    BEAT_INTERVAL = 30    # 秒
    pending = b""

    def _emit_line(line_b: bytes):
        line = line_b.decode("utf-8", errors="replace").rstrip()
        collected.append(line)
        # 只打印有意义的行（驱动标记 + 结果）
        if any(tag in line for tag in ("[driver]", "RESULT:", "✅", "❌", "⚠")):
            elapsed = int(time.time() - t_start)
            print(f"   [{elapsed:4d}s] {line}", flush=True)

    if proc.stdout is None:
        return -1, "", "无法读取容器输出"
    fd = proc.stdout.fileno()
    try:
        os.set_blocking(fd, False)
    except Exception:
        pass

    def _drain_stdout():
        nonlocal pending
        while True:
            try:
                chunk = os.read(fd, 65536)
            except BlockingIOError:
                break
            except OSError:
                break
            if not chunk:
                break
            pending += chunk
            while True:
                pos = pending.find(b"\n")
                if pos < 0:
                    break
                line_b = pending[:pos]
                pending = pending[pos + 1:]
                _emit_line(line_b)

    try:
        while True:
            # 非阻塞检查进程是否结束
            retcode = proc.poll()
            _drain_stdout()

            # 心跳：距上次心跳超过 BEAT_INTERVAL 且进程还在运行
            now = time.time()
            if retcode is None and now - t_beat >= BEAT_INTERVAL:
                elapsed = int(now - t_start)
                print(f"   ⏳ 翻译进行中... 已用 {elapsed}s / {timeout}s", flush=True)
                t_beat = now

            if retcode is not None:
                # 进程已结束，读尽剩余输出
                _drain_stdout()
                if pending:
                    _emit_line(pending)
                    pending = b""
                return retcode, "\n".join(collected), ""

            if time.time() - t_start > timeout:
                cleanup = _cleanup_container_driver(proc, arxiv_id)
                suffix = ""
                if not cleanup.get("verified"):
                    suffix = "；容器进程树清理未完全验证"
                return -1, "\n".join(collected), f"超时 ({timeout}s){suffix}"

            time.sleep(0.5)

    except KeyboardInterrupt:
        _cleanup_container_driver(proc, arxiv_id)
        raise
    except Exception as e:
        _cleanup_container_driver(proc, arxiv_id)
        return -1, "\n".join(collected), str(e)


def extract_result(stdout: str):
    """从驱动脚本输出中提取结果路径（只认 SUCCESS 和 ERROR）"""
    for line in stdout.splitlines():
        if line.startswith("RESULT:SUCCESS:"):
            return "pdf", line[len("RESULT:SUCCESS:"):]
        if line.startswith("RESULT:ERROR:"):
            return "error", line[len("RESULT:ERROR:"):]
    return "unknown", ""


def copy_from_container(container_path: str, local_path: str):
    """docker cp 将文件从容器复制到本地"""
    r = _run_docker_control(
        ["docker", "cp",
         f"{CONTAINER_NAME}:{container_path}", local_path],
        f"复制生成 PDF {os.path.basename(local_path)}",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return r is not None and r.returncode == 0


def _write_error_log(arxiv_id: str, stdout: str):
    """
    从驱动输出中提取 PDF_DIAGNOSIS 诊断信息，写入宿主机 logs/pdf_errors/<arxiv_id>.log。
    日志包含：失败阶段、错误类型、修复建议、完整插件 traceback（translate 阶段）、
    LaTeX 错误上下文（compile 阶段）、编译日志尾部、以及完整驱动运行记录。
    """
    diag = None
    for line in stdout.splitlines():
        if line.startswith("PDF_DIAGNOSIS:"):
            try:
                diag = json.loads(line[len("PDF_DIAGNOSIS:"):])
            except Exception:
                pass
            break

    base_dir  = os.path.dirname(os.path.abspath(__file__))
    err_dir   = os.path.join(base_dir, "logs", "pdf_errors")
    os.makedirs(err_dir, exist_ok=True)
    log_path  = os.path.join(err_dir, f"{arxiv_id}.log")
    diag_path = os.path.join(err_dir, f"{arxiv_id}.json")

    from datetime import datetime
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if diag:
        structured = diag
    else:
        refined = classify_failure("translate", plugin_error=stdout)
        if refined.get("category") == "translate.unknown":
            structured = {
                "arxiv_id": arxiv_id,
                "phase": "unknown",
                "category": "unknown.unstructured",
                "family": "unknown",
                "retry_strategy": "manual_review",
                "repair_action": "inspect_driver_output",
                "retryable": False,
                "suggestion": "驱动未输出结构化诊断；检查原始日志。",
                "evidence": "",
            }
        else:
            structured = {
                "arxiv_id": arxiv_id,
                "phase": (
                    "infrastructure"
                    if str(refined.get("category", "")).startswith("infrastructure.")
                    else "translate"
                ),
                **refined,
            }
    structured["recorded_at"] = ts
    write_json_atomic(diag_path, structured)

    SEP  = "=" * 60
    SEP2 = "-" * 60

    with open(log_path, "w", encoding="utf-8") as f:
        f.write(f"{SEP}\n")
        f.write(f"PDF 翻译失败诊断报告\n")
        f.write(f"时间: {ts}\n")
        f.write(f"论文: {arxiv_id}\n")
        f.write(f"{SEP}\n\n")

        if diag:
            phase    = diag.get('phase', '?')
            category = diag.get('category', '?')
            phase_cn = 'GPT 翻译阶段崩溃' if phase == 'translate' else 'LaTeX 编译阶段失败'

            f.write(f"【失败阶段】  {phase}  —  {phase_cn}\n")
            f.write(f"【错误类型】  {category}\n")
            f.write(f"【错误家族】  {diag.get('family', '?')}\n")
            f.write(f"【重试策略】  {diag.get('retry_strategy', '?')}\n")
            f.write(f"【修复动作】  {diag.get('repair_action', '?')}\n")
            f.write(f"【原始 tex】  {'存在' if diag.get('has_orig_tex') else '不存在（源码未解压成功）'}\n")
            f.write(f"【翻译 tex】  {'存在' if diag.get('has_trans_tex') else '不存在（GPT 翻译未完成）'}\n\n")

            evidence = diag.get('evidence', '').strip()
            if evidence:
                f.write(f"【关键证据】\n  {evidence}\n\n")

            f.write(f"【修复建议】\n")
            for line in diag.get('suggestion', '').splitlines():
                f.write(f"  {line}\n")
            f.write("\n")

            # ── translate 阶段：完整插件报错 ──────────────────────────────
            plugin_err = diag.get('plugin_error_full', '').strip()
            if plugin_err:
                f.write(f"{SEP2}\n")
                f.write(f"【插件完整报错 / Traceback】\n")
                f.write(f"{SEP2}\n")
                # gpt-academic 把换行存成空格，尝试还原缩进
                import re as _re
                # 把连续多个空格前出现的 "File " / "  File " / "> " / "raise" / 错误类名 换回换行
                restored = _re.sub(
                    r'  +(File "|raise |RuntimeError|ValueError|KeyError|'
                    r'TypeError|AttributeError|ImportError|OSError)',
                    r'\n  \1', plugin_err
                )
                f.write(restored)
                f.write("\n\n")

            # ── compile 阶段：LaTeX 错误上下文 ────────────────────────────
            top_errors = diag.get('top_errors', [])
            if top_errors:
                f.write(f"{SEP2}\n")
                f.write(f"【LaTeX 编译错误（共 {len(top_errors)} 处，含上下文）】\n")
                f.write(f"{SEP2}\n")
                for i, err in enumerate(top_errors, 1):
                    f.write(f"\n── 错误 #{i} ──\n")
                    f.write(err)
                    f.write("\n")
                f.write("\n")

            # ── LaTeX 日志尾部片段 ────────────────────────────────────────
            log_tail = diag.get('tex_log_tail', '').strip()
            if log_tail:
                f.write(f"{SEP2}\n")
                f.write(f"【编译日志尾部（最后 60 行）】\n")
                f.write(f"【容器内日志路径】 {diag.get('log_file', '(none)')}\n")
                f.write(f"{SEP2}\n")
                f.write(log_tail)
                f.write("\n\n")

        else:
            f.write("（未能获取结构化诊断，以下为驱动原始输出中的关键行）\n\n")
            for line in stdout.splitlines():
                if any(k in line for k in ("❌", "Error", "Fatal", "Emergency",
                                            "Traceback", "RuntimeError",
                                            "[driver]", "RESULT:", "找不到")):
                    f.write(f"  {line}\n")
            f.write("\n")

        # ── 驱动运行完整记录（所有 [driver] 行）──────────────────────────
        driver_lines = [ln for ln in stdout.splitlines()
                        if ln.startswith("[driver") or "✦" in ln or "·" in ln
                        or ln.startswith("RESULT:") or "异常:" in ln]
        if driver_lines:
            f.write(f"{SEP2}\n")
            f.write(f"【驱动运行记录（[driver] 输出）】\n")
            f.write(f"{SEP2}\n")
            for ln in driver_lines:
                f.write(f"  {ln}\n")
            f.write("\n")

        f.write(f"{SEP}\n")
        f.write("如需手动进入容器排查:\n")
        f.write(f"  docker exec -it {CONTAINER_NAME} bash\n")
        f.write(f"  # 查看完整编译日志:\n")
        f.write(f"  cat /gpt/gpt_log/arxiv_cache/{arxiv_id}/workfolder/merge_translate_zh.log\n")
        f.write(f"  # 编辑翻译文件:\n")
        f.write(f"  vi /gpt/gpt_log/arxiv_cache/{arxiv_id}/workfolder/merge_translate_zh.tex\n")
        f.write(f"  # 手动重编译:\n")
        f.write(f"  cd /gpt/gpt_log/arxiv_cache/{arxiv_id}/workfolder\n")
        f.write(f"  pdflatex -interaction=nonstopmode merge_translate_zh.tex\n")
        f.write(f"{SEP}\n")

    print(f"📋 错误诊断已写入: {log_path}", flush=True)


def _clear_error_log(arxiv_id: str):
    """Remove stale failure diagnosis after the same paper succeeds."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    err_dir = os.path.join(base_dir, "logs", "pdf_errors")
    removed = False
    for suffix in (".log", ".json"):
        path = os.path.join(err_dir, f"{arxiv_id}{suffix}")
        if not os.path.exists(path):
            continue
        try:
            os.remove(path)
            removed = True
        except OSError as e:
            print(f"⚠️  旧错误诊断清理失败: {path} ({e})", flush=True)
    if removed:
        print(f"🧹 已清理旧错误诊断: {arxiv_id}", flush=True)
    return removed


def _clear_failed_tex_backup(arxiv_id: str):
    """Remove stale failed-run tex backup after a successful PDF build."""
    path = os.path.join(TEX_FAILED_BACKUP_DIR, f"{arxiv_id}_merge_translate_zh.tex")
    if not os.path.exists(path):
        return False
    try:
        os.remove(path)
        print(f"🧹 已清理旧失败现场 tex: {path}", flush=True)
        return True
    except OSError as e:
        print(f"⚠️  旧失败现场 tex 清理失败: {path} ({e})", flush=True)
        return False


def check_local_pdf_integrity(filepath: str) -> bool:
    """Use the same bounded PDF gate as the paper store and project audit."""
    return pdf_file_valid(filepath)


class GlobalTranslationLock:
    """Serialize every host entrypoint that uses the shared LaTeX container."""

    def __init__(self, arxiv_id: str, wait_seconds: int, lock_path: str = None):
        self.arxiv_id = arxiv_id
        self.wait_seconds = max(0, wait_seconds)
        configured_path = os.environ.get(
            "PAPER_TRANS_FULL_TRANSLATION_LOCK",
            "",
        ).strip()
        self.path = os.path.abspath(
            lock_path
            or configured_path
            or os.path.join(LOCK_DIR, "full-translation.lock")
        )
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._handle = None

    def __enter__(self):
        self._handle = open(self.path, "a+")
        deadline = time.monotonic() + self.wait_seconds
        announced = False
        while True:
            try:
                fcntl.flock(
                    self._handle,
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )
                break
            except BlockingIOError:
                if not announced:
                    print(
                        f"⏳ 全文翻译队列繁忙，等待全局锁: {self.path}",
                        flush=True,
                    )
                    announced = True
                if time.monotonic() >= deadline:
                    self._handle.close()
                    self._handle = None
                    raise TimeoutError(
                        f"等待全文翻译全局锁超时 ({self.wait_seconds}s)"
                    )
                time.sleep(1)

        self._handle.seek(0)
        self._handle.truncate()
        self._handle.write(json.dumps({
            "pid": os.getpid(),
            "arxiv_id": self.arxiv_id,
            "acquired_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }, ensure_ascii=False))
        self._handle.flush()
        return self

    def __exit__(self, *_):
        if self._handle:
            fcntl.flock(self._handle, fcntl.LOCK_UN)
            self._handle.close()
            self._handle = None


def _translate_full_locked(arxiv_id: str, output_dir: str,
                           no_cache: bool = False, timeout: int = 3600,
                           keep_translation: bool = False) -> dict:
    """
    全文翻译主函数：仅以 PDF 为成功标准，失败则直接报错（由驱动内部重试）。
    Returns: {
        'success': bool,
        'pdf_path': str | None,
        'error': str | None,
    }
    """
    os.makedirs(output_dir, exist_ok=True)
    result = {'success': False, 'pdf_path': None, 'error': None}

    disk_error = _disk_preflight_error()
    if disk_error:
        result['error'] = disk_error
        print(f"❌ {disk_error}", flush=True)
        _write_error_log(arxiv_id, disk_error)
        return result

    # 1. 检查容器
    if not check_container():
        result['error'] = f"容器 {CONTAINER_NAME} 未运行"
        print(f"❌ {result['error']}", flush=True)
        return result

    # 2. 复制驱动脚本
    print(f"📦 复制驱动脚本到容器...", flush=True)
    if not copy_driver_to_container():
        result['error'] = "无法复制驱动脚本到容器"
        print(f"❌ {result['error']}", flush=True)
        return result

    if keep_translation and not _prepare_keep_translation(arxiv_id):
        result['error'] = f"找不到可复用的翻译 tex 备份: {arxiv_id}"
        print(f"❌ {result['error']}", flush=True)
        return result

    # 3. 在容器内执行翻译
    print(f"🚀 启动容器内翻译 (timeout={timeout}s)...", flush=True)
    t0 = time.time()
    rc, stdout, stderr = run_in_container(arxiv_id, no_cache, timeout,
                                          keep_translation=keep_translation)
    elapsed = time.time() - t0
    print(f"⏱️  耗时: {elapsed:.0f}s", flush=True)

    if rc == -1:
        result['error'] = f"超时 ({timeout}s)"
        print(f"❌ {result['error']}", flush=True)
        _backup_tex_from_container(arxiv_id, failed=True)
        return result

    # 4. 解析输出
    kind, container_path = extract_result(stdout)
    # 驱动脚本返回相对路径（相对于 /gpt），转为绝对路径（仅对文件路径操作，不处理错误消息）
    if kind in ("pdf", "zip", "tex") and container_path and not container_path.startswith("/"):
        container_path = "/gpt/" + container_path
    print(f"   输出类型: {kind}  路径: {container_path}", flush=True)

    if kind == "error" or kind == "unknown":
        result['error'] = container_path or "翻译失败（驱动所有重试均未生成 PDF）"
        print(f"❌ {result['error']}", flush=True)
        _backup_tex_from_container(arxiv_id, failed=True)
        _write_error_log(arxiv_id, stdout)
        return result

    # 5. 复制 PDF 到本地
    if kind == "pdf":
        local_pdf = os.path.join(output_dir, f"{arxiv_id}_zh.pdf")
        temp_pdf = os.path.join(
            output_dir,
            f".{arxiv_id}_zh.pdf.{os.getpid()}.tmp",
        )
        try:
            if copy_from_container(container_path, temp_pdf):
                if check_local_pdf_integrity(temp_pdf):
                    with open(temp_pdf, "rb") as handle:
                        os.fsync(handle.fileno())
                    with paper_publication_lock(
                        arxiv_id,
                        lock_dir=LOCK_DIR,
                    ):
                        os.replace(temp_pdf, local_pdf)
                    result['success'] = True
                    result['pdf_path'] = local_pdf
                    size_mb = os.path.getsize(local_pdf) / 1024 / 1024
                    print(f"✅ PDF 翻译成功: {local_pdf} ({size_mb:.2f} MB)", flush=True)
                    _backup_tex_from_container(arxiv_id)
                    _clear_error_log(arxiv_id)
                    _clear_failed_tex_backup(arxiv_id)
                else:
                    result['error'] = "PDF 复制成功但文件损坏或为空（header/EOF 校验失败）"
                    print(f"❌ {result['error']}", flush=True)
                    _backup_tex_from_container(arxiv_id, failed=True)
            else:
                result['error'] = f"无法从容器复制 PDF: {container_path}"
                print(f"❌ {result['error']}", flush=True)
                _backup_tex_from_container(arxiv_id, failed=True)
        finally:
            try:
                os.remove(temp_pdf)
            except OSError:
                pass

    return result


def translate_full(arxiv_id: str, output_dir: str,
                   no_cache: bool = False, timeout: int = 3600,
                   keep_translation: bool = False) -> dict:
    """Run one full translation behind the cross-mode/container global lock."""
    default_wait = timeout + 300
    try:
        wait_seconds = int(os.environ.get(
            "PAPER_TRANS_GLOBAL_LOCK_TIMEOUT",
            str(default_wait),
        ))
    except (TypeError, ValueError):
        wait_seconds = default_wait

    try:
        with GlobalTranslationLock(arxiv_id, wait_seconds):
            cleanup_enabled = _cleanup_retry_cache_enabled()
            baseline_files = (
                _snapshot_retry_runtime_files() if cleanup_enabled else None
            )
            try:
                return _translate_full_locked(
                    arxiv_id,
                    output_dir,
                    no_cache=no_cache,
                    timeout=timeout,
                    keep_translation=keep_translation,
                )
            finally:
                if cleanup_enabled:
                    _cleanup_completed_retry_runtime_cache(
                        arxiv_id,
                        baseline_files=baseline_files,
                    )
    except TimeoutError as exc:
        error = str(exc)
        print(f"❌ {error}", flush=True)
        return {"success": False, "pdf_path": None, "error": error}


def main():
    parser = argparse.ArgumentParser(description="全文翻译 arXiv 论文")
    parser.add_argument("arxiv_id", help="arXiv ID, 如 2602.10388")
    parser.add_argument("-o", "--output", default=os.path.join(BASE_DIR, "weekly"),
                        help="输出目录")
    parser.add_argument("--no-cache", action="store_true", help="强制重新翻译")
    parser.add_argument("--keep-translation", action="store_true",
                        help="复用宿主机备份的 merge_translate_zh.tex，只重跑编译")
    parser.add_argument("--timeout", type=int, default=3600, help="超时秒数")
    args = parser.parse_args()

    print(f"\n🔬 全文翻译: {args.arxiv_id}", flush=True)
    result = translate_full(args.arxiv_id, args.output,
                            no_cache=args.no_cache, timeout=args.timeout,
                            keep_translation=args.keep_translation)
    print(f"\n📋 结果: {json.dumps(result, ensure_ascii=False, indent=2)}")
    sys.exit(0 if result['success'] else 1)


if __name__ == "__main__":
    import json
    main()
