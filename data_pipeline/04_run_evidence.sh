#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CODE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON="${PYTHON:-python3}"
MODE="${MODE:-smoke}"
CONFIG="${CONFIG:-${SCRIPT_DIR}/configs/incremental_train_test.json}"
OUTPUT_ROOT="${OUTPUT_ROOT:-${SCRIPT_DIR}/output/incremental}"
STAGE4_ROOT="${OUTPUT_ROOT}/stage4"
STAGE5_ROOT="${OUTPUT_ROOT}/stage5"

args=(
  --input-root "${STAGE4_ROOT}"
  --output-root "${STAGE5_ROOT}"
  --config "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["evidence_config"])' "${CONFIG}")"
  --environment "${ENVIRONMENT_FILE:-${CODE_DIR}/docs/environment_skill.example.json}"
  --credential-section "${CREDENTIAL_SECTION:-oneapi}"
  --splits sft rl
  --concurrency "${CONCURRENCY:-20}"
  --progress-every "${PROGRESS_EVERY:-25}"
)
if [[ "${MODE}" == "smoke" ]]; then
  args+=(--smoke-per-benchmark "${SMOKE_PER_BENCHMARK:-1}")
else
  args+=(--smoke-per-benchmark 0 --retry-api-errors --retry-invalid)
fi

cd "${CODE_DIR}"
"${PYTHON}" 5_query_diff_evidence.py "${args[@]}"
