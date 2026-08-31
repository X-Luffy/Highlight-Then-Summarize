# Reproducibility Notes

## Artifact boundary

The project snapshot used for the experiments had four canonical data files: `sft_v1.jsonl` (4,228 strict/native-GT records), `rl_v1.jsonl` (2,419 active-pool records), `id_v1.jsonl` (500 records), and `ood_v1.jsonl` (500 records). They are intentionally excluded from this code repository. Model checkpoints, optimizer states, API keys, and internal absolute paths are also excluded.

The source pool covered 21 benchmarks. The data audit reported 5,260 SFT Stage-6 source cases, 5,160 summary passes, 4,539 answerable cases, and 4,228 strict paper outputs. The RL audit reported 3,000 Stage-6 source cases, 2,945 summary passes, and 2,600 answerable cases before the active-pool materialization. These are audit counts, not additional released records.

## Data construction

The pipeline uses semantic block segmentation with minimum/maximum block-window constraints, then combines BM25 and embedding rankings with reciprocal rank fusion. Stage-5 produces decomposed subqueries, claims, and source spans; Stage-6 deduplicates provenance, builds a query-aware summary, and applies coverage, factuality/span, and answerability checks.

The wrappers in `data_pipeline/` are reproducible only when compatible implementations of the external Stage-2/4/5/6 executors are supplied. The current snapshot contains the orchestration layer and materialization logic, but not those API-backed executors. This limitation is deliberate and is also stated in the paper.

## Training examples

The YAML files under `configs/` preserve the important experimental settings while removing machine-specific paths. In particular, the 7B curriculum runs use 34,816/67,584/133,120 maximum sequence lengths and 2,048 completion tokens; the 14B runs use sequence parallelism 8, eight generations per prompt, and 2,048 completion tokens. The phase-1 7B 128K example is a separate 8,192-token completion configuration and should not be confused with the curriculum files.

Before launching, replace `model`, `dataset`, `external_plugins`, and output paths with paths valid on the target machine. Do not use the example credential file as a real secret store.

## Evaluation

The versioned offline evaluator in `eval/evaluator_v2.py`, driven by `eval/run_eval_v2.py`, is deterministic and benchmark-native. Base/API predictions use the `native` protocol; SFT/RL predictions use the `tagged` protocol, which requires one complete `<answer>...</answer>` pair. ID evaluation excludes `MSMARCO-Rerank`; OOD evaluation excludes `HELMET-Rerank`, leaving 475 valid cases per split. The report's `task_macro_score` is the task-level macro used as Avg in the comparison table. The full prediction files and reports are separate experiment artifacts, not part of this source release.

The quick examples and curated benchmark-diverse cases in `data/` show the SFT,
RL, ID, and OOD schemas. The case-level examples in
`eval/examples/cases/` contain three shared cases for each of seven benchmarks
across 15 models. Inputs are capped at 1,000 characters for readability;
predictions and ground-truth answers are retained in full. These examples are
result snapshots only and must not be mistaken for the complete data package
or prediction artifacts.

## Checks

```bash
python3 -m unittest discover -s rl -p 'test_*.py'
python3 -m unittest discover -s data_pipeline -p 'test_*.py'
python3 -m py_compile $(find . -name '*.py' -not -path './.git/*')
```

No online service is needed for reward unit tests. Full data construction requires the project-approved API environment and the external executors described above.
