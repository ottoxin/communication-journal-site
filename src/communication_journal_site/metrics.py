from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

from .http_client import HttpClient
from .models import JournalConfig


OPENALEX_SOURCES_API = "https://api.openalex.org/sources"
METRIC_ID = "openalex-2yr-mean-citedness"
METRIC_LABEL = "OpenAlex 2-year mean citedness"
METRIC_DEFINITION_URL = (
    "https://github.com/ourresearch/openalex-docs/blob/main/"
    "api-entities/sources/source-object.md#summary_stats"
)
OPENALEX_LICENSE_URL = "https://help.openalex.org/hc/en-us/articles/24397762024087-Pricing"


def collect_openalex_journal_metrics(
    journals: list[JournalConfig],
    output_path: str | Path,
    *,
    http_client: HttpClient | None = None,
    api_key: str | None = None,
    today: date | None = None,
) -> dict:
    """Collect one complete ISSN-matched snapshot, preserving the old file on failure."""

    active = [journal for journal in journals if journal.active]
    # One stable registry ISSN per journal keeps the OpenAlex OR filter below
    # its 100-value limit; returned source records still include every linked ISSN.
    issns = sorted({journal.issns[0] for journal in active})
    query = {
        "filter": f"issn:{'|'.join(issns)}",
        "per-page": "100",
        "select": "id,issn_l,issn,display_name,summary_stats,updated_date,type",
    }
    if api_key:
        query["api_key"] = api_key
    client = http_client or HttpClient(timeout=30, max_attempts=3)
    payload = client.get_json(f"{OPENALEX_SOURCES_API}?{urlencode(query)}")
    results = payload.get("results")
    if not isinstance(results, list):
        raise RuntimeError("OpenAlex source response did not include a result list.")

    by_issn: dict[str, dict] = {}
    for result in results:
        if not isinstance(result, dict) or result.get("type") != "journal":
            continue
        result_issns = {str(item) for item in result.get("issn") or []}
        if result.get("issn_l"):
            result_issns.add(str(result["issn_l"]))
        for issn in result_issns:
            by_issn.setdefault(issn, result)

    rows: dict[str, dict] = {}
    missing: list[str] = []
    for journal in active:
        result = next((by_issn[issn] for issn in journal.issns if issn in by_issn), None)
        stats = result.get("summary_stats", {}) if result else {}
        value = stats.get("2yr_mean_citedness") if isinstance(stats, dict) else None
        if result is None or not isinstance(value, (int, float)):
            missing.append(journal.id)
            continue
        openalex_id = str(result.get("id") or "")
        rows[journal.id] = {
            "value": float(value),
            "openalex_id": openalex_id,
            "source_url": openalex_id,
            "source_updated_at": str(result.get("updated_date") or ""),
            "matched_issns": sorted(set(journal.issns) & set(result.get("issn") or [])),
        }
    if missing:
        raise RuntimeError(
            "OpenAlex returned an incomplete journal-metric snapshot for: " + ", ".join(missing)
        )

    today = today or date.today()
    snapshot = {
        "schema_version": 1,
        "metric_id": METRIC_ID,
        "metric_label": METRIC_LABEL,
        "definition_url": METRIC_DEFINITION_URL,
        "license_url": OPENALEX_LICENSE_URL,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "retrieved_on": today.isoformat(),
        "citation_year": today.year - 1,
        "publication_years": [today.year - 3, today.year - 2],
        "journal_count": len(rows),
        "journals": dict(sorted(rows.items())),
    }
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(snapshot, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    temporary.replace(output)
    return snapshot
