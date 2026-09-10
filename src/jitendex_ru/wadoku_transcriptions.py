"""Source-bound requests and stored model decisions for Japanese transcriptions."""
import json
import re
from .util import canonical_json, sha256_bytes
from .wadoku_quality import tree_paths, plain, source_identity
from .wadoku_xml import source_transcription_resolutions


def request_for(value, prompt_hash, run_id=None):
    known = source_transcription_resolutions(value)
    occurrences = [{'path': path, 'source_text': plain(node)}
                   for node, path in tree_paths(value['tree'])
                   if node['tag'] == 'transcr' and plain(node) not in known
                   and not value.get('transcription_path_resolutions', {}).get(path)
                   and not re.search(r'/(?:ref|sref)\[\d+\]/', path)]
    if not occurrences:
        return None
    request = {'entry_id': value['entry_id'], 'source_sha256': source_identity(value),
               'prompt_sha256': prompt_hash, 'tree': value['tree'], 'occurrences': occurrences}
    if run_id is not None:
        request['run_id'] = run_id
    request['batch_id'] = 'wtr-' + sha256_bytes(canonical_json(request))[:24]
    request['manifest_sha256'] = sha256_bytes(canonical_json(request))
    return request


def response_schema():
    fields = {k: {'type': 'string'} for k in ('path', 'source_text', 'target', 'reason')}
    fields['state'] = {'type': 'string', 'enum': ['resolved', 'unresolved']}
    return {'type': 'object', 'additionalProperties': False,
            'required': ['batch_id', 'manifest_sha256', 'resolutions'], 'properties': {
                'batch_id': {'type': 'string'}, 'manifest_sha256': {'type': 'string'},
                'resolutions': {'type': 'array', 'items': {'type': 'object',
                    'additionalProperties': False, 'required': list(fields), 'properties': fields}}}}


def validate_resolution(request, response):
    import fastjsonschema
    fastjsonschema.compile(response_schema())(response)
    if any(response[k] != request[k] for k in ('batch_id', 'manifest_sha256')):
        raise ValueError('transcription response identity differs')
    if [(r['path'], r['source_text']) for r in response['resolutions']] != [
            (r['path'], r['source_text']) for r in request['occurrences']]:
        raise ValueError('transcription occurrence coverage differs')
    for item in response['resolutions']:
        if not item['reason'].strip():
            raise ValueError('transcription resolution lacks evidence')
        if item['state'] == 'resolved':
            if not re.fullmatch(r'[\u3040-\u30ff\u3400-\u9fffー]+', item['target']):
                raise ValueError('transcription target must be Japanese')
        elif item['target']:
            raise ValueError('unresolved transcription cannot supply a target')


def apply_stored_resolutions(connection, run_id, value):
    """Keep decisions separate from immutable XML and translation provenance."""
    mapping = {}
    for row in connection.execute("""SELECT details_json FROM audit_event
        WHERE event_type='wadoku_transcription_resolution' AND entity_type='run' AND entity_id=?
        ORDER BY id""", (str(run_id),)):
        record = json.loads(row[0])
        if record['entry_id'] != value['entry_id'] or record['source_sha256'] != source_identity(value):
            continue
        for item in record['response']['resolutions']:
            mapping[item['path']] = item['target'] if item['state'] == 'resolved' else None
    return {**value, 'transcription_path_resolutions': mapping}
