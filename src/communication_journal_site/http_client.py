from __future__ import annotations

import json
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class HttpClientError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class HttpResponse:
    url: str
    body: str
    content_type: str = ""
    headers: dict[str, str] | None = None


class HttpClient:
    # A browser-like User-Agent + Accept headers. Many publisher sites (e.g. SAGE)
    # return 403 to a bare tool UA but serve normally to this; Crossref/OpenAlex
    # are unaffected (they key the polite pool off the mailto query param).
    default_user_agent = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )

    def __init__(
        self,
        timeout: int = 30,
        user_agent: str | None = None,
        max_attempts: int = 3,
    ):
        self.timeout = timeout
        self.user_agent = user_agent or self.default_user_agent
        self.max_attempts = max_attempts

    def get_text(self, url: str) -> HttpResponse:
        last_error: Exception | None = None
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        for attempt in range(1, self.max_attempts + 1):
            try:
                request = Request(url, headers=headers)
                with urlopen(request, timeout=self.timeout) as response:
                    raw = response.read()
                    content_type = response.headers.get("content-type", "")
                    charset = response.headers.get_content_charset() or "utf-8"
                    return HttpResponse(
                        url=response.geturl(),
                        body=raw.decode(charset, errors="replace"),
                        content_type=content_type,
                        headers={key.lower(): value for key, value in response.headers.items()},
                    )
            except (HTTPError, URLError, TimeoutError) as exc:
                last_error = exc
                if attempt >= self.max_attempts:
                    break
                retry_after = 0.0
                if isinstance(exc, HTTPError) and exc.code == 429:
                    try:
                        retry_after = float(exc.headers.get("Retry-After", "0"))
                    except (TypeError, ValueError):
                        retry_after = 0.0
                time.sleep(max(min(2 ** (attempt - 1), 8), min(retry_after, 30)))
        raise HttpClientError(f"Could not fetch {url}: {last_error}")

    def get_json(self, url: str) -> dict:
        response = self.get_text(url)
        try:
            data = json.loads(response.body)
        except json.JSONDecodeError as exc:
            raise HttpClientError(f"Could not decode JSON from {url}: {exc}") from exc
        if not isinstance(data, dict):
            raise HttpClientError(f"JSON response from {url} was not an object.")
        return data
