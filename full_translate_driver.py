#!/usr/bin/env python3
"""
在 gpt-academic Docker 容器内运行的全文翻译驱动脚本
用法: python3 full_translate_driver.py <arxiv_id> [--no-cache] [--retries N]
输出: RESULT:SUCCESS:<pdf_path>  或  RESULT:ERROR:<msg>
"""
import sys, os, glob, time, shutil

sys.path.insert(0, '/gpt')
os.chdir('/gpt')

arxiv_id   = sys.argv[1] if len(sys.argv) > 1 else None
no_cache   = "--no-cache" in sys.argv
max_retries = 2   # 首次 + 最多2次重试

if not arxiv_id:
    print("RESULT:ERROR:请提供 arxiv_id", flush=True)
    sys.exit(1)

print(f"[driver] 开始处理: {arxiv_id}  no_cache={no_cache}  max_retries={max_retries}", flush=True)

# ── 代理注入（必须在所有 gpt-academic 模块导入之前）──────────────────────────────
HOST_PROXY   = os.environ.get("HOST_PROXY", "http://127.0.0.1:7890")
PROXIES_DICT = {"http": HOST_PROXY, "https": HOST_PROXY}
PROXIES_STR  = '{{"http": "{p}", "https": "{p}"}}'.format(p=HOST_PROXY)

for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ[k] = HOST_PROXY
os.environ["NO_PROXY"]               = "localhost,127.0.0.1"
os.environ["GPT_ACADEMIC_USE_PROXY"] = "True"
os.environ["GPT_ACADEMIC_proxies"]   = PROXIES_STR
os.environ["USE_PROXY"]              = "True"
os.environ["proxies"]                = PROXIES_STR

print(f"[driver] 代理: {HOST_PROXY}", flush=True)

from loguru import logger
logger.disable("root")

import shared_utils.config_loader as _cfg
try:
    _cfg.read_single_conf_with_lru_cache.cache_clear()
except AttributeError:
    pass
_orig_read = _cfg.read_single_conf_with_lru_cache
def _patched_read(arg):
    if arg == 'proxies':   return PROXIES_DICT
    if arg == 'USE_PROXY': return True
    return _orig_read(arg)
_cfg.read_single_conf_with_lru_cache = _patched_read

import requests as _req
_OrigSession = _req.Session
class _PatchedSession(_OrigSession):
    def __init__(self):
        super().__init__()
        self.proxies.update(PROXIES_DICT)
_req.Session = _PatchedSession

# ── Patch compile_latex_with_timeout：用进程组 kill，防止 pdflatex 变孤儿进程 ─────
import subprocess as _subprocess
import os as _os
import signal as _signal

def _patched_compile_with_timeout(command, cwd, timeout=90):
    """修复版：shell=True + 进程组 kill，确保 pdflatex 子进程也被杀掉。"""
    process = _subprocess.Popen(
        command, shell=True,
        stdout=_subprocess.PIPE, stderr=_subprocess.PIPE,
        cwd=cwd,
        preexec_fn=_os.setsid,   # 创建新进程组
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        return True
    except _subprocess.TimeoutExpired:
        try:
            _os.killpg(_os.getpgid(process.pid), _signal.SIGKILL)
        except Exception:
            process.kill()
        try:
            process.communicate(timeout=5)
        except Exception:
            pass
        print(f"[driver] ⚠️  pdflatex 超时（{timeout}s），已强制终止", flush=True)
        return False

_patched_compile_with_timeout.__defaults__ = (300,)  # 将默认 timeout 从 60 改为 300s

# 导入后替换（必须在导入 crazy_functions 之前设置好）
import importlib
_latex_toolbox_spec = importlib.util.find_spec('crazy_functions.latex_fns.latex_toolbox')
if _latex_toolbox_spec:
    _lt = importlib.import_module('crazy_functions.latex_fns.latex_toolbox')
    _lt.compile_latex_with_timeout = _patched_compile_with_timeout
    print(f"[driver] ✅ compile_latex_with_timeout 已 patch（timeout=300s，进程组 kill）", flush=True)

from toolbox import get_conf, ChatBotWithCookies, default_user_name

api_key   = get_conf('API_KEY')
llm_model = get_conf('LLM_MODEL')
ARXIV_CACHE_DIR = get_conf('ARXIV_CACHE_DIR')
print(f"[driver] 模型: {llm_model}", flush=True)
print(f"[driver] 缓存目录: {ARXIV_CACHE_DIR}", flush=True)

from crazy_functions.Latex_Function import Latex翻译中文并重新编译PDF

arxiv_url = f"https://arxiv.org/abs/{arxiv_id}"


def run_translation(attempt_no_cache: bool, attempt_idx: int) -> str | None:
    """执行一次翻译+编译，成功返回 PDF 路径，否则返回 None。"""
    llm_kwargs = {
        'api_key': api_key, 'llm_model': llm_model,
        'top_p': 1.0, 'max_length': None, 'temperature': 1.0,
    }
    cookie     = {**llm_kwargs, 'user_name': default_user_name, 'files_to_promote': []}
    chatbot    = ChatBotWithCookies(cookie)
    plugin_kwargs = {'advanced_arg': '--no-cache' if attempt_no_cache else ''}

    print(f"[driver] 第 {attempt_idx} 次尝试  no_cache={attempt_no_cache}", flush=True)
    print(f"[driver] 调用插件: {arxiv_url}", flush=True)

    last_msg = ''
    step_cnt = 0
    t0       = time.time()

    def elapsed():
        return f"{int(time.time() - t0)}s"

    try:
        gen = Latex翻译中文并重新编译PDF(
            arxiv_url, llm_kwargs, plugin_kwargs,
            chatbot, [], '', ''
        )
        for step in gen:
            step_cnt += 1
            if not isinstance(step, tuple) or len(step) < 2:
                continue
            cb = step[1]
            if not cb:
                continue
            try:
                last_pair = list(cb)[-1] if cb else None
                if last_pair and len(last_pair) >= 2 and last_pair[1]:
                    msg   = str(last_pair[1])
                    clean = msg.replace('`', '').replace('\n', ' ').strip()
                    if clean != last_msg or step_cnt % 20 == 0:
                        is_key = any(k in clean for k in [
                            '下载', '解压', '分析', '切分', '开始翻译', '编译',
                            '成功', '失败', '错误', 'Error', 'PDF', '完成',
                            '第', '次编译', 'GPT结果', '插件调用',
                        ])
                        prefix = f"[driver|{elapsed()}]"
                        if is_key:
                            print(f"{prefix} ✦ {clean[:180]}", flush=True)
                        elif clean != last_msg:
                            print(f"{prefix} · {clean[:100]}", flush=True)
                        last_msg = clean
            except Exception:
                pass
        print(f"[driver|{elapsed()}] 生成器完成，共 {step_cnt} 步", flush=True)
    except Exception as e:
        import traceback
        print(f"[driver|{elapsed()}] 异常: {e}", flush=True)
        traceback.print_exc()

    # 查找生成的 PDF（只在固定位置找，绝不搜索 Figures 子目录）
    translation_dir = os.path.join(ARXIV_CACHE_DIR, arxiv_id, 'translation')
    workfolder      = os.path.join(ARXIV_CACHE_DIR, arxiv_id, 'workfolder')

    # 优先：translation/translate_zh.pdf（插件最终输出）
    candidate = os.path.join(translation_dir, 'translate_zh.pdf')
    if os.path.exists(candidate) and os.path.getsize(candidate) > 50 * 1024:
        kb = os.path.getsize(candidate) // 1024
        print(f"[driver|{elapsed()}] ✅ translate_zh.pdf ({kb}KB)", flush=True)
        return candidate

    # 备选：workfolder 根目录里的翻译 PDF（名字含 translate_zh）
    for fname in ('merge_translate_zh.pdf', 'translate_zh.pdf'):
        fp = os.path.join(workfolder, fname)
        if os.path.exists(fp) and os.path.getsize(fp) > 50 * 1024:
            kb = os.path.getsize(fp) // 1024
            print(f"[driver|{elapsed()}] ✅ workfolder/{fname} ({kb}KB)", flush=True)
            return fp

    # 最后：workfolder 根目录（非子目录）内最大 PDF（排除原始 merge.pdf）
    root_pdfs = [
        os.path.join(workfolder, fn)
        for fn in os.listdir(workfolder)
        if fn.endswith('.pdf') and fn not in ('merge.pdf',)
    ] if os.path.isdir(workfolder) else []
    if root_pdfs:
        best = max(root_pdfs, key=os.path.getsize)
        if os.path.getsize(best) > 50 * 1024:
            kb = os.path.getsize(best) // 1024
            print(f"[driver|{elapsed()}] ✅ 找到 workfolder 根目录 PDF: {os.path.basename(best)} ({kb}KB)", flush=True)
            return best

    print(f"[driver|{elapsed()}] ❌ 本次未生成有效翻译 PDF（>50KB）", flush=True)
    return None


def clear_compile_cache(full=False):
    """清除 workfolder 和 translation（full=True 时也清 extract/e-print）。"""
    cache_base = os.path.join(ARXIV_CACHE_DIR, arxiv_id)
    targets = ['workfolder', 'translation']
    if full:
        targets += ['extract']
    for subdir in targets:
        d = os.path.join(cache_base, subdir)
        if os.path.exists(d):
            shutil.rmtree(d)
            print(f"[driver] 已清除缓存: {d}", flush=True)


# ── 主逻辑：首次 + 重试 ────────────────────────────────────────────────────────
result_pdf = None

# 首次调用且 no_cache=True 时，先清编译缓存（但保留 e-print 避免重复下载）
if no_cache:
    clear_compile_cache(full=False)

for attempt in range(1, max_retries + 2):   # 最多3次（1次首次 + 2次重试）
    if attempt == 1:
        # 首次：遵循调用方的 no_cache 参数
        result_pdf = run_translation(no_cache, attempt)
    else:
        # 重试：强制清缓存，重新翻译
        print(f"\n[driver] ══ 第 {attempt} 次重试（清除缓存后重新翻译）══", flush=True)
        clear_compile_cache()
        result_pdf = run_translation(True, attempt)

    if result_pdf:
        break
    if attempt <= max_retries:
        print(f"[driver] 等待 5s 后重试...", flush=True)
        time.sleep(5)

# ── 输出结果 ────────────────────────────────────────────────────────────────
if result_pdf:
    print(f"RESULT:SUCCESS:{result_pdf}", flush=True)
else:
    print(f"RESULT:ERROR:所有 {max_retries+1} 次尝试均未生成 PDF", flush=True)
    sys.exit(1)
