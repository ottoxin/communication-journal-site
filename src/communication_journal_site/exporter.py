from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from .models import AppConfig, ArticleRecord, JournalConfig, SpecialIssueRecord
from .normalize import clean_abstract, slugify
from .storage import StateStore
from .weekly import compute_weekly_windows


def export_public_data(
    config: AppConfig,
    store: StateStore,
    output_dir: str | Path,
    digest_date: date | None = None,
) -> dict[str, Path]:
    data_dir = Path(output_dir) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    articles = store.get_all_articles()
    special_issues = store.get_special_issues()
    journal_by_id = config.journal_by_id
    article_dicts = [_article_public_dict(article, journal_by_id[article.journal_id]) for article in articles]
    special_dicts = [_special_issue_public_dict(record) for record in special_issues]
    journal_dicts = _journal_public_dicts(config.journals, articles)
    paths = {
        "articles": data_dir / "articles.jsonl",
        "journals": data_dir / "journals.json",
        "special_issues": data_dir / "special_issues.jsonl",
        "latest": data_dir / "latest.json",
    }
    _write_jsonl(paths["articles"], article_dicts)
    _write_json(paths["journals"], journal_dicts)
    _write_jsonl(paths["special_issues"], special_dicts)
    latest_payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "site_title": config.settings.title,
        "article_count": len(article_dicts),
        "journal_count": len(config.journals),
        "active_special_issue_count": sum(1 for item in special_dicts if item["status"] == "active"),
        "digest_date": digest_date.isoformat() if digest_date else None,
        "windows": compute_weekly_windows(digest_date).to_dict() if digest_date else None,
        "latest_articles": article_dicts[:50],
        "active_special_issues": [item for item in special_dicts if item["status"] == "active"][:25],
    }
    _write_json(paths["latest"], latest_payload)
    return paths


def _article_public_dict(article: ArticleRecord, journal: JournalConfig) -> dict:
    return {
        "journal_id": article.journal_id,
        "journal_slug": slugify(article.journal_title),
        "journal": article.journal_title,
        "title": article.title,
        "published_date": article.published_date,
        "doi": article.doi,
        "link": article.canonical_url or (f"https://doi.org/{article.doi}" if article.doi else None),
        "abstract": clean_abstract(article.abstract) or "Abstract unavailable.",
        "authors": article.authors,
        "affiliations": article.affiliations,
        "subjects": article.subjects,
        "volume": article.volume,
        "issue": article.issue,
        "pages": article.pages,
        "publisher": article.publisher or journal.publisher,
        "subareas": journal.subareas,
        "primary_subarea": journal.primary_subarea,
        "first_seen_at": article.first_seen_at,
    }


def _special_issue_public_dict(record: SpecialIssueRecord) -> dict:
    return {
        "source_id": record.source_id,
        "journal_id": record.journal_id,
        "journal": record.journal_title,
        "title": record.title,
        "source_url": record.source_url,
        "status": record.status,
        "deadline": record.deadline,
        "confidence": record.confidence,
        "discovered_at": record.discovered_at,
        "last_seen_at": record.last_seen_at,
        "snippet": record.raw_snippet,
    }


def _journal_public_dicts(journals: list[JournalConfig], articles: list[ArticleRecord]) -> list[dict]:
    counts: dict[str, int] = {}
    latest: dict[str, str] = {}
    for article in articles:
        counts[article.journal_id] = counts.get(article.journal_id, 0) + 1
        latest[article.journal_id] = max(latest.get(article.journal_id, ""), article.published_date)
    return [
        {
            "id": journal.id,
            "slug": slugify(journal.title),
            "title": journal.title,
            "issns": journal.issns,
            "publisher": journal.publisher,
            "homepage_url": journal.homepage_url,
            "latest_articles_url": journal.latest_articles_url,
            "subareas": journal.subareas,
            "primary_subarea": journal.primary_subarea,
            "associations": journal.associations,
            "active": journal.active,
            "collect": journal.collect,
            "paper_count": counts.get(journal.id, 0),
            "latest_published_date": latest.get(journal.id),
        }
        for journal in journals
    ]


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=True, sort_keys=True) for row in rows) + ("\n" if rows else ""),
        encoding="utf-8",
    )

