#!/usr/bin/env python3
"""
在 gpt-academic Docker 容器内运行的全文翻译驱动脚本
用法: python3 full_translate_driver.py <arxiv_id> [--no-cache] [--retries N]
输出: RESULT:SUCCESS:<pdf_path>  或  RESULT:ERROR:<msg>
"""
import sys, os, glob, time, shutil

sys.path.insert(0, '/gpt')
os.chdir('/gpt')

arxiv_id        = sys.argv[1] if len(sys.argv) > 1 else None
no_cache        = "--no-cache" in sys.argv
keep_translation = "--keep-translation" in sys.argv   # 保留已有翻译，只重跑编译
max_retries = 0   # 只翻译一次，不重试

if not arxiv_id:
    print("RESULT:ERROR:请提供 arxiv_id", flush=True)
    sys.exit(1)

print(f"[driver] 开始处理: {arxiv_id}  no_cache={no_cache}  keep_translation={keep_translation}  max_retries={max_retries}", flush=True)

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


def _extract_env_blocks(content, env):
    """提取所有 \\begin{env}...\\end{env} 块，返回 (start, end, text) 列表。"""
    begin_tag = r'\begin{' + env + '}'
    end_tag   = r'\end{' + env + '}'
    blocks, pos = [], 0
    while True:
        start = content.find(begin_tag, pos)
        if start < 0:
            break
        end_idx = content.find(end_tag, start + len(begin_tag))
        if end_idx < 0:
            break
        end_idx += len(end_tag)
        blocks.append((start, end_idx, content[start:end_idx]))
        pos = end_idx
    return blocks


def patch_verbatim_envs(trans_tex_path, orig_tex_path):
    """
    将翻译后的 tex 文件中所有 verbatim 类环境（tcblisting / lstlisting / verbatim）
    还原为原始文件中的对应块，避免 GPT 翻译破坏代码/prompt 内容导致编译失败。
    返回替换的块数量。
    """
    VERBATIM_ENVS = ['tcblisting', 'lstlisting', 'verbatim', 'Verbatim', 'minted']
    with open(orig_tex_path, encoding='utf-8') as f:
        orig = f.read()
    with open(trans_tex_path, encoding='utf-8') as f:
        trans = f.read()

    result = trans
    total = 0
    for env in VERBATIM_ENVS:
        orig_blocks  = _extract_env_blocks(orig, env)
        trans_blocks = _extract_env_blocks(trans, env)
        if not orig_blocks or not trans_blocks:
            continue
        if len(orig_blocks) != len(trans_blocks):
            print(f"[driver] ⚠️  {env} 块数不一致 (orig={len(orig_blocks)} trans={len(trans_blocks)})，跳过", flush=True)
            continue
        # 从后往前替换，避免索引偏移
        for (_, _, ob), (ts, te, tb) in reversed(list(zip(orig_blocks, trans_blocks))):
            if ob != tb:
                result = result[:ts] + ob + result[te:]
                total += 1

    if total:
        with open(trans_tex_path, 'w', encoding='utf-8') as f:
            f.write(result)
    return total


def patch_and_recompile(workfolder, arxiv_id_):
    """
    当 gpt-academic 翻译完成但编译失败时：
    1. 还原 verbatim 类环境为原始内容
    2. 直接用 pdflatex 重新编译 merge_translate_zh.tex
    3. 成功则把 PDF 复制到 translation 目录并返回路径
    """
    import subprocess as _sp
    trans_tex  = os.path.join(workfolder, 'merge_translate_zh.tex')
    orig_tex   = os.path.join(workfolder, 'merge.tex')
    output_pdf = os.path.join(workfolder, 'merge_translate_zh.pdf')
    dest_dir   = os.path.join(ARXIV_CACHE_DIR, arxiv_id_, 'translation')
    dest_pdf   = os.path.join(dest_dir, 'translate_zh.pdf')

    if not os.path.exists(trans_tex) or not os.path.exists(orig_tex):
        return None

    print(f"[driver] 🔧 检测到编译失败但翻译已完成，尝试 verbatim 修补+重编译...", flush=True)
    n = patch_verbatim_envs(trans_tex, orig_tex)
    print(f"[driver] 🔧 修补了 {n} 个 verbatim 类环境块", flush=True)

    try:
        _sp.run(
            ['pdflatex', '-interaction=nonstopmode', 'merge_translate_zh.tex'],
            cwd=workfolder, timeout=300,
            stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
        )
    except Exception as e:
        print(f"[driver] ⚠️  pdflatex 执行异常: {e}", flush=True)
        return None

    if os.path.exists(output_pdf) and os.path.getsize(output_pdf) > 50 * 1024:
        kb = os.path.getsize(output_pdf) // 1024
        os.makedirs(dest_dir, exist_ok=True)
        shutil.copy2(output_pdf, dest_pdf)
        print(f"[driver] ✅ 修补重编译成功: {dest_pdf} ({kb}KB)", flush=True)
        return dest_pdf

    print(f"[driver] ❌ 修补重编译仍未生成有效 PDF", flush=True)
    return None


def diagnose_failure(workfolder, arxiv_id_):
    """
    分析编译失败原因，输出结构化诊断行供宿主机捕获。
    格式: PDF_DIAGNOSIS:<json>
    """
    import json as _json

    # ── 找最后一个 LaTeX 日志文件 ──────────────────────────────────────────
    log_candidates = sorted(glob.glob(os.path.join(workfolder, '*.log')))
    # 优先用 fix 链末尾的 log，其次用 merge_translate_zh.log
    tex_log = None
    for cand in reversed(log_candidates):
        if 'merge_translate_zh' in os.path.basename(cand):
            tex_log = cand
            break
    if not tex_log and log_candidates:
        tex_log = log_candidates[-1]

    errors_raw = []
    if tex_log and os.path.exists(tex_log):
        with open(tex_log, encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
        for i, ln in enumerate(lines):
            if ln.startswith('!') and 'TeX capacity' not in ln or \
               ('! LaTeX Error' in ln or '! Package' in ln or '! Missing' in ln or
                '! Extra' in ln or '! Emergency' in ln or '! Undefined' in ln):
                ctx_start = max(0, i - 1)
                ctx_end   = min(len(lines), i + 5)
                errors_raw.append(''.join(lines[ctx_start:ctx_end]).strip())
                if len(errors_raw) >= 8:
                    break

    # ── 错误类型判断 ──────────────────────────────────────────────────────
    all_text = '\n'.join(errors_raw)
    trans_tex = os.path.join(workfolder, 'merge_translate_zh.tex')
    orig_tex  = os.path.join(workfolder, 'merge.tex')
    has_trans = os.path.exists(trans_tex)
    has_orig  = os.path.exists(orig_tex)

    category = 'unknown'
    suggestion = '查看下方错误日志，手动 patch merge_translate_zh.tex 后重新编译'

    if 'input stack size' in all_text or ('normalsize' in all_text and '->' in all_text):
        category   = 'normalsize_recursion'
        suggestion = ('preamble 中 \\normalsize 自引用递归。\n'
                      '修复: 在 merge_translate_zh.tex 中把\n'
                      '  \\expandafter\\def\\expandafter\\normalsize\\expandafter{\\normalsize ...}\n'
                      '替换为:\n'
                      '  \\let\\normalsizesaved\\normalsize\n'
                      '  \\def\\normalsize{\\normalsizesaved ...}')
    elif 'pgfkeys Error' in all_text or ('tcblisting' in all_text and 'Missing $' in all_text):
        category   = 'tcblisting_translated'
        suggestion = ('tcblisting/lstlisting 内代码被 GPT 翻译，导致特殊字符破坏编译。\n'
                      '修复: 运行 patch_and_recompile 或手动从 merge.tex 还原 verbatim 块')
    elif 'File' in all_text and 'not found' in all_text:
        import re as _re
        m = _re.search(r"File '([^']+)' not found", all_text)
        pkg = m.group(1) if m else '未知'
        category   = f'missing_package:{pkg}'
        suggestion = (f'缺少 LaTeX 包: {pkg}。\n'
                      f'修复: 在容器内 tlmgr install 或在 texmf 目录创建 stub .sty')
    elif 'twocolumn' in all_text or ('begin{document} ended by' in all_text):
        category   = 'missing_bracket'
        suggestion = ('\\twocolumn[ 缺少闭合 ]，GPT 翻译时被删除。\n'
                      '修复: 在 merge_translate_zh.tex 中 \\printAffiliationsAndNotice 前补回 ]\n'
                      '参考 merge.tex 对应位置')
    elif 'Emergency stop' in all_text or 'Missing }' in all_text:
        category   = 'group_mismatch'
        suggestion = '大括号/环境不匹配导致 Emergency stop，通常是 GPT 翻译破坏了嵌套结构'
    elif 'Undefined control sequence' in all_text:
        import re as _re
        m = _re.search(r'\\([A-Za-z@]+)', all_text.split('Undefined control sequence')[1][:80])
        cmd = ('\\' + m.group(1)) if m else '未知'
        category   = f'undefined_command:{cmd}'
        suggestion = f'未定义命令 {cmd}，可能是自定义宏未翻译/丢失'

    # ── 是否有翻译文件（区分翻译失败 vs 编译失败）──────────────────────────
    phase = 'compile' if has_trans else 'translate'

    diag = {
        'arxiv_id':   arxiv_id_,
        'phase':      phase,
        'category':   category,
        'suggestion': suggestion,
        'top_errors': errors_raw[:5],
        'log_file':   tex_log or '(none)',
        'has_orig_tex': has_orig,
        'has_trans_tex': has_trans,
    }
    print(f"PDF_DIAGNOSIS:{_json.dumps(diag, ensure_ascii=False)}", flush=True)
    return diag


def clear_compile_cache(full=False):
    """清除 workfolder 和 translation（full=True 时也清 extract）。"""
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

TRANSLATE_TEX = os.path.join(ARXIV_CACHE_DIR, arxiv_id, 'workfolder', 'merge_translate_zh.tex')

if keep_translation and os.path.exists(TRANSLATE_TEX):
    # 保留已有 GPT 翻译，只重跑编译：不清缓存，以 no_cache=False 调用插件
    print(f"[driver] ♻️  复用已有翻译缓存: {TRANSLATE_TEX}（跳过 GPT 翻译）", flush=True)
    actual_no_cache = False
elif no_cache:
    # 强制全量重新翻译
    clear_compile_cache(full=True)
    actual_no_cache = True
else:
    actual_no_cache = False

for attempt in range(1, max_retries + 2):   # 最多3次（1次首次 + 2次重试）
    if attempt == 1:
        result_pdf = run_translation(actual_no_cache, attempt)
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

# ── Fallback：翻译完成但编译失败时，修补 verbatim 环境后重编译 ──────────────
if not result_pdf:
    workfolder = os.path.join(ARXIV_CACHE_DIR, arxiv_id, 'workfolder')
    result_pdf = patch_and_recompile(workfolder, arxiv_id)

# ── 输出结果 ────────────────────────────────────────────────────────────────
if result_pdf:
    print(f"RESULT:SUCCESS:{result_pdf}", flush=True)
else:
    workfolder_ = os.path.join(ARXIV_CACHE_DIR, arxiv_id, 'workfolder')
    diagnose_failure(workfolder_, arxiv_id)
    print(f"RESULT:ERROR:所有 {max_retries+1} 次尝试均未生成 PDF", flush=True)
    sys.exit(1)
