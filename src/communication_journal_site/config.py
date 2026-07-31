from __future__ import annotations

import json
from pathlib import Path
from datetime import date
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from .models import (
    AppConfig,
    JournalConfig,
    JournalMetric,
    SiteSettings,
    SpecialIssueSourceConfig,
    SubareaConfig,
)
from .special_issues import AGGREGATOR_SOURCE_TYPES, SOURCE_TYPES


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
    metric_path = resolve_project_path(config_path, settings.journal_metrics_path)
    journal_metrics, metric_metadata = _load_journal_metrics(metric_path)
    _validate_config(settings, journals, subareas, special_sources, journal_metrics)
    return AppConfig(
        settings=settings,
        journals=journals,
        subareas=subareas,
        special_issue_sources=special_sources,
        journal_metrics=journal_metrics,
        journal_metric_metadata=metric_metadata,
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


def _load_journal_metrics(path: Path) -> tuple[dict[str, JournalMetric], dict[str, Any]]:
    if not path.exists():
        return {}, {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Could not read journal metrics from {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object.")
    rows = payload.get("journals", {})
    if not isinstance(rows, dict):
        raise ValueError(f"{path} journals must be a JSON object.")
    metrics: dict[str, JournalMetric] = {}
    for journal_id, row in rows.items():
        if not isinstance(row, dict):
            raise ValueError(f"Metric for {journal_id} must be a JSON object.")
        metrics[journal_id] = JournalMetric(
            journal_id=journal_id,
            value=float(row["value"]),
            source_url=str(row["source_url"]),
            openalex_id=str(row["openalex_id"]),
            source_updated_at=str(row.get("source_updated_at", "")),
            matched_issns=[str(item) for item in row.get("matched_issns", [])],
        )
    metadata = {key: value for key, value in payload.items() if key != "journals"}
    return metrics, metadata


def _validate_config(
    settings: SiteSettings,
    journals: list[JournalConfig],
    subareas: list[SubareaConfig],
    special_sources: list[SpecialIssueSourceConfig],
    journal_metrics: dict[str, JournalMetric],
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
    unknown_metric_ids = sorted(set(journal_metrics) - journal_ids)
    if unknown_metric_ids:
        raise ValueError(f"Metrics use unknown journal id(s): {', '.join(unknown_metric_ids)}")
    for metric in journal_metrics.values():
        if metric.value < 0:
            raise ValueError(f"{metric.journal_id} metric value cannot be negative.")
        parsed_metric_url = urlparse(metric.source_url)
        if parsed_metric_url.scheme not in {"http", "https"} or not parsed_metric_url.netloc:
            raise ValueError(f"{metric.journal_id} must use a valid metric source URL.")
    for journal in journals:
        if journal.active and not journal.issns:
            raise ValueError(f"{journal.id} is active but has no ISSNs.")
        missing = [subarea for subarea in journal.subareas if subarea not in subarea_ids]
        if missing:
            raise ValueError(f"{journal.id} uses unknown subarea(s): {', '.join(missing)}")
        if journal.special_issue_checked_on:
            if not journal.special_issue_audit_url:
                raise ValueError(
                    f"{journal.id} special_issue_checked_on requires special_issue_audit_url."
                )
            try:
                date.fromisoformat(journal.special_issue_checked_on)
            except ValueError as exc:
                raise ValueError(
                    f"{journal.id} special_issue_checked_on must use YYYY-MM-DD."
                ) from exc
        if journal.special_issue_audit_url:
            parsed_audit_url = urlparse(journal.special_issue_audit_url)
            if parsed_audit_url.scheme not in {"http", "https"} or not parsed_audit_url.netloc:
                raise ValueError(f"{journal.id} must use a valid special_issue_audit_url.")
    source_ids = {source.id for source in special_sources}
    if len(source_ids) != len(special_sources):
        raise ValueError("Special-issue source ids must be unique.")
    for source in special_sources:
        if source.source_type not in SOURCE_TYPES:
            raise ValueError(
                f"{source.id} uses unsupported source_type {source.source_type!r}."
            )
        parsed_url = urlparse(source.url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError(f"{source.id} must use a valid HTTP(S) URL.")
        if source.source_type == "manual":
            if not source.title.strip():
                raise ValueError(f"{source.id} manual source must define a title.")
            if not source.verified_on or not source.review_after:
                raise ValueError(
                    f"{source.id} manual source must define verified_on and review_after."
                )
            for field_name, value in (
                ("deadline", source.deadline),
                ("verified_on", source.verified_on),
                ("review_after", source.review_after),
            ):
                if not value:
                    continue
                try:
                    date.fromisoformat(value)
                except ValueError as exc:
                    raise ValueError(
                        f"{source.id} manual source {field_name} must use YYYY-MM-DD."
                    ) from exc
            if date.fromisoformat(source.review_after) < date.fromisoformat(source.verified_on):
                raise ValueError(f"{source.id} review_after cannot precede verified_on.")
        # Feed/aggregator/hub sources span multiple journals, so they are not
        # required to map to a registry journal; page sources still must.
        if source.source_type in AGGREGATOR_SOURCE_TYPES:
            continue
        if source.journal_id not in journal_ids:
            raise ValueError(f"{source.id} refers to unknown journal_id {source.journal_id}.")
