import copy
import xml.etree.ElementTree as ET
import pytest
from jitendex_ru.wadoku_xml import canonical_entry
from jitendex_ru.wadoku_transcriptions import request_for, validate_resolution


def fixture():
    return canonical_entry(ET.fromstring('''<entry id="1"><form><orth>は</orth>
        <reading><hira>は</hira></reading></form><sense><expl><transcr>wa</transcr></expl></sense>
        <ref id="2"><transcr>ignored</transcr></ref></entry>'''))


def test_requests_are_run_scoped_and_skip_resolved_or_reference_transcriptions():
    value = fixture()
    request = request_for(value, 'prompt', 16)
    assert len(request['occurrences']) == 1
    assert request['batch_id'] != request_for(value, 'prompt', 17)['batch_id']
    assert request['batch_id'] != request_for(value, 'new-prompt', 16)['batch_id']
    value['transcription_path_resolutions'] = {request['occurrences'][0]['path']: 'は'}
    assert request_for(value, 'prompt', 16) is None


def test_response_requires_exact_identity_coverage_and_japanese():
    request = request_for(fixture(), 'prompt', 16)
    response = {k: request[k] for k in ('batch_id', 'manifest_sha256')}
    response['resolutions'] = [{**request['occurrences'][0], 'target': 'は',
                                'state': 'resolved', 'reason': 'Particle spelling from context.'}]
    validate_resolution(request, response)
    for target in ('wa', '', 'は<script>'):
        bad = copy.deepcopy(response); bad['resolutions'][0]['target'] = target
        with pytest.raises(ValueError):
            validate_resolution(request, bad)
    bad = copy.deepcopy(response); bad['resolutions'] = []
    with pytest.raises(ValueError, match='coverage'):
        validate_resolution(request, bad)
    bad = copy.deepcopy(response); bad['manifest_sha256'] = 'other'
    with pytest.raises(ValueError, match='identity'):
        validate_resolution(request, bad)
