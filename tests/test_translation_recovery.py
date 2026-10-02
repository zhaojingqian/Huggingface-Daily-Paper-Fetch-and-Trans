import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

from paperhub import translation_runtime


class TranslationRecoveryTest(unittest.TestCase):
    def test_missing_chunks_use_normal_concurrency_not_retry_cap(self):
        calls = []
        def request(**options):
            calls.append(options)
            if False:
                yield
            return [value for source in options["inputs_array"]
                    for value in (source, "这是一段完整的中文翻译正文。")]

        utils = types.SimpleNamespace(
            request_gpt_model_multi_threads_with_very_awesome_ui_and_high_efficiency=request)
        sources = ["This paragraph describes the experimental results in detail."] * 21
        with mock.patch.dict(sys.modules, {"crazy_functions": types.SimpleNamespace(crazy_utils=utils)}), \
             mock.patch.dict(os.environ, {"PAPER_TRANS_LLM_WORKERS": "50"}), \
             mock.patch.object(translation_runtime, "_load_translation_recovery", return_value={0: "这是一段完整的中文翻译正文。"}), \
             mock.patch.object(translation_runtime, "_save_translation_recovery", return_value=False):
            translation_runtime._patch_latex_llm_rate_limit_handling()
            generator = utils.request_gpt_model_multi_threads_with_very_awesome_ui_and_high_efficiency(
                inputs_array=sources, inputs_show_user_array=["paragraph"] * 21,
                history_array=[[]] * 21, sys_prompt_array=["translate"] * 21)
            with self.assertRaises(StopIteration) as done:
                next(generator)
        self.assertEqual(len(done.exception.value), 42)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["max_workers"], 20)

    def test_merge_restores_source_whitespace_without_inventing_boundaries(self):
        toolbox = types.SimpleNamespace(fix_content=lambda translated, source: translated.strip())
        actions = types.SimpleNamespace()
        package = types.SimpleNamespace(latex_toolbox=toolbox, latex_actions=actions)
        with mock.patch.dict(sys.modules, {"crazy_functions.latex_fns": package}):
            translation_runtime._patch_latex_fix_content_artifacts()
            for source, translated, expected in (
                ("config\\par\n", "配置\\par", "配置\\par\n"),
                ("\nstatus", "状态", "\n状态"),
                ("{caption}", "{标题}", "{标题}"),
            ):
                self.assertEqual(toolbox.fix_content(translated, source), expected)
                self.assertEqual(toolbox.fix_content(expected, source), expected)
            self.assertIs(actions.fix_content, toolbox.fix_content)

    def test_structural_sources_never_reach_translation_api(self):
        request = mock.Mock(side_effect=AssertionError("structural data must not call API"))
        utils = types.SimpleNamespace(
            request_gpt_model_multi_threads_with_very_awesome_ui_and_high_efficiency=request,
        )
        package = types.SimpleNamespace(crazy_utils=utils)
        source = r"\newgeometry{top=0.9cm,bottom=1.9cm,left=2cm,right=2cm}"
        with mock.patch.dict(sys.modules, {"crazy_functions": package}), \
             mock.patch.object(translation_runtime, "_load_translation_recovery", return_value={}), \
             mock.patch.object(translation_runtime, "_save_translation_recovery", return_value=False) as save:
            translation_runtime._patch_latex_llm_rate_limit_handling()
            generator = utils.request_gpt_model_multi_threads_with_very_awesome_ui_and_high_efficiency(
                inputs_array=[source], inputs_show_user_array=["layout"],
                history_array=[[]], sys_prompt_array=["translate"],
            )
            with self.assertRaises(StopIteration) as done:
                next(generator)
        self.assertEqual(done.exception.value, [source, source])
        request.assert_not_called()
        save.assert_called_once_with([source], [source, source])

    def setUp(self):
        self.env = mock.patch.dict(
            os.environ,
            {
                "PAPER_TRANS_EFFECTIVE_MODEL": "deepseek-v4-flash-0731",
            },
            clear=False,
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_recovery_keeps_only_valid_responses(self):
        sources = [
            "This is a translated paragraph with enough words to validate.",
            "Another English paragraph that still needs translation.",
            "A third English paragraph that still needs translation.",
        ]
        payload = [
            "prompt-0",
            "这是一段已经翻译完成的中文正文。",
            "prompt-1",
            "",
            "prompt-2",
            "insufficient_user_quota: API balance is insufficient",
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "paper.json")
            with mock.patch.dict(
                os.environ,
                {"PAPER_TRANS_RECOVERY_FILE": path},
                clear=False,
            ):
                self.assertTrue(
                    translation_runtime._save_translation_recovery(
                        sources,
                        payload,
                    )
                )
                self.assertEqual(
                    translation_runtime._load_translation_recovery(sources),
                    {0: "这是一段已经翻译完成的中文正文。"},
                )

    def test_recovery_requires_same_model_and_source_not_same_splitter_version(self):
        sources = ["A translated paragraph with enough words to validate."]
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "paper.json")
            with mock.patch.dict(
                os.environ,
                {"PAPER_TRANS_RECOVERY_FILE": path},
                clear=False,
            ):
                translation_runtime._save_translation_recovery(
                    sources,
                    ["prompt", "这是一段中文翻译结果。"],
                )
                with mock.patch.dict(
                    os.environ,
                    {"PAPER_TRANS_EFFECTIVE_MODEL": "other-model"},
                    clear=False,
                ):
                    self.assertEqual(
                        translation_runtime._load_translation_recovery(sources),
                        {},
                    )
                with open(path, encoding="utf-8") as handle:
                    payload = json.load(handle)
                payload["splitter"] = "stale"
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle)
                self.assertEqual(
                    translation_runtime._load_translation_recovery(sources),
                    {0: "这是一段中文翻译结果。"},
                )
                self.assertEqual(translation_runtime._load_translation_recovery(["Different source paragraph."]), {})

    def test_recovery_matches_transport_whitespace_and_reordered_chunks(self):
        sources = [
            "First source paragraph with enough words to validate.",
            "Second source paragraph with enough words to validate.",
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "paper.json")
            with mock.patch.dict(
                os.environ,
                {"PAPER_TRANS_RECOVERY_FILE": path},
                clear=False,
            ):
                self.assertTrue(
                    translation_runtime._save_translation_recovery(
                        sources,
                        [
                            "prompt-0",
                            "第一段已经完成翻译。",
                            "prompt-1",
                            "第二段已经完成翻译。",
                        ],
                    )
                )
                with open(path, encoding="utf-8") as handle:
                    payload = json.load(handle)
                payload["items"] = list(reversed(payload["items"]))
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False)
                self.assertEqual(
                    translation_runtime._load_translation_recovery(
                        ["\n" + sources[1] + "\n", sources[0]],
                    ),
                    {0: "第二段已经完成翻译。", 1: "第一段已经完成翻译。"},
                )


if __name__ == "__main__":
    unittest.main()
