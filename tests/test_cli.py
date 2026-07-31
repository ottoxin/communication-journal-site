from __future__ import annotations

from argparse import Namespace
from types import SimpleNamespace

from communication_journal_site import cli
from communication_journal_site.cli import _select_journals, build_parser
from communication_journal_site.models import JournalConfig


def _journal(jid: str, active: bool = True, collect: bool = True) -> JournalConfig:
    return JournalConfig(id=jid, title=jid.title(), issns=["0000-0000"], active=active, collect=collect)


def test_select_journals_defaults_to_active_and_collectable() -> None:
    journals = [_journal("a"), _journal("b", collect=False), _journal("c", active=False)]
    assert [j.id for j in _select_journals(journals, None)] == ["a"]


def test_select_journals_filters_by_ids() -> None:
    journals = [_journal("a"), _journal("b"), _journal("c")]
    assert [j.id for j in _select_journals(journals, "b, c")] == ["b", "c"]


def test_select_journals_drops_unknown_or_inactive_ids() -> None:
    journals = [_journal("a"), _journal("b", active=False)]
    assert _select_journals(journals, "b,does-not-exist") == []


def test_collect_papers_parser_accepts_journals_flag() -> None:
    args = build_parser().parse_args(["collect-papers", "--journals", "x,y"])
    assert args.journals == "x,y"


def test_weekly_parser_supports_strict_mode() -> None:
    args = build_parser().parse_args(["run-weekly", "--strict"])
    assert args.strict is True


def test_fetch_covers_parser_supports_import_and_strict_mode() -> None:
    args = build_parser().parse_args(
        ["fetch-covers", "--import-dir", "downloads/covers", "--strict"]
    )
    assert args.import_dir == "downloads/covers"
    assert args.strict is True


def test_weekly_run_publishes_partial_results_unless_strict(monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    monkeypatch.setattr(cli, "cmd_collect_papers", lambda args: calls.append("papers") or 1)
    monkeypatch.setattr(cli, "cmd_collect_special_issues", lambda args: calls.append("calls") or 1)
    monkeypatch.setattr(cli, "cmd_export_public_data", lambda args: calls.append("export") or 0)
    monkeypatch.setattr(cli, "cmd_build_site", lambda args: calls.append("build") or 0)
    args = Namespace(
        config_dir=str(cli.DEFAULT_CONFIG_DIR),
        state_dir=str(tmp_path / "state"),
        output_dir=str(tmp_path / "site"),
        digest_date="2026-06-20",
        lookback_days=None,
        no_openalex=True,
        strict=False,
    )

    assert cli.cmd_run_weekly(args) == 0
    assert calls == ["papers", "calls", "export", "build"]
    args.strict = True
    assert cli.cmd_run_weekly(args) == 1


def test_paper_collection_stops_after_repeated_upstream_failures(monkeypatch, tmp_path) -> None:
    attempted: list[str] = []

    class FailingCrossref:
        def __init__(self, *args, **kwargs):
            pass

        def fetch_journal_records(self, journal, start_date, end_date):
            attempted.append(journal.id)
            raise RuntimeError("Crossref unavailable")

    config = SimpleNamespace(
        settings=SimpleNamespace(
            timezone="America/Chicago",
            default_lookback_days=28,
            state_dir=str(tmp_path / "state"),
        ),
        journals=[
            _journal("a"), _journal("b"), _journal("c"), _journal("d"), _journal("e")
        ],
    )
    monkeypatch.setattr(cli, "load_config", lambda config_dir: config)
    monkeypatch.setattr(cli, "CrossrefClient", FailingCrossref)
    monkeypatch.setattr(cli, "_write_archive", lambda *args, **kwargs: tmp_path / "archive.json")
    args = Namespace(
        config_dir=str(cli.DEFAULT_CONFIG_DIR),
        state_dir=str(tmp_path / "state"),
        end_date="2026-06-20",
        lookback_days=28,
        no_openalex=True,
        journals=None,
    )

    assert cli.cmd_collect_papers(args) == 1
    assert attempted == ["a", "b", "c", "d"]


def test_paper_collection_does_not_stop_after_journal_404s(monkeypatch, tmp_path) -> None:
    attempted: list[str] = []

    class NotFoundCrossref:
        def __init__(self, *args, **kwargs):
            pass

        def fetch_journal_records(self, journal, start_date, end_date):
            attempted.append(journal.id)
            raise RuntimeError("Could not fetch Crossref URL: HTTP Error 404: Not Found")

    config = SimpleNamespace(
        settings=SimpleNamespace(
            timezone="America/Chicago",
            default_lookback_days=28,
            state_dir=str(tmp_path / "state"),
        ),
        journals=[_journal("a"), _journal("b"), _journal("c")],
    )
    monkeypatch.setattr(cli, "load_config", lambda config_dir: config)
    monkeypatch.setattr(cli, "CrossrefClient", NotFoundCrossref)
    monkeypatch.setattr(cli, "_write_archive", lambda *args, **kwargs: tmp_path / "archive.json")
    args = Namespace(
        config_dir=str(cli.DEFAULT_CONFIG_DIR),
        state_dir=str(tmp_path / "state"),
        end_date="2026-06-20",
        lookback_days=28,
        no_openalex=True,
        journals=None,
    )

    assert cli.cmd_collect_papers(args) == 1
    assert attempted == ["a", "b", "c"]
