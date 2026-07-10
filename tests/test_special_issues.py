from __future__ import annotations

from communication_journal_site.models import (
    JournalConfig,
    SpecialIssueRecord,
    SpecialIssueSourceConfig,
)
from communication_journal_site.http_client import HttpResponse
from communication_journal_site.special_issues import (
    SpecialIssueCollector,
    _is_communication_relevant,
    is_plausible_special_issue_title,
    parse_special_issues_from_feed,
    parse_special_issues_from_html,
)


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


def test_parse_special_issue_rejects_page_prose_and_adjacent_journal_name() -> None:
    html = """
    <html><body>
      <h1>Journal of Communication</h1>
      <p>This Special Issue aims to propel time into the forefront of communication research.</p>
      <h2>Special Issue Call for Papers: Time in Communication Research</h2>
      <p>Deadline: October 15, 2026</p>
    </body></html>
    """
    source = SpecialIssueSourceConfig(
        id="test-source",
        journal_id="test-journal",
        label="Test source",
        url="https://example.org/calls",
    )
    journal = JournalConfig(id="test-journal", title="Test Journal", issns=["0000-0000"])

    records = parse_special_issues_from_html(html, source, journal, source.url)

    assert [record.title for record in records] == [
        "Special Issue Call for Papers: Time in Communication Research"
    ]
    assert records[0].deadline == "2026-10-15"


def test_parse_special_issues_from_feed_keeps_special_issue_drops_conference() -> None:
    feed = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel>
      <title>Test CFP feed</title>
      <item>
        <title>Special Issue on Misinformation and Trust</title>
        <link>https://example.org/cfp/misinfo</link>
        <description>Call for papers for a special issue. Submission deadline: March 1, 2027.</description>
      </item>
      <item>
        <title>ICA 2027: Annual Conference Call for Papers</title>
        <link>https://example.org/ica</link>
        <description>The annual conference welcomes submissions. Deadline November 1, 2026.</description>
      </item>
    </channel></rss>"""
    source = SpecialIssueSourceConfig(
        id="feed-source",
        journal_id="aggregator-communication",
        label="Aggregator feed",
        url="https://example.org/feed",
        source_type="rss",
    )
    journal = JournalConfig(id="aggregator-communication", title="Aggregator feed", issns=[])
    records = parse_special_issues_from_feed(feed, source, journal, "https://example.org/feed")
    assert len(records) == 1
    assert records[0].title == "Special Issue on Misinformation and Trust"
    assert records[0].source_url == "https://example.org/cfp/misinfo"
    assert records[0].deadline == "2027-03-01"
    assert records[0].confidence == "high"
    assert records[0].status == "active"


def test_communication_relevance_filter_keeps_comm_drops_offtopic() -> None:
    registry_titles = {"communication research", "journalism studies"}
    comm = SpecialIssueRecord(
        source_id="sage-hub", journal_id="aggregator-sage", journal_title="SAGE",
        title="Special Issue: Platforms and Democracy",
        source_url="https://example.org/a",
        raw_snippet="New Media & Society special issue on media and democracy",
    )
    offtopic = SpecialIssueRecord(
        source_id="sage-hub", journal_id="aggregator-sage", journal_title="SAGE",
        title="Special Issue: Hotel Revenue Management",
        source_url="https://example.org/b",
        raw_snippet="Tourism Economics special issue on hotel pricing",
    )
    assert _is_communication_relevant(comm, registry_titles) is True
    assert _is_communication_relevant(offtopic, registry_titles) is False


def test_stored_title_validation_rejects_legacy_page_fragments() -> None:
    assert is_plausible_special_issue_title("Special Issue Call for Papers: Time and Communication")
    assert not is_plausible_special_issue_title("Journal of Communication")
    assert not is_plausible_special_issue_title(
        "This Special Issue aims to propel time into the forefront of communication research."
    )
    assert not is_plausible_special_issue_title("https://mc.manuscriptcentral.com/jcom")


def test_collector_tracks_successes_without_dropping_other_source_errors() -> None:
    class FakeHttpClient:
        def get_text(self, url: str) -> HttpResponse:
            if url.endswith("/broken"):
                raise RuntimeError("publisher unavailable")
            return HttpResponse(
                url=url,
                body="<h2>Special Issue on Platform Governance</h2>",
            )

    journal = JournalConfig(id="test-journal", title="Test Journal", issns=["0000-0000"])
    sources = [
        SpecialIssueSourceConfig(
            id="working",
            journal_id=journal.id,
            label="Working",
            url="https://example.org/working",
        ),
        SpecialIssueSourceConfig(
            id="broken",
            journal_id=journal.id,
            label="Broken",
            url="https://example.org/broken",
        ),
    ]
    collector = SpecialIssueCollector(http_client=FakeHttpClient())

    records, errors = collector.collect(sources, {journal.id: journal})

    assert [record.title for record in records] == ["Special Issue on Platform Governance"]
    assert collector.successful_source_ids == {"working"}
    assert errors == [{"source_id": "broken", "error": "publisher unavailable"}]
