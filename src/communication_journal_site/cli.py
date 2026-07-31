from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .audit import audit_journal_registry
from .config import DEFAULT_CONFIG_DIR, load_config, resolve_project_path
from .covers import collect_covers, load_cover_sources
from .crossref import CrossrefClient
from .enrichment import MetadataEnricher, OpenAlexClient
from .exporter import export_public_data
from .http_client import HttpClient
from .health import local_today, write_last_run, write_special_issue_run
from .normalize import slugify
from .site import build_site
from .snapshots import bootstrap_public_snapshot
from .special_issues import SpecialIssueCollector
from .storage import StateStore


MAX_CONSECUTIVE_JOURNAL_FAILURES = 4


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 1
    load_local_env(args.config_dir)
    return args.func(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Communication journal static-site pipeline.")
    parser.add_argument("--config-dir", default=str(DEFAULT_CONFIG_DIR))
    subparsers = parser.add_subparsers(dest="command")

    audit_parser = subparsers.add_parser("audit-journals", help="Audit the configured journal registry.")
    audit_parser.add_argument("--source-list", default=None, help="Optional SJR/Scopus Communication source-list CSV/XLSX.")
    audit_parser.add_argument("--output", default="data/audit/journal_registry_audit.csv")
    audit_parser.set_defaults(func=cmd_audit_journals)

    collect_parser = subparsers.add_parser("collect-papers", help="Collect paper metadata from Crossref.")
    collect_parser.add_argument("--state-dir", default=None)
    collect_parser.add_argument("--lookback-days", type=int, default=None)
    collect_parser.add_argument("--end-date", default=None)
    collect_parser.add_argument("--no-openalex", action="store_true")
    collect_parser.add_argument(
        "--journals",
        default=None,
        help="Comma-separated journal IDs to collect (default: all active and collectable journals).",
    )
    collect_parser.set_defaults(func=cmd_collect_papers)

    special_parser = subparsers.add_parser("collect-special-issues", help="Screen configured special-issue pages.")
    special_parser.add_argument("--state-dir", default=None)
    special_parser.set_defaults(func=cmd_collect_special_issues)

    bootstrap_parser = subparsers.add_parser(
        "bootstrap-public-data",
        help="Restore public article/call history into local state for an ephemeral runner.",
    )
    bootstrap_parser.add_argument("--state-dir", default=None)
    bootstrap_parser.add_argument("--input-dir", default="data/public")
    bootstrap_parser.set_defaults(func=cmd_bootstrap_public_data)

    export_parser = subparsers.add_parser("export-public-data", help="Export public JSON/JSONL data.")
    export_parser.add_argument("--state-dir", default=None)
    export_parser.add_argument("--output-dir", default=None)
    export_parser.add_argument("--digest-date", default=None)
    export_parser.set_defaults(func=cmd_export_public_data)

    build_parser_ = subparsers.add_parser("build-site", help="Generate static HTML pages.")
    build_parser_.add_argument("--state-dir", default=None)
    build_parser_.add_argument("--output-dir", default=None)
    build_parser_.set_defaults(func=cmd_build_site)

    covers_parser = subparsers.add_parser(
        "fetch-covers",
        help="Collect and audit real journal cover images.",
    )
    covers_parser.add_argument("--state-dir", default=None)
    covers_parser.add_argument(
        "--sources",
        default=None,
        help="YAML file of curated direct image URLs (default: cover_sources.yaml in --config-dir).",
    )
    covers_parser.add_argument(
        "--import-dir",
        default=None,
        help="Directory of local covers named JOURNAL_ID.(jpg|png|webp|svg|gif).",
    )
    covers_parser.add_argument(
        "--strict",
        action="store_true",
        help="Return a failure status unless every configured journal has a real cover.",
    )
    covers_parser.set_defaults(func=cmd_fetch_covers)

    weekly_parser = subparsers.add_parser("run-weekly", help="Run collection, exports, and static-site build.")
    weekly_parser.add_argument("--state-dir", default=None)
    weekly_parser.add_argument("--output-dir", default=None)
    weekly_parser.add_argument("--digest-date", default=None)
    weekly_parser.add_argument("--lookback-days", type=int, default=None)
    weekly_parser.add_argument("--no-openalex", action="store_true")
    weekly_parser.add_argument(
        "--strict",
        action="store_true",
        help="Return a failure status after publishing if any source failed.",
    )
    weekly_parser.set_defaults(func=cmd_run_weekly)

    return parser


def cmd_audit_journals(args: argparse.Namespace) -> int:
    config = load_config(args.config_dir)
    output = resolve_project_path(args.config_dir, args.output)
    source_list = resolve_project_path(args.config_dir, args.source_list) if args.source_list else None
    written = audit_journal_registry(config.journals, output, source_list_path=source_list)
    print(f"Wrote journal registry audit to {written}.")
    return 0


def cmd_collect_papers(args: argparse.Namespace) -> int:
    config = load_config(args.config_dir)
    store = _store(config, args)
    end_date = date.fromisoformat(args.end_date) if args.end_date else local_today(config.settings.timezone)
    lookback_days = args.lookback_days or config.settings.default_lookback_days
    start_date = end_date - timedelta(days=lookback_days - 1)
    seen_at = utc_now().isoformat()
    client = CrossrefClient(
        http_client=HttpClient(timeout=12, max_attempts=3),
        mailto=os.environ.get("CJS_CROSSREF_MAILTO"),
        request_interval_seconds=float(
            os.environ.get("CJS_CROSSREF_INTERVAL_SECONDS", "0.5")
        ),
    )
    enricher = None if args.no_openalex else MetadataEnricher(
        OpenAlexClient(
            http_client=HttpClient(timeout=10, max_attempts=1),
            mailto=os.environ.get("CJS_OPENALEX_MAILTO"),
        )
    )
    archive = {
        "run_started_at": seen_at,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "journals": [],
    }
    stored_total = 0
    errors: list[dict[str, object]] = []
    selected_journals = _select_journals(config.journals, getattr(args, "journals", None))
    if getattr(args, "journals", None):
        requested = {item.strip() for item in args.journals.split(",") if item.strip()}
        unknown = sorted(requested - {journal.id for journal in selected_journals})
        if unknown:
            print(f"Warning: no active, collectable journal for: {', '.join(unknown)}", file=sys.stderr)
    consecutive_failures = 0
    for index, journal in enumerate(selected_journals):
        try:
            records = client.fetch_journal_records(journal, start_date, end_date)
            if enricher:
                records = enricher.enrich_records(records)
            stored = store.upsert_articles(records, seen_at=seen_at)
            stored_total += stored
            archive["journals"].append(
                {"journal_id": journal.id, "title": journal.title, "retrieved": len(records), "stored": stored}
            )
            consecutive_failures = 0
        except Exception as exc:
            error_text = str(exc)
            if _counts_toward_crossref_circuit(error_text):
                consecutive_failures += 1
            else:
                consecutive_failures = 0
            errors.append({"journal_id": journal.id, "error": str(exc)})
            archive["journals"].append(
                {"journal_id": journal.id, "title": journal.title, "retrieved": 0, "stored": 0, "error": str(exc)}
            )
            if consecutive_failures >= MAX_CONSECUTIVE_JOURNAL_FAILURES:
                skipped_reason = (
                    f"Skipped after {consecutive_failures} consecutive Crossref failures; "
                    "the upstream service may be unavailable."
                )
                for skipped in selected_journals[index + 1 :]:
                    errors.append({"journal_id": skipped.id, "error": skipped_reason, "skipped": True})
                    archive["journals"].append(
                        {
                            "journal_id": skipped.id,
                            "title": skipped.title,
                            "retrieved": 0,
                            "stored": 0,
                            "skipped": True,
                            "error": skipped_reason,
                        }
                    )
                break
    _write_archive(args.config_dir, config, f"collect-papers-{seen_at.replace(':', '-')}.json", archive)
    print(f"Collected {stored_total} articles from {len(archive['journals'])} configured journals.")
    if errors:
        skipped_count = sum(1 for error in errors if error.get("skipped"))
        failed_count = len(errors) - skipped_count
        summary = f"{failed_count} journal request(s) failed"
        if skipped_count:
            summary += f"; {skipped_count} skipped after the circuit breaker opened"
        print(f"{summary}. See .state/archives for details.", file=sys.stderr)
        return 1
    return 0


def cmd_collect_special_issues(args: argparse.Namespace) -> int:
    config = load_config(args.config_dir)
    store = _store(config, args)
    seen_at = utc_now().isoformat()
    collector = SpecialIssueCollector()
    records, errors = collector.collect(
        config.special_issue_sources,
        config.journal_by_id,
        today=local_today(config.settings.timezone),
    )
    stored = store.upsert_special_issues(records, seen_at=seen_at)
    unverified = store.reconcile_special_issues(records, collector.authoritative_source_ids)
    archive = {
        "run_started_at": seen_at,
        "retrieved": len(records),
        "stored": stored,
        "marked_unverified": unverified,
        "successful_source_ids": sorted(collector.successful_source_ids),
        "authoritative_source_ids": sorted(collector.authoritative_source_ids),
        "empty_source_ids": sorted(
            collector.successful_source_ids - collector.authoritative_source_ids
        ),
        "errors": errors,
    }
    _write_archive(args.config_dir, config, f"collect-special-issues-{seen_at.replace(':', '-')}.json", archive)
    active_sources = [source for source in config.special_issue_sources if source.active]
    manual_review_dates = [
        source.review_after
        for source in active_sources
        if source.source_type == "manual" and source.review_after
    ]
    write_special_issue_run(
        store.db_path.parent,
        {
            "status": "partial" if errors else "complete",
            "active_source_count": len(active_sources),
            "automated_source_count": sum(
                source.source_type != "manual" for source in active_sources
            ),
            "verified_source_count": sum(
                source.source_type == "manual" for source in active_sources
            ),
            "successful_source_count": len(collector.successful_source_ids),
            "failed_source_count": len(errors),
            "finding_count": len(records),
            "next_verified_review_on": min(manual_review_dates) if manual_review_dates else None,
        },
    )
    print(f"Collected {stored} special-issue finding(s).")
    if errors:
        print(f"{len(errors)} special-issue source(s) failed. See .state/archives for details.", file=sys.stderr)
        return 1
    return 0


def cmd_bootstrap_public_data(args: argparse.Namespace) -> int:
    config = load_config(args.config_dir)
    store = _store(config, args)
    input_dir = resolve_project_path(args.config_dir, args.input_dir)
    result = bootstrap_public_snapshot(store, input_dir)
    print(
        f"Bootstrapped {result['articles']} public article(s) and "
        f"{result['special_issues']} special-issue record(s)."
    )
    return 0


def cmd_export_public_data(args: argparse.Namespace) -> int:
    config = load_config(args.config_dir)
    store = _store(config, args)
    output_dir = _output_dir(config, args)
    digest_date = date.fromisoformat(args.digest_date) if args.digest_date else None
    paths = export_public_data(config, store, output_dir, digest_date=digest_date)
    for label, path in paths.items():
        print(f"Wrote {label}: {path}")
    return 0


def cmd_build_site(args: argparse.Namespace) -> int:
    config = load_config(args.config_dir)
    store = _store(config, args)
    output_dir = _output_dir(config, args)
    written = build_site(config, store, output_dir)
    print(f"Wrote {len(written)} site files to {output_dir}.")
    return 0


def cmd_fetch_covers(args: argparse.Namespace) -> int:
    config = load_config(args.config_dir)
    configured = args.state_dir or config.settings.state_dir
    cache_dir = resolve_project_path(args.config_dir, configured) / "covers"
    sources_path = (
        resolve_project_path(args.config_dir, args.sources)
        if getattr(args, "sources", None)
        else Path(args.config_dir) / "cover_sources.yaml"
    )
    import_dir = (
        resolve_project_path(args.config_dir, args.import_dir)
        if getattr(args, "import_dir", None)
        else None
    )
    sources = load_cover_sources(sources_path)
    known_source_keys = {
        key
        for journal in config.journals
        for key in (journal.id, slugify(journal.title))
    }
    unknown_sources = sorted(set(sources) - known_source_keys)
    if unknown_sources:
        print(
            f"Warning: cover sources contain unknown journal ids: {', '.join(unknown_sources)}",
            file=sys.stderr,
        )
    result = collect_covers(
        config.journals,
        cache_dir,
        sources=sources,
        import_dir=import_dir,
    )
    total = len(config.journals)
    print(
        f"Cached {len(result.covers)}/{total} real journal cover(s) in {cache_dir} "
        f"({len(result.imported)} imported, {len(result.downloaded)} URL, "
        f"{len(result.wikidata)} Wikidata this run)."
    )
    for journal_id, error in sorted(result.errors.items()):
        print(f"Cover error [{journal_id}]: {error}", file=sys.stderr)
    if result.missing:
        print(f"Missing {len(result.missing)} cover(s): {', '.join(result.missing)}", file=sys.stderr)
    return 1 if getattr(args, "strict", False) and result.missing else 0


def cmd_run_weekly(args: argparse.Namespace) -> int:
    config = load_config(args.config_dir)
    digest_date = date.fromisoformat(args.digest_date) if args.digest_date else local_today(config.settings.timezone)
    collect_args = argparse.Namespace(**vars(args), end_date=digest_date.isoformat())
    papers_rc = cmd_collect_papers(collect_args)
    special_rc = cmd_collect_special_issues(args)
    state_dir = _store(config, args).db_path.parent
    partial = papers_rc != 0 or special_rc != 0
    write_last_run(
        state_dir,
        {
            "digest_date": digest_date.isoformat(),
            "status": "partial" if partial else "complete",
            "paper_collection": "partial" if papers_rc else "complete",
            "special_issue_collection": "partial" if special_rc else "complete",
        },
    )
    export_args = argparse.Namespace(**{**vars(args), "digest_date": digest_date.isoformat()})
    export_rc = cmd_export_public_data(export_args)
    build_rc = cmd_build_site(args)
    failed = any(code != 0 for code in (papers_rc, special_rc, export_rc, build_rc))
    if failed and getattr(args, "strict", False):
        return 1
    return 0


def _select_journals(journals, journals_arg):
    collectable = [journal for journal in journals if journal.active and journal.collect]
    if not journals_arg:
        return collectable
    selected = {item.strip() for item in journals_arg.split(",") if item.strip()}
    return [journal for journal in collectable if journal.id in selected]


def _counts_toward_crossref_circuit(error_text: str) -> bool:
    """Return whether a collection failure likely means Crossref is broadly down."""

    # Crossref returns 404 for some ISSN/date windows or deprecated journal ISSNs.
    # Those are journal-specific failures; do not skip unrelated later journals.
    if "HTTP Error 404" in error_text:
        return False
    return True


def _store(config, args: argparse.Namespace) -> StateStore:
    configured = args.state_dir or config.settings.state_dir
    return StateStore(resolve_project_path(args.config_dir, configured) / "site.db")


def _output_dir(config, args: argparse.Namespace) -> Path:
    configured = args.output_dir or config.settings.output_dir
    return resolve_project_path(args.config_dir, configured)


def _write_archive(config_dir: str | Path, config, filename: str, payload: dict) -> Path:
    archive_dir = resolve_project_path(config_dir, config.settings.state_dir) / "archives"
    archive_dir.mkdir(parents=True, exist_ok=True)
    path = archive_dir / filename
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    return path


def load_local_env(config_dir: str | Path) -> None:
    root = Path(config_dir).resolve().parent
    for path in [root / ".env.local", root / ".env"]:
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'").strip('"'))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
