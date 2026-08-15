#!/usr/bin/env python3
"""
在 gpt-academic Docker 容器内运行的全文翻译驱动脚本
用法: python3 full_translate_driver.py <arxiv_id> [--no-cache] [--retries N]
输出: RESULT:SUCCESS:<pdf_path>  或  RESULT:ERROR:<msg>
"""
import sys, os, glob, time, shutil
import latex_translation_filters as _ltf
from failure_taxonomy import classify_failure
try:
    # Container deployment copies this support module beside the driver.
    from translation_quality import (
        analyze_tex as _analyze_translated_tex,
        is_untranslated_prose as _is_untranslated_prose,
    )
    from residual_translation import (
        candidate_line_numbers as _residual_candidate_lines,
        normalize_residual_response as _normalize_residual_response,
        residual_score as _residual_score,
        terminal_repair_eligible as _terminal_repair_eligible,
    )
except ImportError:
    # Keep direct host-side diagnostics/imports usable from the repository.
    from paperhub.translation_quality import (
        analyze_tex as _analyze_translated_tex,
        is_untranslated_prose as _is_untranslated_prose,
    )
    from paperhub.residual_translation import (
        candidate_line_numbers as _residual_candidate_lines,
        normalize_residual_response as _normalize_residual_response,
        residual_score as _residual_score,
        terminal_repair_eligible as _terminal_repair_eligible,
    )

try:
    from latex_pipeline import (
        configure as _configure_latex_pipeline,
        install_gpt_academic_patches as _install_gpt_academic_latex_patches,
        patch_and_recompile,
    )
except ImportError:
    from paperhub.latex_pipeline import (
        configure as _configure_latex_pipeline,
        install_gpt_academic_patches as _install_gpt_academic_latex_patches,
        patch_and_recompile,
    )

sys.path.insert(0, '/gpt')
os.chdir('/gpt')

arxiv_id        = sys.argv[1] if len(sys.argv) > 1 else None
no_cache        = "--no-cache" in sys.argv
keep_translation = "--keep-translation" in sys.argv   # 保留已有翻译，只重跑编译
max_retries = 0   # 只翻译一次，不重试
SPLITTER_CACHE_VERSION = "paper-trans-splitter-2026-08-15-v45-policy-bounded"

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
def _bounded_positive_int_env(name, default, minimum, maximum):
    """Read an operator-controlled timeout without making the driver fragile."""
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


_LLM_HTTP_TIMEOUT = _bounded_positive_int_env(
    "PAPER_TRANS_LLM_HTTP_TIMEOUT",
    120,
    5,
    3600,
)

# arXiv source retrieval is deliberately bounded separately from LLM requests.
# ``requests``' read timeout is an *idle* timeout: a peer that sends one byte
# periodically can otherwise hold the global translation lock forever.  Keep
# these knobs operator-configurable, but put safe ceilings on each attempt and
# on the complete proxy/direct fallback sequence.
_SOURCE_DOWNLOAD_CONNECT_TIMEOUT = _bounded_positive_int_env(
    "PAPER_TRANS_SOURCE_CONNECT_TIMEOUT", 15, 5, 60,
)
_SOURCE_DOWNLOAD_READ_TIMEOUT = _bounded_positive_int_env(
    "PAPER_TRANS_SOURCE_READ_TIMEOUT", 90, 15, 300,
)
_SOURCE_DOWNLOAD_ATTEMPT_SECONDS = _bounded_positive_int_env(
    "PAPER_TRANS_SOURCE_ATTEMPT_SECONDS", 300, 60, 1800,
)
_SOURCE_DOWNLOAD_TOTAL_SECONDS = _bounded_positive_int_env(
    "PAPER_TRANS_SOURCE_TOTAL_SECONDS", 600, 120, 3600,
)
_SOURCE_DOWNLOAD_RATE_GRACE_SECONDS = _bounded_positive_int_env(
    "PAPER_TRANS_SOURCE_RATE_GRACE_SECONDS", 45, 10, 300,
)
_SOURCE_DOWNLOAD_MIN_BYTES_PER_SECOND = _bounded_positive_int_env(
    "PAPER_TRANS_SOURCE_MIN_BYTES_PER_SECOND", 8 * 1024, 1024, 512 * 1024,
)
_SOURCE_DOWNLOAD_MAX_BYTES = _bounded_positive_int_env(
    "PAPER_TRANS_SOURCE_MAX_BYTES", 512 * 1024 * 1024, 1024 * 1024, 2 * 1024 * 1024 * 1024,
)


class _PatchedSession(_OrigSession):
    def __init__(self):
        super().__init__()
        self.proxies.update(PROXIES_DICT)

    def request(self, method, url, **kwargs):
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = _LLM_HTTP_TIMEOUT
        return super().request(method, url, **kwargs)


_orig_request = _req.request


def _patched_request(method, url, **kwargs):
    if kwargs.get("timeout") is None:
        kwargs["timeout"] = _LLM_HTTP_TIMEOUT
    return _orig_request(method, url, **kwargs)


_req.Session = _PatchedSession
_req.request = _patched_request

# gpt-academic 的 TeX monkey-patch 统一收口到 latex_pipeline；driver 只保留
# 请求/生命周期边界，不再内嵌编译、主文件选择和 include merge 实现。
_install_gpt_academic_latex_patches()


try:
    from translation_runtime import bootstrap as _bootstrap_translation_runtime
except ImportError:
    from paperhub.translation_runtime import bootstrap as _bootstrap_translation_runtime


_bootstrap_translation_runtime(SPLITTER_CACHE_VERSION)

from toolbox import get_conf, ChatBotWithCookies, default_user_name

api_key   = get_conf('API_KEY')
llm_model = os.environ.get("PAPER_TRANS_LLM_MODEL") or get_conf('LLM_MODEL')
ARXIV_CACHE_DIR = get_conf('ARXIV_CACHE_DIR')
print(f"[driver] 模型: {llm_model}", flush=True)
print(f"[driver] 缓存目录: {ARXIV_CACHE_DIR}", flush=True)

from crazy_functions.Latex_Function import Latex翻译中文并重新编译PDF

arxiv_url = f"https://arxiv.org/abs/{arxiv_id}"

# 模块级：收集插件运行中所有完整消息（不截断），供 diagnose_failure 分析
_plugin_msgs_full: list[str] = []
_last_quality_report: dict = {}


def translation_quality_report(workfolder: str) -> dict:
    """Apply the shared repository/publication translated-TeX quality gate."""
    trans_tex = os.path.join(workfolder, "merge_translate_zh.tex")
    if not os.path.exists(trans_tex):
        return {"ok": False, "reason": "missing merge_translate_zh.tex"}
    report = _analyze_translated_tex(trans_tex)
    report["ok"] = not _is_untranslated_prose(report)
    report["samples"] = [
        (sample["line"], sample["text"])
        for sample in report.get("samples", [])
    ]
    return report


def check_pdf_integrity(pdf_path: str) -> bool:
    """Check that the PDF file exists, is larger than 50KB, and can be successfully parsed with pages."""
    if not os.path.exists(pdf_path) or os.path.getsize(pdf_path) < 50 * 1024:
        return False
    try:
        import pypdf
        reader = pypdf.PdfReader(pdf_path)
        return len(reader.pages) > 0
    except Exception as e:
        print(f"[driver] ⚠️ PDF 完整性检查失败 ({os.path.basename(pdf_path)}): {e}", flush=True)
        return False


def translation_quality_ok(workfolder: str, arxiv_id_: str) -> bool:
    global _last_quality_report
    report = translation_quality_report(workfolder)
    _last_quality_report = report
    if not report.get("ok"):
        print(
            f"[driver] ❌ 翻译覆盖率检查失败: {arxiv_id_} "
            f"cjk_pct={report.get('cjk_pct', 0):.1f}% "
            f"long_english_lines={report.get('long_english_lines', 0)} "
            f"prose_lines={report.get('prose_lines', 0)}",
            flush=True,
        )
        for line_no, sample in report.get("samples", []):
            print(f"[driver]    untranslated line {line_no}: {sample}", flush=True)
        return False

    print(
        f"[driver] ✅ 翻译覆盖率检查通过: {arxiv_id_} "
        f"cjk_pct={report.get('cjk_pct', 0):.1f}% "
        f"long_english_lines={report.get('long_english_lines', 0)}",
        flush=True,
    )
    return True


def repair_terminal_translation_residuals(workfolder: str, arxiv_id_: str) -> bool:
    """Retranslate only a few quality-gate lines and keep proven improvements.

    This is intentionally a terminal repair, not a second chunking framework:
    it runs only after a high-coverage full translation, issues at most twelve
    serialized requests, and commits each line only when the shared quality
    score strictly improves and LaTeX structure remains identical.
    """
    global _last_quality_report

    report = translation_quality_report(workfolder)
    _last_quality_report = report
    policy_report = dict(report)
    if os.environ.get("PAPER_TRANS_FORCE_RESIDUAL_REPAIR") == "1":
        policy_report["ok"] = False
    if not _terminal_repair_eligible(policy_report):
        return False

    trans_tex = os.path.join(workfolder, "merge_translate_zh.tex")
    try:
        with open(trans_tex, encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
    except OSError as exc:
        print(f"[driver] ⚠️  末端残留修复无法读取 TeX: {exc}", flush=True)
        return False

    llm_kwargs = {
        "api_key": api_key,
        "llm_model": llm_model,
        "top_p": 1.0,
        "max_length": None,
        "temperature": 0.1,
    }
    from request_llms.bridge_all import predict_no_ui_long_connection

    system_prompt = (
        "你是 LaTeX 学术论文中文翻译修复器。只输出修复后的单行 TeX，"
        "不要 Markdown、解释或前后缀。把自然语言英文正文翻译成中文；"
        "保留已有中文、所有 LaTeX 命令、参数、数学、引用键、标签和专名。"
    )
    score = _residual_score(report)
    repaired = 0
    candidates = _residual_candidate_lines(report)
    print(
        f"[driver] 🩹 末端英文残留定向修复: lines={candidates} score={score}",
        flush=True,
    )

    for line_no in candidates:
        index = line_no - 1
        if not 0 <= index < len(lines):
            continue
        original = lines[index]
        source = original.rstrip("\r\n")
        if not source.strip():
            continue
        try:
            response = predict_no_ui_long_connection(
                source,
                llm_kwargs,
                history=[],
                sys_prompt=system_prompt,
                observe_window=None,
                console_silence=True,
            )
        except Exception as exc:
            print(
                f"[driver] ⚠️  残留行 {line_no} 请求失败: {type(exc).__name__}: {exc}",
                flush=True,
            )
            continue
        translated = _normalize_residual_response(response)
        invalid = _ltf.llm_translation_response_invalid(source, translated)
        if invalid or _ltf.llm_translation_response_untranslated(source, translated):
            print(
                f"[driver] ⚠️  残留行 {line_no} 响应未采用: {invalid or 'still_untranslated'}",
                flush=True,
            )
            continue

        newline = "\n" if original.endswith("\n") else ""
        lines[index] = translated + newline
        with open(trans_tex, "w", encoding="utf-8") as handle:
            handle.writelines(lines)
        next_report = translation_quality_report(workfolder)
        next_score = _residual_score(next_report)
        if next_score >= score:
            lines[index] = original
            with open(trans_tex, "w", encoding="utf-8") as handle:
                handle.writelines(lines)
            print(
                f"[driver] ↩️  残留行 {line_no} 未改善质量分数，已回滚",
                flush=True,
            )
            continue
        score = next_score
        report = next_report
        repaired += 1
        print(
            f"[driver] ✅ 残留行 {line_no} 已修复: score={score}",
            flush=True,
        )
    _last_quality_report = report
    return repaired > 0 and bool(report.get("ok"))


def _compile_health_report(workfolder: str) -> dict:
    """Read one TeX log and classify all compile-health facts once."""
    import re as _re

    log_path = os.path.join(workfolder, "merge_translate_zh.log")
    if not os.path.exists(log_path):
        return {"exists": False, "complete": False, "failures": [], "warnings": []}
    with open(log_path, encoding="utf-8", errors="replace") as f:
        log = f.read()

    primitive_re = _re.compile(
        r"\\(?:" + "|".join(_re.escape(name) for name in _ltf.PDFTEX_PRIMITIVE_NAMES) + r")\b"
    )
    fatal_undefined = False
    pdftex_undefined = False
    for match in _re.finditer(r"Undefined control sequence", log):
        context = log[match.start(): match.start() + 300]
        if primitive_re.search(context):
            pdftex_undefined = True
        else:
            fatal_undefined = True

    failures = []
    if fatal_undefined:
        failures.append("undefined control sequence")
    if _re.search(r"Missing number, treated as zero", log):
        failures.append("missing number")

    promoted = []
    if _re.search(r"(?<!Package natbib Warning: )Citation .* undefined", log):
        promoted.append("undefined citation")
    if _re.search(r"Reference .* undefined", log):
        promoted.append("undefined reference")
    if _re.search(r"There were undefined references", log):
        promoted.append("undefined references")

    warnings = []
    if _re.search(r"Package natbib Warning: .* undefined", log):
        warnings.append("natbib undefined")
    if pdftex_undefined:
        warnings.append("pdftex primitive undef")

    sample = ""
    sample_re = _re.compile(
        r".{0,120}(Missing number, treated as zero|"
        r"(?<!Package natbib Warning: )Citation .* undefined|"
        r"Reference .* undefined|There were undefined references|"
        r"Label\(s\) may have changed|Rerun to get cross-references right|"
        r"Package natbib Warning: .* undefined).{0,160}",
        flags=_re.DOTALL,
    )
    sample_match = sample_re.search(log)
    if sample_match:
        sample = " ".join(sample_match.group(0).split())[:260]
    fatal_sample = ""
    if fatal_undefined:
        for match in _re.finditer(r"Undefined control sequence", log):
            context = log[match.start(): match.start() + 300]
            if not primitive_re.search(context):
                fatal_sample = " ".join(context[:200].split())[:260]
                break

    return {
        "exists": True,
        "complete": (
            "Output written on merge_translate_zh.xdv" in log
            or "Output written on merge_translate_zh.pdf" in log
        ),
        "failures": failures,
        "promoted": promoted,
        "warnings": warnings,
        "sample": sample,
        "fatal_sample": fatal_sample,
    }


def latex_compile_health_ok(workfolder: str, arxiv_id_: str, strict: bool = False) -> bool:
    """Reject PDFs that compiled but still have unresolved TeX/cite/ref issues."""
    report = _compile_health_report(workfolder)
    if not report["exists"]:
        print(
            f"[driver] ⚠️  找不到编译日志，跳过健康检查: "
            f"{os.path.join(workfolder, 'merge_translate_zh.log')}",
            flush=True,
        )
        return True
    if not report["complete"]:
        print(
            f"[driver] ❌ 编译健康检查失败: {arxiv_id_} 找不到输出写入标记，"
            "编译日志不完整（可能已被 OOM 强杀）",
            flush=True,
        )
        return False

    failures = list(report["failures"])
    warnings = list(report["warnings"])
    if strict:
        failures.extend(report["promoted"])
    else:
        warnings.extend(report["promoted"])

    if warnings and not failures:
        print(
            f"[driver] ⚠️  编译健康警告(非致命): {arxiv_id_} "
            f"warnings={', '.join(warnings)}",
            flush=True,
        )
    if failures:
        print(
            f"[driver] ❌ 编译健康检查失败: {arxiv_id_} "
            f"issues={', '.join(failures + warnings)}",
            flush=True,
        )
        if report["sample"]:
            print(f"[driver]    log: {report['sample']}", flush=True)
        if report["fatal_sample"]:
            print(f"[driver]    log: {report['fatal_sample']}", flush=True)
        return False

    print(f"[driver] ✅ 编译健康检查通过: {arxiv_id_}", flush=True)
    return True


def latex_compile_health_only_stale_refs(workfolder: str) -> bool:
    """True when the shared health report contains only rerunnable ref warnings."""
    report = _compile_health_report(workfolder)
    if not report["exists"] or not report["complete"] or report["failures"]:
        return False
    if "undefined citation" in report["promoted"]:
        return False
    return bool(
        "undefined reference" in report["promoted"]
        or "undefined references" in report["promoted"]
    )


_configure_latex_pipeline(
    arxiv_cache_dir=ARXIV_CACHE_DIR,
    check_pdf_integrity=check_pdf_integrity,
    translation_quality_ok=translation_quality_ok,
    latex_compile_health_ok=latex_compile_health_ok,
    latex_compile_health_only_stale_refs=latex_compile_health_only_stale_refs,
)


def run_translation(attempt_no_cache: bool, attempt_idx: int) -> str | None:
    """执行一次翻译+编译，成功返回 PDF 路径，否则返回 None。"""
    llm_kwargs = {
        'api_key': api_key, 'llm_model': llm_model,
        'top_p': 1.0, 'max_length': None, 'temperature': 0.2,
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
    if check_pdf_integrity(candidate):
        kb = os.path.getsize(candidate) // 1024
        if not translation_quality_ok(workfolder, arxiv_id):
            return None
        if not latex_compile_health_ok(workfolder, arxiv_id):
            return None
        print(f"[driver|{elapsed()}] ✅ translate_zh.pdf ({kb}KB)", flush=True)
        return candidate

    # 备选：workfolder 根目录里的翻译 PDF（名字含 translate_zh）
    for fname in ('merge_translate_zh.pdf', 'translate_zh.pdf'):
        fp = os.path.join(workfolder, fname)
        if check_pdf_integrity(fp):
            kb = os.path.getsize(fp) // 1024
            if not translation_quality_ok(workfolder, arxiv_id):
                return None
            if not latex_compile_health_ok(workfolder, arxiv_id):
                return None
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
        if check_pdf_integrity(best):
            kb = os.path.getsize(best) // 1024
            if not translation_quality_ok(workfolder, arxiv_id):
                return None
            if not latex_compile_health_ok(workfolder, arxiv_id):
                return None
            print(f"[driver|{elapsed()}] ✅ 找到 workfolder 根目录 PDF: {os.path.basename(best)} ({kb}KB)", flush=True)
            return best

    print(f"[driver|{elapsed()}] ❌ 本次未生成有效翻译 PDF（>50KB）", flush=True)
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
                or 'Undefined control sequence' in ln
                or 'LaTeX Error:' in ln
                or 'Package Error:' in ln
                or 'Missing number' in ln
                or 'Illegal unit of measure' in ln
                or 'TeX capacity exceeded' in ln
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

    # ── 3. 稳定分类 + 可执行重试策略 ──────────────────────────────────────
    diagnostic_text = '\n'.join(errors_raw + [tex_log_tail])
    if phase == "compile" and _last_quality_report and not _last_quality_report.get("ok"):
        quality_evidence = (
            "翻译覆盖率检查失败: "
            f"cjk_pct={_last_quality_report.get('cjk_pct', 0):.1f}% "
            f"long_english_lines={_last_quality_report.get('long_english_lines', 0)} "
            f"prose_lines={_last_quality_report.get('prose_lines', 0)}"
        )
        classified = classify_failure("compile", quality_evidence, plugin_error_full)
    else:
        classified = classify_failure(phase, diagnostic_text, plugin_error_full)

    diag = {
        'arxiv_id':          arxiv_id_,
        'phase':             phase,
        **classified,
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
    reason = _ltf.source_tar_safety_error(src_tar)
    if reason:
        print(f"[driver] ⚠️  arXiv 源码缓存不安全/无效: {reason}", flush=True)
        return False
    return True


def _source_content_length(response):
    """Return a sane Content-Length when the server supplied one."""
    raw = response.headers.get('Content-Length')
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise RuntimeError(f'invalid Content-Length: {raw!r}')
    if value < 0:
        raise RuntimeError(f'invalid Content-Length: {raw!r}')
    if value > _SOURCE_DOWNLOAD_MAX_BYTES:
        raise RuntimeError(
            'source archive exceeds download limit: '
            f'{value} > {_SOURCE_DOWNLOAD_MAX_BYTES} bytes'
        )
    return value


def prefetch_source_cache(max_rounds=3):
    """预下载 arXiv 源码包，代理/直连交替重试，且为慢速断流设总时限。"""
    src_dir = os.path.join(ARXIV_CACHE_DIR, arxiv_id, 'e-print')
    src_tar = os.path.join(src_dir, arxiv_id + '.tar')
    tmp_tar = src_tar + '.part'
    url = f'https://arxiv.org/e-print/{arxiv_id}'

    if source_cache_is_valid():
        print(f"[driver] ♻️  arXiv 源码缓存已存在: {src_tar}", flush=True)
        return True

    os.makedirs(src_dir, exist_ok=True)
    plans = [('proxy', True), ('direct', False)]
    started_at = time.monotonic()
    total_deadline = started_at + _SOURCE_DOWNLOAD_TOTAL_SECONDS

    for round_idx in range(1, max_rounds + 1):
        for label, use_proxy in plans:
            try:
                remaining = total_deadline - time.monotonic()
                if remaining <= 0:
                    print(
                        '[driver] ⚠️  arXiv 源码预下载达到总时限 '
                        f'({_SOURCE_DOWNLOAD_TOTAL_SECONDS}s)，停止重试',
                        flush=True,
                    )
                    return False
                attempt_started = time.monotonic()
                attempt_deadline = min(
                    total_deadline,
                    attempt_started + _SOURCE_DOWNLOAD_ATTEMPT_SECONDS,
                )
                if os.path.exists(tmp_tar):
                    os.remove(tmp_tar)
                session = _OrigSession()
                if use_proxy:
                    session.proxies.update(PROXIES_DICT)
                else:
                    session.trust_env = False
                print(f"[driver] ⬇️  预下载 arXiv 源码 ({label}, round={round_idx}): {url}", flush=True)
                request_read_timeout = min(
                    _SOURCE_DOWNLOAD_READ_TIMEOUT,
                    max(15, int(remaining)),
                )
                with session.get(
                    url,
                    stream=True,
                    timeout=(_SOURCE_DOWNLOAD_CONNECT_TIMEOUT, request_read_timeout),
                ) as r:
                    r.raise_for_status()
                    content_length = _source_content_length(r)
                    # requests may transparently decode an HTTP-compressed
                    # entity, so its yielded byte count need not equal the
                    # wire Content-Length in that rare case.
                    if r.headers.get('Content-Encoding', '').lower() not in ('', 'identity'):
                        content_length = None
                    written = 0
                    with open(tmp_tar, 'wb') as f:
                        for chunk in r.iter_content(chunk_size=1024 * 256):
                            if chunk:
                                f.write(chunk)
                                written += len(chunk)
                            elapsed = time.monotonic() - attempt_started
                            if time.monotonic() > attempt_deadline:
                                raise TimeoutError(
                                    'source download attempt exceeded '
                                    f'{int(attempt_deadline - attempt_started)}s'
                                )
                            if (
                                elapsed >= _SOURCE_DOWNLOAD_RATE_GRACE_SECONDS
                                and written / max(elapsed, 1) < _SOURCE_DOWNLOAD_MIN_BYTES_PER_SECOND
                            ):
                                raise TimeoutError(
                                    'source download below minimum rate: '
                                    f'{written / max(elapsed, 1):.0f}B/s < '
                                    f'{_SOURCE_DOWNLOAD_MIN_BYTES_PER_SECOND}B/s'
                                )
                    if content_length is not None and written != content_length:
                        raise RuntimeError(
                            'incomplete source archive: '
                            f'expected {content_length} bytes, got {written}'
                        )
                if os.path.getsize(tmp_tar) < 1024:
                    raise RuntimeError('downloaded source is too small')
                unsafe_reason = _ltf.source_tar_safety_error(tmp_tar)
                if unsafe_reason:
                    raise RuntimeError(
                        "downloaded source archive rejected: " + unsafe_reason
                    )
                os.replace(tmp_tar, src_tar)
                kb = os.path.getsize(src_tar) // 1024
                print(f"[driver] ✅ arXiv 源码预下载成功: {src_tar} ({kb}KB)", flush=True)
                return True
            except Exception as e:
                print(f"[driver] ⚠️  arXiv 源码预下载失败 ({label}, round={round_idx}): {type(e).__name__}: {e}", flush=True)
                try:
                    if os.path.exists(tmp_tar):
                        os.remove(tmp_tar)
                except Exception:
                    pass
        remaining = total_deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(2 * round_idx, 6, remaining))

    return False


def prepare_keep_translation_workfolder():
    """
    只有宿主机恢复了 merge_translate_zh.tex、但 workfolder 源码不完整时：
    1. 确保 arXiv 源码包已缓存；
    2. 解压源码并重建 gpt-academic workfolder；
    3. 放回已翻译 tex，并尽量生成 merge.tex 供修补/诊断使用。
    """
    src_tar = os.path.join(ARXIV_CACHE_DIR, arxiv_id, 'e-print', arxiv_id + '.tar')
    extract_dst = os.path.join(ARXIV_CACHE_DIR, arxiv_id, 'extract')

    if not os.path.exists(TRANSLATE_TEX):
        return False
    try:
        with open(TRANSLATE_TEX, 'rb') as f:
            translated_tex = f.read()
    except Exception as e:
        print(f"[driver] ⚠️  读取翻译 tex 失败，无法恢复 workfolder: {e}", flush=True)
        return False

    if not (source_cache_is_valid() or prefetch_source_cache()):
        print(f"[driver] ⚠️  源码缓存不可用，无法恢复 workfolder", flush=True)
        return False

    try:
        from toolbox import extract_archive
        from crazy_functions.Latex_Function import (
            descend_to_extracted_folder_if_exist,
            move_project,
        )
        from crazy_functions.latex_fns import latex_toolbox as _lt_local

        if os.path.exists(extract_dst):
            shutil.rmtree(extract_dst)
        os.makedirs(extract_dst, exist_ok=True)
        extract_archive(file_path=src_tar, dest_dir=extract_dst)

        project_folder = descend_to_extracted_folder_if_exist(extract_dst)
        os.makedirs(project_folder, exist_ok=True)
        # 也放一份到 extract 侧，若后续退回插件编译，move_project 后仍可跳过 GPT。
        with open(os.path.join(project_folder, 'merge_translate_zh.tex'), 'wb') as f:
            f.write(translated_tex)

        workfolder = move_project(project_folder, arxiv_id)
        with open(os.path.join(workfolder, 'merge_translate_zh.tex'), 'wb') as f:
            f.write(translated_tex)

        file_manifest = [
            f for f in glob.glob(f'{workfolder}/**/*.tex', recursive=True)
            if not os.path.basename(f).startswith('merge')
        ]
        if file_manifest:
            maintex = _lt_local.find_main_tex_file(file_manifest, 'translate_zh')
            with open(maintex, 'r', encoding='utf-8', errors='replace') as f:
                merged_content = _lt_local.merge_tex_files(workfolder, f.read(), 'translate_zh')
            with open(os.path.join(workfolder, 'merge.tex'), 'w', encoding='utf-8', errors='replace') as f:
                f.write(merged_content)
            print(f"[driver] ✅ 已恢复完整 workfolder 并生成 merge.tex: {workfolder}", flush=True)
        else:
            print(f"[driver] ⚠️  源码解压后未找到 tex 文件，仅恢复中文 tex: {workfolder}", flush=True)
        return True
    except Exception as e:
        print(f"[driver] ⚠️  恢复 keep-translation workfolder 失败: {type(e).__name__}: {e}", flush=True)
        return False


# ── 主逻辑：首次 + 重试 ────────────────────────────────────────────────────────
result_pdf = None

WORKFOLDER = os.path.join(ARXIV_CACHE_DIR, arxiv_id, 'workfolder')
TRANSLATE_TEX = os.path.join(WORKFOLDER, 'merge_translate_zh.tex')
ORIG_TEX = os.path.join(WORKFOLDER, 'merge.tex')

if keep_translation and os.path.exists(TRANSLATE_TEX) and os.path.exists(ORIG_TEX):
    # 保留已有 GPT 翻译，只重跑编译。绕开插件生成器，避免它重建 workfolder 后删掉已恢复的中文 tex。
    print(f"[driver] ♻️  复用已有翻译缓存: {TRANSLATE_TEX}（直接重编译，跳过 GPT 翻译）", flush=True)
    repair_terminal_translation_residuals(WORKFOLDER, arxiv_id)
    result_pdf = patch_and_recompile(WORKFOLDER, arxiv_id)
else:
    if keep_translation and os.path.exists(TRANSLATE_TEX):
        # 只有中文 tex、没有完整源码 workfolder 时，先重建 workfolder 并直编译。
        print(f"[driver] ♻️  发现翻译缓存但 workfolder 不完整，尝试恢复源码后直编译", flush=True)
        if prepare_keep_translation_workfolder():
            repair_terminal_translation_residuals(WORKFOLDER, arxiv_id)
            result_pdf = patch_and_recompile(WORKFOLDER, arxiv_id)
        if result_pdf:
            actual_no_cache = False
        else:
            print(f"[driver] ⚠️  直编译未成功，退回插件路径（仍尝试复用翻译 tex）", flush=True)
        actual_no_cache = False
    elif no_cache:
        # 强制重新翻译/编译；若源码包已经有效缓存，则复用源码，避免 arXiv 下载断流导致无法进入编译阶段。
        clear_compile_cache(full=True)
        if source_cache_is_valid() or prefetch_source_cache():
            print(f"[driver] ♻️  复用已下载源码缓存（仍会重新翻译/编译）", flush=True)
            actual_no_cache = False
        else:
            actual_no_cache = True
    else:
        if not source_cache_is_valid():
            prefetch_source_cache()
        actual_no_cache = False

    if not result_pdf:
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
        repair_terminal_translation_residuals(WORKFOLDER, arxiv_id)
        result_pdf = patch_and_recompile(WORKFOLDER, arxiv_id)

# ── 输出结果 ────────────────────────────────────────────────────────────────
if result_pdf:
    print(f"RESULT:SUCCESS:{result_pdf}", flush=True)
    # gpt-academic may leave non-daemon worker threads alive after all output
    # has been produced.  This file is a one-shot subprocess, so waiting for
    # those idle workers only makes the host wrapper appear hung.
    os._exit(0)
else:
    workfolder_ = os.path.join(ARXIV_CACHE_DIR, arxiv_id, 'workfolder')
    diagnose_failure(workfolder_, arxiv_id)
    print(f"RESULT:ERROR:所有 {max_retries+1} 次尝试均未生成 PDF", flush=True)
    os._exit(1)
