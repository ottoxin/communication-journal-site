from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from html import escape
from pathlib import Path
from shutil import copyfile

from .covers import ensure_cover_asset
from .health import collection_health, local_today, read_last_run
from .models import AppConfig, ArticleRecord, JournalConfig, SpecialIssueRecord, SubareaConfig
from .normalize import slugify
from .publication import (
    PUBLIC_ARTICLE_POLICY,
    local_only_article_count,
    public_abstract,
    public_articles,
)
from .storage import StateStore


BUILD_MANIFEST = ".build-manifest.json"
ASSET_VERSION = "20260710-publish"


def build_site(config: AppConfig, store: StateStore, output_dir: str | Path) -> list[Path]:
    output_path = Path(output_dir)
    _clean_previous_build(output_path)
    output_path.mkdir(parents=True, exist_ok=True)
    assets_dir = output_path / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    all_articles = store.get_all_articles()
    articles = public_articles(all_articles)
    hidden_article_count = local_only_article_count(all_articles)
    today = local_today(config.settings.timezone)
    special_issues = store.get_special_issues(
        verification_days=config.settings.special_issue_verification_days,
        today=today,
    )
    journal_by_id = config.journal_by_id
    subarea_by_id = config.subarea_by_id
    health = collection_health(
        articles,
        special_issues,
        config.settings.freshness_warning_days,
        today=today,
    )
    health["local_only_article_count"] = hidden_article_count
    health["public_article_policy"] = PUBLIC_ARTICLE_POLICY
    last_run = read_last_run(store.db_path.parent)
    cover_cache = store.db_path.parent / "covers"
    covers = {
        journal.id: ensure_cover_asset(journal, cover_cache, assets_dir)
        for journal in config.journals
    }
    special_issues_page = _special_issues_page(config, special_issues)
    social_source = Path(__file__).resolve().parents[2] / "assets" / "og.png"
    written = [
        _write(output_path / "assets" / "styles.css", _styles()),
        _write(output_path / "assets" / "search.js", _search_js()),
        _write(output_path / "index.html", _home_page(config, articles, special_issues, journal_by_id, subarea_by_id, covers, health)),
        _write(output_path / "journals" / "index.html", _journals_page(config, articles, covers)),
        _write(output_path / "special-issues" / "index.html", special_issues_page),
        _write(output_path / "calls" / "index.html", special_issues_page),
        _write(output_path / "status" / "index.html", _status_page(config, health, last_run)),
    ]
    if social_source.exists():
        written.append(_copy(social_source, output_path / "assets" / "og.png"))
    for subarea in config.subareas:
        subarea_articles = [
            article
            for article in articles
            if subarea.id in journal_by_id[article.journal_id].subareas
        ]
        written.append(
            _write(
                output_path / "subareas" / subarea.id / "index.html",
                _subarea_page(config, subarea, subarea_articles, journal_by_id),
            )
        )
    for journal in config.journals:
        journal_articles = [article for article in articles if article.journal_id == journal.id]
        written.append(
            _write(
                output_path / "journals" / slugify(journal.title) / "index.html",
                _journal_page(config, journal, journal_articles, subarea_by_id, covers.get(journal.id, "")),
            )
        )
    written.extend(output_path / relative for relative in sorted(set(covers.values())))
    written.append(_write_build_manifest(output_path, written))
    return written


def _home_page(
    config: AppConfig,
    articles: list[ArticleRecord],
    special_issues: list[SpecialIssueRecord],
    journal_by_id: dict[str, JournalConfig],
    subarea_by_id: dict[str, SubareaConfig],
    covers: dict[str, str],
    health: dict,
) -> str:
    all_active_special = [issue for issue in special_issues if issue.status == "active"]
    active_special = all_active_special[:6]
    last_updated = (max((a.last_seen_at for a in articles if a.last_seen_at), default="")[:10]) or "—"
    by_journal: dict[str, list[ArticleRecord]] = defaultdict(list)
    for article in articles:
        by_journal[article.journal_id].append(article)
    subarea_counts = {
        subarea.id: sum(1 for article in articles if subarea.id in journal_by_id[article.journal_id].subareas)
        for subarea in config.subareas
    }
    featured_subareas = [
        subarea_by_id[subarea_id]
        for subarea_id in config.settings.featured_subareas
        if subarea_id in subarea_by_id
    ]
    groups: list[str] = []
    for subarea in config.subareas:
        subarea_journals = [
            journal
            for journal in config.journals
            if subarea.id in journal.subareas and by_journal.get(journal.id)
        ]
        if not subarea_journals:
            continue
        subarea_journals.sort(key=lambda journal: len(by_journal[journal.id]), reverse=True)
        shown = subarea_journals[:8]
        extra = len(subarea_journals) - len(shown)
        cards = "".join(
            _journal_cover_card(journal, by_journal[journal.id], covers.get(journal.id, ""), prefix="")
            for journal in shown
        )
        more_label = f"+{extra} more journals" if extra > 0 else f"All {escape(subarea.label)} papers"
        groups.append(f"""
    <section class="subarea-group" id="sa-{escape(subarea.id)}">
      <div class="section-heading">
        <div>
          <p class="section-kicker">Subarea</p>
          <h2><a href="subareas/{escape(subarea.id)}/index.html">{escape(subarea.label)}</a></h2>
        </div>
        <span class="muted">{subarea_counts[subarea.id]} papers</span>
      </div>
      <div class="journal-grid">{cards}</div>
      <a class="group-more" href="subareas/{escape(subarea.id)}/index.html">{more_label} &rarr;</a>
    </section>""")
    groups_html = "".join(groups) or _empty_latest_state()
    hero_links = "".join(_hero_link(subarea, prefix="") for subarea in featured_subareas)
    if active_special:
        calls_html = f'<div class="calls-row">{"".join(_special_issue_compact(issue) for issue in active_special)}</div>'
        calls_all_link = '<a href="special-issues/index.html">All calls &rarr;</a>'
    else:
        calls_all_link = ""
        calls_html = """
      <div class="calls-empty">
        <div>
          <p class="section-kicker">Nothing open right now</p>
          <p>There are no verified active calls in the latest collection.</p>
        </div>
        <a class="button-link" href="special-issues/index.html">Review monitored calls</a>
      </div>"""
    freshness = _health_banner(health)
    body = f"""
    <section class="overview-band">
      <div>
        <p class="eyebrow">Weekly communication research monitor</p>
        <h1>{escape(config.settings.title)}</h1>
        <p class="lede">{escape(config.settings.description)}</p>
        <div class="hero-actions" aria-label="Featured subareas">
          {hero_links}
        </div>
      </div>
      <div class="metrics" aria-label="Collection metrics">
        {_metric("Journals", str(len([journal for journal in config.journals if journal.active])))}
        {_metric("Latest paper", _display_date(health.get("latest_published_date")))}
        {_metric("Last sync", _display_date(last_updated))}
        {_metric("Active calls", str(len(all_active_special)))}
      </div>
    </section>
    {freshness}
    <section class="trust-strip" aria-label="How the monitor works">
      <span><strong>Curated registry</strong> across {len([journal for journal in config.journals if journal.active])} active journals</span>
      <span><strong>Abstract required</strong> for every public record</span>
      <span><strong>Direct source links</strong> to DOI or publisher pages</span>
      <span><strong>Open exports</strong> for reuse and auditing</span>
    </section>
    <section class="calls-band">
      <div class="section-heading">
        <div>
          <p class="section-kicker">Opportunities</p>
          <h2>Active special issues</h2>
        </div>
        {calls_all_link}
      </div>
      {calls_html}
    </section>
    <div class="home-toolbar">
      <div>
        <p class="section-kicker">Research directory</p>
        <h2>Browse by subarea &amp; journal</h2>
        <p class="search-status" data-search-status aria-live="polite"></p>
      </div>
      <label class="search-label"><span>Find a journal or paper</span><input class="search-input" data-search-input data-search-kind="journal card" placeholder="Try “political” or a journal title" aria-label="Filter journals"></label>
    </div>
    <div data-search-list>
      {groups_html}
    </div>
    <p class="search-empty" data-search-empty hidden>No journals or paper titles match that search.</p>
    """
    return _layout(config, "Home", body, prefix="", script=True)


def _journals_page(config: AppConfig, articles: list[ArticleRecord], covers: dict[str, str]) -> str:
    article_counts: dict[str, int] = defaultdict(int)
    latest: dict[str, str] = {}
    for article in articles:
        article_counts[article.journal_id] += 1
        latest[article.journal_id] = max(latest.get(article.journal_id, ""), article.published_date)
    groups: list[str] = []
    nav_links: list[str] = []
    for subarea in config.subareas:
        subarea_journals = [journal for journal in config.journals if subarea.id in journal.subareas]
        if not subarea_journals:
            continue
        subarea_journals.sort(key=lambda journal: (-article_counts[journal.id], journal.title))
        nav_links.append(
            f'<a href="#sa-{escape(subarea.id)}">{escape(subarea.label)} ({len(subarea_journals)})</a>'
        )
        cards = "".join(
            _journal_directory_card(
                journal, config.subarea_by_id, article_counts[journal.id],
                latest.get(journal.id), covers.get(journal.id, ""), prefix="../",
            )
            for journal in subarea_journals
        )
        groups.append(f"""
    <section class="subarea-group" id="sa-{escape(subarea.id)}">
      <div class="section-heading">
        <div>
          <p class="section-kicker">Area</p>
          <h2>{escape(subarea.label)}</h2>
        </div>
        <span class="muted">{len(subarea_journals)} journals</span>
      </div>
      <div class="journal-grid">{cards}</div>
    </section>""")
    body = f"""
    <section class="page-header">
      <p class="eyebrow">Registry</p>
      <h1>Journals by Area</h1>
      <p>{escape(config.settings.registry_source_note)}</p>
      <label class="search-label search-label--page"><span>Find a journal</span><input class="search-input" data-search-input data-search-kind="journal card" placeholder="Search by title, publisher, or area" aria-label="Filter journals"></label>
      <p class="search-status" data-search-status aria-live="polite"></p>
    </section>
    <nav class="area-nav" aria-label="Jump to area">{''.join(nav_links)}</nav>
    <div data-search-list>
      {''.join(groups)}
    </div>
    <p class="search-empty" data-search-empty hidden>No journals match that search.</p>
    """
    return _layout(config, "Journals by Area", body, prefix="../", script=True, section="journals")


def _subarea_page(
    config: AppConfig,
    subarea: SubareaConfig,
    articles: list[ArticleRecord],
    journal_by_id: dict[str, JournalConfig],
) -> str:
    shown = articles[: config.settings.article_page_limit]
    body = f"""
    <section class="page-header">
      <p class="eyebrow">Subarea</p>
      <h1>{escape(subarea.label)}</h1>
      <p>{escape(subarea.description)}</p>
      <label class="search-label search-label--page"><span>Find a paper</span><input class="search-input" data-search-input data-search-kind="paper" placeholder="Search title, author, or journal" aria-label="Filter papers"></label>
      <p class="search-status" data-search-status aria-live="polite"></p>
    </section>
    {_article_limit_notice(len(articles), len(shown))}
    <section class="article-list" data-search-list>
      {''.join(_article_card(article, journal_by_id[article.journal_id], config.subarea_by_id, prefix="../../") for article in shown) or '<p class="empty">No papers collected for this subarea yet.</p>'}
    </section>
    <p class="search-empty" data-search-empty hidden>No papers match that search.</p>
    """
    return _layout(config, subarea.label, body, prefix="../../", script=True, section=subarea.id)


def _journal_page(
    config: AppConfig,
    journal: JournalConfig,
    articles: list[ArticleRecord],
    subarea_by_id: dict[str, SubareaConfig],
    cover_path: str,
) -> str:
    subarea_labels = [subarea_by_id[item].label for item in journal.subareas if item in subarea_by_id]
    shown = articles[: config.settings.article_page_limit]
    cover_img = (
        f'<img src="{escape("../../" + cover_path)}" alt="{escape(journal.title)} cover">'
        if cover_path
        else ""
    )
    body = f"""
    <section class="page-header page-header--journal">
      <div class="journal-hero">
        <div class="journal-hero__copy">
          <p class="eyebrow">Journal</p>
          <h1>{escape(journal.title)}</h1>
          <p>{escape(journal.publisher or 'Publisher unavailable')} · ISSN {escape(', '.join(journal.issns))}</p>
          <div class="tag-row">{''.join(f'<span>{escape(label)}</span>' for label in subarea_labels)}</div>
          <div class="action-row">
            {_external_link('Homepage', journal.homepage_url)}
            {_external_link('Latest articles', journal.latest_articles_url)}
          </div>
        </div>
        <div class="journal-cover-large" aria-hidden="true">{cover_img}</div>
      </div>
    </section>
    {_article_limit_notice(len(articles), len(shown))}
    <section class="article-list">
      {''.join(_article_card(article, journal, subarea_by_id, prefix="../../") for article in shown) or '<p class="empty">No papers collected for this journal yet.</p>'}
    </section>
    """
    return _layout(config, journal.title, body, prefix="../../", section="journals")


def _special_issues_page(config: AppConfig, issues: list[SpecialIssueRecord]) -> str:
    active = [issue for issue in issues if issue.status == "active"]
    unverified = [issue for issue in issues if issue.status == "unverified"]
    closed = [issue for issue in issues if issue.status not in {"active", "unverified"}]
    body = f"""
    <section class="page-header">
      <p class="eyebrow">Calls and special issues</p>
      <h1>Special-Issue Monitor</h1>
      <p>Verified calls from monitored publisher pages, with deadlines and source links kept visible.</p>
      <label class="search-label search-label--page"><span>Find a call</span><input class="search-input" data-search-input data-search-kind="call" placeholder="Search by journal, topic, or status" aria-label="Filter special issues"></label>
      <p class="search-status" data-search-status aria-live="polite"></p>
    </section>
    <section class="issue-section" data-search-list>
      <h2>Active</h2>
      {''.join(_special_issue_card(issue) for issue in active) or '<p class="empty">No active calls collected yet.</p>'}
      <h2>Needs verification</h2>
      {''.join(_special_issue_card(issue) for issue in unverified) or '<p class="empty">No calls awaiting verification.</p>'}
      <h2>Closed</h2>
      {''.join(_special_issue_card(issue) for issue in closed) or '<p class="empty">No closed calls collected yet.</p>'}
    </section>
    <p class="search-empty" data-search-empty hidden>No calls match that search.</p>
    """
    return _layout(config, "Special Issues", body, prefix="../", script=True, section="calls")


def _status_page(config: AppConfig, health: dict, last_run: dict | None) -> str:
    last_run = last_run or {}
    run_status = last_run.get("status", "not recorded")
    body = f"""
    <section class="page-header">
      <p class="eyebrow">Operations</p>
      <h1>Collection Status</h1>
      <p>Freshness and pipeline health for the public dataset.</p>
    </section>
    {_health_banner(health, link="")}
    <section class="status-grid">
      {_metric("Dataset", str(health.get('status', 'unknown')).title())}
      {_metric("Articles", str(health.get('article_count', 0)))}
      {_metric("Latest paper", health.get('latest_published_date') or '—')}
      {_metric("Last article sync", _short_date(health.get('latest_article_sync')))}
      {_metric("Active calls", str(health.get('active_special_issue_count', 0)))}
      {_metric("Calls to verify", str(health.get('unverified_special_issue_count', 0)))}
      {_metric("Local-only records", str(health.get('local_only_article_count', 0)))}
    </section>
    <section class="run-panel">
      <p class="section-kicker">Last weekly run</p>
      <h2>{escape(str(run_status).title())}</h2>
      <p class="muted">Digest date: {escape(str(last_run.get('digest_date') or 'not recorded'))} · Recorded: {escape(_short_date(last_run.get('recorded_at')))}</p>
      <div class="tag-row">
        <span>Papers: {escape(str(last_run.get('paper_collection') or 'not recorded'))}</span>
        <span>Calls: {escape(str(last_run.get('special_issue_collection') or 'not recorded'))}</span>
      </div>
      <p><a href="../data/health.json">View machine-readable health data &rarr;</a></p>
    </section>
    """
    return _layout(config, "Collection Status", body, prefix="../", section="status")


def _layout(
    config: AppConfig,
    title: str,
    body: str,
    prefix: str,
    script: bool = False,
    section: str = "",
) -> str:
    script_tag = f'<script src="{prefix}assets/search.js?v={ASSET_VERSION}"></script>' if script else ""
    site_url = config.settings.site_url.rstrip("/")
    social_image = (
        f'<meta property="og:image" content="{escape(site_url)}/assets/og.png">\n'
        f'  <meta name="twitter:image" content="{escape(site_url)}/assets/og.png">'
        if site_url
        else ""
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="{escape(config.settings.description)}">
  <meta name="theme-color" content="#faf6ef">
  <meta property="og:type" content="website">
  <meta property="og:title" content="{escape(title)} · {escape(config.settings.title)}">
  <meta property="og:description" content="{escape(config.settings.description)}">
  <meta name="twitter:card" content="summary_large_image">
  {social_image}
  <title>{escape(title)} · {escape(config.settings.title)}</title>
  <link rel="stylesheet" href="{prefix}assets/styles.css?v={ASSET_VERSION}">
</head>
<body>
  <a class="skip-link" href="#content">Skip to content</a>
  <header class="site-header">
    <a class="brand" href="{prefix}index.html"><span class="brand-mark" aria-hidden="true">CJ</span><span>{escape(config.settings.title)}</span></a>
    <nav class="primary-nav" aria-label="Primary navigation">
      {_nav_link("Journals", f"{prefix}journals/index.html", section == "journals")}
      {_nav_link("Political", f"{prefix}subareas/political-communication/index.html", section == "political-communication")}
      {_nav_link("Health", f"{prefix}subareas/health-communication/index.html", section == "health-communication")}
      {_nav_link("Methods", f"{prefix}subareas/methods/index.html", section == "methods")}
      {_nav_link("Calls", f"{prefix}special-issues/index.html", section == "calls")}
      {_nav_link("Status", f"{prefix}status/index.html", section == "status")}
    </nav>
  </header>
  <main id="content">
    {body}
  </main>
  <footer class="site-footer">
    <div>
      <strong>{escape(config.settings.title)}</strong>
      <p>Weekly, reproducible monitoring for communication researchers.</p>
    </div>
    <div>
      <strong>Transparent by design</strong>
      <p>Only records with usable abstracts are published. Dates and links remain tied to their source metadata.</p>
    </div>
    <div class="footer-links">
      <strong>Explore</strong>
      <a href="{prefix}data/latest.json">Latest JSON export</a>
      <a href="{prefix}status/index.html">Collection status</a>
      <span>{escape(config.settings.timezone)}</span>
    </div>
  </footer>
  {script_tag}
</body>
</html>
"""


def _article_card(
    article: ArticleRecord,
    journal: JournalConfig,
    subarea_by_id: dict[str, SubareaConfig],
    prefix: str,
) -> str:
    link = article.canonical_url or (f"https://doi.org/{article.doi}" if article.doi else "")
    subarea_labels = [_subarea_label(subarea_id, subarea_by_id) for subarea_id in journal.subareas]
    search = escape(" ".join([article.title, article.journal_title, " ".join(article.authors), " ".join(subarea_labels)]).lower())
    authors = "; ".join(article.authors[:6])
    more_authors = " et al." if len(article.authors) > 6 else ""
    issue_bits = [part for part in [article.volume, article.issue, article.pages] if part]
    abstract_text = public_abstract(article)
    if abstract_text is None:
        raise ValueError(f"Article {article.title!r} is not eligible for public rendering.")
    abstract = escape(_truncate(abstract_text, 420))
    return f"""
    <article class="article-card" data-search-item data-search-text="{search}">
      <div class="article-meta">
        <a href="{prefix}journals/{slugify(journal.title)}/index.html">{escape(article.journal_title)}</a>
        <span>{escape(article.published_date)}</span>
      </div>
      <h3>{_maybe_link(escape(article.title), link)}</h3>
      <p class="authors">{escape(authors + more_authors) if authors else 'Authors unavailable'}</p>
      <p>{abstract}</p>
      <div class="tag-row">{''.join(f'<span>{escape(item)}</span>' for item in subarea_labels)}</div>
      {'<p class="muted">Volume/issue/pages: ' + escape(', '.join(issue_bits)) + '</p>' if issue_bits else ''}
    </article>
    """


def _special_issue_card(issue: SpecialIssueRecord) -> str:
    search = escape(" ".join([issue.title, issue.journal_title, issue.status, issue.confidence]).lower())
    deadline = f"<span>Deadline: {escape(issue.deadline)}</span>" if issue.deadline else "<span>Deadline unavailable</span>"
    return f"""
    <article class="issue-card" data-search-item data-search-text="{search}">
      <div class="article-meta">
        <span>{escape(issue.journal_title)}</span>
        <span>{escape(issue.status)} · {escape(issue.confidence)} confidence</span>
      </div>
      <h3><a href="{escape(issue.source_url)}">{escape(issue.title)}</a></h3>
      <p>{escape(_truncate(issue.raw_snippet, 320))}</p>
      <div class="article-meta">{deadline}<span>Last seen: {escape(issue.last_seen_at or '')}</span></div>
    </article>
    """


def _special_issue_compact(issue: SpecialIssueRecord) -> str:
    return f"""
    <a class="mini-item" href="{escape(issue.source_url)}">
      <strong>{escape(issue.title)}</strong>
      <span>{escape(issue.journal_title)} · {escape(issue.deadline or 'no deadline')}</span>
    </a>
    """


def _journal_directory_card(
    journal: JournalConfig,
    subarea_by_id: dict[str, SubareaConfig],
    count: int,
    latest: str | None,
    cover_path: str,
    prefix: str,
) -> str:
    slug = slugify(journal.title)
    subarea_labels = [_subarea_label(subarea_id, subarea_by_id) for subarea_id in journal.subareas]
    search = escape(" ".join([journal.title, journal.publisher or "", " ".join(subarea_labels)]).lower())
    cover_img = (
        f'<img src="{escape(prefix + cover_path)}" alt="{escape(journal.title)} cover" loading="lazy">'
        if cover_path
        else ""
    )
    tags = "".join(f"<span>{escape(label)}</span>" for label in subarea_labels)
    return f"""
    <article class="journal-card" data-search-item data-search-text="{search}">
      <a class="cover" href="{prefix}journals/{slug}/index.html">{cover_img}</a>
      <div class="journal-card__body">
        <h3><a href="{prefix}journals/{slug}/index.html">{escape(journal.title)}</a></h3>
        <p class="muted">{escape(journal.publisher or 'Publisher unavailable')} &middot; {count} papers &middot; latest {escape(latest or '—')}</p>
        <div class="tag-row">{tags}</div>
      </div>
    </article>"""


def _journal_cover_card(
    journal: JournalConfig,
    papers: list[ArticleRecord],
    cover_path: str,
    prefix: str,
) -> str:
    slug = slugify(journal.title)
    shown = papers[:3]
    search = escape(
        " ".join([journal.title, journal.publisher or "", " ".join(p.title for p in shown)]).lower()
    )
    latest = papers[0].published_date if papers else "—"
    items = "".join(
        f'<li><a href="{escape(_paper_link(paper, slug, prefix))}">{escape(_truncate(paper.title, 88))}</a>'
        f"<span>{escape(paper.published_date)}</span></li>"
        for paper in shown
    )
    cover_img = (
        f'<img src="{escape(prefix + cover_path)}" alt="{escape(journal.title)} cover" loading="lazy">'
        if cover_path
        else ""
    )
    return f"""
    <article class="journal-card" data-search-item data-search-text="{search}">
      <a class="cover" href="{prefix}journals/{slug}/index.html">{cover_img}</a>
      <div class="journal-card__body">
        <h3><a href="{prefix}journals/{slug}/index.html">{escape(journal.title)}</a></h3>
        <p class="muted">{len(papers)} papers &middot; latest {escape(latest)}</p>
        <ul class="paper-mini">{items}</ul>
      </div>
    </article>"""


def _paper_link(article: ArticleRecord, journal_slug: str, prefix: str) -> str:
    return article.canonical_url or (
        f"https://doi.org/{article.doi}"
        if article.doi
        else f"{prefix}journals/{journal_slug}/index.html"
    )


def _hero_link(subarea: SubareaConfig, prefix: str) -> str:
    return (
        f'<a class="hero-link" href="{prefix}subareas/{subarea.id}/index.html">'
        f'{escape(_short_subarea_label(subarea))}</a>'
    )


def _short_subarea_label(subarea: SubareaConfig) -> str:
    labels = {
        "political-communication": "Political Communication",
        "health-communication": "Health Communication",
        "methods": "Methods",
    }
    return labels.get(subarea.id, subarea.label)


def _subarea_label(subarea_id: str, subarea_by_id: dict[str, SubareaConfig]) -> str:
    return subarea_by_id[subarea_id].label if subarea_id in subarea_by_id else subarea_id


def _empty_latest_state() -> str:
    return """
    <div class="empty-state" data-search-item data-search-text="empty collection">
      <p class="section-kicker">Ready for collection</p>
      <h3>No papers collected yet</h3>
      <p>Run the weekly pipeline to populate this stream from the configured communication journal registry.</p>
      <code>communication-journal-site run-weekly --digest-date YYYY-MM-DD</code>
    </div>
    """


def _health_banner(health: dict, link: str = "status/index.html") -> str:
    status = health.get("status")
    if status == "current":
        return ""
    if status == "empty":
        message = "The collection is ready but does not contain papers yet."
        tone = "neutral"
    else:
        age = health.get("article_sync_age_days")
        age_label = f"{age} days ago" if isinstance(age, int) else "on an unknown date"
        message = f"Article collection last ran {age_label}. Some recent papers may be missing."
        tone = "warning"
    status_link = f' <a href="{escape(link)}">View status</a>' if link else ""
    return f'<aside class="status-banner status-banner--{tone}">{escape(message)}{status_link}</aside>'


def _article_limit_notice(total: int, shown: int) -> str:
    if total <= shown:
        return ""
    return (
        '<p class="listing-note">'
        f"Showing the newest {shown:,} of {total:,} papers to keep this page fast. "
        '<a href="../../data/articles.jsonl">Download the complete dataset</a>.'
        "</p>"
    )


def _short_date(value: object) -> str:
    text = str(value or "")
    return text[:10] if text else "—"


def _display_date(value: object) -> str:
    text = str(value or "")[:10]
    if not text:
        return "—"
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        return text
    return f"{parsed.strftime('%b')} {parsed.day}, {parsed.year}"


def _metric(label: str, value: str) -> str:
    return f"<div><strong>{escape(value)}</strong><span>{escape(label)}</span></div>"


def _nav_link(label: str, href: str, current: bool) -> str:
    current_attr = ' aria-current="page"' if current else ""
    return f'<a href="{escape(href)}"{current_attr}>{escape(label)}</a>'


def _external_link(label: str, url: str) -> str:
    if not url:
        return ""
    return f'<a class="button-link" href="{escape(url)}">{escape(label)}</a>'


def _maybe_link(label: str, url: str) -> str:
    return f'<a href="{escape(url)}">{label}</a>' if url else label


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    clipped = text[:limit].rsplit(" ", 1)[0]
    clipped = (clipped or text[:limit]).rstrip(" .,;:—–-")
    return f"{clipped}…"


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _copy(source: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    copyfile(source, destination)
    return destination


def _clean_previous_build(output_path: Path) -> None:
    manifest_path = output_path / BUILD_MANIFEST
    if not manifest_path.exists():
        return
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    root = output_path.resolve()
    for relative in payload.get("files", []):
        if not isinstance(relative, str):
            continue
        candidate = (output_path / relative).resolve()
        if candidate == root or root not in candidate.parents:
            continue
        if candidate.is_file():
            candidate.unlink()
    manifest_path.unlink(missing_ok=True)


def _write_build_manifest(output_path: Path, paths: list[Path]) -> Path:
    relative_paths = sorted(
        {
            str(path.relative_to(output_path))
            for path in paths
            if path.exists() and path.is_file() and output_path in path.parents
        }
    )
    return _write(
        output_path / BUILD_MANIFEST,
        json.dumps({"files": relative_paths}, indent=2, ensure_ascii=True) + "\n",
    )


def _styles() -> str:
    return """
:root {
  --bg:#fbf7f0; --ink:#1b1714; --muted:#655d54; --line:#e4d8c9; --panel:#fffdf9;
  --accent:#8a2b2b; --accent-ink:#7c2222; --accent-soft:#f8eeee;
  --radius:8px; --radius-lg:14px; --radius-pill:999px;
  --shadow:0 18px 50px rgba(58, 38, 24, .08); --shadow-card:0 10px 28px rgba(58, 38, 24, .06);
  --font:Charter,Georgia,'Iowan Old Style','Times New Roman',serif;
  --font-head:Charter,Georgia,'Iowan Old Style',serif;
  --head-weight:700; --head-tracking:-0.01em;
  --header-bg:rgba(255, 253, 249, .92); --header-blur:saturate(1.15) blur(12px);
  --band-bg:linear-gradient(135deg, #fffaf2 0%, #f5ecdf 100%); --band-border:1px solid rgba(138, 43, 43, .14);
  --tag-bg:#fff9f6; --tag-ink:#7c2222; --tag-border:#ead3d0; --input-bg:#fff;
  --focus-1:#fff7ed; --focus-2:#f6f0e7; --focus-3:#fff5f5; --nav-ink:#1b1714;
}
.eyebrow { font-family:var(--font); font-style:italic; text-transform:none; letter-spacing:0; font-weight:400; color:var(--accent); font-size:15px; }
.focus-card { border-top:3px solid var(--accent); }
.lede { font-size:19px; }

* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0;
  font-family: var(--font);
  color: var(--ink);
  background:
    radial-gradient(circle at top left, rgba(138, 43, 43, .08), transparent 28rem),
    linear-gradient(180deg, #fffdf9 0%, var(--bg) 42rem);
  line-height: 1.5;
  overflow-x: hidden;
}
a { color: var(--accent-ink); text-decoration: none; }
a:hover { text-decoration: underline; }
a:focus-visible, input:focus-visible { outline: 3px solid #d6a35f; outline-offset: 3px; }
.skip-link { position: fixed; left: 12px; top: -80px; z-index: 10; padding: 9px 12px; background: var(--ink); color: #fff; }
.skip-link:focus { top: 12px; }
.site-header {
  display: grid; grid-template-columns: minmax(max-content, 1fr) auto; align-items: center;
  gap: 20px; padding: 12px clamp(16px, 4vw, 48px);
  border-bottom: 1px solid var(--line);
  background: var(--header-bg);
  position: sticky; top: 0; z-index: 2;
  backdrop-filter: var(--header-blur, none);
}
.brand {
  font-weight: 800; color: var(--ink); display: inline-flex;
  align-items: center; gap: 10px; font-family: var(--font-head);
  line-height: 1.1; white-space: nowrap;
}
.brand:hover { text-decoration: none; color: var(--accent-ink); }
.brand-mark {
  display: inline-grid; place-items: center; flex: 0 0 auto;
  width: 36px; height: 36px; border-radius: 50%;
  background: var(--ink); color: #fffaf2; font-size: 13px; letter-spacing: .04em;
}
.primary-nav { display: flex; gap: 4px; flex-wrap: nowrap; justify-content: flex-end; overflow-x: auto; scrollbar-width: none; }
.primary-nav::-webkit-scrollbar { display: none; }
.primary-nav a {
  color: var(--nav-ink); border: 1px solid transparent;
  border-radius: var(--radius-pill); padding: 7px 10px; font-size: 14px;
  white-space: nowrap;
}
.primary-nav a:hover { background: #fff; border-color: var(--line); text-decoration: none; }
.primary-nav a[aria-current="page"] { background: var(--ink); border-color: var(--ink); color: #fffaf2; }
main { width: min(1180px, calc(100% - 32px)); margin: 0 auto; padding: 30px 0 56px; }
.overview-band {
  display: grid; grid-template-columns: minmax(0, 1.15fr) minmax(260px, 360px);
  gap: clamp(22px, 4vw, 42px); align-items: stretch;
  padding: clamp(24px, 4vw, 46px);
  border: var(--band-border);
  border-radius: var(--radius-lg);
  background: var(--band-bg);
  box-shadow: var(--shadow);
}
.eyebrow {
  margin: 0 0 8px; text-transform: uppercase; font-size: 12px;
  font-weight: 700; letter-spacing: .08em; color: var(--accent-ink);
}
h1 { margin: 0; font-size: clamp(32px, 5vw, 54px); line-height: 1.05; font-family: var(--font-head); font-weight: var(--head-weight, 800); letter-spacing: var(--head-tracking, 0); }
h2 { margin: 0 0 14px; font-size: 21px; font-family: var(--font-head); }
h3 { margin: 8px 0; font-size: 18px; font-family: var(--font-head); line-height: 1.25; }
.lede { max-width: 760px; color: var(--muted); font-size: 18px; }
.hero-actions { display: flex; flex-wrap: wrap; gap: 9px; margin-top: 22px; }
.hero-link {
  display: inline-flex; align-items: center; border: 1px solid var(--line);
  background: var(--panel); color: var(--ink); border-radius: var(--radius-pill);
  padding: 9px 14px; font-weight: 700; box-shadow: 0 4px 12px rgba(58, 38, 24, .04);
}
.hero-link:hover { border-color: var(--accent); color: var(--accent-ink); text-decoration: none; transform: translateY(-1px); }
.metrics { display: grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap: 10px; align-content: end; }
.metrics div, .row-stat { border: 1px solid var(--line); background: rgba(255,255,255,.76); border-radius: var(--radius); padding: 16px; }
.metrics strong, .row-stat strong { display: block; font-size: clamp(20px, 2vw, 28px); line-height: 1.05; font-family: var(--font-head); text-wrap: balance; }
.metrics span, .row-stat span, .muted { color: var(--muted); font-size: 13px; }
.trust-strip {
  display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 0;
  margin: 16px 0 0; border: 1px solid var(--line); border-radius: var(--radius);
  background: rgba(255, 253, 249, .78); box-shadow: var(--shadow-card);
}
.trust-strip span { padding: 13px 15px; color: var(--muted); font-size: 12px; }
.trust-strip span + span { border-left: 1px solid var(--line); }
.trust-strip strong { display: block; margin-bottom: 2px; color: var(--ink); font-size: 13px; }
.focus-strip { display: grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap: 14px; margin-top: 16px; }
.focus-card {
  display: grid; gap: 10px; min-height: 170px; padding: 18px;
  border: 1px solid var(--line); border-radius: var(--radius);
  background: var(--panel); color: var(--ink); box-shadow: var(--shadow-card);
}
.focus-card:hover { text-decoration: none; border-color: var(--accent); }
.focus-card strong { font-size: 44px; line-height: 1; font-family: var(--font-head); }
.focus-card span:last-child { color: var(--muted); font-size: 14px; }
.focus-card__label { font-size: 12px; font-weight: 800; letter-spacing: .04em; text-transform: uppercase; }
.focus-card--political-communication { background: var(--focus-1); }
.focus-card--health-communication { background: var(--focus-2); }
.focus-card--methods { background: var(--focus-3); }
.section-grid { display: grid; grid-template-columns: minmax(0,1fr) 340px; gap: 28px; margin-top: 30px; }
.section-heading { display: flex; justify-content: space-between; gap: 16px; align-items: end; margin-bottom: 14px; }
.section-kicker { margin: 0 0 4px; color: var(--muted); font-size: 12px; font-weight: 800; text-transform: uppercase; letter-spacing: .06em; }
.search-label { display: grid; gap: 5px; width: min(100%, 440px); color: var(--muted); font-size: 12px; font-weight: 700; }
.search-label--page { margin-top: 18px; }
.search-input { width: 100%; border: 1px solid var(--line); border-radius: var(--radius-pill); padding: 11px 15px; background: var(--input-bg); color: var(--ink); font: inherit; font-weight: 400; box-shadow: inset 0 1px 2px rgba(58,38,24,.04); }
.search-input::placeholder { color: #8d8378; }
.search-status { min-height: 1.3em; margin: 5px 0 0; color: var(--muted); font-size: 12px; }
.search-empty { margin-top: 18px; border: 1px dashed var(--line); border-radius: var(--radius); padding: 22px; text-align: center; color: var(--muted); background: var(--panel); }
[hidden] { display: none !important; }
.article-list, .issue-section, .directory { display: grid; gap: 12px; }
.article-card, .issue-card, .journal-row, .side-panel { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); box-shadow: var(--shadow-card); }
.article-card, .issue-card { padding: 18px; transition: border-color .16s ease, box-shadow .16s ease, transform .16s ease; }
.article-card:hover, .issue-card:hover, .journal-row:hover { border-color: rgba(138,43,43,.45); box-shadow: 0 14px 34px rgba(58,38,24,.08); transform: translateY(-1px); }
.article-card p, .issue-card p, .journal-row p { margin: 8px 0; color: var(--muted); }
.article-meta { display: flex; gap: 10px; flex-wrap: wrap; color: var(--muted); font-size: 13px; }
.authors { color: var(--ink) !important; font-size: 14px; }
.tag-row { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 10px; }
.tag-row span { display: inline-flex; border: 1px solid var(--tag-border); border-radius: var(--radius-pill); padding: 3px 9px; font-size: 12px; color: var(--tag-ink); background: var(--tag-bg); }
.side-panel { padding: 18px; align-self: start; position: sticky; top: 82px; }
.link-list { display: grid; gap: 8px; margin-bottom: 24px; }
.link-list a, .mini-item { display: flex; justify-content: space-between; gap: 12px; border-bottom: 1px solid var(--line); padding: 9px 0; }
.link-list a strong { font-family: var(--font-head); }
.mini-item { display: grid; color: var(--ink); }
.mini-item span { color: var(--muted); font-size: 13px; }
.page-header { padding: clamp(22px, 4vw, 38px); border: var(--band-border); border-radius: var(--radius-lg); background: var(--band-bg); box-shadow: var(--shadow); margin-bottom: 24px; }
.page-header p { max-width: 820px; color: var(--muted); }
.journal-hero { display: grid; grid-template-columns: minmax(0,1fr) 156px; gap: clamp(20px, 4vw, 38px); align-items: center; }
.journal-cover-large {
  justify-self: end; width: 138px; min-height: 180px; display: grid; place-items: center;
  padding: 10px; border: 1px solid rgba(138,43,43,.18); border-radius: 10px;
  background: rgba(255,255,255,.72); box-shadow: 0 18px 32px rgba(58,38,24,.12);
}
.journal-cover-large img { width: 100%; max-height: 184px; object-fit: cover; display: block; border-radius: 4px; }
.journal-row { display: grid; grid-template-columns: minmax(0,1fr) 140px; gap: 20px; align-items: center; padding: 16px 18px; }
.row-stat { text-align: right; }
.action-row { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 14px; }
.button-link { display: inline-flex; align-items: center; border: 1px solid rgba(138,43,43,.45); border-radius: var(--radius-pill); padding: 8px 13px; color: var(--accent-ink); background: var(--accent-soft); font-weight: 700; }
.button-link:hover { text-decoration: none; background: #fff6f6; border-color: var(--accent); }
.empty, .empty-state { border: 1px dashed var(--line); padding: 20px; border-radius: var(--radius); color: var(--muted); background: var(--panel); }
.empty-state code { display: inline-block; margin-top: 8px; max-width: 100%; overflow-wrap: anywhere; border: 1px solid var(--line); background: var(--bg); border-radius: var(--radius); padding: 8px 10px; color: var(--accent-ink); font-family: ui-monospace, Menlo, monospace; }
.site-footer { width: min(1180px, calc(100% - 32px)); margin: 0 auto; padding: 26px 0 34px; border-top: 1px solid var(--line); display: grid; grid-template-columns: 1.1fr 1.4fr .8fr; gap: clamp(24px, 5vw, 60px); color: var(--muted); font-size: 13px; }
.site-footer strong { color: var(--ink); font-family: var(--font-head); }
.site-footer p { max-width: 460px; margin: 6px 0 0; }
.footer-links { display: grid; gap: 5px; align-content: start; }
.calls-band { margin-top: 24px; padding: 20px; border: 1px solid var(--line); border-radius: var(--radius); background: var(--panel); box-shadow: var(--shadow-card); }
.calls-band .section-heading { margin-bottom: 10px; }
.calls-row { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 10px; }
.calls-empty { display: flex; justify-content: space-between; align-items: center; gap: 20px; padding-top: 6px; }
.calls-empty p { margin: 0; color: var(--muted); }
.calls-empty .section-kicker { margin-bottom: 3px; color: var(--accent-ink); }
.home-toolbar { display: flex; justify-content: space-between; align-items: flex-end; gap: 24px; margin: 36px 0 10px; border-top: 1px solid var(--line); padding-top: 24px; }
.home-toolbar .section-kicker { margin: 0; }
.home-toolbar h2 { margin: 2px 0 0; }
.subarea-group { margin: 22px 0; scroll-margin-top: 80px; }
.subarea-group > .section-heading { margin-bottom: 12px; }
.journal-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 320px), 1fr)); gap: 14px; }
.journal-card { display: grid; grid-template-columns: 88px minmax(0, 1fr); gap: 14px; padding: 14px; background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); box-shadow: var(--shadow-card); transition: border-color .16s ease, box-shadow .16s ease, transform .16s ease; min-width: 0; }
.journal-card:hover { border-color: rgba(138,43,43,.45); box-shadow: 0 16px 36px rgba(58,38,24,.08); transform: translateY(-1px); }
.journal-card .cover { display: block; }
.journal-card .cover img { width: 88px; height: 117px; object-fit: cover; border: 1px solid var(--line); display: block; background: var(--band-bg); border-radius: 3px; box-shadow: 0 8px 18px rgba(58,38,24,.10); }
.journal-card__body { min-width: 0; }
.journal-card__body h3 { margin: 0 0 4px; font-size: 16px; line-height: 1.2; }
.journal-card__body .muted { margin: 0; font-size: 12px; }
.paper-mini { list-style: none; margin: 10px 0 0; padding: 0; display: grid; gap: 6px; min-width: 0; }
.paper-mini li { display: flex; justify-content: space-between; gap: 10px; font-size: 13px; align-items: baseline; min-width: 0; }
.paper-mini li a { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.paper-mini li span { flex: 0 0 auto; color: var(--muted); white-space: nowrap; font-size: 12px; }
.group-more { display: inline-block; margin-top: 12px; font-size: 13px; font-weight: 700; }
.area-nav { display: flex; flex-wrap: wrap; justify-content: center; gap: 8px; margin: 0 0 20px; }
.area-nav a { border: 1px solid var(--line); border-radius: var(--radius-pill); padding: 5px 11px; font-size: 13px; color: var(--ink); }
.area-nav a:hover { border-color: var(--accent); color: var(--accent-ink); text-decoration: none; }
.status-banner { margin: 16px 0 0; padding: 13px 16px; border: 1px solid var(--line); border-radius: var(--radius); background: var(--panel); }
.status-banner--warning { border-color: #d6a35f; background: #fff8e8; }
.status-banner a { font-weight: 700; }
.listing-note { margin: -8px 0 18px; padding: 12px 14px; border-left: 3px solid var(--accent); background: var(--band-bg); color: var(--muted); }
.status-grid { display: grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap: 12px; }
.status-grid > div, .run-panel { border: 1px solid var(--line); background: var(--panel); border-radius: var(--radius); padding: 18px; }
.status-grid strong { display: block; font-family: var(--font-head); font-size: 26px; }
.status-grid span { color: var(--muted); font-size: 13px; }
.run-panel { margin-top: 18px; }
@media (max-width: 860px) {
  .overview-band, .section-grid, .journal-row { grid-template-columns: 1fr; }
  .journal-hero { grid-template-columns: 1fr; }
  .journal-cover-large { justify-self: start; }
  .metrics { grid-template-columns: 1fr 1fr; }
  .status-grid { grid-template-columns: 1fr 1fr; }
  .section-heading { align-items: stretch; flex-direction: column; }
  .search-input { width: 100%; }
  .row-stat { text-align: left; }
  .side-panel { position: static; }
  .trust-strip { grid-template-columns: 1fr 1fr; }
  .trust-strip span:nth-child(3) { border-left: 0; border-top: 1px solid var(--line); }
  .trust-strip span:nth-child(4) { border-top: 1px solid var(--line); }
  .site-footer { grid-template-columns: 1fr 1fr; }
  .footer-links { grid-column: 1 / -1; }
}
@media (max-width: 680px) {
  .site-header { grid-template-columns: 1fr; align-items: start; }
  .primary-nav { justify-content: flex-start; max-width: 100%; padding-bottom: 2px; }
  .brand span:last-child { white-space: normal; }
}
@media (max-width: 560px) {
  .focus-strip { grid-template-columns: 1fr; }
  .metrics { grid-template-columns: 1fr; }
  .status-grid { grid-template-columns: 1fr; }
  .journal-card { grid-template-columns: 72px minmax(0, 1fr); }
  .journal-card .cover img { width: 72px; height: 96px; }
  .home-toolbar { align-items: stretch; flex-direction: column; }
  .trust-strip { grid-template-columns: 1fr; }
  .trust-strip span + span, .trust-strip span:nth-child(3) { border-left: 0; border-top: 1px solid var(--line); }
  .calls-empty { align-items: flex-start; flex-direction: column; }
  .site-footer { grid-template-columns: 1fr; }
  .footer-links { grid-column: auto; }
  h1 { font-size: 34px; }
}
@media (prefers-reduced-motion: reduce) {
  html { scroll-behavior: auto; }
  *, *::before, *::after { transition-duration: .01ms !important; }
}
"""


def _search_js() -> str:
    return """
document.querySelectorAll('[data-search-input]').forEach((input) => {
  const list = input.closest('main').querySelector('[data-search-list]');
  if (!list) return;
  const status = input.closest('main').querySelector('[data-search-status]');
  const empty = input.closest('main').querySelector('[data-search-empty]');
  const items = Array.from(list.querySelectorAll('[data-search-item]'));
  const groups = Array.from(list.querySelectorAll('.subarea-group'));
  const kind = input.dataset.searchKind || 'result';

  const update = () => {
    const query = input.value.trim().toLowerCase();
    let visible = 0;
    items.forEach((item) => {
      const text = item.getAttribute('data-search-text') || item.textContent.toLowerCase();
      item.hidden = query.length > 0 && !text.includes(query);
      if (!item.hidden) visible += 1;
    });

    groups.forEach((group) => {
      const groupItems = Array.from(group.querySelectorAll('[data-search-item]'));
      group.hidden = query.length > 0 && groupItems.every((item) => item.hidden);
    });

    if (status) {
      status.textContent = query
        ? `${visible} matching ${kind}${visible === 1 ? '' : 's'}`
        : `${items.length} ${kind}${items.length === 1 ? '' : 's'} available`;
    }
    if (empty) empty.hidden = visible !== 0;
  };

  input.addEventListener('input', update);
  update();
});
"""
