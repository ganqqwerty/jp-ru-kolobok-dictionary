from __future__ import annotations

import json
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterator

from .util import canonical_json, sha256_bytes, sha256_file


WADOKU_NAMESPACE = "http://www.wadoku.de/xml/entry"
WADOKU_PIPELINE = "wadoku-xml-v2"
WADOKU_ARCHIVE_SHA256 = "1028ad3e5d64a14097ae0fef47e57d4615238028b1d0315dbe28082660f07d3b"
EXPECTED_COUNTS = {
    "entries": 446_501,
    "written_forms": 756_859,
    "hiragana_readings": 446_501,
    "senses": 512_363,
    "translations": 867_198,
    "translation_texts": 866_347,
    "definitions": 49_587,
    "explanations": 44_744,
    "etymologies": 43_412,
    "references": 324_764,
    "usage_nodes": 203_159,
    "accents": 289_531,
    "pronunciation_records": 446_501,
}
COUNT_TAGS = {
    "entry": "entries",
    "orth": "written_forms",
    "hira": "hiragana_readings",
    "sense": "senses",
    "trans": "translations",
    "tr": "translation_texts",
    "def": "definitions",
    "expl": "explanations",
    "etym": "etymologies",
    "ref": "references",
    "usg": "usage_nodes",
    "accent": "accents",
    "hatsuon": "pronunciation_records",
}
BLOCK_ROLES = {
    "tr": "translation",
    "def": "definition",
    "expl": "explanation",
    "etym": "etymology",
    "descr": "description",
}
PROTECTED_TAGS = frozenset({
    "orth", "hira", "hatsuon", "romaji", "accent", "jap", "transcr",
    "foreign", "scientif", "specchar", "birthdeath", "date", "ref", "sref",
    "link", "steinhaus", "wikide", "wikija", "ruigo", "title",
})
PLACEHOLDER_RE = re.compile(r"⟦WDXP\d{4}⟧")
TRANSLATABLE_RE = re.compile(r"[A-Za-z\u00c0-\u024f\u1e00-\u1eff]")
SECTION_CODES = (
    "forms", "reading", "pronunciation", "grammar", "senses", "translation",
    "definition", "explanation", "etymology", "description", "reference", "note",
)
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
YOMITAN_INFLECTION_RULES = {
    "5": "v5",
    "1e": "v1",
    "1i": "v1",
    "suru": "vs",
    "kuru": "vk",
}
ARTICLE_LOOKUP_POLICIES = frozenset({
    "deinflect_to_parent", "shared_direct", "independent_direct",
})
ARTICLE_GROUP_DECISION_MODES = frozenset({"deterministic", "luna_required"})


def local_name(value: str) -> str:
    return value.rsplit("}", 1)[-1]


def lossless_node(element: ET.Element) -> dict[str, Any]:
    return {
        "tag": local_name(element.tag),
        "attributes": {key: element.attrib[key] for key in sorted(element.attrib)},
        "text": element.text or "",
        "tail": element.tail or "",
        "children": [lossless_node(child) for child in element],
    }


def _space_enabled(value: str | None) -> bool:
    return value is not None and value.lower() not in {"false", "0", "no"}


def render_plain(element: ET.Element) -> str:
    pieces: list[str] = []
    if _space_enabled(element.attrib.get("hasPrecedingSpace")):
        pieces.append(" ")
    pieces.append(element.text or "")
    for child in element:
        pieces.append(render_plain(child))
        pieces.append(child.tail or "")
    if _space_enabled(element.attrib.get("hasFollowingSpace")):
        pieces.append(" ")
    return "".join(pieces)


def render_prompt(element: ET.Element) -> tuple[str, list[dict[str, Any]]]:
    fragments: list[dict[str, Any]] = []

    def walk(node: ET.Element) -> str:
        if local_name(node.tag) in PROTECTED_TAGS:
            placeholder = f"⟦WDXP{len(fragments) + 1:04d}⟧"
            fragments.append({
                "placeholder": placeholder,
                "text": render_plain(node),
                "tree": lossless_node(node),
            })
            return placeholder
        pieces: list[str] = []
        if _space_enabled(node.attrib.get("hasPrecedingSpace")):
            pieces.append(" ")
        pieces.append(node.text or "")
        for child in node:
            pieces.append(walk(child))
            pieces.append(child.tail or "")
        if _space_enabled(node.attrib.get("hasFollowingSpace")):
            pieces.append(" ")
        return "".join(pieces)

    prompt = walk(element)
    if PLACEHOLDER_RE.search(render_plain(element)):
        raise ValueError("Wadoku source contains a reserved protected placeholder")
    return prompt, fragments


def _paths(root: ET.Element) -> Iterator[tuple[ET.Element, str, str | None, bool]]:
    def walk(
        node: ET.Element, path: str, sense_path: str | None, inside_block: bool,
    ) -> Iterator[tuple[ET.Element, str, str | None, bool]]:
        tag = local_name(node.tag)
        current_sense = path if tag == "sense" else sense_path
        yield node, path, current_sense, inside_block
        totals: Counter[str] = Counter()
        for child in node:
            child_tag = local_name(child.tag)
            totals[child_tag] += 1
            child_path = f"{path}/{child_tag}[{totals[child_tag]}]"
            yield from walk(child, child_path, current_sense, inside_block or tag in BLOCK_ROLES)

    yield from walk(root, "/entry[1]", None, False)


def extract_blocks(entry: ET.Element) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for element, path, sense_path, inside_block in _paths(entry):
        tag = local_name(element.tag)
        if tag not in BLOCK_ROLES or inside_block:
            continue
        source_text = render_plain(element)
        prompt_text, fragments = render_prompt(element)
        unprotected = PLACEHOLDER_RE.sub("", prompt_text)
        blocks.append({
            "xml_path": path,
            "role": BLOCK_ROLES[tag],
            "sense_path": sense_path,
            "source_text": source_text,
            "prompt_text": prompt_text,
            "protected_fragments": fragments,
            "has_translatable_text": bool(TRANSLATABLE_RE.search(unprotected)),
        })
    return blocks


def canonical_entry(entry: ET.Element) -> dict[str, Any]:
    if local_name(entry.tag) != "entry":
        raise ValueError("canonical Wadoku object requires an entry element")
    try:
        entry_id = int(entry.attrib["id"])
    except (KeyError, ValueError) as error:
        raise ValueError("Wadoku entry has no valid integer ID") from error
    return {
        "schema_version": 1,
        "namespace": WADOKU_NAMESPACE,
        "entry_id": entry_id,
        "tree": lossless_node(entry),
        "blocks": extract_blocks(entry),
    }


def canonical_identity(value: dict[str, Any]) -> tuple[str, str, int]:
    if value.get("schema_version") != 1 or value.get("namespace") != WADOKU_NAMESPACE:
        raise ValueError("unsupported Wadoku canonical object")
    tree = value["tree"]
    orthographies: list[dict[str, Any]] = []
    readings: list[str] = []

    def visit(node: dict[str, Any]) -> None:
        if node["tag"] == "orth":
            orthographies.append(node)
        elif node["tag"] == "hira":
            readings.append(_plain_tree(node))
        for child in node["children"]:
            visit(child)

    visit(tree)
    if not orthographies or not readings:
        raise ValueError(f"Wadoku entry {value['entry_id']} lacks an orthography or reading")
    expression_node = next(
        (node for node in orthographies if node["attributes"].get("midashigo") != "true"),
        orthographies[0],
    )
    return _plain_tree(expression_node), readings[0], int(value["entry_id"])


def _plain_tree(node: dict[str, Any]) -> str:
    pieces = [node["text"]]
    for child in node["children"]:
        pieces.extend((_plain_tree(child), child["tail"]))
    return "".join(pieces)


def translation_unit_id(
    archive_sha256: str, entry_id: int, block: dict[str, Any],
) -> str:
    prompt_sha256 = sha256_bytes(block["prompt_text"].encode("utf-8"))
    identity = "\0".join((
        archive_sha256, str(entry_id), block["role"], block["xml_path"], prompt_sha256,
    ))
    return "wdx-" + sha256_bytes(identity.encode("utf-8"))[:32]


def decode_v1_single_block(source_text: str, target_text: str) -> tuple[str, str] | None:
    """Return the only safe V1 German/Russian pair, or reject its shape."""
    try:
        source = json.loads(source_text)
        target = json.loads(target_text)
    except (TypeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(source, list) or len(source) != 1
        or not isinstance(source[0], str) or "\n" in source[0]
        or not isinstance(target, list) or len(target) != 1
        or not isinstance(target[0], str) or not target[0]
    ):
        return None
    return source[0], target[0]


def iter_canonical_entries(source: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    ordinal = 0
    context = ET.iterparse(source, events=("start", "end"))
    _event, root = next(context)
    for event, element in context:
        if event == "end" and local_name(element.tag) == "entry":
            ordinal += 1
            yield ordinal, canonical_entry(element)
            element.clear()
            root.clear()


def validate_xml(source: Path, xsd: Path) -> None:
    wrapper = f'''<?xml version="1.0"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:wd="{WADOKU_NAMESPACE}">
  <xs:import namespace="{WADOKU_NAMESPACE}" schemaLocation="{xsd.resolve().as_uri()}"/>
  <xs:element name="entries"><xs:complexType><xs:sequence>
    <xs:element ref="wd:entry" minOccurs="0" maxOccurs="unbounded"/>
  </xs:sequence><xs:anyAttribute processContents="lax"/></xs:complexType></xs:element>
</xs:schema>'''
    with tempfile.TemporaryDirectory(prefix="wadoku-xsd-") as directory:
        wrapper_path = Path(directory) / "wadoku-document.xsd"
        wrapper_path.write_text(wrapper, encoding="utf-8")
        completed = subprocess.run(
            ["xmllint", "--noout", "--schema", str(wrapper_path), str(source)],
            capture_output=True, text=True, check=False,
        )
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()
        raise ValueError(f"Wadoku XML schema validation failed: {detail}")


def _controlled_labels(entry: ET.Element, output: Counter[tuple[str, str]]) -> None:
    for element in entry.iter():
        tag = local_name(element.tag)
        if tag == "gramGrp":
            for child in element:
                output[("grammar", local_name(child.tag))] += 1
        elif tag == "usg":
            usage_type = element.attrib.get("type", "")
            output[("usage-category", usage_type)] += 1
            text = render_plain(element)
            if text:
                output[("usage", f"{usage_type}:{text}")] += 1
        elif tag == "orth":
            if "type" in element.attrib:
                output[("orthography", element.attrib["type"])] += 1
            if "midashigo" in element.attrib:
                output[("orthography", f"midashigo:{element.attrib['midashigo']}")] += 1
        elif tag in {"ref", "sref"}:
            output[("reference", f"{tag}:type:{element.attrib.get('type', '')}")] += 1
            if "subentrytype" in element.attrib:
                output[("reference", f"subentrytype:{element.attrib['subentrytype']}")] += 1
        elif tag in {"seasonword", "count"}:
            output[("usage", tag)] += 1


def load_labels(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    items = value.get("items") if isinstance(value, dict) else value
    if not isinstance(items, list):
        raise ValueError("Wadoku labels must contain an items array")
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Wadoku label item must be an object")
        key = (item.get("category"), item.get("code"))
        if not all((isinstance(key[0], str), isinstance(key[1], str))):
            raise ValueError("Wadoku label requires category and code")
        if key in result:
            raise ValueError(f"duplicate Wadoku label: {key}")
        result[key] = item
    return result


def load_subentry_group_policies(path: Path) -> dict[str, dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("unsupported Wadoku subentry-group policy schema")
    items = value.get("policies")
    if not isinstance(items, list):
        raise ValueError("Wadoku subentry-group policies must be an array")
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Wadoku subentry-group policy must be an object")
        subtype = item.get("subentrytype")
        mode = item.get("decision_mode")
        allowed = item.get("allowed_lookup_policies")
        if not isinstance(subtype, str) or not subtype:
            raise ValueError("Wadoku subentry-group policy needs subentrytype")
        if subtype in result:
            raise ValueError(f"duplicate Wadoku subentry-group policy: {subtype}")
        if mode not in ARTICLE_GROUP_DECISION_MODES:
            raise ValueError(f"invalid decision mode for Wadoku subentrytype {subtype}")
        if (
            not isinstance(allowed, list) or not allowed
            or any(policy not in ARTICLE_LOOKUP_POLICIES for policy in allowed)
            or len(set(allowed)) != len(allowed)
        ):
            raise ValueError(f"invalid lookup policies for Wadoku subentrytype {subtype}")
        if not isinstance(item.get("reason"), str) or not item["reason"]:
            raise ValueError(f"missing reason for Wadoku subentrytype {subtype}")
        result[subtype] = item
    return result


def source_report(
    source: Path, xsd: Path, labels_path: Path,
    subentry_groups_path: Path | None = None,
) -> dict[str, Any]:
    validate_xml(source, xsd)
    counts: Counter[str] = Counter()
    tag_counts: Counter[str] = Counter()
    controlled: Counter[tuple[str, str]] = Counter(
        {("section", code): 1 for code in SECTION_CODES}
    )
    semantic_blocks = translatable_blocks = protected_blocks = 0
    context = ET.iterparse(source, events=("start", "end"))
    _event, root = next(context)
    for event, element in context:
        if event != "end":
            continue
        tag = local_name(element.tag)
        tag_counts[tag] += 1
        if tag in COUNT_TAGS:
            counts[COUNT_TAGS[tag]] += 1
        if tag == "entry":
            _controlled_labels(element, controlled)
            blocks = extract_blocks(element)
            semantic_blocks += len(blocks)
            translatable_blocks += sum(block["has_translatable_text"] for block in blocks)
            protected_blocks += sum(bool(block["protected_fragments"]) for block in blocks)
            element.clear()
            root.clear()
    actual = {key: counts[key] for key in EXPECTED_COUNTS}
    mismatches = {
        key: {"expected": expected, "actual": actual[key]}
        for key, expected in EXPECTED_COUNTS.items() if actual[key] != expected
    }
    labels = load_labels(labels_path)
    missing = [
        {"category": category, "code": code, "occurrences": occurrences}
        for (category, code), occurrences in sorted(controlled.items())
        if (category, code) not in labels
        or not isinstance(labels[(category, code)].get("de"), str)
        or not labels[(category, code)].get("de")
        or not isinstance(labels[(category, code)].get("ru"), str)
        or not labels[(category, code)].get("ru")
        or not isinstance(labels[(category, code)].get("order"), int)
    ]
    observed_subentry_types = {
        code.removeprefix("subentrytype:"): occurrences
        for (category, code), occurrences in controlled.items()
        if category == "reference" and code.startswith("subentrytype:")
    }
    group_policies = (
        load_subentry_group_policies(subentry_groups_path)
        if subentry_groups_path is not None else {}
    )
    missing_subentry_policies = sorted(set(observed_subentry_types) - set(group_policies))
    unused_subentry_policies = sorted(set(group_policies) - set(observed_subentry_types))
    return {
        "schema_version": 1,
        "pipeline": WADOKU_PIPELINE,
        "xsd_valid": True,
        "source_counts": actual,
        "expected_count_mismatches": mismatches,
        "normalized_counts": {
            "canonical_entries": actual["entries"],
            "semantic_blocks": semantic_blocks,
            "translatable_blocks": translatable_blocks,
            "blocks_with_protected_fragments": protected_blocks,
        },
        "tag_counts": dict(sorted(tag_counts.items())),
        "controlled_labels": [
            {"category": category, "code": code, "occurrences": occurrences}
            for (category, code), occurrences in sorted(controlled.items())
        ],
        "missing_controlled_labels": missing,
        "missing_controlled_label_count": len(missing),
        "subentry_types": dict(sorted(observed_subentry_types.items())),
        "missing_subentry_policies": missing_subentry_policies,
        "unused_subentry_policies": unused_subentry_policies,
        "skipped_entries": 0,
        "silently_skipped_learner_text_nodes": 0,
        "unsupported_nodes": [],
    }


def label_catalog(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    catalog = load_labels(path)
    incomplete = [key for key, item in catalog.items() if not all((
        isinstance(item.get("order"), int), isinstance(item.get("de"), str) and item["de"],
        isinstance(item.get("ru"), str) and item["ru"],
    ))]
    if incomplete:
        raise ValueError(f"incomplete Wadoku labels: {incomplete[:5]}")
    return catalog


def _tree_paths(root: dict[str, Any]) -> Iterator[tuple[dict[str, Any], str]]:
    def walk(node: dict[str, Any], path: str) -> Iterator[tuple[dict[str, Any], str]]:
        yield node, path
        totals: Counter[str] = Counter()
        for child in node["children"]:
            totals[child["tag"]] += 1
            yield from walk(child, f"{path}/{child['tag']}[{totals[child['tag']]}]")
    yield from walk(root, "/entry[1]")


def _tree_descendants(node: dict[str, Any], tag: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for child in node["children"]:
        if child["tag"] == tag:
            result.append(child)
        result.extend(_tree_descendants(child, tag))
    return result


def _localized_label(
    labels: dict[tuple[str, str], dict[str, Any]], category: str, code: str,
    language: str, default: str | None = None,
) -> str:
    item = labels.get((category, code))
    if item is None:
        if default is not None:
            return default
        raise ValueError(f"missing Wadoku label: {(category, code)}")
    return str(item[language])


def _span(text: str, language: str | None = None, *, bold: bool = False) -> dict[str, Any]:
    value: dict[str, Any] = {"tag": "span", "content": text}
    if language is not None:
        value["lang"] = language
    if bold:
        value["style"] = {"fontWeight": "bold"}
    return value


def _labeled_div(label: str, content: list[Any]) -> dict[str, Any]:
    return {"tag": "div", "content": [_span(label + ": ", bold=True), *content]}


def _block_spans(block: dict[str, Any], target: str, language: str) -> list[dict[str, Any]]:
    fragments = {item["placeholder"]: item for item in block["protected_fragments"]}
    expected = list(fragments)
    if PLACEHOLDER_RE.findall(target) != expected:
        raise ValueError(f"protected placeholders differ at {block['xml_path']}")
    result: list[dict[str, Any]] = []
    position = 0
    for match in PLACEHOLDER_RE.finditer(target):
        if match.start() > position:
            result.append(_span(target[position:match.start()], language))
        result.append(_span(fragments[match.group()]["text"]))
        position = match.end()
    if position < len(target):
        result.append(_span(target[position:], language))
    return result or [_span("", language)]


def _render_reference(
    node: dict[str, Any], labels: dict[tuple[str, str], dict[str, Any]], language: str,
) -> dict[str, Any]:
    ref_type = node["attributes"].get("type", "")
    key = f"{node['tag']}:type:{ref_type}"
    kind = _localized_label(labels, "reference", key, language, ref_type or node["tag"])
    target = " ".join(filter(None, [
        "#" + node["attributes"].get("id", "") if node["attributes"].get("id") else "",
        *(_plain_tree(item) for item in _tree_descendants(node, "jap")),
        *(_plain_tree(item) for item in _tree_descendants(node, "transcr")),
    ]))
    return _labeled_div(kind, [_span(target, "ja" if _tree_descendants(node, "jap") else None)])


def structured_entry(
    value: dict[str, Any], language: str,
    labels: dict[tuple[str, str], dict[str, Any]],
    targets: dict[int, str] | None = None,
) -> dict[str, Any]:
    if language not in {"de", "ru"}:
        raise ValueError(f"unsupported Wadoku render language: {language}")
    targets = targets or {}
    blocks = value["blocks"]
    block_by_path = {block["xml_path"]: (index, block) for index, block in enumerate(blocks)}
    rendered_block_indices: set[int] = set()
    tree = value["tree"]
    form = next((child for child in tree["children"] if child["tag"] == "form"), None)
    senses = [child for child in tree["children"] if child["tag"] == "sense"]
    if form is None or not senses:
        raise ValueError(f"Wadoku entry {value['entry_id']} lacks form or sense")

    def render_node(node: dict[str, Any], path: str) -> list[Any]:
        selected = block_by_path.get(path)
        if selected is not None:
            index, block = selected
            if index in rendered_block_indices:
                raise ValueError(f"Wadoku block rendered twice: {value['entry_id']}:{index}")
            rendered_block_indices.add(index)
            target = block["prompt_text"] if language == "de" else targets.get(index)
            if target is None:
                if block["has_translatable_text"]:
                    raise ValueError(f"missing Russian target for block {index} of {value['entry_id']}")
                target = block["prompt_text"]
            label = _localized_label(labels, "section", block["role"], language)
            return [_labeled_div(label, _block_spans(block, target, language))]
        tag = node["tag"]
        if tag == "usg":
            usage_type = node["attributes"].get("type", "")
            raw_text = _plain_tree(node)
            code = f"{usage_type}:{raw_text}"
            content = (
                [_span(_localized_label(labels, "usage", code, language), language)]
                if raw_text else []
            )
            return [_labeled_div(
                _localized_label(labels, "usage-category", usage_type, language, usage_type),
                content,
            )]
        if tag in {"ref", "sref"}:
            return [_render_reference(node, labels, language)]
        rendered: list[Any] = []
        if node["text"]:
            rendered.append(_span(node["text"], language))
        totals: Counter[str] = Counter()
        for child in node["children"]:
            totals[child["tag"]] += 1
            child_path = f"{path}/{child['tag']}[{totals[child['tag']]}]"
            rendered.extend(render_node(child, child_path))
            if child["tail"]:
                rendered.append(_span(child["tail"], language))
        return rendered

    orths = _tree_descendants(form, "orth")
    hiras = _tree_descendants(form, "hira")
    hatsuon = _tree_descendants(form, "hatsuon")
    accents = _tree_descendants(form, "accent")
    content: list[Any] = [
        _labeled_div(
            _localized_label(labels, "section", "forms", language),
            [_span(" · ".join(_plain_tree(node) for node in orths), "ja")],
        ),
        _labeled_div(
            _localized_label(labels, "section", "reading", language),
            [_span(" · ".join(_plain_tree(node) for node in hiras), "ja")],
        ),
    ]
    pronunciation = " · ".join([
        *(_plain_tree(node) for node in hatsuon),
        *(f"accent={_plain_tree(node)}" for node in accents),
    ])
    if pronunciation:
        content.append(_labeled_div(
            _localized_label(labels, "section", "pronunciation", language),
            [_span(pronunciation, "ja")],
        ))
    grammar_nodes = [child for child in tree["children"] if child["tag"] == "gramGrp"]
    grammar_codes = [child["tag"] for group in grammar_nodes for child in group["children"]]
    if grammar_codes:
        content.append(_labeled_div(
            _localized_label(labels, "section", "grammar", language),
            [_span(", ".join(_localized_label(labels, "grammar", code, language)
                             for code in grammar_codes), language)],
        ))
    sense_items = []
    for sense_index, sense in enumerate(senses, 1):
        sense_items.append({
            "tag": "li",
            "content": render_node(sense, f"/entry[1]/sense[{sense_index}]") or [_span("")],
        })
    content.append(_labeled_div(
        _localized_label(labels, "section", "senses", language),
        [{"tag": "ol", "content": sense_items}],
    ))
    totals: Counter[str] = Counter()
    for child in tree["children"]:
        totals[child["tag"]] += 1
        if child is form or child in senses or child["tag"] == "gramGrp":
            continue
        path = f"/entry[1]/{child['tag']}[{totals[child['tag']]}]"
        content.extend(render_node(child, path))
    if rendered_block_indices != set(range(len(blocks))):
        missing = sorted(set(range(len(blocks))) - rendered_block_indices)
        raise ValueError(f"Wadoku blocks were not rendered: {value['entry_id']}:{missing[:10]}")
    return {"type": "structured-content", "content": {"tag": "div", "content": content}}


def _entry_forms(value: dict[str, Any]) -> list[dict[str, Any]]:
    orths = _tree_descendants(value["tree"], "orth")
    searchable = [node for node in orths if node["attributes"].get("midashigo") != "true"]
    selected = searchable or orths
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in selected:
        text = _plain_tree(node)
        if text and text not in seen:
            result.append(node)
            seen.add(text)
    return result


def yomitan_inflection_rules(value: dict[str, Any]) -> str:
    """Map only audited Wadoku classes to Yomitan deinflection rules."""
    rules: list[str] = []
    for node in _tree_descendants(value["tree"], "doushi"):
        rule = YOMITAN_INFLECTION_RULES.get(node["attributes"].get("level", ""))
        if rule is not None and rule not in rules:
            rules.append(rule)
    if _tree_descendants(value["tree"], "keiyoushi") and "adj-i" not in rules:
        rules.append("adj-i")
    return " ".join(rules)


def yomitan_rows(
    value: dict[str, Any], language: str,
    labels: dict[tuple[str, str], dict[str, Any]], targets: dict[int, str] | None = None,
) -> tuple[list[list[Any]], list[list[Any]]]:
    _expression, reading, sequence = canonical_identity(value)
    glossary = [structured_entry(value, language, labels, targets)]
    grammar = [child["tag"] for group in _tree_descendants(value["tree"], "gramGrp")
               for child in group["children"]]
    inflection_rules = yomitan_inflection_rules(value)
    accents = []
    for node in _tree_descendants(value["tree"], "accent"):
        text = _plain_tree(node)
        if text.isdigit():
            accents.append({"position": int(text)})
    rows: list[list[Any]] = []
    metadata: list[list[Any]] = []
    for form in _entry_forms(value):
        expression = _plain_tree(form)
        term_tags = [form["attributes"]["type"]] if form["attributes"].get("type") else []
        rows.append([expression, reading, " ".join(dict.fromkeys(grammar)), inflection_rules, 0,
                     glossary, sequence, " ".join(term_tags)])
        if accents:
            metadata.append([expression, "pitch", {"reading": reading, "pitches": accents}])
    return rows, metadata


def tag_rows(
    labels: dict[tuple[str, str], dict[str, Any]], language: str,
) -> list[list[Any]]:
    category_map = {"grammar": "grammar", "usage": "usage",
                    "usage-category": "usage", "orthography": "orthography",
                    "reference": "reference"}
    return [
        [code, category_map[category], item["order"], item[language], 0]
        for (category, code), item in sorted(labels.items(), key=lambda pair: pair[1]["order"])
        if category in category_map
    ]


def _write_zip_member(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    archive.writestr(info, data, compresslevel=9)


def localized_archive_shape(value: Any) -> Any:
    """Remove translated leaf text while retaining the Yomitan structure."""
    if isinstance(value, list):
        return [
            localized_archive_shape(item) for item in value
            if not (
                isinstance(item, dict)
                and item.get("lang") in {"de", "ru"}
                and isinstance(item.get("content"), str)
            )
        ]
    if isinstance(value, dict):
        normalized = {key: localized_archive_shape(item) for key, item in value.items()}
        if value.get("lang") in {"de", "ru"}:
            normalized["lang"] = "<localized>"
            if isinstance(value.get("content"), str):
                normalized["content"] = "<localized>"
        if (
            isinstance(value.get("content"), str)
            and isinstance(value.get("style"), dict)
            and value["style"].get("fontWeight") == "bold"
        ):
            normalized["content"] = "<localized-label>"
        return normalized
    return value


def build_rich_archive(
    entries: Iterator[tuple[dict[str, Any], dict[int, str] | None]], output: Path,
    *, language: str, labels: dict[tuple[str, str], dict[str, Any]], license_text: bytes,
    title: str, revision: str, source_url: str, source_sha256: str,
    export_audit_id: int | str, description_note: str | None = None,
) -> dict[str, Any]:
    index = {
        "title": title, "revision": revision, "format": 3, "sequenced": True,
        "sourceLanguage": "ja", "targetLanguage": language, "url": source_url,
        "attribution": "Wadoku.de; see bundled LICENSE and https://www.wadoku.de/wiki/display/WAD/Wadoku.de-Lizenz",
        "description": (
            f"Official Wadoku source 2026-07-05; archive SHA-256 {source_sha256}; "
            f"pipeline {WADOKU_PIPELINE}; edition {revision}; PostgreSQL export audit {export_audit_id}."
            + (f" {description_note}" if description_note else "")
        ),
    }
    tags = tag_rows(labels, language)
    output.parent.mkdir(parents=True, exist_ok=True)
    file_hashes: dict[str, str] = {}
    file_bytes: dict[str, int] = {}
    entry_count = term_count = meta_count = 0
    term_bank_number = meta_bank_number = 0
    term_buffer: list[list[Any]] = []
    meta_buffer: list[list[Any]] = []

    def write(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
        _write_zip_member(archive, name, data)
        file_hashes[name] = sha256_bytes(data)
        file_bytes[name] = len(data)

    with zipfile.ZipFile(output, "w") as archive:
        write(archive, "index.json", canonical_json(index))
        write(archive, "LICENSE", license_text)
        for tag_bank_number, offset in enumerate(range(0, len(tags), 10_000), 1):
            write(archive, f"tag_bank_{tag_bank_number}.json",
                  canonical_json(tags[offset:offset + 10_000]))
        for value, targets in entries:
            rows, metadata = yomitan_rows(value, language, labels, targets)
            term_buffer.extend(rows)
            meta_buffer.extend(metadata)
            entry_count += 1
            term_count += len(rows)
            meta_count += len(metadata)
            while len(term_buffer) >= 10_000:
                term_bank_number += 1
                write(archive, f"term_bank_{term_bank_number}.json",
                      canonical_json(term_buffer[:10_000]))
                del term_buffer[:10_000]
            while len(meta_buffer) >= 10_000:
                meta_bank_number += 1
                write(archive, f"term_meta_bank_{meta_bank_number}.json",
                      canonical_json(meta_buffer[:10_000]))
                del meta_buffer[:10_000]
        if term_buffer:
            term_bank_number += 1
            write(archive, f"term_bank_{term_bank_number}.json", canonical_json(term_buffer))
        if meta_buffer:
            meta_bank_number += 1
            write(archive, f"term_meta_bank_{meta_bank_number}.json", canonical_json(meta_buffer))
    if term_count == 0:
        raise ValueError("Wadoku rich archive has no term rows")
    return {
        "entries": entry_count, "term_rows": term_count, "term_meta_rows": meta_count,
        "term_banks": term_bank_number, "term_meta_banks": meta_bank_number,
        "tag_rows": len(tags), "files": file_hashes, "file_bytes": file_bytes,
        "zip_sha256": sha256_file(output),
    }
