from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class SiteSettings:
    title: str
    description: str
    timezone: str
    state_dir: str
    output_dir: str
    default_lookback_days: int
    registry_source_note: str
    featured_subareas: list[str] = field(
        default_factory=lambda: ["political-communication", "health-communication", "methods"]
    )
    article_page_limit: int = 250
    freshness_warning_days: int = 10
    special_issue_verification_days: int = 10
    site_url: str = ""


@dataclass(frozen=True, slots=True)
class SubareaConfig:
    id: str
    label: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class JournalConfig:
    id: str
    title: str
    issns: list[str]
    publisher: str = ""
    homepage_url: str = ""
    latest_articles_url: str = ""
    primary_subarea: str = "general-communication"
    secondary_subareas: list[str] = field(default_factory=list)
    associations: list[str] = field(default_factory=list)
    registry_sources: list[str] = field(default_factory=list)
    active: bool = True
    collect: bool = True
    special_issue_monitor: bool = False
    special_issue_checked_on: str | None = None
    special_issue_audit_url: str = ""
    notes: str = ""

    @property
    def subareas(self) -> list[str]:
        ordered = [self.primary_subarea, *self.secondary_subareas]
        seen: set[str] = set()
        return [item for item in ordered if item and not (item in seen or seen.add(item))]


@dataclass(frozen=True, slots=True)
class SpecialIssueSourceConfig:
    id: str
    journal_id: str
    label: str
    url: str
    source_type: str = "publisher-page"
    active: bool = True
    selectors_hint: str = ""
    title: str = ""
    deadline: str | None = None
    verified_on: str | None = None
    review_after: str | None = None
    summary: str = ""


@dataclass(frozen=True, slots=True)
class AppConfig:
    settings: SiteSettings
    journals: list[JournalConfig]
    subareas: list[SubareaConfig]
    special_issue_sources: list[SpecialIssueSourceConfig]

    @property
    def journal_by_id(self) -> dict[str, JournalConfig]:
        return {journal.id: journal for journal in self.journals}

    @property
    def subarea_by_id(self) -> dict[str, SubareaConfig]:
        return {subarea.id: subarea for subarea in self.subareas}


@dataclass(slots=True)
class ArticleRecord:
    journal_id: str
    journal_title: str
    title: str
    published_date: str
    article_type: str = "journal-article"
    doi: str | None = None
    canonical_url: str | None = None
    abstract: str | None = None
    authors: list[str] = field(default_factory=list)
    affiliations: list[str] = field(default_factory=list)
    subjects: list[str] = field(default_factory=list)
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    publisher: str | None = None
    source_issn: str | None = None
    first_seen_at: str | None = None
    last_seen_at: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SpecialIssueRecord:
    source_id: str
    journal_id: str
    journal_title: str
    title: str
    source_url: str
    status: str = "active"
    deadline: str | None = None
    discovered_at: str | None = None
    last_seen_at: str | None = None
    confidence: str = "medium"
    raw_snippet: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class WeeklyWindows:
    digest_date: str
    new_this_week_start: str
    new_this_week_end: str
    previous_week_start: str
    previous_week_end: str
    late_additions_before: str
    late_additions_first_seen_since: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)
