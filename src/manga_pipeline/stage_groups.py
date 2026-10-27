"""Presentation groups only; storage checkpoints remain independent."""
def stage_group(step):
    if step in {"layout", "extract", "bubble_geometry"}:
        return "Extraction"
    if step in {"normalize", "graph", "analyze", "reanalyze"}:
        return "Analysis"
    return "Output"
