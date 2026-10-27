# 7. Reanalyze — two independent review halves

**Input:** the frozen analyze snapshot, current OCR, semantic decisions, evidence and reference catalogs. **No images.** Up to two requests, each with a 64K context and thinking disabled.

## Ownership and independence

Events remain intact. Code selects the event boundary nearest the utterance midpoint and assigns the first and second contiguous halves to `half:1` and `half:2`. Every utterance and event belongs to exactly one batch. A chapter with only one event uses one batch; an empty chapter needs no request.

Both batches read the same original analyze snapshot. Neither receives the other's corrections. Requests run concurrently, capped at two and by `runtime.llm_slots`. Responses may change only their own `scope.target_ids` and `scope.event_ids`; out-of-scope proposals are rejected even if the IDs exist elsewhere in the chapter.

Code applies each response independently against the original snapshot, then merges only accepted patches in deterministic batch order. It never merges whole worker snapshots, which could overwrite the other half's changes. Event boundaries and historical event memories remain unchanged.

## Smaller input

Each batch receives:

- Full current OCR, semantic decisions, confidence, mention decisions and decision evidence for its own half.
- Its owned event descriptions and the relevant scene descriptions.
- Read-only scene summaries for the other half and OCR from the immediately adjacent event at the boundary.
- A compact person catalog, an explicit cluster-to-person map, owned name occurrences and a shared name-group table.
- Compact anchor assignments/conflicts and MAGIv2 tail presence/index metadata.

Images, pixel bboxes, detection observation histories, name-group member histories, OCR replacement histories and duplicate candidate metadata are omitted from the model packet. Original artifacts retain them. The reviewer does not need pixel coordinates to review text consistency.

Transport uses `chapter-review-tables-v2`: short references, declared columns, enum codebooks and interned repeated evidence. Owned text and semantic evidence remain complete. The other half is intentionally represented by context summaries rather than a second full semantic table.

## Decisions and language

All generated titles, descriptions, semantic evidence and correction reasons must be **English**. Quoted source OCR, names and aliases retain their source form. Existing frozen historical artifacts are not translated or rewritten.

The special **anchor weight 8/10 belongs to analyze**, where images and source anchors guide speaker attribution. Reanalyze has no special anchor-priority rule. It reviews consistency from the supplied evidence and may correct a decision even when the new confidence is lower than the old confidence.

All three stages share the evidence/confidence instruction in `v6/policy.py`. Source-grounded inference is permitted. Ambiguous or unsupported evidence must not receive inflated confidence. Earlier model hypotheses, summaries, memories, name assignments and decisions are not independent source observations: reuse must trace back to the supplied sources, and uncertainty in unverified premises must carry forward rather than disappear through repeated reasoning.

MAGIv2 tail metadata is immutable source data. The model does not detect tails. `has_tail=false` means no tail is available for that target; neither stage may invent one or borrow another bubble's tail. Reanalyze cannot change the saved links or bboxes.

Speaker/cluster and mention references must remain consistent. Only visual people may be speakers/listeners; reference-only people may be targets of reference mentions. Every owned mention ID must be retained in a replacement row.

## Applying responses

A response contains `corrections` and `event_updates`. Each correction provides a complete replacement semantic row and a reason. Each event update provides its ID, title, description, confidence, reason and `requires` list of corrections on which it depends.

Code validates scope, IDs, reference consistency, mention coverage, structural fields and finite confidence in 0–10. It does not compare old and new confidence to block a change. Event prose is applied only when its required owned corrections were accepted. No OCR edits, bank edits, scene/event boundary changes or automatic replay of later events are allowed.

## Failures, resume and downstream use

- If one batch fails, retain analyze rows for that half and keep the other half's accepted corrections. Mark `completed_with_review` and record `batch_failures`.
- If both batches fail, use analyze unchanged: `status=failed`, `fallback_to_analyze=true`.
- If a response contains invalid proposals, retain rejected proposals with reasons and keep valid, independent corrections.
- Request receipts are keyed independently by batch and payload. Resume reuses saved responses/errors and does not duplicate a completed or uncertain POST. No automatic semantic/JSON retry is added.

Recoverable review failures use `batch_failures`, not the pipeline's fatal `failures` list, so downstream stages can consume the documented effective snapshot. `RunStore.analysis()` returns that snapshot; `read('analyze')` preserves the original.

## Artifacts and configuration

The stage root contains `plan.json`, a `packet.json` index, `before.json`, `changes.json`, `after.json`, `result.json`, request receipts and automatic English Markdown/HTML reviews.

Each `batches/half-N/` directory contains `packet.json`, `wire-packet.json`, `reference-ledger.json` and `result.json`. Results retain batch scope/status and accepted/rejected patches with before/after values.

`v6.reanalyze_output_tokens=8192` reserves output for **each** request. Each uses `llm.num_ctx=65536`, the normal request timeout and thinking disabled. Context budget checks run before each POST.

Code: [reanalysis.py](../../../src/manga_pipeline/v6/reanalysis.py), [reanalysis_input.py](../../../src/manga_pipeline/v6/reanalysis_input.py), [reanalyze_wire.py](../../../src/manga_pipeline/v6/reanalyze_wire.py).

Historical examples linked elsewhere retain the policies and language of their frozen runs; they are not evidence of this new two-batch behavior. See [the r9 audit](../reviews/iris-zero-r9-analyze-audit-20261027.md) for the local artifact review and offline input-size comparison.
