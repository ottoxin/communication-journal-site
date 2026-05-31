from __future__ import annotations

import csv
from pathlib import Path

from communication_journal_site.audit import audit_journal_registry
from communication_journal_site.config import load_config


def test_audit_reports_source_list_missing_from_registry(tmp_path: Path) -> None:
    config = load_config()
    source_list = tmp_path / "source.csv"
    source_list.write_text(
        "Title,ISSN,Publisher\nJournal of Communication,0021-9916,OUP\nUnconfigured Communication Journal,1234-5678,Test\n",
        encoding="utf-8",
    )
    output = audit_journal_registry(config.journals, tmp_path / "audit.csv", source_list)
    rows = list(csv.DictReader(output.open("r", encoding="utf-8")))
    missing = [row for row in rows if row["status"] == "missing_from_registry"]
    assert len(missing) == 1
    assert missing[0]["title"] == "Unconfigured Communication Journal"

