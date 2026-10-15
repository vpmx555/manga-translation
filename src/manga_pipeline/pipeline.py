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
from .bank.names import NameBank, known_names
from .extraction.document import validate_raw
from .extraction.config import DETECTION_DEFAULTS, threshold_for_manifest, validate_thresholds
from .extraction.hayai import DEFAULT_CONFIG as OCR_CONFIG, validate_config as validate_ocr_config
from .extraction.magi import MODEL_FINGERPRINT
from .extraction.pages import source_manifest
from .names import stages
from .normalization.dialogue import normalize_document, validate_normalized
from .progress import NullProgress
from .policies import DEFAULT_ANALYSIS_POLICY, DEFAULT_SPEAKER_POLICY, ESSENTIAL_POLICIES, is_essential_policy
from .storage.io import atomic_json, file_hash, fingerprint, read_json
from .storage.locking import exclusive_lock
from .storage.runs import RunStore
from .normalization.noise import POLICY as NOISE_POLICY
from .translation.stage import DEFAULT_CONFIG as TRANSLATION_CONFIG, validate_config as validate_translation_config
from .visual.config import LAYOUT_CONFIG, INPAINT_CONFIG, validate_config as validate_visual_config
from .v6.config import DEFAULT_CONFIG as V6_CONFIG, validate_config as validate_v6_config
from .integrations.core_config import DEFAULT_CONFIG as CORE_CONFIG, validate_config as validate_core_config

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parent
DEFAULT_CONFIG = {
    "dialogue_analysis": DEFAULT_ANALYSIS_POLICY,
    "speaker_policy": DEFAULT_SPEAKER_POLICY,
    "noise_filter": {"policy": NOISE_POLICY, "rules_path": None},
    "translation": copy.deepcopy(TRANSLATION_CONFIG),
    "layout": copy.deepcopy(LAYOUT_CONFIG),
    "v6": copy.deepcopy(V6_CONFIG),
    "core": copy.deepcopy(CORE_CONFIG),
    "inpaint": copy.deepcopy(INPAINT_CONFIG),
    "ner": {"backend": "gliner", "model": "gliner-community/gliner_small-v2.5",
            "revision": "f227d3cd637bd4e6757ae143935316d062393341", "threshold": 0.85, "device": "cpu"},
    "ocr": copy.deepcopy(OCR_CONFIG),
    "device": "auto", **DETECTION_DEFAULTS, "visualizations": True,
    "spacy_model": "en_core_web_sm", "ollama": {
        "host": "http://127.0.0.1:11434", "model": "gemma4:e4b-it-q4_K_M",
        "num_ctx": 8192, "num_predict": 768, "image_max_side": 768, "timeout": 600, "retries": 1},
}


def load_config(path=None, device=None):
    config = copy.deepcopy(DEFAULT_CONFIG)
    if path:
        supplied = read_json(path)
        if set(supplied) - set(config):
            raise ValueError("Unknown pipeline configuration fields")
        sections = ("ollama", "ner", "noise_filter", "translation", "layout", "inpaint", "ocr", "v6", "core")
        config.update({k: v for k, v in supplied.items() if k not in sections})
        for section in sections:
            if not isinstance(supplied.get(section, {}), dict) or set(supplied.get(section, {})) - set(config[section]):
                raise ValueError(f"Unknown {section} configuration fields")
            config[section].update(supplied.get(section, {}))
    if device:
        config["device"] = device
    validate_ocr_config(config["ocr"])
    validate_v6_config(config["v6"])
    validate_core_config(config["core"])
    if config["core"]["enabled"] and (not config["v6"]["enabled"] or config["ocr"]["backend"] != "hayai"):
        raise ValueError("Core integration requires v6 and Hayai OCR; MAGI character linking is retained")
    if config["device"] not in {"auto", "cpu", "cuda"}:
        raise ValueError("Invalid extraction configuration")
    validate_thresholds(config)
    if config["dialogue_analysis"] not in ESSENTIAL_POLICIES:
        raise ValueError("dialogue_analysis must be essential-v1, essential-v2 or essential-v3")
    if config["speaker_policy"] != DEFAULT_SPEAKER_POLICY:
        raise ValueError("speaker_policy must be individual-v1 for new runs")
    if (config["noise_filter"]["policy"] != NOISE_POLICY
            or config["noise_filter"]["rules_path"] is not None and not isinstance(config["noise_filter"]["rules_path"], str)):
        raise ValueError("Invalid noise filter configuration")
    validate_translation_config(config["translation"])
    validate_visual_config(config["layout"], config["inpaint"])
    if config["layout"]["enabled"] and config["dialogue_analysis"] != "essential-v3":
        raise ValueError("Koharu stages require essential-v3; disable layout/inpaint for legacy policies")
    ner = config["ner"]
    if (ner["backend"] not in {"gliner", "spacy"} or ner["device"] not in {"auto", "cpu", "cuda"}
            or type(ner["threshold"]) not in {int, float} or not 0 < ner["threshold"] < 1
            or any(not isinstance(ner[k], str) or not ner[k].strip() for k in ("model", "revision"))):
        raise ValueError("Invalid NER configuration")
    for key in ("num_ctx", "num_predict", "image_max_side", "timeout"):
        if type(config["ollama"][key]) is not int or config["ollama"][key] <= 0:
            raise ValueError(f"Invalid {key}")
    if type(config["ollama"]["retries"]) is not int or not 0 <= config["ollama"]["retries"] <= 3:
        raise ValueError("retries must be 0..3")
    return config


def prepare_bank(bank_root, story, legacy_root=None, *, visual=False):
    target = Path(bank_root).resolve() / story
    metadata = target / "metadata.json"
    old = Path(legacy_root or PROJECT / "data" / "character_banks") / story
    with exclusive_lock(target.parent / ("." + story + ".prepare.lock")):
        if not metadata.exists() and target.exists() and any(target.iterdir()):
            raise ValueError(f"Bank directory has data but no metadata: {target}")
        if not visual and not target.exists() and (old / "metadata.json").exists() and old.resolve() != target:
            staging = target.parent / ("." + story + ".migration-" + uuid.uuid4().hex)
            try:
                with exclusive_lock(old / ".bank.lock"):
                    old_metadata = read_json(old / "metadata.json")
                    if old_metadata.get("schema_version") != 1 or old_metadata.get("model_fingerprint") != MODEL_FINGERPRINT:
                        raise ValueError("Legacy bank schema/model mismatch; migrate explicitly before using it")
                    # Publish only a complete copy; leave the old bank untouched.
                    shutil.copytree(old, staging, ignore=shutil.ignore_patterns(".bank.lock"))
                    atomic_json(staging / "migration_backup.json", old_metadata)
                os.replace(staging, target)
            finally:
                # A crash can leave an unpublished staging directory, never a partial target.
                if staging.exists():
                    if staging.resolve().parent != target.parent.resolve():
                        raise ValueError("Migration staging directory escaped bank root")
                    shutil.rmtree(staging)
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
        raise ValueError("New runs require Koharu layout; legacy runs can still resume")
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
        visual = config.get("layout", {}).get("enabled")
        for key in DETECTION_DEFAULTS:
            if threshold_for_manifest(config, key) != threshold_for_manifest(source.manifest["config"], key):
                raise ValueError(f"Extraction setting {key} changed; start a fresh run")
        if visual:
            if source.manifest["pipeline_version"] not in {5, 6}:
                raise ValueError("MAGI-only extraction cannot be reused as Koharu geometry; start a fresh run or disable layout/inpaint")
            if config.get("layout") != source.manifest["config"].get("layout"):
                raise ValueError("Extraction setting layout changed; start a fresh run")
            if config.get("v6", {}).get("enabled") and source.manifest["pipeline_version"] != 6:
                raise ValueError("Pipeline v6 requires fresh full-page/crop candidates; start a fresh run")
            source.read("bubble_geometry")
        elif source.manifest["pipeline_version"] in {5, 6}:
            raise ValueError("Koharu extraction cannot be silently downgraded to MAGI-only")
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        info = source.manifest["source"]
        directory = Path(output_root).resolve() / info["story"] / info["chapter"] / run_id
        target = RunStore.create(directory, source=copy.deepcopy(info), config=config,
                                 bank_path=source.manifest["bank_path"])
        target.manifest["extraction_reused_from"] = str(source.directory)
        target.manifest["steps"]["extract"] = {"runtime_seconds": 0.0}
        if visual:
            reused_stages = ("layout", "extract") if target.manifest["pipeline_version"] == 6 else ("layout", "extract", "bubble_geometry")
            for stage in reused_stages:
                src = source.artifact_path(stage).parent
                dst = target.artifact_path(stage).parent
                shutil.copytree(src, dst, ignore=shutil.ignore_patterns("commit.json"))
                target.finish(stage, source.read(stage))
            return target
        images = source.artifact_path("extract").parent / "visualizations"
        if images.is_dir():
            shutil.copytree(images, target.artifact_path("extract").parent / "visualizations")
        target.finish("extract", raw)
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
        return self.store.read("bubble_geometry" if self.store.manifest["pipeline_version"] in {5, 6} else "extract")

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
            from .providers.ollama import Ollama
            self._provider = Ollama(self.store.manifest["config"]["ollama"], self.store.directory / "cache")
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
        if self.store.manifest["pipeline_version"] in {5, 6}:
            with exclusive_lock(self.store.directory / f".{step}.lock", timeout=1):
                self.store = RunStore(self.store.directory)
                return self._execute(step)
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
            elif step == "bubble_geometry":
                from .visual.stages import bubble_geometry
                output = bubble_geometry(self.store, self.progress)
            elif step == "inpaint":
                if self.inpaint_backend is not None:
                    from .visual.inpaint import inpaint
                    output = inpaint(self.store, self.inpaint_backend, self.progress)
                else:
                    output = self.visual_worker("inpaint")
            elif step == "extract":
                output = self.extraction()
            elif step == "normalize":
                policy = self.store.manifest["config"].get("dialogue_analysis")
                noise_rules = None
                if self.store.manifest["config"].get("noise_filter"):
                    noise_rules = read_json(self.store.directory / "snapshots/noise_rules.json")
                    if fingerprint(noise_rules) != self.store.manifest.get("noise_rules_hash"):
                        raise ValueError("Normalization noise rules snapshot changed/corrupt")
                output = normalize_document(self.raw_document(), progress=self.progress,
                                            essential_only=is_essential_policy(policy), analysis_policy=policy, noise_rules=noise_rules)
                validate_normalized(output)
            elif step == "scan":
                try:
                    output = stages.scan(self.store, self.store.read("normalize"), self.detector(), progress=self.progress)
                finally:
                    if self._detector is not None and hasattr(self._detector, "close"):
                        self._detector.close()
                        self._detector = None
            elif step == "classify":
                output = stages.classify(self.store, self.store.read("normalize"), self.store.read("scan"), self.provider(), progress=self.progress)
            elif step == "link":
                output = stages.link(self.store, self.store.read("extract"), self.store.read("normalize"),
                                     self.store.read("classify"), self.provider(), progress=self.progress)
            elif step == "analyze":
                from .analysis.stage import analyze
                output = analyze(self.store, self.raw_document(), self.store.read("normalize"),
                                 self.store.read("scan"), self.provider(), progress=self.progress)
            elif step == "translate":
                output = translate_store(self.store, provider=self.translation_provider,
                                         context_provider=self.context_provider, progress=self.progress)
            else:
                output = self.export()
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
        if self.store.manifest["pipeline_version"] == 6:
            from .v6.stages import write_review
            write_review(self.store, step)
            return
        if os.environ.get("MANGA_DEFER_REVIEWS") == "1":
            return
        from .review.renderer import write_reviews
        try:
            write_reviews(self.store, steps=[step])
        except OSError as exc:
            print(f"Could not write review for {step}: {exc}; use 'run.py review --run-dir ...' to retry", file=sys.stderr)

    def export(self):
        if is_essential_policy(self.store.read("normalize").get("analysis_policy")):
            return self.export_dialogue()
        document = copy.deepcopy(self.store.read("normalize"))
        original_rows = copy.deepcopy(document["utterances"])
        classified = {x["utterance_id"]: x for x in self.store.read("classify")["utterances"]}
        bank = stages.snapshot_bank(self.store, "export")
        names = known_names(bank)
        self.progress.reset(total=len(document["utterances"]), unit="utterance")
        for row in document["utterances"]:
            row["source_speaker_id"] = row.get("speaker_id")
            classification = classified.get(row["id"], {})
            kind = classification.get("content_type", row["content_type"])
            row["content_type"] = kind
            row["voice_type"] = "narrator" if kind == "narration" else ("character" if row.get("speaker_id") is not None else "unknown")
            if kind == "narration":
                row["speaker_id"] = None
            character = bank["characters"].get(str(row.get("speaker_id")), {})
            row["speaker_name"] = character.get("display_name")
            addressees, mentions = [], []
            for mention in classification.get("mentions", []):
                identity = names.get(mention["key"], [])
                value = {**mention, "referenced_id": identity[0] if len(identity) == 1 else None}
                mentions.append(value)
                direct = mention["mention_type"] == "direct_address"
                # Known names skip LLM; only a narrow dialogue vocative rule is used.
                if mention["skipped"] and kind == "dialogue":
                    import re
                    direct = bool(re.match(r"^(?:hey[,! ]+)?" + re.escape(mention["name"]) + r"\s*[,!:]", row["text"], re.I))
                if direct and len(identity) == 1 and kind == "dialogue":
                    addressees.append(identity[0])
            row["mentions"] = mentions
            row["addressee_ids"] = list(dict.fromkeys(addressees))
            row["addressee_type"] = ("not_applicable" if kind == "narration" else
                                     "group" if len(row["addressee_ids"]) > 1 else
                                     "self" if row["addressee_ids"] == [row.get("speaker_id")] else
                                     "individual" if row["addressee_ids"] else "unspecified")
            self.progress.advance()
        for old, new in zip(original_rows, document["utterances"]):
            for field in ("id", "text", "text_original", "source_text_ids", "source_boxes", "bbox", "page_id", "panel_index", "reading_order"):
                if old.get(field) != new.get(field):
                    raise ValueError(f"Export altered source/order: {field}")
        document.update(kind="structured_dialogue", pipeline_version=2,
                        name_links=self.store.read("link")["links"],
                        limitations=["Speaker associations come from MAGI; no global speaker correction.",
                                     "Unknown addressees remain unspecified; no scene or ordering inference."],
                        failures=[])
        atomic_json(self.store.directory / "06_export" / "dialogue.json", document)
        return document

    def export_dialogue(self):
        document = copy.deepcopy(self.store.read("normalize"))
        analysis = self.store.read("analyze" if "analyze" in self.store.steps else "classify")
        classified = {x["utterance_id"]: x for x in analysis["utterances"]}
        bank = stages.snapshot_bank(self.store, "export")
        names = known_names(bank)
        self.progress.reset(total=len(document["utterances"]), unit="utterance")
        for row in document["utterances"]:
            item = classified[row["id"]]
            row["source_speaker_id"] = row.get("speaker_id")
            for field in ("content_type", "speaker_id", "addressee_ids", "addressee_type",
                          "evidence", "speaker_candidates", "listener_candidates"):
                row[field] = copy.deepcopy(item[field])
            if document["analysis_policy"] == "essential-v3":
                for field in ("identity_candidates", "warnings", "classification_source"):
                    row[field] = copy.deepcopy(item[field])
                from .analysis.speech import EXPORT_FIELDS
                for field in EXPORT_FIELDS:
                    if field in item:
                        row[field] = copy.deepcopy(item[field])
            row["voice_type"] = ("narrator" if row["speaker_id"] in {"NARRATOR", "narrator"} else
                                 "groups" if row["speaker_id"] == "groups" else
                                 "unknown" if row["speaker_id"] in {"UNKNOWN", "others"} else "character")
            row["speaker_name"] = bank["characters"].get(str(row["speaker_id"]), {}).get("display_name")
            row["mentions"] = []
            for mention in item["mentions"]:
                identities = names.get(mention["key"], [])
                row["mentions"].append({**mention, "referenced_id": identities[0] if len(identities) == 1 else None})
            self.progress.advance()
        limitations = ["Text-only IDs can be uncertain; UNKNOWN is an explicit unresolved result.",
                       "Candidate memory uses ten previous essential turns; no scene or ordering inference."]
        if document["analysis_policy"] == "essential-v2":
            limitations = ["Text-only IDs can be uncertain; others is not a persistent speaker identity.",
                           "Narration targets public_audience by convention; unknown group members remain unresolved.",
                           "Candidate memory uses ten previous essential turns; no scene or ordering inference."]
        if document["analysis_policy"] == "essential-v3":
            limitations = ["Text-only IDs can remain unknown; others is not a persistent identity.",
                           "Narration describes function, not a fixed speaker or recipient scope.",
                           "Group IDs may cover only known members, not the entire group.",
                           "Five-turn context and ten-turn confirmed memory; no scene or reading-order inference."]
        document.update(kind="structured_dialogue", pipeline_version=self.store.manifest["pipeline_version"],
                        name_links=analysis["links"] if "analyze" in self.store.steps else self.store.read("link")["links"], failures=[],
                        limitations=limitations)
        if self.store.manifest.get("speech_style_policy"):
            document["speech_style_policy"] = self.store.manifest["speech_style_policy"]
            for row in document.get("translation_only", []):
                row.update(speech_style="unknown", speech_style_source="not_analyzed",
                           speech_style_status="not_analyzed", speech_style_evidence="Nonessential text not semantically analyzed")
        export_dir = self.store.artifact_path("export").parent
        atomic_json(export_dir / "dialogue.json", document)
        atomic_json(export_dir / "translation_only.json", document.get("translation_only", []))
        return document

    def run(self, until=None, *, only_step=False):
        # A reconstruction-only invocation may overlap the analysis branch after
        # normalize is committed. Per-stage locks prevent duplicate inference.
        branch_lock = ".inpaint-run.lock" if self.store.manifest["pipeline_version"] in {5, 6} and until == "inpaint" else ".run.lock"
        with exclusive_lock(self.store.directory / branch_lock, timeout=1):
            self.store = RunStore(self.store.directory)
            self.store.save()
            run_all = until is None
            until = until or self.store.steps[-1]
            try:
                # Verify changed sources if present, without rerunning completed MAGI.
                for source in self.store.manifest["source"]["images"]:
                    path = Path(source["path"])
                    if ((not path.exists() and self.store.manifest["pipeline_version"] in {5, 6}) or
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
                                    if self.store.manifest["pipeline_version"] in {5, 6} and
                                    step in {"layout", "extract", "inpaint", "scan", "analyze", "translate"}
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


def translate_store(store, *, config=None, provider=None, context_provider=None, progress=None):
    """Standalone translation reads source run state without saving/changing it."""
    from .translation.stage import translate
    configuration = config or store.manifest["config"]
    translation = copy.deepcopy(configuration.get("translation", TRANSLATION_CONFIG))
    bank = Path(store.manifest["bank_path"]).parent
    glossary = translation["glossary_path"] or bank / "translation_glossary.json"
    overrides = translation["overrides_path"] or store.directory / "translation_overrides.json"
    return translate(store.read("export"), store.artifact_path("export"), store.directory / "translations/vi",
        translation, configuration["ollama"], provider=provider, glossary_path=glossary,
        overrides_path=overrides, context_provider=context_provider, progress=progress)
