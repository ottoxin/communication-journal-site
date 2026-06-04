from __future__ import annotations

from communication_journal_site.models import JournalConfig, SpecialIssueSourceConfig
from communication_journal_site.special_issues import parse_special_issues_from_html


def test_parse_special_issue_candidates_with_deadline() -> None:
    html = """
    <html><body>
      <h2>Call for Papers: Special Issue on Platform Governance</h2>
      <p>Submissions due: September 15, 2026. This special issue welcomes papers.</p>
    </body></html>
    """
    source = SpecialIssueSourceConfig(
        id="test-source",
        journal_id="test-journal",
        label="Test source",
        url="https://example.org/calls",
    )
    journal = JournalConfig(id="test-journal", title="Test Journal", issns=["0000-0000"])
    records = parse_special_issues_from_html(html, source, journal, "https://example.org/calls")
    assert len(records) == 1
    assert records[0].title == "Special Issue on Platform Governance"
    assert records[0].deadline == "2026-09-15"
    assert records[0].confidence == "high"


def test_parse_special_issue_skips_generic_submission_page() -> None:
    html = """
    <html><body>
      <a href="/call-for-submissions/">Call for Submissions</a>
      <p>Submit your manuscript to the journal.</p>
    </body></html>
    """
    source = SpecialIssueSourceConfig(
        id="test-source",
        journal_id="test-journal",
        label="Test source",
        url="https://example.org/calls",
    )
    journal = JournalConfig(id="test-journal", title="Test Journal", issns=["0000-0000"])
    records = parse_special_issues_from_html(html, source, journal, "https://example.org/calls")
    assert records == []
