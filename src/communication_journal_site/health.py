from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .models import (
    ArticleRecord,
    JournalConfig,
    SpecialIssueRecord,
    SpecialIssueSourceConfig,
)


LAST_RUN_FILENAME = "last_run.json"
SPECIAL_ISSUE_RUN_FILENAME = "special_issue_last_run.json"


def local_today(timezone_name: str) -> date:
    return datetime.now(ZoneInfo(timezone_name)).date()


def collection_health(
    articles: list[ArticleRecord],
    special_issues: list[SpecialIssueRecord],
    warning_days: int,
    today: date | None = None,
) -> dict[str, Any]:
    today = today or date.today()
    latest_sync = max((item.last_seen_at or "" for item in articles), default="")
    latest_paper = max((item.published_date for item in articles), default="")
    latest_call_sync = max((item.last_seen_at or "" for item in special_issues), default="")
    sync_date = _date_prefix(latest_sync)
    call_sync_date = _date_prefix(latest_call_sync)
    # Collection timestamps are recorded in UTC and may fall on the following
    # calendar day relative to the configured site timezone. Freshness ages
    # should never be negative in that boundary window.
    age_days = _sync_age_days(today, sync_date)
    call_age_days = _sync_age_days(today, call_sync_date)
    if not articles:
        article_status = "empty"
    elif age_days is not None and (age_days > warning_days or age_days < 0):
        article_status = "stale"
    else:
        article_status = "current"
    if not special_issues:
        special_issue_status = "empty"
    elif call_age_days is None or call_age_days > warning_days or call_age_days < 0:
        special_issue_status = "stale"
    else:
        special_issue_status = "current"
    if article_status == "empty":
        status = "empty"
    elif article_status == "stale":
        status = "stale"
    elif special_issue_status in {"empty", "stale"}:
        status = "degraded"
    else:
        status = "current"
    return {
        "status": status,
        "article_status": article_status,
        "special_issue_status": special_issue_status,
        "checked_on": today.isoformat(),
        "article_count": len(articles),
        "latest_published_date": latest_paper or None,
        "latest_article_sync": latest_sync or None,
        "article_sync_age_days": age_days,
        "latest_special_issue_sync": latest_call_sync or None,
        "special_issue_sync_age_days": call_age_days,
        "active_special_issue_count": sum(1 for item in special_issues if item.status == "active"),
        "upcoming_special_issue_count": sum(
            1 for item in special_issues if item.status == "upcoming"
        ),
        "unverified_special_issue_count": sum(1 for item in special_issues if item.status == "unverified"),
    }


def apply_special_issue_coverage(
    health: dict[str, Any],
    journals: list[JournalConfig],
    sources: list[SpecialIssueSourceConfig],
    verification_days: int = 10,
    today: date | None = None,
) -> dict[str, Any]:
    today = today or date.today()
    priority_ids = {
        journal.id
        for journal in journals
        if journal.active and journal.special_issue_monitor
    }
    directly_covered_ids = {
        source.journal_id
        for source in sources
        if source.active and source.journal_id in priority_ids
    }
    recently_audited_ids = {
        journal.id
        for journal in journals
        if journal.id in priority_ids
        and journal.special_issue_checked_on
        and _date_within_days(journal.special_issue_checked_on, today, verification_days)
    }
    # A configured page can fail or silently change structure, so it does not
    # substitute for a dated, evidence-linked journal audit. Direct sources are
    # reported separately as an automation metric.
    covered_ids = recently_audited_ids
    missing_ids = sorted(priority_ids - covered_ids)
    health.update(
        {
            "special_issue_priority_journal_count": len(priority_ids),
            "special_issue_direct_coverage_count": len(directly_covered_ids),
            "special_issue_audit_coverage_count": len(covered_ids),
            "special_issue_coverage_status": "complete" if not missing_ids else "limited",
            "special_issue_uncovered_journal_ids": missing_ids,
        }
    )
    if missing_ids and health.get("status") == "current":
        health["status"] = "degraded"
    return health


def _date_within_days(value: str, today: date, maximum_age_days: int) -> bool:
    try:
        checked_on = date.fromisoformat(value)
    except ValueError:
        return False
    return 0 <= (today - checked_on).days <= maximum_age_days


def _sync_age_days(today: date, sync_date: date | None) -> int | None:
    if sync_date is None:
        return None
    age = (today - sync_date).days
    return 0 if age == -1 else age


def write_last_run(state_dir: Path, payload: dict[str, Any]) -> Path:
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / LAST_RUN_FILENAME
    body = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        **payload,
    }
    path.write_text(json.dumps(body, indent=2, ensure_ascii=True), encoding="utf-8")
    return path


def read_last_run(state_dir: Path) -> dict[str, Any] | None:
    path = state_dir / LAST_RUN_FILENAME
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def write_special_issue_run(state_dir: Path, payload: dict[str, Any]) -> Path:
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / SPECIAL_ISSUE_RUN_FILENAME
    body = {"recorded_at": datetime.now(timezone.utc).isoformat(), **payload}
    path.write_text(json.dumps(body, indent=2, ensure_ascii=True), encoding="utf-8")
    return path


def read_special_issue_run(state_dir: Path) -> dict[str, Any] | None:
    path = state_dir / SPECIAL_ISSUE_RUN_FILENAME
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _date_prefix(value: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None
