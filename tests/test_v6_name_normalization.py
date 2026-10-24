"""Name-only patches, provenance, ambiguity and downstream propagation."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from manga_pipeline.v6 import name_normalization as names, scene, semantic
from manga_pipeline.v6.independent import analyze
from manga_pipeline.v6.merge import merge
from manga_pipeline.v6.planning import pool
from manga_pipeline.v6.stages import write_review
from manga_pipeline.storage.io import atomic_json
from manga_pipeline.storage.io import read_json
from manga_pipeline.v6.verification import verify
from test_v6_independent_batches import fixture, Provider


def proposal(uid, original, replacement, start=0, refs=None, status="confirmed"):
    return {"utterance_id": uid, "start": start, "end": start+len(original),
            "original": original, "replacement": replacement, "status": status,
            "evidence": "A clear name occurrence supports this form.", "evidence_refs": refs or [uid]}


def normalize(rows, patches=(), groups=(), suspicions=(), state=None, surfaces=None):
    spans = scene.validate_names({"names": surfaces or [
        {"utterance_id": r["id"], "name": r["text"]} for r in rows]}, rows)
    return names.process({"patches": list(patches), "groups": list(groups),
                          "suspicions": list(suspicions)}, rows, spans, state or {})


class NamePatches(unittest.TestCase):
    def test_preserves_sentence_and_unicode_offsets_and_maps_later_mentions(self):
        text = "😀 Hi SASA- MORI - CHAN! Ask SASA- MORI."
        a, b = "SASA- MORI - CHAN", "SASA- MORI"
        rows = [{"id": "u", "text": text}]
        # Detect the two complete names, not the surname inside the honorific.
        spans = {"u": [{"start": text.index(a), "end": text.index(a)+len(a)},
                       {"start": text.rindex(b), "end": text.rindex(b)+len(b)}]}
        payload = {"patches": [proposal("u", a, "sasamori-chan", text.index(a)),
                               proposal("u", b, "sasamori", text.rindex(b))], "groups": [], "suspicions": []}
        result = names.process(payload, rows, spans, {})
        fixed = names.apply({"utterances": rows}, result)["utterances"][0]
        self.assertEqual(fixed["text"], "😀 Hi SASAMORI-CHAN! Ask SASAMORI.")
        self.assertEqual(fixed["original_text"], text)
        for span in spans["u"]:
            start, end = names.mapped_span(span["start"], span["end"], names.edits(result, "u"))
            self.assertIn(fixed["text"][start:end], ("SASAMORI-CHAN", "SASAMORI"))
        self.assertEqual(rows[0]["text"], text)

    def test_spelling_requires_clear_chapter_or_registry_evidence(self):
        rows = [{"id": "bad", "text": "Sasamorl"}, {"id": "good", "text": "Sasamori"}]
        result = normalize(rows, [proposal("bad", "Sasamorl", "Sasamori", refs=["good"])])
        self.assertEqual(result["patches"][0]["status"], "applied")
        rejected = normalize(rows, [proposal("bad", "Sasamorl", "Sasamori")])
        self.assertIn("supporting", rejected["rejected"][0]["reason"])
        state = {"persons": {"p": {"primary_name": "Sasamori", "name_forms": []}}}
        result = normalize(rows[:1], [proposal("bad", "Sasamorl", "Sasamori", refs=["p"])], state=state)
        self.assertEqual(result["patches"][0]["status"], "applied")

    def test_reject_bad_source_non_name_unknown_evidence_honorific_and_expansion(self):
        rows = [{"id": "u", "text": "Sasamori-chan"}]
        for p in (proposal("u", "Sasamori-chan", "Sasamori-sama"),
                  proposal("u", "Sasamori-chan", "Sasamori!"),
                  proposal("u", "Sasamori-chan", "Sasamori-chan", refs=["invented"]),
                  proposal("u", "Sasamori-chan", "Sasamori Koyuki Full New Name-chan"),
                  {**proposal("u", "Sasamori-chan", "Sasamori-chan"), "original": "wrong"}):
            result = normalize(rows, [p])
            self.assertEqual(len(result["rejected"]), 1)
            self.assertEqual(names.apply({"utterances": rows}, result)["utterances"], rows)

    def test_conflicts_and_suspicions_leave_source_unchanged(self):
        rows = [{"id": "u", "text": "sasa- mori"}]
        p = proposal("u", rows[0]["text"], "sasamori")
        result = normalize(rows, [p, copy.deepcopy(p)])
        self.assertEqual([p["status"] for p in result["patches"]], ["rejected", "rejected"])
        self.assertEqual(names.apply({"utterances": rows}, result)["utterances"], rows)
        p["status"] = "suspect"
        result = normalize(rows, [p], suspicions=[{"utterance_id": "u", "start": 0,
                                                 "end": len(rows[0]["text"]), "reason": "Uncertain spelling"}])
        self.assertEqual(result["patches"][0]["status"], "suspect")
        self.assertEqual(names.apply({"utterances": rows}, result)["utterances"], rows)

    def test_alias_groups_remain_proposals_without_mutating_registry(self):
        rows = [{"id": "a", "text": "Sasamori"}, {"id": "b", "text": "Sasamori Koyuki"}]
        group = {"group_id": "g1", "canonical_name": "Sasamori Koyuki", "members": [
            {"utterance_id": r["id"], "start": 0, "end": len(r["text"])} for r in rows],
            "evidence": "The introduction supports these aliases.", "evidence_refs": ["a", "b"]}
        state = {"persons": {}}
        result = normalize(rows, groups=[group], state=state)
        self.assertEqual(result["groups"][0]["status"], "proposed")
        self.assertEqual(state, {"persons": {}})
        result = normalize(rows, groups=[group, {**group, "group_id": "g2"}])
        self.assertTrue(all(g["status"] == "suspect" for g in result["groups"]))

    def test_group_can_expose_registry_owner_without_forcing_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            _, _, doc, _, frozen = fixture(Path(directory), 2)
            state = copy.deepcopy(frozen["state"])
            state["persons"]["person:3"] = {"primary_name": "Sasamori Koyuki", "name_forms": []}
            state["name_index"]["sasamori koyuki"] = ["person:3"]
            candidate = {"name": "koyuki-chan", "alias_groups": [{"group_id": "g", "status": "proposed",
                "canonical_name": "Sasamori Koyuki", "aliases": ["Koyuki-chan", "Sasamori Koyuki"]}]}
            _, known = pool(doc["utterances"][:1], state, {"m": candidate})
            self.assertIn("person:3", known)
            self.assertNotIn("koyuki", state["name_index"])

    def test_changed_source_cannot_receive_cached_patch(self):
        rows = [{"id": "u", "text": "sasa- mori"}]
        result = normalize(rows, [proposal("u", "sasa- mori", "sasamori")])
        with self.assertRaisesRegex(ValueError, "source changed"):
            names.apply({"utterances": [{"id": "u", "text": "Other"}]}, result)

    def test_only_confirmed_owned_names_persist_and_review_contains_suspicions(self):
        with tempfile.TemporaryDirectory() as directory:
            store, raw, doc, _, frozen = fixture(Path(directory), 2)
            candidate = {"id": "m", "utterance_id": "u0", "name": "sasamori-chan",
                         "raw_surface": "SASA- MORI - CHAN", "start": 3, "end": 16,
                         "original_start": 3, "original_end": 19, "name_suspect": True}
            mention = {"candidate_id": "m", "confirmed_person_name": True,
                       "target_person_id": "person:1", "mention_type": "reference", "components": [],
                       "evidence": "The dialogue identifies this person."}
            record = {"utterance_id": "u0", "mentions": [mention], "semantic_resolution": "resolved",
                      "content_type": "dialogue", "speaker_person_id": "person:1",
                      "speaker_cluster_ref": None, "speaker_kind": "person"}
            _, state, _ = merge(doc["utterances"], [[record]], frozen["state"], {"m": candidate}, raw)
            self.assertIn("sasamori", state["name_index"])
            self.assertNotIn("sasa- mori", state["name_index"])
            mention.update(confirmed_person_name=False, target_person_id=None)
            output, state, _ = merge(doc["utterances"], [[record]], frozen["state"], {"m": candidate}, raw)
            self.assertEqual(state["name_index"], {})
            store.steps = ["scan", "analyze"]
            store.directories = {step: step for step in store.steps}
            store.manifest["steps"] = {}
            patch_data = proposal("u0", "SASA- MORI - CHAN", "SASAMORI-CHAN", 3, status="suspect")
            normalization = {"patches": [patch_data], "groups": [], "suspicions": [
                {"utterance_id": "u0", "start": 3, "end": 19, "reason": "Ambiguous spelling"}]}
            atomic_json(store.artifact_path("scan"), {"name_normalization": normalization})
            atomic_json(store.artifact_path("analyze"), {"name_normalization": normalization, "utterances": output})
            write_review(store)
            review = (store.directory / "analyze/review.md").read_text(encoding="utf-8")
            self.assertIn("Name suspect", review)
            self.assertIn("confirmed=False", review)
            self.assertIn("SASA- MORI - CHAN", review)

    def test_one_scene_call_then_analyze_export_use_corrected_names(self):
        class Joint(Provider):
            def infer_reserved(self, task, prompt, data, schema, validator, *, reserve):
                value = super().infer_reserved(task, prompt, data, schema, validator, reserve=reserve)
                if task == "analyze_scene_v6":
                    self.registry_seen = data["name_registry"]
                    value["names"] = [{"utterance_id": "u0", "name": "SASA- MORI - CHAN"}]
                    value["name_normalization"] = {"patches": [proposal("u0", "SASA- MORI - CHAN", "sasamori-chan", 3)],
                                                   "groups": [{"group_id": "sasamori", "canonical_name": "Sasamori",
                                                       "members": [{"utterance_id": "u0", "name": "SASA- MORI - CHAN", "occurrence_index": 0}],
                                                       "evidence": "Same corrected name.", "evidence_refs": ["u0"]}], "suspicions": []}
                elif task == "analyze_dialogue_v6":
                    for row in data["targets"]:
                        for mention in data["mentions"]:
                            if mention["utterance_id"] == row["id"]:
                                value["utterances"][row["id"]]["mentions"][mention["id"]] = {
                                    "confirmed_person_name": False, "mention_type": "unknown", "target_person_id": None,
                                    "recipient_cluster_ref": None, "visual_support": "unknown", "components": [],
                                    "evidence": "Uncertain owner."}
                return value
        with tempfile.TemporaryDirectory() as directory:
            store, raw, doc, _, frozen = fixture(Path(directory), 2)
            store.manifest["config"]["ner"]["backend"] = "llm-scene"
            store.target = lambda *args: None
            doc["utterances"][0]["text"] = "HI SASA- MORI - CHAN!"
            provider = Joint()
            scanned = semantic.scan(store, doc, raw, None, provider=provider)
            self.assertEqual(scanned["candidates"][0]["name"], "sasamori-chan")
            with patch.object(scene, "plan", side_effect=AssertionError("Extra scene request")):
                analyzed = analyze(store, raw, doc, scanned, provider)
            verify({"normalize": doc, "extract": raw, "scan": scanned, "analyze": analyzed},
                   store.manifest["config"], frozen, read_json(store.manifest["bank_path"]),
                   read_json(store.artifact_path("analyze").parent / "plan.json"))
            requests = [data for task, data, _ in provider.calls if task == "analyze_dialogue_v6"]
            self.assertEqual(requests[0]["targets"][0]["text"], "HI SASAMORI-CHAN!")
            exported = semantic.export(doc, analyzed, {"person_state": analyzed["person_state"], "proposals": []})
            self.assertEqual(exported["utterances"][0]["text"], "HI SASAMORI-CHAN!")
            self.assertEqual(exported["utterances"][0]["original_text"], "HI SASA- MORI - CHAN!")
            self.assertEqual(analyzed["person_state"]["name_index"], {})
            self.assertEqual(provider.registry_seen, [])


if __name__ == "__main__":
    unittest.main()
