# Public data snapshot

These files are the latest GitHub-published outputs from Communication Journal Monitor:

- `articles.jsonl`: complete public article history; one JSON object per line.
- `special_issues.jsonl`: active, upcoming, closed, and verification-state CFP records.
- `journals.json`: journal registry metadata and article counts.
- `journal_metrics.json`: ISSN-matched OpenAlex 2-year mean citedness values and provenance.
- `latest.json`: compact recent-article and active-call payload.
- `health.json`: collection freshness, run results, source counts, and CFP audit coverage.
- `paper_collection.json`: journal-by-journal results and errors from the latest article run.
- `special_issue_collection.json`: source-by-source results and errors from the latest CFP run.

For a human-readable summary, see the repository-level [`STATUS.md`](../../STATUS.md).

The scheduled GitHub workflow rebuilds state from this public snapshot, collects the latest 28-day window, and commits refreshed outputs every Monday. It does not publish the local SQLite database or records that fail the public abstract policy.

The article, CFP, and journal-impact jobs are separate. A publisher that blocks CFP automation does not reduce the 70-journal ISSN article collection. Journal metrics use the CC0 OpenAlex 2-year mean citedness field, not the Clarivate Journal Impact Factor; provenance and retrieval dates are embedded in `journal_metrics.json` and each `journals.json` record.

CFP coverage is intentionally not described as exhaustive. Read the main README's coverage table and known exclusions before using the absence of a call as evidence that none exists.
