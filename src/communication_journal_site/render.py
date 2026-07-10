"""Optional headless-browser fetcher for JavaScript-rendered / anti-bot pages.

Playwright is an optional dependency (see the ``render`` extra). It is imported
lazily so the core pipeline keeps working with plain HTTP when Playwright is not
installed; a rendered source then simply fails with a clear, per-source error
instead of crashing the run.
"""
from __future__ import annotations

from .http_client import HttpClient


class RenderedFetchError(RuntimeError):
    pass


class RenderedFetcher:
    def __init__(
        self,
        user_agent: str | None = None,
        timeout_ms: int = 35000,
        settle_ms: int = 3000,
    ):
        self.user_agent = user_agent or HttpClient.default_user_agent
        self.timeout_ms = timeout_ms
        self.settle_ms = settle_ms
        self._playwright = None
        self._browser = None

    def _ensure_browser(self) -> None:
        if self._browser is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - depends on optional install
            raise RenderedFetchError(
                "Playwright is required for rendered sources. Install with "
                "`pip install -e \".[render]\"` then `playwright install chromium`."
            ) from exc
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch()

    def get_text(self, url: str) -> str:
        self._ensure_browser()
        assert self._browser is not None
        context = self._browser.new_context(
            user_agent=self.user_agent,
            viewport={"width": 1280, "height": 1400},
        )
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            page.wait_for_timeout(self.settle_ms)
            return page.content()
        except Exception as exc:  # pragma: no cover - network/runtime dependent
            raise RenderedFetchError(f"Could not render {url}: {exc}") from exc
        finally:
            context.close()

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None
