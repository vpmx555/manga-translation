"""Mask topology and visual-only hints. OCR is used only by map_text()."""
import copy
import math
import hashlib
import numpy as np
from scipy import ndimage as ndi
from PIL import Image

from ..storage.io import file_hash, fingerprint
from ..visual.geometry import clipped_box, mask_contour, write_png
from ..visual.bubbles import classify_bubble, unknown_geometry


def disk(radius):
    y, x = np.ogrid[-radius:radius+1, -radius:radius+1]
    return x*x + y*y <= radius*radius


def bbox(mask):
    ys, xs = np.nonzero(mask)
    return [int(xs.min()), int(ys.min()), int(xs.max()+1), int(ys.max()+1)] if len(xs) else None


def rings(mask):
    """Retain every component and enclosed hole as an explicitly closed ring."""
    result = []
    labels, count = ndi.label(mask)
    for label in range(1, count+1):
        component = labels == label
        outer = mask_contour(component)
        if len(outer) >= 3:
            result.append({"kind": "outer", "points": outer+[outer[0]]})
        holes, number = ndi.label(ndi.binary_fill_holes(component) & ~component)
        for hole in range(1, number+1):
            contour = mask_contour(holes == hole)
            if len(contour) >= 3:
                result.append({"kind": "hole", "points": contour+[contour[0]]})
    return result


def fuse_candidates(candidates, masks, config):
    """Containment/IoU fusion uses masks, preserving all detection receipts."""
    if len(candidates) != len(masks):
        raise ValueError("Candidate/mask counts differ")
    parent = list(range(len(masks)))
    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    areas = [int(mask.sum()) for mask in masks]
    for i, mask in enumerate(masks):
        for j in range(i):
            intersection = int(np.count_nonzero(mask & masks[j]))
            union = areas[i]+areas[j]-intersection
            if (intersection / max(1, union) >= config["mask_merge_iou"] or
                    intersection / max(1, min(areas[i], areas[j])) >= config["mask_containment"]):
                parent[find(i)] = find(j)
    groups = {}
    for i in range(len(masks)):
        groups.setdefault(find(i), []).append(i)
    return [(indices, np.logical_or.reduce([masks[i] for i in indices])) for indices in groups.values()]


def refine_mask(observed, pixels, config):
    """Repair short segmentation gaps only where source pixels support fill."""
    radius = config["repair_radius"]
    if not radius:
        return observed.copy(), np.zeros_like(observed)
    closed = ndi.binary_closing(observed, structure=disk(radius), border_value=0)
    white = pixels[..., :3].mean(axis=2) >= 185
    inferred = closed & ~observed & white
    return observed | inferred, inferred


def partition(mask, tails, config):
    """Wide cores retain connected groups; thin bridges never become bodies."""
    radius = max(2, min(16, round(math.sqrt(int(mask.sum()))*config["neck_width_ratio"])))
    opened = ndi.binary_opening(mask, structure=disk(radius))
    labels, count = ndi.label(opened)
    seeds = []
    for number in range(1, count+1):
        body = labels == number
        if body.sum() >= config["min_body_area"]:
            seeds.append(body)
    if not seeds:
        return [mask.copy()], np.zeros_like(mask), np.zeros_like(mask), []
    residual = mask & ~np.logical_or.reduce(seeds)
    labels, count = ndi.label(residual)
    bridge, tail = np.zeros_like(mask), np.zeros_like(mask)
    edges = []
    for number in range(1, count+1):
        piece = labels == number
        near = ndi.binary_dilation(piece, iterations=2)
        adjacent = [i for i, body in enumerate(seeds) if (near & body).any()]
        if len(adjacent) > 1:
            bridge |= piece
            edges.append({"part_indices": adjacent, "kind": "observed_bridge"})
        elif len(adjacent) == 1:
            anchor = False
            h, w = mask.shape
            for box in tails:
                x1, y1, x2, y2 = clipped_box(box, (w, h))
                anchor |= bool(piece[y1:y2, x1:x2].any())
            if anchor:
                tail |= piece
            else:
                seeds[adjacent[0]] |= piece
        else:
            # An observed disconnected lobe is retained, never silently deleted.
            if piece.sum() >= config["min_body_area"]:
                seeds.append(piece)
            else:
                tail |= piece
    return seeds, bridge, tail, edges


def visual_hint(*, pixels, body_mask, observed_mask, inferred_mask, config):
    """Strict keyword signature: neither OCR, text boxes nor dialogue score."""
    boundary = body_mask & ~ndi.binary_erosion(body_mask)
    inferred_near = ndi.binary_dilation(inferred_mask, iterations=3)
    count = int(boundary.sum())
    observed_boundary = observed_mask & ~ndi.binary_erosion(observed_mask)
    supported = ndi.binary_dilation(observed_boundary, iterations=2)
    fraction = float((boundary & supported & ~inferred_near).sum()/max(1, count))
    evidence = classify_bubble(mask_contour(body_mask), pixels)
    reasons = []
    if 1-fraction > config["max_reconstructed_fraction"]:
        reasons.append("too_much_inferred_boundary")
    if evidence["confidence"] < config["hint_acceptance"]:
        reasons.append("visual_support_below_acceptance")
    hints = evidence.get("style_hints", [])
    # Cloud alone does not establish a thought; solid/round alone does not establish normal.
    if hints == ["thought"]:
        reasons.append("cloud_shape_without_independent_border_evidence")
    if not hints:
        reasons.append("no_distinctive_visual_style")
    if reasons:
        evidence["bubble_style"], evidence["style_hints"] = "unknown", []
    evidence.update(text_used=False, feature_sources=["image_pixels", "geometry_masks"],
                    observed_boundary_fraction=round(fraction, 6), rejection_reasons=reasons,
                    policy="visual-hints-v6.1")
    return evidence


def save_mask(root, directory, name, mask):
    path = directory / (name+".png")
    write_png(path, Image.fromarray(mask.astype(np.uint8)*255))
    return {"mask_path": path.relative_to(root).as_posix(), "mask_sha256": file_hash(path)}


def build_groups(root, directory, page, candidates, masks, pixels, config):
    groups, files = [], {}
    image_hash = hashlib.sha256(pixels.tobytes()).hexdigest()
    fused = []
    for indices, observed in fuse_candidates(candidates, masks, config):
        refined, inferred = refine_mask(observed, pixels, config)
        labels, count = ndi.label(refined)
        large = [labels == i for i in range(1, count+1) if np.count_nonzero(labels == i) >= config["min_body_area"]]
        small = [labels == i for i in range(1, count+1) if np.count_nonzero(labels == i) < config["min_body_area"]]
        # Disconnected large lobes cannot establish a common utterance.
        if len(large) > 1:
            for piece in small:
                distances = [float(ndi.distance_transform_edt(~body)[piece].min()) for body in large]
                nearest = int(np.argmin(distances))
                if distances[nearest] <= max(12, math.sqrt(int(large[nearest].sum()))*.30):
                    large[nearest] |= piece
                else:
                    large.append(piece)
            for component in large:
                fused.append((indices, observed & component, refined & component, inferred & component))
        else:
            fused.append((indices, observed, refined, inferred))
    for number, (indices, observed, refined, inferred) in enumerate(fused, 1):
        if not observed.any():
            continue
        gid = f"{page['id']}:g{number}"
        bodies, bridge, tail, edges = partition(refined, page.get("tails", []), config)
        for edge in edges:
            if np.any(bridge & inferred):
                edge["kind"] = "partly_inferred_bridge"
        group = {"id": gid, "bbox": bbox(refined), "candidate_ids": [candidates[i]["id"] for i in indices],
                 "panel_indices": [], "outer_rings": rings(refined), "parts": [], "connections": edges,
                 "inferred_pixel_count": int(inferred.sum()), "policy": "group-geometry-v6.1"}
        for field, mask in (("observed", observed), ("refined", refined), ("inferred", inferred),
                            ("bridge", bridge), ("tail", tail)):
            record = save_mask(root, directory, f"{number}-{field}", mask)
            group[field+"_mask"] = record
            files[record["mask_path"]] = record["mask_sha256"]
        for index, body in enumerate(bodies, 1):
            if not body.any():
                continue
            part_id = f"{gid}:part{index}"
            record = save_mask(root, directory, f"{number}-body-{index}", body)
            files[record["mask_path"]] = record["mask_sha256"]
            hint = visual_hint(pixels=pixels, body_mask=body, observed_mask=observed,
                               inferred_mask=inferred, config=config)
            group["parts"].append({"id": part_id, "bbox": bbox(body), "body_mask": record,
                "body_rings": rings(body), "visual_evidence": hint,
                "visual_cache_key": fingerprint([image_hash, record["mask_sha256"],
                                                 group["inferred_mask"]["mask_sha256"], config])})
        for index, box in enumerate(page.get("panels", [])):
            h, w = observed.shape
            x1, y1, x2, y2 = clipped_box(box, (w, h))
            if observed[y1:y2, x1:x2].any():
                group["panel_indices"].append(index)
        groups.append(group)
    return groups, files


def map_text(box, groups, masks, coverage=0.80, margin=0.15):
    """Position-only mapping; its output is never a visual classifier feature."""
    if not masks:
        return {"group_id": None, "part_ids": [], "mapping_status": "unmatched"}
    h, w = next(iter(masks.values())).shape
    x1, y1, x2, y2 = clipped_box(box, (w, h))
    if x2 <= x1 or y2 <= y1:
        return {"group_id": None, "part_ids": [], "mapping_status": "empty_box"}
    scores = sorted([(float(masks[g["id"]][y1:y2, x1:x2].mean()), g) for g in groups],
                    key=lambda x: x[0], reverse=True)
    best, group = scores[0]
    if best < coverage or len(scores)>1 and scores[1][0] >= coverage and best-scores[1][0] < margin:
        return {"group_id": None, "part_ids": [], "mapping_status": "ambiguous_or_low_coverage",
                "candidate_groups": [g["id"] for s, g in scores if s >= coverage]}
    parts = [p["id"] for p in group["parts"] if masks[p["id"]][y1:y2, x1:x2].any()]
    return {"group_id": group["id"], "part_ids": parts, "mapping_status": "matched", "coverage": round(best, 6)}
