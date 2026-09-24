# Evaluation Examples

This directory contains readable case-level examples from the current
15-model evaluation comparison. It is intended for qualitative inspection of
questions, long-context inputs, model predictions, and ground-truth answers.
It does not contain score tables or other per-case metrics.

## Files

Each JSONL file corresponds to one benchmark:

- `cases/docfinqa.jsonl`
- `cases/frames.jsonl`
- `cases/longcite.jsonl`
- `cases/mrcr.jsonl`
- `cases/aa_lcr.jsonl`
- `cases/longbenchv2.jsonl`
- `cases/helmet_summ.jsonl`

Every file contains 45 records: 15 models and 3 shared cases per model. The
records are grouped by case so that predictions can be compared directly.

The included models are:

- Qwen2.5-7B Base
- Qwen2.5-7B SFT
- Qwen2.5-7B RL 32K
- Qwen2.5-7B RL 64K
- Qwen2.5-7B RL 128K
- Qwen2.5-7B RL 32K + Process Reward
- Qwen2.5-7B RL 64K + Process Reward
- Qwen2.5-7B RL 128K + Process Reward
- Qwen2.5-14B Base
- Qwen2.5-14B SFT
- Qwen2.5-14B RL 32K
- Qwen2.5-14B RL 64K
- Qwen2.5-14B RL 128K
- DeepSeek-v4-pro
- Kimi-k2.5

## Record Fields

Each record contains:

- `model`, `model_key`
- `split`, `benchmark`, `case_id`, `ability`
- `question`
- `input`: the serialized input messages, truncated to at most 1,000
  characters
- `input_truncated`
- `prediction`: the complete non-empty model prediction
- `gt_answer`: the complete ground-truth answer

The input is serialized as `[system]` and `[user]` message blocks. The
prediction and ground-truth fields are not truncated; list-valued ground truth
answers retain their original JSON type.

## Selection Scope

The examples were selected on 2026-08-31 from the 500-case ID/OOD comparison
sample used for the paper comparison. For each benchmark, the
first three cases in source order were retained only when all 15 models had a
non-empty prediction for that case. The source comparison sample is a curated
500-case view and is not the complete extended benchmark pool.
