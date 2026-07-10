from __future__ import annotations

import html

import pytest

from communication_journal_site import covers
from communication_journal_site.covers import (
    collect_covers,
    ensure_cover_asset,
    generated_cover_svg,
    load_cover_sources,
)
from communication_journal_site.models import JournalConfig
from communication_journal_site.normalize import slugify


def _journal(title: str) -> JournalConfig:
    return JournalConfig(id=slugify(title), title=title, issns=["0000-0000"])


def test_generated_cover_svg_is_deterministic() -> None:
    journal = _journal("Journal of Computer-Mediated Communication")
    assert generated_cover_svg(journal) == generated_cover_svg(journal)


def test_generated_cover_svg_contains_escaped_title_and_eyebrow() -> None:
    journal = _journal("Media & Society")
    svg = generated_cover_svg(journal)
    assert "JOURNAL" in svg
    assert html.escape("&") in svg  # "&amp;" present
    assert "&amp;" in svg


def test_ensure_cover_asset_writes_generated_svg_when_cache_empty(tmp_path) -> None:
    journal = _journal("Communication Theory")
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    assets_dir = tmp_path / "assets"
    slug = slugify(journal.title)

    rel_path = ensure_cover_asset(journal, cache_dir, assets_dir)

    assert rel_path == f"assets/covers/{slug}.svg"
    written = assets_dir / "covers" / f"{slug}.svg"
    assert written.exists()
    assert "JOURNAL" in written.read_text(encoding="utf-8")


def test_ensure_cover_asset_copies_real_cover_when_present(tmp_path) -> None:
    journal = _journal("New Media & Society")
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    assets_dir = tmp_path / "assets"
    slug = slugify(journal.title)

    fake_cover = cache_dir / f"{slug}.png"
    fake_cover.write_bytes(b"\x89PNG\r\n\x1a\nfake-png-bytes")

    rel_path = ensure_cover_asset(journal, cache_dir, assets_dir)

    assert rel_path == f"assets/covers/{slug}.png"
    copied = assets_dir / "covers" / f"{slug}.png"
    assert copied.exists()
    assert copied.read_bytes() == fake_cover.read_bytes()


def test_collect_covers_imports_file_named_with_journal_id(monkeypatch, tmp_path) -> None:
    journal = JournalConfig(
        id="new-media-and-society",
        title="New Media & Society",
        issns=["1461-4448"],
    )
    import_dir = tmp_path / "imports"
    import_dir.mkdir()
    image = b"\x89PNG\r\n\x1a\nreal-cover"
    (import_dir / "new-media-and-society.png").write_bytes(image)
    monkeypatch.setattr(covers, "fetch_wikidata_covers", lambda journals, cache_dir: {})

    result = collect_covers([journal], tmp_path / "cache", import_dir=import_dir)

    assert result.imported == [journal.id]
    assert result.missing == []
    assert (tmp_path / "cache" / "new-media-society.png").read_bytes() == image


def test_collect_covers_rejects_non_image_import(monkeypatch, tmp_path) -> None:
    journal = _journal("Communication Theory")
    import_dir = tmp_path / "imports"
    import_dir.mkdir()
    (import_dir / f"{journal.id}.jpg").write_text("<html>blocked</html>", encoding="utf-8")
    monkeypatch.setattr(covers, "fetch_wikidata_covers", lambda journals, cache_dir: {})

    result = collect_covers([journal], tmp_path / "cache", import_dir=import_dir)

    assert result.missing == [journal.id]
    assert "not a supported image" in result.errors[journal.id]


def test_load_cover_sources_supports_object_and_string_entries(tmp_path) -> None:
    manifest = tmp_path / "covers.yaml"
    manifest.write_text(
        """
covers:
  journal-a:
    image_url: https://publisher.example/a.jpg
    source_page: https://publisher.example/a
  journal-b: https://publisher.example/b.png
""".strip(),
        encoding="utf-8",
    )

    sources = load_cover_sources(manifest)

    assert sources["journal-a"].source_page == "https://publisher.example/a"
    assert sources["journal-b"].image_url == "https://publisher.example/b.png"


def test_load_cover_sources_rejects_non_http_url(tmp_path) -> None:
    manifest = tmp_path / "covers.yaml"
    manifest.write_text("covers:\n  journal-a: file:///tmp/a.jpg\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"needs an http\(s\) image_url"):
        load_cover_sources(manifest)
