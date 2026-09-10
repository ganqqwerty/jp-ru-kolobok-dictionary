from pathlib import Path
from types import SimpleNamespace

import pytest

from jitendex_ru.util import sha256_file
from jitendex_ru.wadoku_profile import load_profile, resolve_prompt


def test_new_profile_is_rich_and_old_profile_is_not_reinterpreted():
    config = load_profile()
    assert resolve_prompt(config, 'translation').name == 'translate_luna_wadoku_xml_ru_v14.txt'
    assert resolve_prompt(config, 'review').name == 'review_luna_wadoku_xml_ru_v9.txt'
    with pytest.raises(ValueError, match='wadoku-xml-v3'):
        load_profile(Path('config.wadoku.xml.luna.toml'))


def test_resumed_run_uses_frozen_prompt_not_new_default(tmp_path):
    prompts = tmp_path/'prompts'
    prompts.mkdir()
    old, new = prompts/'old.txt', prompts/'new.txt'
    old.write_text('original immutable prompt')
    new.write_text('new default')
    config = SimpleNamespace(root=tmp_path, raw={'rich': {'prompts': {'translation': 'prompts/new.txt'}}})
    assert resolve_prompt(config, 'translation') == new
    assert resolve_prompt(config, 'translation', frozen_sha256=sha256_file(old)) == old
    assert resolve_prompt(config, 'translation', old, frozen_sha256=sha256_file(old)) == old
    with pytest.raises(ValueError, match='explicit prompt differs'):
        resolve_prompt(config, 'translation', new, frozen_sha256=sha256_file(old))
    with pytest.raises(ValueError, match='frozen prompt unavailable'):
        resolve_prompt(config, 'translation', frozen_sha256='missing')
