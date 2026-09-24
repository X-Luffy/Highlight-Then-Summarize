# H2S evaluator

`h2s_evaluator.py` implements the deterministic, task-specific metrics used for H2S-Bench. `run_h2s_eval.py` joins references and predictions by ID and produces per-case, per-benchmark, and macro-average reports.

```bash
python3 eval/run_h2s_eval.py \
  --data /path/to/H2S-Bench.jsonl \
  --predictions /path/to/predictions.jsonl \
  --output outputs/report.json \
  --protocol tagged
```

Use `--protocol native` for unstructured base/API responses and `--protocol tagged` for H2S SFT/RL responses. The seven H2S-Bench tasks are DocFinQA, Frames, LongCite, MRCR, AA-LCR, LongBenchV2, and HELMET-Summ.
