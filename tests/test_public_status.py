from __future__ import annotations

from scripts.write_public_status import render_status


def test_render_status_includes_core_collection_metrics() -> None:
    rendered = render_status(
        {
            "checked_on": "2026-07-30",
            "status": "current",
            "article_count": 3979,
            "latest_published_date": "2026-07-30",
            "active_special_issue_count": 12,
            "upcoming_special_issue_count": 1,
            "unverified_special_issue_count": 0,
            "special_issue_audit_coverage_count": 12,
            "special_issue_priority_journal_count": 12,
            "special_issue_run": {"automated_source_count": 4, "failed_source_count": 0},
            "last_run": {"status": "complete"},
        }
    )

    assert "Dataset status: **Current**" in rendered
    assert "| Public articles | 3979 |" in rendered
    assert "| Current priority-journal CFP audits | 12/12 |" in rendered
    assert "| Upcoming calls | 1 |" in rendered
