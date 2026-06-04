from __future__ import annotations

from collections import defaultdict
from html import escape
from pathlib import Path

from .models import AppConfig, ArticleRecord, JournalConfig, SpecialIssueRecord, SubareaConfig
from .normalize import clean_abstract, slugify
from .storage import StateStore


FEATURED_SUBAREA_IDS = ("political-communication", "health-communication", "methods")


def build_site(config: AppConfig, store: StateStore, output_dir: str | Path) -> list[Path]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    assets_dir = output_path / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    articles = store.get_all_articles()
    special_issues = store.get_special_issues()
    journal_by_id = config.journal_by_id
    subarea_by_id = config.subarea_by_id
    written = [
        _write(output_path / "assets" / "styles.css", _styles()),
        _write(output_path / "assets" / "search.js", _search_js()),
        _write(output_path / "index.html", _home_page(config, articles, special_issues, journal_by_id, subarea_by_id)),
        _write(output_path / "journals" / "index.html", _journals_page(config, articles)),
        _write(output_path / "special-issues" / "index.html", _special_issues_page(config, special_issues)),
    ]
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
                _journal_page(config, journal, journal_articles, subarea_by_id),
            )
        )
    return written


def _home_page(
    config: AppConfig,
    articles: list[ArticleRecord],
    special_issues: list[SpecialIssueRecord],
    journal_by_id: dict[str, JournalConfig],
    subarea_by_id: dict[str, SubareaConfig],
) -> str:
    latest_articles = articles[:60]
    active_special = [issue for issue in special_issues if issue.status == "active"][:8]
    subarea_counts = {
        subarea.id: sum(1 for article in articles if subarea.id in journal_by_id[article.journal_id].subareas)
        for subarea in config.subareas
    }
    featured_subareas = [
        subarea_by_id[subarea_id]
        for subarea_id in FEATURED_SUBAREA_IDS
        if subarea_id in subarea_by_id
    ]
    remaining_subareas = [
        subarea
        for subarea in config.subareas
        if subarea.id not in FEATURED_SUBAREA_IDS
    ]
    latest_published = max((article.published_date for article in articles), default="—")
    body = f"""
    <section class="overview-band">
      <div>
        <p class="eyebrow">Weekly communication research monitor</p>
        <h1>{escape(config.settings.title)}</h1>
        <p class="lede">{escape(config.settings.description)}</p>
        <div class="hero-actions" aria-label="Featured subarea pages">
          {''.join(_hero_link(subarea, prefix="") for subarea in featured_subareas)}
        </div>
      </div>
      <div class="metrics" aria-label="Collection metrics">
        {_metric("Journals", str(len([journal for journal in config.journals if journal.active])))}
        {_metric("Papers", str(len(articles)))}
        {_metric("Latest paper", latest_published)}
        {_metric("Active calls", str(len(active_special)))}
      </div>
    </section>
    <section class="focus-strip" aria-label="Featured communication subareas">
      {''.join(_featured_subarea_card(subarea, subarea_counts[subarea.id], prefix="") for subarea in featured_subareas)}
    </section>
    <section class="section-grid">
      <div>
        <div class="section-heading">
          <div>
            <p class="section-kicker">Collection stream</p>
            <h2>Latest Papers</h2>
          </div>
          <input class="search-input" data-search-input placeholder="Filter papers" aria-label="Filter papers">
        </div>
        <div class="article-list" data-search-list>
          {''.join(_article_card(article, journal_by_id[article.journal_id], subarea_by_id, prefix="") for article in latest_articles) or _empty_latest_state()}
        </div>
      </div>
      <aside class="side-panel">
        <h2>More Subareas</h2>
        <div class="link-list">
          {''.join(_subarea_link(subarea, subarea_counts[subarea.id], prefix="") for subarea in remaining_subareas)}
        </div>
        <h2>Active Special Issues</h2>
        <div class="mini-list">
          {''.join(_special_issue_compact(issue) for issue in active_special) or '<p class="muted">No active calls collected yet.</p>'}
        </div>
      </aside>
    </section>
    """
    return _layout(config, "Home", body, prefix="", script=True)


def _journals_page(config: AppConfig, articles: list[ArticleRecord]) -> str:
    article_counts: dict[str, int] = defaultdict(int)
    latest: dict[str, str] = {}
    for article in articles:
        article_counts[article.journal_id] += 1
        latest[article.journal_id] = max(latest.get(article.journal_id, ""), article.published_date)
    body = f"""
    <section class="page-header">
      <p class="eyebrow">Registry</p>
      <h1>Journal Directory</h1>
      <p>{escape(config.settings.registry_source_note)}</p>
      <input class="search-input" data-search-input placeholder="Filter journals" aria-label="Filter journals">
    </section>
    <section class="directory" data-search-list>
      {''.join(_journal_row(journal, config.subarea_by_id, article_counts[journal.id], latest.get(journal.id), prefix="../") for journal in config.journals)}
    </section>
    """
    return _layout(config, "Journal Directory", body, prefix="../", script=True)


def _subarea_page(
    config: AppConfig,
    subarea: SubareaConfig,
    articles: list[ArticleRecord],
    journal_by_id: dict[str, JournalConfig],
) -> str:
    body = f"""
    <section class="page-header">
      <p class="eyebrow">Subarea</p>
      <h1>{escape(subarea.label)}</h1>
      <p>{escape(subarea.description)}</p>
      <input class="search-input" data-search-input placeholder="Filter papers" aria-label="Filter papers">
    </section>
    <section class="article-list" data-search-list>
      {''.join(_article_card(article, journal_by_id[article.journal_id], config.subarea_by_id, prefix="../../") for article in articles) or '<p class="empty">No papers collected for this subarea yet.</p>'}
    </section>
    """
    return _layout(config, subarea.label, body, prefix="../../", script=True)


def _journal_page(
    config: AppConfig,
    journal: JournalConfig,
    articles: list[ArticleRecord],
    subarea_by_id: dict[str, SubareaConfig],
) -> str:
    subarea_labels = [subarea_by_id[item].label for item in journal.subareas if item in subarea_by_id]
    body = f"""
    <section class="page-header">
      <p class="eyebrow">Journal</p>
      <h1>{escape(journal.title)}</h1>
      <p>{escape(journal.publisher or 'Publisher unavailable')} · ISSN {escape(', '.join(journal.issns))}</p>
      <div class="tag-row">{''.join(f'<span>{escape(label)}</span>' for label in subarea_labels)}</div>
      <div class="action-row">
        {_external_link('Homepage', journal.homepage_url)}
        {_external_link('Latest articles', journal.latest_articles_url)}
      </div>
    </section>
    <section class="article-list">
      {''.join(_article_card(article, journal, subarea_by_id, prefix="../../") for article in articles) or '<p class="empty">No papers collected for this journal yet.</p>'}
    </section>
    """
    return _layout(config, journal.title, body, prefix="../../")


def _special_issues_page(config: AppConfig, issues: list[SpecialIssueRecord]) -> str:
    active = [issue for issue in issues if issue.status == "active"]
    closed = [issue for issue in issues if issue.status != "active"]
    body = f"""
    <section class="page-header">
      <p class="eyebrow">Calls and special issues</p>
      <h1>Special-Issue Monitor</h1>
      <p>Deterministic screening of configured top-journal publisher pages.</p>
      <input class="search-input" data-search-input placeholder="Filter calls" aria-label="Filter special issues">
    </section>
    <section class="issue-section" data-search-list>
      <h2>Active</h2>
      {''.join(_special_issue_card(issue) for issue in active) or '<p class="empty">No active calls collected yet.</p>'}
      <h2>Closed or Unknown</h2>
      {''.join(_special_issue_card(issue) for issue in closed) or '<p class="empty">No closed calls collected yet.</p>'}
    </section>
    """
    return _layout(config, "Special Issues", body, prefix="../", script=True)


def _layout(config: AppConfig, title: str, body: str, prefix: str, script: bool = False) -> str:
    script_tag = f'<script src="{prefix}assets/search.js"></script>' if script else ""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)} · {escape(config.settings.title)}</title>
  <link rel="stylesheet" href="{prefix}assets/styles.css">
</head>
<body>
  <header class="site-header">
    <a class="brand" href="{prefix}index.html">{escape(config.settings.title)}</a>
    <nav>
      <a href="{prefix}journals/index.html">Journals</a>
      <a href="{prefix}subareas/political-communication/index.html">Political</a>
      <a href="{prefix}subareas/health-communication/index.html">Health</a>
      <a href="{prefix}subareas/methods/index.html">Methods</a>
      <a href="{prefix}special-issues/index.html">Calls</a>
    </nav>
  </header>
  <main>
    {body}
  </main>
  <footer class="site-footer">
    <span>Generated from systematic collection code.</span>
    <span><a href="{prefix}data/latest.json">Data export</a> · {escape(config.settings.timezone)}</span>
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
    abstract_text = clean_abstract(article.abstract)
    abstract = escape(_truncate(abstract_text, 420)) if abstract_text else "Abstract unavailable."
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


def _journal_row(
    journal: JournalConfig,
    subarea_by_id: dict[str, SubareaConfig],
    count: int,
    latest: str | None,
    prefix: str,
) -> str:
    subarea_labels = [_subarea_label(subarea_id, subarea_by_id) for subarea_id in journal.subareas]
    search = escape(" ".join([journal.title, journal.publisher, " ".join(subarea_labels)]).lower())
    return f"""
    <article class="journal-row" data-search-item data-search-text="{search}">
      <div>
        <h2><a href="{prefix}journals/{slugify(journal.title)}/index.html">{escape(journal.title)}</a></h2>
        <p>{escape(journal.publisher or 'Publisher unavailable')} · ISSN {escape(', '.join(journal.issns))}</p>
        <div class="tag-row">{''.join(f'<span>{escape(item)}</span>' for item in subarea_labels)}</div>
      </div>
      <div class="row-stat"><strong>{count}</strong><span>{escape(latest or 'No papers yet')}</span></div>
    </article>
    """


def _subarea_link(subarea: SubareaConfig, count: int, prefix: str) -> str:
    return f"""
    <a href="{prefix}subareas/{subarea.id}/index.html">
      <span>{escape(subarea.label)}</span>
      <strong>{count}</strong>
    </a>
    """


def _hero_link(subarea: SubareaConfig, prefix: str) -> str:
    return (
        f'<a class="hero-link" href="{prefix}subareas/{subarea.id}/index.html">'
        f'{escape(_short_subarea_label(subarea))}</a>'
    )


def _featured_subarea_card(subarea: SubareaConfig, count: int, prefix: str) -> str:
    return f"""
    <a class="focus-card focus-card--{escape(subarea.id)}" href="{prefix}subareas/{subarea.id}/index.html">
      <span class="focus-card__label">{escape(_short_subarea_label(subarea))}</span>
      <strong>{count}</strong>
      <span>{escape(subarea.description)}</span>
    </a>
    """


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


def _metric(label: str, value: str) -> str:
    return f"<div><strong>{escape(value)}</strong><span>{escape(label)}</span></div>"


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


def _styles() -> str:
    return """
:root {
  --bg:#ffffff; --ink:#1b1714; --muted:#5c554e; --line:#e6ded1; --panel:#ffffff;
  --accent:#8a2b2b; --accent-ink:#8a2b2b; --accent-soft:#f6ebeb;
  --radius:4px; --radius-lg:6px; --radius-pill:4px;
  --shadow:none; --shadow-card:none;
  --font:Charter,Georgia,'Iowan Old Style','Times New Roman',serif;
  --font-head:Charter,Georgia,'Iowan Old Style',serif;
  --head-weight:700; --head-tracking:-0.01em;
  --header-bg:#ffffff; --header-blur:none;
  --band-bg:#faf6ef; --band-border:1px solid var(--line);
  --tag-bg:transparent; --tag-ink:#8a2b2b; --tag-border:#e0cfcf; --input-bg:#fff;
  --focus-1:#faf6ef; --focus-2:#faf6ef; --focus-3:#faf6ef; --nav-ink:#1b1714;
}
.eyebrow { font-family:var(--font); font-style:italic; text-transform:none; letter-spacing:0; font-weight:400; color:var(--accent); font-size:15px; }
.focus-card { border-top:3px solid var(--accent); }
.lede { font-size:19px; }

* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: var(--font);
  color: var(--ink);
  background: var(--bg);
  line-height: 1.5;
}
a { color: var(--accent-ink); text-decoration: none; }
a:hover { text-decoration: underline; }
.site-header {
  display: flex; justify-content: space-between; align-items: center;
  gap: 24px; padding: 14px clamp(18px, 4vw, 48px);
  border-bottom: 1px solid var(--line);
  background: var(--header-bg);
  position: sticky; top: 0; z-index: 2;
  backdrop-filter: var(--header-blur, none);
}
.brand {
  font-weight: 800; color: var(--ink); display: inline-flex;
  align-items: center; gap: 10px; font-family: var(--font-head);
}
nav { display: flex; gap: 7px; flex-wrap: wrap; justify-content: flex-end; }
nav a {
  color: var(--nav-ink); border: 1px solid transparent;
  border-radius: var(--radius-pill); padding: 7px 12px; font-size: 14px;
}
nav a:hover { background: var(--panel); border-color: var(--line); text-decoration: none; }
main { width: min(1180px, calc(100% - 32px)); margin: 0 auto; padding: 30px 0 56px; }
.overview-band {
  display: grid; grid-template-columns: minmax(0, 1fr) minmax(280px, 420px);
  gap: 32px; align-items: stretch;
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
  padding: 9px 13px; font-weight: 700;
}
.hero-link:hover { border-color: var(--accent); color: var(--accent-ink); text-decoration: none; }
.metrics { display: grid; grid-template-columns: 1fr; gap: 10px; align-content: end; }
.metrics div, .row-stat { border: 1px solid var(--line); background: var(--panel); border-radius: var(--radius); padding: 16px; }
.metrics strong, .row-stat strong { display: block; font-size: 28px; line-height: 1; font-family: var(--font-head); }
.metrics span, .row-stat span, .muted { color: var(--muted); font-size: 13px; }
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
.search-input { width: min(100%, 320px); border: 1px solid var(--line); border-radius: var(--radius); padding: 10px 12px; background: var(--input-bg); color: var(--ink); font: inherit; }
.article-list, .issue-section, .directory { display: grid; gap: 12px; }
.article-card, .issue-card, .journal-row, .side-panel { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); box-shadow: var(--shadow-card); }
.article-card, .issue-card { padding: 18px; }
.article-card:hover, .issue-card:hover, .journal-row:hover { border-color: var(--accent); }
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
.journal-row { display: grid; grid-template-columns: minmax(0,1fr) 140px; gap: 20px; align-items: center; padding: 16px 18px; }
.row-stat { text-align: right; }
.action-row { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 14px; }
.button-link { display: inline-flex; align-items: center; border: 1px solid var(--accent); border-radius: var(--radius); padding: 8px 12px; color: var(--accent-ink); background: var(--accent-soft); }
.empty, .empty-state { border: 1px dashed var(--line); padding: 20px; border-radius: var(--radius); color: var(--muted); background: var(--panel); }
.empty-state code { display: inline-block; margin-top: 8px; max-width: 100%; overflow-wrap: anywhere; border: 1px solid var(--line); background: var(--bg); border-radius: var(--radius); padding: 8px 10px; color: var(--accent-ink); font-family: ui-monospace, Menlo, monospace; }
.site-footer { width: min(1180px, calc(100% - 32px)); margin: 0 auto; padding: 18px 0 28px; border-top: 1px solid var(--line); display: flex; justify-content: space-between; gap: 16px; color: var(--muted); font-size: 13px; }
@media (max-width: 860px) {
  .overview-band, .section-grid, .journal-row { grid-template-columns: 1fr; }
  .metrics { grid-template-columns: 1fr 1fr 1fr; }
  .section-heading { align-items: stretch; flex-direction: column; }
  .search-input { width: 100%; }
  .row-stat { text-align: left; }
  .side-panel { position: static; }
}
@media (max-width: 560px) {
  .site-header { align-items: flex-start; flex-direction: column; }
  .focus-strip { grid-template-columns: 1fr; }
  .metrics { grid-template-columns: 1fr; }
  nav { justify-content: flex-start; }
  h1 { font-size: 34px; }
}
"""


def _search_js() -> str:
    return """
document.querySelectorAll('[data-search-input]').forEach((input) => {
  const list = input.closest('main').querySelector('[data-search-list]');
  if (!list) return;
  input.addEventListener('input', () => {
    const query = input.value.trim().toLowerCase();
    list.querySelectorAll('[data-search-item]').forEach((item) => {
      const text = item.getAttribute('data-search-text') || item.textContent.toLowerCase();
      item.hidden = query.length > 0 && !text.includes(query);
    });
  });
});
"""
