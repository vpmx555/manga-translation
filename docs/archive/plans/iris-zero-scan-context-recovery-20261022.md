# Recover the scene/name scan after the Iris Zero context failure

Status: compact whole-chapter wire contract, ID validation/replay, budget diagnostics,
frozen-config verifier and import-through-normalize are implemented. Syntax and diff
checks pass; regression tests are written but not executed under the current validation
policy. Conditional window/reconciliation fallback below remains a separate extension;
oversized compact calls fail before POST and retain diagnostic receipts.
No new Runpod access, deployment, model pilot or test execution was performed.
The user reports the previous pod has been terminated.

## Observed failure

Verified backup: `outputs/runpod-iris-zero-ch1-glm-natural-l40s-20261022-r6`.
Run: `20261022T132926Z-e9e1e526`.
Layout, extract (GLM OCR), bubble geometry and normalize completed. Scan failed:
`Protected request exceeds context: 58633 > 27852`.
This is the combined scene/name/normalization request, before independent analyze.

The normalized artifact has 260 utterances, 243 essential utterances and 9,502
characters of OCR text. Current scene.py sends essential text as `targets`, then
the same text again inside `name_targets`. Full source IDs are repeated in enums
for scene starts, names, patches, group members and suspicions.

Static JSON size comparison, not tokenizer measurements:

| Component | Current | Compact proposal |
|---|---:|---:|
| Text payload, JSON characters | 40,574 | 19,113 |
| One full ID schema, JSON characters | 7,298 | 39 |

The payload calculation excludes registry, prompt and output schema; it does not
prove that the complete request fits. Actual tokenizer accounting remains required.
64 concurrent requests is a scheduling ceiling and does not increase context per
request. Keep the current 32,768 model context and protected 27,852 token budget.

## First implementation: retain one combined chapter call

1. Add a deterministic, versioned wire mapping from source IDs to `u0`, `u1`, etc.
   Keep original IDs in stored artifacts and reviews. Map registry IDs separately
   to `p0`, `p1`, etc. Persist mapping, source hash and contract version in the
   request snapshot; never derive mappings from model output.
2. Send each original text exactly once as a row with a scene-eligible flag.
   All rows participate in name scanning; only essential rows can start scenes.
   Do not omit targets, truncate text, or change original Unicode offsets.
3. Replace repeated full-ID enums with compact ID schemas. Validate every returned
   ID against the frozen mapping in code, including scene eligibility, name spans,
   patch locations, group members and evidence refs. Reject unknown IDs and refs.
4. Decode refs back to full source/registry IDs before running the existing scene,
   name and normalization validators. Retain both raw wire response and decoded
   artifacts so provenance remains verifiable. Corrections remain source-addressed
   name patches; no rewritten sentences/documents. Analyze permissions stay unchanged.
5. Budget the complete final prompt, payload, response schema, output reserve and
   existing safety allowance with the real tokenizer before submitting. Record a
   component breakdown. If output reserve grows on retry, run admission/budgeting
   again. BudgetExceeded must not resend the same oversized request.

Primary files: `v6/scene.py`, `v6/name_normalization.py`, request snapshots and
`v6/verification.py`. Give the wire contract a new version so old cached responses
cannot be mistaken for compact responses. Preserve the public scene.plan result
shape used by semantic.scan and independent analyze.

## Fallback only when the complete compact request still does not fit

Split by actual token budget at page/panel boundaries, not a fixed sentence count.
Assign every utterance to exactly one owning window. Optional overlapping boundary
context is read-only: no second ownership, edits or duplicate names from overlap.
Each window still combines scene, names and patch proposals in one call.

Persist independent window receipts/results; resume successful windows rather than
replaying them. Split an oversized/truncated window into smaller children with
complete coverage. Never silently lose a target or accept partial generated JSON.

Reconcile only scene boundaries across adjacent windows with bounded summaries
and boundary dialogue. Local scene IDs are not blindly concatenated and a window
boundary is not automatically a scene boundary. For aliases requiring evidence
across windows, use a compact chapter-wide mention/candidate table plus bounded
evidence snippets in a separate reconciliation call; do not resend the full chapter.
Keep uncertain aliases flagged for review and do not merge person/MAGI identities.
This fallback adds calls; it is unnecessary if the compact whole-chapter call fits.

## Recovery from existing artifacts

- Restore only SHA/CRC/inventory-verified archives from r6 into a new frozen batch.
- Extend the pre-analysis importer to support an explicit completed-stage boundary
  through `normalize`. Its current fixed stage list includes `scan`, which failed;
  do not mark that failed stage complete or import its cache as a successful result.
- Reuse layout, OCR/extract, bubble geometry and normalize. Preserve source and
  artifact hashes, bank crop/embedding assets and source snapshots. Re-run scan,
  then analyze, identity, export, inpaint, translate and typeset normally.
- Fix `deployment/runpod/v6_contract.py` before retry: it still requires Hayai and
  obsolete character thresholds. Verify against the applicable frozen config and
  supported policy, rather than historical hardcoded defaults. Do not weaken
  artifact integrity or accept incompatible imported stage inputs.
- Pod provisioning is a separate explicit Runpod request. A future launch must use
  L40S Secure, a fresh batch and current stock/pricing/CUDA-floor checks. The previous
  pod's qualification cannot be reused on a different pod; qualify that pod again.

## Acceptance checks for the implementation task

Use model-free tests for compact mapping round trips, unknown refs, sparse scene
eligibility, Unicode offsets, raw OCR preservation, exact target coverage, cache
versioning and verifier replay. Include the retained Iris Zero source as a budget
regression without copying its full content into prompts/log summaries.

For the window fallback, cover boundary continuity, overlapping context ownership,
cross-window aliases, truncation splits and interrupted-resume behavior. Confirm
that the importer rejects failed scan artifacts and that changed upstream settings
invalidate reuse. Test execution and a new GPU run should follow the authorized
scope of that task; this document records a design, not a passing implementation.
