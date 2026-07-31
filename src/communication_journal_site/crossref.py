from __future__ import annotations

from datetime import date
import time
from typing import Any
from urllib.parse import urlencode

from .http_client import HttpClient
from .models import ArticleRecord, JournalConfig
from .normalize import (
    canonicalize_url,
    clean_abstract,
    clean_markup_text,
    compact_list,
    date_from_parts,
    normalize_doi,
    normalize_whitespace,
)


class CrossrefClient:
    api_url = "https://api.crossref.org/works"

    def __init__(
        self,
        http_client: HttpClient | None = None,
        mailto: str | None = None,
        request_interval_seconds: float = 0.0,
    ):
        self.http_client = http_client or HttpClient()
        self.mailto = mailto
        self.request_interval_seconds = max(0.0, request_interval_seconds)
        self._last_request_at = 0.0

    def fetch_journal_records(
        self,
        journal: JournalConfig,
        start_date: date,
        end_date: date,
    ) -> list[ArticleRecord]:
        deduped: dict[str, ArticleRecord] = {}
        for issn in journal.issns:
            for item in self._iter_works(issn, start_date, end_date):
                record = self._item_to_record(journal, issn, item)
                key = record.doi or f"{record.journal_title}:{record.title}:{record.published_date}"
                existing = deduped.get(key)
                if existing and existing.abstract:
                    continue
                deduped[key] = record
        return sorted(
            deduped.values(),
            key=lambda article: (article.published_date, article.journal_title, article.title),
            reverse=True,
        )

    def _iter_works(self, issn: str, start_date: date, end_date: date):
        cursor = "*"
        rows = 100
        while True:
            filters = [
                f"from-pub-date:{start_date.isoformat()}",
                f"until-pub-date:{end_date.isoformat()}",
                "type:journal-article",
                f"issn:{issn}",
            ]
            params = {
                "rows": str(rows),
                "cursor": cursor,
                "sort": "published",
                "order": "desc",
                "filter": ",".join(filters),
            }
            if self.mailto:
                params["mailto"] = self.mailto
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < self.request_interval_seconds:
                time.sleep(self.request_interval_seconds - elapsed)
            payload = self.http_client.get_json(f"{self.api_url}?{urlencode(params)}")
            self._last_request_at = time.monotonic()
            message = payload.get("message", {})
            items = message.get("items", [])
            if not items:
                break
            for item in items:
                if isinstance(item, dict):
                    yield item
            next_cursor = message.get("next-cursor")
            if not next_cursor or next_cursor == cursor or len(items) < rows:
                break
            cursor = next_cursor

    def _item_to_record(self, journal: JournalConfig, issn: str, item: dict[str, Any]) -> ArticleRecord:
        title = clean_markup_text((item.get("title") or ["Untitled article"])[0]) or "Untitled article"
        published_online = date_from_parts(item.get("published-online", {}).get("date-parts"))
        published_print = date_from_parts(item.get("published-print", {}).get("date-parts"))
        issued = date_from_parts(item.get("issued", {}).get("date-parts"))
        created = (item.get("created") or {}).get("date-time", "")[:10] or None
        authors: list[str] = []
        affiliations: list[str] = []
        for author in item.get("author", []):
            if not isinstance(author, dict):
                continue
            given = normalize_whitespace(author.get("given"))
            family = normalize_whitespace(author.get("family"))
            name = " ".join(part for part in [given, family] if part)
            if name:
                authors.append(name)
            for affiliation in author.get("affiliation", []):
                if isinstance(affiliation, dict):
                    affiliations.append(affiliation.get("name"))
        return ArticleRecord(
            journal_id=journal.id,
            journal_title=journal.title,
            title=title,
            published_date=published_online or published_print or issued or created or "1900-01-01",
            article_type=item.get("type", "journal-article"),
            doi=normalize_doi(item.get("DOI")),
            canonical_url=canonicalize_url(item.get("URL")),
            abstract=clean_abstract(item.get("abstract")),
            authors=compact_list(authors),
            affiliations=compact_list(affiliations),
            subjects=compact_list(str(subject) for subject in item.get("subject", [])),
            volume=_string_or_none(item.get("volume")),
            issue=_string_or_none(item.get("issue")),
            pages=_string_or_none(item.get("page")),
            publisher=_string_or_none(item.get("publisher") or journal.publisher),
            source_issn=issn,
            provenance={
                "source": "crossref",
                "abstract_source": "crossref" if item.get("abstract") else "unavailable",
                "crossref_issn": item.get("ISSN", []),
                "container_title": (item.get("container-title") or [journal.title])[0],
                "subtype": item.get("subtype"),
            },
        )


def _string_or_none(value: object) -> str | None:
    cleaned = normalize_whitespace(str(value)) if value is not None else ""
    return cleaned or None
