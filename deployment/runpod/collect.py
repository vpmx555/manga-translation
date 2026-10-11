"""Download completed chapters while the GPU continues; never terminate a pod."""
import argparse
import json
from pathlib import Path, PurePosixPath
import sys
import time

import sync


def verify_local(folder, entry, record):
    sys.path.insert(0, str(sync.PROJECT / "src"))
    from manga_pipeline.storage.runs import RunStore
    from manga_pipeline.storage.io import read_json
    directory = folder / PurePosixPath(record["run_dir"].replace("\\", "/")).name
    store = RunStore(directory)
    if store.manifest.get("pipeline_version") == 6:
        from v6_contract import verify_v6
        return verify_v6(store, entry, allow_partial=record.get("status") != "completed")
    with store.verification_pass():
        if not all(store.completed(step) for step in store.steps):
            raise RuntimeError("Downloaded chapter has incomplete/corrupt artifacts")
        raw, exported, translated = store.read("extract"), store.read("export"), store.read("translate")
    if raw.get("panel_source") != "koharu" or len(raw["pages"]) != entry["pages"]:
        raise RuntimeError("Downloaded chapter page count or provenance mismatch")
    fields = ("speech_style", "speech_style_evidence", "speech_style_source", "speech_style_status", "source_boxes")
    if any(any(key not in row for key in fields) for row in exported["utterances"]):
        raise RuntimeError("Downloaded export lost semantic metadata")
    revision = read_json(directory / "translations/vi" / PurePosixPath(translated["directory"].replace("\\", "/")).name / "result.json")
    count = len(exported["utterances"]) + len(exported.get("translation_only", []))
    if revision.get("failures") or any(len(revision["styles"].get(style, [])) != count for style in ("natural", "localized")):
        raise RuntimeError("Downloaded translation coverage incomplete")
    if not (directory / "review.md").is_file():
        raise RuntimeError("Downloaded chapter review is missing")
    return {"run": directory.name, "pages": len(raw["pages"]), "translations_per_style": count}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--outputs", required=True, type=Path)
    parser.add_argument("--banks", required=True, type=Path)
    parser.add_argument("--audit", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--poll", type=int, default=30)
    args = parser.parse_args(argv)
    if not 10 <= args.poll <= 120:
        parser.error("Poll must be 10..120 seconds")
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    batch = plan["batch"]
    if not batch or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in batch):
        raise ValueError("Unsafe batch name")
    connection = sync.load_connection()
    if plan.get("pod_id") != connection.get("pod_id"):
        raise RuntimeError("Collector connection is not the plan's pod")
    # Use the existing atomic JSON writer; output folders are never overwritten.
    import manage
    local = manage.read_json(args.state, {"batch": batch, "pod_id": connection["pod_id"], "chapters": []})
    if local["batch"] != batch or local["pod_id"] != connection["pod_id"]:
        raise RuntimeError("Collector state belongs to another task")
    script = """
from pathlib import Path
import json,sys
sys.path.insert(0,'/workspace/manga-translate-v2/deployment/runpod')
import manage
directory=manage.RUNTIME/'jobs'/sys.argv[1]
state=manage.read_json(directory/'state.json',{})
launch=manage.read_json(directory/'launch.json',{})
identity=state.get('pid') or launch.get('identity')
if identity and manage.service_identity(identity['pid']) != identity:
    if not state:
        state={'plan':manage.read_json(directory/'plan.json'), 'chapters':[], 'status':'failed',
               'error':'Driver exited before committing batch state; inspect driver.log'}
    elif state.get('status') == 'running':
        state.update(status='interrupted',error='Driver exited; chapter writer state requires investigation')
print(json.dumps(state))
"""
    try:
        local.update(status="collecting")
        manage.atomic_json(args.state, local)
        while True:
            remote = json.loads(sync.remote(connection, ["python3", "-c", script, batch], capture=True))
            if remote and remote.get("plan") != plan:
                raise RuntimeError("Remote collection plan changed")
            records = remote.get("chapters", [])
            for index, entry in enumerate(plan["chapters"]):
                if index >= len(records):
                    continue
                record = records[index]
                v6 = plan.get("pipeline_version") == 6
                if record.get("status") != "completed" and not (
                        v6 and record.get("status") in {"failed", "interrupted"}
                        and record.get("writer_stopped") and record.get("run_dir")):
                    continue
                if any(row["input"] == entry["input"] for row in local["chapters"]):
                    continue
                sync.fetch_data(connection, "outputs", args.outputs, story=entry["story_key"], chapter=entry["chapter"])
                try:
                    summary = verify_local(args.outputs / entry["story_key"] / entry["chapter"], entry, record)
                except Exception as exc:
                    if not v6:
                        raise
                    # Rescue all stopped output first. A source artifact defect is
                    # separate from the transfer inventory/checksums already verified.
                    summary = {"run": PurePosixPath(record["run_dir"]).name,
                               "coverage_complete": False, "artifact_error": str(exc)}
                local["chapters"].append({"input": entry["input"], "remote_status": record.get("status"), **summary})
                manage.atomic_json(args.state, local)
                print("VERIFIED_CHAPTER " + json.dumps(local["chapters"][-1]), flush=True)
            if remote.get("status") in {"failed", "interrupted"} and plan.get("pipeline_version") != 6:
                raise RuntimeError("Remote batch " + remote["status"] + ": " + str(remote.get("error")))
            if remote.get("status") in {"completed", "completed_with_errors", "failed", "interrupted"}:
                if remote.get("status") == "completed" and len(local["chapters"]) != len(plan["chapters"]):
                    raise RuntimeError("Completed batch is missing downloaded chapters")
                if any(r.get("status") == "running" or r.get("writer_stopped") is False for r in records):
                    raise RuntimeError("A chapter writer may still be active; do not archive mutable banks")
                break
            time.sleep(args.poll)
        sync.fetch_data(connection, "banks", args.banks)
        sync.fetch_data(connection, "audit", args.audit)
        incomplete = len(local["chapters"]) != len(plan["chapters"]) or any(
            row.get("remote_status") != "completed" or row.get("coverage_complete") is False
            for row in local["chapters"])
        local.update(status="verified_partial" if incomplete else "verified", remote_status=remote.get("status"),
                     transfer_integrity_verified=True,
                     artifact_integrity_verified=not any(r.get("artifact_error") for r in local["chapters"]),
                     missing_chapters=[e["input"] for e in plan["chapters"]
                                       if not any(r["input"] == e["input"] for r in local["chapters"])],
                     outputs=str(args.outputs.resolve()), banks=str(args.banks.resolve()),
                     audit=str(args.audit.resolve()), finished_at=time.time())
        manage.atomic_json(args.state, local)
        print("COLLECTION_VERIFIED_PARTIAL" if incomplete else "COLLECTION_VERIFIED", flush=True)
        return 0
    except BaseException as exc:
        local.update(status="failed", error=str(exc), updated_at=time.time())
        manage.atomic_json(args.state, local)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
