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

    # ── Patch find_main_tex_file：先去注释再搜索 \documentclass，避免注释行误匹配 ──────
    # gpt-academic 原实现在原始文本（含注释）中搜索 \documentclass，
    # 导致注释掉的 \documentclass 也会使非主文件被列为候选，进而得分更高后被误选。
    _orig_find_main = _lt.find_main_tex_file

    def _patched_find_main_tex_file(file_manifest, mode):
        import os as _os
        import re as _re
        import numpy as _np

        def _rm_comments_simple(text):
            lines = []
            for line in text.splitlines():
                stripped = line.lstrip()
                if stripped.startswith('%'):
                    continue
                idx = line.find('%')
                if idx >= 0:
                    line = line[:idx]
                lines.append(line)
            return '\n'.join(lines)

        candidates = []
        for texf in file_manifest:
            if _os.path.basename(texf).startswith('merge'):
                continue
            try:
                with open(texf, 'r', encoding='utf8', errors='ignore') as _f:
                    raw = _f.read()
            except Exception:
                continue
            clean = _rm_comments_simple(raw)
            if r'\documentclass' in clean:
                candidates.append(texf)

        if len(candidates) == 0:
            raise RuntimeError('无法找到一个主Tex文件（包含documentclass关键字）')
        if len(candidates) == 1:
            print(f"[driver] ✅ 主 Tex 文件: {candidates[0]}", flush=True)
            return candidates[0]

        # 多个候选时按原始逻辑打分（但在去注释后的内容上）
        unexpected_words = [r'\LaTeX', 'manuscript', 'Guidelines', 'font',
                            'citations', 'rejected', 'blind review', 'reviewers']
        expected_words   = [r'\input', r'\ref', r'\cite']
        scores = []
        for texf in candidates:
            try:
                with open(texf, 'r', encoding='utf8', errors='ignore') as _f:
                    content = _lt.rm_comments(_f.read())
            except Exception:
                content = ''
            s = 0
            for w in unexpected_words:
                if w in content:
                    s -= 1
            for w in expected_words:
                if w in content:
                    s += 1
            scores.append(s)
        best = candidates[int(_np.argmax(scores))]
        print(f"[driver] ✅ 主 Tex 文件 (多候选, scores={dict(zip([_os.path.basename(c) for c in candidates], scores))}): {best}", flush=True)
        return best

    _lt.find_main_tex_file = _patched_find_main_tex_file
    # 同步更新 latex_actions 模块中已导入的引用
    _la_spec = importlib.util.find_spec('crazy_functions.latex_fns.latex_actions')
    if _la_spec:
        _la = importlib.import_module('crazy_functions.latex_fns.latex_actions')
        _la.find_main_tex_file = _patched_find_main_tex_file
    print(f"[driver] ✅ find_main_tex_file 已 patch（注释行不参与 documentclass 检测）", flush=True)

    # ── Patch merge_tex_files_：系统级 TeX 文件（如 glyphtounicode）不在项目目录里，
    #    原实现直接 raise RuntimeError，改为先用 kpsewhich 确认是否为系统文件，是则跳过。
    _orig_merge_tex_files_ = _lt.merge_tex_files_

    def _patched_merge_tex_files_(project_folder, main_file, mode):
        import re as _re, subprocess as _sp
        main_file = _lt.rm_comments(main_file)
        for s in reversed([q for q in _re.finditer(r"\\input\{(.*?)\}", main_file, _re.M)]):
            f = s.group(1)
            fp = _os.path.join(project_folder, f)
            fp_ = _lt.find_tex_file_ignore_case(fp)
            if fp_:
                try:
                    with open(fp_, "r", encoding="utf-8", errors="replace") as fx:
                        c = fx.read()
                except Exception:
                    c = "\n\nWarning from GPT-Academic: LaTex source file is missing!\n\n"
            else:
                # 检查是否为系统级 TeX 文件（通过 kpsewhich 查找）
                try:
                    probe = f if f.endswith('.tex') else f + '.tex'
                    r = _sp.run(['kpsewhich', probe],
                                capture_output=True, text=True, timeout=5)
                    if r.returncode == 0 and r.stdout.strip():
                        print(f"[driver] ⚠️  跳过系统 TeX 文件（非项目文件）: {f}", flush=True)
                        c = f"% [system file skipped by driver patch: {f}]\n"
                    else:
                        raise RuntimeError(f"找不到{fp}，Tex源文件缺失！")
                except _sp.TimeoutExpired:
                    raise RuntimeError(f"找不到{fp}，Tex源文件缺失！")
            c = _patched_merge_tex_files_(project_folder, c, mode)
            main_file = main_file[:s.span()[0]] + c + main_file[s.span()[1]:]
        return main_file

    _lt.merge_tex_files_ = _patched_merge_tex_files_
    print(f"[driver] ✅ merge_tex_files_ 已 patch（系统 TeX 文件引用自动跳过）", flush=True)

from toolbox import get_conf, ChatBotWithCookies, default_user_name

api_key   = get_conf('API_KEY')
llm_model = get_conf('LLM_MODEL')
ARXIV_CACHE_DIR = get_conf('ARXIV_CACHE_DIR')
print(f"[driver] 模型: {llm_model}", flush=True)
print(f"[driver] 缓存目录: {ARXIV_CACHE_DIR}", flush=True)

from crazy_functions.Latex_Function import Latex翻译中文并重新编译PDF

arxiv_url = f"https://arxiv.org/abs/{arxiv_id}"

# 模块级：收集插件运行中所有完整消息（不截断），供 diagnose_failure 分析
_plugin_msgs_full: list[str] = []


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
                    # 完整消息存档（供诊断用，不截断）
                    _plugin_msgs_full.append(msg)
                    clean = msg.replace('`', '').replace('\n', ' ').strip()
                    if clean != last_msg or step_cnt % 20 == 0:
                        is_key = any(k in clean for k in [
                            '下载', '解压', '分析', '切分', '开始翻译', '编译',
                            '成功', '失败', '错误', 'Error', 'PDF', '完成',
                            '第', '次编译', 'GPT结果', '插件调用',
                        ])
                        prefix = f"[driver|{elapsed()}]"
                        if is_key:
                            # 关键消息：完整打印（不截断），宿主机可捕获完整 traceback
                            print(f"{prefix} ✦ {clean}", flush=True)
                        elif clean != last_msg:
                            print(f"{prefix} · {clean[:120]}", flush=True)
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


def _discover_tcb_listing_envs(content):
    """从 \\newtcblisting{...} 定义中发现自定义 verbatim/listing 环境。"""
    import re as _re
    envs = set()
    for pat in (
        r'\\newtcblisting\s*\{([A-Za-z][A-Za-z0-9*_-]*)\}',
        r'\\DeclareTCBListing\s*\{([A-Za-z][A-Za-z0-9*_-]*)\}',
    ):
        envs.update(_re.findall(pat, content))
    return sorted(envs)


def _discover_tcolorbox_envs(content):
    """从 \\newtcolorbox{...} 定义中发现普通 tcolorbox 环境。"""
    import re as _re
    envs = set()
    for pat in (
        r'\\newtcolorbox\s*\{([A-Za-z][A-Za-z0-9*_-]*)\}',
        r'\\DeclareTColorBox\s*\{([A-Za-z][A-Za-z0-9*_-]*)\}',
    ):
        envs.update(_re.findall(pat, content))
    return sorted(envs)


def fix_label_ref_emdash(trans_tex_path):
    """
    修复 GPT 翻译时将 \\label{}/\\ref{}/\\cite{}/\\eqref{} 等命令参数中的
    ASCII 连字符 '-' 替换为 Unicode 破折号（em-dash '—' U+2014、en-dash '–' U+2013）
    导致的 LaTeX 编译报错。

    仅替换这些命令的花括号参数内部，不触碰正文。
    返回修复的数量。
    """
    import re as _re
    with open(trans_tex_path, encoding='utf-8') as f:
        text = f.read()

    CMD_RE = _re.compile(
        r'(\\(?:label|ref|eqref|cite|citealt|citep|citet|pageref|nameref|hyperref|autoref)'
        r'(?:\[[^\]]*\])?'   # 可选 [别名]
        r'\{)([^}]*?)(\})',  # 捕获花括号内容
        _re.DOTALL,
    )

    total = 0

    def _replace(m):
        nonlocal total
        inner = m.group(2)
        fixed = inner.replace('\u2014', '-').replace('\u2013', '-')
        if fixed != inner:
            total += len([c for c in inner if c in '\u2014\u2013'])
        return m.group(1) + fixed + m.group(3)

    new_text = CMD_RE.sub(_replace, text)
    if total:
        with open(trans_tex_path, 'w', encoding='utf-8') as f:
            f.write(new_text)
        print(f"[driver] 🔧 fix_label_ref_emdash: 修复了 {total} 处破折号", flush=True)
    return total


def patch_body_endinput(trans_tex_path):
    """
    合并后的论文正文里偶尔会带入子文件的 \\endinput，导致 TeX 提前停止读取，
    后面的 \\end{document} 被忽略。只注释 \\begin{document} 之后整行的 \\endinput。
    """
    with open(trans_tex_path, encoding='utf-8') as f:
        text = f.read()

    doc_pos = text.find(r'\begin{document}')
    if doc_pos < 0:
        return 0

    head, body = text[:doc_pos], text[doc_pos:]
    lines = body.splitlines(keepends=True)
    total = 0
    for i, line in enumerate(lines):
        if line.strip() == r'\endinput':
            newline = '\n' if line.endswith('\n') else ''
            lines[i] = '% \\endinput removed by paper-trans repair' + newline
            total += 1

    if total:
        with open(trans_tex_path, 'w', encoding='utf-8') as f:
            f.write(head + ''.join(lines))
        print(f"[driver] 🔧 patch_body_endinput: 注释了 {total} 处正文 \\endinput", flush=True)
    return total


def patch_tcolorbox_small_groups(trans_tex_path):
    """
    GPT 有时把 \\begin{trajcase} 后的 {\\small ... } 保留下来。
    这种跨 breakable tcolorbox 的显式分组容易触发 tcb@savebox 分组错误。
    """
    import re as _re
    with open(trans_tex_path, encoding='utf-8') as f:
        text = f.read()

    total = 0

    def _replace_begin(m):
        nonlocal total
        total += 1
        return m.group(1) + r'\small' + '\n'

    new_text = _re.sub(
        r'(\\begin\{trajcase\}(?:\[[^\n]*\])?(?:\{[^\n]*\})?\s*)\{\\small\s*',
        _replace_begin,
        text,
    )
    if total:
        new_text = _re.sub(
            r'(?m)^\s*\}\s*\n(\s*\\end\{trajcase\})',
            r'\1',
            new_text,
        )
        with open(trans_tex_path, 'w', encoding='utf-8') as f:
            f.write(new_text)
        print(f"[driver] 🔧 patch_tcolorbox_small_groups: 修复了 {total} 个 trajcase 字号分组", flush=True)
    return total


def patch_unbalanced_groups_in_tcolorboxes(trans_tex_path):
    """在自定义 tcolorbox 块内补齐明显漏掉的 \\endgroup。"""
    with open(trans_tex_path, encoding='utf-8') as f:
        text = f.read()

    envs = _discover_tcolorbox_envs(text)
    result = text
    total = 0
    for env in envs:
        blocks = _extract_env_blocks(result, env)
        for start, end, block in reversed(blocks):
            missing = block.count(r'\begingroup') - block.count(r'\endgroup')
            if missing <= 0:
                continue
            end_tag = r'\end{' + env + '}'
            pos = block.rfind(end_tag)
            if pos < 0:
                continue
            fixed = block[:pos] + ('\\endgroup\n' * missing) + block[pos:]
            result = result[:start] + fixed + result[end:]
            total += missing

    if total:
        with open(trans_tex_path, 'w', encoding='utf-8') as f:
            f.write(result)
        print(f"[driver] 🔧 patch_unbalanced_groups_in_tcolorboxes: 补齐了 {total} 个 endgroup", flush=True)
    return total


def patch_verbatim_envs(trans_tex_path, orig_tex_path):
    """
    将翻译后的 tex 文件中所有 verbatim 类环境（tcblisting / lstlisting / verbatim）
    还原为原始文件中的对应块，避免 GPT 翻译破坏代码/prompt 内容导致编译失败。
    返回替换的块数量。
    """
    with open(orig_tex_path, encoding='utf-8') as f:
        orig = f.read()
    with open(trans_tex_path, encoding='utf-8') as f:
        trans = f.read()

    VERBATIM_ENVS = [
        'tcblisting', 'lstlisting', 'verbatim', 'Verbatim', 'minted',
        *_discover_tcb_listing_envs(orig),
        *_discover_tcb_listing_envs(trans),
    ]
    VERBATIM_ENVS = sorted(set(VERBATIM_ENVS))

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
    patch_body_endinput(trans_tex)
    fix_label_ref_emdash(trans_tex)
    patch_tcolorbox_small_groups(trans_tex)
    n = patch_verbatim_envs(trans_tex, orig_tex)
    print(f"[driver] 🔧 修补了 {n} 个 verbatim 类环境块", flush=True)
    patch_unbalanced_groups_in_tcolorboxes(trans_tex)

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
    import json as _json, re as _re

    trans_tex = os.path.join(workfolder, 'merge_translate_zh.tex')
    orig_tex  = os.path.join(workfolder, 'merge.tex')
    has_trans = os.path.exists(trans_tex)
    has_orig  = os.path.exists(orig_tex)
    phase     = 'compile' if has_trans else 'translate'

    # ── 1. 插件报错全文（translate 阶段：从收集的 chatbot 消息中提取）──────
    plugin_error_full = ''
    for msg in _plugin_msgs_full:
        if '插件调用出错' in msg or 'Traceback' in msg or 'RuntimeError' in msg \
                or 'Error:' in msg or '找不到' in msg:
            # 还原换行（gpt-academic 把换行替换成了空格，用 4 空格对齐重新断行）
            plugin_error_full = msg.strip()
            break

    # ── 2. 读取 LaTeX 编译日志 ────────────────────────────────────────────
    log_candidates = sorted(glob.glob(os.path.join(workfolder, '*.log')))
    tex_log = None
    for cand in reversed(log_candidates):
        if 'merge_translate_zh' in os.path.basename(cand):
            tex_log = cand
            break
    if not tex_log and log_candidates:
        tex_log = log_candidates[-1]

    all_log_lines: list[str] = []
    errors_raw:    list[str] = []
    tex_log_tail   = ''

    if tex_log and os.path.exists(tex_log):
        with open(tex_log, encoding='utf-8', errors='replace') as f:
            all_log_lines = f.readlines()

        # 找所有错误行，带更多上下文（前2行 + 后10行）
        for i, ln in enumerate(all_log_lines):
            is_err = (
                (ln.startswith('!') and 'TeX capacity exceeded' not in ln)
                or '! LaTeX Error' in ln
                or '! Package' in ln
                or '! Missing' in ln
                or '! Extra' in ln
                or '! Emergency' in ln
                or '! Undefined' in ln
                or '! Too many' in ln
            )
            if is_err:
                ctx_start = max(0, i - 2)
                ctx_end   = min(len(all_log_lines), i + 12)
                errors_raw.append(''.join(all_log_lines[ctx_start:ctx_end]).rstrip())
                if len(errors_raw) >= 10:
                    break

        # 日志尾部（最后 60 行，通常包含 Fatal/Emergency stop 位置）
        tail_lines = all_log_lines[-60:] if len(all_log_lines) > 60 else all_log_lines
        tex_log_tail = ''.join(tail_lines).strip()

    # ── 3. 错误类型判断 ───────────────────────────────────────────────────
    all_text = '\n'.join(errors_raw)
    category   = 'unknown'
    suggestion = '查看下方详细错误信息'

    if phase == 'translate':
        # translate 阶段：从插件报错里提取具体原因
        if '找不到' in plugin_error_full and 'Tex源文件缺失' in plugin_error_full:
            m = _re.search(r'找不到([^\n，]+)[，,]', plugin_error_full)
            missing = m.group(1).strip() if m else '未知文件'
            category   = f'missing_input_file:{_os.path.basename(missing)}'
            suggestion = (f'\\input 引用的文件不在项目目录中: {missing}\n'
                          f'如果是系统级 TeX 文件（如 glyphtounicode），已由驱动 patch 自动跳过。\n'
                          f'如果是项目自定义文件，需手动将其加入 arxiv 源码包后重试。')
        elif 'RuntimeError' in plugin_error_full:
            m = _re.search(r'RuntimeError:\s*(.+)', plugin_error_full)
            msg = m.group(1).strip()[:120] if m else plugin_error_full[:120]
            category   = f'runtime_error'
            suggestion = f'插件内部 RuntimeError: {msg}'
        elif 'Traceback' in plugin_error_full or 'Error:' in plugin_error_full:
            category   = 'plugin_exception'
            suggestion = '插件抛出异常，见下方完整 Traceback'
        elif not plugin_error_full:
            suggestion = '无插件报错消息，可能是网络/API 超时，或驱动脚本被意外终止'
    else:
        # compile 阶段：从 LaTeX log 判断
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
            m = _re.search(r"File '([^']+)' not found", all_text)
            pkg = m.group(1) if m else '未知'
            category   = f'missing_package:{pkg}'
            suggestion = (f'缺少 LaTeX 包: {pkg}。\n'
                          f'修复: 在容器内 tlmgr install 或在 texmf 目录创建 stub .sty')
        elif ('twocolumn' in all_text and 'ended by' in all_text) or \
             'begin{document} ended by' in all_text:
            category   = 'missing_bracket'
            suggestion = ('\\twocolumn[ 缺少闭合 ]，GPT 翻译时被删除。\n'
                          '修复: 在 merge_translate_zh.tex 中 \\printAffiliationsAndNotice 前补回 ]\n'
                          '参考 merge.tex 对应位置')
        elif 'Emergency stop' in all_text or 'Missing }' in all_text:
            category   = 'group_mismatch'
            suggestion = '大括号/环境不匹配导致 Emergency stop，通常是 GPT 翻译破坏了嵌套结构'
        elif 'Undefined control sequence' in all_text:
            m = _re.search(r'\\([A-Za-z@]+)',
                           all_text.split('Undefined control sequence')[1][:80])
            cmd = ('\\' + m.group(1)) if m else '未知'
            category   = f'undefined_command:{cmd}'
            suggestion = f'未定义命令 {cmd}，可能是自定义宏未翻译/丢失'
        elif errors_raw:
            # 有错误行但未匹配具体类型
            first_line = errors_raw[0].splitlines()[0][:80]
            category   = f'latex_error:{first_line}'
            suggestion = '见下方 LaTeX 错误详情'

    diag = {
        'arxiv_id':          arxiv_id_,
        'phase':             phase,
        'category':          category,
        'suggestion':        suggestion,
        'top_errors':        errors_raw[:8],
        'tex_log_tail':      tex_log_tail,
        'plugin_error_full': plugin_error_full,
        'log_file':          tex_log or '(none)',
        'has_orig_tex':      has_orig,
        'has_trans_tex':     has_trans,
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


def source_cache_is_valid():
    """检查已下载的 arXiv 源码包是否可复用，避免 --no-cache 重试反复卡在下载断流。"""
    src_tar = os.path.join(ARXIV_CACHE_DIR, arxiv_id, 'e-print', arxiv_id + '.tar')
    if not os.path.exists(src_tar) or os.path.getsize(src_tar) < 1024:
        return False
    try:
        import tarfile
        return tarfile.is_tarfile(src_tar)
    except Exception:
        return False


# ── 主逻辑：首次 + 重试 ────────────────────────────────────────────────────────
result_pdf = None

TRANSLATE_TEX = os.path.join(ARXIV_CACHE_DIR, arxiv_id, 'workfolder', 'merge_translate_zh.tex')

if keep_translation and os.path.exists(TRANSLATE_TEX):
    # 保留已有 GPT 翻译，只重跑编译：不清缓存，以 no_cache=False 调用插件
    print(f"[driver] ♻️  复用已有翻译缓存: {TRANSLATE_TEX}（跳过 GPT 翻译）", flush=True)
    actual_no_cache = False
elif no_cache:
    # 强制重新翻译/编译；若源码包已经有效缓存，则复用源码，避免 arXiv 下载断流导致无法进入编译阶段。
    clear_compile_cache(full=True)
    if source_cache_is_valid():
        print(f"[driver] ♻️  复用已下载源码缓存（仍会重新翻译/编译）", flush=True)
        actual_no_cache = False
    else:
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
