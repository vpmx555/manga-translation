import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from manga_pipeline.pipeline import load_config
from manga_pipeline.storage.io import file_hash
from manga_pipeline.storage.runs import RunStore
from manga_pipeline.visual.bubbles import match_bubble
from manga_pipeline.visual.geometry import write_png
from manga_pipeline.visual.inpaint import inpaint, plan_masks
from manga_pipeline.visual.magi_panels import koharu_panels


def sort_panels(panel_tensor):
    return list(range(len(panel_tensor)))


class MagiPanelProbe:
    def predict_detections_and_associations(self, panel_tensor):
        return panel_tensor[sort_panels(panel_tensor)]


class LayoutBackend:
    def __init__(self):
        self.calls = 0

    def detect(self, image):
        self.calls += 1
        mask = np.zeros((image.height, image.width), dtype=bool)
        mask[4:60, 4:60] = True
        return {
            "panels": [{"bbox": [0, 0, 64, 64], "confidence": 0.9}],
            "bubbles": [{"bbox": [4, 4, 60, 60], "confidence": 0.9,
                         "contour": [[4, 4], [60, 4], [60, 60], [4, 60]], "mask": mask}],
        }


class InpaintBackend:
    def __init__(self):
        self.calls = 0

    def predict(self, image, mask):
        self.calls += 1
        return Image.new("RGB", image.size, (10, 20, 200))


class VisualPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_ambiguous_bubble_is_unmatched(self):
        mask = np.ones((20, 20), dtype=bool)
        bubbles = [{"id": "a", "bbox": [0, 0, 20, 20], "geometry": {}},
                   {"id": "b", "bbox": [0, 0, 20, 20], "geometry": {}}]
        result = match_bubble([5, 5, 10, 10], bubbles, [mask, mask],
                              {"mapping_coverage": 0.8, "mapping_margin": 0.15})
        self.assertIsNone(result["bubble_id"])
        self.assertEqual(result["bubble_mapping"]["reason"], "ambiguous_overlap")
        self.assertEqual(result["bubble_geometry"]["bubble_style"], "unknown")

    def test_koharu_panels_replace_magi_sort_input(self):
        import torch

        original = sort_panels
        pages = [{"panels": [{"bbox": [5, 6, 20, 30]}]}]
        with koharu_panels(MagiPanelProbe(), pages):
            output = MagiPanelProbe().predict_detections_and_associations(torch.zeros((2, 4)))
            self.assertEqual(output.tolist(), [[5.0, 6.0, 20.0, 30.0]])
        self.assertIs(sort_panels, original)

    def test_inpaint_mask_protects_bubble_boundary(self):
        bubble_mask = np.zeros((64, 64), dtype=np.uint8)
        bubble_mask[4:60, 4:60] = 255
        mask_path = self.root / "bubble.png"
        write_png(mask_path, Image.fromarray(bubble_mask))
        page = {"bubbles": [{"id": "b1", "mask_path": "bubble.png",
                             "mask_sha256": file_hash(mask_path)}]}
        texts = [{"id": "t1", "bbox": [4, 20, 10, 30], "bubble_id": "b1"}]
        config = load_config()["inpaint"]
        white, aot, decisions = plan_masks(Image.new("RGB", (64, 64), "white"), page,
                                             texts, {"t1"}, self.root, config)
        self.assertFalse(white.any())
        self.assertFalse(aot[25, 4])
        self.assertTrue(aot[25, 8])
        self.assertTrue(decisions[0]["requires_review"])

if __name__ == "__main__":
    unittest.main()
