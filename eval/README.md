# Evaluator

`evaluator_v2.py` implements the versioned deterministic benchmark evaluator used for the final comparison tables. `run_eval_v2.py` joins records and predictions by `id`, applies split-specific exclusions, extracts the requested output protocol, and writes per-case, per-benchmark, and task-level reports.

```bash
python3 eval/run_eval_v2.py \
  --data /path/to/id.jsonl \
  --predictions /path/to/predictions_id.jsonl \
  --output outputs/id_report.json \
  --split id \
  --protocol tagged
```

Use `--protocol native` for base/API responses and `--protocol tagged` for SFT/RL responses. The ID split omits `MSMARCO-Rerank`; the OOD split omits `HELMET-Rerank`. The report's `task_macro_score` is the macro average over benchmark tasks, with LongBench-Pro T1--T11 averaged as one task.

## Configs and public examples

- `eval/configs/` contains the SFT/RL evaluation YAML templates. Machine-specific
  model paths and cluster addresses are represented by `${PROJECT_ROOT}` and
  `${MASTER_ADDR}` and must be replaced before use.
- `eval/examples/evaluator_all_models_seven_benchmark.{md,csv,json}` contains
  the latest seven-task summary snapshot.
- `eval/examples/qwen2.5_14b_rl128_case_scores_sample.jsonl` contains one
  compact per-case score record for each of the seven tasks. It is derived
  from the 14B RL128 `report_*_no_truncation.json` reports and does not include
  the large prediction files.

The seven tasks in the snapshot are DocFinQA, Frames, LongCite, MRCR, AA-LCR,
LongBenchV2, and HELMET-Summ. The summary is a result snapshot, not a
replacement for the full evaluator outputs.
