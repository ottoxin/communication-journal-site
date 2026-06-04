from __future__ import annotations

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
