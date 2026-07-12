from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .models import ArticleRecord, SpecialIssueRecord


LAST_RUN_FILENAME = "last_run.json"


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
    age_days = (today - sync_date).days if sync_date else None
    call_age_days = (today - call_sync_date).days if call_sync_date else None
    if not articles:
        article_status = "empty"
    elif age_days is not None and age_days > warning_days:
        article_status = "stale"
    else:
        article_status = "current"
    if not special_issues:
        special_issue_status = "empty"
    elif call_age_days is None or call_age_days > warning_days:
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
        "unverified_special_issue_count": sum(1 for item in special_issues if item.status == "unverified"),
    }


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


def _date_prefix(value: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None
