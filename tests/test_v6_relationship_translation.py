"""Offline graph, crop ranking, natural-only batching and resume checks."""
import copy
import base64
import io
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from manga_pipeline.pipeline import load_config
from manga_pipeline.storage.io import atomic_json, read_json
from manga_pipeline.runtime.inference_state import InferenceUncertain
from manga_pipeline.runtime.resources import ResourceExhausted
from manga_pipeline.runtime.llm_budget import OutputTruncated
from manga_pipeline.v6 import relationships as graph, translation_parallel as translation
from manga_pipeline.v6.scheduling import Admission, run
from manga_pipeline.translation.contract import STYLES


class Store:
    def __init__(self, root):
        self.directory = root
        self.manifest = {"config": load_config(), "source": {"story": "test"}, "bank_path": str(root / "bank/bank.json")}
        self.values, self.commits = {}, {}
        atomic_json(Path(self.manifest["bank_path"]), {"characters": {}})

    def artifact_path(self, step):
        return self.directory / step / "result.json"

    def read(self, step):
        return copy.deepcopy(self.values[step])

    def target(self, step, key, request):
        from manga_pipeline.storage.io import fingerprint
        return copy.deepcopy(self.commits.get((step, key, fingerprint(request))))

    def commit_target(self, step, key, request, result=None, **kwargs):
        from manga_pipeline.storage.io import fingerprint
        if result is not None:
            self.commits[(step, key, fingerprint(request))] = copy.deepcopy(result)


def state(count=8):
    return {"extraction_scope": "s", "persons": {p: {"primary_name": None, "name_forms": []} for p in ("p1", "p2")},
            "redirects": {}, "cluster_to_person": {"s:a": "p1", "s:b": "p2"},
            "observations": {"s:a": [{"source_bank_id": 5 if i < 6 else 14, "page_id": "page",
                "bbox": [i, 0, i+1, 1]} for i in range(count)],
                "s:b": [{"source_bank_id": 8, "page_id": "page", "bbox": [i, 2, i+1, 3]} for i in range(count)]}}


def rows(count=4):
    return [{"id": f"u{i}", "reading_order": i, "text": "Hello.", "speaker_person_id": "p1" if i%2 == 0 else "p2",
             "addressee_ids": ["p2" if i%2 == 0 else "p1"], "mentions": [], "semantic_resolution": "resolved",
             "content_type": "dialogue", "speech_style": "normal", "page_id": "page"} for i in range(count)]


def relation(a="p1", b="p2", basis="dialogue"):
    return {"a": a, "b": b, "relationship": "classmates", "role_a": "student", "role_b": "student",
            "a_to_b": {"self": "tớ", "address": "cậu"}, "b_to_a": {"self": "tớ", "address": "cậu"},
            "basis": basis, "evidence_refs": ["u0"], "reason": "Dialogue supports this choice."}


class Provider:
    def __init__(self):
        self.calls, self.active, self.peak = [], 0, 0
        self.lock = threading.Lock()
        self.fail_id, self.uncertain, self.limit, self.force_split = None, False, 32768, False

    def request_budget(self, task, prompt, data, schema, images=None, *, reserve=None):
        estimate = 40000 if self.force_split and len(data["targets"]) > 1 else 1000
        return {"estimate": estimate, "limit": self.limit, "output_reserve": reserve or 2048,
                "input_tokens": 100, "num_ctx": 32768}

    def metrics(self):
        return {"kv": .2, "preemptions": 0, "generated_tokens": 0}

    def healthy(self):
        return True

    def infer_reserved(self, task, prompt, data, schema, validator, *, reserve):
        return self.infer(task, prompt, data, schema, validator, reserve=reserve)

    def infer(self, task, prompt, data, schema, validator, images=None, **kwargs):
        with self.lock:
            self.calls.append((task, copy.deepcopy(data), bool(images)))
            self.active += 1
            self.peak = max(self.active, self.peak)
        try:
            time.sleep(.01)
            if task == "relationship_vi_v1":
                return relation(data["a"], data["b"])
            if any(r["id"] == self.fail_id for r in data["targets"]):
                if self.uncertain:
                    raise OSError("Connection lost after POST")
                return {"translations": {}}
            return {"translations": {r["id"]: {"translation": "Xin chào.", "needs_review": False,
                        "review_reason": "", "glossary_proposals": []} for r in data["targets"]}}
        finally:
            with self.lock:
                self.active -= 1


def document(count=4):
    utterances = rows(count)
    batches = [{"batch_id": f"batch:{i//2+1}", "rows": utterances[i:i+2],
                "request": {"scene_id": "scene:1", "scene_summary": "People talk."}} for i in range(0,count,2)]
    return {"utterances": utterances, "effects": [], "batches": batches, "person_registry_snapshot": state()}


class RelationshipTests(unittest.TestCase):
    def test_threshold_current_chapter_and_same_identity_sum(self):
        s = state()
        s["observations"]["s:a"].append(copy.deepcopy(s["observations"]["s:a"][0]))
        s["observations"]["old:a"] = [{"source_bank_id": 5, "page_id": "old", "bbox": [0,0,1,1]}]*100
        s["cluster_to_person"]["old:a"] = "p1"
        candidates = graph.candidates(s, 8, graph.empty_graph())
        self.assertEqual(candidates["p1"]["chapter_appearances"], 8)
        self.assertEqual(candidates["p1"]["magi_ids"], ["14", "5"])
        self.assertEqual(graph.candidates(s, 9, graph.empty_graph()), {})
        prior = {**graph.empty_graph(), "identities": {"p1": candidates["p1"]}}
        self.assertIn("p1", graph.candidates(s, 100, prior))

    def test_crops_rank_distance_per_magi_from_entire_bank(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            root = Path(store.manifest["bank_path"]).parent
            record = {"prototype_crop_paths": [f"c{i}.png" for i in range(4)], "disabled_prototype_indices": [3]}
            atomic_json(root / "bank.json", {"characters": {"5": record}})
            for i in range(4):
                Image.new("RGB", (20,20), (i*60,0,0)).save(root / f"c{i}.png")
            (root / "embeddings").mkdir()
            np.savez(root / "embeddings/vectors.npz", character_5=np.array([[1,0],[.99,.1],[0,1],[-1,0]],dtype=np.float32))
            crops, issues = graph.crop_selection(root / "bank.json", ["5"], 3, 384)
            self.assertEqual(len(crops), 3)
            self.assertEqual([c["distance"] for c in crops], sorted(c["distance"] for c in crops))
            self.assertEqual(issues, [])
            self.assertTrue(all("image" in c and c["sha256"] for c in crops))

    def test_image_age_is_not_factual_social_role_and_unknown_stays_unknown(self):
        request = {"a": "p1", "b": "p2", "targets": [{"id": "u0"}],
                   "crops": [{"crop_id": "c0", "person_id": "p1"}, {"crop_id": "c1", "person_id": "p2"}]}
        value = {**relation(basis="visual_age_estimate"), "evidence_refs": ["c0", "c1"]}
        validated = graph.validate(value, request)
        self.assertEqual(validated["relationship"], "unknown")
        self.assertEqual(validated["a_to_b"]["self"], "tớ")
        validated = graph.validate(relation(basis="unknown"), request)
        self.assertEqual(validated["a_to_b"], {"self": "unknown", "address": "unknown"})
        with self.assertRaises(ValueError):
            graph.validate({**value, "evidence_refs": ["imaginary"]}, request)

    def test_append_only_reuse_and_mirrored_edges(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            provider = Provider()
            result = graph.build(store, document(), provider)
            self.assertEqual(len(provider.calls), 1)
            edges = result["view"]["edges"]
            self.assertEqual(len([e for e in edges if e["relationship"] == "same_identity"]), 1)
            external = [e for e in edges if e["relationship"] != "same_identity"]
            self.assertEqual(len(external), 2)
            self.assertEqual(external[0]["a_to_b"], external[1]["a_to_b"])
            self.assertEqual(graph.build(store, document(), provider), result)
            self.assertEqual(len(provider.calls), 1)
            later = Store(Path(directory)/"chapter2")
            later.manifest["bank_path"] = store.manifest["bank_path"]
            doc = document()
            doc["person_registry_snapshot"] = state(1)
            reused = graph.build(later, doc, provider)
            self.assertEqual(reused["address_table"], result["address_table"])
            self.assertEqual(len(provider.calls), 1)

    def test_no_interaction_does_not_create_all_pairs(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            doc = document()
            for row in doc["utterances"]:
                row["addressee_ids"] = []
            provider = Provider()
            result = graph.build(store, doc, provider)
            self.assertEqual(result["address_table"], [])
            self.assertEqual(provider.calls, [])

    def test_image_request_retains_reference_crops_and_does_not_invent_social_role(self):
        class Visual(Provider):
            def infer(self, task, prompt, data, schema, validator, images=None, **kwargs):
                self.calls.append((task, copy.deepcopy(data), bool(images)))
                return {**relation(data["a"], data["b"], "visual_age_estimate"),
                        "evidence_refs": [crop["crop_id"] for crop in data["crops"]]}
        with tempfile.TemporaryDirectory() as directory:
            store, provider = Store(Path(directory)), Visual()
            buffer = io.BytesIO()
            Image.new("RGB", (8,8)).save(buffer, format="PNG")
            encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
            def crops(path, ids, count, side):
                return ([{"crop_id": f"crop:{mid}", "magi_id": mid, "path": "old-bank/crop.png",
                          "sha256": "original-hash", "distance": .2, "image": encoded} for mid in ids], [])
            with patch.object(graph, "crop_selection", side_effect=crops):
                result = graph.build(store, document(), provider)
            self.assertTrue(provider.calls[0][2])
            self.assertEqual(result["address_table"][0]["relationship"], "unknown")
            self.assertEqual(result["address_table"][0]["basis"], "visual_age_estimate")
            self.assertEqual(len(result["reference_crops"]), 3)
            self.assertTrue(all((store.directory / c["file"]).exists() for c in result["reference_crops"]))
            from manga_pipeline.v6.stages import write_review
            store.steps, store.directories = ["translate"], {"translate": "translate"}
            store.manifest["steps"] = {"translate": {"status": "completed"}}
            atomic_json(store.artifact_path("translate"), {"relationship_graph": result})
            write_review(store, "translate")
            review = (store.artifact_path("translate").parent / "review.md").read_text(encoding="utf-8")
            self.assertIn("visual_age_estimate", review)
            self.assertIn("same_identity", review)
            self.assertIn("![reference crop]", review)

    def test_failed_inference_is_not_saved_as_unknown_relation(self):
        class Invalid(Provider):
            def infer_reserved(self, *args, **kwargs):
                return {"invalid": True}
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            result = graph.build(store, document(), Invalid())
            self.assertTrue(result["failures"])
            self.assertEqual(result["address_table"], [])
            self.assertEqual(read_json(graph.graph_path(store))["relations"], {})


class TranslationTests(unittest.TestCase):
    def run_translation(self, store, provider):
        frozen = {"address_table": [relation()], "graph": graph.empty_graph(), "failures": []}
        with patch.object(graph, "build", return_value=frozen):
            return translation.translate(store, provider)

    def test_partition_matches_analyze_and_additional_eight_without_scene_rule(self):
        doc = document()
        extra = rows(19)
        for i, row in enumerate(extra):
            row["id"] = f"extra{i}"
        jobs = translation.partition(doc["utterances"]+extra, doc["batches"])
        self.assertEqual(jobs[0]["target_ids"], ["u0", "u1"])
        self.assertEqual([len(j["target_ids"]) for j in jobs], [2,2,8,8,3])
        with self.assertRaises(ValueError):
            translation.partition(doc["utterances"], doc["batches"]*2)

    def test_natural_parallel_frozen_table_and_cached_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            store, provider = Store(Path(directory)), Provider()
            store.values["export"] = document(8)
            result = self.run_translation(store, provider)
            self.assertEqual(STYLES, ("natural",))
            self.assertEqual(result["failures"], [])
            self.assertGreaterEqual(provider.peak, 2)
            self.assertTrue(all(set(r["translations"]) == {"natural"} for r in result["utterances"]))
            self.assertTrue(all(d["address_table"] == [relation()] for _,d,_ in provider.calls))
            count = len(provider.calls)
            again = self.run_translation(store, provider)
            self.assertEqual(len(provider.calls), count)
            self.assertEqual(again["utterances"], result["utterances"])
            self.assertEqual(read_json(store.artifact_path("translate").parent/"plan.json")["maximum_concurrency"], 64)

    def test_token_budget_split_never_drops_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            store, provider = Store(Path(directory)), Provider()
            provider.force_split = True
            store.values["export"] = document()
            result = self.run_translation(store, provider)
            self.assertEqual(result["unresolved_translation_targets"], [])
            self.assertTrue(all(len(d["targets"]) == 1 for _, d, _ in provider.calls))

    def test_one_bad_target_does_not_discard_successful_sibling_or_other_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            store, provider = Store(Path(directory)), Provider()
            store.values["export"] = document(8)
            provider.fail_id = "u1"
            result = self.run_translation(store, provider)
            self.assertEqual(result["unresolved_translation_targets"], ["u1"])
            self.assertEqual(sum("natural" in r["translations"] for r in result["utterances"]), 7)
            self.assertTrue(result["failures"])

    def test_uncertain_post_pauses_new_work_and_resume_does_not_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            store, provider = Store(Path(directory)), Provider()
            store.values["export"] = document(12)
            provider.fail_id, provider.uncertain = "u0", True
            with self.assertRaises(InferenceUncertain):
                self.run_translation(store, provider)
            count = len(provider.calls)
            self.assertLessEqual(count, 2)
            self.assertTrue((store.artifact_path("translate").parent / "partial.json").exists())
            with self.assertRaises(InferenceUncertain):
                self.run_translation(store, provider)
            self.assertEqual(len(provider.calls), count)

    def test_scheduler_maximum_64_but_analyze_policy_stays_separate(self):
        self.assertEqual(Admission(64).maximum, 64)
        with self.assertRaises(ValueError):
            Admission(65)
        cfg = load_config()
        self.assertEqual(cfg["v6"]["relationship_concurrency"], 8)
        self.assertEqual(cfg["v6"]["translation_concurrency"], 64)
        self.assertEqual(cfg["vllm"]["max_num_seqs"], 64)

    def test_exhausted_oom_reduces_admission_and_other_jobs_continue(self):
        provider, counts = Provider(), {}
        jobs = [{"batch_id": str(i)} for i in range(3)]
        def worker(job):
            key = job["batch_id"]
            counts[key] = counts.get(key, 0)+1
            if key == "0":
                raise ResourceExhausted("OOM")
            return [key]
        completed, failures = run(jobs, worker, provider, maximum=32, stop_on_error=lambda exc: False)
        self.assertEqual(completed, [["1"], ["2"]])
        self.assertEqual(counts["0"], 2)
        self.assertEqual(len(failures), 1)
        self.assertFalse(failures[0]["systemic"])


if __name__ == "__main__":
    unittest.main()
