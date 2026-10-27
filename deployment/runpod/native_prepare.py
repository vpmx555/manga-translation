"""Offline preparation and integrity helpers for the native working-set controller."""
import argparse
import hashlib
import json
import sys
from pathlib import Path
import tempfile

import manage
import native_transport as sync
from batch_v6 import safe_key

SUPPORTED = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def prepared(plan_path):
    plan = manage.read_json(plan_path)
    if (not plan or plan.get("pipeline_version") != 6 or not safe_key(plan.get("batch"))
            or plan.get("analysis_contract") != "chapter-story-graph-v7"):
        raise ValueError("Invalid prepared v6 plan")
    if manage.file_hash(plan_path.parent / "pipeline-v6.json") != plan["config_sha256"]:
        raise ValueError("Prepared config changed; prepare a new job")
    with tempfile.TemporaryDirectory(prefix="v6-source-") as temporary:
        actual = sync.bundle(Path(temporary) / "code.zip")["version"]
    if actual != plan["source_bundle_hash"]:
        raise ValueError("Source changed after preparation; prepare a new job before renting/launching")
    return plan


def prepare(args):
    if not safe_key(args.batch) or not safe_key(args.story) or not safe_key(args.chapter):
        raise ValueError("Batch/story/chapter must be lowercase ASCII keys")
    source = args.folder.resolve()
    images = sorted(p for p in source.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED)
    if not images:
        raise ValueError("Source contains no supported chapter images")
    if not args.execution_root.startswith("/") or ".." in Path(args.execution_root).parts:
        raise ValueError("Execution root must be an absolute Linux path")
    destination = sync.STATE / "v6" / args.batch
    if destination.exists():
        raise ValueError("Prepared job already exists; choose a new batch ID")
    config = (sync.PROJECT / "configs/pipeline-v6-runpod.json").read_bytes()
    if getattr(args, "gpu_preset", None):
        supplied = json.loads(config)
        required = {"rtx4090": "RTX 4090", "rtx5090": "RTX 5090", "l40s": "L40S"}.get(args.gpu_preset)
        supplied.setdefault("runtime", {}).update(enabled=True, preset=args.gpu_preset, required_gpu=required)
        config = (json.dumps(supplied, ensure_ascii=False, indent=2) + "\n").encode()
    overrides = {name: getattr(args, name, None) for name in ("llm_slots", "story_workers")}
    if any(value is not None for value in overrides.values()):
        supplied = json.loads(config)
        for name in ("llm_slots", "story_workers"):
            if overrides[name] is not None:
                supplied.setdefault("runtime", {}).update(enabled=True, **{name: overrides[name]})
        config = (json.dumps(supplied, ensure_ascii=False, indent=2) + "\n").encode()
    sys.path.insert(0, str(sync.PROJECT / "src"))
    from manga_pipeline.runtime.llm_budget import validate_runpod_profile
    validate_runpod_profile(json.loads(config))
    receipts = {p.name: {"bytes": p.stat().st_size, "sha256": manage.file_hash(p)} for p in images}
    destination.mkdir(parents=True)
    bundle = sync.bundle(destination / "source.zip")
    (destination / "pipeline-v6.json").write_bytes(config)
    plan = {"pipeline_version": 6, "analysis_contract": "chapter-story-graph-v7", "batch": args.batch, "execution_root": args.execution_root,
            "reanalyze_existing": bool(getattr(args, "reanalyze_existing", False)),
            "source_bundle_hash": bundle["version"], "config_sha256": hashlib.sha256(config).hexdigest(),
            "chapters": [{"story": args.story, "story_key": args.story, "chapter": args.chapter,
                          "input": args.story + "-" + args.chapter + "-v6", "pages": len(images),
                          "source_folder": str(source), "images": receipts}]}
    additions = [(args.story, chapter, folder) for chapter, folder in args.additional_chapter]
    additions.extend(getattr(args, "additional_story", []))
    for story, chapter, folder in additions:
        if (not safe_key(story) or not safe_key(chapter)
                or any((e["story_key"], e["chapter"]) == (story, chapter) for e in plan["chapters"])):
            raise ValueError("Invalid or duplicate additional chapter")
        folder = Path(folder).resolve()
        extra = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED)
        if not extra:
            raise ValueError("Additional chapter contains no images")
        plan["chapters"].append({"story": story, "story_key": story,
            "chapter": chapter, "input": story + "-" + chapter + "-v6",
            "pages": len(extra), "source_folder": str(folder),
            "images": {p.name: {"bytes": p.stat().st_size, "sha256": manage.file_hash(p)} for p in extra}})
    until = getattr(args, "until", None)
    if until:
        if until not in {'graph', 'analyze', 'reanalyze', 'build_identity', 'relationship'}:
            raise ValueError('Unsupported semantic stage limit')
        plan["until"] = until
        for entry in plan["chapters"]:
            entry["until"] = until
    manage.atomic_json(destination / "plan.json", plan)
    print(json.dumps({"plan": str(destination / "plan.json"), "pages": len(images),
                      "bytes": sum(p["bytes"] for p in receipts.values()),
                      "status": "prepared_offline", "runpod_accessed": False}, ensure_ascii=False))



def connection_for(pod_id):
    connection = sync.load_connection()
    if not pod_id or connection.get("pod_id") != pod_id:
        raise ValueError("Explicit Pod ID does not match the saved connection")
    return connection

