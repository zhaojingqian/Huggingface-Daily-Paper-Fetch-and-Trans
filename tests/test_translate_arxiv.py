import unittest
import tempfile
from unittest.mock import Mock, patch

import translate_arxiv


class TranslateArxivTest(unittest.TestCase):
    def test_repairs_odd_latex_backslash_runs_for_json(self):
        self.assertEqual(
            translate_arxiv._repair_json_backslashes(r'{"x":"\\\method"}'),
            r'{"x":"\\\\method"}',
        )
        self.assertEqual(
            translate_arxiv._repair_json_backslashes(r'{"x":"line\nnext"}'),
            r'{"x":"line\nnext"}',
        )

    def test_extracts_compatibility_reasoning_content(self):
        result = translate_arxiv._extract_chat_completion_text({
            "choices": [{
                "message": {
                    "content": "",
                    "reasoning_content": '{"title_zh":"测试"}',
                },
            }],
        })

        self.assertEqual(result, '{"title_zh":"测试"}')

    def test_extracts_text_from_content_parts(self):
        result = translate_arxiv._extract_chat_completion_text({
            "choices": [{
                "message": {
                    "content": [
                        {"type": "text", "text": {"value": "第一段"}},
                        {"type": "text", "text": "第二段"},
                    ],
                },
            }],
        })

        self.assertEqual(result, "第一段\n第二段")

    def test_call_llm_forwards_json_response_format(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "choices": [{"message": {"content": '{"title_zh":"测试"}'}}]
        }
        config = {
            "api_base": "https://example.test/v1/chat/completions",
            "api_key": "test-key",
            "model": "test-model",
        }

        with patch("translate_arxiv.requests.post", return_value=response) as post:
            result = translate_arxiv.call_llm(
                [{"role": "user", "content": "translate"}],
                config,
                response_format={"type": "json_object"},
            )

        self.assertEqual(result, '{"title_zh":"测试"}')
        self.assertEqual(
            post.call_args.kwargs["json"]["response_format"],
            {"type": "json_object"},
        )

    def test_translate_paper_requests_json_mode(self):
        translated = (
            '{"title_zh":"测试标题","abstract_zh":"测试摘要",'
            '"keywords_zh":["测试"],"summary_zh":"测试总结"}'
        )
        with patch("translate_arxiv.call_llm", return_value=translated) as call:
            result = translate_arxiv.translate_paper(
                {"title": "Test", "abstract": "An abstract."},
                {"model": "test"},
                max_retries=1,
            )

        self.assertEqual(result["title_zh"], "测试标题")
        self.assertEqual(
            call.call_args.kwargs["response_format"],
            {"type": "json_object"},
        )

    def test_translate_paper_preserves_title_from_truncated_json(self):
        truncated = (
            '{"title_zh":"测试标题","abstract_zh":"不断重复的内容'
        )
        with patch("translate_arxiv.call_llm", return_value=truncated):
            result = translate_arxiv.translate_paper(
                {"title": "Test", "abstract": "Abstract."},
                {"model": "test"},
                max_retries=1,
            )

        self.assertEqual(result["title_zh"], "测试标题")

    def test_translate_paper_repairs_latex_u_escape_in_json(self):
        translated = (
            '{"title_zh":"测试标题","abstract_zh":"公式 \\\\underbrace{x}",'
            '"keywords_zh":["测试"],"summary_zh":"测试总结"}'
        )
        # Simulate a gateway returning a single backslash before a LaTeX
        # command, which is invalid JSON but recoverable.
        translated = translated.replace("\\\\underbrace", "\\underbrace")
        with patch("translate_arxiv.call_llm", return_value=translated):
            result = translate_arxiv.translate_paper(
                {"title": "Test", "abstract": "Abstract."},
                {"model": "test"},
                max_retries=1,
            )

        self.assertEqual(result["title_zh"], "测试标题")
        self.assertIn(r"\underbrace", result["abstract_zh"])

    def test_completes_truncated_translation_from_cached_tex(self):
        tex = (
            r"\begin{abstract}"
            r"*{\scriptsize\textbf{警告：占位。禁止移除或修改此警告。}}\\"
            "这是第一句。这里是第二句。第三句。"
            r"\keywords{智能体 \and 强化学习}"
            r"\end{abstract}"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            tex_path = temp_dir + "/2607.00001_merge_translate_zh.tex"
            with open(tex_path, "w", encoding="utf-8") as handle:
                handle.write(tex)
            with patch("translate_arxiv.TEX_BACKUP_DIR", temp_dir):
                result = translate_arxiv._complete_translation_from_tex(
                    "2607.00001",
                    {"title_zh": "测试标题"},
                )

        self.assertIn("这是第一句", result["abstract_zh"])
        self.assertEqual(result["summary_zh"], "这是第一句。这里是第二句。")
        self.assertEqual(result["keywords_zh"], ["智能体", "强化学习"])


if __name__ == "__main__":
    unittest.main()
