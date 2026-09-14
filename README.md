# H2S: Highlight-Then-Summarize

This repository contains the public code release for
**Highlight-Then-Summarize (H2S)**, an evidence-grounded long-context
reading procedure. H2S extracts source-grounded evidence, organizes it into a
question-aware summary, and then generates the final answer from that
intermediate representation.

The paper trains H2S with supervised fine-tuning followed by full-parameter
GRPO using deterministic process rewards for evidence validity, span
alignment, summary quality, and final-answer quality.

## Repository scope

This is a code and schema release. It does not contain model checkpoints,
optimizer states, private API clients, credentials, cluster launch scripts,
or the complete training/evaluation corpora. The `data/` and
`eval/examples/` directories contain small, curated examples for schema and
qualitative inspection only.

The full paper artifacts are maintained separately and should be obtained
from the project-approved artifact store. Do not commit large JSONL files,
model weights, runtime logs, or credentials to this repository.

## Layout

```text
block_input.py       Block-aware rendering, parsing, and token counting
configs/             Public SFT, RL, and evaluation configuration templates
data/                Small schema examples and release manifests
data_pipeline/       Data-construction orchestration and materialization code
docs/                Release, reproducibility, and implementation notes
eval/                Deterministic Evaluator V2 and evaluation CLI
rl/                  Programmatic process rewards and Swift/GRPO adapter
sft/                 SFT prompts, data preparation, and validation helpers
```

## Output protocol

H2S uses the following structured response:

```text
<evidence>
[{"id":"E0001","block_id":"...","quote":"verbatim source span"}]
</evidence>
<summary>
Question-aware summary with evidence references such as [E0001].
</summary>
<answer>
The final answer in the benchmark-native format.
</answer>
```

The implementation in `rl/rewards.py` computes the auditable reward:

```text
R_path   = H(R_valid, R_span, R_summary)
R_total  = R_format * (0.60 * R_answer + 0.40 * R_path)
```

Here `H` is the harmonic mean. The final-answer component uses the benchmark
registry in `eval/evaluator_v2.py`.

## Quick checks

The unit tests do not require model weights or an online service:

```bash
python3 -m unittest \
  rl.test_rewards \
  eval.test_evaluator_v2
python3 data_pipeline/test_pipeline_common.py
python3 -m py_compile $(find . -name '*.py' -not -path './.git/*')
```

Run the deterministic evaluator on a local prediction file with:

```bash
python3 eval/run_eval_v2.py \
  --data /path/to/id_v1_extended.jsonl \
  --predictions /path/to/predictions_id.jsonl \
  --output outputs/id_report.json \
  --split id \
  --protocol tagged
```

Use `--protocol native` for base/API predictions. Use `--protocol tagged` for
H2S SFT/RL responses.

## Training templates

The YAML files under `configs/` preserve the paper's important settings:

- Qwen2.5-7B/14B-Instruct-1M initialization;
- 128K input budget and 4K SFT output budget;
- 32K -> 64K -> 128K RL context curriculum;
- eight GRPO generations per prompt;
- 2,048-token RL completion budget;
- bf16, gradient checkpointing, cosine scheduling, and ZeRO-3.

Before launching, replace model, data, output, and distributed-runtime paths
with values valid on the target machine. The external training framework and
private data-construction executors are intentionally outside this release.

## Data and licensing

The release samples are included only to document schemas and code paths.
Check the licenses of all upstream benchmark sources before redistributing
full documents or derived datasets. The code is released under the MIT
License; see `LICENSE`.

## Paper and citation

The accompanying paper is titled
**Highlight-Then-Summarize: Evidence-Grounded Long-Context Understanding**.
Citation metadata is provided in `CITATION.cff`. The paper's anonymous source
keeps its repository URL as a placeholder during review; replace that
placeholder only when the submission is ready for a non-anonymous release.
