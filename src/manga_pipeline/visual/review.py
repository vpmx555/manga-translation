"""Cheap review artifacts; no inference or edits to source images."""
from PIL import ImageDraw


def layout_overlay(image, page):
    result = image.copy()
    draw = ImageDraw.Draw(result)
    for i, panel in enumerate(page["panels"]):
        draw.rectangle(panel["bbox"], outline="blue", width=3)
        draw.text(tuple(panel["bbox"][:2]), f"panel {i}", fill="blue")
    for bubble in page["bubbles"]:
        points = [tuple(p) for p in bubble.get("contour", [])]
        if len(points) >= 3:
            draw.line(points+[points[0]], fill="red", width=2)
        draw.text(tuple(bubble["bbox"][:2]), bubble["id"], fill="red")
    return result
