from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from typing import Any


def validate_archive(path: Path, schema_dir: Path) -> dict[str, int]:
    try:
        import fastjsonschema
    except ImportError as error:  # pragma: no cover - packaging guarantees this dependency
        raise RuntimeError("fastjsonschema is required for pinned Yomitan schema validation") from error
    schema_files = {
        "term": "dictionary-term-bank-v3-schema.json",
        "term_meta": "dictionary-term-meta-bank-v3-schema.json",
        "tag": "dictionary-tag-bank-v3-schema.json",
    }
    index_validator = fastjsonschema.compile(json.loads(
        (schema_dir / "dictionary-index-schema.json").read_text(encoding="utf-8")
    ))
    validators = {
        family: fastjsonschema.compile(json.loads((schema_dir / filename).read_text(encoding="utf-8")))
        for family, filename in schema_files.items() if (schema_dir / filename).is_file()
    }
    bank_re = re.compile(r"^(term|term_meta|tag)_bank_(\d+)\.json$")
    numbers: dict[str, list[int]] = {family: [] for family in schema_files}
    with zipfile.ZipFile(path) as archive:
        index_validator(json.loads(archive.read("index.json")))
        for name in archive.namelist():
            match = bank_re.fullmatch(name)
            if match:
                family, number = match.group(1), int(match.group(2))
                if family not in validators:
                    raise ValueError(f"missing pinned schema for {family} bank")
                validators[family](json.loads(archive.read(name)))
                numbers[family].append(number)
            elif "_bank_" in name and name.endswith(".json"):
                raise ValueError(f"unknown Yomitan bank: {name}")
    if not numbers["term"]:
        raise ValueError("archive has no term banks")
    for family, actual in numbers.items():
        ordered = sorted(actual)
        if actual and ordered != list(range(1, len(ordered) + 1)):
            raise ValueError(f"non-consecutive {family} bank numbering: {ordered}")
    return {
        "schema_validated_banks": sum(map(len, numbers.values())),
        "term_banks": len(numbers["term"]),
        "term_meta_banks": len(numbers["term_meta"]),
        "tag_banks": len(numbers["tag"]),
    }
