# Evaluator V2

`evaluator_v2.py` and `run_eval_v2.py` are the versioned FinalAnswer evaluator.
The original `evaluators.py` and `run_eval.py` remain unchanged for historical
reports and the existing RL reward implementation.

## One scoring contract

Every model is reduced to one final-answer string before benchmark scoring.

| Model output | Invocation | Normalization |
| --- | --- | --- |
| Base/Qwen Base/API baseline | `--protocol native` | Score the direct response. JSON answer wrappers are unwrapped; ordinary prose is preserved. |
| SFT/RL | `--protocol tagged` | Require exactly one complete `<answer>...</answer>` pair. Only the tag body is scored. Missing, duplicate, or empty tags score zero. |

Evidence and summary are never passed to a benchmark evaluator. The report
records `normalization_status` and `answer_tag_count` so format failures are
separable from answer quality.

After protocol extraction, both paths use the same wrapper normalization for
JSON answer objects, `[Answer]`/`[答案]`, and leading `the answer is`/`答案是`
cues. No model-specific scoring rule is applied after this point.

## Benchmark routing

- `CNNSum`, `GovReport`, `HELMET-Summ`, `LongBench-Pro-T4`: ROUGE-L.
- `CUAD`, `FinGLM`, `Frames`, `HELMET-LongQA`: max of normalized EM and token F1.
- `DocFinQA`, `QwenLong-Test-DocMath`, `LongBench-Pro-T8`: numeric tolerance,
  with a QA fallback only when the reference is not numeric.
- `LongBench-Pro-T1/T5/T6/T7/T9`, `AA-LCR`: set-style matching. T1/T6 use
  ranking only when the question explicitly requests an order; AA-LCR first
  classifies numeric answers, single-answer QA, and collection prompts.
- `LongBench-Pro-T2`, `MSMARCO-Rerank`, `HELMET-Rerank`: NDCG. The split-inapplicable rerank task is excluded from the corresponding ID/OOD aggregate (ID excludes `MSMARCO-Rerank`; OOD excludes `HELMET-Rerank`).
- `LongBench-Pro-T3/T11`, `LongBenchV2`: choice accuracy.
- `LongBench-Pro-T10`: protocol-aware QA, set, ranking, numeric, or choice.
- `LongCite`: claim-level content/citation harmonic mean. Claims are aligned
  one-to-one; citation ranges such as `[69-70]`, `[C516-C517]`, and separate
  `[69][70]` references are normalized to the same citation IDs.
- `HELMET-Cite`: a singular question over a list of acceptable aliases uses QA
  F1; collection questions use alias-aware soft Set F1.
- `MRCR`: normalized exact match.

## Reproducible commands

```bash
python train/eval/run_eval_v2.py \
  --data train/data/id.jsonl \
  --predictions path/to/predictions_id.jsonl \
  --output path/to/evaluator_v2_id.json \
  --split id \
  --protocol native

python train/eval/run_eval_v2.py \
  --data train/data/ood.jsonl \
  --predictions path/to/predictions_ood.jsonl \
  --output path/to/evaluator_v2_ood.json \
  --split ood \
  --protocol tagged
```

The split rule is recorded in `excluded_benchmarks` in every report. Use
`--exclude-benchmark` for any additional, experiment-specific exclusions.
