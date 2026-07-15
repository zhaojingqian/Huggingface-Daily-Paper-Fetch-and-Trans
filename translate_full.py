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
from pathlib import Path

from paperhub.json_io import write_json_atomic
from paperhub.paths import (
    ROOT_DIR as BASE_DIR,
    DEFAULT_GPT_ACADEMIC_CONTAINER,
    TEX_BACKUP_DIR,
    TEX_FAILED_BACKUP_DIR,
)

DEFAULT_CONTAINER_NAME = DEFAULT_GPT_ACADEMIC_CONTAINER
CONTAINER_NAME  = os.environ.get("GPT_ACADEMIC_CONTAINER", DEFAULT_CONTAINER_NAME)
DRIVER_SCRIPT   = os.path.join(BASE_DIR, "full_translate_driver.py")
DRIVER_SUPPORT_FILES = [
    DRIVER_SCRIPT,
    os.path.join(BASE_DIR, "latex_translation_filters.py"),
    os.path.join(BASE_DIR, "failure_taxonomy.py"),
]
# 容器内 gpt_log/arxiv_cache 对应的绝对路径
CONTAINER_CACHE = "/gpt/gpt_log/arxiv_cache"
# 宿主机侧 tex 备份目录（容器重启后可从这里恢复翻译缓存，避免重复调 GPT）


def _container_workfolder(arxiv_id: str) -> str:
    return f"{CONTAINER_CACHE}/{arxiv_id}/workfolder"


def _container_translated_tex(arxiv_id: str) -> str:
    return f"{_container_workfolder(arxiv_id)}/merge_translate_zh.tex"


def _container_tex_exists(arxiv_id: str) -> bool:
    return subprocess.run(
        ["docker", "exec", CONTAINER_NAME, "test", "-s", _container_translated_tex(arxiv_id)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0


def _ensure_workfolder_writable(arxiv_id: str) -> bool:
    workfolder = _container_workfolder(arxiv_id)
    chown = subprocess.run(
        ["docker", "exec", "-u", "root", CONTAINER_NAME,
         "chown", "-R", "gptuser:gptuser", workfolder],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    chmod = subprocess.run(
        ["docker", "exec", "-u", "root", CONTAINER_NAME,
         "chmod", "-R", "u+rw", workfolder],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    ok = chown.returncode == 0 and chmod.returncode == 0
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
    check = subprocess.run(
        ["docker", "exec", CONTAINER_NAME, "test", "-s", container_tex],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if check.returncode != 0:
        return False
    backup_dir = TEX_FAILED_BACKUP_DIR if failed else TEX_BACKUP_DIR
    os.makedirs(backup_dir, exist_ok=True)
    local_tex = os.path.join(backup_dir, f"{arxiv_id}_merge_translate_zh.tex")
    r = subprocess.run(
        ["docker", "cp", f"{CONTAINER_NAME}:{container_tex}", local_tex],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    ok = r.returncode == 0 and os.path.exists(local_tex) and os.path.getsize(local_tex) > 0
    if ok:
        label = "失败现场 tex" if failed else "翻译 tex"
        print(f"💾 已备份{label} 到宿主机: {local_tex}", flush=True)
    return ok


def _restore_tex_to_container(arxiv_id: str) -> bool:
    """
    将宿主机备份的 merge_translate_zh.tex 恢复到容器内 workfolder。
    返回是否恢复成功。
    """
    local_tex = os.path.join(TEX_BACKUP_DIR, f"{arxiv_id}_merge_translate_zh.tex")
    if not os.path.exists(local_tex) or os.path.getsize(local_tex) == 0:
        local_tex = os.path.join(TEX_FAILED_BACKUP_DIR, f"{arxiv_id}_merge_translate_zh.tex")
        if not os.path.exists(local_tex) or os.path.getsize(local_tex) == 0:
            return False
    workfolder = _container_workfolder(arxiv_id)
    # 确保容器内目标目录存在
    subprocess.run(
        ["docker", "exec", CONTAINER_NAME, "mkdir", "-p", workfolder],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    container_tex = _container_translated_tex(arxiv_id)
    r = subprocess.run(
        ["docker", "cp", local_tex, f"{CONTAINER_NAME}:{container_tex}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    ok = r.returncode == 0
    if ok:
        # docker cp writes files as root. The driver runs as gptuser and needs to
        # rewrite merge_translate_zh.tex during keep-translation repair passes.
        _ensure_workfolder_writable(arxiv_id)
        print(f"♻️  已从宿主机恢复翻译 tex 到容器: {container_tex} (来自 {os.path.basename(os.path.dirname(local_tex))})", flush=True)
    return ok


def _prepare_keep_translation(arxiv_id: str) -> bool:
    """Prepare an existing translated tex for a compile-only retry."""
    if _restore_tex_to_container(arxiv_id):
        return True
    if _container_tex_exists(arxiv_id):
        _ensure_workfolder_writable(arxiv_id)
        print(f"♻️  容器内已有翻译 tex，直接复用: {_container_translated_tex(arxiv_id)}", flush=True)
        return True
    return False


def check_container():
    r = subprocess.run(
        ["docker", "container", "inspect", "-f", "{{.State.Running}}", CONTAINER_NAME],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    return r.returncode == 0 and r.stdout.strip() == "true"


def copy_driver_to_container():
    """将驱动脚本及其纯 Python 支持模块复制进容器"""
    copied = []
    for src in DRIVER_SUPPORT_FILES:
        name = os.path.basename(src)
        dst = f"{CONTAINER_NAME}:/tmp/{name}"
        r = subprocess.run(
            ["docker", "cp", src, dst],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True,
        )
        if r.returncode != 0:
            msg = (r.stderr or r.stdout or "").strip()
            if msg:
                print(f"❌ 复制 {name} 到容器失败: {msg}", flush=True)
            return False
        copied.append(f"/tmp/{name}")
    chmod = subprocess.run(
        ["docker", "exec", "-u", "root", CONTAINER_NAME, "chmod", "0644", *copied],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    if chmod.returncode != 0:
        msg = (chmod.stderr or chmod.stdout or "").strip()
        if msg:
            print(f"❌ 设置容器驱动脚本权限失败: {msg}", flush=True)
    return chmod.returncode == 0


def _terminate_container_driver(arxiv_id: str):
    """Best-effort cleanup for a timed-out docker exec driver process."""
    pattern = f"/tmp/full_translate_driver.py {arxiv_id}"
    for signal_name in ("-TERM", "-KILL"):
        subprocess.run(
            ["docker", "exec", "-u", "root", CONTAINER_NAME,
             "pkill", signal_name, "-f", pattern],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(1)


def run_in_container(arxiv_id: str, no_cache: bool, timeout: int,
                     keep_translation: bool = False):
    """
    在容器内运行翻译驱动，实时流式打印进度，返回 (returncode, stdout_full, "")
    每 30s 打印一次心跳，避免长时间无输出让人误以为卡死。
    """
    cmd = [
        "docker", "exec", CONTAINER_NAME,
        "python3", "/tmp/full_translate_driver.py", arxiv_id,
    ]
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
                proc.kill()
                _terminate_container_driver(arxiv_id)
                return -1, "\n".join(collected), f"超时 ({timeout}s)"

            time.sleep(0.5)

    except Exception as e:
        proc.kill()
        _terminate_container_driver(arxiv_id)
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
    r = subprocess.run(
        ["docker", "cp",
         f"{CONTAINER_NAME}:{container_path}", local_path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return r.returncode == 0


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
    structured = diag or {
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
    """Read the tail of the file to verify it ends with standard %%EOF marker."""
    if not os.path.exists(filepath) or os.path.getsize(filepath) < 4096:
        return False
    try:
        with open(filepath, 'rb') as f:
            f.seek(-1024, os.SEEK_END)
            tail = f.read()
            return b'%%EOF' in tail
    except Exception:
        return False


def translate_full(arxiv_id: str, output_dir: str,
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
        if copy_from_container(container_path, local_pdf):
            if check_local_pdf_integrity(local_pdf):
                result['success'] = True
                result['pdf_path'] = local_pdf
                size_mb = os.path.getsize(local_pdf) / 1024 / 1024
                print(f"✅ PDF 翻译成功: {local_pdf} ({size_mb:.2f} MB)", flush=True)
                _backup_tex_from_container(arxiv_id)
                _clear_error_log(arxiv_id)
                _clear_failed_tex_backup(arxiv_id)
            else:
                result['error'] = "PDF 复制成功但文件损坏或为空（未找到 EOF 标记）"
                print(f"❌ {result['error']}", flush=True)
                _backup_tex_from_container(arxiv_id, failed=True)
        else:
            result['error'] = f"无法从容器复制 PDF: {container_path}"
            print(f"❌ {result['error']}", flush=True)
            _backup_tex_from_container(arxiv_id, failed=True)

    return result


def main():
    parser = argparse.ArgumentParser(description="全文翻译 arXiv 论文")
    parser.add_argument("arxiv_id", help="arXiv ID, 如 2602.10388")
    parser.add_argument("-o", "--output", default="/root/workspace/paper-trans/weekly",
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
