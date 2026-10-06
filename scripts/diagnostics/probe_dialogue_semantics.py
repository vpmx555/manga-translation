"""Isolated semantic probe; no production run or character-bank writes."""
import argparse
import copy
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from manga_pipeline.names.stages import object_schema
from manga_pipeline.providers.ollama import Ollama
from manga_pipeline.storage.io import atomic_json, read_json


CONTENT = {"dialogue", "narration", "thought", "unknown"}
ADDRESS = {"single", "group", "audience", "self", "unknown"}
ROLES = {"introduction", "direct_address", "reference", "unknown"}
PROMPT = (
    "Analyze each target using only its context_ids. This is a semantic classification probe, "
    "not an identity-resolution task: do not enumerate speakers or group members. "
    "Source speaker hints can be mistaken and do not determine whether a text is narration. "
    "content_type: narration is external narrative prose or a character identity caption; "
    "dialogue is speech to someone, including spoken introductions; thought is internal speech; "
    "unknown if unclear. Do not infer narration merely from past tense, coherent sentences or public recipients. "
    "addressee_type: single for one recipient, group for multiple people, audience for readers/viewers, "
    "self for oneself, unknown if unclear. Unknown member identities do not make a collective address single. "
    "For EACH supplied name candidate choose its communicative role: introduction identifies or presents "
    "who a person is, including self-identification, presenting someone else or an identity caption; "
    "direct_address calls or addresses that person as the recipient; reference discusses an already "
    "mentioned person without introducing or addressing them; unknown for non-person candidates or uncertainty. "
    "Mentioning a name is not sufficient for introduction or direct_address. Decide from the sentence's "
    "purpose and context, not a particular spelling, number of name words or one caption template. "
    "Introduction can occur in narration or dialogue; it does not require an identifiable speaker. "
    "Return each target and each of its candidates exactly once. Evidence must be short. Return JSON only."
)


def cases_from_run(directory):
    document = read_json(directory / "02_normalize" / "result.json")
    scanned = read_json(directory / "03_scan" / "result.json")
    classified = read_json(directory / "04_classify" / "result.json")
    rows = document["utterances"]
    by_id = {r["utterance_id"]: r for r in classified["utterances"]}
    specs = [
        ("P01/T01", "p1:t0:u", None, {"group", "audience"}, None),
        ("P02/T23", "p2:t22:u", {"narration"}, None, "introduction"),
        ("P03/T06", "p3:t5:u", {"narration"}, None, "introduction"),
        ("P02/T02", "p2:t1:u", None, None, "introduction"),
        ("P04/T03", "p4:t2:u", {"dialogue"}, None, "direct_address"),
    ]
    output = []
    for label, suffix, kinds, addresses, role in specs:
        index = next(i for i, row in enumerate(rows) if row["id"].endswith(suffix))
        row = rows[index]
        names = [c["name"] for c in scanned["candidates"] if c["utterance_id"] == row["id"]]
        old = by_id[row["id"]]
        output.append({"label": label, "source_id": row["id"], "target": row,
                       "context": rows[max(0, index - 2):index + 3], "names": names,
                       "expected_content": kinds, "expected_address": addresses, "expected_role": role,
                       "baseline": {"content_type": old["content_type"], "addressee_type": old["addressee_type"],
                                    "mentions": [{"name": m["name"], "mention_type": m["mention_type"]}
                                                 for m in old["mentions"]]}})
    controls = [
        ("spoken introduction", "Who's joining us today?", "This is our new club member, Hana Mori.",
         "Nice to meet you both.", "introduction", None),
        ("self introduction", "You haven't introduced yourself. What's your name?", "My name is Hana Mori.",
         "Nice to meet you, Hana.", "introduction", None),
        ("reference", "Did Hana call you yesterday?", "I haven't heard from Hana Mori since last week.",
         "Let me know if she calls.", "reference", {"dialogue"}),
        ("direct address", "Can we start the call?", "Hana Mori, can you hear me?",
         "Yes, I'm listening.", "direct_address", {"dialogue"}),
    ]
    for i, (label, before, target, after, role, kinds) in enumerate(controls):
        context = [{"id": f"control-{i}-{j}", "text": text, "speaker_id": 1 if j == 1 else 3}
                   for j, text in enumerate((before, target, after))]
        output.append({"label": "control: " + label, "source_id": context[1]["id"],
                       "target": context[1], "context": context, "names": ["Hana Mori"],
                       "expected_content": kinds, "expected_address": None, "expected_role": role,
                       "baseline": None})
    return output


def request_for(batch):
    turns, refs, targets = [], {}, []
    for i, case in enumerate(batch):
        for row in case["context"]:
            if row["id"] not in refs:
                refs[row["id"]] = f"u{len(refs)}"
                turns.append({"id": refs[row["id"]], "text": row["text"],
                              "source_speaker_id": row.get("speaker_id")})
        targets.append({"id": f"t{i}", "turn_id": refs[case["source_id"]],
                        "context_ids": [refs[r["id"]] for r in case["context"]],
                        "candidates": [{"id": f"n{i}-{j}", "name": name}
                                       for j, name in enumerate(case["names"])]})
    return {"turns": turns, "targets": targets}


def response_schema(targets):
    candidates = [c["id"] for t in targets for c in t["candidates"]]
    return object_schema({"targets": {"type": "array", "minItems": len(targets), "maxItems": len(targets),
        "items": object_schema({"id": {"enum": [t["id"] for t in targets]},
            "content_type": {"enum": sorted(CONTENT)}, "addressee_type": {"enum": sorted(ADDRESS)},
            "mentions": {"type": "array", "items": object_schema({
                "candidate_id": {"enum": candidates}, "mention_type": {"enum": sorted(ROLES)}})},
            "evidence": {"type": "string", "maxLength": 120}})}})


def validator_for(targets):
    expected = {t["id"]: t for t in targets}

    def validate(value):
        rows = value.get("targets")
        if not isinstance(rows, list) or len(rows) != len(expected) or {r.get("id") for r in rows} != set(expected):
            raise ValueError("Probe target coverage changed")
        for row in rows:
            if row.get("content_type") not in CONTENT or row.get("addressee_type") not in ADDRESS:
                raise ValueError("Invalid probe semantic labels")
            ids = {c["id"] for c in expected[row["id"]]["candidates"]}
            mentions = row.get("mentions")
            if not isinstance(mentions, list) or len(mentions) != len(ids) or {m.get("candidate_id") for m in mentions} != ids:
                raise ValueError("Probe name coverage changed")
            if any(m.get("mention_type") not in ROLES for m in mentions):
                raise ValueError("Invalid probe name role")
            if not isinstance(row.get("evidence"), str) or len(row["evidence"]) > 120:
                raise ValueError("Invalid probe evidence")
    return validate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    manifest = read_json(args.run_dir / "manifest.json")
    config = copy.deepcopy(manifest["config"]["ollama"])
    provider = Ollama(config, args.output_dir / "cache")
    cases = cases_from_run(args.run_dir)
    report = {"kind": "isolated_semantic_probe", "production_pipeline_modified": False,
              "source_run": str(args.run_dir.resolve()), "model": config["model"],
              "prompt": PROMPT, "role_labels": sorted(ROLES), "requests": [], "results": [], "failures": [],
              "limitations": ["Nine exploratory cases, not an accuracy benchmark.",
                              "Identity resolution and group-member enumeration are intentionally omitted.",
                              "Synthetic controls use supplied names; no NER test is performed.",
                              "Production baseline came from the existing run and has a different task/schema."]}
    original_http = provider.http

    def capture(route, body=None):
        if route == "/api/chat":
            report["requests"].append({"think": body.get("think"),
                                      "images": sum(len(m.get("images", [])) for m in body["messages"])})
        return original_http(route, body)
    provider.http = capture
    started = time.perf_counter()
    try:
        for start in range(0, len(cases), 3):
            batch = cases[start:start + 3]
            request = request_for(batch)
            t0 = time.perf_counter()
            print(f"Probe batch {start // 3 + 1}/3: " + ", ".join(c["label"] for c in batch), flush=True)
            try:
                value = provider.infer("probe_dialogue_semantics_v1", PROMPT, request,
                                       response_schema(request["targets"]), validator_for(request["targets"]))
                by_id = {x["id"]: x for x in value["targets"]}
                for i, case in enumerate(batch):
                    result = by_id[f"t{i}"]
                    names = {c["id"]: c["name"] for c in request["targets"][i]["candidates"]}
                    mentions = [{"name": names[m["candidate_id"]], "mention_type": m["mention_type"]}
                                for m in result["mentions"]]
                    checks = {}
                    if case["expected_content"]:
                        checks["content"] = result["content_type"] in case["expected_content"]
                    if case["expected_address"]:
                        checks["address"] = result["addressee_type"] in case["expected_address"]
                    if case["expected_role"]:
                        checks["role"] = bool(mentions) and all(m["mention_type"] == case["expected_role"] for m in mentions)
                    report["results"].append({"label": case["label"], "source_id": case["source_id"],
                        "text": case["target"]["text"], "content_type": result["content_type"],
                        "addressee_type": result["addressee_type"], "mentions": mentions,
                        "evidence": result["evidence"], "checks": checks, "baseline": case["baseline"]})
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
