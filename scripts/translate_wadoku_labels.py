#!/usr/bin/env python3
"""Create the reviewed-input draft for Wadoku controlled labels with Luna CLI workers."""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from jitendex_ru.util import atomic_write, canonical_json


CODEX = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
REVIEWED_OVERRIDES = {
    ("usage", "dom:Familienn.."): ("Familienname", "фамилия", "XML context and Wadoku naming domain"),
    ("usage", "dom:Machinenb."): ("Maschinenbau", "машиностроение", "clear source spelling error"),
    ("usage", "dom:Wz."): ("Warenzeichen", "торговая марка", "product-name XML contexts"),
    ("reference", "sref:type:"): ("Kurzverweis", "краткая ссылка", "sref element without subtype"),
    ("reference", "subentrytype:ZSprW"): ("Sprichwort", "пословица", "XSD documentation: Z_Sprichwort"),
    ("reference", "subentrytype:e"): ("Form mit Partikel へ", "форма с частицей へ", "XML reference context"),
    ("reference", "subentrytype:da"): ("Ableitung mit だ", "производное с だ", "XSD documentation"),
    ("reference", "subentrytype:ge"): ("Ableitung mit げ", "производное с げ", "XSD documentation"),
    ("reference", "subentrytype:mi"): ("Nominalisierung mit み", "номинализация с み", "XSD documentation"),
    ("reference", "subentrytype:o"): ("Ableitung mit Präfix お", "производное с префиксом お", "XSD documentation"),
    ("reference", "subentrytype:shite"): ("Ableitung mit して", "производное с して", "XSD documentation"),
    ("reference", "subentrytype:tail"): ("Komponentenende", "конец компонента", "XSD documentation"),
}


def review_existing(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    changed = 0
    for item in value["items"]:
        override = REVIEWED_OVERRIDES.get((item["category"], item["code"]))
        if override is None:
            continue
        item["de"], item["ru"], item["note"] = override
        item["confidence"] = "high"
        item["reviewed"] = True
        changed += 1
    if changed != len(REVIEWED_OVERRIDES):
        raise ValueError("not every reviewed Wadoku label override matched")
    atomic_write(path, canonical_json(value) + b"\n")
    return {"reviewed_overrides": changed, "output": str(path)}


def output_schema(items: list[dict[str, Any]]) -> dict[str, Any]:
    variants = []
    for item in items:
        properties = {
            "category": {"type": "string", "const": item["category"]},
            "code": {"type": "string", "const": item["code"]},
            "de": {"type": "string", "minLength": 1},
            "ru": {"type": "string", "minLength": 1},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "note": {"type": ["string", "null"]},
        }
        variants.append({
            "type": "object", "additionalProperties": False,
            "required": list(properties), "properties": properties,
        })
    return {
        "type": "object", "additionalProperties": False,
        "required": ["items"], "properties": {"items": {
            "type": "array", "minItems": len(items), "maxItems": len(items),
            "items": {"anyOf": variants},
        }},
    }


def run_worker(
    number: int, items: list[dict[str, Any]], prompt: str, output_dir: Path,
) -> dict[str, Any]:
    output = output_dir / f"labels-{number:02d}.json"
    events = output_dir / f"labels-{number:02d}.events.jsonl"
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".json") as schema:
        json.dump(output_schema(items), schema, ensure_ascii=False)
        schema.flush()
        command = [
            str(CODEX), "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules",
            "--skip-git-repo-check", "-s", "read-only", "-C", "/private/tmp",
            "-m", "gpt-5.6-luna", "-c", 'model_reasoning_effort="medium"',
            "--output-schema", schema.name, "--json", "-o", str(output), "-",
        ]
        supplied = canonical_json({"items": items}).decode()
        completed = subprocess.run(
            command, input=f"{prompt.rstrip()}\n\nSUPPLIED ITEMS\n{supplied}",
            capture_output=True, text=True, timeout=240, check=False,
        )
    atomic_write(events, completed.stdout.encode("utf-8"))
    if completed.returncode or not output.is_file():
        raise RuntimeError(f"label worker {number} failed: {completed.stderr[-2000:]}")
    result = json.loads(output.read_text(encoding="utf-8"))
    actual = [(item["category"], item["code"]) for item in result["items"]]
    expected = [(item["category"], item["code"]) for item in items]
    if actual != expected:
        raise ValueError(f"label worker {number} changed label identity or order")
    return {"number": number, "items": result["items"]}


def execute(args: argparse.Namespace) -> dict[str, Any]:
    if args.review_existing:
        return review_existing(args.output)
    report = json.loads(args.report.read_text(encoding="utf-8"))
    missing = report["missing_controlled_labels"]
    if not missing:
        return {"workers": 0, "items": 0, "output": str(args.output)}
    args.work_dir.mkdir(parents=True, exist_ok=True)
    prompt = args.prompt.read_text(encoding="utf-8")
    chunks = [missing[index::args.workers] for index in range(args.workers)]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(
            lambda pair: run_worker(pair[0], pair[1], prompt, args.work_dir),
            enumerate(chunks, 1),
        ))
    translated = {
        (item["category"], item["code"]): item
        for result in results for item in result["items"]
    }
    if len(translated) != len(missing):
        raise ValueError("Luna label results are incomplete or duplicated")
    existing_items = []
    if args.output.is_file():
        existing_items = json.loads(args.output.read_text(encoding="utf-8")).get("items", [])
    combined = {(item["category"], item["code"]): item for item in existing_items}
    for source in missing:
        translated_item = translated[(source["category"], source["code"])]
        combined[(source["category"], source["code"])] = {
            "code": source["code"], "category": source["category"],
            "de": translated_item["de"], "ru": translated_item["ru"],
            "confidence": translated_item["confidence"], "note": translated_item["note"],
            "occurrences": source["occurrences"], "translation_source": "gpt-5.6-luna",
        }
    category_order = {"section": 5, "grammar": 10, "usage-category": 20, "usage": 30,
                      "orthography": 40, "reference": 50}
    items = []
    for offset, key in enumerate(sorted(combined, key=lambda item: (
        category_order.get(item[0], 99), item[1],
    ))):
        item = dict(combined[key])
        item["order"] = category_order.get(item["category"], 99) * 10_000 + offset
        items.append(item)
    atomic_write(args.output, canonical_json({"schema_version": 1, "items": items}) + b"\n")
    return {
        "workers": len(results), "items": len(items), "output": str(args.output),
        "confidence": {level: sum(item["confidence"] == level for item in items)
                       for level in ("high", "medium", "low")},
    }


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    value.add_argument("--report", type=Path, required=True)
    value.add_argument("--prompt", type=Path, required=True)
    value.add_argument("--output", type=Path, required=True)
    value.add_argument("--work-dir", type=Path, required=True)
    value.add_argument("--workers", type=int, default=4)
    value.add_argument("--review-existing", action="store_true")
    return value


def main() -> None:
    print(json.dumps(execute(parser().parse_args()), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
