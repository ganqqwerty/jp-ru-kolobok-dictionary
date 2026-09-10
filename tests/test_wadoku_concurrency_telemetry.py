import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import threading

import pytest

from jitendex_ru.wadoku_telemetry import Journal
from jitendex_ru.wadoku_quality import lexical_contract
from jitendex_ru.validate_response import wadoku_lexical_contract_issues
import wadoku_translate_window as runner
import jitendex_ru.wadoku_telemetry as telemetry


def test_incomplete_run_is_failure_not_success():
    runner.require_complete({'missing_units': []})
    with pytest.raises(RuntimeError, match='1 units missing'):
        runner.require_complete({'missing_units': ['u']})


def test_transport_failure_enters_shared_retry_state(monkeypatch, tmp_path):
    request = tmp_path / 'request.json'
    request.write_text(json.dumps({'articles': [{'sequence': 7}]}))
    state = {'batch': 'leased'}
    class Connection:
        def execute(self, sql, params):
            if sql.startswith('SELECT attempt_count'):
                return SimpleNamespace(fetchone=lambda: {'attempt_count':1,'state':state['batch']})
            if sql.startswith('UPDATE batch'):
                assert "state='retryable'" in sql
                assert "lease_token=?" in sql
                state['batch'] = 'retryable'
            return SimpleNamespace(rowcount=1)
        def commit(self): pass
        def close(self): pass
    db = SimpleNamespace(connect=Connection)
    item = {'attempt_id':'a','batch_id':'b','lease_token':'lease','response_path':str(tmp_path/'response.json')}
    monkeypatch.setattr(runner, 'retry_prompt', lambda *args: 'prompt')
    monkeypatch.setattr(runner, 'build_output_schema', lambda *args: {})
    monkeypatch.setattr(runner, 'claim', lambda *args, **kwargs: item)
    monkeypatch.setattr(runner, 'save_runtime_prompt', lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, 'logged_dispatch', lambda *args, **kwargs: SimpleNamespace(
        stdout='',stderr='Selected model is at capacity',returncode=1,usage=None))
    def retry(*args, **kwargs):
        assert state['batch'] == 'retryable'
        state['batch'] = 'ready'
        return {'requeued':True}
    monkeypatch.setattr(runner, 'retry_or_split', retry)
    monkeypatch.setattr(runner.time, 'sleep', lambda seconds: None)
    runner.dispatch_batch(None,1,{'id':'b','manifest_path':str(request)},tmp_path,'prompt',128000,db)
    assert state['batch'] == 'ready'


def test_transport_exhaustion_never_splits(monkeypatch):
    queries=[]
    class Connection:
        def execute(self, sql, params):
            queries.append(sql)
            return SimpleNamespace(fetchone=lambda: {'attempt_count':3,'state':'retryable'})
        def commit(self): pass
    monkeypatch.setattr(runner, 'retry_or_split', lambda *a, **k: pytest.fail('must not split transport failures'))
    result = runner.recover_transport(Connection(), 'b')
    assert result['blocked'] and not result['split']
    assert "state='blocked'" in queries[-1]


def test_stage_name_can_be_logged(monkeypatch, tmp_path):
    journal = Journal(tmp_path / 'events.jsonl', 'pipeline')
    monkeypatch.setattr(telemetry, '_journal', journal)
    telemetry.event('stage_started', name='classification-pass1')
    assert json.loads(journal.path.read_text())['name'] == 'classification-pass1'


def test_journal_records_one_hundred_workers_and_errors(tmp_path):
    journal = Journal(tmp_path / 'events.jsonl', 'test')
    barrier = threading.Barrier(100)
    def task(i):
        journal.emit('worker_started', attempt_id=str(i))
        barrier.wait(timeout=10)
        journal.emit('worker_finished', attempt_id=str(i), duration_s=1, returncode=1 if i == 0 else 0)
    with ThreadPoolExecutor(max_workers=100) as pool:
        list(pool.map(task, range(100)))
    journal.finish('completed')
    rows = [json.loads(line) for line in journal.path.read_text().splitlines()]
    assert len(rows) == 201 and journal.peak == 100 and journal.active == 0
    assert all('utc' in r and 'elapsed_s' in r for r in rows)
    assert len([r for r in rows if r.get('returncode') == 1]) == 1


def test_dispatch_drains_other_workers_after_one_exception(monkeypatch, tmp_path):
    states = {str(i): 'ready' for i in range(100)}
    barrier = threading.Barrier(100)
    completed = []
    class DB:
        metrics = SimpleNamespace(snapshot=lambda: {})
        def __init__(self, config): pass
        def close(self): pass
    monkeypatch.setattr(runner, 'Database', DB)
    monkeypatch.setattr(runner, 'window_batches', lambda *args: [{'id': i, 'state': s} for i,s in states.items()])
    def dispatch(config,run_id,batch,work,prompt,budget,database):
        states[batch['id']] = 'leased'
        barrier.wait(timeout=10)
        completed.append(batch['id'])
        states[batch['id']] = 'done'
        if batch['id'] == '0':
            raise ValueError('simulated failure')
    monkeypatch.setattr(runner, 'dispatch_batch', dispatch)
    with pytest.raises(RuntimeError, match='1 batches failed'):
        runner.dispatch_window(None,SimpleNamespace(commit=lambda: None),1,1,tmp_path,'prompt',128000,100,100)
    assert len(completed) == 100


def test_lexical_guard_catches_inline_german_without_banning_notation():
    unit = {'unit_id': 'u', 'source_lexical_terms': ['Soldat'], 'required_literals': []}
    assert wadoku_lexical_contract_issues(unit, ['храбрый Soldat'])[0]['code'] == 'untranslated_source_lexeme'
    assert not wadoku_lexical_contract_issues(unit, ['храбрый солдат'])
    assert not wadoku_lexical_contract_issues({'unit_id':'u','required_literals':['fp']}, ['форте-пиано (fp)'])
    assert wadoku_lexical_contract_issues({'unit_id':'u','required_literals':['fp']}, ['форте-пиано'])[0]['code'] == 'required_literal_missing'
    assert not wadoku_lexical_contract_issues({'unit_id':'u'}, ['вид Homo sapiens'])


def test_scientific_annotations_are_not_ordinary_lexical_terms():
    node = lambda tag,text,attrs={}: {'tag':tag,'text':text,'attributes':attrs,'children':[],'tail':''}
    root = '/entry[1]/sense[1]'
    nodes = {root:node('sense',''),root+'/tr[1]':node('tr','',{'langdesc':'scientific'}),
             root+'/tr[1]/token[1]':node('token','Homo sapiens',{'type':'N'})}
    assert lexical_contract(nodes, root, [root+'/tr[1]'], 'Homo sapiens')['source_lexical_terms'] == []
