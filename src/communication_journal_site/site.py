from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from html import escape
from pathlib import Path
from shutil import copyfile

from .covers import ensure_cover_asset
from .health import (
    apply_special_issue_coverage,
    collection_health,
    local_today,
    read_last_run,
    read_special_issue_run,
)
from .models import (
    AppConfig,
    ArticleRecord,
    JournalConfig,
    JournalMetric,
    SpecialIssueRecord,
    SubareaConfig,
)
from .normalize import slugify
from .publication import (
    PUBLIC_ARTICLE_POLICY,
    local_only_article_count,
    public_abstract,
    public_articles,
)
from .special_issues import special_issue_opportunity_type
from .storage import StateStore


BUILD_MANIFEST = ".build-manifest.json"
ASSET_VERSION = "20260731-home-covers"


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
    apply_special_issue_coverage(
        health,
        config.journals,
        config.special_issue_sources,
        verification_days=config.settings.special_issue_verification_days,
        today=today,
    )
    health["local_only_article_count"] = hidden_article_count
    health["journal_count"] = len([journal for journal in config.journals if journal.active])
    health["public_article_policy"] = PUBLIC_ARTICLE_POLICY
    last_run = read_last_run(store.db_path.parent)
    health["last_run"] = last_run
    health["special_issue_run"] = read_special_issue_run(store.db_path.parent)
    cover_cache = store.db_path.parent / "covers"
    covers = {
        journal.id: ensure_cover_asset(journal, cover_cache, assets_dir)
        for journal in config.journals
    }
    special_issues_page = _special_issues_page(config, special_issues)
    social_source = Path(__file__).resolve().parents[2] / "assets" / "og-monochrome.png"
    written = [
        _write(output_path / "assets" / "styles.css", _styles()),
        _write(output_path / "assets" / "search.js", _search_js()),
        _write(
            output_path / "index.html",
            _home_page(config, articles, special_issues, journal_by_id, subarea_by_id, covers, health),
        ),
        _write(output_path / "journals" / "index.html", _journals_page(config, articles, covers)),
        _write(output_path / "special-issues" / "index.html", special_issues_page),
        _write(output_path / "calls" / "index.html", special_issues_page),
        _write(output_path / "status" / "index.html", _status_page(config, health, last_run)),
    ]
    if social_source.exists():
        written.append(_copy(social_source, output_path / "assets" / "og-monochrome.png"))
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
    shown_articles = articles[:50]
    if active_special:
        calls_html = f'<div class="calls-row">{"".join(_special_issue_compact(issue) for issue in active_special)}</div>'
        calls_all_link = '<a href="special-issues/index.html">View all special issues &rarr;</a>'
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
    {_home_cover_showcase(config, articles, journal_by_id, subarea_by_id, covers)}
    <section class="calls-band">
      <div class="section-heading">
        <div>
          <p class="section-kicker">Special-issue calls</p>
          <h2>Active special issues</h2>
        </div>
        {calls_all_link}
      </div>
      {calls_html}
    </section>
    <div class="home-toolbar">
      <div>
        <p class="section-kicker">Latest research</p>
        <h2>New papers across {len([journal for journal in config.journals if journal.active])} journals</h2>
        <p class="muted">Full titles, every author supplied by the source metadata, and an abstract excerpt.</p>
        <p class="search-status" data-search-status aria-live="polite"></p>
      </div>
      <label class="search-label"><span>Find a paper</span><input class="search-input" data-search-input data-search-kind="paper" placeholder="Search title, author, journal, or area" aria-label="Filter papers"></label>
    </div>
    {_article_limit_notice(len(articles), len(shown_articles), prefix="")}
    <section class="article-list" data-search-list>
      {''.join(_article_card(article, journal_by_id[article.journal_id], subarea_by_id, prefix="") for article in shown_articles) or _empty_latest_state()}
    </section>
    <p class="search-empty" data-search-empty hidden>No papers match that search.</p>
    """
    return _layout(config, "Home", body, prefix="", script=True, section="home")


def _home_cover_showcase(
    config: AppConfig,
    articles: list[ArticleRecord],
    journal_by_id: dict[str, JournalConfig],
    subarea_by_id: dict[str, SubareaConfig],
    covers: dict[str, str],
) -> str:
    """Show covers for journals represented in the latest research feed."""
    journal_ids: list[str] = []
    for article in articles:
        if article.journal_id not in journal_ids and covers.get(article.journal_id):
            journal_ids.append(article.journal_id)
        if len(journal_ids) == 8:
            break
    if len(journal_ids) < 8:
        for journal in config.journals:
            if journal.active and journal.id not in journal_ids and covers.get(journal.id):
                journal_ids.append(journal.id)
            if len(journal_ids) == 8:
                break

    items = []
    for journal_id in journal_ids:
        journal = journal_by_id[journal_id]
        area = subarea_by_id.get(journal.primary_subarea)
        items.append(
            f"""<a class="cover-showcase__item" href="journals/{slugify(journal.title)}/index.html">
          <img src="{escape(covers[journal_id])}" alt="{escape(journal.title)} cover" loading="lazy">
          <strong>{escape(journal.title)}</strong>
          <span>{escape(area.label if area else _subarea_label(journal.primary_subarea))}</span>
        </a>"""
        )
    if not items:
        return ""
    items_html = "\n        ".join(items)
    return f"""<section class="cover-showcase" aria-labelledby="cover-showcase-title">
      <div class="section-heading">
        <div>
          <p class="section-kicker">From the collection</p>
          <h2 id="cover-showcase-title">Journals publishing new research</h2>
        </div>
        <a href="journals/index.html">Open the journal dictionary &rarr;</a>
      </div>
      <div class="cover-showcase__grid">
        {items_html}
      </div>
    </section>"""


def _journals_page(config: AppConfig, articles: list[ArticleRecord], covers: dict[str, str]) -> str:
    article_counts: dict[str, int] = defaultdict(int)
    latest: dict[str, str] = {}
    for article in articles:
        article_counts[article.journal_id] += 1
        latest[article.journal_id] = max(latest.get(article.journal_id, ""), article.published_date)
    groups: list[str] = []
    nav_links: list[str] = []
    for subarea in config.subareas:
        subarea_journals = [
            journal
            for journal in config.journals
            if journal.active and journal.primary_subarea == subarea.id
        ]
        if not subarea_journals:
            continue
        subarea_journals.sort(
            key=lambda journal: (
                -(config.journal_metrics[journal.id].value if journal.id in config.journal_metrics else -1),
                journal.title,
            )
        )
        nav_links.append(
            f'<a href="#sa-{escape(subarea.id)}">{escape(subarea.label)} ({len(subarea_journals)})</a>'
        )
        cards = "".join(
            _journal_directory_card(
                journal, config.subarea_by_id, article_counts[journal.id],
                latest.get(journal.id), covers.get(journal.id, ""),
                config.journal_metrics.get(journal.id), prefix="../",
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
      <p class="eyebrow">Journal dictionary</p>
      <h1>{len([journal for journal in config.journals if journal.active])} journals by primary area</h1>
      <p>{escape(config.settings.registry_source_note)}</p>
      {_metric_methodology(config)}
      <label class="search-label search-label--page"><span>Find a journal</span><input class="search-input" data-search-input data-search-kind="journal card" placeholder="Search by title, publisher, or area" aria-label="Filter journals"></label>
      <p class="search-status" data-search-status aria-live="polite"></p>
    </section>
    <nav class="area-nav" aria-label="Jump to area">{''.join(nav_links)}</nav>
    <div data-search-list>
      {''.join(groups)}
    </div>
    <p class="search-empty" data-search-empty hidden>No journals match that search.</p>
    """
    return _layout(config, "Journal Dictionary", body, prefix="../", script=True, section="journals")


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
    {_article_limit_notice(len(articles), len(shown), prefix="../../")}
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
    {_article_limit_notice(len(articles), len(shown), prefix="../../")}
    <section class="article-list">
      {''.join(_article_card(article, journal, subarea_by_id, prefix="../../") for article in shown) or '<p class="empty">No papers collected for this journal yet.</p>'}
    </section>
    """
    return _layout(config, journal.title, body, prefix="../../", section="journals")


def _special_issues_page(config: AppConfig, issues: list[SpecialIssueRecord]) -> str:
    active = [issue for issue in issues if issue.status == "active"]
    upcoming = [issue for issue in issues if issue.status == "upcoming"]
    unverified = [issue for issue in issues if issue.status == "unverified"]
    closed = [
        issue for issue in issues if issue.status not in {"active", "upcoming", "unverified"}
    ]
    body = f"""
    <section class="page-header">
      <p class="eyebrow">Calls and special issues</p>
      <h1>Special-Issue Monitor</h1>
      <p>Verified calls from monitored publisher pages, with deadlines and source links kept visible.</p>
      <aside class="metric-note">
        <p><strong>Separate pipelines:</strong> CFP access limits do not reduce article coverage. Articles are collected independently by ISSN for all {len([journal for journal in config.journals if journal.active])} journals.</p>
        <p>Automated CFP monitoring now covers all configured Taylor &amp; Francis and SAGE journals, plus the Cogitatio and Wiley titles. OUP and IJoC use dated official-source records; Elsevier and Hogrefe remain qualified gaps.</p>
        <p class="citation-links"><a href="https://github.com/ottoxin/communication-journal-site#call-for-papers-coverage">Coverage methods and exclusions</a></p>
      </aside>
      <label class="search-label search-label--page"><span>Find a call</span><input class="search-input" data-search-input data-search-kind="call" placeholder="Search by journal, topic, or status" aria-label="Filter special issues"></label>
      <p class="search-status" data-search-status aria-live="polite"></p>
    </section>
    <section class="issue-section" data-search-list>
      <h2>Active</h2>
      {''.join(_special_issue_card(issue) for issue in active) or '<p class="empty">No active calls collected yet.</p>'}
      <h2>Upcoming</h2>
      {''.join(_special_issue_card(issue) for issue in upcoming) or '<p class="empty">No announced calls are waiting for their submission window to open.</p>'}
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
      {_metric("Upcoming calls", str(health.get('upcoming_special_issue_count', 0)))}
      {_metric("Calls to verify", str(health.get('unverified_special_issue_count', 0)))}
      {_metric("Source collection", str((health.get('special_issue_run') or {}).get('status', (last_run or {}).get('special_issue_collection', 'unknown'))).title())}
      {_metric("Automated sources", str((health.get('special_issue_run') or {}).get('automated_source_count', 0)))}
      {_metric("Publisher-automated journals", str((health.get('special_issue_run') or {}).get('automated_publisher_journal_count', 0)))}
      {_metric("Verified fallbacks", str((health.get('special_issue_run') or {}).get('verified_source_count', 0)))}
      {_metric("Current journal audits", f"{health.get('special_issue_audit_coverage_count', 0)}/{health.get('special_issue_priority_journal_count', 0)}")}
      {_metric("Direct active sources", f"{health.get('special_issue_direct_coverage_count', 0)}/{health.get('special_issue_priority_journal_count', 0)}")}
      {_metric("Next source review", str((health.get('special_issue_run') or {}).get('next_verified_review_on') or '—'))}
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
        f'<meta property="og:image" content="{escape(site_url)}/assets/og-monochrome.png">\n'
        f'  <meta name="twitter:image" content="{escape(site_url)}/assets/og-monochrome.png">'
        if site_url
        else ""
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="{escape(config.settings.description)}">
  <meta name="theme-color" content="#ffffff">
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
      {_nav_link("Latest research", f"{prefix}index.html", section == "home")}
      {_nav_link("Journal dictionary", f"{prefix}journals/index.html", section == "journals")}
      {_nav_link("Special issues", f"{prefix}special-issues/index.html", section == "calls")}
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
    authors = "; ".join(article.authors)
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
      <p class="authors">{escape(authors) if authors else 'Authors unavailable'}</p>
      <p>{abstract}</p>
      <div class="tag-row">{''.join(f'<span>{escape(item)}</span>' for item in subarea_labels)}</div>
      {'<p class="muted">Volume/issue/pages: ' + escape(', '.join(issue_bits)) + '</p>' if issue_bits else ''}
    </article>
    """


def _special_issue_card(issue: SpecialIssueRecord) -> str:
    opportunity_type = special_issue_opportunity_type(issue.title)
    opportunity_label = (
        "Special-issue proposal" if opportunity_type == "special-issue-proposal" else "Call for papers"
    )
    search = escape(" ".join([issue.title, issue.journal_title, issue.status, issue.confidence, opportunity_label]).lower())
    deadline = f"<span>Deadline: {escape(issue.deadline)}</span>" if issue.deadline else "<span>Deadline unavailable</span>"
    return f"""
    <article class="issue-card" data-search-item data-search-text="{search}">
      <div class="article-meta">
        <span>{escape(issue.journal_title)}</span>
        <span>{escape(opportunity_label)}</span>
        <span>{escape(issue.status)} · {escape(issue.confidence)} confidence</span>
      </div>
      <h3><a href="{escape(issue.source_url)}">{escape(issue.title)}</a></h3>
      <p>{escape(_truncate(issue.raw_snippet, 320))}</p>
      <div class="article-meta">{deadline}<span>Last seen: {escape(issue.last_seen_at or '')}</span></div>
    </article>
    """


def _special_issue_compact(issue: SpecialIssueRecord) -> str:
    opportunity_label = (
        "proposal"
        if special_issue_opportunity_type(issue.title) == "special-issue-proposal"
        else "call for papers"
    )
    return f"""
    <a class="mini-item" href="{escape(issue.source_url)}">
      <strong>{escape(issue.title)}</strong>
      <span>{escape(issue.journal_title)} · {escape(opportunity_label)} · {escape(issue.deadline or 'no deadline')}</span>
    </a>
    """


def _journal_directory_card(
    journal: JournalConfig,
    subarea_by_id: dict[str, SubareaConfig],
    count: int,
    latest: str | None,
    cover_path: str,
    metric: JournalMetric | None,
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
    impact = (
        f'<p class="impact-metric"><strong>2-year mean citedness {metric.value:.2f}</strong>'
        f' <a href="{escape(metric.source_url)}" rel="noreferrer">OpenAlex source</a></p>'
        if metric
        else '<p class="impact-metric"><strong>Impact metric unavailable</strong></p>'
    )
    return f"""
    <article class="journal-card" data-search-item data-search-text="{search}">
      <a class="cover" href="{prefix}journals/{slug}/index.html">{cover_img}</a>
      <div class="journal-card__body">
        <h3><a href="{prefix}journals/{slug}/index.html">{escape(journal.title)}</a></h3>
        <p class="muted">{escape(journal.publisher or 'Publisher unavailable')} &middot; {count} papers &middot; latest {escape(latest or '—')}</p>
        {impact}
        <div class="tag-row">{tags}</div>
      </div>
    </article>"""


def _metric_methodology(config: AppConfig) -> str:
    metadata = config.journal_metric_metadata
    retrieved = escape(str(metadata.get("retrieved_on") or "not yet recorded"))
    citation_year = escape(str(metadata.get("citation_year") or "—"))
    years = metadata.get("publication_years") or []
    publication_years = "–".join(escape(str(year)) for year in years) or "—"
    definition_url = escape(str(metadata.get("definition_url") or "https://developers.openalex.org/api-reference/sources/get-a-single-source"))
    license_url = escape(str(metadata.get("license_url") or "https://help.openalex.org/hc/en-us/articles/24397762024087-Pricing"))
    return f"""
      <aside class="metric-note">
        <p><strong>Impact metric:</strong> OpenAlex 2-year mean citedness, retrieved {retrieved}. For this snapshot it measures {citation_year} citations to works published in {publication_years}. It is not the Clarivate Journal Impact Factor.</p>
        <p class="citation-links"><a href="{definition_url}">Metric definition</a> · <a href="https://doi.org/10.48550/arXiv.2205.01833">OpenAlex scholarly citation</a> · <a href="{license_url}">CC0 data and API terms</a></p>
      </aside>"""


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
    last_run = health.get("last_run") or {}
    special_issue_run = health.get("special_issue_run") or {}
    source_partial = (
        special_issue_run.get("status") == "partial"
        if special_issue_run
        else last_run.get("special_issue_collection") == "partial"
    )
    coverage_limited = health.get("special_issue_coverage_status") == "limited"
    if status == "current" and not source_partial and not coverage_limited:
        return ""
    if status == "empty":
        message = "The collection is ready but does not contain papers yet."
        tone = "neutral"
    elif status == "stale" or health.get("article_status") == "stale":
        age = health.get("article_sync_age_days")
        age_label = f"{age} days ago" if isinstance(age, int) else "on an unknown date"
        message = f"Article collection last ran {age_label}. Some recent papers may be missing."
        tone = "warning"
    elif status == "degraded" and health.get("special_issue_status") in {"empty", "stale"}:
        call_status = health.get("special_issue_status")
        if call_status == "empty":
            message = "Special-issue coverage has no verified findings yet. Article collection is current."
        else:
            age = health.get("special_issue_sync_age_days")
            age_label = f"{age} days ago" if isinstance(age, int) else "on an unknown date"
            message = f"Special-issue collection last produced a verified finding {age_label}. Calls may be incomplete."
        tone = "warning"
    elif source_partial:
        message = "Verified opportunities are available, but some automated publisher sources failed in the latest run."
        tone = "warning"
    elif coverage_limited:
        covered = health.get("special_issue_audit_coverage_count", 0)
        priority = health.get("special_issue_priority_journal_count", 0)
        message = f"Special-issue calls were recently audited for {covered} of {priority} priority journals. Coverage is not yet comprehensive."
        tone = "warning"
    else:
        message = "Collection status needs review."
        tone = "warning"
    status_link = f' <a href="{escape(link)}">View status</a>' if link else ""
    return f'<aside class="status-banner status-banner--{tone}">{escape(message)}{status_link}</aside>'


def _article_limit_notice(total: int, shown: int, prefix: str) -> str:
    if total <= shown:
        return ""
    return (
        '<p class="listing-note">'
        f"Showing the newest {shown:,} of {total:,} papers to keep this page fast. "
        f'<a href="{prefix}data/articles.jsonl.gz">Download the complete dataset</a>.'
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
  --bg:#fff; --ink:#0d0d0d; --muted:#5f5f5f; --line:#d9d9d9; --panel:#fff;
  --line-strong:#0d0d0d; --subtle:#f5f5f5;
  --accent:#0d0d0d; --accent-ink:#0d0d0d; --accent-soft:#f2f2f2;
  --radius:0; --radius-lg:0; --radius-pill:0;
  --shadow:none; --shadow-card:none;
  --font:ui-sans-serif,-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;
  --font-head:var(--font);
  --head-weight:500; --head-tracking:-.035em;
  --header-bg:rgba(255,255,255,.96); --header-blur:blur(12px);
  --band-bg:#fff; --band-border:1px solid var(--line-strong);
  --tag-bg:#f5f5f5; --tag-ink:#242424; --tag-border:#d9d9d9; --input-bg:#fff;
  --nav-ink:#0d0d0d;
}
.eyebrow { font-family:var(--font); font-style:normal; text-transform:uppercase; letter-spacing:.06em; font-weight:600; color:var(--ink); font-size:12px; }
.focus-card { border-top:3px solid var(--accent); }
.lede { font-size:19px; }

* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0;
  font-family: var(--font);
  color: var(--ink);
  background: var(--bg);
  line-height: 1.5;
  overflow-x: hidden;
}
a { color: var(--accent-ink); text-decoration: none; }
a:hover { text-decoration: underline; }
a:focus-visible, input:focus-visible { outline: 3px solid var(--ink); outline-offset: 3px; }
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
  width: 36px; height: 36px; border: 1px solid var(--ink); border-radius: 0;
  background: #fff; color: var(--ink); font-size: 13px; letter-spacing: .04em;
}
.primary-nav { display: flex; gap: 4px; flex-wrap: nowrap; justify-content: flex-end; overflow-x: auto; scrollbar-width: none; }
.primary-nav::-webkit-scrollbar { display: none; }
.primary-nav a {
  color: var(--nav-ink); border: 1px solid transparent;
  border-radius: var(--radius-pill); padding: 7px 10px; font-size: 14px;
  white-space: nowrap;
}
.primary-nav a:hover { background: #fff; border-color: transparent; text-decoration: underline; text-underline-offset: 4px; }
.primary-nav a[aria-current="page"] { background: #fff; border-color: transparent; border-bottom-color: var(--ink); color: var(--ink); }
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
h1 { margin: 0; font-size: clamp(40px, 6vw, 72px); line-height: .98; font-family: var(--font-head); font-weight: var(--head-weight, 500); letter-spacing: var(--head-tracking, 0); }
h2 { margin: 0 0 14px; font-size: 28px; line-height:1.1; font-family: var(--font-head); font-weight:500; letter-spacing:-.02em; }
h3 { margin: 8px 0; font-size: 18px; font-family: var(--font-head); line-height: 1.25; }
.lede { max-width: 760px; color: var(--muted); font-size: 18px; }
.hero-actions { display: flex; flex-wrap: wrap; gap: 9px; margin-top: 22px; }
.hero-link {
  display: inline-flex; align-items: center; border: 1px solid var(--line);
  background: var(--panel); color: var(--ink); border-radius: var(--radius-pill);
  padding: 9px 14px; font-weight: 600; box-shadow: none;
}
.hero-link:hover { border-color: var(--ink); color: var(--ink); text-decoration: underline; transform: none; }
.metrics { display: grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap: 10px; align-content: end; }
.metrics div, .row-stat { border: 0; border-top: 1px solid var(--line-strong); background: #fff; border-radius: 0; padding: 16px 0; }
.metrics strong, .row-stat strong { display: block; font-size: clamp(20px, 2vw, 28px); line-height: 1.05; font-family: var(--font-head); text-wrap: balance; }
.metrics span, .row-stat span, .muted { color: var(--muted); font-size: 13px; }
.trust-strip {
  display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 0;
  margin: 16px 0 0; border: 1px solid var(--line); border-radius: var(--radius);
  background: #fff; box-shadow: none;
}
.trust-strip span { padding: 13px 15px; color: var(--muted); font-size: 12px; }
.trust-strip span + span { border-left: 1px solid var(--line); }
.trust-strip strong { display: block; margin-bottom: 2px; color: var(--ink); font-size: 13px; }
.cover-showcase { margin-top: 28px; padding: 0 0 24px; border-bottom: 1px solid var(--line); }
.cover-showcase__grid { display: grid; grid-template-columns: repeat(8, minmax(0, 1fr)); gap: clamp(10px, 1.5vw, 18px); }
.cover-showcase__item { display: grid; grid-template-rows: auto auto 1fr; gap: 7px; min-width: 0; color: var(--ink); }
.cover-showcase__item:hover { text-decoration: none; }
.cover-showcase__item img {
  width: 100%; aspect-ratio: 3 / 4; object-fit: cover; display: block;
  border: 1px solid var(--line); background: var(--band-bg); filter: grayscale(100%);
  transition: filter .16s ease, border-color .16s ease;
}
.cover-showcase__item:hover img { filter: grayscale(0); border-color: var(--ink); }
.cover-showcase__item strong { font-size: 12px; line-height: 1.25; }
.cover-showcase__item span { color: var(--muted); font-size: 11px; line-height: 1.25; }
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
.focus-card--political-communication, .focus-card--health-communication, .focus-card--methods { background: #fff; }
.section-grid { display: grid; grid-template-columns: minmax(0,1fr) 340px; gap: 28px; margin-top: 30px; }
.section-heading { display: flex; justify-content: space-between; gap: 16px; align-items: end; margin-bottom: 14px; }
.section-kicker { margin: 0 0 4px; color: var(--muted); font-size: 12px; font-weight: 800; text-transform: uppercase; letter-spacing: .06em; }
.search-label { display: grid; gap: 5px; width: min(100%, 440px); color: var(--muted); font-size: 12px; font-weight: 700; }
.search-label--page { margin-top: 18px; }
.search-input { width: 100%; border: 1px solid #8c8c8c; border-radius: 0; padding: 11px 15px; background: var(--input-bg); color: var(--ink); font: inherit; font-weight: 400; box-shadow: none; }
.search-input::placeholder { color: #6b6b6b; }
.search-status { min-height: 1.3em; margin: 5px 0 0; color: var(--muted); font-size: 12px; }
.search-empty { margin-top: 18px; border: 1px dashed var(--line); border-radius: var(--radius); padding: 22px; text-align: center; color: var(--muted); background: var(--panel); }
[hidden] { display: none !important; }
.article-list, .issue-section, .directory { display: grid; gap: 12px; }
.article-card, .issue-card, .journal-row, .side-panel { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); box-shadow: var(--shadow-card); }
.article-card, .issue-card { padding: 18px; transition: border-color .16s ease, box-shadow .16s ease, transform .16s ease; }
.article-card:hover, .issue-card:hover, .journal-row:hover { border-color: var(--ink); box-shadow: none; transform: none; }
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
  padding: 10px; border: 1px solid var(--line); border-radius: 0;
  background: #fff; box-shadow: none;
}
.journal-cover-large img { width: 100%; max-height: 184px; object-fit: cover; display: block; border-radius: 4px; }
.journal-row { display: grid; grid-template-columns: minmax(0,1fr) 140px; gap: 20px; align-items: center; padding: 16px 18px; }
.row-stat { text-align: right; }
.action-row { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 14px; }
.button-link { display: inline-flex; align-items: center; border: 1px solid var(--ink); border-radius: 0; padding: 9px 14px; color: #fff; background: var(--ink); font-weight: 600; }
.button-link:hover { text-decoration: none; background: #fff; color: var(--ink); border-color: var(--ink); }
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
.journal-card:hover { border-color: var(--ink); box-shadow: none; transform: none; }
.journal-card .cover { display: block; }
.journal-card .cover img { width: 88px; height: 117px; object-fit: cover; border: 1px solid var(--line); display: block; background: var(--band-bg); border-radius: 0; box-shadow: none; }
.journal-card__body { min-width: 0; }
.journal-card__body h3 { margin: 0 0 4px; font-size: 16px; line-height: 1.2; }
.journal-card__body .muted { margin: 0; font-size: 12px; }
.impact-metric { margin: 10px 0 0; font-size: 12px; line-height: 1.35; }
.impact-metric strong { display: block; color: var(--ink); }
.impact-metric a { color: var(--muted); text-decoration: underline; text-underline-offset: 2px; }
.metric-note { max-width: 900px; margin: 18px 0; padding: 14px 16px; border: 1px solid var(--line); border-left: 3px solid var(--ink); background: #fff; }
.metric-note p { margin: 0; font-size: 13px; }
.metric-note p + p { margin-top: 7px; }
.metric-note .citation-links a { text-decoration: underline; text-underline-offset: 3px; }
.paper-mini { list-style: none; margin: 10px 0 0; padding: 0; display: grid; gap: 6px; min-width: 0; }
.paper-mini li { display: flex; justify-content: space-between; gap: 10px; font-size: 13px; align-items: baseline; min-width: 0; }
.paper-mini li a { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.paper-mini li span { flex: 0 0 auto; color: var(--muted); white-space: nowrap; font-size: 12px; }
.group-more { display: inline-block; margin-top: 12px; font-size: 13px; font-weight: 700; }
.area-nav { display: flex; flex-wrap: wrap; justify-content: center; gap: 8px; margin: 0 0 20px; }
.area-nav a { border: 1px solid var(--line); border-radius: var(--radius-pill); padding: 5px 11px; font-size: 13px; color: var(--ink); }
.area-nav a:hover { border-color: var(--accent); color: var(--accent-ink); text-decoration: none; }
.status-banner { margin: 16px 0 0; padding: 13px 16px; border: 1px solid var(--line); border-radius: var(--radius); background: var(--panel); }
.status-banner--warning { border: 1px solid var(--ink); border-left-width: 3px; background: var(--subtle); }
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
  .cover-showcase__grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }
  .site-footer { grid-template-columns: 1fr 1fr; }
  .footer-links { grid-column: 1 / -1; }
}
@media (max-width: 680px) {
  .site-header { grid-template-columns: 1fr; align-items: start; }
  .primary-nav { justify-content: flex-start; max-width: 100%; padding-bottom: 2px; flex-wrap: wrap; }
  .primary-nav a { padding: 5px 8px; }
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
  .cover-showcase { overflow: hidden; }
  .cover-showcase__grid {
    display: flex; overflow-x: auto; gap: 12px; padding-bottom: 8px;
    scroll-snap-type: x proximity; scrollbar-width: thin;
  }
  .cover-showcase__item { flex: 0 0 118px; scroll-snap-align: start; }
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
