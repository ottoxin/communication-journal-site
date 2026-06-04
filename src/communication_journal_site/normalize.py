from __future__ import annotations

import hashlib
import html
import re
from datetime import date
from typing import Iterable
from urllib.parse import urlsplit, urlunsplit


def normalize_whitespace(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "item"


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.strip().lower()
    cleaned = re.sub(r"^https?://(dx\.)?doi\.org/", "", cleaned)
    cleaned = cleaned.removeprefix("doi:")
    return cleaned.strip() or None


def canonicalize_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlsplit(value.strip())
    if not parsed.scheme or not parsed.netloc:
        return value.strip()
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/") or parsed.path
    return urlunsplit((parsed.scheme.lower(), netloc, path, "", ""))


# Strips a leading "Abstract" heading left over from JATS/Crossref markup, but only
# when the next character is uppercase/digit (the real abstract body) so genuine
# sentences such as "Abstract concepts are..." are preserved.
_ABSTRACT_LABEL_RE = re.compile(r"^(?i:abstract)\b[\s:.—–-]*(?=[A-Z0-9])")


def clean_abstract(value: str | None) -> str | None:
    cleaned = clean_markup_text(value)
    if not cleaned:
        return None
    return _ABSTRACT_LABEL_RE.sub("", cleaned, count=1) or cleaned


def clean_markup_text(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = html.unescape(value)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = html.unescape(cleaned)
    return normalize_whitespace(cleaned) or None


def date_from_parts(parts: object) -> str | None:
    if not isinstance(parts, list) or not parts:
        return None
    first = parts[0]
    if not isinstance(first, list) or not first:
        return None
    year = int(first[0])
    month = int(first[1]) if len(first) > 1 else 1
    day = int(first[2]) if len(first) > 2 else 1
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def make_dedupe_key(
    doi: str | None,
    canonical_url: str | None,
    journal_title: str,
    title: str,
    published_date: str,
) -> str:
    normalized_doi = normalize_doi(doi)
    if normalized_doi:
        return f"doi:{normalized_doi}"
    if canonical_url:
        return f"url:{canonicalize_url(canonical_url)}"
    fallback = "|".join(
        [
            normalize_whitespace(journal_title).lower(),
            normalize_whitespace(title).lower(),
            published_date,
        ]
    )
    return f"title:{sha256_text(fallback)}"


def compact_list(values: Iterable[str | None]) -> list[str]:
    seen: set[str] = set()
    compacted: list[str] = []
    for value in values:
        cleaned = normalize_whitespace(value)
        if cleaned and cleaned.lower() not in seen:
            seen.add(cleaned.lower())
            compacted.append(cleaned)
    return compacted
