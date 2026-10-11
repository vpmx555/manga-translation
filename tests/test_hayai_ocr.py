import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from manga_pipeline.extraction.hayai import DEFAULT_CONFIG, recognize_isolated, text_crop, validate_config
from manga_pipeline.pipeline import load_config


class HayaiTests(unittest.TestCase):
    def test_crop_clips_and_rejects_invalid_boxes(self):
        image = np.zeros((20, 30, 3), dtype=np.uint8)
        self.assertEqual(text_crop(image, [-2, 1.2, 40, 9.1]).size, (30, 9))
        for box in ([4, 4, 4, 8], [31, 0, 40, 10], [0, 0, float("nan"), 1]):
            with self.assertRaises(ValueError):
                text_crop(image, box)

    def test_default_and_config_validation(self):
        self.assertEqual(load_config()["ocr"]["backend"], "hayai")
        for update in ({"batch_size": 0}, {"backend": "unknown"}, {"revision": ""}):
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
