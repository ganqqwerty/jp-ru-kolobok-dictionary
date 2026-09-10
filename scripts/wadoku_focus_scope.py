"""Freeze a deterministic 100-entry stress sample; selection is not classification."""
import json
import argparse
import copy
import re
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from jitendex_ru.database import Database
from jitendex_ru.util import atomic_write, canonical_json, sha256_bytes, sha256_file
from jitendex_ru.wadoku_xml import canonical_entry
from jitendex_ru.wadoku_quality import example_candidates
from jitendex_ru.wadoku_profile import load_profile
from jitendex_ru.wadoku_scope import store_scope


def scan(path):
    ordinal = 0
    for _, elem in ET.iterparse(path, events=("end",)):
        if elem.tag.rsplit("}", 1)[-1] == "entry":
            ordinal += 1
            yield ordinal, elem
            elem.clear()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repeat-from', type=Path)
    parser.add_argument('--revision')
    parser.add_argument('--output', type=Path, default=Path('work/wadoku-xml/focus100/scope.json'))
    args = parser.parse_args()
    config = load_profile(Path("config.wadoku.rich.luna.toml"))
    from psycopg.conninfo import conninfo_to_dict
    if conninfo_to_dict(config.database_url()).get("dbname") != "wadoku_rich_pilot":
        raise ValueError("isolated pilot PostgreSQL required")
    if args.repeat_from:
        if not args.revision or args.output.resolve() == args.repeat_from.resolve():
            raise ValueError('repeat requires a revision and a separate output file')
        data = repeat_scope(json.loads(args.repeat_from.read_text()), args.revision)
        db = Database(config)
        c = db.connect()
        try:
            baseline = data['manifest']['repeated_scope']
            snapshot = c.execute('SELECT snapshot_id FROM wadoku_scope WHERE id=?', (baseline,)).fetchone()[0]
            result = store_scope(c, snapshot, data)
            c.commit()
            atomic_write(args.output, canonical_json(data))
            print(json.dumps(result))
        finally:
            c.close()
            db.close()
        return
    source = Path("work/wadoku-xml/source/wadoku-xml-20260705/wadoku.xml")
    digest = sha256_file(source)
    if digest != config.raw["source"]["xml_sha256"]:
        raise ValueError("XML hash mismatch")
    old = json.loads(Path("work/wadoku-xml/pilot-v6/scope-193.json").read_text())
    excluded = {e["entry_id"] for e in old["entries"]}
    pools = {k: [] for k in ("open-template", "suffix", "phrase")}
    summaries = {}
    for ordinal, elem in scan(source):
        eid = int(elem.attrib["id"])
        orths = ["".join(n.itertext()) for n in elem.findall("{*}form/{*}orth")]
        if not orths:
            continue
        word = orths[0]
        refs = [dict(n.attrib) for n in elem.iter() if n.tag.rsplit("}",1)[-1] in {"ref","sref"} and n.get("id")]
        summaries[eid] = {"expression": word, "reading": elem.findtext("{*}form/{*}reading/{*}hira", ""), "refs": refs}
        if eid in excluded:
            continue
        if any(re.search(r"[…~〜～]", w) for w in orths):
            category = "open-template"
        elif elem.find(".//{*}suffix") is not None:
            category = "suffix"
        elif len(word) >= 5 and re.search(r"[をにがのともでは]", word) and any(r.get("type")=="main" for r in refs):
            category = "phrase"
        else:
            continue
        pools[category].append(eid)
    selected = {}
    for category, count in (("open-template",40),("suffix",20),("phrase",40)):
        ids = sorted(pools[category], key=lambda eid: sha256_bytes(f"wadoku-focus100-v1:{eid}".encode()))
        if len(ids) < count:
            raise ValueError(f"insufficient {category}: {len(ids)}")
        selected.update({eid: [category] for eid in ids[:count]})
    parents = {int(r["id"]) for eid in selected for r in summaries[eid]["refs"] if r.get("type")=="main"}
    entries, candidates, contexts = [], [], {}
    for ordinal, elem in scan(source):
        eid = int(elem.attrib["id"])
        relevant = eid in selected or eid in parents or any(n.get("type")=="main" and int(n.get("id","0")) in selected for n in elem.findall(".//{*}ref"))
        if not relevant:
            continue
        value = canonical_entry(elem)
        if eid in selected:
            entries.append({"ordinal":ordinal,"entry_id":eid,"categories":selected[eid],"source":value,"source_sha256":sha256_bytes(canonical_json(value))})
        if eid in parents:
            contexts[str(eid)] = value
        candidates.extend(example_candidates([value],set(selected)))
    manifest = {"version":"wadoku-focused-scope-v1","xml_sha256":digest,"entry_count":len(entries),
        "source_entry_count":len(summaries),"candidate_count":len(candidates),"category_counts":dict(Counter(c for cs in selected.values() for c in cs)),
        "parent_contexts":contexts,"selection_seed":"wadoku-focus100-v1","excluded_scope":old["manifest"]["scope_id"],
        "reference_targets":{str(int(r["id"])):{k:summaries[int(r["id"])][k] for k in ("expression","reading")} for eid in selected for r in summaries[eid]["refs"] if int(r["id"]) in summaries}}
    if len(entries)!=100 or sha256_file(source)!=digest:
        raise ValueError("incomplete or changed source")
    manifest["scope_id"]=sha256_bytes(canonical_json([manifest,[(e["entry_id"],e["source_sha256"]) for e in entries]]))
    data={"manifest":manifest,"entries":entries,"candidates":candidates}
    db=Database(config)
    c=db.connect()
    try:
        snapshot=c.execute("SELECT snapshot_id FROM wadoku_scope WHERE id=?",(old["manifest"]["scope_id"],)).fetchone()[0]
        result=store_scope(c,snapshot,data)
        c.commit()
        atomic_write(args.output,canonical_json(data))
        print(json.dumps(result),flush=True)
    finally:
        c.close()
        db.close()


def repeat_scope(original, revision):
    """Repeat the frozen sources, not the random selection or old decisions."""
    data = copy.deepcopy(original)
    manifest = data['manifest']
    baseline = manifest.pop('scope_id')
    for entry in data['entries']:
        if sha256_bytes(canonical_json(entry['source'])) != entry['source_sha256']:
            raise ValueError('frozen source hash mismatch')
    manifest.update(repeated_scope=baseline, repeat_revision=revision)
    manifest['scope_id'] = sha256_bytes(canonical_json([manifest, [
        (e['entry_id'], e['source_sha256']) for e in data['entries']]]))
    if manifest['scope_id'] == baseline:
        raise ValueError('repeat must have a new identity')
    return data


if __name__ == "__main__":
    main()
