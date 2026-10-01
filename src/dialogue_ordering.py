"""Stage 3D: evidence-backed permutations within a scene and physical page."""
from __future__ import annotations

from collections import OrderedDict
from reasoning_client import BudgetExceeded, InvalidResponse, ResourceLimit


ORDER_PROMPT = (
    "For each target return ordered_ids, a permutation of exactly its member_ids. "
    "Correct bubble/panel reading order using the actual page layout and reading direction, "
    "supported by dialogue continuity. Fluent narrative alone is not evidence. "
    "Never move text across pages or scenes, never rewrite text. If uncertain retain the given order "
    "and use needs_review or unknown. Cite layout/utterance evidence for a change."
)


def reorder_dialogue(rows: list[dict], runner, block_limit: int | None = None) -> tuple[list[dict], list[str], list[dict]]:
    groups = OrderedDict()
    for index, row in enumerate(rows):
        # Missing page metadata must not authorize a cross-page swap.
        page = row.get("page_id") or row.get("page") or f"unknown:{row['id']}"
        groups.setdefault((row["scene_id"], page), []).append(index)
    targets, members = [], {}
    limit = max(2, block_limit or runner.reasoner.config.max_targets)
    for (scene, page), indexes in groups.items():
        if len(indexes) == 1:
            rows[indexes[0]]["order_status"] = "not_applicable"
        # Owned local blocks remain independent; reconcile their adjacent seams afterwards.
        for offset in range(0, len(indexes), limit):
            selected = indexes[offset:offset + limit]
            if len(selected) < 2:
                continue
            key = f"order:{scene}:{page}:{offset}"
            members[key] = selected
            targets.append({"id": key, "member_ids": [rows[i]["id"] for i in selected]})
    def check(result, target, data):
        if set(result["ordered_ids"]) != set(target["member_ids"]) or len(result["ordered_ids"]) != len(target["member_ids"]):
            raise ValueError("Ordering must be an exact permutation of its same-page/scene members")
        if result["ordered_ids"] != target["member_ids"] and result["status"] != "predicted":
            raise ValueError("Uncertain ordering must retain the source order")
    try:
        predicted = runner.execute("ordering", ORDER_PROMPT, targets, rows, lambda t: members[t["id"]], check)
    except (BudgetExceeded, ResourceLimit, InvalidResponse):
        if limit <= 2:
            raise
        # A single ordering target owns multiple utterances, so split its members,
        # not just the list of requests. No permutation has been applied yet.
        return reorder_dialogue(rows, runner, max(2, limit // 2))
    audit, changed = [], set()
    by_id = {r["id"]: r for r in rows}
    for target in targets:
        value = predicted[target["id"]]
        audit.append({**value, "original_ids": target["member_ids"], "pass": "owned_block"})
        if value["status"] == "predicted":
            for index, identity in zip(members[target["id"]], value["ordered_ids"]):
                if rows[index]["id"] != identity:
                    changed.add(identity)
                rows[index] = by_id[identity]
        for identity in target["member_ids"]:
            by_id[identity]["order_status"] = value["status"]
    # Adjacent blocks can also have a reversed boundary pair; inspect each seam once.
    seams, seam_indexes = [], {}
    for (scene, page), indexes in groups.items():
        for offset in range(limit, len(indexes), limit):
            chosen = indexes[offset - 1:offset + 1]
            key = f"order-seam:{scene}:{page}:{offset}"
            seams.append({"id": key, "member_ids": [rows[i]["id"] for i in chosen]})
            seam_indexes[key] = chosen
    if seams:
        seam_values = runner.execute("ordering", ORDER_PROMPT, seams, rows,
                                     lambda t: seam_indexes[t["id"]], check)
        for target in seams:
            value = seam_values[target["id"]]
            audit.append({**value, "original_ids": target["member_ids"], "pass": "seam"})
            if value["status"] == "predicted":
                for index, identity in zip(seam_indexes[target["id"]], value["ordered_ids"]):
                    if rows[index]["id"] != identity:
                        changed.add(identity)
                    rows[index] = by_id[identity]
            else:
                for identity in target["member_ids"]:
                    by_id[identity]["order_status"] = "needs_review"
    counts = {}
    for index, row in enumerate(rows):
        row["corrected_order"] = row["reading_order"] = index
        row["order_in_scene"] = counts.get(row["scene_id"], 0)
        counts[row["scene_id"]] = row["order_in_scene"] + 1
        row.setdefault("order_status", "unknown")
    return rows, sorted(changed), audit
