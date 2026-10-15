"""
Visualizer module
Draws detection bounding boxes, speech bubble outlines, and reading order badges on manga images.
"""

import base64
import io
from typing import List, Optional, Tuple, Union
import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from .core_detector import DetectionBox, PanelRegion


class Visualizer:
    # Color palette
    COLOR_TEXT_BOX = (0, 180, 255)       # Cyan/Blue for text bounding boxes
    COLOR_BUBBLE_BOX = (50, 220, 100)    # Soft Green for speech bubbles
    COLOR_FREE_TEXT = (255, 80, 140)     # Magenta for free-floating text/SFX
    COLOR_BADGE_BG = (20, 20, 30, 230)   # Dark background for reading order badge
    COLOR_BADGE_TEXT = (255, 255, 255)   # White badge text

    def __init__(self, font_size: int = 18):
        self.font_size = font_size
        self._font = None
        self._init_font()

    def _init_font(self):
        try:
            # Try to load standard Windows font
            self._font = ImageFont.truetype("arialbd.ttf", self.font_size)
        except Exception:
            try:
                self._font = ImageFont.truetype("DejaVuSans-Bold.ttf", self.font_size)
            except Exception:
                self._font = ImageFont.load_default()

    @staticmethod
    def draw_mask_outline(
        image: Image.Image,
        mask: np.ndarray,
        origin: Tuple[int, int],
        color: Tuple[int, int, int],
        line_width: int = 2,
        opacity: int = 210,
    ) -> None:
        """Draw the boundary of a binary segmentation mask onto an RGBA image."""
        mask_array = np.asarray(mask)
        if mask_array.ndim != 2:
            raise ValueError("Segmentation masks must be two-dimensional.")

        mask_image = Image.fromarray((mask_array > 0.5).astype(np.uint8) * 255, mode="L")
        eroded = mask_image.filter(ImageFilter.MinFilter(3))
        outline = ImageChops.subtract(mask_image, eroded)
        if line_width > 1:
            filter_size = line_width if line_width % 2 else line_width + 1
            outline = outline.filter(ImageFilter.MaxFilter(filter_size))

        overlay = Image.new("RGBA", outline.size, color + (0,))
        overlay.putalpha(outline.point(lambda value: value * opacity // 255))
        image.alpha_composite(overlay, dest=origin)

    def draw_annotations(
        self,
        image: Image.Image,
        detections: List[DetectionBox],
        panels: Optional[List[PanelRegion]] = None,
        draw_bubbles: bool = True,
        draw_panel_frames: bool = False,
        draw_text_boxes: bool = True,
        line_width: int = 3,
    ) -> Image.Image:
        """Draw panel frames, speech-bubble contours, text boxes, and reading-order badges."""
        img = image.convert("RGBA")
        draw = ImageDraw.Draw(img)

        if draw_panel_frames and panels:
            for panel in panels:
                x1, y1, x2, y2 = panel.bbox
                draw.rectangle(
                    [(x1, y1), (x2, y2)],
                    outline=(255, 190, 40, 255),
                    width=line_width,
                )
                label = f"P{panel.id}"
                if hasattr(draw, "textbbox"):
                    label_bbox = draw.textbbox((0, 0), label, font=self._font)
                else:
                    label_width, label_height = draw.textsize(label, font=self._font)
                    label_bbox = (0, 0, label_width, label_height)
                label_width = label_bbox[2] - label_bbox[0]
                label_height = label_bbox[3] - label_bbox[1]
                padding = 4
                draw.rectangle(
                    [
                        (x1, y1),
                        (
                            min(x2, x1 + label_width + padding * 2),
                            min(y2, y1 + label_height + padding * 2),
                        ),
                    ],
                    fill=(255, 190, 40, 255),
                )
                draw.text(
                    (
                        x1 + padding - label_bbox[0],
                        y1 + padding - label_bbox[1],
                    ),
                    label,
                    fill=(20, 20, 20, 255),
                    font=self._font,
                )

        # Draw text boxes and bubble contours with reading-order badges.
        for det in detections:
            if det.label != "bubble" and (det.is_bubble or not draw_text_boxes):
                continue

            x1, y1, x2, y2 = det.bbox
            if det.label == "bubble":
                color = self.COLOR_BUBBLE_BOX
            else:
                color = self.COLOR_TEXT_BOX if det.is_bubble else self.COLOR_FREE_TEXT

            if det.label == "bubble" and draw_bubbles and det.bubble_mask is not None:
                self.draw_mask_outline(
                    img,
                    det.bubble_mask,
                    det.bubble_mask_origin or (x1, y1),
                    color,
                    line_width=max(1, line_width - 1),
                    opacity=210,
                )
            elif det.label != "bubble" and draw_text_boxes:
                draw.rectangle([(x1, y1), (x2, y2)], outline=color + (255,), width=line_width)

            # Badge with reading order number
            badge_text = str(det.reading_order)
            badge_r = max(12, int(self.font_size * 0.8))
            bx = x1 + badge_r + 2
            by = y1 + badge_r + 2

            # Badge circle
            draw.ellipse(
                [(bx - badge_r, by - badge_r), (bx + badge_r, by + badge_r)],
                fill=color + (240,),
                outline=(255, 255, 255, 255),
                width=1,
            )

            # Text inside badge
            if hasattr(draw, "textbbox"):
                bbox = draw.textbbox((0, 0), badge_text, font=self._font)
                tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            else:
                tw, th = draw.textsize(badge_text, font=self._font)

            draw.text(
                (bx - tw / 2, by - th / 2 - 1),
                badge_text,
                fill=(255, 255, 255, 255),
                font=self._font,
            )

        return img.convert("RGB")

    def create_side_by_side(
        self,
        orig_image: Image.Image,
        annotated_image: Image.Image,
    ) -> Image.Image:
        """Create a side-by-side comparison image."""
        w1, h1 = orig_image.size
        w2, h2 = annotated_image.size

        total_w = w1 + w2 + 10
        max_h = max(h1, h2)

        canvas = Image.new("RGB", (total_w, max_h), (25, 25, 30))
        canvas.paste(orig_image, (0, 0))
        canvas.paste(annotated_image, (w1 + 10, 0))
        return canvas

    @staticmethod
    def to_base64(image: Image.Image, format: str = "JPEG", quality: int = 90) -> str:
        """Convert a PIL Image to a base64 encoded data URI."""
        buffered = io.BytesIO()
        image.save(buffered, format=format, quality=quality)
        b64_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
        return f"data:image/{format.lower()};base64,{b64_str}"
