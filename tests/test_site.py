from __future__ import annotations

from pathlib import Path
import json

from communication_journal_site.config import load_config
from communication_journal_site.models import ArticleRecord, SpecialIssueRecord
from communication_journal_site.site import _health_banner, build_site
from communication_journal_site.storage import StateStore


def test_health_banner_discloses_partial_special_issue_sources() -> None:
    banner = _health_banner({
        "status": "current",
        "special_issue_run": {"status": "partial"},
    })

    assert "some automated publisher sources failed" in banner


def test_build_site_outputs_requested_pages(tmp_path: Path) -> None:
    config = load_config()
    store = StateStore(tmp_path / "site.db")
    store.upsert_articles(
        [
            ArticleRecord(
                journal_id="journal-of-communication",
                journal_title="Journal of Communication",
                title="Communication futures",
                published_date="2026-05-29",
                doi="10.1093/example",
                canonical_url="https://doi.org/10.1093/example",
                abstract="A test abstract.",
            ),
            ArticleRecord(
                journal_id="journal-of-communication",
                journal_title="Journal of Communication",
                title="Hidden paper without abstract",
                published_date="2026-05-30",
                doi="10.1093/noabstract",
                canonical_url="https://doi.org/10.1093/noabstract",
            ),
        ],
        seen_at="2026-05-30T00:00:00+00:00",
    )
    store.upsert_special_issues(
        [
            SpecialIssueRecord(
                source_id="joc-test",
                journal_id="journal-of-communication",
                journal_title="Journal of Communication",
                title="Special Issue on Time",
                source_url="https://example.org/special",
                deadline="2026-09-01",
                raw_snippet="Special issue deadline: September 1, 2026",
            )
        ],
        seen_at="2026-05-30T00:00:00+00:00",
    )
    written = build_site(config, store, tmp_path / "site")
    assert tmp_path.joinpath("site/index.html").exists()
    assert tmp_path.joinpath("site/subareas/general-communication/index.html").exists()
    assert tmp_path.joinpath("site/subareas/political-communication/index.html").exists()
    assert tmp_path.joinpath("site/subareas/health-communication/index.html").exists()
    assert tmp_path.joinpath("site/subareas/methods/index.html").exists()
    assert tmp_path.joinpath("site/journals/journal-of-communication/index.html").exists()
    # Weekly-digest archive pages are no longer generated.
    assert not tmp_path.joinpath("site/weeks").exists()
    assert tmp_path.joinpath("site/special-issues/index.html").exists()
    assert tmp_path.joinpath("site/calls/index.html").exists()
    assert tmp_path.joinpath("site/status/index.html").exists()
    assert tmp_path.joinpath("site/.build-manifest.json").exists()
    assert any(path.name == "styles.css" for path in written)
    home = tmp_path.joinpath("site/index.html").read_text(encoding="utf-8")
    assert "Communication futures" in home
    assert "Hidden paper without abstract" not in home
    assert "subareas/political-communication/index.html" in home
    assert "subareas/health-communication/index.html" in home
    assert "subareas/methods/index.html" in home
    # The weekly-digest section has been removed from the site.
    assert "weeks/2026-06-01/index.html" not in home
    assert "Weekly Digests" not in home
    # Collection freshness is surfaced as a metric.
    assert "Last sync" in home
    # Home groups journals by subarea with cover images.
    assert "journal-card" in home
    assert "assets/covers/" in home
    journal_page = tmp_path.joinpath("site/journals/journal-of-communication/index.html").read_text(encoding="utf-8")
    assert "journal-cover-large" in journal_page
    assert "Hidden paper without abstract" not in journal_page

    stale = tmp_path / "site" / "stale-page.html"
    stale.write_text("old", encoding="utf-8")
    manifest_path = tmp_path / "site" / ".build-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"].append("stale-page.html")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    build_site(config, store, tmp_path / "site")
    assert not stale.exists()
