"""Pinned MAGI hook replacing panels BEFORE text sorting and bank assignment.

MAGI selects panel_bboxes by advanced tensor indexing (an independent tensor).
Replacing that tensor at its module-local sort_panels hook leaves detector tokens,
other boxes and affinity matrices intact. This hook is scoped to the isolated
worker and restored even on failure.
"""
from contextlib import contextmanager


@contextmanager
def koharu_panels(model, pages):
    method = model.predict_detections_and_associations
    function = getattr(method,"__func__",method)
    namespace = function.__globals__
    original = namespace.get("sort_panels")
    if not callable(original):
        raise RuntimeError("Pinned MAGI sort_panels hook missing; unsupported MAGI implementation")
    index = 0

    def replace(panel_tensor):
        nonlocal index
        if index >= len(pages):
            raise ValueError("MAGI requested more panel pages than Koharu supplied")
        boxes = [p["bbox"] for p in pages[index]["panels"]]
        index += 1
        replacement = panel_tensor.new_tensor(boxes).reshape(-1,4)
        panel_tensor.resize_(replacement.shape).copy_(replacement)
        return list(range(len(boxes)))

    namespace["sort_panels"] = replace
    try:
        yield
        if index != len(pages):
            raise ValueError("MAGI did not consume every Koharu panel page")
    finally:
        namespace["sort_panels"] = original
