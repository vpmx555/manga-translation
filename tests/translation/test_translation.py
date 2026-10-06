import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.helpers import FakeDetector
from tests.analysis.test_analyze import Model as AnalysisModel, chapter, store_for
from manga_pipeline.cli.main import build_parser
from manga_pipeline.pipeline import Pipeline, load_config, translate_store
from manga_pipeline.review.renderer import Review
from manga_pipeline.storage.io import atomic_json, file_hash, read_json
from manga_pipeline.storage.runs import RunStore, TRANSLATE_STEPS
from manga_pipeline.translation import contract, stage
from manga_pipeline.translation.source import targets


def exported(count=4):
    return {"kind": "structured_dialogue", "pipeline_version": 3, "story_id": "story", "chapter_id": "chapter",
        "utterances": [{"id": f"t{i}:u", "text": "Hana-san, join the Rocket Club!", "page": 1,
            "reading_order": i, "content_type": "dialogue", "speaker_id": 1, "addressee_ids": [2],
            "addressee_type": "single"} for i in range(count)],
        "translation_only": [{"source_text_id": "aux", "text": "So cute!", "page": 2,
                              "reading_order": 0, "speaker_id": None, "addressee_ids": []}]}


class Translator:
    def __init__(self, transform=None):
        self.calls, self.transform = [], transform

    def infer(self, task, prompt, data, schema, validator):
        self.calls.append((task, copy.deepcopy(data), prompt))
        value = {"translations": [{"id": t["id"],
            "translation": ("Hana-san, tham gia câu lạc bộ tên lửa nhé!" if "Hana-san" in t["text"] else "Dễ thương quá!")
                + (" Nào!" if task.endswith("localized") else ""),
            "needs_review": False, "review_reason": "", "glossary_proposals": []} for t in data["targets"]]}
        if self.transform:
            value = self.transform(self, task, data, value)
        validator(value)
        return value


class TranslationTests(unittest.TestCase):
    def test_auxiliary_text_is_interleaved_using_original_box_position(self):
        document = exported(1)
        document["utterances"] = [
            {"id": "doc:p2:t0:u", "text": "First", "page": 2, "text_index": 0, "reading_order": 3},
            {"id": "doc:p2:t4:u", "text": "Last", "page": 2, "text_index": 4, "reading_order": 4}]
        document["translation_only"] = [{"source_text_id": "doc:p2:t2", "text": "Middle", "page": 2, "reading_order": 20}]
        self.assertEqual([r["source"]["text"] for r in targets(document)], ["First", "Middle", "Last"])

    def case(self, provider=None, document=None, config=None, **kwargs):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        document = document or exported()
        source = root / "export.json"
        atomic_json(source, document)
        provider = provider or Translator()
        output = stage.translate(document, source, root / "translations", config or copy.deepcopy(stage.DEFAULT_CONFIG),
                                 load_config()["ollama"], provider=provider, **kwargs)
        return root, source, provider, output

    def test_both_styles_all_buckets_metadata_and_resume(self):
        root, source, model, output = self.case()
        before = file_hash(source)
        self.assertEqual(len(model.calls), 4)
        self.assertFalse(output["failures"])
        self.assertEqual(set(output["styles"]), set(contract.STYLES))
        for style, rows in output["styles"].items():
            self.assertEqual(len(rows), 5)
            self.assertEqual(rows[0]["source"]["speaker_id"], 1)
            self.assertEqual(rows[0]["source"]["addressee_ids"], [2])
            self.assertEqual(rows[-1]["bucket"], "translation_only")
            self.assertIn("Hana-san", rows[0]["translation"])
            self.assertTrue((Path(output["directory"]) / f"review_{style}.md").exists())
        self.assertNotEqual(output["styles"]["natural"][0]["translation"], output["styles"]["localized"][0]["translation"])
        again = stage.translate(read_json(source), source, root / "translations", copy.deepcopy(stage.DEFAULT_CONFIG),
                                load_config()["ollama"], provider=model)
        self.assertEqual(output, again)
        self.assertEqual(len(model.calls), 4)
        self.assertEqual(file_hash(source), before)

    def test_partial_style_failure_resumes_only_failed_targets(self):
        def fail(model, task, data, value):
            if task.endswith("localized"):
                raise RuntimeError("timeout")
            return value
        root, source, model, first = self.case(Translator(fail), exported(1))
        self.assertEqual(len(first["failures"]), 2)
        successful = list(Path(first["directory"]).joinpath("targets/natural").glob("*.json"))
        before = {p: file_hash(p) for p in successful}
        second = Translator()
        final = stage.translate(read_json(source), source, root / "translations", copy.deepcopy(stage.DEFAULT_CONFIG),
                                load_config()["ollama"], provider=second)
        self.assertFalse(final["failures"])
        self.assertEqual(len(second.calls), 1)
        self.assertTrue(second.calls[0][0].endswith("localized"))
        self.assertEqual(before, {p: file_hash(p) for p in successful})

    def test_repair_missing_honorific_only_and_model_cannot_edit_ids(self):
        def bad(model, task, data, value):
            if len(model.calls) == 1:
                value["translations"][0]["translation"] = "Tham gia nhé!"
            for row in value["translations"]:
                row.update(speaker_id=99, addressee_ids=[99])
            return value
        _, _, model, result = self.case(Translator(bad))
        repair = model.calls[1][1]
        self.assertEqual(len(repair["targets"]), 1)
        self.assertTrue(repair["validation_errors"])
        self.assertEqual(result["styles"]["natural"][0]["source"]["speaker_id"], 1)
        self.assertNotIn("speaker_id", {k: v for k, v in result["styles"]["natural"][0].items() if k != "source"})

    def test_crash_after_journal_replays_response(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        document, model = exported(1), Translator()
        source = root / "export.json"
        atomic_json(source, document)
        with patch.object(stage, "commit", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                stage.translate(document, source, root / "translations", copy.deepcopy(stage.DEFAULT_CONFIG),
                                load_config()["ollama"], provider=model)
        self.assertEqual(len(model.calls), 1)
        result = stage.translate(document, source, root / "translations", copy.deepcopy(stage.DEFAULT_CONFIG),
                                 load_config()["ollama"], provider=model)
        self.assertFalse(result["failures"])
        self.assertEqual(len(model.calls), 2)

    def test_manual_layer_survives_glossary_revision_and_does_not_change_model_receipt(self):
        root, source, model, first = self.case(document=exported(1))
        receipt = next(Path(first["directory"]).joinpath("targets/natural").glob("*.json"))
        original = file_hash(receipt)
        overrides = root / "overrides.json"
        atomic_json(overrides, {"natural": {"t0:u": "Hana-san, bản đã sửa."}})
        output = stage.translate(read_json(source), source, root / "translations", copy.deepcopy(stage.DEFAULT_CONFIG),
            load_config()["ollama"], provider=model, overrides_path=overrides)
        self.assertEqual(output["styles"]["natural"][0]["status"], "manual")
        self.assertEqual(file_hash(receipt), original)
        glossary = root / "glossary.json"
        atomic_json(glossary, {"entries": [{"source": "Rocket Club", "vi": "câu lạc bộ tên lửa", "confirmed": True},
                                          {"source": "So cute", "vi": "ignored", "confirmed": False}]})
        revised = stage.translate(read_json(source), source, root / "translations", copy.deepcopy(stage.DEFAULT_CONFIG),
            load_config()["ollama"], provider=model, overrides_path=overrides, glossary_path=glossary)
        self.assertNotEqual(first["directory"], revised["directory"])
        self.assertEqual(revised["styles"]["natural"][0]["translation"], "Hana-san, bản đã sửa.")
        self.assertEqual(file_hash(receipt), original)
        self.assertTrue(all(e["vi"] != "ignored" for _, data, _ in model.calls for e in data["glossary"]))

    def test_ocr_guesses_and_normalize_review_are_visible(self):
        def guess(model, task, data, value):
            for row in value["translations"]:
                row.update(needs_review=True, review_reason="OCR ambiguous; guessed meaning",
                           glossary_proposals=[{"source": "Rocket Club", "vi": "CLB tên lửa"}])
            return value
        root, _, _, output = self.case(Translator(guess), exported(1))
        self.assertTrue(all(r["status"] == "review" for r in output["styles"]["natural"]))
        self.assertIn("Unconfirmed glossary", (Path(output["directory"]) / "review_natural.md").read_text(encoding="utf8"))
        document = exported(1)
        document["utterances"][0]["content_review_status"] = "needs_review"
        _, _, _, result = self.case(document=document)
        self.assertTrue(result["styles"]["natural"][0]["needs_review"])

    def test_optional_graph_boundary_snapshot_no_default_profiles(self):
        class Graph:
            def get_context(self, query):
                self.query = query
                return {query.targets[0]["id"]: {"nodes": [{"id": 1}, {"id": 2}],
                    "edges": [{"source": 1, "target": 2, "kind": "event", "label": "helped", "page": 1}]}}
        graph = Graph()
        document = exported(1)
        document["utterances"][0].update(text_index=4, reading_order=99)
        _, _, model, output = self.case(document=document, context_provider=graph)
        self.assertEqual(graph.query.character_ids, (1, 2))
        self.assertEqual(graph.query.targets[0]["source_position"], {"page": 1, "text_index": 4})
        self.assertIn("extension_context", model.calls[0][1])
        self.assertIn("u0", model.calls[0][1]["extension_context"])
        self.assertIn("extension_context", read_json(Path(output["directory"]) / "manifest.json")["inputs"])
        _, _, model, _ = self.case(document=exported(1))
        self.assertNotIn("extension_context", model.calls[0][1])

    def test_legacy_export_direct_translation_preserves_source_run_hashes(self):
        with tempfile.TemporaryDirectory() as root:
            source = store_for(root, legacy=True)
            source.finish("export", exported(1))
            paths = [source.path, source.directory / "snapshots/config.json", source.artifact_path("export")]
            before = {p: file_hash(p) for p in paths}
            output = translate_store(RunStore(source.directory), provider=Translator())
            self.assertFalse(output["failures"])
            self.assertEqual(before, {p: file_hash(p) for p in paths})
            document = exported(1)
            document["pipeline_version"] = 2
            document["utterances"][0].update(speaker_id=None, addressee_ids=[], addressee_type="unspecified")
            self.assertIsNone(targets(document)[0]["source"]["speaker_id"])

    def test_new_pipeline_auto_translates_and_old_flow_stays_five_steps(self):
        with tempfile.TemporaryDirectory() as root:
            old = store_for(root)
            self.assertNotIn("translate", old.steps)
            store = RunStore.create(Path(root) / "v4", source=old.manifest["source"], config=load_config(),
                                    bank_path=old.manifest["bank_path"])
            translator = Translator()
            runner = Pipeline(store, provider=AnalysisModel(), translation_provider=translator,
                              detector=FakeDetector("Hana Mori"), extractor=lambda _: chapter(2, "Hello!"))
            self.assertTrue(runner.run())
            self.assertEqual(runner.store.steps, TRANSLATE_STEPS)
            self.assertEqual(runner.store.manifest["status"], "completed")
            self.assertEqual(len(translator.calls), 2)
            self.assertIn("review_natural.md", Review(runner.store).render_step("translate"))
            measured = {step: item["runtime_seconds"] for step, item in runner.store.manifest["steps"].items()}
            reused = Pipeline(RunStore(store.directory), translation_provider=translator)
            self.assertTrue(reused.run())
            self.assertEqual(measured, {step: item["runtime_seconds"] for step, item in reused.store.manifest["steps"].items()})
            self.assertEqual(len(translator.calls), 2)
            args = build_parser().parse_args(["translate", "--run-dir", str(store.directory)])
            self.assertEqual(args.command, "translate")

    def test_pipeline_can_stop_at_export_then_resume_only_failed_style(self):
        with tempfile.TemporaryDirectory() as root:
            old = store_for(root)
            store = RunStore.create(Path(root) / "v4", source=old.manifest["source"], config=load_config(),
                                    bank_path=old.manifest["bank_path"])
            def fail_localized(model, task, data, value):
                if task.endswith("localized"):
                    raise RuntimeError("timeout")
                return value
            translator = Translator(fail_localized)
            runner = Pipeline(store, provider=AnalysisModel(), translation_provider=translator,
                              detector=FakeDetector("Hana Mori"), extractor=lambda _: chapter(2, "Hello!"))
            self.assertTrue(runner.run("export"))
            self.assertFalse(translator.calls)
            export_hash = file_hash(store.artifact_path("export"))
            self.assertFalse(Pipeline(RunStore(store.directory), translation_provider=translator).run())
            partial = RunStore(store.directory)
            self.assertEqual(partial.manifest["steps"]["translate"]["status"], "partial")
            revision = Path(read_json(partial.artifact_path("translate"))["directory"])
            completed = {p: file_hash(p) for p in (revision / "targets/natural").glob("*.json")}
            retry = Translator()
            self.assertTrue(Pipeline(partial, translation_provider=retry).run())
            self.assertTrue(retry.calls)
            self.assertTrue(all(task.endswith("localized") for task, _, _ in retry.calls))
            self.assertEqual(completed, {p: file_hash(p) for p in completed})
            self.assertEqual(export_hash, file_hash(partial.artifact_path("export")))


if __name__ == "__main__":
    unittest.main()
