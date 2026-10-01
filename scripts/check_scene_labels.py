"""Probe scene label semantics on adjacent real utterances before a long run."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from reasoning_batching import TaskRunner
from reasoning_client import OllamaReasoner, atomic_json, load_config
from reasoning_pipeline import load_chapter
from scene_reasoning import BOUNDARY_PROMPT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--page", type=int, default=5)
    args = parser.parse_args()
    directory = args.run_dir.resolve()
    check = json.loads((directory / "check.json").read_text(encoding="utf-8"))
    chapter = load_chapter(directory / "stage2/dialogue.normalized.json",
                           raw_path=directory / "stage1/transcript.raw.json",
                           images_dir=Path(check["source_folder"]))
    rows = chapter.rows
    selected = [i for i, row in enumerate(rows) if row["page"] == args.page]
    if len(selected) < 4:
        raise ValueError("Probe needs at least four utterances on the selected page")
    # Include adjacent turns in the middle of the page, not an across-page gap.
    starts = selected[3:6]
    gaps = [{"id": f"gap:{rows[i]['id']}->{rows[i+1]['id']}",
             "left_id": rows[i]["id"], "right_id": rows[i+1]["id"], "before_index": i+1}
            for i in starts if i+1 < len(rows) and rows[i+1]["page"] == args.page]
    client = OllamaReasoner(load_config("cpu", ROOT / "configs/reasoning.chapter-cpu.json"),
                            directory / "internal/cache")
    client.prepare()
    try:
        cards = check["steps"]["scenes"]["output"]["visual_observations"]
        result = TaskRunner(client, chapter, cards).execute(
            "boundaries", BOUNDARY_PROMPT, gaps, rows,
            lambda gap: [gap["before_index"]-1, gap["before_index"]])
        output = directory / "internal/scene-label-probe.json"
        atomic_json(output, {"kind": "scene_label_probe", "page": args.page,
                            "results": result, "events": client.events})
        for value in result.values():
            print(value["decision"], value["reason"])
        print(output)
    finally:
        client.unload()


if __name__ == "__main__":
    main()
