#!/usr/bin/env bash
set -euo pipefail

# Repository layout anchors; see src/core/layout.py for the canonical defaults.
export ITL_DATA_ROOT="${ITL_DATA_ROOT:-../../data}"
export ITL_WORKSPACE_ROOT="${ITL_WORKSPACE_ROOT:-../../workspace}"

INV="${1:-${ITL_DATA_ROOT}/collected-tests/_logs/tests_inventory.csv}"
MAP="${2:-${ITL_WORKSPACE_ROOT}/out/cut_to_fatjar_map.csv}"
STEP="${3:-all}"
SUBSTEP="${4:-}"
VARIANTS="${5:-}"
TOP_N="${6:-}"

STEP_LABEL="${STEP}"
if [[ -n "${SUBSTEP}" ]]; then
  STEP_LABEL="${STEP} ${SUBSTEP}"
fi
echo "[integration-pipeline] Starting (step: ${STEP_LABEL})..."

CMD_ARGS=(
  "${INV}"
  "${MAP}"
  "${STEP}"
)
if [[ -n "${SUBSTEP}" ]]; then
  CMD_ARGS+=("${SUBSTEP}")
fi

EXTRA_ARGS=()
if [[ -n "${VARIANTS}" ]]; then
  EXTRA_ARGS+=(--variants "${VARIANTS}")
fi
if [[ "${STEP}" == "reduce" && -n "${TOP_N}" ]]; then
  EXTRA_ARGS+=(--max-tests "${TOP_N}")
elif [[ "${STEP}" == "coverage" && "${SUBSTEP}" == "compare-reduced" && -n "${TOP_N}" ]]; then
  EXTRA_ARGS+=(--top-n "${TOP_N}")
fi

python -m src \
  --generated-dir "${ITL_DATA_ROOT}/collected-tests/generated" \
  --manual-dir "${ITL_DATA_ROOT}/collected-tests/manual" \
  --repos-dir "${ITL_WORKSPACE_ROOT}/repos" \
  --libs-cp "vendor/libs/*" \
  --build-dir "${ITL_WORKSPACE_ROOT}/pipeline/build/agt" \
  --out-dir "${ITL_WORKSPACE_ROOT}/pipeline/tmp" \
  "${CMD_ARGS[@]}" \
  "${EXTRA_ARGS[@]}"

echo "[integration-pipeline] Done."
