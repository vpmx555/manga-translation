"""Compact chapter review transport; preserve texts and semantic decisions."""
import copy

POLICY = 'chapter-review-tables-v2'
ID_FIELDS = {'id', 'person_id', 'utterance_id', 'candidate_id', 'scene_id', 'event_id',
    'speaker_person_id', 'target_person_id', 'speaker_cluster_ref', 'recipient_cluster_ref',
    'source_text_id', 'cluster_ref', 'continuation_of'}
ID_LISTS = {'target_ids', 'event_ids', 'addressee_person_ids', 'conflicting_person_ids', 'candidate_person_ids',
    'evidence_refs', 'source_ids', 'requires'}


def table(records):
    columns = list(dict.fromkeys(k for row in records for k in row))
    return {'columns': columns, 'rows': [[row.get(k) for k in columns] for row in records]}


def replace_ids(value, mapping):
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, list):
        return [replace_ids(x, mapping) for x in value]
    if isinstance(value, dict):
        return {mapping.get(k, k): replace_ids(v, mapping) for k, v in value.items()}
    return value


def encode(request):
    forward, reverse = {}, {}
    def register(value, prefix='x'):
        if isinstance(value, str) and value and value not in forward:
            token = prefix+str(sum(k.startswith(prefix) for k in reverse))
            forward[value], reverse[token] = token, value
    for row in request['texts']:
        register(row['id'], 'u')
    for pid in request['people']:
        register(pid, 'p')
    for row in request['mentions']:
        register(row['id'], 'm')
    for scene in request['scenes']:
        register(scene['scene_id'], 's')
        for event in scene['events']:
            register(event['event_id'], 'e')

    def collect(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in ID_FIELDS:
                    register(child)
                elif key in ID_LISTS and isinstance(child, list):
                    for ident in child:
                        register(ident)
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)
    collect(request)
    data = replace_ids(request, forward)
    codebooks = {}
    def coded(field, value):
        if not isinstance(value, str):
            return value
        options = codebooks.setdefault(field, [])
        if value not in options:
            options.append(value)
        return options.index(value)
    anchors, sources, groups, prose, person_attributes, profiles = [], [], [], [], [], []
    def intern(pool, value, prefix):
        for index, old in enumerate(pool):
            if old == value:
                return prefix+str(index)
        pool.append(copy.deepcopy(value))
        return prefix+str(len(pool)-1)
    decision_rows = []
    mention_rows = []
    membership = {uid: (s['scene_id'], e['event_id']) for s in data['scenes']
                  for e in s['events'] for uid in e['target_ids']}
    for source_row in data['decisions']:
        row = copy.deepcopy(source_row)
        for key in ('content_type','speech_style','speaker_kind','addressee_type','semantic_resolution'):
            row[key] = coded(key, row.get(key))
        row['confidence'] = [row.get('confidence', {}).get(k) for k in ('speaker','addressee','content_type','speech_style')]
        profile = {k: row.pop(k) for k in ('content_type','speech_style','speaker_kind','addressee_type','confidence','semantic_resolution')}
        row['semantic_profile'] = intern(profiles, profile, 'd')
        parent = membership.get(row['utterance_id'])
        if parent and row.get('scene_id') in (None, parent[0]) and row.get('event_id') in (None, parent[1]):
            row.pop('scene_id', None)
            row.pop('event_id', None)
        if isinstance(row.get('evidence'), str):
            row['evidence'] = intern(prose, {'text': row['evidence']}, 'q')
        anchor = row.get('speaker_anchor')
        if isinstance(anchor, dict):
            anchor['origin'] = coded('anchor_origin', anchor.get('origin'))
            anchor['sources'] = [intern(sources, x, 'b') for x in anchor.get('sources', [])]
            row['speaker_anchor'] = intern(anchors, anchor, 'a')
        references = []
        for mention in row.get('mentions', []):
            mention = copy.deepcopy(mention)
            mention['mention_type'] = coded('mention_type', mention.get('mention_type'))
            mention['confidence'] = [mention.get('confidence', {}).get(k) for k in ('mention_type','target')]
            if isinstance(mention.get('evidence'), str):
                mention['evidence'] = intern(prose, {'text': mention['evidence']}, 'q')
            references.append(len(mention_rows))
            mention_rows.append(mention)
        row['mentions'] = references
        decision_rows.append(row)
    candidates = []
    for source_row in data['mentions']:
        row = copy.deepcopy(source_row)
        row['alias_groups'] = [intern(groups, x, 'g') for x in row.get('alias_groups', [])]
        candidates.append(row)
    scenes, events = [], []
    for source_scene in data['scenes']:
        scene = copy.deepcopy(source_scene)
        for event in scene['events']:
            events.append({'scene_id': scene['scene_id'], **event})
        scene['events'] = [e['event_id'] for e in scene['events']]
        scenes.append(scene)
    person_members = {}
    for pid, record in data['people'].items():
        attributes = {k:v for k,v in record.items() if k != 'person_id'}
        ref = intern(person_attributes, attributes, 'h')
        person_members.setdefault(ref, []).append(pid)
    people = [{'person_ids': person_members['h'+str(i)], **v} for i,v in enumerate(person_attributes)]
    packed = {'policy': data['policy'], 'wire_policy': POLICY, 'codebooks': codebooks,
        'decision_confidence_columns': ['speaker','addressee','content_type','speech_style'],
        'mention_confidence_columns': ['mention_type','target'],
        'texts': table(data['texts']), 'scenes': table(scenes), 'events': table(events),
        'decisions': table(decision_rows), 'mention_decisions': table(mention_rows),
        'people': table(people), 'mentions': table(candidates),
        'anchors': table([{'id': 'a'+str(i), **v} for i,v in enumerate(anchors)]),
        'anchor_sources': table([{'id': 'b'+str(i), **v} for i,v in enumerate(sources)]),
        'alias_groups': table([{'wire_group_ref': 'g'+str(i), **v} for i,v in enumerate(groups)]),
        'semantic_profiles': table([{'id': 'd'+str(i), **v} for i,v in enumerate(profiles)]),
        'prose': table([{'id': 'q'+str(i), **v} for i,v in enumerate(prose)]),
        'event_failures': data['event_failures']}
    if 'scope' in data:
        packed.update(scope=data['scope'], output_language=data['output_language'],
                      context_scenes=table(data['context_scenes']), boundary_context=data['boundary_context'],
                      clusters=table(data['clusters']), name_groups=table(data['name_groups']))
    return packed, {'policy': POLICY, 'references': reverse}, forward


def decode(value, ledger):
    references = ledger['references']
    def visit(item, key=None):
        if isinstance(item, str):
            return references.get(item, item) if key in ID_FIELDS or key in ID_LISTS else item
        if isinstance(item, list):
            return [visit(x, key) for x in item]
        if isinstance(item, dict):
            return {k: visit(v, k) for k,v in item.items()}
        return item
    return visit(value)


PROMPT = '''Input uses lossless column tables: every table has columns and row arrays.
Read each row by its declared columns. decisions enum integers resolve through
codebooks by field name. Confidence arrays follow the supplied confidence columns.
decisions.mentions contains zero-based row indexes into mention_decisions.
speaker_anchor references anchors.id; anchors.sources references anchor_sources.id.
mentions.alias_groups references alias_groups.wire_group_ref. scenes.events lists
event IDs in the events table. decisions inherit scene/event membership from the
events target_ids unless an explicit override is present. decisions.semantic_profile
references semantic_profiles.id; it contains role enums, confidence and resolution.
people.person_ids lists DISTINCT person IDs sharing display metadata only:
this storage factoring NEVER merges people or establishes identity. Input evidence references
prose.id; owned decision evidence is retained there. scope declares the ONLY IDs
you may correct. context_scenes and boundary_context are read-only; they summarize
the other half and retain adjacent event text. clusters maps allowed visual
cluster_ref to person_id. name_groups organizes name surfaces, not visual identities.
Use the supplied compact u/p/m/s/e/x references in OUTPUT ID fields. Output still
uses the normal JSON object schema with string enums and named confidence fields;
do NOT return table arrays or enum integers. Never use a honorific such as san,
kun or chan as a given name, surname or nickname component.'''
