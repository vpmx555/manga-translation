"""A committed step is authoritative; target commits survive interrupted steps."""
import copy
import math
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

from ..policies import DEFAULT_SPEAKER_POLICY
from .io import atomic_json, file_hash, fingerprint, read_json

# Preserve these exports for callers and receipts from six-step v2 runs.
STEPS = ("extract", "normalize", "scan", "classify", "link", "export")
ANALYZE_STEPS = ("extract", "normalize", "scan", "analyze", "export")
TRANSLATE_STEPS = (*ANALYZE_STEPS, "translate")
VISUAL_STEPS = ("layout", "extract", "bubble_geometry", "normalize", "inpaint", "scan", "analyze", "export", "translate")
V6_STEPS = ("layout", "extract", "bubble_geometry", "normalize", "scan", "analyze", "identity", "export", "inpaint", "translate", "typeset")
ALL_STEPS = tuple(dict.fromkeys((*STEPS, *TRANSLATE_STEPS, *VISUAL_STEPS, *V6_STEPS)))
DIRECTORIES = {name: f"{i:02d}_{name}" for i, name in enumerate(STEPS, 1)}


def now():
    return datetime.now(timezone.utc).isoformat()


class RunStore:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.path = self.directory / "manifest.json"
        self.manifest = read_json(self.path)
        version = self.manifest.get("pipeline_version", 2)
        if version not in {2, 3, 4, 5, 6}:
            raise ValueError("Only pipeline v2/v3/v4/v5/v6 runs can resume")
        self.steps = TRANSLATE_STEPS if version == 4 else (ANALYZE_STEPS if version == 3 else STEPS)
        if version in {5, 6}:
            if not self.manifest["config"].get("layout", {}).get("enabled"):
                raise ValueError("Pipeline v5 requires Koharu layout")
            visual_steps = V6_STEPS if version == 6 else VISUAL_STEPS
            self.steps = tuple(s for s in visual_steps if
                               (s not in {"translate", "typeset"} or self.manifest["config"].get("translation")) and
                               (s != "inpaint" or self.manifest["config"].get("inpaint", {}).get("enabled")))
        self.directories = {name: f"{i:02d}_{name}" for i, name in enumerate(self.steps, 1)}
        self.manifest.setdefault("pipeline_version", version)
        self._step_timers = {}
        self._dirty_steps = set()
        self._verified_artifacts = None
        snapshot = read_json(self.directory / "snapshots" / "config.json")
        if fingerprint(snapshot) != fingerprint(self.manifest["config"]):
            raise ValueError("Run configuration changed; restore it or create a new run")
        if version in {5, 6} and fingerprint(read_json(self.directory / "snapshots/source.json")) != fingerprint(self.manifest["source"]):
            raise ValueError("Run source manifest changed; create a new run")
        self.recover()

    @classmethod
    def create(cls, directory, *, source, config, bank_path):
        config = copy.deepcopy(config)
        if config.get("dialogue_analysis") == "essential-v3":
            config.setdefault("speaker_policy", DEFAULT_SPEAKER_POLICY)
            if config["speaker_policy"] != DEFAULT_SPEAKER_POLICY:
                raise ValueError("speaker_policy must be individual-v1 for new v3 runs")
        directory = Path(directory).resolve()
        directory.mkdir(parents=True, exist_ok=False)
        extra = {}
        if config.get("noise_filter"):
            from ..normalization.noise import POLICY, rules_for
            if config["noise_filter"]["policy"] != POLICY:
                raise ValueError("Unsupported normalization noise policy")
            rules_path = config["noise_filter"].get("rules_path")
            if rules_path is None:
                candidate = Path(bank_path).parent / "normalization_rules.json"
                rules_path = candidate if candidate.is_file() else None
            rules = rules_for(rules_path)
            extra["noise_rules_hash"] = fingerprint(rules)
            atomic_json(directory / "snapshots/noise_rules.json", rules)
        version = (4 if config.get("translation") else 3) if config.get("dialogue_analysis") == "essential-v3" else 2
        if config.get("layout", {}).get("enabled") and version in {3, 4}:
            version = 6 if config.get("v6", {}).get("enabled") else 5
            from ..analysis.speech import POLICY as SPEECH_POLICY
            extra["speech_style_policy"] = SPEECH_POLICY
        atomic_json(directory / "manifest.json", {
            "pipeline_version": version,
            "created_at": now(), "status": "pending",
            "source": source, "config": config, "bank_path": str(Path(bank_path).resolve()),
            "steps": {}, **extra,
        })
        atomic_json(directory / "snapshots" / "config.json", config)
        if version in {5, 6}:
            atomic_json(directory / "snapshots/source.json", source)
        return cls(directory)

    def save(self):
        if self.manifest["pipeline_version"] in {5, 6}:
            from .locking import exclusive_lock
            # Analysis and reconstruction use different branch locks; serialize only
            # manifest commits and merge the other branch's live step state.
            with exclusive_lock(self.directory / ".state.lock"):
                disk = read_json(self.path)
                merged = disk.get("steps", {}).copy()
                for step in self._dirty_steps:
                    merged[step] = self.manifest["steps"][step]
                self.manifest["steps"] = merged
                self.manifest["status"] = self.overall_status()
                self.manifest["updated_at"] = now()
                atomic_json(self.path, self.manifest)
                self._dirty_steps.clear()
            return
        self.manifest["updated_at"] = now()
        atomic_json(self.path, self.manifest)

    def dependencies(self, step):
        if step not in self.steps:
            raise ValueError(f"Step {step} is not in this run")
        if self.manifest["pipeline_version"] not in {5, 6}:
            return self.steps[:self.steps.index(step)]
        if self.manifest["pipeline_version"] == 6:
            parents = {"layout": (), "extract": ("layout",), "bubble_geometry": ("layout", "extract"),
                "scan": ("normalize",), "analyze": ("scan", "normalize", "extract"),
                "normalize": ("bubble_geometry",), "inpaint": ("export", "bubble_geometry"),
                "identity": ("analyze", "normalize", "extract"), "export": ("identity", "analyze", "normalize"),
                "translate": ("export",), "typeset": ("translate", "normalize", "bubble_geometry")}
            if step == "typeset" and "inpaint" in self.steps:
                return (*parents[step], "inpaint")
            return parents[step]
        return {"layout": (), "extract": ("layout",), "bubble_geometry": ("layout", "extract"),
                "normalize": ("bubble_geometry",), "inpaint": ("normalize", "bubble_geometry"),
                "scan": ("normalize",), "analyze": ("scan", "normalize", "bubble_geometry"),
                "export": ("analyze", "normalize"), "translate": ("export",)}[step]

    def ancestors(self, step):
        needed = {step}
        def visit(s):
            for parent in self.dependencies(s):
                if parent not in needed:
                    needed.add(parent)
                    visit(parent)
        visit(step)
        return tuple(s for s in self.steps if s in needed)

    def overall_status(self):
        statuses = [self.manifest["steps"].get(s, {}).get("status", "pending") for s in self.steps]
        if all(s == "completed" for s in statuses):
            return "completed"
        if "running" in statuses:
            return "running"
        if any(s in {"failed", "partial"} for s in statuses):
            return "partial"
        if "interrupted" in statuses:
            return "interrupted"
        return "pending"

    @contextmanager
    def verification_pass(self):
        """Deduplicate DAG checks within a read-only, locked snapshot only.

        No result survives the outer pass: subsequent public reads still detect
        changed artifacts and masks. Callers grouping reads must prevent writes.
        """
        outer = self._verified_artifacts is None
        if outer:
            self._verified_artifacts = set()
        try:
            yield
        finally:
            if outer:
                self._verified_artifacts = None

    def input_hash(self, step):
        with self.verification_pass():
            inputs = {}
            for prerequisite in self.dependencies(step):
                if not self.completed(prerequisite):
                    raise ValueError(f"Run {prerequisite} before {step}")
                inputs[prerequisite] = self.manifest["steps"][prerequisite]["artifact_hash"]
            return fingerprint(inputs)

    def artifact_path(self, step):
        return self.directory / self.directories[step] / "result.json"

    def target_path(self, step, key):
        return self.directory / self.directories[step] / "targets" / (fingerprint(key) + ".json")

    def recover(self):
        # A step receipt is written after its artifact and before the manifest.
        # This completes the commit if a process died between receipt and manifest.
        for step in self.steps:
            receipt = self.artifact_path(step).with_name("commit.json")
            if receipt.exists() and self.manifest["steps"].get(step, {}).get("status") != "completed":
                item = read_json(receipt)
                if item.get("step") != step or item.get("status") != "completed":
                    raise ValueError(f"Invalid step receipt: {step}")
                self._check_artifact(step, item)
                self.manifest["steps"][step] = item
                self._dirty_steps.add(step)

    def _check_artifact(self, step, item):
        with self.verification_pass():
            key = (step, fingerprint(item))
            if key in self._verified_artifacts:
                return
            path = self.artifact_path(step)
            if not path.is_file() or file_hash(path) != item.get("artifact_hash"):
                raise ValueError(f"Committed {step} artifact missing/changed; start a new run or restore it")
            artifact = read_json(path)
            if self.manifest["pipeline_version"] in {5, 6}:
                if item.get("input_hash") != self.input_hash(step):
                    raise ValueError(f"Committed {step} dependencies changed; create a new run")
                if step in {"layout", "inpaint"} or self.manifest["pipeline_version"] == 6 and step in {"bubble_geometry", "typeset"}:
                    from ..visual.stages import verify_files
                    for page in artifact.get("pages", []):
                        verify_files(self.directory, page)
            self._verified_artifacts.add(key)

    def completed(self, step):
        item = self.manifest["steps"].get(step, {})
        if item.get("status") == "completed":
            self._check_artifact(step, item)
            return True
        return False

    def read(self, step):
        if not self.completed(step):
            raise ValueError(f"Prerequisite {step} is not completed")
        return read_json(self.artifact_path(step))

    def begin(self, step):
        previous = self.manifest["steps"].get(step, {})
        timing = {"runtime_seconds": previous["runtime_seconds"]} if self._valid_runtime(previous.get("runtime_seconds")) else {}
        self._step_timers[step] = monotonic()
        self.manifest["status"] = "running"
        self.manifest["steps"][step] = {"status": "running", "started_at": now(), **timing}
        self._dirty_steps.add(step)
        self.save()

    @staticmethod
    def _valid_runtime(value):
        return type(value) in {int, float} and math.isfinite(value) and value >= 0

    def _timing(self, step):
        previous = self.manifest["steps"].get(step, {})
        timing = {"started_at": previous["started_at"]} if "started_at" in previous else {}
        elapsed = previous.get("runtime_seconds")
        started = self._step_timers.pop(step, None)
        if started is not None:
            elapsed = (elapsed if self._valid_runtime(elapsed) else 0) + max(0, monotonic() - started)
        if self._valid_runtime(elapsed):
            timing["runtime_seconds"] = round(elapsed, 6)
            previous["runtime_seconds"] = timing["runtime_seconds"]
        return timing

    def finish(self, step, output, *, failures=0):
        path = self.artifact_path(step)
        atomic_json(path, output)
        item = {"step": step, "status": "partial" if failures else "completed",
                "finished_at": now(), "artifact_hash": file_hash(path), "failures": failures,
                **self._timing(step)}
        if self.manifest["pipeline_version"] in {5, 6}:
            item["input_hash"] = self.input_hash(step)
        if not failures:
            atomic_json(path.with_name("commit.json"), item)
        self.manifest["steps"][step] = item
        self._dirty_steps.add(step)
        self.manifest["status"] = "partial" if failures else ("completed" if step == self.steps[-1] else "running")
        self.save()

    def failed(self, step, exc):
        self.manifest["steps"][step] = {"status": "failed", "error": str(exc), "at": now(), **self._timing(step)}
        self.manifest["status"] = "failed"
        self._dirty_steps.add(step)
        self.save()

    def interrupted(self, step):
        self.manifest["steps"][step] = {"status": "interrupted", "at": now(), **self._timing(step)}
        self.manifest["status"] = "interrupted"
        self._dirty_steps.add(step)
        self.save()

    def target(self, step, key, request):
        path = self.target_path(step, key)
        if not path.exists():
            return None
        item = read_json(path)
        if item["request_hash"] != fingerprint(request):
            raise ValueError(f"Committed target input changed: {step}/{key}")
        if item["status"] == "completed":
            if fingerprint(item["result"]) != item["result_hash"]:
                raise ValueError(f"Corrupt target: {step}/{key}")
            return copy.deepcopy(item["result"])
        return None

    def commit_target(self, step, key, request, *, result=None, error=None):
        atomic_json(self.target_path(step, key), {
            "key": key, "request_hash": fingerprint(request), "at": now(),
            "status": "failed" if error is not None else "completed",
            "result": result, "result_hash": fingerprint(result), "error": error,
        })
