from __future__ import annotations

import html
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

import yaml

from .normalize import slugify

WIKIDATA_SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = "communication-journal-site/0.1 (haohangxin565@gmail.com)"

# Wikidata recommends keeping VALUES blocks small; we chunk ISSNs to stay well
# within URL/length limits for the GET request.
ISSN_CHUNK_SIZE = 50

# Image extensions we recognise for both cached downloads and asset reuse.
_IMAGE_EXTENSIONS = ("jpg", "jpeg", "png", "webp", "svg", "gif")

# Refuse unexpectedly large responses before writing them to the persistent
# cache. Journal covers are normally well below this limit.
MAX_IMAGE_BYTES = 20 * 1024 * 1024

# Maps response Content-Type to a file extension when the URL path lacks one.
_CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/svg+xml": "svg",
    "image/gif": "gif",
}


@dataclass(frozen=True)
class CoverSource:
    """A curated direct image URL and its human-verifiable source page."""

    image_url: str
    source_page: str | None = None


@dataclass
class CoverCollectionResult:
    """Summary of one cover collection pass."""

    covers: dict[str, str]
    imported: list[str] = field(default_factory=list)
    downloaded: list[str] = field(default_factory=list)
    wikidata: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)


def load_cover_sources(path) -> dict[str, CoverSource]:
    """Load curated cover URLs from a YAML manifest.

    The manifest accepts either a URL string or an object with ``image_url``
    and optional ``source_page`` for each journal id::

        covers:
          journal-of-communication:
            image_url: https://example.org/cover.jpg
            source_page: https://example.org/journal
    """
    source_path = Path(path)
    if not source_path.exists():
        return {}
    with source_path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"{source_path} must contain a YAML object.")
    raw_sources = payload.get("covers", payload)
    if not isinstance(raw_sources, dict):
        raise ValueError(f"{source_path}: covers must be a YAML object.")

    sources: dict[str, CoverSource] = {}
    for journal_id, raw_source in raw_sources.items():
        if not raw_source:
            continue
        if isinstance(raw_source, str):
            image_url = raw_source
            source_page = None
        elif isinstance(raw_source, dict):
            image_url = raw_source.get("image_url") or raw_source.get("url")
            source_page = raw_source.get("source_page")
        else:
            raise ValueError(f"{source_path}: invalid cover source for {journal_id}.")
        if not isinstance(image_url, str) or not image_url.startswith(("http://", "https://")):
            raise ValueError(f"{source_path}: {journal_id} needs an http(s) image_url.")
        if source_page is not None and not isinstance(source_page, str):
            raise ValueError(f"{source_path}: {journal_id} source_page must be a string.")
        sources[str(journal_id)] = CoverSource(image_url=image_url, source_page=source_page)
    return sources


def collect_covers(
    journals: list,
    cache_dir,
    *,
    sources: dict[str, CoverSource] | None = None,
    import_dir=None,
) -> CoverCollectionResult:
    """Collect and audit real cover assets using a deterministic source order.

    Existing cached files win, followed by local bulk imports, curated direct
    URLs, and finally Wikidata. ``import_dir`` files may be named after either
    the configured journal id or the title slug. The returned ``missing`` list
    makes complete coverage enforceable by callers.
    """
    cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)
    result = CoverCollectionResult(covers={})

    if import_dir is not None:
        _import_local_covers(journals, Path(import_dir), cache_path, result)

    configured_sources = sources or {}
    for journal in journals:
        slug = slugify(journal.title)
        if _find_cached_cover(cache_path, slug) is not None:
            continue
        source = configured_sources.get(journal.id) or configured_sources.get(slug)
        if source is None:
            continue
        try:
            filename = _download_image(
                source.image_url,
                cache_path,
                slug,
                referer=source.source_page,
            )
        except Exception as exc:
            result.errors[journal.id] = str(exc)
            continue
        if filename:
            result.downloaded.append(journal.id)

    before_wikidata = _cover_ids(journals, cache_path)
    missing_journals = [journal for journal in journals if journal.id not in before_wikidata]
    if missing_journals:
        fetch_wikidata_covers(missing_journals, cache_path)
    after_wikidata = _cover_ids(journals, cache_path)
    result.wikidata = sorted(after_wikidata - before_wikidata)

    for journal in journals:
        slug = slugify(journal.title)
        cached = _find_cached_cover(cache_path, slug)
        if cached is None:
            result.missing.append(journal.id)
        else:
            result.covers[slug] = cached.name
    return result


def fetch_wikidata_covers(journals: list, cache_dir) -> dict[str, str]:
    """Download Wikidata (P18) cover images for journals into ``cache_dir``.

    Journals are matched by ISSN (P236). For each journal with an image, the
    binary is saved to ``cache_dir/<slug>.<ext>`` where ``slug`` is derived from
    the journal title. Existing cached files are left untouched. Returns a
    mapping ``{slug: filename}`` for every journal whose cover is now cached.

    Network failures for individual downloads are swallowed so one bad image
    never aborts the run; missing images are normal (~25% coverage).
    """
    cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)

    # Build an ISSN -> [journals] index so a single query result can satisfy
    # every journal that lists that ISSN.
    issn_to_journals: dict[str, list] = {}
    ordered_issns: list[str] = []
    for journal in journals:
        for issn in getattr(journal, "issns", []) or []:
            issn = (issn or "").strip()
            if not issn:
                continue
            if issn not in issn_to_journals:
                issn_to_journals[issn] = []
                ordered_issns.append(issn)
            issn_to_journals[issn].append(journal)

    # Query Wikidata for the ISSN -> image-URL mapping in chunks.
    issn_to_image: dict[str, str] = {}
    for chunk in _chunked(ordered_issns, ISSN_CHUNK_SIZE):
        try:
            results = _query_wikidata_images(chunk)
        except (HTTPError, URLError, TimeoutError, ValueError, OSError):
            # A failed batch should not abort the whole run.
            continue
        for issn, image_url in results.items():
            issn_to_image.setdefault(issn, image_url)

    cached: dict[str, str] = {}
    for journal in journals:
        slug = slugify(journal.title)
        if slug in cached:
            continue
        existing = _find_cached_cover(cache_path, slug)
        if existing is not None:
            cached[slug] = existing.name
            continue
        # Try each ISSN until one yields a downloadable image.
        for issn in getattr(journal, "issns", []) or []:
            image_url = issn_to_image.get((issn or "").strip())
            if not image_url:
                continue
            try:
                filename = _download_image(image_url, cache_path, slug)
            except Exception:
                # Skip this image; try the journal's next ISSN.
                continue
            if filename:
                cached[slug] = filename
                break
    return cached


def generated_cover_svg(journal) -> str:
    """Return a deterministic placeholder cover SVG for ``journal``.

    The output is a pure function of ``journal.title`` (no network, no
    randomness), styled to match the site's Editorial theme.
    """
    title = journal.title or ""
    lines = _wrap_title(title, width=14, max_lines=6)
    escaped_lines = [html.escape(line) for line in lines]

    # Vertically centre the title block within the cover.
    line_height = 26
    block_height = line_height * len(escaped_lines)
    start_y = 200 - block_height / 2 + 20

    tspans = []
    for index, line in enumerate(escaped_lines):
        y = start_y + index * line_height
        tspans.append(f'<tspan x="150" y="{_fmt(y)}">{line}</tspan>')
    title_tspans = "\n      ".join(tspans)

    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 400" '
        'width="300" height="400" role="img">\n'
        '  <rect x="0" y="0" width="300" height="400" fill="#faf6ef"/>\n'
        '  <rect x="12" y="12" width="276" height="376" fill="none" '
        'stroke="#e6ded1" stroke-width="1"/>\n'
        '  <text x="150" y="60" text-anchor="middle" '
        "font-family=\"Georgia, 'Times New Roman', serif\" font-size=\"14\" "
        'font-style="italic" letter-spacing="3" fill="#8a2b2b">JOURNAL</text>\n'
        '  <text text-anchor="middle" '
        "font-family=\"Georgia, 'Times New Roman', serif\" font-size=\"20\" "
        'fill="#1b1714">\n'
        f"      {title_tspans}\n"
        "  </text>\n"
        "</svg>\n"
    )


def ensure_cover_asset(journal, cache_dir, assets_dir) -> str:
    """Materialise a cover asset for ``journal`` under ``assets_dir/covers``.

    If a real cached cover exists in ``cache_dir`` it is copied into the assets
    directory and its site-root-relative path is returned. Otherwise a
    deterministic placeholder SVG is written. Network-free and safe to call on
    every build.
    """
    slug = slugify(journal.title)
    covers_dir = Path(assets_dir) / "covers"
    covers_dir.mkdir(parents=True, exist_ok=True)

    cached = _find_cached_cover(Path(cache_dir), slug)
    if cached is not None:
        ext = cached.suffix.lstrip(".").lower()
        destination = covers_dir / f"{slug}.{ext}"
        shutil.copyfile(cached, destination)
        return f"assets/covers/{slug}.{ext}"

    destination = covers_dir / f"{slug}.svg"
    destination.write_text(generated_cover_svg(journal), encoding="utf-8")
    return f"assets/covers/{slug}.svg"


def _query_wikidata_images(issns: list[str]) -> dict[str, str]:
    """Return ``{issn: image_url}`` for the given ISSNs via a SPARQL query."""
    values = " ".join(f'"{issn}"' for issn in issns)
    query = (
        "SELECT ?issn ?image WHERE { "
        f"VALUES ?issn {{ {values} }} "
        "?item wdt:P236 ?issn. ?item wdt:P18 ?image. }"
    )
    url = f"{WIKIDATA_SPARQL_ENDPOINT}?{urlencode({'query': query, 'format': 'json'})}"
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/sparql-results+json",
        },
    )
    with urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))

    mapping: dict[str, str] = {}
    for binding in payload.get("results", {}).get("bindings", []):
        issn = binding.get("issn", {}).get("value")
        image = binding.get("image", {}).get("value")
        if issn and image:
            mapping.setdefault(issn, image)
    return mapping


def _download_image(
    image_url: str,
    cache_path: Path,
    slug: str,
    *,
    referer: str | None = None,
) -> str | None:
    """Download ``image_url`` into ``cache_path`` and return the filename."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "image/webp,image/png,image/svg+xml,image/*;q=0.8,*/*;q=0.5",
    }
    if referer:
        headers["Referer"] = referer
    request = Request(image_url, headers=headers)
    with urlopen(request, timeout=30) as response:
        data = response.read(MAX_IMAGE_BYTES + 1)
        content_type = response.headers.get("content-type", "")
        final_url = response.geturl()
    if not data:
        return None
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError(f"Cover image exceeds {MAX_IMAGE_BYTES} bytes.")
    ext = _validated_extension(data, image_url, final_url, content_type)
    filename = f"{slug}.{ext}"
    _write_atomically(cache_path / filename, data)
    return filename


def _import_local_covers(
    journals: list,
    import_path: Path,
    cache_path: Path,
    result: CoverCollectionResult,
) -> None:
    """Import journal-id/title-slug image files into the persistent cache."""
    if not import_path.exists():
        raise ValueError(f"Cover import directory does not exist: {import_path}")
    if not import_path.is_dir():
        raise ValueError(f"Cover import path is not a directory: {import_path}")

    for journal in journals:
        slug = slugify(journal.title)
        if _find_cached_cover(cache_path, slug) is not None:
            continue
        source = _find_import_cover(import_path, journal.id, slug)
        if source is None:
            continue
        try:
            data = source.read_bytes()
            if len(data) > MAX_IMAGE_BYTES:
                raise ValueError(f"Cover image exceeds {MAX_IMAGE_BYTES} bytes.")
            ext = _validated_extension(data, str(source), "", "")
            _write_atomically(cache_path / f"{slug}.{ext}", data)
        except Exception as exc:
            result.errors[journal.id] = f"{source}: {exc}"
            continue
        result.imported.append(journal.id)


def _find_import_cover(import_path: Path, journal_id: str, slug: str) -> Path | None:
    for basename in dict.fromkeys((journal_id, slug)):
        for ext in _IMAGE_EXTENSIONS:
            candidate = import_path / f"{basename}.{ext}"
            if candidate.is_file():
                return candidate
    return None


def _cover_ids(journals: list, cache_path: Path) -> set[str]:
    return {
        journal.id
        for journal in journals
        if _find_cached_cover(cache_path, slugify(journal.title)) is not None
    }


def _validated_extension(data: bytes, *urls_and_type: str) -> str:
    """Validate image bytes and return their canonical file extension."""
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    prefix = data[:1024].lstrip()
    if prefix.startswith(b"<?xml"):
        prefix = prefix.split(b"?>", 1)[-1].lstrip()
    if prefix.startswith(b"<svg"):
        return "svg"
    hint = _extension_for(*urls_and_type)
    raise ValueError(f"Response is not a supported image (hint: {hint}).")


def _write_atomically(destination: Path, data: bytes) -> None:
    temporary = destination.with_name(f".{destination.name}.tmp")
    temporary.write_bytes(data)
    temporary.replace(destination)


def _extension_for(*urls_and_type: str) -> str:
    """Derive an image extension from URL paths, falling back to Content-Type.

    Earlier positional arguments are treated as URLs; the final argument is the
    response Content-Type. Defaults to ``jpg`` when nothing matches.
    """
    *urls, content_type = urls_and_type
    for url in urls:
        if not url:
            continue
        suffix = Path(urlsplit(url).path).suffix.lstrip(".").lower()
        if suffix in _IMAGE_EXTENSIONS:
            return suffix
    mapped = _CONTENT_TYPE_EXTENSIONS.get((content_type or "").split(";")[0].strip().lower())
    if mapped:
        return mapped
    return "jpg"


def _find_cached_cover(cache_path: Path, slug: str) -> Path | None:
    """Return an existing cached cover file for ``slug`` if one exists."""
    if not cache_path.exists():
        return None
    for ext in _IMAGE_EXTENSIONS:
        candidate = cache_path / f"{slug}.{ext}"
        if candidate.exists():
            return candidate
    return None


def _wrap_title(title: str, width: int, max_lines: int) -> list[str]:
    """Word-wrap ``title`` to ~``width`` chars/line, capped at ``max_lines``.

    Words longer than ``width`` are hard-split. When the title overflows
    ``max_lines`` the final line is truncated with an ellipsis.
    """
    words = title.split()
    if not words:
        return [""]

    lines: list[str] = []
    current = ""
    for word in words:
        # Hard-split words that cannot fit on a single line.
        while len(word) > width:
            if current:
                lines.append(current)
                current = ""
            lines.append(word[:width])
            word = word[width:]
        candidate = f"{current} {word}".strip()
        if len(candidate) <= width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)

    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        if len(last) >= width:
            last = last[: max(0, width - 1)]
        lines[-1] = f"{last.rstrip()}…"
    return lines


def _chunked(items: list[str], size: int):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _fmt(value: float) -> str:
    """Format a coordinate compactly (drop a trailing ``.0``)."""
    if value == int(value):
        return str(int(value))
    return f"{value:.1f}"
