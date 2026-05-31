# Communication Journal Site

`communication-journal-site` is a standalone static-site generator for weekly communication-journal paper collection. It intentionally lives outside the `weekly-journal-digest` repository and does not depend on, edit, or replace that project.

The first version is deterministic and non-AI:

- journal metadata comes from YAML registry files;
- paper collection uses Crossref, with optional OpenAlex abstract fallback;
- papers inherit subarea tags from their journal;
- special-issue monitoring scrapes configured top-journal pages and stores source-linked findings;
- public pages and JSON exports are generated under `site/`.

## Quick Start

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"

communication-journal-site audit-journals
communication-journal-site collect-papers --end-date 2026-05-31
communication-journal-site collect-special-issues
communication-journal-site export-public-data
communication-journal-site build-site
```

Open `site/index.html` in a browser after building.

## Weekly Run

```bash
communication-journal-site run-weekly --digest-date 2026-06-01
```

The weekly command:

1. collects the 28-day paper window ending on the digest date;
2. screens configured special-issue pages;
3. exports public JSON;
4. builds the static website.

## Registry Strategy

`config/journals.yaml` contains a broad communication-journal seed registry with ISSNs, publisher/homepage metadata, journal-level subareas, and collection status. The registry is deliberately editable and auditable.

For larger coverage, place a downloaded Communication-category source list in `data/audit/` and run:

```bash
communication-journal-site audit-journals --source-list data/audit/scimago_communication.csv
```

The command writes `data/audit/journal_registry_audit.csv` and marks registry issues such as missing ISSNs, duplicate ISSNs, disabled journals, and source-list titles that are not yet present in the local registry.

## Configuration

- `config/journals.yaml`: site settings and journal registry.
- `config/subareas.yaml`: subarea labels and descriptions.
- `config/special_issue_sources.yaml`: pages to scrape for calls/special issues.

Local runtime state is stored in `.state/site.db`; generated pages are stored in `site/`. Both are ignored by git.

## Design Notes

- V1 has no login, admin UI, AI summaries, or AI tagging.
- Article subareas are journal-based only. If a journal belongs to multiple subareas, every paper appears in each configured subarea.
- Special-issue scraping is best-effort and source-linked. Ambiguous findings are stored with lower confidence instead of being silently dropped.

