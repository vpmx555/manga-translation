"""Translate core engines into v6 contracts without running core's orchestrator."""
import importlib

import numpy as np

_MODULES = {
    "detector": "manga_pipeline.visual.core_detector",
    "ocr": "manga_pipeline.extraction.core_ocr",
    "aot_model": "manga_pipeline.visual.core_aot_model",
    "inpaint": "manga_pipeline.visual.core_inpaint",
    "typeset": "manga_pipeline.v6.typeset_geometry",
}


def import_core(module):
    """Load bundled engines lazily in the appropriate worker environment."""
    return importlib.import_module(_MODULES[module])


class CoreLayout:
    def __init__(self, config):
        # Keep the pinned checkpoint receipt and strict state-dict validation.
        from ..visual.koharu import Koharu
        native = Koharu(config)
        self.asset_hashes = native.asset_hashes
        self.detector = import_core("detector").MangaDetector(
            device=native.device, model=native.model,
            class_thresholds={0: .2, 1: config["bubble_threshold"],
                              2: config["bubble_threshold"], 3: config["panel_threshold"]},
        )

    def detect(self, image):
        from ..visual.geometry import clipped_box, mask_contour
        detections, panels = self.detector.detect_with_panels(image)
        records, texts = [], []
        for detection in detections:
            panel = detection.panel_id - 1 if detection.panel_id else None
            box = clipped_box(detection.bbox, image.size)
            if detection.label == "text":
                texts.append({"bbox": box, "confidence": detection.confidence,
                              "panel_index": panel, "reading_order": detection.reading_order})
                continue
            if detection.label != "bubble":
                continue
            if detection.bubble_mask is None or detection.bubble_mask_origin is None:
                raise ValueError("Core bubble detection is missing its segmentation mask")
            local = np.asarray(detection.bubble_mask, dtype=bool)
            x, y = detection.bubble_mask_origin
            if (local.ndim != 2 or x < 0 or y < 0 or x + local.shape[1] > image.width
                    or y + local.shape[0] > image.height or not local.any()):
                raise ValueError("Core bubble mask is outside original page coordinates")
            mask = np.zeros((image.height, image.width), dtype=bool)
            mask[y:y+local.shape[0], x:x+local.shape[1]] = local
            records.append({"bbox": box, "mask": mask, "contour": mask_contour(mask),
                "confidence": detection.confidence, "panel_index": panel,
                "source_panel_index": panel, "source_pass": "core_panel_crop",
                "clipped_edges": []})
        effects = [{"bbox": clipped_box(item["bbox"], image.size), "confidence": item["confidence"]}
                   for item in getattr(self.detector, "effect_candidates", [])]
        return {"panels": [{"bbox": clipped_box(p.bbox, image.size)} for p in panels],
                "bubbles": records, "bubble_candidates": records, "text_regions": texts,
                "onomatopoeia": effects}


class CoreAOT:
    def __init__(self, config):
        import torch
        from safetensors.torch import load_file
        from ..visual.config import checked_assets
        from ..extraction.pages import choose_device
        directory, self.asset_hashes = checked_assets(config)
        self.config = config
        self.device = choose_device(config["device"], torch.cuda.is_available())
        self.model = import_core("aot_model").AOTGenerator()
        self.model.load_state_dict(load_file(str(directory / "model.safetensors"), device="cpu"), strict=True)
        self.model.to(self.device).eval()

    def predict(self, image, mask):
        return import_core("inpaint")._aot_inpaint(
            image, mask, backend=self.device, model=self.model, max_side=self.config["max_side"])


def rectangle_mask(mask):
    """Use core's rectangle fitter inside the v6-eroded body; never bridge a hole."""
    output = np.zeros_like(mask, dtype=bool)
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return output
    x, y = int(xs.min()), int(ys.min())
    rectangle = import_core("typeset").largest_rectangle(
        mask[y:int(ys.max())+1, x:int(xs.max())+1])
    if rectangle is not None:
        top, left, bottom, right = rectangle
        output[y+top:y+bottom, x+left:x+right] = True
    return output
