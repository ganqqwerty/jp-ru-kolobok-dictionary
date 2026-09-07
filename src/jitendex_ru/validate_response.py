from __future__ import annotations

from .database import ConnectionLike, RowLike

import json
import re
from pathlib import Path
from typing import Any

from .db import audit
from .dojg import (
    DOJG_JAPANESE_TEXT_RE, DOJG_PLACEHOLDER_RE, DOJG_ROLE, dojg_protected_tokens,
)
from .extract_units import SOURCE_ACRONYM_RE
from .util import (
    ASCII_WORD_RE, CONTROL_RE, CYRILLIC_RE, KEY_CHORD_RE, LANGUAGE_ORIGIN_RE,
    LATIN_TAXON_RE, MIXED_ALPHABET_RE, TAG_RE, canonical_json, sha256_bytes,
    source_xref_taxa,
)


class ValidationFailure(ValueError):
    def __init__(self, issues: list[dict[str, Any]]):
        super().__init__("response validation failed")
        self.issues = issues


ACRONYM_DEFINITION_RE = re.compile(r"^[A-Z][A-Z0-9.+/-]{1,11}$")
ENGLISH_GRAMMAR_TOKEN_RE = re.compile(
    r"\b(?:this|that|these|those|which|who|whom|whose)\b", re.IGNORECASE,
)
DOJG_NOTATION_TOKEN_RE = re.compile(r"[A-Za-z]+(?:\d+)?")
DOJG_QUOTED_ASCII_RE = re.compile(r'["“”«»]+([^"“”«»]+)["“”«»]+')
DOJG_FORMULA_RE = re.compile(
    r"\b(?:V(?:(?:volitional|conditional|informal|neg(?:ative)?|stem)|-[A-Za-z]+)?\d*"
    r"|S(?:n|\d+)?|N|pH\d*|mg\d*)\b"
)
DOJG_ROMAN_NUMERAL_RE = re.compile(r"\b[IVXLCDM]{2,}\b")
DOJG_THE_PROPER_NAME_RE = re.compile(r"\bThe(?:\s+[A-Z][A-Za-z.-]+){2,}\b")
DOJG_ENGLISH_GRAMMAR_LABELS = {
    "Adjective", "Adverb", "Auxiliary", "Conjunction", "Formal", "Informal",
    "Interjection", "Negative", "Nonpast", "Noun", "Particle", "Past",
    "Polite", "Positive", "Predicate", "Stem", "Verb", "Volitional",
    "Clause", "Copula", "Sentence",
}
DOJG_ENGLISH_NON_NAMES = DOJG_ENGLISH_GRAMMAR_LABELS | {
    "According", "Although", "Because", "English", "For", "From", "Here",
    "Japanese", "That", "The", "There", "These", "This", "Those", "When",
    "Where", "Which", "While",
}
SCIENTIFIC_NAME_RE = re.compile(
    r"\b[A-Z][a-z]{2,}\s+[a-z][a-z-]{2,}"
    r"(?:\s+(?!(?:subsp|ssp|var|f)\.)[a-z][a-z-]{2,})?"
    r"(?:\s+(?:subsp|ssp|var|f)\.\s+[a-z][a-z-]{2,})?(?=$|[^a-z-])"
)
WADOKU_NEUTRAL_TOKEN_RE = re.compile(
    r"(?:[a-z][A-Za-z0-9.+/-]*|[A-Z][a-z0-9.+/-]*[A-Z][A-Za-z0-9.+/-]*|"
    r"\d+(?:[.,]\d+)?(?:\s*%)?|[^\w\s]{1,3})"
)


def allows_japanese_grammar_label(role: str, source_text: str) -> bool:
    """Allow Japanese-only output when the source is itself a grammar label."""
    return role == "pos" and source_text.strip().lower() in {"suru"}


def allows_dojg_notation_only(source_text: str, target_text: Any) -> bool:
    """Allow unchanged formula or object-language cells with no learner text."""
    if not isinstance(target_text, str):
        return False
    residual = DOJG_PLACEHOLDER_RE.sub("", source_text)
    residual = re.sub(r"^\([a-z]+\)\.?", "", residual, flags=re.IGNORECASE)
    target_residual = DOJG_PLACEHOLDER_RE.sub("", target_text)
    target_residual = re.sub(r"^\([a-z]+\)\.?", "", target_residual, flags=re.IGNORECASE)
    tokens = DOJG_NOTATION_TOKEN_RE.findall(residual)
    notation_only = target_text == source_text and bool(tokens) and all(
        re.fullmatch(r"(?:pH|mg)\d*", token) is not None
        or re.fullmatch(r"S(?:n|\d+)?", token) is not None
        for token in tokens
    )
    stripped_quotes = residual.strip().strip('"“”«»')
    target_stripped_quotes = target_residual.strip().strip('"“”«»')
    quoted_object_text = (
        bool(DOJG_PLACEHOLDER_RE.search(source_text))
        and stripped_quotes != residual.strip()
        and target_stripped_quotes != target_residual.strip()
        and target_stripped_quotes == stripped_quotes
        and re.fullmatch(r"[A-Za-z][A-Za-z .'-]*", stripped_quotes) is not None
    )
    return notation_only or quoted_object_text


def dojg_allowed_english(source_text: str) -> list[str]:
    """Return source terms that may stay Latin inside an otherwise Russian target."""
    allowed = list(SOURCE_ACRONYM_RE.findall(source_text))
    for quoted in DOJG_QUOTED_ASCII_RE.findall(source_text):
        allowed.extend(ASCII_WORD_RE.findall(quoted))
    allowed.extend(
        token for token in ASCII_WORD_RE.findall(source_text)
        if len(token) > 1 and token[:1].isupper() and token not in DOJG_ENGLISH_NON_NAMES
    )
    return list(dict.fromkeys(allowed))


def dojg_untranslated_english(source_text: str, target_text: Any) -> list[str]:
    """Return Latin learner text that is not notation, a name, or quoted object language."""
    if not isinstance(target_text, str):
        return []
    residual = target_text
    for token in dojg_protected_tokens(source_text):
        residual = residual.replace(token, " ")
    residual = DOJG_FORMULA_RE.sub(" ", residual)
    residual = DOJG_ROMAN_NUMERAL_RE.sub(" ", residual)
    allowed = {token.casefold() for token in dojg_allowed_english(source_text)}
    source_casefold = source_text.casefold()
    for quoted in DOJG_QUOTED_ASCII_RE.findall(target_text):
        allowed.update(
            token.casefold() for token in ASCII_WORD_RE.findall(quoted)
            if token.casefold() in source_casefold
        )
    for proper_name in DOJG_THE_PROPER_NAME_RE.findall(target_text):
        if proper_name in source_text:
            allowed.update(token.casefold() for token in ASCII_WORD_RE.findall(proper_name))
    for token in ASCII_WORD_RE.findall(target_text):
        if token[:1].isupper() and token.casefold() in source_casefold:
            if token not in DOJG_ENGLISH_NON_NAMES:
                allowed.add(token.casefold())
    return [
        token for token in ASCII_WORD_RE.findall(residual)
        if token.casefold() not in allowed
    ]


def source_scientific_names(source_text: str) -> list[str]:
    """Find exact scientific names that a plain dictionary gloss may retain."""
    values = [source_text]
    try:
        parsed = json.loads(source_text)
        if isinstance(parsed, list) and all(isinstance(item, str) for item in parsed):
            values = parsed
    except json.JSONDecodeError:
        pass
    return list(dict.fromkeys(
        name for value in values for name in SCIENTIFIC_NAME_RE.findall(value)
    ))


def source_glossary_contains(source_text: str, target: str) -> bool:
    """Check exact text membership after decoding a plain glossary array."""
    if target in source_text:
        return True
    try:
        parsed = json.loads(source_text)
    except json.JSONDecodeError:
        return False
    return isinstance(parsed, list) and any(
        isinstance(item, str) and target in item for item in parsed
    )


def source_glossary_is_exact_single(source_text: str, target: str) -> bool:
    """Return true when a plain glossary contains one unchanged neutral item."""
    try:
        parsed = json.loads(source_text)
    except json.JSONDecodeError:
        return False
    return parsed == [target]


def _plain_text_issues(
    target: Any, protected: list[str], *, allow_no_cyrillic: bool = False,
    allowed_english: list[str] | None = None,
) -> list[str]:
    issues: list[str] = []
    if not isinstance(target, str) or not target.strip():
        return ["empty_or_non_string"]
    if TAG_RE.search(target):
        issues.append("markup_detected")
    if CONTROL_RE.search(target):
        issues.append("control_character")
    for token in protected:
        if token not in target:
            issues.append("protected_token_missing")
            break
    if not allow_no_cyrillic and not CYRILLIC_RE.search(target):
        issues.append("no_cyrillic")
    unprotected = target
    for token in protected:
        unprotected = unprotected.replace(token, " ")
    for token in allowed_english or []:
        unprotected = re.sub(rf"\b{re.escape(token)}\b", " ", unprotected, flags=re.IGNORECASE)
    unprotected = LATIN_TAXON_RE.sub("", unprotected)
    if MIXED_ALPHABET_RE.search(unprotected):
        issues.append("mixed_alphabet_token")
    if len(ASCII_WORD_RE.findall(unprotected)) > 2:
        issues.append("too_much_english")
    return issues


def target_storage(role: str, target: Any) -> str:
    if role == "glossary_set":
        if not isinstance(target, list):
            raise ValueError("glossary_set target must be an array")
        return canonical_json(target).decode()
    if not isinstance(target, str):
        raise ValueError("scalar target must be a string")
    return target


def validate_worker_payload(connection: ConnectionLike, attempt: RowLike, payload: Any) -> list[dict[str, Any]]:
    batch = connection.execute("SELECT * FROM batch WHERE id=?", (attempt["batch_id"],)).fetchone()
    issues: list[dict[str, Any]] = []
    if not isinstance(payload, dict):
        return [{"code": "invalid_shape"}]
    if set(payload) != {"schema_version", "batch_id", "manifest_sha256", "translations"}:
        issues.append({"code": "unexpected_top_level_fields"})
    run = connection.execute(
        "SELECT pipeline_version,extractor_version FROM run WHERE id=?", (batch["run_id"],),
    ).fetchone()
    wadoku_plain = run["extractor_version"] == "extractor-plain-glossary-v1"
    expected_schema = 2 if run["pipeline_version"] in {"lexicographer-v2", "dojg-v1", "kanjidic-v1"} else 1
    if payload.get("schema_version") != expected_schema:
        issues.append({"code": "wrong_schema_version"})
    if payload.get("batch_id") != batch["id"]:
        issues.append({"code": "batch_id_mismatch"})
    if payload.get("manifest_sha256") != batch["manifest_sha256"]:
        issues.append({"code": "manifest_hash_mismatch"})
    expected = connection.execute(
        """SELECT tu.* FROM batch_item bi JOIN translation_unit tu ON tu.id=bi.unit_id
        WHERE bi.batch_id=? ORDER BY bi.ordinal""", (batch["id"],)
    ).fetchall()
    required_targets: dict[str, str] = {}
    manifest_path = Path(batch["manifest_path"])
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for article in manifest.get("articles", []):
            for unit in article.get("units", []):
                required = unit.get("required_terminology")
                if isinstance(required, dict) and isinstance(required.get("target_text"), str):
                    required_targets[unit["unit_id"]] = required["target_text"]
    translations = payload.get("translations")
    if not isinstance(translations, list):
        return issues + [{"code": "translations_not_array"}]
    actual_ids = [item.get("unit_id") for item in translations if isinstance(item, dict)]
    expected_ids = [row["id"] for row in expected]
    if actual_ids != expected_ids:
        issues.append({"code": "unit_order_or_set_mismatch", "expected": expected_ids, "actual": actual_ids})
        return issues
    for source, item in zip(expected, translations):
        if set(item) != {"unit_id", "source_sha256", "target_text", "confidence", "review_reason"}:
            issues.append({"code": "unexpected_translation_fields", "unit_id": source["id"]})
        if item.get("source_sha256") != source["source_sha256"]:
            issues.append({"code": "source_hash_mismatch", "unit_id": source["id"]})
        if item.get("confidence") not in {"high", "medium", "low"}:
            issues.append({"code": "invalid_confidence", "unit_id": source["id"]})
        if item.get("confidence") != "high" and not item.get("review_reason"):
            issues.append({"code": "missing_review_reason", "unit_id": source["id"]})
        target = item.get("target_text")
        required_target = required_targets.get(source["id"])
        # Approved whole-leaf terminology is strong generation guidance, but
        # an intermediate JPDB batch is not rejected solely for varying from
        # it. One deterministic pass canonicalizes these leaves and structured
        # tags in the final cumulative run before the definitive export.
        if source["role"] == "glossary_set":
            maximum_definitions = 32 if wadoku_plain else 12
            if not isinstance(target, list) or not 1 <= len(target) <= maximum_definitions:
                issues.append({"code": "invalid_glossary_set", "unit_id": source["id"]})
                continue
            if len(set(target)) != len(target):
                issues.append({"code": "duplicate_glossary_definition", "unit_id": source["id"]})
            protected = [] if wadoku_plain else [
                *json.loads(source["protected_tokens_json"]),
                *SOURCE_ACRONYM_RE.findall(source["source_text"]),
            ]
            combined = " ".join(item for item in target if isinstance(item, str))
            missing = [token for token in protected if token not in combined]
            if missing:
                issues.append({"code": "protected_token_missing", "unit_id": source["id"], "tokens": missing})
            for index, definition in enumerate(target):
                definition_acronyms = [] if wadoku_plain else [
                    token for token in SOURCE_ACRONYM_RE.findall(source["source_text"])
                    if isinstance(definition, str) and token in definition
                ]
                allowed_latin = []
                exact_scientific_name = False
                if wadoku_plain and isinstance(definition, str):
                    allowed_latin.extend(source_xref_taxa(definition))
                    allowed_latin.extend(SOURCE_ACRONYM_RE.findall(definition))
                    source_taxa = [
                        taxon for taxon in source_scientific_names(definition)
                        if source_glossary_contains(source["source_text"], taxon)
                    ]
                    allowed_latin.extend(
                        token for taxon in source_taxa for token in ASCII_WORD_RE.findall(taxon)
                    )
                    exact_scientific_name = definition.strip() in source_taxa
                    allowed_latin.extend(
                        token for token in ASCII_WORD_RE.findall(definition)
                        if token[:1].isupper() and token in source["source_text"]
                    )
                exact_source_acronym = (
                    isinstance(definition, str)
                    and ACRONYM_DEFINITION_RE.fullmatch(definition) is not None
                    and definition in source["source_text"]
                    and (
                        CYRILLIC_RE.search(combined) is not None
                        or (
                            wadoku_plain
                            and source_glossary_is_exact_single(source["source_text"], definition)
                        )
                    )
                )
                exact_source_neutral_token = (
                    wadoku_plain
                    and isinstance(definition, str)
                    and WADOKU_NEUTRAL_TOKEN_RE.fullmatch(definition) is not None
                    and source_glossary_contains(source["source_text"], definition)
                    and (
                        CYRILLIC_RE.search(combined) is not None
                        or source_glossary_is_exact_single(source["source_text"], definition)
                    )
                )
                for code in _plain_text_issues(
                    definition, definition_acronyms,
                    allow_no_cyrillic=(
                        exact_source_acronym
                        or exact_scientific_name
                        or exact_source_neutral_token
                        or allows_japanese_grammar_label(source["role"], source["source_text"])
                    ),
                    allowed_english=allowed_latin,
                ):
                    issues.append({"code": code, "unit_id": source["id"], "definition_index": index})
        else:
            protected = [] if required_target is not None else json.loads(source["protected_tokens_json"])
            if source["role"] != DOJG_ROLE:
                protected = [*protected, *KEY_CHORD_RE.findall(source["source_text"])]
                protected = [*protected, *SOURCE_ACRONYM_RE.findall(source["source_text"])]
                if source["role"] == "xref_gloss":
                    protected = [*protected, *source_xref_taxa(source["source_text"])]
                if source["role"] == "note" and (match := LANGUAGE_ORIGIN_RE.fullmatch(source["source_text"].strip())):
                    protected = [*protected, match.group(1)]
            allowed_english = []
            if source["role"] == "example" and "antecedent" in source["source_text"].lower():
                allowed_english = ENGLISH_GRAMMAR_TOKEN_RE.findall(source["source_text"])
            if source["role"] == DOJG_ROLE:
                allowed_english = dojg_allowed_english(source["source_text"])
            for code in _plain_text_issues(
                target,
                protected,
                allow_no_cyrillic=(
                    (required_target is not None and target == required_target)
                    or allows_japanese_grammar_label(source["role"], source["source_text"])
                    or (
                        source["role"] == DOJG_ROLE
                        and allows_dojg_notation_only(source["source_text"], target)
                    )
                ),
                allowed_english=allowed_english,
            ):
                issues.append({"code": code, "unit_id": source["id"]})
            if source["role"] == DOJG_ROLE and isinstance(target, str):
                if "\n" in target or "|" in target:
                    issues.append({"code": "dojg_structural_delimiter_added", "unit_id": source["id"]})
                expected_placeholders = DOJG_PLACEHOLDER_RE.findall(source["source_text"])
                actual_placeholders = DOJG_PLACEHOLDER_RE.findall(target)
                if actual_placeholders != expected_placeholders:
                    issues.append({
                        "code": "dojg_placeholder_order_or_set_mismatch",
                        "unit_id": source["id"],
                        "expected": expected_placeholders,
                        "actual": actual_placeholders,
                    })
                if DOJG_JAPANESE_TEXT_RE.search(target):
                    issues.append({"code": "dojg_japanese_added", "unit_id": source["id"]})
                residual_english = dojg_untranslated_english(source["source_text"], target)
                if residual_english:
                    issues.append({
                        "code": "dojg_untranslated_english",
                        "unit_id": source["id"],
                        "tokens": residual_english,
                    })
    return issues


def ingest_response(connection: ConnectionLike, path: Path) -> dict[str, int]:
    attempt = connection.execute("SELECT * FROM attempt WHERE response_path=?", (str(path),)).fetchone()
    if attempt is None:
        raise ValueError(f"no claimed attempt expects {path}")
    lock = " FOR UPDATE" if getattr(connection, "backend", "sqlite") == "postgresql" else ""
    owned_batch = connection.execute(
        "SELECT state,lease_token FROM batch WHERE id=?" + lock, (attempt["batch_id"],),
    ).fetchone()
    if (
        attempt["outcome"] != "claimed" or owned_batch is None
        or owned_batch["state"] != "leased"
        or not attempt["lease_token"] or owned_batch["lease_token"] != attempt["lease_token"]
    ):
        raise ValueError(f"stale attempt no longer owns batch lease: {attempt['id']}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        payload = None
        issues = [{"code": "invalid_json", "error": str(error)}]
    else:
        issues = validate_worker_payload(connection, attempt, payload)
    if issues:
        for issue in issues:
            connection.execute(
                """INSERT INTO validation_issue(run_id,unit_id,attempt_id,validator,severity,code,details_json)
                SELECT b.run_id,?,?, 'deterministic-v1','error',?,? FROM batch b WHERE b.id=?""",
                (issue.get("unit_id"), attempt["id"], issue["code"], json.dumps(issue, ensure_ascii=False), attempt["batch_id"]),
            )
        connection.execute(
            """UPDATE attempt SET outcome='rejected',error_json=?,completed_at=CURRENT_TIMESTAMP
            WHERE id=? AND outcome='claimed' AND lease_token=?""",
            (json.dumps(issues), attempt["id"], attempt["lease_token"]),
        )
        connection.execute(
            """UPDATE batch SET state='retryable' WHERE id=? AND state='leased' AND lease_token=?""",
            (attempt["batch_id"], attempt["lease_token"]),
        )
        audit(connection, "reject", "attempt", attempt["id"], {"issues": issues})
        raise ValidationFailure(issues)
    batch = connection.execute("SELECT * FROM batch WHERE id=?", (attempt["batch_id"],)).fetchone()
    accepted_attempt = connection.execute(
        """UPDATE attempt SET outcome='accepted',completed_at=CURRENT_TIMESTAMP
        WHERE id=? AND outcome='claimed' AND lease_token=?""",
        (attempt["id"], attempt["lease_token"]),
    ).rowcount
    validated_batch = connection.execute(
        """UPDATE batch SET state='deterministic_validated' WHERE id=?
        AND state='leased' AND lease_token=?""",
        (attempt["batch_id"], attempt["lease_token"]),
    ).rowcount
    if accepted_attempt != 1 or validated_batch != 1:
        raise ValueError(f"lease ownership changed during ingestion: {attempt['id']}")
    for item in payload["translations"]:
        source = connection.execute("SELECT role FROM translation_unit WHERE id=?", (item["unit_id"],)).fetchone()
        stored_target = target_storage(source["role"], item["target_text"])
        connection.execute(
            """INSERT INTO translation(run_id,unit_id,attempt_id,target_text,confidence,review_reason,target_sha256)
            VALUES (?,?,?,?,?,?,?)""",
            (batch["run_id"], item["unit_id"], attempt["id"], stored_target, item["confidence"],
             item["review_reason"], sha256_bytes(stored_target.encode())),
        )
        connection.execute("UPDATE translation_unit SET status='translated' WHERE id=?", (item["unit_id"],))
    connection.execute(
        """UPDATE validation_issue SET resolved_at=CURRENT_TIMESTAMP,
        waiver_reason='superseded by a later deterministically valid response for the same batch'
        WHERE resolved_at IS NULL AND validator='deterministic-v1' AND attempt_id IN (
          SELECT id FROM attempt WHERE batch_id=? AND id<>?
        )""",
        (attempt["batch_id"], attempt["id"]),
    )
    audit(connection, "ingest", "attempt", attempt["id"], {"translations": len(payload["translations"])})
    return {"translations_ingested": len(payload["translations"])}
