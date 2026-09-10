import sqlite3

import pytest
import wadoku_review_window as driver


@pytest.mark.parametrize('attempts,state', [(1, 'ready'), (3, 'blocked')])
def test_invalid_review_rolls_back_partial_writes_and_bounds_retry(monkeypatch, attempts, state):
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript('''
        CREATE TABLE batch(id TEXT,state TEXT,lease_token TEXT,lease_expires_at TEXT,attempt_count INT);
        CREATE TABLE attempt(id TEXT,batch_id TEXT,outcome TEXT,lease_token TEXT,completed_at TEXT,error_json TEXT);
        CREATE TABLE partial_review(value TEXT);
    ''')
    c.execute("INSERT INTO batch VALUES ('b','leased','lease',NULL,?)", (attempts,))
    c.execute("INSERT INTO attempt VALUES ('a','b','claimed','lease',NULL,NULL)")
    c.commit()
    events = []
    monkeypatch.setattr(driver, 'audit', lambda *args: events.append(args[-1]))

    def reject(connection, path):
        connection.execute("INSERT INTO partial_review VALUES ('must rollback')")
        raise ValueError('invalid typed replacement')

    monkeypatch.setattr(driver, 'ingest_review', reject)
    item = {'attempt_id': 'a', 'batch_id': 'b', 'response_path': 'fixture.json'}
    outcome = driver.ingest_or_retry(c, item)
    assert outcome['next_state'] == state
    assert c.execute('SELECT count(*) FROM partial_review').fetchone()[0] == 0
    assert tuple(c.execute('SELECT state,lease_token FROM batch').fetchone()) == (state, None)
    assert c.execute('SELECT outcome FROM attempt').fetchone()[0] == 'rejected'
    assert events[0]['reason'] == 'invalid typed replacement'
    # A stale response must never requeue or change the completed rejection.
    with pytest.raises(ValueError):
        driver.ingest_or_retry(c, item)
    assert c.execute('SELECT state FROM batch').fetchone()[0] == state
    c.close()
