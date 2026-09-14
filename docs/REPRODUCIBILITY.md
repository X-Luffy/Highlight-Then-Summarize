# Reproducibility Notes

## Artifact boundary

The public repository contains implementation, configuration templates,
schemas, tests, and small examples. It deliberately excludes model weights,
optimizer states, runtime logs, private API clients, credentials, and the
complete training/evaluation JSONL files.

The paper's current evaluation uses the extended ID/OOD files:

```text
id_v1_extended.jsonl
ood_v1_extended.jsonl
```

The older `id_v1.jsonl` and `ood_v1.jsonl` files are historical data and
should not be used for the final paper tables.

## Data construction

The public pipeline contains candidate-pool construction, block-aware
materialization, deterministic filtering, and release-manifest utilities.
The original Stage-2/4/5/6 retrieval, evidence, summary, and QC executors
were private/API-backed services and are not part of this repository.

## Training

The paper uses:

- Qwen2.5-7B/14B-Instruct-1M initialization;
- full-parameter SFT;
- a 32K -> 64K -> 128K RL context curriculum;
- eight GRPO generations per prompt;
- a 2,048-token RL completion budget;
- bf16, gradient checkpointing, cosine scheduling, and ZeRO-3.

The public YAML files preserve these settings while leaving machine-specific
paths as values to be filled in. Do not commit credentials or absolute
cluster paths.

## Evaluation

`eval/evaluator_v2.py` is the versioned deterministic evaluator used by the
paper's seven-task comparison. `eval/run_eval_v2.py` joins records and
predictions by ID, applies split-specific rules, extracts the requested
protocol, and writes per-case and task-level reports.

Use `--protocol native` for base/API responses and `--protocol tagged` for
H2S SFT/RL responses. The current Evaluator V2 includes the paper's MRCR
fuzzy-95 rule and the robust LongBenchV2 final-answer parsing used for the
authoritative results.

Example:

```bash
python3 eval/run_eval_v2.py \
  --data /path/to/id_v1_extended.jsonl \
  --predictions /path/to/predictions_id.jsonl \
  --output outputs/id_report.json \
  --split id \
  --protocol tagged
```

## Checks

```bash
python3 -m unittest \
  rl.test_rewards \
  eval.test_evaluator_v2
python3 data_pipeline/test_pipeline_common.py
python3 -m py_compile $(find . -name '*.py' -not -path './.git/*')
```

The unit tests do not require model weights or online services.
