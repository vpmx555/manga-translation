"""One-call semantic/identity experiment with a simple schema; no bank updates."""
import argparse
import copy
import json
import time
from pathlib import Path

from probe_dialogue_semantics import cases_from_run, request_for, response_schema, validator_for
from manga_pipeline.bank.names import known_names
from manga_pipeline.providers.ollama import Ollama
from manga_pipeline.storage.io import atomic_json, read_json


PROMPT = (
    "Analyze every target using its context_ids and identity_candidates. Return semantic labels and IDs together. "
    "Do not infer scenes or change order. No images are supplied. Source speaker IDs are fallible hints. "
    "Narration is a narrative or presentational function: reporting events, explaining or presenting information "
    "or identities. It can be an external caption or an actual character speaking. "
    "Dialogue primarily interacts with someone: calling, asking, answering, requesting. "
    "Thought is internal speech; unknown if function is unclear. An introduction can be narration or dialogue. "
    "Do not infer the speaker or recipients merely from content_type. A spoken presentation may have a character "
    "speaker and single/group/audience recipients. An identity caption is normally narrator; its MAGI association "
    "can refer to the introduced person, not its teller. Use narrator for external narrative voice, "
    "others for an unidentified speaking character, or a supplied stable character ID when supported. "
    "single means one recipient; group multiple people; audience readers/viewers; self oneself; unknown unclear. "
    "Unidentified group members do not make an address single. Group member enumeration is optional: "
    "keep only IDs with evidence, or [unknown]. A partial group may contain one known ID; do not add IDs "
    "or an unknown member just to satisfy a numeric minimum. Candidate IDs are possibilities, not recipients. "
    "audience uses [public_audience]; self uses [speaker_id] for a known character or [self] for others; "
    "unknown type uses [unknown]; single uses one other stable ID or [unknown]. "
    "Use only each target's supplied stable IDs or special tokens. Narrator is not an in-story stable ID. "
    "For each supplied person-name candidate, introduction presents who someone is, including self-identification, "
    "another-person introduction and captions; direct_address calls that person as recipient; reference discusses "
    "the person without introducing or addressing them; unknown for non-person candidates or uncertainty. "
    "A direct_address conclusion to a single known recipient must have name_target_id equal that recipient ID. "
    "If the name is already unambiguously mapped, do not assign it to another ID. "
    "Introduction subject and speaker are separate: a clear self-introduction can hint the speaker's ID; "
    "otherwise leave name_target_id null if the subject's ID is unclear. Introduction identity hints will be "
    "checked by a separate panel linker; do not guess from a shared surname or use an ID outside the pool. "
    "Return every target/name exactly once, with short evidence. JSON only."
)


def pair_issues(row, target):
    allowed = {x["id"] for x in target["identity_candidates"]}
    speaker, listeners, address = row["speaker_id"], row["addressee_ids"], row["addressee_type"]
    issues = []
    valid_id = lambda x: type(x) is int and x in allowed
    if not valid_id(speaker) and speaker not in {"others", "narrator"}:
        issues.append("speaker outside pool")
    if not listeners or len(set(listeners)) != len(listeners):
        issues.append("empty/duplicate recipients")
    if any(not valid_id(x) and x not in {"unknown", "self", "public_audience"} for x in listeners):
        issues.append("recipient outside pool")
    if address == "audience" and listeners != ["public_audience"]:
        issues.append("audience/public_audience mismatch")
    if address == "unknown" and listeners != ["unknown"]:
        issues.append("unknown recipient mismatch")
    if address == "self" and listeners != ([speaker] if valid_id(speaker) else ["self"]):
        issues.append("self/speaker mismatch")
    if address == "single" and (len(listeners) != 1 or not (valid_id(listeners[0]) or listeners == ["unknown"])):
        issues.append("single recipient mismatch")
    if address == "single" and valid_id(speaker) and speaker in listeners:
        issues.append("single speaker/recipient mismatch")
    if address == "group" and any(not valid_id(x) and x != "unknown" for x in listeners):
        issues.append("group uses incompatible token")
    candidates = {c["id"]: c for c in target["candidates"]}
    for m in row["mentions"]:
        identity = m["name_target_id"]
        if identity is not None and not valid_id(identity):
            issues.append("name ID outside pool")
        if m["mention_type"] == "direct_address":
            if address == "single" and len(listeners) == 1 and valid_id(listeners[0]) and identity != listeners[0]:
                issues.append("direct name/single recipient mismatch")
            if identity is not None and identity not in listeners:
                issues.append("direct name absent from recipients")
        mapped = candidates[m["candidate_id"]]["mapped_ids"]
        if identity is not None and mapped and mapped != [identity]:
            issues.append("known name remapped")
    return issues


def joint_schema(targets):
    """Three target branches, without enumerating semantic label combinations."""
    schema = response_schema(targets)
    branches = []
    for target in targets:
        item = response_schema([target])["properties"]["targets"]["items"]
        ids = [candidate["id"] for candidate in target["identity_candidates"]]
        item["properties"].update(speaker_id={"enum": [*ids, "others", "narrator"]},
            addressee_ids={"type": "array", "minItems": 1, "uniqueItems": True,
                           "items": {"enum": [*ids, "unknown", "self", "public_audience"]}})
        item["required"].extend(["speaker_id", "addressee_ids"])
        mentions = item["properties"]["mentions"]
        count = len(target["candidates"])
        mentions.update(minItems=count, maxItems=count)
        if count:
            mention = mentions["items"]
            mention["properties"]["name_target_id"] = {"enum": [*ids, None]}
            mention["required"].append("name_target_id")
        else:
            mentions["items"] = {"type": "object"}
        branches.append(item)
    schema["properties"]["targets"]["items"] = {"anyOf": branches}
    return schema


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    manifest = read_json(args.run_dir / "manifest.json")
    bank = read_json(Path(manifest["bank_path"]))
    known = known_names(bank)
    old = {x["utterance_id"]: x for x in read_json(args.run_dir / "04_classify" / "result.json")["utterances"]}
    cases = cases_from_run(args.run_dir)
    provider = Ollama(copy.deepcopy(manifest["config"]["ollama"]), args.output_dir / "cache")
    report = {"kind": "joint_dialogue_probe", "prompt": PROMPT, "model": manifest["config"]["ollama"]["model"],
              "source_run": str(args.run_dir.resolve()), "results": [], "requests": [], "failures": [],
              "limitations": ["Exploratory nine-case sample, not a semantic accuracy benchmark.",
                              "No bank updates, panel linking, recipient memory updates or semantic repair.",
                              "Pair issues are observed after inference, not used to coerce model output."]}
    original_http = provider.http

    def capture(route, body=None):
        if route == "/api/chat":
            report["requests"].append({"think": body.get("think"),
                                      "images": sum(len(m.get("images", [])) for m in body["messages"])})
        result = original_http(route, body)
        if route == "/api/chat":
            try:
                value = json.loads(result.get("message", {}).get("content", ""))
                report["requests"][-1]["returned_coverage"] = [
                    {"id": row.get("id"), "candidate_ids": [m.get("candidate_id") for m in row.get("mentions", [])]}
                    for row in value.get("targets", [])]
            except (ValueError, TypeError, AttributeError):
                report["requests"][-1]["returned_coverage"] = "unparseable"
        return result
    provider.http = capture
    started = time.perf_counter()
    try:
        for start in range(0, len(cases), 3):
            batch = cases[start:start + 3]
            request = request_for(batch)
            for target, case in zip(request["targets"], batch):
                if case["source_id"] in old:
                    ids = [x["id"] for x in old[case["source_id"]]["identity_candidates"]]
                else:
                    ids = sorted({r["speaker_id"] for r in case["context"] if type(r.get("speaker_id")) is int})
                target["identity_candidates"] = [{"id": i, "name": bank["characters"].get(str(i), {}).get("display_name"),
                    "aliases": bank["characters"].get(str(i), {}).get("aliases", [])} for i in ids]
                for c in target["candidates"]:
                    c["mapped_ids"] = known.get(c["name"].casefold().strip(), [])
            schema = joint_schema(request["targets"])
            t0 = time.perf_counter()
            print(f"Joint batch {start // 3 + 1}/3: " + ", ".join(c["label"] for c in batch), flush=True)
            try:
                value = provider.infer("probe_joint_dialogue_v2", PROMPT, request, schema,
                                       validator_for(request["targets"]))
                by_id = {x["id"]: x for x in value["targets"]}
                for i, case in enumerate(batch):
                    row = by_id[f"t{i}"]
                    target = request["targets"][i]
                    names = {c["id"]: c["name"] for c in target["candidates"]}
                    report["results"].append({"label": case["label"], "source_id": case["source_id"],
                        "text": case["target"]["text"], "content_type": row["content_type"],
                        "speaker_id": row["speaker_id"], "addressee_type": row["addressee_type"],
                        "addressee_ids": row["addressee_ids"],
                        "mentions": [{"name": names[m["candidate_id"]], "mention_type": m["mention_type"],
                                      "name_target_id": m["name_target_id"]} for m in row["mentions"]],
                        "evidence": row["evidence"], "pair_issues": pair_issues(row, target)})
                print(f"Finished batch in {time.perf_counter() - t0:.1f}s", flush=True)
            except Exception as exc:
                report["failures"].append({"labels": [c["label"] for c in batch], "error": str(exc)})
                print(f"Batch failed: {exc}", flush=True)
            report["elapsed_seconds"] = round(time.perf_counter() - started, 2)
            atomic_json(args.output_dir / "report.json", report)
    finally:
        provider.close()
    print(json.dumps({"report": str((args.output_dir / "report.json").resolve()),
                      "completed": len(report["results"]), "failed_batches": len(report["failures"]),
                      "requests": len(report["requests"]), "elapsed_seconds": report["elapsed_seconds"]}), flush=True)
    return 2 if report["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
