# Natural-only translation and reusable relationship graph

New default runs use `vi-natural-v2` / `natural-analyze-batches-v1`. Only `natural`
is generated, reviewed and rendered. Existing output directories are not migrated
or deleted. Create a new run/frozen batch for the new configuration; do not edit a
historical manifest to make it pass a new policy check.

## Pipeline

After analyze, identity and export, translation prepares the chapter relationship
snapshot, then dispatches all translation jobs against that frozen address table.
No translated batch feeds another. The existing independent analyze batches supply
the initial target memberships and scene context. Targets not present in those
batches are grouped in source reading order, eight per additional batch, without
requiring a common scene. Effects still receive data-only translations; their image
rendering policy is unchanged. Excluded export targets are not reintroduced.

Requests split deterministically when they exceed their measured context budget.
Shape/truncation recovery is bounded; targets are never silently dropped. Confirmed
glossary, manual natural overrides and the address table survive splitting. Hard
context splits may reduce surrounding source context, never target text. Successful
children are retained even if a sibling fails. No fabricated fallback translation
is inserted for failed targets.

## Relationship graph

The story-level file is `relationships.json` beside the semantic person registry
under `_persons_v6/<story>/`. Source MAGI bank files are not changed.

- Eligibility defaults to **at least eight** unique current-chapter detections per
  final semantic identity. Deduplicate page/bounding-box occurrences, sum across
  member MAGI IDs; never use cumulative bank appearances or retained crop count.
- Existing graph identities remain usable below that threshold in later chapters.
- Each eligible identity exposes one node per member MAGI ID. Same-identity nodes
  have `relationship=same_identity`, `address=none`. External relations are stored
  once per identity pair and projected identically to all member MAGI nodes.
- Only pairs with resolved speaker-to-addressee or confirmed name-reference
  evidence become inference jobs. Include relevant utterances and two neighboring
  source rows on either side as context; co-occurrence alone creates no relation.
- Select up to **three active paired crop embeddings per MAGI ID**, from the entire
  bank, sorted by L2 distance to that MAGI prototype. Fewer valid images are allowed;
  missing pairs/files are reported. No embeddings are recomputed by a model.
- Each pair has its own multimodal request. Output contains social relationship,
  each party's role, directed self-reference/address terms in both directions,
  basis, evidence references and reason. Image-only age estimates may supply
  address terms with `basis=visual_age_estimate`; they do not establish family or
  social roles. If both image and dialogue are insufficient, preserve `unknown`.
- One address choice per direction is used across the chapter. Scene-specific
  relationship/address evolution is not implemented.
- Persistence is append-only under a story lock. Existing relations, including
  semantic `unknown`, are never revised automatically. Inference execution failures
  are recorded as failures, not committed as permanent unknown relationships.

Snapshots retain selected image bytes, source paths/SHA256/distances and request
data. Selected resized crops are also published under `translate/relationship-crops`.
`relationships.json` beside the translation artifact contains the graph view,
address table, failures and reference crop inventory. Once created, the chapter
result is reused unchanged even if later chapters extend the story graph.

## Concurrency and recovery

Defaults in all four pipeline configurations:

| Setting | Default |
|---|---:|
| analyze dispatch ceiling | 8 |
| `v6.relationship_concurrency` | 8 |
| `v6.translation_concurrency` | 64 |
| `v6.relationship_min_appearances` | 8 |
| `v6.relationship_crops_per_magi` | 3 |
| runtime shared LLM slots / vLLM max sequences | 64 |

Relationship jobs finish before translation admission begins. Admission starts
small and uses real service metrics to increase or reduce active work; a ceiling
of 32 does not promise 32 simultaneous jobs or improved throughput on every GPU.
OOM reduces admission and permits one bounded queue retry. Exhausted individual
jobs do not discard independent successful jobs. An uncertain POST pauses new
admission; request receipts prevent duplicate generation on resume. Started or
uncertain receipts require explicit reconciliation using existing recovery rules.
The existing real-GPU concurrency qualification remains required and now accepts
the configured ceiling up to 32; this change does not bypass it.

Review includes graph nodes/edges, both address directions, evidence, selected
crop images/distances, visual-age estimates, unknowns, failures and target-level
translation review flags. `partial.json` retains completed translations when a
queue is paused. Rendering produces one natural page per source page.

## Offline verification and limitations

`tests/test_v6_relationship_translation.py` covers threshold/deduplication,
same-identity sums, nearest crop ranking, age-based address decisions, immutable
cross-chapter reuse, edge projection, no co-occurrence-only edges, retained images,
inference failures not becoming unknown edges, analyze-aligned/additional batches,
parallel frozen inputs, cached resume, hard-token splits, successful siblings,
uncertain POST protection and OOM isolation.

The mocked full v6 pipeline test exercises natural-only rendering and resume.
Runtime/config/budget checks cover the new ceiling. No model inference or Runpod
execution was performed for this implementation. Visual relationship accuracy and
New batches default to a ceiling of 64 text requests; admission still adapts to
measured GPU/KV pressure. Analyze and relationship inference retain their cap of 8.
Qualification covers 8 long contexts and the configured short-context ceiling
separately, with real usage and simultaneous-running evidence. Each phase
emits 512 items and still requires at least 1024 measured output
tokens per request. A configured ceiling does not prove 64 long contexts fit in
VRAM. Frozen batches keep their original limits and qualification contracts.

Validation on 2026-10-22: **92 related tests passed, 2 existing skips** across the
relationship/translation, mocked v6 pipeline, independent batches, name patch,
scene/name, semantic contract, runtime resources, vLLM inference policy and Runpod
runtime unit suites. All four default configs load successfully; syntax checks
and `git diff --check` pass. This is selected-suite validation, not a claim that
the entire repository test suite was executed. The independent analyzer verifier
also reconstructs validated name patches before checking frozen batch source text.

Append-only reuse deliberately preserves mistaken/unknown relationships until a
future graph-update mechanism is designed. Visual-age address choices are estimates
and remain flagged for review; they are not asserted as exact demographic facts.
