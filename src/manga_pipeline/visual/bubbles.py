"""Deterministic shape/border evidence; never semantic ground truth."""
import math
import numpy as np
from .geometry import clipped_box


def unknown_geometry(reason):
    return {"bubble_shape":"unknown", "border_style":"unknown", "bubble_style":"unknown",
            "confidence":0.0, "confidence_kind":"heuristic_not_calibrated",
            "source":"geometry_rules_v1", "style_hints":[], "evidence":{"reason":reason}}


def classify_bubble(contour, pixels):
    from shapely.geometry import Polygon
    result = unknown_geometry("insufficient_contour")
    if len(contour) < 4:
        return result
    poly = Polygon(contour)
    if not poly.is_valid or poly.area < 64 or poly.length <= 0:
        return result
    solidity = poly.area / max(poly.convex_hull.area,1)
    rectangularity = poly.area / max(poly.minimum_rotated_rectangle.area,1)
    circularity = 4*math.pi*poly.area/poly.length**2
    clean = poly.simplify(max(1.5, math.sqrt(poly.area)*0.015), preserve_topology=True)
    points = np.asarray(list(clean.exterior.coords)[:-1],dtype=float)
    acute = 0
    for i,p in enumerate(points):
        a,b = points[i-1]-p,points[(i+1)%len(points)]-p
        denom = np.linalg.norm(a)*np.linalg.norm(b)
        if denom and math.degrees(math.acos(float(np.clip(np.dot(a,b)/denom,-1,1)))) < 65:
            acute += 1
    if rectangularity >= 0.93 and solidity >= 0.97:
        shape,confidence = "rectangular",0.85
    elif acute >= 3 and solidity < 0.92:
        shape,confidence = "spiky",0.75
    elif solidity >= 0.94 and circularity >= 0.65:
        shape,confidence = "rounded",0.80
    elif 0.80 <= solidity < 0.94 and acute == 0 and len(points) >= 12:
        shape,confidence = "cloud",0.60
    else:
        shape,confidence = "irregular",0.40
    dark = []
    h,w = pixels.shape[:2]
    for i, start in enumerate(contour):
        end = contour[(i+1)%len(contour)]
        for f in np.linspace(0,1,max(1,int(math.dist(start,end)/3)),endpoint=False):
            x,y = np.rint(np.asarray(start)*(1-f)+np.asarray(end)*f).astype(int)
            crop = pixels[max(0,y-2):min(h,y+3),max(0,x-2):min(w,x+3)]
            if crop.size:
                dark.append(bool((crop.mean(axis=2)<100).any()))
    border = "unknown"
    support = float(np.mean(dark)) if dark else 0.0
    transitions = sum(a != b for a,b in zip(dark,dark[1:]+dark[:1])) if dark else 0
    if len(dark) >= 20:
        if support >= 0.90:
            border = "solid"
        elif 0.25 <= support <= 0.80 and transitions >= 8:
            runs, count = [], 1
            for a,b in zip(dark,dark[1:]):
                if a == b:
                    count += 1
                else:
                    runs.append(count)
                    count = 1
            if runs and np.std(runs)/max(1,np.mean(runs)) < 0.8:
                border = "dashed"
    hints = []
    if shape == "spiky": hints.append("shout")
    if shape == "cloud": hints.append("thought")
    if shape == "rectangular": hints.append("caption")
    if border == "dashed": hints.append("whisper")
    style = hints[0]+"_hint" if len(hints) == 1 else ("normal" if shape == "rounded" and border == "solid" else "unknown")
    result.update(bubble_shape=shape,border_style=border,bubble_style=style,
                  confidence=confidence,style_hints=hints,
                  evidence={"solidity":round(solidity,4),"rectangularity":round(rectangularity,4),
                            "circularity":round(circularity,4),"acute_corners":acute,
                            "border_dark_support":round(support,4),"border_transitions":transitions})
    return result


def match_bubble(box, bubbles, masks, config):
    result = {"bubble_id":None, "bubble_bbox":None, "bubble_mapping":{
        "source":"mask_coverage_v1","coverage":0.0,"reason":"no_confident_match"},
        "bubble_geometry":unknown_geometry("unmatched_text")}
    if not masks:
        return result
    h,w = masks[0].shape
    x1,y1,x2,y2 = clipped_box(box,(w,h))
    if x2<=x1 or y2<=y1:
        result["bubble_mapping"]["reason"] = "empty_text_box"
        return result
    coverage = sorted([(float(mask[y1:y2,x1:x2].mean()),i) for i,mask in enumerate(masks)],reverse=True)
    best,index = coverage[0]
    second = coverage[1][0] if len(coverage)>1 else 0
    result["bubble_mapping"]["coverage"] = round(best,4)
    if best < config["mapping_coverage"]:
        return result
    if second >= config["mapping_coverage"] and best-second < config["mapping_margin"]:
        result["bubble_mapping"]["reason"] = "ambiguous_overlap"
        return result
    bubble = bubbles[index]
    result.update(bubble_id=bubble["id"],bubble_bbox=bubble["bbox"],bubble_geometry=bubble["geometry"])
    result["bubble_mapping"].update(reason="matched",confidence=round(best,4))
    return result
