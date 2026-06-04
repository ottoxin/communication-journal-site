from __future__ import annotations

from datetime import date

from communication_journal_site.crossref import CrossrefClient
from communication_journal_site.models import JournalConfig


class FakeHttpClient:
    def get_json(self, url: str) -> dict:
        return {
            "message": {
                "items": [
                    {
                        "title": ["&lt;i&gt;A test article&lt;/i&gt;"],
                        "published-online": {"date-parts": [[2026, 5, 30]]},
                        "type": "journal-article",
                        "DOI": "10.1234/ABC",
                        "URL": "https://doi.org/10.1234/ABC",
                        "abstract": "<jats:p>Crossref abstract.</jats:p>",
                        "author": [
                            {
                                "given": "Ada",
                                "family": "Lovelace",
                                "affiliation": [{"name": "Analytical University"}],
                            }
                        ],
                        "subject": ["Communication"],
                        "volume": "12",
                        "issue": "3",
                        "page": "1-20",
                        "publisher": "Test Publisher",
                        "ISSN": ["0000-0000"],
                        "container-title": ["Test Journal"],
                    }
                ]
            }
        }


def test_crossref_normalizes_article_metadata() -> None:
    journal = JournalConfig(
        id="test-journal",
        title="Test Journal",
        issns=["0000-0000"],
        publisher="Fallback Publisher",
    )
    records = CrossrefClient(http_client=FakeHttpClient()).fetch_journal_records(
        journal,
        date(2026, 5, 1),
        date(2026, 5, 31),
    )
    assert len(records) == 1
    record = records[0]
    assert record.title == "A test article"
    assert record.doi == "10.1234/abc"
    assert record.published_date == "2026-05-30"
    assert record.abstract == "Crossref abstract."
    assert record.authors == ["Ada Lovelace"]
    assert record.affiliations == ["Analytical University"]
    assert record.volume == "12"
    assert record.issue == "3"
    assert record.pages == "1-20"
