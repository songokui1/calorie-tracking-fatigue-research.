#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SMOKE_DIR="$ROOT_DIR/.smoke-test"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.test-venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Python executable not found at $PYTHON_BIN" >&2
  echo "Set PYTHON_BIN=/path/to/python or create $ROOT_DIR/.test-venv first." >&2
  exit 1
fi

for key in REDDIT_CLIENT_ID REDDIT_CLIENT_SECRET REDDIT_USER_AGENT; do
  if [[ -z "${!key:-}" ]]; then
    echo "Missing required environment variable: $key" >&2
    exit 1
  fi
done

mkdir -p "$SMOKE_DIR"
cd "$SMOKE_DIR"

echo "Running smoke-test scrape into $SMOKE_DIR"
env \
  SMOKE_TEST=1 \
  REDDIT_SUBREDDITS="${REDDIT_SUBREDDITS:-Myfitnesspal}" \
  REDDIT_POSTS_PER_YEAR="${REDDIT_POSTS_PER_YEAR:-1}" \
  REDDIT_SLEEP_SECONDS="${REDDIT_SLEEP_SECONDS:-0}" \
  REDDIT_YEARS_BACK="${REDDIT_YEARS_BACK:-5}" \
  "$PYTHON_BIN" "$ROOT_DIR/script.py"

echo "Running smoke-test analysis"
env \
  SMOKE_TEST=1 \
  REDDIT_YEARS_BACK="${REDDIT_YEARS_BACK:-5}" \
  "$PYTHON_BIN" "$ROOT_DIR/Pipeline.py"

echo "Smoke test complete."
echo "Outputs are in $SMOKE_DIR"