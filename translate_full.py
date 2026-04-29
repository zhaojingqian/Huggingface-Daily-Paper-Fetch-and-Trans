#!/usr/bin/env python3
"""
全文翻译入口脚本 (容器外调用)
使用 docker exec 在 gpt-academic-latex 容器内运行驱动脚本，
翻译 arxiv 论文全文（LaTeX → 中文 PDF），然后 docker cp 取回 PDF。

用法:
  python3 translate_full.py <arxiv_id> -o <output_dir> [--no-cache] [--timeout 3600]
"""

import subprocess
import sys
import os
import argparse
import time
import shutil
import json
from pathlib import Path

CONTAINER_NAME  = "gpt-academic-latex"
DRIVER_SCRIPT   = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "full_translate_driver.py")
# 容器内 gpt_log/arxiv_cache 对应的绝对路径
CONTAINER_CACHE = "/gpt/gpt_log/arxiv_cache"


def check_container():
    r = subprocess.run(
        ["docker", "ps", "-q", "-f", f"name={CONTAINER_NAME}"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return r.stdout.strip() != b""


def copy_driver_to_container():
    """将驱动脚本复制进容器"""
    r = subprocess.run(
        ["docker", "cp", DRIVER_SCRIPT,
         f"{CONTAINER_NAME}:/tmp/full_translate_driver.py"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return r.returncode == 0


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

    try:
        while True:
            # 非阻塞检查进程是否结束
            retcode = proc.poll()

            # 读取当前所有可用行（非阻塞）
            while True:
                line_b = proc.stdout.readline()
                if not line_b:
                    break
                line = line_b.decode("utf-8", errors="replace").rstrip()
                collected.append(line)
                # 只打印有意义的行（驱动标记 + 结果）
                if any(tag in line for tag in ("[driver]", "RESULT:", "✅", "❌", "⚠")):
                    elapsed = int(time.time() - t_start)
                    print(f"   [{elapsed:4d}s] {line}", flush=True)

            # 心跳：距上次心跳超过 BEAT_INTERVAL 且进程还在运行
            now = time.time()
            if retcode is None and now - t_beat >= BEAT_INTERVAL:
                elapsed = int(now - t_start)
                print(f"   ⏳ 翻译进行中... 已用 {elapsed}s / {timeout}s", flush=True)
                t_beat = now

            if retcode is not None:
                # 进程已结束，读尽剩余输出
                for line_b in proc.stdout:
                    line = line_b.decode("utf-8", errors="replace").rstrip()
                    collected.append(line)
                    if any(tag in line for tag in ("[driver]", "RESULT:", "✅", "❌", "⚠")):
                        elapsed = int(time.time() - t_start)
                        print(f"   [{elapsed:4d}s] {line}", flush=True)
                return retcode, "\n".join(collected), ""

            if time.time() - t_start > timeout:
                proc.kill()
                return -1, "\n".join(collected), f"超时 ({timeout}s)"

            time.sleep(1)

    except Exception as e:
        proc.kill()
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

    from datetime import datetime
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(log_path, "w", encoding="utf-8") as f:
        f.write(f"{'='*60}\n")
        f.write(f"PDF 翻译失败诊断报告\n")
        f.write(f"时间: {ts}\n")
        f.write(f"论文: {arxiv_id}\n")
        f.write(f"{'='*60}\n\n")

        if diag:
            f.write(f"【失败阶段】 {diag.get('phase', '?')}  "
                    f"(translate=GPT翻译失败 / compile=编译失败)\n\n")
            f.write(f"【错误类型】 {diag.get('category', '?')}\n\n")
            f.write(f"【修复建议】\n{diag.get('suggestion', '')}\n\n")
            f.write(f"【LaTeX 错误摘要】\n")
            for i, err in enumerate(diag.get('top_errors', []), 1):
                f.write(f"  [{i}] {err}\n\n")
            f.write(f"【容器内日志文件】 {diag.get('log_file', '(none)')}\n")
            f.write(f"【原始 tex 存在】  {diag.get('has_orig_tex')}\n")
            f.write(f"【翻译 tex 存在】  {diag.get('has_trans_tex')}\n")
        else:
            f.write("（未能获取结构化诊断，请查看 repair.log 中的原始输出）\n\n")
            # 把驱动输出里的错误相关行也写进来
            for line in stdout.splitlines():
                if any(k in line for k in ("❌", "Error", "Fatal", "Emergency",
                                            "[driver]", "RESULT:")):
                    f.write(f"  {line}\n")

        f.write(f"\n{'='*60}\n")
        f.write("如需手动修复，参考命令:\n")
        f.write(f"  docker exec gpt-academic-latex bash\n")
        f.write(f"  # 查看完整编译日志:\n")
        f.write(f"  cat /gpt/gpt_log/arxiv_cache/{arxiv_id}/workfolder/merge_translate_zh.log\n")
        f.write(f"  # 编辑翻译文件:\n")
        f.write(f"  vi /gpt/gpt_log/arxiv_cache/{arxiv_id}/workfolder/merge_translate_zh.tex\n")
        f.write(f"  # 手动重编译:\n")
        f.write(f"  cd /gpt/gpt_log/arxiv_cache/{arxiv_id}/workfolder\n")
        f.write(f"  pdflatex -interaction=nonstopmode merge_translate_zh.tex\n")
        f.write(f"{'='*60}\n")

    print(f"📋 错误诊断已写入: {log_path}", flush=True)


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
        _write_error_log(arxiv_id, stdout)
        return result

    # 5. 复制 PDF 到本地
    if kind == "pdf":
        local_pdf = os.path.join(output_dir, f"{arxiv_id}_zh.pdf")
        if copy_from_container(container_path, local_pdf):
            if os.path.exists(local_pdf) and os.path.getsize(local_pdf) > 4096:
                result['success'] = True
                result['pdf_path'] = local_pdf
                size_mb = os.path.getsize(local_pdf) / 1024 / 1024
                print(f"✅ PDF 翻译成功: {local_pdf} ({size_mb:.2f} MB)", flush=True)
            else:
                result['error'] = "PDF 复制成功但文件过小或为空"
                print(f"❌ {result['error']}", flush=True)
        else:
            result['error'] = f"无法从容器复制 PDF: {container_path}"
            print(f"❌ {result['error']}", flush=True)

    return result


def main():
    parser = argparse.ArgumentParser(description="全文翻译 arXiv 论文")
    parser.add_argument("arxiv_id", help="arXiv ID, 如 2602.10388")
    parser.add_argument("-o", "--output", default="/root/workspace/paper-trans/weekly",
                        help="输出目录")
    parser.add_argument("--no-cache", action="store_true", help="强制重新翻译")
    parser.add_argument("--timeout", type=int, default=3600, help="超时秒数")
    args = parser.parse_args()

    print(f"\n🔬 全文翻译: {args.arxiv_id}", flush=True)
    result = translate_full(args.arxiv_id, args.output,
                            no_cache=args.no_cache, timeout=args.timeout)
    print(f"\n📋 结果: {json.dumps(result, ensure_ascii=False, indent=2)}")
    sys.exit(0 if result['success'] else 1)


if __name__ == "__main__":
    import json
    main()
