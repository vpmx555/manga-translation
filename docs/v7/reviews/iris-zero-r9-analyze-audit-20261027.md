# Iris Zero r9 analyze audit — 2026-10-27

This audit found seven structurally failed analyze events and a separate, visually confirmed MAGI identity swap in accepted decisions. Reanalyze's historical rejection log is dominated by invalid person/cluster pairs, not confidence gating.

## Scope and provenance

Batch: `outputs/runpod-iris-zero-ch1-reanalyze-v7-l40s-20261026-r9`.

The reviewed checkpoint is `local-verified/20261026T130516Z-344ff898`. The graph, analyze and reanalyze result files match their SHA-256 entries in its manifest. No frozen result, manifest, source image or request receipt was modified. No model inference, Runpod connection or infrastructure operation was performed.

The review covers all 260 semantic rows, all 78 event records, saved analyze responses and reanalyze rejections. Three retained event images were manually inspected: pages 8, 13 and 24. This is a structural audit plus targeted visual verification, not a full visual accuracy benchmark for all pages. Stage Markdown reviews were not used as approval gates.

| Artifact | SHA-256 |
|---|---|
| `05_graph/result.json` | `045cf879291fe1e6b68a2f20dc4114699377135c4210d83177bc3a8b2b92bba1` |
| `06_analyze/result.json` | `f89ea92821b50b20c06ae28464faab74a4ff0f3630e857c45f3974d230eb6160` |
| `07_reanalyze/result.json` | `84fb26588dbf43769200ab7c63aa4e841780a94af190183fa70a58937182a31b` |

All shortened utterance IDs below have prefix `b68ed9e490d577e6:`. Page numbers are source-image/page IDs, not printed manga page numbers.

## 1. Event validation discards more rows than the invalid decisions alone

Analyze contains 39 scenes, 78 events, 231 resolved utterances and 29 unresolved utterances. Seven events failed semantic validation. Their saved model responses contain 11 individually invalid rows; the remaining 18 rows in those events also become unresolved because validation is atomic for the whole event. Those 18 rows pass the structural checks used here; that does not establish their semantic correctness.

| Failed event | Page(s) | Utterances lost | Event's reported error |
|---|---|---:|---|
| `scene:1:event:3` | 2 | 7 | Only reference may name a person without visual observations |
| `scene:2:event:1` | 3 | 2 | Person speaker must select a person |
| `scene:7:event:1` | 11 | 3 | Speaker and cluster IDs disagree |
| `scene:12:event:2` | 18 | 6 | Speaker and cluster IDs disagree |
| `scene:13:event:1` | 18–19 | 3 | Speaker and cluster IDs disagree |
| `scene:22:event:1` | 28 | 6 | Speaker and cluster IDs disagree |
| `scene:39:event:1` | 44 | 2 | Speaker and cluster IDs disagree |

Revalidating the saved response rows without invoking a model identifies:

- Seven invalid speaker/cluster pairs: `p11:g1:u`, `p18:g7:u`, `p19:g3:u`, `p28:g4:u`, `p28:g1:u`, `p28:g2:u`, `p44:t1:u`.
- Two person-speaker rows without a selected person: `p3:t0:u`, `p3:t1:u`.
- Two introduction mentions targeting reference-only people: `p2:t1:u`, `p2:t6:u`.

Examples of incompatible pairs include MAGI 15 with a cluster owned by MAGI 5 at `p11:g1:u`, MAGI 13 with a MAGI 5 cluster at `p18:g7:u`, and MAGI 2 with MAGI 5 clusters on page 28. At `p44:t1:u`, a non-null cluster is retained with a null speaker person.

These are contract violations, not network errors or evidence that the entire event's content is wrong. The current change preserves validation and failure provenance. It adds explicit person/cluster consistency guidance to analyze and an explicit cluster-to-person table to reanalyze. It does not silently accept mismatched IDs, auto-retry the model or salvage individual rows from a failed event.

Sources: [analyze result](../../../outputs/runpod-iris-zero-ch1-reanalyze-v7-l40s-20261026-r9/local-verified/20261026T130516Z-344ff898/06_analyze/result.json), [saved responses](../../../outputs/runpod-iris-zero-ch1-reanalyze-v7-l40s-20261026-r9/local-verified/20261026T130516Z-344ff898/06_analyze/requests).

## 2. Accepted decisions swap MAGI 2 and MAGI 5 despite 10/10 confidence

The retained images show the dark-haired male student labelled MAGI 2 and the light-haired female student labelled MAGI 5. Six speaker errors are directly supported by the inspected images:

| Utterance | Text | Analyze chose | Image supports | Speaker confidence |
|---|---|---|---|---:|
| `p8:g1:u` | `...I FOUND IT...` | MAGI 2 | MAGI 5 | 10 |
| `p13:g4:u` | `MY IRIS ALLOWS ME TO SEE "QUALIFIED PEOPLE".` | MAGI 2 | MAGI 5 | 10 |
| `p13:g5:u` | Explanation of failing to find a qualified president | MAGI 2 | MAGI 5 | 10 |
| `p13:g3:u` | `AND THIS "QUALIFIED PERSON" TO DO THAT IS ME, HUH...?` | MAGI 5 | MAGI 2 | 10 |
| `p13:g6:u` | `I SEE...` | MAGI 5 | MAGI 2 | 10 |
| `p13:g2:u` | Apology for the sudden explanation | MAGI 2 | MAGI 5 | 10 |

The explanations often identify the narrative role plausibly while selecting the opposite visual person ID. Increasing confidence cannot resolve that ID/visual mismatch.

Evidence images: [page 8](../../../outputs/runpod-iris-zero-ch1-reanalyze-v7-l40s-20261026-r9/local-verified/20261026T130516Z-344ff898/06_analyze/images/c1d820e8a33e00a147e2f6d55978fb685293502021aafa586d789a151a546b8e.jpg), [page 13](../../../outputs/runpod-iris-zero-ch1-reanalyze-v7-l40s-20261026-r9/local-verified/20261026T130516Z-344ff898/06_analyze/images/ba5276d769435297aa1d24fe17ec721920a75ac00b30dc3aef4a8c479b01edc2.jpg).

### The graph-to-analyze contamination route is visible in saved inputs

The graph's visual-person catalog and the analyze packet's visual-person entries have no names for MAGI 2 and 5. However, `scene:8` supplies a graph hypothesis labelled `Koyuki Sasamori` with confidence 10 and `possible_person_ids=[person:a2f1956f505cb644a8e8]`, which is MAGI 2. Its source includes `p13:g4:u`.

Analyze then uses MAGI 2 for Koyuki's explanation despite the image label MAGI 5. Its merged catalog ends up naming MAGI 2 `sasamori` and MAGI 5 `mizushima`. This demonstrates an available path for a speculative name/visual-ID mapping to become downstream naming evidence. The conclusion that the graph influenced these decisions is an inference from the saved inputs/outputs; the model's internal reasoning is not observable.

The graph hypothesis is in [the saved scene-8 packet](../../../outputs/runpod-iris-zero-ch1-reanalyze-v7-l40s-20261026-r9/local-verified/20261026T130516Z-344ff898/06_analyze/packets/698ecdf57f7c515ed2c1fe82f77f4f4381054e48b45c91b1a04e4212e1c4c793.json). New graph output and analyze packets no longer generate or forward these character hypotheses.

## 3. A direct-address mention points back to its own speaker

At `p24:g4:u` (`MIZU- SHIMA- SA...`), the selected speaker is MAGI 5 and the listener is MAGI 2. The `direct_address` mention nevertheless targets MAGI 5, the speaker. Its explanation says Koyuki is calling Toru's surname. The page shows the female student MAGI 5 addressing the male student MAGI 2. The mention target should therefore be MAGI 2 in this inspected case.

The row's speaker explanation also says `Koyuki (MAGI 2)` even though its selected speaker ID maps to MAGI 5. This is an additional mismatch between explanatory prose and selected references, separate from whether the speaker selection itself is correct.

Evidence: [page 24](../../../outputs/runpod-iris-zero-ch1-reanalyze-v7-l40s-20261026-r9/local-verified/20261026T130516Z-344ff898/06_analyze/images/5b5cfd19659676e868b48377e39b7ff6198b76284d1af7bc42a31b9c0432014e.jpg).

## 4. Confidence and anchor conflicts need separate interpretation

There are 92 rows with a single anchor person. Of 33 rows whose selected speaker differs from that anchor, six are unresolved event-failure rows. The remaining **27 are resolved decisions**: 22 score speaker confidence 10, four score 9 and one scores 8. Overall, 135 rows score speaker confidence 10.

The following is a review queue, not a claim that all 27 anchors are correct. Six speaker errors are visually confirmed above; the remaining conflicts need their own image/source checks. Analyze now receives the requested anchor weight 8/10 and must explain clear visual/source evidence when overriding it. Reanalyze has no special anchor-priority rule.

| Utterance | Selected MAGI | Anchor MAGI | Speaker confidence |
|---|---|---|---:|
| `p8:g1:u` | 2 | 5 | 10 |
| `p10:g3:u` | 2 | 5 | 10 |
| `p10:g1:u` | 2 | 5 | 10 |
| `p13:g4:u` | 2 | 5 | 10 |
| `p13:g5:u` | 2 | 5 | 10 |
| `p13:g3:u` | 5 | 2 | 10 |
| `p13:g6:u` | 5 | 2 | 10 |
| `p13:g2:u` | 2 | 5 | 10 |
| `p14:g2:u` | 2 | 5 | 10 |
| `p16:g2:u` | 5 | 2 | 10 |
| `p16:g3:u` | 2 | 5 | 10 |
| `p17:g7:u` | 12 | 2 | 10 |
| `p17:g8:u` | 15 | 2 | 9 |
| `p17:g5:u` | 15 | 2 | 9 |
| `p17:g4:u` | 15 | 2 | 10 |
| `p25:g5:u` | Unassigned visual person | 2 | 8 |
| `p27:g2:u` | 2 | 5 | 10 |
| `p27:g4:u` | 2 | 5 | 10 |
| `p27:g3:u` | 15 | 5 | 9 |
| `p29:g1:u` | 2 | 5 | 10 |
| `p29:g2:u` | 15 | 2 | 10 |
| `p29:g3:u` | 15 | 2 | 10 |
| `p39:g2:u` | 2 | 15 | 10 |
| `p42:g1:u` | 15 | 2 | 10 |
| `p42:g6:u` | 15 | 2 | 10 |
| `p42:g3:u` | 2 | 5 | 10 |
| `p43:g1:u` | 2 | 15 | 9 |

## 5. Historical reanalyze rejects mostly inconsistent references

The saved r9 reanalyze has six accepted changes and 23 rejected proposals:

| Rejection reason | Count |
|---|---:|
| Speaker and cluster IDs disagree | 17 |
| Mention IDs must match this utterance | 1 |
| Event prose depends on a correction not applied to this event | 5 |
| New confidence lower than old | 0 |

Removing the old confidence gate alone would not have admitted these 23 proposals. The dominant issue is changing a person without providing a compatible cluster reference. The five dependent prose updates are blocked because required corrections were not accepted. New input includes the explicit cluster/person mapping; scope and consistency checks remain enabled.

## 6. Tail statements in this historical run are not structured detector output

The inspected r9 images show panel/MAGI character labels but no explicit tail-bbox annotation layer. Its semantic rows also lack the new deterministic `bubble_tail` field. Free-form statements about a bubble being attached to a character cannot be treated as structured tail detections from the LLM.

New runs take `tails` and `text_tail_associations` from MAGIv2, draw their bboxes before analyze, and supply per-target presence and links. The model does not detect or invent tails. An absent source link is supplied as `has_tail=false`; the model must not infer a missing tail from the image. The original r9 records remain unchanged.

## 7. Offline measurement of the new two-half input

The new input builder was exercised on these saved dictionaries without a provider or model. Events stay intact; the nearest midpoint gives:

| Request | Owned utterances | Owned events | Serialized wire characters | Reduction per request vs old wire |
|---|---:|---:|---:|---:|
| Historical whole chapter | 260 | 78 | 100,808 | — |
| New `half:1` | 129 | 34 | 57,524 | 42.9% |
| New `half:2` | 131 | 44 | 62,965 | 37.5% |

The first half runs from `p1:t0:u` to `p23:g3:u`; the second runs from `p23:g5:u` to `p44:t1:u`. No utterance/event is owned twice or omitted. The second half does not consume the first half's corrections.

Sizes count compact JSON Unicode characters using the same serialization for all requests; they exclude prompt/schema and are not token counts. Combined wire size is 120,489 characters, **19.5% larger in total** because both requests repeat context/catalog information. The improvement is smaller individual requests and independent repair, not a demonstrated reduction in total inference cost.

The new builder removes full other-half semantic rows, pixel bboxes, observation histories and repeated name-candidate metadata. It retains all owned OCR/decision evidence, other-half scene summaries, adjacent event text and the reference catalogs needed to propose consistent IDs.

## 8. Implemented changes and remaining limits

- Analyze prioritizes the source anchor at weight 8/10, with explicit evidence required for an override. Reanalyze has no special anchor-priority rule.
- Reanalyze uses two independent, scoped halves and merges accepted patches only. One failed half retains analyze for that half while preserving the other's accepted corrections; two failed halves fall back to analyze unchanged.
- New graph/analyze/reanalyze generated prose and automatic review labels use English. Source OCR/names and frozen historical output retain their original form.
- MAGIv2 tail metadata stays immutable; no tail detection is requested from the model.
- Model-free tests cover independent concurrent requests, lower-confidence corrections, ownership rejection, partial failure, receipt reuse, tail handling and downstream export.

These changes have not been tested for semantic quality with a new real-model run. They do not repair this frozen r9 checkpoint, automatically replay later scene memories, salvage structurally valid rows inside a failed analyze event, or establish a chapter-wide speaker accuracy score.
