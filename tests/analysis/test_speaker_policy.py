"""Versioned speaker rules, targeted repair and interrupted-run compatibility."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.helpers import FakeDetector
from tests.analysis.test_analyze import Model, chapter, store_for
from manga_pipeline.analysis import contract, pairs
from manga_pipeline.pipeline import Pipeline, load_config, reanalyze_run
from manga_pipeline.storage.io import atomic_json, file_hash, fingerprint, read_json
from manga_pipeline.storage.runs import RunStore

POLICY = "individual-v1"


class SpeakerPolicyTests(unittest.TestCase):
    def run_case(self, model, *, text="Hello!", characters=None, count=3):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        store = store_for(temporary.name, characters)
        runner = Pipeline(store, provider=model, detector=FakeDetector("Hana Mori"),
                          extractor=lambda _: chapter(count, text))
        self.assertTrue(runner.run())
        return runner.store

    def test_small_schema_prompt_and_saved_contract_exclude_joint_speakers(self):
        model = Model()
        store = self.run_case(model)
        self.assertEqual(load_config()["speaker_policy"], POLICY)
        self.assertEqual(load_config(Path(__file__).resolve().parents[2] / "configs/pipeline.json")["speaker_policy"], POLICY)
        self.assertEqual(store.manifest["config"]["speaker_policy"], POLICY)
        self.assertEqual(read_json(store.directory / "snapshots/config.json")["speaker_policy"], POLICY)
        self.assertEqual(store.read("analyze")["speaker_policy"], POLICY)
        prompt, schema = model.contracts[0]
        self.assertNotIn("groups", prompt)
        branches = schema["properties"]["utterances"]["items"]["anyOf"]
        self.assertEqual(len(branches), 3)
        for branch in branches:
            self.assertEqual(branch["properties"]["speaker_id"]["enum"], [1, 2, "others", "narrator"])
        base = store.artifact_path("analyze").parent
        for path in [*base.joinpath("plans").glob("*.json"), *base.joinpath("attempts").glob("*.json")]:
            self.assertEqual(read_json(path)["request"]["speaker_policy"], POLICY)
        self.assertEqual(model.calls[0][1]["speaker_policy"], POLICY)

    def test_create_stamps_missing_policy_without_changing_caller_config(self):
        with tempfile.TemporaryDirectory() as root:
            source = store_for(root)
            config = load_config()
            config.pop("speaker_policy")
            fresh = RunStore.create(Path(root) / "fresh", source=source.manifest["source"],
                                    config=config, bank_path=source.manifest["bank_path"])
            self.assertNotIn("speaker_policy", config)
            self.assertEqual(fresh.manifest["config"]["speaker_policy"], POLICY)
            for invalid in (None, "groups-v1"):
                config["speaker_policy"] = invalid
                path = Path(root) / "config.json"
                atomic_json(path, config)
                with self.assertRaisesRegex(ValueError, "speaker_policy"):
                    load_config(path)

    def test_only_narrator_requires_narration(self):
        target = {"utterance_id": "u", "identity_candidates": [{"id": 1}, {"id": 2}], "candidates": []}
        base = {"utterance_id": "u", "content_type": "dialogue", "speaker_id": "narrator",
                "addressee_type": "single", "addressee_ids": [2], "mentions": [], "evidence": "Question"}
        for kind in ("dialogue", "thought", "unknown"):
            with self.subTest(kind=kind):
                row = {**base, "content_type": kind}
                if kind == "thought":
                    row.update(addressee_type="self", addressee_ids=["self"])
                self.assertEqual(pairs.check(row, target, POLICY),
                                 [{"code": "narrator_requires_narration", "fields": ["speaker_id"]}])
                self.assertEqual(pairs.check(row, target), [])
                fallback, affected = pairs.fallback(row, target, pairs.check(row, target, POLICY), POLICY)
                self.assertEqual(fallback, {**row, "speaker_id": "others"})
                self.assertFalse(affected)
        for speaker in ("narrator", 1, "others"):
            self.assertEqual(pairs.check({**base, "content_type": "narration", "speaker_id": speaker}, target, POLICY), [])
        for speaker in ("groups", 99):
            self.assertEqual(pairs.check({**base, "speaker_id": speaker}, target, POLICY),
                             [{"code": "invalid_speaker_id", "fields": ["speaker_id"]}])

    def test_narration_accepts_narrator_character_and_others_without_repair(self):
        def narration(model, task, data, value):
            for row, speaker in zip(value["utterances"], ("narrator", 1, "others")):
                row.update(content_type="narration", speaker_id=speaker)
            return value
        model = Model(narration)
        store = self.run_case(model)
        self.assertEqual(len(model.calls), 1)
        self.assertEqual([r["speaker_id"] for r in store.read("export")["utterances"]], ["narrator", 1, "others"])

    def test_narrator_repair_locks_type_recipients_names_and_evidence(self):
        def repair(model, task, data, value):
            row = value["utterances"][0]
            if len(model.calls) == 1:
                row.update(content_type="dialogue", speaker_id="narrator", addressee_type="single",
                           addressee_ids=[2], evidence="Calling Hana Mori")
                row["mentions"][0].update(mention_type="direct_address", name_target_id=2)
            else:
                row.update(content_type="narration", speaker_id="others", addressee_type="unknown",
                           addressee_ids=["unknown"], evidence="Changed independent fields")
                row["mentions"][0].update(mention_type="unknown", name_target_id=None)
            return value
        model = Model(repair)
        store = self.run_case(model, text="Hana Mori, can you hear me?",
                              characters={"1": {"id": 1}, "2": {"id": 2, "display_name": "Hana Mori"}})
        self.assertEqual([len(c[1]["targets"]) for c in model.calls], [3, 1])
        errors = model.calls[1][1]["validation_errors"][0]
        self.assertEqual(errors["editable"], ["speaker_id"])
        self.assertEqual(errors["issues"], [{"code": "narrator_requires_narration", "fields": ["speaker_id"]}])
        row = store.read("export")["utterances"][0]
        self.assertEqual((row["content_type"], row["speaker_id"], row["addressee_type"], row["addressee_ids"]),
                         ("dialogue", "others", "single", [2]))
        self.assertEqual(row["mentions"][0]["mention_type"], "direct_address")
        self.assertEqual(row["mentions"][0]["name_target_id"], 2)
        self.assertEqual(row["evidence"], "Calling Hana Mori")
        self.assertEqual(row["classification_source"], "repaired")

    def test_groups_repair_uses_others_and_targets_only_invalid_speaker(self):
        def joint(model, task, data, value):
            value["utterances"][0]["speaker_id"] = "groups" if len(model.calls) == 1 else "others"
            return value
        model = Model(joint)
        store = self.run_case(model)
        self.assertEqual([len(c[1]["targets"]) for c in model.calls], [3, 1])
        self.assertEqual(model.calls[1][1]["validation_errors"][0]["editable"], ["speaker_id"])
        row = store.read("export")["utterances"][0]
        self.assertEqual(row["speaker_id"], "others")
        self.assertEqual(row["classification_source"], "repaired")

    def test_thought_narrator_repair_keeps_type_and_recomputes_self_pair(self):
        def thought(model, task, data, value):
            value["utterances"][0].update(content_type="thought" if len(model.calls) == 1 else "narration",
                speaker_id="narrator" if len(model.calls) == 1 else 1,
                addressee_type="self", addressee_ids=["self"], evidence="Inner speech")
            return value
        model = Model(thought)
        store = self.run_case(model, count=1)
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(model.calls[1][1]["validation_errors"][0]["editable"], ["speaker_id"])
        row = store.read("export")["utterances"][0]
        self.assertEqual((row["content_type"], row["speaker_id"], row["addressee_type"], row["addressee_ids"]),
                         ("thought", 1, "self", [1]))

    def test_failed_speaker_repairs_keep_independent_fields_and_complete(self):
        for kind in ("dialogue", "thought"):
            for failure in ("narrator", "groups", "exception", "missing"):
                with self.subTest(kind=kind, failure=failure):
                    def fail(model, task, data, value):
                        if len(model.calls) == 2:
                            if failure == "exception":
                                raise RuntimeError("repair unavailable")
                            if failure == "missing":
                                return {"utterances": []}
                        value["utterances"][0].update(content_type=kind,
                            speaker_id=failure if len(model.calls) == 2 else "narrator",
                            addressee_type="self" if kind == "thought" else "single",
                            addressee_ids=["self"] if kind == "thought" else [2], evidence="Independent cue")
                        return value
                    model = Model(fail)
                    store = self.run_case(model, count=2)
                    row = store.read("export")["utterances"][0]
                    self.assertEqual(len(model.calls), 2)
                    self.assertEqual((row["content_type"], row["speaker_id"], row["addressee_ids"], row["evidence"]),
                                     (kind, "others", ["self"] if kind == "thought" else [2], "Independent cue"))
                    self.assertEqual(row["classification_source"], "fallback")
                    self.assertTrue(any(w["code"] == "unresolved_after_repair" for w in row["warnings"]))
                    before = {s: file_hash(store.artifact_path(s)) for s in store.steps}
                    self.assertTrue(Pipeline(RunStore(store.directory), provider=model).run())
                    self.assertEqual(len(model.calls), 2)
                    self.assertEqual(before, {s: file_hash(store.artifact_path(s)) for s in store.steps})

    def test_failed_repair_journals_survive_crash_before_target_commit(self):
        def narrator(model, task, data, value):
            value["utterances"][0]["speaker_id"] = "narrator"
            return value
        with tempfile.TemporaryDirectory() as root:
            store, model = store_for(root), Model(narrator)
            runner = Pipeline(store, provider=model, detector=FakeDetector(), extractor=lambda _: chapter(1))
            commit = RunStore.commit_target
            def crash(current, step, *args, **kwargs):
                if step == "analyze":
                    raise KeyboardInterrupt()
                return commit(current, step, *args, **kwargs)
            with patch.object(RunStore, "commit_target", crash):
                with self.assertRaises(KeyboardInterrupt):
                    runner.run()
            paths = list(store.artifact_path("analyze").parent.joinpath("attempts").glob("*.json"))
            self.assertEqual(len(paths), 2)
            before = {p: file_hash(p) for p in paths}
            self.assertTrue(Pipeline(RunStore(store.directory), provider=model).run())
            self.assertEqual(len(model.calls), 2)
            self.assertEqual(before, {p: file_hash(p) for p in paths})
            self.assertEqual(RunStore(store.directory).read("export")["utterances"][0]["speaker_id"], "others")

    def test_old_unfinished_v3_keeps_contract_plans_journals_and_committed_hashes(self):
        def old_speakers(model, task, data, value):
            if len(model.calls) == 2:
                raise KeyboardInterrupt()
            for index, row in enumerate(value["utterances"]):
                row.update(content_type="dialogue", speaker_id="narrator" if index == 0 else "groups")
            return value
        with tempfile.TemporaryDirectory() as root:
            store, first = store_for(root, legacy=True), Model(old_speakers)
            runner = Pipeline(store, provider=first, detector=FakeDetector(), extractor=lambda _: chapter(4))
            with self.assertRaises(KeyboardInterrupt):
                runner.run()
            base = store.artifact_path("analyze").parent
            paths = [store.directory / "snapshots/config.json", *base.rglob("*.json")]
            before = {p: file_hash(p) for p in paths}
            second = Model()
            self.assertTrue(Pipeline(RunStore(store.directory), provider=second).run())
            store = RunStore(store.directory)
            self.assertEqual(len(second.calls), 1)
            self.assertEqual(before, {p: file_hash(p) for p in paths})
            self.assertNotIn("speaker_policy", second.calls[0][1])
            self.assertEqual(second.contracts[0][0], contract.PROMPT)
            self.assertIn("groups", second.contracts[0][1]["properties"]["utterances"]["items"]["anyOf"][0]["properties"]["speaker_id"]["enum"])
            self.assertEqual([r["speaker_id"] for r in store.read("export")["utterances"]][:3], ["narrator", "groups", "groups"])
            for path in [*base.joinpath("plans").glob("*.json"), *base.joinpath("attempts").glob("*.json")]:
                self.assertNotIn("speaker_policy", read_json(path)["request"])
            self.assertNotIn("speaker_policy", store.read("analyze"))
            preserved = {p: file_hash(p) for p in [store.path, store.directory / "snapshots/config.json", *base.rglob("*.json")]}
            fresh = reanalyze_run(store.directory, Path(root) / "reanalyzed", load_config())
            self.assertEqual(fresh.manifest["config"]["speaker_policy"], POLICY)
            self.assertEqual(fresh.read("extract"), store.read("extract"))
            self.assertEqual(preserved, {p: file_hash(p) for p in preserved})

    def test_plan_and_attempt_policy_must_match_saved_run(self):
        for journal in (False, True):
            with self.subTest(journal=journal), tempfile.TemporaryDirectory() as root:
                store, model = store_for(root), Model()
                runner = Pipeline(store, provider=model, detector=FakeDetector(), extractor=lambda _: chapter(1))
                commit = RunStore.commit_target
                def crash(current, step, *args, **kwargs):
                    if step == "analyze":
                        raise KeyboardInterrupt()
                    return commit(current, step, *args, **kwargs)
                with patch.object(RunStore, "commit_target", crash):
                    with self.assertRaises(KeyboardInterrupt):
                        runner.run()
                base = store.artifact_path("analyze").parent
                path = base / ("attempts/batch-000000-0.json" if journal else "plans/batch-000000.json")
                saved = read_json(path)
                saved["request"].pop("speaker_policy")
                if journal:
                    saved["request_hash"] = fingerprint(saved["request"])
                else:
                    saved["hash"] = fingerprint([saved["request"], saved["bank"]])
                atomic_json(path, saved)
                with self.assertRaisesRegex(ValueError, "speaker policy changed"):
                    Pipeline(RunStore(store.directory), provider=model).run()
                self.assertEqual(len(model.calls), 1)


if __name__ == "__main__":
    unittest.main()
