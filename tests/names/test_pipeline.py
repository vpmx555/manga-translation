import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.helpers import FakeDetector, FakeProvider, make_store, raw_document
from manga_pipeline.pipeline import Pipeline
from manga_pipeline.bank.names import NameBank
from manga_pipeline.storage.io import atomic_json, read_json
from manga_pipeline.storage.runs import RunStore


class NamePipelineTests(unittest.TestCase):
    def test_context_is_two_before_target_two_after(self):
        with tempfile.TemporaryDirectory() as directory:
            store = make_store(directory)
            raw = raw_document([1])
            source = raw["texts"][0]
            raw["texts"] = []
            for index in range(7):
                row = copy.deepcopy(source)
                row.update(id=f"text{index}", reading_order=index,
                           text="This is Roronoa Zoro." if index == 3 else f"Turn {index}.")
                raw["texts"].append(row)
            provider = FakeProvider()
            self.assertTrue(Pipeline(store, provider=provider, detector=FakeDetector(), extractor=lambda _: raw).run())
            self.assertEqual(len(provider.calls), 1)
            self.assertEqual([x["id"] for x in provider.calls[0][1]["context"]], [f"text{i}:u" for i in range(1, 6)])
            payload = provider.calls[0][1]
            self.assertEqual(set(payload["target"]), {"id", "text", "speaker_id"})
            self.assertTrue(all(set(turn) == {"id", "text", "speaker_id"} for turn in payload["context"]))
            self.assertEqual(set(payload["candidates"][0]), {"id", "name", "start", "end"})
            self.assertEqual(provider.calls[0][2], [])

    def test_unknown_is_completed_not_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            store = make_store(directory)
            provider = FakeProvider()
            def abstain(task, prompt, data, schema, validator, images=None):
                provider.calls.append((task, data, []))
                value = {"content_type": "unknown", "mentions": [{"candidate_id": c["id"], "mention_type": "unknown"}
                                                                    for c in data["candidates"]]}
                validator(value)
                return value
            provider.infer = abstain
            self.assertTrue(Pipeline(store, provider=provider, detector=FakeDetector(), extractor=lambda _: raw_document([1, 2])).run())
            self.assertTrue(Pipeline(RunStore(store.directory), provider=provider).run())
            self.assertEqual(len(provider.calls), 1)

    def test_known_names_are_removed_before_single_id_rule(self):
        class TwoNames:
            def detect(self, text):
                return [{"name": name, "start": text.index(name), "end": text.index(name) + len(name), "source": "spacy"}
                        for name in ("Luffy", "Zoro")]
        runner, provider, _ = self.run_case([1], {"1": {"id": 1, "display_name": None},
                                               "2": {"id": 2, "display_name": "Luffy"}},
                                          text="Luffy knew Zoro.", detector=TwoNames())
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual([c["name"] for c in provider.calls[0][1]["candidates"]], ["Zoro"])
        self.assertEqual(NameBank(runner.store.manifest["bank_path"]).read()["characters"]["1"]["display_name"], "Zoro")

    def test_individual_step_does_not_run_missing_predecessors(self):
        with tempfile.TemporaryDirectory() as directory:
            store = make_store(directory)
            with self.assertRaises(ValueError):
                Pipeline(store, extractor=lambda _: self.fail("Unexpected MAGI")).run("normalize", only_step=True)

    def run_case(self, ids, characters=None, text="This is Roronoa Zoro.", detector=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        store = make_store(temporary.name, characters)
        provider, detector = FakeProvider(), detector or FakeDetector()
        raw = raw_document(ids, text)
        runner = Pipeline(store, detector=detector, provider=provider, extractor=lambda _: raw)
        with patch("manga_pipeline.names.stages.panel_image", return_value="image"):
            self.assertTrue(runner.run())
        return runner, provider, detector

    def test_one_id_auto_links_without_vlm_and_preserves_source(self):
        runner, provider, _ = self.run_case([1])
        self.assertEqual([x[0] for x in provider.calls], ["classify_mentions"])
        output = runner.store.read("export")
        row = output["utterances"][0]
        self.assertIsNone(row["speaker_id"])
        self.assertEqual(row["source_speaker_id"], 1)
        self.assertEqual(row["mentions"][0]["referenced_id"], 1)
        self.assertEqual(row["addressee_type"], "not_applicable")
        self.assertEqual(NameBank(runner.store.manifest["bank_path"]).read()["characters"]["1"]["display_name"], "Roronoa Zoro")
        self.assertNotIn("scene_id", row)
        self.assertEqual(row["reading_order"], 0)

    def test_known_name_skips_both_llm_and_vlm(self):
        runner, provider, _ = self.run_case([1], {"1": {"id": 1, "display_name": "Roronoa Zoro"}})
        self.assertEqual(provider.calls, [])
        self.assertTrue(runner.store.completed("export"))

    def test_no_id_or_pending_only_never_receives_name(self):
        for ids in ([], [None]):
            with self.subTest(ids=ids):
                runner, provider, _ = self.run_case(ids)
                self.assertEqual([x[0] for x in provider.calls], ["classify_mentions"])
                self.assertTrue(all(not c.get("display_name") for c in NameBank(runner.store.manifest["bank_path"]).read()["characters"].values()))
                self.assertEqual(runner.store.read("link")["links"][0]["status"], "unknown")

    def test_multiple_stable_ids_use_vlm_only_for_narration(self):
        runner, provider, _ = self.run_case([1, 2])
        self.assertEqual([x[0] for x in provider.calls], ["classify_mentions", "link_narration_names"])
        self.assertTrue(provider.calls[-1][2])
        self.assertEqual(len(provider.calls[-1][1]["eligible_detections"]), 2)

    def test_same_id_multiple_detections_count_as_one(self):
        _, provider, _ = self.run_case([1, 1])
        self.assertEqual(len(provider.calls), 1)

    def test_one_id_multiple_names_remain_unresolved(self):
        class TwoNames:
            def detect(self, text):
                return [{"name": name, "start": text.index(name), "end": text.index(name) + len(name), "source": "spacy"}
                        for name in ("Luffy", "Zoro")]
        runner, provider, _ = self.run_case([1], text="Luffy and Zoro were pirates.", detector=TwoNames())
        self.assertEqual(len(provider.calls), 1)
        self.assertTrue(all(x["status"] == "unknown" for x in runner.store.read("link")["links"]))

    def test_non_narration_does_not_call_vlm(self):
        provider = FakeProvider()
        original = provider.infer
        def dialogue(task, prompt, data, schema, validator, images=None):
            if task == "classify_mentions":
                provider.calls.append((task, data, []))
                result = {"content_type": "dialogue", "mentions": [{"candidate_id": c["id"], "mention_type": "direct_address"}
                                                                    for c in data["candidates"]]}
                validator(result)
                return result
            return original(task, prompt, data, schema, validator, images)
        provider.infer = dialogue
        with tempfile.TemporaryDirectory() as directory:
            store = make_store(directory)
            self.assertTrue(Pipeline(store, detector=FakeDetector(), provider=provider,
                                     extractor=lambda _: raw_document([1, 2])).run())
            self.assertEqual(len(provider.calls), 1)

    def test_completed_run_resume_never_calls_models_again(self):
        runner, provider, detector = self.run_case([1])
        prior = runner.store.read("export")
        self.assertTrue(Pipeline(RunStore(runner.store.directory), detector=detector, provider=provider,
                                 extractor=lambda _: self.fail("MAGI rerun")).run())
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(detector.calls, 1)
        self.assertEqual(runner.store.read("export"), prior)

    def test_partial_classification_retries_only_failed_target(self):
        with tempfile.TemporaryDirectory() as directory:
            store = make_store(directory)
            raw = raw_document([1])
            second = copy.deepcopy(raw["texts"][0])
            second.update(id="text2", reading_order=1)
            raw["texts"].append(second)
            provider = FakeProvider()
            original = provider.infer
            def fail_one(task, prompt, data, schema, validator, images=None):
                if task == "classify_mentions" and data["target"]["id"].startswith("text2"):
                    raise RuntimeError("timeout")
                return original(task, prompt, data, schema, validator, images)
            provider.infer = fail_one
            detector = FakeDetector()
            runner = Pipeline(store, detector=detector, provider=provider, extractor=lambda _: raw)
            self.assertFalse(runner.run())
            self.assertEqual(runner.store.manifest["steps"]["classify"]["status"], "partial")
            provider.infer = original
            self.assertTrue(Pipeline(RunStore(store.directory), detector=detector, provider=provider,
                                     extractor=lambda _: self.fail("MAGI rerun")).run())
            self.assertEqual(len(provider.calls), 2)
            self.assertEqual(detector.calls, 2)

    def test_crash_after_bank_write_reuses_plan_and_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            store = make_store(directory)
            provider = FakeProvider()
            pipeline = Pipeline(store, detector=FakeDetector(), provider=provider, extractor=lambda _: raw_document([1, 2]))
            self.assertTrue(pipeline.run("classify"))
            real_apply = NameBank.apply
            def die_after_apply(*args, **kwargs):
                real_apply(*args, **kwargs)
                raise KeyboardInterrupt()
            with patch("manga_pipeline.names.stages.panel_image", return_value="image"), patch.object(NameBank, "apply", side_effect=die_after_apply, autospec=True):
                with self.assertRaises(KeyboardInterrupt):
                    pipeline.run()
            with patch("manga_pipeline.names.stages.panel_image", side_effect=AssertionError("VLM rerun")):
                self.assertTrue(Pipeline(RunStore(store.directory), provider=provider).run())
            self.assertEqual(len(provider.calls), 2)
            bank = NameBank(store.manifest["bank_path"]).read()
            self.assertEqual(len(bank["characters"]["1"]["name_history"]), 1)
            self.assertEqual(RunStore(store.directory).read("link")["links"][0]["status"], "linked")
