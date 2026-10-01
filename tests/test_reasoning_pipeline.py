"""Contract and regression tests using a deterministic, schema-checked provider."""
from __future__ import annotations

import copy
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dialogue_data import document_fingerprint, import_utterances, normalize_document, write_json
from dialogue_ordering import reorder_dialogue
from dialogue_pipeline import build_parser, run
from dialogue_reconstruction import validate_reconstructed
from reasoning_batching import Chapter, TaskRunner
from reasoning_client import BudgetExceeded, InvalidResponse, OllamaReasoner, ReasoningConfig, ResourceLimit, load_config
from reasoning_evaluation import evaluate, review_template, validate_reference
from reasoning_pipeline import run_reasoning
from reasoning_schemas import SCHEMAS, validate


def fixture():
    raw = import_utterances([
        {"text": "Answer from B", "page": 1, "speaker_id": 1,
         "addressee_type": "individual", "addressee_ids": [999]},
        {"text": "Question from A", "page": 1, "speaker_id": 1},
        {"text": "Offscreen B", "page": 1, "speaker_id": 2},
        {"text": "Later on page two", "page": 2, "speaker_id": 2},
    ], document_id="chapter")
    normalized = normalize_document(raw)
    bank = {"characters": {"1": {"id": 1, "display_name": "A"}, "2": {"id": 2, "display_name": "B"}}}
    return raw, normalized, bank


class ScriptedProvider(OllamaReasoner):
    def __init__(self, cache=None, *, bad_identity=False, bad_order=False, limit=None, uncertain=False,
                 config=None):
        super().__init__(config or ReasoningConfig(num_ctx=100000, num_predict=2048, max_targets=6), cache)
        self.calls, self.bad_identity, self.bad_order = [], bad_identity, bad_order
        self.limit, self.uncertain = limit, uncertain

    def _http(self, endpoint, payload=None):
        if endpoint == "/api/tags":
            return {"models": [{"name": self.config.model, "digest": "test-digest"}]}
        if endpoint == "/api/show":
            return {"capabilities": ["vision", "thinking"]}
        if endpoint == "/api/generate":
            return {"done": True}
        if endpoint == "/api/ps":
            return {"models": []}
        data = json.loads(payload["messages"][1]["content"])
        instruction = payload["messages"][0]["content"]
        self.calls.append((instruction, copy.deepcopy(data)))
        if self.limit and len(data["targets"]) > self.limit:
            raise ResourceLimit("Test provider rejects large batches")
        selected = {r["id"]: r for r in data.get("utterances", data.get("ocr", []))}
        result = []
        for target in data["targets"]:
            identity = target["id"]
            evidence = identity if identity in selected or "Describe only" in instruction else (
                target.get("left_id") or target.get("member_ids", [identity])[0])
            value = {"id": identity, "evidence_refs": [evidence], "reason": "Fixture source evidence"}
            row = selected.get(identity, {})
            if "Describe only" in instruction:
                value.update(setting="room", time_cue=None, event="conversation", status="predicted")
            elif "For every target gap" in instruction:
                value["decision"] = "uncertain" if self.uncertain else (
                    "boundary" if target["before_index"] == 3 else "no_boundary")
            elif "Stage 3A:" in instruction:
                speaker = 2 if row["text"].startswith(("Answer", "Offscreen", "Later")) else 1
                value.update(speaker_cluster_id=None, speaker_id=999 if self.bad_identity else speaker, status="predicted")
            elif "Stage 3B" in instruction:
                speaker = row.get("speaker_id")
                value.update(speaker_id=speaker, speaker_name="A" if speaker == 1 else "B",
                             status="predicted", name_status="predicted")
            elif "Stage 3C" in instruction:
                # The deliberately invalid source addressee must never leak into Stage 3.
                if row.get("addressee_ids") == [999]:
                    raise AssertionError("Original reference-like addressee leaked into inference")
                ordered = data["utterances"]
                position = next(i for i, x in enumerate(ordered) if x["id"] == identity)
                if row["text"].startswith("Answer") and position and ordered[position - 1]["text"].startswith("Question"):
                    value.update(addressee_type="individual", addressee_ids=[1], status="predicted")
                else:
                    value.update(addressee_type="unspecified", addressee_ids=[], status="unknown")
            else:
                members = target["member_ids"]
                permutation = list(members)
                if len(members) > 1 and selected[members[0]]["text"].startswith("Answer"):
                    permutation[:2] = reversed(permutation[:2])
                if self.bad_order:
                    permutation[-1] = "other-page-utterance"
                value.update(ordered_ids=permutation, status="predicted")
            result.append(value)
        return {"message": {"content": json.dumps({"rows": result})}, "done": True,
                "done_reason": "stop", "prompt_eval_count": 500, "eval_count": 100}


class PipelineContracts(unittest.TestCase):
    def test_uncertain_candidates_are_retained_only_as_proposals(self):
        class UncertainProvider(ScriptedProvider):
            def _http(self, endpoint, payload=None):
                value = super()._http(endpoint, payload)
                if endpoint == "/api/chat" and "Stage 3" in payload["messages"][0]["content"]:
                    uncertain = json.loads(value["message"]["content"])
                    for row in uncertain["rows"]:
                        row["status"] = "unknown"
                        if "speaker_name" in row:
                            row.update(speaker_id=2, speaker_name="B", name_status="unknown")
                        if "addressee_type" in row:
                            row.update(addressee_type="individual", addressee_ids=[])
                    value["message"]["content"] = json.dumps(uncertain)
                return value
        raw, document, bank = fixture()
        result = run_reasoning(Chapter(document, raw, bank=bank, text_only=True), UncertainProvider(), correct_order=False)
        for row in result["utterances"]:
            self.assertIsNone(row["speaker_id"])
            self.assertIsNone(row["speaker_name"])
            self.assertEqual(row["proposed_speaker_id"], 2)
            self.assertEqual(row["proposed_speaker_name"], "B")
            self.assertEqual(row["addressee_ids"], [])
            self.assertEqual(row["addressee_type"], "unspecified")
            self.assertEqual(row["proposed_addressee_type"], "individual")
            self.assertIn("addressee_unresolved", row["review_flags"])
        self.assertTrue(result["identity_proposals"])

    def test_predicted_addressee_still_requires_cardinality_and_unknown_cannot_invent_ids(self):
        class InvalidListener(ScriptedProvider):
            def __init__(self, *, invented=False):
                super().__init__()
                self.invented = invented

            def _http(self, endpoint, payload=None):
                value = super()._http(endpoint, payload)
                if endpoint == "/api/chat" and "Stage 3C" in payload["messages"][0]["content"]:
                    result = json.loads(value["message"]["content"])
                    for row in result["rows"]:
                        row.update(addressee_type="individual", addressee_ids=[999] if self.invented else [],
                                   status="unknown" if self.invented else "predicted")
                    value["message"]["content"] = json.dumps(result)
                return value
        raw, document, bank = fixture()
        for invented, message in ((False, "cardinality"), (True, "supplied as a candidate")):
            with self.subTest(invented=invented), self.assertRaisesRegex(InvalidResponse, message):
                run_reasoning(Chapter(document, raw, bank=bank, text_only=True),
                              InvalidListener(invented=invented), correct_order=False)

    def test_end_to_end_preserves_sources_and_reruns_after_same_page_order_fix(self):
        raw, document, bank = fixture()
        before = copy.deepcopy(document)
        chapter = Chapter(document, raw, bank=bank, text_only=True)
        client = ScriptedProvider()
        result = run_reasoning(chapter, client)
        self.assertEqual(document, before)
        self.assertEqual(result["kind"], "structured_dialogue")
        self.assertEqual([r["text"] for r in result["utterances"][:2]], ["Question from A", "Answer from B"])
        self.assertEqual([r["page"] for r in result["utterances"]], [1, 1, 1, 2])
        self.assertEqual(result["local_rerun_count"], 1)
        self.assertEqual(result["utterances"][-1]["order_status"], "not_applicable")
        self.assertNotIn("order_unresolved", result["utterances"][-1]["review_flags"])
        answer = next(r for r in result["utterances"] if r["text"].startswith("Answer"))
        self.assertEqual(answer["original_speaker_id"], 1)
        self.assertEqual(answer["speaker_id"], 2)
        self.assertEqual(answer["speaker_name"], "B")
        self.assertEqual(answer["speaker_name_status"], "confirmed")
        self.assertEqual(answer["addressee_ids"], [1])
        self.assertEqual(answer["original_addressee_ids"], [999])
        self.assertEqual(len({r["id"] for r in result["utterances"]}), len(document["utterances"]))
        self.assertEqual(bank["characters"]["1"]["display_name"], "A")

    def test_without_ordering_does_not_trigger_local_rerun(self):
        raw, document, bank = fixture()
        result = run_reasoning(Chapter(document, raw, bank=bank, text_only=True), ScriptedProvider(), correct_order=False)
        self.assertEqual(result["local_rerun_count"], 0)
        self.assertEqual(result["utterances"][0]["text"], "Answer from B")

    def test_step_snapshots_are_independent_and_include_stage3_passes(self):
        raw, document, bank = fixture()
        snapshots = {}
        result = run_reasoning(Chapter(document, raw, bank=bank, text_only=True), ScriptedProvider(),
                               step_callback=lambda stage, artifact: snapshots.update({stage: artifact}))
        self.assertTrue({"visual", "scenes", "3A", "3B", "3C", "3D", "3_local_rerun", "complete"} <= snapshots.keys())
        self.assertNotIn("identity", snapshots["3A"]["utterances"][0]["reasoning"])
        self.assertNotIn("addressee", snapshots["3B"]["utterances"][0]["reasoning"])
        self.assertEqual(snapshots["3C"]["utterances"][0]["text"], "Answer from B")
        self.assertEqual(snapshots["3D"]["utterances"][0]["text"], "Question from A")
        self.assertEqual(snapshots["complete"]["utterances"], result["utterances"])

    def test_budget_compacts_view_before_splitting_owned_targets(self):
        class CompactOnly(ScriptedProvider):
            def infer(self, task, instruction, data, schema, images, validator=None):
                if "context_geometry_rounded_to_source_pixels" not in data:
                    raise BudgetExceeded("Fixture verbose metadata exceeds budget")
                return super().infer(task, instruction, data, schema, images, validator)
        raw, document, bank = fixture()
        original = copy.deepcopy(document)
        chapter = Chapter(document, raw, bank=bank, text_only=True)
        client = CompactOnly()
        client.prepare()
        runner = TaskRunner(client, chapter)
        targets = [{"id": f"gap-{i}", "left_id": chapter.rows[i - 1]["id"], "before_index": i}
                   for i in range(1, len(chapter.rows))]
        result = runner.execute("boundaries", "For every target gap", targets, chapter.rows,
                                lambda t: [t["before_index"] - 1, t["before_index"]])
        self.assertEqual(set(result), {t["id"] for t in targets})
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(len(client.calls[0][1]["targets"]), len(targets))
        self.assertEqual(client.calls[0][1]["identity_candidates"], [])
        self.assertEqual(document, original)

    def test_stage3_receives_unassigned_visual_cluster_and_tail_geometry(self):
        raw, document, bank = fixture()
        raw["pages"] = [{"id": "source:p1", "page": 1, "panels": [[0, 0, 100, 100]],
                         "characters": [[10, 20, 30, 40]], "tails": [[35, 45, 50, 60]],
                         "character_cluster_labels": [7], "character_ids": [2]}]
        document["raw_fingerprint"] = document_fingerprint(raw)
        document["utterances"][0].update(page_id="source:p1", character_detection_index=None,
                                           tail_index=0, text_index=0)
        chapter = Chapter(document, raw, bank=bank, text_only=True)
        data, allowed = chapter.context(chapter.rows, [0], include_clusters=True)
        self.assertIn({"id": "source:p1:c7", "identity_candidates": [2]}, data["visual_cluster_candidates"])
        self.assertEqual(data["pages"][0]["tails"], [[35, 45, 50, 60]])
        self.assertEqual(data["utterances"][0]["tail_index"], 0)
        self.assertIn("source:p1:tail:0", allowed)

    def test_speaker_cannot_select_existing_cluster_outside_supplied_window(self):
        class WrongWindow(ScriptedProvider):
            def _http(self, endpoint, payload=None):
                response = super()._http(endpoint, payload)
                if endpoint == "/api/chat" and "Stage 3A:" in payload["messages"][0]["content"]:
                    result = json.loads(response["message"]["content"])
                    for value in result["rows"]:
                        value["speaker_cluster_id"] = "source:p2:c8"
                    response["message"]["content"] = json.dumps(result)
                return response
        raw, document, bank = fixture()
        raw["pages"] = [{"id": f"source:p{p}", "page": p, "panels": [], "characters": [],
                         "character_cluster_labels": [p + 6], "character_ids": [p]} for p in (1, 2)]
        document["raw_fingerprint"] = document_fingerprint(raw)
        for row in document["utterances"]:
            row["page_id"] = f"source:p{row['page']}"
        chapter = Chapter(document, raw, bank=bank, text_only=True)
        self.assertIn("source:p2:c8", chapter.clusters)
        client = WrongWindow(config=ReasoningConfig(num_ctx=100000, max_targets=1, overlap=1))
        with self.assertRaisesRegex(InvalidResponse, "not supplied in this source window"):
            run_reasoning(chapter, client, correct_order=False)

    def test_unknown_scene_boundaries_remain_soft_and_flagged(self):
        raw, document, bank = fixture()
        result = run_reasoning(Chapter(document, raw, bank=bank, text_only=True), ScriptedProvider(uncertain=True), scenes_only=True)
        self.assertEqual(len({r["scene_id"] for r in result["utterances"]}), 1)
        self.assertTrue(all(r["scene_status"] == "needs_review" for r in result["utterances"]))

    def test_invented_identity_and_cross_page_permutation_are_rejected(self):
        raw, document, bank = fixture()
        chapter = Chapter(document, raw, bank=bank, text_only=True)
        with self.assertRaises(InvalidResponse):
            run_reasoning(chapter, ScriptedProvider(bad_identity=True))
        with self.assertRaises(InvalidResponse):
            run_reasoning(chapter, ScriptedProvider(bad_order=True))

    def test_source_fingerprint_and_visual_requirement(self):
        raw, document, bank = fixture()
        raw["texts"][0]["text"] = "changed source"
        with self.assertRaises(ValueError):
            Chapter(document, raw, bank=bank, text_only=True)
        with self.assertRaises(ValueError):
            Chapter(document, bank=bank)

    def test_real_image_path_and_silent_panel_are_processed(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            Image.new("RGB", (100, 100), "white").save(root / "page_1.png")
            raw, document, bank = fixture()
            raw["texts"] = raw["texts"][:3]
            document = normalize_document(raw)
            chapter = Chapter(document, raw, bank=bank, images_dir=root)
            client = ScriptedProvider()
            result = run_reasoning(chapter, client, scenes_only=True)
            self.assertEqual(len(result["visual_observations"]), 1)
            self.assertEqual(len(chapter.encode_image(next(iter(chapter.blocks)), 64)["data"]) > 0, True)

    def test_silent_magi_panel_is_not_discarded_by_visual_extraction(self):
        import numpy as np
        from PIL import Image
        from dialogue_data import build_raw_document
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "page_1.png"
            Image.new("RGB", (100, 100), "white").save(path)
            prediction = {"texts": [[60, 5, 80, 20]], "ocr": ["Hello"],
                          "panels": [[50, 0, 100, 100], [0, 0, 49, 100]], "characters": [],
                          "character_cluster_labels": [], "text_character_associations": [],
                          "is_essential_text": [True]}
            raw = build_raw_document([np.zeros((100, 100, 3))], [path], [prediction],
                                     story_id="s", chapter_id="c", model_fingerprint="fixture")
            document = normalize_document(raw)
            result = run_reasoning(Chapter(document, raw), ScriptedProvider(), scenes_only=True)
            self.assertEqual(len(result["visual_observations"]), 2)

    def test_ordering_member_blocks_shrink_on_budget_limit(self):
        raw, document, bank = fixture()
        chapter = Chapter(document, raw, bank=bank, text_only=True)
        client = ScriptedProvider()
        client.prepare()
        from scene_reasoning import segment_scenes
        scenes = segment_scenes(chapter, client, observations={})
        runner = TaskRunner(client, chapter)
        base_execute = runner.execute
        def limited(task, instruction, targets, rows, anchor, check=None):
            if any(len(t.get("member_ids", [])) > 2 for t in targets):
                raise BudgetExceeded("Order block too large")
            return base_execute(task, instruction, targets, rows, anchor, check)
        runner.execute = limited
        reordered, changed, audit = reorder_dialogue(copy.deepcopy(scenes["utterances"]), runner)
        self.assertEqual(reordered[0]["text"], "Question from A")
        self.assertTrue(changed)

    def test_imported_image_adapter_keeps_reference_fingerprint_for_gold_oracle(self):
        from PIL import Image
        raw, document, bank = fixture()
        reference = review_template(document)
        reference.update(confirmed=True, reviewer="independent annotator")
        for row in reference["boundaries"]:
            row["boundary"] = False
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for number in (1, 2):
                Image.new("RGB", (100, 100)).save(root / f"page_{number}.png")
            chapter = Chapter(document, raw, bank=bank, images_dir=root)
            result = run_reasoning(chapter, ScriptedProvider(), scenes_only=True,
                                   scene_source="gold", reference=reference)
            self.assertEqual(result["scene_config"]["method"], "gold_scene_oracle")
            self.assertNotIn("page_id", document["utterances"][0])

    def test_owned_windows_split_on_resource_limit_without_losing_targets(self):
        raw, document, bank = fixture()
        chapter = Chapter(document, raw, bank=bank, text_only=True)
        provider = ScriptedProvider(limit=2)
        provider.prepare()
        runner = TaskRunner(provider, chapter)
        targets = [{"id": r["id"]} for r in chapter.rows]
        mapped = runner.execute("speaker", "Stage 3A:", targets, chapter.rows,
                                lambda t: [next(i for i, r in enumerate(chapter.rows) if r["id"] == t["id"])])
        self.assertEqual(set(mapped), {r["id"] for r in chapter.rows})
        self.assertTrue(runner.warnings)

    def test_validator_rejects_a_cross_page_move_even_with_valid_ids(self):
        raw, document, bank = fixture()
        chapter = Chapter(document, raw, bank=bank, text_only=True)
        client = ScriptedProvider()
        from scene_reasoning import segment_scenes
        client.prepare()
        scenes = segment_scenes(chapter, client, observations={})
        from dialogue_reconstruction import reconstruct_dialogue
        result = reconstruct_dialogue(chapter, scenes, client)
        result["utterances"][0], result["utterances"][-1] = result["utterances"][-1], result["utterances"][0]
        with self.assertRaises(ValueError):
            validate_reconstructed(chapter, scenes, result)


class ClientContracts(unittest.TestCase):
    def test_semantic_retry_tells_model_what_to_repair(self):
        class RepairProvider(ScriptedProvider):
            def _http(self, endpoint, payload=None):
                value = super()._http(endpoint, payload)
                if endpoint == "/api/chat" and len(self.calls) == 1:
                    broken = json.loads(value["message"]["content"])
                    broken["rows"][0]["status"] = "unknown"
                    value["message"]["content"] = json.dumps(broken)
                return value
        provider = RepairProvider()
        provider.prepare()
        data = {"targets": [{"id": "a"}], "utterances": [{"id": "a", "text": "Question from A"}]}
        def check(value):
            row = value["rows"][0]
            if row["status"] == "unknown" and row["speaker_id"] is not None:
                raise ValueError("Unknown identity must be null")
        result = provider.infer("speaker", "Stage 3A:", data, SCHEMAS["speaker"], [], check)
        self.assertEqual(result["rows"][0]["status"], "predicted")
        self.assertIn("Validator correction: Unknown identity must be null", provider.calls[1][0])

    def test_checkpoint_resume_and_corrupt_cache_revalidation(self):
        raw, document, bank = fixture()
        chapter = Chapter(document, raw, bank=bank, text_only=True)
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            first = ScriptedProvider(cache)
            run_reasoning(chapter, first, correct_order=False)
            second = ScriptedProvider(cache)
            run_reasoning(chapter, second, correct_order=False)
            self.assertEqual(len(second.calls), 0)
            self.assertTrue(all(e["cache_hit"] for e in second.events))
            file = next(cache.glob("*.json"))
            corrupt = json.loads(file.read_text())
            corrupt["result"]["rows"][0]["id"] = "invented"
            file.write_text(json.dumps(corrupt), encoding="utf-8")
            third = ScriptedProvider(cache)
            run_reasoning(chapter, third, correct_order=False)
            self.assertGreater(len(third.calls), 0)

    def test_prompt_change_invalidates_cache_and_budget_rejects_before_http(self):
        with tempfile.TemporaryDirectory() as temporary:
            client = ScriptedProvider(Path(temporary))
            client.prepare()
            data = {"targets": [{"id": "x", "before_index": 1, "left_id": "a"}], "utterances": []}
            client.infer("boundaries", "For every target gap", data, SCHEMAS["boundaries"], [])
            client.infer("boundaries", "For every target gap revised", data, SCHEMAS["boundaries"], [])
            self.assertEqual(len(client.calls), 2)
            tiny = ScriptedProvider(config=ReasoningConfig(num_ctx=2048, num_predict=1024))
            tiny.prepare()
            with self.assertRaises(BudgetExceeded):
                tiny.infer("boundaries", "x" * 3000, data, SCHEMAS["boundaries"], [])
            self.assertEqual(tiny.calls, [])

    def test_estimated_budget_still_checks_real_usage_before_caching(self):
        class OversizedUsage(ScriptedProvider):
            def _http(self, endpoint, payload=None):
                response = super()._http(endpoint, payload)
                if endpoint == "/api/chat":
                    response["prompt_eval_count"] = self.config.num_ctx
                return response
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            client = OversizedUsage(directory, config=ReasoningConfig(text_bytes_per_token=2.0))
            client.prepare()
            data = {"targets": [{"id": "gap", "before_index": 1, "left_id": "a"}], "utterances": []}
            with self.assertRaisesRegex(ResourceLimit, "Actual prompt usage"):
                client.infer("boundaries", "For every target gap", data, SCHEMAS["boundaries"], [])
            self.assertEqual(list(directory.glob("*.json")), [])
            with self.assertRaises(ValueError):
                load_config(text_bytes_per_token=float("nan"))

    def test_schema_does_not_accept_bool_as_identity_or_unexpected_commands(self):
        with self.assertRaises(ValueError):
            validate(True, {"type": "integer"})
        with self.assertRaises(ValueError):
            validate({"rows": [], "bank_update": True}, SCHEMAS["speaker"])
        with self.assertRaises(ValueError):
            load_config("cpu", model="")


class EvaluationContracts(unittest.TestCase):
    def test_reference_is_blank_and_requires_human_confirmation(self):
        _, document, _ = fixture()
        reference = review_template(document)
        self.assertFalse(reference["confirmed"])
        self.assertTrue(all(r["speaker_reviewed"] is False for r in reference["utterances"]))
        with self.assertRaises(ValueError):
            validate_reference(document, reference)

    def test_masked_metrics_count_false_corrections_and_typed_ids(self):
        raw, document, bank = fixture()
        result = run_reasoning(Chapter(document, raw, bank=bank, text_only=True), ScriptedProvider(), correct_order=False)
        reference = review_template(document)
        reference.update(confirmed=True, reviewer="independent test annotator")
        reference["boundaries"][0]["boundary"] = False
        reference["boundaries"][-1]["boundary"] = True
        reference["utterances"][0].update(speaker_reviewed=True, speaker_id=2)
        reference["utterances"][1].update(speaker_reviewed=True, speaker_id="1")
        report = evaluate(document, result, reference)
        self.assertEqual(report["boundary"]["scored_gaps"], 2)
        self.assertEqual(report["speaker"]["accuracy"], 0.5)
        self.assertEqual(report["speaker"]["correction"]["tp"], 1)

    def test_gold_labels_are_rejected_on_predicted_cli_and_dry_run_is_offline(self):
        raw, document, bank = fixture()
        with tempfile.TemporaryDirectory() as temporary, redirect_stdout(io.StringIO()):
            root = Path(temporary)
            write_json(root / "source.json", document)
            args = build_parser().parse_args(["reason", str(root / "source.json"), "--output", str(root / "plan.json"),
                                              "--dry-run", "--text-only"])
            with patch.object(OllamaReasoner, "prepare", side_effect=AssertionError("No server call allowed")):
                self.assertEqual(run(args), 0)
            self.assertEqual(json.loads((root / "plan.json").read_text())["kind"], "reasoning_plan")
            args = build_parser().parse_args(["reason", str(root / "source.json"), "--output", str(root / "bad.json"),
                                              "--text-only", "--reference", str(root / "gold.json")])
            with self.assertRaises(ValueError):
                run(args)


if __name__ == "__main__":
    unittest.main()
