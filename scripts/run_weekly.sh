#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI="$ROOT/.venv/bin/communication-journal-site"

if [[ ! -x "$CLI" ]]; then
  echo "Project environment not found. Run 'make setup' first." >&2
  exit 2
fi

cd "$ROOT"
OPENALEX_FLAG=()
if [[ "${OPENALEX:-1}" == "0" || "${NO_OPENALEX:-0}" == "1" ]]; then
  OPENALEX_FLAG=(--no-openalex)
fi

exec "$CLI" run-weekly --digest-date "${DIGEST_DATE:-$(date +%F)}" "${OPENALEX_FLAG[@]}" "$@"
