"""Contract checks for core adapters; no model downloads or inference."""
import copy
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image

from manga_pipeline.integrations import core
from manga_pipeline.pipeline import Pipeline, load_config
from manga_pipeline.extraction.hayai import recognize_core, DEFAULT_CONFIG as OCR_CONFIG


class CoreIntegrationTests(unittest.TestCase):
    def test_profile_and_legacy_provider_selection(self):
        self.assertFalse(load_config()["core"]["enabled"])
        profile = load_config(Path("configs/core-v6.json"))
        self.assertTrue(profile["core"]["enabled"])
        self.assertEqual(profile["ocr"]["backend"], "hayai")
        injected = object()
        pipeline = Pipeline(SimpleNamespace(manifest={"pipeline_version": 6, "config": profile}),
                            translation_provider=injected)
        self.assertIs(pipeline.translation_provider, injected)
        pipeline = Pipeline(SimpleNamespace(manifest={"pipeline_version": 6, "config": load_config()}),
                            provider=injected)
        self.assertIs(pipeline.provider(), injected)

    def test_layout_restores_crop_mask_coordinates_and_preserves_text_evidence(self):
        local = np.ones((3, 4), dtype=bool)
        bubble = SimpleNamespace(label="bubble", panel_id=1, bbox=[5, 6, 9, 9],
            confidence=.9, bubble_mask=local, bubble_mask_origin=(5, 6))
        text = SimpleNamespace(label="text", panel_id=1, bbox=[6, 6, 8, 8],
                               confidence=.8, reading_order=2)
        adapter = core.CoreLayout.__new__(core.CoreLayout)
        adapter.detector = SimpleNamespace(detect_with_panels=lambda image:
            ([bubble, text], [SimpleNamespace(bbox=[0, 0, 20, 20])]))
        result = adapter.detect(Image.new("RGB", (20, 20)))
        mask = result["bubbles"][0]["mask"]
        self.assertEqual(mask.shape, (20, 20))
        self.assertEqual(mask.sum(), 12)
        self.assertTrue(mask[6:9, 5:9].all())
        self.assertEqual(result["bubbles"][0]["panel_index"], 0)
        self.assertEqual(result["text_regions"][0]["bbox"], [6, 6, 8, 8])
        bubble.bubble_mask_origin = (19, 6)
        with self.assertRaises(ValueError):
            adapter.detect(Image.new("RGB", (20, 20)))

    def test_core_ocr_preserves_magi_character_links_and_batches_crop_order(self):
        results = [{"texts": [[1, 1, 4, 4], [5, 1, 8, 4]],
                    "text_character_associations": [[0, 2]], "characters": [[0, 0, 9, 9]]},
                   {"texts": [[1, 1, 4, 4]]}]
        original_links = copy.deepcopy(results[0]["text_character_associations"])
        calls = []
        class Engine:
            def __init__(self, **kwargs):
                self.offset = 0
            def predict_batch(self, images):
                calls.append(len(images))
                output = [str(i) for i in range(self.offset, self.offset+len(images))]
                self.offset += len(images)
                return output
        with patch.object(core, "import_core", return_value=SimpleNamespace(MangaOcrEngine=Engine)), \
             patch("manga_pipeline.extraction.hayai.local_assets", return_value="pinned-assets"):
            recognize_core([np.zeros((10, 10, 3), dtype=np.uint8)]*2, results,
                           {**OCR_CONFIG, "batch_size": 2}, "cpu")
        self.assertEqual(calls, [2, 1])
        self.assertEqual([r["ocr"] for r in results], [["0", "1"], ["2"]])
        self.assertEqual(results[0]["text_character_associations"], original_links)

    def test_rectangle_never_crosses_a_hole_or_changes_empty_mask(self):
        from manga_pipeline.v6.typeset_geometry import largest_rectangle
        mask = np.zeros((20, 30), dtype=bool)
        mask[2:18, 3:27] = True
        mask[7:12, 10:15] = False
        with patch.object(core, "import_core", return_value=SimpleNamespace(largest_rectangle=largest_rectangle)):
            rectangle = core.rectangle_mask(mask)
            empty = core.rectangle_mask(np.zeros_like(mask))
        self.assertTrue(rectangle.any())
        self.assertFalse(np.any(rectangle & ~mask))
        self.assertFalse(empty.any())

    def test_core_aot_pastes_only_original_mask_and_uses_injected_model(self):
        import torch
        from manga_pipeline.visual.core_inpaint import _aot_inpaint
        pixels = np.arange(32*32*3, dtype=np.uint8).reshape(32, 32, 3)
        image = Image.fromarray(pixels)
        mask = np.zeros((32, 32), dtype=np.uint8)
        mask[10:15, 11:16] = 255
        calls = []
        def fake_model(tensor, mask_tensor):
            calls.append(tuple(tensor.shape))
            return torch.ones_like(tensor)
        output = np.asarray(_aot_inpaint(image, Image.fromarray(mask), backend="cpu", model=fake_model))
        self.assertEqual(len(calls), 1)
        np.testing.assert_array_equal(output[mask == 0], pixels[mask == 0])
        self.assertTrue((output[mask > 0] == 255).all())

    def test_core_ocr_true_batch_and_rejects_truncated_results(self):
        from manga_pipeline.extraction.core_ocr import MangaOcrEngine
        engine = MangaOcrEngine(device="cpu")
        engine._is_loaded = True
        class Inputs(dict):
            def to(self, device):
                return self
        processed = []
        def processor(**kwargs):
            processed.append(len(kwargs["images"]))
            return Inputs(pixel_values=None, pixel_attention_mask=None, spatial_shapes=None)
        engine.processor = processor
        engine.tokenizer = object()
        engine.model = SimpleNamespace(generate=lambda **kwargs: [" One  two ", "Three"])
        crops = [Image.new("RGB", (4, 4))]*2
        self.assertEqual(engine.predict_batch(crops), ["One two", "Three"])
        self.assertEqual(processed, [2])
        engine.model.generate = lambda **kwargs: ["missing"]
        with self.assertRaises(RuntimeError):
            engine.predict_batch(crops)

    def test_core_aot_checkpoint_keys_and_shapes_match_v6_without_allocating_weights(self):
        import torch
        from manga_pipeline.visual.core_aot_model import AOTGenerator as CoreGenerator
        from manga_pipeline.visual.aot_model import AOTGenerator as V6Generator
        with torch.device("meta"):
            source = CoreGenerator().state_dict()
            target = V6Generator().state_dict()
        self.assertEqual({key: tuple(value.shape) for key, value in source.items()},
                         {key: tuple(value.shape) for key, value in target.items()})


if __name__ == "__main__":
    unittest.main()
