import threading
import time
from pathlib import Path

import pytest
import wadoku_translate_window as driver


def test_parallel_dispatch_stays_in_window_and_bounds_workers(monkeypatch):
    rows = [{'id': str(i), 'state': 'ready'} for i in range(7)]
    lock = threading.Lock()
    active = peak = 0
    seen = []
    monkeypatch.setattr(driver, 'window_batches', lambda c, r, o: rows)

    def dispatch(config, run_id, batch, work, prompt, budget):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            seen.append(batch['id'])
        time.sleep(0.01)
        with lock:
            rows[int(batch['id'])]['state'] = 'complete'
            active -= 1

    monkeypatch.setattr(driver, 'dispatch_batch', dispatch)
    driver.dispatch_window(None, None, 16, 7, Path('.'), '', 128000, 7, 3)
    assert 1 < peak <= 3
    assert sorted(seen) == [str(i) for i in range(7)]


def test_parallel_dispatch_waits_for_siblings_after_failure(monkeypatch):
    rows = [{'id': str(i), 'state': 'ready'} for i in range(4)]
    saved = []
    monkeypatch.setattr(driver, 'window_batches', lambda c, r, o: rows)

    def dispatch(config, run_id, batch, work, prompt, budget):
        if batch['id'] == '0':
            raise ValueError('fixture worker failure')
        time.sleep(0.01)
        saved.append(batch['id'])

    monkeypatch.setattr(driver, 'dispatch_batch', dispatch)
    with pytest.raises(ValueError, match='fixture worker failure'):
        driver.dispatch_window(None, None, 16, 7, Path('.'), '', 128000, 4, 3)
    assert sorted(saved) == ['1', '2']
