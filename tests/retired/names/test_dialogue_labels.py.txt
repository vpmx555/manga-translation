import copy
import tempfile
import unittest
from pathlib import Path

from tests.helpers import FakeDetector, make_store, raw_document
from manga_pipeline.names import stages
from manga_pipeline.names.dialogue import compact_payload, response_schema, validate_response
from manga_pipeline.normalization.dialogue import normalize_document
from manga_pipeline.pipeline import Pipeline, load_config
from manga_pipeline.providers.ollama import Ollama
from manga_pipeline.review.renderer import Review
from manga_pipeline.storage.io import atomic_json, file_hash, fingerprint, read_json
from manga_pipeline.storage.runs import RunStore


CASES = {
    "single": ("dialogue", 1, [2], "single"),
    "single unresolved": ("dialogue", "others", ["unknown"], "single"),
    "group": ("dialogue", "others", [1, 2], "group"),
    "partial group": ("dialogue", 1, [2, "unknown"], "group"),
    "crowd": ("dialogue", 1, ["unknown"], "group"),
    "readers": ("dialogue", 1, ["public_audience"], "audience"),
    "self": ("dialogue", 1, [1], "self"),
    "self unresolved": ("dialogue", "others", ["self"], "self"),
    "unresolved": ("unknown", "others", ["unknown"], "unknown"),
    "narrator": ("narration", "narrator", ["public_audience"], "audience"),
    "character narration": ("narration", 2, ["public_audience"], "audience"),
    "thought": ("thought", 2, [2], "self"),
    "thought unresolved": ("thought", "others", ["self"], "self"),
}


def item(case="single", utterance_id="u"):
    kind, speaker, listeners, address = CASES[case]
    return {"utterance_id": utterance_id, "content_type": kind, "speaker_id": speaker,
            "addressee_ids": list(listeners), "addressee_type": address,
            "mentions": [], "evidence": "Context supports this recipient"}


def chapter(texts):
    raw = raw_document((1, 2))
    source = raw["texts"][0]
    raw["texts"] = [{**copy.deepcopy(source), "id": f"text{i}", "reading_order": i,
                     "text": text, "speaker_id": 1 if i % 2 == 0 else 2}
                    for i, text in enumerate(texts)]
    return raw


class LabelProvider:
    def __init__(self):
        self.calls = []

    def infer(self, task, prompt, data, schema, validator, images=None):
        if task != "classify_dialogue" or images:
            raise AssertionError("Unexpected task or visual input")
        self.calls.append(copy.deepcopy(data))
        turns = {x["id"]: x["text"] for x in data["turns"]}
        rows = []
        for target in data["targets"]:
            text = turns[target["utterance_id"]]
            value = item(text if text in CASES else "single", target["utterance_id"])
            if data["policy"] == "essential-dialogue-v1":
                value["addressee_type"] = "individual"
            value["mentions"] = [{"candidate_id": c["id"], "mention_type": "direct_address",
                                  "name_target_id": 2} for c in target["candidates"]]
            rows.append(value)
        result = {"utterances": rows}
        validator(result)
        return result


class DialogueLabelTests(unittest.TestCase):
    def setUp(self):
        self.target = {"utterance_id": "u", "identity_candidates": [{"id": 1}, {"id": 2}], "candidates": []}

    def validate(self, value, target=None):
        validate_response({"utterances": [value]}, [target or self.target], analysis_policy="essential-v2")

    def test_all_valid_label_combinations(self):
        for case in CASES:
            with self.subTest(case=case):
                self.validate(item(case))

    def test_rejects_conflicting_roles_cardinality_and_sentinels(self):
        invalid = [
            {"speaker_id": "UNKNOWN"}, {"speaker_id": True}, {"speaker_id": 99},
            {"speaker_id": "self"}, {"speaker_id": "narrator"},
            {"addressee_ids": []}, {"addressee_ids": [2, 2]}, {"addressee_ids": [True]},
            {"addressee_ids": [99]}, {"addressee_ids": ["others"]},
            {"addressee_type": "unspecified"}, {"addressee_type": "individual"},
            {"addressee_type": "not_applicable", "addressee_ids": ["NOT_APPLICABLE"]},
            {"addressee_type": "unknown"}, {"addressee_ids": [1]},
            {"addressee_type": "single", "addressee_ids": [2, "unknown"]},
            {"addressee_type": "single", "addressee_ids": ["public_audience"]},
            {"addressee_type": "group"},
            {"addressee_type": "group", "addressee_ids": ["public_audience", "unknown"]},
            {"addressee_type": "group", "addressee_ids": [1, 2]},
            {"addressee_type": "audience"},
            {"addressee_type": "self", "addressee_ids": ["self"]},
            {"addressee_type": "self", "speaker_id": "others", "addressee_ids": [1]},
            {"content_type": "thought"},
            {"content_type": "narration", "speaker_id": "others",
             "addressee_type": "audience", "addressee_ids": ["public_audience"]},
            {"content_type": "narration", "speaker_id": "narrator", "addressee_type": "group",
             "addressee_ids": ["unknown"]},
        ]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.validate({**item(), **changes})

    def test_schema_version_and_per_target_identity_limits(self):
        other = {**self.target, "utterance_id": "v", "identity_candidates": [{"id": 3}]}
        new = response_schema([self.target, other], analysis_policy="essential-v2")
        branches = new["properties"]["utterances"]["items"]["anyOf"]
        self.assertEqual({b["properties"]["addressee_type"]["const"] for b in branches},
                         {"single", "group", "audience", "self", "unknown"})
        for branch in branches:
            properties = branch["properties"]
            speaker = properties["speaker_id"]
            speakers = speaker.get("enum", [speaker.get("const")])
            pool = {1, 2} if properties["utterance_id"]["const"] == "u" else {3}
            self.assertTrue(set(speakers) <= pool | {"others", "narrator"})
        with self.assertRaises(ValueError):
            self.validate({**item(), "addressee_ids": [3]})
        old = response_schema([self.target])["properties"]["utterances"]["items"]["properties"]
        self.assertIn("unspecified", old["addressee_type"]["enum"])
        self.assertNotIn("single", old["addressee_type"]["enum"])
        self.assertIn("UNKNOWN", old["speaker_id"]["enum"])

    def test_schema_prevents_observed_group_and_narrator_errors(self):
        branches = response_schema([self.target], analysis_policy="essential-v2")[
            "properties"]["utterances"]["items"]["anyOf"]
        fields = [b["properties"] for b in branches]
        group = next(p for p in fields if p["speaker_id"] == {"const": 1}
                     and p["addressee_type"] == {"const": "group"})
        self.assertNotIn([2], group["addressee_ids"]["enum"])
        self.assertNotIn([1, 2], group["addressee_ids"]["enum"])
        self.assertIn(["unknown"], group["addressee_ids"]["enum"])
        self.assertIn([2, "unknown"], group["addressee_ids"]["enum"])
        narration = next(p for p in fields if p["content_type"] == {"enum": ["narration"]})
        self.assertNotIn("others", narration["speaker_id"]["enum"])
        self.assertIn("narrator", narration["speaker_id"]["enum"])
        self.assertEqual(narration["addressee_ids"], {"const": ["public_audience"]})
        self.assertEqual(narration["addressee_type"], {"const": "audience"})

    def test_repair_feedback_uses_short_ids_for_the_failed_targets(self):
        request = {"targets": [{**self.target, "context_ids": ["u"]}], "turns": [{"id": "u", "text": "Hello"}],
                   "validation_errors": [{"utterance_id": "u", "error": "Invalid group"}]}
        payload, _ = compact_payload(request)
        self.assertEqual(payload["validation_errors"][0]["utterance_id"], payload["targets"][0]["utterance_id"])
        self.assertEqual(payload["validation_errors"][0]["utterance_id"], "u0")
        self.assertEqual(request["validation_errors"][0]["utterance_id"], "u")

    def test_mixed_model_response_commits_good_targets_without_caching_bad_batch(self):
        import json

        with tempfile.TemporaryDirectory() as root:
            store = make_store(root)
            doc = normalize_document(chapter(["single"] * 3), essential_only=True, analysis_policy="essential-v2")
            scanned = stages.scan(store, doc, FakeDetector())
            cache = Path(root) / "cache"
            provider = Ollama(load_config()["ollama"], cache)
            calls = []

            def http(route, body=None):
                if route == "/api/tags":
                    return {"models": [{"name": provider.config["model"], "digest": "test"}]}
                data = json.loads(body["messages"][1]["content"])
                calls.append(data)
                values = [item("single", t["utterance_id"]) for t in data["targets"]]
                if len(calls) == 1:
                    values[-1]["addressee_type"] = "group"
                return {"done": True, "message": {"content": json.dumps({"utterances": values})}}

            provider.http = http
            result = stages.classify(store, doc, scanned, provider)
            self.assertFalse(result["failures"])
            self.assertEqual([len(x["targets"]) for x in calls], [3, 1])
            self.assertEqual(calls[1]["validation_errors"][0]["utterance_id"], calls[1]["targets"][0]["utterance_id"])
            cached = list(cache.glob("*.json"))
            self.assertEqual(len(cached), 1)
            self.assertEqual(len(read_json(cached[0])["result"]["utterances"]), 1)
            self.assertEqual(stages.classify(store, doc, scanned, provider), result)
            self.assertEqual(len(calls), 2)

    def test_names_only_link_to_supported_stable_direct_listeners(self):
        target = {**self.target, "candidates": [{"id": "name", "mapped_ids": [2]}]}
        mention = {"candidate_id": "name", "mention_type": "direct_address", "name_target_id": 2}
        self.validate({**item("partial group"), "mentions": [mention]}, target)
        for changes in [{"name_target_id": "unknown"}, {"name_target_id": 1},
                        {"name_target_id": True}, {"mention_type": "third_person"}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.validate({**item(), "mentions": [{**mention, **changes}]}, target)
        with self.assertRaises(ValueError):
            self.validate({**item("character narration"), "mentions": [mention]}, target)

    def test_v2_pipeline_export_review_and_resume(self):
        with tempfile.TemporaryDirectory() as root:
            old = make_store(root)
            config = load_config()
            config["dialogue_analysis"] = "essential-v2"
            store = RunStore.create(Path(root) / "fresh", source=old.manifest["source"],
                                    config=config, bank_path=old.manifest["bank_path"])
            raw = chapter(list(CASES))
            raw["texts"].append({**raw["texts"][-1], "id": "noise", "reading_order": len(CASES),
                                 "text": "TRANSLATION ONLY", "is_essential_text": False})
            before_bank = file_hash(Path(store.manifest["bank_path"]))
            provider = LabelProvider()
            self.assertTrue(Pipeline(store, provider=provider, detector=FakeDetector(), extractor=lambda _: raw).run())
            store = RunStore(store.directory)
            output = store.read("export")
            self.assertEqual(output["analysis_policy"], "essential-v2")
            for row, case in zip(output["utterances"], CASES):
                expected = item(case)
                for key in ("content_type", "speaker_id", "addressee_ids", "addressee_type"):
                    self.assertEqual(row[key], expected[key])
            self.assertEqual(output["utterances"][10]["voice_type"], "character")
            self.assertEqual(output["utterances"][9]["voice_type"], "narrator")
            self.assertEqual(output["utterances"][1]["voice_type"], "unknown")
            self.assertEqual(output["translation_only"][0]["speaker_id"], None)
            self.assertEqual(before_bank, file_hash(Path(store.manifest["bank_path"])))
            review = Review(store)
            for step in ("classify", "export"):
                text = review.render_step(step)
                self.assertIn("public_audience", text)
                self.assertIn("others", text)
                self.assertIn("Ứng viên nghe", text)
                self.assertIn("Addressee type", text)
                self.assertNotIn("ID public_audience", text)
            self.assertIn("4 câu chưa rõ hoặc chưa đủ ID người nghe", review.render_index())
            calls = len(provider.calls)
            self.assertTrue(Pipeline(RunStore(store.directory), provider=provider, detector=FakeDetector(),
                                     extractor=lambda _: self.fail("Extraction repeated")).run())
            self.assertEqual(len(provider.calls), calls)

    def test_v2_direct_name_link_preserves_bank_identity_and_assets(self):
        with tempfile.TemporaryDirectory() as root:
            store = make_store(root, {"1": {"id": 1, "display_name": None},
                                      "2": {"id": 2, "display_name": "Madol Arase", "crop_paths": ["a.png"],
                                            "embedding": [0.2]}})
            raw = chapter(["Hello, Madol-san!", "single", "single"])
            doc = normalize_document(raw, essential_only=True, analysis_policy="essential-v2")
            provider = LabelProvider()
            scanned = stages.scan(store, doc, FakeDetector("Madol-san"))
            classified = stages.classify(store, doc, scanned, provider)
            self.assertFalse(classified["failures"])
            result = stages.link(store, raw, doc, classified, provider)
            self.assertFalse(result["failures"])
            person = stages.NameBank(store.manifest["bank_path"]).read()["characters"]["2"]
            self.assertEqual(person["id"], 2)
            self.assertEqual(person["display_name"], "Madol Arase")
            self.assertEqual(person["aliases"], ["Madol-san"])
            self.assertEqual(person["crop_paths"], ["a.png"])
            self.assertEqual(person["embedding"], [0.2])
            self.assertEqual(len(provider.calls), 1)

    def test_interrupt_repair_reuses_frozen_targets_for_both_policies(self):
        for policy in ("essential-v1", "essential-v2"):
            with self.subTest(policy=policy), tempfile.TemporaryDirectory() as root:
                old = make_store(root)
                config = load_config()
                config["dialogue_analysis"] = policy
                store = RunStore.create(Path(root) / "fresh", source=old.manifest["source"],
                                        config=config, bank_path=old.manifest["bank_path"])
                raw = chapter(["single"] * 3)

                class Interrupted(LabelProvider):
                    def infer(self, *args, **kwargs):
                        if self.calls:
                            raise KeyboardInterrupt()
                        value = super().infer(*args, **kwargs)
                        value["utterances"][-1]["addressee_type"] = "unknown" if policy == "essential-v2" else "self"
                        return value

                with self.assertRaises(KeyboardInterrupt):
                    Pipeline(store, provider=Interrupted(), detector=FakeDetector(), extractor=lambda _: raw).run("classify")
                paths = list((store.directory / "04_classify" / "plans").glob("*.json"))
                paths += list((store.directory / "04_classify" / "targets").glob("*.json"))
                hashes = {p: file_hash(p) for p in paths}
                provider = LabelProvider()
                self.assertTrue(Pipeline(RunStore(store.directory), provider=provider,
                                         extractor=lambda _: self.fail("Extraction repeated")).run("classify"))
                self.assertEqual([len(x["targets"]) for x in provider.calls], [1])
                self.assertEqual(provider.calls[0]["policy"], "essential-dialogue-" + policy.split("-")[-1])
                self.assertEqual(hashes, {p: file_hash(p) for p in paths})
                address = "single" if policy == "essential-v2" else "individual"
                self.assertTrue(all(x["addressee_type"] == address
                                    for x in RunStore(store.directory).read("classify")["utterances"]))

    def test_frozen_plan_cannot_cross_policy_versions(self):
        with tempfile.TemporaryDirectory() as root:
            store = make_store(root)
            doc = normalize_document(chapter(["single"] * 3), essential_only=True, analysis_policy="essential-v2")
            scan = stages.scan(store, doc, FakeDetector())
            self.assertFalse(stages.classify(store, doc, scan, LabelProvider())["failures"])
            path = store.directory / "04_classify" / "plans" / "batch-000000.json"
            plan = read_json(path)
            plan["request"]["policy"] = "essential-dialogue-v1"
            plan["hash"] = fingerprint(plan["request"])
            atomic_json(path, plan)
            with self.assertRaisesRegex(ValueError, "plan changed/corrupt"):
                stages.classify(store, doc, scan, LabelProvider())


if __name__ == "__main__":
    unittest.main()
