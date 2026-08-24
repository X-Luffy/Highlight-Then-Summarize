#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CODE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON="${PYTHON:-python3}"
MODE="${MODE:-smoke}"
CONFIG="${CONFIG:-${SCRIPT_DIR}/configs/incremental_train_test.json}"
OUTPUT_ROOT="${OUTPUT_ROOT:-${SCRIPT_DIR}/output/incremental}"
STAGE2_ROOT="${OUTPUT_ROOT}/stage2"
STAGE4_ROOT="${OUTPUT_ROOT}/stage4"

args=(
  --input-root "${STAGE2_ROOT}"
  --output-root "${STAGE4_ROOT}"
  --config "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["retrieval_config"])' "${CONFIG}")"
  --bm25-tokenizer "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["tokenizer"])' "${CONFIG}")"
  --splits sft rl
  --progress-every "${PROGRESS_EVERY:-10}"
)
if [[ "${MODE}" == "smoke" ]]; then
  args+=(--smoke-cases "${SMOKE_CASES:-14}" --smoke-target-blocks "${SMOKE_TARGET_BLOCKS:-32}")
fi

cd "${CODE_DIR}"
"${PYTHON}" 4_retrieve_blocks_api.py "${args[@]}"
