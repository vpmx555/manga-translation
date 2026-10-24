"""Frozen independent batches; soft cuts wait for an anchor, hard cuts never drop text."""
import math
import time

from ..runtime.llm_budget import BudgetExceeded
from ..runtime.resources import event
from . import persons
from .batch_contract import schema as batch_schema, PROMPT
from .policy import POLICY


def output_reserve(targets, mentions):
    return min(8192, max(512, 256 * math.ceil(1.25 * (128 + 384*targets + 192*mentions) / 256)))


def pool(rows, state, mentions):
    pages = {r["page_id"] for r in rows}
    clusters = {ref: [o for o in items if o["page_id"] in pages]
                for ref, items in state["observations"].items()
                if ref.startswith(state["extraction_scope"] + ":")
                and any(o["page_id"] in pages for o in items)}
    wanted = {persons.canonical(state, state["cluster_to_person"][ref]) for ref in clusters}
    for mention in mentions.values():
        form = persons.name_form(mention["name"])
        wanted.update(persons.canonical(state, p) for k in (form["surface"], form["base_name"])
                      for p in state["name_index"].get(k, []))
        # Alias proposals can make an existing registry person relevant to the
        # batch, but never constrain the owner of a particular mention.
        for group in mention.get("alias_groups", []):
            if group["status"] != "proposed":
                continue
            for surface in [group["canonical_name"], *group["aliases"]]:
                alias = persons.name_form(surface)
                wanted.update(persons.canonical(state, p) for k in (alias["surface"], alias["base_name"])
                              for p in state["name_index"].get(k, []))
    return clusters, sorted(wanted)


def prepare(rows, scene, state, candidates, reason):
    ids = {r["id"] for r in rows}
    mentions = {k: m for k, m in candidates.items() if m["utterance_id"] in ids}
    clusters, known = pool(rows, state, mentions)
    request = {"policy": POLICY, "scene_id": scene["scene_id"], "scene_summary": scene["summary"],
        "speaker_merge_policy": "inference-first-v1", "source_anchors_enforced": False,
        "analysis_branch": rows[0]["analysis_branch"],
        "targets": [{k: r[k] for k in ("id", "text", "essential_index", "speaker_anchor", "original_speaker")}
                    for r in rows], "mentions": [
                        {k: v for k, v in m.items() if k not in {'raw_surface', 'original_start', 'original_end'}}
                        if m.get('source') == 'llm_scan_v3' else m for m in mentions.values()],
        "cluster_pool": {ref: {"person_id": persons.canonical(state, state["cluster_to_person"][ref]),
                               "observations": items} for ref, items in clusters.items()},
        "known_names": {p: {"name": state["persons"][p]["primary_name"],
                            "aliases": [f["surface"] for f in state["persons"][p]["name_forms"]]} for p in known},
        "unknown_reference": "others"}
    request["caption_max_distance"] = scene.get("caption_max_distance", 7)
    schema = batch_schema(rows, known, clusters, mentions)
    return {"rows": rows, "request": request, "schema": schema, "mentions": mentions,
            "known": known, "clusters": clusters, "anchor_exception": reason,
            "reserve": output_reserve(len(rows), len(mentions))}


def plan(rows, scenes, state, candidates, calls, soft=8):
    started = time.monotonic()
    jobs = []
    for scene in scenes:
        for branch in ("caption", "dialogue"):
            branch_rows = [r for r in rows if r["scene_id"] == scene["scene_id"] and r["analysis_branch"] == branch]
            start = 0
            while start < len(branch_rows):
                end = min(len(branch_rows), start+soft)
                if branch == "dialogue":
                    while end < len(branch_rows) and not branch_rows[end]["speaker_anchor"]["locked"]:
                        end += 1
                reason = "scene_start" if start == 0 and not branch_rows[start]["speaker_anchor"]["locked"] and branch == "dialogue" else None
                if start and branch == "dialogue" and not branch_rows[start]["speaker_anchor"]["locked"]:
                    reason = "hard_token_limit"
                selected = None
                soft_end, overflow = end, None
                # Test the soft partition, then prefer the largest fitting anchored cut.
                cuts = [end] + sorted([i for i in range(start+1, end) if branch_rows[i]["speaker_anchor"]["locked"]], reverse=True)
                cuts += [i for i in range(end-1, start, -1) if i not in cuts]
                for cut in cuts:
                    job = prepare(branch_rows[start:cut], scene, state, candidates, reason)
                    budget = calls.budget("analyze_dialogue_v6", PROMPT, job["request"], job["schema"], job["reserve"])
                    if budget["estimate"] <= budget["limit"]:
                        selected = job
                        end = cut
                        break
                    if cut == soft_end:
                        overflow = budget
                if selected is None:
                    raise BudgetExceeded("Single target exceeds protected context: " + branch_rows[start]["id"])
                selected["batch_id"] = f"batch:{len(jobs)+1}"
                selected["budget"] = budget
                selected["hard_split"] = end != soft_end
                selected["overflow_budget"] = overflow
                jobs.append(selected)
                start = end
    event("analyze_planning", seconds=time.monotonic()-started, batches=len(jobs), targets=len(rows))
    return jobs
