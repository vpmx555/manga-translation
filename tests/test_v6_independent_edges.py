"""Failure, isolation and delivery regressions using only local fixtures/mocks."""
import copy
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_v6_independent_batches import fixture, Provider, result
from manga_pipeline.pipeline import load_config, prepare_bank
from manga_pipeline.storage.io import atomic_json, fingerprint, read_json
from manga_pipeline.v6.independent import analyze
from manga_pipeline.v6.requests import Calls
from manga_pipeline.v6.batch_contract import validate
from manga_pipeline.v6.planning import prepare
from manga_pipeline.v6.planning import pool
from manga_pipeline.v6.verification import verify
from manga_pipeline.v6.scene import plan as scene_plan
from manga_pipeline.v6.scene import validate as validate_scene
from manga_pipeline.v6.scheduling import Admission
from manga_pipeline.runtime.inference_state import InferenceUncertain
from manga_pipeline.runtime.resources import ResourceExhausted
from manga_pipeline.providers.vllm import VLLM

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deployment/runpod"))
import local_v6
import vllm_runtime
from validate_vllm_concurrency import validate_receipt, probe_contract, PROBE_POLICY, SAMPLE_COUNT


class IsolationTests(unittest.TestCase):
    def test_caption_and_dialogue_across_scenes_share_one_queue(self):
        later_scene = threading.Event()
        class Mixed(Provider):
            def infer_reserved(self, task, prompt, data, schema, validator, *, reserve):
                value = super().infer_reserved(task, prompt, data, schema, validator, reserve=reserve)
                if task == "analyze_scene_v6":
                    return {"scenes": [{"start_id": "u0", "summary": "They arrive."},
                                       {"start_id": "u8", "summary": "They leave."}]}
                if data["scene_id"] == "scene:2":
                    later_scene.set()
                if data["scene_id"] == "scene:1" and data["analysis_branch"] == "caption":
                    if not later_scene.wait(2):
                        raise AssertionError("Scene/branch barrier prevented refill")
                if data["analysis_branch"] == "caption":
                    for row in value["utterances"].values():
                        row.update(addressee_type="audience", speech_style="caption",
                                   content_type="narration", speaker_kind="narrator")
                return value
        with tempfile.TemporaryDirectory() as directory:
            args = fixture(Path(directory))
            for index, row in enumerate(args[2]["utterances"]):
                if index % 3 == 0:
                    row["visual_evidence"] = [{"bubble_style": "caption_hint"}]
            output = analyze(*args[:4], Mixed())
            self.assertTrue(output["semantic_coverage_complete"], output["unresolved_targets"])
            self.assertEqual(len(output["batches"]), 4)
            for batch in output["batches"]:
                self.assertEqual([r["id"] for r in batch["request"]["targets"]], [r["id"] for r in batch["rows"]])
                self.assertFalse({"history", "main_narrator", "forward_results", "context"} & set(batch["request"]))

    def test_english_summary_allows_accented_names_and_titles(self):
        rows = [{"id": "u"}]
        value = {"scenes": [{"start_id": "u", "summary": "Dr. Sōta comforts Ayame. They talk."}]}
        self.assertEqual(len(validate_scene(value, rows)), 1)
        for invalid in (123, [], None):
            with self.assertRaises(ValueError):
                validate_scene({"scenes": [{"start_id": "u", "summary": invalid}]}, rows)

    def test_completion_order_and_concurrency_do_not_change_semantics(self):
        third_started = threading.Event()
        class Reordered(Provider):
            def infer_reserved(self, task, prompt, data, schema, validator, *, reserve):
                if task != "analyze_scene_v6":
                    first = data["targets"][0]["id"]
                    if first == "u0" and not third_started.wait(2):
                        raise AssertionError("Queue did not continuously refill")
                    if first == "u16":
                        third_started.set()
                return super().infer_reserved(task, prompt, data, schema, validator, reserve=reserve)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            a = fixture(root / "one")
            a[0].manifest["config"]["runtime"]["llm_slots"] = 1
            baseline = analyze(*a[:4], Provider())
            b = fixture(root / "eight")
            actual = analyze(*b[:4], Reordered())
            self.assertEqual(actual, baseline)

    def test_completed_peer_is_retained_when_another_request_becomes_uncertain(self):
        class Failure(Provider):
            def infer_reserved(self, task, prompt, data, schema, validator, *, reserve):
                if task != "analyze_scene_v6" and data["targets"][0]["id"] == "u8":
                    raise OSError("Completion unknown")
                return super().infer_reserved(task, prompt, data, schema, validator, reserve=reserve)
        with tempfile.TemporaryDirectory() as directory:
            args = fixture(Path(directory))
            provider = Failure()
            output = analyze(*args[:4], provider)
            self.assertTrue(output["failures"])
            self.assertFalse(output["semantic_coverage_complete"])
            self.assertTrue(set(f"u{i}" for i in range(8)).issubset(r["utterance_id"] for r in output["utterances"]))
            self.assertIn("independent-batches-v6.1:batch:1", args[0].commits)
            calls_before = len(provider.calls)
            with self.assertRaises(InferenceUncertain):
                analyze(*args[:4], provider)
            self.assertEqual(len(provider.calls), calls_before)

    def test_oom_retry_waits_for_existing_requests_to_drain(self):
        first_failed, peer_finished, peer_started = threading.Event(), threading.Event(), threading.Event()
        class OOM(Provider):
            attempted = False
            def infer_reserved(self, task, prompt, data, schema, validator, *, reserve):
                if task != "analyze_scene_v6":
                    first = data["targets"][0]["id"]
                    if first == "u0":
                        if not self.attempted:
                            self.attempted = True
                            if not peer_started.wait(2):
                                raise AssertionError("Concurrent peer was not admitted")
                            first_failed.set()
                            raise ResourceExhausted("out of memory")
                        if not peer_finished.is_set():
                            raise AssertionError("OOM retry bypassed reduced admission")
                    elif first == "u8":
                        peer_started.set()
                        if not first_failed.wait(2):
                            raise AssertionError("OOM fixture did not start")
                        peer_finished.set()
                return super().infer_reserved(task, prompt, data, schema, validator, reserve=reserve)
        with tempfile.TemporaryDirectory() as directory:
            args = fixture(Path(directory), 16)
            output = analyze(*args[:4], OOM())
            self.assertTrue(output["semantic_coverage_complete"], output["failures"])

    def test_exhausted_scene_retry_does_not_reset_on_resume(self):
        class Invalid(Provider):
            def infer_reserved(self, *args, **kwargs):
                super().infer_reserved(*args, **kwargs)
                return {"scenes": []}
        with tempfile.TemporaryDirectory() as directory:
            store, _, document, _, _ = fixture(Path(directory), 3)
            provider = Invalid()
            for _ in range(2):
                with self.assertRaises(ValueError):
                    scene_plan(document["utterances"], Calls(store, provider))
            self.assertEqual(len(provider.calls), 2)
            self.assertTrue(all(call[1]["targets"] == [{"id": r["id"], "text": r["text"]}
                               for r in document["utterances"]] for call in provider.calls))

    def test_receipt_corruption_stops_before_tokenization(self):
        with tempfile.TemporaryDirectory() as directory:
            store, _, _, _, _ = fixture(Path(directory), 1)
            atomic_json(store.artifact_path("analyze").parent / "requests/bad.json", {"status": "garbled"})
            provider = Provider()
            with patch.object(provider, "request_budget", side_effect=AssertionError("Must not tokenize")):
                with self.assertRaisesRegex(RuntimeError, "Malformed request receipt"):
                    Calls(store, provider)

    def test_uncertain_request_pauses_other_batch_calls_before_tokenization(self):
        with tempfile.TemporaryDirectory() as directory:
            args = fixture(Path(directory), 1)
            provider = Provider()
            provider.failures = [OSError("timeout")]
            calls = Calls(args[0], provider)
            request = {"targets": [{"id": "u", "text": "Hello."}]}
            with self.assertRaises(OSError):
                calls.call("first", "task", "prompt", request, {}, reserve=512)
            with patch.object(provider, "request_budget", side_effect=AssertionError("Paused queue tokenized")):
                with self.assertRaises(InferenceUncertain):
                    calls.call("different-batch", "task", "prompt", request, {}, reserve=512)
            self.assertEqual(len(provider.calls), 1)

    def test_known_name_owner_and_anchor_cannot_be_changed(self):
        with tempfile.TemporaryDirectory() as directory:
            args = fixture(Path(directory), 2)
            output = analyze(*args[:4], Provider())
            state = copy.deepcopy(args[4]["state"])
            state["name_index"]["sam"] = ["person:1"]
            old = output["batches"][0]
            mentions = {"m": {"id": "m", "utterance_id": "u0", "name": "Sam", "start": 0, "end": 3}}
            job = prepare(old["rows"], output["scenes"][0], state, mentions, old["anchor_exception"])
            job["batch_id"] = old["batch_id"]
            value = result(job["rows"][0])
            value["mentions"] = {"m": {"confirmed_person_name": True, "mention_type": "reference",
                "target_person_id": "person:2", "recipient_cluster_ref": None, "visual_support": "unknown",
                "components": [], "evidence": "A person is mentioned."}}
            with self.assertRaisesRegex(ValueError, "ownership"):
                validate(value, job["rows"][0], job, state)
            value["mentions"]["m"]["target_person_id"] = "person:1"
            for changes in ({"speaker_person_id": "person:2"}, {"evidence_refs": ["another-batch:u"]}):
                with self.assertRaises(ValueError):
                    validate({**value, **changes}, job["rows"][0], job, state)
            # Same-name candidates do not force either a merge or a conflict.
            state["name_index"]["sam"].append("person:2")
            value["mentions"]["m"]["target_person_id"] = "person:2"
            self.assertEqual(validate(value, job["rows"][0], job, state)["semantic_resolution"], "resolved")
            self.assertEqual(state["redirects"], {})


class VerificationTests(unittest.TestCase):
    def test_cached_completions_do_not_count_as_real_engine_samples(self):
        now = [0]
        admission = Admission(clock=lambda: now[0])
        admission.completed = 20
        admission.sample({"kv": .3, "preemptions": 0, "generated_tokens": 0, "completed_requests": 0}, True)
        self.assertEqual(admission.completed, 0)
        now[0] = 10
        admission.sample({"kv": .3, "preemptions": 0, "generated_tokens": 0, "completed_requests": 0}, True)
        self.assertEqual(admission.limit, 2)
        now[0] = 20
        admission.sample({"kv": .3, "preemptions": 0, "generated_tokens": 40, "completed_requests": 2}, True)
        self.assertEqual(admission.limit, 3)

    def test_admission_bounds_preemption_and_cooldown(self):
        for invalid in (0, 65, True, 1.5):
            with self.assertRaises(ValueError):
                Admission(invalid)
        now = [0]
        admission = Admission(clock=lambda: now[0])
        admission.limit = 8
        admission.sample({"kv": .3, "preemptions": 0, "generated_tokens": 0}, True)
        admission.sample({"kv": .3, "preemptions": 1, "generated_tokens": 512}, True)
        self.assertEqual(admission.limit, 4)
        self.assertEqual(admission.cooldown, 30)
        now[0] = 10
        admission.completed = 2
        admission.sample({"kv": .3, "preemptions": 1, "generated_tokens": 1024}, True)
        self.assertEqual(admission.limit, 4)

    def test_metrics_loss_discards_stale_rollback_level_and_recovery_is_incremental(self):
        now = [0]
        admission = Admission(clock=lambda: now[0])
        admission.limit, admission.stable, admission.baseline = 5, 4, 100
        admission.sample(None, True)
        admission.sample(None, True)
        self.assertEqual(admission.limit, 1)
        for second, tokens in ((10, 10), (20, 20)):
            now[0] = second
            admission.completed = 2
            admission.sample({"kv": .4, "preemptions": 0, "generated_tokens": tokens}, True)
        self.assertEqual(admission.limit, 2)

    def test_semantically_invalid_plan_is_rejected_even_with_matching_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            args = fixture(Path(directory))
            baseline = analyze(*args[:4], Provider())
            original = read_json(args[0].artifact_path("analyze").parent / "plan.json")
            def check(mutator):
                output, plan = copy.deepcopy(baseline), copy.deepcopy(original)
                mutator(output)
                plan["batches"] = copy.deepcopy(output["batches"])
                output["analysis_plan_hash"] = fingerprint(plan)
                with self.assertRaises(ValueError):
                    verify({"analyze": output, "normalize": args[2], "extract": args[1], "scan": args[3]},
                           args[0].manifest["config"], args[4], read_json(args[0].manifest["bank_path"]), plan)
            check(lambda o: o["batches"][1].update(batch_id="batch:1"))
            check(lambda o: o["batches"][0]["rows"].reverse())
            check(lambda o: o["batches"][0].update(anchor_exception="hard_token_limit"))
            check(lambda o: o["utterances"][0].update(batch_id="batch:other"))
            check(lambda o: o["batches"][0].update(hard_split=True))

    def test_compatible_bank_is_reused_and_incompatible_geometry_is_rejected(self):
        from manga_pipeline.extraction.magi import MODEL_FINGERPRINT
        from manga_pipeline.visual.config import KOHARU_MODEL, KOHARU_REVISION
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "story/metadata.json"
            metadata = {"schema_version": 1, "model_fingerprint": MODEL_FINGERPRINT, "characters": {"1": {}},
                "geometry_fingerprint": fingerprint({"model": KOHARU_MODEL, "revision": KOHARU_REVISION, "panel_rule": "overlap-v1"})}
            atomic_json(path, metadata)
            before = path.read_bytes()
            self.assertEqual(prepare_bank(root, "story"), path)
            self.assertEqual(path.read_bytes(), before)
            metadata["geometry_fingerprint"] = "old"
            atomic_json(path, metadata)
            with self.assertRaisesRegex(ValueError, "Koharu"):
                prepare_bank(root, "story")
            with self.assertRaisesRegex(ValueError, "legacy migration"):
                prepare_bank(root, "new", root / "old")

    def test_person_registry_checks_recorded_model_and_geometry_contract(self):
        from manga_pipeline.v6 import persons
        with tempfile.TemporaryDirectory() as directory:
            args = fixture(Path(directory), 2)
            raw = {**args[1], "document_id": "story"}
            bank = {"schema_version": 1, "model_fingerprint": "model:a", "geometry_fingerprint": "geometry:a", "characters": {}}
            state = persons.bootstrap(raw, bank)
            self.assertEqual(persons.bootstrap(raw, bank, state)["source_bank_contract"], state["source_bank_contract"])
            for field in ("schema_version", "model_fingerprint", "geometry_fingerprint"):
                with self.assertRaisesRegex(ValueError, "schema/model/geometry"):
                    persons.bootstrap(raw, {**bank, field: "changed"}, state)

    def test_equal_geometry_in_two_runs_does_not_reuse_cluster_references(self):
        from manga_pipeline.v6 import persons
        with tempfile.TemporaryDirectory() as directory:
            args = fixture(Path(directory), 2)
            raw = {**args[1], "document_id": "story"}
            for index, page in enumerate(raw["pages"], 1):
                page["character_ids"] = [str(index)]
            bank = {"characters": {}}
            first = persons.bootstrap(raw, bank, run_scope="run:a")
            second = persons.bootstrap(raw, bank, first, run_scope="run:b")
            self.assertNotEqual(first["extraction_scope"], second["extraction_scope"])
            clusters, people = pool(args[2]["utterances"], second, {})
            self.assertEqual(len(clusters), 2)
            self.assertTrue(all(ref.startswith(second["extraction_scope"]+":") for ref in clusters))
            self.assertFalse(set(clusters) & set(first["observations"]))
            self.assertEqual(set(people), set(first["cluster_to_person"].values()))


class NativeContractTests(unittest.TestCase):
    def test_metrics_aliases_do_not_double_count_kv_pressure(self):
        import io
        metrics = b'vllm:kv_cache_usage_perc{model_name="qwen"} 0.5\nvllm:gpu_cache_usage_perc{model_name="qwen"} 0.5\nvllm:num_preemptions_total 1\nvllm:generation_tokens_total 1024\n'
        with tempfile.TemporaryDirectory() as directory:
            provider = VLLM(load_config(), Path(directory))
            with patch("manga_pipeline.providers.vllm.urllib.request.urlopen", return_value=io.BytesIO(metrics)):
                self.assertEqual(provider.metrics(), {"kv": .5, "preemptions": 1, "generated_tokens": 1024, "completed_requests": None})

    def test_tokenizer_cache_is_stable_when_object_key_order_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = VLLM(load_config(), Path(directory))
            provider._ready = lambda: None
            with patch.object(provider, "_http", return_value={"count": 100}) as tokenize:
                provider.request_budget("task", "prompt", {"b": 2, "a": 1}, {"z": {}, "a": {}}, reserve=512)
                provider.request_budget("task", "prompt", {"a": 1, "b": 2}, {"a": {}, "z": {}}, reserve=512)
            self.assertEqual(tokenize.call_count, 1)

    def test_qualification_lifecycle_stops_after_both_success_and_failure(self):
        config = load_config()
        for failure in (None, RuntimeError("probe failed")):
            events = []
            with patch.object(vllm_runtime, "start", side_effect=lambda c: events.append("start")), \
                    patch.object(vllm_runtime, "stop", side_effect=lambda: events.append("stop")), \
                    patch.object(local_v6.manage, "current_python", return_value=Path("mock-python")), \
                    patch.object(local_v6.manage, "call", side_effect=lambda args: (_ for _ in ()).throw(failure) if failure else events.append("probe")):
                if failure:
                    with self.assertRaisesRegex(RuntimeError, "probe failed"):
                        local_v6.qualify_concurrency(config, Path("fixture"))
                else:
                    local_v6.qualify_concurrency(config, Path("fixture"))
            self.assertEqual(events[0], "start")
            self.assertEqual(events[-1], "stop")

    def test_qualification_requires_profile_and_real_usage_fields(self):
        import manage
        config = load_config()
        # An in-memory validator fixture, never an operational qualification file.
        config['runtime']['llm_slots'] = 8
        slots = config['runtime']['llm_slots']
        receipt = {"status": "passed", "slots": slots, "signature": vllm_runtime.signature(config),
            "probe_policy": PROBE_POLICY, "sample_count": SAMPLE_COUNT,
            "config_fingerprint": manage.config_fingerprint(config), "cpu_offload_gb": 0,
            "peak_running_requests": slots, "input_tokens_each": 16000,
            "responses": [{"usage": {"prompt_tokens": 16000, "completion_tokens": 4096}} for _ in range(slots)]}
        validate_receipt(config, receipt)
        target, reserve, schema = probe_contract(config)
        self.assertGreaterEqual(target, 16000)
        self.assertLessEqual(target + reserve + 640, config['llm']['num_ctx'] * config['runtime']['context_fraction'])
        self.assertEqual(schema['properties']['samples']['minItems'], SAMPLE_COUNT)
        for update in ({}, {"signature": "old"}, {"probe_policy": "short-decode-v1"},
                       {"slots": 2}, {"peak_running_requests": 7}, {"responses": []},
                       {"responses": [{"usage": {"prompt_tokens": 100, "completion_tokens": 6}} for _ in range(8)]}):
            value = {} if not update else {**receipt, **update}
            with self.assertRaises(RuntimeError):
                validate_receipt(config, value)


if __name__ == "__main__":
    unittest.main()
