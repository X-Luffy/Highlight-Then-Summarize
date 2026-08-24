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
