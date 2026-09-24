# Highlight-Then-Summarize (H2S)

Official implementation and data schemas for **Highlight-Then-Summarize: Learning to Compress Evidence for Long-Context Understanding**. H2S turns long-context answering into one source-traceable trajectory:

```text
long document + question -> evidence -> question-conditioned summary -> answer
```

![H2S framework](assets/h2s-framework.png)

H2S first localizes source-grounded evidence, then integrates scattered evidence into a compact question-conditioned summary, and finally generates the task answer. Models are initialized from `Qwen/Qwen2.5-7B-Instruct-1M` or `Qwen/Qwen2.5-14B-Instruct-1M` and trained with full-parameter SFT followed by GRPO.

## Repository layout

```text
assets/          Paper framework and analysis figures
configs/         SFT and three-stage RL configuration templates
data/            H2S-SFT, H2S-RL, and H2S-Bench examples
data_pipeline/   Data-construction and materialization utilities
docs/            Code map, release manifest, and reproducibility notes
eval/            H2S evaluator and command-line runner
rl/              H2S process reward and ms-swift adapter
sft/             Prompt, preparation, and validation helpers
```

## Data

The local research artifacts corresponding to this release are:

| Public artifact | Local source artifact | Cases |
|---|---|---:|
| `H2S-SFT.jsonl` | `sft_v1.jsonl` | 4,228 |
| `H2S-RL.jsonl` | `rl_v1.jsonl` | 2,419 |
| `H2S-Bench.jsonl` | `test_v2.jsonl` | 2,575 |

The repository contains representative records rather than the full corpora:

- `data/H2S-SFT-example.jsonl`
- `data/H2S-RL-example.jsonl`
- `data/H2S-Bench-ID-example.jsonl`
- `data/H2S-Bench-OOD-example.jsonl`
- `data/examples/` for multi-case samples

Every H2S target follows:

```text
<evidence>source-addressable evidence</evidence>
<summary>question-conditioned summary</summary>
<answer>task answer</answer>
```

![H2S data construction](assets/h2s-data-pipeline.png)

## Training

The configurations in `configs/sft/` initialize from the Qwen2.5 Instruct-1M checkpoints. The RL configurations implement the 32K -> 64K -> 128K context curriculum with eight GRPO generations per prompt and a 2,048-token completion budget. Replace checkpoint, dataset, output, and distributed-runtime paths before use.

The public reward implementation is `rl/h2s_rewards.py`; `rl/h2s_reward_swift.py` registers the `h2s_reward` plugin for ms-swift.

## Evaluation

Evaluate predictions with the task-specific H2S evaluator:

```bash
python3 eval/run_h2s_eval.py \
  --data /path/to/H2S-Bench.jsonl \
  --predictions /path/to/predictions.jsonl \
  --output outputs/report.json \
  --protocol tagged
```

Use `--protocol native` for unstructured base/API responses and `--protocol tagged` for H2S outputs. The evaluator implementation is `eval/h2s_evaluator.py`.

![Evidence--Summary Quality](assets/h2s-esq.png)

## Validation

The core checks require no model weights or online service:

```bash
python3 -m unittest rl.test_h2s_rewards eval.test_h2s_evaluator
python3 data_pipeline/test_pipeline_common.py
python3 -m py_compile $(find . -name '*.py' -not -path './.git/*')
```

## Release scope

This public tree excludes model checkpoints, optimizer states, full datasets and predictions, private API clients, cluster launchers, credentials, and runtime logs. Verify the licenses of all upstream benchmarks before redistributing full documents or derived records. Code is released under the MIT License.

## Citation

Citation metadata is available in `CITATION.cff`.
