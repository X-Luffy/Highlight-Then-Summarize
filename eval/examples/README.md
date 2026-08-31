# Evaluation Examples

`evaluator_all_models_seven_benchmark.{md,csv,json}` is the latest local
seven-task summary snapshot. The JSON is a machine-independent public
projection of the detailed provenance bundle; it omits internal filesystem
paths, cluster addresses, and raw predictions.

- DocFinQA
- Frames
- LongCite
- MRCR
- AA-LCR
- LongBenchV2
- HELMET-Summ

`qwen2.5_14b_rl128_case_scores_sample.jsonl` contains one compact per-case
record for each task, using the Qwen2.5-14B RL128 report. It retains the case
ID, score, metric, evaluator, protocol, and normalization status while
excluding the full prediction and report directories.

The summary was generated from the 2026-08-28 LongBenchV2 parser-revision
bundle. It is intended for browsing and provenance orientation; rerun the
full evaluator to reproduce complete reports.
