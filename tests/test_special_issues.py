from __future__ import annotations

from datetime import date

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
    special_issue_opportunity_type,
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


def test_parse_topic_heading_with_multiblock_call_context_and_link() -> None:
    html = """
    <h2>Platform Power and Climate</h2>
    <p>We invite submissions to a special issue.</p>
    <p>This call for papers welcomes communication research.</p>
    <p>Submit by 31st August 2026.</p>
    <a href="/submit/platform-power">Submission details</a>
    """
    source = SpecialIssueSourceConfig(
        id="test-source", journal_id="test-journal", label="Test source",
        url="https://example.org/calls",
    )
    journal = JournalConfig(id="test-journal", title="Test Journal", issns=["0000-0000"])

    records = parse_special_issues_from_html(
        html, source, journal, source.url, today=date(2026, 7, 13)
    )

    assert len(records) == 1
    assert records[0].title == "Platform Power and Climate"
    assert records[0].deadline == "2026-08-31"
    assert records[0].source_url == "https://example.org/submit/platform-power"
    assert records[0].status == "active"


def test_deadline_status_is_deterministic() -> None:
    html = """
    <h2>Special Issue: Trust and News</h2>
    <p>Call for papers. Deadline: June 30 2026.</p>
    """
    source = SpecialIssueSourceConfig(
        id="test-source", journal_id="test-journal", label="Test source",
        url="https://example.org/calls",
    )
    journal = JournalConfig(id="test-journal", title="Test Journal", issns=["0000-0000"])

    records = parse_special_issues_from_html(
        html, source, journal, source.url, today=date(2026, 7, 13)
    )

    assert records[0].deadline == "2026-06-30"
    assert records[0].status == "closed"


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
    assert collector.authoritative_source_ids == {"working"}
    assert errors == [{"source_id": "broken", "error": "publisher unavailable"}]


def test_collector_does_not_treat_empty_or_blocked_pages_as_authoritative() -> None:
    class FakeHttpClient:
        def get_text(self, url: str) -> HttpResponse:
            if url.endswith("/blocked"):
                return HttpResponse(url=url, body="<title>Attention Required! | Cloudflare</title>")
            return HttpResponse(url=url, body="<h1>Journal homepage</h1>")

    journal = JournalConfig(id="test-journal", title="Test Journal", issns=["0000-0000"])
    sources = [
        SpecialIssueSourceConfig(
            id="empty", journal_id=journal.id, label="Empty", url="https://example.org/empty"
        ),
        SpecialIssueSourceConfig(
            id="blocked", journal_id=journal.id, label="Blocked", url="https://example.org/blocked"
        ),
    ]
    collector = SpecialIssueCollector(http_client=FakeHttpClient())

    records, errors = collector.collect(sources, {journal.id: journal})

    assert records == []
    assert collector.successful_source_ids == {"empty"}
    assert collector.authoritative_source_ids == set()
    assert errors == [{
        "source_id": "blocked",
        "error": "publisher returned an access or bot-challenge page",
    }]


def test_manual_verified_source_is_collected_without_http() -> None:
    class NoHttpClient:
        def get_text(self, url: str) -> HttpResponse:
            raise AssertionError("manual sources must not make an HTTP request")

    journal = JournalConfig(id="hcr", title="Human Communication Research", issns=["0000-0000"])
    source = SpecialIssueSourceConfig(
        id="hcr-2026", journal_id=journal.id, label="HCR 2026",
        url="https://example.org/hcr-2026", source_type="manual",
        title="Special Issue: Group and Network Processes in Communication",
        deadline="2026-08-31",
        verified_on="2026-07-13", review_after="2026-08-31",
    )
    collector = SpecialIssueCollector(http_client=NoHttpClient())

    records, errors = collector.collect([source], {journal.id: journal}, today=date(2026, 7, 13))

    assert errors == []
    assert len(records) == 1
    assert records[0].status == "active"
    assert records[0].deadline == "2026-08-31"
    assert collector.authoritative_source_ids == {"hcr-2026"}


def test_manual_source_expires_when_its_review_date_passes() -> None:
    journal = JournalConfig(id="hcr", title="Human Communication Research", issns=["0000-0000"])
    source = SpecialIssueSourceConfig(
        id="manual", journal_id=journal.id, label="Manual", url="https://example.org/call",
        source_type="manual", title="Special Issue: Audiences", verified_on="2026-07-01",
        review_after="2026-07-10",
    )

    records, errors = SpecialIssueCollector().collect(
        [source], {journal.id: journal}, today=date(2026, 7, 13)
    )

    assert errors == []
    assert records[0].status == "unverified"


def test_special_issue_proposal_is_classified_separately() -> None:
    assert special_issue_opportunity_type("Call for Special Issue Proposals") == "special-issue-proposal"
    assert special_issue_opportunity_type("Special Issue: Audiences") == "call-for-papers"
