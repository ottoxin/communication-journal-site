from __future__ import annotations

from urllib.parse import quote

from .http_client import HttpClient
from .models import ArticleRecord
from .normalize import clean_abstract


class OpenAlexClient:
    api_url = "https://api.openalex.org/works"

    def __init__(self, http_client: HttpClient | None = None, mailto: str | None = None):
        self.http_client = http_client or HttpClient()
        self.mailto = mailto

    def abstract_for_doi(self, doi: str) -> str | None:
        params = f"https://doi.org/{quote(doi)}"
        url = f"{self.api_url}/{quote(params, safe='')}"
        if self.mailto:
            url = f"{url}?mailto={quote(self.mailto)}"
        payload = self.http_client.get_json(url)
        inverted = payload.get("abstract_inverted_index")
        if not isinstance(inverted, dict):
            return None
        positions: list[tuple[int, str]] = []
        for word, indices in inverted.items():
            if not isinstance(indices, list):
                continue
            for index in indices:
                if isinstance(index, int):
                    positions.append((index, word))
        if not positions:
            return None
        words = [word for _, word in sorted(positions)]
        return clean_abstract(" ".join(words))


class MetadataEnricher:
    def __init__(self, openalex: OpenAlexClient | None = None):
        self.openalex = openalex or OpenAlexClient()

    def enrich_records(self, records: list[ArticleRecord]) -> list[ArticleRecord]:
        enriched: list[ArticleRecord] = []
        for record in records:
            if record.abstract or not record.doi:
                enriched.append(record)
                continue
            try:
                abstract = self.openalex.abstract_for_doi(record.doi)
            except Exception:
                abstract = None
            if abstract:
                record.abstract = abstract
                record.provenance["abstract_source"] = "openalex"
            enriched.append(record)
        return enriched

