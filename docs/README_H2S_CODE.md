# H2S Code Map

This document maps the public release to the implementation described in the
paper. It is intentionally separate from the private training workspace.

## Reading path

1. `block_input.py` defines block-aware rendering, parsing, and prompt token
   counting.
2. `sft/system_prompt.py` defines the canonical H2S output prompts.
3. `sft/prepare_paper_sft.py` materializes structured SFT messages from
   Stage-6 records.
4. `rl/rewards.py` implements deterministic evidence, span, summary, and
   answer rewards.
5. `rl/longtext_reward_v3_swift.py` adapts the reward to Swift/GRPO.
6. `eval/evaluator_v2.py` and `eval/run_eval_v2.py` implement the offline
   Evaluator V2 used for the comparison tables.

## Structured interface

The assistant target is:

```text
<evidence>...</evidence>
<summary>...</summary>
<answer>...</answer>
```

Evidence entries identify a source block and a verbatim quote. Summary claims
refer back to evidence IDs. The answer section follows the native format of
the benchmark.

## Data-construction boundary

`data_pipeline/` contains the public orchestration and materialization
utilities. The private/API-backed Stage-2/4/5/6 executors are not included.
This means the repository documents the data contract and deterministic
materialization logic, but does not provide an offline one-command rebuild of
the full H2S-Bench.

## Training boundary

The YAML files in `configs/` are templates for the paper settings. Replace
model, dataset, output, and distributed-runtime paths before use. Full
training requires the external Swift/Transformers environment and the
complete data artifacts.

## Evaluation boundary

Use `id_v1_extended.jsonl` and `ood_v1_extended.jsonl` for the current
extended evaluation release. The repository includes only small examples;
full predictions and reports remain experiment artifacts.
