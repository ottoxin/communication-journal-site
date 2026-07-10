from __future__ import annotations

from .models import ArticleRecord
from .normalize import clean_abstract


PUBLIC_ARTICLE_POLICY = "requires_clean_abstract"


def public_abstract(article: ArticleRecord) -> str | None:
    """Return the cleaned abstract that makes an article publishable."""

    return clean_abstract(article.abstract)


def is_public_article(article: ArticleRecord) -> bool:
    """Return whether an article is eligible for public HTML/JSON output."""

    return public_abstract(article) is not None


def public_articles(articles: list[ArticleRecord]) -> list[ArticleRecord]:
    """Filter locally stored articles down to public web/export records."""

    return [article for article in articles if is_public_article(article)]


def local_only_article_count(articles: list[ArticleRecord]) -> int:
    """Count locally retained records hidden from public outputs."""

    return len(articles) - len(public_articles(articles))
