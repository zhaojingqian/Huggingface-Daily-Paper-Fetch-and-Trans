import os
import tempfile
import unittest

from failure_taxonomy import classify_failure
from paperhub.failure_reports import load_failure_records, summarize_failures
from paperhub.json_io import write_json_atomic


class FailureTaxonomyTest(unittest.TestCase):
    def test_compile_categories_are_stable_and_actionable(self):
        missing = classify_failure(
            "compile", "xdvipdfmx:fatal: Image inclusion failed. Could not find file: assets/logo.png"
        )
        undefined = classify_failure("compile", "! Undefined control sequence. l.42 \\method中文")

        self.assertEqual(missing["category"], "compile.asset_missing")
        self.assertEqual(missing["retry_strategy"], "reuse_translation")
        self.assertEqual(undefined["repair_action"], "patch_undefined_command")

    def test_pdftex_primitive_has_a_specific_engine_category(self):
        result = classify_failure(
            "compile", "Undefined control sequence. l.7 \\pdfinfoomitdate 1"
        )

        self.assertEqual(result["category"], "compile.pdftex_primitive")
        self.assertEqual(result["repair_action"], "guard_pdftex_primitive")

    def test_legacy_cjk_environment_has_a_specific_category(self):
        result = classify_failure("compile", "LaTeX Error: Environment CJK* undefined")

        self.assertEqual(result["category"], "compile.legacy_cjk_environment")
        self.assertEqual(result["retry_strategy"], "reuse_translation")

    def test_recent_repair_categories_are_specific(self):
        leak = classify_failure(
            "compile",
            'Undefined control sequence. "translation": "\\\\section{引言}\\\\n正文"',
        )
        font = classify_failure(
            "compile",
            'Package fontspec Error: The font "SourceSans3-RegularIt" cannot be found.',
        )
        driver = classify_failure(
            "compile",
            r"Undefined control sequence. \ifnum \pdfshellescape >0",
        )
        environment = classify_failure(
            "compile",
            r"Undefined control sequence. \endtcolorbox ->\tcb@insert@after@part",
        )

        self.assertEqual(leak["category"], "compile.model_response_leak")
        self.assertEqual(font["category"], "compile.font_family_missing")
        self.assertEqual(driver["category"], "compile.engine_driver_mismatch")
        self.assertEqual(environment["category"], "compile.unmatched_environment")

    def test_tikz_and_duplicated_macro_failures_are_specific(self):
        tikz = classify_failure(
            "compile",
            r"Undefined control sequence. \pgf@matrix@last@nextcell@options",
        )
        duplicated = classify_failure(
            "compile",
            r"Undefined control sequence. l.72 \nndiff{\theta}",
        )

        self.assertEqual(tikz["category"], "compile.tikz_matrix_legend")
        self.assertEqual(duplicated["category"], "compile.duplicated_macro_initial")

    def test_translation_categories_distinguish_auth_and_timeout(self):
        auth = classify_failure("translate", plugin_error="401 Unauthorized: invalid API key")
        timeout = classify_failure("translate", plugin_error="Connection timed out")

        self.assertEqual(auth["category"], "translate.api_auth")
        self.assertFalse(auth["retryable"])
        self.assertEqual(timeout["retry_strategy"], "retry_translation")

    def test_translation_quota_failure_is_not_blindly_retried(self):
        quota = classify_failure(
            "translate",
            plugin_error=(
                "insufficient_user_quota: need pre-deduct $0.010, "
                "balance $0.005 is insufficient"
            ),
        )

        self.assertEqual(quota["category"], "translate.api_quota")
        self.assertEqual(quota["retry_strategy"], "manual_review")
        self.assertEqual(quota["repair_action"], "recharge_api_balance")

    def test_timeout_configuration_does_not_mask_quality_failure(self):
        result = classify_failure(
            "compile",
            "[driver] timeout=600\n"
            "[driver] 翻译覆盖率检查失败: cjk_pct=2.3% long_english_lines=101",
        )

        self.assertEqual(result["category"], "quality.untranslated_prose")

    def test_missing_relative_compile_workdir_is_runtime_not_auth(self):
        result = classify_failure(
            "translate",
            plugin_error=(
                "Traceback: FileNotFoundError: [Errno 2] No such file or directory: "
                "'gpt_log/arxiv_cache/2607.04033/workfolder'"
            ),
        )

        self.assertEqual(result["category"], "runtime.workdir_missing")
        self.assertEqual(result["retry_strategy"], "reuse_translation")

    def test_failure_report_reads_sidecars_and_legacy_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_json_atomic(
                os.path.join(tmp, "2607.00001.json"),
                {"arxiv_id": "2607.00001", "category": "compile.asset_missing", "retry_strategy": "reuse_translation"},
            )
            with open(os.path.join(tmp, "2607.00002.log"), "w", encoding="utf-8") as handle:
                handle.write("【失败阶段】 compile — LaTeX 编译阶段失败\nUndefined control sequence")

            records = load_failure_records(tmp)
            summary = summarize_failures(records)

            self.assertEqual(summary["total"], 2)
            self.assertEqual(summary["by_category"]["compile.undefined_command"], 1)

    def test_unknown_sidecar_is_refined_from_driver_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_json_atomic(
                os.path.join(tmp, "2607.00003.json"),
                {
                    "arxiv_id": "2607.00003",
                    "category": "compile.unknown",
                    "retry_strategy": "manual_review",
                },
            )
            with open(os.path.join(tmp, "2607.00003.log"), "w", encoding="utf-8") as handle:
                handle.write("[driver] 翻译覆盖率检查失败: cjk_pct=2.3% long_english_lines=42")

            record = load_failure_records(tmp)[0]

            self.assertEqual(record["category"], "quality.untranslated_prose")
            self.assertEqual(record["retry_strategy"], "retry_translation")
            self.assertEqual(record["reclassified_from"], "compile.unknown")


if __name__ == "__main__":
    unittest.main()
