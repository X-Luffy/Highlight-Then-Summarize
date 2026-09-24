# H2S data examples

This directory contains complete example records for the three paper artifacts. The full data files are intentionally excluded from Git.

| Example | Local source | Purpose |
|---|---|---|
| `H2S-SFT-example.jsonl` | `H2S-SFT.jsonl` (local: `sft_v1.jsonl`) | Structured SFT conversation |
| `H2S-RL-example.jsonl` | `H2S-RL.jsonl` (local: `rl_v1.jsonl`) | Prompt, reference process, and reward metadata |
| `H2S-Bench-ID-example.jsonl` | `H2S-Bench.jsonl` (local: `test_v2.jsonl`) | In-distribution evaluation case |
| `H2S-Bench-OOD-example.jsonl` | `H2S-Bench.jsonl` (local: `test_v2.jsonl`) | Out-of-distribution evaluation case |

`examples/` provides larger multi-case samples with H2S-facing filenames. Records retain complete message and reference fields so the public reward and evaluator can be exercised without guessing the schema.

Key fields are `id`, `benchmark`, `ability`, `question`, token-count metadata, and `messages` or `prompt`. H2S-RL and annotated H2S-Bench records additionally contain `gt`, `reference_spans`, `reference_claims`, `reference_subqueries`, and `reference_summary`.

```python
import json

with open("data/H2S-RL-example.jsonl", encoding="utf-8") as handle:
    case = json.loads(handle.readline())
print(case["benchmark"], case["question"])
```

These samples document interfaces only. Redistribution of full source documents must comply with each upstream benchmark license.
