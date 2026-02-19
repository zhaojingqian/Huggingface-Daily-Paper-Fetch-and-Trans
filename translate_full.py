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
from pathlib import Path

CONTAINER_NAME  = "gpt-academic-latex"
DRIVER_SCRIPT   = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "full_translate_driver.py")
# 容器内 gpt_log/arxiv_cache 对应的绝对路径
CONTAINER_CACHE = "/gpt/gpt_log/arxiv_cache"


def check_container():
    r = subprocess.run(
        ["docker", "ps", "-q", "-f", f"name={CONTAINER_NAME}"],
        capture_output=True, text=True,
    )
    return r.stdout.strip() != ""


def copy_driver_to_container():
    """将驱动脚本复制进容器"""
    r = subprocess.run(
        ["docker", "cp", DRIVER_SCRIPT,
         f"{CONTAINER_NAME}:/tmp/full_translate_driver.py"],
        capture_output=True, text=True,
    )
    return r.returncode == 0


def run_in_container(arxiv_id: str, no_cache: bool, timeout: int):
    """
    在容器内运行翻译驱动，返回 (returncode, stdout, stderr)
    """
    cmd = [
        "docker", "exec", CONTAINER_NAME,
        "python3", "/tmp/full_translate_driver.py", arxiv_id,
    ]
    if no_cache:
        cmd.append("--no-cache")

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout,
        )
        return (result.returncode,
                result.stdout.decode("utf-8", errors="replace"),
                result.stderr.decode("utf-8", errors="replace"))
    except subprocess.TimeoutExpired:
        return -1, "", f"超时 ({timeout}s)"


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
        capture_output=True, text=True,
    )
    return r.returncode == 0


def translate_full(arxiv_id: str, output_dir: str,
                   no_cache: bool = False, timeout: int = 3600) -> dict:
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
    rc, stdout, stderr = run_in_container(arxiv_id, no_cache, timeout)
    elapsed = time.time() - t0
    print(f"⏱️  耗时: {elapsed:.0f}s", flush=True)

    # 打印容器内日志（过滤掉 INFO/DEBUG 级别的噪声）
    for line in stdout.splitlines():
        if "[driver]" in line or "RESULT:" in line:
            print(f"   {line}", flush=True)

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
