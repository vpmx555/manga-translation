"""Read-only chapter smoke: text inference on three essential turns, temporary checkpoints."""
import argparse
import copy
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from manga_pipeline.names import stages
from manga_pipeline.normalization.dialogue import normalize_document
from manga_pipeline.pipeline import load_config
from manga_pipeline.providers.ollama import Ollama
from manga_pipeline.providers.names import create_detector
from manga_pipeline.storage.io import atomic_json, read_json
from manga_pipeline.storage.runs import RunStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--focus", default="Madol-san")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--no-retry", action="store_true")
    parser.add_argument("--output", type=Path, help="Save the smoke report as JSON")
    args = parser.parse_args()
    started = time.perf_counter()
    source = RunStore(args.run_dir)
    config = load_config(args.config)
    document = normalize_document(source.read("extract"), essential_only=True,
                                  analysis_policy=config["dialogue_analysis"])
    rows = document["utterances"]
    index = next((i for i, r in enumerate(rows) if args.focus.casefold() in r["text"].casefold()), 0)
    start = max(0, min(index - 2, len(rows) - 3))
    document["utterances"] = copy.deepcopy(rows[start:start + 3])
    for i, row in enumerate(document["utterances"]):
        row["reading_order"] = i
    if args.no_retry:
        config["ollama"]["retries"] = 0
    with tempfile.TemporaryDirectory(prefix="manga-dialogue-smoke-") as root:
        bank = Path(root) / "bank.json"
        atomic_json(bank, read_json(source.manifest["bank_path"]))
        store = RunStore.create(Path(root) / "run", source=source.manifest["source"], config=config, bank_path=bank)
        detector = create_detector(config)
        try:
            scanned = stages.scan(store, document, detector)
        finally:
            if hasattr(detector, "close"):
                detector.close()
        provider = Ollama(config["ollama"], Path(root) / "cache")
        attempts = []
        requests = []
        original_http = provider.http

        def capture(route, body=None):
            if route == "/api/chat":
                requests.append({"think": body.get("think"),
                                 "images": sum(len(m.get("images", [])) for m in body.get("messages", []))})
            response = original_http(route, body)
            if route == "/api/chat":
                try:
                    value = json.loads(response["message"]["content"])
                    attempts.append(value)
                except (ValueError, KeyError, TypeError):
                    attempts.append({"invalid_json": True})
            return response
        provider.http = capture
        try:
            result = stages.classify(store, document, scanned, provider)
        finally:
            provider.close()
        report = {"analysis_policy": document["analysis_policy"], "model": config["ollama"]["model"],
                  "elapsed_seconds": round(time.perf_counter() - started, 2), "requests": requests,
                  "targets": len(document["utterances"]), "completed": len(result["utterances"]),
                          "failures": result["failures"], "results": [
                              {**{k: r[k] for k in ("utterance_id", "content_type", "speaker_id", "addressee_ids", "addressee_type", "evidence")},
                               "names": [{"name": m["name"], "mention_type": m["mention_type"],
                                          "name_target_id": m["name_target_id"]} for m in r["mentions"]]}
                              for r in result["utterances"]]}
        if result["failures"]:
            report["rejected_responses"] = attempts
        if args.output:
            atomic_json(args.output, report)
        print(json.dumps(report, ensure_ascii=True))
        return 2 if result["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
