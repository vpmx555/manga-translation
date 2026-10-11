"""Koharu RF-DETR backend, isolated from MAGI's Transformers 4 process.

Model construction follows the pinned mayocream loader (MIT model assets).
Text detection/EasyOCR from AIP419 are deliberately not ported.
"""
from importlib.metadata import version
import numpy as np
from PIL import Image
from .config import checked_assets
from .geometry import clipped_box, mask_contour, ordered_boxes, suppress_duplicates
from ..extraction.document import panel_for_box
from ..extraction.pages import choose_device


class Koharu:
    def __init__(self, config):
        import torch
        from safetensors.torch import load_file
        if version("rfdetr") != "1.7.0":
            raise RuntimeError("Koharu requires rfdetr==1.7.0 in the separate layout environment")
        from rfdetr import RFDETRSeg2XLarge
        directory, self.asset_hashes = checked_assets(config)
        self.config = config
        self.device = choose_device(config["device"], torch.cuda.is_available())
        self.model = RFDETRSeg2XLarge(pretrain_weights=None, resolution=1152, num_select=160,
                                     num_classes=4, device=self.device)
        self.model.model.model.load_state_dict(load_file(str(directory / "model.safetensors"), device="cpu"), strict=True)
        self.model.model.class_names = ["text", "onomatopoeia", "bubble", "panel"]

    def _records(self, image):
        # One forward pass yields all classes; class-1 boxes are role evidence only.
        threshold = min(self.config["panel_threshold"], self.config["bubble_threshold"])
        output = self.model.predict(image, threshold=threshold)
        masks = getattr(output, "mask", None)
        records = {1: [], 2: [], 3: []}
        for i, (box, label, confidence) in enumerate(zip(output.xyxy, output.class_id, output.confidence)):
            cls = int(label)
            if cls not in records:
                continue
            minimum = self.config["panel_threshold"] if cls == 3 else self.config["bubble_threshold"]
            if float(confidence) < minimum:
                continue
            record = {"bbox": clipped_box(box, image.size), "confidence": float(confidence)}
            if cls == 2:
                if masks is None:
                    raise ValueError("Koharu segmentation did not return bubble masks")
                mask = np.asarray(masks[i]) > 0.5
                if mask.shape != (image.height, image.width):
                    raise ValueError("Koharu bubble mask dimensions differ from crop")
                if not mask.any():
                    continue
                record["mask"] = mask
            records[cls].append(record)
        return records

    def _predict(self, image, cls, threshold):
        return [r for r in self._records(image).get(cls, []) if r["confidence"] >= threshold]

    def detect(self, image):
        full = self._records(image)
        panels = ordered_boxes(suppress_duplicates(full[3]))
        candidates, effects = [], []
        def collect(records, crop, panel_index):
            x1, y1, x2, y2 = crop
            for cls, destination in ((2, candidates), (1, effects)):
                for source in records[cls]:
                    record = dict(source)
                    b = source["bbox"]
                    record["bbox"] = [b[0]+x1, b[1]+y1, b[2]+x1, b[3]+y1]
                    record.update(source_pass="full_page" if panel_index is None else "panel_crop",
                                  crop_bbox=list(crop), source_panel_index=panel_index,
                                  clipped_edges=[side for side, hit in zip(("left", "top", "right", "bottom"),
                                      (b[0]<=1, b[1]<=1, b[2]>=x2-x1-1, b[3]>=y2-y1-1)) if hit])
                    if cls == 2:
                        mask = np.zeros((image.height, image.width), dtype=bool)
                        mask[y1:y2, x1:x2] = source["mask"]
                        record["mask"] = mask
                        record["contour"] = mask_contour(mask)
                    record["panel_index"] = panel_for_box(record["bbox"], [p["bbox"] for p in panels])
                    destination.append(record)
        collect(full, (0, 0, image.width, image.height), None)
        pad = self.config["crop_padding"]
        for index, panel in enumerate(panels):
            crop = clipped_box([panel["bbox"][0]-pad, panel["bbox"][1]-pad,
                                panel["bbox"][2]+pad, panel["bbox"][3]+pad], image.size)
            if crop[2] > crop[0] and crop[3] > crop[1]:
                collect(self._records(image.crop(tuple(crop))), crop, index)
        bubbles = ordered_boxes(suppress_duplicates(candidates, threshold=0.65))
        return {"panels": panels, "bubbles": bubbles, "bubble_candidates": candidates,
                "onomatopoeia": ordered_boxes(suppress_duplicates(effects, threshold=0.65))}
