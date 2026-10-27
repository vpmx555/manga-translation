"""Short CPU-only contract tests. No model, subprocess runtime or network access."""
# Historical contract test; requires the matching frozen source.
import copy
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from manga_pipeline.pipeline import load_config as current_config
from manga_pipeline.storage.io import atomic_json, fingerprint, read_json
from manga_pipeline.runtime.llm_budget import BudgetExceeded, OutputTruncated
from manga_pipeline.runtime.inference_state import InferenceUncertain
from manga_pipeline.runtime.resources import ResourceExhausted
from manga_pipeline.v6 import scene, planning, scheduling, persons
from manga_pipeline.v6.independent import analyze
from manga_pipeline.v6.requests import Calls
from manga_pipeline.v6.batch_contract import validate
from manga_pipeline.v6.verification import verify
from manga_pipeline.providers.vllm import VLLM


def load_config(*args, **kwargs):
    config = current_config(*args, **kwargs)
    config['v6'].update(scan_policy=None, identity_name_binding_policy='chapter-multimodal-name-binding-v1',
                        scan_output_tokens=4096)
    config['llm'].update(analyze_thinking=False, analyze_num_ctx=32768, analyze_timeout=600, analyze_num_predict=8192)
    return config


class Store:
    def __init__(self, root, config):
        self.directory = root
        self.manifest = {"config": config, "bank_path": str(root / "bank.json")}
        atomic_json(root / "bank.json", {"characters": {"1": {}, "2": {}}})
        self.commits = {}

    def artifact_path(self, step):
        return self.directory / step / "result.json"

    def commit_target(self, step, key, request, result=None, **kwargs):
        self.commits[key] = copy.deepcopy(result)


class Provider:
    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()
        self.limit = 32768
        self.failures = []

    def request_budget(self, task, prompt, data, schema, images=None, *, reserve=None):
        reserve = reserve or 2048
        count = sum(len(r["text"]) for r in data["targets"])
        return {"estimate": count+reserve+512, "input_tokens": count,
                "limit": self.limit, "output_reserve": reserve, "num_ctx": 32768}

    def infer_reserved(self, task, prompt, data, schema, validator, *, reserve):
        with self.lock:
            self.calls.append((task, copy.deepcopy(data), reserve))
            failure = self.failures.pop(0) if self.failures else None
        if failure:
            raise failure
        if task == "analyze_scene_v6":
            return {"scenes": [{"start_id": data["targets"][0]["id"], "summary": "Two people talk."}]}
        return {"utterances": {r["id"]: result(r) for r in data["targets"]}, "distinct_person_pairs": []}

    def metrics(self):
        return {"kv": .4, "preemptions": 0, "generated_tokens": 0}

    def healthy(self):
        return True


def result(row):
    pid = row["speaker_anchor"]["person_id"]
    return {"content_type": "dialogue", "speech_style": "normal", "speaker_person_id": pid,
            "speaker_cluster_ref": None, "speaker_kind": "person" if pid else "others",
            "addressee_person_ids": [], "addressee_type": "unknown", "mentions": {},
            "evidence": "Source anchor." if pid else "Insufficient source evidence.", "evidence_refs": [row["id"]]}


def fixture(root, count=18):
    config = load_config()
    # These fixtures exercise historical scans without a saved joint scene plan.
    config["ner"]["backend"] = "gliner"
    config["runtime"]["llm_slots"] = 8
    store = Store(root, config)
    pages, texts, rows, observations = [], [], [], {}
    for i in (1, 2):
        image = root / f"p{i}.png"
        image.write_bytes(b"fixture image " + bytes([i]))
        pages.append({"id": f"p{i}", "image_path": str(image), "characters": [[1, 1, 9, 9]],
                      "character_cluster_labels": [1], "tails": [[1, 1, 2, 2]],
                      "text_tail_associations": [], "text_character_associations": []})
        observations[f"scope:p{i}:c1"] = [{"page_id": f"p{i}", "character_index": 0,
            "source_bank_id": str(i), "appearance_key": f"appearance:{i}", "bbox": [1, 1, 9, 9]}]
    for i in range(count):
        page = pages[0 if i < count//2 else 1]
        pid = "person:1" if page["id"] == "p1" else "person:2"
        tid = f"{page['id']}:t{i}"
        text = {"id": tid, "page_id": page["id"], "text_index": i, "tail_index": 0,
                "character_detection_index": 0, "speaker_id": "1" if pid == "person:1" else "2"}
        page["text_tail_associations"].append([i, 0])
        page["text_character_associations"].append([i, 0])
        texts.append(text)
        rows.append({"id": f"u{i}", "text": "Hello.", "page_id": page["id"],
            "source_text_ids": [tid], "source_boxes": [{"source_text_id": tid, "bbox": [1, 2, 3, 4]}],
            "visual_evidence": [], "is_essential_text": True})
    state = {"schema_version": 6, "extraction_scope": "scope", "observations": observations,
             "cluster_to_person": {"scope:p1:c1": "person:1", "scope:p2:c1": "person:2"},
             "persons": {p: {"primary_name": None, "name_forms": [], "appearance_refs": [], "components": []}
                         for p in ("person:1", "person:2")}, "name_index": {}, "redirects": {}, "calling_events": {}}
    frozen = {"state": state, "source_bank_snapshot": {}}
    atomic_json(root / "snapshots/persons-v6.json", frozen)
    return store, {"pages": pages, "texts": texts}, {"utterances": rows}, {
        "candidates": [], "person_snapshot_hash": fingerprint(frozen)}, frozen


class SceneTests(unittest.TestCase):
    def test_ranges_and_summary_contract(self):
        rows = [{"id": str(i)} for i in range(5)]
        value = {"scenes": [{"start_id": "0", "summary": "A conversation."}, {"start_id": "3", "summary": "They leave."}]}
        self.assertEqual([s["target_ids"] for s in scene.validate(value, rows)], [["0", "1", "2"], ["3", "4"]])
        for starts in (["1"], ["0", "0"], ["0", "3", "2"]):
            with self.assertRaises(ValueError):
                scene.validate({"scenes": [{"start_id": s, "summary": "They talk."} for s in starts]}, rows)
        for summary in ("", "x"*513, "Một cảnh mới.", "One. Two. Three."):
            with self.assertRaises(ValueError):
                scene.validate({"scenes": [{"start_id": "0", "summary": summary}]}, rows)

    def test_empty_no_model(self):
        self.assertEqual(scene.plan([], None), [])

    def test_legacy_truncation_retry_is_durable_with_8k_reserve(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory), load_config())
            provider = Provider()
            provider.failures = [OutputTruncated("length")]
            calls = Calls(store, provider)
            rows = [{"id": "u", "text": "Entire chapter."}]
            self.assertEqual(len(scene.plan(rows, calls)), 1)
            self.assertEqual([r[2] for r in provider.calls], [8192, 8192])
            scene.plan(rows, Calls(store, provider))
            self.assertEqual(len(provider.calls), 2)

    def test_overflow_does_not_split_or_post(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = Provider()
            provider.limit = 100
            with self.assertRaises(BudgetExceeded):
                scene.plan([{"id": "u", "text": "Protected."}], Calls(Store(Path(directory), load_config()), provider))
            self.assertEqual(provider.calls, [])


class BatchTests(unittest.TestCase):
    def test_plan_merge_and_resume_are_independent(self):
        with tempfile.TemporaryDirectory() as directory:
            store, raw, doc, scanned, frozen = fixture(Path(directory))
            provider = Provider()
            output = analyze(store, raw, doc, scanned, provider)
            self.assertTrue(output["semantic_coverage_complete"])
            self.assertEqual([r["utterance_id"] for r in output["utterances"]], [r["id"] for r in doc["utterances"]])
            for job in output["batches"]:
                self.assertFalse({"history", "forward_results", "context", "main_narrator"} & set(job["request"]))
                self.assertEqual(job["request"]["unknown_reference"], "others")
            plan = read_json(store.artifact_path("analyze").parent / "plan.json")
            verify({"normalize": doc, "analyze": output, "extract": raw, "scan": scanned}, store.manifest["config"], frozen, read_json(store.manifest["bank_path"]), plan)
            before = len(provider.calls)
            again = analyze(store, raw, doc, scanned, provider)
            self.assertEqual(again, output)
            self.assertEqual(len(provider.calls), before)

    def test_nonessential_is_preserved_without_request(self):
        with tempfile.TemporaryDirectory() as directory:
            store, raw, doc, scanned, _ = fixture(Path(directory), 1)
            doc["utterances"][0]["is_essential_text"] = False
            provider = Provider()
            output = analyze(store, raw, doc, scanned, provider)
            self.assertEqual(provider.calls, [])
            self.assertEqual(output["utterances"][0]["semantic_resolution"], "source_only")

    def test_soft_cut_waits_for_next_anchor(self):
        with tempfile.TemporaryDirectory() as directory:
            store, raw, doc, scanned, frozen = fixture(Path(directory), 15)
            for text in raw["texts"][8:11]:
                text["tail_index"] = None
            output = analyze(store, raw, doc, scanned, Provider())
            self.assertEqual(len(output["batches"][0]["rows"]), 11)
            self.assertTrue(output["batches"][1]["rows"][0]["speaker_anchor"]["locked"])

    def test_hard_cut_and_singleton_overflow(self):
        rows = [{"id": str(i), "text": "x"*100, "page_id": "p", "essential_index": i,
                 "scene_id": "s", "analysis_branch": "dialogue", "original_speaker": [],
                 "speaker_anchor": {"locked": False, "person_id": None}} for i in range(5)]
        state = {"extraction_scope": "scope", "observations": {}, "persons": {}, "name_index": {}, "cluster_to_person": {}}
        class Budget:
            def budget(self, task, prompt, request, schema, reserve):
                return {"estimate": len(request["targets"]), "limit": 2}
        jobs = planning.plan(rows, [{"scene_id": "s", "summary": "They talk."}], state, {}, Budget())
        self.assertEqual([len(j["rows"]) for j in jobs], [2, 2, 1])
        self.assertEqual(jobs[1]["anchor_exception"], "hard_token_limit")
        with patch.object(Budget, "budget", return_value={"estimate": 3, "limit": 2}):
            with self.assertRaises(BudgetExceeded):
                planning.plan(rows, [{"scene_id": "s", "summary": "They talk."}], state, {}, Budget())

    def test_pool_is_page_and_run_scoped(self):
        with tempfile.TemporaryDirectory() as directory:
            _, _, _, _, frozen = fixture(Path(directory), 1)
            state = frozen["state"]
            state["observations"]["other:p1:c1"] = [{"page_id": "p1"}]
            state["name_index"]["sam"] = ["person:1", "person:2"]
            clusters, people = planning.pool([{"page_id": "p1"}], state, {"m": {"name": "Sam"}})
            self.assertEqual(list(clusters), ["scope:p1:c1"])
            self.assertEqual(people, ["person:1", "person:2"])

    def test_invalid_target_keeps_valid_peers_without_repair(self):
        class Bad(Provider):
            def infer_reserved(self, *args, **kwargs):
                value = super().infer_reserved(*args, **kwargs)
                if "utterances" in value:
                    value["utterances"]["u0"]["speaker_person_id"] = "invented"
                return value
        with tempfile.TemporaryDirectory() as directory:
            store, raw, doc, scanned, _ = fixture(Path(directory), 3)
            provider = Bad()
            output = analyze(store, raw, doc, scanned, provider)
            self.assertEqual(output["unresolved_targets"], ["u0"])
            self.assertEqual(len(provider.calls), 2)
            self.assertEqual(output["utterances"][1]["semantic_resolution"], "resolved")


class AdmissionTests(unittest.TestCase):
    def test_loss_recovery_pressure_and_cooldown(self):
        now = [0]
        a = scheduling.Admission(clock=lambda: now[0])
        a.sample(None, True)
        a.sample(None, True)
        self.assertEqual(a.limit, 1)
        now[0] = 10
        a.completed = 2
        a.sample({"kv": .4, "preemptions": 0, "generated_tokens": 100}, True)
        self.assertEqual(a.limit, 1)
        now[0] = 20
        a.completed = 2
        a.sample({"kv": .4, "preemptions": 0, "generated_tokens": 400}, True)
        self.assertEqual(a.limit, 2)
        a.sample({"kv": .95, "preemptions": 0, "generated_tokens": 400}, True)
        self.assertEqual(a.limit, 1)
        self.assertEqual(a.cooldown, 50)

    def test_throughput_rollback_after_two_windows(self):
        now = [0]
        a = scheduling.Admission(clock=lambda: now[0])
        a.good = 2
        a.previous = {"kv": .4, "preemptions": 0, "generated_tokens": 0}
        for t, tokens in ((10, 1000), (20, 1600), (30, 2200)):
            now[0] = t
            a.completed = 2
            a.sample({"kv": .4, "preemptions": 0, "generated_tokens": tokens}, True)
        self.assertEqual(a.limit, 2)
        self.assertEqual(a.cooldown, 60)

    def test_continuous_refill_without_wave_barrier(self):
        release = threading.Event()
        third = threading.Event()
        def worker(job):
            if job["batch_id"] == "0":
                self.assertTrue(third.wait(2))
            if job["batch_id"] == "2":
                third.set()
            return job["batch_id"]
        results, errors = scheduling.run([{"batch_id": str(i)} for i in range(3)], worker, Provider(), 2)
        self.assertEqual(results, ["0", "1", "2"])
        self.assertEqual(errors, [])


class ReceiptTests(unittest.TestCase):
    def test_uncertain_completion_blocks_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            store, provider = Store(Path(directory), load_config()), Provider()
            provider.failures = [OSError("timeout")]
            calls = Calls(store, provider)
            request = {"targets": [{"id": "u", "text": "Hi"}]}
            with self.assertRaises(OSError):
                calls.call("a", "task", "prompt", request, {}, reserve=512)
            with self.assertRaises(InferenceUncertain):
                Calls(store, provider).call("a", "task", "prompt", request, {}, reserve=512)
            self.assertEqual(len(provider.calls), 1)

    def test_oom_retry_budget_survives_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            store, provider = Store(Path(directory), load_config()), Provider()
            provider.reduce_admission = lambda: None
            provider.failures = [ResourceExhausted("oom"), ResourceExhausted("oom")]
            request = {"targets": [{"id": "u", "text": "Hi"}]}
            for _ in range(2):
                with self.assertRaises(ResourceExhausted):
                    Calls(store, provider).call("a", "task", "prompt", request, {}, reserve=512)
            self.assertEqual(len(provider.calls), 2)


class ProviderTests(unittest.TestCase):
    def test_tokenizer_cache_is_reused_by_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = VLLM(load_config(), Path(directory))
            provider._ready = lambda: None
            calls = []
            def http(route, payload=None):
                calls.append(route)
                if route == "/tokenize":
                    return {"count": 100}
                return {"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}], "usage": {"prompt_tokens": 100}}
            provider._http = http
            provider.request_budget("task", "prompt", {}, {}, reserve=512)
            provider.infer_reserved("task", "prompt", {}, {}, lambda v: None, reserve=512)
            self.assertEqual(calls.count("/tokenize"), 1)
            provider.request_budget("task", "prompt", {"changed": True}, {}, reserve=512)
            self.assertEqual(calls.count("/tokenize"), 2)

    def test_provider_receipt_precedes_post_and_crash_blocks_repeat(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = VLLM(load_config(), Path(directory))
            provider._ready = lambda: None
            def http(route, payload=None):
                if route == "/tokenize":
                    return {"count": 100}
                receipts = list((Path(directory) / "timeouts").glob("*.json"))
                self.assertEqual(read_json(receipts[0])["status"], "started")
                raise KeyboardInterrupt()
            provider._http = http
            with self.assertRaises(KeyboardInterrupt):
                provider.infer_reserved("task", "prompt", {}, {}, lambda v: None, reserve=512)
            with self.assertRaises(InferenceUncertain):
                provider.infer_reserved("task", "prompt", {}, {}, lambda v: None, reserve=512)


class IdentityTests(unittest.TestCase):
    def test_recipient_provenance_uses_recipient_page_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            _, raw, doc, _, frozen = fixture(Path(directory), 2)
            candidate = {"name": "Sam", "start": 0, "end": 3}
            event = persons.calling_event(raw=raw, row=doc["utterances"][0], mention=candidate,
                target_person_id="person:2", recipient_cluster_ref="scope:p2:c1",
                recipient_observations=frozen["state"]["observations"]["scope:p2:c1"])
            self.assertEqual(event["recipient_observations"][0]["page_id"], "p2")
            copied_source = copy.deepcopy(doc["utterances"][0])
            copied_source["id"] = "new-run:u1"
            copied_source["source_text_ids"] = ["new-run:t1"]
            copied_source["source_boxes"][0]["source_text_id"] = "new-run:t1"
            replay = persons.calling_event(raw=raw, row=copied_source, mention=candidate,
                target_person_id="person:2", recipient_cluster_ref="scope:p2:c1",
                recipient_observations=frozen["state"]["observations"]["scope:p2:c1"])
            self.assertEqual(event["source_occurrence_key"], replay["source_occurrence_key"])
            event["recipient_appearance_key"] = "appearance:2"
            proposals = persons.propose(frozen["state"], [event, event], minimum=2)
            self.assertEqual(proposals[0]["confirmed_event_count"], 1)

    def test_legacy_config_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            atomic_json(path, {"ollama": {"backend": "vllm"}})
            with self.assertRaisesRegex(ValueError, "Legacy config"):
                load_config(path)


if __name__ == "__main__":
    unittest.main()
