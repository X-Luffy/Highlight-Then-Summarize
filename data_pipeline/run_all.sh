#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python3}"
MODE="${MODE:-smoke}"
CONFIG="${CONFIG:-${SCRIPT_DIR}/configs/incremental_train_test.json}"
OUTPUT_ROOT="${OUTPUT_ROOT:-${SCRIPT_DIR}/output/incremental}"

if [[ "${1:-}" == "--full" ]]; then
  MODE="full"
  shift
fi
export MODE CONFIG OUTPUT_ROOT PYTHON

pool_args=()
if [[ "$#" -gt 0 ]]; then
  pool_args=("$@")
fi
source_args=(
  --config "${CONFIG}" \
  --output-root "${OUTPUT_ROOT}"
)
if [[ "${MODE}" == "smoke" ]]; then
  pool_args+=(
    --smoke-per-benchmark "${SMOKE_PER_BENCHMARK:-1}"
  )
  source_args+=(
    --smoke-per-benchmark "${SMOKE_PER_BENCHMARK:-1}"
  )
fi
if [[ "${#pool_args[@]}" -gt 0 ]]; then
  "${PYTHON}" "${SCRIPT_DIR}/00_build_incremental_pool.py" \
    "${pool_args[@]}"
else
  "${PYTHON}" "${SCRIPT_DIR}/00_build_incremental_pool.py"
fi
"${PYTHON}" "${SCRIPT_DIR}/01_build_incremental_source.py" \
  "${source_args[@]}"
"${SCRIPT_DIR}/02_run_block_split.sh"
"${SCRIPT_DIR}/03_run_retrieval.sh"
"${SCRIPT_DIR}/04_run_evidence.sh"
"${SCRIPT_DIR}/05_run_summary_qc.sh"

materialize_args=(
  --config "${CONFIG}" \
  --output-root "${OUTPUT_ROOT}"
)
if [[ "${MODE}" == "smoke" ]]; then
  materialize_args+=(
    --source-root "${OUTPUT_ROOT}/source_smoke/source"
    --legacy-source-root "${OUTPUT_ROOT}/source_smoke"
  )
fi
"${PYTHON}" "${SCRIPT_DIR}/06_materialize_train_test.py" \
  "${materialize_args[@]}"
