#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TIMESTAMP="$(date +"%Y%m%d_%H%M%S")"
RUNS_DIR="${RUNS_DIR:-$ROOT_DIR/study_runs}"
RUN_DIR="$RUNS_DIR/$TIMESTAMP"
RAW_DIR="$RUN_DIR/raw_scrape"
BASELINE_DIR="$RUN_DIR/baseline_pipeline"
LLM_DIR="$RUN_DIR/llm_publication"
SOURCE_DIR="$RUN_DIR/source_snapshot"
LOG_DIR="$RUN_DIR/logs"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.test-venv/bin/python}"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.study.env}"

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
fi

mkdir -p "$RAW_DIR" "$BASELINE_DIR" "$LLM_DIR" "$SOURCE_DIR" "$LOG_DIR"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Python executable not found at $PYTHON_BIN" >&2
  echo "Set PYTHON_BIN=/path/to/python or create the project venv first." >&2
  exit 1
fi

for key in REDDIT_CLIENT_ID REDDIT_CLIENT_SECRET REDDIT_USER_AGENT OPENAI_API_KEY; do
  if [[ -z "${!key:-}" ]]; then
    echo "Missing required environment variable: $key" >&2
    exit 1
  fi
done

cp "$ROOT_DIR/script.py" "$SOURCE_DIR/"
cp "$ROOT_DIR/Pipeline.py" "$SOURCE_DIR/"
cp "$ROOT_DIR/llm_publication_analysis.py" "$SOURCE_DIR/"
cp "$ROOT_DIR/run_full_study.sh" "$SOURCE_DIR/"

if [[ -f "$ROOT_DIR/Document 1 (1).pdf" ]]; then
  cp "$ROOT_DIR/Document 1 (1).pdf" "$SOURCE_DIR/"
fi

cat > "$RUN_DIR/RUN_METADATA.txt" <<EOF
run_timestamp=$TIMESTAMP
root_dir=$ROOT_DIR
python_bin=$PYTHON_BIN
primary_model=${OPENAI_PRIMARY_MODEL:-gpt-5.4-mini}
audit_model=${OPENAI_AUDIT_MODEL:-gpt-5.4-mini}
synthesis_model=${OPENAI_SYNTHESIS_MODEL:-gpt-5.4-mini}
coding_reasoning_effort=${OPENAI_CODING_REASONING_EFFORT:-low}
audit_reasoning_effort=${OPENAI_AUDIT_REASONING_EFFORT:-low}
synthesis_reasoning_effort=${OPENAI_SYNTHESIS_REASONING_EFFORT:-medium}
reddit_subreddits=${REDDIT_SUBREDDITS:-DEFAULT}
reddit_years_back=${REDDIT_YEARS_BACK:-5}
reddit_posts_per_year=${REDDIT_POSTS_PER_YEAR:-50}
llm_thread_limit=${LLM_THREAD_LIMIT:-ALL}
llm_max_comments_per_thread=${LLM_MAX_COMMENTS_PER_THREAD:-12}
EOF

echo "Run directory: $RUN_DIR"

echo "Step 1/3: scraping Reddit data"
SCRAPE_ENV=(
  "REDDIT_CLIENT_ID=$REDDIT_CLIENT_ID"
  "REDDIT_CLIENT_SECRET=$REDDIT_CLIENT_SECRET"
  "REDDIT_USER_AGENT=$REDDIT_USER_AGENT"
  "REDDIT_YEARS_BACK=${REDDIT_YEARS_BACK:-5}"
  "REDDIT_POSTS_PER_YEAR=${REDDIT_POSTS_PER_YEAR:-50}"
  "REDDIT_SLEEP_SECONDS=${REDDIT_SLEEP_SECONDS:-2}"
)

if [[ -n "${REDDIT_SUBREDDITS:-}" ]]; then
  SCRAPE_ENV+=("REDDIT_SUBREDDITS=$REDDIT_SUBREDDITS")
fi

(
  cd "$RAW_DIR"
  env "${SCRAPE_ENV[@]}" \
    "$PYTHON_BIN" "$ROOT_DIR/script.py"
) 2>&1 | tee "$LOG_DIR/01_scrape.log"

SCRAPED_POSTS="$(
  "$PYTHON_BIN" - <<PY
import pandas as pd
from pathlib import Path
path = Path("$RAW_DIR/all_reddit_posts.xlsx")
if not path.exists():
    print(0)
else:
    try:
        df = pd.read_excel(path)
        print(len(df))
    except Exception:
        print(0)
PY
)"

if [[ "$SCRAPED_POSTS" -eq 0 ]]; then
  echo "Scraper completed but collected 0 posts." >&2
  echo "If you did not intentionally set REDDIT_SUBREDDITS, the defaults should now apply on the next run." >&2
  echo "If this still happens, check subreddit names or the text filters in script.py." >&2
  exit 1
fi

cp "$RAW_DIR"/*.xlsx "$BASELINE_DIR/"

echo "Step 2/3: running baseline non-LLM pipeline"
(
  cd "$BASELINE_DIR"
  env \
    REDDIT_YEARS_BACK="${REDDIT_YEARS_BACK:-5}" \
    SENTENCE_MODEL_NAME="${SENTENCE_MODEL_NAME:-all-MiniLM-L6-v2}" \
    "$PYTHON_BIN" "$ROOT_DIR/Pipeline.py"
) 2>&1 | tee "$LOG_DIR/02_baseline_pipeline.log"

echo "Step 3/3: running publication-grade OpenAI pipeline"
LLM_ARGS=(
  --posts "$RAW_DIR/all_reddit_posts.xlsx"
  --comments "$RAW_DIR/all_reddit_comments.xlsx"
  --output-dir "$LLM_DIR"
  --years-back "${REDDIT_YEARS_BACK:-5}"
  --max-comments-per-thread "${LLM_MAX_COMMENTS_PER_THREAD:-12}"
  --primary-model "${OPENAI_PRIMARY_MODEL:-gpt-5.4-mini}"
  --audit-model "${OPENAI_AUDIT_MODEL:-gpt-5.4-mini}"
  --synthesis-model "${OPENAI_SYNTHESIS_MODEL:-gpt-5.4-mini}"
  --coding-reasoning-effort "${OPENAI_CODING_REASONING_EFFORT:-low}"
  --audit-reasoning-effort "${OPENAI_AUDIT_REASONING_EFFORT:-low}"
  --synthesis-reasoning-effort "${OPENAI_SYNTHESIS_REASONING_EFFORT:-medium}"
)

if [[ -n "${LLM_THREAD_LIMIT:-}" ]]; then
  LLM_ARGS+=(--thread-limit "$LLM_THREAD_LIMIT")
fi

(
  cd "$ROOT_DIR"
  env OPENAI_API_KEY="$OPENAI_API_KEY" \
    "$PYTHON_BIN" "$ROOT_DIR/llm_publication_analysis.py" "${LLM_ARGS[@]}"
) 2>&1 | tee "$LOG_DIR/03_llm_publication.log"

echo "Study run complete."
echo "Saved source snapshot: $SOURCE_DIR"
echo "Saved raw scrape files: $RAW_DIR"
echo "Saved baseline outputs: $BASELINE_DIR"
echo "Saved LLM outputs: $LLM_DIR"
echo "Saved logs: $LOG_DIR"