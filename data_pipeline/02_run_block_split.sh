#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CODE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON="${PYTHON:-python3}"
MODE="${MODE:-smoke}"
CONFIG="${CONFIG:-${SCRIPT_DIR}/configs/incremental_train_test.json}"
OUTPUT_ROOT="${OUTPUT_ROOT:-${SCRIPT_DIR}/output/incremental}"
SOURCE_ROOT="${OUTPUT_ROOT}/source"
STAGE2_ROOT="${OUTPUT_ROOT}/stage2"
BUILD_SOURCE_ROOT="${SOURCE_ROOT}"

if [[ "${MODE}" == "smoke" ]]; then
  SMOKE_OUTPUT_ROOT="${OUTPUT_ROOT}/source_smoke"
  BUILD_SOURCE_ROOT="${SMOKE_OUTPUT_ROOT}/source"
  "${PYTHON}" "${SCRIPT_DIR}/01_build_incremental_source.py" \
    --config "${CONFIG}" \
    --candidate-pool "${OUTPUT_ROOT}/candidate_pool.jsonl" \
    --output-root "${SMOKE_OUTPUT_ROOT}" \
    --smoke-per-benchmark "${SMOKE_PER_BENCHMARK:-1}" >/dev/null
fi

args=(
  --data-root "${BUILD_SOURCE_ROOT}"
  --parser-config "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["parser_config"])' "${CONFIG}")"
  --policy-config "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["policy_config"])' "${CONFIG}")"
  --tokenizer "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["tokenizer"])' "${CONFIG}")"
  --write-rendered-cases
)
smoke_args=()
cd "${CODE_DIR}"
"${PYTHON}" 2_build_block_store.py \
  "${args[@]}" \
  --output-dir "${STAGE2_ROOT}/sft" \
  --splits sft \
  --smoke-cases 0
"${PYTHON}" 2_build_block_store.py \
  "${args[@]}" \
  --output-dir "${STAGE2_ROOT}/rl" \
  --splits rl \
  --smoke-cases 0
"${PYTHON}" 3_qc_block_store.py \
  --data-root "${BUILD_SOURCE_ROOT}" \
  --output-root "${STAGE2_ROOT}" \
  --splits sft rl
