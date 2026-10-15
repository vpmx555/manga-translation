"""Detect manga panels, speech bubbles, and text with Koharu RF-DETR."""

import importlib.util
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
import cv2
import numpy as np
import torch
from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)

KOHARU_MODEL_REPO = "mayocream/koharu-layout-rfdetr-seg-2xl-1152"
KOHARU_CLASS_NAMES = {0: "text", 1: "onomatopoeia", 2: "bubble", 3: "panel"}
KOHARU_CLASS_THRESHOLDS = {0: 0.2, 1: 0.25, 2: 0.25, 3: 0.50}


def load_koharu_model(model_dir: Optional[str] = None):
    """Load Koharu RF-DETR from local files only."""
    local_model_dir = (
        Path(model_dir)
        if model_dir
        else Path(__file__).resolve().parents[3] / "models" / "detector"
    )
    weights = local_model_dir / "model.safetensors"
    loader_path = local_model_dir / "load_model.py"
    missing = [path.name for path in (weights, loader_path) if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            f"Missing local detector assets in {local_model_dir}: {', '.join(missing)}. "
            "Run setup_local_models.py once before using offline mode."
        )
    spec = importlib.util.spec_from_file_location("koharu_layout_loader", loader_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load the Koharu model loader.")
    loader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loader)
    model = loader.load_model(weights)
    optimize = getattr(model, "optimize_for_inference", None)
    if callable(optimize):
        try:
            optimize(compile=False)
            logger.info("Koharu RF-DETR optimized for inference.")
        except Exception as error:
            logger.warning("RF-DETR optimization unavailable; using standard inference: %s", error)
    return model


def _to_numpy(value: Any) -> np.ndarray:
    if hasattr(value, "detach"):
        value = value.detach().cpu().numpy()
    elif hasattr(value, "cpu"):
        value = value.cpu().numpy()
    return np.asarray(value)


def _mask_to_contour(
    mask: np.ndarray, origin: Tuple[int, int] = (0, 0)
) -> Optional[List[List[int]]]:
    """Convert a binary bubble mask to a simplified absolute-coordinate polygon."""
    mask_array = np.asarray(mask)
    if mask_array.ndim != 2:
        raise ValueError("Segmentation masks must be two-dimensional.")

    binary_mask = (mask_array > 0.5).astype(np.uint8)
    contours, _ = cv2.findContours(
        binary_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    if not contours:
        return None

    contour = max(contours, key=cv2.contourArea)
    epsilon = max(1.0, cv2.arcLength(contour, closed=True) * 0.003)
    polygon = cv2.approxPolyDP(contour, epsilon, closed=True).reshape(-1, 2)
    origin_x, origin_y = origin
    return [
        [int(point[0]) + origin_x, int(point[1]) + origin_y]
        for point in polygon
    ]


def _sort_top_down_right_to_left(items: List[dict], min_row_tolerance: int) -> List[dict]:
    if not items:
        return []

    heights = sorted(max(1, item["bbox"][3] - item["bbox"][1]) for item in items)
    row_tolerance = max(min_row_tolerance, int(heights[len(heights) // 2] * 0.35))
    rows = []
    for item in sorted(items, key=lambda value: value["bbox"][1]):
        top = item["bbox"][1]
        matching_rows = [row for row in rows if abs(row["top"] - top) <= row_tolerance]
        if not matching_rows:
            rows.append({"top": top, "items": [item]})
        else:
            row = min(matching_rows, key=lambda value: abs(value["top"] - top))
            row["items"].append(item)
            row["top"] = sum(value["bbox"][1] for value in row["items"]) / len(row["items"])

    ordered = []
    for row in sorted(rows, key=lambda value: value["top"]):
        ordered.extend(
            sorted(
                row["items"],
                key=lambda value: (
                    -(value["bbox"][0] + value["bbox"][2]) / 2,
                    value["bbox"][1],
                ),
            )
        )
    return ordered


def _sort_reading_order(items: List[dict]) -> List[dict]:
    """Sort objects top-to-bottom, then right-to-left within each row."""
    return _sort_top_down_right_to_left(items, min_row_tolerance=12)


def _sort_panel_order(items: List[dict]) -> List[dict]:
    """Sort panel rows top-to-bottom, then panels right-to-left within each row."""
    return _sort_top_down_right_to_left(items, min_row_tolerance=16)


def detect_layout(
    image_path: str,
    output_dir: str,
    threshold: float = 0.20,
    device: Optional[str] = None,
    model_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Run Koharu layout detection and save annotated output and panel crops."""
    source = Path(image_path)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    panel_dir = destination / "panels"
    panel_dir.mkdir(exist_ok=True)

    model = load_koharu_model(model_dir)
    image = Image.open(source).convert("RGB")
    layout = model.predict(
        image,
        threshold=threshold,
        shape=(1152, 1152),
        include_source_image=False,
    )
    class_ids = _to_numpy(layout.class_id).reshape(-1).astype(int)
    confidences = _to_numpy(layout.confidence).reshape(-1)
    boxes = _to_numpy(layout.xyxy)
    masks = getattr(layout, "mask", None)
    if masks is not None:
        masks = _to_numpy(masks)
    records = []
    for index, (class_id, confidence, box) in enumerate(zip(class_ids, confidences, boxes)):
        label = KOHARU_CLASS_NAMES.get(int(class_id), str(class_id))
        if label not in KOHARU_CLASS_NAMES.values():
            continue
        if confidence < KOHARU_CLASS_THRESHOLDS.get(int(class_id), threshold):
            continue
        record = {
            "label": label,
            "class_id": int(class_id),
            "confidence": round(float(confidence), 4),
            "bbox": [int(round(value)) for value in box],
        }
        if label == "bubble" and masks is not None:
            record["mask"] = masks[index]
            record["bubble_contour"] = _mask_to_contour(record["mask"])
        records.append(record)

    panels = _sort_panel_order([record for record in records if record["label"] == "panel"])
    regions = [record for record in records if record["label"] != "panel"]
    ordered_records = panels[:]
    for panel_index, panel in enumerate(panels, start=1):
        panel["panel_order"] = panel_index
        panel["reading_order"] = panel_index
        panel_regions = []
        px1, py1, px2, py2 = panel["bbox"]
        for region in regions:
            cx = (region["bbox"][0] + region["bbox"][2]) / 2
            cy = (region["bbox"][1] + region["bbox"][3]) / 2
            if px1 <= cx <= px2 and py1 <= cy <= py2:
                panel_regions.append(region)
        for region in _sort_reading_order(panel_regions):
            region["panel_order"] = panel_index
            ordered_records.append(region)

    ordered_records.extend(region for region in regions if "panel_order" not in region)
    for reading_order, record in enumerate(ordered_records, start=1):
        record["id"] = reading_order
        record["reading_order"] = reading_order

    annotated = image.convert("RGBA")
    draw = ImageDraw.Draw(annotated)
    from .core_visualizer import Visualizer

    for record in ordered_records:
        x1, y1, x2, y2 = record["bbox"]
        if record["label"] == "panel":
            draw.rectangle((x1, y1, x2, y2), outline=(255, 190, 40), width=4)
        elif record["label"] == "bubble" and "mask" in record:
            Visualizer.draw_mask_outline(
                annotated,
                record["mask"],
                (0, 0),
                color=Visualizer.COLOR_BUBBLE_BOX,
                line_width=3,
            )
        if record["label"] != "text":
            draw.text((x1 + 5, y1 + 5), f"{record['reading_order']}: {record['label']}", fill=(255, 255, 255))
        if record["label"] == "panel":
            image.crop((x1, y1, x2, y2)).save(panel_dir / f"panel_{record['panel_order']:02d}.jpg")

    annotated.convert("RGB").save(destination / "annotated_layout.jpg")
    result = {
        "image": str(source),
        "model": KOHARU_MODEL_REPO,
        "class_names": KOHARU_CLASS_NAMES,
        "total_detections": len(ordered_records),
        "detections": [
            {key: value for key, value in record.items() if key != "mask"}
            for record in ordered_records
        ],
    }
    (destination / "layout.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


@dataclass
class DetectionBox:
    id: int
    reading_order: int
    bbox: List[int]            # [x1, y1, x2, y2] - Vùng chứa chữ để OCR
    bubble_bbox: Optional[List[int]] = None  # [bx1, by1, bx2, by2] - Khung thoại chứa chữ
    is_bubble: bool = True
    confidence: float = 0.0
    text: str = ""             # Điền sau khi OCR
    label: str = "text"
    panel_id: int = 0
    bubble_id: int = 0
    bubble_order: int = 0
    text_order: int = 0
    bubble_mask: Optional[np.ndarray] = None
    bubble_mask_origin: Optional[Tuple[int, int]] = None
    bubble_contour: Optional[List[List[int]]] = None

    @property
    def width(self) -> int:
        return self.bbox[2] - self.bbox[0]

    @property
    def height(self) -> int:
        return self.bbox[3] - self.bbox[1]

    @property
    def center(self) -> Tuple[float, float]:
        return (self.bbox[0] + self.bbox[2]) / 2.0, (self.bbox[1] + self.bbox[3]) / 2.0


@dataclass
class PanelRegion:
    id: int
    bbox: List[int]
    text_boxes: List[DetectionBox]


class MangaDetector:
    def __init__(
        self,
        device: Optional[str] = None,
        conf_threshold: float = 0.30,
        model=None,
        class_thresholds=None,
    ):
        if device is None:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            self.device = device

        self.conf_threshold = conf_threshold
        self._koharu_model = model
        self.class_thresholds = dict(class_thresholds or KOHARU_CLASS_THRESHOLDS)

    def load(self):
        """Lazy load the Koharu layout detector."""
        if self._koharu_model is None:
            logger.info("Loading Koharu RF-DETR model...")
            self._koharu_model = load_koharu_model()

    def detect(self, image: Union[Image.Image, str]) -> List[DetectionBox]:
        """
        Detect panels and text regions, returning detections in reading order.
        """
        detections, _ = self.detect_with_panels(image)
        return detections

    def detect_with_panels(
        self, image: Union[Image.Image, str]
    ) -> Tuple[List[DetectionBox], List[PanelRegion]]:
        """Detect and order panels first, then detect text regions in each panel."""
        if isinstance(image, str):
            image = Image.open(image)
        image = image.convert("RGB")

        if self._koharu_model is None:
            logger.info("Loading Koharu RF-DETR model...")
            self._koharu_model = load_koharu_model()

        def predict_records(source_image: Image.Image, threshold: float) -> List[dict]:
            layout = self._koharu_model.predict(
                source_image,
                threshold=threshold,
                shape=(1152, 1152),
                include_source_image=False,
            )
            class_ids = _to_numpy(layout.class_id).reshape(-1).astype(int)
            confidences = _to_numpy(layout.confidence).reshape(-1)
            boxes = _to_numpy(layout.xyxy)
            masks = getattr(layout, "mask", None)
            if masks is not None:
                masks = _to_numpy(masks)
            records = []
            for index, (class_id, confidence, box) in enumerate(zip(class_ids, confidences, boxes)):
                label = KOHARU_CLASS_NAMES.get(int(class_id), str(class_id))
                if label not in KOHARU_CLASS_NAMES.values():
                    continue
                if confidence < self.class_thresholds.get(int(class_id), self.conf_threshold):
                    continue
                record = {
                    "label": label,
                    "confidence": float(confidence),
                    "bbox": [int(round(value)) for value in box],
                }
                if label == "bubble" and masks is not None:
                    record["mask"] = masks[index]
                records.append(record)
            return records

        logger.info("Detecting panels on %sx%s page...", image.width, image.height)
        panel_records = predict_records(image, min(self.class_thresholds.values()))
        self.effect_candidates = [item for item in panel_records if item["label"] == "onomatopoeia"]
        panels = []
        for item in panel_records:
            if item["label"] != "panel":
                continue
            x1, y1, x2, y2 = item["bbox"]
            bbox = [
                max(0, min(image.width, x1)),
                max(0, min(image.height, y1)),
                max(0, min(image.width, x2)),
                max(0, min(image.height, y2)),
            ]
            if bbox[2] > bbox[0] and bbox[3] > bbox[1]:
                panels.append(PanelRegion(id=0, bbox=bbox, text_boxes=[]))

        if not panels:
            logger.warning("No panels detected; running region detection on the full page.")
            panels.append(PanelRegion(id=1, bbox=[0, 0, image.width, image.height], text_boxes=[]))

        ordered_panel_records = _sort_panel_order(
            [{"bbox": panel.bbox, "panel": panel} for panel in panels]
        )
        panels = [record["panel"] for record in ordered_panel_records]
        for panel_id, panel in enumerate(panels, start=1):
            panel.id = panel_id

        logger.info("Detected and ordered %s panels; detecting text within each panel.", len(panels))
        text_threshold = min(
            self.class_thresholds[class_id] for class_id in (0, 2)
        )
        context_padding = max(64, int(image.height * 0.08))
        detections = []
        next_bubble_id = 1
        for panel in panels:
            px1, py1, px2, py2 = panel.bbox
            crop_x1 = max(0, px1 - context_padding)
            crop_y1 = max(0, py1 - context_padding)
            crop_x2 = min(image.width, px2 + context_padding)
            crop_y2 = min(image.height, py2 + context_padding)
            panel_image = image.crop((crop_x1, crop_y1, crop_x2, crop_y2))
            region_records = predict_records(panel_image, text_threshold)
            bubble_records = []
            text_records = []
            for item in region_records:
                if item["label"] not in {"bubble", "text"}:
                    continue
                x1, y1, x2, y2 = item["bbox"]
                x1 = max(0, min(panel_image.width, x1))
                y1 = max(0, min(panel_image.height, y1))
                x2 = max(0, min(panel_image.width, x2))
                y2 = max(0, min(panel_image.height, y2))
                if x2 <= x1 or y2 <= y1:
                    continue
                if item["label"] == "text" and (x2 - x1 < 4 or y2 - y1 < 4):
                    continue
                bbox = [x1 + crop_x1, y1 + crop_y1, x2 + crop_x1, y2 + crop_y1]
                record = {**item, "bbox": bbox}
                (bubble_records if item["label"] == "bubble" else text_records).append(record)

            ordered_bubbles = _sort_reading_order(bubble_records)
            bubble_texts = {id(bubble): [] for bubble in ordered_bubbles}
            free_texts = []
            for text_record in text_records:
                containing_bubbles = [
                    bubble
                    for bubble in ordered_bubbles
                    if self._is_inside_or_overlapping(text_record["bbox"], bubble["bbox"])
                ]
                if containing_bubbles:
                    bubble = min(
                        containing_bubbles,
                        key=lambda item: max(
                            1,
                            (item["bbox"][2] - item["bbox"][0])
                            * (item["bbox"][3] - item["bbox"][1]),
                        ),
                    )
                    bubble_texts[id(bubble)].append(text_record)
                else:
                    free_texts.append(text_record)

            for bubble_order, bubble in enumerate(ordered_bubbles, start=1):
                bubble_id = next_bubble_id
                next_bubble_id += 1
                bubble_bbox = bubble["bbox"]
                bubble_mask = None
                bubble_mask_origin = None
                bubble_contour = None
                if "mask" in bubble:
                    mask = np.asarray(bubble["mask"])
                    if mask.ndim != 2 or mask.shape != (panel_image.height, panel_image.width):
                        raise ValueError(
                            "Detector bubble mask dimensions do not match the detected panel crop."
                        )
                    mask = mask > 0.5
                    mask_y, mask_x = np.nonzero(mask)
                    if mask_x.size:
                        mask_x1, mask_x2 = int(mask_x.min()), int(mask_x.max()) + 1
                        mask_y1, mask_y2 = int(mask_y.min()), int(mask_y.max()) + 1
                        bubble_mask = mask[mask_y1:mask_y2, mask_x1:mask_x2]
                        bubble_mask_origin = (
                            crop_x1 + mask_x1,
                            crop_y1 + mask_y1,
                        )
                        bubble_contour = _mask_to_contour(
                            bubble_mask, bubble_mask_origin
                        )
                detections.append(
                    DetectionBox(
                        id=0,
                        reading_order=0,
                        bbox=bubble_bbox,
                        is_bubble=True,
                        confidence=round(bubble["confidence"], 3),
                        label="bubble",
                        panel_id=panel.id,
                        bubble_id=bubble_id,
                        bubble_order=bubble_order,
                        bubble_mask=bubble_mask,
                        bubble_mask_origin=bubble_mask_origin,
                        bubble_contour=bubble_contour,
                    )
                )
                lines = sorted(
                    bubble_texts[id(bubble)],
                    key=lambda item: (item["bbox"][1], item["bbox"][0]),
                )
                for text_order, text_record in enumerate(lines, start=1):
                    detections.append(
                        DetectionBox(
                            id=0,
                            reading_order=0,
                            bbox=text_record["bbox"],
                            bubble_bbox=bubble_bbox,
                            bubble_contour=bubble_contour,
                            is_bubble=True,
                            confidence=round(text_record["confidence"], 3),
                            label=text_record["label"],
                            panel_id=panel.id,
                            bubble_id=bubble_id,
                            bubble_order=bubble_order,
                            text_order=text_order,
                        )
                    )

            ordered_free_texts = _sort_reading_order(free_texts)
            for text_record in ordered_free_texts:
                detections.append(
                    DetectionBox(
                        id=0,
                        reading_order=0,
                        bbox=text_record["bbox"],
                        is_bubble=False,
                        confidence=round(text_record["confidence"], 3),
                        label=text_record["label"],
                        panel_id=panel.id,
                    )
                )

        return self._finalize_layout_order(detections, panels)

    @staticmethod
    def _finalize_layout_order(
        detections: List[DetectionBox], panels: List[PanelRegion]
    ) -> Tuple[List[DetectionBox], List[PanelRegion]]:
        """Suppress duplicate objects before assigning panels and reading order."""
        detections = MangaDetector._suppress_duplicate_objects(detections)
        panel_records = [
            {"bbox": panel.bbox, "panel": panel} for panel in panels
        ]
        ordered_panel_records = _sort_panel_order(panel_records)
        ordered_panels = [record["panel"] for record in ordered_panel_records]
        for panel_id, panel in enumerate(ordered_panels, start=1):
            panel.id = panel_id
            panel.text_boxes = []

        bubble_panels = {}
        for detection in detections:
            if detection.label != "bubble":
                continue
            panel = MangaDetector._best_panel_for_box(detection.bbox, ordered_panels)
            detection.panel_id = panel.id if panel else 0
            if panel:
                bubble_panels[detection.bubble_id] = panel.id

        for detection in detections:
            if detection.label == "bubble":
                continue
            panel_id = bubble_panels.get(detection.bubble_id)
            panel = next(
                (candidate for candidate in ordered_panels if candidate.id == panel_id),
                None,
            ) if panel_id else MangaDetector._best_panel_for_box(detection.bbox, ordered_panels)
            detection.panel_id = panel.id if panel else 0

        detections_by_panel = {panel.id: [] for panel in ordered_panels}
        unassigned_detections = []
        for detection in detections:
            if detection.panel_id in detections_by_panel:
                detections_by_panel[detection.panel_id].append(detection)
            else:
                unassigned_detections.append(detection)

        ordered_detections = []
        for panel in ordered_panels:
            panel_detections = detections_by_panel[panel.id]
            bubble_groups = {}
            standalone = []
            for detection in panel_detections:
                if detection.bubble_id:
                    bubble_groups.setdefault(detection.bubble_id, []).append(detection)
                else:
                    standalone.append(detection)

            reading_units = []
            for group in bubble_groups.values():
                bubble = next((item for item in group if item.label == "bubble"), None)
                anchor = bubble or group[0]
                reading_units.append({"bbox": anchor.bbox, "detections": group})
            reading_units.extend(
                {"bbox": detection.bbox, "detections": [detection]}
                for detection in standalone
            )
            bubble_order = 0
            for unit in _sort_reading_order(reading_units):
                group = unit["detections"]
                if any(item.label == "bubble" for item in group):
                    bubble_order += 1
                    for item in group:
                        item.bubble_order = bubble_order
                ordered_detections.extend(
                    sorted(
                        (item for item in group if item.label == "bubble"),
                        key=lambda item: item.bubble_order,
                    )
                )
                ordered_detections.extend(
                    sorted(
                        (item for item in group if item.label != "bubble"),
                        key=lambda item: (item.text_order or 1, item.bbox[1], item.bbox[0]),
                    )
                )
        ordered_detections.extend(unassigned_detections)
        for panel_id, panel in enumerate(ordered_panels, start=1):
            panel.text_boxes = [
                detection for detection in ordered_detections if detection.panel_id == panel_id
            ]
            panel.id = panel_id
        for reading_order, detection in enumerate(ordered_detections, start=1):
            detection.id = reading_order
            detection.reading_order = reading_order
        return ordered_detections, ordered_panels

    @staticmethod
    def _suppress_duplicate_objects(
        detections: List[DetectionBox],
    ) -> List[DetectionBox]:
        """Suppress duplicate regions without merging distinct dialogue bubbles."""
        bubbles = [item for item in detections if item.label == "bubble"]
        text_items = [item for item in detections if item.label != "bubble"]

        text_boxes_by_bubble: Dict[int, List[List[int]]] = {}
        for text_item in text_items:
            if text_item.label == "text" and text_item.bubble_id:
                text_boxes_by_bubble.setdefault(text_item.bubble_id, []).append(
                    text_item.bbox
                )

        def suppress(
            items: List[DetectionBox],
            preserve_distinct_bubble_text: bool = False,
        ) -> Tuple[List[DetectionBox], Dict[int, DetectionBox]]:
            kept = []
            representatives = {}
            sorted_items = sorted(
                items,
                key=lambda item: (
                    -max(1, item.width * item.height),
                    -item.confidence,
                    item.bbox[1],
                    item.bbox[0],
                ),
            )
            for item in sorted_items:
                duplicate = None
                for candidate in kept:
                    if item.label != candidate.label:
                        continue
                    iou, intersection_over_smaller = MangaDetector._box_overlap(
                        item.bbox, candidate.bbox
                    )
                    same_panel = item.panel_id == candidate.panel_id
                    same_bubble = bool(
                        item.bubble_id and item.bubble_id == candidate.bubble_id
                    )
                    if same_panel:
                        iou_threshold, containment_threshold = 0.85, 0.95
                    elif same_bubble:
                        iou_threshold, containment_threshold = 0.45, 0.80
                    elif item.label == "bubble":
                        iou_threshold, containment_threshold = 0.25, 0.60
                    else:
                        iou_threshold, containment_threshold = 0.65, 0.90
                    if iou >= iou_threshold or intersection_over_smaller >= containment_threshold:
                        if preserve_distinct_bubble_text:
                            item_text_boxes = text_boxes_by_bubble.get(
                                item.bubble_id, []
                            )
                            candidate_text_boxes = text_boxes_by_bubble.get(
                                candidate.bubble_id, []
                            )
                            if item_text_boxes and candidate_text_boxes:
                                same_text_region = False
                                for item_text_box in item_text_boxes:
                                    for candidate_text_box in candidate_text_boxes:
                                        text_iou, text_overlap = (
                                            MangaDetector._box_overlap(
                                                item_text_box, candidate_text_box
                                            )
                                        )
                                        if text_iou >= 0.5 or text_overlap >= 0.8:
                                            same_text_region = True
                                            break
                                    if same_text_region:
                                        break
                                if not same_text_region:
                                    continue
                        duplicate = candidate
                        break
                if duplicate is None:
                    kept.append(item)
                    representatives[id(item)] = item
                else:
                    representatives[id(item)] = duplicate
            return kept, representatives

        kept_bubbles, bubble_representatives = suppress(
            bubbles,
            preserve_distinct_bubble_text=True,
        )
        bubble_ids = {
            item.bubble_id: bubble_representatives[id(item)].bubble_id
            for item in bubbles
        }

        for item in text_items:
            item.bubble_id = bubble_ids.get(item.bubble_id, item.bubble_id)
            if item.bubble_id:
                parent_bubble = next(
                    (bubble for bubble in kept_bubbles if bubble.bubble_id == item.bubble_id),
                    None,
                )
                if parent_bubble:
                    item.bubble_bbox = parent_bubble.bbox
                    item.bubble_contour = parent_bubble.bubble_contour
                    item.bubble_mask = parent_bubble.bubble_mask
                    item.bubble_mask_origin = parent_bubble.bubble_mask_origin

        kept_text, _ = suppress(text_items)
        return kept_bubbles + kept_text

    @staticmethod
    def _box_overlap(box_a: List[int], box_b: List[int]) -> Tuple[float, float]:
        intersection_width = max(0, min(box_a[2], box_b[2]) - max(box_a[0], box_b[0]))
        intersection_height = max(0, min(box_a[3], box_b[3]) - max(box_a[1], box_b[1]))
        intersection = intersection_width * intersection_height
        area_a = max(1, (box_a[2] - box_a[0]) * (box_a[3] - box_a[1]))
        area_b = max(1, (box_b[2] - box_b[0]) * (box_b[3] - box_b[1]))
        return intersection / (area_a + area_b - intersection), intersection / min(area_a, area_b)

    @staticmethod
    def _best_panel_for_box(
        bbox: List[int], panels: List[PanelRegion]
    ) -> Optional[PanelRegion]:
        box_area = max(1, (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))
        candidates = []
        for panel in panels:
            px1, py1, px2, py2 = panel.bbox
            intersection = max(0, min(bbox[2], px2) - max(bbox[0], px1)) * max(
                0, min(bbox[3], py2) - max(bbox[1], py1)
            )
            if intersection:
                panel_area = max(1, (px2 - px1) * (py2 - py1))
                candidates.append((intersection / box_area, -panel_area, panel))
        return max(candidates, key=lambda candidate: (candidate[0], candidate[1]))[2] if candidates else None

    @staticmethod
    def _is_inside_or_overlapping(box_a: List[float], box_b: List[float]) -> bool:
        """Check if box_a (text) center is inside box_b (bubble) or has significant overlap."""
        cx = (box_a[0] + box_a[2]) / 2.0
        cy = (box_a[1] + box_a[3]) / 2.0
        # Center containment
        if box_b[0] <= cx <= box_b[2] and box_b[1] <= cy <= box_b[3]:
            return True

        # Area overlap
        ix1 = max(box_a[0], box_b[0])
        iy1 = max(box_a[1], box_b[1])
        ix2 = min(box_a[2], box_b[2])
        iy2 = min(box_a[3], box_b[3])
        if ix2 > ix1 and iy2 > iy1:
            inter = (ix2 - ix1) * (iy2 - iy1)
            area_a = max(1.0, (box_a[2] - box_a[0]) * (box_a[3] - box_a[1]))
            if inter / area_a > 0.25:
                return True
        return False
