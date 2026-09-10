"""Explicit rich profile and immutable prompt resolution for resumed runs."""
from pathlib import Path

from .config import Config
from .util import sha256_file


DEFAULT_PROFILE = Path('config.wadoku.rich.luna.toml')


def load_profile(path=DEFAULT_PROFILE):
    config = Config.load(path)
    if config.raw.get('versions', {}).get('pipeline') != 'wadoku-xml-v3':
        raise ValueError('new rich commands require a wadoku-xml-v3 profile')
    return config


def resolve_prompt(config, role, explicit=None, *, frozen_sha256=None):
    selected = Path(explicit or config.raw['rich']['prompts'][role])
    if not selected.is_absolute():
        selected = config.root / selected
    if frozen_sha256 and sha256_file(selected) != frozen_sha256:
        if explicit is not None:
            raise ValueError('explicit prompt differs from frozen run')
        matches = sorted(p for p in (config.root/'prompts').glob('*.txt')
                         if sha256_file(p) == frozen_sha256)
        if not matches:
            raise ValueError('frozen prompt unavailable; supply the original --prompt file')
        selected = matches[0]
    return selected
