# Reproducibility notes

The public release contains implementation, configurations, schemas, tests, figures, and small examples. It excludes weights, optimizer states, private API clients, credentials, runtime logs, and full corpora. The public artifact names are `H2S-SFT.jsonl` (4,228 cases), `H2S-RL.jsonl` (2,419 cases), and `H2S-Bench.jsonl` (2,575 cases); their local source filenames are `sft_v1.jsonl`, `rl_v1.jsonl`, and `test_v2.jsonl`.

## Training

- `Qwen/Qwen2.5-7B-Instruct-1M` and `Qwen/Qwen2.5-14B-Instruct-1M`;
- full-parameter SFT;
- 32K -> 64K -> 128K GRPO curriculum;
- eight candidates per prompt and 2,048 completion tokens;
- bf16, gradient checkpointing, cosine scheduling, and ZeRO-3.

## Reward and evaluation

`rl/h2s_rewards.py` implements the trajectory reward and `rl/h2s_reward_swift.py` exposes it to ms-swift. `eval/h2s_evaluator.py` implements task metrics; `eval/run_h2s_eval.py` joins references and predictions by ID.

```bash
python3 eval/run_h2s_eval.py \
  --data /path/to/H2S-Bench.jsonl \
  --predictions /path/to/predictions.jsonl \
  --output outputs/report.json \
  --protocol tagged
```

Use `--protocol native` for base/API responses and `--protocol tagged` for H2S outputs.

## Checks

```bash
python3 -m unittest rl.test_h2s_rewards eval.test_h2s_evaluator
python3 data_pipeline/test_pipeline_common.py
python3 -m py_compile $(find . -name '*.py' -not -path './.git/*')
```
