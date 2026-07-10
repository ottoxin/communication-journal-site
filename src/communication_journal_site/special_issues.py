from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from html.parser import HTMLParser
from urllib.parse import urljoin

from .http_client import HttpClient
from .models import JournalConfig, SpecialIssueRecord, SpecialIssueSourceConfig
from .normalize import clean_markup_text, normalize_whitespace
from .render import RenderedFetcher

FEED_SOURCE_TYPES = ("rss", "atom", "feed")
RENDERED_SOURCE_TYPE = "rendered"
# Source types whose journal_id need not map to a registry journal (they span journals).
AGGREGATOR_SOURCE_TYPES = FEED_SOURCE_TYPES + (RENDERED_SOURCE_TYPE,)
_ATOM_NS = "{http://www.w3.org/2005/Atom}"
# Used to keep cross-journal hub findings on-topic for a communication site.
COMMUNICATION_TERMS = (
    "communication", "journalism", "media", "rhetoric", "discourse",
    "public relations", "advertising", "public opinion", "broadcast",
)

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


def parse_special_issues_from_feed(
    feed_xml: str,
    source: SpecialIssueSourceConfig,
    journal: JournalConfig,
    base_url: str,
) -> list[SpecialIssueRecord]:
    try:
        root = ET.fromstring(feed_xml)
    except ET.ParseError:
        return []
    items = root.findall(".//item")
    is_atom = False
    if not items:
        items = root.findall(f".//{_ATOM_NS}entry")
        is_atom = True
    records: list[SpecialIssueRecord] = []
    seen_titles: set[str] = set()
    for item in items:
        if is_atom:
            title_raw = item.findtext(f"{_ATOM_NS}title") or ""
            description = item.findtext(f"{_ATOM_NS}summary") or item.findtext(f"{_ATOM_NS}content") or ""
            link_el = item.find(f"{_ATOM_NS}link")
            link = (link_el.get("href") if link_el is not None else "") or ""
        else:
            title_raw = item.findtext("title") or ""
            description = item.findtext("description") or ""
            link = item.findtext("link") or ""
        title_raw = clean_markup_text(title_raw) or ""
        description = clean_markup_text(description) or ""
        combined_text = normalize_whitespace(f"{title_raw} {description}")
        if not _is_special_issue_candidate(title_raw, combined_text):
            continue
        title = _candidate_title(title_raw)
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
                source_url=urljoin(base_url, link) if link else source.url,
                status=_status_for_deadline(deadline),
                deadline=deadline,
                confidence=_confidence(combined_text),
                raw_snippet=combined_text[:500],
            )
        )
    return records


def _is_communication_relevant(record: SpecialIssueRecord, journal_titles: set[str]) -> bool:
    haystack = f"{record.title} {record.raw_snippet}".lower()
    if any(title and title in haystack for title in journal_titles):
        return True
    return any(term in haystack for term in COMMUNICATION_TERMS)


class SpecialIssueCollector:
    def __init__(
        self,
        http_client: HttpClient | None = None,
        rendered_fetcher: RenderedFetcher | None = None,
    ):
        # Publisher pages are auxiliary inputs. Keep their failure budget short
        # so blocked sites cannot dominate the weekly article collection.
        self.http_client = http_client or HttpClient(timeout=12, max_attempts=1)
        self._rendered_fetcher = rendered_fetcher
        self.successful_source_ids: set[str] = set()

    def collect(
        self,
        sources: list[SpecialIssueSourceConfig],
        journal_by_id: dict[str, JournalConfig],
    ) -> tuple[list[SpecialIssueRecord], list[dict[str, str]]]:
        records: list[SpecialIssueRecord] = []
        errors: list[dict[str, str]] = []
        self.successful_source_ids = set()
        journal_titles = {j.title.lower() for j in journal_by_id.values() if j.title}
        owns_fetcher = False
        try:
            active_sources = [source for source in sources if source.active]
            http_sources = [source for source in active_sources if source.source_type != RENDERED_SOURCE_TYPE]
            rendered_sources = [source for source in active_sources if source.source_type == RENDERED_SOURCE_TYPE]
            if http_sources:
                worker_count = min(4, len(http_sources))
                with ThreadPoolExecutor(max_workers=worker_count) as executor:
                    futures = {
                        executor.submit(self._collect_source, source, journal_by_id, journal_titles): source
                        for source in http_sources
                    }
                    for future in as_completed(futures):
                        source = futures[future]
                        try:
                            records.extend(future.result())
                            self.successful_source_ids.add(source.id)
                        except Exception as exc:
                            errors.append({"source_id": source.id, "error": str(exc)})
            for source in rendered_sources:
                try:
                    if self._rendered_fetcher is None:
                        self._rendered_fetcher = RenderedFetcher()
                        owns_fetcher = True
                    records.extend(self._collect_source(source, journal_by_id, journal_titles))
                    self.successful_source_ids.add(source.id)
                except Exception as exc:
                    errors.append({"source_id": source.id, "error": str(exc)})
        finally:
            if owns_fetcher and self._rendered_fetcher is not None:
                self._rendered_fetcher.close()
                self._rendered_fetcher = None
        records.sort(key=lambda item: (item.source_id, item.title.lower(), item.source_url))
        errors.sort(key=lambda item: item["source_id"])
        return records, errors

    def _collect_source(
        self,
        source: SpecialIssueSourceConfig,
        journal_by_id: dict[str, JournalConfig],
        journal_titles: set[str],
    ) -> list[SpecialIssueRecord]:
        is_aggregator = source.journal_id not in journal_by_id
        journal = journal_by_id.get(source.journal_id) or JournalConfig(
            id=source.journal_id or "aggregator",
            title=source.label or source.journal_id or "Various sources",
            issns=[],
        )
        if source.source_type == RENDERED_SOURCE_TYPE:
            if self._rendered_fetcher is None:
                raise RuntimeError("Rendered fetcher is not initialized.")
            body = self._rendered_fetcher.get_text(source.url)
            base_url = source.url
        else:
            response = self.http_client.get_text(source.url)
            body = response.body
            base_url = response.url or source.url
        if source.source_type in FEED_SOURCE_TYPES:
            parsed = parse_special_issues_from_feed(
                body, source=source, journal=journal, base_url=base_url
            )
        else:
            parsed = parse_special_issues_from_html(
                body, source=source, journal=journal, base_url=base_url
            )
        if is_aggregator:
            parsed = [record for record in parsed if _is_communication_relevant(record, journal_titles)]
        return parsed


def _candidate_title(text: str) -> str:
    cleaned = normalize_whitespace(text)
    cleaned = re.sub(r"^(open )?call for (papers|submissions)[:\-\s]+", "", cleaned, flags=re.I)
    sentence = re.split(r"(?<=[.!?])\s+", cleaned)[0]
    if len(sentence) > 180:
        sentence = sentence[:177].rstrip() + "..."
    return sentence


def _is_special_issue_candidate(text: str, combined_text: str) -> bool:
    lowered_text = text.lower()
    # The candidate block itself must identify a special issue. Looking only at
    # adjacent prose produced false titles such as journal names, body copy, and
    # submission URLs whenever the following paragraph mentioned a special issue.
    has_special_issue = any(phrase in lowered_text for phrase in SPECIAL_ISSUE_PHRASES)
    has_call = any(phrase in lowered_text for phrase in CALL_PHRASES)
    adjacent_has_special_issue = any(phrase in combined_text.lower() for phrase in SPECIAL_ISSUE_PHRASES)
    if not has_special_issue and not (has_call and adjacent_has_special_issue):
        return False
    stripped = text.strip()
    if stripped.endswith((".", "!", "?")) and not lowered_text.startswith(
        ("call for", "open call for", "special issue", "special section", "thematic issue", "theme issue")
    ):
        return False
    if len(stripped) > 120 and stripped.endswith((".", "!", "?")):
        return False
    return True


def _is_generic_title(title: str) -> bool:
    normalized = title.strip().lower().rstrip(":")
    if normalized.startswith(("http://", "https://", "www.")):
        return True
    if normalized in GENERIC_CALL_TITLES:
        return True
    if normalized in {
        "call for proposals - special issue",
        "call for proposals – special issue",
        "call for proposals — special issue",
        "special issue call for papers",
    }:
        return True
    return normalized.startswith(("deadline", "submissions due", "abstracts due"))


def is_plausible_special_issue_title(title: str) -> bool:
    """Validate stored titles defensively as parsing rules improve over time."""
    return _is_special_issue_candidate(title, title) and not _is_generic_title(title)


def _extract_deadline(text: str) -> str | None:
    patterns = [
        r"(?:deadline|due|submissions? due|abstracts? due)[:\s]+([A-Z][a-z]+ \d{1,2}, \d{4})",
        r"(?:deadline|due|submissions? due|abstracts? due)[:\s]+(\d{1,2}/\d{1,2}/\d{4})",
        r"(?:deadline|due|submissions? due|abstracts? due)[:\s]+(\d{4}-\d{2}-\d{2})",
        r"(?:deadline|due|submissions? due|abstracts? due)[:\s]+(\d{1,2} [A-Z][a-z]+ \d{4})",
        r"\b([A-Z][a-z]+ \d{1,2}, \d{4})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if not match:
            continue
        parsed = _parse_date(match.group(1))
        if parsed:
            return parsed
    return None


def _parse_date(value: str) -> str | None:
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%m/%d/%Y", "%Y-%m-%d", "%d %B %Y", "%d %b %Y"):
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
