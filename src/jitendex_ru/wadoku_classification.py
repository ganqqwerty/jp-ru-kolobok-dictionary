"""Bounded source-bound linguistic decisions, separate from translation."""
from __future__ import annotations
import json
import copy
import re
import xml.etree.ElementTree as ET
from typing import Any

from .util import canonical_json, sha256_bytes
from .wadoku_quality import tree_paths, plain

REQUEST_VERSION = 'wadoku-structure-v5'
RESPONSE_SCHEMA_VERSION = 'wadoku-structure-schema-v5'


def parse_response(text: str) -> dict[str, Any]:
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:
                raise ValueError(f'duplicate response property: {key}')
            result[key]=value
        return result
    return json.loads(text,object_pairs_hook=unique)


def source_context(value: dict[str, Any]) -> dict[str, Any]:
    """Lossless XML text avoids repeating JSON keys for every source node."""
    def element(node):
        result=ET.Element(node['tag'],node['attributes'])
        result.text=node['text']
        result.tail=node['tail']
        result.extend(element(child) for child in node['children'])
        return result
    return {'entry_id':value['entry_id'], 'xml':ET.tostring(element(value['tree']),encoding='unicode'),
            'source_tree_sha256':sha256_bytes(canonical_json(value['tree']))}


def entry_tree(entry: dict[str, Any]) -> dict[str, Any]:
    if 'tree' in entry:  # Existing frozen requests remain readable.
        return entry['tree']
    from .wadoku_xml import canonical_entry
    return canonical_entry(ET.fromstring(entry['xml']))['tree']


def normalize_decision(result: dict[str, Any], request: dict[str, Any] | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Remove display spacing from alias kana readings, preserving the raw response."""
    if not isinstance(result,dict):
        raise ValueError('classification response must be an object')
    normalized = copy.deepcopy(result)
    changes = []
    if request and request.get('response_schema_version') in {'wadoku-structure-schema-v4', 'wadoku-structure-schema-v5'}:
        import fastjsonschema
        fastjsonschema.compile(response_schema(request))(result)
        selected=result['selected_example_ids']
        if len(set(selected))!=len(selected):
            raise ValueError('duplicate selected example ID')
        if any(result['examples'][key]['eligible'] is not True for key in selected):
            raise ValueError('selected example is not eligible')
        rows=[]
        for candidate in request['examples']:
            key=candidate['decision_id']
            decision=result['examples'][key]
            eligible=decision['eligible']
            state='accept' if key in selected else 'needs_review' if eligible is None else 'reject'
            reason=decision['reason']
            if eligible is True and key not in selected:
                reason='Не выбран по лимиту показа. '+reason
            rows.append({'decision_id':key,'state':state,'source_path':decision['source_path'],
                         'sense_path':decision['sense_path'],'reason':reason})
        normalized['examples']=rows
        normalized.pop('selected_example_ids')
        changes.append({'rule':'example-eligibility-map-v1','selected_example_ids':selected,
                        'candidate_count':len(rows)})
    if not isinstance(normalized.get('lookup_aliases'),list):
        return normalized, changes  # Let the schema reject missing or mistyped fields.
    nodes = list(tree_paths(entry_tree(request['entry']))) if request else []
    display_forms = {plain(node) for node,_ in nodes if node['tag']=='orth' and node['attributes'].get('midashigo')=='true'}
    lookup_forms = {plain(node) for node,_ in nodes if node['tag']=='orth' and node['attributes'].get('midashigo')!='true'}
    source_reading = next((plain(node) for node,_ in nodes if node['tag']=='hira'), '')
    kept=[]
    for index, alias in enumerate(normalized.get('lookup_aliases', [])):
        if not isinstance(alias,dict):
            kept.append(alias)
            continue
        if alias.get('source_form') in display_forms - lookup_forms:
            changes.append({'path':f'/lookup_aliases/{index}','original':copy.deepcopy(alias),'replacement':None,
                            'rule':'display-heading-not-lookup-v1'})
            continue
        kept.append(alias)
        if alias.get('kind') == 'suffix_only':
            evidence_paths = [path for node, path in nodes
                              if node['tag'] == 'orth' and path.startswith('/entry[1]/form[1]/orth[')
                              and node['attributes'].get('midashigo') != 'true'
                              and plain(node) == alias.get('source_form')]
            if evidence_paths and alias.get('evidence_path') not in evidence_paths:
                changes.append({'path': f'/lookup_aliases/{index}/evidence_path',
                                'original': alias.get('evidence_path'), 'replacement': evidence_paths[0],
                                'rule': 'source-suffix-evidence-path-v1'})
                alias['evidence_path'] = evidence_paths[0]
        reading = alias.get('reading')
        if not isinstance(reading, str):
            continue
        compact = re.sub(r'\s+', '', reading)
        if compact != reading:
            alias['reading'] = compact
            changes.append({'path': f'/lookup_aliases/{index}/reading', 'original':reading,
                            'replacement':compact, 'rule':'kana-lookup-spacing-v1'})
        if (alias.get('kind')=='suffix_only' and alias.get('source_form') in lookup_forms
                and re.search(r'[…~〜～]',source_reading)):
            forms=[part.strip() for part in re.split(r'[…~〜～]+',alias['source_form']) if part.strip()]
            readings=[part.strip() for part in re.split(r'[…~〜～]+',source_reading) if part.strip()]
            if forms and readings:
                for field,value in (('expression',forms[-1]),('reading',readings[-1])):
                    if alias.get(field)!=value:
                        changes.append({'path':f'/lookup_aliases/{index}/{field}','original':alias.get(field),
                                        'replacement':value,'rule':'source-fixed-suffix-v1'})
                        alias[field]=value
    normalized['lookup_aliases']=kept
    return normalized, changes


def object_schema(properties):
    return {'type': 'object', 'additionalProperties': False, 'properties': properties, 'required': list(properties)}


def response_schema(request: dict[str, Any], *, canonical: bool = False) -> dict[str, Any]:
    import re
    template_forms = sorted({plain(node) for node,_ in tree_paths(entry_tree(request['entry']))
                             if node['tag']=='orth' and re.search(r'[…~〜～]',plain(node))})
    candidate_ids = [item['decision_id'] for item in request['examples']]
    example = object_schema({
        'decision_id': {'type': 'string', 'enum': candidate_ids or ['none']},
        'state': {'type': 'string', 'enum': ['accept', 'reject', 'needs_review']},
        'source_path': {'type': ['string', 'null']},
        'sense_path': {'type': ['string', 'null']},
        'reason': {'type': 'string'},
    })
    alias = object_schema({'source_form': {'type': 'string','enum':template_forms or ['none']}, 'expression': {'type': 'string'},
        'reading': {'type': 'string'}, 'kind': {'type': 'string', 'enum': ['full_expansion','suffix_only']},
        'evidence_path': {'type': ['string','null']}, 'reason': {'type': 'string'}})
    schema = object_schema({
        'batch_id': {'type': 'string', 'const': request['batch_id']},
        'manifest_sha256': {'type': 'string', 'const': request['manifest_sha256']},
        'entry_id': {'type': 'integer', 'const': request['entry']['entry_id']},
        'article_policy': {'type': 'string', 'enum': ['independent','inherit_parent','needs_review']},
        'parent_id': {'type': ['integer','null']},
        'lookup_policy': {'type': 'string', 'enum': ['independent_direct','shared_direct','deinflect_to_parent','needs_review']},
        'reason': {'type': 'string'},
        'confidence': {'type': 'string', 'enum': ['high','medium','low']},
        'lookup_aliases': {'type': 'array', 'items': alias, 'maxItems':max(0,len(template_forms)*3)},
        'lookup_needs_review': {'type': ['string','null']},
        'examples': {'type': 'array', 'items': example, 'minItems':len(candidate_ids), 'maxItems':len(candidate_ids)},
    })
    if request.get('response_schema_version') in {'wadoku-structure-schema-v4', 'wadoku-structure-schema-v5'} and not canonical:
        schema['$defs']={'example':object_schema({
            'eligible':{'type':['boolean','null']},
            'source_path':{'type':['string','null']},
            'sense_path':{'type':['string','null']},
            'reason':{'type':'string'},
        })}
        schema['properties']['examples']=object_schema({key:{'$ref':'#/$defs/example'} for key in candidate_ids})
        if request['response_schema_version'] == 'wadoku-structure-schema-v5':
            sense_paths = [path for node, path in tree_paths(entry_tree(request['entry'])) if node['tag'] == 'sense']
            properties = {}
            for candidate in request['examples']:
                item = copy.deepcopy(schema['$defs']['example'])
                item['properties']['source_path']['enum'] = [None, *dict.fromkeys(
                    block['xml_path'] for block in candidate['source_blocks'] if block['role'] in {'translation', 'definition'})]
                item['properties']['sense_path']['enum'] = [None, *sense_paths]
                properties[candidate['decision_id']] = item
            schema['properties']['examples'] = object_schema(properties)
            del schema['$defs']
        schema['properties']['selected_example_ids']={'type':'array','maxItems':min(3,len(candidate_ids)),
            'items':{'type':'string','enum':candidate_ids or ['none']}}
        schema['required'].append('selected_example_ids')
    return schema


def make_request(entry: dict[str, Any], parents: list[dict[str, Any]], candidates: list[dict[str, Any]], scope_id: str,
                 prompt_sha256: str = '') -> dict[str, Any]:
    # Keep the complete tree once. Canonical block text duplicates that tree;
    # example blocks still need their exact source paths and protected objects.
    examples = [{**candidate, 'source_blocks': [
        {key:value for key,value in block.items() if key not in {'source_text','has_translatable_text'}}
        for block in candidate['source_blocks']], 'decision_id': sha256_bytes(canonical_json([
        scope_id,candidate['parent_id'],candidate['child_id'],candidate['relation_path'],candidate['source_sha256']]))[:32]}
        for candidate in candidates]
    request = {'version': REQUEST_VERSION, 'response_schema_version': RESPONSE_SCHEMA_VERSION,
               'scope_id': scope_id, 'prompt_sha256': prompt_sha256, 'entry': source_context(entry),
               'parents': [source_context(parent) for parent in parents], 'examples': examples}
    request['batch_id'] = 'wdc-' + sha256_bytes(canonical_json(request))[:32]
    request['manifest_sha256'] = sha256_bytes(canonical_json(request))
    return request


def rebase_proposal(old_request: dict[str, Any], proposal: dict[str, Any], new_request: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Reuse a linguistic answer only when all supplied source context is identical."""
    def comparable_tree(entry):
        tree = copy.deepcopy(entry_tree(entry))
        # XML parsing discards the whitespace after the closing entry element.
        # Do not normalize any whitespace inside the dictionary article.
        if not tree['tail'].strip():
            tree['tail'] = ''
        return tree
    def context(request):
        return {'entry': comparable_tree(request['entry']),
                'parents': [(p['entry_id'], comparable_tree(p)) for p in request['parents']],
                'examples': [{**{k:v for k,v in c.items() if k not in {'decision_id','source_blocks'}},
                    'source_blocks': [{k:v for k,v in b.items() if k not in {'source_text','has_translatable_text'}}
                                      for b in c['source_blocks']]} for c in request['examples']],
                'prompt_sha256': request['prompt_sha256']}
    if context(old_request) != context(new_request):
        raise ValueError('classification context changed; cannot reuse')
    if [e['decision_id'] for e in proposal['examples']] != [e['decision_id'] for e in old_request['examples']]:
        raise ValueError('old proposal coverage differs')
    result = copy.deepcopy(proposal)
    for key in ('entry_id','batch_id','manifest_sha256'):
        result[key] = new_request['entry']['entry_id'] if key == 'entry_id' else new_request[key]
    for decision, candidate in zip(result['examples'], new_request['examples']):
        decision['decision_id'] = candidate['decision_id']
    # The stored proposal already uses the canonical list contract, not the wire map.
    result, changes = normalize_decision(result, {**new_request, 'response_schema_version':'canonical-list'})
    errors = validate_decision(new_request, result)
    if errors:
        raise ValueError(f'reused proposal fails current validation: {errors}')
    return result, changes


def validate_decision(request: dict[str, Any], result: dict[str, Any]) -> list[str]:
    import fastjsonschema
    try:
        fastjsonschema.compile(response_schema(request,canonical=True))(result)
    except fastjsonschema.JsonSchemaException as error:
        return [str(error)]
    errors = []
    parent_ids = {entry['entry_id'] for entry in request['parents']}
    policy = result['article_policy']
    lookup = result['lookup_policy']
    valid_pairs = {('independent','independent_direct'), ('inherit_parent','shared_direct'),
                   ('inherit_parent','deinflect_to_parent'), ('needs_review','needs_review')}
    if (policy,lookup) not in valid_pairs:
        errors.append('incompatible article and lookup decisions')
    if policy == 'inherit_parent' and result['parent_id'] not in parent_ids:
        errors.append('parent not present in source context')
    if policy == 'independent' and result['parent_id'] is not None:
        errors.append('independent article cannot inherit a parent')
    expected = request['examples']
    if [item['decision_id'] for item in result['examples']] != [item['decision_id'] for item in expected]:
        errors.append('example identity/order differs')
        return errors
    accepted = []
    sense_paths = {path for node,path in tree_paths(entry_tree(request['entry'])) if node['tag']=='sense'}
    for candidate, decision in zip(expected,result['examples']):
        if not decision['reason'].strip():
            errors.append('empty example reason')
        if decision['state'] != 'accept':
            continue
        if candidate['template']:
            errors.append('unexpanded example template selected')
        source_paths = {block['xml_path'] for block in candidate['source_blocks'] if block['role'] in {'translation','definition'}}
        if decision['source_path'] not in source_paths:
            errors.append('example meaning path not in source')
        if decision['sense_path'] is not None and decision['sense_path'] not in sense_paths:
            errors.append('example parent sense absent')
        accepted.append((candidate['japanese'],candidate['reading']))
    if len(accepted)>3 or len(set(accepted))!=len(accepted):
        errors.append('example limit or duplicate')
    import re
    forms = {plain(node) for node,_ in tree_paths(entry_tree(request['entry'])) if node['tag']=='orth'}
    has_template = any(re.search(r'[…~〜～]',form) for form in forms)
    if has_template and not result['lookup_aliases'] and not result['lookup_needs_review']:
        errors.append('template has neither an alias nor an unresolved reason')
    reading = next(plain(node) for node,_ in tree_paths(entry_tree(request['entry'])) if node['tag']=='hira')
    paths = {path:plain(node) for node,path in tree_paths(entry_tree(request['entry']))}
    for alias in result['lookup_aliases']:
        if alias['source_form'] not in forms or not alias['expression'] or not alias['reading']:
            errors.append('alias source or output missing')
        if re.search(r'[…~〜～]', alias['expression']+alias['reading']):
            errors.append('unexpanded lookup alias')
        if alias['kind']=='suffix_only':
            if (not str(alias['evidence_path']).startswith('/entry[1]/form[1]/orth[')
                    or paths.get(alias['evidence_path']) != alias['source_form']):
                errors.append('suffix alias has no exact source-form evidence')
            if not alias['source_form'].endswith(alias['expression']) or not reading.endswith(alias['reading']):
                errors.append('suffix alias not aligned with source')
            fixed = [part.strip() for part in re.split(r'[…~〜～]+',alias['source_form']) if part.strip()]
            if not fixed or alias['expression'] != fixed[-1]:
                errors.append('suffix alias loses fixed lexical material')
        elif paths.get(alias['evidence_path']) != alias['expression']:
            errors.append('full expansion has no exact attested evidence')
    if not result['reason'].strip():
        errors.append('empty lexical reason')
    return errors
