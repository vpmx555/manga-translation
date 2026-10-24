"""Default v5 contract, provenance, targeted repairs and legacy-safe resumes."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from tests.helpers import FakeDetector, make_store, raw_document
from tests.analysis.test_analyze import Model
from tests.test_visual_pipeline import LayoutBackend, MagiPanelProbe
from tests.translation.test_translation import Translator
from manga_pipeline.analysis import contract, speech
from manga_pipeline.bank.assignment import install_detection_patch
from manga_pipeline.normalization.dialogue import normalize_document
from manga_pipeline.pipeline import Pipeline, create_run, load_config, prepare_bank, reanalyze_run
from manga_pipeline.review.renderer import Review
from manga_pipeline.storage.io import atomic_json, file_hash
from manga_pipeline.storage.runs import RunStore
from manga_pipeline.translation.source import targets, view
from manga_pipeline.visual.config import preflight
from manga_pipeline.visual.magi_panels import koharu_panels


class VisualSemanticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def fixture(self, transform=None):
        old = make_store(self.root)
        image = self.root / "page.png"
        Image.new("RGB", (64, 64), "white").save(image)
        source = {"folder": str(self.root), "story": "story", "chapter": "chapter",
                  "images": [{"path": str(image), "sha256": file_hash(image)}]}
        config = load_config()
        config["v6"]["enabled"] = False  # This fixture exercises the committed v5 contract.
        store = RunStore.create(self.root / "visual", source=source, config=config,
                                bank_path=old.manifest["bank_path"])
        raw = raw_document(text="Hello!")
        raw["pages"][0].update(image_path=str(image), width=64, height=64, panels=[[0, 0, 64, 64]])
        raw["texts"][0]["bbox"] = [20, 20, 30, 30]
        raw["texts"].append({**copy.deepcopy(raw["texts"][0]), "id": "side", "reading_order": 1,
                             "text": "Side text", "is_essential_text": False})

        def semantic(model, task, data, value):
            if task == "analyze_dialogue_v3":
                for row in value["utterances"]:
                    row.update(speech_style="normal", speech_style_evidence="Conversational greeting")
            return transform(model, task, data, value) if transform else value

        model, translator, backend = Model(semantic), Translator(), LayoutBackend()
        runner = Pipeline(store, extractor=lambda _: raw, layout_backend=backend,
                          provider=model, detector=FakeDetector(), translation_provider=translator)
        return runner, model, translator, backend

    def test_default_configs_and_threshold_forwarding(self):
        project = Path(__file__).resolve().parents[1]
        for path in (None, project / "configs/pipeline.json", project / "configs/pipeline-visual.json",
                     project / "configs/pipeline-visual-runpod.json"):
            config = load_config(path)
            self.assertTrue(config["layout"]["enabled"])
            self.assertFalse(config["inpaint"]["enabled"])
            self.assertEqual(config["character_character_matching_threshold"], 0.75)
            self.assertEqual(config["character_detection_threshold"], 0.35)
            self.assertEqual(config["text_detection_threshold"], 0.45)
            self.assertEqual(config["text_character_matching_threshold"], 0.25)
            self.assertEqual(config["text_classification_threshold"], -0.5)
            self.assertEqual(config["layout"]["bubble_threshold"], 0.45)

        class Probe:
            def predict_detections_and_associations(self, images, **kwargs):
                self.kwargs = kwargs
                return []
        probe = Probe()
        install_detection_patch(probe)
        probe.predict_detections_and_associations([])
        self.assertEqual(probe.kwargs["character_character_matching_threshold"], 0.75)
        self.assertEqual(probe.kwargs["character_detection_threshold"], 0.35)
        self.assertEqual(probe.kwargs["text_detection_threshold"], 0.45)
        self.assertEqual(probe.kwargs["text_character_matching_threshold"], 0.25)
        self.assertEqual(probe.kwargs["text_classification_threshold"], -0.5)
        probe.predict_detections_and_associations([], character_detection_threshold=0.30)
        self.assertEqual(probe.kwargs["character_detection_threshold"], 0.30)
        probe.predict_detections_and_associations([], text_character_matching_threshold=0.4,
                                                 text_classification_threshold=-1.0)
        self.assertEqual(probe.kwargs["text_character_matching_threshold"], 0.4)
        self.assertEqual(probe.kwargs["text_classification_threshold"], -1.0)

    def test_threshold_validation_and_legacy_manifest_defaults(self):
        from manga_pipeline.extraction.config import threshold_for_manifest
        self.assertEqual(threshold_for_manifest({}, "text_character_matching_threshold"), 0.35)
        self.assertEqual(threshold_for_manifest({}, "text_classification_threshold"), 0.5)
        self.assertEqual(threshold_for_manifest({}, "character_detection_threshold"), 0.30)
        path = self.root / "thresholds.json"
        for key, value in (("text_detection_threshold", True),
                           ("text_character_matching_threshold", 1.01),
                           ("text_character_matching_threshold", -0.1),
                           ("text_classification_threshold", float("inf")),
                           ("text_classification_threshold", float("nan"))):
            with self.subTest(key=key, value=value):
                # JSON writer rejects NaN/Inf, so validate those directly.
                from manga_pipeline.extraction.config import validate_thresholds
                config = load_config()
                config[key] = value
                with self.assertRaises(ValueError):
                    validate_thresholds(config)
        atomic_json(path, {"text_classification_threshold": -2.5})
        self.assertEqual(load_config(path)["text_classification_threshold"], -2.5)

    def test_reanalyze_rejects_changed_thresholds_for_magi_only_run(self):
        store = make_store(self.root)
        store.finish("extract", raw_document())
        before = file_hash(store.path)
        for key, value in (("text_character_matching_threshold", 0.4),
                           ("text_classification_threshold", -0.25)):
            with self.subTest(key=key):
                config = load_config()
                config["layout"]["enabled"] = False
                config[key] = value
                with self.assertRaisesRegex(ValueError, key):
                    reanalyze_run(store.directory, self.root / "forks", config)
                self.assertEqual(file_hash(store.path), before)

    def test_verification_pass_checks_dag_once_but_new_reads_detect_tampering(self):
        from manga_pipeline.visual.stages import verify_files
        runner, _, _, _ = self.fixture()
        self.assertTrue(runner.run())
        store = runner.store
        with patch("manga_pipeline.visual.stages.verify_files", wraps=verify_files) as verify:
            with store.verification_pass():
                self.assertTrue(store.completed("translate"))
                store.read("export")
                store.read("scan")
            self.assertEqual(verify.call_count, 1)
            self.assertTrue(store.completed("translate"))
            self.assertEqual(verify.call_count, 2)
        mask = next(store.artifact_path("layout").parent.joinpath("masks").glob("*.png"))
        original = mask.read_bytes()
        mask.write_bytes(original + b"changed")
        with self.assertRaisesRegex(ValueError, "Visual artifact missing/changed"):
            store.read("export")
        self.assertIsNone(store._verified_artifacts)
        mask.write_bytes(original)
        self.assertTrue(store.completed("export"))
        artifact = store.artifact_path("scan")
        artifact.write_bytes(artifact.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "Committed scan artifact missing/changed"):
            store.read("export")

    def test_deferred_reviews_keep_visualizations_and_render_once_without_inference(self):
        from manga_pipeline.review.renderer import write_reviews
        from manga_pipeline.visual.stages import verify_files
        runner, model, translator, backend = self.fixture()
        with patch.dict("os.environ", {"MANGA_DEFER_REVIEWS": "1"}):
            self.assertTrue(runner.run())
        store = runner.store
        self.assertFalse((store.directory / "review.md").exists())
        self.assertTrue(next(store.artifact_path("layout").parent.joinpath("overlays").glob("*.png"), None))
        calls = (len(model.calls), len(translator.calls), backend.calls)
        with patch("manga_pipeline.visual.stages.verify_files", wraps=verify_files) as verify:
            write_reviews(store)
            self.assertEqual(verify.call_count, 1)
        self.assertTrue((store.directory / "review.md").is_file())
        self.assertTrue(all((store.artifact_path(step).parent / "review.md").is_file() for step in store.steps))
        self.assertEqual((len(model.calls), len(translator.calls), backend.calls), calls)

    def test_schema_opt_in_preserves_old_contract(self):
        target = {"utterance_id": "u", "identity_candidates": [], "candidates": []}
        old = contract.schema([target], "individual-v1")
        new = contract.schema([target], "individual-v1", speech.POLICY)
        old_fields = old["properties"]["utterances"]["items"]["anyOf"][0]["properties"]
        new_branch = new["properties"]["utterances"]["items"]["anyOf"][0]
        self.assertNotIn("speech_style", old_fields)
        self.assertIn("speech_style", new_branch["required"])
        self.assertEqual(new_branch["properties"]["speech_style"]["enum"], list(speech.LABELS))

    def test_end_to_end_style_geometry_export_translation_resume(self):
        runner, model, translator, backend = self.fixture()
        self.assertTrue(runner.run())
        store = runner.store
        self.assertEqual(store.manifest["pipeline_version"], 5)
        self.assertNotIn("inpaint", store.steps)
        self.assertEqual(store.manifest["speech_style_policy"], speech.POLICY)
        for step in ("normalize", "analyze", "export"):
            row = store.read(step)["utterances"][0]
            self.assertIsNotNone(row["bubble_id"])
            self.assertEqual(row["source_boxes"][0]["bubble_id"], row["bubble_id"])
            if step != "normalize":
                self.assertEqual(row["speech_style"], "normal")
                self.assertEqual(row["speech_style_source"], "model")
        exported = store.read("export")
        self.assertEqual(exported["translation_only"][0]["speech_style_status"], "not_analyzed")
        self.assertEqual(view(targets(exported)[0])["speech_style"], "normal")
        for _, request, _ in translator.calls:
            self.assertEqual(request["targets"][0]["speech_style"], "normal")
            self.assertIn("bubble_geometry", request["targets"][0])
        analyzed_review = Review(store).render_step("analyze")
        self.assertIn("Speech style", analyzed_review)
        self.assertIn("Conversational greeting", analyzed_review)
        index = Review(store).render_index()
        self.assertIn("Extraction / layout", index)
        self.assertIn("Analysis / analyze", index)
        self.assertIn("Output / translate", index)
        hashes = {s: file_hash(store.artifact_path(s)) for s in store.steps}
        self.assertTrue(Pipeline(RunStore(store.directory), provider=model, translation_provider=translator).run())
        self.assertEqual(backend.calls, 1)
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(len(translator.calls), 2)
        self.assertEqual(hashes, {s: file_hash(store.artifact_path(s)) for s in store.steps})

    def test_style_repair_cannot_change_independent_semantics(self):
        def transform(model, task, request, value):
            row = value["utterances"][0]
            if len(model.calls) == 1:
                row["speech_style"] = "invalid"
            else:
                self.assertEqual(request["validation_errors"][0]["editable"],
                                 ["speech_style", "speech_style_evidence"])
                row.update(content_type="thought", speaker_id=2, addressee_type="self",
                           addressee_ids=[2], evidence="Changed unrelated evidence", speech_style="whisper")
            return value
        runner, model, _, _ = self.fixture(transform)
        self.assertTrue(runner.run("export"))
        row = runner.store.read("export")["utterances"][0]
        self.assertEqual(row["content_type"], "dialogue")
        self.assertEqual(row["speaker_id"], 1)
        self.assertEqual(row["addressee_type"], "group")
        self.assertEqual(row["speech_style"], "whisper")
        self.assertEqual(row["speech_style_source"], "repaired")

    def test_saved_visual_run_without_semantic_marker_keeps_old_contract(self):
        runner, _, _, _ = self.fixture()
        runner.store.manifest.pop("speech_style_policy")
        atomic_json(runner.store.path, runner.store.manifest)
        model = Model()
        runner._provider = model
        self.assertTrue(runner.run("export"))
        self.assertNotIn("speech_style_policy", model.calls[0][1])
        branch = model.contracts[0][1]["properties"]["utterances"]["items"]["anyOf"][0]
        self.assertNotIn("speech_style", branch["properties"])
        self.assertNotIn("speech_style", runner.store.read("export")["utterances"][0])
        before = file_hash(runner.store.artifact_path("analyze"))
        self.assertTrue(Pipeline(RunStore(runner.store.directory), provider=model).run("export"))
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(before, file_hash(runner.store.artifact_path("analyze")))

    def test_failed_style_repair_is_unknown_and_resume_does_not_retry(self):
        def invalid(model, task, request, value):
            value["utterances"][0]["speech_style"] = None
            return value
        runner, model, _, _ = self.fixture(invalid)
        self.assertTrue(runner.run("export"))
        row = runner.store.read("export")["utterances"][0]
        self.assertEqual((row["speech_style"], row["speech_style_source"], row["speech_style_status"]),
                         ("unknown", "fallback", "needs_review"))
        self.assertEqual(row["speaker_id"], 1)
        self.assertTrue(Pipeline(RunStore(runner.store.directory), provider=model).run("export"))
        self.assertEqual(len(model.calls), 2)

    def test_preflight_failure_leaves_no_run_or_bank(self):
        config = load_config()
        with patch("manga_pipeline.visual.config.preflight", side_effect=RuntimeError("Koharu unavailable")):
            with self.assertRaisesRegex(RuntimeError, "Koharu unavailable"):
                create_run(self.root, "story", "chapter", self.root / "outputs", self.root / "banks", config)
        self.assertFalse((self.root / "outputs").exists())
        self.assertFalse((self.root / "banks").exists())
        config["layout"]["enabled"] = False
        with self.assertRaisesRegex(ValueError, "require Koharu"):
            create_run(self.root, "story", "chapter", self.root / "outputs", self.root / "banks", config)

    def test_empty_model_responses_fallback_without_crash(self):
        def missing(model, task, request, value):
            return {"utterances": []}
        runner, model, _, _ = self.fixture(missing)
        self.assertTrue(runner.run("export"))
        row = runner.store.read("export")["utterances"][0]
        self.assertEqual(row["speech_style"], "unknown")
        self.assertEqual(row["speech_style_source"], "fallback")
        self.assertEqual(len(model.calls), 2)

    def test_pair_only_repair_keeps_original_style_provenance(self):
        def pair(model, task, request, value):
            if len(model.calls) == 1:
                value["utterances"][0]["speaker_id"] = "groups"
            else:
                self.assertEqual(request["validation_errors"][0]["editable"], ["speaker_id"])
                value["utterances"][0]["speech_style"] = "shout"
            return value
        runner, _, _, _ = self.fixture(pair)
        self.assertTrue(runner.run("export"))
        row = runner.store.read("export")["utterances"][0]
        self.assertEqual(row["speech_style"], "normal")
        self.assertEqual(row["speech_style_source"], "model")

    def test_preflight_rejects_assets_and_worker_before_inference(self):
        config = load_config()["layout"]
        config["model_root"] = str(self.root)
        with self.assertRaises(FileNotFoundError):
            preflight(config)
        with patch("manga_pipeline.visual.config.checked_assets"), \
             patch("subprocess.run", side_effect=FileNotFoundError("worker missing")):
            with self.assertRaisesRegex(RuntimeError, "worker unavailable"):
                preflight(config)

    def test_visual_bank_does_not_migrate_or_accept_old_ids(self):
        legacy = self.root / "legacy/story/metadata.json"
        atomic_json(legacy, {"schema_version": 1, "characters": {"1": {"id": 1}}})
        target = prepare_bank(self.root / "banks", "story", self.root / "legacy", visual=True)
        self.assertFalse(target.exists())
        from manga_pipeline.extraction.magi import MODEL_FINGERPRINT
        atomic_json(target, {"schema_version": 1, "model_fingerprint": MODEL_FINGERPRINT,
                             "characters": {"1": {"id": 1}}})
        with self.assertRaisesRegex(ValueError, "fresh bank"):
            prepare_bank(self.root / "banks", "story", visual=True)

    def test_merge_keeps_per_source_bubbles_without_false_aggregate(self):
        raw = raw_document(text="Hello")
        raw["texts"][0].update(bubble_id="a", bubble_geometry={"bubble_shape": "rounded"})
        raw["texts"].append({**copy.deepcopy(raw["texts"][0]), "id": "text2", "reading_order": 1,
                             "bubble_id": "b", "bubble_geometry": {"bubble_shape": "spiky"}})
        doc = normalize_document(raw, merge_groups=[{"source_text_ids": ["text1", "text2"],
            "confirmed": True, "reason": "User confirmed merge"}])
        row = doc["utterances"][0]
        self.assertNotIn("bubble_geometry", row)
        self.assertEqual([b["bubble_id"] for b in row["source_boxes"]], ["a", "b"])
        raw["texts"][0]["text"] = "sfx: BOOM"
        raw["texts"][0].update(speaker_id=None, character_detection_index=None)
        self.assertEqual(normalize_document(raw)["excluded"][0]["bubble_id"], "a")

    def test_empty_essential_page_still_has_review_image(self):
        runner, _, _, _ = self.fixture()
        self.assertTrue(runner.run("bubble_geometry"))
        store = runner.store
        text = Review(store).pages([], ["Text"], lambda r: (r["text"],), store.artifact_path("extract").parent)
        self.assertIn("Trang 1", text)
        self.assertIn("page.png", text)

    def test_koharu_hook_restores_on_failure(self):
        import torch
        from tests import test_visual_pipeline
        original = test_visual_pipeline.sort_panels
        with self.assertRaisesRegex(RuntimeError, "failure"):
            with koharu_panels(MagiPanelProbe(), [{"panels": []}]):
                raise RuntimeError("failure")
        self.assertIs(test_visual_pipeline.sort_panels, original)
        with self.assertRaisesRegex(ValueError, "did not consume"):
            with koharu_panels(MagiPanelProbe(), [{"panels": []}]):
                pass
        self.assertIs(test_visual_pipeline.sort_panels, original)
        with koharu_panels(MagiPanelProbe(), [{"panels": []}]):
            self.assertEqual(MagiPanelProbe().predict_detections_and_associations(torch.zeros((2, 4))).shape[0], 0)
