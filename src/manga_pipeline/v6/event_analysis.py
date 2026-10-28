"""Parallel scenes, sequential multimodal events, frozen chapter context."""
import copy
import threading
from concurrent.futures import ThreadPoolExecutor

from ..progress import NullProgress
from ..analysis.speech import CONSERVATIVE_SHOUT_PROMPT
from ..runtime.llm_budget import BudgetExceeded
from ..storage.io import atomic_json, fingerprint, read_json
from . import merge
from .anchors import route
from .event_visuals import load_images, magi_tail_evidence, render
from .name_normalization import apply
from .policy import EVIDENCE_CONFIDENCE_PROMPT, POLICY
from .requests import Calls
from .semantic import snapshot
from .story_contract import SCORE, TEXT, failed_row, object_schema, row_schema, score, short_title, validate_row
from .story_graph import assemble, reference_people

PROMPT = '''Analyze ONLY the target utterances of this event; other text is context.
Use the frozen whole-chapter graph, current scene, previous event prose, cumulative
scene memory, source text and labelled page regions. Silent panels are included.
Prioritize scene content and textual understanding before interpreting images.
Read the SUPPLIED target/context dialogue in source order, using the supplied
scene summaries, previous event and incoming scene memory for context. Do not
assume the packet contains the complete source dialogue of the entire scene.
Within this ONE request, analyze auxiliary fields BEFORE selecting speaker and
addressee person IDs. These are ordered analysis instructions, not separate API
calls, an extra output object or a request to expose private reasoning.
First choose provisional content_type, speech_style, and each supplied mention's
mention_type and supported name components from the text. Then explicitly use
those choices as constraints/cues for separate speaker/addressee hypotheses.
Only image-dependent grounding may remain unknown at this text-first step.

Auxiliary fields: use exactly the following schema labels and meanings.
content_type:
- dialogue: spoken words in a communicative exchange.
- thought: a character's unspoken internal words, including thoughts in captions.
- narration: narrative exposition/commentary rather than an identified character's
  internal words; a character's explicitly supported narrative voice is still possible.
- sfx: written sound effects rather than spoken dialogue.
- action_state: text describing an action, condition or state rather than speech.
- ui: interface/system text, menus, prompts or similar on-screen labels.
- watermark: publisher/scanner/branding or ownership marks.
- unknown: insufficient evidence to choose a content category.
Classify the actual text function, not only its bubble shape or detector label.
Use this classification to decide whether a personal voice/intended recipient
exists. Do not force every sfx/action_state/ui/watermark into a human conversation.
Do not infer the speaker's identity merely from the content_type.

speech_style:
- normal: ordinary speaking when raised/quiet delivery is not independently supported.
- whisper: explicitly supported quiet/whispered delivery, not just small text or ellipses.
- shout: clear raised-volume delivery; punctuation, uppercase, emotion or a spiky
  bubble alone do not establish shouting. Follow the conservative shout policy.
- thought: inner-voice delivery when supported by context.
- caption: caption-style presentation, which can contain thought OR narration.
- unknown: delivery/presentation cannot be determined.
Use style to check the proposed communication situation, not to identify a person
from emotion, typography or volume. No tail alone does not establish thought.

For EVERY supplied mention candidate preserve candidate_id and choose mention_type:
- direct_address: a name used to call/address its owner, a cue for the addressee.
- introduction: an explicit naming/introduction of oneself or another person.
  Only a supported self-introduction links that name to the speaker; introducing
  another person does not make that person the speaker or the addressee.
- reference: talking ABOUT a named person, not necessarily talking TO that person.
- unknown: insufficient evidence to determine the name's role.
Infer the role of the name from its use in the utterance, not just a shared alias.
An empty mentions array means no supplied mention candidates for that utterance;
do not invent a candidate or an absent name to justify a speaker/addressee.
components must use only supported parts: given_name, middle_name, surname,
nickname or full_name, with the corresponding source name in value. Do not expand
a short name into the canonical full name or invent name parts from group metadata.

After source/image grounding, finalize each mention's target_person_id and
recipient_cluster_ref (the NAMED person's cluster, not automatically the utterance's
addressee). Use null when no matching grounded reference applies. Set
confirmed_person_name=true only when the mention is supported as a person's name
AND a supported target_person_id is selected; this flag is not a new MAGI identity
assignment or proof that the utterance's speaker owns the name.
visual_support=compatible means supplied visual observations support compatibility;
different_appearance means they show an appearance mismatch; unknown means the
comparison lacks evidence. Never turn a textual name group into visual support.
Score mention confidence.mention_type and confidence.target separately, and give
a concise mention evidence statement tied to supplied sources.

Apply the auxiliary analysis to attribution BEFORE finalizing it:
- A direct_address proposes the named person as an addressee candidate, not the
  speaker. A reference alone establishes neither speaker nor addressee.
- An introduction can suggest a name owner only as its actual wording/context
  supports; still ground the person with allowed IDs and independent evidence.
- Dialogue/thought/narration and style constrain the kind of voice and intended
  recipient; they do not automatically assign a person or force self/audience.
- Question/reply structure and pronouns help connect the exchange; automatic
  alternation, repeated summaries and a plausible story do not establish identity.
Form separate provisional speaker and addressee hypotheses from these auxiliary
results and what is said:
explicit address, questions and replies, pronouns, self-reference, knowledge and
conversational intent. Use graph prose and scene memory only as fallible context;
check their claims against source text. Do not assume a mentioned person is the
speaker or addressee, or assign turns by automatic alternation.
Then check those hypotheses against source anchors, supplied person mappings,
linked MAGIv2 tails and labelled images. Use image evidence to confirm, distinguish
or revise text-grounded candidates, not as the starting point for attribution.
Clear contradictory source/visual evidence may overturn a hypothesis; do not force
images to fit it. Weak proximity, pose or apparent gaze must not outweigh clear
dialogue evidence. If evidence remains ambiguous or conflicts, select the best
supported allowed candidate with lower confidence and briefly cite the supporting
source IDs and unresolved conflict in the existing evidence fields. Hypotheses
are provisional, not new identity facts or independent evidence for later turns.
Panel labels show reading order; MAGI labels identify bank characters. Unlabelled
characters still have supplied detection references. Respect the SOURCE ANCHOR
for speaker attribution as strong evidence with weight 8/10. Follow its person
assignment unless CLEAR, specific visual/source evidence demonstrates it is wrong.
If overriding it, cite the conflicting source IDs/labelled people and explain
exactly why the anchor is wrong. Explicit source dialogue can establish a conflict;
text-first ordering does not waive the anchor override requirement.
Graph prose, a mentioned name, alternation,
narrative plausibility or a higher self-rated confidence is not sufficient.
Weight 8/10 is evidential strength, not a confidence floor or a code lock.
If no single anchor person_id exists, consider its sources/conflicts without
inventing an anchor assignment. An anchor never proves who owns a mentioned name.
Infer speaker and listener independently, never by automatic alternation. Select
supplied visual person/cluster IDs for speakers/listeners under the existing
person/narrator/others and individual/group/self/audience contract. Only a REFERENCE
mention may target a reference-only named person with no MAGI/crop. Saying Toru
does not make the speaker Toru. Keep mention type and name-owner confidence separate.
Graph supplies narrative structure and textual name groups, NOT character or
speaker assignments to MAGI. Name groups may create reference-only named entities,
but those entities have no visual identity. Graph prose and confidence do not
prove who owns a bubble.
Bubble tails are ALREADY detected by MAGIv2, not by you. Each target's
bubble_tail.has_tail and bubble_tail.tails give its source text-to-tail links
and ORIGINAL PAGE bboxes. Supplied images mark these detections with magenta
TAIL labels matching tail_index. Use ONLY the supplied linked detections as
tail evidence; do NOT detect, invent, move, resize or reassign tails yourself.
If bubble_tail.has_tail=false, treat this target as having NO tail. Never
claim a visible/inferred tail or borrow another bubble's TAIL label in that
case, even if the image seems suggestive. A tail does not automatically name
a person and can point off-panel. With no supplied tail, explain independent
speaker evidence; if ambiguous choose the best supported candidate and score
it low rather than invent an ID. Never output somebody as a final person ID.
The selected speaker_cluster_ref must belong to speaker_person_id in visual_people;
use null if no matching cluster applies. speaker_kind=person requires a visual
person ID; narrator/others require null person and cluster IDs.
No tail alone does not classify speech as narration/thought. A mentioned name,
proximity, alternation, graph prose or a repeated summary is not tail evidence.
Caption is presentation, NOT narration: caption/narration may be thought. Do not
reject thought because the source label was caption. Speech dialogue cannot be
narrator_introduction. Cite supplied source IDs when possible; no proof substring
or fixed caption-distance requirement. Preserve every target and mention ID.
Finalize speaker_kind=person for an attributed visual person, narrator for a
supported impersonal narrative voice, or others when no personal/narrator source
can be assigned under the contract. Keep the required null IDs for non-person kinds.
Choose addressee_type from its actual communicative function:
- individual: one intended visual person; select exactly one addressee_person_id.
- group: multiple intended people; include only supported visual recipient IDs,
  without inventing people or treating all people in the panel as recipients.
- self: genuinely self-directed words; when an identified person addresses themself,
  the addressee is that speaker. Thought alone does not force self.
- audience: a general/readership audience, not a particular selected visual person.
- unknown: there is insufficient support to establish the recipient.
- not_applicable: the text has no intended recipient, such as a non-communicative
  sound/state/mark; use its actual function, not an automatic content-type mapping.
For audience/unknown/not_applicable, do not invent addressee person IDs.
Before returning JSON, cross-check auxiliary labels, named targets, speaker and
addressee for consistency. If source/image evidence contradicts a provisional
auxiliary label or hypothesis, revise the affected fields and their confidence;
do not keep mutually contradictory choices just to preserve an initial guess.
In the existing evidence/evidence_refs, briefly state which auxiliary cue mattered
to speaker/addressee selection and which supplied source/visual evidence grounds it.
Do not expose step-by-step reasoning or claim the auxiliary labels themselves are
new independent observations. Judge speaker/addressee confidence from the sources,
not by copying content_type/speech_style/mention confidence.
After these decisions, update event/scene prose and outgoing scene_memory. Their
new summaries support the NEXT event; do not cite a just-generated summary as
independent proof for this event's own speaker/addressee decisions.
Score EVERY decision independently 0..10; anchors support the relevant decision
only. All generated titles, descriptions, evidence, explanations, memory facts
and unresolved notes MUST be in English; preserve quoted OCR/names. Return concise
event prose, cumulative scene memory, and updated
scene prose. Do not alter scene/event boundaries. Keep titles under five words.
Do not treat repeated summaries of the same claim as independent evidence. No
causal/motive graph edges. JSON only, without thinking or a rewritten OCR document.'''


PROMPT += "\n" + CONSERVATIVE_SHOUT_PROMPT + "\n" + EVIDENCE_CONFIDENCE_PROMPT


def schema(state, candidates, ids, context=None):
    memory = object_schema({'description': TEXT,
        'facts': {'type': 'array', 'maxItems': 32, 'items': object_schema({
            'description': TEXT, 'source_ids': {'type': 'array', 'items': TEXT}, 'confidence': SCORE})},
        'characters': {'type': 'array', 'items': object_schema({'person_id': {'type': 'string', 'enum': list(state['persons'])},
            'description': TEXT, 'confidence': SCORE})},
        'unresolved': {'type': 'array', 'items': TEXT}})
    return object_schema({'utterances': {'type': 'array', 'minItems': len(ids), 'maxItems': len(ids),
        'items': row_schema(list(context['people']) if context else list(state['persons']),
                           list(context['clusters']) if context else list(state['cluster_to_person']), list(candidates), ids)},
        'event_title': TEXT, 'event_description': TEXT, 'event_confidence': SCORE,
        'scene_title': TEXT, 'scene_description': TEXT, 'scene_confidence': SCORE, 'scene_memory': memory})


def person_context(state, page_ids=None):
    people, clusters = {}, {}
    for ref, pid in state['cluster_to_person'].items():
        observations = state['observations'].get(ref, [])
        current = [o for o in observations if ref.startswith(state['extraction_scope']+':')]
        if not current or page_ids is not None and not any(o['page_id'] in page_ids for o in current):
            continue
        clusters[ref] = {'person_id': pid, 'observations': [
            {k: o.get(k) for k in ('page_id', 'bbox', 'character_index', 'source_bank_id')} for o in current]}
    for pid, p in state['persons'].items():
        refs = [ref for ref, value in clusters.items() if value['person_id'] == pid]
        bank_ids = {str(o['source_bank_id']) for ref, observations in state['observations'].items()
                    if state['cluster_to_person'].get(ref) == pid for o in observations if o.get('source_bank_id') is not None}
        if refs or p.get('primary_name') or bank_ids:
            people[pid] = {'name': p.get('primary_name'), 'reference_only': bool(p.get('story_reference_only')),
                           'cluster_refs': refs, 'magi_ids': sorted(bank_ids)}
    return {'people': people, 'clusters': clusters}


def analyze(store, provider, progress=None):
    scanned, raw, normalized = store.read('graph'), store.read('extract'), store.read('normalize')
    frozen = snapshot(store, raw)
    if scanned['graph_source_hash'] != fingerprint(normalized) or scanned['person_snapshot_hash'] != fingerprint(frozen):
        raise ValueError('Frozen graph inputs changed')
    document = apply(normalized, scanned['name_normalization'])
    state = reference_people(frozen['state'], scanned['name_normalization']['groups'])
    rows = route(document['utterances'], raw, state, read_json(store.manifest['bank_path']))
    tails = magi_tail_evidence(rows, raw['pages'])
    for row in rows:
        row['bubble_tail'] = tails[row['id']]
    by_id = {r['id']: r for r in rows}
    candidates = {c['id']: c for c in scanned['candidates']}
    cfg = store.manifest['config']['v6']
    scenes = copy.deepcopy(scanned['scenes'])
    for scene in scenes:
        # Older frozen graphs may still contain hypotheses; never feed them forward.
        scene['hypotheses'] = []
        for event in scene['events']:
            for uid in event['target_ids']:
                by_id[uid].update(scene_id=scene['scene_id'], event_id=event['event_id'])
    calls = Calls(store, provider, 'analyze', allow_uncertain=True)
    chapter_context = {'chapter': scanned['story_graph']['chapter'],
        'name_groups': [{k: g[k] for k in ('group_id', 'canonical_name', 'aliases')} for g in scanned['name_normalization']['groups']],
        'scenes': [{'scene_id': s['scene_id'], 'title': s['title'], 'description': s['initial_description'],
                    'events': [{k: e[k] for k in
                    ('event_id', 'title', 'initial_description')} for e in s['events']]} for s in scanned['scenes']],
        'links': [e for e in scanned['story_graph']['links'] if e['type'] not in {'contains', 'next'}]}
    geometry = store.read('bubble_geometry')
    lock = threading.Lock()
    progress = progress or NullProgress()
    progress.reset(total=len(rows), unit='utterance')
    directory = store.artifact_path('analyze').parent
    plan = {'policy': POLICY, 'graph_hash': fingerprint(scanned), 'scenes': copy.deepcopy(scenes),
            'source_hash': fingerprint(document), 'snapshot_hash': fingerprint(frozen)}
    atomic_json(directory / 'plan.json', plan)

    def run_scene(scene):
        memory = {'description': '', 'facts': [], 'characters': [], 'unresolved': []}
        previous, missing, records, packets, batches = None, [], [], [], []
        for event in scene['events']:
            target_rows = [by_id[uid] for uid in event['target_ids']]
            owned = {cid: c for cid, c in candidates.items() if c['utterance_id'] in event['target_ids']}
            event.update(memory_input=copy.deepcopy(memory), previous_event=copy.deepcopy(previous),
                         input_incomplete_events=list(missing))
            try:
                images = render(store, event, target_rows, rows, geometry)
                packets.extend(images)
                if len(images) > store.manifest['config']['vllm']['max_images_per_prompt']:
                    raise BudgetExceeded('Event page images exceed max_images_per_prompt; no page omitted')
                context_ids = set(uid for p in images for uid in p['context_ids'])
                context = person_context(state, {p['page_id'] for p in images})
                request = {'policy': POLICY, 'output_language': 'en', 'event_id': event['event_id'], 'scene_id': scene['scene_id'],
                    'chapter_context': chapter_context, 'scene_initial_description': scene['initial_description'],
                    'scene_current_description': scene['description'], 'previous_event': previous,
                    'scene_memory': memory, 'incomplete_events': missing,
                    'incomplete_event_text': [{'event_id': failed['event_id'], 'text': failed['text']} for failed in records
                                             if failed.get('status') == 'failed'],
                    'targets': [{k: r.get(k) for k in ('id', 'text', 'page_id', 'panel_index', 'panel_indices',
                                'bbox', 'speaker_anchor', 'original_speaker', 'bubble_tail')} for r in target_rows],
                    'context': [{'id': uid, 'text': by_id[uid]['text']} for uid in sorted(context_ids)],
                    'mentions': list(owned.values()), 'visual_people': context, 'images': images}
                wire = schema(state, owned, event['target_ids'], context)
                request_hash = fingerprint([PROMPT, request, wire, cfg['event_output_tokens']])
                with lock:
                    cached = store.target('analyze', event['event_id'], {'request_hash': request_hash})
                if cached is None:
                    value = calls.call(event['event_id'], 'analyze_event_v7', PROMPT, request, wire,
                        reserve=cfg['event_output_tokens'], images=load_images(store, images), oom_retry=False)
                    received = value.get('utterances') if isinstance(value, dict) else None
                    if (not isinstance(received, list) or len(received) != len(target_rows)
                            or {r.get('utterance_id') for r in received} != set(event['target_ids'])):
                        raise ValueError('Event response target coverage changed')
                    mapped = {r['utterance_id']: r for r in received}
                    accepted = [{**validate_row(mapped[r['id']], r, state, owned), 'batch_id': event['event_id']} for r in target_rows]
                    for field in ('event_confidence', 'scene_confidence'):
                        score(value[field])
                    if (not isinstance(value.get('scene_memory'), dict) or
                            not all(isinstance(value.get(k), str) for k in ('event_description', 'scene_description'))):
                        raise ValueError('Event/scene prose or memory missing')
                    cached = {'status': 'completed', 'rows': accepted, 'response': value}
                    with lock:
                        store.commit_target('analyze', event['event_id'], {'request_hash': request_hash}, result=cached)
                value = cached['response']
                event.update(status='completed', title=short_title(value.get('event_title'), event['title']),
                    description=value['event_description'], description_confidence=value['event_confidence'],
                    memory_output=copy.deepcopy(value['scene_memory']), image_packets=images)
                scene.update(title=short_title(value.get('scene_title'), scene['title']),
                             description=value['scene_description'], description_confidence=value['scene_confidence'])
                memory = copy.deepcopy(value['scene_memory'])
                previous = {'event_id': event['event_id'], 'description': event['description'],
                            'source_ids': event['target_ids']}
                records.append({'event_id': event['event_id'], 'status': 'completed', 'rows': cached['rows']})
                atomic_json(directory / 'packets' / (fingerprint(event['event_id'])+'.json'), request)
            except Exception as exc:
                event.update(status='failed', error=str(exc))
                missing.append(event['event_id'])
                records.append({'event_id': event['event_id'], 'status': 'failed', 'error': str(exc),
                    'text': [{'id': r['id'], 'text': r['text']} for r in target_rows],
                    'rows': [failed_row(r, list(owned.values()), exc) for r in target_rows]})
            batches.append({'batch_id': event['event_id'], 'rows': target_rows,
                            'request': {'scene_id': scene['scene_id'], 'scene_summary': scene['description']}})
            atomic_json(directory / 'events' / (fingerprint(event['event_id'])+'.json'), event)
            with lock:
                for _ in target_rows:
                    progress.advance()
        scene.update(summary=scene['description'], incomplete_events=missing,
                     analysis_status='completed_with_review' if missing else 'completed')
        return records, packets, batches

    maximum = min(cfg['scene_concurrency'], store.manifest['config'].get('runtime', {}).get('llm_slots', 8) or 8)
    with ThreadPoolExecutor(max_workers=max(1, maximum), thread_name_prefix='scene') as executor:
        completed = list(executor.map(run_scene, scenes))
    batches = [b for _, _, values in completed for b in values]
    all_records = [r for values, _, _ in completed for r in values]
    utterances, merged_state, calling = merge.merge(rows, [r['rows'] for r in all_records], state, candidates, raw)
    unresolved = [r['utterance_id'] for r in utterances if r['semantic_resolution'] != 'resolved']
    name_reviews = [{'utterance_id': r['utterance_id'], 'candidate_id': m['candidate_id'],
                     'registration': m.get('name_registration_review'), 'components': m.get('name_component_reviews', [])}
                    for r in utterances for m in r.get('mentions', [])
                    if m.get('name_registration_review') or m.get('name_component_reviews')]
    result = {'policy': POLICY, 'output_language': 'en', 'utterances': utterances, 'person_state': merged_state, 'calling_events': calling,
        'name_normalization': scanned['name_normalization'], 'source_bank_snapshot': frozen['source_bank_snapshot'],
        'speaker_merge_policy': 'inference-first-v1', 'scenes': scenes, 'batches': batches,
        'analysis_plan_hash': fingerprint(plan), 'unresolved_targets': unresolved,
        'event_failures': [{k: r[k] for k in ('event_id', 'error')} for r in all_records if r['status'] == 'failed'],
        'reference_images': [p for _, packets, _ in completed for p in packets],
        'semantic_validation_issues': name_reviews, 'semantic_coverage_complete': not unresolved,
        'failures': [], 'review_status': 'completed_with_review' if unresolved or name_reviews else 'completed'}
    result['story_graph'] = assemble(document, scenes, merged_state, utterances, scanned['story_graph']['links'])
    return result
