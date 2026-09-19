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
    LATIN_TAXON_RE, MIXED_ALPHABET_RE, TAG_RE, canonical_json, json_pointer_get, sha256_bytes,
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
WADOKU_XML_PLACEHOLDER_RE = re.compile(r"⟦WDXP\d{4}⟧")
WADOKU_XML_TRANSLATABLE_RE = re.compile(r"[A-Za-z\u00c0-\u024f\u1e00-\u1eff]")
WADOKU_XML_CHEMICAL_FORMULA_RE = re.compile(r"(?<!\w)(?:[A-Z][a-z]?\d*){2,}(?!\w)")
WADOKU_XML_FORMULA_ONLY_RE = re.compile(
    r"[()\[\]{}A-Za-z0-9₀-₉₊₋₌₍₎ₐₑₒₓₔₕₖₗₘₙₚₛₜ"
    r"⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱᵤ½+−‑\-·.…′°µΩ/:=×≡§\s]+"
)
WADOKU_XML_CATALOG_ID_RE = re.compile(
    r"(?:[A-Z]|[A-Z][A-Za-z]*[A-Z][A-Za-z]*|[A-Z][A-Za-z]*\s+[0-9]+|[A-Z]\s+[0-9]+)"
)
WADOKU_XML_UNIT_ONLY_RE = re.compile(
    r"(?:[fpnumkMGT]?)(?:Ci|Bq|Gy|Sv|Hz|Pa|J|W|V|A|K|mol|cd|g|m|s|l)"
)
WADOKU_XML_ISBN_RE = re.compile(r"ISBN:?\s+[0-9Xx-]+")
WADOKU_XML_EQUATION_RE = re.compile(
    r"(?=.*\d)(?=.*=)[0-9A-Za-z₀-₉⁰-⁹.,+−‑\-/*=×°Ωµ\s]+"
)
WADOKU_XML_NOTATION_SIGNAL_RE = re.compile(
    r"[0-9₀-₉₊₋₌₍₎⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ᵤ½+−‑/*=×°Ωµ≡…′§:]|(?:[A-Z].*[A-Z])"
)
WADOKU_XML_PATH_SEGMENT_RE = re.compile(r"([^/\[]+)\[(\d+)\]")
WADOKU_XML_STANDARD_ID_RE = re.compile(r"(?:JIS|ISO|IEC|DIN|ASTM)\s+[A-Z0-9][A-Z0-9 .:/-]*")
WADOKU_XML_MEDICAL_ID_RE = re.compile(r"ICD-?10:\s*[A-Z][0-9.]+", re.IGNORECASE)
WADOKU_XML_TAXON_SUFFIX_RE = re.compile(
    r"[A-Z][A-Za-z-]*(?:aceae|idae|inae|ales|ini|phyta|mycota|virus)", re.IGNORECASE,
)
WADOKU_XML_NAME_WORD_RE = re.compile(
    r"[A-Z\u00c0-\u00de\u0100-\u017d][A-Za-z\u00c0-\u024f\u1e00-\u1eff·'’.-]*"
    r"(?:-[A-Z0-9][A-Za-z0-9-]*)?"
)
WADOKU_XML_NAME_CONNECTORS = frozenset({
    "&", "al", "and", "da", "de", "del", "della", "der", "di", "du", "en", "et", "la",
    "for", "le", "no", "of", "pour", "the", "un", "und", "van", "von", "y",
})
WADOKU_XML_ROMANIZED_MARK_RE = re.compile(
    r"[ĀĒĪŌŪāēīōūÁÀǍÉÈĚÍÌǏÓÒǑÚÙǓǕǗǙǛǖǘǚǜáàǎéèěíìǐóòǒúùǔ]",
    re.IGNORECASE,
)
WADOKU_XML_FOREIGN_TITLE_RE = re.compile(
    r"^(?:engl|franz|lat|ital|span|port|niederl|chin|korean)\.\s*(.+)$", re.IGNORECASE,
)
WADOKU_XML_LATIN_ENDING_RE = re.compile(
    r"(?:a|ae|am|arum|as|e|es|i|ii|is|orum|um|us)$", re.IGNORECASE,
)
WADOKU_XML_NAME_DOMAINS = frozenset({
    "Firmenn.", "Flussn.", "Familienn.", "Ländern.", "Ortsn.", "Personenn.",
    "Persönlichk.", "Stadtn.", "Vorn.", "Wz.",
})
WADOKU_XML_SCIENCE_DOMAINS = frozenset({
    "Anat.", "Biochem.", "Biol.", "Bot.", "Chem.", "Fischk.", "Insektenk.",
    "Math.", "Med.", "Mykol.", "Pharm.", "Phys.", "Zool.",
})
WADOKU_XML_EXACT_LABEL_DOMAINS = frozenset({
    "EDV", "Firmenn.", "Funkt.", "Gesch.", "Golf", "Internet", "Luftf.",
    "Maß", "Milit.", "Mus.", "Org.", "Rechtsw.", "Telekom.", "Verlagsn.",
    "Videospiel", "Wz.",
})
WADOKU_XML_EXACT_FOREIGN_LABELS = frozenset({
    "Chkdsk", "Dir en grey", "Gamescom", "gamescom",
})
WADOKU_XML_AMINO_ACID_CODES = frozenset({
    "Ala", "Arg", "Asn", "Asp", "Cys", "Gln", "Glu", "Gly", "His", "Ile",
    "Leu", "Lys", "Met", "Phe", "Pro", "Ser", "Thr", "Trp", "Tyr", "Val",
})
WADOKU_XML_SOURCE_NAME_RE = re.compile(
    r"(?<!\w)[A-Z\u00c0-\u00de\u0100-\u017d]"
    r"[A-Za-z\u00c0-\u024f\u1e00-\u1eff·.-]*"
    r"(?:\s+[A-Z\u00c0-\u00de\u0100-\u017d]"
    r"[A-Za-z\u00c0-\u024f\u1e00-\u1eff·.-]*)*"
)
WADOKU_XML_PRODUCT_TOKEN_RE = re.compile(
    r"(?<!\w)(?:[a-z]+[A-Z][A-Za-z]*|[A-Za-z]+-[A-Za-z]+)(?!\w)"
)
WADOKU_XML_ABBREVIATION_TOKEN_RE = re.compile(r"\b([A-Za-z]{1,4})\.")
WADOKU_XML_FOREIGN_TAIL_RE = re.compile(
    r"(?:\b(?:engl|lat)\.\s*|\bsteht\s+für\s+|^von\s+)(.+)$", re.IGNORECASE,
)
WADOKU_XML_ROMAN_NUMERAL_TOKEN_RE = re.compile(r"\b[IVXLCDM]{2,}\b")


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


def wadoku_scientific_source(unit: dict[str, Any]) -> bool:
    """Trust scientific annotations only on every selected source member's ancestry."""
    from .wadoku_quality import tree_paths
    context = unit.get('local_context')
    if not isinstance(context, dict):
        return False
    tree = context.get('example_source_context')
    root = '/entry[1]'
    if not tree:
        tree = context.get('sense_context')
        root = context.get('sense_path')
    selected = unit.get('member_paths') or context.get('member_paths') or [unit.get('source_path')]
    if not tree or not root or not selected or not all(isinstance(p, str) and p for p in selected):
        return False
    nodes = list(tree_paths(tree, root))
    paths = {path for _, path in nodes}
    return all(path in paths and any(
        node.get('attributes', {}).get('langdesc') == 'scientific'
        and (path == ancestor or path.startswith(ancestor + '/'))
        for node, ancestor in nodes
    ) for path in selected)


def wadoku_redundant_source_echo(unit, target):
    """Reject only a copied lexical gloss beside Russian, not all Latin text."""
    if unit.get('role') != 'glossary_set' or wadoku_scientific_source(unit):
        return []
    if unit.get('protected_tokens') or not isinstance(target, list):
        return []
    words = {w.casefold() for w in re.findall(r'\b[A-Za-zÀ-ž]{3,}\b', unit['source_text']) if not w.isupper()}
    found = []
    for item in target:
        if not isinstance(item, str) or not re.search(r'[А-Яа-яЁё]', item):
            continue
        fragments = []
        for parenthesis in re.findall(r'\(([^()]+)\)', item):
            if re.fullmatch(r'[A-Za-zÀ-ž,; /-]+', parenthesis):
                candidates = re.findall(r'\b[A-Za-zÀ-ž]{3,}\b', parenthesis)
                if candidates and all(w.casefold() in words for w in candidates):
                    fragments.extend(candidates)
        prefix = re.match(r'^([A-Za-zÀ-ž]{3,})\s*[—–:]', item)
        if prefix:
            fragments.append(prefix[1])
        found.extend(w for w in fragments if w.casefold() in words and not w.isupper())
    return list(dict.fromkeys(found))


def wadoku_lexical_contract_issues(unit, target):
    """Validate explicit v5 source annotations, never an unrestricted Latin ban."""
    parts = target if isinstance(target, list) else [target]
    combined = ' '.join(p for p in parts if isinstance(p, str))
    target_words = {w.casefold() for w in re.findall(r'\b[A-Za-zÀ-ž]{3,}\b', combined)}
    copied = [w for w in unit.get('source_lexical_terms', []) if w.casefold() in target_words]
    missing = [w for w in unit.get('required_literals', []) if not re.search(r'(?<!\w)' + re.escape(w) + r'(?!\w)', combined)]
    issues = []
    if copied and not wadoku_scientific_source(unit):
        issues.append({'code': 'untranslated_source_lexeme', 'unit_id': unit['unit_id'], 'words': copied})
    if missing:
        issues.append({'code': 'required_literal_missing', 'unit_id': unit['unit_id'], 'literals': missing})
    return issues


def wadoku_target_issues(
    source_text: str, target: Any, protected: list[str], unit_id: str | None = None,
    *, allow_exact_source: bool = False, scientific_source: bool = False,
) -> list[dict[str, Any]]:
    """Validate one Wadoku XML scalar with the worker-response rules."""
    neutral_residual = WADOKU_XML_PLACEHOLDER_RE.sub("", source_text).strip()
    exact_scientific_name = SCIENTIFIC_NAME_RE.fullmatch(neutral_residual) is not None
    formula_or_identifier = (
        WADOKU_XML_FORMULA_ONLY_RE.fullmatch(neutral_residual) is not None
        and WADOKU_XML_NOTATION_SIGNAL_RE.search(neutral_residual) is not None
    )
    compact_identifier = WADOKU_XML_CATALOG_ID_RE.fullmatch(neutral_residual) is not None
    unit_symbol = WADOKU_XML_UNIT_ONLY_RE.fullmatch(neutral_residual) is not None
    isbn = WADOKU_XML_ISBN_RE.fullmatch(neutral_residual) is not None
    equation = WADOKU_XML_EQUATION_RE.fullmatch(neutral_residual) is not None
    biochemical_code = neutral_residual in WADOKU_XML_AMINO_ACID_CODES
    standard_identifier = WADOKU_XML_STANDARD_ID_RE.fullmatch(neutral_residual) is not None
    taxon_name = WADOKU_XML_TAXON_SUFFIX_RE.fullmatch(neutral_residual) is not None
    foreign_name = wadoku_xml_is_foreign_name(neutral_residual)
    latin_term = wadoku_xml_is_latin_term(neutral_residual)
    single_latin_taxon = bool(
        len(neutral_residual) >= 6
        and re.fullmatch(r"[A-Z][a-z]+", neutral_residual)
        and WADOKU_XML_LATIN_ENDING_RE.search(neutral_residual)
    )
    neutral_target = isinstance(target, str) and WADOKU_XML_TRANSLATABLE_RE.search(target) is None
    source_equivalent = isinstance(target, str) and wadoku_xml_neutral_equivalent(
        target, source_text,
    )
    allow_unchanged_neutral = (
        isinstance(target, str)
        and source_equivalent
        and (
            WADOKU_XML_TRANSLATABLE_RE.search(neutral_residual) is None
            or exact_scientific_name
            or formula_or_identifier
            or compact_identifier
            or unit_symbol
            or isbn
            or equation
            or biochemical_code
            or standard_identifier
            or WADOKU_XML_MEDICAL_ID_RE.fullmatch(neutral_residual) is not None
            or taxon_name
            or foreign_name
            or latin_term
            or single_latin_taxon
            or neutral_residual in WADOKU_XML_EXACT_FOREIGN_LABELS
            or allow_exact_source or scientific_source
        )
    )
    allowed_english = list(dict.fromkeys(
        token
        for name in WADOKU_XML_SOURCE_NAME_RE.findall(source_text)
        for token in ASCII_WORD_RE.findall(name)
    ))
    if isinstance(target, str) and CYRILLIC_RE.search(target):
        allowed_english = list(dict.fromkeys([
            *allowed_english,
            *WADOKU_XML_PRODUCT_TOKEN_RE.findall(source_text),
            *WADOKU_XML_ABBREVIATION_TOKEN_RE.findall(source_text),
            *WADOKU_XML_ROMAN_NUMERAL_TOKEN_RE.findall(target),
            *wadoku_xml_foreign_tail_tokens(source_text),
        ]))
    if allow_unchanged_neutral or scientific_source:
        allowed_english = list(dict.fromkeys(
            [*allowed_english, *ASCII_WORD_RE.findall(source_text)],
        ))
    foreign_title = WADOKU_XML_FOREIGN_TITLE_RE.match(source_text.strip())
    if isinstance(target, str) and foreign_title and foreign_title.group(1) in target:
        allowed_english = list(dict.fromkeys(
            [*allowed_english, *ASCII_WORD_RE.findall(foreign_title.group(1))],
        ))
    issues = [
        {"code": code, **({"unit_id": unit_id} if unit_id is not None else {})}
        for code in _plain_text_issues(
            target, protected, allow_no_cyrillic=allow_unchanged_neutral or neutral_target,
            allowed_english=allowed_english,
        )
    ]
    if isinstance(target, str):
        actual_placeholders = WADOKU_XML_PLACEHOLDER_RE.findall(target)
        if actual_placeholders != protected:
            issues.append({
                "code": "wadoku_placeholder_order_or_set_mismatch",
                **({"unit_id": unit_id} if unit_id is not None else {}),
                "expected": protected, "actual": actual_placeholders,
            })
    return issues


def wadoku_glossary_issues(source_text: str, target: Any, protected: list[str],
                          unit_id: str) -> list[dict[str, Any]]:
    """Validate a whole sense without imposing Jitendex's English-token rules."""
    if (not isinstance(target, list) or not 1 <= len(target) <= 12
            or not all(isinstance(item, str) and item.strip() for item in target)):
        return [{"code": "invalid_glossary_set", "unit_id": unit_id}]
    issues = []
    if len(set(item.strip().casefold() for item in target)) != len(target):
        issues.append({"code": "duplicate_glossary_definition", "unit_id": unit_id})
    for index, item in enumerate(target):
        tokens = WADOKU_XML_PLACEHOLDER_RE.findall(item)
        issues.extend({**issue, "definition_index": index} for issue in
                      wadoku_target_issues(source_text, item, tokens, unit_id))
    actual = WADOKU_XML_PLACEHOLDER_RE.findall(" ".join(target))
    if actual != protected:
        issues.append({"code": "wadoku_placeholder_order_or_set_mismatch", "unit_id": unit_id,
                       "expected": protected, "actual": actual})
    return issues


def wadoku_xml_is_foreign_name(value: str) -> bool:
    """Recognize a proper foreign form without accepting ordinary German nouns."""
    if WADOKU_XML_ROMANIZED_MARK_RE.search(value) is not None or "·" in value:
        return True
    words = value.split()
    if len(words) < 2:
        return False
    proper = 0
    for raw_word in words:
        word = raw_word.strip(",:;()[]{}")
        if word.casefold().rstrip(".") in WADOKU_XML_NAME_CONNECTORS:
            continue
        normalized_word = word.replace("‑", "-")
        if (
            WADOKU_XML_NAME_WORD_RE.fullmatch(normalized_word) is None
            and ACRONYM_DEFINITION_RE.fullmatch(normalized_word) is None
            and re.fullmatch(r"[A-Z][A-Za-z]*[A-Z][A-Za-z]*", normalized_word) is None
        ):
            return False
        proper += 1
    return proper >= 2


def wadoku_xml_is_latin_term(value: str) -> bool:
    """Recognize a multi-word Latin scientific or anatomical form."""
    words = [word.strip(".,:;()[]{}") for word in value.split()]
    if (
        len(words) < 2
        or not words[0][:1].isupper()
        or any(not word.isalpha() for word in words)
    ):
        return False
    latin_endings = sum(WADOKU_XML_LATIN_ENDING_RE.search(word) is not None for word in words)
    return latin_endings >= max(2, len(words) - 1)


def wadoku_xml_neutral_equivalent(left: str, right: str) -> bool:
    """Compare preserved labels while ignoring harmless XML spacing and dash variants."""
    def normalize(value: str) -> str:
        normalized = re.sub(r"\s+", " ", value.strip()).replace("‑", "-")
        return re.sub(r"\s*&\s*", "&", normalized).casefold()

    return normalize(left) == normalize(right)


def wadoku_xml_foreign_tail_tokens(source_text: str) -> list[str]:
    """Return exact source words from a named foreign phrase or abbreviation expansion."""
    if "=" in source_text:
        return ASCII_WORD_RE.findall(source_text)
    match = WADOKU_XML_FOREIGN_TAIL_RE.search(source_text)
    return ASCII_WORD_RE.findall(match.group(1)) if match else []


def wadoku_xml_block_ancestry(raw_json: str, pointer: str) -> list[dict[str, Any]]:
    """Resolve one canonical block back to its lossless XML ancestry."""
    try:
        canonical = json.loads(raw_json)
        block = json_pointer_get(canonical, pointer)
        xml_path = block["xml_path"]
        node = canonical["tree"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return []
    segments = WADOKU_XML_PATH_SEGMENT_RE.findall(xml_path)
    if not segments or segments[0][0] != "entry":
        return []
    ancestry = [node]
    for tag, raw_index in segments[1:]:
        matches = [child for child in node.get("children", []) if child.get("tag") == tag]
        index = int(raw_index) - 1
        if index < 0 or index >= len(matches):
            return []
        node = matches[index]
        ancestry.append(node)
    return ancestry


def wadoku_xml_block_is_scientific(raw_json: str, pointer: str) -> bool:
    """Use canonical XML ancestry to identify an exact scientific source block."""
    return any(
        item.get("tag") == "trans"
        and str(item.get("attributes", {}).get("langdesc", "")).casefold() == "scientific"
        for item in wadoku_xml_block_ancestry(raw_json, pointer)
    )


def wadoku_xml_block_allows_exact_source(raw_json: str, pointer: str, source_text: str) -> bool:
    """Use Wadoku sense labels to preserve untagged names and scientific forms."""
    ancestry = wadoku_xml_block_ancestry(raw_json, pointer)
    if ancestry:
        def plain(node: dict[str, Any]) -> str:
            return str(node.get("text", "")) + "".join(
                plain(child) + str(child.get("tail", ""))
                for child in node.get("children", [])
            )

        def descendants(node: dict[str, Any]) -> list[dict[str, Any]]:
            return [node, *(item for child in node.get("children", []) for item in descendants(child))]

        if any(
            item.get("tag") == "foreign" and plain(item).strip() == source_text.strip()
            for item in descendants(ancestry[0])
        ):
            return True
    if any(
        item.get("tag") == "trans"
        and str(item.get("attributes", {}).get("langdesc", "")).casefold() == "scientific"
        for item in ancestry
    ):
        return True
    sense = next((item for item in reversed(ancestry) if item.get("tag") == "sense"), None)
    if sense is None:
        return False
    domains = {
        str(child.get("text", "")).strip()
        for child in sense.get("children", [])
        if child.get("tag") == "usg" and child.get("attributes", {}).get("type") == "dom"
    }
    if domains & WADOKU_XML_NAME_DOMAINS:
        return True
    source = source_text.strip()
    if domains & WADOKU_XML_SCIENCE_DOMAINS:
        return bool(
            re.fullmatch(
                r"[A-Z\u00c0-\u00de\u0100-\u017d][A-Za-z\u00c0-\u024f\u1e00-\u1eff-]*",
                source,
            )
            or WADOKU_XML_FORMULA_ONLY_RE.fullmatch(source)
        )
    if domains & WADOKU_XML_EXACT_LABEL_DOMAINS:
        return bool(
            re.fullmatch(r"\S+", source)
            or wadoku_xml_is_foreign_name(source)
            or WADOKU_XML_FORMULA_ONLY_RE.fullmatch(source)
        )
    return False


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
    wadoku_xml = run["pipeline_version"] in {"wadoku-xml-v2", "wadoku-xml-v3"}
    expected_schema = 2 if run["pipeline_version"] in {
        "lexicographer-v2", "dojg-v1", "kanjidic-v1", "wadoku-xml-v2", "wadoku-xml-v3",
    } else 1
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
    scientific_units: set[str] = set()
    strict_units = {}
    manifest_path = Path(batch["manifest_path"])
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for article in manifest.get("articles", []):
            for unit in article.get("units", []):
                if article.get('read_only_context', {}).get('versions', {}).get('schema') in {'rich-v4', 'rich-v5', 'rich-v6', 'rich-v7'}:
                    strict_units[unit['unit_id']] = unit
                if wadoku_scientific_source(unit):
                    scientific_units.add(unit['unit_id'])
                required = unit.get("required_terminology")
                if isinstance(required, dict) and isinstance(required.get("target_text"), str):
                    required_targets[unit["unit_id"]] = required["target_text"]
    translations = payload.get("translations")
    if not isinstance(translations, list):
        return issues + [{"code": "translations_not_array"}]
    actual_ids = [item.get("unit_id") for item in translations if isinstance(item, dict)]
    expected_ids = [row["id"] for row in expected]
    if (run['pipeline_version'] == 'wadoku-xml-v3'
            and len(actual_ids) == len(translations) == len(expected_ids)
            and all(isinstance(unit_id, str) for unit_id in actual_ids)
            and len(set(actual_ids)) == len(actual_ids)
            and set(actual_ids) == set(expected_ids)):
        # Identity and source hashes bind values; response array order does not.
        # Keep the saved response intact and validate pairs in request order.
        indexed = {item['unit_id']: item for item in translations}
        translations = [indexed[unit_id] for unit_id in expected_ids]
        actual_ids = expected_ids
    if actual_ids != expected_ids:
        issues.append({"code": "unit_order_or_set_mismatch", "expected": expected_ids, "actual": actual_ids})
        return issues
    if wadoku_xml:
        issues.extend(wadoku_cross_article_duplicate_issues(expected, translations))
    wadoku_exact_source: set[str] = set()
    if run["pipeline_version"] == "wadoku-xml-v2":
        article_ids = sorted({row["article_id"] for row in expected})
        placeholders = ",".join("?" for _ in article_ids)
        raw_by_article = {
            row["id"]: row["raw_json"]
            for row in connection.execute(
                f"SELECT id,raw_json FROM article WHERE id IN ({placeholders})",
                tuple(article_ids),
            ).fetchall()
        }
        for source, item in zip(expected, translations):
            if (
                isinstance(item, dict)
                and isinstance(item.get("target_text"), str)
                and wadoku_xml_neutral_equivalent(
                    item["target_text"], source["source_text"],
                )
                and wadoku_xml_block_allows_exact_source(
                    raw_by_article.get(source["article_id"], ""), source["json_pointer"],
                    source["source_text"],
                )
            ):
                wadoku_exact_source.add(source["id"])
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
        issues.extend(wadoku_lexical_contract_issues(strict_units.get(source['id'], {}), target))
        echo = wadoku_redundant_source_echo(strict_units.get(source['id'], {}), target)
        if echo:
            issues.append({'code': 'redundant_source_echo', 'unit_id': source['id'], 'fragments': echo})
        required_target = required_targets.get(source["id"])
        # Approved whole-leaf terminology is strong generation guidance, but
        # an intermediate JPDB batch is not rejected solely for varying from
        # it. One deterministic pass canonicalizes these leaves and structured
        # tags in the final cumulative run before the definitive export.
        if source["role"] == "glossary_set":
            if wadoku_xml:
                issues.extend(wadoku_glossary_issues(source["source_text"], target,
                    json.loads(source["protected_tokens_json"]), source["id"]))
                continue
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
            if source["role"] != DOJG_ROLE and not wadoku_xml:
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
            if wadoku_xml:
                issues.extend(wadoku_target_issues(
                    source["source_text"], target, list(protected), source["id"],
                    allow_exact_source=source["id"] in wadoku_exact_source,
                    scientific_source=source['id'] in scientific_units,
                ))
            else:
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


def wadoku_cross_article_duplicate_issues(
    expected: list[RowLike], translations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Reject the narrow duplicate signature left by a shifted batch response."""
    groups: dict[str, list[tuple[RowLike, dict[str, Any]]]] = {}
    for source, item in zip(expected, translations):
        role = source["role"]
        if role in {"etymology", "example_translation"}:
            continue
        target = item.get("target_text")
        if not isinstance(target, (str, list)):
            continue
        target_key = canonical_json(target).decode()
        readable = " ".join(target) if isinstance(target, list) and all(
            isinstance(value, str) for value in target
        ) else target
        if not isinstance(readable, str) or len(CYRILLIC_RE.findall(readable)) < 12:
            continue
        groups.setdefault(target_key, []).append((source, item))
    issues = []
    for target_key, rows in groups.items():
        article_ids = {int(source["article_id"]) for source, _item in rows}
        source_texts = {source["source_text"] for source, _item in rows}
        roles = {source["role"] for source, _item in rows}
        if len(article_ids) < 2 or len(source_texts) < 2 or len(roles) != 1:
            continue
        issues.append({
            "code": "wadoku_cross_article_duplicate_target",
            "unit_ids": [source["id"] for source, _item in rows],
            "article_ids": sorted(article_ids),
            "role": next(iter(roles)),
            "target_text": json.loads(target_key),
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
        WHERE resolved_at IS NULL
        AND validator IN ('deterministic-v1','deterministic-alignment-v1')
        AND attempt_id IN (
          SELECT id FROM attempt WHERE batch_id=? AND id<>?
        )""",
        (attempt["batch_id"], attempt["id"]),
    )
    audit(connection, "ingest", "attempt", attempt["id"], {"translations": len(payload["translations"])})
    return {"translations_ingested": len(payload["translations"])}
