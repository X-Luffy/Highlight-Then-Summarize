# H2S-SFT

This directory contains the framework-neutral SFT prompt, data preparation,
and validation helpers used by H2S.

## Structured target

The paper target uses:

```text
<evidence>...</evidence>
<summary>...</summary>
<answer>...</answer>
```

`system_prompt.py` defines the canonical prompts. `dataset.py` validates both
the native record schema and the message-based paper schema.

## Validation

```bash
python3 -m sft.validate_data \
  --data /path/to/H2S-SFT.jsonl \
  --format messages
```

## Materialization

`prepare_paper_sft.py` converts Stage-6 records and frozen native answers into
structured SFT messages. It depends on the public block renderer and on the
Stage-6 input files supplied by the project artifact store. The external
retrieval and construction services are not included in this repository.
