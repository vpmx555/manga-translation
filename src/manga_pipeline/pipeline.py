"""Orchestration with authoritative step artifacts and lazy providers."""
import copy
import os
import shutil
import subprocess
import sys
import uuid
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path

from .bank.identity import normalize_story_key
from .bank.config import DEFAULT_CONFIG as BANK_CONFIG, validate_config as validate_bank_config
from .extraction.document import validate_raw
from .extraction.config import DETECTION_DEFAULTS, threshold_for_manifest, validate_thresholds
from .extraction.glm_ocr import DEFAULT_CONFIG as OCR_CONFIG
from .extraction.hayai import validate_config as validate_ocr_config
from .extraction.magi import MODEL_FINGERPRINT
from .extraction.pages import source_manifest
from .progress import NullProgress
from .policies import DEFAULT_SPEAKER_POLICY
from .storage.io import atomic_json, file_hash, fingerprint, read_json
from .storage.locking import exclusive_lock
from .storage.runs import RunStore
from .normalization.noise import POLICY as NOISE_POLICY
from .translation.stage import DEFAULT_CONFIG as TRANSLATION_CONFIG, validate_config as validate_translation_config
from .visual.config import LAYOUT_CONFIG, INPAINT_CONFIG, validate_config as validate_visual_config
from .v6.config import DEFAULT_CONFIG as V6_CONFIG, validate_config as validate_v6_config
from .runtime.resources import DEFAULT_CONFIG as RUNTIME_CONFIG, validate_config as validate_runtime_config

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parent
DEFAULT_CONFIG = {
    "analysis_contract": "independent-batches-v6.1",
    "dialogue_analysis": "essential-v3",
    "speaker_policy": DEFAULT_SPEAKER_POLICY,
    "noise_filter": {"policy": NOISE_POLICY, "rules_path": None},
    "translation": copy.deepcopy(TRANSLATION_CONFIG),
    "layout": {**copy.deepcopy(LAYOUT_CONFIG), "enabled": True},
    "v6": copy.deepcopy(V6_CONFIG),
    "runtime": copy.deepcopy(RUNTIME_CONFIG),
    "vllm": {"version": "0.31.0", "revision": "5cba9b5f1bca69fab9b75c88ae7817ae22d7d898",
             "quantization": "compressed-tensors", "dtype": "bfloat16",
              "gpu_memory_utilization": 0.94, "max_num_batched_tokens": 2048, "max_num_seqs": 64,
              "max_images_per_prompt": 10, "startup_timeout": 600},
    "inpaint": copy.deepcopy(INPAINT_CONFIG),
    "ner": {"backend": "llm-scene", "model": "gliner-community/gliner_small-v2.5",
            "revision": "f227d3cd637bd4e6757ae143935316d062393341", "threshold": 0.85, "device": "cpu"},
    "ocr": copy.deepcopy(OCR_CONFIG),
    "character_bank": copy.deepcopy(BANK_CONFIG),
    "device": "auto", **DETECTION_DEFAULTS, "visualizations": True,
    "llm": {
        "backend": "vllm", "host": "http://127.0.0.1:8000", "model": "cyankiwi/Qwen3.5-27B-AWQ-4bit",
        "num_ctx": 32768, "num_predict": 2048, "image_max_side": 768, "timeout": 600, "retries": 0,
        "scene_num_predict": 4096, "analyze_num_predict": 8192, "translation_num_predict": 2048,
        "temperature": 0, "top_k": 20, "top_p": 0.95, "presence_penalty": 0,
        "compact_context": False, "require_full_gpu": True, "timeout_retries": 0},
}


def load_config(path=None, device=None):
    config = copy.deepcopy(DEFAULT_CONFIG)
    if path:
        supplied = read_json(path)
        if supplied.get("analysis_contract") != config["analysis_contract"] or "ollama" in supplied:
            raise ValueError("Legacy config is incompatible; use a new v6 config with llm and analysis_contract")
        if set(supplied) - set(config):
            raise ValueError("Unknown pipeline configuration fields")
        sections = ("llm", "vllm", "ner", "noise_filter", "translation", "layout", "inpaint", "ocr", "v6", "runtime", "character_bank")
        config.update({k: v for k, v in supplied.items() if k not in sections})
        for section in sections:
            if not isinstance(supplied.get(section, {}), dict) or set(supplied.get(section, {})) - set(config[section]):
                raise ValueError(f"Unknown {section} configuration fields")
            config[section].update(supplied.get(section, {}))
        # Unversioned historical configs retain their original contracts.
        # New defaults and shipped configs explicitly opt into the new policies.
        if 'scan_policy' not in supplied.get('v6', {}):
            config['v6']['scan_policy'] = None
        if 'identity_name_binding_policy' not in supplied.get('v6', {}):
            config['v6']['identity_name_binding_policy'] = 'chapter-multimodal-name-binding-v1'
    if device:
        config["device"] = device
    validate_ocr_config(config["ocr"])
    validate_bank_config(config['character_bank'])
    validate_v6_config(config["v6"])
    validate_runtime_config(config["runtime"])
    if config["device"] not in {"auto", "cpu", "cuda"}:
        raise ValueError("Invalid extraction configuration")
    validate_thresholds(config)
    if config["dialogue_analysis"] != "essential-v3":
        raise ValueError("Only essential-v3 with pipeline v6 is supported")
    if config["speaker_policy"] != DEFAULT_SPEAKER_POLICY:
        raise ValueError("speaker_policy must be individual-v1 for new runs")
    if (config["noise_filter"]["policy"] != NOISE_POLICY
            or config["noise_filter"]["rules_path"] is not None and not isinstance(config["noise_filter"]["rules_path"], str)):
        raise ValueError("Invalid noise filter configuration")
    validate_translation_config(config["translation"])
    validate_visual_config(config["layout"], config["inpaint"])
    if config["layout"]["enabled"] and config["dialogue_analysis"] != "essential-v3":
        raise ValueError("Koharu stages require essential-v3")
    ner = config["ner"]
    if (ner["backend"] not in {"gliner", "llm-scene"} or ner["device"] not in {"auto", "cpu", "cuda"}
            or type(ner["threshold"]) not in {int, float} or not 0 < ner["threshold"] < 1
            or any(not isinstance(ner[k], str) or not ner[k].strip() for k in ("model", "revision"))):
        raise ValueError("Invalid NER configuration")
    for key in ("num_ctx", "num_predict", "image_max_side", "timeout", "scene_num_predict",
                "analyze_num_predict", "translation_num_predict"):
        if type(config["llm"][key]) is not int or config["llm"][key] <= 0:
            raise ValueError(f"Invalid {key}")
    if type(config["llm"]["retries"]) is not int or not 0 <= config["llm"]["retries"] <= 3:
        raise ValueError("retries must be 0..3")
    llm = config["llm"]
    if llm["backend"] != "vllm":
        raise ValueError("llm.backend must be vllm")
    if llm["backend"] == "vllm":
        vllm = config["vllm"]
        if (vllm["version"] != "0.31.0" or not isinstance(vllm["revision"], str) or len(vllm["revision"]) != 40
                or any(c not in "0123456789abcdef" for c in vllm["revision"])
                or vllm["quantization"] != "compressed-tensors" or vllm["dtype"] != "bfloat16"
                or vllm["max_num_seqs"] not in {8, 32, 64} or llm["num_ctx"] != 32768
                or type(vllm["gpu_memory_utilization"]) not in (int, float)
                or not 0.5 <= vllm["gpu_memory_utilization"] <= 0.95
                or type(vllm["max_num_batched_tokens"]) is not int or not 512 <= vllm["max_num_batched_tokens"] <= llm["num_ctx"]
                or type(vllm["startup_timeout"]) is not int or not 30 <= vllm["startup_timeout"] <= 1200):
            raise ValueError("Invalid frozen vLLM configuration")
        if type(vllm['max_images_per_prompt']) is not int or not 10 <= vllm['max_images_per_prompt'] <= 32:
            raise ValueError('vllm.max_images_per_prompt must support the ten-crop profile')
    for key in ("compact_context", "require_full_gpu"):
        if type(llm[key]) is not bool:
            raise ValueError(f"Invalid llm.{key}")
    for key, low, high in (("temperature", 0, 2), ("top_p", 0.01, 1), ("presence_penalty", -2, 2)):
        if type(llm[key]) not in (int, float) or not low <= llm[key] <= high:
            raise ValueError(f"Invalid llm.{key}")
    if type(llm["top_k"]) is not int or not 1 <= llm["top_k"] <= 100:
        raise ValueError("Invalid llm.top_k")
    if type(llm["timeout_retries"]) is not int or not 0 <= llm["timeout_retries"] <= 1:
        raise ValueError("llm.timeout_retries must be 0..1")
    if max(llm[k] for k in ("scene_num_predict", "analyze_num_predict", "translation_num_predict")) + 512 >= llm["num_ctx"]:
        raise ValueError("Ollama output reserve leaves no input context")
    if config["runtime"].get("required_gpu"):
        from .runtime.llm_budget import validate_runpod_profile
        validate_runpod_profile(config)
    from .v6.policy import require_config
    require_config(config)
    if not config["layout"]["enabled"] or config["ocr"]["backend"] not in {"hayai", "glm-ocr"}:
        raise ValueError("New v6 runs require Koharu and a supported isolated OCR backend")
    return config


def prepare_bank(bank_root, story, legacy_root=None, *, visual=True):
    if legacy_root is not None or not visual:
        raise ValueError("Only compatible v6 banks are supported; legacy migration is explicit and external")
    target = Path(bank_root).resolve() / story
    metadata = target / "metadata.json"
    with exclusive_lock(target.parent / ("." + story + ".prepare.lock")):
        if not metadata.exists() and target.exists() and any(target.iterdir()):
            raise ValueError(f"Bank directory has data but no metadata: {target}")
        if metadata.exists():
            bank = read_json(metadata)
            if bank.get("schema_version") != 1 or bank.get("model_fingerprint") != MODEL_FINGERPRINT:
                raise ValueError("Character bank schema/model incompatible with MAGI")
            if visual and (bank.get("characters") or bank.get("pending")):
                from .visual.config import KOHARU_MODEL, KOHARU_REVISION
                expected = fingerprint({"model": KOHARU_MODEL, "revision": KOHARU_REVISION,
                                        "panel_rule": "overlap-v1"})
                if bank.get("geometry_fingerprint") != expected:
                    raise ValueError("Legacy character bank cannot be reused with Koharu; use a fresh bank")
    return metadata


def create_run(folder, story, chapter, output_root, bank_root, config, *, run_id=None):
    folder = Path(folder).resolve()
    if not config.get("layout", {}).get("enabled"):
        raise ValueError("New v6 runs require Koharu layout")
    from .visual.config import preflight
    preflight(config["layout"])
    story_key, chapter_key = normalize_story_key(story), normalize_story_key(chapter)
    bank_path = prepare_bank(bank_root, story_key, visual=True)
    source = {"folder": str(folder), "story": story_key, "story_title": story,
              "chapter": chapter_key, "images": source_manifest(folder)}
    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    directory = Path(output_root).resolve() / story_key / chapter_key / run_id
    return RunStore.create(directory, source=source, config=config, bank_path=bank_path)


def reanalyze_run(source_directory, output_root, config):
    """Fork validated extraction into a fresh run without importing old analysis."""
    with exclusive_lock(Path(source_directory).resolve() / ".run.lock", timeout=1):
        source = RunStore(source_directory)
        raw = source.read("extract")
        validate_raw(raw)
        previous_ocr = source.manifest["config"].get("ocr", {"backend": "magi"})
        requested_ocr = config.get("ocr", {"backend": "magi"})
        if {k: v for k, v in previous_ocr.items() if k != "python"} != {k: v for k, v in requested_ocr.items() if k != "python"}:
            raise ValueError("OCR setting changed; start a fresh run")
        for key in DETECTION_DEFAULTS:
            if threshold_for_manifest(config, key) != threshold_for_manifest(source.manifest["config"], key):
                raise ValueError(f"Extraction setting {key} changed; start a fresh run")
        if config.get('character_bank') != source.manifest['config'].get('character_bank'):
            raise ValueError('Extraction setting character_bank changed; start a fresh run')
        if config.get("layout") != source.manifest["config"].get("layout"):
            raise ValueError("Extraction setting layout changed; start a fresh run")
        source.read("bubble_geometry")
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        info = source.manifest["source"]
        directory = Path(output_root).resolve() / info["story"] / info["chapter"] / run_id
        target = RunStore.create(directory, source=copy.deepcopy(info), config=config,
                                 bank_path=source.manifest["bank_path"])
        target.manifest["extraction_reused_from"] = str(source.directory)
        # Disposable inference responses are reusable only for the identical model,
        # context and generation settings. The provider still checks the full
        # request key, result hash and semantic validator before consuming them.
        if all(source.manifest["config"].get(k) == config.get(k) for k in ("llm", "vllm")):
            count = 0
            for cached in sorted((source.directory / "cache").glob("*.json")):
                try:
                    item = read_json(cached)
                    if (item.get("key") != cached.stem
                            or item.get("result_hash") != fingerprint(item["result"])):
                        continue
                except (OSError, ValueError, KeyError, TypeError):
                    continue
                destination = target.directory / "cache" / cached.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(cached, destination)
                count += 1
            target.manifest["inference_cache_reused_from"] = {
                "run": str(source.directory), "responses": count,
                "policy": "exact-model-config-and-request-key-v1"}
        target.manifest["steps"]["extract"] = {"runtime_seconds": 0.0}
        for stage in ("layout", "extract"):
            src = source.artifact_path(stage).parent
            dst = target.artifact_path(stage).parent
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("commit.json"))
            target.finish(stage, source.read(stage))
        return target


class Pipeline:
    def __init__(self, store, *, detector=None, provider=None, extractor=None, progress=None,
                 translation_provider=None, context_provider=None, layout_backend=None, inpaint_backend=None):
        self.store = store
        self._detector, self._provider, self.extractor = detector, provider, extractor
        self.progress = progress if progress is not None else NullProgress()
        self.translation_provider, self.context_provider = translation_provider, context_provider
        self.layout_backend, self.inpaint_backend = layout_backend, inpaint_backend

    def raw_document(self):
        return self.store.read("bubble_geometry")

    def visual_worker(self, stage):
        config = self.store.manifest["config"]
        from .visual.config import layout_interpreter
        python = layout_interpreter(config["layout"]) if stage == "layout" else sys.executable
        log = self.store.directory / "logs" / f"{stage}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        result = self.store.artifact_path(stage).with_name("worker_result.json")
        error = result.with_name("worker_error.json")
        # Never consume a stale summary from a previous worker attempt.
        result.unlink(missing_ok=True)
        error.unlink(missing_ok=True)
        with log.open("a", encoding="utf-8") as stream:
            worker = subprocess.Popen([python, "-B", "-m", "manga_pipeline.visual.worker", stage, str(self.store.path)],
                                      cwd=PROJECT, env=env, stdout=stream, stderr=subprocess.STDOUT)
            try:
                code = worker.wait()
            except BaseException:
                worker.terminate()
                try:
                    worker.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    worker.kill()
                    worker.wait()
                raise
        if code or not result.is_file():
            detail = read_json(error)["error"] if error.exists() else f"worker exit {code}"
            raise RuntimeError(f"{stage} failed: {detail}; log: {log}")
        return read_json(result)

    def detector(self):
        if self._detector is None:
            from .providers.names import create_detector
            self._detector = create_detector(self.store.manifest["config"])
        return self._detector

    def provider(self):
        if self._provider is None:
            from .providers.factory import create_provider
            self._provider = create_provider(self.store.manifest["config"], self.store.directory / "cache")
        return self._provider

    def extraction(self):
        receipt = self.store.artifact_path("extract").with_name("worker_commit.json")
        if not receipt.exists():
            if self.extractor:
                output = self.extractor(self.store)
                validate_raw(output)
                atomic_json(receipt, {"output": output, "hash": fingerprint(output)})
            else:
                log = self.store.directory / "logs" / "magi.log"
                log.parent.mkdir(parents=True, exist_ok=True)
                env = os.environ.copy()
                env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
                progress_path = receipt.with_name("progress.json")
                env.pop("MANGA_PIPELINE_PROGRESS_PATH", None)
                if self.progress.enabled:
                    env["MANGA_PIPELINE_PROGRESS_PATH"] = str(progress_path)
                    atomic_json(progress_path, {"phase": "Starting MAGI", "done": 0, "total": None, "unit": "batch"})
                # Exit releases torch/MAGI memory before loading the next provider.
                with log.open("a", encoding="utf-8") as stream:
                    worker = subprocess.Popen([sys.executable, "-B", "-m", "manga_pipeline.extraction.magi", str(self.store.path)],
                                              cwd=PROJECT, env=env, stdout=stream, stderr=subprocess.STDOUT)
                    try:
                        while True:
                            try:
                                code = worker.wait(timeout=0.25)
                                break
                            except subprocess.TimeoutExpired:
                                if self.progress.enabled and progress_path.exists():
                                    try:
                                        self.progress.worker(read_json(progress_path))
                                    except (OSError, ValueError, KeyError, TypeError):
                                        pass
                    except BaseException:
                        worker.terminate()
                        try:
                            worker.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            worker.kill()
                            worker.wait()
                        raise
                if code:
                    error = receipt.with_name("worker_error.json")
                    detail = read_json(error)["error"] if error.exists() else f"worker exit {code}"
                    raise RuntimeError(f"MAGI extraction failed: {detail}; log: {log}")
        value = read_json(receipt)
        if fingerprint(value["output"]) != value["hash"]:
            raise ValueError("Extraction worker receipt is corrupt")
        if "transcript_hash" in value and file_hash(receipt.with_name("transcript.txt")) != value["transcript_hash"]:
            raise ValueError("Committed extraction transcript is corrupt")
        validate_raw(value["output"])
        return value["output"]

    def execute(self, step):
        if self.store.manifest["pipeline_version"] == 6:
            with exclusive_lock(self.store.directory / f".{step}.lock", timeout=1):
                self.store = RunStore(self.store.directory)
                from .runtime.resources import gpu_lease, event, ResourceExhausted, is_oom
                import time
                config = self.store.manifest["config"]
                if config.get("runtime", {}).get("enabled") and not os.environ.get("MANGA_GPU_COORDINATOR"):
                    raise RuntimeError("Enabled GPU runtime requires the frozen batch controller and shared coordinator")
                visual = step in {"layout", "extract", "inpaint", "bubble_geometry"}
                visual |= (step == "scan" and config.get("ner", {}).get("backend") == "gliner"
                           and config.get("ner", {}).get("device") in {"cuda", "auto"})
                guarded = visual and not self.store.completed(step)
                started, cpu_started = time.monotonic(), time.process_time()
                try:
                    with gpu_lease("visual") if guarded else nullcontext():
                        result = self._execute(step)
                    event("stage_finished", run=str(self.store.directory), stage=step,
                          wall_seconds=time.monotonic()-started, cpu_seconds=time.process_time()-cpu_started,
                          success=result)
                    return result
                except Exception as exc:
                    event("stage_failed", run=str(self.store.directory), stage=step, error=str(exc)[:500],
                          wall_seconds=time.monotonic()-started, cpu_seconds=time.process_time()-cpu_started)
                    if is_oom(exc):
                        raise ResourceExhausted(str(exc)) from exc
                    raise
        return self._execute(step)

    def _execute(self, step):
        if step not in self.store.steps:
            raise ValueError(f"Step {step} is not in this run's flow: {', '.join(self.store.steps)}")
        if self.store.completed(step):
            self.update_reviews(step)
            return True
        for prerequisite in self.store.dependencies(step):
            if not self.store.completed(prerequisite):
                raise ValueError(f"Run {prerequisite} before {step}; retry its missing/failed targets")
        self.store.begin(step)
        try:
            if self.store.manifest["pipeline_version"] == 6 and step not in {"layout", "extract", "inpaint"}:
                from .v6.stages import dispatch
                output = dispatch(self, step)
            elif step == "layout":
                if self.layout_backend is not None:
                    from .visual.stages import layout
                    output = layout(self.store, self.layout_backend, self.progress)
                else:
                    output = self.visual_worker("layout")
            elif step == "inpaint":
                if self.inpaint_backend is not None:
                    from .visual.inpaint import inpaint
                    output = inpaint(self.store, self.inpaint_backend, self.progress)
                else:
                    output = self.visual_worker("inpaint")
            elif step == "extract":
                output = self.extraction()
            else:
                raise ValueError("Unsupported v6 stage: " + step)
            failures = len(output.get("failures", []))
            self.store.finish(step, output, failures=failures)
        except KeyboardInterrupt:
            self.store.interrupted(step)
            self.update_reviews(step)
            raise
        except Exception as exc:
            self.store.failed(step, exc)
            self.update_reviews(step)
            raise
        self.update_reviews(step)
        return not failures

    def update_reviews(self, step):
        from .v6.stages import write_review
        from .storage.checkpoints import stage_checkpoint
        write_review(self.store, step)
        stage_checkpoint(self.store, step)

    def run(self, until=None, *, only_step=False):
        # A reconstruction-only invocation may overlap the analysis branch after
        # normalize is committed. Per-stage locks prevent duplicate inference.
        branch_lock = ".inpaint-run.lock" if self.store.manifest["pipeline_version"] == 6 and until == "inpaint" else ".run.lock"
        with exclusive_lock(self.store.directory / branch_lock, timeout=1):
            self.store = RunStore(self.store.directory)
            self.store.save()
            run_all = until is None
            until = until or self.store.steps[-1]
            try:
                # Verify changed sources if present, without rerunning completed MAGI.
                for source in self.store.manifest["source"]["images"]:
                    path = Path(source["path"])
                    if ((not path.exists() and self.store.manifest["pipeline_version"] == 6) or
                            (path.exists() and file_hash(path) != source["sha256"])):
                        raise ValueError(f"Source image changed: {path}; create a new run")
                if until not in self.store.steps:
                    raise ValueError(f"Step {until} is not in this run's flow: {', '.join(self.store.steps)}")
                selected = [until] if only_step else (self.store.steps if run_all else self.store.ancestors(until))
                with self.store.verification_pass():
                    reused_steps = {step for step in selected if self.store.completed(step)}
                success = True
                for index, step in enumerate(selected, 1):
                    reused = step in reused_steps
                    self.progress.start(step, index, len(selected), reused=reused)
                    if reused:
                        self.update_reviews(step)
                        self.progress.finish("reused")
                        continue
                    try:
                        gpu_lock = (exclusive_lock(self.store.directory / ".gpu-stage.lock", timeout=3600)
                                    if self.store.manifest["pipeline_version"] == 6 and
                                     step in {"layout", "extract", "inpaint", "scan", "analyze", "build_identity", "relationship", "translate"}
                                    else nullcontext())
                        with gpu_lock:
                            completed = self.execute(step)
                    except Exception as exc:
                        if step != "inpaint" or len(selected) == 1:
                            raise
                        success = False
                        self.progress.finish("failed")
                        print(f"Inpaint branch incomplete: {exc}; analysis continues", file=sys.stderr)
                        continue
                    if not completed:
                        self.progress.finish("partial")
                        if step == "inpaint" and len(selected) > 1:
                            success = False
                            continue
                        return False
                    self.progress.finish("reused" if reused else "completed")
                return success
            except KeyboardInterrupt:
                self.progress.finish("interrupted")
                raise
            except Exception:
                self.progress.finish("failed")
                raise
            finally:
                try:
                    if self._provider is not None and hasattr(self._provider, "close"):
                        self._provider.close()
                finally:
                    if self.translation_provider is not None and hasattr(self.translation_provider, "close"):
                        self.translation_provider.close()
                    self.progress.close()
