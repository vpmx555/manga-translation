"""Isolated GPU workers: RF-DETR/Transformers 5 never imports MAGI."""
import sys
from pathlib import Path
from ..storage.io import atomic_json
from ..storage.runs import RunStore


def main(stage, manifest):
    store = RunStore(Path(manifest).parent)
    if stage == "layout":
        from .stages import layout
        output = layout(store)
    elif stage == "inpaint":
        from .inpaint import inpaint
        output = inpaint(store)
    else:
        raise ValueError("Unsupported visual worker stage")
    atomic_json(store.artifact_path(stage).with_name("worker_result.json"), output)


if __name__ == "__main__":
    stage, manifest = sys.argv[1:3]
    try:
        main(stage, manifest)
    except Exception as exc:
        store = RunStore(Path(manifest).parent)
        atomic_json(store.artifact_path(stage).with_name("worker_error.json"), {"error": str(exc)})
        raise
