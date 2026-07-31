from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from communication_journal_site.metrics import collect_openalex_journal_metrics
from communication_journal_site.models import JournalConfig


class FakeClient:
    def __init__(self, payload: dict):
        self.payload = payload

    def get_json(self, url: str) -> dict:
        assert "filter=issn%3A0000-0001" in url
        return self.payload


def test_metric_snapshot_is_complete_and_source_linked(tmp_path: Path) -> None:
    journal = JournalConfig(id="test", title="Test Journal", issns=["0000-0001"])
    payload = {
        "results": [{
            "id": "https://openalex.org/S1",
            "type": "journal",
            "issn": ["0000-0001"],
            "issn_l": "0000-0001",
            "summary_stats": {"2yr_mean_citedness": 3.25},
            "updated_date": "2026-07-29T00:00:00",
        }]
    }
    output = tmp_path / "metrics.json"

    snapshot = collect_openalex_journal_metrics(
        [journal], output, http_client=FakeClient(payload), today=date(2026, 7, 30)
    )

    assert snapshot["citation_year"] == 2025
    assert snapshot["publication_years"] == [2023, 2024]
    assert snapshot["journals"]["test"]["value"] == 3.25
    assert json.loads(output.read_text())["journals"]["test"]["source_url"] == "https://openalex.org/S1"


def test_metric_failure_preserves_last_known_good_file(tmp_path: Path) -> None:
    journal = JournalConfig(id="test", title="Test Journal", issns=["0000-0001"])
    output = tmp_path / "metrics.json"
    output.write_text('{"old": true}\n', encoding="utf-8")

    with pytest.raises(RuntimeError, match="incomplete"):
        collect_openalex_journal_metrics(
            [journal], output, http_client=FakeClient({"results": []}),
            today=date(2026, 7, 30),
        )

    assert output.read_text(encoding="utf-8") == '{"old": true}\n'
