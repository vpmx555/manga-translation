import copy
import tempfile
import unittest
from pathlib import Path

from tests.helpers import FakeDetector, make_store, raw_document
from manga_pipeline.names import stages
from manga_pipeline.names.dialogue import validate_response
from manga_pipeline.normalization.dialogue import normalize_document
from manga_pipeline.pipeline import Pipeline, load_config, reanalyze_run
from manga_pipeline.review.renderer import Review
from manga_pipeline.storage.io import atomic_json, file_hash, read_json
from manga_pipeline.storage.runs import RunStore


def chapter(count=15):
    raw = raw_document((1, 2), text="Hello, Madol-san!")
    base = raw["texts"][0]
    raw["texts"] = [{**copy.deepcopy(base), "id": f"text{i}", "reading_order": i,
                     "text": "Hello, Madol-san!" if i == 0 else f"Follow-up {i}.",
                     "speaker_id": 2 if i == 0 else 1} for i in range(count)]
    return raw


class DialogueProvider:
    def __init__(self):
        self.calls = []
        self.fail_ids = set()

    def infer(self, task, prompt, data, schema, validator, images=None):
        if task != "classify_dialogue" or images:
            raise AssertionError("Unexpected task/images")
        self.calls.append(copy.deepcopy(data))
        text_by_id = {x["id"]: x["text"] for x in data["turns"]}
        if any(text_by_id[x["utterance_id"]] in self.fail_ids for x in data["targets"]):
            raise RuntimeError("temporary failure")
        result = {"utterances": [{"utterance_id": t["utterance_id"], "content_type": "dialogue",
                                 "speaker_id": 1, "addressee_ids": [2],
                                 "addressee_type": "single" if data["policy"] == "essential-dialogue-v2" else "individual",
                                 "mentions": [{"candidate_id": c["id"], "mention_type": "direct_address",
                                               "name_target_id": 2} for c in t["candidates"]],
                                 "evidence": "Direct name or continuation"} for t in data["targets"]]}
        validator(result)
        return result


class DialogueAnalysisTests(unittest.TestCase):
    def test_interrupt_during_repair_keeps_already_valid_targets(self):
        with tempfile.TemporaryDirectory() as root:
            store = make_store(root)
            doc = normalize_document(chapter(3), essential_only=True)
            scanned = stages.scan(store, doc, FakeDetector("Madol-san"))

            class InterruptRepair(DialogueProvider):
                def infer(self, *args, **kwargs):
                    if self.calls:
                        raise KeyboardInterrupt()
                    value = super().infer(*args, **kwargs)
                    value["utterances"][-1]["addressee_ids"] = ["NOT_APPLICABLE"]
                    return value

            with self.assertRaises(KeyboardInterrupt):
                stages.classify(store, doc, scanned, InterruptRepair())
            provider = DialogueProvider()
            result = stages.classify(store, doc, scanned, provider)
            self.assertFalse(result["failures"])
            self.assertEqual(len(provider.calls), 1)
            self.assertEqual(len(provider.calls[0]["targets"]), 1)

    def test_semantic_error_retries_only_invalid_target(self):
        with tempfile.TemporaryDirectory() as root:
            store = make_store(root)
            doc = normalize_document(chapter(3), essential_only=True)
            scanned = stages.scan(store, doc, FakeDetector("Madol-san"))

            class FlawedOnce(DialogueProvider):
                def infer(self, *args, **kwargs):
                    value = super().infer(*args, **kwargs)
                    if len(self.calls) == 1:
                        value["utterances"][-1]["addressee_ids"] = ["NOT_APPLICABLE"]
                    return value

            provider = FlawedOnce()
            result = stages.classify(store, doc, scanned, provider)
            self.assertFalse(result["failures"])
            self.assertEqual(len(result["utterances"]), 3)
            self.assertEqual([len(call["targets"]) for call in provider.calls], [3, 1])
            self.assertIn("validation_errors", provider.calls[-1])

    def test_memory_keeps_inferred_name_only_within_ten_turns(self):
        with tempfile.TemporaryDirectory() as root:
            store = make_store(root)
            raw = chapter(15)
            raw["texts"][3]["text"] = "Madol-san, are you okay?"
            doc = normalize_document(raw, essential_only=True)
            scanned = stages.scan(store, doc, FakeDetector("Madol-san"))
            result = stages.classify(store, doc, scanned, DialogueProvider())
            pool = result["utterances"][12]["listener_candidates"]
            self.assertIn("Madol-san", next(x for x in pool if x["id"] == 2)["inferred_aliases"])
            pool = result["utterances"][14]["listener_candidates"]
            self.assertNotIn("Madol-san", next(x for x in pool if x["id"] == 2).get("inferred_aliases", []))

    def test_new_run_end_to_end_and_resume_do_not_repeat_inference(self):
        with tempfile.TemporaryDirectory() as root:
            old = make_store(root)
            config = load_config()
            config["dialogue_analysis"] = "essential-v2"
            fresh = RunStore.create(Path(root) / "fresh", source=old.manifest["source"],
                                    config=config, bank_path=old.manifest["bank_path"])
            provider, detector = DialogueProvider(), FakeDetector("Madol-san")
            raw = chapter(6)
            raw["texts"].append({**copy.deepcopy(raw["texts"][-1]), "id": "noise", "reading_order": 6,
                                 "text": "NONESSENTIAL NOISE", "is_essential_text": False})
            self.assertTrue(Pipeline(fresh, provider=provider, detector=detector, extractor=lambda _: raw).run())
            self.assertEqual(len(provider.calls), 2)
            self.assertTrue(RunStore(fresh.directory).completed("export"))
            self.assertTrue(Pipeline(fresh, provider=provider, detector=detector,
                                     extractor=lambda _: self.fail("MAGI reran")).run())
            self.assertEqual(len(provider.calls), 2)
            self.assertEqual(detector.calls, 6)
            self.assertTrue((fresh.directory / "06_export" / "translation_only.json").exists())
            self.assertNotIn("NONESSENTIAL NOISE", Review(RunStore(fresh.directory)).render_step("extract"))

    def test_essential_gate_retains_translation_and_does_not_filter_essential_ui(self):
        raw = chapter(3)
        raw["texts"][0]["text"] = "Sort by"
        raw["texts"][0]["speaker_id"] = None
        raw["texts"][0]["character_detection_index"] = None
        raw["texts"][1]["is_essential_text"] = False
        raw["texts"][2]["is_essential_text"] = None
        document = normalize_document(raw, essential_only=True)
        self.assertEqual([r["text"] for r in document["utterances"]], ["Sort by"])
        self.assertEqual(len(document["translation_only"]), 2)
        self.assertTrue(all(r["speaker_id"] is None and r["addressee_ids"] == [] for r in document["translation_only"]))
        self.assertEqual(document["utterances"][0]["reading_order"], 0)

    def test_all_turns_batched_text_only_memory_and_completed_targets_reused(self):
        with tempfile.TemporaryDirectory() as root:
            store = make_store(root)
            raw = chapter()
            raw["texts"].insert(5, {**copy.deepcopy(raw["texts"][4]), "id": "noise", "text": "SECRET NOISE", "is_essential_text": False})
            for i, row in enumerate(raw["texts"]):
                row["reading_order"] = i
            doc = normalize_document(raw, essential_only=True)
            scanned = stages.scan(store, doc, FakeDetector("Madol-san"))
            provider = DialogueProvider()
            result = stages.classify(store, doc, scanned, provider)
            self.assertFalse(result["failures"])
            self.assertEqual(len(result["utterances"]), 15)
            self.assertEqual(len(provider.calls), 5)
            for call in provider.calls:
                self.assertNotIn("SECRET NOISE", str(call))
                for turn in call["turns"]:
                    self.assertEqual(set(turn), {"id", "text", "source_speaker_id"})
                    self.assertTrue(turn["id"].startswith("u"))
                for target in call["targets"]:
                    self.assertLessEqual(len(target["context_ids"]), 5)
            final_pool = result["utterances"][12]["listener_candidates"]
            person = next(x for x in final_pool if x["id"] == 2)
            self.assertIn("memory:LLM", person["sources"])
            self.assertNotIn("memory:MAGI", person["sources"])
            replay = stages.classify(store, doc, scanned, provider)
            self.assertEqual(replay, result)
            self.assertEqual(len(provider.calls), 5)

    def test_failed_batch_retries_frozen_inputs_and_preserves_other_targets(self):
        with tempfile.TemporaryDirectory() as root:
            store = make_store(root)
            doc = normalize_document(chapter(9), essential_only=True)
            scanned = stages.scan(store, doc, FakeDetector("Madol-san"))
            provider = DialogueProvider()
            provider.fail_ids = {"Follow-up 3."}
            partial = stages.classify(store, doc, scanned, provider)
            self.assertEqual(len(partial["failures"]), 3)
            self.assertEqual(len(partial["utterances"]), 6)
            plans = {p: file_hash(p) for p in (store.directory / "04_classify" / "plans").glob("*.json")}
            old_request = provider.calls[1]
            provider.fail_ids.clear()
            complete = stages.classify(store, doc, scanned, provider)
            self.assertFalse(complete["failures"])
            self.assertEqual(len(provider.calls), 4)
            self.assertEqual(provider.calls[-1], old_request)
            self.assertEqual(plans, {p: file_hash(p) for p in plans})

    def test_new_name_alias_bank_and_export_review_preserve_source(self):
        with tempfile.TemporaryDirectory() as root:
            store = make_store(root, {"1": {"id": 1, "display_name": None},
                                      "2": {"id": 2, "display_name": "Madol Arase", "name_source": "manual",
                                            "embedding": [0.1], "crop_paths": ["same.png"]}})
            doc = normalize_document(chapter(3), essential_only=True)
            scanned = stages.scan(store, doc, FakeDetector("Madol-san"))
            provider = DialogueProvider()
            classified = stages.classify(store, doc, scanned, provider)
            linked = stages.link(store, chapter(3), doc, classified, provider)
            self.assertFalse(linked["failures"])
            bank = stages.NameBank(store.manifest["bank_path"]).read()
            person = bank["characters"]["2"]
            self.assertEqual(person["display_name"], "Madol Arase")
            self.assertEqual(person["aliases"], ["Madol-san"])
            self.assertEqual(person["embedding"], [0.1])
            self.assertEqual(person["crop_paths"], ["same.png"])
            # Replaying link doesn't duplicate aliases/history or call VLM.
            self.assertEqual(stages.link(store, chapter(3), doc, classified, provider), linked)
            self.assertEqual(len(provider.calls), 1)
            for step, output in [("extract", chapter(3)), ("normalize", doc), ("scan", scanned),
                                 ("classify", classified), ("link", linked)]:
                store.finish(step, output)
            output = Pipeline(store).export()
            store.finish("export", output)
            row = output["utterances"][0]
            self.assertEqual(row["source_speaker_id"], 2)
            self.assertEqual(row["speaker_id"], 1)
            self.assertEqual(row["addressee_ids"], [2])
            self.assertEqual(row["mentions"][0]["referenced_id"], 2)
            self.assertEqual(row["text"], doc["utterances"][0]["text"])
            review = Review(store)
            for step in ["classify", "export"]:
                text = review.render_step(step)
                self.assertIn("Ứng viên nói", text)
                self.assertIn("Ứng viên nghe", text)
                self.assertIn("Follow-up 2.", text)
                self.assertIn("Direct name or continuation", text)

    def test_validator_special_ids_and_rejects_hallucinated_or_conflicting_links(self):
        target = {"utterance_id": "u", "identity_candidates": [{"id": 1}, {"id": 2}],
                  "candidates": [{"id": "name", "mapped_ids": [2]}]}
        item = {"utterance_id": "u", "content_type": "dialogue", "speaker_id": 1,
                "addressee_ids": [2], "addressee_type": "individual", "evidence": "Name",
                "mentions": [{"candidate_id": "name", "mention_type": "direct_address", "name_target_id": 2}]}
        validate_response({"utterances": [item]}, [target])
        for changes in [{"speaker_id": 99}, {"speaker_id": True}, {"addressee_ids": []},
                        {"speaker_id": 2},
                        {"addressee_ids": [99]}, {"addressee_ids": [1], "mentions": [
                            {"candidate_id": "name", "mention_type": "direct_address", "name_target_id": 1}]}]:
            with self.assertRaises(ValueError):
                validate_response({"utterances": [{**item, **changes}]}, [target])
        for kind, speaker, listeners, address in [("narration", "NARRATOR", ["NOT_APPLICABLE"], "not_applicable"),
                                                    ("thought", "UNKNOWN", ["NOT_APPLICABLE"], "not_applicable"),
                                                    ("dialogue", "UNKNOWN", ["UNKNOWN"], "unspecified")]:
            valid = {**item, "content_type": kind, "speaker_id": speaker, "addressee_ids": listeners,
                     "addressee_type": address, "mentions": [{"candidate_id": "name", "mention_type": "unknown", "name_target_id": None}]}
            validate_response({"utterances": [valid]}, [target])

    def test_reanalyze_reuses_only_extraction_preserving_old_run(self):
        with tempfile.TemporaryDirectory() as root:
            old = make_store(root)
            old.finish("extract", chapter(3))
            old.finish("normalize", normalize_document(chapter(3)))
            paths = [old.path, old.artifact_path("extract"), old.artifact_path("normalize"), Path(old.manifest["bank_path"])]
            before = {p: file_hash(p) for p in paths}
            visual = old.directory / "01_extract" / "visualizations" / "page_1.png"
            visual.parent.mkdir(parents=True)
            visual.write_bytes(b"test visual")
            fresh = reanalyze_run(old.directory, Path(root) / "new_outputs", load_config())
            self.assertEqual(fresh.read("extract"), old.read("extract"))
            self.assertFalse(fresh.completed("normalize"))
            self.assertEqual(fresh.manifest["bank_path"], old.manifest["bank_path"])
            self.assertEqual(fresh.manifest["config"]["dialogue_analysis"], "essential-v3")
            self.assertEqual(fresh.manifest["config"]["speaker_policy"], "individual-v1")
            self.assertEqual(read_json(fresh.directory / "snapshots/config.json")["speaker_policy"], "individual-v1")
            self.assertEqual(before, {p: file_hash(p) for p in paths})
            self.assertEqual((fresh.directory / "01_extract" / "visualizations" / "page_1.png").read_bytes(), b"test visual")


if __name__ == "__main__":
    unittest.main()
