from __future__ import annotations

from datetime import date

from communication_journal_site.health import apply_special_issue_coverage, collection_health
from communication_journal_site.models import (
    ArticleRecord,
    JournalConfig,
    SpecialIssueRecord,
    SpecialIssueSourceConfig,
)


def test_collection_health_clamps_utc_date_boundary_to_zero_days() -> None:
    articles = [
        ArticleRecord(
            journal_id="journal",
            journal_title="Journal",
            title="Paper",
            published_date="2026-07-30",
            last_seen_at="2026-07-31T03:00:00+00:00",
        )
    ]
    calls = [
        SpecialIssueRecord(
            source_id="source",
            journal_id="journal",
            journal_title="Journal",
            title="Special Issue: Test",
            source_url="https://example.org/call",
            last_seen_at="2026-07-31T03:00:00+00:00",
        )
    ]

    health = collection_health(articles, calls, warning_days=10, today=date(2026, 7, 30))

    assert health["article_sync_age_days"] == 0
    assert health["special_issue_sync_age_days"] == 0


def test_limited_priority_journal_coverage_degrades_health() -> None:
    journals = [
        JournalConfig(
            id="covered", title="Covered", issns=["0000-0000"],
            special_issue_monitor=True,
        ),
        JournalConfig(
            id="missing", title="Missing", issns=["0000-0001"],
            special_issue_monitor=True,
        ),
    ]
    sources = [
        SpecialIssueSourceConfig(
            id="covered-source", journal_id="covered", label="Covered",
            url="https://example.org/calls",
        )
    ]
    health = {"status": "current"}

    apply_special_issue_coverage(health, journals, sources)

    assert health["status"] == "degraded"
    assert health["special_issue_direct_coverage_count"] == 1
    assert health["special_issue_priority_journal_count"] == 2
    assert health["special_issue_audit_coverage_count"] == 0
    assert health["special_issue_uncovered_journal_ids"] == ["covered", "missing"]


def test_future_corrupt_sync_timestamp_is_not_current() -> None:
    articles = [
        ArticleRecord(
            journal_id="journal",
            journal_title="Journal",
            title="Paper",
            published_date="2026-07-30",
            last_seen_at="2026-08-15T03:00:00+00:00",
        )
    ]

    health = collection_health(articles, [], warning_days=10, today=date(2026, 7, 30))

    assert health["article_sync_age_days"] == -16
    assert health["article_status"] == "stale"


def test_recent_no_call_audit_counts_toward_priority_coverage() -> None:
    journals = [
        JournalConfig(
            id="audited",
            title="Audited",
            issns=["0000-0000"],
            special_issue_monitor=True,
            special_issue_checked_on="2026-07-30",
            special_issue_audit_url="https://example.org/journal/calls",
        )
    ]
    health = {"status": "current"}

    apply_special_issue_coverage(
        health, journals, [], verification_days=10, today=date(2026, 7, 30)
    )

    assert health["status"] == "current"
    assert health["special_issue_audit_coverage_count"] == 1
    assert health["special_issue_direct_coverage_count"] == 0
    assert health["special_issue_coverage_status"] == "complete"


def test_stale_no_call_audit_does_not_count_toward_priority_coverage() -> None:
    journals = [
        JournalConfig(
            id="stale",
            title="Stale",
            issns=["0000-0000"],
            special_issue_monitor=True,
            special_issue_checked_on="2026-07-01",
        )
    ]
    health = {"status": "current"}

    apply_special_issue_coverage(
        health, journals, [], verification_days=10, today=date(2026, 7, 30)
    )

    assert health["status"] == "degraded"
    assert health["special_issue_audit_coverage_count"] == 0
    assert health["special_issue_uncovered_journal_ids"] == ["stale"]
