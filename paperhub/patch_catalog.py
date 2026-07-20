#!/usr/bin/env python3
"""Catalog of reusable repair patches and the failure classes they address."""

from typing import Dict, Iterable, List


PATCH_CATALOG: Dict[str, Dict[str, object]] = {
    "translate.api_auth": {
        "patches": ("fix_api_credentials",),
        "source": "config_private.py / translate_full.py",
        "strategy": "manual_review",
        "note": "凭据类失败不自动重试；先修复 API key 或代理配置，再重跑翻译。",
    },
    "translate.api_rate_limit": {
        "patches": ("backoff_translation",),
        "source": "translate_full.py / run_repair.py",
        "strategy": "retry_later",
        "note": "记录限流窗口并退避重试，避免重复消耗翻译请求。",
    },
    "translate.network_timeout": {
        "patches": ("retry_with_backoff",),
        "source": "translate_full.py / full_translate_driver.py",
        "strategy": "retry_translation",
        "note": "优先复用源码和翻译缓存，网络恢复后退避重试。",
    },
    "translate.plugin_runtime": {
        "patches": ("inspect_plugin_runtime",),
        "source": "full_translate_driver.py / logs/pdf_errors",
        "strategy": "retry_translation",
        "note": "检查容器插件运行时和残留进程，再决定是否清缓存重译。",
    },
    "translate.plugin_exception": {
        "patches": ("inspect_plugin_traceback",),
        "source": "full_translate_driver.py / logs/pdf_errors",
        "strategy": "retry_translation",
        "note": "根据结构化 traceback 定位插件异常，避免盲目重复翻译。",
    },
    "translate.unknown": {
        "patches": ("inspect_translation_output",),
        "source": "translate_full.py / logs/pdf_errors",
        "strategy": "retry_translation",
        "note": "检查翻译输出是否为空、被截断或包含模型回显后再重试。",
    },
    "compile.asset_missing": {
        "patches": ("patch_missing_graphics",),
        "source": "full_translate_driver.py",
        "strategy": "reuse_translation",
        "note": "将安全的相对图片引用替换为资源或可编译占位图。",
    },
    "compile.macro_recursion": {
        "patches": ("patch_recursive_macro",),
        "source": "full_translate_driver.py / latex_translation_filters.py",
        "strategy": "reuse_translation",
        "note": "限制递归宏展开并复用已有中文 TeX 重编译。",
    },
    "compile.dependency_missing": {
        "patches": ("install_or_stub_dependency",),
        "source": "scripts/setup_docker_env.sh / docker/latex-slim",
        "strategy": "reuse_translation",
        "note": "优先补齐容器依赖或加入兼容 stub，再重编译。",
    },
    "compile.undefined_command": {
        "patches": (
            "patch_xelatex_compatibility_fallbacks",
            "patch_fontawesome_legacy_aliases",
            "patch_spurious_cjk_command_escapes",
            "patch_missing_math_aliases",
        ),
        "source": "full_translate_driver.py / latex_translation_filters.py",
        "strategy": "reuse_translation",
        "note": "优先补兼容命令、图标 fallback 和中文误转义清理。",
    },
    "compile.numeric_syntax": {
        "patches": ("patch_tcolorbox_opening_options", "patch_numeric_argument"),
        "source": "full_translate_driver.py / latex_translation_filters.py",
        "strategy": "reuse_translation",
        "note": "从原始 TeX 恢复不可翻译的环境键名、长度单位和数值参数。",
    },
    "compile.structure_mismatch": {
        "patches": (
            "patch_fragile_cleveref_references",
            "patch_duplicate_end_environments",
            "patch_unbalanced_groups_in_tcolorboxes",
        ),
        "source": "full_translate_driver.py / latex_translation_filters.py",
        "strategy": "reuse_translation",
        "note": "修复 runaway argument、重复环境结束和引用宏结构损坏。",
    },
    "compile.math_or_alignment": {
        "patches": ("restore_math_or_table_syntax",),
        "source": "full_translate_driver.py / latex_translation_filters.py",
        "strategy": "reuse_translation",
        "note": "恢复数学分隔符、对齐环境和表格结构后重编译。",
    },
    "compile.verbatim_corruption": {
        "patches": ("restore_protected_environment",),
        "source": "full_translate_driver.py / latex_translation_filters.py",
        "strategy": "reuse_translation",
        "note": "从原始 TeX 恢复 verbatim、代码和提示块，避免翻译破坏命令。",
    },
    "compile.resource_exhaustion": {
        "patches": ("reduce_compile_resources",),
        "source": "scripts/run_latex_slim.sh / full_translate_driver.py",
        "strategy": "retry_later",
        "note": "确认容器内存、Swap 和超时，再用资源友好的编译参数重试。",
    },
    "compile.pdftex_primitive": {
        "patches": ("patch_pdftex_primitives_for_xelatex",),
        "source": "full_translate_driver.py / latex_translation_filters.py",
        "strategy": "reuse_translation",
        "note": "为 XeLaTeX 不支持的 pdfTeX 原语加 engine guard。",
    },
    "compile.legacy_cjk_environment": {
        "patches": ("add_xelatex_compatibility_fallbacks",),
        "source": "latex_translation_filters.py / full_translate_driver.py",
        "strategy": "reuse_translation",
        "note": "为未定义或未被 XeLaTeX 暴露的旧 CJK/CJK* 环境提供 no-op 兼容。",
    },
    "runtime.workdir_missing": {
        "patches": ("normalize_compile_workdir",),
        "source": "full_translate_driver.py",
        "strategy": "reuse_translation",
        "note": "将插件相对工作目录锚定到容器绝对路径。",
    },
    "translate.source_missing": {
        "patches": ("normalize_tex_include_target", "restore_source_manifest"),
        "source": "full_translate_driver.py / latex_translation_filters.py",
        "strategy": "restore_source",
        "note": "清理 input/include 路径空白并校验源码清单，再继续翻译。",
    },
    "quality.untranslated_prose": {
        "patches": ("split_preserved_prose", "protect_translation_artifacts"),
        "source": "full_translate_driver.py / latex_translation_filters.py",
        "strategy": "retry_translation",
        "note": "扩大正文安全拆分并清理模型回显后重新翻译。",
    },
    "compile.latex_error": {
        "patches": ("inspect_first_latex_error",),
        "source": "logs/pdf_errors/<id>.log",
        "strategy": "reuse_translation",
        "note": "以第一处 LaTeX 错误为根因，避免被后续连锁错误误导。",
    },
    "compile.unknown": {
        "patches": ("inspect_compile_log",),
        "source": "logs/pdf_errors/<id>.log / full_translate_driver.py",
        "strategy": "manual_review",
        "note": "保留翻译 TeX 和完整日志，先补充 taxonomy 再新增定向 patch。",
    },
    "unknown.unstructured": {
        "patches": ("inspect_driver_output",),
        "source": "logs/pdf_errors/<id>.log",
        "strategy": "manual_review",
        "note": "驱动未输出结构化诊断；先读取原始输出，再归入可复用失败类别。",
    },
}


def patches_for_records(records: Iterable[Dict[str, object]]) -> List[Dict[str, object]]:
    """Return a de-duplicated, serializable patch plan for failure records."""
    result: List[Dict[str, object]] = []
    seen = set()
    for record in records:
        category = str(record.get("category") or "")
        if category in seen:
            continue
        spec = PATCH_CATALOG.get(category)
        if not spec:
            continue
        seen.add(category)
        result.append({"category": category, **spec})
    return result
