# Identity and relationship v2

New runs now use:

`analyze -> build_identity -> relationship -> export -> inpaint (if enabled) -> translate -> typeset`

The effective configuration selects `v6.identity_relationship_policy=identity-relationship-v2`.
Historical runs without that marker retain their original `identity` stage and
directory numbering. Do not add the marker to a frozen historical manifest.

## Identity and profile stage

`build_identity` protects named, confirmed semantic identities from reassignment.
It preserves the existing confirmed-name-call criteria and adds visual linking of
an unnamed MAGI member to a named identity when minimum retained crop-vector
distance is strictly below `0.38`.

The visual target is the identity's main MAGI member, selected by the largest
lifetime `appearance_count` in the bank. Matching inputs are frozen for the pass;
secondary and newly linked members cannot propagate links. When several identities
qualify, the minimum distance wins. Semantic cannot-links remain enforced. The
original MAGI metadata, vectors and crop files are read-only.

Profiles use up to ten unique, enabled crops nearest that main member's production
prototype, plus dialogue and descriptions naming the identity. Attributes cover
appearance, apparent age/presentation, personality, formality, brevity, roles and
communication behavior. They do not include habitual word choices or speech tone.
Visual estimates are provisional; social roles need story evidence.

Eligibility counts unique current-chapter detections across identity members,
with default minimum 8. Named identities already in the v2 graph remain eligible.
Later profile updates require new character dialogue or a named description;
changed crops or scene numbering alone do not trigger an update.

## Relationship stage

Only resolved direct speaker/addressee communication creates a new candidate pair.
A name mention alone does not. One pair request returns social relations and two
independent directed address edges. Social categories are family, friendship,
romance, school/work, authority and unknown, with separate concrete relationship
labels and symmetry/direction. A pair can have more than one social relation.

Address fields are Vietnamese; named address terms must belong to the target
identity. Each direction records its own evidence, status and basis. Appearance
address estimates require both profile anchors and remain provisional. Explicit
dialogue may replace a provisional choice. Existing confirmed choices are retained
when proposed replacements conflict and the competing claim is sent to review.

Profiles live beside the person registry in `profiles-v2.json`; relationship
history lives in `relationships-v2.json`. Both have version history and locked,
atomic publication. Old `relationships.json` data is not used as a v2 seed or
fallback. Each run freezes its versions; translation reads the committed stage
artifact and never infers relationships itself.

## Request operation and failures

- One inference request per profile create/update; one per communicating pair.
- At most 8 concurrent requests per stage, bounded by the runtime-wide limit.
- The provider tokenizes complete multimodal messages before inference. Packing
  keeps selected images and whole evidence segments, prefers direct/named evidence,
  and records omitted segments. Mandatory overflow sends no inference POST.
- No worker retry, provider recovery retry or scheduler OOM retry for these tasks.
  OOM lowers admission for future jobs without re-enqueueing the failed job.
- Decisions and raw request receipts are durable. Valid completed decisions are
  reused. Failed and uncertain requests are not resent on resume. Never-sent work
  can continue after service recovery; unresolved POST completion is never assumed.
- Inference failures are committed as reviewable outcomes, separate from fatal
  artifact/provenance failures. They do not block export. Valid prior v2 decisions
  are preferred; otherwise profile anchors yield provisional address or unknown.
- A failed request cannot overwrite a valid historical profile or relation.

The managed vLLM command pins `--limit-mm-per-prompt` to ten images and zero videos
for new effective configs. This setting participates in the engine signature and
qualification fingerprint. The option's JSON form is documented in the
[vLLM 0.31.0 CLI reference](https://docs.vllm.ai/en/v0.31.0/cli/serve/).

## Artifacts and reviews

Each stage writes `result.json`, `review.md`, a frozen plan, packed evidence,
`requests/`, durable `decisions/`, `request-summary.json`, and retained reference
crops. The relationship review includes a directed Mermaid graph and separate
social/address tables. Human reviews display MAGI numbers; person IDs remain
machine keys. Review generation never acts as an approval gate.

RunStore validates retained crop hashes when reopening committed stages. The
Runpod verifier checks profile and graph fingerprints, directed-edge uniqueness,
export snapshot lineage and crop integrity. The importer supports the new stage
boundaries for compatible v2 runs; attempting post-analysis import across the old
identity contract is rejected. Use an upstream boundary (`scan` or `normalize`)
and a new run instead of changing historical dependencies.

## Validation scope

Validation uses synthetic crops, retained vectors, fake vLLM responses and real
RunStore checkpoints. It exercises threshold strictness, closest-target selection,
main-member history, no chaining, confirmed identity protection, ten-crop packets,
profile update triggers, independent directions, target-name validation, no retries,
context packing, fallback continuation, resume and reference-image tampering.

No live model inference or Runpod deployment was performed for this change. Real
profile quality and graph accuracy still require a separately authorized inference
run. In the wider unit suite, three existing scene/anchor expectations also fail
with the v2 configuration disabled: scene reserve growth, the old two-attempt scene
retry limit, and the pre-inference-first anchor immutability expectation.

The agreed requirements and detailed rationale are in
[`identity-relationship-v2-design.md`](../plans/identity-relationship-v2-design.md).
