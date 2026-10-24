"""Model-free regressions for context contracts and uncertain transport handling.

Prepared for a separately authorized test pass; no GPU, models or HTTP services.
"""
import copy
from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from manga_pipeline.providers.vllm import VLLM
from manga_pipeline.runtime.inference_state import InferenceUncertain
from manga_pipeline.runtime.llm_budget import BudgetExceeded, fit_request, final_prompt, output_tokens, validate_runpod_profile


def config():
    return {"llm": {"backend": "vllm", "host": "http://127.0.0.1:8000",
        "model": "example/qwen", "num_ctx": 32768, "num_predict": 2048,
        "analyze_num_predict": 2048, "translation_num_predict": 2048,
        "compact_context": True, "timeout": 600, "timeout_retries": 1},
        "vllm": {"revision": "a" * 40, "version": "0.31.0", "startup_timeout": 600},
        "runtime": {"enabled": True, "context_fraction": 0.85}}


class BudgetPolicyTests(unittest.TestCase):
    def test_full_gpu_vllm_slots_are_bounded(self):
        cfg = config()
        cfg['device'] = 'cuda'
        cfg['llm']['require_full_gpu'] = True
        cfg['runtime'].update(preset='rtx5090', required_gpu='RTX 5090', story_workers=1)
        for slots in (1, 2, 4, 8, 16, 32, 64):
            cfg['runtime']['llm_slots'] = slots
            validate_runpod_profile(cfg)
        for slots in (0, 65, True, None):
            cfg['runtime']['llm_slots'] = slots
            with self.assertRaises(ValueError):
                validate_runpod_profile(cfg)
        cfg['runtime']['llm_slots'] = 2
        cfg['llm']['backend'] = 'ollama'
        with self.assertRaises(ValueError):
            validate_runpod_profile(cfg)

    def test_full_gpu_profile_accepts_only_the_selected_matching_preset(self):
        cfg = config()
        cfg['device'] = 'cuda'
        cfg['llm']['require_full_gpu'] = True
        cfg['runtime'].update(story_workers=1, llm_slots=1)
        for preset, gpu in [('rtx4090', 'RTX 4090'), ('rtx5090', 'RTX 5090')]:
            cfg['runtime'].update(preset=preset, required_gpu=gpu)
            validate_runpod_profile(cfg)
        cfg['runtime']['required_gpu'] = 'RTX 4090'
        with self.assertRaises(ValueError):
            validate_runpod_profile(cfg)
        cfg['runtime'].update(preset='a40', required_gpu='A40')
        with self.assertRaises(ValueError):
            validate_runpod_profile(cfg)

    def test_output_budget_belongs_to_request_not_each_target(self):
        self.assertEqual(output_tokens(config(), "analyze_dialogue_v6"), 2048)
        self.assertEqual(output_tokens(config(), "translate_vi_group_v6_natural"), 2048)

    def test_multi_target_overflow_must_split_before_context_reduction(self):
        data = {"targets": [{"id": "u1", "text": "first"}, {"id": "u2", "text": "second"}], "history": [1, 2]}
        def measure(*args):
            return {"estimate": 200, "limit": 100}
        with self.assertRaises(BudgetExceeded):
            fit_request(config(), "analyze_dialogue_v6", "prompt", data, {}, measure=measure)
        self.assertEqual(data["history"], [1, 2])

    def test_singleton_compaction_keeps_source_and_records_removed_history(self):
        data = {"targets": [{"id": "u1", "text": "Do not shorten this.",
                             "speaker_anchor": {"locked": True, "person_id": "p1"}}],
                "previous_translations": [{"id": "old1"}, {"id": "old2"}], "mentions": [{"id": "m1"}]}
        original = copy.deepcopy(data)
        def measure(task, prompt, value, schema, images):
            return {"estimate": 80 + 20 * len(value["previous_translations"]), "limit": 100}
        fitted, report = fit_request(config(), "translate_vi_group_v6_natural", "prompt", data, {}, measure=measure)
        self.assertEqual(fitted["targets"], original["targets"])
        self.assertEqual(fitted["mentions"], original["mentions"])
        self.assertEqual(data, original)
        self.assertEqual(fitted["previous_translations"], [{"id": "old2"}])
        self.assertEqual(report["reductions"][0]["field"], "previous_translations")

    def test_protected_singleton_cannot_be_silently_truncated(self):
        def measure(*args):
            return {"estimate": 200, "limit": 100}
        with self.assertRaises(BudgetExceeded):
            fit_request(config(), "analyze_dialogue_v6", "prompt",
                        {"targets": [{"id": "u1", "text": "protected"}]}, {}, measure=measure)

    def test_prompt_suffix_is_not_duplicated_on_repair(self):
        prompt = final_prompt("analyze_dialogue_v6", "prompt")
        self.assertEqual(final_prompt("analyze_dialogue_v6", prompt + " repair"), prompt + " repair")


class VLLMProtocolTests(unittest.TestCase):
    def test_tokenizer_receives_exact_chat_schema_and_thinking_switch(self):
        with tempfile.TemporaryDirectory() as root:
            provider = VLLM(config(), root)
            schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
            with patch("manga_pipeline.providers.vllm.gpu_lease", return_value=nullcontext()), \
                 patch.object(provider, "_ready"), patch.object(provider, "_http", return_value={"count": 1500}) as http:
                report = provider.request_budget("analyze_dialogue_v6", "prompt", {"targets": []}, schema)
            route, body = http.call_args.args
            self.assertEqual(route, "/tokenize")
            self.assertFalse(body["chat_template_kwargs"]["enable_thinking"])
            self.assertIn(json.dumps(schema, ensure_ascii=False, sort_keys=True), body["messages"][0]["content"])
            self.assertEqual(report["estimate"], 1500 + 2048 + 512)
            self.assertEqual(report["limit"], int(32768 * 0.85))

    def test_lost_generation_pauses_and_cannot_immediately_retry(self):
        with tempfile.TemporaryDirectory() as root, patch.dict("os.environ", {"MANGA_LLM_PAUSE_FILE": str(Path(root) / "pause.json")}):
            provider = VLLM(config(), root)
            budget = {"estimate": 3000, "limit": 27000, "input_tokens": 440,
                      "output_reserve": 2048, "num_ctx": 32768, "method": "test"}
            with patch("manga_pipeline.providers.vllm.gpu_lease", side_effect=lambda kind: nullcontext()), \
                 patch("manga_pipeline.providers.vllm.quarantine"), patch.object(provider, "_ready"), \
                 patch.object(provider, "request_budget", return_value=budget), \
                 patch.object(provider, "_http", side_effect=TimeoutError("lost response")) as http:
                for _ in range(2):
                    with self.assertRaises(InferenceUncertain):
                        provider.infer_once("analyze_dialogue_v6", "prompt", {"targets": [{"id": "u1"}]}, {}, lambda value: None)
                self.assertEqual(http.call_count, 1)
            receipt = json.loads((Path(root) / "pause.json").read_text(encoding="utf-8"))
            self.assertEqual(receipt["status"], "paused")
            ledgers = list((Path(root) / "timeouts").glob("*.json"))
            self.assertEqual(len(ledgers), 1)
            self.assertEqual(json.loads(ledgers[0].read_text(encoding="utf-8"))["timeouts"], 1)


if __name__ == "__main__":
    unittest.main()
