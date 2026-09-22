from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

from jitendex_ru.build_dictionary import YOMITAN_SMOKE_CHECKS
from jitendex_ru.yomitan_remediation import validate_yomitan_metadata


BUILD_SITE_PATH = Path(__file__).parents[1] / "terra-luna-site" / "build_site.py"
SPEC = importlib.util.spec_from_file_location("terra_luna_build_site", BUILD_SITE_PATH)
assert SPEC is not None and SPEC.loader is not None
BUILD_SITE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD_SITE)


def test_metadata_markup_has_social_card_and_schema() -> None:
    markup = BUILD_SITE.metadata_markup("Сравнение", "Описание", "/jpdb-top-5k/")

    canonical_url = (
        "https://ganqqwerty.github.io/jp-ru-kolobok-dictionary/"
        "comparison/jpdb-top-5k/"
    )
    assert f'<link rel="canonical" href="{canonical_url}">' in markup
    assert '<meta name="twitter:card" content="summary_large_image">' in markup
    assert '<meta property="og:image:width" content="1724">' in markup
    assert BUILD_SITE.SOCIAL_IMAGE_URL in markup

    match = re.search(
        r'<script type="application/ld\+json">(.*?)</script>',
        markup,
        flags=re.DOTALL,
    )
    assert match is not None
    structured_data = json.loads(match.group(1))
    assert structured_data["@type"] == "WebPage"
    assert structured_data["url"] == canonical_url
    assert structured_data["about"]["@id"].endswith("/#dictionary")


def test_refresh_metadata_preserves_page_body_and_is_idempotent(tmp_path: Path) -> None:
    page = tmp_path / "sample" / "index.html"
    page.parent.mkdir()
    page.write_text(
        """<!doctype html>
<html><head>
  <meta name="description" content="Описание">
  <title>Заголовок</title>
</head><body><p>Не менять</p></body></html>
""",
        encoding="utf-8",
    )

    BUILD_SITE.refresh_metadata(tmp_path)
    first = page.read_text(encoding="utf-8")
    BUILD_SITE.refresh_metadata(tmp_path)

    assert page.read_text(encoding="utf-8") == first
    assert "<p>Не менять</p>" in first
    assert "/comparison/sample/" in first
    assert first.count("<!-- SOCIAL-METADATA:START -->") == 1


def test_checked_in_pages_have_unique_canonical_urls_and_valid_schema() -> None:
    project_root = Path(__file__).parents[1]
    pages = [project_root / "site-home" / "index.html"]
    pages.extend(
        page
        for page in (project_root / "terra-luna-site").rglob("index.html")
        if "dist" not in page.relative_to(project_root / "terra-luna-site").parts
    )
    canonical_urls: set[str] = set()

    for page in pages:
        source = page.read_text(encoding="utf-8")
        canonical_match = re.search(r'<link rel="canonical" href="([^"]+)">', source)
        schema_matches = re.findall(
            r'<script type="application/ld\+json">(.*?)</script>',
            source,
            flags=re.DOTALL,
        )
        assert canonical_match is not None, page
        assert canonical_match.group(1) not in canonical_urls, page
        assert len(schema_matches) == 1, page
        assert source.count('<meta property="og:image"') == 1, page
        assert BUILD_SITE.SOCIAL_IMAGE_URL in source, page
        json.loads(schema_matches[0])
        canonical_urls.add(canonical_match.group(1))

    assert len(pages) == 39


def test_homepage_stages_v101_assets_without_obsolete_upgrade_warning() -> None:
    homepage = (Path(__file__).parents[1] / "site-home" / "index.html").read_text(
        encoding="utf-8",
    )

    assert "releases/tag/v1.0.1" in homepage
    for format_name in ("yomitan", "yomitan-light", "goldendict", "mdict", "pocketbook", "apple-dictionary"):
        assert f"jp-ru-kolobok-400k-v1.0.1-{format_name}.zip" in homepage
    assert "Важно для пользователей Yomitan v1.0" not in homepage
    assert "releases/download/run59-tags-ru-v1" not in homepage
    assert "jitendex.org/static/yomitan.json" not in homepage.lower()


def test_hosted_yomitan_index_uses_owned_release_channel() -> None:
    project_root = Path(__file__).parents[1]
    hosted = json.loads((project_root / "site-home" / "yomitan.json").read_text())

    validate_yomitan_metadata(hosted, require_updatable=True)
    assert hosted["title"] == "Колобок 400k"
    assert hosted["revision"] == "2026.08.21.0-jp-ru-kolobok-400k-v1.0.1-tags-ru-v1"
    assert hosted["indexUrl"] == (
        "https://ganqqwerty.github.io/jp-ru-kolobok-dictionary/yomitan.json"
    )
    assert hosted["downloadUrl"] == (
        "https://github.com/ganqqwerty/jp-ru-kolobok-dictionary/releases/download/"
        "v1.0.1/jp-ru-kolobok-400k-v1.0.1-yomitan.zip"
    )
    assert "jitendex.org" not in hosted["indexUrl"].lower()
    assert "jitendex.org" not in hosted["downloadUrl"].lower()


def test_yomitan_smoke_page_covers_the_release_contract() -> None:
    project_root = Path(__file__).parents[1]
    page = (project_root / "site-home" / "yomitan-smoke.html").read_text(
        encoding="utf-8",
    )

    page_checks = re.findall(r'data-check="([^"]+)"', page)
    assert set(page_checks) == YOMITAN_SMOKE_CHECKS
    assert len(page_checks) == len(YOMITAN_SMOKE_CHECKS)
    assert page.count('id="clean-profile"') == 1
    assert page.count('id="imported"') == 1
    assert "run59-v1.0.1-smoke.json" in page
    assert "f0e8a6d8823398401994d0c7738aee4dca83b225bf276f9b08282cafbbac68b7" in page
    assert "http://127.0.0.1:8766/yomitan.json" in page
    assert "один раз перезагрузи страницу настроек Yomitan" in page
    assert "jitendex.org/static/yomitan.json" not in page.lower()
    assert "github.com/stephenmk/" not in page.lower()


def test_yomitan_smoke_page_pins_every_manual_lookup_and_expected_result() -> None:
    page = (Path(__file__).parents[1] / "site-home" / "yomitan-smoke.html").read_text(
        encoding="utf-8",
    )

    for query in (
        "食べる", "たべる", "食べました", "ありがとう", "生", "悪どい", "明白",
        "ＣＤプレーヤー", "掛ける", "社会情報學", "中２", "オメガ", "アンド",
        "Ｗｉｎｄｏｗｓ", "鱒の介", "ブルーバック", "格", "スベタ", "バカ騒ぎ",
    ):
        assert f'>{query}</span>' in page
    for evidence in (
        "вариант написания: 社会情報學",
        "только 中２・中二",
        "только Ω",
        "только ＡＮＤ",
        "допустимо только для этих форм и/или чтений",
        "Oncorhynchus tshawytscha",
        "Португальский: «espada»",
        "Мы вчера вечером, выпив, ходили по всему городу и вовсю кутили.",
    ):
        assert evidence in page


def test_completed_yomitan_smoke_records_the_observed_client_and_refresh() -> None:
    project_root = Path(__file__).parents[1]
    payload = json.loads(
        (project_root / "reports/yomitan_localization/run59-v1.0.1-smoke.json").read_text(
            encoding="utf-8",
        )
    )

    assert payload["clean_profile"] is True
    assert payload["imported"] is True
    assert set(payload["checks"]) == YOMITAN_SMOKE_CHECKS
    assert all(payload["checks"].values())
    assert "Chrome 151.0.7922.169" in payload["notes"]
    assert "Yomitan Popup Dictionary 26.7.29.0" in payload["notes"]
    assert "После однократной перезагрузки" in payload["notes"]
    assert "Итоговый список установленных словарей: Колобок 400k" in payload["notes"]
