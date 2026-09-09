"""Versioned, side-effect-free preparation and QA for the rich Wadoku pipeline.

These functions never dispatch models, alter source trees or infer lexical
classification from string similarity. Unresolved data stays a blocking issue.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import Counter
from typing import Any, Iterable

from .util import canonical_json, sha256_bytes

QUALITY_VERSION = "wadoku-quality-v1"
PROJECTION_VERSION = "wadoku-sense-context-v1"
ISSUE_IDS = tuple(f"WPM-P{i:02}" for i in range(1, 31))
TEMPLATE_RE = re.compile(r"[…~〜～]")


def tree_paths(tree: dict[str, Any], path: str = "/entry[1]"):
    yield tree, path
    counts: Counter[str] = Counter()
    for child in tree["children"]:
        counts[child["tag"]] += 1
        yield from tree_paths(child, f"{path}/{child['tag']}[{counts[child['tag']]}]")


def plain(node: dict[str, Any]) -> str:
    return node["text"] + "".join(plain(c) + c["tail"] for c in node["children"])


def source_identity(value: dict[str, Any]) -> str:
    """Hash only immutable source, never attached translations or decisions."""
    return sha256_bytes(canonical_json({"entry_id": value["entry_id"], "tree": value["tree"]}))


def morae(reading: str) -> list[str]:
    reading = "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in reading)
    result: list[str] = []
    for char in reading:
        if not ("ぁ" <= char <= "ゖ" or char == "ー"):
            raise ValueError("reading is not an unambiguous kana string")
        if char in "ゃゅょぁぃぅぇぉゎ" and result:
            result[-1] += char
        else:
            result.append(char)
    return result


def parse_devoicing(hatsuon: str, reading: str) -> list[int]:
    """Accept only source notation that aligns completely; never guess a mora."""
    if "[Dev]" not in hatsuon:
        return []
    segments = hatsuon.split("[Dev]")
    cleaned = [re.sub(r"[\s'’~·･・<>]", "", s) for s in segments]
    expected = morae(reading)
    if morae("".join(cleaned)) != expected:
        raise ValueError("devoicing notation does not align with the exact reading")
    positions = []
    prefix = ""
    for before, after in zip(cleaned, cleaned[1:]):
        prefix += before
        if not after:
            raise ValueError("devoicing marker has no following mora")
        position = len(morae(prefix)) + 1
        if position > len(expected) or after[0] in "ゃゅょぁぃぅぇぉゎ":
            raise ValueError("devoicing marker is not at a mora boundary")
        positions.append(position)
    return sorted(set(positions))


def pronunciation_records(value: dict[str, Any]) -> dict[str, Any]:
    form = next(c for c in value["tree"]["children"] if c["tag"] == "form")
    nodes = list(tree_paths(form, "/entry[1]/form[1]"))
    reading = next(plain(n) for n, _ in nodes if n["tag"] == "hira")
    accents, issues = [], []
    seen = set()
    try:
        length = len(morae(reading))
    except ValueError:
        length = None
    devoice: list[int] = []
    for node, path in nodes:
        if node["tag"] == "hatsuon" and "[Dev]" in plain(node):
            try:
                devoice = parse_devoicing(plain(node), reading)
            except ValueError as error:
                issues.append({"code": "unresolved_devoicing", "path": path, "reason": str(error)})
    for node, path in tree_paths(value["tree"]):
        if node["tag"] != "accent":
            continue
        sense = re.search(r"/sense\[(\d+)\]", path)
        # Accent inside a reference or other nested structure is not entry pitch.
        if not (path.startswith("/entry[1]/form[1]/reading[") or
                re.fullmatch(r"/entry\[1\]/sense\[\d+\]/accent\[\d+\]", path)):
            issues.append({"code": "unsupported_accent_scope", "path": path})
            continue
        raw = plain(node)
        if not raw.isdigit() or length is None or int(raw) > length:
            issues.append({"code": "invalid_accent", "path": path, "value": raw})
            continue
        scope = f"sense:{sense[1]}" if sense else "entry"
        key = (scope, int(raw))
        if key in seen:
            continue
        seen.add(key)
        pitch: dict[str, Any] = {"position": int(raw)}
        if sense:
            pitch["tags"] = [f"wdx-sense-{sense[1]}"]
        if devoice:
            pitch["devoice"] = devoice
        accents.append({"scope": scope, "path": path, "source_value": raw, "pitch": pitch})
    if devoice and not accents:
        issues.append({"code": "devoicing_without_pitch", "path": "/entry[1]/form[1]"})
    return {"version": QUALITY_VERSION, "entry_id": value["entry_id"],
            "reading": reading, "accents": accents, "issues": issues}


def usage_codes(node: dict[str, Any]) -> list[str]:
    codes = []
    if plain(node).strip():
        codes.append(f"{node['attributes'].get('type', '')}:{plain(node)}")
    for attr, val in sorted(node["attributes"].items()):
        if attr != "type":
            codes.append(f"@{attr}:{val}")
    return codes


def pronunciation_groups(value: dict[str, Any]) -> dict[str, Any]:
    """Same sense set => pronunciation variants; different sets => articles.

    Explicit sense accents override entry-level pronunciation for that sense.
    Unmarked senses inherit entry pronunciation, never an arbitrary neighbour.
    Missing inheritance evidence remains unresolved.
    """
    parsed = pronunciation_records(value)
    if parsed["issues"]:
        return {"groups": [], "issues": parsed["issues"]}
    senses = [n for n in value["tree"]["children"] if n["tag"] == "sense"]
    general = {r["pitch"]["position"] for r in parsed["accents"] if r["scope"] == "entry"}
    restricted: dict[int, set[int]] = {}
    for record in parsed["accents"]:
        if record["scope"].startswith("sense:"):
            restricted.setdefault(int(record["scope"].split(":")[1]), set()).add(record["pitch"]["position"])
    if not restricted:
        return {"groups": [{"sense_indices": list(range(1, len(senses) + 1)),
                            "pitches": [r["pitch"] for r in parsed["accents"]]}], "issues": []}
    membership: dict[int, list[int]] = {}
    issues = []
    for index in range(1, len(senses) + 1):
        pitches = restricted.get(index, general)
        if not pitches:
            issues.append({"code": "unresolved_sense_pitch", "sense_index": index})
        for position in sorted(pitches):
            membership.setdefault(position, []).append(index)
    for orphan in general - membership.keys():
        issues.append({"code": "pitch_without_assigned_sense", "position": orphan})
    if issues:
        return {"groups": [], "issues": issues}
    by_senses: dict[tuple[int, ...], list[dict[str, Any]]] = {}
    for position, indices in sorted(membership.items()):
        prototype = next(r["pitch"] for r in parsed["accents"] if r["pitch"]["position"] == position)
        pitch = {k: copy.deepcopy(v) for k, v in prototype.items() if k != "tags"}
        by_senses.setdefault(tuple(indices), []).append(pitch)
    return {"groups": [{"sense_indices": list(indices), "pitches": pitches}
                        for indices, pitches in by_senses.items()], "issues": []}


def pitch_group_sequence(entry_id: int, sense_indices: list[int]) -> int:
    digest = sha256_bytes(canonical_json([QUALITY_VERSION, "pitch-senses", entry_id, sense_indices]))
    # Reserve an integer range above source IDs, still safe in JavaScript.
    return (1 << 48) + int(digest[:12], 16)


def example_candidates(entries: Iterable[dict[str, Any]], parent_ids: set[int]) -> list[dict[str, Any]]:
    """Build reverse edges, not automatic example/inflection decisions."""
    from .wadoku_xml import canonical_identity
    candidates = []
    for value in entries:
        expression, reading, _ = canonical_identity(value)
        for node, path in tree_paths(value["tree"]):
            attrs = node["attributes"]
            if (node["tag"] != "ref" or attrs.get("type") != "main"
                    or attrs.get("subentrytype") != "VwBsp"):
                continue
            parent = int(attrs["id"])
            if parent not in parent_ids:
                continue
            candidates.append({
                "parent_id": parent, "child_id": value["entry_id"], "relation_path": path,
                "source_sha256": source_identity(value), "japanese": expression, "reading": reading,
                "source_blocks": copy.deepcopy(value["blocks"]), "sense_path": None,
                "selection_state": "unresolved", "template": bool(TEMPLATE_RE.search(expression)),
            })
    return sorted(candidates, key=lambda x: (x["parent_id"], x["child_id"], x["relation_path"]))


def select_examples(candidates: list[dict[str, Any]], decisions: list[dict[str, Any]],
                    limit: int = 3, override_reason: str | None = None) -> dict[str, Any]:
    if not 1 <= limit <= 5 or (limit > 3 and not override_reason):
        raise ValueError("example limit requires a reviewed reason above three, maximum five")
    by_key = {(d["parent_id"], d["child_id"], d["relation_path"]): d for d in decisions}
    if len(by_key) != len(decisions):
        raise ValueError("duplicate example decision")
    known = {(c["parent_id"], c["child_id"], c["relation_path"]) for c in candidates}
    if set(by_key) - known:
        raise ValueError("example decision has no candidate")
    selected, rejected, unresolved = [], [], []
    counts: Counter[int] = Counter()
    seen = set()
    for candidate in candidates:
        key = (candidate["parent_id"], candidate["child_id"], candidate["relation_path"])
        decision = by_key.get(key)
        if decision is None:
            unresolved.append(candidate)
            continue
        if decision["source_sha256"] != candidate["source_sha256"]:
            raise ValueError("stale example decision")
        if not decision.get("reviewer") or not decision.get("reason"):
            raise ValueError("example decision requires reviewer and reason")
        if decision.get("state") == "reject":
            rejected.append({**candidate, "reason": decision["reason"]})
            continue
        if decision.get("state") != "accept" or candidate["template"]:
            unresolved.append(candidate)
            continue
        sense = decision.get("sense_path")
        if sense is not None and not decision.get("sense_evidence"):
            raise ValueError("sense attachment requires explicit evidence")
        text_key = (candidate["parent_id"], candidate["japanese"], candidate["reading"])
        if text_key in seen or counts[candidate["parent_id"]] >= limit:
            rejected.append({**candidate, "reason": "duplicate_or_display_limit"})
            continue
        selected.append({**candidate, "selection_state": "accepted", "sense_path": sense,
                         "decision": copy.deepcopy(decision)})
        seen.add(text_key)
        counts[candidate["parent_id"]] += 1
    return {"selected": selected, "rejected": rejected, "unresolved": unresolved,
            "override_reason": override_reason}


def quality_gate(ledger: dict[str, Any], artifact_identity: str) -> dict[str, Any]:
    """Reject missing/stale evidence; contract validity is not semantic approval."""
    errors = []
    if ledger.get("artifact_identity") != artifact_identity:
        errors.append("stale_or_missing_artifact_identity")
    records = ledger.get("issues", [])
    indexed = {r.get("id"): r for r in records}
    if len(indexed) != len(records) or set(indexed) != set(ISSUE_IDS):
        errors.append("issue_inventory_mismatch")
    for issue_id in ISSUE_IDS:
        record = indexed.get(issue_id, {})
        state = record.get("status")
        if state not in {"fixed-with-evidence", "reviewed-limitation"}:
            errors.append(f"{issue_id}:open")
        elif not all(record.get(k) for k in ("reviewer", "evidence", "regression_case")):
            errors.append(f"{issue_id}:missing_evidence")
        elif state == "reviewed-limitation" and (issue_id not in {"WPM-P08", "WPM-P09", "WPM-P10"}
                                                or not record.get("nonblocking_rationale")):
            errors.append(f"{issue_id}:unjustified_limitation")
    for gate in ("implementation_contract", "semantic_review", "lookup_smoke", "user_pilot_approval"):
        evidence = ledger.get(gate, {})
        if evidence.get("passed") is not True or not evidence.get("evidence") or not evidence.get("reviewer"):
            errors.append(f"{gate}:missing")
    return {"version": QUALITY_VERSION, "release_ready": not errors, "blocking_issues": errors}


def run_quality_identity(connection, run_id: int) -> str:
    run = connection.execute(
        "SELECT pipeline_version,prompt_sha256,limits_json,run_identity_sha256 FROM run WHERE id=?", (run_id,),
    ).fetchone()
    if run is None or not run["pipeline_version"].startswith("wadoku-xml-"):
        raise ValueError("quality review requires a Wadoku XML run")
    digest = hashlib.sha256(canonical_json([QUALITY_VERSION, run_id, dict(run)]))
    for row in connection.execute(
        """SELECT tu.id,tu.source_sha256,t.target_sha256,t.id AS translation_id
        FROM translation_unit tu LEFT JOIN translation t ON t.unit_id=tu.id AND t.accepted=1
        WHERE tu.run_id=? ORDER BY tu.id,t.id""", (run_id,),
    ):
        digest.update(b"\n" + canonical_json(dict(row)))
    return digest.hexdigest()


def database_quality_report(connection, run_id: int) -> dict[str, Any]:
    identity = run_quality_identity(connection, run_id)
    event = connection.execute(
        """SELECT details_json FROM audit_event WHERE event_type='wadoku_quality_review'
        AND entity_type='run' AND entity_id=? ORDER BY id DESC LIMIT 1""", (str(run_id),),
    ).fetchone()
    ledger = json.loads(event[0]) if event else {}
    return {"artifact_identity": identity, **quality_gate(ledger, identity)}


def record_quality_review(connection, run_id: int, ledger: dict[str, Any]) -> dict[str, Any]:
    from .db import audit
    identity = run_quality_identity(connection, run_id)
    if ledger.get("artifact_identity") != identity:
        raise ValueError("quality review does not match current accepted data")
    report = quality_gate(ledger, identity)
    audit(connection, "wadoku_quality_review", "run", run_id, ledger)
    return report


def translation_projection(value: dict[str, Any], *, versions: dict[str, str],
                           examples: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Build a new contract without altering legacy raw blocks or unit IDs."""
    from .wadoku_xml import sense_translation_entry
    required = {"labels", "morphology", "examples", "corrections", "prompt", "schema"}
    if set(versions) != required or not all(versions.values()):
        raise ValueError("projection requires every meaning-relevant version")
    projected = sense_translation_entry(value)
    nodes = {path: node for node, path in tree_paths(value["tree"])}
    example_context = copy.deepcopy(examples or [])
    context = {
        "entry_id": value["entry_id"], "source_sha256": source_identity(value),
        "versions": {**versions, "projection": PROJECTION_VERSION},
        "source_tree": copy.deepcopy(value["tree"]),
        "article_group_decision": copy.deepcopy(value.get("article_group_decision")),
        "lookup_aliases": copy.deepcopy(value.get("lookup_aliases", [])),
        "source_correction_audit": copy.deepcopy(value.get("source_correction_audit")),
        "examples": example_context,
    }
    context_hash = sha256_bytes(canonical_json(context))
    units = []
    for index, block in enumerate(projected["blocks"]):
        if not block["has_translatable_text"]:
            continue
        scope = block["sense_path"]
        member_paths = block.get("member_paths", [block["xml_path"]])
        identity = {"context_sha256": context_hash, "source_text": block["prompt_text"],
                    "role": block["role"], "member_paths": member_paths}
        units.append({
            "unit_id": "wdq-" + sha256_bytes(canonical_json(identity))[:32],
            "role": block["role"], "source_text": block["prompt_text"],
            "source_sha256": sha256_bytes(block["prompt_text"].encode()),
            "context_sha256": context_hash, "projection_index": index,
            "member_paths": member_paths, "sense_path": scope,
            "sense_context": copy.deepcopy(nodes.get(scope)) if scope else None,
            "protected_tokens": [f["placeholder"] for f in block["protected_fragments"]],
            "protected_fragment_context": copy.deepcopy(block["protected_fragments"]),
        })
    paths = [path for block in projected["blocks"] for path in block.get("member_paths", [block["xml_path"]])]
    expected = [b["xml_path"] for b in value["blocks"]]
    if Counter(paths) != Counter(expected):
        raise ValueError("projection lost or duplicated source blocks")
    return {"version": PROJECTION_VERSION, "context": context, "context_sha256": context_hash,
            "render_value": projected, "units": units}


def example_translation_unit(candidate: dict[str, Any], source_path: str) -> dict[str, Any]:
    """One reviewed source meaning, not a guessed mixture of child senses."""
    if candidate.get("selection_state") != "accepted":
        raise ValueError("example is not accepted")
    block = next((b for b in candidate["source_blocks"] if b["xml_path"] == source_path), None)
    if block is None or block["role"] not in {"translation", "definition"}:
        raise ValueError("example source path is not a learner-facing meaning")
    if not candidate.get("decision", {}).get("source_path") == source_path:
        raise ValueError("example translation path was not reviewed")
    context_hash = sha256_bytes(canonical_json(candidate))
    return {"unit_id": "wde-" + sha256_bytes(canonical_json([context_hash, source_path]))[:32],
            "role": "example_translation", "source_text": block["prompt_text"],
            "source_sha256": sha256_bytes(block["prompt_text"].encode()),
            "context_sha256": context_hash, "japanese": candidate["japanese"],
            "reading": candidate["reading"], "parent_id": candidate["parent_id"],
            "child_id": candidate["child_id"], "sense_path": candidate["sense_path"],
            "source_path": source_path, "protected_tokens": [f["placeholder"] for f in block["protected_fragments"]],
            "protected_fragment_context": copy.deepcopy(block["protected_fragments"])}


def apply_source_corrections(value: dict[str, Any], corrections: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a derived tree and immutable audit; source data is never rewritten."""
    derived = copy.deepcopy(value)
    nodes = {path: node for node, path in tree_paths(derived["tree"])}
    original_hash = source_identity(value)
    seen = set()
    audit = []
    for correction in corrections:
        if correction["entry_id"] != value["entry_id"]:
            raise ValueError("correction entry differs")
        if correction["source_sha256"] != original_hash:
            raise ValueError("stale source correction")
        if not all(correction.get(k) for k in ("reviewer", "reason", "evidence", "version")):
            raise ValueError("source correction lacks review evidence")
        path, attribute = correction["path"], correction["attribute"]
        if (path, attribute) in seen:
            raise ValueError("duplicate source correction")
        seen.add((path, attribute))
        node = nodes.get(path)
        if node is None or node["tag"] not in {"ref", "sref"} or attribute != "type":
            raise ValueError("only reviewed reference-type overlays are supported")
        if node["attributes"].get(attribute) != correction["original"]:
            raise ValueError("source correction original differs")
        if correction["replacement"] not in {"syn", "anto", "other"}:
            raise ValueError("unsupported reference correction")
        node["attributes"][attribute] = correction["replacement"]
        # References can also exist inside protected block fragments.
        for block in derived["blocks"]:
            if not path.startswith(block["xml_path"] + "/"):
                continue
            original_node = dict((p, n) for n, p in tree_paths(value["tree"]))[path]
            matches = [f for f in block["protected_fragments"] if f["tree"] == original_node]
            if len(matches) != 1:
                raise ValueError("correction cannot resolve exact protected fragment")
            matches[0]["tree"] = copy.deepcopy(node)
        audit.append(copy.deepcopy(correction))
    derived["source_correction_audit"] = {"source_sha256": original_hash, "corrections": audit}
    return derived
