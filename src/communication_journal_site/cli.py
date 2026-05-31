from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .audit import audit_journal_registry
from .config import DEFAULT_CONFIG_DIR, load_config, resolve_project_path
from .crossref import CrossrefClient
from .enrichment import MetadataEnricher, OpenAlexClient
from .exporter import export_public_data
from .http_client import HttpClient
from .site import build_site
from .special_issues import SpecialIssueCollector
from .storage import StateStore


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
    collect_parser.set_defaults(func=cmd_collect_papers)

    special_parser = subparsers.add_parser("collect-special-issues", help="Screen configured special-issue pages.")
    special_parser.add_argument("--state-dir", default=None)
    special_parser.set_defaults(func=cmd_collect_special_issues)

    export_parser = subparsers.add_parser("export-public-data", help="Export public JSON/JSONL data.")
    export_parser.add_argument("--state-dir", default=None)
    export_parser.add_argument("--output-dir", default=None)
    export_parser.add_argument("--digest-date", default=None)
    export_parser.set_defaults(func=cmd_export_public_data)

    build_parser_ = subparsers.add_parser("build-site", help="Generate static HTML pages.")
    build_parser_.add_argument("--state-dir", default=None)
    build_parser_.add_argument("--output-dir", default=None)
    build_parser_.set_defaults(func=cmd_build_site)

    weekly_parser = subparsers.add_parser("run-weekly", help="Run collection, exports, and static-site build.")
    weekly_parser.add_argument("--state-dir", default=None)
    weekly_parser.add_argument("--output-dir", default=None)
    weekly_parser.add_argument("--digest-date", default=None)
    weekly_parser.add_argument("--lookback-days", type=int, default=None)
    weekly_parser.add_argument("--no-openalex", action="store_true")
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
    end_date = date.fromisoformat(args.end_date) if args.end_date else date.today()
    lookback_days = args.lookback_days or config.settings.default_lookback_days
    start_date = end_date - timedelta(days=lookback_days - 1)
    seen_at = utc_now().isoformat()
    client = CrossrefClient(mailto=os.environ.get("CJS_CROSSREF_MAILTO"))
    enricher = None if args.no_openalex else MetadataEnricher(OpenAlexClient(mailto=os.environ.get("CJS_OPENALEX_MAILTO")))
    archive = {
        "run_started_at": seen_at,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "journals": [],
    }
    stored_total = 0
    errors: list[dict[str, str]] = []
    for journal in config.journals:
        if not journal.active or not journal.collect:
            continue
        try:
            records = client.fetch_journal_records(journal, start_date, end_date)
            if enricher:
                records = enricher.enrich_records(records)
            stored = store.upsert_articles(records, seen_at=seen_at)
            stored_total += stored
            archive["journals"].append(
                {"journal_id": journal.id, "title": journal.title, "retrieved": len(records), "stored": stored}
            )
        except Exception as exc:
            errors.append({"journal_id": journal.id, "error": str(exc)})
            archive["journals"].append(
                {"journal_id": journal.id, "title": journal.title, "retrieved": 0, "stored": 0, "error": str(exc)}
            )
    _write_archive(args.config_dir, config, f"collect-papers-{seen_at.replace(':', '-')}.json", archive)
    print(f"Collected {stored_total} articles from {len(archive['journals'])} configured journals.")
    if errors:
        print(f"{len(errors)} journal(s) failed. See .state/archives for details.", file=sys.stderr)
        return 1
    return 0


def cmd_collect_special_issues(args: argparse.Namespace) -> int:
    config = load_config(args.config_dir)
    store = _store(config, args)
    seen_at = utc_now().isoformat()
    records, errors = SpecialIssueCollector().collect(
        config.special_issue_sources,
        config.journal_by_id,
    )
    stored = store.upsert_special_issues(records, seen_at=seen_at)
    archive = {
        "run_started_at": seen_at,
        "retrieved": len(records),
        "stored": stored,
        "errors": errors,
    }
    _write_archive(args.config_dir, config, f"collect-special-issues-{seen_at.replace(':', '-')}.json", archive)
    print(f"Collected {stored} special-issue finding(s).")
    if errors:
        print(f"{len(errors)} special-issue source(s) failed. See .state/archives for details.", file=sys.stderr)
        return 1
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


def cmd_run_weekly(args: argparse.Namespace) -> int:
    config = load_config(args.config_dir)
    digest_date = date.fromisoformat(args.digest_date) if args.digest_date else date.today()
    collect_args = argparse.Namespace(**vars(args), end_date=digest_date.isoformat())
    rc = cmd_collect_papers(collect_args)
    if rc != 0:
        return rc
    rc = cmd_collect_special_issues(args)
    if rc != 0:
        return rc
    export_args = argparse.Namespace(**vars(args), digest_date=digest_date.isoformat())
    rc = cmd_export_public_data(export_args)
    if rc != 0:
        return rc
    return cmd_build_site(args)


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

