import copy
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.helpers import FakeDetector, make_store, raw_document
from manga_pipeline.analysis import contract, pairs
from manga_pipeline.bank.names import NameBank
from manga_pipeline.cli.main import build_parser
from manga_pipeline.pipeline import Pipeline, load_config
from manga_pipeline.progress import TerminalProgress
from manga_pipeline.review.renderer import Review
from manga_pipeline.storage.io import atomic_json, file_hash, read_json
from manga_pipeline.storage.runs import ANALYZE_STEPS, RunStore


def store_for(root, characters=None, *, legacy=False):
    old = make_store(root, characters)
    config = load_config()
    config.pop("translation")
    config.pop("noise_filter")
    store = RunStore.create(Path(root) / "new", source=old.manifest["source"], config=config,
                            bank_path=old.manifest["bank_path"])
    if legacy:
        # Simulate a persisted v3 run from before speaker_policy was introduced.
        store.manifest["config"].pop("speaker_policy")
        atomic_json(store.path, store.manifest)
        atomic_json(store.directory / "snapshots/config.json", store.manifest["config"])
        store = RunStore(store.directory)
    return store


def chapter(count=3, text="Hello, everyone!", ids=(1, 2)):
    raw = raw_document(ids, text)
    source = raw["texts"][0]
    raw["texts"] = [{**copy.deepcopy(source), "id": f"text{i}", "reading_order": i,
                     "speaker_id": ids[i % len(ids)] if ids else None,
                     "text": text if i == 0 else f"Follow-up {i}."} for i in range(count)]
    return raw


class Model:
    def __init__(self, transform=None):
        self.calls, self.transform = [], transform
        self.contracts = []

    def infer(self, task, prompt, data, schema, validator, images=None):
        self.calls.append((task, copy.deepcopy(data), list(images or [])))
        self.contracts.append((prompt, copy.deepcopy(schema)))
        if task == "bind_introduction_names_v3":
            value = {"links": [{"candidate_id": n["candidate_id"],
                "character_id": data["eligible_detections"][0]["character_id"], "reason": "Introduced visible person"}
                for n in data["names"]]}
        else:
            value = {"utterances": [{"utterance_id": t["utterance_id"],
                "content_type": "narration" if t["candidates"] else "dialogue",
                "speaker_id": "narrator" if t["candidates"] else (t["identity_candidates"][0]["id"] if t["identity_candidates"] else "others"),
                "addressee_type": "audience" if t["candidates"] else "group",
                "addressee_ids": ["public_audience"] if t["candidates"] else ["unknown"],
                "mentions": [{"candidate_id": c["id"], "mention_type": "introduction", "name_target_id": None}
                             for c in t["candidates"]], "evidence": "Presentation or collective context"}
                for t in data["targets"]]}
        if self.transform:
            value = self.transform(self, task, data, value)
        validator(value)
        return value

    def close(self):
        pass


class AnalyzeTests(unittest.TestCase):
    def run_case(self, raw, *, characters=None, model=None, detector=None, legacy=False):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        store = store_for(temporary.name, characters, legacy=legacy)
        model = model or Model()
        runner = Pipeline(store, provider=model, detector=detector or FakeDetector("Hana Mori"), extractor=lambda _: raw)
        with patch("manga_pipeline.analysis.binding.panel_image", return_value="panel-image"):
            self.assertTrue(runner.run())
        return runner.store, model

    def test_new_five_step_flow_and_completed_resume(self):
        store, model = self.run_case(chapter(3))
        self.assertEqual(store.steps, ANALYZE_STEPS)
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(len(model.calls[0][1]["targets"]), 3)
        self.assertFalse(model.calls[0][2])
        self.assertEqual(len(model.calls[0][1]["turns"]), 3)
        self.assertTrue((store.directory / "05_export/dialogue.json").exists())
        before = {s: file_hash(store.artifact_path(s)) for s in store.steps}
        self.assertTrue(Pipeline(RunStore(store.directory), provider=model).run())
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(before, {s: file_hash(store.artifact_path(s)) for s in store.steps})

    def test_fixed_pairs_are_normalized_without_another_request(self):
        def bad_pairs(model, task, data, value):
            for row, scope in zip(value["utterances"], ("audience", "unknown", "self")):
                row.update(content_type="narration", addressee_type=scope, addressee_ids=[2])
            return value
        store, model = self.run_case(chapter(3), model=Model(bad_pairs))
        rows = store.read("analyze")["utterances"]
        self.assertEqual([r["addressee_ids"] for r in rows], [["public_audience"], ["unknown"], [1]])
        self.assertEqual(len(model.calls), 1)
        self.assertTrue(all(r["warnings"] for r in rows))
        self.assertIn("Chuẩn hóa", Review(store).render_step("analyze"))

    def test_partial_group_and_character_narration_single_are_valid(self):
        def partial(model, task, data, value):
            value["utterances"][0].update(speaker_id=1, addressee_type="group", addressee_ids=[2])
            value["utterances"][1].update(content_type="narration", speaker_id=1,
                                         addressee_type="single", addressee_ids=[2])
            return value
        store, model = self.run_case(chapter(2), model=Model(partial))
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(store.read("export")["utterances"][0]["addressee_ids"], [2])
        self.assertEqual(store.read("export")["utterances"][1]["speaker_id"], 1)

    def test_joint_speakers_survive_export_review_memory_and_completed_resume(self):
        def joint(model, task, data, value):
            for row in value["utterances"]:
                row.update(content_type="dialogue", speaker_id="groups", addressee_type="single",
                           addressee_ids=[1], evidence="Several voices respond together.")
            return value
        store, model = self.run_case(chapter(4, "Together: Thank you!"), model=Model(joint), legacy=True)
        rows = store.read("export")["utterances"]
        self.assertEqual(len(model.calls), 2)
        self.assertTrue(all(r["speaker_id"] == "groups" and r["voice_type"] == "groups" for r in rows))
        self.assertTrue(all(r["addressee_type"] == "single" and r["addressee_ids"] == [1] for r in rows))
        self.assertTrue(all(r["speaker_name"] is None for r in rows))
        self.assertTrue(all(m["speaker_id"] == "groups" for m in model.calls[1][1]["memory"]))
        self.assertFalse(any(c["id"] == "groups" for t in model.calls[1][1]["targets"] for c in t["identity_candidates"]))
        self.assertIn("groups — Nhiều người cùng nói", Review(store).render_step("export"))
        self.assertNotIn("ID groups", Review(store).render_step("export"))
        self.assertNotIn("groups", read_json(store.manifest["bank_path"])["characters"])
        schema = contract.schema(model.calls[0][1]["targets"])
        self.assertIn("groups", schema["properties"]["utterances"]["items"]["anyOf"][0]["properties"]["speaker_id"]["enum"])
        before = {s: file_hash(store.artifact_path(s)) for s in store.steps}
        self.assertTrue(Pipeline(RunStore(store.directory), provider=model).run())
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(before, {s: file_hash(store.artifact_path(s)) for s in store.steps})

    def test_joint_speaker_token_is_not_a_listener_identity(self):
        def wrong_listener(model, task, data, value):
            value["utterances"][0].update(speaker_id="groups", addressee_type="group", addressee_ids=["groups"])
            return value
        store, model = self.run_case(chapter(1), model=Model(wrong_listener), legacy=True)
        row = store.read("export")["utterances"][0]
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(row["speaker_id"], "groups")
        self.assertEqual(row["classification_source"], "fallback")
        self.assertEqual(row["addressee_ids"], ["unknown"])

    def test_introduction_in_spoken_dialogue_uses_one_panel_id_without_vlm(self):
        def spoken(model, task, data, value):
            value["utterances"][0].update(content_type="dialogue", speaker_id=1,
                                         addressee_type="group", addressee_ids=["unknown"])
            return value
        store, model = self.run_case(chapter(1, "This is Hana Mori.", (1,)), model=Model(spoken))
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(NameBank(store.manifest["bank_path"]).read()["characters"]["1"]["display_name"], "Hana Mori")
        self.assertEqual(store.read("export")["utterances"][0]["mentions"][0]["name_target_id"], 1)

    def test_zero_or_pending_panel_ids_are_unresolved_without_vlm(self):
        for ids in ((), (None,)):
            with self.subTest(ids=ids):
                store, model = self.run_case(chapter(1, "This is Hana Mori.", ids))
                self.assertEqual(len(model.calls), 1)
                self.assertEqual(store.read("analyze")["links"][0]["reason"], "no_stable_id_in_panel")
                self.assertTrue(all(not c.get("display_name") for c in NameBank(store.manifest["bank_path"]).read()["characters"].values()))

    def test_multiple_stable_ids_use_vlm_and_duplicate_id_does_not(self):
        for ids, calls in [((1, 2), 2), ((1, 1), 1)]:
            with self.subTest(ids=ids):
                store, model = self.run_case(chapter(1, "This is Hana Mori.", ids))
                self.assertEqual(len(model.calls), calls)
                if calls == 2:
                    self.assertTrue(model.calls[-1][2])
                    self.assertNotIn("is_narration", model.calls[-1][1])
                self.assertEqual(store.read("analyze")["links"][0]["character_id"], 1)

    def test_no_name_never_reads_a_panel_image(self):
        with patch("manga_pipeline.analysis.binding.panel_candidates", side_effect=AssertionError("panel gate")):
            self.run_case(chapter(3))

    def test_multiple_new_names_with_one_id_do_not_create_aliases(self):
        class Names:
            def detect(self, text):
                return [{"name": name, "start": text.index(name), "end": text.index(name) + len(name), "source": "spacy"}
                        for name in ("Hana Mori", "Arase Mahoru")]
        store, model = self.run_case(chapter(1, "Hana Mori and Arase Mahoru.", (1,)), detector=Names())
        self.assertEqual(len(model.calls), 1)
        self.assertTrue(all(x["reason"] == "multiple_new_names_one_id" for x in store.read("analyze")["links"]))
        self.assertIsNone(NameBank(store.manifest["bank_path"]).read()["characters"]["1"]["display_name"])

    def test_known_name_skips_vlm_and_keeps_stable_mapping(self):
        store, model = self.run_case(chapter(1, "This is Hana Mori.", (1, 2)),
                                    characters={"1": {"id": 1, "display_name": "Hana Mori"}, "2": {"id": 2}})
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(store.read("analyze")["links"][0]["character_id"], 1)

    def test_names_are_available_to_next_batch_with_role_aware_memory(self):
        store, model = self.run_case(chapter(6, "This is Hana Mori.", (1,)))
        self.assertEqual(len(model.calls), 2)
        next_request = model.calls[1][1]
        pool = next_request["targets"][0]["identity_candidates"]
        self.assertEqual(pool[0]["name"], "Hana Mori")
        self.assertIn("memory:confirmed-name", pool[0]["sources"])
        self.assertTrue(any(m["names"] for m in next_request["memory"]))
        self.assertLessEqual(len(next_request["memory"]), 10)

    def test_repair_targets_only_bad_pair_and_locks_independent_semantics(self):
        def direct(model, task, data, value):
            row = value["utterances"][0]
            if len(model.calls) == 1:
                row.update(content_type="dialogue", speaker_id=1, addressee_type="single", addressee_ids=[2])
                row["mentions"][0].update(mention_type="direct_address", name_target_id=None)
            else:
                row.update(content_type="thought", speaker_id=2, addressee_type="group", addressee_ids=[1])
                row["mentions"][0].update(mention_type="direct_address", name_target_id=2)
            return value
        store, model = self.run_case(chapter(3, "Hana Mori, can you hear me?"), model=Model(direct))
        self.assertEqual([len(c[1]["targets"]) for c in model.calls], [3, 1])
        row = store.read("export")["utterances"][0]
        self.assertEqual((row["content_type"], row["speaker_id"], row["addressee_type"], row["addressee_ids"]),
                         ("dialogue", 1, "single", [2]))
        self.assertEqual(row["mentions"][0]["name_target_id"], 2)
        self.assertIn("previous_results", model.calls[-1][1])

    def test_failed_requests_fallback_complete_and_never_retry_on_resume(self):
        def fail(model, *args):
            raise RuntimeError("temporary model failure")
        model = Model(fail)
        store, model = self.run_case(chapter(3), model=model)
        self.assertEqual(len(model.calls), 2)
        self.assertTrue(all(r["speaker_id"] == "others" for r in store.read("export")["utterances"]))
        self.assertTrue(all(r["classification_source"] == "fallback" for r in store.read("analyze")["utterances"]))
        self.assertTrue(Pipeline(RunStore(store.directory), provider=model).run())
        self.assertEqual(len(model.calls), 2)

    def test_interruption_during_repair_keeps_committed_siblings(self):
        def interrupt(model, task, data, value):
            if len(model.calls) == 2:
                raise KeyboardInterrupt()
            value["utterances"][-1]["speaker_id"] = 99
            return value
        with tempfile.TemporaryDirectory() as root:
            store, first = store_for(root), Model(interrupt)
            runner = Pipeline(store, provider=first, detector=FakeDetector("Hana Mori"), extractor=lambda _: chapter(3))
            with self.assertRaises(KeyboardInterrupt):
                runner.run()
            second = Model()
            self.assertTrue(Pipeline(RunStore(store.directory), provider=second).run())
            self.assertEqual(len(second.calls), 1)
            self.assertEqual(len(second.calls[0][1]["targets"]), 1)

    def test_malformed_model_fields_fallback_without_crashing_chapter(self):
        def malformed(model, task, data, value):
            value["utterances"][0].update(content_type=[], speaker_id={"id": 1},
                addressee_type=[], addressee_ids={}, evidence={},
                mentions=[{"candidate_id": [], "mention_type": {}, "name_target_id": []}])
            return value
        store, model = self.run_case(chapter(1, "This is Hana Mori."), model=Model(malformed))
        row = store.read("export")["utterances"][0]
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(row["classification_source"], "fallback")
        self.assertEqual((row["content_type"], row["speaker_id"], row["addressee_type"], row["addressee_ids"]),
                         ("unknown", "others", "unknown", ["unknown"]))
        self.assertIsNone(row["mentions"][0]["name_target_id"])
        self.assertTrue(row["warnings"])

    def test_missing_name_coverage_keeps_independent_narration_and_audience(self):
        def omit_name(model, task, data, value):
            value["utterances"][0]["mentions"] = []
            return value
        store, model = self.run_case(chapter(1, "This is Hana Mori."), model=Model(omit_name))
        row = store.read("export")["utterances"][0]
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(row["classification_source"], "fallback")
        self.assertEqual((row["content_type"], row["speaker_id"], row["addressee_type"], row["addressee_ids"]),
                         ("narration", "narrator", "audience", ["public_audience"]))
        self.assertEqual(row["mentions"][0]["mention_type"], "unknown")
        self.assertIsNone(row["mentions"][0]["name_target_id"])
        self.assertFalse(read_json(store.manifest["bank_path"])["characters"]["1"].get("display_name"))

    def test_crash_after_vlm_or_bank_write_does_not_repeat_inference(self):
        for after_write in (False, True):
            with self.subTest(after_write=after_write), tempfile.TemporaryDirectory() as root:
                store, model = store_for(root), Model()
                runner = Pipeline(store, provider=model, detector=FakeDetector("Hana Mori"),
                                  extractor=lambda _: chapter(1, "This is Hana Mori."))
                real_bind = NameBank.bind
                def crash(bank, *args, **kwargs):
                    if after_write:
                        real_bind(bank, *args, **kwargs)
                    raise KeyboardInterrupt()
                with patch("manga_pipeline.analysis.binding.panel_image", return_value="image"), patch.object(NameBank, "bind", crash):
                    with self.assertRaises(KeyboardInterrupt):
                        runner.run()
                calls = len(model.calls)
                with patch("manga_pipeline.analysis.binding.panel_image", side_effect=AssertionError("VLM repeated")):
                    self.assertTrue(Pipeline(RunStore(store.directory), provider=model).run())
                self.assertEqual(len(model.calls), calls)
                bank = NameBank(store.manifest["bank_path"]).read()
                self.assertEqual(len(bank["characters"]["1"]["name_history"]), 1)

    def test_progress_uses_five_sequential_stages_and_cli_exposes_analyze(self):
        with tempfile.TemporaryDirectory() as root:
            stream = io.StringIO()
            runner = Pipeline(store_for(root), provider=Model(), detector=FakeDetector("Hana Mori"),
                              extractor=lambda _: chapter(3), progress=TerminalProgress(stream))
            self.assertTrue(runner.run())
            positions = [stream.getvalue().index(f"[{i}/5] {step}") for i, step in enumerate(ANALYZE_STEPS, 1)]
            self.assertEqual(positions, sorted(positions))
        self.assertEqual(build_parser().parse_args(["analyze", "--run-dir", "example"]).command, "analyze")
        self.assertEqual(load_config(device="cuda")["device"], "cuda")


class NamePriorityTests(unittest.TestCase):
    def test_introduction_promotes_display_keeps_alias_and_does_not_change_crops(self):
        with tempfile.TemporaryDirectory() as root:
            store = make_store(root, {"1": {"id": 1, "display_name": "Madol-san", "name_source": "model",
                "last_auto_name": "Madol-san", "crop_paths": ["crop.png"], "embedding": [0.1, 0.2]}})
            bank = NameBank(store.manifest["bank_path"])
            result = bank.bind("intro", 1, "Madoi Ayame", {}, source="introduction")
            character = bank.read()["characters"]["1"]
            self.assertTrue(result["promoted"])
            self.assertEqual(character["display_name"], "Madoi Ayame")
            self.assertIn("Madol-san", character["aliases"])
            self.assertEqual(character["embedding"], [0.1, 0.2])
            self.assertEqual(character["crop_paths"], ["crop.png"])
            self.assertEqual(bank.bind("intro", 1, "Madoi Ayame", {}, source="introduction"), result)
            result = bank.bind("another-intro", 1, "Ayame Madoi", {}, source="introduction")
            self.assertTrue(result["name_conflict"])
            self.assertEqual(bank.read()["characters"]["1"]["display_name"], "Madoi Ayame")

    def test_manual_name_protected_and_known_name_cannot_move_id(self):
        with tempfile.TemporaryDirectory() as root:
            store = make_store(root, {"1": {"id": 1, "display_name": "Manual Name"}, "2": {"id": 2}})
            bank = NameBank(store.manifest["bank_path"])
            self.assertEqual(bank.bind("intro", 1, "Hana Mori", {}, source="introduction")["status"], "protected")
            self.assertEqual(bank.read()["characters"]["1"]["display_name"], "Manual Name")
            with self.assertRaises(ValueError):
                bank.bind("wrong", 2, "Hana Mori", {}, source="introduction")


if __name__ == "__main__":
    unittest.main()
