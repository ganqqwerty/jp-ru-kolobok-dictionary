"""Lossless, versioned worker transport; never changes canonical validation inputs."""
from copy import deepcopy
import xml.etree.ElementTree as ET

from .util import canonical_json
from .wadoku_xml import lossless_node

FORMAT = 'wadoku-xml-context-v1'
MARKER = 'WTR-19 — COMPACT SOURCE CONTEXT (wadoku-xml-context-v1).'
REF = '$wadoku_xml_ref'
TREE_KEYS = {'tag', 'attributes', 'text', 'tail', 'children'}


def _element(node):
    element = ET.Element(node['tag'], node['attributes'])
    element.text, element.tail = node['text'], node['tail']
    element.extend(_element(child) for child in node['children'])
    return element


def expand_wire(wire):
    """Reconstruct the original JSON, including mixed-content tails and unit order."""
    if wire.get('wire_format') != FORMAT:
        raise ValueError('unknown Wadoku wire format')

    def expand(value):
        if isinstance(value, dict):
            if set(value) == {REF}:
                # Wrapper permits a source root to have trailing mixed text.
                wrapper = ET.fromstring('<wire>' + wire['contexts'][value[REF]] + '</wire>')
                if len(wrapper) != 1:
                    raise ValueError('invalid source context')
                return lossless_node(wrapper[0])
            return {key: expand(child) for key, child in value.items()}
        if isinstance(value, list):
            return [expand(child) for child in value]
        return value

    return expand(wire['manifest'])


def translation_wire(manifest, prompt):
    """Opt in through the frozen prompt; fall back if XML is not lossless or smaller."""
    original = canonical_json(manifest)
    if MARKER not in prompt:
        return original, 'canonical-json'
    contexts, identities = {}, {}

    def compact(value):
        if isinstance(value, dict):
            if REF in value:
                raise ValueError('reserved transport key in canonical input')
            if set(value) == TREE_KEYS:
                identity = canonical_json(value)
                if identity not in identities:
                    key = f'context-{len(contexts)}'
                    identities[identity] = key
                    contexts[key] = ET.tostring(_element(value), encoding='unicode')
                return {REF: identities[identity]}
            return {key: compact(child) for key, child in value.items()}
        if isinstance(value, list):
            return [compact(child) for child in value]
        return value

    try:
        wire = {'wire_format': FORMAT, 'manifest': compact(deepcopy(manifest)), 'contexts': contexts}
        if expand_wire(wire) != manifest:
            return original, 'canonical-json'
        encoded = canonical_json(wire)
        return (encoded, FORMAT) if len(encoded) < len(original) else (original, 'canonical-json')
    except (ValueError, TypeError, KeyError, ET.ParseError):
        return original, 'canonical-json'
