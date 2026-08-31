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
- `eval/examples/cases/*.jsonl` contains case-level examples for seven
  benchmarks, covering 15 models and three shared cases per benchmark.
  Each record includes the case metadata, question, a 1,000-character input
  preview, the complete prediction, and the complete ground-truth answer.

The seven tasks in the snapshot are DocFinQA, Frames, LongCite, MRCR, AA-LCR,
LongBenchV2, and HELMET-Summ. The examples are a qualitative result snapshot,
not a replacement for the full evaluator outputs or prediction files.
