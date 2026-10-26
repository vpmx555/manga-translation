"""Explicit derived identity view for consumers; exported analyze fields stay intact."""
import copy


def row_view(row):
    result = copy.deepcopy(row)
    if row.get('identity_projection_policy') != 'analyze-authoritative-v3':
        return result
    result['speaker_person_id'] = row['speaker_identity_id']
    result['addressee_ids'] = copy.deepcopy(row['addressee_identity_ids'])
    for mention in result.get('mentions', []):
        if mention.get('candidate_id') in row.get('mention_identity_ids', {}):
            mention['target_person_id'] = row['mention_identity_ids'][mention['candidate_id']]
    return result


def document_view(document):
    result = copy.deepcopy(document)
    for field in ('utterances', 'effects', 'excluded'):
        if field in result:
            result[field] = [row_view(r) for r in result[field]]
    return result
