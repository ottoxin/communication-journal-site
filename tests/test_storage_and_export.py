from __future__ import annotations

import json
from pathlib import Path

from communication_journal_site.config import load_config
from communication_journal_site.exporter import export_public_data
from communication_journal_site.models import ArticleRecord
from communication_journal_site.storage import StateStore
from communication_journal_site.weekly import compute_weekly_windows


def test_storage_dedupes_articles_and_export_uses_journal_subareas(tmp_path: Path) -> None:
    config = load_config()
    store = StateStore(tmp_path / "site.db")
    article = ArticleRecord(
        journal_id="political-communication",
        journal_title="Political Communication",
        title="Campaign media effects",
        published_date="2026-05-29",
        doi="10.1111/example",
        canonical_url="https://doi.org/10.1111/example",
        authors=["One Author"],
    )
    store.upsert_articles([article, article], seen_at="2026-05-30T00:00:00+00:00")
    assert len(store.get_all_articles()) == 1
    paths = export_public_data(config, store, tmp_path / "site")
    rows = [
        json.loads(line)
        for line in paths["articles"].read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert rows[0]["subareas"] == ["political-communication", "journalism-media"]
    assert rows[0]["journal_slug"] == "political-communication"


def test_weekly_windows_match_monday_digest_pattern() -> None:
    windows = compute_weekly_windows(__import__("datetime").date(2026, 6, 1))
    assert windows.new_this_week_start == "2026-05-25"
    assert windows.new_this_week_end == "2026-05-31"
    assert windows.previous_week_start == "2026-05-18"

