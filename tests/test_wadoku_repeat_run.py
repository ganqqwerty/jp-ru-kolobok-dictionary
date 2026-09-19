from pathlib import Path

from wadoku_repeat_run import translation_run_id


def test_translation_run_id_accepts_window_summary(tmp_path: Path):
    log = tmp_path / 'translation.log'
    log.write_text('{"prepared":{"run_id":33}}\n{"run_id":33,"articles":5000}\n')
    assert translation_run_id(log) == 33


def test_translation_run_id_accepts_prepared_only_noop_resume(tmp_path: Path):
    log = tmp_path / 'translation.log'
    log.write_text('{"prepared":{"run_id":33,"created":false}}\n{"window":{"state":"no_ready_batches"}}\n')
    assert translation_run_id(log) == 33
