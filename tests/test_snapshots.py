from __future__ import annotations

import json

from communication_journal_site.snapshots import bootstrap_public_snapshot
from communication_journal_site.storage import StateStore


def test_bootstrap_public_snapshot_restores_articles_and_calls(tmp_path) -> None:
    public = tmp_path / "public"
    public.mkdir()
    (public / "health.json").write_text(
        json.dumps(
            {
                "latest_article_sync": "2026-07-30T12:00:00+00:00",
                "latest_special_issue_sync": "2026-07-30T13:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    (public / "articles.jsonl").write_text(
        json.dumps(
            {
                "journal_id": "journal",
                "journal": "Journal",
                "title": "Public paper",
                "published_date": "2026-07-29",
                "doi": "10.1000/example",
                "link": "https://doi.org/10.1000/example",
                "abstract": "A usable abstract.",
                "authors": ["A. Author"],
                "affiliations": [],
                "subjects": ["Communication"],
                "first_seen_at": "2026-07-30T12:00:00+00:00",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (public / "special_issues.jsonl").write_text(
        json.dumps(
            {
                "source_id": "source",
                "journal_id": "journal",
                "journal": "Journal",
                "title": "Special Issue: Public call",
                "source_url": "https://example.org/call",
                "status": "active",
                "deadline": "2026-09-01",
                "confidence": "high",
                "snippet": "Official call.",
                "discovered_at": "2026-07-30T13:00:00+00:00",
                "last_seen_at": "2026-07-30T13:00:00+00:00",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    store = StateStore(tmp_path / "state" / "site.db")

    result = bootstrap_public_snapshot(store, public)

    assert result == {"articles": 1, "special_issues": 1}
    assert store.get_all_articles()[0].title == "Public paper"
    assert store.get_all_articles()[0].last_seen_at == "2026-07-30T12:00:00+00:00"
    assert store.get_special_issues()[0].title == "Special Issue: Public call"
