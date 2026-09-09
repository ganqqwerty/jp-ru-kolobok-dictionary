#!/usr/bin/env python3
"""Build and run the 150-entry Wadoku XML quality pilot without PostgreSQL."""

from __future__ import annotations

import argparse
import copy
import hashlib
import heapq
import html
import json
import random
import re
import secrets
import sqlite3
import subprocess
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from jitendex_ru.batch import _manifest, _pack_envelopes
from jitendex_ru.import_kaishi import _collection_from_apkg, split_fields
from jitendex_ru.schema_validation import validate_archive
from jitendex_ru.util import atomic_write, canonical_json, sha256_bytes, sha256_file
from jitendex_ru.validate_response import wadoku_target_issues
from jitendex_ru.wadoku_xml import (
    WADOKU_ARCHIVE_SHA256,
    _plain_tree,
    _tree_descendants,
    build_rich_archive,
    canonical_identity,
    iter_canonical_entries,
    label_catalog,
    translation_unit_id,
    yomitan_rows,
    sense_translation_entry,
    reference_target_index,
)
from jitendex_ru.wadoku_quality import pronunciation_groups


SOURCE = Path("work/wadoku-xml/source/wadoku-xml-20260705/wadoku.xml")
LICENSE = Path("work/wadoku-xml/source/wadoku-xml-20260705/LICENSE")
KAISHI = Path("work/downloads/kaishi-1.5k-v2.4.1.apkg")
OUTPUT = Path("work/wadoku-xml/pilot-v4")
EXPORT_OUTPUT = Path("work/wadoku-xml/pilot-v4")
SCHEMA_DIR = Path("schemas/yomitan-77e200428902abf4fa48284df92da7af3dcb4162")
PROMPT = Path("prompts/translate_luna_wadoku_xml_ru_v3.txt")
LABELS = Path("terminology/wadoku-xml-labels-v2.json")
MODEL = "gpt-5.6-luna"
CODEX = Path("/Applications/ChatGPT.app/Contents/Resources/codex")

NAME_IDS = {1942, 4839, 11834, 14127, 161559, 202992, 214381, 16981, 18669, 63370}
GRAMMAR_IDS = {
    714571, 761205, 1276992, 1823533, 2343439, 3398440, 4290949, 4727607,
    5030190, 5127541, 6656159, 7629172, 8031579, 8708141, 9721719,
}
ELLIPSIS_IDS = {906, 1102, 1119, 2233, 5908, 15465, 22898, 34262, 34981, 39133, 44597, 64967}
FIXED_IDS = {15, 44, 52, 9858285, 10084606}
FIXED_EXPRESSIONS = {"バーン･ジョーンズ", "暴食", "三ケ日人骨"}
GRAMMAR_EXPRESSIONS = {"の", "は", "が", "を", "に", "で", "と", "も", "へ", "か", "ね", "よ", "より"}
PLACEHOLDER_RE = re.compile(r"⟦WDXP\d{4}⟧")
ELLIPSIS_RE = re.compile(r"^[…~〜～]+")


def _kaishi_notes(path: Path) -> list[dict[str, str]]:
    with tempfile.TemporaryDirectory(prefix="wadoku-pilot-kaishi-") as directory:
        collection = _collection_from_apkg(path, Path(directory))
        connection = sqlite3.connect(collection)
        connection.row_factory = sqlite3.Row
        field_names: dict[int, list[str]] = {}
        for row in connection.execute("SELECT ntid,ord,name FROM fields ORDER BY ntid,ord"):
            field_names.setdefault(row["ntid"], []).append(row["name"])
        notes = []
        for row in connection.execute("SELECT id,mid,flds FROM notes ORDER BY id"):
            fields = split_fields(row["flds"], field_names[row["mid"]])
            word = re.sub(r"<[^>]+>", "", fields.get("Word", "")).strip()
            reading = fields.get("Word Reading", "").strip()
            if word and reading:
                notes.append({
                    "word": word, "reading": reading,
                    "meaning_en": fields.get("Word Meaning", ""),
                    "sentence_ja": fields.get("Sentence", ""),
                    "sentence_en": fields.get("Sentence Meaning", ""),
                })
        connection.close()
        return notes


def _grammar(value: dict[str, Any]) -> list[str]:
    return list(dict.fromkeys(
        child["tag"] for group in _tree_descendants(value["tree"], "gramGrp")
        for child in group["children"]
    ))


def _forms(value: dict[str, Any]) -> list[str]:
    return list(dict.fromkeys(_plain_tree(node) for node in _tree_descendants(value["tree"], "orth")))


def _usage(value: dict[str, Any]) -> list[str]:
    return [_plain_tree(node) for node in _tree_descendants(value["tree"], "usg")]


def _feature_tags(value: dict[str, Any]) -> list[str]:
    tags: list[str] = []
    if len(_tree_descendants(value["tree"], "sense")) >= 3:
        tags.append("several-senses")
    if len(_forms(value)) >= 3:
        tags.append("several-forms")
    if _tree_descendants(value["tree"], "ref"):
        tags.append("references")
    if _tree_descendants(value["tree"], "usg"):
        tags.append("usage-domain")
    if _tree_descendants(value["tree"], "etym"):
        tags.append("etymology")
    if any(block["protected_fragments"] for block in value["blocks"]):
        tags.append("protected-inline")
    if len(_tree_descendants(value["tree"], "accent")) >= 2:
        tags.append("multiple-accents")
    if any("[Dev]" in _plain_tree(node) for node in _tree_descendants(value["tree"], "hatsuon")):
        tags.append("devoicing")
    if len(value["blocks"]) >= 8:
        tags.append("many-blocks")
    if not any(block["has_translatable_text"] for block in value["blocks"]):
        tags.append("no-translatable-text")
    return tags


def _selection_record(ordinal: int, value: dict[str, Any], categories: list[str], note: dict[str, str] | None = None) -> dict[str, Any]:
    expression, reading, sequence = canonical_identity(value)
    return {
        "ordinal": ordinal, "entry_id": sequence, "expression": expression, "reading": reading,
        "categories": sorted(set(categories)), "features": _feature_tags(value),
        "source_sha256": sha256_bytes(canonical_json(value)), "kaishi_evidence": note,
    }


def select(
    source: Path,
    kaishi: Path,
    output: Path,
    *,
    seed: int,
    excluded_entry_ids: set[int],
) -> dict[str, Any]:
    notes = _kaishi_notes(kaishi)
    wanted_words = {note["word"] for note in notes}
    note_by_key: dict[tuple[str, str], list[tuple[int, dict[str, str]]]] = {}
    for index, note in enumerate(notes):
        for reading in re.split(r"[・･/]", note["reading"]):
            note_by_key.setdefault((note["word"], reading), []).append((index, note))

    rng = random.Random(seed)
    random_reservoir: list[tuple[int, dict[str, Any]]] = []
    risk_reservoir: list[tuple[int, dict[str, Any]]] = []
    random_seen = 0
    risk_seen = 0

    def keep_random(
        reservoir: list[tuple[int, dict[str, Any]]],
        seen: int,
        item: tuple[int, dict[str, Any]],
        limit: int,
    ) -> None:
        if len(reservoir) < limit:
            reservoir.append(item)
            return
        replacement = rng.randrange(seen)
        if replacement < limit:
            reservoir[replacement] = item

    for ordinal, value in iter_canonical_entries(source):
        expression, reading, entry_id = canonical_identity(value)
        if entry_id in excluded_entry_ids:
            continue
        size = len(canonical_json(value))
        translatable = sum(block["has_translatable_text"] for block in value["blocks"])
        if not 1 <= translatable <= 80 or size > 45_000:
            continue
        random_seen += 1
        keep_random(random_reservoir, random_seen, (ordinal, value), 300)
        if _feature_tags(value):
            risk_seen += 1
            keep_random(risk_reservoir, risk_seen, (ordinal, value), 300)

    rng.shuffle(random_reservoir)
    rng.shuffle(risk_reservoir)
    chosen: dict[int, tuple[int, dict[str, Any], list[str], dict[str, str] | None]] = {}

    def add(ordinal: int, value: dict[str, Any], category: str) -> None:
        if value["entry_id"] not in chosen:
            chosen[value["entry_id"]] = (ordinal, value, [category], None)

    for ordinal, value in random_reservoir:
        add(ordinal, value, "random-corpus")
        if len(chosen) == 105:
            break
    for ordinal, value in risk_reservoir:
        add(ordinal, value, "random-structural-risk")
        if len(chosen) == 150:
            break
    if len(chosen) != 150:
        raise ValueError(f"randomized pilot selection has {len(chosen)} entries, expected 150")

    records = [_selection_record(ordinal, value, categories, note)
               for ordinal, value, categories, note in sorted(chosen.values())]
    counts: dict[str, int] = {}
    for record in records:
        for category in record["categories"]:
            counts[category] = counts.get(category, 0) + 1
    payload = {
        "schema_version": 2,
        "selection_method": "seeded-reservoir-random-v1",
        "random_seed": str(seed),
        "excluded_entry_count": len(excluded_entry_ids),
        "eligible_entry_count": random_seen,
        "structural_risk_entry_count": risk_seen,
        "source_sha256": sha256_file(source),
        "entries": records,
        "entry_count": len(records),
        "category_counts": dict(sorted(counts.items())),
        "selection_sha256": sha256_bytes(canonical_json(records)),
    }
    atomic_write(output, canonical_json(payload) + b"\n")
    return payload


def select_legacy(source: Path, kaishi: Path, output: Path) -> dict[str, Any]:
    """Preserve the fixed pilot-selection implementation for old run provenance."""
    notes = _kaishi_notes(kaishi)
    wanted_words = {note["word"] for note in notes}
    note_by_key: dict[tuple[str, str], list[tuple[int, dict[str, str]]]] = {}
    for index, note in enumerate(notes):
        for reading in re.split(r"[・･/]", note["reading"]):
            note_by_key.setdefault((note["word"], reading), []).append((index, note))

    fixed: dict[int, tuple[int, dict[str, Any]]] = {}
    fixed_expression: dict[str, tuple[int, dict[str, Any]]] = {}
    kaishi_matches: dict[tuple[str, str], list[tuple[int, dict[str, Any]]]] = {}
    risk_heap: list[tuple[int, str, int, dict[str, Any]]] = []
    random_heap: list[tuple[int, int, dict[str, Any]]] = []
    all_fixed_ids = NAME_IDS | GRAMMAR_IDS | ELLIPSIS_IDS | FIXED_IDS

    for ordinal, value in iter_canonical_entries(source):
        expression, reading, entry_id = canonical_identity(value)
        forms = _forms(value)
        if entry_id in all_fixed_ids:
            fixed[entry_id] = (ordinal, value)
        if expression in FIXED_EXPRESSIONS or any(form in FIXED_EXPRESSIONS for form in forms):
            fixed_expression[expression] = (ordinal, value)
        for form in forms:
            if form in wanted_words and (form, reading) in note_by_key:
                kaishi_matches.setdefault((form, reading), []).append((ordinal, value))
        size = len(canonical_json(value))
        translatable = sum(block["has_translatable_text"] for block in value["blocks"])
        if translatable <= 80 and size <= 45_000 and not any("…" in form for form in forms):
            features = _feature_tags(value)
            score = len(features) * 20 + min(len(value["blocks"]), 15)
            tie = hashlib.sha256(f"risk:{entry_id}".encode()).hexdigest()
            item = (score, tie, ordinal, value)
            if len(risk_heap) < 120:
                heapq.heappush(risk_heap, item)
            elif item[:2] > risk_heap[0][:2]:
                heapq.heapreplace(risk_heap, item)
            random_key = int(hashlib.sha256(f"{WADOKU_ARCHIVE_SHA256}:{entry_id}".encode()).hexdigest(), 16)
            random_item = (-random_key, ordinal, value)
            if len(random_heap) < 400:
                heapq.heappush(random_heap, random_item)
            elif random_item[0] > random_heap[0][0]:
                heapq.heapreplace(random_heap, random_item)

    missing = sorted(all_fixed_ids - set(fixed))
    if missing:
        raise ValueError(f"fixed pilot IDs missing from source: {missing}")
    for expression in FIXED_EXPRESSIONS:
        if not any(expression in _forms(value) for _ordinal, value in fixed_expression.values()):
            raise ValueError(f"fixed pilot expression missing: {expression}")

    chosen: dict[int, tuple[int, dict[str, Any], list[str], dict[str, str] | None]] = {}
    def add(ordinal: int, value: dict[str, Any], category: str, note: dict[str, str] | None = None) -> None:
        entry_id = value["entry_id"]
        if entry_id in chosen:
            chosen[entry_id][2].append(category)
        else:
            chosen[entry_id] = (ordinal, value, [category], note)

    unique_kaishi = []
    for key, candidates in kaishi_matches.items():
        if len(candidates) != 1 or key[0] in GRAMMAR_EXPRESSIONS:
            continue
        index, note = note_by_key[key][0]
        unique_kaishi.append((index, candidates[0][0], candidates[0][1], note))
    unique_kaishi.sort(key=lambda item: item[0])
    if len(unique_kaishi) < 75:
        raise ValueError(f"only {len(unique_kaishi)} unambiguous Kaishi matches")
    positions = [round(index * (len(unique_kaishi) - 1) / 74) for index in range(75)]
    for position in positions:
        _index, ordinal, value, note = unique_kaishi[position]
        add(ordinal, value, "kaishi", note)

    for entry_id in sorted(NAME_IDS):
        add(*fixed[entry_id], "proper-name")
    for entry_id in sorted(GRAMMAR_IDS):
        add(*fixed[entry_id], "grammar")
    for entry_id in sorted(ELLIPSIS_IDS):
        add(*fixed[entry_id], "ellipsis")
    for entry_id in sorted(FIXED_IDS):
        add(*fixed[entry_id], "fixed-sample")
    for _expression, pair in sorted(fixed_expression.items()):
        add(*pair, "fixed-sample")

    random_candidates = sorted(
        ((-key, ordinal, value) for key, ordinal, value in random_heap), key=lambda item: item[0]
    )
    holdout_added = 0
    for _key, ordinal, value in random_candidates:
        if value["entry_id"] not in chosen:
            add(ordinal, value, "random-holdout")
            holdout_added += 1
            if holdout_added == 15:
                break
    if holdout_added != 15:
        raise ValueError("could not select 15 random holdouts")

    for _score, _tie, ordinal, value in sorted(risk_heap, reverse=True):
        if len(chosen) >= 150:
            break
        if value["entry_id"] not in chosen:
            add(ordinal, value, "structural-risk")
    if len(chosen) != 150:
        raise ValueError(f"pilot selection has {len(chosen)} entries, expected 150")

    records = [_selection_record(ordinal, value, categories, note)
               for ordinal, value, categories, note in sorted(chosen.values())]
    counts: dict[str, int] = {}
    for record in records:
        for category in record["categories"]:
            counts[category] = counts.get(category, 0) + 1
    payload = {
        "schema_version": 1, "source_sha256": sha256_file(source), "entries": records,
        "entry_count": len(records), "category_counts": dict(sorted(counts.items())),
        "selection_sha256": sha256_bytes(canonical_json(records)),
    }
    atomic_write(output, canonical_json(payload) + b"\n")
    return payload


def _selected_entries(source: Path, selection: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    record_by_id = {item["entry_id"]: item for item in selection["entries"]}
    result = []
    for _ordinal, value in iter_canonical_entries(source):
        record = record_by_id.get(value["entry_id"])
        if record is not None:
            if sha256_bytes(canonical_json(value)) != record["source_sha256"]:
                raise ValueError("selected source changed")
            result.append((sense_translation_entry(value), record))
    if len(result) != 150:
        raise ValueError(f"loaded {len(result)} selected entries")
    return result


def _envelope(value: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    expression, reading, sequence = canonical_identity(value)
    grammar = _grammar(value)
    blocks = value["blocks"]
    units = []
    for index, block in enumerate(blocks):
        if not block["has_translatable_text"]:
            continue
        source_hash = sha256_bytes(block["prompt_text"].encode())
        units.append({
            "unit_id": translation_unit_id(WADOKU_ARCHIVE_SHA256, value["entry_id"], block),
            "source_sha256": source_hash, "role": block["role"],
            "source_text": block["prompt_text"],
            "protected_tokens": [item["placeholder"] for item in block["protected_fragments"]],
            "protected_fragment_context": [
                {"token": item["placeholder"], "tree": item["tree"]}
                for item in block["protected_fragments"]
            ],
            "local_context": {
                "unit_role": block["role"], "xml_path": block["xml_path"],
                "sense_path": block["sense_path"],
                "nearby_source_blocks": [item["source_text"] for item in blocks[max(0, index - 2):index + 3]],
            },
        })
    return {
        "article_id": f"wdx-{sequence}", "source_sha256": record["source_sha256"],
        "term": expression, "reading": reading, "sequence": sequence,
        "read_only_context": {
            "dictionary": "Wadoku", "entry_id": sequence, "expression": expression,
            "source_forms": _forms(value), "lookup_aliases": [
                ELLIPSIS_RE.sub("", form) for form in _forms(value) if ELLIPSIS_RE.match(form)
            ],
            "reading": reading, "entry_grammar": grammar,
            "usage": _usage(value),
            "complete_source_blocks": [{"role": b["role"], "sense_path": b["sense_path"], "text": b["source_text"]} for b in blocks],
            "article_group_id": sequence, "article_policy": "independent",
            "lookup_policy": "independent_direct", "source_entry_role": "primary",
            "kaishi_evidence": record.get("kaishi_evidence"),
            "preservation_rule": "Protected tokens and XML structure are immutable.",
        },
        "units": units,
    }


def prepare_batches(source: Path, selection_path: Path, batch_dir: Path) -> dict[str, Any]:
    if any(batch_dir.glob("wadoku-pilot-*.json")):
        raise ValueError("Refusing to overwrite frozen manifests; choose a new --work-dir")
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    envelopes = [_envelope(value, record) for value, record in _selected_entries(source, selection)]
    active = [envelope for envelope in envelopes if envelope["units"]]
    groups = _pack_envelopes(active, {}, 18, 24_576, 80, 16_384, 49_152, 200, True)
    batch_dir.mkdir(parents=True, exist_ok=True)
    batches = []
    for index, group in enumerate(groups, 1):
        batch_id = f"wadoku-pilot-{index:03d}"
        manifest, data = _manifest(batch_id, group, {})
        path = batch_dir / f"{batch_id}.json"
        atomic_write(path, data + b"\n")
        batches.append({
            "batch_id": batch_id, "path": str(path), "articles": len(group),
            "units": sum(len(item["units"]) for item in group), "bytes": len(data),
            "manifest_sha256": manifest["manifest_sha256"],
        })
    report = {"batch_count": len(batches), "batches": batches,
              "articles": len(active), "units": sum(item["units"] for item in batches)}
    atomic_write(batch_dir.parent / "batch-report.json", canonical_json(report) + b"\n")
    return report


def _dispatch(manifest_path: Path, response_path: Path, prompt: str) -> dict[str, Any]:
    from run_codex_batches import DispatchResult, build_output_schema, parse_events

    manifest_text = manifest_path.read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".json", delete=False) as schema:
        json.dump(build_output_schema(manifest, "translation"), schema, ensure_ascii=False)
        schema_path = Path(schema.name)
    command = [
        str(CODEX), "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules",
        "--skip-git-repo-check", "-s", "read-only", "-C", "/private/tmp", "-m", MODEL,
        "-c", 'model_reasoning_effort="medium"', "--output-schema", str(schema_path),
        "--json", "-o", str(response_path.resolve()), "-",
    ]
    try:
        completed = subprocess.run(
            command, input=f"{prompt.rstrip()}\n\nSUPPLIED BATCH\n{manifest_text}", text=True,
            capture_output=True, timeout=360, check=False,
        )
    finally:
        schema_path.unlink(missing_ok=True)
    thread_id, usage = parse_events(completed.stdout)
    if completed.returncode or usage is None or not response_path.is_file():
        raise RuntimeError((completed.stderr or completed.stdout)[-4000:])
    result = {"batch_id": manifest["batch_id"], "thread_id": thread_id, "usage": usage}
    atomic_write(response_path.with_suffix(".usage.json"), canonical_json(result))
    return result


def translate(batch_dir: Path, response_dir: Path, prompt_path: Path, concurrency: int) -> dict[str, Any]:
    response_dir.mkdir(parents=True, exist_ok=True)
    prompt = prompt_path.read_text(encoding="utf-8")
    prompt_hash = sha256_bytes(prompt.encode())
    provenance_path = response_dir / "prompt-provenance.json"
    if provenance_path.exists():
        if json.loads(provenance_path.read_text())["sha256"] != prompt_hash:
            raise ValueError("Prompt changed; use a new --work-dir")
    elif any(response_dir.glob("wadoku-pilot-*.json")):
        raise ValueError("Existing responses lack prompt provenance; do not mix them with a new run")
    else:
        atomic_write(provenance_path, canonical_json({"path": str(prompt_path), "sha256": prompt_hash}))
    manifests = sorted(batch_dir.glob("wadoku-pilot-*.json"))
    results = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {
            executor.submit(_dispatch, path, response_dir / path.name, prompt): path
            for path in manifests if not (response_dir / path.name).is_file()
        }
        for future in as_completed(futures):
            results.append(future.result())
    for path in manifests:
        if not any(item["batch_id"] == path.stem for item in results):
            results.append(json.loads((response_dir / path.name).with_suffix(".usage.json").read_text()))
    report = {"model": MODEL, "concurrency": concurrency, "workers": len(manifests), "results": sorted(results, key=lambda x: x["batch_id"])}
    atomic_write(response_dir.parent / "translation-run.json", canonical_json(report) + b"\n")
    return report


def _validated_targets(batch_dir: Path, response_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    targets: dict[str, Any] = {}
    issues: list[dict[str, Any]] = []
    for manifest_path in sorted(batch_dir.glob("wadoku-pilot-*.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        response = json.loads((response_dir / manifest_path.name).read_text(encoding="utf-8"))
        expected = [unit for article in manifest["articles"] for unit in article["units"]]
        actual = response.get("translations", [])
        if response.get("batch_id") != manifest["batch_id"] or response.get("manifest_sha256") != manifest["manifest_sha256"]:
            issues.append({"batch_id": manifest["batch_id"], "code": "response-identity-mismatch"})
            continue
        if [item.get("unit_id") for item in actual] != [item["unit_id"] for item in expected]:
            issues.append({"batch_id": manifest["batch_id"], "code": "unit-order-or-set-mismatch"})
            continue
        for unit, item in zip(expected, actual, strict=True):
            target = item.get("target_text")
            if item.get("source_sha256") != unit["source_sha256"]:
                issues.append({"unit_id": unit["unit_id"], "code": "source-hash-mismatch"})
            if unit["role"] == "glossary_set":
                if (not isinstance(target, list) or not 1 <= len(target) <= 12
                        or not all(isinstance(t, str) and t.strip() for t in target)
                        or len({t.casefold().strip() for t in target}) != len(target)):
                    issues.append({"unit_id": unit["unit_id"], "code": "invalid-glossary-array"})
                    continue
                for gloss in target:
                    source_members = json.loads(unit["source_text"])
                    source_evidence = gloss if gloss in source_members else unit["source_text"]
                    issues.extend(wadoku_target_issues(source_evidence, gloss, [], unit["unit_id"]))
                targets[unit["unit_id"]] = target
                continue
            if not isinstance(target, str) or not target.strip():
                issues.append({"unit_id": unit["unit_id"], "code": "invalid-target-type"})
                continue
            issues.extend(wadoku_target_issues(
                unit["source_text"], target, unit["protected_tokens"], unit["unit_id"]
            ))
            targets[unit["unit_id"]] = target
    return targets, issues


def _pilot_rows(value: dict[str, Any], labels: dict[tuple[str, str], dict[str, Any]], targets: dict[int, str]):
    pilot_value = value
    _expression, reading, _sequence = canonical_identity(value)
    pilot_limitations: list[dict[str, Any]] = []
    if ELLIPSIS_RE.match(reading):
        pilot_value = copy.deepcopy(value)
        for node in _tree_descendants(pilot_value["tree"], "hira"):
            node["text"] = ELLIPSIS_RE.sub("", node["text"])
        pilot_limitations.append({
            "code": "template_reading_normalized_for_suffix_lookup",
            "source_reading": reading,
            "lookup_reading": ELLIPSIS_RE.sub("", reading),
        })
    pronunciation = pronunciation_groups(pilot_value)
    rows, metadata = yomitan_rows(pilot_value, "ru", labels, targets)
    clean_rows = []
    aliases = []
    for row in rows:
        expression, reading = row[0], row[1]
        if ELLIPSIS_RE.match(expression):
            expression = ELLIPSIS_RE.sub("", expression)
            reading = ELLIPSIS_RE.sub("", reading)
            aliases.append(expression)
        if any(mark in expression or mark in reading for mark in "…~〜～"):
            continue
        clean = list(row)
        clean[0], clean[1] = expression, reading
        clean_rows.append(clean)
    clean_meta = []
    for item in metadata:
        expression = ELLIPSIS_RE.sub("", item[0])
        if expression != item[0]:
            # A pitch measured on an open template cannot be assigned to a shortened alias.
            continue
        if not any(mark in expression for mark in "…~〜～"):
            clean_meta.append([expression, *item[1:]])
    if not clean_rows:
        raise ValueError(f"entry {value['entry_id']} has no placeholder-free lookup row")
    return clean_rows, clean_meta, aliases, [
        *pilot_limitations, *pronunciation.get("limitations", []),
    ]


def export(
    source: Path,
    selection_path: Path,
    batch_dir: Path,
    response_dir: Path,
    output: Path,
    *,
    pilot_number: int,
    pilot_date: str,
    editorial_path: Path | None,
) -> dict[str, Any]:
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    unit_targets, issues = _validated_targets(batch_dir, response_dir)
    units = {u['unit_id']: u for p in batch_dir.glob('wadoku-pilot-*.json')
             for a in json.loads(p.read_text())['articles'] for u in a['units']}
    editorial_corrections = 0
    if editorial_path is not None:
        editorial = json.loads(editorial_path.read_text())
        for correction in editorial['corrections']:
            unit = units.get(correction['unit_id'])
            if unit is None:
                continue
            if correction['source_sha256'] != unit['source_sha256']:
                raise ValueError('editorial correction source hash differs')
            targets = correction['target_text']
            target_values = targets if isinstance(targets, list) else [targets]
            for gloss in target_values:
                issues.extend(wadoku_target_issues(unit['source_text'], gloss, [], unit['unit_id']))
            unit_targets[unit['unit_id']] = targets
            editorial_corrections += 1
    atomic_write(output.parent / "validation-issues.json", canonical_json(issues) + b"\n")
    if issues:
        raise ValueError(f"translation validation found {len(issues)} issues")
    labels = label_catalog(LABELS)
    reference_targets = reference_target_index(source)
    prepared = []
    aliases: dict[str, list[str]] = {}
    pronunciation_limitations: list[dict[str, Any]] = []
    for value, _record in _selected_entries(source, selection):
        value = {**value, "reference_targets": reference_targets}
        block_targets = {}
        for index, block in enumerate(value["blocks"]):
            if block["has_translatable_text"]:
                unit_id = translation_unit_id(WADOKU_ARCHIVE_SHA256, value["entry_id"], block)
                block_targets[index] = unit_targets[unit_id]
        rows, metadata, entry_aliases, entry_pronunciation_limitations = _pilot_rows(
            value, labels, block_targets
        )
        aliases[str(value["entry_id"])] = entry_aliases
        pronunciation_limitations.extend({"entry_id": value["entry_id"], **item}
                                          for item in entry_pronunciation_limitations)
        prepared.append((value, block_targets, rows, metadata))

    def entries():
        for value, block_targets, _rows, _meta in prepared:
            yield value, block_targets

    rows_by_id = {value["entry_id"]: (rows, meta) for value, _targets, rows, meta in prepared}

    report = build_rich_archive(
        entries(), output, language="ru", labels=labels, license_text=LICENSE.read_bytes(),
        title=f"Wadoku RU · пилот {pilot_number} · {pilot_date}",
        revision=f"{pilot_date.replace('-', '.')}-wadoku-rich-ru-pilot{pilot_number}-random150-v4",
        source_url="https://www.wadoku.de/", source_sha256=WADOKU_ARCHIVE_SHA256,
        export_audit_id=f"standalone-pilot-{pilot_number}",
        description_note=f"Пилот {pilot_number} от {pilot_date}: новая случайная выборка из 150 статей.",
        row_factory=lambda value, *_args: rows_by_id[value["entry_id"]],
    )
    schema = validate_archive(output, SCHEMA_DIR)
    with zipfile.ZipFile(output) as archive:
        term_rows = [row for name in archive.namelist() if name.startswith("term_bank_")
                     for row in json.loads(archive.read(name))]
    literal_templates = sum(any(mark in row[0] or mark in row[1] for mark in "…~〜～") for row in term_rows)
    if report["entries"] != 150 or literal_templates:
        raise ValueError(f"pilot export gate failed: entries={report['entries']} templates={literal_templates}")
    run_report = json.loads((response_dir.parent / "translation-run.json").read_text(encoding="utf-8"))
    batch_report = json.loads((batch_dir.parent / "batch-report.json").read_text(encoding="utf-8"))
    usage_rows = [item.get("usage", {}) for item in run_report["results"]]
    confidence_counts: dict[str, int] = {}
    for response_path in response_dir.glob("wadoku-pilot-*.json"):
        if response_path.name.endswith(".usage.json"):
            continue
        response = json.loads(response_path.read_text(encoding="utf-8"))
        for item in response.get("translations", []):
            confidence = item.get("confidence", "missing")
            confidence_counts[confidence] = confidence_counts.get(confidence, 0) + 1
    result = {
        **report, **schema, "literal_template_lookup_rows": literal_templates,
        "lookup_aliases": aliases, "archive": str(output.resolve()),
        "selection_sha256": selection["selection_sha256"],
        "pilot_number": pilot_number, "pilot_date": pilot_date,
        "random_seed": selection.get("random_seed"),
        "editorial_corrections": editorial_corrections,
        "pronunciation_limitations": pronunciation_limitations,
        "category_counts": selection["category_counts"],
        "translation": {
            "model": run_report["model"], "concurrency": run_report["concurrency"],
            "requests": len(run_report["results"]), "units": batch_report["units"],
            "input_tokens": sum(item.get("input_tokens", 0) for item in usage_rows),
            "output_tokens": sum(item.get("output_tokens", 0) for item in usage_rows),
            "reasoning_tokens": sum(item.get("reasoning_output_tokens", 0) for item in usage_rows),
            "confidence_counts": dict(sorted(confidence_counts.items())),
        },
    }
    atomic_write(output.parent / "pilot-result.json", canonical_json(result) + b"\n")
    return result


def build_site(
    selection_path: Path,
    archive: Path,
    site_dir: Path,
    *,
    pilot_number: int,
    pilot_date: str,
) -> dict[str, Any]:
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    static_dir = site_dir / "dist"
    static_dir.mkdir(parents=True, exist_ok=True)
    destination = static_dir / archive.name
    destination.write_bytes(archive.read_bytes())
    by_category: dict[str, list[dict[str, Any]]] = {}
    for entry in selection["entries"]:
        for category in entry["categories"]:
            by_category.setdefault(category, []).append(entry)
    labels = {
        "kaishi": "Kaishi: базовая лексика", "proper-name": "Имена и названия",
        "grammar": "Грамматика и служебные слова", "ellipsis": "Шаблоны и суффиксный поиск",
        "fixed-sample": "Фиксированные сложные примеры", "structural-risk": "Структурные риски",
        "random-holdout": "Случайная контрольная группа",
        "random-corpus": "Случайная выборка из корпуса",
        "random-structural-risk": "Случайная выборка сложных структур",
    }
    sections = []
    for key in labels:
        entries = by_category.get(key, [])
        words = html.escape("　".join(entry["expression"].lstrip("…~〜～") for entry in entries))
        sections.append(f'<section><h2>{labels[key]} <small>{len(entries)}</small></h2><p class="scan" lang="ja">{words}</p></section>')
    sentences = [re.sub(r'<[^>]+>', '', entry['kaishi_evidence']['sentence_ja'])
                 for entry in selection['entries'] if entry.get('kaishi_evidence')]
    sections.append('<section><h2>Слова в предложениях Kaishi</h2>' + ''.join(
        f'<p class="scan" lang="ja">{html.escape(sentence)}</p>' for sentence in sentences) + '</section>')
    sections.append('<section><h2>Спряжение и границы выражений</h2><p class="scan" lang="ja">知らなかった。知らない人が来た。見なかった。待っていました。素晴らしかった。</p><p>Проверяйте поиск с начала слова. Для шаблонов с пропуском сканируйте фиксированную часть выражения; она не покрывает все возможные начала фразы.</p></section>')
    def render_preview(node):
        if isinstance(node, str):
            return html.escape(node)
        if isinstance(node, list):
            return ''.join(render_preview(item) for item in node)
        if isinstance(node, dict):
            tag = node.get('tag', 'div')
            if tag not in {'div', 'span', 'ol', 'ul', 'li', 'ruby', 'rt'}:
                tag = 'span'
            return f'<{tag}>' + render_preview(node.get('content', '')) + f'</{tag}>'
        return ''
    with zipfile.ZipFile(archive) as zipped:
        rows = [row for name in zipped.namelist() if name.startswith('term_bank_')
                for row in json.loads(zipped.read(name))]
    unique = {}
    for row in rows:
        unique.setdefault(row[6], row)
    featured = [627246, 4797765, 1823533, 2849163, 202992, 10084606]
    review_rows = sorted(unique.values(), key=lambda row: (row[6] not in featured, row[6]))
    sections.insert(0, '<section><h2>Предпросмотр всех 150 статей</h2><p>Текст взят из нового ZIP. Настоящий вид всплывающего окна проверьте в Yomitan.</p>' + ''.join(
        '<details' + (' open' if row[6] in featured else '') + f'><summary><span lang="ja">{html.escape(row[0])}</span> · {html.escape(row[1])} · {html.escape(row[2])}</summary>'
        + render_preview(row[5]) + '</details>' for row in review_rows) + '</section>')
    page_html = f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Wadoku pilot {pilot_number} — Yomitan test</title><style>
body{{font:17px/1.55 system-ui,sans-serif;max-width:1050px;margin:auto;padding:32px;color:#18202a;background:#f5f7fa}}header,section{{background:white;border:1px solid #d9e0e8;border-radius:14px;padding:20px;margin:16px 0}}h1{{margin-top:0}}h2{{font-size:1.15rem}}small{{color:#637083}}.scan{{font-size:1.55rem;line-height:2.2;word-break:keep-all}}a.button{{display:inline-block;background:#1769e0;color:white;padding:10px 16px;border-radius:9px;text-decoration:none}}code{{background:#edf1f5;padding:2px 5px}}</style></head><body>
<header><h1>Wadoku: пилот {pilot_number} · {pilot_date} · 150 новых статей</h1><p><a class="button" href="{archive.name}">Скачать Yomitan ZIP</a></p><p>Отключите старый пилот и импортируйте новый ZIP в Yomitan. Затем наведите курсор на слова ниже с зажатой клавишей Yomitan.</p><p>Проверяйте перевод, разделение значений, формы, чтение, ударение, пометы, ссылки и примеры.</p></header>
{''.join(sections)}
</body></html>'''
    (static_dir / "index.html").write_text(page_html, encoding="utf-8")
    report = {"site": str((static_dir / "index.html").resolve()), "archive": str(destination.resolve()), "sections": {k: len(v) for k, v in by_category.items()}}
    atomic_write(site_dir / "site-report.json", canonical_json(report) + b"\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("select", "prepare", "translate", "export", "site", "all"))
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--work-dir", type=Path, default=OUTPUT)
    parser.add_argument("--export-dir", type=Path, default=EXPORT_OUTPUT)
    parser.add_argument("--prompt", type=Path, default=PROMPT)
    parser.add_argument("--pilot-number", type=int, default=4)
    parser.add_argument("--pilot-date", default="2026-09-09")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--selection", type=Path)
    parser.add_argument("--exclude-selection", type=Path, action="append", default=[])
    parser.add_argument("--editorial", type=Path)
    args = parser.parse_args()
    selection_path = args.selection or args.work_dir / "pilot-selection.json"
    batch_dir = args.work_dir / "batches"
    response_dir = args.work_dir / "responses"
    archive = args.export_dir / f"wadoku-jp-ru-rich-pilot-{args.pilot_number}-{args.pilot_date}.zip"
    site_dir = args.export_dir / "site"
    result: dict[str, Any] = {}
    if args.command in {"select", "all"}:
        if selection_path.exists():
            result["selection"] = json.loads(selection_path.read_text(encoding="utf-8"))
        else:
            exclude_paths = args.exclude_selection or [Path("work/wadoku-xml/pilot/pilot-selection.json")]
            excluded_entry_ids = {
                entry["entry_id"]
                for path in exclude_paths if path.is_file()
                for entry in json.loads(path.read_text(encoding="utf-8"))["entries"]
            }
            result["selection"] = select(
                SOURCE, KAISHI, selection_path,
                seed=args.seed if args.seed is not None else secrets.randbits(128),
                excluded_entry_ids=excluded_entry_ids,
            )
    if args.command in {"prepare", "all"}:
        result["batches"] = prepare_batches(SOURCE, selection_path, batch_dir)
    if args.command in {"translate", "all"}:
        result["translation"] = translate(batch_dir, response_dir, args.prompt, args.concurrency)
    if args.command in {"export", "all"}:
        result["export"] = export(
            SOURCE, selection_path, batch_dir, response_dir, archive,
            pilot_number=args.pilot_number, pilot_date=args.pilot_date,
            editorial_path=args.editorial,
        )
    if args.command in {"site", "all"}:
        result["site"] = build_site(
            selection_path, archive, site_dir,
            pilot_number=args.pilot_number, pilot_date=args.pilot_date,
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
