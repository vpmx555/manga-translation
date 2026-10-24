"""GLM crop protocol tests; fake model tensors only, no CUDA/model downloads."""
import copy
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import torch
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from manga_pipeline.extraction.glm_ocr import GlmOcrEngine, DEFAULT_CONFIG, normalize, MODEL, REVISION
from manga_pipeline.extraction.hayai import recognize_isolated
from manga_pipeline.pipeline import load_config


class GlmTests(unittest.TestCase):
    def engine(self, texts, token_rows=None):
        engine = GlmOcrEngine(model_name=MODEL, revision=REVISION, device="cuda")
        self.messages = None
        class Inputs(dict):
            def to(self, device):
                return self
        def template(messages, **kwargs):
            self.messages = messages
            self.assertEqual(kwargs["padding"], True)
            for message in messages:
                self.assertTrue(Path(message[0]["content"][0]["url"]).is_file())
                self.assertEqual(message[0]["content"][1]["text"], "Text Recognition:")
            return Inputs(input_ids=torch.tensor([[99,98]]*len(messages)))
        engine.processor = SimpleNamespace(apply_chat_template=template,
                                           batch_decode=lambda *a, **k: texts)
        engine.model = SimpleNamespace(generate=lambda **kwargs: torch.tensor(
            [[99,98]+r for r in (token_rows or [[7,2]]*len(texts))]),
            generation_config=SimpleNamespace(eos_token_id=2))
        return engine

    def test_default_model_batch_and_revision(self):
        for name in (None, "pipeline.json", "pipeline-v6-runpod.json", "pipeline-visual.json", "pipeline-visual-runpod.json"):
            config = load_config(Path("configs")/name if name else None)["ocr"]
            self.assertEqual(config["backend"], "glm-ocr")
            self.assertEqual(config["model"], MODEL)
            self.assertEqual(config["revision"], REVISION)
            self.assertEqual(config["batch_size"], 8)
            self.assertEqual(config["max_new_tokens"], 2048)

    def test_markup_is_not_dialogue_and_hyphen_is_not_guessed(self):
        self.assertEqual(normalize("```markdown\n\n```"), "")
        self.assertEqual(normalize("```markdown\nHi\nthere!\n```"), "Hi there!")
        self.assertEqual(normalize("SASA-\nMORI"), "SASA- MORI")

    def test_order_raw_text_and_empty_flags(self):
        raw = ["SASA-\nMORI", "```markdown\n\n```"]
        engine = self.engine(raw)
        output = engine.predict_batch([Image.new("RGB",(8,8))]*2)
        self.assertEqual(output, ["SASA- MORI", ""])
        self.assertEqual([d["raw_text"] for d in engine.last_details], raw)
        self.assertTrue(engine.last_details[1]["empty"])
        self.assertEqual([d["output_tokens"] for d in engine.last_details], [1,1])

    def test_truncation_and_alignment_are_never_accepted(self):
        engine = self.engine(["partial"], [[7,8]])
        engine.max_new_tokens = 2
        with self.assertRaisesRegex(ValueError, "truncated"):
            engine.predict_batch([Image.new("RGB",(8,8))])
        engine = self.engine(["one"])
        with self.assertRaisesRegex(ValueError, "alignment"):
            engine.predict_batch([Image.new("RGB",(8,8))]*2)

    def test_cpu_is_not_a_silent_fallback(self):
        engine = GlmOcrEngine(model_name=MODEL, revision=REVISION, device="cpu")
        with self.assertRaisesRegex(RuntimeError, "requires CUDA"):
            engine.load()

    def test_isolation_transfers_details_without_changing_boxes(self):
        results = [{"texts": [[0,0,5,5]]}]
        before = copy.deepcopy(results)
        detail = {"raw_text": "```markdown\n\n```", "text": "", "empty": True, "truncated": False}
        def worker(args, **kwargs):
            request = json.loads(Path(args[-2]).read_text())
            self.assertEqual(request["config"]["backend"], "glm-ocr")
            Path(args[-1]).write_text(json.dumps({"ocr": [[""]], "details": [[detail]]}))
        with patch("manga_pipeline.extraction.hayai.subprocess.run", side_effect=worker):
            recognize_isolated(["page.png"], results, {**DEFAULT_CONFIG, "python": "fake-glm-python"}, "cuda")
        self.assertEqual(results[0]["texts"], before[0]["texts"])
        self.assertEqual(results[0]["ocr_details"], [detail])


if __name__ == "__main__":
    unittest.main()
