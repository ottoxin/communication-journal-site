# Communication Journal Monitor

[![Weekly data refresh](https://github.com/ottoxin/communication-journal-site/actions/workflows/weekly-data.yml/badge.svg)](https://github.com/ottoxin/communication-journal-site/actions/workflows/weekly-data.yml)

[Browse the current public data and status](data/public/README.md).

`communication-journal-site` is a reusable weekly collection and static-publishing pipeline for communication research. It collects journal articles, monitors calls for special issues, keeps local history in SQLite, and builds a searchable website plus public JSON exports.

The pipeline is deterministic and non-AI:

- journal metadata comes from YAML registry files;
- paper collection uses Crossref, with optional OpenAlex abstract fallback;
- papers inherit subarea tags from their journal;
- special-issue monitoring combines official publisher APIs/pages with time-limited, source-linked verification records;
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
2. screens configured special-issue sources, including the paginated Taylor & Francis API;
3. exports public JSON;
4. writes public health/freshness metadata;
5. builds the static website.

The GitHub `Weekly data refresh` workflow runs this collection every Monday at 14:15 UTC and can also be started manually. It commits the public data/status snapshot under `data/public/`; it does not redeploy the existing private Sites deployment. Because GitHub runners are ephemeral, the workflow bootstraps its SQLite state from the prior public snapshot before collecting the newest window. Local-only records are never included in that bootstrap.

For more reliable Crossref/OpenAlex polite-pool access, repository owners may set the non-secret Actions variables `CJS_CROSSREF_MAILTO` and `CJS_OPENALEX_MAILTO` to a monitored contact address. The scheduled runner also uses a conservative Crossref request interval and retries rate-limited requests before opening its circuit breaker.

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

## Article collection versus call-for-papers collection

These are separate pipelines. The article job queries Crossref by ISSN for all 70 active journals in `config/journals.yaml` and optionally uses OpenAlex to fill missing abstracts. Articles without a clean abstract stay in the local SQLite database and are not published. CFP collection does not inherit that 70-journal coverage: it runs only the sources declared active in `config/special_issue_sources.yaml`.

## Call-for-papers coverage

Every weekly run fetches active CFP sources, extracts still-open public-entry opportunities, links each result to the official source, stores history, exports status, and rebuilds the site. Failed sources retain last-known-good records; a call missing after a successful authoritative refresh becomes `unverified`. Manual records and dated journal audits expire under the configured 10-day verification window, so the status output reports reduced coverage instead of silently presenting stale checks as complete.

| Coverage mode | Included | Limitation |
|---|---|---|
| Systematic publisher API | All 35 Taylor & Francis journals in the registry are eligible through the official paginated special-issues API. Calls are mapped by publisher journal code/title. | Calls for journals outside the registry, entries without a usable deadline/direct link, closed public-entry stages, and invitation-only manuscript stages are excluded. |
| Direct publisher pages | Health Communication and Journal of Public Relations Research are checked directly as supplemental Taylor & Francis sources. | Page-layout changes can break extraction; the publisher API remains the broader source. |
| Association discovery | The National Communication Association journals page is checked and filtered for communication-relevant calls. | It is a discovery page, not a complete publisher inventory. |
| Verified manual records | Human Communication Research (OUP) and Communication and the Public (SAGE PDF) currently have source-linked, dated records. | They require re-verification by `review_after`; the weekly code run does not itself renew a human audit. |
| Dated journal audits | Twelve priority journals currently have evidence-linked audit dates: JoC, JCMC, Communication Theory, HCR, Annals, Communication Monographs, Political Communication, Communication Research, New Media & Society, Information, Communication & Society, Health Communication, and JPRR. | Audits expire after 10 days and indicate that an official source was checked, not that the journal has a stable automated feed. |

### Known CFP exclusions

- Oxford University Press is not covered publisher-wide because there is no stable central machine-readable communication-journal CFP feed and individual pages are inconsistent or intermittently unavailable. HCR is included through a dated manual record; JoC, JCMC, Communication Theory, and Annals are audit-only. Communication, Culture & Critique and Public Opinion Quarterly are article-only and are not currently CFP-audited.
- SAGE is not covered publisher-wide because its central CFP hubs return empty results or bot-mitigation responses to unattended/headless collection. Communication Research and New Media & Society are audit-only. The other 17 configured SAGE journals are article-only for CFP purposes. Communication and the Public is a separately verified PDF proposal call and is not part of the 70-journal article registry.
- No CFP source is currently configured for Elsevier (Public Relations Review; Discourse, Context & Media; Language & Communication; International Journal of Intercultural Relations), USC Annenberg Press (International Journal of Communication), Hogrefe (Journal of Media Psychology), Mary Ann Liebert (Cyberpsychology, Behavior, and Social Networking), Wiley (Personal Relationships), or Cogitatio (Media and Communication). Their articles are still collected.
- WikiCFP is intentionally disabled because its “communication” feed is dominated by telecommunications/networking conferences rather than communication-studies journal calls.
- Calls announced only in email lists, social media, or inaccessible pages are outside current systematic coverage unless a dated official-source record is added.

The weekly program is systematic and reproducible, but it is not a claim of publisher-wide CFP completeness. Human audit dates and manual records must be refreshed separately.

## Registry Strategy

`config/journals.yaml` contains a broad communication-journal seed registry with ISSNs, publisher/homepage metadata, journal-level subareas, and collection status. The registry is deliberately editable and auditable.

For larger coverage, place a downloaded Communication-category source list in `data/audit/` and run:

```bash
communication-journal-site audit-journals --source-list data/audit/scimago_communication.csv
```

The command writes `data/audit/journal_registry_audit.csv` and marks registry issues such as missing ISSNs, duplicate ISSNs, disabled journals, and source-list titles that are not yet present in the local registry.

## Configuration

- `config/journals.yaml`: site settings and journal registry. Priority journals can record `special_issue_checked_on` and an official `special_issue_audit_url`; the status page treats that audit as current only within the configured verification window.
- `config/subareas.yaml`: subarea labels and descriptions.
- `config/special_issue_sources.yaml`: layered special-issue sources. Automated publisher pages and feeds are preferred; source-linked manual records cover official pages or PDFs blocked by publisher anti-bot systems. Manual records require `verified_on` and `review_after` dates so they cannot remain current indefinitely.

The Taylor & Francis collector uses the publisher's official WordPress API, maps calls back to journals by publisher code/title, and paginates until the result set is exhausted. When a call has both an abstract/proposal deadline and a later invitation-only manuscript deadline, the collector uses the earliest entry-stage deadline. Page-expiry metadata is never treated as a submission deadline. Publisher hubs remain discovery inputs rather than sole proof: a current call is published only with a direct official source URL and a still-open public-entry deadline.

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
