#!/usr/bin/env python3
"""Freeze and store the first N Wadoku source entries."""
import argparse
import json
from pathlib import Path

from jitendex_ru.database import Database
from jitendex_ru.util import atomic_write, canonical_json
from jitendex_ru.wadoku_profile import load_profile
from jitendex_ru.wadoku_scope import linked_prefix_inventory, prefix_inventory, store_scope


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--size', type=int, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--link-closed', action='store_true')
    args = parser.parse_args()
    config = load_profile(Path('config.wadoku.rich.luna.toml'))
    source = Path('work/wadoku-xml/source/wadoku-xml-20260705/wadoku.xml')
    builder = linked_prefix_inventory if args.link_closed else prefix_inventory
    data = builder(source, expected_sha256=config.raw['source']['xml_sha256'], size=args.size)
    db = Database(config)
    c = db.connect()
    try:
        snapshot = c.execute("SELECT id FROM source_snapshot WHERE kind='wadoku' ORDER BY id DESC LIMIT 1").fetchone()
        if not snapshot:
            raise ValueError('Wadoku source snapshot is absent')
        result = store_scope(c, int(snapshot[0]), data)
        c.commit()
        atomic_write(args.output, canonical_json(data))
        print(json.dumps(result), flush=True)
    finally:
        c.close()
        db.close()


if __name__ == '__main__':
    main()
