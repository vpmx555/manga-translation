# Identity and relationship v2 — implemented design

Status: implemented after the user's explicit request; model-free validation only.
Scope: replace the current identity stage with `build_identity` and `relationship`,
and move relationship preparation out of translate. No deployment is authorized
by this document.

## Pipeline and stage boundaries

`analyze -> build_identity -> relationship -> export -> inpaint -> translate -> typeset`

Existing upstream stages remain prerequisites. Both new stages write their own
committed artifacts and Markdown reviews. Reviews are generated for inspection,
not automatic approval gates. Translate consumes the frozen profile/relationship
version for its run, rather than consulting mutable story state during requests.

`build_identity` performs deterministic identity linking and then profile inference.
`relationship` consumes the completed identity/profile snapshot and produces social
relations and independent directed address edges. MAGI bank records are not merged
or renamed by these semantic stages.

## Confirmed identity versus provisional person IDs

An automatically allocated provisional `person_id` is not a confirmed identity.
For these rules, an existing identity means a character with a confirmed name
association in the semantic registry. Normalized aliases must belong to a confirmed
registry name group; matching a surname string alone does not establish identity.

An already confirmed identity cannot automatically move or merge into another
confirmed identity. Contradictory confirmed identities require review. Existing
confirmed identity mistakes are not automatically split in this scope.

## Identity linking

Two supported paths link an unconfirmed MAGI identity to an existing named identity:

1. Preserve the existing confirmed-name-call rule: at least two distinct confirmed
   calling occurrences, or one confirmed occurrence with visual support, with no
   contradictory target evidence. Use the normalized registry alias group.
2. Visual matching: the minimum Euclidean distance between retained, enabled,
   L2-normalized crop embeddings is strictly less than `0.38`. Exactly one side
   must have a confirmed named identity.

For the visual path, compare an unconfirmed MAGI ID only with the main MAGI member
of a confirmed identity. The main member is the member with the highest appearance
count across the entire bank history. The comparison uses the individual retained
crop vectors of that main member, not a distance to its mean vector and not the
union of all secondary members' vectors.

If several target identities qualify visually, choose the one with the smallest
minimum crop distance. Do not add a margin criterion. Equidistant results must be
reported explicitly and resolved deterministically without an extra LLM call.

Freeze main members and matching inputs before the linking pass. Newly linked
members and secondary members cannot become bridges for further visual links.
Retain the source/target MAGI IDs, crop references, distances, name-call events,
candidate list and decision in the review artifacts.

The diagnostic observation that two bank IDs occur in the same panel is not by
itself a new automatic merge veto: duplicated detections can create that condition.
Do not introduce an additional visual threshold without a user decision.

## Eligibility and character profiles

For current-chapter eligibility, count unique source detections across all members
of the canonical identity. Do not sum repeated copies of the same detection.
The default minimum is 8 detections, configurable. Preserve identities already
present in the prior v2 graph even if their current-chapter count is below 8.
Profiles and graph participation require confirmed named identities.

Profile fields:

- Confirmed primary name, aliases and MAGI members.
- Shared appearance characteristics, apparent age group and presentation.
- Personality hypotheses with evidence.
- Speech formality and brevity.
- Confirmed social/occupational roles.
- Observed communication behavior toward other identities.

Exclude habitual word choices and speech-tone fields requested to be removed.
Do not select person-specific address edges in the profile stage.

Select up to 10 crop/vector pairs from the identity's main MAGI member, ranked by
distance to that member's production consensus prototype. Exclude disabled items
and exact duplicate crops; use all available valid crops if fewer than 10 exist.
Do not pad the set with secondary members or fabricated samples.

Infer shared appearance across these crops. This is the default anchor for address
selection, rather than a factual proof of age, gender, family or social status.
Explicit dialogue establishing age, role or address can override an appearance-
based address choice while leaving the recorded apparent appearance intact.

Other profile evidence includes the character's dialogue and descriptions that
mention a name confirmed to refer to that identity. Preserve evidence references
and distinguish confirmed statements from inference.

Create an initial profile on eligibility. Later profile updates are triggered by
new dialogue from that character or new named descriptions linked to that identity.
A changed crop set alone does not trigger a profile update. Keep versioned history.

## Relationship candidates and output

Only direct communication establishes a new candidate pair. A name mention or
co-occurrence alone is insufficient. Resolve speakers/addressees through the frozen
identity mapping, then collapse member MAGI IDs to their identity.

One request evaluates one communicating identity pair. It returns:

- Zero or more distinct social relations, using standardized categories covering
  family, friendship, romance, school/work, authority and unknown. Record specific
  roles, direction or symmetry, and evidence separately for each relation.
- An A-to-B address decision with self-reference, addressee term, basis, evidence
  and confirmed/provisional/unknown state.
- An independent B-to-A decision with the same fields.

The two address decisions are independent directed edges even though they are
inferred in one request. Never fill one direction by copying the other. Names used
as addressee terms must refer to the target identity, not the speaker. Validate
Vietnamese address fields and retained evidence references.

Retain graph nodes without edges. Same-identity MAGI members are represented inside
one node; do not duplicate relationship edges for each member.

Pair requests consume both profiles and direct dialogue evidence. Preserve the
existing representative-image context of up to three main-member crops per side;
the ten-crop appearance inference belongs to the profile request. These are shared
references to retained crops, not a new embedding inference pass.

## Updating across chapters

Retain versioned profile, social relation and directed-address history with chapter,
run and evidence provenance. A translation run freezes the versions it consumes.

New explicit story evidence may replace a provisional appearance-based address.
When two confirmed statements conflict, flag review and retain the most recent
confirmed decision pending resolution. Update each address direction separately.
An absent interaction in a later chapter does not erase a prior relation.

## vLLM request and batch operation

Use the configured vLLM provider and pinned model/revision. The current effective
Runpod configuration is Qwen3.5-27B-AWQ, context 32768, runtime ceiling 64 slots.
Read these values from the effective frozen configuration, not hardcoded prompts.

- Name/embedding linking uses code and retained data, with no extra LLM request.
- Profile unit: one identity create/update request with up to ten images and a
  bounded evidence packet.
- Relationship unit: one communicating identity pair, returning both directed
  address decisions and social relations.
- Use a continuously filled queue, not fixed waves that wait for every item.
- Each new stage has a maximum of 8 simultaneous inference requests, configurable
  independently and bounded by the runtime-wide limit.
- Finish `build_identity` and freeze profiles before admitting relationship jobs.
- Reuse adaptive admission based on engine metrics; reducing admission affects
  future jobs and never re-enqueues a failed job.

Preflight each complete request with the served model's `/tokenize` endpoint,
including multimodal messages, system prompt and output schema. Input, configured
output reserve and safety allowance must fit the configured context fraction
(currently 85%, with an existing 512-token safety allowance). Record the budget.

Prepare and reduce evidence by code before the sole inference POST. Preserve
complete dialogue segments, prioritize direct relevant evidence and named
descriptions, distribute selected evidence across scenes, and remove repetition.
For profile requests, retain the selected up-to-ten crops and resize according to
configuration rather than silently dropping images to fit context. Record chosen
and omitted evidence; full source evidence remains reviewable.

Repeated tokenization while packing an unsent request is permitted. It is not an
inference retry. If mandatory inputs still cannot fit, record `budget_exceeded`
without an inference POST and use the fallback policy.

## No retries, receipts and resume

Profile creation/update and relationship inference get no automatic retry,
including invalid schema, semantic validation failure, truncated output, timeout,
network error or OOM. Disable all three retry surfaces for these tasks: in-worker
attempt loops, Calls/provider recovery, and scheduler OOM re-enqueue.

Before sending, persist a request identity derived from task, model/revision,
prompt/schema version, frozen profile/identity/evidence content, crop hashes and
generation settings. Persist started, raw response, validation and terminal state.

On resume:

- Reuse completed, validated, matching results with verified hashes.
- Do not automatically reissue failed requests.
- Do not reissue started/uncertain requests whose completion is unknown.
- Remaining never-sent jobs can continue when the service is usable.
- Preserve failure provenance instead of hiding the failure by changing keys.

A worker failure must not discard successful sibling results. For server-wide
unavailability, avoid draining the queue into repeated failures: record pending
work and continue downstream with the established fallback where possible.

## Failure behavior and publication

Record errors and omitted decisions in stage reviews. Use a previous valid profile
or edge where available. Otherwise use a successfully established appearance
anchor for provisional address selection. Keep unknown when evidence and anchor
are insufficient; translate then handles the unresolved case from its context.
Never publish a failed inference as a confirmed fact or overwrite a valid old fact
with a failure fallback.

Workers write isolated results. Validate and combine them deterministically before
publishing a version under the story lock. Do not let workers mutate shared graph
state while other requests are reading it. Write a stage summary that distinguishes
successful, fallback, failed, uncertain and never-sent work.

## Migration and implementation scope

Preserve existing MAGI bank and confirmed identity registry. Rebuild profiles,
relations and address edges into a new schema/version from retained evidence.
Retain the old graph for comparison only; it is not an inference seed or fallback
for v2. Future v2 runs can reuse valid prior v2 data.

Update pipeline DAG, stage directories, config/schema checks, cache fingerprints,
reviews, export/translation consumers and Runpod import/checkpoint/verification
contracts for the new stage boundaries. Frozen historical batches are immutable;
new source/configuration requires a new batch for any later authorized Runpod run.

Validation should cover name and visual linking restrictions, main-member history
selection, minimum crop distance, no chain propagation, directed address semantics,
profile update triggers, direct-communication eligibility, immutable version reuse,
budget overflow and absence of retries at every layer. No new model inference or
Runpod provisioning is authorized merely by preparing this design.

## Risks and explicit limits

- A single close crop pair can still create a wrong visual identity link. The
  chosen rule intentionally selects the minimum and adds no confidence margin.
- Retained bank vectors are a compact subset, not all historical observations.
- Appearance-based character/personality judgments remain inference.
- Evidence packing may omit relevant dialogue; omitted references stay available.
- Already incorrect confirmed identities need a separate review/correction action.
- New social relationship categories may require an explicit schema extension;
  do not collapse several roles into one free-text label.
