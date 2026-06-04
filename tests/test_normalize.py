from __future__ import annotations

from communication_journal_site.normalize import clean_abstract
from communication_journal_site.site import _truncate


def test_clean_abstract_strips_leading_label() -> None:
    assert clean_abstract("Abstract Numerous stakeholders contend that") == (
        "Numerous stakeholders contend that"
    )
    assert clean_abstract("ABSTRACT: This study examines") == "This study examines"
    assert clean_abstract("<jats:p>Abstract&#x2014;We argue</jats:p>") == "We argue"


def test_clean_abstract_preserves_genuine_sentences() -> None:
    # "Abstract" as a real first word (lowercase continuation) must be kept.
    assert clean_abstract("Abstract concepts are hard to measure") == (
        "Abstract concepts are hard to measure"
    )
    # A word that merely starts with "abstract" is untouched.
    assert clean_abstract("Abstracts due September 1") == "Abstracts due September 1"


def test_truncate_breaks_on_word_boundary() -> None:
    text = "one two three four five"
    truncated = _truncate(text, 12)
    assert truncated == "one two…"
    assert "thr" not in truncated  # no mid-word cut


def test_truncate_returns_short_text_unchanged() -> None:
    assert _truncate("short", 100) == "short"
