"""Frozen analyze-aligned natural translation with bounded parallel admission."""
import copy
import threading

from ..progress import NullProgress
from ..storage.io import atomic_json, fingerprint
from ..translation import contract
from ..runtime.inference_state import InferenceUncertain
from ..runtime.llm_budget import BudgetExceeded, OutputTruncated
from ..runtime.resources import ResourceExhausted
from . import relationships, scheduling
from .requests import Calls

POLICY = "natural-analyze-batches-v1"
ADDRESS_PROMPT = """Use the frozen address_table for each directed person pair.
Never invent a new identity or relationship or change the table. Dialogue-backed
decisions and visual_age_estimate decisions are distinguished; flag image-age
estimates for review. Unknown remains unknown: translate naturally from source,
flag unresolved pronouns, and do not invent ages, genders or social roles.
No previous translation batch is supplied; batches are independent. Preserve
proper names, honorifics and confirmed glossary. Effects remain data-only for
image rendering. Each target is one complete utterance, not a contour fragment.
"""


def partition(rows, analyzed_batches):
    by_id = {r["id"]: r for r in rows}
    if len(by_id) != len(rows):
        raise ValueError("Duplicate exported translation target")
    used, jobs = set(), []
    for batch in analyzed_batches:
        ids = [r["id"] for r in batch["rows"] if r["id"] in by_id]
        if len(set(ids)) != len(ids) or used.intersection(ids):
            raise ValueError("Overlapping analyze batches")
        used.update(ids)
        if ids:
            jobs.append({"batch_id": "natural:"+batch["batch_id"], "target_ids": ids,
                         "analyze_batch_id": batch["batch_id"], "scene_id": batch["request"]["scene_id"],
                         "scene_summary": batch["request"]["scene_summary"]})
    extra = [r["id"] for r in rows if r["id"] not in used]
    for offset in range(0, len(extra), 8):
        jobs.append({"batch_id": f"natural:additional:{offset//8+1}", "target_ids": extra[offset:offset+8],
                     "analyze_batch_id": None, "scene_id": None, "scene_summary": ""})
    return jobs


def translate(store, provider, progress=None):
    from .translation import resources, wire_schema, ordered_response
    from .identity_projection import document_view
    source_document = store.read('export')
    document = document_view(source_document)
    rows = sorted([*document["utterances"], *document["effects"]], key=lambda r: r["reading_order"])
    cfg = store.manifest["config"]
    frozen = resources(store, rows)
    v2 = 'relationship' in getattr(store, 'steps', ())
    graph = store.read('relationship') if v2 else relationships.build(store, document, provider)
    profiles = store.read('build_identity')['profiles'] if v2 else {}
    jobs = partition(rows, document.get("batches", []))
    by_id = {r["id"]: r for r in rows}
    positions = {r["id"]: i for i, r in enumerate(rows)}
    for row in rows:
        row["translations"] = {}
    fields = ("id", "text", "content_type", "speech_style", "speaker_person_id", "speaker_name",
              "addressee_ids", "mentions", "semantic_resolution", "semantic_needs_review", "source_name_candidates",
              "analysis_branch", "scene_id", "batch_id", "essential_index", "speaker_kind", "addressee_type")
    for job in jobs:
        selected = [by_id[uid] for uid in job["target_ids"]]
        people = {r.get("speaker_person_id") for r in selected}
        people.update(p for r in selected for p in r.get("addressee_ids", []))
        # All jobs read the same table snapshot; no completion can feed another.
        address = [r for r in graph["address_table"] if
                   r['from_id'] in people or r['to_id'] in people] if v2 else [
                   r for r in graph['address_table'] if r['a'] in people or r['b'] in people]
        context_ids = set()
        for uid in job["target_ids"]:
            i, radius = positions[uid], cfg["translation"]["context_radius"]
            context_ids.update(range(max(0, i-radius), min(len(rows), i+radius+1)))
        job["request"] = {"policy": POLICY, "style": "natural", "scene_id": job["scene_id"],
            "scene_summary": job["scene_summary"],
            "targets": [{k: r.get(k) for k in fields} for r in selected],
            "context": [{k: rows[i].get(k) for k in fields} for i in sorted(context_ids)],
            "address_table": address, "glossary": frozen["glossary"],
            "instructions": cfg["translation"]["instructions"],
            "manual": {uid: frozen["overrides"]["natural"][uid] for uid in job["target_ids"]
                       if uid in frozen["overrides"]["natural"]}}
        if v2:
            job['request']['profiles'] = {p: {'attributes': profiles[p]['attributes'], 'version': profiles[p]['version']}
                                         for p in people if p in profiles}
            job['request']['relationship_snapshot_hash'] = graph['snapshot_hash']
    plan = {"policy": POLICY, "source_hash": fingerprint(store.read("export")), "jobs": jobs,
            "address_table_hash": fingerprint(graph["address_table"]),
            "maximum_concurrency": min(cfg["v6"]["translation_concurrency"], cfg["runtime"]["llm_slots"])}
    atomic_json(store.artifact_path("translate").parent / "plan.json", plan)
    calls = Calls(store, provider, "translate")
    progress = progress or NullProgress()
    progress.reset(total=len(rows), unit="translation")
    lock = threading.RLock()
    prompt = contract.prompt("natural", cfg["translation"]["instructions"], "semantic-v6") + ADDRESS_PROMPT

    def validate(response, ids):
        ordered_response(response, ids)
        contract.readable(response)
        for item in response["translations"]:
            errors = contract.issues(item, by_id[item["id"]], frozen["glossary"])
            if errors:
                raise ValueError(", ".join(errors))

    def execute(key, request, depth=0):
        ids = [r["id"] for r in request["targets"]]
        manual = request["manual"]
        pending = [uid for uid in ids if uid not in manual]
        cached = store.target("translate", key, request)
        if cached is not None:
            validate(copy.deepcopy(cached), ids)
            publish(cached["translations"], request)
            return copy.deepcopy(cached["translations"])
        if not pending:
            result = [{"id": uid, "translation": manual[uid], "needs_review": False,
                       "review_reason": "", "glossary_proposals": []} for uid in ids]
            validate({"translations": result}, ids)
            with lock:
                store.commit_target("translate", key, request, result={"translations": result})
            publish(result, request)
            return result
        wire = {**request, "targets": [r for r in request["targets"] if r["id"] in pending]}
        schema = wire_schema(pending)
        reserve = min(8192, max(1536, 384*len(pending)))
        try:
            report = calls.budget("translate_vi_group_v6_natural", prompt, wire, schema, reserve)
            if report["estimate"] > report["limit"]:
                raise BudgetExceeded("Translation batch exceeds protected context")
            response = calls.call(key, "translate_vi_group_v6_natural", prompt, wire, schema, reserve=reserve)
            validate(response, pending)
            generated = {r["id"]: r for r in response["translations"]}
            result = [generated[uid] if uid in generated else {"id": uid, "translation": manual[uid],
                "needs_review": False, "review_reason": "", "glossary_proposals": []} for uid in ids]
            validate({"translations": result}, ids)
        except (ValueError, TypeError, KeyError) as exc:
            # Budget/shape/truncation recovery is a deterministic child tree.
            # Do not fit by deleting targets, glossary or the frozen address table.
            if len(ids) > 1 and (isinstance(exc, BudgetExceeded) or depth < cfg["runtime"]["recovery_attempts"]):
                cut = len(ids)//2
                result = []
                for number, subset in enumerate((ids[:cut], ids[cut:])):
                    child = {**request, "targets": [r for r in request["targets"] if r["id"] in subset],
                             "manual": {uid: value for uid, value in manual.items() if uid in subset}}
                    # Context is frozen source-only; eliminate unrelated target
                    # copies when a hard context limit forces a split.
                    if isinstance(exc, BudgetExceeded):
                        child["context"] = child["targets"]
                    result.extend(execute(key+f":split:{number}", child, depth+1))
            elif (len(ids) == 1 and (not isinstance(exc, BudgetExceeded) or isinstance(exc, OutputTruncated))
                  and depth < cfg["runtime"]["recovery_attempts"]):
                response = calls.call(key, "translate_vi_group_v6_natural", prompt, wire, schema,
                                      reserve=min(8192, reserve*2) if isinstance(exc, OutputTruncated) else reserve,
                                      attempt=depth+1)
                validate(response, pending)
                result = response["translations"]
            else:
                raise
        with lock:
            store.commit_target("translate", key, request, result={"translations": result})
        publish(result, request)
        return result

    def publish(result, request):
        with lock:
            for item in result:
                row = by_id[item["id"]]
                if "natural" in row["translations"]:
                    continue
                reasons = []
                if row.get("semantic_resolution") in {"unresolved", "source_only"}:
                    reasons.append("Unresolved source semantic")
                speaker, targets = row.get("speaker_person_id"), set(row.get("addressee_ids", []))
                if v2:
                    # A reverse edge is a separate choice, not evidence about
                    # the current speaker's terms or review status.
                    relations = [r for r in request["address_table"] if
                                 r["from_id"] == speaker and r["to_id"] in targets]
                else:
                    relations = [r for r in request["address_table"] if
                        r["a"] == speaker and r["b"] in targets or r["b"] == speaker and r["a"] in targets]
                if not speaker or not relations or any(r["basis"] == "unknown" for r in relations):
                    reasons.append("Unknown address relationship")
                elif any(any(term.casefold() == "unknown" for term in
                             ([r["self"], r["address"]] if v2 else
                              r["a_to_b" if r["a"] == speaker else "b_to_a"].values())) for r in relations):
                    reasons.append("Unknown directed address terms")
                if any(r["basis"] == "visual_age_estimate" for r in relations):
                    reasons.append("Address terms inferred from visual age estimate")
                item = copy.deepcopy(item)
                if reasons:
                    item.update(needs_review=True, review_reason="; ".join([item["review_reason"], *reasons]).strip("; "))
                row["translations"]["natural"] = item
                progress.advance()

    def worker(job):
        result = execute(job["batch_id"], job["request"])
        return result

    completed, failures = scheduling.run(jobs, worker, provider, maximum=plan["maximum_concurrency"],
        stop_on_error=lambda exc: isinstance(exc, (InferenceUncertain, OSError)), stage="translate")
    missing = [r["id"] for r in rows if "natural" not in r["translations"]]
    if missing:
        failures.append({"target_ids": missing, "error": "Translation incomplete", "systemic": False})
        for _ in missing:
            progress.advance(failed=True)
    document.update(policy=POLICY, failures=failures, translation_resources=frozen,
                    relationship_graph=graph, translation_plan_hash=fingerprint(plan),
                    unresolved_translation_targets=missing)
    for field in ('utterances', 'effects', 'excluded'):
        if field in source_document:
            document[field] = [{**copy.deepcopy(row), **({'translations': by_id[row['id']]['translations']}
                                if row['id'] in by_id else {})} for row in source_document[field]]
    atomic_json(store.artifact_path("translate").parent / "effects-translated.json", {"effects": document["effects"]})
    atomic_json(store.artifact_path("translate").parent / "partial.json", document)
    if any(f.get("systemic") for f in failures):
        raise InferenceUncertain("Translation queue paused; completed batches retained")
    return document
