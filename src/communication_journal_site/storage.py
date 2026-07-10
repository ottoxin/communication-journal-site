from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

from .models import ArticleRecord, SpecialIssueRecord
from .normalize import make_dedupe_key, sha256_text
from .special_issues import is_plausible_special_issue_title


class StateStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS articles (
                    dedupe_key TEXT PRIMARY KEY,
                    journal_id TEXT NOT NULL,
                    journal_title TEXT NOT NULL,
                    title TEXT NOT NULL,
                    doi TEXT,
                    canonical_url TEXT,
                    article_type TEXT NOT NULL,
                    published_date TEXT NOT NULL,
                    abstract TEXT,
                    authors_json TEXT NOT NULL,
                    affiliations_json TEXT NOT NULL,
                    subjects_json TEXT NOT NULL,
                    volume TEXT,
                    issue TEXT,
                    pages TEXT,
                    publisher TEXT,
                    source_issn TEXT,
                    provenance_json TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_articles_journal ON articles(journal_id);
                CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published_date);
                CREATE INDEX IF NOT EXISTS idx_articles_first_seen ON articles(first_seen_at);

                CREATE TABLE IF NOT EXISTS special_issues (
                    dedupe_key TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    journal_id TEXT NOT NULL,
                    journal_title TEXT NOT NULL,
                    title TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    status TEXT NOT NULL,
                    deadline TEXT,
                    confidence TEXT NOT NULL,
                    raw_snippet TEXT NOT NULL,
                    discovered_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_special_issues_status ON special_issues(status);
                CREATE INDEX IF NOT EXISTS idx_special_issues_deadline ON special_issues(deadline);
                """
            )

    def upsert_articles(self, articles: list[ArticleRecord], seen_at: str) -> int:
        with self._connect() as connection:
            for article in articles:
                first_seen_at = article.first_seen_at or seen_at
                last_seen_at = article.last_seen_at or seen_at
                dedupe_key = make_dedupe_key(
                    article.doi,
                    article.canonical_url,
                    article.journal_title,
                    article.title,
                    article.published_date,
                )
                connection.execute(
                    """
                    INSERT INTO articles (
                        dedupe_key, journal_id, journal_title, title, doi, canonical_url,
                        article_type, published_date, abstract, authors_json,
                        affiliations_json, subjects_json, volume, issue, pages, publisher,
                        source_issn, provenance_json, first_seen_at, last_seen_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(dedupe_key) DO UPDATE SET
                        journal_id = excluded.journal_id,
                        journal_title = excluded.journal_title,
                        title = excluded.title,
                        doi = COALESCE(articles.doi, excluded.doi),
                        canonical_url = COALESCE(articles.canonical_url, excluded.canonical_url),
                        article_type = excluded.article_type,
                        published_date = excluded.published_date,
                        abstract = CASE
                            WHEN articles.abstract IS NULL OR articles.abstract = '' THEN excluded.abstract
                            ELSE articles.abstract
                        END,
                        authors_json = CASE
                            WHEN articles.authors_json = '[]' THEN excluded.authors_json
                            ELSE articles.authors_json
                        END,
                        affiliations_json = CASE
                            WHEN articles.affiliations_json = '[]' THEN excluded.affiliations_json
                            ELSE articles.affiliations_json
                        END,
                        subjects_json = CASE
                            WHEN articles.subjects_json = '[]' THEN excluded.subjects_json
                            ELSE articles.subjects_json
                        END,
                        volume = COALESCE(articles.volume, excluded.volume),
                        issue = COALESCE(articles.issue, excluded.issue),
                        pages = COALESCE(articles.pages, excluded.pages),
                        publisher = COALESCE(articles.publisher, excluded.publisher),
                        source_issn = COALESCE(articles.source_issn, excluded.source_issn),
                        provenance_json = excluded.provenance_json,
                        first_seen_at = MIN(articles.first_seen_at, excluded.first_seen_at),
                        last_seen_at = MAX(articles.last_seen_at, excluded.last_seen_at)
                    """,
                    (
                        dedupe_key,
                        article.journal_id,
                        article.journal_title,
                        article.title,
                        article.doi,
                        article.canonical_url,
                        article.article_type,
                        article.published_date,
                        article.abstract,
                        json.dumps(article.authors, sort_keys=True),
                        json.dumps(article.affiliations, sort_keys=True),
                        json.dumps(article.subjects, sort_keys=True),
                        article.volume,
                        article.issue,
                        article.pages,
                        article.publisher,
                        article.source_issn,
                        json.dumps(article.provenance, sort_keys=True),
                        first_seen_at,
                        last_seen_at,
                    ),
                )
        return len(articles)

    def upsert_special_issues(self, records: list[SpecialIssueRecord], seen_at: str) -> int:
        with self._connect() as connection:
            for record in records:
                discovered_at = record.discovered_at or seen_at
                last_seen_at = record.last_seen_at or seen_at
                dedupe_key = _special_issue_dedupe_key(record)
                connection.execute(
                    """
                    INSERT INTO special_issues (
                        dedupe_key, source_id, journal_id, journal_title, title, source_url,
                        status, deadline, confidence, raw_snippet, discovered_at, last_seen_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(dedupe_key) DO UPDATE SET
                        journal_title = excluded.journal_title,
                        source_url = excluded.source_url,
                        status = excluded.status,
                        deadline = COALESCE(excluded.deadline, special_issues.deadline),
                        confidence = excluded.confidence,
                        raw_snippet = excluded.raw_snippet,
                        last_seen_at = MAX(special_issues.last_seen_at, excluded.last_seen_at)
                    """,
                    (
                        dedupe_key,
                        record.source_id,
                        record.journal_id,
                        record.journal_title,
                        record.title,
                        record.source_url,
                        record.status,
                        record.deadline,
                        record.confidence,
                        record.raw_snippet,
                        discovered_at,
                        last_seen_at,
                    ),
                )
        return len(records)

    def reconcile_special_issues(
        self,
        records: list[SpecialIssueRecord],
        successful_source_ids: set[str],
    ) -> int:
        """Mark previously active calls missing from a successful source as unverified.

        Failed sources are deliberately excluded: a publisher outage must not make
        all of its calls disappear. A later successful run can reactivate a record
        through the normal upsert path.
        """
        seen_by_source: dict[str, set[str]] = {}
        for record in records:
            seen_by_source.setdefault(record.source_id, set()).add(_special_issue_dedupe_key(record))

        changed = 0
        with self._connect() as connection:
            for source_id in successful_source_ids:
                seen_keys = seen_by_source.get(source_id, set())
                rows = connection.execute(
                    "SELECT dedupe_key FROM special_issues WHERE source_id = ? AND status = 'active'",
                    (source_id,),
                ).fetchall()
                missing = [row["dedupe_key"] for row in rows if row["dedupe_key"] not in seen_keys]
                if not missing:
                    continue
                placeholders = ",".join("?" for _ in missing)
                cursor = connection.execute(
                    f"UPDATE special_issues SET status = 'unverified' WHERE dedupe_key IN ({placeholders})",
                    missing,
                )
                changed += cursor.rowcount
        return changed

    def get_articles_between(self, start_date: str, end_exclusive: str) -> list[ArticleRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM articles
                WHERE published_date >= ? AND published_date < ?
                ORDER BY published_date DESC, journal_title ASC, title ASC
                """,
                (start_date, end_exclusive),
            ).fetchall()
        return [self._row_to_article(row) for row in rows]

    def get_all_articles(self) -> list[ArticleRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM articles
                ORDER BY published_date DESC, journal_title ASC, title ASC
                """
            ).fetchall()
        return [self._row_to_article(row) for row in rows]

    def get_special_issues(
        self,
        verification_days: int | None = None,
        today: date | None = None,
    ) -> list[SpecialIssueRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM special_issues
                ORDER BY
                    CASE WHEN deadline IS NULL THEN 1 ELSE 0 END,
                    deadline ASC,
                    journal_title ASC,
                    title ASC
                """
            ).fetchall()
        records = [self._row_to_special_issue(row) for row in rows]
        records = [record for record in records if is_plausible_special_issue_title(record.title)]
        if verification_days is not None:
            today = today or date.today()
            for record in records:
                if record.status != "active" or not record.last_seen_at:
                    continue
                try:
                    last_seen = date.fromisoformat(record.last_seen_at[:10])
                except ValueError:
                    continue
                if (today - last_seen).days > verification_days:
                    record.status = "unverified"
        return records

    def _row_to_article(self, row: sqlite3.Row) -> ArticleRecord:
        return ArticleRecord(
            journal_id=row["journal_id"],
            journal_title=row["journal_title"],
            title=row["title"],
            published_date=row["published_date"],
            article_type=row["article_type"],
            doi=row["doi"],
            canonical_url=row["canonical_url"],
            abstract=row["abstract"],
            authors=json.loads(row["authors_json"]),
            affiliations=json.loads(row["affiliations_json"]),
            subjects=json.loads(row["subjects_json"]),
            volume=row["volume"],
            issue=row["issue"],
            pages=row["pages"],
            publisher=row["publisher"],
            source_issn=row["source_issn"],
            first_seen_at=row["first_seen_at"],
            last_seen_at=row["last_seen_at"],
            provenance=json.loads(row["provenance_json"]),
        )

    def _row_to_special_issue(self, row: sqlite3.Row) -> SpecialIssueRecord:
        status = row["status"]
        deadline = row["deadline"]
        if status == "active" and deadline:
            try:
                if date.fromisoformat(deadline) < date.today():
                    status = "closed"
            except ValueError:
                pass
        return SpecialIssueRecord(
            source_id=row["source_id"],
            journal_id=row["journal_id"],
            journal_title=row["journal_title"],
            title=row["title"],
            source_url=row["source_url"],
            status=status,
            deadline=deadline,
            discovered_at=row["discovered_at"],
            last_seen_at=row["last_seen_at"],
            confidence=row["confidence"],
            raw_snippet=row["raw_snippet"],
        )


def _special_issue_dedupe_key(record: SpecialIssueRecord) -> str:
    return sha256_text(
        "|".join(
            [
                record.source_id.lower(),
                record.journal_id.lower(),
                record.title.lower(),
                record.source_url.lower(),
            ]
        )
    )
