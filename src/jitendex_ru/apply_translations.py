from __future__ import annotations

from .database import ConnectionLike, RowLike

import copy
import json
from typing import Any

from .extract_units import NON_TRANSLATABLE_KEYS
from .util import json_pointer_get, json_pointer_set, sha256_bytes, structural_fingerprint


def _scalar_source_and_target(current: Any, expected: str, target: str) -> tuple[str, str]:
    """Validate a stripped scalar unit and preserve its source whitespace."""
    if not isinstance(current, str) or current.strip() != expected:
        raise ValueError("scalar source text changed")
    leading = current[:len(current) - len(current.lstrip())]
    trailing = current[len(current.rstrip()):]
    return current, f"{leading}{target}{trailing}"


def _set_language_for_leaf(source: Any, pointer: str) -> None:
    segments = pointer.removeprefix("/").split("/")
    if not segments:
        return
    if segments[-1] not in {"content", "title"}:
        return
    for depth in range(len(segments) - 1, -1, -1):
        parent_pointer = "/" + "/".join(segments[:depth]) if depth else ""
        try:
            parent = json_pointer_get(source, parent_pointer)
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if isinstance(parent, dict) and parent.get("lang") in {"en", "ja"}:
            parent["lang"] = "ru"
            return


def _compose_glossary(source_items: Any, serialized_target: str, pointer: str) -> Any:
    definitions = json.loads(serialized_target)
    if not isinstance(definitions, list) or not definitions:
        raise ValueError(f"glossary target is not a non-empty list at {pointer}")
    if isinstance(source_items, str):
        return definitions[0] if len(definitions) == 1 else "; ".join(definitions)
    if isinstance(source_items, dict) and "content" in source_items:
        rendered = []
        for definition in definitions:
            item = copy.deepcopy(source_items)
            item["content"] = definition
            if item.get("lang") == "en":
                item["lang"] = "ru"
            rendered.append(item)
        return rendered[0] if len(rendered) == 1 else rendered
    if not isinstance(source_items, list) or not source_items:
        raise ValueError(f"glossary source has an unsupported shape at {pointer}")
    template = source_items[0]
    output: list[Any] = []
    for definition in definitions:
        if not isinstance(definition, str) or not definition.strip():
            raise ValueError(f"invalid glossary definition at {pointer}")
        if isinstance(template, str):
            output.append(definition)
        elif isinstance(template, dict) and "content" in template:
            item = copy.deepcopy(template)
            item["content"] = definition
            if item.get("lang") == "en":
                item["lang"] = "ru"
            output.append(item)
        else:
            raise ValueError(f"unsupported glossary item shape at {pointer}")
    return output


def apply_article(
    connection: ConnectionLike, run_id: int, article: RowLike,
    unit_rows: list[RowLike] | None = None,
) -> list[Any]:
    if sha256_bytes(article["raw_json"].encode()) != article["source_sha256"]:
        raise ValueError(f"article {article['id']} source hash changed")
    source = json.loads(article["raw_json"])
    if unit_rows is None:
        rows = connection.execute(
            """SELECT tu.json_pointer,tu.role,tu.source_text,t.target_text,t.target_sha256
            FROM translation_unit tu JOIN translation t ON t.unit_id=tu.id
            WHERE tu.run_id=? AND tu.article_id=? AND t.accepted=1 ORDER BY tu.json_pointer""",
            (run_id, article["id"]),
        ).fetchall()
        all_units = connection.execute(
            "SELECT json_pointer FROM translation_unit WHERE run_id=? AND article_id=? ORDER BY json_pointer",
            (run_id, article["id"]),
        ).fetchall()
        accepted_complete = len(rows) == len(all_units)
    else:
        rows = unit_rows
        all_units = unit_rows
        accepted_complete = all(row["translation_id"] is not None for row in rows)
    if not accepted_complete:
        raise ValueError(f"article {article['id']} has unaccepted translation units")
    pointers = {row["json_pointer"] for row in all_units}
    if unit_rows is None:
        run_article = connection.execute(
            "SELECT structural_fingerprint FROM run_article WHERE run_id=? AND article_id=?",
            (run_id, article["id"]),
        ).fetchone()
        expected_fingerprint = run_article["structural_fingerprint"] if run_article else article["structural_fingerprint"]
    else:
        expected_fingerprint = article["run_structural_fingerprint"]
    if structural_fingerprint(source, pointers) != expected_fingerprint:
        raise ValueError(f"article {article['id']} source structural fingerprint changed")
    output = copy.deepcopy(source)
    for row in rows:
        if sha256_bytes(row["target_text"].encode()) != row["target_sha256"]:
            raise ValueError(f"accepted target hash changed at {row['json_pointer']}")
        current = json_pointer_get(output, row["json_pointer"])
        expected_source = json.loads(row["source_text"]) if row["role"] == "glossary_set" else row["source_text"]
        if row["role"] == "glossary_set":
            if current != expected_source:
                raise ValueError(f"source text changed at {row['json_pointer']}")
            original = expected_source
            translated = _compose_glossary(current, row["target_text"], row["json_pointer"])
        else:
            try:
                original, translated = _scalar_source_and_target(
                    current, expected_source, row["target_text"],
                )
            except ValueError as error:
                raise ValueError(f"source text changed at {row['json_pointer']}") from error
        # Compatibility for runs extracted before rendering controls were
        # classified as structural: validate their provenance but preserve
        # the source value so the Yomitan schema remains valid.
        if row["json_pointer"].rsplit("/", 1)[-1] in NON_TRANSLATABLE_KEYS:
            continue
        json_pointer_set(output, row["json_pointer"], translated)
        _set_language_for_leaf(output, row["json_pointer"])
    # Language edits are the only structural exception, checked by reverting them.
    comparison = copy.deepcopy(output)
    for row in rows:
        current_source = json_pointer_get(source, row["json_pointer"])
        original = json.loads(row["source_text"]) if row["role"] == "glossary_set" else current_source
        json_pointer_set(comparison, row["json_pointer"], original)
        segments = row["json_pointer"].removeprefix("/").split("/")
        for depth in range(len(segments) - 1, -1, -1):
            parent_pointer = "/" + "/".join(segments[:depth]) if depth else ""
            parent = json_pointer_get(comparison, parent_pointer)
            source_parent = json_pointer_get(source, parent_pointer)
            if isinstance(parent, dict) and isinstance(source_parent, dict) and "lang" in source_parent:
                parent["lang"] = source_parent["lang"]
                break
    if structural_fingerprint(comparison, pointers) != expected_fingerprint:
        raise ValueError(f"article {article['id']} unapproved structure changed")
    return output
