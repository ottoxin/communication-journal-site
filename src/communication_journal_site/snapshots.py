from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import ArticleRecord, SpecialIssueRecord
from .storage import StateStore


def bootstrap_public_snapshot(store: StateStore, input_dir: str | Path) -> dict[str, int]:
    """Restore public history into an empty/ephemeral runner state.

    Only already-public fields are imported. Local-only records intentionally
    remain local, while the public article history survives stateless CI runs.
    The normal weekly collectors then update and enrich these records.
    """
    directory = Path(input_dir)
    article_path = directory / "articles.jsonl"
    special_issue_path = directory / "special_issues.jsonl"
    health = _read_json(directory / "health.json")
    default_article_seen_at = str(health.get("latest_article_sync") or "")
    default_call_seen_at = str(health.get("latest_special_issue_sync") or "")

    article_rows = _read_jsonl(article_path)
    articles = [
        ArticleRecord(
            journal_id=str(row.get("journal_id") or ""),
            journal_title=str(row.get("journal") or ""),
            title=str(row.get("title") or ""),
            published_date=str(row.get("published_date") or ""),
            doi=_optional_string(row.get("doi")),
            canonical_url=_optional_string(row.get("link")),
            abstract=_optional_string(row.get("abstract")),
            authors=_string_list(row.get("authors")),
            affiliations=_string_list(row.get("affiliations")),
            subjects=_string_list(row.get("subjects")),
            volume=_optional_string(row.get("volume")),
            issue=_optional_string(row.get("issue")),
            pages=_optional_string(row.get("pages")),
            publisher=_optional_string(row.get("publisher")),
            first_seen_at=_optional_string(row.get("first_seen_at")),
            last_seen_at=default_article_seen_at or _optional_string(row.get("first_seen_at")),
        )
        for row in article_rows
        if row.get("journal_id") and row.get("title") and row.get("published_date")
    ]
    if articles:
        store.upsert_articles(
            articles,
            seen_at=default_article_seen_at or articles[0].first_seen_at or "1970-01-01T00:00:00+00:00",
        )

    call_rows = _read_jsonl(special_issue_path)
    calls = [
        SpecialIssueRecord(
            source_id=str(row.get("source_id") or ""),
            journal_id=str(row.get("journal_id") or ""),
            journal_title=str(row.get("journal") or ""),
            title=str(row.get("title") or ""),
            source_url=str(row.get("source_url") or ""),
            status=str(row.get("status") or "unverified"),
            deadline=_optional_string(row.get("deadline")),
            confidence=str(row.get("confidence") or "medium"),
            raw_snippet=str(row.get("snippet") or ""),
            discovered_at=_optional_string(row.get("discovered_at")),
            last_seen_at=_optional_string(row.get("last_seen_at")) or default_call_seen_at,
        )
        for row in call_rows
        if row.get("source_id") and row.get("journal_id") and row.get("title")
    ]
    if calls:
        store.upsert_special_issues(
            calls,
            seen_at=default_call_seen_at or calls[0].discovered_at or "1970-01-01T00:00:00+00:00",
        )
    return {"articles": len(articles), "special_issues": len(calls)}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON on {path}:{line_number}: {exc}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"Expected a JSON object on {path}:{line_number}.")
        rows.append(row)
    return rows


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc
    return payload if isinstance(payload, dict) else {}


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]
