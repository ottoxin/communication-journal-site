from __future__ import annotations

import gzip
import json
from datetime import date
from pathlib import Path

from communication_journal_site.config import load_config
from communication_journal_site.exporter import export_public_data
from communication_journal_site.models import ArticleRecord, SpecialIssueRecord
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
        abstract="A public abstract.",
        authors=["One Author"],
    )
    local_only = ArticleRecord(
        journal_id="political-communication",
        journal_title="Political Communication",
        title="Hidden until abstract exists",
        published_date="2026-05-30",
        doi="10.1111/noabstract",
        canonical_url="https://doi.org/10.1111/noabstract",
        authors=["Two Author"],
    )
    store.upsert_articles([article, article, local_only], seen_at="2026-05-30T00:00:00+00:00")
    assert len(store.get_all_articles()) == 2
    paths = export_public_data(config, store, tmp_path / "site")
    rows = [
        json.loads(line)
        for line in paths["articles"].read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    compressed_rows = [
        json.loads(line)
        for line in gzip.decompress(paths["articles_gzip"].read_bytes()).decode("utf-8").splitlines()
        if line.strip()
    ]
    assert compressed_rows == rows
    assert len(rows) == 1
    assert rows[0]["subareas"] == ["political-communication", "journalism-media"]
    assert rows[0]["journal_slug"] == "political-communication"
    assert rows[0]["abstract"] == "A public abstract."
    latest = json.loads(paths["latest"].read_text(encoding="utf-8"))
    assert latest["article_count"] == 1
    assert latest["local_only_article_count"] == 1
    health = json.loads(paths["health"].read_text(encoding="utf-8"))
    assert health["article_count"] == 1
    assert health["local_only_article_count"] == 1
    assert paths["health"].exists()
    assert paths["journal_metrics"].exists()
    journals = json.loads(paths["journals"].read_text(encoding="utf-8"))
    political = next(item for item in journals if item["id"] == "political-communication")
    assert political["impact_metric"]["label"] == "OpenAlex 2-year mean citedness"
    assert political["impact_metric"]["source_url"].startswith("https://openalex.org/")


def test_special_issue_reconciliation_only_expires_successful_sources(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "site.db")
    records = [
        SpecialIssueRecord(
            source_id="source-a",
            journal_id="journal-a",
            journal_title="Journal A",
            title="Special Issue on Platforms",
            source_url="https://example.org/a",
        ),
        SpecialIssueRecord(
            source_id="source-b",
            journal_id="journal-b",
            journal_title="Journal B",
            title="Special Issue on Trust",
            source_url="https://example.org/b",
        ),
    ]
    store.upsert_special_issues(records, seen_at="2026-06-01T00:00:00+00:00")

    changed = store.reconcile_special_issues([], {"source-a"})
    issues = {item.source_id: item for item in store.get_special_issues()}

    assert changed == 1
    assert issues["source-a"].status == "unverified"
    assert issues["source-b"].status == "active"


def test_special_issue_url_change_keeps_one_stable_record(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "site.db")
    first = SpecialIssueRecord(
        source_id="source-a", journal_id="journal-a", journal_title="Journal A",
        title="Call for Papers: Special Issue on Platform Governance",
        source_url="https://example.org/old",
    )
    revised = SpecialIssueRecord(
        source_id="source-a", journal_id="journal-a", journal_title="Journal A",
        title="Special Issue: Platform Governance",
        source_url="https://example.org/new",
    )

    store.upsert_special_issues([first], seen_at="2026-06-01T00:00:00+00:00")
    store.upsert_special_issues([revised], seen_at="2026-06-08T00:00:00+00:00")
    issues = store.get_special_issues()

    assert len(issues) == 1
    assert issues[0].source_url == "https://example.org/new"
    assert issues[0].discovered_at == "2026-06-01T00:00:00+00:00"


def test_special_issue_same_document_prefers_active_verified_record(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "site.db")
    shared_url_encoded = "https://example.org/Call%20for%20Proposals.pdf"
    shared_url_plain = "https://example.org/Call for Proposals.pdf"
    store.upsert_special_issues(
        [
            SpecialIssueRecord(
                source_id="old-hub", journal_id="wrong-journal", journal_title="Wrong Journal",
                title="Call for Special Issue Proposals", source_url=shared_url_plain,
                status="unverified",
            ),
            SpecialIssueRecord(
                source_id="verified", journal_id="publisher", journal_title="Correct Journal",
                title="Call for Special Issue Proposals", source_url=shared_url_encoded,
                status="active",
            ),
        ],
        seen_at="2026-07-12T00:00:00+00:00",
    )

    issues = store.get_special_issues()

    assert len(issues) == 1
    assert issues[0].source_id == "verified"


def test_old_unverified_call_is_not_presented_as_active(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "site.db")
    store.upsert_special_issues(
        [
            SpecialIssueRecord(
                source_id="source-a",
                journal_id="journal-a",
                journal_title="Journal A",
                title="Special Issue on Platforms",
                source_url="https://example.org/a",
            )
        ],
        seen_at="2026-06-01T00:00:00+00:00",
    )

    issue = store.get_special_issues(verification_days=10, today=date(2026, 6, 20))[0]

    assert issue.status == "unverified"


def test_weekly_windows_match_monday_digest_pattern() -> None:
    windows = compute_weekly_windows(__import__("datetime").date(2026, 6, 1))
    assert windows.new_this_week_start == "2026-05-25"
    assert windows.new_this_week_end == "2026-05-31"
    assert windows.previous_week_start == "2026-05-18"
