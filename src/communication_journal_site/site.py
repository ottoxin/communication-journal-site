from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from html import escape
from pathlib import Path

from .models import AppConfig, ArticleRecord, JournalConfig, SpecialIssueRecord, SubareaConfig
from .normalize import slugify
from .storage import StateStore


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
    for week_key, week_articles in _articles_by_week(articles).items():
        written.append(
            _write(
                output_path / "weeks" / week_key / "index.html",
                _week_page(config, week_key, week_articles, journal_by_id),
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
    body = f"""
    <section class="overview-band">
      <div>
        <p class="eyebrow">Weekly communication research monitor</p>
        <h1>{escape(config.settings.title)}</h1>
        <p class="lede">{escape(config.settings.description)}</p>
      </div>
      <div class="metrics" aria-label="Collection metrics">
        {_metric("Journals", str(len([journal for journal in config.journals if journal.active])))}
        {_metric("Papers", str(len(articles)))}
        {_metric("Special issues", str(len(active_special)))}
      </div>
    </section>
    <section class="section-grid">
      <div>
        <div class="section-heading">
          <h2>Latest Papers</h2>
          <input class="search-input" data-search-input placeholder="Filter papers" aria-label="Filter papers">
        </div>
        <div class="article-list" data-search-list>
          {''.join(_article_card(article, journal_by_id[article.journal_id], prefix="") for article in latest_articles)}
        </div>
      </div>
      <aside class="side-panel">
        <h2>Subareas</h2>
        <div class="link-list">
          {''.join(_subarea_link(subarea_by_id[item], subarea_counts[item], prefix="") for item in subarea_counts)}
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
      {''.join(_journal_row(journal, article_counts[journal.id], latest.get(journal.id), prefix="../") for journal in config.journals)}
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
      {''.join(_article_card(article, journal_by_id[article.journal_id], prefix="../../") for article in articles) or '<p class="empty">No papers collected for this subarea yet.</p>'}
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
      {''.join(_article_card(article, journal, prefix="../../") for article in articles) or '<p class="empty">No papers collected for this journal yet.</p>'}
    </section>
    """
    return _layout(config, journal.title, body, prefix="../../")


def _week_page(
    config: AppConfig,
    week_key: str,
    articles: list[ArticleRecord],
    journal_by_id: dict[str, JournalConfig],
) -> str:
    body = f"""
    <section class="page-header">
      <p class="eyebrow">Weekly archive</p>
      <h1>Digest Week {escape(week_key)}</h1>
      <p>Papers published in the complete week before this Monday digest date.</p>
    </section>
    <section class="article-list">
      {''.join(_article_card(article, journal_by_id[article.journal_id], prefix="../../") for article in articles)}
    </section>
    """
    return _layout(config, f"Week {week_key}", body, prefix="../../")


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
      <a href="{prefix}special-issues/index.html">Special Issues</a>
      <a href="{prefix}data/latest.json">Data</a>
    </nav>
  </header>
  <main>
    {body}
  </main>
  <footer class="site-footer">
    <span>Generated from systematic collection code.</span>
    <span>{escape(config.settings.timezone)}</span>
  </footer>
  {script_tag}
</body>
</html>
"""


def _article_card(article: ArticleRecord, journal: JournalConfig, prefix: str) -> str:
    link = article.canonical_url or (f"https://doi.org/{article.doi}" if article.doi else "")
    search = escape(" ".join([article.title, article.journal_title, " ".join(article.authors), " ".join(journal.subareas)]).lower())
    authors = "; ".join(article.authors[:6])
    more_authors = " et al." if len(article.authors) > 6 else ""
    issue_bits = [part for part in [article.volume, article.issue, article.pages] if part]
    return f"""
    <article class="article-card" data-search-item data-search-text="{search}">
      <div class="article-meta">
        <a href="{prefix}journals/{slugify(journal.title)}/index.html">{escape(article.journal_title)}</a>
        <span>{escape(article.published_date)}</span>
      </div>
      <h3>{_maybe_link(escape(article.title), link)}</h3>
      <p class="authors">{escape(authors + more_authors) if authors else 'Authors unavailable'}</p>
      <p>{escape((article.abstract or 'Abstract unavailable.')[:420])}</p>
      <div class="tag-row">{''.join(f'<span>{escape(item)}</span>' for item in journal.subareas)}</div>
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
      <p>{escape(issue.raw_snippet[:320])}</p>
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


def _journal_row(journal: JournalConfig, count: int, latest: str | None, prefix: str) -> str:
    search = escape(" ".join([journal.title, journal.publisher, " ".join(journal.subareas)]).lower())
    return f"""
    <article class="journal-row" data-search-item data-search-text="{search}">
      <div>
        <h2><a href="{prefix}journals/{slugify(journal.title)}/index.html">{escape(journal.title)}</a></h2>
        <p>{escape(journal.publisher or 'Publisher unavailable')} · ISSN {escape(', '.join(journal.issns))}</p>
        <div class="tag-row">{''.join(f'<span>{escape(item)}</span>' for item in journal.subareas)}</div>
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


def _metric(label: str, value: str) -> str:
    return f"<div><strong>{escape(value)}</strong><span>{escape(label)}</span></div>"


def _external_link(label: str, url: str) -> str:
    if not url:
        return ""
    return f'<a class="button-link" href="{escape(url)}">{escape(label)}</a>'


def _maybe_link(label: str, url: str) -> str:
    return f'<a href="{escape(url)}">{label}</a>' if url else label


def _articles_by_week(articles: list[ArticleRecord]) -> dict[str, list[ArticleRecord]]:
    grouped: dict[str, list[ArticleRecord]] = defaultdict(list)
    for article in articles:
        try:
            published = date.fromisoformat(article.published_date)
        except ValueError:
            continue
        grouped[_digest_date_for_published(published).isoformat()].append(article)
    return dict(sorted(grouped.items(), reverse=True))


def _digest_date_for_published(published: date) -> date:
    days_until_monday = (7 - published.weekday()) % 7
    if days_until_monday == 0:
        days_until_monday = 7
    return published + timedelta(days=days_until_monday)


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _styles() -> str:
    return """
:root {
  --bg: #f7f8f5;
  --ink: #1f2933;
  --muted: #667085;
  --line: #d8ddd2;
  --panel: #ffffff;
  --accent: #0f766e;
  --accent-soft: #dff3ee;
  --accent-dark: #0b4f49;
  --warn: #8a4b16;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  color: var(--ink);
  background: var(--bg);
  line-height: 1.5;
}
a { color: var(--accent-dark); text-decoration: none; }
a:hover { text-decoration: underline; }
.site-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 24px;
  padding: 18px clamp(18px, 4vw, 48px);
  border-bottom: 1px solid var(--line);
  background: rgba(255,255,255,.92);
  position: sticky;
  top: 0;
  z-index: 2;
}
.brand { font-weight: 750; color: var(--ink); }
nav { display: flex; gap: 16px; flex-wrap: wrap; }
main { width: min(1180px, calc(100% - 32px)); margin: 0 auto; padding: 34px 0 56px; }
.overview-band {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(260px, 390px);
  gap: 32px;
  align-items: end;
  padding: 26px 0 34px;
  border-bottom: 1px solid var(--line);
}
.eyebrow {
  margin: 0 0 8px;
  text-transform: uppercase;
  font-size: 12px;
  font-weight: 760;
  color: var(--accent-dark);
}
h1 { margin: 0; font-size: clamp(32px, 5vw, 58px); line-height: 1.02; letter-spacing: 0; }
h2 { margin: 0 0 14px; font-size: 21px; letter-spacing: 0; }
h3 { margin: 8px 0; font-size: 18px; letter-spacing: 0; }
.lede { max-width: 760px; color: var(--muted); font-size: 18px; }
.metrics {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
}
.metrics div, .row-stat {
  border: 1px solid var(--line);
  background: var(--panel);
  border-radius: 8px;
  padding: 14px;
}
.metrics strong, .row-stat strong { display: block; font-size: 24px; }
.metrics span, .row-stat span, .muted { color: var(--muted); font-size: 13px; }
.section-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 330px;
  gap: 28px;
  margin-top: 28px;
}
.section-heading {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: center;
  margin-bottom: 14px;
}
.search-input {
  width: min(100%, 320px);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 10px 12px;
  background: #fff;
  color: var(--ink);
  font: inherit;
}
.article-list, .issue-section, .directory { display: grid; gap: 12px; }
.article-card, .issue-card, .journal-row, .side-panel {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
}
.article-card, .issue-card { padding: 18px; }
.article-card p, .issue-card p, .journal-row p { margin: 8px 0; color: var(--muted); }
.article-meta {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  color: var(--muted);
  font-size: 13px;
}
.authors { color: var(--ink) !important; font-size: 14px; }
.tag-row { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 10px; }
.tag-row span {
  display: inline-flex;
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 3px 8px;
  font-size: 12px;
  color: var(--accent-dark);
  background: var(--accent-soft);
}
.side-panel { padding: 18px; align-self: start; }
.link-list { display: grid; gap: 8px; margin-bottom: 24px; }
.link-list a, .mini-item {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  border-bottom: 1px solid var(--line);
  padding: 9px 0;
}
.mini-item { display: grid; color: var(--ink); }
.mini-item span { color: var(--muted); font-size: 13px; }
.page-header {
  padding: 22px 0 24px;
  border-bottom: 1px solid var(--line);
  margin-bottom: 24px;
}
.page-header p { max-width: 820px; color: var(--muted); }
.journal-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 140px;
  gap: 20px;
  align-items: center;
  padding: 16px 18px;
}
.row-stat { text-align: right; }
.action-row { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 14px; }
.button-link {
  display: inline-flex;
  align-items: center;
  border: 1px solid var(--accent);
  border-radius: 8px;
  padding: 8px 10px;
  color: var(--accent-dark);
  background: var(--accent-soft);
}
.empty {
  border: 1px dashed var(--line);
  padding: 18px;
  border-radius: 8px;
  color: var(--muted);
}
.site-footer {
  width: min(1180px, calc(100% - 32px));
  margin: 0 auto;
  padding: 18px 0 28px;
  border-top: 1px solid var(--line);
  display: flex;
  justify-content: space-between;
  gap: 16px;
  color: var(--muted);
  font-size: 13px;
}
@media (max-width: 860px) {
  .overview-band, .section-grid, .journal-row { grid-template-columns: 1fr; }
  .metrics { grid-template-columns: 1fr 1fr 1fr; }
  .section-heading { align-items: stretch; flex-direction: column; }
  .search-input { width: 100%; }
  .row-stat { text-align: left; }
}
@media (max-width: 560px) {
  .site-header { align-items: flex-start; flex-direction: column; }
  .metrics { grid-template-columns: 1fr; }
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
