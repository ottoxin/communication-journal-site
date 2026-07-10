from __future__ import annotations

from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from .models import (
    AppConfig,
    JournalConfig,
    SiteSettings,
    SpecialIssueSourceConfig,
    SubareaConfig,
)
from .special_issues import AGGREGATOR_SOURCE_TYPES


DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


def load_config(config_dir: str | Path = DEFAULT_CONFIG_DIR) -> AppConfig:
    config_path = Path(config_dir)
    journals_raw = _read_yaml(config_path / "journals.yaml")
    subareas_raw = _read_yaml(config_path / "subareas.yaml")
    special_raw = _read_yaml(config_path / "special_issue_sources.yaml")
    settings = SiteSettings(**journals_raw.get("settings", {}))
    subareas = [SubareaConfig(**item) for item in subareas_raw.get("subareas", [])]
    journals = [JournalConfig(**item) for item in journals_raw.get("journals", [])]
    special_sources = [
        SpecialIssueSourceConfig(**item)
        for item in special_raw.get("sources", [])
    ]
    _validate_config(settings, journals, subareas, special_sources)
    return AppConfig(
        settings=settings,
        journals=journals,
        subareas=subareas,
        special_issue_sources=special_sources,
    )


def repo_root_for_config(config_dir: str | Path = DEFAULT_CONFIG_DIR) -> Path:
    return Path(config_dir).resolve().parent


def resolve_project_path(config_dir: str | Path, configured_path: str) -> Path:
    path = Path(configured_path)
    if path.is_absolute():
        return path
    return repo_root_for_config(config_dir) / path


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML object.")
    return data


def _validate_config(
    settings: SiteSettings,
    journals: list[JournalConfig],
    subareas: list[SubareaConfig],
    special_sources: list[SpecialIssueSourceConfig],
) -> None:
    try:
        ZoneInfo(settings.timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone: {settings.timezone}") from exc
    if settings.default_lookback_days < 7:
        raise ValueError("default_lookback_days must be at least 7.")
    if settings.article_page_limit < 25:
        raise ValueError("article_page_limit must be at least 25.")
    if settings.freshness_warning_days < 1:
        raise ValueError("freshness_warning_days must be positive.")
    if settings.special_issue_verification_days < 1:
        raise ValueError("special_issue_verification_days must be positive.")
    subarea_ids = {subarea.id for subarea in subareas}
    if len(subarea_ids) != len(subareas):
        raise ValueError("Subarea ids must be unique.")
    unknown_featured = [item for item in settings.featured_subareas if item not in subarea_ids]
    if unknown_featured:
        raise ValueError(f"Unknown featured subarea(s): {', '.join(unknown_featured)}")
    journal_ids = {journal.id for journal in journals}
    if len(journal_ids) != len(journals):
        raise ValueError("Journal ids must be unique.")
    for journal in journals:
        if journal.active and not journal.issns:
            raise ValueError(f"{journal.id} is active but has no ISSNs.")
        missing = [subarea for subarea in journal.subareas if subarea not in subarea_ids]
        if missing:
            raise ValueError(f"{journal.id} uses unknown subarea(s): {', '.join(missing)}")
    for source in special_sources:
        # Feed/aggregator/hub sources span multiple journals, so they are not
        # required to map to a registry journal; page sources still must.
        if source.source_type in AGGREGATOR_SOURCE_TYPES:
            continue
        if source.journal_id not in journal_ids:
            raise ValueError(f"{source.id} refers to unknown journal_id {source.journal_id}.")
