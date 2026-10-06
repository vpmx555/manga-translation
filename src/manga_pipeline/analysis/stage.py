"""Batch commits cover text, name decisions, bank receipts and participant memory."""
from ..bank.names import NameBank
from ..names.stages import snapshot_bank
from ..progress import NullProgress
from ..storage.io import atomic_json, fingerprint, read_json
from .binding import bind_batch
from .context import bank_view, memory, request_for
from .contract import POLICY, speakers
from .text import classify_batch


def analyze(store, raw, document, scanned, provider, *, progress=None):
    if document.get("analysis_policy") != "essential-v3":
        raise ValueError("analyze requires an essential-v3 run; reanalyze into a new run to upgrade")
    speaker_policy = store.manifest["config"].get("speaker_policy")
    speakers(speaker_policy)
    progress = progress if progress is not None else NullProgress()
    rows, previous, utterances, links, batches = document["utterances"], {}, [], [], []
    mentions = {}
    for candidate in scanned["candidates"]:
        mentions.setdefault(candidate["utterance_id"], []).append(candidate)
    snapshot_bank(store, "analyze")
    progress.reset(total=len(rows), unit="utterance")
    for start in range(0, len(rows), 3):
        path = store.artifact_path("analyze").parent / "plans" / f"batch-{start:06d}.json"
        if path.exists():
            plan = read_json(path)
            if plan["hash"] != fingerprint([plan["request"], plan["bank"]]) or plan["request"]["policy"] != POLICY:
                raise ValueError("Analyze batch plan changed/corrupt")
            if plan["request"].get("speaker_policy") != speaker_policy:
                raise ValueError("Analyze batch speaker policy changed")
        else:
            bank = bank_view(NameBank(store.manifest["bank_path"]).read())
            request = request_for(rows, start, mentions, previous, bank, speaker_policy)
            plan = {"request": request, "bank": bank, "hash": fingerprint([request, bank])}
            atomic_json(path, plan)
        request, key = plan["request"], f"batch:{start}"
        committed = store.target("analyze", key, {"plan_hash": plan["hash"]})
        reused = committed is not None
        if committed is None:
            text = classify_batch(store, request, mentions, provider, start, progress)
            text, outcomes = bind_batch(store, raw, rows, text, plan["bank"], provider, start, progress)
            committed = {"utterances": text, "links": outcomes, "memory_after": memory({
                **previous, **{row["utterance_id"]: row for row in text}})}
            progress.set_phase(f"batch {start // 3 + 1}: checkpoint")
            store.commit_target("analyze", key, {"plan_hash": plan["hash"]}, result=committed)
        utterances.extend(committed["utterances"])
        links.extend(committed["links"])
        for row in committed["utterances"]:
            previous[row["utterance_id"]] = row
            progress.advance(reused=reused)
        batches.append({"start": start, "targets": len(committed["utterances"]),
                        "memory_after": committed["memory_after"]})
    warnings = [{"utterance_id": row["utterance_id"], **warning} for row in utterances for warning in row["warnings"]]
    return {"policy": POLICY, **({"speaker_policy": speaker_policy} if speaker_policy is not None else {}),
            "utterances": utterances, "links": links,
            "warnings": warnings, "batches": batches, "failures": []}
