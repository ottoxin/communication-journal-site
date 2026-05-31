from __future__ import annotations

from pathlib import Path

from communication_journal_site.config import load_config
from communication_journal_site.models import ArticleRecord, SpecialIssueRecord
from communication_journal_site.site import build_site
from communication_journal_site.storage import StateStore


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
            )
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
    assert tmp_path.joinpath("site/journals/journal-of-communication/index.html").exists()
    assert tmp_path.joinpath("site/weeks/2026-06-01/index.html").exists()
    assert tmp_path.joinpath("site/special-issues/index.html").exists()
    assert any(path.name == "styles.css" for path in written)
    assert "Communication futures" in tmp_path.joinpath("site/index.html").read_text(encoding="utf-8")

