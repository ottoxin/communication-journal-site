# Communication Journal Monitor

`communication-journal-site` is a reusable weekly collection and static-publishing pipeline for communication research. It collects journal articles, monitors calls for special issues, keeps local history in SQLite, and builds a searchable website plus public JSON exports.

The pipeline is deterministic and non-AI:

- journal metadata comes from YAML registry files;
- paper collection uses Crossref, with optional OpenAlex abstract fallback;
- papers inherit subarea tags from their journal;
- special-issue monitoring screens configured publisher pages and stores source-linked findings with lifecycle status;
- public pages and JSON exports are generated under `site/`.

## Quick Start

```bash
make setup
make update DATE=2026-06-20
make serve
```

Open <http://localhost:8000> while the server is running.

## Weekly Run

```bash
make update                        # defaults to today
make update DATE=2026-06-20
make update OPENALEX=0             # faster run; fewer abstracts may publish
./scripts/run_weekly.sh            # suitable for cron/systemd
```

The weekly command:

1. collects the 28-day paper window ending on the digest date;
2. screens configured special-issue pages;
3. exports public JSON;
4. writes public health/freshness metadata;
5. builds the static website.

Publisher outages are isolated. Successful journal data is still exported and the site is rebuilt using last-known-good records. Use `make update-strict` when automation should return a failure after publishing partial results.

Public HTML and JSON exports only include articles with a usable abstract. Records without abstracts remain in `.state/site.db` for future enrichment but are not published on the website or in `site/data/*.json*`. The standard weekly commands attempt OpenAlex abstract enrichment by default; set `OPENALEX=0` or pass `--no-openalex` when you need a faster run.

The operational status is visible at `site/status/index.html` and in `site/data/health.json`. Detailed per-run diagnostics are retained under `.state/archives/`.

## Scheduling

The state directory must persist between runs; it contains collection history and first-seen timestamps. Back it up before moving hosts.

Example weekly cron entry, running Mondays at 08:00:

```cron
0 8 * * 1 cd /path/to/communication-journal-site && ./scripts/run_weekly.sh >> .state/weekly.log 2>&1
```

After a successful run, deploy the complete `site/` directory to any static host. Do not deploy `.state/`.

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

Reusable presentation settings—including featured subareas, maximum papers per page, and article/call freshness thresholds—live in the `settings` block of `config/journals.yaml`.

Local runtime state is stored in `.state/site.db`; generated pages are stored in `site/`. Both are ignored by git. The builder maintains `site/.build-manifest.json` so pages removed from configuration do not survive future builds.

## Journal covers

Cover collection is deliberately separate from the weekly run. Publisher pages commonly block unattended requests, and Wikidata has only partial cover coverage, so the supported workflow combines three sources in priority order:

1. existing files in `.state/covers/`;
2. a local bulk import or curated direct URLs from `config/cover_sources.yaml`;
3. Wikidata images matched by ISSN.

Run the automatic pass first:

```bash
communication-journal-site fetch-covers
```

For the journals reported as missing, download the cover from the journal or publisher page and name it with the journal id from `config/journals.yaml`, for example `new-media-and-society.jpg`. Import a whole folder and require complete coverage with:

```bash
communication-journal-site fetch-covers --import-dir ~/Downloads/journal-covers --strict
communication-journal-site build-site
```

For repeatable remote downloads, add a publisher-owned direct image URL and its verification page to `config/cover_sources.yaml`. Downloads are cached locally and validated by file signature, so an HTML block page cannot be mistaken for a cover. Keep `.state/covers/` with the rest of the persistent state when moving or rebuilding the site.

## Development checks

```bash
make check
```

The test suite covers configuration, Crossref normalization, storage/deduplication, call parsing and lifecycle, partial weekly runs, exports, and static-site generation.

## Design Notes

- V1 has no login, admin UI, AI summaries, or AI tagging.
- Article subareas are journal-based only. If a journal belongs to multiple subareas, every paper appears in each configured subarea.
- Special-issue scraping is best-effort and source-linked. Findings missing after a successful source refresh become `unverified`; failed sources retain their last-known-good records.
