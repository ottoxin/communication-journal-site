from __future__ import annotations

import re
from datetime import date, datetime
from html.parser import HTMLParser
from urllib.parse import urljoin

from .http_client import HttpClient
from .models import JournalConfig, SpecialIssueRecord, SpecialIssueSourceConfig
from .normalize import normalize_whitespace

SPECIAL_ISSUE_PHRASES = (
    "special issue",
    "special section",
    "thematic issue",
    "theme issue",
)
CALL_PHRASES = (
    "call for papers",
    "call for submissions",
)
GENERIC_CALL_TITLES = (
    "call for papers",
    "call for submissions",
    "submissions",
    "submit",
)


class TextBlockParser(HTMLParser):
    block_tags = {"h1", "h2", "h3", "h4", "p", "li", "a"}

    def __init__(self):
        super().__init__()
        self.blocks: list[tuple[str, str | None]] = []
        self._active_tag: str | None = None
        self._active_href: str | None = None
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs):
        if tag in self.block_tags:
            self._flush()
            self._active_tag = tag
            self._active_href = None
            if tag == "a":
                attr_map = dict(attrs)
                self._active_href = attr_map.get("href")

    def handle_endtag(self, tag: str):
        if self._active_tag == tag:
            self._flush()

    def handle_data(self, data: str):
        if self._active_tag:
            self._buffer.append(data)

    def close(self):
        super().close()
        self._flush()

    def _flush(self) -> None:
        if not self._active_tag:
            return
        text = normalize_whitespace(" ".join(self._buffer))
        if text:
            self.blocks.append((text, self._active_href))
        self._active_tag = None
        self._active_href = None
        self._buffer = []


def parse_special_issues_from_html(
    html: str,
    source: SpecialIssueSourceConfig,
    journal: JournalConfig,
    base_url: str,
) -> list[SpecialIssueRecord]:
    parser = TextBlockParser()
    parser.feed(html)
    parser.close()
    records: list[SpecialIssueRecord] = []
    seen_titles: set[str] = set()
    blocks = parser.blocks
    for index, (text, href) in enumerate(blocks):
        next_text = blocks[index + 1][0] if index + 1 < len(blocks) else ""
        combined_text = normalize_whitespace(f"{text} {next_text}")
        if not _is_special_issue_candidate(text, combined_text):
            continue
        title = _candidate_title(text)
        if _is_generic_title(title):
            continue
        key = title.lower()
        if key in seen_titles:
            continue
        seen_titles.add(key)
        deadline = _extract_deadline(combined_text)
        records.append(
            SpecialIssueRecord(
                source_id=source.id,
                journal_id=journal.id,
                journal_title=journal.title,
                title=title,
                source_url=urljoin(base_url, href) if href else source.url,
                status=_status_for_deadline(deadline),
                deadline=deadline,
                confidence=_confidence(combined_text),
                raw_snippet=combined_text[:500],
            )
        )
    return records


class SpecialIssueCollector:
    def __init__(self, http_client: HttpClient | None = None):
        self.http_client = http_client or HttpClient()

    def collect(
        self,
        sources: list[SpecialIssueSourceConfig],
        journal_by_id: dict[str, JournalConfig],
    ) -> tuple[list[SpecialIssueRecord], list[dict[str, str]]]:
        records: list[SpecialIssueRecord] = []
        errors: list[dict[str, str]] = []
        for source in sources:
            if not source.active:
                continue
            journal = journal_by_id[source.journal_id]
            try:
                response = self.http_client.get_text(source.url)
                records.extend(
                    parse_special_issues_from_html(
                        response.body,
                        source=source,
                        journal=journal,
                        base_url=response.url or source.url,
                    )
                )
            except Exception as exc:
                errors.append({"source_id": source.id, "error": str(exc)})
        return records, errors


def _candidate_title(text: str) -> str:
    cleaned = normalize_whitespace(text)
    cleaned = re.sub(r"^(open )?call for (papers|submissions)[:\-\s]+", "", cleaned, flags=re.I)
    sentence = re.split(r"(?<=[.!?])\s+", cleaned)[0]
    if len(sentence) > 180:
        sentence = sentence[:177].rstrip() + "..."
    return sentence


def _is_special_issue_candidate(text: str, combined_text: str) -> bool:
    lowered_text = text.lower()
    lowered_combined = combined_text.lower()
    if any(phrase in lowered_combined for phrase in SPECIAL_ISSUE_PHRASES):
        return True
    return any(phrase in lowered_text for phrase in CALL_PHRASES) and any(
        phrase in lowered_combined for phrase in SPECIAL_ISSUE_PHRASES
    )


def _is_generic_title(title: str) -> bool:
    normalized = title.strip().lower().rstrip(":")
    if normalized in GENERIC_CALL_TITLES:
        return True
    return normalized.startswith(("deadline", "submissions due", "abstracts due"))


def _extract_deadline(text: str) -> str | None:
    patterns = [
        r"(?:deadline|due|submissions? due|abstracts? due)[:\s]+([A-Z][a-z]+ \d{1,2}, \d{4})",
        r"(?:deadline|due|submissions? due|abstracts? due)[:\s]+(\d{1,2}/\d{1,2}/\d{4})",
        r"\b([A-Z][a-z]+ \d{1,2}, \d{4})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        parsed = _parse_date(match.group(1))
        if parsed:
            return parsed
    return None


def _parse_date(value: str) -> str | None:
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _status_for_deadline(deadline: str | None) -> str:
    if not deadline:
        return "active"
    try:
        return "closed" if date.fromisoformat(deadline) < date.today() else "active"
    except ValueError:
        return "active"


def _confidence(text: str) -> str:
    lowered = text.lower()
    if "special issue" in lowered and ("deadline" in lowered or "due" in lowered):
        return "high"
    if "call for papers" in lowered or "special issue" in lowered:
        return "medium"
    return "low"
