import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

import numpy as np
from manga_pipeline.extraction.hayai import DEFAULT_CONFIG, recognize, recognize_isolated, text_crop, validate_config
from manga_pipeline.extraction.core_ocr import MangaOcrEngine
from manga_pipeline.pipeline import load_config


class HayaiTests(unittest.TestCase):
    def test_crop_clips_and_rejects_invalid_boxes(self):
        image = np.zeros((20, 30, 3), dtype=np.uint8)
        self.assertEqual(text_crop(image, [-2, 1.2, 40, 9.1]).size, (30, 9))
        for box in ([4, 4, 4, 8], [31, 0, 40, 10], [0, 0, float("nan"), 1]):
            with self.assertRaises(ValueError):
                text_crop(image, box)

    def test_default_and_config_validation(self):
        self.assertEqual(load_config()["ocr"]["backend"], "glm-ocr")
        self.assertEqual(load_config()['ocr']['engine'], 'core')
        self.assertEqual(load_config()['ocr']['batch_size'], 8)
        for update in ({"batch_size": 0}, {"backend": "unknown"}, {"revision": ""}, {'engine': 'unknown'}):
            with self.assertRaises(ValueError):
                validate_config(dict(DEFAULT_CONFIG, **update))

    def test_isolated_worker_preserves_order_and_rejects_missing_text(self):
        results = [{"texts": [[0, 0, 1, 1], [1, 1, 2, 2]]}, {"texts": []}]
        config = dict(DEFAULT_CONFIG, python="hayai-python")
        def worker(args, **kwargs):
            request = json.loads(Path(args[-2]).read_text(encoding="utf-8"))
            self.assertEqual(request["paths"], ["one.png", "two.png"])
            Path(args[-1]).write_text(json.dumps([["first", "second"], []]), encoding="utf-8")
        with patch("manga_pipeline.extraction.hayai.subprocess.run", side_effect=worker):
            recognize_isolated([Path("one.png"), Path("two.png")], results, config, "cpu")
        self.assertEqual(results[0]["ocr"], ["first", "second"])
        def bad_worker(args, **kwargs):
            Path(args[-1]).write_text(json.dumps([["missing"], []]), encoding="utf-8")
        with patch("manga_pipeline.extraction.hayai.subprocess.run", side_effect=bad_worker):
            with self.assertRaises(ValueError):
                recognize_isolated(["one.png", "two.png"], results, config, "cpu")

    def engine(self, predictions):
        engine = MangaOcrEngine(model_name='test', revision='pinned', processor_name='test-processor',
            processor_revision='pinned', device='cpu')
        engine._is_loaded = True
        class Inputs(dict):
            def to(self, device):
                return self
        engine.processor = lambda **kwargs: Inputs(pixel_values='pixels', pixel_attention_mask='mask', spatial_shapes='shape')
        engine.tokenizer = object()
        engine.model = SimpleNamespace(generate=lambda **kwargs: predictions)
        return engine

    def test_core_normalizes_whitespace_and_accepts_single_string(self):
        from PIL import Image
        image = Image.new('RGB', (8, 8))
        self.assertEqual(self.engine(' One  two\nThree ').predict_batch([image]), ['One two Three'])
        self.assertEqual(self.engine([' A\tB ', 'C\nD']).predict_batch([image, image]), ['A B', 'C D'])
        self.assertEqual(self.engine([]).predict_batch([]), [])

    def test_core_rejects_truncated_or_invalid_results(self):
        from PIL import Image
        images = [Image.new('RGB', (8, 8))]*2
        for output in (['one'], 'two', [None, 'two'], {'one': 'two'}):
            with self.subTest(output=output), self.assertRaises(ValueError):
                self.engine(output).predict_batch(images)

    def test_core_worker_keeps_crop_order_and_character_links(self):
        engine = SimpleNamespace(load=lambda: None)
        sizes = []
        def predict(images):
            sizes.append(len(images))
            return [f'text {len(sizes)}']
        engine.predict_batch = predict
        results = [{'texts': [[0, 0, 3, 3], [3, 0, 6, 3]], 'text_character_associations': [[0, 2], [1, 3]]},
                   {'texts': [[0, 0, 3, 3]]}]
        with patch('manga_pipeline.extraction.core_ocr.MangaOcrEngine', return_value=engine):
            recognize([np.zeros((8, 8, 3), dtype=np.uint8)]*2, results, dict(DEFAULT_CONFIG, batch_size=1), 'cpu')
        self.assertEqual(sizes, [1, 1, 1])
        self.assertEqual([r['ocr'] for r in results], [['text 1', 'text 2'], ['text 3']])
        self.assertEqual(results[0]['text_character_associations'], [[0, 2], [1, 3]])

    def test_core_worker_retains_bounded_oom_batch_recovery(self):
        import torch
        calls = []
        def predict(images):
            calls.append(len(images))
            if len(images) > 1:
                raise torch.cuda.OutOfMemoryError('CUDA out of memory')
            return ['ok']
        engine = SimpleNamespace(load=lambda: None, predict_batch=predict)
        results = [{'texts': [[0, 0, 3, 3], [3, 0, 6, 3]]}]
        with patch('manga_pipeline.extraction.core_ocr.MangaOcrEngine', return_value=engine), \
             patch.dict('os.environ', {'MANGA_GPU_RECOVERY_ATTEMPTS': '1'}), \
             patch('torch.cuda.empty_cache'):
            recognize([np.zeros((8, 8, 3), dtype=np.uint8)], results, dict(DEFAULT_CONFIG, batch_size=2), 'cuda')
        self.assertEqual(calls, [2, 1, 1])
        self.assertEqual(results[0]['ocr'], ['ok', 'ok'])

    def test_default_batch_sixteen_preserves_partial_batch_and_order(self):
        calls, completed = [], []
        def predict(images):
            calls.append(len(images))
            output = [f'text {len(completed)+i}' for i in range(len(images))]
            completed.extend(output)
            return output
        engine = SimpleNamespace(load=lambda: None, predict_batch=predict)
        results = [{'texts': [[0, 0, 3, 3]]*17}]
        with patch('manga_pipeline.extraction.core_ocr.MangaOcrEngine', return_value=engine):
            recognize([np.zeros((8, 8, 3), dtype=np.uint8)], results, DEFAULT_CONFIG, 'cpu')
        self.assertEqual(calls, [16, 1])
        self.assertEqual(results[0]['ocr'], [f'text {i}' for i in range(17)])
