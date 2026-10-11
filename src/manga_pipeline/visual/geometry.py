"""Pure geometry and lossless mask helpers shared by both visual branches."""
import math
from pathlib import Path
import numpy as np
from PIL import Image
from ..extraction.document import _box, panel_for_box
from ..storage.io import atomic_bytes, file_hash


def clipped_box(box, size):
    x1, y1, x2, y2 = _box(box)
    w, h = size
    return [max(0, min(w, math.floor(x1))), max(0, min(h, math.floor(y1))),
            max(0, min(w, math.ceil(x2))), max(0, min(h, math.ceil(y2)))]


def ordered_boxes(records):
    """Rows top to bottom, right to left; do not let tall boxes merge distant rows."""
    rows = []
    for record in sorted(records, key=lambda r: (r["bbox"][1], -r["bbox"][2])):
        b = record["bbox"]
        tolerance = max(8, min(40, (b[3] - b[1]) * 0.2))
        row = next((r for r in rows if abs(r[0]["bbox"][1] - b[1]) <= tolerance), None)
        if row is None:
            rows.append([record])
        else:
            row.append(record)
    return [item for row in rows for item in sorted(row, key=lambda r: (-r["bbox"][2], r["bbox"][1]))]


def suppress_duplicates(records, threshold=0.85):
    kept = []
    for r in sorted(records, key=lambda r: -r.get("confidence", 1)):
        x1, y1, x2, y2 = r["bbox"]
        a = max(0, x2-x1) * max(0, y2-y1)
        duplicate = False
        for k in kept:
            b = k["bbox"]
            inter = max(0, min(x2,b[2])-max(x1,b[0])) * max(0,min(y2,b[3])-max(y1,b[1]))
            area_b = max(0,b[2]-b[0]) * max(0,b[3]-b[1])
            if inter / max(1, a+area_b-inter) >= threshold:
                duplicate = True
                break
        if not duplicate and a > 0:
            kept.append(r)
    return kept


def mask_contour(mask):
    """Trace pixel-cell boundaries; retain the largest outer contour (holes omitted).

    Grid edges are traced before polygon simplification, preserving concavities.
    No OpenCV dependency is needed in the MAGI environment.
    """
    from shapely.geometry import Polygon
    mask = np.asarray(mask, dtype=bool)
    if mask.ndim != 2 or not mask.any():
        return []
    padded = np.pad(mask, 1)
    boundaries = [mask & ~padded[:-2,1:-1], mask & ~padded[1:-1,2:],
                  mask & ~padded[2:,1:-1], mask & ~padded[1:-1,:-2]]
    edges = {}
    for side, pixels in enumerate(boundaries):
        ys, xs = np.nonzero(pixels)
        for x,y in zip(xs.tolist(),ys.tolist()):
            corners = [(x,y),(x+1,y),(x+1,y+1),(x,y+1)]
            start, end = corners[side], corners[(side+1)%4]
            edges.setdefault(start, []).append(end)
    polygons = []
    while edges:
        first = next(iter(edges))
        current, contour = first, [first]
        while current in edges:
            following = edges[current].pop()
            if not edges[current]:
                del edges[current]
            contour.append(following)
            current = following
            if current == first:
                break
        if current == first and len(contour) >= 4:
            poly = Polygon(contour)
            if poly.is_valid and poly.area > 0:
                polygons.append(poly)
    if not polygons:
        return []
    poly = max(polygons, key=lambda x:x.area).simplify(1.0, preserve_topology=True)
    return [[float(x),float(y)] for x,y in list(poly.exterior.coords)[:-1]]


def write_png(path, image):
    import io
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    atomic_bytes(path, stream.getvalue())


def read_mask(root, record, size):
    rel = Path(record["mask_path"])
    path = (Path(root) / rel).resolve()
    if rel.is_absolute() or not path.is_relative_to(Path(root).resolve()):
        raise ValueError("Mask path escapes run")
    if file_hash(path) != record["mask_sha256"]:
        raise ValueError(f"Bubble mask changed/corrupt: {path}")
    with Image.open(path) as im:
        if im.size != tuple(size):
            raise ValueError("Bubble mask dimensions differ from source page")
        return np.asarray(im.convert("L")) > 0
