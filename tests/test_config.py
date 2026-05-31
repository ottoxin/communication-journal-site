from __future__ import annotations

from communication_journal_site.config import load_config


def test_loads_seed_registry() -> None:
    config = load_config()
    assert config.settings.title == "Communication Journal Monitor"
    assert len(config.journals) >= 50
    assert "political-communication" in config.subarea_by_id
    assert config.journal_by_id["journal-of-communication"].primary_subarea == "general-communication"


def test_all_journal_subareas_are_configured() -> None:
    config = load_config()
    subareas = set(config.subarea_by_id)
    for journal in config.journals:
        assert set(journal.subareas).issubset(subareas)

