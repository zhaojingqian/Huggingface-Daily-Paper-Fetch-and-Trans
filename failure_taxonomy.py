#!/usr/bin/env python3
"""Stable failure taxonomy shared by the container driver and host logs."""

import re
from typing import Dict


def _result(code, family, retry_strategy, repair_action, suggestion, evidence="") -> Dict[str, object]:
    return {
        "category": code,
        "family": family,
        "retry_strategy": retry_strategy,
        "repair_action": repair_action,
        "retryable": retry_strategy != "manual_review",
        "suggestion": suggestion,
        "evidence": evidence.strip()[:500],
    }


def _evidence(text: str, pattern: str) -> str:
    match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
    if not match:
        return ""
    start = max(0, match.start() - 100)
    end = min(len(text), match.end() + 180)
    return " ".join(text[start:end].split())


def classify_failure(phase: str, latex_log: str = "", plugin_error: str = "") -> Dict[str, object]:
    """Classify a failure into a stable code and an actionable retry strategy."""
    latex = latex_log or ""
    plugin = plugin_error or ""
    combined = f"{plugin}\n{latex}"

    if phase == "translate":
        if re.search(
            r"insufficient[_ ](?:user[_ ])?quota|insufficient.*balance|"
            r"balance .* is insufficient|额度不足|余额不足",
            plugin,
            re.I,
        ):
            return _result(
                "translate.api_quota", "api", "manual_review", "recharge_api_balance",
                "API 余额或额度不足；充值或切换已授权模型凭据后再重试。",
                _evidence(
                    plugin,
                    r"insufficient[_ ](?:user[_ ])?quota|insufficient.*balance|"
                    r"balance .* is insufficient|额度不足|余额不足",
                ),
            )
        if re.search(r"FileNotFoundError.*(?:workfolder|gpt_log/arxiv_cache)", plugin, re.I | re.S):
            return _result(
                "runtime.workdir_missing", "runtime_path", "reuse_translation", "normalize_compile_workdir",
                "编译工作目录解析失败；规范化容器绝对路径后复用翻译缓存重编译。",
                _evidence(plugin, r"FileNotFoundError.*(?:workfolder|gpt_log/arxiv_cache)"),
            )
        if re.search(r"Tex源文件缺失|source.*not found|找不到.*(?:tex|sty|cls)", plugin, re.I):
            return _result(
                "translate.source_missing", "source", "restore_source", "verify_source_manifest",
                "源码包或被引用的 TeX 文件缺失；先恢复源码，再重新翻译。",
                _evidence(plugin, r"Tex源文件缺失|source.*not found|找不到.*(?:tex|sty|cls)"),
            )
        if re.search(r"401|403|unauthori[sz]ed|invalid.*api.?key", plugin, re.I):
            return _result(
                "translate.api_auth", "api", "manual_review", "fix_api_credentials",
                "API 鉴权失败；修复凭据后再重试。", _evidence(plugin, r"401|403|unauthori[sz]ed|invalid.*api.?key"),
            )
        if re.search(r"429|rate.?limit|too many requests", plugin, re.I):
            return _result(
                "translate.api_rate_limit", "api", "retry_later", "backoff_translation",
                "API 触发限流；退避后重新翻译。", _evidence(plugin, r"429|rate.?limit|too many requests"),
            )
        if re.search(r"timeout|timed out|connection reset|temporary failure", plugin, re.I):
            return _result(
                "translate.network_timeout", "network", "retry_translation", "retry_with_backoff",
                "网络或 API 请求超时；保留源码并退避重试翻译。",
                _evidence(plugin, r"timeout|timed out|connection reset|temporary failure"),
            )
        if re.search(r"RuntimeError", plugin):
            return _result(
                "translate.plugin_runtime", "plugin", "retry_translation", "inspect_plugin_runtime",
                "翻译插件运行时异常；检查 traceback，修复后重新翻译。", _evidence(plugin, r"RuntimeError"),
            )
        if re.search(r"Traceback|\bError:", plugin):
            return _result(
                "translate.plugin_exception", "plugin", "retry_translation", "inspect_plugin_traceback",
                "翻译插件抛出异常；根据 traceback 定位后重新翻译。", _evidence(plugin, r"Traceback|\bError:"),
            )
        return _result(
            "translate.unknown", "translation", "retry_translation", "inspect_translation_output",
            "翻译阶段未产生 TeX；检查网络、API 与插件输出后重试。", plugin[:500],
        )

    if re.search(r"TeX capacity exceeded|input stack size", latex, re.I):
        return _result(
            "compile.macro_recursion", "latex_structure", "reuse_translation", "patch_recursive_macro",
            "宏递归耗尽 TeX 栈；对照原文修复递归宏后复用翻译重编译。",
            _evidence(latex, r"TeX capacity exceeded|input stack size"),
        )
    if re.search(r"Image inclusion failed|Could not find file:.*\.(?:png|jpe?g|pdf|eps)", latex, re.I):
        return _result(
            "compile.asset_missing", "asset", "reuse_translation", "replace_missing_graphic",
            "图片资源缺失；恢复资源或插入占位图后复用翻译重编译。",
            _evidence(latex, r"Image inclusion failed|Could not find file:.*\.(?:png|jpe?g|pdf|eps)"),
        )
    if re.search(r"File [`']?[^\n`']+\.(?:sty|cls|def)[`']? not found", latex, re.I):
        return _result(
            "compile.dependency_missing", "dependency", "reuse_translation", "install_or_stub_dependency",
            "LaTeX 依赖文件缺失；安装依赖或提供兼容 stub 后重编译。",
            _evidence(latex, r"File [`']?[^\n`']+\.(?:sty|cls|def)[`']? not found"),
        )
    if re.search(r"Environment CJK\*? undefined|Undefined control sequence.*?endCJK\*?", latex, re.I | re.S):
        return _result(
            "compile.legacy_cjk_environment", "latex_command", "reuse_translation", "add_cjk_environment_fallback",
            "旧模板使用未加载的 CJK/CJK* 环境；补 XeLaTeX 兼容定义后复用翻译重编译。",
            _evidence(latex, r"Environment CJK\*? undefined|Undefined control sequence.*?endCJK\*?"),
        )
    if re.search(r'"translation"\s*:\s*"\\\\section|\[\s*"\\\\section', latex, re.I):
        return _result(
            "compile.model_response_leak", "translation_artifact", "reuse_translation",
            "strip_serialized_translation_artifact",
            "模型序列化响应泄漏进 TeX；清除整行 JSON/list 残留后复用翻译重编译。",
            _evidence(latex, r'"translation"\s*:\s*"\\\\section|\[\s*"\\\\section'),
        )
    if re.search(r'fontspec Error: The font "SourceSans3-', latex, re.I):
        return _result(
            "compile.font_family_missing", "latex_font", "reuse_translation",
            "fallback_sourcesans3_family",
            "slim 镜像缺少 SourceSans3 字体族；映射到兼容 SourceSansPro 字体后重编译。",
            _evidence(latex, r'fontspec Error: The font "SourceSans3-'),
        )
    if re.search(r"Undefined control sequence.*?\\(?:pdfshellescape|pdfcolorstack)", latex, re.I | re.S):
        return _result(
            "compile.engine_driver_mismatch", "latex_engine", "reuse_translation",
            "remove_pdftex_graphics_driver",
            "graphicx 被强制为 pdftex driver；移除错误 driver 选项后由 XeLaTeX 自动选择。",
            _evidence(latex, r"Undefined control sequence"),
        )
    if re.search(
        r"Undefined control sequence.*?\\pdf(?:infoomitdate|trailerid|suppressptexinfo|gentounicode|output|inclusioncopyfonts)",
        latex,
        re.I | re.S,
    ):
        return _result(
            "compile.pdftex_primitive", "latex_engine", "reuse_translation", "guard_pdftex_primitive",
            "模板调用了 XeLaTeX 不支持的 pdfTeX 原语；加引擎 guard 后重编译。",
            _evidence(latex, r"Undefined control sequence"),
        )
    if re.search(r"Undefined control sequence.*?\\endtcolorbox", latex, re.I | re.S):
        return _result(
            "compile.unmatched_environment", "latex_structure", "reuse_translation",
            "remove_unmatched_environment_ending",
            "存在无对应 begin 的环境结束标签；按环境栈移除多余结束标签后重编译。",
            _evidence(latex, r"Undefined control sequence.*?\\endtcolorbox"),
        )
    if re.search(r"\\pgf@matrix@last@nextcell@options|matrix of nodes", latex, re.I):
        return _result(
            "compile.tikz_matrix_legend", "latex_structure", "reuse_translation",
            "disable_fragile_tikz_matrix_legends",
            "TikZ matrix 图例混入显式 node/draw 后破坏单元格解析；省略图例并保留主图与图注。",
            _evidence(latex, r"\\pgf@matrix@last@nextcell@options|matrix of nodes"),
        )
    if re.search(r"Undefined control sequence.*?\\([A-Za-z])\1[A-Za-z@]+", latex, re.I | re.S):
        return _result(
            "compile.duplicated_macro_initial", "translation_artifact", "reuse_translation",
            "repair_duplicated_macro_initials",
            "翻译结果把宏名首字母重复；仅在原文存在对应单首字母宏时恢复宏名。",
            _evidence(latex, r"Undefined control sequence"),
        )
    if re.search(r"Undefined control sequence", latex, re.I):
        return _result(
            "compile.undefined_command", "latex_command", "reuse_translation", "patch_undefined_command",
            "存在未定义命令；识别命令来源并补兼容定义或修复宏与中文粘连。",
            _evidence(latex, r"Undefined control sequence"),
        )
    if re.search(r"begin\{document\} ended by|Runaway argument|Missing \}|Extra \}|Emergency stop", latex, re.I):
        return _result(
            "compile.structure_mismatch", "latex_structure", "reuse_translation", "restore_tex_structure",
            "环境、参数或大括号结构被破坏；对照原始 TeX 恢复结构后重编译。",
            _evidence(latex, r"begin\{document\} ended by|Runaway argument|Missing \}|Extra \}|Emergency stop"),
        )
    if re.search(r"Missing number|Illegal unit of measure", latex, re.I):
        return _result(
            "compile.numeric_syntax", "latex_syntax", "reuse_translation", "patch_numeric_argument",
            "长度或数值参数语法损坏；修正参数后复用翻译重编译。",
            _evidence(latex, r"Missing number|Illegal unit of measure"),
        )
    if re.search(r"Missing \$ inserted|Extra alignment tab|Misplaced alignment tab", latex, re.I):
        return _result(
            "compile.math_or_alignment", "latex_syntax", "reuse_translation", "restore_math_or_table_syntax",
            "数学或表格对齐语法损坏；从原始 TeX 恢复对应块。",
            _evidence(latex, r"Missing \$ inserted|Extra alignment tab|Misplaced alignment tab"),
        )
    if re.search(r"tcblisting|lstlisting|minted|verbatim", latex, re.I) and re.search(r"LaTeX Error|Package .* Error", latex, re.I):
        return _result(
            "compile.verbatim_corruption", "protected_content", "reuse_translation", "restore_protected_environment",
            "代码或 verbatim 环境被翻译破坏；从原文恢复保护块后重编译。",
            _evidence(latex, r"tcblisting|lstlisting|minted|verbatim"),
        )
    if re.search(r"翻译覆盖率检查失败|translation coverage.*failed", combined, re.I):
        return _result(
            "quality.untranslated_prose", "translation_quality", "retry_translation", "protect_examples_or_retranslate",
            "普通正文翻译覆盖不足；先排除代码/数据示例，再重新翻译真实英文正文。",
            _evidence(combined, r"翻译覆盖率检查失败|translation coverage.*failed"),
        )
    if re.search(
        r"out of memory|cannot allocate memory|(?:process |compile )killed|"
        r"segmentation fault|(?:compile|compilation|command) timed? ?out",
        combined,
        re.I,
    ):
        return _result(
            "compile.resource_exhaustion", "runtime_resource", "retry_later", "reduce_compile_resources",
            "编译资源不足或超时；清理缓存、降低资源压力后重试。",
            _evidence(
                combined,
                r"out of memory|cannot allocate memory|(?:process |compile )killed|"
                r"segmentation fault|(?:compile|compilation|command) timed? ?out",
            ),
        )
    if re.search(r"LaTeX Error|Package .* Error|Fatal error", latex, re.I):
        return _result(
            "compile.latex_error", "latex", "reuse_translation", "inspect_first_latex_error",
            "LaTeX 编译错误；优先处理日志中的第一个错误后重编译。",
            _evidence(latex, r"LaTeX Error|Package .* Error|Fatal error"),
        )
    return _result(
        "compile.unknown", "compile", "manual_review", "inspect_compile_log",
        "未匹配已知编译类型；检查结构化证据和完整日志后扩展分类规则。", latex[:500],
    )
