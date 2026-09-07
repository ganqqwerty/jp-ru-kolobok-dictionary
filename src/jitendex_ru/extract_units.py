from __future__ import annotations

from .database import ConnectionLike, RowLike

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Iterator

from .db import audit
from .dojg import DOJG_ROLE, extract_dojg_segments, translation_pointer_base
from .prep_metrics import PrepMetrics
from .util import (
    KEY_CHORD_RE, LANGUAGE_ORIGIN_RE, canonical_json, json_pointer_escape,
    sha256_bytes, source_xref_taxa, structural_fingerprint,
)


ROLE_BY_SELECTOR = {
    "glossary": "glossary", "example-sentence-b": "example", "part-of-speech-info": "pos",
    "misc-info": "register", "field-info": "label", "dialect-info": "register",
    "sense-note": "note", "info-gloss": "note", "xref": "xref_gloss", "antonym": "xref_gloss",
    "forms": "label", "lang-source": "note",
    # Common selectors used by compact Japanese monolingual Yomitan sources.
    "glossaryShortDefinition": "glossary", "glossaryExtendedDefinition": "note",
    "examples": "example",
}
EXCLUDED_SELECTORS = {
    "example-sentence-a", "attribution", "attribution-footnote", "redirect", "ruby", "rt",
}
NON_TRANSLATABLE_KEYS = {
    "data", "href", "path", "src", "lang", "style", "tag",
    # Yomitan image rendering controls. These are schema values/CSS, not copy.
    "imageRendering", "appearance", "verticalAlign", "border", "borderRadius", "sizeUnits",
}
PROTECTED_RE = re.compile(r"https?://\S+|\b(?:JMdict|Tatoeba)\b|[\u3040-\u30ff\u3400-\u9fff]+|\{[^{}]+\}|\b\d+(?:\.\d+)*\b")
SOURCE_ACRONYM_RE = re.compile(r"\b[A-Z][A-Z0-9.+/-]{1,11}\b")
TRANSLATABLE_TEXT_RE = re.compile(r"[A-Za-z\u3040-\u30ff\u3400-\u9fff]")


@dataclass(frozen=True)
class ExtractedUnit:
    pointer: str
    role: str
    source_text: str
    protected_tokens: tuple[str, ...]


def protected_tokens(role: str, text: str) -> tuple[str, ...]:
    visible_text = " ".join(glossary_evidence(text)) if role == "glossary_set" else text
    # Japanese is source content in a JP->RU unit, not text that the Russian
    # target must repeat. A URL or short Latin label must not switch the whole
    # Japanese sentence back to the older Jitendex protection behavior.
    japanese_count = len(re.findall(r"[\u3040-\u30ff\u3400-\u9fff]", visible_text))
    latin_count = len(re.findall(r"[A-Za-z]", visible_text))
    tokens = list(PROTECTED_RE.findall(visible_text))
    if japanese_count > latin_count:
        tokens = []
    tokens.extend(SOURCE_ACRONYM_RE.findall(visible_text))
    tokens.extend(KEY_CHORD_RE.findall(visible_text))
    if role == "xref_gloss":
        tokens.extend(source_xref_taxa(text))
    if role == "note" and (match := LANGUAGE_ORIGIN_RE.fullmatch(text.strip())):
        tokens.append(match.group(1))
    return tuple(dict.fromkeys(tokens))


def _flatten_text(node: Any, output: list[str]) -> None:
    if isinstance(node, str):
        output.append(node)
    elif isinstance(node, dict):
        if "content" in node:
            _flatten_text(node["content"], output)
    elif isinstance(node, list):
        for child in node:
            _flatten_text(child, output)


def _visible_text(node: Any) -> str:
    output: list[str] = []
    _flatten_text(node, output)
    return " ".join(part.strip() for part in output if part.strip())


def glossary_evidence(serialized_subtree: str) -> list[str]:
    subtree = json.loads(serialized_subtree)
    items = subtree if isinstance(subtree, list) else [subtree]
    return [text for item in items if (text := _visible_text(item))]


def _selector(node: dict[str, Any]) -> str | None:
    data = node.get("data")
    if isinstance(data, dict) and isinstance(data.get("content"), str):
        return data["content"]
    return None


def _walk(node: Any, pointer: str = "", selectors: tuple[str, ...] = ()) -> Iterator[ExtractedUnit]:
    if isinstance(node, dict):
        selector = _selector(node)
        active = selectors + ((selector,) if selector else ())
        if any(item in EXCLUDED_SELECTORS for item in active):
            return
        role = next((ROLE_BY_SELECTOR[item] for item in reversed(active) if item in ROLE_BY_SELECTOR), None)
        for key, value in node.items():
            child_pointer = f"{pointer}/{json_pointer_escape(key)}"
            if key in NON_TRANSLATABLE_KEYS:
                continue
            if key in {"content", "title"} and isinstance(value, str):
                text = value.strip()
                if role and text and TRANSLATABLE_TEXT_RE.search(text):
                    unit_role = "tooltip" if key == "title" else role
                    yield ExtractedUnit(child_pointer, unit_role, text, protected_tokens(unit_role, text))
            else:
                yield from _walk(value, child_pointer, active)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk(value, f"{pointer}/{index}", selectors)
    elif isinstance(node, str):
        role = next((ROLE_BY_SELECTOR[item] for item in reversed(selectors) if item in ROLE_BY_SELECTOR), None)
        text = node.strip()
        if role and text and TRANSLATABLE_TEXT_RE.search(text):
            yield ExtractedUnit(pointer, role, text, protected_tokens(role, text))


def _walk_lexicographer(node: Any, pointer: str = "", selectors: tuple[str, ...] = ()) -> Iterator[ExtractedUnit]:
    """Extract one variable-length unit per glossary and scalar units elsewhere."""
    if isinstance(node, dict):
        selector = _selector(node)
        active = selectors + ((selector,) if selector else ())
        if any(item in EXCLUDED_SELECTORS for item in active):
            return
        if selector == "glossary" and "content" in node:
            subtree = node["content"]
            items = subtree if isinstance(subtree, list) else [subtree]
            glosses = [_visible_text(item) for item in items]
            glosses = [item for item in glosses if item]
            if glosses and any(re.search(r"[A-Za-z]", item) for item in glosses):
                source = canonical_json(subtree).decode()
                protected = protected_tokens("glossary_set", source)
                yield ExtractedUnit(f"{pointer}/content", "glossary_set", source, protected)
            return
        role = next((ROLE_BY_SELECTOR[item] for item in reversed(active) if item in ROLE_BY_SELECTOR), None)
        for key, value in node.items():
            child_pointer = f"{pointer}/{json_pointer_escape(key)}"
            if key in NON_TRANSLATABLE_KEYS:
                continue
            if key in {"content", "title"} and isinstance(value, str):
                text = value.strip()
                if role and text and TRANSLATABLE_TEXT_RE.search(text):
                    unit_role = "tooltip" if key == "title" else role
                    yield ExtractedUnit(child_pointer, unit_role, text, protected_tokens(unit_role, text))
            else:
                yield from _walk_lexicographer(value, child_pointer, active)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk_lexicographer(value, f"{pointer}/{index}", selectors)
    elif isinstance(node, str):
        role = next((ROLE_BY_SELECTOR[item] for item in reversed(selectors) if item in ROLE_BY_SELECTOR), None)
        text = node.strip()
        if role and text and TRANSLATABLE_TEXT_RE.search(text):
            yield ExtractedUnit(pointer, role, text, protected_tokens(role, text))


def extract_article_units(row: list[Any], pipeline_version: str = "scalar-v1") -> list[ExtractedUnit]:
    if len(row) < 6:
        return []
    if pipeline_version == "kanjidic-v1":
        meanings = row[4]
        if not isinstance(meanings, list) or not meanings or not all(isinstance(item, str) for item in meanings):
            return []
        source = canonical_json(meanings).decode()
        return [ExtractedUnit("/4", "glossary_set", source, protected_tokens("glossary_set", source))]
    if pipeline_version == "dojg-v1":
        return [
            ExtractedUnit(segment.pointer, DOJG_ROLE, segment.source_text, segment.protected_tokens)
            for segment in extract_dojg_segments(row)
        ]
    if pipeline_version == "lexicographer-v2" and isinstance(row[5], list) and row[5] and all(
        isinstance(item, str) for item in row[5]
    ):
        source = canonical_json(row[5]).decode()
        return [ExtractedUnit("/5", "glossary_set", source, protected_tokens("glossary_set", source))]
    walker = _walk_lexicographer if pipeline_version == "lexicographer-v2" else _walk
    return list(walker(row[5], "/5"))


def kanjidic_context(row: list[Any]) -> dict[str, Any]:
    """Return read-only KANJIDIC evidence without treating metadata as text."""
    return {
        "dictionary": "KANJIDIC2",
        "kanji": row[0],
        "on_readings": row[1],
        "kun_readings": row[2],
        "character_class": row[3],
        "source_gloss_evidence": row[4],
        "metadata": row[5],
        "preservation_rule": "Keep the kanji, readings, class, codes, and numeric metadata unchanged.",
    }


def _selector_nodes(node: Any, wanted: str) -> Iterator[dict[str, Any]]:
    if isinstance(node, dict):
        if _selector(node) == wanted:
            yield node
        for child in node.values():
            yield from _selector_nodes(child, wanted)
    elif isinstance(node, list):
        for child in node:
            yield from _selector_nodes(child, wanted)


def lexicographic_context(row: list[Any]) -> dict[str, Any]:
    """A sense-oriented brief: English is evidence, Japanese and metadata lead."""
    senses: list[dict[str, Any]] = []
    for index, sense in enumerate(_selector_nodes(row[5], "sense"), 1):
        glosses = []
        for glossary in _selector_nodes(sense, "glossary"):
            content = glossary.get("content")
            items = content if isinstance(content, list) else [content]
            glosses.extend(_visible_text(item) for item in items)
        examples = []
        japanese = [_visible_text(item) for item in _selector_nodes(sense, "example-sentence-a")]
        english = [_visible_text(item) for item in _selector_nodes(sense, "example-sentence-b")]
        for position in range(max(len(japanese), len(english))):
            examples.append({
                "japanese": japanese[position] if position < len(japanese) else "",
                "english_evidence": english[position] if position < len(english) else "",
            })
        labels = []
        for selector in ("part-of-speech-info", "misc-info", "field-info", "dialect-info", "sense-note", "info-gloss"):
            labels.extend(_visible_text(item) for item in _selector_nodes(sense, selector))
        senses.append({
            "sense_id": f"sense-{index}",
            "english_gloss_evidence": [item for item in glosses if item],
            "linguistic_metadata": [item for item in labels if item],
            "examples": examples,
        })
    inventory = []
    for selector in ("forms", "example-sentence-a", "xref", "antonym", "sense-note", "lang-source", "attribution"):
        nodes = list(_selector_nodes(row[5], selector))
        if nodes:
            inventory.append({"element": selector, "count": len(nodes), "content": [_visible_text(item) for item in nodes]})
    if not senses and isinstance(row[5], list) and all(isinstance(item, str) for item in row[5]):
        senses.append({
            "sense_id": "sense-1",
            "source_gloss_evidence": row[5],
            "linguistic_metadata": [],
            "examples": [],
        })
    return {"senses": senses, "preservation_inventory": inventory}


def semantic_context(row: list[Any]) -> dict[str, Any]:
    units = extract_article_units(row)
    examples: list[dict[str, str]] = []

    def collect(node: Any, active: str | None = None) -> None:
        if isinstance(node, dict):
            own_selector = _selector(node)
            current = own_selector or active
            if own_selector == "example-sentence-a":
                texts: list[str] = []
                _flatten_text(node, texts)
                examples.append({"japanese": " ".join(texts)})
            elif own_selector == "example-sentence-b":
                texts = []
                _flatten_text(node, texts)
                if examples and "english" not in examples[-1]:
                    examples[-1]["english"] = " ".join(texts)
            for child in node.values():
                collect(child, current)
        elif isinstance(node, list):
            for child in node:
                collect(child, active)

    collect(row[5])
    return {
        "sense_groups": [{"role": unit.role, "text": unit.source_text} for unit in units if unit.role != "example"],
        "examples": examples,
        "cross_references": [unit.source_text for unit in units if unit.role == "xref_gloss"],
    }


_RUN_ARTICLE_COLUMNS = ("run_id", "article_id", "structural_fingerprint")
_UNIT_COLUMNS = (
    "id", "run_id", "article_id", "json_pointer", "role", "source_text", "source_sha256",
    "protected_tokens_json", "byte_count", "status",
)


def _flush_postgres_extraction(
    connection: ConnectionLike,
    article_rows: list[tuple[Any, ...]],
    unit_rows: list[tuple[Any, ...]],
) -> int:
    if article_rows:
        connection.copy_rows("prep_run_article", _RUN_ARTICLE_COLUMNS, article_rows)
        connection.execute(
            """INSERT INTO run_article(run_id,article_id,structural_fingerprint)
            SELECT run_id,article_id,structural_fingerprint FROM prep_run_article
            ON CONFLICT(run_id,article_id) DO UPDATE
            SET structural_fingerprint=excluded.structural_fingerprint"""
        )
        connection.execute("TRUNCATE prep_run_article")
    added = 0
    if unit_rows:
        connection.copy_rows("prep_translation_unit", _UNIT_COLUMNS, unit_rows)
        added = connection.execute(
            """INSERT INTO translation_unit
            (id,run_id,article_id,json_pointer,role,source_text,source_sha256,
             protected_tokens_json,byte_count,status)
            SELECT id,run_id,article_id,json_pointer,role,source_text,source_sha256,
            protected_tokens_json,byte_count,status FROM prep_translation_unit
            ON CONFLICT(run_id,article_id,json_pointer) DO NOTHING"""
        ).rowcount
        connection.execute("TRUNCATE prep_translation_unit")
    article_rows.clear()
    unit_rows.clear()
    return added


def _copy_source_units(connection: ConnectionLike, source_run_id: int, target_run_id: int) -> int:
    source = connection.execute(
        """SELECT jitendex_snapshot_id,extractor_version,pipeline_version
        FROM run WHERE id=?""", (source_run_id,),
    ).fetchone()
    target = connection.execute(
        """SELECT jitendex_snapshot_id,extractor_version,pipeline_version
        FROM run WHERE id=?""", (target_run_id,),
    ).fetchone()
    if source is None:
        raise ValueError(f"unknown source run {source_run_id}")
    if target is None:
        raise ValueError(f"unknown target run {target_run_id}")
    identity = ("jitendex_snapshot_id", "extractor_version", "pipeline_version")
    if any(source[key] != target[key] for key in identity):
        raise ValueError("source and target runs do not share extraction identity")
    missing = connection.execute(
        """SELECT COUNT(*) FROM run_article ra
        LEFT JOIN article a ON a.id=ra.article_id AND a.selected=1
        WHERE ra.run_id=? AND a.id IS NULL""", (source_run_id,),
    ).fetchone()[0]
    if missing:
        raise ValueError(f"target selection omits {missing} source-run articles")
    malformed = connection.execute(
        """SELECT COUNT(*) FROM translation_unit
        WHERE run_id=? AND id NOT LIKE ?""",
        (source_run_id, f"u-r{source_run_id}-%"),
    ).fetchone()[0]
    if malformed:
        raise ValueError(f"source run has {malformed} non-deterministic unit IDs")
    connection.execute(
        """INSERT INTO run_article(run_id,article_id,structural_fingerprint)
        SELECT ?,ra.article_id,ra.structural_fingerprint
        FROM run_article ra JOIN article a ON a.id=ra.article_id AND a.selected=1
        WHERE ra.run_id=?
        ON CONFLICT(run_id,article_id) DO UPDATE
        SET structural_fingerprint=excluded.structural_fingerprint""",
        (target_run_id, source_run_id),
    )
    return connection.execute(
        """INSERT INTO translation_unit
        (id,run_id,article_id,json_pointer,role,source_text,source_sha256,
         protected_tokens_json,byte_count,status)
        SELECT 'u-r' || CAST(? AS TEXT) ||
               SUBSTRING(old.id FROM LENGTH('u-r' || CAST(? AS TEXT)) + 1),
        ?,old.article_id,old.json_pointer,old.role,old.source_text,old.source_sha256,
        old.protected_tokens_json,old.byte_count,'ready'
        FROM translation_unit old
        JOIN article a ON a.id=old.article_id AND a.selected=1
        WHERE old.run_id=?
        ON CONFLICT(run_id,article_id,json_pointer) DO NOTHING""",
        (target_run_id, source_run_id, target_run_id, source_run_id),
    ).rowcount


def extract_selected(
    connection: ConnectionLike, run_id: int, source_run_id: int | None = None,
) -> dict[str, Any]:
    added = parsed_articles = copied_units = loaded_units = input_bytes = 0
    parse_wall = parse_cpu = load_wall = load_cpu = 0.0
    json_wall = structure_wall = hashing_wall = 0.0
    metrics = PrepMetrics("extract_units")
    run = connection.execute("SELECT pipeline_version FROM run WHERE id=?", (run_id,)).fetchone()
    if run is None:
        raise ValueError(f"unknown run {run_id}")
    if source_run_id == run_id:
        raise ValueError("source and target runs must differ")
    pipeline_version = run["pipeline_version"]
    backend = getattr(connection, "backend", "sqlite")
    incremental = backend == "postgresql" and source_run_id is not None
    if backend == "postgresql":
        with metrics.phase("extraction_staging_setup"):
            connection.execute(
                "CREATE TEMP TABLE prep_run_article (LIKE run_article INCLUDING DEFAULTS) ON COMMIT DROP"
            )
            connection.execute(
                "CREATE TEMP TABLE prep_translation_unit (LIKE translation_unit INCLUDING DEFAULTS) ON COMMIT DROP"
            )
    if incremental:
        with metrics.phase("old_unit_copy") as phase:
            copied_units = _copy_source_units(connection, source_run_id, run_id)
            added += copied_units
            phase.update(input_rows=copied_units, output_rows=copied_units)
        articles = connection.execute(
            """SELECT a.* FROM article a WHERE a.selected=1 AND NOT EXISTS (
            SELECT 1 FROM run_article source WHERE source.run_id=? AND source.article_id=a.id)
            ORDER BY a.bank_number,a.entry_ordinal""", (source_run_id,),
        )
    else:
        articles = connection.execute(
            "SELECT * FROM article WHERE selected=1 ORDER BY bank_number,entry_ordinal"
        )
    pending_articles: list[tuple[Any, ...]] = []
    pending_units: list[tuple[Any, ...]] = []
    for article in articles:
        article_wall = time.monotonic()
        article_cpu = time.process_time()
        parsed_articles += 1
        input_bytes += len(article["raw_json"].encode())
        detail_started = time.monotonic()
        source = json.loads(article["raw_json"])
        json_wall += time.monotonic() - detail_started
        detail_started = time.monotonic()
        units = extract_article_units(source, pipeline_version)
        pointers = {translation_pointer_base(unit.pointer) for unit in units}
        fingerprint = structural_fingerprint(source, pointers)
        structure_wall += time.monotonic() - detail_started
        pending_articles.append((run_id, article["id"], fingerprint))
        detail_started = time.monotonic()
        for unit in units:
            source_hash = sha256_bytes(unit.source_text.encode())
            unit_id = f"u-r{run_id}-{article['id']}-{sha256_bytes(unit.pointer.encode())[:12]}"
            pending_units.append(
                (unit_id, run_id, article["id"], unit.pointer, unit.role, unit.source_text, source_hash,
                 json.dumps(unit.protected_tokens, ensure_ascii=False), len(unit.source_text.encode()), "ready")
            )
        hashing_wall += time.monotonic() - detail_started
        parse_wall += time.monotonic() - article_wall
        parse_cpu += time.process_time() - article_cpu
        if parsed_articles % 1_000 == 0:
            metrics.progress(
                "new_article_parsing", "busy_python", articles_parsed=parsed_articles,
                units_buffered=len(pending_units), input_bytes=input_bytes,
            )
        if backend == "postgresql" and (
            len(pending_articles) >= 1_000 or len(pending_units) >= 10_000
        ):
            flush_wall = time.monotonic()
            flush_cpu = time.process_time()
            flushed = _flush_postgres_extraction(connection, pending_articles, pending_units)
            load_wall += time.monotonic() - flush_wall
            load_cpu += time.process_time() - flush_cpu
            loaded_units += flushed
            added += flushed
            metrics.progress(
                "new_unit_loading", "waiting_postgresql", articles_parsed=parsed_articles,
                units_loaded=loaded_units,
            )
    if backend == "postgresql":
        flush_wall = time.monotonic()
        flush_cpu = time.process_time()
        flushed = _flush_postgres_extraction(connection, pending_articles, pending_units)
        load_wall += time.monotonic() - flush_wall
        load_cpu += time.process_time() - flush_cpu
        loaded_units += flushed
        added += flushed
    else:
        flush_wall = time.monotonic()
        flush_cpu = time.process_time()
        connection.executemany(
            """INSERT INTO run_article(run_id,article_id,structural_fingerprint) VALUES (?,?,?)
            ON CONFLICT(run_id,article_id) DO UPDATE
            SET structural_fingerprint=excluded.structural_fingerprint""", pending_articles,
        )
        for values in pending_units:
            added += connection.execute(
                """INSERT INTO translation_unit
                (id,run_id,article_id,json_pointer,role,source_text,source_sha256,
                 protected_tokens_json,byte_count,status) VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(run_id,article_id,json_pointer) DO NOTHING""", values,
            ).rowcount
        loaded_units = added
        load_wall += time.monotonic() - flush_wall
        load_cpu += time.process_time() - flush_cpu
    metrics.record(
        "new_article_parsing", parse_wall, parse_cpu, input_rows=parsed_articles,
        output_rows=loaded_units, input_bytes=input_bytes, json_seconds=round(json_wall, 6),
        structure_seconds=round(structure_wall, 6), hashing_seconds=round(hashing_wall, 6),
    )
    metrics.record(
        "new_unit_loading", load_wall, load_cpu,
        input_rows=loaded_units, output_rows=loaded_units,
    )
    # The composite foreign key guarantees that every unit article belongs to
    # this run.  Counting both indexed sets avoids a correlated anti-join whose
    # PostgreSQL estimate is stale inside the bulk-loading transaction.
    with metrics.phase("extraction_final_checks") as phase:
        quarantined = connection.execute(
            """SELECT
            (SELECT COUNT(*) FROM run_article WHERE run_id=?) -
            (SELECT COUNT(DISTINCT article_id) FROM translation_unit WHERE run_id=?)""",
            (run_id, run_id),
        ).fetchone()[0]
        phase.update(input_rows=added, articles_without_units=quarantined)
    audit(connection, "extract_units", "run", run_id, {
        "added": added, "articles_without_units": quarantined,
        "source_run_id": source_run_id, "articles_parsed": parsed_articles,
    })
    return {
        "units_added": added, "articles_without_units": quarantined,
        "copied_units": copied_units, "parsed_articles": parsed_articles,
        "loaded_new_units": loaded_units, "phase_metrics": metrics.phases,
    }
