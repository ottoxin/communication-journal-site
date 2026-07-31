from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin, urlsplit

from .http_client import HttpClient
from .models import JournalConfig, SpecialIssueRecord, SpecialIssueSourceConfig
from .normalize import clean_markup_text, normalize_whitespace
from .render import RenderedFetcher

FEED_SOURCE_TYPES = ("rss", "atom", "feed")
RENDERED_SOURCE_TYPE = "rendered"
MANUAL_SOURCE_TYPE = "manual"
TANDF_API_SOURCE_TYPE = "tandf-api"
# Source types whose journal_id need not map to a registry journal (they span journals).
AGGREGATOR_SOURCE_TYPES = FEED_SOURCE_TYPES + (
    RENDERED_SOURCE_TYPE,
    "publisher-index",
    "association-page",
    TANDF_API_SOURCE_TYPE,
    MANUAL_SOURCE_TYPE,
)
SOURCE_TYPES = ("publisher-page", *AGGREGATOR_SOURCE_TYPES)
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
    "focus of the special issue",
    "for a special issue on",
    "special issue editor(s)",
    "submissions",
    "submit",
)


class TextBlockParser(HTMLParser):
    block_tags = {"h1", "h2", "h3", "h4", "p", "li", "a"}

    def __init__(self):
        super().__init__()
        self.blocks: list[tuple[str, str, str | None]] = []
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
            self.blocks.append((self._active_tag, text, self._active_href))
        self._active_tag = None
        self._active_href = None
        self._buffer = []


def parse_special_issues_from_html(
    html: str,
    source: SpecialIssueSourceConfig,
    journal: JournalConfig,
    base_url: str,
    today: date | None = None,
) -> list[SpecialIssueRecord]:
    parser = TextBlockParser()
    parser.feed(html)
    parser.close()
    records: list[SpecialIssueRecord] = []
    seen_titles: set[str] = set()
    blocks = parser.blocks
    # Taylor & Francis call pages use a stable labeled layout in which the
    # topic itself does not contain the words "special issue":
    #   <p>For a Special Issue on</p><h2>Topic</h2>
    # Handle that structure explicitly before applying the generic heuristic.
    labeled_record = _parse_labeled_special_issue_page(
        html, blocks, source=source, journal=journal, today=today
    )
    if labeled_record is not None:
        return [labeled_record]
    for index, (tag, text, href) in enumerate(blocks):
        combined_text, context_href = _html_candidate_context(blocks, index)
        if not _is_special_issue_candidate(text, combined_text) and not _is_contextual_heading_candidate(
            tag, text, combined_text, journal.title
        ):
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
                source_url=urljoin(base_url, href or context_href) if (href or context_href) else source.url,
                status=_status_for_deadline(deadline, today=today),
                deadline=deadline,
                confidence=_confidence(combined_text),
                raw_snippet=combined_text[:500],
            )
        )
    return records


def _parse_labeled_special_issue_page(
    html: str,
    blocks: list[tuple[str, str, str | None]],
    source: SpecialIssueSourceConfig,
    journal: JournalConfig,
    today: date | None,
) -> SpecialIssueRecord | None:
    marker_index = next(
        (
            index
            for index, (_, text, _) in enumerate(blocks)
            if normalize_whitespace(text).lower().rstrip(":") == "for a special issue on"
        ),
        None,
    )
    if marker_index is None:
        return None
    topic = next(
        (
            text
            for tag, text, _ in blocks[marker_index + 1 : marker_index + 6]
            if tag in {"h1", "h2", "h3", "h4"} and not _is_generic_title(text)
        ),
        "",
    )
    if not topic:
        return None
    title = topic if any(phrase in topic.lower() for phrase in SPECIAL_ISSUE_PHRASES) else f"Special Issue: {topic}"
    full_text = clean_markup_text(html) or ""
    deadline = _extract_deadline(full_text)
    marker = full_text.lower().find("for a special issue on")
    snippet = full_text[marker : marker + 500] if marker >= 0 else full_text[:500]
    return SpecialIssueRecord(
        source_id=source.id,
        journal_id=journal.id,
        journal_title=journal.title,
        title=title,
        source_url=source.url,
        status=_status_for_deadline(deadline, today=today),
        deadline=deadline,
        confidence="high" if deadline else "medium",
        raw_snippet=snippet,
    )


def parse_special_issues_from_feed(
    feed_xml: str,
    source: SpecialIssueSourceConfig,
    journal: JournalConfig,
    base_url: str,
    today: date | None = None,
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
                status=_status_for_deadline(deadline, today=today),
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
        self.authoritative_source_ids: set[str] = set()

    def collect(
        self,
        sources: list[SpecialIssueSourceConfig],
        journal_by_id: dict[str, JournalConfig],
        today: date | None = None,
    ) -> tuple[list[SpecialIssueRecord], list[dict[str, str]]]:
        records: list[SpecialIssueRecord] = []
        errors: list[dict[str, str]] = []
        self.successful_source_ids = set()
        self.authoritative_source_ids = set()
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
                        executor.submit(
                            self._collect_source, source, journal_by_id, journal_titles, today
                        ): source
                        for source in http_sources
                    }
                    for future in as_completed(futures):
                        source = futures[future]
                        try:
                            source_records = future.result()
                            records.extend(source_records)
                            self.successful_source_ids.add(source.id)
                            if source_records or source.source_type == TANDF_API_SOURCE_TYPE:
                                self.authoritative_source_ids.add(source.id)
                        except Exception as exc:
                            errors.append({"source_id": source.id, "error": str(exc)})
            for source in rendered_sources:
                try:
                    if self._rendered_fetcher is None:
                        self._rendered_fetcher = RenderedFetcher()
                        owns_fetcher = True
                    source_records = self._collect_source(
                        source, journal_by_id, journal_titles, today
                    )
                    records.extend(source_records)
                    self.successful_source_ids.add(source.id)
                    if source_records:
                        self.authoritative_source_ids.add(source.id)
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
        today: date | None,
    ) -> list[SpecialIssueRecord]:
        is_aggregator = source.journal_id not in journal_by_id
        journal = journal_by_id.get(source.journal_id) or JournalConfig(
            id=source.journal_id or "aggregator",
            title=source.label or source.journal_id or "Various sources",
            issns=[],
        )
        if source.source_type == MANUAL_SOURCE_TYPE:
            review_expired = bool(
                source.review_after
                and date.fromisoformat(source.review_after) < (today or date.today())
            )
            return [
                SpecialIssueRecord(
                    source_id=source.id,
                    journal_id=journal.id,
                    journal_title=journal.title,
                    title=source.title,
                    source_url=source.url,
                    status=(
                        "unverified"
                        if review_expired
                        else _status_for_deadline(source.deadline, today=today)
                    ),
                    deadline=source.deadline,
                    confidence="high",
                    raw_snippet=(
                        source.summary
                        or f"Verified against the official source on {source.verified_on}."
                    ),
                )
            ]
        if source.source_type == TANDF_API_SOURCE_TYPE:
            return self._collect_tandf_api(source, journal_by_id, today=today)
        if source.source_type == RENDERED_SOURCE_TYPE:
            if self._rendered_fetcher is None:
                raise RuntimeError("Rendered fetcher is not initialized.")
            body = self._rendered_fetcher.get_text(source.url)
            base_url = source.url
        else:
            response = self.http_client.get_text(source.url)
            body = response.body
            base_url = response.url or source.url
        if _looks_like_block_page(body):
            raise RuntimeError("publisher returned an access or bot-challenge page")
        if source.source_type in FEED_SOURCE_TYPES:
            parsed = parse_special_issues_from_feed(
                body, source=source, journal=journal, base_url=base_url, today=today
            )
        else:
            parsed = parse_special_issues_from_html(
                body, source=source, journal=journal, base_url=base_url, today=today
            )
        if is_aggregator:
            parsed = [record for record in parsed if _is_communication_relevant(record, journal_titles)]
        return parsed

    def _collect_tandf_api(
        self,
        source: SpecialIssueSourceConfig,
        journal_by_id: dict[str, JournalConfig],
        today: date | None,
    ) -> list[SpecialIssueRecord]:
        entries: list[dict] = []
        page_size = 100
        total_pages: int | None = None
        for page in range(1, 51):
            query = urlencode(
                {
                    "per_page": page_size,
                    "page": page,
                    "_fields": "id,slug,link,title,meta,special_issues",
                }
            )
            separator = "&" if "?" in source.url else "?"
            response = self.http_client.get_text(f"{source.url}{separator}{query}")
            if total_pages is None and response.headers:
                try:
                    total_pages = int(response.headers.get("x-wp-totalpages", ""))
                except ValueError:
                    total_pages = None
            try:
                payload = json.loads(response.body)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Taylor & Francis API returned invalid JSON: {exc}") from exc
            if not isinstance(payload, list):
                raise RuntimeError("Taylor & Francis API response was not a list")
            entries.extend(item for item in payload if isinstance(item, dict))
            if (total_pages is not None and page >= total_pages) or len(payload) < page_size:
                break
        else:
            raise RuntimeError("Taylor & Francis API exceeded the pagination safety limit")
        return parse_tandf_api_records(entries, source, journal_by_id, today=today)


def parse_tandf_api_records(
    entries: list[dict],
    source: SpecialIssueSourceConfig,
    journal_by_id: dict[str, JournalConfig],
    today: date | None = None,
) -> list[SpecialIssueRecord]:
    today = today or date.today()
    journals_by_title = {
        normalize_whitespace(journal.title).lower(): journal
        for journal in journal_by_id.values()
    }
    journals_by_code: dict[str, JournalConfig] = {}
    for journal in journal_by_id.values():
        for url in (journal.homepage_url, journal.latest_articles_url):
            match = re.search(r"/(?:journals|toc)/([a-z0-9]+)", urlsplit(url).path, flags=re.I)
            if match:
                journals_by_code[re.sub(r"\d+$", "", match.group(1).lower())] = journal

    records: list[SpecialIssueRecord] = []
    seen_urls: set[str] = set()
    for entry in entries:
        fields = entry.get("special_issues")
        if not isinstance(fields, dict):
            continue
        journal_title = _first_api_value(fields.get("_special_issues_journal_title"))
        journal_code = re.sub(
            r"\d+$", "", _first_api_value(fields.get("_special_issues_journal_select")).lower()
        )
        journal = journals_by_code.get(journal_code) or journals_by_title.get(
            normalize_whitespace(journal_title).lower()
        )
        if journal is None:
            continue
        topic = _first_api_value(fields.get("_special_issues_title"))
        if not topic:
            title_payload = entry.get("title")
            topic = (
                str(title_payload.get("rendered", ""))
                if isinstance(title_payload, dict)
                else str(title_payload or "")
            )
        topic = clean_markup_text(topic) or ""
        link = str(entry.get("link") or "").strip()
        if not topic or not link or link in seen_urls:
            continue
        # T&F can expose an abstract/proposal deadline and a later, invitation-
        # only manuscript deadline. The earliest configured submission stage is
        # the conservative public-entry deadline; webpage expiry metadata is not
        # a submission deadline and is intentionally ignored.
        deadlines = [
            _parse_date(value)
            for key in ("_special_issues_deadline", "_special_issues_deadline2")
            for value in _api_values(fields.get(key))
        ]
        valid_deadlines = sorted(value for value in deadlines if value)
        if not valid_deadlines:
            continue
        deadline = valid_deadlines[0]
        if _status_for_deadline(deadline, today=today) != "active":
            continue
        title = topic if any(phrase in topic.lower() for phrase in SPECIAL_ISSUE_PHRASES) else f"Special Issue: {topic}"
        instructions = clean_markup_text(
            " ".join(_api_values(fields.get("_special_issues_submissions_instructions")))
        ) or ""
        opens_on = _extract_submission_open_date(instructions)
        status = "upcoming" if opens_on and date.fromisoformat(opens_on) > today else "active"
        records.append(
            SpecialIssueRecord(
                source_id=source.id,
                journal_id=journal.id,
                journal_title=journal.title,
                title=title,
                source_url=link,
                status=status,
                deadline=deadline,
                confidence="high",
                raw_snippet=instructions[:500] or f"Official Taylor & Francis call for {journal.title}.",
            )
        )
        seen_urls.add(link)
    return records


def _api_values(value: object) -> list[str]:
    if isinstance(value, list):
        return [normalize_whitespace(str(item)) for item in value if normalize_whitespace(str(item))]
    normalized = normalize_whitespace(str(value or ""))
    return [normalized] if normalized else []


def _first_api_value(value: object) -> str:
    values = _api_values(value)
    return values[0] if values else ""


def _extract_submission_open_date(text: str) -> str | None:
    date_patterns = (
        r"[A-Z][a-z]+ \d{1,2}(?:st|nd|rd|th)?,? \d{4}",
        r"\d{1,2}(?:st|nd|rd|th)? [A-Z][a-z]+ \d{4}",
        r"\d{4}-\d{2}-\d{2}",
    )
    labels = (
        r"special issue submission window opens?",
        r"submission window opens?",
        r"submissions? (?:will )?opens?",
    )
    for label in labels:
        for date_pattern in date_patterns:
            match = re.search(
                rf"{label}[:\s]+(?:on\s+)?({date_pattern})",
                text,
                flags=re.I,
            )
            if match:
                parsed = _parse_date(match.group(1))
                if parsed:
                    return parsed
    return None


def _candidate_title(text: str) -> str:
    cleaned = normalize_whitespace(text)
    cleaned = re.sub(r"^(open )?call for (papers|submissions)[:\-\s]+", "", cleaned, flags=re.I)
    sentence = re.split(r"(?<=[.!?])\s+", cleaned)[0]
    if len(sentence) > 180:
        sentence = sentence[:177].rstrip() + "..."
    return sentence


def _html_candidate_context(
    blocks: list[tuple[str, str, str | None]],
    index: int,
    max_following: int = 4,
) -> tuple[str, str | None]:
    _, text, href = blocks[index]
    context = [text]
    context_href = href
    for next_tag, next_text, next_href in blocks[index + 1 : index + 1 + max_following]:
        if next_tag in {"h1", "h2", "h3", "h4"}:
            break
        context.append(next_text)
        if context_href is None and next_href:
            context_href = next_href
    return normalize_whitespace(" ".join(context)), context_href


def _is_contextual_heading_candidate(
    tag: str,
    text: str,
    combined_text: str,
    journal_title: str,
) -> bool:
    if tag not in {"h1", "h2", "h3", "h4", "a"}:
        return False
    normalized = normalize_whitespace(text).lower().rstrip(":")
    if not normalized or normalized == normalize_whitespace(journal_title).lower().rstrip(":"):
        return False
    if _is_generic_title(text) or len(text) > 180:
        return False
    lowered = combined_text.lower()
    return any(phrase in lowered for phrase in SPECIAL_ISSUE_PHRASES) and any(
        phrase in lowered for phrase in CALL_PHRASES
    )


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


def special_issue_identity_title(title: str) -> str:
    """Normalize presentational CFP wording into a stable topic identity."""
    normalized = normalize_whitespace(title).lower()
    normalized = re.sub(r"^(open )?call for (papers|submissions)[:\-\s]+", "", normalized)
    normalized = re.sub(
        r"\b(call for papers|call for submissions|special issue|special section|thematic issue|theme issue)\b",
        " ",
        normalized,
    )
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    normalized = re.sub(r"^(?:on|for)\s+", "", normalize_whitespace(normalized))
    return normalize_whitespace(normalized) or normalize_whitespace(title).lower()


def special_issue_opportunity_type(title: str) -> str:
    lowered = title.lower()
    if "proposal" in lowered:
        return "special-issue-proposal"
    return "call-for-papers"


def _extract_deadline(text: str) -> str | None:
    patterns = [
        r"(?:deadline|due|submissions? due|abstracts? due|submit by)[:\s]+([A-Z][a-z]+ \d{1,2}(?:st|nd|rd|th)?,? \d{4})",
        r"(?:deadline|due|submissions? due|abstracts? due)[:\s]+(\d{1,2}/\d{1,2}/\d{4})",
        r"(?:deadline|due|submissions? due|abstracts? due)[:\s]+(\d{4}-\d{2}-\d{2})",
        r"(?:deadline|due|submissions? due|abstracts? due|submit by)[:\s]+(\d{1,2}(?:st|nd|rd|th)? [A-Z][a-z]+ \d{4})",
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
    value = re.sub(r"(?<=\d)(st|nd|rd|th)\b", "", value, flags=re.I)
    for fmt in (
        "%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%b %d %Y",
        "%m/%d/%Y", "%Y-%m-%d", "%d %B %Y", "%d %b %Y",
    ):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _status_for_deadline(deadline: str | None, today: date | None = None) -> str:
    if not deadline:
        return "active"
    today = today or date.today()
    try:
        return "closed" if date.fromisoformat(deadline) < today else "active"
    except ValueError:
        return "active"


def _confidence(text: str) -> str:
    lowered = text.lower()
    if "special issue" in lowered and ("deadline" in lowered or "due" in lowered):
        return "high"
    if "call for papers" in lowered or "special issue" in lowered:
        return "medium"
    return "low"


def _looks_like_block_page(body: str) -> bool:
    lowered = body.lower()
    markers = (
        "attention required! | cloudflare",
        "access denied",
        "enable javascript and cookies to continue",
        "verify you are human",
        "captcha",
        "cloudflare ray id",
    )
    return any(marker in lowered for marker in markers)
