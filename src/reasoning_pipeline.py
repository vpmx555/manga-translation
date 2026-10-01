"""Chapter-level orchestration without loading MAGI or modifying its outputs."""
from __future__ import annotations

import copy
import hashlib
import json
import time
from dataclasses import asdict
from pathlib import Path

from dialogue_data import document_fingerprint, read_json
from dialogue_reconstruction import reconstruct_dialogue
from reasoning_batching import Chapter
from reasoning_client import OllamaReasoner, atomic_json
from reasoning_schemas import VERSION
from scene_reasoning import segment_scenes, visual_observations


def load_chapter(input_path: Path, *, raw_path: Path | None = None, bank_path: Path | None = None,
                 images_dir: Path | None = None, text_only: bool = False) -> Chapter:
    document = read_json(input_path)
    if raw_path is None and document.get("raw_json_path"):
        raw_path = Path(document["raw_json_path"])
        if not raw_path.is_absolute():
            raw_path = input_path.resolve().parent / raw_path
    if raw_path is None and input_path.name.endswith(".normalized.json"):
        candidate = input_path.with_name(input_path.name[:-len(".normalized.json")] + ".raw.json")
        if candidate.exists():
            raw_path = candidate
    raw = read_json(raw_path) if raw_path is not None else None
    bank = read_json(bank_path) if bank_path is not None else None
    return Chapter(document, raw, bank=bank, images_dir=images_dir, text_only=text_only)


def pipeline_plan(chapter: Chapter, config, *, scenes_only: bool = False, scene_source: str = "predicted",
                  correct_order: bool = True) -> dict:
    stages = (["visual"] if not chapter.text_only else []) + [
        {"predicted": "boundaries", "none": "no_scene_partition", "gold": "gold_scene_partition"}[scene_source]]
    if not scenes_only:
        stages += ["3A", "3B", "3C"] + (["3D", "one_local_rerun"] if correct_order else []) + ["validation"]
    return {"schema_version": 1, "kind": "reasoning_plan", "document_id": chapter.document["document_id"],
            "source_fingerprint": chapter.source_fingerprint, "config": asdict(config),
            "utterances": len(chapter.rows), "source_pages": len(chapter.pages),
            "visual_targets_including_silent_panels": 0 if chapter.text_only else len(chapter.blocks),
            "boundary_targets": max(0, len(chapter.rows) - 1) if scene_source == "predicted" else 0,
            "identity_candidates": len(chapter.profiles), "text_only_ablation": chapter.text_only,
            "stages": stages,
            "scene_source": scene_source, "schema_version_reasoning": VERSION,
            "notes": ["No inference or model download performed by dry-run.",
                      "Requests split context as well as targets; actual budgets are checked at inference time."]}


def no_scene_partition(chapter, observations: dict) -> dict:
    output = copy.deepcopy(chapter.document)
    output["utterances"] = copy.deepcopy(chapter.rows)
    output.update({"kind": "scene_dialogue", "source_fingerprint": chapter.source_fingerprint,
                   "visual_observations": observations, "scene_boundaries": [], "reasoning_warnings": [],
                   "scene_config": {"method": "no_scene_ablation", "text_only_ablation": chapter.text_only}})
    for index, row in enumerate(output["utterances"]):
        row.update({"scene_id": f"{output['document_id']}:chapter", "scene_status": "not_run",
                    "order_in_scene": index, "original_order": index, "corrected_order": index})
    return output


def gold_partition(chapter, observations: dict, reference: dict) -> dict:
    from reasoning_evaluation import validate_reference
    validate_reference(chapter.document, reference)
    if any(row["boundary"] is None for row in reference["boundaries"]):
        raise ValueError("Gold-scene oracle requires a complete scene boundary reference")
    output = no_scene_partition(chapter, observations)
    output["scene_config"]["method"] = "gold_scene_oracle"
    output["scene_boundaries"] = copy.deepcopy(reference["boundaries"])
    current, order = 1, 0
    for index, row in enumerate(output["utterances"]):
        if index and reference["boundaries"][index - 1]["boundary"]:
            current, order = current + 1, 0
        row.update({"scene_id": f"{output['document_id']}:s{current:04d}", "scene_status": "confirmed",
                    "order_in_scene": order})
        order += 1
    return output


def run_reasoning(chapter: Chapter, reasoner, *, scenes_only: bool = False, correct_order: bool = True,
                  scene_source: str = "predicted", reference: dict | None = None,
                  checkpoint_dir: Path | None = None, step_callback=None) -> dict:
    if scene_source not in ("predicted", "none", "gold"):
        raise ValueError("Unsupported scene source")
    if scene_source == "gold" and reference is None:
        raise ValueError("Gold-scene oracle needs confirmed independent reference labels")
    started = time.perf_counter()
    reasoner.prepare()
    manifest = {"schema_version": 1, "source_fingerprint": chapter.source_fingerprint,
                "raw_fingerprint": chapter.raw_fingerprint, "bank_fingerprint": chapter.bank_fingerprint,
                "text_only_ablation": chapter.text_only,
                "model": reasoner.config.model, "model_digest": reasoner.digest,
                "config": asdict(reasoner.config), "schema_prompt_version": VERSION,
                "scene_source": scene_source, "correct_order": correct_order, "scenes_only": scenes_only,
                "source_images": {page_id: hashlib.sha256(Path(p["image_path"]).read_bytes()).hexdigest()
                                  for page_id, p in chapter.pages.items() if not chapter.text_only}}
    run_id = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    run_dir = checkpoint_dir / run_id if checkpoint_dir is not None else None
    def checkpoint(stage, artifact=None):
        manifest["completed_stage"] = stage
        manifest["events"] = reasoner.events
        if step_callback is not None:
            step_callback(stage, copy.deepcopy(artifact if artifact is not None else manifest))
        if run_dir is not None:
            if artifact is not None:
                atomic_json(run_dir / (stage + ".json"), artifact)
            atomic_json(run_dir / "manifest.json", manifest)
    try:
        observations = visual_observations(chapter, reasoner)
        checkpoint("visual", {"observations": observations})
        if scene_source == "predicted":
            scenes = segment_scenes(chapter, reasoner, observations=observations)
        elif scene_source == "gold":
            scenes = gold_partition(chapter, observations, reference)
        else:
            scenes = no_scene_partition(chapter, observations)
        checkpoint("scenes", scenes)
        output = scenes if scenes_only else reconstruct_dialogue(chapter, scenes, reasoner, correct_order=correct_order,
                                                                 step_callback=step_callback)
        output["reasoning_run"] = {**manifest, "run_id": run_id,
                                  "elapsed_seconds": time.perf_counter() - started,
                                  "cache_hits": sum(e.get("cache_hit", False) for e in reasoner.events),
                                  "requests": sum(not e.get("cache_hit", False) for e in reasoner.events),
                                  "server_memory_samples_not_peak": reasoner.memory_samples,
                                  "input_tokens_current_run": sum((e.get("usage", {}).get("prompt_eval_count") or 0)
                                                                  for e in reasoner.events if not e.get("cache_hit")),
                                  "output_tokens_current_run": sum((e.get("usage", {}).get("eval_count") or 0)
                                                                   for e in reasoner.events if not e.get("cache_hit")),
                                  "limits": ["Image-token budget is an estimate; actual usage is checked.",
                                             "Model predictions and names require review; bank is never written."]}
        checkpoint("complete", output)
        return output
    except Exception as exc:
        manifest["error"] = str(exc)
        checkpoint("failed")
        raise
    finally:
        try:
            reasoner.close()
        except RuntimeError:
            # Inference/checkpoint results remain valid when the server cannot unload.
            if run_dir is not None:
                manifest["unload_warning"] = "Could not unload the selected model"
                atomic_json(run_dir / "manifest.json", manifest)
