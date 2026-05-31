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


class HttpClient:
    def __init__(
        self,
        timeout: int = 30,
        user_agent: str = "communication-journal-site/0.1",
        max_attempts: int = 3,
    ):
        self.timeout = timeout
        self.user_agent = user_agent
        self.max_attempts = max_attempts

    def get_text(self, url: str) -> HttpResponse:
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                request = Request(url, headers={"User-Agent": self.user_agent})
                with urlopen(request, timeout=self.timeout) as response:
                    raw = response.read()
                    content_type = response.headers.get("content-type", "")
                    charset = response.headers.get_content_charset() or "utf-8"
                    return HttpResponse(
                        url=response.geturl(),
                        body=raw.decode(charset, errors="replace"),
                        content_type=content_type,
                    )
            except (HTTPError, URLError, TimeoutError) as exc:
                last_error = exc
                if attempt >= self.max_attempts:
                    break
                time.sleep(min(2 ** (attempt - 1), 8))
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

