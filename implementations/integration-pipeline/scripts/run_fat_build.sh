#!/usr/bin/env bash
set -euo pipefail

# Convenience wrapper for the fat-jar build: picks a JDK 21 out of SDKMAN and
# then delegates to the integration pipeline's `fatjar` command. The build
# itself lives in the pipeline (src/steps/fatjar.py); this script only handles
# the JDK selection that SDKMAN users need.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PIPELINE_DIR="${PIPELINE_DIR:-${SCRIPT_DIR}/..}"

# ---- config you may edit ----
CUT_CSV="${1:-${SCRIPT_DIR}/../../../data/selected_cut_classes.csv}"   # first arg (optional)
MODE="${MODE:-local}"                                    # local | docker
BASE_DIR="${BASE_DIR:-../../workspace}"                  # where repos/out/.cache live, relative to the pipeline
LOG_DIR="${LOG_DIR:-}"                                   # optional override, e.g. ../../workspace/out/logs
FAILURES_CSV="${FAILURES_CSV:-../../results/run-agt/log/generate-auto.failures.csv}"
RETRY_ONLY="${RETRY_ONLY:-0}"                            # 1 to retry only failures
SDKMAN_CANDIDATES_DIR="${SDKMAN_CANDIDATES_DIR:-}"
JAVA21_HOME="${JAVA21_HOME:-}"
# -----------------------------

if [[ ! -f "$CUT_CSV" ]]; then
  echo "[ERROR] CUT_CSV not found: $CUT_CSV" >&2
  exit 1
fi

if [[ ! -d "$PIPELINE_DIR" ]]; then
  echo "[ERROR] Pipeline directory not found: $PIPELINE_DIR" >&2
  echo "Set PIPELINE_DIR to the integration-pipeline checkout." >&2
  exit 1
fi

# ---- find JDK 21 from SDKMAN without any interactive shell switching ----
if [[ -z "${JAVA21_HOME:-}" ]]; then
  if [[ -z "${SDKMAN_CANDIDATES_DIR:-}" ]]; then
    echo "[ERROR] Neither JAVA21_HOME nor SDKMAN_CANDIDATES_DIR is set." >&2
    echo "Set one, e.g.:  JAVA21_HOME=/path/to/jdk21 ./run_fat_build.sh" >&2
    exit 1
  fi
  # pick latest installed 21.x
  JAVA21_HOME="$SDKMAN_CANDIDATES_DIR/java/$(ls -1 "$SDKMAN_CANDIDATES_DIR/java" \
    | grep -E '^21\.' \
    | sort -V \
    | tail -n 1)"
fi

if [[ ! -d "$JAVA21_HOME" ]]; then
  echo "[ERROR] JAVA21_HOME does not exist: $JAVA21_HOME" >&2
  [[ -n "${SDKMAN_CANDIDATES_DIR:-}" ]] && { echo "Installed SDKMAN Javas:" >&2; ls -1 "$SDKMAN_CANDIDATES_DIR/java" || true; }
  exit 1
fi

echo "[INFO] Using JAVA21_HOME=$JAVA21_HOME"

CUT_CSV_ABS="$(cd -- "$(dirname -- "$CUT_CSV")" && pwd)/$(basename -- "$CUT_CSV")"

cmd=(python3 -m src fatjar
     --selected-cut-csv "$CUT_CSV_ABS"
     --mode "$MODE"
     --base-dir "$BASE_DIR"
     --java21-home "$JAVA21_HOME")

if [[ -n "$LOG_DIR" ]]; then
  cmd+=(--log-dir "$LOG_DIR")
fi

if [[ -n "$FAILURES_CSV" ]]; then
  cmd+=(--failures-csv "$FAILURES_CSV")
fi

if [[ "$RETRY_ONLY" == "1" ]]; then
  cmd+=(--retry-only)
fi

# Optional: force all builds to run under JDK 21 (not only retries)
export JAVA_HOME="$JAVA21_HOME"
export PATH="$JAVA21_HOME/bin:$PATH"

echo "[INFO] Running from ${PIPELINE_DIR}: ${cmd[*]}"
cd "$PIPELINE_DIR"
"${cmd[@]}"

echo "[DONE] Fat-jar build finished."
