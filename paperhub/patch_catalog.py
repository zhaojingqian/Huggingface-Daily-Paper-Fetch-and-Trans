#!/usr/bin/env python3
"""Catalog of reusable repair patches and the failure classes they address."""

from typing import Dict, Iterable, List


PATCH_CATALOG: Dict[str, Dict[str, object]] = {
    "infrastructure.disk_full": {
        "patches": (
            "preflight_translation_disk_watermark",
            "cleanup_completed_retry_runtime_cache",
            "daily_watermark_cache_cleanup",
        ),
        "source": (
            "translate_full.py / scripts/cleanup_docker_cache.sh / "
            "scripts/send_maintenance_alert.py"
        ),
        "strategy": "retry_later",
        "note": (
            "Errno 28 归为基础设施故障；翻译前检查磁盘，批量 retry 每篇结束后"
            "清理可再生容器缓存，并由每日水位清理与 Gmail 告警兜底。"
        ),
    },
    "translate.api_quota": {
        "patches": (
            "recharge_or_switch_authorized_model",
            "propagate_quota_abort_to_outer_coordinator",
            "persist_completed_translation_chunks",
            "retry_only_missing_translation_chunks",
        ),
        "source": (
            "config_private.py / PAPER_TRANS_LLM_MODEL / run_papers.py / "
            "run_repair.py / topic_engine.py / paperhub/translation_runtime.py / "
            "full_translate_driver.py / scripts/send_maintenance_alert.py"
        ),
        "strategy": "manual_review",
        "note": (
            "余额不足向最外层传播 abort_reason，停止剩余 index/topic/mode；"
            "已完成 chunk 按模型和 splitter 版本留账，下次只请求缺口；"
            "充值或显式切换到同一凭据可用且质量验证通过的模型。"
        ),
    },
    "translate.api_auth": {
        "patches": ("fix_api_credentials",),
        "source": "config_private.py / translate_full.py",
        "strategy": "manual_review",
        "note": "凭据类失败不自动重试；先修复 API key 或代理配置，再重跑翻译。",
    },
    "translate.api_rate_limit": {
        "patches": (
            "throttle_latex_chunk_requests",
            "retry_failed_translation_chunks",
            "reject_partial_translation_cache",
        ),
        "source": "full_translate_driver.py / translate_full.py",
        "strategy": "retry_later",
        "note": (
            "全文翻译首轮并发由 PAPER_TRANS_LLM_WORKERS 控制（当前默认 50）；"
            "重试按失败槽限流，仍失败时拒绝缓存英文回填的半成品。"
        ),
    },
    "translate.network_timeout": {
        "patches": ("retry_with_backoff",),
        "source": "translate_full.py / full_translate_driver.py",
        "strategy": "retry_translation",
        "note": "优先复用源码和翻译缓存，网络恢复后退避重试。",
    },
    "translate.plugin_runtime": {
        "patches": (
            "inspect_plugin_runtime",
            "protect_tex_quoted_literals",
            "protect_tikz_drawing_fragments",
            "protect_tikz_style_definitions",
            "protect_single_line_prompt_source_data",
        ),
        "source": "full_translate_driver.py / logs/pdf_errors",
        "strategy": "retry_translation",
        "note": "检查容器运行时和残留进程；源码中的 TikZ/style、提示模板及精确输出协议先保护，再决定是否清缓存重译。",
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
    "translate.summary_response_format": {
        "patches": (
            "normalize_chat_completion_content",
            "repair_odd_json_backslashes",
            "recover_truncated_summary_from_tex",
        ),
        "source": "translate_arxiv.py",
        "strategy": "retry_translation",
        "note": "兼容网关字段、截断 JSON 和 LaTeX 反斜杠，并从成功中文 TeX 回填摘要。",
    },
    "compile.model_response_leak": {
        "patches": ("strip_serialized_translation_artifact",),
        "source": "latex_translation_filters.py / full_translate_driver.py",
        "strategy": "reuse_translation",
        "note": "移除独立成行的序列化 translation JSON/list，保留中文 TeX 直接重编译。",
    },
    "compile.font_family_missing": {
        "patches": ("fallback_sourcesans3_family", "patch_local_textls_fallback"),
        "source": "latex_translation_filters.py / full_translate_driver.py",
        "strategy": "reuse_translation",
        "note": "将 SourceSans3 映射到镜像内 SourceSansPro，并补本地样式 textls fallback。",
    },
    "compile.engine_driver_mismatch": {
        "patches": ("remove_pdftex_graphics_driver",),
        "source": "latex_translation_filters.py / full_translate_driver.py",
        "strategy": "reuse_translation",
        "note": "移除 graphicx 的 pdftex 强制 driver，让 XeLaTeX 自动选择正确后端。",
    },
    "compile.engine_specific": {
        "patches": ("fallback_lualatex_on_xelatex_health_failure",),
        "source": "full_translate_driver.py",
        "strategy": "reuse_translation",
        "note": "XeLaTeX 生成带真实错误的 PDF 时，清理中间文件并用 LuaLaTeX 兼容重编译。",
    },
    "compile.unmatched_environment": {
        "patches": ("remove_unmatched_environment_ending",),
        "source": "latex_translation_filters.py / full_translate_driver.py",
        "strategy": "reuse_translation",
        "note": "使用环境栈删除无匹配 begin 的 tcolorbox 结束标签。",
    },
    "compile.tikz_matrix_legend": {
        "patches": ("disable_fragile_tikz_matrix_legends",),
        "source": "latex_translation_filters.py / full_translate_driver.py",
        "strategy": "reuse_translation",
        "note": "仅省略被显式 node/draw 破坏的 matrix 图例，保留主图、数据和图注。",
    },
    "compile.duplicated_macro_initial": {
        "patches": ("repair_duplicated_macro_initials",),
        "source": "latex_translation_filters.py / full_translate_driver.py",
        "strategy": "reuse_translation",
        "note": "依据原始 TeX 的已定义宏，恢复被翻译重复的宏名首字母。",
    },
    "compile.asset_missing": {
        "patches": ("patch_missing_graphics",),
        "source": "full_translate_driver.py",
        "strategy": "reuse_translation",
        "note": "将安全的相对图片引用替换为资源或可编译占位图。",
    },
    "compile.macro_recursion": {
        "patches": (
            "patch_recursive_macro",
            "relocate_nested_preamble_fallback",
        ),
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
            "patch_aaai_xelatex_affiliations",
            "patch_fontawesome_legacy_aliases",
            "inspect_input_tex_siblings_for_fallbacks",
            "patch_spurious_cjk_command_escapes",
            "patch_missing_math_aliases",
            "separate_builtin_layout_commands_from_cjk",
            "patch_bbding_symbol_fallbacks",
            "patch_missing_custom_macro_definitions",
        ),
        "source": "full_translate_driver.py / latex_translation_filters.py",
        "strategy": "reuse_translation",
        "note": "优先补兼容命令、图标 fallback、中文误转义，并修复 \\par 等内建布局命令与中文粘连。",
    },
    "compile.numeric_syntax": {
        "patches": (
            "patch_tcolorbox_opening_options",
            "patch_numeric_argument",
            "patch_enumitem_for_optional_lists",
            "patch_local_tex_microtype_loads",
            "normalize_ctex_fontset_for_slim_xelatex",
        ),
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
        "patches": (
            "normalize_tex_include_target",
            "preserve_dynamic_tex_include_targets",
            "restore_source_manifest",
        ),
        "source": "full_translate_driver.py / latex_translation_filters.py",
        "strategy": "restore_source",
        "note": "清理 input/include 路径空白并校验源码清单，再继续翻译。",
    },
    "quality.untranslated_prose": {
        "patches": (
            "split_math_adjacent_short_prose",
            "split_heading_structure_units",
            "split_heading_arguments_from_body",
            "preserve_bounded_split_boundaries",
            "enforce_normal_chunk_limit",
            "adaptively_subdivide_failed_structure_chunks",
            "repair_terminal_translation_residual_lines",
            "protect_split_citation_payloads",
            "protect_short_citation_name_catalogs",
            "allow_translated_prefix_name_catalogs",
            "detect_short_exact_english_echo",
            "normalize_citation_identity",
            "protect_translation_artifacts",
            "protect_single_line_prompt_source_data",
            "protect_safety_benchmark_instructions",
            "exclude_code_metadata_tikz_and_catalog_false_positives",
            "protect_http_endpoint_catalogs",
            "protect_detached_citation_key_lists",
            "protect_structured_identifier_paths",
            "protect_person_name_catalogs",
            "protect_contact_author_metadata",
            "protect_bracketed_heading_fragments",
            "protect_algorithmic_pseudocode_fragments",
            "protect_tool_call_result_fragments",
            "protect_unbracketed_latex_option_lists",
            "protect_latex_configuration_commands",
            "protect_pure_latex_math_fragments",
            "preserve_custom_text_macro_arguments",
            "promote_short_structural_bridge_prose",
            "validate_unescaped_brace_balance",
            "normalize_safe_trailing_unmatched_brace",
            "restore_splitter_boundary_braces",
            "restore_dropped_leading_headings",
            "restore_adaptive_combined_references",
            "protect_email_macros",
            "protect_code_command_fragments",
            "protect_pure_math_fragments",
            "restore_missing_citation_payloads",
            "throttle_latex_chunk_requests",
            "retry_failed_translation_chunks",
            "retry_untranslated_prose_with_explicit_instruction",
        ),
        "source": "full_translate_driver.py / latex_translation_filters.py",
        "strategy": "retry_translation",
        "note": "正常正文保持 1200 字符上下文；结构门禁失败时仅细分该 slot 串行重试。高覆盖 TeX 若只剩少量英文接缝，仅定向重译门禁行并按质量分数逐行提交，不再整篇重译。",
    },
    "quality.translation_chunk_invalid": {
        "patches": (
            "adaptively_subdivide_failed_structure_chunks",
            "protect_split_citation_payloads",
            "protect_structural_source_data",
            "protect_email_and_tool_catalog_fragments",
            "restore_missing_trailing_reference_commands",
            "retry_outer_braced_prose_entries",
            "repair_missing_text_command_opening_braces",
            "restore_splitter_boundary_braces",
            "protect_grader_output_directives",
            "protect_compiler_command_fragments",
            "preserve_citations_after_urls",
            "protect_email_href_catalogs",
            "protect_name_catalogs_with_conjunctions",
        ),
        "source": "full_translate_driver.py / latex_translation_filters.py",
        "strategy": "retry_translation",
        "note": "读取 abnormal reason，区分请求、漏译与结构门禁；只修改共享谓词或失败 slot 策略。",
    },
    "quality.pdf_sustained_untranslated": {
        "patches": (
            "scan_pdf_text_without_tex",
            "split_preserved_prose",
            "retry_failed_translation_chunks",
        ),
        "source": (
            "paperhub/pdf_text_quality.py / "
            "scripts/queue_quality_repairs.py / full_translate_driver.py"
        ),
        "strategy": "retry_translation",
        "note": "无 TeX 的有效 PDF 出现连续多页英文正文时，复用扫描缓存定位并重新翻译源码。",
    },
    "quality.pdf_partial_untranslated": {
        "patches": (
            "scan_pdf_text_without_tex",
            "split_preserved_prose",
            "retry_failed_translation_chunks",
        ),
        "source": (
            "paperhub/pdf_text_quality.py / "
            "scripts/queue_quality_repairs.py / full_translate_driver.py"
        ),
        "strategy": "retry_translation",
        "note": "无 TeX 的有效 PDF 出现局部英文正文或证明时，按命中页复核 chunk 后重新翻译。",
    },
    "quality.translation_refusal": {
        "patches": (
            "detect_translation_refusal",
            "reject_partial_translation_cache",
            "retry_failed_translation_chunks",
        ),
        "source": (
            "paperhub/pdf_text_quality.py / "
            "scripts/queue_quality_repairs.py / full_translate_driver.py"
        ),
        "strategy": "retry_translation",
        "note": "检测模型拒绝翻译回显，拒绝发布和半成品缓存，并重新提交失败翻译。",
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
        item = {"category": category, **spec}
        # The persisted diagnosis owns the actual retry strategy.  Keep the
        # catalog value only as a fallback for legacy records that predate the
        # structured field; otherwise a stale catalog entry must not contradict
        # the classification used by the repair runner.
        retry_strategy = str(record.get("retry_strategy") or "").strip()
        if retry_strategy:
            item["strategy"] = retry_strategy
        result.append(item)
    return result
