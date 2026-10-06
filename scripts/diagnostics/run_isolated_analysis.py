"""Exercise the five-step flow using completed extraction and a private bank copy."""
import argparse
import copy
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from manga_pipeline.pipeline import Pipeline, load_config
from manga_pipeline.progress import NullProgress, TerminalProgress
from manga_pipeline.providers.ollama import Ollama
from manga_pipeline.review.renderer import reference
from manga_pipeline.storage.io import atomic_json, file_hash, read_json
from manga_pipeline.storage.locking import exclusive_lock
from manga_pipeline.storage.runs import RunStore


class RecordingOllama(Ollama):
    def __init__(self, config, cache, report, report_path):
        super().__init__(config, cache)
        self.report, self.report_path, self.task = report, report_path, None

    def infer_once(self, task, *args):
        self.task = task
        return super().infer_once(task, *args)

    def http(self, route, body=None):
        if route != "/api/chat":
            return super().http(route, body)
        record = {"task": self.task, "think": body["think"],
                  "images": sum(len(m.get("images", [])) for m in body["messages"])}
        started = time.perf_counter()
        try:
            value = super().http(route, body)
            record.update({k: value.get(k) for k in ("done_reason", "prompt_eval_count", "eval_count")})
            return value
        except Exception as exc:
            record["error"] = str(exc)
            raise
        finally:
            record["seconds"] = round(time.perf_counter() - started, 2)
            self.report["requests"].append(record)
            atomic_json(self.report_path, self.report)


def hashes(source):
    paths = [source.path, Path(source.manifest["bank_path"])]
    paths.extend(source.artifact_path(step) for step in source.steps if source.completed(step))
    return {str(path): file_hash(path) for path in paths}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path, help="Source run; remains unchanged")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--no-progress", action="store_true")
    args = parser.parse_args()
    source, root = RunStore(args.run_dir), args.output_dir.resolve()
    if args.resume:
        setup = read_json(root / "setup.json")
        if setup["source_run"] != str(source.directory):
            raise ValueError("Resume source changed")
        store, report = RunStore(root / "run"), read_json(root / "report.json")
    else:
        root.mkdir(parents=True, exist_ok=False)
        setup = {"source_run": str(source.directory), "source_hashes": hashes(source)}
        bank = Path(source.manifest["bank_path"])
        with exclusive_lock(bank.parent / ".bank.lock"):
            shutil.copytree(bank.parent, root / "bank", ignore=shutil.ignore_patterns(".bank.lock"))
        config = load_config(args.config)
        if config["dialogue_analysis"] != "essential-v3":
            raise ValueError("Isolated analysis requires essential-v3")
        store = RunStore.create(root / "run", source=copy.deepcopy(source.manifest["source"]),
                                config=config, bank_path=root / "bank" / bank.name)
        store.manifest["diagnostic_source_run"] = str(source.directory)
        visual = source.directory / "01_extract" / "visualizations"
        if visual.is_dir():
            shutil.copytree(visual, store.directory / "01_extract" / "visualizations")
        store.finish("extract", source.read("extract"))
        atomic_json(root / "setup.json", setup)
        report = {"kind": "isolated_analysis_v3", "source_run": str(source.directory),
                  "run_dir": str(store.directory), "bank_path": str(root / "bank" / bank.name), "requests": []}
        atomic_json(root / "report.json", report)
    provider = RecordingOllama(store.manifest["config"]["ollama"], store.directory / "cache", report, root / "report.json")
    before_hashes = hashes(store)
    requests_before = len(report["requests"])
    completed_before = all(store.completed(step) for step in store.steps)
    started = time.perf_counter()
    print(f"Isolated run: {store.directory}", flush=True)
    try:
        success = Pipeline(store, provider=provider, progress=NullProgress() if args.no_progress else TerminalProgress()).run()
        store = RunStore(store.directory)
        report["workflow_completed"] = success
        if success:
            output = store.read("export")
            report["utterances"] = len(output["utterances"])
            report["fallback_targets"] = sum(r["classification_source"] == "fallback" for r in output["utterances"])
            report["warnings"] = len(store.read("analyze")["warnings"])
            report["name_links"] = store.read("analyze")["links"]
            selected = {reference(r): r for r in output["utterances"]
                        if reference(r) in {"P01/T01", "P02/T23", "P03/T06", "P04/T03"}}
            report["cases"] = {key: {field: row.get(field) for field in (
                "text", "content_type", "speaker_id", "addressee_type", "addressee_ids", "mentions", "warnings")}
                for key, row in selected.items()}
            report["checks"] = {"P01/T01_collective": selected.get("P01/T01", {}).get("addressee_type") in {"group", "audience"}}
            for key in ("P02/T23", "P03/T06"):
                report["checks"][key + "_introduction"] = any(m["mention_type"] == "introduction" for m in selected.get(key, {}).get("mentions", []))
            row = selected.get("P04/T03", {})
            report["checks"]["P04/T03_direct_ID1"] = row.get("speaker_id") == 3 and row.get("addressee_ids") == [1] and any(
                m["mention_type"] == "direct_address" and m.get("name_target_id") == 1 for m in row.get("mentions", []))
        return 0 if success else 2
    finally:
        report["last_execution_seconds"] = round(time.perf_counter() - started, 2)
        report["source_unchanged"] = hashes(source) == setup["source_hashes"]
        after_hashes = hashes(RunStore(store.directory))
        changed_paths = sorted(path for path in set(before_hashes) | set(after_hashes)
                               if before_hashes.get(path) != after_hashes.get(path))
        report.setdefault("executions", []).append({
            "resume": args.resume, "completed_before": completed_before,
            "seconds": report["last_execution_seconds"],
            "new_requests": len(report["requests"]) - requests_before,
            "outputs_and_bank_unchanged": all(path == str(store.path) for path in changed_paths),
            "changed_paths": changed_paths,
        })
        atomic_json(root / "report.json", report)
        print(json.dumps({"report": str(root / "report.json"), "completed": report.get("workflow_completed"),
                          "source_unchanged": report["source_unchanged"], "requests": len(report["requests"]),
                          "seconds": report["last_execution_seconds"]}), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
