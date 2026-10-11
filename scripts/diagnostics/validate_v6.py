"""Run offline regressions and retain a concise machine-readable receipt."""
import contextlib
import io
import json
import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "src"))
sys.path.insert(0, str(PROJECT))
from manga_pipeline.storage.io import atomic_json, fingerprint


def main():
    suite = unittest.defaultTestLoader.discover(str(PROJECT / "tests"), top_level_dir=str(PROJECT))
    result = unittest.TestResult()
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        suite.run(result)
    receipt = {"tests_run": result.testsRun, "skipped": [str(t) for t,_ in result.skipped],
        "errors": [{"test":str(t),"trace":trace} for t,trace in result.errors],
        "failures": [{"test":str(t),"trace":trace} for t,trace in result.failures],
        "source_fingerprint": fingerprint({p.relative_to(PROJECT).as_posix(): p.read_text(encoding="utf-8")
            for directory in (PROJECT/"src/manga_pipeline/v6", PROJECT/"tests") for p in directory.rglob("*.py")}),
        "network_model_inference": False, "runpod_resources_used": False}
    destination = PROJECT / "outputs/diagnostics/v6-regression.json"
    atomic_json(destination,receipt)
    print(json.dumps({"tests_run":receipt["tests_run"],"skipped":len(result.skipped),"errors":len(result.errors),
        "failures":len(result.failures),"receipt":str(destination)}),flush=True)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
