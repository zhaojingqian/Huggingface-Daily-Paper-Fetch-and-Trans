import tempfile
import unittest
from pathlib import Path
from unittest import mock

from paperhub.pdf_text_quality import (
    PdfTextQualityError,
    analyze_pdf,
    analyze_pdf_cached,
    analyze_pdf_text,
    extract_pdf_text,
    is_untranslated_pdf_prose,
    pdftotext_command,
)


class PdfTextQualityTest(unittest.TestCase):
    @staticmethod
    def _english_page(label="ordinary paper prose"):
        sentence = (
            "This {} explains the experimental method, evaluation protocol, "
            "observed results, limitations, and scientific implications in "
            "complete natural-language sentences for readers. "
        ).format(label)
        return (sentence * 12).strip()

    @staticmethod
    def _chinese_page():
        return (
            "这是已经完整翻译的中文论文正文，详细说明实验方法、评估流程、"
            "主要结果、局限性以及科学意义。"
        ) * 24

    def test_sustained_english_body_is_flagged_but_references_are_ignored(self):
        pages = [self._chinese_page(), self._chinese_page()]
        pages.extend(self._english_page() for _ in range(6))
        pages.append(
            "已翻译结论                    参考文献                    "
            + self._english_page("bibliographic citation")
        )
        pages.extend(self._english_page("bibliographic citation") for _ in range(3))

        report = analyze_pdf_text("\f".join(pages))

        self.assertTrue(is_untranslated_pdf_prose(report))
        self.assertTrue(report["untranslated_prose"])
        self.assertEqual(report["english_dominant_pages"], 6)
        self.assertEqual(report["longest_english_page_run"], 6)
        self.assertEqual(report["reference_pages"], 4)
        self.assertEqual(report["samples"][0]["page"], 3)

    def test_reference_only_english_and_normal_terms_do_not_trigger(self):
        translated = (
            self._chinese_page()
            + "\nTransformer, CUDA, PyTorch, GPT-5, benchmark, API."
        )
        references = (
            "参考文献                    "
            + self._english_page("Smith et al. 2025 arXiv citation")
        )
        report = analyze_pdf_text(
            "\f".join([translated] * 5 + [references] * 4)
        )

        self.assertFalse(report["untranslated_prose"])
        self.assertEqual(report["english_dominant_pages"], 0)
        self.assertEqual(report["reference_pages"], 4)

    def test_split_and_repeated_chinese_reference_heading_is_recognized(self):
        extracted_heading = "参\n参\n参考\n考\n考文\n文\n文献\n献\n献"
        report = analyze_pdf_text("\f".join([
            self._chinese_page(),
            extracted_heading + "\n" + self._english_page("citation"),
            self._english_page("citation"),
        ]))

        self.assertFalse(report["untranslated_prose"])
        self.assertEqual(report["reference_pages"], 2)
        self.assertEqual(report["english_dominant_pages"], 0)

    def test_split_reference_words_inside_caption_do_not_start_references(self):
        caption = (
            "Figure 1 对411篇2014年后参\n"
            "参\n参考\n考\n考文\n文\n文献\n献\n"
            "献的发表趋势分析。\n"
        )
        report = analyze_pdf_text("\f".join(
            [caption + self._chinese_page()]
            + [self._english_page("ordinary survey body")] * 4
        ))

        self.assertEqual(report["reference_pages"], 0)
        self.assertEqual(report["english_dominant_pages"], 4)
        self.assertTrue(report["untranslated_prose"])

    def test_prose_before_same_page_references_is_still_analyzed(self):
        english_lines = "\n".join([
            (
                "By openly releasing the complete dataset and model weights, "
                "we aim to support transparent future research."
            ),
            (
                "This release removes data barriers and enables a broader "
                "community to reproduce the reported scientific results."
            ),
            (
                "The resulting ecosystem should improve collaborative work "
                "on reliable search agents across institutions."
            ),
        ])
        split_heading = "参\n参\n参考\n考\n考文\n文\n文献\n献\n献"
        page = (
            "6 Conclusions\n"
            + self._chinese_page()
            + "\n"
            + english_lines
            + "\n"
            + split_heading
            + "\n"
            + self._english_page("citation")
        )
        report = analyze_pdf_text(page)

        self.assertEqual(report["reference_pages"], 1)
        self.assertEqual(report["analyzable_pages"], 1)
        self.assertEqual(report["partial_untranslated_prose_pages"], 1)
        self.assertEqual(
            report["partial_untranslated_prose_page_numbers"],
            [1],
        )

    def test_single_english_proof_page_is_partial_untranslated_prose(self):
        report = analyze_pdf_text(
            "Proof.\n" + self._english_page("mathematical proof")
        )

        self.assertFalse(report["untranslated_prose"])
        self.assertEqual(report["english_dominant_pages"], 1)
        self.assertEqual(report["partial_untranslated_prose_pages"], 1)
        self.assertEqual(
            report["partial_untranslated_prose_samples"][0]["reason"],
            "scholarly_proof",
        )

    def test_citation_dense_reference_pages_need_no_heading(self):
        citation_line = (
            "[12] Smith et al. A referenced paper. Proceedings of the "
            "Conference on Testing, 2025. https://example.test/paper"
        )
        references = "\n".join([citation_line] * 12)
        report = analyze_pdf_text(
            "\f".join([self._chinese_page()] * 3 + [references] * 3)
        )

        self.assertFalse(report["untranslated_prose"])
        self.assertEqual(report["reference_pages"], 3)
        self.assertEqual(report["english_dominant_pages"], 0)

    def test_citation_dense_page_does_not_hide_later_body_without_heading(self):
        citation_line = (
            "[12] Smith et al. A referenced paper. Proceedings of the "
            "Conference on Testing, 2025. https://example.test/paper"
        )
        references = "\n".join([citation_line] * 12)
        report = analyze_pdf_text(
            "\f".join([
                self._chinese_page(),
                references,
                self._english_page(),
                self._chinese_page(),
            ])
        )

        self.assertEqual(report["reference_pages"], 1)
        self.assertEqual(report["english_dominant_pages"], 1)

    def test_chinese_related_work_with_many_citations_is_not_a_reference_page(self):
        cited_line = (
            "相关工作讨论了已有方法及其局限，并比较不同实验设置。"
            "Smith et al. 2025 arXiv https://example.test/paper"
        )
        page = "\n".join([cited_line] * 10)
        report = analyze_pdf_text(page)

        self.assertEqual(report["reference_pages"], 0)
        self.assertEqual(report["analyzable_pages"], 1)

    def test_english_introduction_with_parenthetical_citations_is_analyzed(self):
        cited_prose = (
            "1. Introduction\n"
            + self._english_page(
                "introduction citing Smith et al. 2025 and Jones et al. 2024"
            )
        )
        report = analyze_pdf_text(cited_prose)

        self.assertEqual(report["reference_pages"], 0)
        self.assertEqual(report["english_dominant_pages"], 1)

    def test_explicit_prompt_source_pages_are_reported_but_not_misclassified(self):
        prompt_page = (
            "System Prompt: You are a benchmark judge.\n"
            "Question: " + self._english_page("benchmark question") + "\n"
            "Response: Return the exact source-data answer.\n"
            "Correct Answer: Published benchmark response.\n"
        )
        report = analyze_pdf_text("\f".join([prompt_page] * 6))

        self.assertFalse(report["untranslated_prose"])
        self.assertEqual(report["source_data_pages"], 6)
        self.assertEqual(report["english_dominant_pages"], 0)

    def test_appendix_after_references_is_scanned_again(self):
        pages = [
            self._chinese_page(),
            "参考文献          " + self._english_page("citation"),
            self._english_page("citation"),
        ]
        pages.extend(
            ["Appendix A\n" + self._english_page()]
            + [self._english_page() for _ in range(3)]
        )

        report = analyze_pdf_text("\f".join(pages))

        self.assertTrue(report["untranslated_prose"])
        self.assertEqual(report["reference_pages"], 2)
        self.assertEqual(report["longest_english_page_run"], 4)

    def test_bare_appendix_heading_after_references_restores_scanning(self):
        pages = [
            self._chinese_page(),
            "参考文献          " + self._english_page("citation"),
            self._english_page("citation"),
            (
                "22 Author et al.\n"
                "A Benchmark Construction\n"
                "A.1 Data Collection\n"
                + self._english_page()
            ),
        ]
        pages.extend(self._english_page() for _ in range(3))

        report = analyze_pdf_text("\f".join(pages))

        self.assertEqual(report["reference_pages"], 2)
        self.assertTrue(report["untranslated_prose"])
        self.assertEqual(report["longest_english_page_run"], 4)

    def test_bibliography_title_does_not_end_reference_section(self):
        pages = [
            self._chinese_page(),
            "参考文献\n" + self._english_page("citation"),
            (
                "A Benchmark for Testing Systems\n"
                "[12] Smith et al. Proceedings of Testing, 2025.\n"
                + self._english_page("citation")
            ),
        ]
        pages.extend(self._english_page("citation") for _ in range(3))

        report = analyze_pdf_text("\f".join(pages))

        self.assertFalse(report["untranslated_prose"])
        self.assertEqual(report["reference_pages"], 5)
        self.assertEqual(report["english_dominant_pages"], 0)

    def test_ordinary_appendix_prompt_discussion_is_not_source_data(self):
        page = (
            "Appendix A Additional Analysis\n"
            + self._english_page(
                "prompt design, prompt selection, and prompt wording"
            )
        )
        report = analyze_pdf_text("\f".join([page] * 4))

        self.assertEqual(report["source_data_pages"], 0)
        self.assertEqual(report["english_dominant_pages"], 4)
        self.assertTrue(report["untranslated_prose"])

    def test_repeated_prompt_terminology_is_not_an_explicit_source_block(self):
        page = self._english_page(
            "results comparing agent prompt, judge prompt, and translation prompt"
        )
        report = analyze_pdf_text("\f".join([page] * 4))

        self.assertEqual(report["source_data_pages"], 0)
        self.assertEqual(report["english_dominant_pages"], 4)
        self.assertTrue(report["untranslated_prose"])

    def test_explicit_source_appendix_section_resets_at_sibling_section(self):
        source_page = (
            "Appendix C\n"
            "C. Task Examples\n"
            + self._english_page("benchmark issue example")
        )
        continuation = self._english_page("benchmark issue example")
        body_page = (
            "D Additional Analysis\n"
            + self._english_page("ordinary scientific appendix prose")
        )
        report = analyze_pdf_text("\f".join(
            [source_page, continuation] + [body_page] * 4
        ))

        self.assertEqual(report["source_data_pages"], 2)
        self.assertEqual(report["english_dominant_pages"], 4)
        self.assertTrue(report["untranslated_prose"])

    def test_task_template_pages_are_source_data(self):
        template = (
            "Task: Analyze the execution trajectory.\n"
            "[Target Context]\n"
            "Output Format: Return JSON.\n"
            + self._english_page("template instruction")
        )
        report = analyze_pdf_text("\f".join([template] * 4))

        self.assertEqual(report["source_data_pages"], 4)
        self.assertEqual(report["english_dominant_pages"], 0)
        self.assertFalse(report["untranslated_prose"])

    def test_instruction_and_in_domain_example_sections_are_source_data(self):
        pages = [
            (
                "Appendix F\n"
                "F Instructions for Benchmark Tasks\n"
                + self._english_page("source instruction")
            ),
            "F.3 In-Domain Examples\n" + self._english_page("dataset example"),
            self._english_page("dataset example"),
        ]
        report = analyze_pdf_text("\f".join(pages))

        self.assertEqual(report["source_data_pages"], 3)
        self.assertEqual(report["english_dominant_pages"], 0)
        self.assertFalse(report["untranslated_prose"])

    def test_markdown_rubric_and_trajectory_examples_are_source_data(self):
        rubric = (
            "### Goal\n"
            "### Given Context\n"
            "### Level Definitions\n"
            "### How to Judge\n"
            + self._english_page("annotation rubric")
        )
        trajectory = (
            "Example 1: Tool Risk\n"
            "Attack Prompt\n"
            + self._english_page("attack prompt example")
            + "\nTrajectory: tool_call to END"
        )
        report = analyze_pdf_text("\f".join([rubric, trajectory]))

        self.assertEqual(report["source_data_pages"], 2)
        self.assertEqual(report["english_dominant_pages"], 0)

    def test_translation_refusal_is_reported_even_inside_source_section(self):
        page = (
            "Appendix F\n"
            "F Instructions for Benchmark Tasks\n"
            "抱歉，我无法查看或访问外部文件内容。"
            "请将您需要翻译的具体文本粘贴到对话框中。\n"
            + self._english_page("source instruction")
        )
        report = analyze_pdf_text(page)

        self.assertEqual(report["source_data_pages"], 1)
        self.assertEqual(report["translation_refusal_pages"], 1)
        self.assertEqual(report["translation_refusal_page_numbers"], [1])
        self.assertIn("无法查看", report["translation_refusal_samples"][0]["text"])

    def test_source_and_structural_pages_do_not_skew_prose_coverage(self):
        prompt_page = (
            "Appendix A Full Prompt\n"
            "Prompt: You are a professional evaluator.\n"
            "Question: " + self._english_page("benchmark question") + "\n"
            "Answer: Return the source answer.\n"
        )
        contents = "Contents\n" + ("1. Method . . . . . . . . 4\n" * 20)
        report = analyze_pdf_text(
            "\f".join([self._chinese_page(), prompt_page, contents])
        )

        self.assertEqual(report["analyzable_pages"], 1)
        self.assertEqual(report["source_data_pages"], 1)
        self.assertEqual(report["structural_pages"], 1)
        self.assertEqual(report["cjk_pct"], 100.0)

    def test_tables_templates_and_json_examples_break_english_runs(self):
        table = (
            "Table 11: Benchmark statistics\n"
            + " ".join(str(index) for index in range(60))
            + "\n"
            + self._english_page("table description")
        )
        template = (
            "Generator Prompts\nGuidelines: preserve semantics.\n"
            "Instruction: rewrite the input.\nExamples: source output pairs.\n"
            + self._english_page("template source")
        )
        json_example = (
            '{"chain_id":"1","domain":"biology","process":"activation",'
            '"summary":"source data","steps":["one"],"answer":"two"}\n'
            + self._english_page("structured example")
        )
        report = analyze_pdf_text("\f".join([
            self._chinese_page(),
            table,
            template,
            json_example,
            self._chinese_page(),
        ]))

        self.assertFalse(report["untranslated_prose"])
        self.assertEqual(report["structural_pages"], 1)
        self.assertEqual(report["source_data_pages"], 2)
        self.assertEqual(report["english_dominant_pages"], 0)

    def test_extraction_command_is_page_bounded_and_missing_tool_is_clear(self):
        command = pdftotext_command(
            "/tmp/paper.pdf",
            "/tmp/paper.txt",
            max_pages=37,
            executable="/usr/bin/pdftotext",
        )
        self.assertEqual(command[1:5], ["-f", "1", "-l", "37"])
        with mock.patch(
            "paperhub.pdf_text_quality.shutil.which",
            return_value=None,
        ):
            with self.assertRaises(PdfTextQualityError) as raised:
                extract_pdf_text("/tmp/missing.pdf")
        self.assertIn("poppler-utils", str(raised.exception))

    def test_page_limit_uses_a_sentinel_page(self):
        with mock.patch(
            "paperhub.pdf_text_quality.extract_pdf_text",
            side_effect=[
                "\f".join([self._chinese_page()] * 2),
                "\f".join([self._chinese_page()] * 3),
            ],
        ) as extract:
            exact = analyze_pdf("/tmp/exact.pdf", max_pages=2)
            truncated = analyze_pdf("/tmp/long.pdf", max_pages=2)

        self.assertFalse(exact["page_limit_reached"])
        self.assertTrue(truncated["page_limit_reached"])
        self.assertEqual(exact["pages_scanned"], 2)
        self.assertEqual(truncated["pages_scanned"], 2)
        self.assertEqual(
            [call[1]["max_pages"] for call in extract.call_args_list],
            [3, 3],
        )

    def test_metric_cache_is_invalidated_by_pdf_signature(self):
        report = {
            "pages_scanned": 3,
            "untranslated_prose": False,
            "page_limit_reached": False,
        }
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "2607.00001_zh.pdf"
            cache = Path(tmp) / "cache"
            pdf.write_bytes(b"%PDF-1.7\nfirst\n%%EOF\n")
            with mock.patch(
                "paperhub.pdf_text_quality.analyze_pdf",
                return_value=report,
            ) as analyze:
                first = analyze_pdf_cached(pdf, cache)
                second = analyze_pdf_cached(pdf, cache)
                pdf.write_bytes(b"%PDF-1.7\nchanged\n%%EOF\n")
                third = analyze_pdf_cached(pdf, cache)

        self.assertFalse(first["_cache_hit"])
        self.assertTrue(second["_cache_hit"])
        self.assertFalse(third["_cache_hit"])
        self.assertEqual(analyze.call_count, 2)


if __name__ == "__main__":
    unittest.main()
