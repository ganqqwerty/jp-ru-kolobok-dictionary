import json
import sqlite3

from jitendex_ru.wadoku_retry import retry_prompt


def test_runtime_prompt_is_saved_with_base_and_runtime_hashes(tmp_path, monkeypatch):
    import jitendex_ru.wadoku_retry as retry
    from jitendex_ru.util import sha256_file, sha256_bytes
    events = []
    monkeypatch.setattr(retry, 'audit', lambda *args: events.append(args))
    item = {'attempt_id': 'a', 'response_path': str(tmp_path/'a.json')}
    retry.save_runtime_prompt(None, item, 'base plus diagnostic feedback', 'base', schema={'type': 'object'})
    event = events[0][-1]
    assert event['runtime_sha256'] == sha256_file(tmp_path/'a.prompt.txt')
    assert event['base_sha256'] == sha256_bytes(b'base')
    assert event['validator_feedback'] is True
    assert event['schema_sha256'] == sha256_file(tmp_path/'a.schema.json')


def test_feedback_is_latest_rejection_bound_to_batch_and_bounded():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.execute('CREATE TABLE attempt(id TEXT,batch_id TEXT,outcome TEXT,created_at INT,error_json TEXT)')
    assert retry_prompt(c, 'batch', 'base') == 'base'
    c.execute('INSERT INTO attempt VALUES (?,?,?,?,?)', ('other', 'other-batch', 'rejected', 9, '{}'))
    c.execute('INSERT INTO attempt VALUES (?,?,?,?,?)', ('first', 'batch', 'rejected', 1, '{"code":"old"}'))
    c.execute('INSERT INTO attempt VALUES (?,?,?,?,?)', ('last', 'batch', 'rejected', 2, '{"code":"wrong_placeholder"}'))
    prompt = retry_prompt(c, 'batch', 'base')
    assert prompt.startswith('base\n\n')
    data = json.loads(prompt.split('\n')[-1])
    assert data == {'previous_attempt_id': 'last', 'validation_errors': {'code': 'wrong_placeholder'}}
    c.execute('UPDATE attempt SET error_json=? WHERE id=?', (json.dumps({'huge': 'x' * 15000}), 'last'))
    prompt = retry_prompt(c, 'batch', 'base')
    assert len(prompt.encode()) < 12000
    assert json.loads(prompt.split('\n')[-1])['previous_attempt_id'] == 'last'
    c.close()
